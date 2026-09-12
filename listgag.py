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

PRIVMSG_PAT = re.compile(
    r"< :(?P<nick>[^!@\s]+)!\S+\s+PRIVMSG\s+(?P<chan>#\S+)\s+:(?P<text>.*?)$"
)

# `!list`, `!List`, `! list`, trailing punctuation — the bait is typed by
# people who are not being careful. Anchored so `whitelist` never matches.
LIST_PAT = re.compile(r"^\s*!\s*list\b[\s.!?]*$", re.IGNORECASE)


# ---------------------------------------------------------------- generation

# Catalog as (release-name, size-pool): the name already looks like a real
# scene release (year + source + codec for video, version for software), and
# the size is drawn from a pool that FITS the content — vjt's order
# 2026-09-12 ("i titoli devono essere piu' verosimili... in modo che sembri
# vero"). A 47M "Windows XP ISO" reads as a broken generator, not warez.
CATALOG = [
    ("Il.Signore.Degli.Anelli.Le.Due.Torri.2002.iTALiAN.DVDRip.XviD", ["699M", "1.36G"]),
    ("Matrix.Reloaded.2003.TS.iTALiAN.XviD", ["699M", "713M"]),
    ("Kill.Bill.Vol.1.2003.DVDSCR.XviD", ["699M", "1.36G"]),
    ("Pirates.of.the.Caribbean.2003.TS.XviD", ["699M", "1.40G"]),
    ("Finding.Nemo.2003.iTALiAN.DVDRip.DivX", ["699M", "1.37G"]),
    ("Il.Ritorno.Del.Re.2003.SCREENER.XviD", ["1.36G", "1.40G"]),
    ("Windows.XP.Professional.SP1.iTA.Corporate.ISO", ["589M", "612M"]),
    ("Windows.98.SE.OEM.iTA.ISO", ["198M", "231M"]),
    ("Adobe.Photoshop.7.0.1.iTA.Retail", ["148M", "172M"]),
    ("Nero.Burning.ROM.6.0.0.19.Multi", ["38M", "47M"]),
    ("Office.XP.Professional.iTA.VOL.ISO", ["546M", "612M"]),
    ("3ds.max.5.1.ISO", ["612M", "698M"]),
    ("AutoCAD.2004.iTA.ISO", ["612M", "1.12G"]),
    ("Norton.Antivirus.2003.Pro.iTA", ["38M", "62M"]),
    ("WinZip.8.1.iTA.Reg", ["3.4M", "4.1M"]),
    ("Counter.Strike.1.6.Full.NoSteam", ["312M", "428M"]),
    ("Diablo.II.LOD.v1.10.NoCD.iTA", ["2.14G", "2.61G"]),
    ("GTA.Vice.City.iTA.RIP", ["1.39G", "2.10G"]),
    ("Age.of.Mythology.iTA.2CD", ["1.28G", "1.36G"]),
    ("Half.Life.2.Beta.LEAK", ["2.9G", "3.1G"]),
]

# Rationed: the SignalOS legend is canon, not a running joke. ~1 in 10.
LEGEND = [
    ("SignalOS.1.0.FINAL.BOOTABLE.ISO", ["648M", "702M"]),
    ("SignalOS.0.9b.SOURCE.LEAKED", ["14M", "22M"]),
]

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
    """`count` distinct catalog entries, SignalOS legend rationed to ~1 in 10.

    The legend is canon ([[reference_signalos_canon]]), not a running joke;
    seeing it in every dump is how canon turns into a catchphrase.
    """
    entries = rng.sample(CATALOG, count)
    if rng.random() < 0.10:
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
    """Header + three pack lines + footer + cross-promo, per the standing order."""
    packs = 3
    open_slots = rng.randint(0, 3)

    # La richiesta si fa IN CANALE, non in /msg privato (vjt 2026-09-12:
    # "vogliamo vederli in canale"). Il bot che ospita il gag raccoglie il
    # `xdcc send #N` dal canale e serve la DCC al richiedente.
    lines = [
        f"** {packs} packs **  {open_slots} of 3 slots open, {rng.choice(SLOT_NOTES)}",
        "** Per richiedere un file scrivi in canale: xdcc send #N **",
    ]

    # Bestemmie present but not prepotente (vjt, 2026-09-12): 2 of 3 lines
    # carry one, a third of the time all three. Never zero — the gag has to
    # land — and the loud CAPS-in-the-middle tag is gone, it rides the crew.
    entries = pick_entries(rng, packs)
    n_best = packs if rng.random() < 0.33 else 2
    best_rows = set(rng.sample(range(packs), n_best))
    total = 0.0
    for i, (title, sizes) in enumerate(entries):
        size = rng.choice(sizes)
        name = scene_name(rng, title, i in best_rows)
        downloads = rng.randint(1, 99)
        lines.append(f"#{i + 1}  {downloads}x [{size}] {name}")
        total += _gigs(size)

    lines.append(
        f"** Total Offered: {total:.1f}G  Total Transferred: "
        f"{total * rng.uniform(8, 99):.1f}G **"
    )
    lines.append(rng.choice(CROSS_PROMO))
    return lines


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


def handle(nick, chan, rng, last_fired):
    key = (nick.lower(), chan.lower())
    now = time.time()
    previous = last_fired.get(key, 0.0)
    last_fired[key] = now

    if now - previous < THROTTLE_S:
        say(chan, rng.choice(THROTTLE_LINES).format(nick=nick))
        return

    for line in build_dump(rng):
        say(chan, line)
        time.sleep(LINE_GAP_S)


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


def main():
    rng = random.Random()
    last_fired = {}
    for line in tail(LOG):
        match = PRIVMSG_PAT.search(line)
        if not match:
            continue
        nick, chan, text = match["nick"], match["chan"], match["text"]
        if nick.lower() in IGNORE_NICKS:
            continue
        if chan not in CHANNELS or not LIST_PAT.match(text):
            continue
        if chan in muted_channels():
            continue
        try:
            handle(nick, chan, rng, last_fired)
        except OSError as exc:
            print(f"list_sidecar: FIFO write failed: {exc}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
