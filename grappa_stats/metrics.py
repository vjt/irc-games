#!/usr/bin/env python3
"""sniffo message-stats — metric aggregation (build-order step 2).

Reads the events DB (built by sniffo_stats.py) and emits one JSON per
(network, channel) plus an index.json. These feed the interactive HTML pages.
Nicks are REAL (vjt policy). Per-channel dir slug is a keyed hash so URLs are
unguessable (§5 confidentiality); the salt lives in a sibling secret file.
"""
import hashlib
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Europe/Rome")
HERE = Path(__file__).resolve().parent

# Bestemmia intensity ladder (hits per 100 messages) — vjt: "catechismo ->
# super bestemmiato". Tunable; seeded from the real per-channel spread.
BESTEMMIA_TIERS = [
    (0.5, "catechismo"),
    (2.0, "chierichetto"),
    (5.0, "peccatore"),
    (12.0, "bestemmiatore"),
    (float("inf"), "super bestemmiato"),
]


def bestemmia_tier(per100):
    for hi, name in BESTEMMIA_TIERS:
        if per100 < hi:
            return name
    return BESTEMMIA_TIERS[-1][1]


def slug_salt():
    f = HERE / "slug.salt"
    if f.exists():
        return f.read_text().strip()
    return "DEV-SALT-not-for-prod"  # deploy writes a real random salt


def channel_slug(network, channel):
    h = hashlib.sha256(f"{slug_salt()}|{network}|{channel}".encode()).hexdigest()
    return h[:16]


def _rome_parts(ts):
    dt = datetime.fromtimestamp(ts, _TZ)
    return dt.hour, dt.strftime("%Y-%m-%d")


def _excluded_channels():
    try:
        cfg = json.loads((HERE / "classes.json").read_text())
        return {c.lower() for c in cfg.get("exclude_channels", [])}
    except Exception:
        return set()


def list_channels(conn):
    ex = _excluded_channels()
    rows = conn.execute(
        "SELECT network, channel, COUNT(*) c FROM events GROUP BY network, channel ORDER BY c DESC"
    ).fetchall()
    return [r for r in rows if r[1].lower() not in ex]


PRESENCE_SAMPLE_SEC = 300       # membership snapshot every 5 minutes
DAU_MIN_MSGS = 3                # messages/day needed to count as "active" (vjt)


def presence_daily(conn, network, channel, sample_sec=PRESENCE_SAMPLE_SEC):
    """Replay the membership deltas -> {day: (median, min, max)} of humans present.

    The count is a STATE, not a sum: it is reconstructed by replaying 353/366
    (baseline, reseeded on every JOIN of the bot) plus join/part/quit/nick/kick,
    then snapshotting the set every `sample_sec`. The daily figure vjt asked for
    is the MEDIAN of that day's snapshots — a netsplit dumps ~20 sessions for a
    minute and would own any max-based bucket; min/max ride along as the band.

    Two honest limits, by construction: nothing exists before the bot first
    joined a channel, and a log gap freezes the set (samples keep being emitted
    with the last known membership).
    """
    rows = conn.execute(
        "SELECT ts, kind, nick, extra FROM presence"
        " WHERE network=? AND (channel=? OR channel='*') ORDER BY ts, id",
        (network, channel)).fetchall()
    if not rows:
        return {}
    sys.path.insert(0, str(HERE))
    import ingest  # noqa: E402  (classification canon lives there, never duplicated)
    cfg = ingest.load_config()
    klass_of = {}

    def human(nick):
        low = nick.lower()
        k = klass_of.get(low)
        if k is None:
            # Relays sit in the channel as bots. In the message pipeline they get
            # unwrapped to the human behind them, so they never surface there —
            # but a body in the member list is the relay itself, not a person.
            k = "classic" if low in cfg["relays"] else ingest.classify(cfg, nick, "<")[1]
            klass_of[low] = k
        return k == "human"

    members = set()
    pending = None          # names chunks accumulated between 353s and the 366
    seeded = False
    cur = 0
    next_ts = None
    samples = defaultdict(list)
    for ts, kind, nick, extra in rows:
        if seeded:
            while next_ts is not None and next_ts <= ts:
                samples[_rome_parts(next_ts)[1]].append(cur)
                next_ts += sample_sec
        if kind == "names":
            if pending is None:
                pending = set()
            # NAMES entries carry op/voice prefixes; the nick is what's after them.
            pending.update(n.lstrip("@+%~&") for n in (extra or "").split() if n.strip("@+%~&"))
            continue
        if kind == "names_end":
            if pending is not None:
                members = pending
                pending = None
                if not seeded:
                    seeded, next_ts = True, ts
        elif kind == "join":
            members.add(nick)
        elif kind in ("part", "kick", "quit"):
            members.discard(nick)
        elif kind == "nick":
            if nick in members:
                members.discard(nick)
                members.add(extra)
        cur = sum(1 for n in members if human(n))
    return {day: (_median(v), min(v), max(v)) for day, v in samples.items() if v}


def _median(v):
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def channel_metrics(conn, network, channel):
    rows = conn.execute(
        "SELECT ts, nick, klass, transport, bestemmie FROM events WHERE network=? AND channel=?",
        (network, channel)).fetchall()
    totals = defaultdict(int)
    transport_human = defaultdict(int)
    hourly = [0] * 24
    talkers = defaultdict(int)
    talker_klass = {}
    best_by_user = defaultdict(int)
    human_nicks = set()
    # date -> {messages,human,ai,classic,service,bestemmie,irc,telegram}
    daily = defaultdict(lambda: defaultdict(int))
    daily_humans = defaultdict(lambda: defaultdict(int))  # date -> {nick: msgs}
    daily_hourly = defaultdict(lambda: [0] * 24)  # date -> [24] hour-of-day counts
    best_total = 0
    for ts, nick, klass, transport, best in rows:
        totals[klass] += 1
        totals["messages"] += 1
        hour, day = _rome_parts(ts)
        hourly[hour] += 1
        daily_hourly[day][hour] += 1
        talkers[nick] += 1
        talker_klass[nick] = klass
        d = daily[day]
        d["messages"] += 1
        d[klass] += 1
        d["bestemmie"] += best
        if klass == "human":
            human_nicks.add(nick)
            daily_humans[day][nick] += 1
            transport_human[transport] += 1
            d[transport] += 1  # per-day human transport (irc/telegram)
        if best:
            best_by_user[nick] += best
            best_total += best

    msgs = totals["messages"] or 1
    human = totals["human"]
    ai = totals["ai"]
    classic = totals["classic"]
    per100 = 100.0 * best_total / msgs
    top_talkers = sorted(talkers.items(), key=lambda x: -x[1])[:20]
    top_best = sorted(best_by_user.items(), key=lambda x: -x[1])[:15]
    # Per-day buckets carry every ADDITIVE metric so the client can re-aggregate
    # over a zoomed [from,to] range (dynamic panels). Non-additive metrics
    # (distinct users, per-nick top-N) stay all-time — see render.py static block.
    # Presence is the one NON-additive per-day figure in the bucket: the dynamic
    # panels must never re-sum present_* over a zoomed range (they'd invent a
    # crowd). It rides here only because the chart reads the same array.
    pres = presence_daily(conn, network, channel)
    # DAU (vjt order, 2026-08-05): distinct HUMAN nicks who SPOKE that Rome-day.
    # "Active" = talked, not present — presence is the separate present_* band,
    # and a lurker in NAMES is not an active user.
    # NON-additive, exactly like presence: summing `dau` over a zoomed range
    # would invent people (the same nick talks every day and gets counted N
    # times). So each day also ships its SET as indices into the file-level
    # `dau_nick_table`, and the client re-aggregates by UNION — which is exact
    # over any range and hands over WAU/MAU + the DAU/MAU stickiness for free.
    # Threshold (vjt, 2026-08-05): one line a day is not an active user. Measured
    # on the live corpus before picking: on #sniffo the 30-day median goes
    # 14 (>=1) → 9 (>=3) → 5 (>=10); ten flattens small channels into noise,
    # three drops the drive-by "ciao" and keeps the shape of the curve.
    dau_sets = {day: {n for n, c in cnt.items() if c >= DAU_MIN_MSGS}
                for day, cnt in daily_humans.items()}
    dau_table = sorted({n for s in dau_sets.values() for n in s})
    dau_idx = {n: i for i, n in enumerate(dau_table)}
    history = [
        {"date": day,
         **{k: dd.get(k, 0) for k in
            ("messages", "human", "ai", "classic", "service", "bestemmie",
             "irc", "telegram")},
         "dau": len(dau_sets.get(day, ())),
         "dau_nicks": sorted(dau_idx[n] for n in dau_sets.get(day, ())),
         **({"present_med": pres[day][0], "present_min": pres[day][1],
             "present_max": pres[day][2]} if day in pres else {}),
         "hourly": daily_hourly[day]}
        for day, dd in sorted(daily.items())
    ]
    return {
        "network": network,
        "channel": channel,
        "slug": channel_slug(network, channel),
        "span": {"from": min((r[0] for r in rows), default=0),
                 "to": max((r[0] for r in rows), default=0)},
        "totals": {"messages": totals["messages"], "human": human, "ai": ai,
                   "classic": classic, "service": totals.get("service", 0)},
        "ratios": {
            "ai_vs_human": round(ai / human, 4) if human else None,
            "allbots_vs_human": round((ai + classic) / human, 4) if human else None,
        },
        "transport_human": dict(transport_human),
        "hourly": hourly,
        "active_users": len(human_nicks),
        # Interning table for `history[].dau_nicks` — real nicks (vjt policy),
        # indices so the per-day sets stay a few bytes each.
        "dau_nick_table": dau_table,
        "top_talkers": [{"nick": n, "klass": talker_klass[n], "count": c} for n, c in top_talkers],
        "bestemmie": {
            "total": best_total,
            "per_100_msgs": round(per100, 2),
            "tier": bestemmia_tier(per100),
            "by_user": [{"nick": n, "count": c} for n, c in top_best],
        },
        "history": history,
    }


def export(conn, outdir):
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    index = []
    for network, channel, c in list_channels(conn):
        m = channel_metrics(conn, network, channel)
        (out / f"{m['slug']}.json").write_text(json.dumps(m, ensure_ascii=False, indent=1))
        b = m["bestemmie"]
        t = m["totals"]
        index.append({
            "network": network, "channel": channel, "slug": m["slug"],
            "messages": t["messages"],
            "human_pct": round(100 * t["human"] / (t["messages"] or 1), 1),
            "ai_pct": round(100 * t["ai"] / (t["messages"] or 1), 1),
            "ai_vs_human": m["ratios"]["ai_vs_human"],
            "bestemmia_per_100": b["per_100_msgs"], "tier": b["tier"],
        })
    # NB: the aggregate index list is returned (render_all writes it to a
    # SECRET-named file); we never write a guessable "index.json" into the
    # published dir — its name would leak every channel slug.
    return index


def _main(argv):
    if len(argv) >= 2 and argv[0] == "export":
        conn = sqlite3.connect(argv[1])
        outdir = argv[2] if len(argv) > 2 else str(HERE / "out")
        idx = export(conn, outdir)
        print(f"exported {len(idx)} channels -> {outdir}")
        for r in idx:
            print(f"  {r['network']:8} {r['channel']:20} msg={r['messages']:6} "
                  f"human={r['human_pct']:5}% ai={r['ai_pct']:5}% "
                  f"best/100={r['bestemmia_per_100']:5}  {r['tier']}")
        return
    print("usage: metrics.py export <db> [outdir]")


if __name__ == "__main__":
    _main(sys.argv[1:])
