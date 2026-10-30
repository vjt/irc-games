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

Any OTHER link gets the page title and description (vjt, #sbiffo 2026-10-24
21:47: "il fetch di title e meta description o og:description per i link
pubblicati? in modo safe, no js at all"; 21:48 on redirects: "followarli ma
con un limite", "3 o 4"). Safe means, see `page_fetch`:

  * http/https on 80/443 only, and every address the name resolves to must
    be global — no loopback, RFC1918, link-local (cloud metadata), ULA;
  * the socket connects to the address we checked, not to a second lookup,
    so a name that resolves public then private (DNS rebinding) gets nowhere;
  * at most MAX_REDIRECTS hops, each re-checked from scratch;
  * text/html only, LINK_MAX_BYTES read, one wall-clock deadline overall;
  * the HTML is parsed, never run, and every control byte is stripped from
    what goes back out, so a title cannot smuggle a newline into the FIFO.

Shape is the house sidecar shape (chanstats_sidecar.py, next_counter.py): tail
bot.log, match, write verbs into the bot.send FIFO, non-blocking, so a dead bot
costs a skipped line and not a wedged daemon.

Usage:
  yt_preview.py                      # daemon (tail bot.log)
  yt_preview.py --once <url|id>      # print the line, send nothing
  yt_preview.py --search <keywords>  # print the !yt line, send nothing
  yt_preview.py --link <url>         # print a generic link's line, send nothing
  yt_preview.py --ip <address>       # print the !ip line, send nothing

Env:
  YT_LOG           bot.log to tail       (default: the vjt-claude repo one)
  YT_FIFO          bot.send to write     ('' disables talking — dry runs mute)
  YT_CHANS         comma separated list  (default: #sniffo,#sbiffo)
  YT_API_KEY       Data API v3 key, or
  YT_API_KEY_FILE  a file holding it (default /usr/local/etc/convento/yt_api_key)
"""

import html.parser
import http.client
import ipaddress
import json
import os
import random
import re
import socket
import ssl
import subprocess
import sys
import threading
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
IP_CMD_PAT = re.compile(r'^\s*!ip\s+(?P<ip>\S+)\s*$', re.I)
# youtu.be/<id>, /watch?v=<id>, /shorts/<id>, /embed/<id>, /live/<id>.
LINK_PAT = re.compile(
    r'https?://(?:[\w-]+\.)*(?:youtube\.com|youtube-nocookie\.com|youtu\.be)/'
    r'(?:watch\?(?:\S*?&)?v=|shorts/|embed/|live/|v/)?'
    r'(?P<id>[\w-]{11})\b', re.I)

# mIRC colours: 00 white, 01 black, 04 red. Real 0x03 bytes, 0x0f resets.
C = "\x03"
RESET = "\x0f"
BADGE = f"{C}00,04 ► {RESET}{C}01,00 YouTube {RESET}"
LINK_BADGE = f"{C}00,02 🌐 {RESET}"

# Any link at all; trailing punctuation is the sentence's, not the URL's.
ANY_LINK_PAT = re.compile(r'https?://[^\s<>"\x00-\x1f]+', re.I)
TRAILING = ".,;:!?)]}'»\""
MAX_REDIRECTS = 4
LINK_PORTS = {"http": 80, "https": 443}
LINK_TIMEOUT = 6          # per socket operation
LINK_DEADLINE = 10        # the whole thing, redirects included
LINK_MAX_BYTES = 256 * 1024
# Hosts that give the og: metas only to link-preview crawlers: with a browser
# UA instagram.com serves an empty shell whose only text is <title>Instagram,
# reddit.com one whose title is "Reddit". Each wants its own crawler: reddit
# answers 429 to facebookexternalhit (measured from m42, 2026-10-26).
CRAWLER_UA = {"instagram.com": "facebookexternalhit/1.1",
              "reddit.com": "Twitterbot/1.0"}


def crawler_ua(host):
    """The crawler UA `host` wants, or None for the browser one."""
    host = host.lower()
    for h, ua in CRAWLER_UA.items():
        if host == h or host.endswith("." + h):
            return ua
    return None
TITLE_MAX = 120
DESC_MAX = 220
CONTROL_PAT = re.compile(r'[\x00-\x1f\x7f-\x9f]')

# "Ambrogio, avverto un leggero languorino" (spot Ferrero Rocher, anni '90):
# la signora lo dice, Ambrogio provvede. vjt, #sbiffo 2026-10-24 21:57.
LANGUORINO_PAT = re.compile(r'\blanguorin[oi]\b', re.I)
LANGUORINO_SEC = 5
LANGUORINO = [
    "Ci pensa Ambrogio.",
    "Ambrogio porge il vassoio d'argento. Sopra, la piramide dorata.",
    "Un leggero languorino, signora? Ho già fermato la Rolls in doppia fila "
    "davanti alla pasticceria.",
    "Signora, i Rocher sono in macchina da stamattina, a temperatura di "
    "caviale.",
    "Quarantotto Rocher in piramide, signora: carta dorata coordinata al "
    "suo tailleur giallo.",
    "Mi permetto di suggerire un Rocher, signora. Anzi, trentadue.",
    "Il languorino della signora è un ordine, per Ambrogio.",
    "Ho lucidato ogni incarto col vapore delle mie lacrime di gioia, signora.",
    "All'ambasciata hanno finito i Rocher, signora. I miei no.",
    "Signora, ho fatto svuotare il guardaroba: adesso lì ci sono solo Rocher.",
    "Avverto anch'io qualcosa, signora: è il suo languorino che riempie "
    "l'abitacolo.",
    "Ambrogio si inginocchia sul tappeto persiano e apre lo scrigno. Dentro, "
    "naturalmente, Rocher.",
]

# E se la signora il Rocher lo nomina e basta, Ambrogio c'e' lo stesso: battute
# sue, non quelle del languorino. vjt, #sbiffo 2026-10-30 15:02.
ROCHER_PAT = re.compile(r'\brocher\b', re.I)
ROCHER = [
    "Rocher, signora? Ambrogio l'ha già scartato, senza toccarlo.",
    "La piramide è pronta, signora: trentasei piani, ascensore escluso.",
    "Signora, il Rocher non si chiede. Si riceve.",
    "Ho fatto rifare gli incarti in oro zecchino, signora. Quelli di serie "
    "erano un filo pacchiani.",
    "Rocher alla destra della signora, nocciola intera rivolta a nord-est.",
    "Ambrogio ne tiene sempre uno nel guanto sinistro, signora. Per le emergenze.",
    "Il Rocher è servito, signora. All'ambasciatore uno solo, come concordato.",
    "Signora, li ho contati due volte e ne manca uno: l'ho assaggiato per "
    "sicurezza.",
    "Ambrogio dispone i Rocher sul cruscotto della Rolls, in ordine di luccichio.",
    "Rocher in arrivo, signora: il pirottino l'ho stirato io.",
    "La signora ha detto Rocher. Ambrogio ha già inchiodato in mezzo alla "
    "tangenziale.",
    "Lo scarto io, signora, o preferisce sentire il fruscio?",
]


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


class LinkRefused(Exception):
    """The link is not one we fetch: wrong scheme/port, private address,
    too many redirects, not HTML, out of time."""


def public_addresses(host, port):
    """Every address `host` resolves to, if ALL of them are global.

    All, not any: a name with one public and one private record is refused,
    or the connect would be a coin toss on which one it picks."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError):
        raise LinkRefused("unresolvable")
    addrs = []
    for *_, sockaddr in infos:
        ip = ipaddress.ip_address(sockaddr[0].split("%")[0])
        if getattr(ip, "ipv4_mapped", None):
            ip = ip.ipv4_mapped
        if not ip.is_global or ip.is_multicast:
            raise LinkRefused(f"not global: {ip}")
        addrs.append(str(ip))
    if not addrs:
        raise LinkRefused("no address")
    return addrs


class _PinnedHTTP(http.client.HTTPConnection):
    """Connects to the address we vetted, whatever DNS says a second time."""

    def __init__(self, host, ip, port, timeout):
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self):
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, ip, port, timeout):
        super().__init__(host, port, timeout=timeout,
                         context=ssl.create_default_context())
        self._ip = ip

    def connect(self):
        sock = socket.create_connection((self._ip, self.port), self.timeout)
        # SNI and certificate checked against the NAME, socket on the IP.
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def page_fetch(url, resolve=public_addresses):
    """(final_url, html) for a public text/html page, or LinkRefused.

    `resolve` is the vetting step, injectable so the tests can point it at a
    local server without teaching the real one to trust loopback."""
    deadline = time.monotonic() + LINK_DEADLINE
    for _ in range(MAX_REDIRECTS + 1):
        u = urllib.parse.urlsplit(url)
        scheme = (u.scheme or "").lower()
        if scheme not in LINK_PORTS or not u.hostname:
            raise LinkRefused(f"scheme {scheme!r}")
        port = u.port or LINK_PORTS[scheme]
        if port != LINK_PORTS[scheme]:
            raise LinkRefused(f"port {port}")
        ip = resolve(u.hostname, port)[0]
        cls = _PinnedHTTPS if scheme == "https" else _PinnedHTTP
        conn = cls(u.hostname, ip, port, LINK_TIMEOUT)
        try:
            path = (u.path or "/") + (f"?{u.query}" if u.query else "")
            crawler = crawler_ua(u.hostname)
            conn.request("GET", path, headers={
                **HEADERS, "Accept": "text/html,application/xhtml+xml",
                **({"User-Agent": crawler} if crawler else {}),
                "Accept-Encoding": "identity", "Connection": "close"})
            r = conn.getresponse()
            if r.status in (301, 302, 303, 307, 308):
                loc = r.getheader("Location")
                if not loc:
                    raise LinkRefused("redirect without Location")
                url = urllib.parse.urljoin(url, loc.strip())
                continue
            if r.status != 200:
                raise LinkRefused(f"HTTP {r.status}")
            ctype = (r.getheader("Content-Type") or "").lower()
            if "text/html" not in ctype and "xhtml" not in ctype:
                raise LinkRefused(f"type {ctype!r}")
            body = b""
            while len(body) < LINK_MAX_BYTES:
                if time.monotonic() > deadline:
                    break  # what we have is usually the <head>, good enough
                chunk = r.read(min(16384, LINK_MAX_BYTES - len(body)))
                if not chunk:
                    break
                body += chunk
            m = re.search(r'charset=["\']?([\w-]+)', ctype) or re.search(
                rb'<meta[^>]+charset=["\']?([\w-]+)', body[:4096], re.I)
            charset = m.group(1) if m else "utf-8"
            if isinstance(charset, bytes):
                charset = charset.decode("ascii", "replace")
            try:
                return url, body.decode(charset, "replace")
            except LookupError:
                return url, body.decode("utf-8", "replace")
        finally:
            conn.close()
        if time.monotonic() > deadline:
            raise LinkRefused("deadline")
    raise LinkRefused("too many redirects")


class _HeadParser(html.parser.HTMLParser):
    """<title> and the description metas. Parsed, never executed."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.meta = {}
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        if tag == "title":
            self._in_title = True
        elif tag == "meta":
            a = dict(attrs)
            key = (a.get("property") or a.get("name") or "").lower()
            if key and a.get("content") and key not in self.meta:
                self.meta[key] = a["content"]

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def clean(s, limit):
    """One line, no control bytes (CR/LF, mIRC colours), at most `limit`."""
    s = " ".join(CONTROL_PAT.sub(" ", s or "").split())
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def page_info(page):
    p = _HeadParser()
    try:
        p.feed(page)
    except Exception:
        pass  # half a page parses into half a result, which is fine
    title = clean(p.meta.get("og:title") or p.title, TITLE_MAX)
    desc = clean(p.meta.get("og:description") or p.meta.get("description")
                 or p.meta.get("twitter:description"), DESC_MAX)
    return title, desc


# Instagram's og:description of a post or reel: counts, author, date, caption,
# where og:title repeats the caption. The hashtags are noise on IRC.
IG_DESC_PAT = re.compile(
    r'^(?P<likes>[\d.,]+[KM]?) likes?, (?P<comments>[\d.,]+[KM]?) comments?'
    r' - (?P<author>\S+) \S+ [^:]+: [\"“](?P<caption>.*)[\"”]\.?\s*$',
    re.S)
HASHTAG_PAT = re.compile(r'#\w+')


def instagram_info(page):
    """(title, desc) for an Instagram post, or None when it is not one."""
    p = _HeadParser()
    try:
        p.feed(page)
    except Exception:
        pass
    m = IG_DESC_PAT.match(p.meta.get("og:description") or "")
    if not m:
        return None
    count = lambda s: s.replace(",", ".")
    title = clean(f"@{m['author']} · {count(m['likes'])} like · "
                  f"{count(m['comments'])} commenti", TITLE_MAX)
    caption = clean(HASHTAG_PAT.sub(" ", m["caption"]), DESC_MAX)
    return title, caption


def link_preview(url, resolve=public_addresses):
    """The preview line for a generic link, or None."""
    final, page = page_fetch(url, resolve=resolve)
    host = (urllib.parse.urlsplit(final).hostname or "").lower()
    ig = host == "instagram.com" or host.endswith(".instagram.com")
    title, desc = (ig and instagram_info(page)) or page_info(page)
    if not title and not desc:
        return None
    host = clean(host, 60)
    if host.startswith("www."):
        host = host[4:]
    parts = [f"{LINK_BADGE}{host}"] + [x for x in (title, desc) if x]
    return " :: ".join(parts)

_languorino_deck = []
_rocher_deck = []


# !ip <address>: reverse DNS plus the origin AS from Team Cymru's whois
# (one TCP line, no key, no dependency), plus geo from ip-api.com. A hostname
# is resolved (first IPv4 + first IPv6) and each address gets its line.
IP_BADGE = f"{C}00,03 IP {RESET}"
IP_SEC = 3
# Another bot already answering !ip (TOOLS on IRCnet): where it sits, Ambrogio
# keeps quiet. Presence is followed from the log (NAMES/JOIN/PART/KICK/QUIT/NICK).
IP_DEFER_TO = os.environ.get("YT_IP_DEFER_TO", "TOOLS").lower()
GEO_URL = ("http://ip-api.com/json/{ip}?fields=status,country,"
           "regionName,city,timezone,isp,mobile,proxy,hosting")


def reverse_dns(ip, timeout=3):
    out = []
    t = threading.Thread(target=lambda: out.append(_ptr(ip)), daemon=True)
    t.start()
    t.join(timeout)
    return out[0] if out else None


def _ptr(ip):
    try:
        return socket.gethostbyaddr(ip)[0]
    except OSError:
        return None


def cymru(ip, timeout=5):
    """[AS, IP, prefix, CC, registry, allocated, AS name] or None."""
    with socket.create_connection(("whois.cymru.com", 43), timeout) as s:
        s.sendall(f" -v {ip}\r\n".encode())
        buf = b""
        while chunk := s.recv(4096):
            buf += chunk
            if len(buf) > 16384:
                break
    rows = [r for r in buf.decode(errors="replace").splitlines()
            if "|" in r and not r.startswith("AS ")]
    if not rows:
        return None
    cols = [c.strip() for c in rows[0].split("|")]
    return cols if len(cols) >= 7 and cols[0] not in ("", "NA") else None


def geo(ip, timeout=4):
    """ip-api.com (free tier: plain HTTP, 45 req/min) or None."""
    req = urllib.request.Request(GEO_URL.format(ip=ip), headers=HEADERS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read(65536))
    return d if d.get("status") == "success" else None


def yes_no(v):
    return "sì" if v else "no"


HOST_PAT = re.compile(r'^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)'
                      r'(?:\.(?!-)[a-z0-9-]{1,63}(?<!-))+\.?$', re.I)


def resolve(host, timeout=3):
    """First IPv4 and first IPv6 of `host`, at most two, [] if none."""
    out = []

    def lookup():
        try:
            out.extend(socket.getaddrinfo(host, None, type=socket.SOCK_STREAM))
        except OSError:
            pass

    t = threading.Thread(target=lookup, daemon=True)
    t.start()
    t.join(timeout)
    picked = {}
    for family, *_, addr in list(out):
        picked.setdefault(family, addr[0])
    return [picked[f] for f in (socket.AF_INET, socket.AF_INET6) if f in picked]


def ip_lines(arg):
    """!ip answer, one line per address: a literal IP, or a hostname's
    first IPv4 and first IPv6."""
    arg = arg.strip("[]")
    try:
        ipaddress.ip_address(arg)
        return [ip_info(arg)]
    except ValueError:
        pass
    if not HOST_PAT.match(arg):
        return [f"{IP_BADGE} :: «{clean(arg, 60)}» non è né un IP né un hostname."]
    addrs = resolve(arg)
    if not addrs:
        return [f"{IP_BADGE} {clean(arg, 80)} :: non risolve."]
    return [ip_info(a, host=arg) for a in addrs]


def ip_info(arg, host=None):
    ip = ipaddress.ip_address(arg)
    if host:
        return ip_info(arg).replace(f"{IP_BADGE} ", f"{IP_BADGE} {clean(host, 80)} = ", 1)
    if not ip.is_global:
        return f"{IP_BADGE} {ip} :: indirizzo privato o riservato, niente AS."
    parts = [str(ip)]
    ptr = reverse_dns(str(ip))
    parts.append(f"→ {clean(ptr, 120)}" if ptr else "→ niente reverse")
    try:
        row = cymru(str(ip))
    except OSError:
        row = None
    if row:
        asn, _, prefix, cc, reg, alloc, name = row[:7]
        parts.append(f":: AS{clean(asn, 20)} {clean(name, 80)}")
        parts.append(f":: {clean(prefix, 50)} {clean(cc, 4)} "
                     f"{clean(reg, 12)} {clean(alloc, 12)}".rstrip())
    else:
        parts.append(":: AS sconosciuto")
    try:
        g = geo(str(ip))
    except (OSError, ValueError):
        g = None
    if g:
        where = ", ".join(clean(g.get(k), 40) for k in
                          ("city", "regionName", "country") if g.get(k))
        tz = clean(g.get("timezone"), 40)
        parts.append(f":: {where}" + (f" ({tz})" if tz else ""))
        if g.get("isp"):
            parts.append(f":: ISP {clean(g['isp'], 60)}")
        parts.append(f":: proxy {yes_no(g.get('proxy'))}, mobile "
                     f"{yes_no(g.get('mobile'))}, hosting {yes_no(g.get('hosting'))}")
    return f"{IP_BADGE} " + " ".join(parts)


NAMES_PAT = re.compile(r'^\S+ < :\S+ 353 \S+ [=*@] (?P<chan>#\S+) :(?P<names>.*)$')
ENDNAMES_PAT = re.compile(r'^\S+ < :\S+ 366 \S+ (?P<chan>#\S+) ')
EVENT_PAT = re.compile(
    r'^\S+ < :(?P<nick>[^!@\s]+)!\S+ (?P<verb>JOIN|PART|QUIT|NICK|KICK)'
    r'(?: :?(?P<arg>\S+))?(?: (?P<victim>\S+))?')


class Presence:
    """Which channels `nick` sits in, followed from the bot log."""

    def __init__(self, nick):
        self.nick = nick
        self.chans = set()
        self._names = {}

    def feed(self, line):
        if m := NAMES_PAT.match(line):
            names = {n.lstrip("@+%&~").lower() for n in m["names"].split()}
            self._names.setdefault(m["chan"].lower(), set()).update(names)
            return
        if m := ENDNAMES_PAT.match(line):
            chan = m["chan"].lower()
            if self.nick in self._names.pop(chan, set()):
                self.chans.add(chan)
            else:
                self.chans.discard(chan)
            return
        m = EVENT_PAT.match(line)
        if not m:
            return
        who, verb = m["nick"].lower(), m["verb"]
        arg = (m["arg"] or "").lower()
        if verb == "KICK":
            if (m["victim"] or "").lower() == self.nick:
                self.chans.discard(arg)
        elif verb == "NICK":
            if who == self.nick:
                self.chans.clear()
        elif who == self.nick:
            if verb == "JOIN":
                self.chans.add(arg)
            elif verb == "PART":
                self.chans.discard(arg)
            elif verb == "QUIT":
                self.chans.clear()

    def seed(self, path):
        try:
            with open(path, errors="replace") as f:
                for line in f:
                    self.feed(line.rstrip("\n"))
        except OSError:
            pass


def _deal(deck, lines):
    """A shuffled deck, not random.choice: no line comes back before every
    other one has had its turn."""
    if not deck:
        deck.extend(random.sample(lines, len(lines)))
    return deck.pop()


def next_languorino():
    return _deal(_languorino_deck, LANGUORINO)


def next_rocher():
    return _deal(_rocher_deck, ROCHER)


# Languorino first: «un languorino da Rocher» gets the languorino answer, and
# one line only — the two share the cooldown.
BUTLER = ((LANGUORINO_PAT, next_languorino), (ROCHER_PAT, next_rocher))


def first_link(text):
    """First non-YouTube link in a line, trailing punctuation trimmed."""
    for m in ANY_LINK_PAT.finditer(text):
        url = m.group(0).rstrip(TRAILING)
        if not LINK_PAT.match(url):
            return url
    return None


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


def generic(chan, link, last_chan, last_seen):
    """Same flood rules as the YouTube line, keyed on the URL."""
    now = time.time()
    if now - last_chan.get(chan, 0) < COOLDOWN_SEC:
        return
    if now - last_seen.get((chan, link), 0) < REPEAT_SEC:
        return
    last_chan[chan] = now
    last_seen[(chan, link)] = now
    try:
        line = link_preview(link)
    except Exception:
        line = None  # refused, unreachable, garbage: say nothing
    if line:
        say(chan, line)


def daemon():
    last_chan = {}
    last_video = {}
    last_languorino = {}
    last_ip = {}
    deferred = Presence(IP_DEFER_TO)
    deferred.seed(LOG)
    p = subprocess.Popen(["tail", "-F", "-n", "0", LOG],
                         stdout=subprocess.PIPE, text=True, errors="replace")
    for line in p.stdout:
        deferred.feed(line.rstrip("\n"))
        m = PRIVMSG_PAT.search(line)
        if not m:
            continue
        chan = m.group("chan")
        text = m.group("text")

        # Ambrogio is a butler everywhere he sits, not only where previews run.
        butler = next((line for pat, line in BUTLER if pat.search(text)), None)
        if butler:
            now = time.time()
            if now - last_languorino.get(chan, 0) >= LANGUORINO_SEC:
                last_languorino[chan] = now
                say(chan, butler())
            continue

        ipcmd = IP_CMD_PAT.match(text)
        if ipcmd:
            if chan.lower() in deferred.chans:
                continue
            now = time.time()
            if now - last_ip.get(chan, 0) >= IP_SEC:
                last_ip[chan] = now
                for out in ip_lines(ipcmd.group("ip")):
                    say(chan, out)
            continue

        if chan not in CHANNELS:
            continue

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
                link = first_link(text)
                if link:
                    generic(chan, link, last_chan, last_video)
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
    if argv[:1] == ["--link"] and len(argv) > 1:
        try:
            line = link_preview(argv[1])
        except LinkRefused as e:
            print(f"refused: {e}")
            return 1
        print(line or "nothing to show")
        return 0 if line else 1
    if argv[:1] == ["--ip"] and len(argv) > 1:
        print("\n".join(ip_lines(argv[1])))
        return 0
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
