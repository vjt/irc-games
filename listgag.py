#!/usr/bin/env python3
"""!list warez-gag sidecar.

Owns the `!list` gag outright (vjt order, 2026-08-07: "te NON rispondi piu al
list e risponde solo il sidecar"). The main session must never answer `!list`
again — one mouth, nothing to coordinate.

Shape is the house sidecar shape (roll_counter.py, aup_watchdog.py): tail
bot.log, match, write verbs into the bot.send FIFO.

Config dall'ambiente, come suore.py e impiccato.py — il codice sta in un repo
suo e gira nella jail `convento` su m42, dove log e FIFO non sono nel checkout:

  LIST_LOG    log del bot da tailare      (default: il vecchio path sul Pi)
  LIST_FIFO   FIFO su cui rispondere      (idem)
  LIST_CHANS  canali, separati da virgola (default: #sniffo,#sbiffo)
  LIST_MUTES  file dei mute della sessione (default: quello sul Pi; assente = nessun mute)
  LIST_IGNORE nick da ignorare in piu', separati da virgola (si somma ai fissi)

Output is canonical iroffer/XDCC LIST (vjt order 2026-07-10, "facciamolo
bene") so a victim running an xdcc-catcher parses it and the bestemmia lands
in the filename field of their download manager. That is why bestemmie live
INSIDE the scene name and never in prose: prose does not survive the parse.

Entries are GENERATED, not drawn from a fixed list — combinatorics over
titles x tags x groups x sizes, so the gag is effectively never the same
twice ("una serie di sequenze e voci ben nutrite cosi che sia sempre nuovo e
variegato", same order).
"""

import os
import random
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

# I default sono i path storici sul Pi: finche' il gag girava nel checkout del
# bot erano gli unici, e lasciarli qui vuol dire che un run a mano da li' si
# comporta come prima. Nella jail arrivano tutti dall'ambiente.
ROOT = Path("/home/vjt/code/IRC/vjt-claude")
LOG = Path(os.environ.get("LIST_LOG", ROOT / "bot.log"))
FIFO = Path(os.environ.get("LIST_FIFO", ROOT / "bot.send"))
MUTES = Path(os.environ.get("LIST_MUTES", ROOT / "memory" / "project_active_mutes.md"))

# Where the gag is live. #it-opers was dropped by vjt on 2026-08-07 ("deve
# rispondere solo su sniffo e sbiffo") — it is a working channel, not a
# playground.
CHANNELS = {c.strip().lower()
            for c in os.environ.get("LIST_CHANS", "#sniffo,#sbiffo").split(",")
            if c.strip()}
if not CHANNELS:
    raise SystemExit("listgag: LIST_CHANS e' vuoto — nessun canale da servire")

# The gag outranks the mute — vjt ruled it explicitly on 2026-08-07 ("no non
# deve tacere"), and only vjt can override a mute. Scope is narrow on purpose:
# THIS sidecar answers `!list` on a muted channel, the session itself stays
# muted for everything else. Flip back to True if that ruling is withdrawn.
RESPECT_MUTES = False

# Nicks we never answer: ourselves, and the other bots. A bot firing `!list`
# at another bot is how you get a two-machine flood and a k-line. Convento,
# MikeBongiorno ed EliteWarez stanno nella stessa jail: si sentono a vicenda
# sul canale, e senza questa lista un `!list` finto in bocca a uno di loro
# farebbe partire il dump.
IGNORE_NICKS = {"vjt-claude", "cristobot", "trillian", "gazzurbo", "ottavia",
                "convento", "mikebongiorno", "elitewarez"}
IGNORE_NICKS |= {n.strip().lower()
                 for n in os.environ.get("LIST_IGNORE", "").split(",")
                 if n.strip()}

# Same nick re-firing inside this window gets one burbero line, not a second
# dump. Repeated full dumps are flood, and flood is cringe.
THROTTLE_S = 60

# Pause between emitted lines. A dump is six lines; without a gap the ircd
# hands us excess flood halfway through the recital.
LINE_GAP_S = 1.2

# Target is a channel (`#chan`) OR the bot's own nick, for a private query.
# XDCC leechers request in query, not in channel (vjt 2026-09-12), so we must
# see both — anything not starting with `#` is a query addressed to us (the bot
# only ever sees privmsgs to a channel it is in or to itself).
PRIVMSG_PAT = re.compile(
    r"< :(?P<nick>[^!@\s]+)!\S+\s+PRIVMSG\s+(?P<target>\S+)\s+:(?P<text>.*?)$"
)

# `!list`, `!List`, `! list`, trailing punctuation — the bait is typed by
# people who are not being careful. Anchored so `whitelist` never matches.
# Trailing words are ignored (Sythos 2026-10-22: `!list così X è contento`
# must still dump), but `!listone` is not `!list`.
LIST_PAT = re.compile(r"^\s*!\s*list\b", re.IGNORECASE)

# The follow-through: a victim who bites types `xdcc send #N`. Real XDCC
# leechers send it in a private query to the bot; humans watching the gag send
# it in channel (vjt 2026-09-12: "vogliamo vederli in canale"). We honour both.
# `#` optional, trailing junk tolerated. The pack number now MATTERS: in channel
# we look up which title pack #N was in the last dump and offer the rickroll
# UNDER THAT NAME (vjt 2026-09-12: "non deve essere rickroll.mp4, deve esse lo
# stesso titolo richiesto quindi si va conservato dello stato"). In a private
# query there is no dump to look up, so a random title is used ("file a caso").
# `send` is optional (vjt 2026-09-13): a victim who types `xdcc 1` or `xdcc #1`
# has bitten just as hard as one who spells out `xdcc send #1`, and dropping his
# request on a syntax detail wastes the whole setup.
XDCC_PAT = re.compile(r"^\s*xdcc\s+(?:send\s+)?#?(\d+)\b", re.IGNORECASE)

# In channel a leecher may address the bot by nick: `elitewarez: xdcc send #2`
# (vjt 2026-09-13). Strip a leading `<selfnick>[:,]` before the xdcc match —
# ONLY our own nick, so `othernick: xdcc ...` aimed at a different bot does not
# false-trigger our dump. The nick is configurable per instance.
SELF_NICK = os.environ.get("LIST_NICK", "elitewarez").lower()
ADDRESS_PAT = re.compile(r"^\s*(?P<nick>[^\s:,]+)\s*[:,]\s*(?P<rest>.*)$")


def strip_address(text):
    """Drop a leading `<selfnick>:`/`,` address prefix, else return text as-is."""
    m = ADDRESS_PAT.match(text)
    if m and m["nick"].lower() == SELF_NICK:
        return m["rest"]
    return text

# The payload pool: one file is drawn at random per offer (vjt 2026-09-15,
# "inviato a random"). No exe (vjt 2026-09-12). They live in the bot's
# BOT_DCC_DIR inside the jail; if one is missing bot.py declines that DCC, so
# the wiring lands even before the files are deployed. Each name must satisfy
# bot.py's DCC_NAME_PAT ([A-Za-z0-9._-]{1,80}) — a basename, no slashes. What
# the VICTIM sees is not this name but the pack title (see the DCC offer alias
# in bot.py). LIST_PAYLOAD is a comma-separated list.
PAYLOAD_FILES = [p.strip() for p in os.environ.get(
    "LIST_PAYLOAD",
    "rickroll.mp4,rickroll-universal.mp4,mosconi-universal.mp4",
).split(",") if p.strip()]


# ---------------------------------------------------------------- generation

# Catalog as (release-name, size-pool): the name already looks like a real
# modern scene release (year + resolution + source + codec) and the size is
# drawn from a pool that FITS the content — vjt's order 2026-09-12 ("i titoli
# devono essere piu' verosimili... in modo che sembri vero"), tightened the
# same day to films only, and RECENT ones ("il set offerto dovrebbero esser
# solo film / non software / e film recenti"). No software, no games: nobody
# leeches Windows XP in 2026, and a WEB-DL of a film in cinemas is what an
# XDCC catcher actually bites on. Sizes are modern-encode plausible: 1080p
# WEB-DL/BluRay in the single-digit-to-low-teens GB, 2160p UHD in the 18-40G.
CATALOG = [
    ("Dune.Parte.Due.2024.iTA.ENG.2160p.UHD.BluRay.x265", ["24.6G", "38.2G"]),
    ("Deadpool.e.Wolverine.2024.iTA.ENG.1080p.BluRay.x264", ["8.7G", "12.4G"]),
    ("Inside.Out.2.2024.iTA.1080p.WEB-DL.DDP5.1.H264", ["4.9G", "7.1G"]),
    ("Il.Gladiatore.II.2024.iTA.ENG.1080p.WEB-DL.x264", ["6.2G", "9.8G"]),
    ("Furiosa.A.Mad.Max.Saga.2024.iTA.2160p.WEB-DL.H265", ["18.4G", "27.9G"]),
    ("Wicked.2024.iTA.ENG.1080p.WEB-DL.DDP5.1.H264", ["7.3G", "10.6G"]),
    ("Oppenheimer.2023.iTA.ENG.1080p.BluRay.x264", ["11.2G", "15.8G"]),
    ("Cattivissimo.Me.4.2024.iTA.1080p.WEB-DL.H264", ["4.1G", "6.3G"]),
    ("Beetlejuice.Beetlejuice.2024.iTA.1080p.BluRay.x264", ["8.1G", "11.9G"]),
    ("Il.Robot.Selvaggio.2024.iTA.ENG.1080p.WEB-DL.x264", ["5.4G", "7.8G"]),
    ("Alien.Romulus.2024.iTA.ENG.2160p.UHD.BluRay.x265", ["22.1G", "34.7G"]),
    ("Venom.The.Last.Dance.2024.iTA.1080p.WEB-DL.H264", ["6.6G", "9.2G"]),
    ("Sonic.3.Il.Film.2024.iTA.1080p.WEB-DL.DDP5.1.H264", ["5.1G", "7.4G"]),
    ("Mufasa.Il.Re.Leone.2024.iTA.1080p.WEB-DL.x264", ["6.9G", "10.1G"]),
]

# SignalOS canon ([[reference_signalos_canon]]) is retired from the dump: it is
# an OS, and the offered set is films only now (vjt 2026-09-12). The reference
# survives here; if vjt wants the legend back, it returns as its own gag, not in
# a film catalogue.
LEGEND = []

# Real scene crews: the clean half of the crew pool, so the list reads
# straight where the bestemmia is not carrying the gag.
CLEAN_GROUPS = [
    "TNT-Village", "RAZOR1911", "FAIRLIGHT", "PARADOX", "DEVIANCE",
    "ECHELON", "CLASS", "iNTENSiON", "FLT", "PiZZA", "RELOADED", "DEViSE",
]

# "Italian" crews with the bestemmia baked into the crew name, mixed-case so
# it reads as a real group rather than a CAPS shout — present but not
# prepotente (vjt, 2026-09-12). This is what carries the gag into the victim's
# download-manager filename field; it must ride IN the name.
BEST_GROUPS = [
    "DioCaneTeam", "PorcoDioRG", "MadonnaMaiala", "CristoCane-iTA",
    "DioBoiaCrew", "PorcaMadonnaTNT", "DioSerpente", "DioLadro-iTA",
    "MadonnaImpestata", "DioBestiaRG", "DioStronzo-iTA", "PorcoGesuSFX",
]

# Occasional extra bestemmia as an uppercase release sub-tag, as if a fixer
# stamped it — sprinkled, not on every line.
BEST_TAGS = [
    "PORCODIO", "DIOCANE", "MADONNAMAIALA", "DIOBESTIA", "CRISTOCANE",
    "DIOBOIA", "PORCAMADONNA",
]

def record_note(rng):
    """Header `Record:` — a believable seedbox transfer record, not a meme
    number (vjt 2026-09-13: "record random, deve esser verosimile"). A real
    XDCC dump shows a few thousand kB/s, so we draw one at random each dump."""
    return f"Record: {rng.uniform(850, 4900):.1f}kB/s"

THROTTLE_LINES = [
    "{nick}: il catalogo e' quello di dieci secondi fa, porco dio. Aspetta.",
    "{nick}: due !list di fila? Il server FTP e' uno solo e ha gia' fumato.",
    "{nick}: madonna che fretta. Il listino non e' cambiato.",
    "{nick}: rileggi quello sopra, dio bestia, non ne stampo un altro.",
]


def pick_entries(rng, count):
    """`count` distinct catalog entries, LEGEND rationed to ~1 in 10 if present.

    LEGEND is empty under the films-only order, so the ration is a no-op; the
    guard keeps it wired for the day the legend comes back as its own gag.
    """
    entries = rng.sample(CATALOG, count)
    if LEGEND and rng.random() < 0.10:
        entries[rng.randrange(count)] = rng.choice(LEGEND)
    return entries


def scene_name(rng, title, bestemmiata):
    """One filename token: the release name plus a scene-crew suffix.

    A bestemmiata line gets a BEST_GROUPS crew (bestemmia baked in, mixed
    case) and ~1 time in 4 an extra uppercase sub-tag stamped into the name;
    a clean line gets a real scene crew. No spaces either way, so whatever
    bestemmia is present survives the download-manager filename parse.
    """
    if bestemmiata:
        if rng.random() < 0.25:
            title = f"{title}.{rng.choice(BEST_TAGS)}"
        group = rng.choice(BEST_GROUPS)
    else:
        group = rng.choice(CLEAN_GROUPS)
    return f"{title}-{group}"


def build_dump(rng):
    """Build one dump. Returns `(lines, names)`: the IRC lines to recite and the
    ordered scene names offered as packs #1..#N, so `xdcc send #N` can offer the
    rickroll under the title the victim actually asked for (vjt 2026-09-12)."""
    packs = 3
    open_slots = rng.randint(0, 3)

    # La richiesta si fa IN CANALE, non in /msg privato (vjt 2026-09-12:
    # "vogliamo vederli in canale") — ma gli script XDCC la mandano in query, e
    # anche quella la serviamo. Il bot raccoglie il `xdcc send #N` e serve la DCC.
    lines = [
        f"** {packs} packs **  {open_slots} of 3 slots open, {record_note(rng)}",
        "** Per richiedere un file scrivi: xdcc send #N **",
    ]

    # Bestemmie present but not prepotente (vjt, 2026-09-12): 2 of 3 lines
    # carry one, a third of the time all three. Never zero — the gag has to
    # land — and the loud CAPS-in-the-middle tag is gone, it rides the crew.
    entries = pick_entries(rng, packs)
    n_best = packs if rng.random() < 0.33 else 2
    best_rows = set(rng.sample(range(packs), n_best))
    total = 0.0
    names = []
    for i, (title, sizes) in enumerate(entries):
        size = rng.choice(sizes)
        name = scene_name(rng, title, i in best_rows)
        names.append(name)
        downloads = rng.randint(1, 99)
        lines.append(f"#{i + 1}  {downloads}x [{size}] {name}")
        total += _gigs(size)

    lines.append(
        f"** Total Offered: {total:.1f}G  Total Transferred: "
        f"{total * rng.uniform(8, 99):.1f}G **"
    )
    # Hint in coda (Lucy, #sbiffo 2026-10-15, via di vjt): chi non trova il suo
    # titolo nei 3 pack sa che c'e' @find.
    lines.append("** Se cerchi qualcosa di specifico, usa @find <parola> **")
    return lines, names


# `@find <termini>` (Lucy, #sbiffo 2026-10-07, via di vjt): il leecher "scafato"
# che non abbocca al !list cerca il suo titolo, e il bot lo "trova" sempre — un
# pack fabbricato coi termini cercati, sotto lo stesso nome che poi `xdcc send`
# gli consegna (payload dal solito pool: rickroll o Mosconi). `!find` uguale.
FIND_PAT = re.compile(r"^\s*[@!]find\s+(?P<q>\S.*?)\s*$", re.IGNORECASE)
# Quanti termini/caratteri del titolo cercato finiscono nel nome scene: basta a
# riconoscere la propria ricerca, non abbastanza da sforare DCC_OFFER_NAME_PAT.
FIND_MAX_WORDS = 6
FIND_MAX_TITLE = 40
# Il pack trovato si accoda al listino del canale (#4, #5, ...): `xdcc send #N`
# lo indicizza come gli altri. Oltre questa soglia il listino riparte da zero.
FIND_MAX_PACKS = 30
FIND_RELEASES = [
    ("iTA.ENG.1080p.WEB-DL.DDP5.1.H264", (4.2, 11.8)),
    ("iTA.1080p.BluRay.x264", (6.5, 14.9)),
    ("iTA.ENG.2160p.UHD.BluRay.x265", (18.0, 39.5)),
    ("iTA.720p.WEB-DL.x264", (1.4, 3.9)),
]
# Chi cerca software non deve vedersi offrire un "Photoshop.1080p.BluRay"
# (Sythos, #sbiffo 2026-10-23): se uno dei termini sa di programma, la release
# prende tag e taglie da warez applicativo invece che da film.
FIND_SOFTWARE_PAT = re.compile(
    r"^(?:windows|win(?:7|8|10|11|xp)|office|word|excel|powerpoint|outlook|"
    r"photoshop|illustrator|indesign|premiere|lightroom|acrobat|adobe|"
    r"autocad|solidworks|revit|sketchup|coreldraw|maya|3dsmax|matlab|"
    r"visual|vmware|winrar|winzip|nero|norton|kaspersky|avast|ccleaner|idm|"
    r"macos|linux|ubuntu|debian|android|ableton|cubase|flstudio|"
    r"crack|keygen|serial|patch|antivirus|driver|software|setup|exe|iso|apk)$",
    re.IGNORECASE)
FIND_SOFTWARE_RELEASES = [
    ("x64.MULTi.Incl.Keygen", (0.3, 4.8)),
    ("x64.Retail.ISO.Incl.Crack", (2.1, 6.4)),
    ("Win.x64.Incl.Patch", (0.2, 2.9)),
    ("MacOS.Incl.Serial", (0.4, 3.6)),
]
FIND_EMPTY_LINE = "{nick}: @find di cosa, porco dio? Scrivi cosa cerchi."


def find_title(query):
    """Termini cercati -> titolo scene (`Il.Signore.Degli.Anelli`), o None se
    non resta niente di stampabile. Accenti piegati in ASCII: il nome deve
    restare [A-Za-z0-9.-], o il DCC offer non passa."""
    ascii_q = unicodedata.normalize("NFKD", query).encode("ascii", "ignore").decode()
    words = re.findall(r"[A-Za-z0-9]+", ascii_q)[:FIND_MAX_WORDS]
    if not words:
        return None
    return ".".join(w[:1].upper() + w[1:] for w in words)[:FIND_MAX_TITLE].strip(".")


# Un anno nei termini cercati ("matrix 1999") diventa l'anno della release.
FIND_YEAR_PAT = re.compile(r"(?:^|\.)((?:19|20)\d\d)(?=\.|$)")


def build_find(rng, title):
    """Il nome scene del pack "trovato" e la sua taglia. Bestemmiato sempre:
    e' una riga sola, la battuta deve atterrare li'.

    L'anno NON si inventa: "Amici.Miei.2020" e' sgamato al primo sguardo (Lucy,
    #sbiffo 2026-10-08). Se chi cerca l'ha scritto, va in coda al titolo come
    nelle release vere; se no la release esce senza anno."""
    software = any(FIND_SOFTWARE_PAT.match(w) for w in title.split("."))
    tags, (lo, hi) = rng.choice(FIND_SOFTWARE_RELEASES if software else FIND_RELEASES)
    m = FIND_YEAR_PAT.search(title)
    if m:
        title = (title[:m.start()] + title[m.end():]).strip(".") or title
        title = f"{title}.{m.group(1)}"
    name = scene_name(rng, f"{title}.{tags}", True)
    return name, f"{rng.uniform(lo, hi):.1f}G"


def handle_find(nick, chan, query, rng, last_dump):
    title = find_title(query)
    if title is None:
        say(chan, FIND_EMPTY_LINE.format(nick=nick))
        return
    name, size = build_find(rng, title)
    names = last_dump.setdefault(chan.lower(), [])
    if len(names) >= FIND_MAX_PACKS:
        names.clear()
    names.append(name)
    n = len(names)
    say(chan, f"{nick}: 1 pack trovato per \"{query.strip()[:60]}\": "
              f"#{n}  {rng.randint(1, 99)}x [{size}] {name}  "
              f"** scrivi: xdcc send #{n} **")


def _gigs(size):
    unit = size[-1]
    value = float(size[:-1])
    return value if unit == "G" else value / 1024.0


# ------------------------------------------------------------------- plumbing


def muted_channels():
    """Channels muted right now, read from the SAME file the session reads.

    One source of truth: a second copy of the mute state is a second copy to
    forget to update. Everything under `## Active` up to the next heading.
    """
    if not RESPECT_MUTES or not MUTES.exists():
        return set()
    try:
        text = MUTES.read_text(encoding="utf-8")
    except OSError:
        return set()  # unreadable state must not silence the gag forever
    active = re.search(r"^## Active\s*$(.*?)^## ", text, re.M | re.S)
    if not active:
        return set()
    return set(re.findall(r"(#[A-Za-z0-9_\-]+)", active.group(1)))


def say(chan, message):
    with FIFO.open("w") as fifo:
        # SAY:list — origin tag, log-only; see bot.py process_cmd.
        fifo.write(f"SAY:list {chan} {message}\n")


def send_dcc(nick, fname, offer=None):
    """Offer `fname` (a basename in BOT_DCC_DIR) to `nick` over CTCP DCC SEND,
    announced to the peer as `offer` if given (the requested pack title) else as
    `fname` itself — see the DCC offer alias in bot.py.

    No `:list` origin tag: the DCC verb is not a channel line, it goes straight
    to the requester. If the file is missing bot.py declines the offer, which is
    exactly what we want while the payloads are not in place yet.
    """
    line = f"DCC {nick} {fname}" + (f" {offer}\n" if offer else "\n")
    with FIFO.open("w") as fifo:
        fifo.write(line)


# bot.py stamps its DCC lifecycle events into bot.log as well as on its stdout
# (vjt's ruling, 2026-09-14: "scrivi l'evento in bot log"), because this sidecar
# only ever tails the PRIVMSG log — the stdout stream belongs to the Monitor.
# Shape: `HH:MM:SS * DCC_DONE TO=<nick> FILE=<offer> BYTES=<n> ACK=<n|none>`.
# FILE= carries the OFFER name, i.e. the pack title the victim asked for, not
# the payload on disk — so the announce reads right even with no pending entry.
DCC_DONE_PAT = re.compile(
    r"\*\s+DCC_DONE\s+TO=(?P<nick>\S+)\s+FILE=(?P<file>\S+)\s+BYTES=(?P<bytes>\d+)\b"
)

# Request announced in channel when it arrives in a private query; completion
# announced when the transfer actually lands. The whole point is that the crew
# sees who is leeching (vjt 2026-09-14: "essi senno non sappiamo").
REQUEST_LINES = [
    "{nick} ha chiesto {pack} in query, che timidezza.",
    "{nick} vuole {pack} e lo chiede di nascosto in /msg, porco dio.",
    "richiesta in query da {nick}: {pack}. Glielo mando.",
]
DONE_LINES = [
    "{nick} ha scaricato {pack} ({size}). Buona visione.",
    "{nick} ha ricevuto {pack}, {size} di roba. Se la merita tutta.",
    "transfer completo: {pack} -> {nick} ({size}).",
]

# How long an offer stays pending before we forget which channel it came from.
# A DCC that lands after this window still gets announced — vjt 2026-09-14:
# "dovrebbe sempre annunciare il dcc complete" — just with the channel resolved
# the same way a query request is.
PENDING_TTL_S = 3600

# ONE channel per announce, never both (vjt 2026-09-14: "dovrebbe annunciare
# solo sul chan dove e' stata fatta la req, non su entrambi"). A request typed
# in channel carries its own answer; a request that arrives in /msg does not, so
# we resolve it to the last served channel that nick was seen talking in. That
# is the channel whose crew watched him bite, which is the whole point of the
# announce. Fallback when he has never spoken: the channel of the most recent
# dump — the gag is live there by definition.
LAST_SEEN_TTL_S = 6 * 3600
last_seen = {}       # nick_lower -> (chan, ts)
last_dump_chan = None  # channel of the most recent !list dump


def note_seen(nick, chan):
    now = time.time()
    for key, (_chan, ts) in list(last_seen.items()):
        if now - ts > LAST_SEEN_TTL_S:
            del last_seen[key]
    last_seen[nick.lower()] = (chan.lower(), now)


def resolve_channel(nick):
    """Which single channel an announce about `nick` belongs in, or None when we
    have no idea (nobody has spoken and no dump has fired since the restart)."""
    entry = last_seen.get(nick.lower())
    if entry and time.time() - entry[1] <= LAST_SEEN_TTL_S:
        return entry[0]
    return last_dump_chan


def remember_offer(pending, nick, offer, chan):
    """Record who we just offered what, so the completion can be announced in
    the right place. `pending` is None in unit tests that only exercise the
    offer path."""
    if pending is None:
        return
    now = time.time()
    for key, (_offer, _chan, ts) in list(pending.items()):
        if now - ts > PENDING_TTL_S:
            del pending[key]
    pending[nick.lower()] = (offer, chan, now)


def human_size(n):
    """Byte count as the gag would print it: MB with one decimal, KB below."""
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f}MB"
    return f"{max(n // 1024, 1)}KB"


def announce_done(nick, fname, nbytes, rng, pending):
    """Announce a completed DCC — always, in exactly one channel: the one the
    pack was asked in, or the one that nick was last seen in when the request
    came in query (or when we no longer remember it)."""
    entry = (pending or {}).pop(nick.lower(), None)
    pack = entry[0] if entry else fname
    chan = (entry[1] if entry else None) or resolve_channel(nick)
    if chan is None:
        return
    say(chan, rng.choice(DONE_LINES).format(
        nick=nick, pack=pack, size=human_size(nbytes)))


def offer_name(scene):
    """Turn a pack's scene name into a DCC offer name: append a `.mp4` extension
    (the payload is a video) and clip to bot.py's DCC_OFFER_NAME_PAT limit of
    120 chars. Scene names are already [A-Za-z0-9.-], so they are CTCP-safe."""
    return f"{scene}.mp4"[:120]


def handle(nick, chan, rng, last_fired, last_dump):
    key = (nick.lower(), chan.lower())
    now = time.time()
    previous = last_fired.get(key, 0.0)
    last_fired[key] = now

    if now - previous < THROTTLE_S:
        say(chan, rng.choice(THROTTLE_LINES).format(nick=nick))
        return

    global last_dump_chan
    lines, names = build_dump(rng)
    last_dump[chan.lower()] = names
    last_dump_chan = chan.lower()
    for line in lines:
        say(chan, line)
        time.sleep(LINE_GAP_S)


def handle_xdcc(nick, chan, n, rng, last_dump, pending=None):
    """Serve the rickroll for `xdcc send #n`.

    In channel, `#n` indexes the channel's last dump and the offer carries that
    pack's title; a number with no matching pack gets one burbero line and no
    transfer. In a private query (`chan` is None) there is no dump to index, so
    a random catalog title is offered (vjt: "file a caso").

    A query request is ANNOUNCED in channel (vjt 2026-09-14: "se la chiedono in
    query ... lo scrive in canale: nick ha richiesto file"). A channel request
    is not: everybody already read the line the victim typed."""
    if chan is None:
        title, _sizes = rng.choice(CATALOG)
        offer = offer_name(scene_name(rng, title, False))
        send_dcc(nick, rng.choice(PAYLOAD_FILES), offer)
        chan_for_announce = resolve_channel(nick)
        remember_offer(pending, nick, offer, chan_for_announce)
        if chan_for_announce is not None:
            say(chan_for_announce,
                rng.choice(REQUEST_LINES).format(nick=nick, pack=offer))
        return
    names = last_dump.get(chan.lower())
    if not names or not (1 <= n <= len(names)):
        say(chan, f"{nick}: pack #{n}? Non c'e', porco dio. Fai !list e leggi.")
        return
    offer = offer_name(names[n - 1])
    send_dcc(nick, rng.choice(PAYLOAD_FILES), offer)
    remember_offer(pending, nick, offer, chan)


def tail(path):
    """Follow the log across rotation (bot.log is rotated under us)."""
    while True:
        try:
            handle_ = path.open("r", encoding="utf-8", errors="replace")
        except OSError:
            time.sleep(2)
            continue
        handle_.seek(0, os.SEEK_END)
        inode = os.fstat(handle_.fileno()).st_ino
        while True:
            line = handle_.readline()
            if line:
                yield line
                continue
            time.sleep(0.4)
            try:
                if path.stat().st_ino != inode:
                    handle_.close()
                    break
            except OSError:
                handle_.close()
                break


def dispatch(nick, target, text, rng, last_fired, last_dump, pending=None):
    """Act on one inbound PRIVMSG: `!list`/channel `xdcc` or a private query."""
    if nick.lower() in IGNORE_NICKS:
        return
    if target.startswith("#"):
        # In channel: `!list` dumps the catalog; `xdcc send #N` from a human
        # watching the gag gets the payload, offered under pack #N's title.
        chan = target.lower()
        if chan not in CHANNELS or chan in {m.lower() for m in muted_channels()}:
            return
        # Every line in a served channel is a location fix: a later query
        # request from this nick is announced where the crew last heard him.
        note_seen(nick, chan)
        if LIST_PAT.match(text):
            handle(nick, target, rng, last_fired, last_dump)
            return
        find = FIND_PAT.match(strip_address(text))
        if find:
            handle_find(nick, target, find["q"], rng, last_dump)
        else:
            m = XDCC_PAT.match(strip_address(text))
            if m:
                handle_xdcc(nick, target, int(m.group(1)), rng, last_dump, pending)
    else:
        m = XDCC_PAT.match(text)
        if m:
            # Private query to the bot: an XDCC leecher's auto-downloader. No
            # channel context, so a random title (vjt: "file a caso").
            handle_xdcc(nick, None, int(m.group(1)), rng, last_dump, pending)


def main():
    rng = random.Random()
    last_fired = {}
    last_dump = {}
    pending = {}
    for line in tail(LOG):
        done = DCC_DONE_PAT.search(line)
        if done:
            try:
                announce_done(done["nick"], done["file"], int(done["bytes"]),
                              rng, pending)
            except OSError as exc:
                print(f"list_sidecar: FIFO write failed: {exc}", file=sys.stderr, flush=True)
            continue
        match = PRIVMSG_PAT.search(line)
        if not match:
            continue
        try:
            dispatch(match["nick"], match["target"], match["text"],
                     rng, last_fired, last_dump, pending)
        except OSError as exc:
            print(f"list_sidecar: FIFO write failed: {exc}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
