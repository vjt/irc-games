#!/usr/bin/env python3
"""Event log per il gioco delle suore.

Due mestieri, tenuti insieme perche' devono per forza parlare la stessa lingua:

  * `append()` — quello che `suore.py` chiama a runtime per scrivere una riga
    NDJSON per ogni spawn risolto. Append-only, mai riletto dal gioco: le
    classifiche continuano a uscire da `suore_state.json`, quindi un log
    troncato o corrotto non puo' rompere una partita.

  * `parse()` — il verso opposto: da una riga annunciata in canale torna
    all'evento. Serve al backfill, che ricostruisce lo storico dai messaggi
    archiviati dal DB di grappa (li' i timestamp ci sono davvero, mentre nel
    `bot.log` c'e' solo `HH:MM:SS`).

Le regex di `parse()` sono la fotografia dei messaggi che `suore.py` emette
oggi. Se cambia la formula di un annuncio va aggiornata anche qui, o il
backfill di domani smette di riconoscere gli eventi di ieri.
"""

import json
import os
import re

# Ordine dei campi nella riga NDJSON. Esplicito perche' un file append-only
# vive anni: le righe vecchie devono restare leggibili senza indovinare.
FIELDS = ("ts", "scope", "nick", "verb", "golden", "reaction")


# --- scrittura (runtime) ----------------------------------------------------

def append(path, ts, scope, nick, verb, golden=False, reaction=None):
    """Appende un evento. Non solleva mai: il log non deve poter rompere il gioco."""
    if not path:
        return
    ev = {
        "ts": round(float(ts), 3),
        "scope": scope,
        "nick": nick,
        "verb": verb,
        "golden": bool(golden),
    }
    if reaction is not None:
        ev["reaction"] = round(float(reaction), 3)
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(ev, ensure_ascii=False, sort_keys=False) + "\n")
    except OSError:
        pass


def read(path):
    """Rilegge il log saltando le righe illeggibili invece di morire."""
    out = []
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except ValueError:
                continue
    return out


# --- lettura (backfill) -----------------------------------------------------

# I codici di colore IRC finiscono nel DB insieme al testo.
CTRL = re.compile(r"[\x02\x0F\x1D\x1F\x16]|\x03(?:\d{1,2}(?:,\d{1,2})?)?")

# `human_time()` di suore.py: "37.989 secondi", "1m 05s", "2h 03m 04s".
T_SEC = re.compile(r"^(\d+(?:\.\d+)?) second[oi]$")
T_MIN = re.compile(r"^(\d+)m (\d+)s$")
T_HOUR = re.compile(r"^(\d+)h (\d+)m (\d+)s$")

_NICK = r"(?P<nick>\S+)"
_TOOK = r"(?P<took>[^!]+)"
_CHAN = r"(?P<chan>#\S+?)"

PATTERNS = (
    # kill dorata — va provata PRIMA di quella normale: "Ne ha stese" e
    # "Ne hai stese" sono formule diverse, ma il nick e' libero e senza
    # ancoraggio si rischia di far combaciare la piu' lasca.
    ("bang", True, re.compile(
        rf"^{_NICK} ha steso la SUORA DORATA in {_TOOK}! "
        rf"Ne ha stese \d+ in {_CHAN}, di cui \d+ dorat[ae]\.")),
    ("bang", False, re.compile(
        rf"^{_NICK} hai fatto secca una suora in {_TOOK}! "
        rf"Ne hai stese \d+ in {_CHAN}\.")),
    ("bef", True, re.compile(
        rf"^{_NICK} si e' fatto amico la SUORA DORATA in {_TOOK}! "
        rf"Ne ha convertite \d+ in {_CHAN}, di cui \d+ dorat[ae]\.")),
    ("bef", False, re.compile(
        rf"^{_NICK} hai fatto amicizia con una suora in {_TOOK}! "
        rf"Ora sei amico di \d+ suor[ae] in {_CHAN}\.")),
)


def parse_took(took):
    """Da "12 secondi" / "1m 05s" / "2h 03m 04s" a secondi. None se non e' un tempo."""
    took = took.strip()
    m = T_SEC.match(took)
    if m:
        return float(m.group(1))
    m = T_MIN.match(took)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = T_HOUR.match(took)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + int(m.group(3))
    return None


def parse(body):
    """Da una riga annunciata in canale all'evento, o None se non e' un annuncio.

    Restituisce (nick, verb, golden, reaction, chan). Il canale torna indietro
    perche' l'annuncio lo nomina: e' l'unico modo di attribuire lo scope giusto
    a una riga archiviata, senza fidarsi del canale in cui e' stata letta.
    """
    text = CTRL.sub("", body).strip()
    for verb, golden, rx in PATTERNS:
        m = rx.match(text)
        if not m:
            continue
        return {
            "nick": m.group("nick"),
            "verb": verb,
            "golden": golden,
            "reaction": parse_took(m.group("took")),
            "chan": m.group("chan").lower(),
        }
    return None
