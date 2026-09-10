#!/usr/bin/env python3
"""sniffo message-stats daemon — ingest core (offline-testable).

Reads bot.log (Azzurra) / bot.libera.log (Libera), one event row per channel
PRIVMSG/ACTION into SQLite (bodies never stored), classifying each message as
ai / classic / human / service and irc / telegram transport. The headline the
page answers ("a dante lo scettico"): how much of a channel is bots/AI vs real
humans — so telegram relays MUST resolve to the human behind them, and the
outbound leg (vjt-claude itself) MUST count as `ai`, not be dropped.

This module is the build-order step 1 of tmp/sniffo-stats/PLAN.md: ingest +
schema + classification, testable against a bot.log COPY without touching the
host. Metric JSON, HTML pages, deploy and systemd are later steps.

Bestemmia detection + bridge/alias canon are IMPORTED from roll_counter.py
(single source of truth, PLAN §9) — not duplicated here.
"""
import json
import os
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

# roll_counter lives in the repo root next to this file (once deployed) — pin
# the path so the import works from tmp/ during the build phase too. The daemon
# also runs inside the `grappa-new` jail on m42, where the bot repo does not
# exist at all: there a copy sits next to this file, so the local directory is
# searched FIRST and the pinned repo path is only the fallback.
REPO = os.environ.get("SNIFFO_STATS_REPO", "/home/vjt/code/IRC/vjt-claude")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(1, REPO)
import roll_counter as rc  # noqa: E402  BLASPHEMY_PAT/CONCAT_PAT/stopwords/bridge/aliases

_TZ = ZoneInfo("Europe/Rome")
# UTC->Rome cutover in bot.log stamps (roll_counter documents the same). Lines
# before this instant were UTC; day/hour buckets tolerate the ≤2h edge skew.
_CUTOVER = datetime(2026, 7, 2, tzinfo=_TZ).timestamp()

HERE = Path(__file__).resolve().parent
CONFIG_PATH = HERE / "classes.json"

# One row per channel PRIVMSG/ACTION. No body column, ever.
SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id        INTEGER PRIMARY KEY,
    ts        INTEGER NOT NULL,      -- unix epoch (UTC)
    network   TEXT    NOT NULL,      -- 'azzurra' | 'libera'
    channel   TEXT    NOT NULL,      -- '#sniffo'
    nick      TEXT    NOT NULL,      -- REAL nick; for relays the inner human
    klass     TEXT    NOT NULL,      -- 'ai' | 'classic' | 'human' | 'service'
    transport TEXT    NOT NULL,      -- 'irc' | 'telegram'
    is_action INTEGER NOT NULL,      -- 0/1
    bestemmie INTEGER NOT NULL       -- count of blasphemy hits in this message
);
CREATE INDEX IF NOT EXISTS ix_events_net_chan_ts ON events(network, channel, ts);
CREATE INDEX IF NOT EXISTS ix_events_nick ON events(nick);
CREATE TABLE IF NOT EXISTS ingest_state (
    source  TEXT PRIMARY KEY,        -- absolute log path
    offset  INTEGER NOT NULL,        -- bytes consumed
    inode   INTEGER NOT NULL,
    size    INTEGER NOT NULL,
    prev_h  INTEGER, prev_m INTEGER, prev_s INTEGER,  -- last HH:MM:SS seen
    cur_date TEXT                     -- ISO date currently assigned (catch-up)
);
-- Raw membership deltas, replayed (not aggregated) by metrics.presence_daily().
-- Stored as EVENTS rather than as a running count because the count depends on
-- who is in the set: QUIT/NICK are network-wide (the protocol gives no channel),
-- so only a replay that knows the current membership can attribute them.
CREATE TABLE IF NOT EXISTS presence (
    id      INTEGER PRIMARY KEY,
    ts      INTEGER NOT NULL,
    network TEXT    NOT NULL,
    channel TEXT    NOT NULL,        -- '#sniffo', or '*' for network-wide (quit/nick)
    kind    TEXT    NOT NULL,        -- names|names_end|join|part|quit|nick|kick
    nick    TEXT    NOT NULL,        -- actor (kick: the victim; names: '')
    extra   TEXT                     -- names: the chunk of the list; nick: the new nick
);
CREATE INDEX IF NOT EXISTS ix_presence_net_ts ON presence(network, ts, id);
"""

# Telegram bridges insert a zero-width/invisible char inside relayed nicks
# (anti-self-highlight), e.g. "M⁣ez". Strip format chars or every relayed
# human mis-keys and distinct-user/top-talker counts break.
_INVISIBLE = re.compile(r"[​-‏‪-‮⁠-⁤﻿]")

# mIRC formatting: colour (\x03 plus up to "fg,bg"), bold, italic, underline,
# reverse, monospace, hex colour and the reset. Gazzurbo paints the relay
# prefix, so a bridged line reaches us as "\x0350<\x02Nick\x02> \x0fbody" and
# BRIDGE_PREFIX_PAT — anchored on a bare "<" — never matches: the relay stays
# classified as a classic bot and shows up in the top-talkers. Measured on the
# prod archive, 2026-09-10: 4643 of the last 5000 #endsummercamp lines carry
# \x03. Strip these before unwrapping, not just the zero-width chars.
_MIRC_FMT = re.compile(r"\x03(?:\d{1,2}(?:,\d{1,2})?)?|\x04[0-9A-Fa-f]{6}|[\x02\x0f\x11\x16\x1d\x1e\x1f]")

# TS + direction prefix bot.py stamps: "HH:MM:SS < ..." / "HH:MM:SS > ...".
_TS = re.compile(r"^(\d{2}):(\d{2}):(\d{2}) ([<>]) (.*)$", re.DOTALL)
# Inbound PRIVMSG (after the "< " prefix is stripped).
_IN = re.compile(r"^:(?P<nick>[^!@ ]+)!\S+ PRIVMSG (?P<target>\S+) :(?P<text>.*)$", re.DOTALL)
# Outbound PRIVMSG (after "> "); sender is vjt-claude itself. bot.py may stamp
# an origin tag right after the direction marker ("> [impiccato] PRIVMSG ..."):
# the LLM leaves it off, the deterministic sidecars set it, so the class comes
# from the tag instead of from the direction being ">" (vjt, 2026-08-26).
_OUT = re.compile(r"^(?:\[(?P<origin>[a-z0-9_-]{1,24})\] )?PRIVMSG (?P<target>\S+) :(?P<text>.*)$", re.DOTALL)

# --- membership deltas (inbound only; the server echoes our own JOIN/PART back)
_P_JOIN = re.compile(r"^:(?P<nick>[^!@ ]+)!\S+ JOIN :?(?P<chan>#\S+)")
_P_PART = re.compile(r"^:(?P<nick>[^!@ ]+)!\S+ PART :?(?P<chan>#\S+)")
_P_QUIT = re.compile(r"^:(?P<nick>[^!@ ]+)!\S+ QUIT\b")
_P_NICK = re.compile(r"^:(?P<nick>[^!@ ]+)!\S+ NICK :?(?P<new>\S+)")
_P_KICK = re.compile(r"^:\S+ KICK (?P<chan>#\S+) (?P<victim>\S+)")
# NAMES reply: ":server 353 me = #chan :nick @nick +nick", possibly several
# chunks, closed by 366. The bot gets a fresh one on every JOIN of its own, so
# the membership set reseeds itself and replay drift never accumulates.
_P_353 = re.compile(r"^:\S+ 353 \S+ [=*@] (?P<chan>#\S+) :(?P<names>.*)$")
_P_366 = re.compile(r"^:\S+ 366 \S+ (?P<chan>#\S+)")


def strip_invisible(s):
    return _INVISIBLE.sub("", s)


def strip_formatting(s):
    """mIRC colour/bold/reset codes out. Invisible chars stay: strip_invisible
    handles those, and the two are wanted in different places."""
    return _MIRC_FMT.sub("", s)


def load_config(path=CONFIG_PATH):
    cfg = json.loads(Path(path).read_text())
    # Build lowercase lookup: exact nick -> class.
    exact = {}
    for klass in ("ai", "classic", "service"):
        pass
    for n in cfg.get("ai", []):
        exact[n.lower()] = "ai"
    for n in cfg.get("classic", []):
        exact[n.lower()] = "classic"
    for n in cfg.get("services", []):
        exact[n.lower()] = "service"
    return {
        "exact": exact,
        "prefixes": {k.lower(): "ai" for k in cfg.get("class_prefixes", {}).get("ai", [])},
        "relays": {n.lower() for n in cfg.get("relays", [])},
        "aliases": {k.lower(): v for k, v in cfg.get("aliases", {}).items()},
        "exclude": {n.lower() for n in cfg.get("exclude_from_ratio", [])},
        "origins": {k.lower(): v for k, v in cfg.get("origin_classes", {}).items()},
        # Consumed by ingest_grappa: {network slug -> [channels]}. Carried
        # through as-is; that module lowercases and set-ifies it, and treats a
        # missing key as "ingest nothing" rather than "ingest everything".
        "grappa_channels": cfg.get("grappa_channels", {}),
    }


def parse_line(raw):
    """bot.log line -> dict or None. Only channel (#...) PRIVMSG/ACTION kept.

    Returns {hms:(h,m,s), direction, nick|None, target, text, is_action}.
    nick is None for outbound (caller assigns vjt-claude). Non-PRIVMSG lines,
    DMs (target not a #channel), and malformed lines return None.
    """
    m = _TS.match(raw)
    if not m:
        return None
    h, mi, s, direction, rest = int(m.group(1)), int(m.group(2)), int(m.group(3)), m.group(4), m.group(5)
    origin = None
    if direction == "<":
        pm = _IN.match(rest)
        if not pm:
            return None
        nick, target, text = pm.group("nick"), pm.group("target"), pm.group("text")
    else:
        pm = _OUT.match(rest)
        if not pm:
            return None
        nick, target, text = None, pm.group("target"), pm.group("text")
        origin = pm.group("origin")
    if not target.startswith("#"):
        return None  # DM, not a channel
    text = text.rstrip("\r\n")
    is_action = False
    if len(text) >= 2 and text[0] == "\x01" and text[-1] == "\x01":
        inner = text[1:-1]
        if inner.startswith("ACTION "):
            is_action = True
            text = inner[len("ACTION "):]
        else:
            text = inner  # other CTCP; keep body
    return {"hms": (h, mi, s), "direction": direction, "nick": nick,
            "target": target, "text": text, "is_action": is_action,
            "origin": origin}


def parse_presence(raw):
    """bot.log line -> membership-delta dict or None.

    {hms, channel, kind, nick, extra}. channel is '*' for QUIT/NICK: the IRC
    protocol carries no channel on either, so the replay resolves them against
    the membership set it is holding. Outbound lines are ignored — the server
    echoes our own JOIN/PART back to us, so they arrive as inbound anyway.
    """
    m = _TS.match(raw)
    if not m or m.group(4) != "<":
        return None
    hms = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
    rest = m.group(5).rstrip("\r\n")

    def ev(kind, chan, nick, extra=None):
        return {"hms": hms, "kind": kind, "channel": chan, "nick": nick, "extra": extra}

    pm = _P_JOIN.match(rest)
    if pm:
        return ev("join", pm.group("chan"), pm.group("nick"))
    pm = _P_PART.match(rest)
    if pm:
        return ev("part", pm.group("chan"), pm.group("nick"))
    pm = _P_QUIT.match(rest)
    if pm:
        return ev("quit", "*", pm.group("nick"))
    pm = _P_NICK.match(rest)
    if pm:
        return ev("nick", "*", pm.group("nick"), pm.group("new"))
    pm = _P_KICK.match(rest)
    if pm:
        return ev("kick", pm.group("chan"), pm.group("victim"))
    pm = _P_353.match(rest)
    if pm:
        return ev("names", pm.group("chan"), "", pm.group("names").strip())
    pm = _P_366.match(rest)
    if pm:
        return ev("names_end", pm.group("chan"), "")
    return None


def classify(cfg, nick, direction, origin=None):
    """(canon_nick, klass, transport). Applies relay unwrap upstream; here nick
    is already the effective speaker. direction '>' is always vjt-claude; the
    class is 'ai' only when nothing tagged the line — a tagged line came from a
    deterministic sidecar, not from the model, and origin_classes says which
    class it counts as (untagged tag-less logs keep the old behaviour)."""
    if direction == ">":
        return "vjt-claude", cfg["origins"].get(origin, "ai"), "irc"
    low = nick.lower()
    canon = cfg["aliases"].get(low, nick)
    low = canon.lower()
    if low in cfg["exact"]:
        return canon, cfg["exact"][low], "irc"
    for pref, klass in cfg["prefixes"].items():
        if low.startswith(pref):
            return canon, klass, "irc"
    return canon, "human", "irc"


def resolve(cfg, parsed):
    """parse_line dict -> (nick, klass, transport, is_action). Handles telegram
    relay unwrap (nick in relays -> inner human, transport=telegram)."""
    direction, nick, text = parsed["direction"], parsed["nick"], parsed["text"]
    transport = "irc"
    if direction == "<" and nick is not None and nick.lower() in cfg["relays"]:
        bm = rc.BRIDGE_PREFIX_PAT.match(strip_formatting(text).lstrip())
        if bm:
            inner = strip_invisible(bm.group(1)).strip()
            if inner:
                canon = cfg["aliases"].get(inner.lower(), inner)
                return canon, "human", "telegram", parsed["is_action"]
        # relay line that didn't unwrap: treat the relay itself as classic bot
        return nick, "classic", "irc", parsed["is_action"]
    canon, klass, transport = classify(cfg, nick if nick else "", direction,
                                       parsed.get("origin"))
    return canon, klass, transport, parsed["is_action"]


def count_blasphemy(text):
    """Blasphemy hit count reusing roll_counter's canon (open-slot form gated by
    stopwords, concat form always counts) — mirrors rc.process_blasphemy."""
    n = 0
    for mm in rc.BLASPHEMY_PAT.finditer(text):
        epi = mm.group("epi")
        if epi and epi.lower() in rc.BLASPHEMY_STOPWORDS:
            continue
        n += 1
    for _ in rc.CONCAT_PAT.finditer(text):
        n += 1
    return n


def _epoch(d, hms):
    """(date, (h,m,s)) -> UTC epoch. Rome-local after cutover, UTC before."""
    naive = datetime(d.year, d.month, d.day, hms[0], hms[1], hms[2])
    rome_ts = naive.replace(tzinfo=_TZ).timestamp()
    if rome_ts < _CUTOVER:
        # pre-cutover stamps were UTC — reinterpret in UTC.
        return datetime(d.year, d.month, d.day, hms[0], hms[1], hms[2],
                        tzinfo=ZoneInfo("UTC")).timestamp()
    return rome_ts


def backfill_file(conn, cfg, path, network):
    """Full two-pass ingest of one log file (empty-DB first run). Pass 1 counts
    midnight rollovers to anchor the last line at the file mtime date; pass 2
    assigns each line a ts and inserts channel PRIVMSG/ACTION rows. Mirrors
    roll_counter.backfill()'s date model."""
    mtime_date = datetime.fromtimestamp(os.path.getmtime(path), _TZ).date()
    prev = None
    rolls = 0
    with open(path, errors="replace") as f:
        for raw in f:
            tm = rc.LOG_TS_PAT.match(raw)
            if not tm:
                continue
            hms = (int(tm.group(1)), int(tm.group(2)), int(tm.group(3)))
            if prev is not None and hms < prev:
                rolls += 1
            prev = hms
    start_date = mtime_date - timedelta(days=rolls)
    prev = None
    day = 0
    rows = []
    prows = []
    with open(path, errors="replace") as f:
        for raw in f:
            tm = rc.LOG_TS_PAT.match(raw)
            if not tm:
                continue
            hms = (int(tm.group(1)), int(tm.group(2)), int(tm.group(3)))
            if prev is not None and hms < prev:
                day += 1
            prev = hms
            d = start_date + timedelta(days=day)
            pres = parse_presence(raw)
            if pres:
                prows.append((int(_epoch(d, pres["hms"])), network, pres["channel"],
                              pres["kind"], pres["nick"], pres["extra"]))
            parsed = parse_line(raw)
            if not parsed:
                continue
            ts = int(_epoch(d, parsed["hms"]))
            nick, klass, transport, is_action = resolve(cfg, parsed)
            if not nick:
                continue
            rows.append((ts, network, parsed["target"], nick, klass, transport,
                         1 if is_action else 0, count_blasphemy(parsed["text"])))
    conn.executemany(
        "INSERT INTO events(ts,network,channel,nick,klass,transport,is_action,bestemmie)"
        " VALUES(?,?,?,?,?,?,?,?)", rows)
    conn.executemany(
        "INSERT INTO presence(ts,network,channel,kind,nick,extra) VALUES(?,?,?,?,?,?)",
        prows)
    size = os.path.getsize(path)
    inode = os.stat(path).st_ino
    conn.execute(
        "INSERT OR REPLACE INTO ingest_state(source,offset,inode,size,prev_h,prev_m,prev_s,cur_date)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (path, size, inode, size, prev[0] if prev else None, prev[1] if prev else None,
         prev[2] if prev else None, (start_date + timedelta(days=day)).isoformat()))
    conn.commit()
    return len(rows)


def catchup_file(conn, cfg, path, network):
    """Incremental ingest: read only bytes past the stored offset, continuing the
    date walk from the persisted (prev_hms, cur_date). Rotation/truncation guard:
    inode change or size < offset → rebuild this network from scratch. Byte-exact
    (consumes only up to the last newline) so a mid-write partial line never
    double-counts or mis-parses."""
    row = conn.execute(
        "SELECT offset,inode,size,prev_h,prev_m,prev_s,cur_date FROM ingest_state WHERE source=?",
        (path,)).fetchone()
    if row is None:
        return backfill_file(conn, cfg, path, network)
    offset, _, _, ph, pm, ps, cur_date = row
    st = os.stat(path)
    if st.st_ino != row[1] or st.st_size < offset:
        conn.execute("DELETE FROM events WHERE network=?", (network,))
        conn.execute("DELETE FROM presence WHERE network=?", (network,))
        conn.execute("DELETE FROM ingest_state WHERE source=?", (path,))
        conn.commit()
        return backfill_file(conn, cfg, path, network)
    if st.st_size == offset:
        return 0
    with open(path, "rb") as fb:
        fb.seek(offset)
        blob = fb.read()
    cut = blob.rfind(b"\n")
    if cut < 0:
        return 0  # only a partial line so far; wait for the newline
    consumed = blob[:cut + 1]
    new_off = offset + len(consumed)
    prev = (ph, pm, ps) if ph is not None else None
    d = date.fromisoformat(cur_date) if cur_date else datetime.fromtimestamp(st.st_mtime, _TZ).date()
    rows = []
    prows = []
    for raw in consumed.decode("utf-8", "replace").split("\n"):
        if not raw:
            continue
        tm = rc.LOG_TS_PAT.match(raw)
        if tm:
            hms = (int(tm.group(1)), int(tm.group(2)), int(tm.group(3)))
            if prev is not None and hms < prev:
                d = d + timedelta(days=1)
            prev = hms
            pres = parse_presence(raw)
            if pres:
                prows.append((int(_epoch(d, pres["hms"])), network, pres["channel"],
                              pres["kind"], pres["nick"], pres["extra"]))
            parsed = parse_line(raw)
            if parsed:
                ts = int(_epoch(d, parsed["hms"]))
                nick, klass, transport, is_action = resolve(cfg, parsed)
                if nick:
                    rows.append((ts, network, parsed["target"], nick, klass, transport,
                                 1 if is_action else 0, count_blasphemy(parsed["text"])))
    conn.executemany(
        "INSERT INTO events(ts,network,channel,nick,klass,transport,is_action,bestemmie)"
        " VALUES(?,?,?,?,?,?,?,?)", rows)
    conn.executemany(
        "INSERT INTO presence(ts,network,channel,kind,nick,extra) VALUES(?,?,?,?,?,?)",
        prows)
    conn.execute(
        "INSERT OR REPLACE INTO ingest_state(source,offset,inode,size,prev_h,prev_m,prev_s,cur_date)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (path, new_off, st.st_ino, new_off, prev[0] if prev else None,
         prev[1] if prev else None, prev[2] if prev else None, d.isoformat()))
    conn.commit()
    return len(rows)


def open_db(db_path):
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    return conn


def summary(conn, network=None):
    """Offline sanity print — ratios, per-channel, top talkers, bestemmie."""
    where = "WHERE network=?" if network else ""
    args = (network,) if network else ()
    cur = conn.cursor()
    tot = cur.execute(f"SELECT COUNT(*) FROM events {where}", args).fetchone()[0]
    print(f"== events: {tot} ==")
    print("\nby class:")
    for k, c in cur.execute(f"SELECT klass, COUNT(*) FROM events {where} GROUP BY klass ORDER BY 2 DESC", args):
        print(f"  {k:8} {c:7}  {100*c/tot:5.1f}%")
    # ratio excludes services + excluded-from-ratio; humans vs ai / vs all-bots.
    def cnt(cond):
        return cur.execute(f"SELECT COUNT(*) FROM events {where} {'AND' if where else 'WHERE'} {cond}", args).fetchone()[0]
    human = cnt("klass='human'")
    ai = cnt("klass='ai'")
    classic = cnt("klass='classic'")
    print("\nratios (ratable = human+ai+classic, services excluded):")
    print(f"  AI vs human:        {ai} : {human}" + (f"  = {ai/human:.2f}" if human else ""))
    print(f"  all-bots vs human:  {ai+classic} : {human}" + (f"  = {(ai+classic)/human:.2f}" if human else ""))
    print("\nby transport (human msgs):")
    for t, c in cur.execute(f"SELECT transport, COUNT(*) FROM events {where} {'AND' if where else 'WHERE'} klass='human' GROUP BY transport ORDER BY 2 DESC", args):
        print(f"  {t:9} {c}")
    print("\ntop channels:")
    for ch, c in cur.execute(f"SELECT channel, COUNT(*) FROM events {where} GROUP BY channel ORDER BY 2 DESC LIMIT 12", args):
        print(f"  {ch:14} {c}")
    print("\ntop talkers (all classes):")
    for nk, c in cur.execute(f"SELECT nick, COUNT(*) FROM events {where} GROUP BY nick ORDER BY 2 DESC LIMIT 15", args):
        print(f"  {nk:20} {c}")
    print("\ntop bestemmiatori:")
    for nk, c in cur.execute(f"SELECT nick, SUM(bestemmie) s FROM events {where} GROUP BY nick HAVING s>0 ORDER BY s DESC LIMIT 12", args):
        print(f"  {nk:20} {c}")
    bt = cur.execute(f"SELECT SUM(bestemmie) FROM events {where}", args).fetchone()[0] or 0
    print(f"\ntotal bestemmie: {bt}")
    lo, hi = cur.execute(f"SELECT MIN(ts), MAX(ts) FROM events {where}", args).fetchone()
    if lo:
        print(f"span: {datetime.fromtimestamp(lo, _TZ)}  ->  {datetime.fromtimestamp(hi, _TZ)}")


def _main(argv):
    # Offline build/test entrypoint:
    #   sniffo_stats.py build <db> <logfile> <network>
    #   sniffo_stats.py summary <db> [network]
    if len(argv) >= 4 and argv[0] == "build":
        cfg = load_config()
        conn = open_db(argv[1])
        n = backfill_file(conn, cfg, argv[2], argv[3])
        print(f"ingested {n} rows from {argv[2]} ({argv[3]})")
        summary(conn, argv[3])
        return
    if len(argv) >= 2 and argv[0] == "summary":
        conn = open_db(argv[1])
        summary(conn, argv[2] if len(argv) > 2 else None)
        return
    print(__doc__)
    print("usage: sniffo_stats.py build <db> <logfile> <network> | summary <db> [network]")


if __name__ == "__main__":
    _main(sys.argv[1:])
