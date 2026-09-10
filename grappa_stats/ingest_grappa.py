#!/usr/bin/env python3
"""Ingest channel stats from the grappa message archive instead of bot.log.

Why this exists (vjt, #sbiffo 2026-09-10: "e se invece di usare il log su disco
usassimo il mio irclog sul db di grappa?"): the bot.log source only ever saw the
23 channels my own bridges sit in, on two networks, and it tied the stats daemon
to the Pi where those files live. vjt's own grappa archive covers eight networks
and every channel he is in, is written by the server itself, and is live to the
second. It is also the authority on WHO said WHAT — no log-line parsing, no
midnight-rollover date reconstruction, no byte offsets.

What it is NOT: a second copy of the classification canon. Nick classes, alias
folding, telegram relay unwrap and blasphemy counting all stay in `ingest.py`
and are called from here. This module only turns SQL rows into the same
`events` / `presence` rows `ingest.backfill_file()` produces.

Cursor: `ingest_state.source = 'grappa:<slug>'`, with the last consumed
`messages.id` in `offset`. The archive is append-only and ids are monotonic, so
a rowid high-water mark is the whole state — no inode, no size, no rollover
bookkeeping. `inode`/`size` are written as 0 to keep the shared table shape.

Channel allowlist: `classes.json["grappa_channels"]`, keyed by network slug
(vjt, #sbiffo 2026-09-10: "non TUTTI i canali chi se ne frega di molti canali").
The key is the PAIR — #linux exists on azzurra, libera and oftc in that archive
and only the azzurra one was asked for.

DMs are never read: every query carries `dm_with IS NULL`. That column is
populated by grappa at persist time for both directions of a private
conversation, so the filter is exact, not a heuristic on the channel name.

ONE SUBJECT, ALWAYS. `messages` is not a channel log — it is per-subject
scrollback, one row per (message, user or visitor who received it). The live
archive holds 3.5M rows of which 2.6M belong to anonymous visitors and the rest
are split across six accounts, so an unfiltered read both multiplies every
channel line by its audience (measured: 1.98M rows where 376k exist) and reads
other people's scrollback. Every query is therefore pinned to a single
`user_id`, vjt's by default — his own archive, which is the thing he offered.
"""
import json
import os
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ingest  # noqa: E402  (classification canon — never duplicated here)

# Default location inside the `grappa-new` jail on m42, where this daemon runs:
# a WAL reader has to be able to write the -shm sidecar, so it must live on the
# same filesystem as the database, i.e. inside that jail and not across a
# nullfs mount from another one.
SOURCE_DB = os.environ.get(
    "GRAPPA_DB", "/home/grappa/grappa/runtime/grappa_prod.db")

# One SELECT per call is bounded so a first run over ~700k rows streams in
# batches instead of building one enormous list in memory.
BATCH = int(os.environ.get("GRAPPA_BATCH", "50000"))

# Whose scrollback to read. See the module docstring: this is a correctness
# requirement before it is a privacy one.
SUBJECT = os.environ.get("GRAPPA_USER", "vjt")

# grappa kinds that carry a human message. `notice` is deliberately absent:
# the bot.log parser only ever counted PRIVMSG/ACTION, and counting notices now
# would put a step in every historical series that is an artefact of the source
# change, not of anything that happened on IRC.
MSG_KINDS = ("privmsg", "action")

# grappa kind -> our presence kind. grappa already fans QUIT and NICK out per
# channel (it knows the membership; the raw protocol does not), so unlike the
# log parser we never emit the network-wide '*' rows that the replay would then
# have to attribute itself.
PRESENCE_KINDS = {
    "join": "join",
    "part": "part",
    "quit": "quit",
    "kick": "kick",
    "nick_change": "nick",
}


def open_source(path=SOURCE_DB):
    """Read-only connection to the grappa archive. `mode=ro` is the promise
    this daemon makes to the server that owns the file: it is a live production
    database, and nothing here may write to it."""
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def network_slugs(src):
    """{network_id: slug} from the archive itself. The ids are grappa's own
    surrogate keys and mean nothing here; the slug is what the stats DB stores
    and what the allowlist is written against."""
    return {i: s for i, s in src.execute("SELECT id, slug FROM networks")}


def allowlist(cfg):
    """{slug: {channel_lowercase, ...}} from classes.json. Missing key means
    'ingest nothing' rather than 'ingest everything': this source can see every
    channel vjt is in, so a config typo must fail closed."""
    raw = cfg.get("grappa_channels") or {}
    return {slug: {c.lower() for c in chans} for slug, chans in raw.items()}


def _cursor(conn, source):
    row = conn.execute(
        "SELECT offset FROM ingest_state WHERE source=?", (source,)).fetchone()
    return row[0] if row else 0


def _save_cursor(conn, source, last_id):
    conn.execute(
        "INSERT OR REPLACE INTO ingest_state(source,offset,inode,size,"
        "prev_h,prev_m,prev_s,cur_date) VALUES(?,?,0,0,NULL,NULL,NULL,NULL)",
        (source, last_id))


def _meta_extra(kind, meta):
    """presence.extra for the two kinds that carry a second name. grappa stores
    meta as a JSON TEXT column; a malformed one is not worth killing a cycle
    over, so it degrades to no extra."""
    if kind not in ("nick_change", "kick") or not meta:
        return None
    try:
        m = json.loads(meta) if isinstance(meta, str) else meta
    except (ValueError, TypeError):
        return None
    return m.get("new_nick") if kind == "nick_change" else m.get("target")


def subject_id(src, name=SUBJECT):
    """The `users.id` whose scrollback we read. Absent user is fatal by
    omission: the caller gets None and skips the network rather than silently
    widening to everybody's rows."""
    row = src.execute("SELECT id FROM users WHERE name=?", (name,)).fetchone()
    return row[0] if row else None


def catchup(conn, cfg, src, network_id, slug, channels, uid):
    """Consume every archive row past the cursor for one network. Returns the
    number of message rows inserted (presence rows are not counted — the daemon
    logs 'new messages', and a netsplit is not news)."""
    if not channels or not uid:
        return 0
    source = f"grappa:{slug}"
    last = _cursor(conn, source)
    placeholders = ",".join("?" * len(channels))
    kinds = tuple(MSG_KINDS) + tuple(PRESENCE_KINDS)
    kind_ph = ",".join("?" * len(kinds))
    total = 0
    while True:
        rows = src.execute(
            "SELECT id, channel, server_time, kind, sender, body, meta"
            " FROM messages"
            f" WHERE user_id=? AND network_id=? AND id>? AND dm_with IS NULL"
            f" AND lower(channel) IN ({placeholders})"
            f" AND kind IN ({kind_ph})"
            " ORDER BY id LIMIT ?",
            (uid, network_id, last, *sorted(channels), *kinds, BATCH)).fetchall()
        if not rows:
            break
        erows, prows = [], []
        for rid, channel, server_time, kind, sender, body, meta in rows:
            # grappa stamps server_time in milliseconds; the stats DB has always
            # stored whole seconds and every bucket downstream is Rome-day.
            ts = server_time // 1000
            if kind in PRESENCE_KINDS:
                prows.append((ts, slug, channel, PRESENCE_KINDS[kind],
                              sender, _meta_extra(kind, meta)))
                last = rid
                continue
            parsed = {"direction": "<", "nick": sender, "target": channel,
                      "text": body or "", "is_action": kind == "action",
                      "origin": None}
            nick, klass, transport, is_action = ingest.resolve(cfg, parsed)
            if nick:
                erows.append((ts, slug, channel, nick, klass, transport,
                              1 if is_action else 0,
                              ingest.count_blasphemy(body or "")))
            last = rid
        conn.executemany(
            "INSERT INTO events(ts,network,channel,nick,klass,transport,"
            "is_action,bestemmie) VALUES(?,?,?,?,?,?,?,?)", erows)
        conn.executemany(
            "INSERT INTO presence(ts,network,channel,kind,nick,extra)"
            " VALUES(?,?,?,?,?,?)", prows)
        _save_cursor(conn, source, last)
        conn.commit()
        total += len(erows)
        if len(rows) < BATCH:
            break
    for channel in sorted(channels):
        seed_presence(conn, slug, channel)
    return total


def seed_presence(conn, network, channel):
    """Reconstruct the membership that existed BEFORE the archive's first row.

    `metrics.presence_daily()` replays deltas from a NAMES snapshot, and grappa
    persists no NAMES: its archive starts mid-conversation, with a channel that
    is already full of people. Without a seed the replay would start from the
    empty set and every pre-existing member would decrement it below zero on
    their way out.

    The seed is measured, not guessed: replay the deltas once and collect every
    nick that LEAVES (part/quit/kick) or is RENAMED while not in the set. Only
    someone who was already there can do that. Those nicks are then written back
    as a synthetic `names`/`names_end` pair stamped one second before the first
    real delta, which is exactly the shape the replay already knows how to read.

    Idempotent: a channel that already has a `names` row is left alone, so a
    later catch-up never re-seeds a set that has since been replayed forward.
    """
    have = conn.execute(
        "SELECT 1 FROM presence WHERE network=? AND channel=? AND kind='names'"
        " LIMIT 1", (network, channel)).fetchone()
    if have:
        return
    rows = conn.execute(
        "SELECT ts, kind, nick, extra FROM presence"
        " WHERE network=? AND channel=? ORDER BY ts, id",
        (network, channel)).fetchall()
    if not rows:
        return
    present = set()
    seed = []
    for _ts, kind, nick, extra in rows:
        low = nick.lower()
        if kind == "join":
            present.add(low)
            continue
        if kind == "nick":
            if low not in present:
                seed.append(nick)
            present.discard(low)
            if extra:
                present.add(extra.lower())
            continue
        # part / quit / kick
        if low not in present:
            seed.append(nick)
        present.discard(low)
    if not seed:
        return
    first_ts = rows[0][0] - 1
    conn.execute(
        "INSERT INTO presence(ts,network,channel,kind,nick,extra)"
        " VALUES(?,?,?,'names','',?)", (first_ts, network, channel, " ".join(seed)))
    conn.execute(
        "INSERT INTO presence(ts,network,channel,kind,nick,extra)"
        " VALUES(?,?,?,'names_end','',NULL)", (first_ts, network, channel))
    conn.commit()


def cycle(conn, cfg, src=None):
    """One full pass over every allowlisted (network, channel). Returns the
    number of new message rows."""
    close = src is None
    src = src or open_source()
    try:
        wanted = allowlist(cfg)
        uid = subject_id(src)
        new = 0
        for net_id, slug in sorted(network_slugs(src).items()):
            new += catchup(conn, cfg, src, net_id, slug,
                           wanted.get(slug, set()), uid)
        return new
    finally:
        if close:
            src.close()


def _main(argv):
    cfg = ingest.load_config()
    conn = ingest.open_db(argv[1] if len(argv) > 1 else str(HERE / "stats.db"))
    print(cycle(conn, cfg), "new message rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
