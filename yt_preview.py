#!/usr/bin/env python3
"""yt_preview — the old TOOLS YouTube line, behind Ambrogio.

vjt order (#sbiffo, 2026-09-13 11:47): "nuova feature per ambrogio: pastando un
link youtube mostra questo formato ma senza lo short link", with his own sample
of what the ancient `TOOLS` bot used to print:

    ►  YouTube  :: San Culamo - ... - Rutto attack :: Time: 1:55 :: Visual: 1.754 :: Like: 18

and, two lines later: "e poi invece se dai !yt bla bla bla / cerca le keyword su
yt e ritorna il primo risultato".

So two triggers, one renderer:

  * a YouTube link pasted in channel  -> the line WITHOUT the link (it is
    already on screen, one line above);
  * `!yt <keywords>`                  -> first search hit, WITH the link,
    because there is nothing on screen to click otherwise.

Colours are mIRC, from the same order: the arrow white on red, the word
YouTube black on white, the rest plain. Written as real 0x03 bytes here —
never copy them back out of bot.log, the log strips them (see the bot repo's
`feedback_irc_color_byte_lost_in_log`).

Two sources, in this order.

  * The **Data API v3** when a key is configured — vjt's call (#sbiffo 11:57,
    "no usiamo l'api key, poi la registro"), because the page route does not
    survive the jail: from convento's IP YouTube answers `LOGIN_REQUIRED`
    ("Accedi per confermare di non essere un bot") to the watch page, to
    innertube on WEB/MWEB/ANDROID/IOS/TVHTML5, and to the public Piped and
    Invidious mirrors. Only oembed replies there, and it carries the title
    alone. The API does not care about the IP: the key is the identity.
  * The **watch page**, scraped, as the fallback for a host with no key — it
    works from a residential address and it is what built this line first.
    `ytInitialPlayerResponse.videoDetails` has title/lengthSeconds/viewCount,
    `"likeCount"` sits a few KB further down, and the search page carries
    `videoRenderer` entries.

Both degrade field by field (a missing one prints `-`); a source we cannot read
at all prints nothing rather than a wrong line.

Shape is the house sidecar shape (chanstats_sidecar.py, next_counter.py): tail
bot.log, match, write verbs into the bot.send FIFO, non-blocking, so a dead bot
costs a skipped line and not a wedged daemon.

Usage:
  yt_preview.py                      # daemon (tail bot.log)
  yt_preview.py --once <url|id>      # print the line, send nothing
  yt_preview.py --search <keywords>  # print the !yt line, send nothing

Env:
  YT_LOG           bot.log to tail       (default: the vjt-claude repo one)
  YT_FIFO          bot.send to write     ('' disables talking — dry runs mute)
  YT_CHANS         comma separated list  (default: #sniffo,#sbiffo)
  YT_API_KEY       Data API v3 key, or
  YT_API_KEY_FILE  a file holding it (default /usr/local/etc/convento/yt_api_key)
"""

import json
import os
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path("/home/vjt/code/IRC/vjt-claude")
LOG = os.environ.get("YT_LOG", str(ROOT / "bot.log"))
FIFO = os.environ.get("YT_FIFO", str(ROOT / "bot.send"))
CHANNELS = {c.strip() for c in os.environ.get(
    "YT_CHANS", "#sniffo,#sbiffo").split(",") if c.strip()}

# Data API v3 key. vjt's call (#sbiffo 2026-09-13 11:57, "no usiamo l'api key,
# poi la registro") after the scraper turned out to be unusable from the jail:
# YouTube answers LOGIN_REQUIRED ("prove you are not a bot") to convento's IP on
# the watch page, on innertube for every client, and on the public Piped and
# Invidious mirrors. The API answers an IP it does not like just fine, because
# the key is the identity.
#
# Quota, so nobody is surprised: 10.000 units/day. `videos.list` costs 1, so a
# pasted link is free in practice; `search.list` costs 100, so `!yt` has room
# for ~100 searches a day. The scraper below stays as the fallback for a host
# that has no key.
API_KEY = os.environ.get("YT_API_KEY", "")
if not API_KEY:
    for _p in (os.environ.get("YT_API_KEY_FILE"),
               "/usr/local/etc/convento/yt_api_key",
               str(Path.home() / ".config" / "youtube-api-key")):
        try:
            API_KEY = Path(_p).read_text().strip()
            break
        except (OSError, TypeError):
            continue
API = "https://www.googleapis.com/youtube/v3"

# One preview per channel per this many seconds, and never the same video twice
# inside it: a link pasted three times in a row is one line, not three.
COOLDOWN_SEC = int(os.environ.get("YT_COOLDOWN", "10"))
REPEAT_SEC = int(os.environ.get("YT_REPEAT", "600"))
HTTP_TIMEOUT = 12
# The watch page is ~1.1 MB and everything we read sits in the first chunk of
# it; the cap is a seatbelt against a redirect to something enormous.
MAX_BYTES = 3 * 1024 * 1024

# Desktop UA on purpose: the mobile/none-UA page serves a different shell with
# no ytInitialPlayerResponse. Italian, so the like-count fallback string is the
# one we know how to read.
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
HEADERS = {"User-Agent": UA, "Accept-Language": "it-IT,it;q=0.9,en;q=0.6"}

PRIVMSG_PAT = re.compile(
    r'< :(?P<nick>[^!@\s]+)!\S+\s+PRIVMSG\s+(?P<chan>#\S+)\s+:(?P<text>.*?)$')
CMD_PAT = re.compile(r'^\s*!yt\s+(?P<q>\S.*?)\s*$', re.I)
# youtu.be/<id>, /watch?v=<id>, /shorts/<id>, /embed/<id>, /live/<id>.
LINK_PAT = re.compile(
    r'https?://(?:[\w-]+\.)*(?:youtube\.com|youtube-nocookie\.com|youtu\.be)/'
    r'(?:watch\?(?:\S*?&)?v=|shorts/|embed/|live/|v/)?'
    r'(?P<id>[\w-]{11})\b', re.I)

# mIRC colours: 00 white, 01 black, 04 red. Real 0x03 bytes, 0x0f resets.
C = "\x03"
RESET = "\x0f"
BADGE = f"{C}00,04 ► {RESET}{C}01,00 YouTube {RESET}"


def fetch(url):
    req = urllib.request.Request(url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return r.read(MAX_BYTES).decode("utf-8", "replace")


def _first_json(html, marker, want=None):
    """The JSON object ASSIGNED to `marker`, brace-matched.

    Two traps, both met on the real page:

      * a non-greedy regex to the first `};` is wrong — the blobs contain `};`
        inside strings — so we count braces and skip string literals;
      * the name appears several times before the assignment does (measured
        from the convento jail: first hit at 708 KB, the object with
        `videoDetails` only at 1136 KB). So we require `= {` right after the
        name, and with `want` we keep walking until an object that carries
        that key — the page shape differs per datacenter/IP.
    """
    for m in re.finditer(re.escape(marker) + r"\s*=\s*(?=\{)", html):
        obj = _brace_match(html, m.end())
        if obj is None:
            continue
        if want is None or want in obj:
            return obj
    return None


def _brace_match(html, i):
    depth, in_str, esc = 0, False, False
    for j in range(i, len(html)):
        ch = html[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(html[i:j + 1])
                except ValueError:
                    return None
    return None


def _likes(html):
    """Likes, in order of how much we trust the source."""
    m = re.search(r'"likeCount"\s*:\s*"(\d+)"', html)
    if m:
        return int(m.group(1))
    # The A11Y label, Italian page: "...come hanno fatto 1.754 altre persone".
    m = re.search(r'"accessibilityText"\s*:\s*"[^"]*?Mi piace[^"]*?'
                  r'([\d][\d. ,]*)\s*altre persone', html)
    if not m:
        m = re.search(r'"accessibilityText"\s*:\s*"([\d][\d. ,]*)\s*likes?"',
                      html)
    if m:
        digits = re.sub(r"\D", "", m.group(1))
        return int(digits) if digits else None
    return None


ISO_DUR_PAT = re.compile(
    r'P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?\Z')


def iso_seconds(s):
    """`PT1M55S` -> 115. The API speaks ISO 8601, the line speaks m:ss."""
    m = ISO_DUR_PAT.match(s or "")
    if not m:
        return None
    d, h, mi, sec = (int(x or 0) for x in m.groups())
    return ((d * 24 + h) * 60 + mi) * 60 + sec


def api(path, **params):
    params["key"] = API_KEY
    url = f"{API}/{path}?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.loads(r.read(MAX_BYTES))


def api_video_info(video_id):
    items = api("videos", part="snippet,contentDetails,statistics",
                id=video_id).get("items") or []
    if not items:
        return None
    it = items[0]
    stats = it.get("statistics") or {}

    def num(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    return {
        "id": video_id,
        "title": " ".join((it["snippet"]["title"]).split()),
        "seconds": iso_seconds((it.get("contentDetails") or {}).get("duration")),
        "views": num(stats.get("viewCount")),
        # Absent, not zero, when the uploader hides likes — `-` says so.
        "likes": num(stats.get("likeCount")),
    }


def video_info(video_id):
    """title/seconds/views/likes for one id, or None if nothing is readable.

    Every field but the title degrades on its own: a line with `Visual: -` is
    still useful, a line with a wrong number is not.
    """
    if API_KEY:
        try:
            info = api_video_info(video_id)
            if info:
                return info
        except Exception:
            pass  # key revoked, quota gone, API down: try the page.
    html = fetch(f"https://www.youtube.com/watch?v={video_id}&hl=it")
    data = _first_json(html, "ytInitialPlayerResponse", want="videoDetails")
    details = (data or {}).get("videoDetails") or {}
    title = details.get("title")
    if not title:
        return None
    def num(v):
        try:
            return int(v)
        except (TypeError, ValueError):
            return None
    return {
        "id": video_id,
        "title": " ".join(title.split()),
        "seconds": num(details.get("lengthSeconds")),
        "views": num(details.get("viewCount")),
        "likes": _likes(html),
    }


def search_first(query):
    """First video hit for `query`. Playlists/channels/ads are not videos."""
    if API_KEY:
        try:
            items = api("search", part="id", q=query, type="video",
                        maxResults=1).get("items") or []
            if items:
                return items[0]["id"]["videoId"]
        except Exception:
            pass  # same reasoning as video_info: fall through to the page.
    url = ("https://www.youtube.com/results?hl=it&search_query="
           + urllib.parse.quote_plus(query))
    data = _first_json(fetch(url), "ytInitialData", want="contents")
    if not data:
        return None

    def walk(o):
        if isinstance(o, dict):
            if "videoRenderer" in o:
                yield o["videoRenderer"]
            for v in o.values():
                yield from walk(v)
        elif isinstance(o, list):
            for v in o:
                yield from walk(v)

    for v in walk(data):
        vid = v.get("videoId")
        if vid:
            return vid
    return None


def fmt_time(seconds):
    if not seconds:
        return "-"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def fmt_num(n):
    # Italian thousands separator, like vjt's sample ("Visual: 1.754").
    return "-" if n is None else f"{n:,}".replace(",", ".")


def render(info, link=False):
    line = (f"{BADGE} :: {info['title']} :: Time: {fmt_time(info['seconds'])} "
            f":: Visual: {fmt_num(info['views'])} :: Like: {fmt_num(info['likes'])}")
    if link:
        line += f" :: Link: https://youtu.be/{info['id']}"
    return line


def say(chan, text):
    """One line to the bot FIFO. Non-blocking open: no reader means ENXIO and a
    skipped line, not a daemon wedged forever on a dead bot."""
    if not FIFO:
        return
    try:
        fd = os.open(FIFO, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return
    try:
        # SAY:yt — origin tag, log-only; see bot.py process_cmd.
        os.write(fd, f"SAY:yt {chan} {text}\n".encode())
    except OSError:
        pass
    finally:
        os.close(fd)


def daemon():
    last_chan = {}
    last_video = {}
    p = subprocess.Popen(["tail", "-F", "-n", "0", LOG],
                         stdout=subprocess.PIPE, text=True, errors="replace")
    for line in p.stdout:
        m = PRIVMSG_PAT.search(line)
        if not m:
            continue
        chan = m.group("chan")
        if chan not in CHANNELS:
            continue
        text = m.group("text")

        cmd = CMD_PAT.match(text)
        video_id, with_link = None, False
        if cmd:
            with_link = True
            try:
                video_id = search_first(cmd.group("q"))
            except Exception:
                video_id = None
            if not video_id:
                say(chan, f"{BADGE} :: niente, su quelle parole YouTube non "
                          f"tira fuori un video.")
                continue
        else:
            hit = LINK_PAT.search(text)
            if not hit:
                continue
            video_id = hit.group("id")

        now = time.time()
        # The explicit command outranks the cooldown — someone asked for it by
        # name; the rate limit is there for link floods, not for commands.
        if not cmd:
            if now - last_chan.get(chan, 0) < COOLDOWN_SEC:
                continue
            if now - last_video.get((chan, video_id), 0) < REPEAT_SEC:
                continue
        last_chan[chan] = now
        last_video[(chan, video_id)] = now

        try:
            info = video_info(video_id)
        except Exception:
            info = None
        if info:
            say(chan, render(info, link=with_link))


def main():
    argv = sys.argv[1:]
    if argv[:1] == ["--once"] and len(argv) > 1:
        arg = argv[1]
        hit = LINK_PAT.search(arg)
        vid = hit.group("id") if hit else arg
        info = video_info(vid)
        print(render(info) if info else "unparseable")
        return 0 if info else 1
    if argv[:1] == ["--search"] and len(argv) > 1:
        vid = search_first(" ".join(argv[1:]))
        if not vid:
            print("no hit")
            return 1
        info = video_info(vid)
        print(render(info, link=True) if info else "unparseable")
        return 0 if info else 1
    daemon()
    return 0


if __name__ == "__main__":
    sys.exit(main())
