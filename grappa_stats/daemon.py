#!/usr/bin/env python3
"""sniffo message-stats daemon (build-order step 5).

Every INTERVAL seconds:
  1. incremental catch-up ingest of bot.log (azzurra) + bot.libera.log (libera)
     into SQLite (first run backfills the whole backlog),
  2. re-render all per-(network,channel) pages + the private index straight into
     the pi5 Syncthing folder (/srv/www-static/t/.x/<hashed>/), which mirrors to
     the m42 web root.

There is no staging copy and no rsync: the renderer writes where the files are
served from. The old stage-then-rsync step bought nothing (nothing private is
ever generated here — metrics.export writes only secret-named <slug>.json, never
a guessable index.json) and cost a second copy of every page, plus a --delete
that made the daemon the sole authority over a directory other producers may
legitimately write into. Removing a channel now leaves its page behind; deleting
it is a manual, deliberate act.

The quote-archive page is no longer built here. It used to be a probe piped over
ssh into the zukker0 jail plus a render on this side; that archive lives in the
jail's MySQL, so both halves now run there (``zukker0.quotestats``, hourly from
cron) and write their own directory in the web root. This daemon owns channel
stats and nothing else.

Transport is Syncthing, not rsync-over-ssh: the pi5<->m42 folder previously
"wasn't flowing" only because m42's .stignore ignored /t/.x — once that line was
removed the hidden dir syncs both ways. Pages fetch their data with a cache-busted
query, so the zone-wide CF edge cache never serves a stale snapshot and no
per-cycle purge is needed.
"""
import hashlib
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import ingest      # noqa: E402
import render      # noqa: E402

REPO = "/home/vjt/code/IRC/vjt-claude"
DB = os.environ.get("SNIFFO_STATS_DB", str(HERE / "stats.db"))
SALT = (HERE / "slug.salt").read_text().strip()
PARENT = "st-" + hashlib.sha256((SALT + "|PARENT").encode()).hexdigest()[:12]
# The renderer's output dir IS the published dir: on the Pi that was the
# Syncthing folder that mirrors to m42; in the `grappa-new` jail it is the
# shared directory nginx aliases straight out of the jail. Either way nothing
# is staged and nothing is copied afterwards.
OUTDIR = os.environ.get("SNIFFO_STATS_OUT", f"/srv/www-static/t/.x/{PARENT}")
# Source selection. 'grappa' reads vjt's own message archive out of the grappa
# server's SQLite (see ingest_grappa); 'log' is the original bot.log pair, kept
# because the Pi can still run it and because it is the thing the grappa source
# was checked against (agreement within 0.25% over August 2026, four channels).
SOURCE = os.environ.get("SNIFFO_STATS_SOURCE", "log")
SOURCES = [(f"{REPO}/bot.log", "azzurra"), (f"{REPO}/bot.libera.log", "libera")]
INTERVAL = int(os.environ.get("SNIFFO_STATS_INTERVAL", "300"))


def log(*a):
    print(time.strftime("%Y-%m-%d %H:%M:%S"), *a, flush=True)


def cycle():
    cfg = ingest.load_config()          # reloaded each cycle → live-editable classes.json
    conn = ingest.open_db(DB)
    new = 0
    if SOURCE == "grappa":
        import ingest_grappa           # noqa: E402  (only when that source is chosen)
        try:
            new += ingest_grappa.cycle(conn, cfg)
        except Exception as e:
            log("INGEST ERROR grappa:", repr(e))
    else:
        for path, net in SOURCES:
            if os.path.exists(path):
                try:
                    new += ingest.catchup_file(conn, cfg, path, net)
                except Exception as e:
                    log("INGEST ERROR", path, repr(e))
    idx, islug = render.render_all(conn, OUTDIR)
    conn.close()
    return new, len(idx), islug


def main():
    log(f"sniffo-stats daemon start · parent={PARENT} · interval={INTERVAL}s · db={DB}")
    log(f"index url: https://sindro.me/t/.x/{PARENT}/{render.index_slug_for()}.html")
    while True:
        t0 = time.time()
        try:
            new, nchan, islug = cycle()
            log(f"cycle ok: +{new} rows, {nchan} channels, {time.time()-t0:.1f}s")
        except Exception as e:
            log("CYCLE FAILED:", repr(e))
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
