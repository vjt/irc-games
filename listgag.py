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
LIST_PAT = re.compile(r"^\s*!\s*list\b[\s.!?]*$", re.IGNORECASE)

# The follow-through: a victim who bites types `xdcc send #N`. Real XDCC
# leechers send it in a private query to the bot; humans watching the gag send
# it in channel (vjt 2026-09-12: "vogliamo vederli in canale"). We honour both.
# `#` optional, trailing junk tolerated. The pack number now MATTERS: in channel
# we look up which title pack #N was in the last dump and offer the rickroll
# UNDER THAT NAME (vjt 2026-09-12: "non deve essere rickroll.mp4, deve esse lo
# stesso titolo richiesto quindi si va conservato dello stato"). In a private
# query there is no dump to look up, so a random title is used ("file a caso").
XDCC_PAT = re.compile(r"^\s*xdcc\s+send\s+#?(\d+)\b", re.IGNORECASE)

# The one payload every request receives: the synthesised rickroll video (see
# payload/make_rickroll_mp4.sh). No exe (vjt 2026-09-12). It lives in the bot's
# BOT_DCC_DIR inside the jail; if it is missing bot.py declines the DCC, so the
# wiring lands even before the file is deployed. The name must satisfy bot.py's
# DCC_NAME_PAT ([A-Za-z0-9._-]{1,80}) — a basename, no slashes. What the VICTIM
# sees is not this name but the pack title (see the DCC offer alias in bot.py).
PAYLOAD_FILE = os.environ.get("LIST_PAYLOAD", "rickroll.mp4")


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

SLOT_NOTES = [
    "Record: 6666.6kB/s",
    "Record: 666.6kB/s",
    "Record: 1.44kB/s",
    "Record: 9999.9kB/s",
]

# The cross-promo vjt asked for: a real CristoBOT command, not an invented
# one (`dio` is in its core command set — CristoBOT CLAUDE.md, "Commands").
CROSS_PROMO = [
    "** Per sapere di piu' prova !dio, !madonna o !bestemmia — CristoBOT serve h24 **",
    "** Fuori catalogo? chiedi a CristoBOT: !dio, !santo, !storiella **",
    "** Assistenza clienti: !dio a CristoBOT. Non risponde il centralino, risponde Lui **",
    "** Il reparto teologico e' di CristoBOT: !bestemmia, !dio, !papa **",
]

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
        f"** {packs} packs **  {open_slots} of 3 slots open, {rng.choice(SLOT_NOTES)}",
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
    lines.append(rng.choice(CROSS_PROMO))
    return lines, names


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

    lines, names = build_dump(rng)
    last_dump[chan.lower()] = names
    for line in lines:
        say(chan, line)
        time.sleep(LINE_GAP_S)


def handle_xdcc(nick, chan, n, rng, last_dump):
    """Serve the rickroll for `xdcc send #n`.

    In channel, `#n` indexes the channel's last dump and the offer carries that
    pack's title; a number with no matching pack gets one burbero line and no
    transfer. In a private query (`chan` is None) there is no dump to index, so
    a random catalog title is offered (vjt: "file a caso")."""
    if chan is None:
        title, _sizes = rng.choice(CATALOG)
        send_dcc(nick, PAYLOAD_FILE, offer_name(scene_name(rng, title, False)))
        return
    names = last_dump.get(chan.lower())
    if not names or not (1 <= n <= len(names)):
        say(chan, f"{nick}: pack #{n}? Non c'e', porco dio. Fai !list e leggi.")
        return
    send_dcc(nick, PAYLOAD_FILE, offer_name(names[n - 1]))


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


def dispatch(nick, target, text, rng, last_fired, last_dump):
    """Act on one inbound PRIVMSG: `!list`/channel `xdcc` or a private query."""
    if nick.lower() in IGNORE_NICKS:
        return
    if target.startswith("#"):
        # In channel: `!list` dumps the catalog; `xdcc send #N` from a human
        # watching the gag gets the payload, offered under pack #N's title.
        chan = target.lower()
        if chan not in CHANNELS or chan in {m.lower() for m in muted_channels()}:
            return
        if LIST_PAT.match(text):
            handle(nick, target, rng, last_fired, last_dump)
        else:
            m = XDCC_PAT.match(text)
            if m:
                handle_xdcc(nick, target, int(m.group(1)), rng, last_dump)
    else:
        m = XDCC_PAT.match(text)
        if m:
            # Private query to the bot: an XDCC leecher's auto-downloader. No
            # channel context, so a random title (vjt: "file a caso").
            handle_xdcc(nick, None, int(m.group(1)), rng, last_dump)


def main():
    rng = random.Random()
    last_fired = {}
    last_dump = {}
    for line in tail(LOG):
        match = PRIVMSG_PAT.search(line)
        if not match:
            continue
        try:
            dispatch(match["nick"], match["target"], match["text"],
                     rng, last_fired, last_dump)
        except OSError as exc:
            print(f"list_sidecar: FIFO write failed: {exc}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
