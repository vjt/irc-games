#!/usr/bin/env python3
"""sillabah — il gioco delle sillabe, rifatto sul regolamento del forum di
rinoadiary.it (2008). Ordine di vjt (2026-10-10 23:43, «si si si vai»).

Stessa forma di impiccato.py, apposta: tail del bot.log, match dei PRIVMSG,
stato su json, risposte scritte sulla FIFO del bot. `bot.py` NON viene toccato.
Chi sa leggere la unit dell'impiccato sa gia' leggere questa.

Regole dell'originale:

  * Ogni manche il bot annuncia una sillaba (due o tre lettere) e un limite
    di lettere: «RI, limite 7 lettere».
  * Si gioca mandando AL BOT, IN QUERY, una parola che CONTIENE la sillaba e
    non supera il limite. Niente iscrizione: si gioca scrivendo. Il bot
    risponde in query «parola accettata» o il motivo del rifiuto.
  * Una parola per giocatore per manche: una nuova sostituisce la vecchia,
    `-delete` la ritira.
  * Ogni lettera vale punti (VALORI); la parola vale la somma. Le lettere
    accentate valgono come la lettera base.
  * Parole straniere solo se d'uso comune in italiano — cioe': decide il
    dizionario (sillabah_parole.txt, Morph-it!, vedi l'intestazione del file).

Aggiunte mie, dichiarate:

  * La manche dura MANCHE_S secondi. Alla fine il canale vede le parole in
    gioco in ordine di punti (al piu' TOP, su una riga), e ogni parola valida
    somma il suo valore alla classifica persistente della rete. Poi PAUSA_S
    secondi e la manche dopo.
  * La sillaba e il limite si scelgono solo se il dizionario ha almeno
    MIN_SOLUZIONI parole valide: una manche irrisolvibile e' un minuto buttato.
  * La partita si chiude da sola dopo IDLE_MANCHE manche di fila senza una
    parola: un bot che parla in eterno in un canale vuoto e' un flood, non un
    gioco (stessa idea dell'IDLE_STOP dell'impiccato). E si chiude comunque
    dopo DEFAULT_MANCHE manche, come il trivial dopo DEFAULT_LIMIT domande.
  * UNA partita per rete alla volta: le parole arrivano in query, e una query
    non dice da quale canale viene. Con due partite vive su due canali una
    parola non saprebbe dove andare.

PUNTEGGI PER RETE, come l'impiccato: su IRCnet non ci sono i services, un nick
li' non prova niente, e mescolare le classifiche regalerebbe a chiunque i punti
di un omonimo.

Il nick del bot non serve per riconoscere le query: il bot vede solo i PRIVMSG
ai canali in cui sta e quelli a se stesso, quindi un target che non e' un canale
E' il bot (stesso ragionamento di listgag.py). SILLABAH_NICK serve solo a dire
in canale a chi scrivere.

CLI:
  sillabah.py --net azzurra             avvia il daemon su Azzurra (#sbiffo)
  sillabah.py --net azzurra classifica  stampa la classifica e esce
  sillabah.py --net azzurra prova [n]   pesca n manche dal dizionario vero e le stampa
  sillabah.py selftest                  esercita la meccanica senza rete ne' FIFO

Env:
  SILLABAH_HOME    dove sta lo stato (default: la directory del file)
  SILLABAH_PAROLE  path del dizionario
  SILLABAH_DB      path del db sqlite costruito dal dizionario (default nello stato)
  SILLABAH_NETS    nome|log|fifo|#chan1,#chan2 ; nome2|...  (formato di IMPICCATO_NETS)
  SILLABAH_LOG     override del bot.log da seguire
  SILLABAH_FIFO    override della FIFO del bot ('' zittisce del tutto)
  SILLABAH_STATE   override del path dello stato json
  SILLABAH_CHANS   override dei canali, separati da virgola
  SILLABAH_NICK    il nick del bot, solo per l'annuncio (default MikeBongiorno)
"""
import json
import os
import random
import re
import select
import sqlite3
import subprocess
import sys
import time
import unicodedata
from collections import deque
from collections.abc import Mapping
from pathlib import Path

import ircgame

# Stato e dati vivono in posti diversi nella jail (vedi IMPICCATO_HOME): il
# dizionario viaggia col codice, lo stato no.
HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("SILLABAH_HOME", HERE))
PAROLE = Path(os.environ.get("SILLABAH_PAROLE", HERE / "sillabah_parole.txt"))
DB = Path(os.environ.get("SILLABAH_DB", REPO / "sillabah_parole.db"))
BOT_NICK = os.environ.get("SILLABAH_NICK", "MikeBongiorno")


def _default_nets():
    """Una rete sola di default: Azzurra, #sbiffo, come l'impiccato."""
    return {
        "azzurra": {
            "log": REPO / "bot.log",
            "fifo": REPO / "bot.send",
            "state": REPO / "sillabah_state.json",
            "chans": {"#sbiffo"},
        },
    }


def load_nets():
    """`SILLABAH_NETS` se c'e', altrimenti la rete di default.

    Stesso formato di IMPICCATO_NETS e SUORE_NETS:

        nome|path/bot.log|path/bot.send|#chan1,#chan2 ; nome2|...

    Lo stato di ogni rete e' `sillabah_state.<rete>.json` sotto REPO, tranne
    'azzurra' che non ha suffisso, come per l'impiccato."""
    raw = os.environ.get("SILLABAH_NETS", "").strip()
    if not raw:
        return _default_nets()
    nets = {}
    for record in raw.split(";"):
        record = record.strip()
        if not record:
            continue
        parts = record.split("|")
        if len(parts) != 4:
            raise SystemExit(f"sillabah: record malformato in SILLABAH_NETS: "
                             f"{record!r} — serve nome|log|fifo|canali")
        name, log, fifo, chanspec = (p.strip() for p in parts)
        chans = {c.strip().lower() for c in chanspec.split(",") if c.strip()}
        if not chans:
            raise SystemExit(f"sillabah: la rete {name!r} non ha canali")
        suffix = "" if name == "azzurra" else f".{name}"
        nets[name] = {"log": Path(log), "fifo": Path(fifo), "chans": chans,
                      "state": REPO / f"sillabah_state{suffix}.json"}
    return nets


NETS = load_nets()

# Quanto dura una manche, e la pausa fra una e l'altra.
MANCHE_S = 60
PAUSA_S = 10
# Manche di fila senza nessuna parola dopo le quali la partita si chiude.
IDLE_MANCHE = 2
# Quante manche dura una partita se nessuno chiede altro, e il tetto a quello
# che si puo' chiedere: `!sillabah 500` e' un'ora e mezza di canale occupato.
DEFAULT_MANCHE = 10
MAX_MANCHE = 30
# Il limite di lettere. Il minimo assoluto vale per le sillabe corte, ma il
# limite resta sempre almeno due lettere sopra la sillaba: una sillaba di tre
# lettere con limite tre lascia in gioco solo la sillaba stessa.
LIMITE_MIN = 6
LIMITE_MAX = 15
SILLABA_LEN = (2, 3)
# Parole valide che il dizionario deve avere perche' una manche si giochi.
MIN_SOLUZIONI = 20
# Quante parole entrano nella riga di fine manche.
TOP = 5
# Ogni quanto il loop si sveglia comunque, a log fermo. Il select dorme meno se
# una scadenza arriva prima.
TICK = 5
# Una manche scaduta da piu' di cosi' (il processo era giu') si butta in
# silenzio: annunciare il risultato di una manche di tre ore fa e' rumore.
STALE_S = 300
# Le parole arrivano in query e il canale sembra morto: durante la manche una
# riga di stato, solo se il numero di parole e' cambiato, al piu' una ogni
# STATUS_GAP secondi, e non a ridosso della scadenza (STATUS_MIN_LEFT).
STATUS_GAP = 15
STATUS_MIN_LEFT = 8
# Tetto in byte di una riga accorpata, come nell'impiccato (vedi MAX_LINE li').
MAX_LINE = 380
# Freno alle risposte in query. bot.py non ha un pacing suo in uscita, e uno
# che spara cinquanta parole al secondo farebbe uscire cinquanta PRIVMSG: Excess
# Flood, e il bot cade per tutti. Le parole si contano comunque, e' la RISPOSTA
# che si salta.
QUERY_GAP = 1.5            # secondi fra due risposte allo stesso nick
QUERY_BUDGET = (8, 10.0)   # al piu' 8 risposte in query ogni 10 secondi, in tutto

# Il valore delle lettere, dal regolamento originale.
VALORI = {
    "a": 1, "b": 3, "c": 1, "d": 3, "e": 1, "f": 3, "g": 3, "h": 6, "i": 1,
    "j": 8, "k": 8, "l": 2, "m": 2, "n": 2, "o": 1, "p": 3, "q": 6, "r": 1,
    "s": 1, "t": 1, "u": 3, "v": 3, "w": 8, "x": 8, "y": 8, "z": 5,
}

# Il target puo' essere un canale O il bot stesso (query): tutto quello che non
# comincia con '#' o '&' e' una query a noi. Le righe '>' sono le nostre e non
# combaciano: il pattern vuole '<'.
# Le parole valgono anche in NOTICE al bot (/notice MikeBongiorno parola): cosi'
# non si cambia buffer (Malekith, via vjt 2026-10-11). Solo in query, e mai dai
# servizi: NickServ & co mandano NOTICE al bot, e ognuna si piglierebbe un
# «Nessuna partita in corso» di risposta.
PRIVMSG_PAT = re.compile(
    r'< :(?P<nick>[^!@\s]+)!(?P<ident>[^@\s]+)@(?P<host>\S+)\s+'
    r'(?P<verb>PRIVMSG|NOTICE)\s+(?P<target>\S+)\s+:(?P<text>.*?)$'
)


def is_input(m):
    """Una riga che il gioco deve guardare: ogni PRIVMSG, NOTICE solo in
    query e non dai servizi."""
    if m["verb"] == "PRIVMSG":
        return True
    return (not m["target"].startswith(("#", "&"))
            and not m["nick"].lower().endswith("serv"))

# `!sillabah` apre, `!sillabah 15` apre con 15 manche, `!sillabah stop` chiude.
# Niente `!stop` nudo: l'impiccato sta negli stessi canali e lo raccoglie gia',
# e un `!stop` per chiudere il trivial chiuderebbe anche le sillabe.
CMD_PAT = re.compile(r'^!\s*sillabah(?:\s+(?P<arg>\S+))?\s*$', re.IGNORECASE)
SILLABA_PAT = re.compile(r'^!\s*sillaba\s*$', re.IGNORECASE)
VALORI_PAT = re.compile(r'^!\s*valori\s*$', re.IGNORECASE)
PUNTI_PAT = re.compile(r'^!\s*punti(?:\s+(?P<nick>\S+))?\s*$', re.IGNORECASE)
# Stesso schema dell'impiccato e dello sparasuore: nudo rispondono tutti i
# giochi vivi, con l'argomento solo quello nominato.
CLASSIFICA_PAT = re.compile(
    r'^!\s*classifica(?:\s+(?P<game>\S+))?\s*$', re.IGNORECASE)
MY_GAME = ("sillabah", "sillaba", "sillabe")
DELETE_PAT = re.compile(r'^-\s*delete\s*$', re.IGNORECASE)
# Le giocate di Cards Against Humanity in query a MikeBongiorno (cah.py).
CAH_QUERY_PAT = re.compile(r'^pl\s+[\d\s,]+$', re.IGNORECASE)


def fold(s):
    """Casefold + via gli accenti: 'È' e 'e' sono la stessa lettera, sia per
    riconoscere la parola sia per contarne i punti."""
    d = unicodedata.normalize("NFD", s)
    return "".join(c for c in d if not unicodedata.combining(c)).casefold()


def norm_parola(s):
    """La forma di confronto di una parola scritta da un giocatore.

    Su IRC l'accento finale si scrive spesso con l'apostrofo — `perche'`,
    `piu'` — e rifiutarlo come «solo lettere» sarebbe pignoleria: un apostrofo
    in CODA vale come accento, e l'accento sparisce comunque nel fold."""
    f = fold(s.strip())
    if len(f) > 1 and f[-1] in "'’`":
        f = f[:-1]
    return f


def punteggio(forma):
    """La somma dei valori delle lettere di una forma gia' normalizzata."""
    return sum(VALORI.get(c, 0) for c in forma)


# --------------------------------------------------------------- dizionario

class Dizionario:
    """Le parole ammesse, in forma normalizzata, con la grafia da mostrare.

    Stanno in sqlite, non in RAM: 845k forme in un dict erano 200 MB di
    processo (vjt, 2026-10-11: «perche' tenerlo in memoria»). Il default e'
    un db in memoria, per i test; load_parole() ne apre uno su file.

    Due grafie possono dare la stessa forma (`papa` e `papà`): per il gioco
    sono la stessa parola, e si mostra la prima in ordine alfabetico."""

    def __init__(self, parole=None, db=":memory:"):
        self.db = sqlite3.connect(db)
        if parole is not None:
            self.riempi(parole)
        self.grafia = _Grafia(self.db)
        # Solo le parole che possono stare sotto un limite: sono queste che si
        # pescano e si scandiscono a ogni scelta di sillaba.
        self.n_corte = self.db.execute(
            "SELECT count(*) FROM parole WHERE len <= ?", (LIMITE_MAX,)).fetchone()[0]

    def riempi(self, parole):
        with self.db:
            self.db.execute("DROP TABLE IF EXISTS parole")
            self.db.execute("CREATE TABLE parole (forma TEXT PRIMARY KEY, "
                            "grafia TEXT NOT NULL, len INTEGER NOT NULL)")
            # INSERT OR IGNORE in ordine alfabetico: vince la prima grafia.
            self.db.executemany(
                "INSERT OR IGNORE INTO parole VALUES (?, ?, ?)",
                ((f, p, len(f)) for p in sorted(parole)
                 for f in [fold(p)] if re.fullmatch(r"[a-z]+", f)))

    def __contains__(self, forma):
        return forma in self.grafia

    def __len__(self):
        return len(self.grafia)

    def a_caso(self, rng=random):
        """Una forma corta a caso. L'OFFSET scandisce, ma una volta per
        tentativo su un db da 10 MB e' un millisecondo."""
        return self.db.execute(
            "SELECT forma FROM parole WHERE len <= ? LIMIT 1 OFFSET ?",
            (LIMITE_MAX, rng.randrange(self.n_corte))).fetchone()[0]

    def soluzioni(self, sillaba, limite):
        return [f for (f,) in self.db.execute(
            "SELECT forma FROM parole WHERE len <= ? AND instr(forma, ?) > 0 "
            "ORDER BY forma", (limite, sillaba))]


class _Grafia(Mapping):
    """forma -> grafia da mostrare, letta dal db a richiesta."""

    def __init__(self, db):
        self.db = db

    def __getitem__(self, forma):
        r = self.db.execute("SELECT grafia FROM parole WHERE forma = ?",
                            (forma,)).fetchone()
        if r is None:
            raise KeyError(forma)
        return r[0]

    def __iter__(self):
        return (f for (f,) in self.db.execute("SELECT forma FROM parole"))

    def __len__(self):
        return self.db.execute("SELECT count(*) FROM parole").fetchone()[0]


def load_parole(path=None, db=None):
    """Il dizionario su file sqlite, ricostruito dal txt quando il txt cambia.

    Il db sta con lo stato (SILLABAH_HOME), non col codice: nella jail il
    codice e' di sola lettura per il bot. La firma del txt (dimensione e
    mtime) sta nel db: un deploy del txt nuovo lo fa ricostruire al riavvio."""
    path = Path(path or PAROLE)
    db = Path(db or DB)
    st = path.stat()
    firma = f"{st.st_size}:{st.st_mtime_ns}"
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        ok = con.execute("SELECT v FROM meta WHERE k = 'firma'").fetchone() == (firma,)
        con.close()
    except sqlite3.Error:
        ok = False
    if not ok:
        tmp = db.with_suffix(".tmp")
        tmp.unlink(missing_ok=True)
        out = []
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line and not line.startswith("#"):
                    out.append(line)
        if not out:
            raise SystemExit(f"sillabah: dizionario vuoto: {path}")
        d = Dizionario(out, db=str(tmp))
        with d.db:
            d.db.execute("CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT)")
            d.db.execute("INSERT INTO meta VALUES ('firma', ?)", (firma,))
        d.db.close()
        del out, d
        tmp.replace(db)
    return Dizionario(db=str(db))


def valuta(diz, sillaba, limite, testo):
    """(forma, None) se la parola vale, (None, motivo) se no.

    L'ordine dei controlli e' quello che serve a chi gioca: prima le cose che
    puo' correggere da solo (sillaba, lunghezza), per ultimo il dizionario."""
    t = (testo or "").strip()
    if not t:
        return None, "parola vuota"
    if len(t.split()) > 1:
        return None, "una parola sola"
    f = norm_parola(t)
    if not re.fullmatch(r"[a-z]+", f):
        return None, "solo lettere, niente numeri o simboli"
    if sillaba not in f:
        return None, f"non contiene {sillaba.upper()}"
    if len(f) > limite:
        return None, f"troppo lunga: {len(f)} lettere, il limite e' {limite}"
    if f not in diz:
        return None, "non e' nel dizionario"
    return f, None


def scegli_sillaba(diz, rng=random, escludi=(), tentativi=300):
    """(sillaba, limite, migliore, n_soluzioni), o None se non si trova niente.

    La sillaba si pesca DENTRO una parola vera, non a caso fra le lettere:
    cosi' `qz` non esce mai, e quella che esce si controlla subito contro il
    dizionario. Il limite e' a caso fra LIMITE_MIN e LIMITE_MAX, ma mai sotto
    sillaba+2. Sotto MIN_SOLUZIONI parole valide si ripesca."""
    escludi = set(escludi)
    for _ in range(tentativi):
        w = diz.a_caso(rng)
        n = rng.choice(SILLABA_LEN)
        if len(w) < n:
            continue
        i = rng.randrange(len(w) - n + 1)
        s = w[i:i + n]
        if s in escludi:
            continue
        limite = rng.randint(max(LIMITE_MIN, n + 2), LIMITE_MAX)
        sol = diz.soluzioni(s, limite)
        if len(sol) >= MIN_SOLUZIONI:
            best = max(sol, key=lambda f: (punteggio(f), -len(f), f))
            return s, limite, best, len(sol)
    return None


# ------------------------------------------------------------------ stato

def empty_state():
    return {"scores": {}, "games": {}}


def load_state(path):
    if path.exists():
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(d.get("scores"), dict):
                if not isinstance(d.get("games"), dict):
                    d["games"] = {}
                return d
        except Exception:
            pass
    return empty_state()


def save_state(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ------------------------------------------------------------------ righe

MEDAGLIE = ("🥇", "🥈", "🥉")
ZWSP = "​"


def sprinkle(s, n=1):
    """Uno spazio a larghezza zero dentro il nick: la riga di fine manche esce
    ogni minuto e nomina fino a TOP giocatori, e senza questo li evidenzierebbe
    tutti a ogni giro."""
    out = list(s)
    for _ in range(n):
        if len(out) < 2:
            break
        out.insert(random.randrange(1, len(out)), ZWSP)
    return "".join(out)


def podio(rows, top=10):
    """Stessa forma del podio dell'impiccato: medaglie ai primi tre."""
    return " · ".join(
        f"{MEDAGLIE[i] if i < len(MEDAGLIE) else '·'} {sprinkle(r['nick'])} {r['points']}"
        for i, r in enumerate(rows[:top]))


def joined_lines(parts, limit=MAX_LINE, sep="  ·  "):
    """Le parti nel minor numero di righe sotto `limit` BYTE, tagliando solo
    fra una parte e l'altra. Copia di quella dell'impiccato: i due giochi sono
    processi separati e non si importano a vicenda."""
    out, cur = [], ""
    for p in parts:
        p = (p or "").strip()
        if not p:
            continue
        cand = f"{cur}{sep}{p}" if cur else p
        if cur and len(cand.encode("utf-8")) > limit:
            out.append(cur)
            cur = p
        else:
            cur = cand
    if cur:
        out.append(cur)
    return out


def riga_valori():
    """I valori raggruppati per punteggio: 26 coppie in fila sono illeggibili,
    sei gruppi no."""
    per = {}
    for c, v in sorted(VALORI.items()):
        per.setdefault(v, []).append(c)
    return "🔢 Valori: " + " · ".join(
        f"{v}: {' '.join(cs)}" for v, cs in sorted(per.items()))


# -------------------------------------------------------------------- gioco

class Sillabah:
    def __init__(self, net, log, fifo, state_path, chans, diz, talk=True,
                 rng=None, locks=None):
        self.net = net
        self.log = str(log)
        self.fifo = str(fifo) if fifo else ""
        self.state_path = state_path
        self.chans = {c.lower() for c in chans}
        self.diz = diz
        self.talk = talk
        self.rng = rng or random.Random()
        self.state = load_state(state_path)
        self.sent = []                  # solo per le prove
        self.notices = []               # idem, le risposte in NOTICE
        self._last_reply = {}           # nick -> istante dell'ultima risposta in query
        self._budget = deque()          # istanti delle ultime risposte in query
        # Una partita per canale fra tutti i giochi (ircgame, IRCGAME_LOCKS);
        # il Tracker serve solo a vedere se c'e' GameBot nel canale.
        self.tracker = ircgame.Tracker()
        self.lock = ircgame.ChannelLock(net, "Sillabah", locks)

    # --- uscita ---------------------------------------------------------

    def say(self, target, text, verb="SAY"):
        (self.notices if verb == "NOTICE" else self.sent).append((target, text))
        if not self.talk or not self.fifo:
            return
        try:
            fd = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            return          # nessun lettore sulla FIFO: si salta
        try:
            # SAY:sillabah — il tag viaggia sul verbo e non arriva in rete:
            # bot.py lo stampa nel bot.log, come per SAY:impiccato.
            os.write(fd, f"{verb}:sillabah {target} {text}\n".encode("utf-8"))
        except OSError:
            pass
        finally:
            os.close(fd)

    def emit(self, chan, parts):
        for line in joined_lines(parts):
            self.say(chan, line)

    def reply(self, nick, text, now, notice=False):
        """Risposta in query, col freno: vedi QUERY_GAP e QUERY_BUDGET.
        In NOTICE se il giocatore ha scritto in NOTICE: risponde dove ha
        scritto, senza aprirgli un buffer. True se e' uscita."""
        key = nick.casefold()
        last = self._last_reply.get(key)
        if last is not None and now - last < QUERY_GAP:
            return False
        n, window = QUERY_BUDGET
        while self._budget and now - self._budget[0] >= window:
            self._budget.popleft()
        if len(self._budget) >= n:
            return False
        self._budget.append(now)
        self._last_reply[key] = now
        if len(self._last_reply) > 500:
            for k, _ in sorted(self._last_reply.items(), key=lambda kv: kv[1])[:100]:
                self._last_reply.pop(k, None)
        self.say(nick, text, "NOTICE" if notice else "SAY")
        return True

    # --- partita ---------------------------------------------------------

    def game(self, chan=None):
        """La partita viva: quella di `chan`, o con chan=None quella della rete
        (ce n'e' al piu' una, vedi la docstring del modulo)."""
        games = self.state["games"]
        if chan is not None:
            return games.get(chan)
        return next(iter(games.values()), None)

    def annuncio(self, g, now):
        left = max(0, int(g["deadline"] - now))
        return (f"🔤 [{g['count']}/{g['limit_manche']}] Sillaba "
                f"{g['sillaba'].upper()}, max {g['limite']} lettere, {left}s. "
                f"Parole in query: /msg o /notice {BOT_NICK} <parola>")

    def start_game(self, chan, n, now):
        self.state["games"][chan] = {
            "chan": chan,
            "limit_manche": n,
            "count": 0,
            "idle": 0,
            "usate": [],
            "points": {},
        }
        self.start_manche(chan, now, lead=[
            f"🎲 Sillabah: {n} manche da {MANCHE_S}s. Una parola per manche, "
            f"in query; una nuova sostituisce la vecchia, `-delete` la ritira. "
            f"`!valori` i punti delle lettere."])

    def start_manche(self, chan, now, lead=()):
        g = self.state["games"][chan]
        pick = scegli_sillaba(self.diz, self.rng, escludi=g.get("usate", []))
        if pick is None:
            # Col dizionario vero non succede; con uno finto e minuscolo si',
            # e una partita senza sillabe va chiusa, non lasciata appesa.
            self.end_game(chan, lead=list(lead) + ["🤷 Finite le sillabe."])
            return
        s, limite, best, nsol = pick
        g.update({
            "fase": "manche",
            "count": int(g.get("count", 0)) + 1,
            "sillaba": s,
            "limite": limite,
            "best": best,
            "soluzioni": nsol,
            "parole": {},
            "deadline": now + MANCHE_S,
            "status_ts": now,
            "status_n": 0,
        })
        g["usate"] = list(g.get("usate", [])) + [s]
        self.emit(chan, list(lead) + [self.annuncio(g, now)])

    def classifica_manche(self, g):
        """Le parole della manche, dalla migliore. A pari punti vince chi
        l'ha mandata prima."""
        return sorted(g.get("parole", {}).values(),
                      key=lambda r: (-r["punti"], r["ts"], r["nick"].casefold()))

    def stato_manche(self, chan, now):
        """La riga di stato durante la manche: quante parole, quanto manca.
        Niente parole ne' nick: in query si gioca alla cieca."""
        g = self.state["games"][chan]
        n = len(g.get("parole", {}))
        left = int(g["deadline"] - now)
        if (n == g.get("status_n", 0) or left < STATUS_MIN_LEFT
                or now - g.get("status_ts", 0) < STATUS_GAP):
            return
        g["status_n"], g["status_ts"] = n, now
        self.say(chan, f"📝 {n} {'parola' if n == 1 else 'parole'} in gioco, "
                       f"mancano {left}s.")

    def end_manche(self, chan, now):
        """Chiude la manche: punti in classifica, riga in canale, e poi pausa,
        fine partita o stop per silenzio."""
        g = self.state["games"][chan]
        rows = self.classifica_manche(g)
        for r in rows:
            key = r["nick"].casefold()
            tot = self.state["scores"].setdefault(key, {"nick": r["nick"], "points": 0})
            tot["nick"] = r["nick"]
            tot["points"] = int(tot["points"]) + r["punti"]
            gp = g["points"].setdefault(key, {"nick": r["nick"], "points": 0})
            gp["nick"] = r["nick"]
            gp["points"] = int(gp["points"]) + r["punti"]
        head = f"🏁 Manche {g['count']}, {g['sillaba'].upper()} max {g['limite']}:"
        if rows:
            g["idle"] = 0
            elenco = " · ".join(
                f"{MEDAGLIE[i] if i < len(MEDAGLIE) else '·'} "
                f"{sprinkle(r['nick'])} {r['parola'].upper()} {r['punti']}"
                for i, r in enumerate(rows[:TOP]))
            if len(rows) > TOP:
                elenco += f" (+{len(rows) - TOP})"
            esito = f"{head} {elenco}"
        else:
            g["idle"] = int(g.get("idle", 0)) + 1
            esito = f"{head} nessuna parola."
        best = g.get("best")
        if best:
            esito += (f" 💡 Il massimo era {self.diz.grafia.get(best, best).upper()} "
                      f"({punteggio(best)}).")
        if g["idle"] >= IDLE_MANCHE:
            self.end_game(chan, lead=[esito, f"💤 {IDLE_MANCHE} manche senza parole, chiudo."])
        elif g["count"] >= g["limit_manche"]:
            self.end_game(chan, lead=[esito])
        else:
            g["fase"] = "pausa"
            g["parole"] = {}
            g["deadline"] = now + PAUSA_S
            self.emit(chan, [esito, f"⏭️ Prossima tra {PAUSA_S}s."])

    def end_game(self, chan, lead=(), head=None):
        g = self.state["games"].pop(chan, None)
        if g is None:
            return
        rows = sorted(g.get("points", {}).values(),
                      key=lambda r: (-r["points"], r["nick"].casefold()))
        head = head or f"🏁 Partita finita dopo {g.get('count', 0)} manche."
        tail = (f"🏆 Punteggio partita: {podio(rows)} — `!classifica` per la hall of fame."
                if rows else "`!sillabah` per un'altra.")
        self.emit(chan, list(lead) + [head, tail])

    def stop_game(self, chan):
        """`!sillabah stop`: la manche in corso NON si conta. Chi ha chiuso non
        ha chiesto un ultimo giro di punti."""
        self.end_game(chan, head="🛑 Chiuso, la manche in corso non conta.")

    # --- classifiche -------------------------------------------------------

    def ranking(self):
        return sorted(self.state["scores"].values(),
                      key=lambda r: (-int(r["points"]), r["nick"].casefold()))

    def classifica(self, chan):
        rows = self.ranking()
        if not rows:
            self.say(chan, "🗒️ [Sillabah] Classifica vuota. `!sillabah` per cominciare.")
            return
        # `[Sillabah]` come `[Trivial]`: su `!classifica` nudo escono le righe
        # di tutti i giochi, e senza etichetta si confondono.
        self.say(chan, f"🏆 [Sillabah] Classifica ({self.net}): {podio(rows)}")

    def punti(self, chan, nick):
        rows = self.ranking()
        key = nick.casefold()
        for i, r in enumerate(rows):
            if r["nick"].casefold() == key:
                self.say(chan, f"🧮 [Sillabah] {r['nick']}: {r['points']} punti, "
                               f"{i + 1}° su {len(rows)}.")
                return
        self.say(chan, f"🧮 [Sillabah] {nick}: nessun punto, per ora.")

    # --- ingresso: canale --------------------------------------------------

    def handle_chan(self, nick, chan, text, now):
        """Una riga di canale. True se lo stato e' cambiato (il chiamante salva)."""
        chan = chan.lower()
        if chan not in self.chans:
            return False

        k = CLASSIFICA_PAT.match(text)
        if k:
            game = (k.group("game") or "").lower()
            if not game or game in MY_GAME:
                self.classifica(chan)
            return False

        if VALORI_PAT.match(text):
            self.say(chan, riga_valori())
            return False

        p = PUNTI_PAT.match(text)
        if p:
            self.punti(chan, p.group("nick") or nick)
            return False

        g = self.game(chan)

        if SILLABA_PAT.match(text):
            if g and g.get("fase") == "manche":
                self.say(chan, self.annuncio(g, now))
            elif g:
                left = max(0, int(g["deadline"] - now))
                self.say(chan, f"⏭️ Prossima manche tra {left}s.")
            else:
                self.say(chan, "Nessuna partita in corso: `!sillabah` per cominciare.")
            return False

        m = CMD_PAT.match(text)
        if not m:
            return False
        arg = (m.group("arg") or "").lower()
        if arg == "stop":
            if g:
                self.stop_game(chan)
                return True
            return False
        if g:
            # Gia' viva qui: si ripubblica e basta, come il trivial.
            if g.get("fase") == "manche":
                self.say(chan, self.annuncio(g, now))
            else:
                left = max(0, int(g["deadline"] - now))
                self.say(chan, f"⏭️ Prossima manche tra {left}s.")
            return False
        if self.game() is not None:
            # Viva in un ALTRO canale della rete. Non lo si nomina: basta dire
            # che c'e', il perche' sta nella docstring del modulo.
            self.say(chan, "C'e' gia' una partita in un altro canale: una per "
                           "volta, le parole arrivano tutte in query.")
            return False
        if arg and not arg.isdigit():
            self.say(chan, "`!sillabah` per giocare, `!sillabah 15` per 15 manche, "
                           "`!sillabah stop` per chiudere.")
            return False
        no = ircgame.start_refusal(self.lock, self.tracker, chan, now)
        if no:
            self.say(chan, no)
            return False
        n = min(MAX_MANCHE, max(1, int(arg))) if arg else DEFAULT_MANCHE
        self.start_game(chan, n, now)
        return True

    # --- ingresso: query ---------------------------------------------------

    def handle_query(self, nick, text, now, notice=False):
        """Una riga mandata al bot in privato. True se lo stato e' cambiato."""
        if text.startswith("\x01"):
            return False                # CTCP: affare di bot.py
        text = text.strip()
        if not text:
            return False

        if VALORI_PAT.match(text):
            self.reply(nick, riga_valori(), now, notice)
            return False

        g = self.game()
        if SILLABA_PAT.match(text):
            if g and g.get("fase") == "manche":
                self.reply(nick, self.annuncio(g, now), now, notice)
            else:
                self.reply(nick, "Nessuna manche aperta adesso.", now, notice)
            return False
        if text.startswith("!"):
            # Un comando di canale scritto in query: silenzio, non e' una parola
            # e non merita un rifiuto a testa.
            return False
        if CAH_QUERY_PAT.match(text):
            # Le giocate di CaH (`pl 3`, `pl 3 5`), stesso bot, stessa query:
            # sono di cah.py, qui si tace.
            return False

        if not g:
            self.reply(nick, "Nessuna partita in corso: si comincia con "
                             "`!sillabah` in canale.", now, notice)
            return False
        if g.get("fase") != "manche":
            left = max(0, int(g["deadline"] - now))
            self.reply(nick, f"Manche chiusa, la prossima tra {left}s.", now, notice)
            return False

        key = nick.casefold()
        parole = g.setdefault("parole", {})
        if DELETE_PAT.match(text):
            old = parole.pop(key, None)
            if old:
                self.reply(nick, f"🗑️ Ritirata: {old['parola'].upper()}.", now, notice)
                return True
            self.reply(nick, "Non avevi parole in gioco.", now, notice)
            return False

        forma, motivo = valuta(self.diz, g["sillaba"], g["limite"], text)
        if forma is None:
            self.reply(nick, f"❌ {motivo}.", now, notice)
            return False
        old = parole.get(key)
        pts = punteggio(forma)
        parole[key] = {"nick": nick, "parola": text.strip(), "forma": forma,
                       "punti": pts, "ts": now}
        msg = f"✅ Parola accettata: {text.strip().upper()} ({pts} punti)"
        if old and old["forma"] != forma:
            msg += f", sostituisce {old['parola'].upper()}"
        self.reply(nick, msg + ".", now, notice)
        return True

    def handle(self, nick, target, text, now=None, notice=False):
        now = float(now if now is not None else time.time())
        if target.startswith(("#", "&")):
            return self.handle_chan(nick, target, text.strip(), now)
        return self.handle_query(nick, text, now, notice)

    # --- tempo -------------------------------------------------------------

    def tick(self, now=None):
        """Fa scadere manche e pause. True se lo stato cambia."""
        now = float(now if now is not None else time.time())
        changed = False
        for chan, g in list(self.state["games"].items()):
            dl = float(g.get("deadline", now))
            if now - dl > STALE_S:
                # Processo giu' per un pezzo: si butta in silenzio.
                self.state["games"].pop(chan, None)
                changed = True
            elif now >= dl:
                if g.get("fase") == "manche":
                    self.end_manche(chan, now)
                else:
                    self.start_manche(chan, now)
                changed = True
            elif g.get("fase") == "manche":
                self.stato_manche(chan, now)
        return changed

    def next_wake(self, now):
        """Quanti secondi puo' dormire il select prima della prossima scadenza."""
        dls = [float(g.get("deadline", now)) for g in self.state["games"].values()]
        wait = min([TICK] + [d - now for d in dls])
        return max(0.2, wait)

    # --- daemon ------------------------------------------------------------

    def run(self):
        p = subprocess.Popen(["tail", "-F", "-n", "0", self.log],
                             stdout=subprocess.PIPE, text=True, errors="replace")
        time.sleep(1)                   # il tail prima del NAMES
        for chan in sorted(self.chans):  # chi c'e' (GameBot?) nei canali
            if self.talk:
                ircgame.fifo_write(self.fifo, f"RAW:sillabah NAMES {chan}\n")
        try:
            while True:
                r, _, _ = select.select([p.stdout], [], [], self.next_wake(time.time()))
                changed = False
                if r:
                    line = p.stdout.readline()
                    if not line:
                        break
                    self.tracker.feed(line)
                    m = PRIVMSG_PAT.search(line)
                    if m and is_input(m):
                        changed |= self.handle(m.group("nick"), m.group("target"),
                                               m.group("text"),
                                               notice=m.group("verb") == "NOTICE")
                changed |= self.tick()
                if changed:
                    save_state(self.state_path, self.state)
                # i lucchetti seguono le partite, qualunque strada le chiuda
                self.lock.sync(self.state["games"])
        finally:
            p.terminate()


# ------------------------------------------------------------------- selftest

def selftest():
    """Un giro intero con un dizionario finto: niente rete, FIFO o disco."""
    import tempfile
    parole = [f"ri{chr(97 + i)}{chr(97 + j)}" for i in range(26) for j in range(26)]
    diz = Dizionario(parole + ["riso", "rima"])
    with tempfile.TemporaryDirectory() as td:
        g = Sillabah("test", "/dev/null", "", Path(td) / "s.json", {"#t"}, diz,
                     talk=False, rng=random.Random(1))
        assert g.handle("a", "#t", "!sillabah 1", now=0) is True
        s = g.game("#t")["sillaba"]
        w = next(f for f in diz.soluzioni(s, g.game("#t")["limite"]))
        assert g.handle("a", "MikeBongiorno", w, now=1) is True
        assert g.sent[-1][0] == "a" and "accettata" in g.sent[-1][1], g.sent[-1]
        g.tick(now=MANCHE_S)
        assert g.game("#t") is None
        assert g.state["scores"]["a"]["points"] == punteggio(w)
    print("selftest ok")


# ----------------------------------------------------------------------- main

def build(net, talk=True, diz=None):
    cfg = NETS[net]
    chans = os.environ.get("SILLABAH_CHANS")
    return Sillabah(
        net,
        os.environ.get("SILLABAH_LOG", cfg["log"]),
        os.environ.get("SILLABAH_FIFO", cfg["fifo"]),
        Path(os.environ.get("SILLABAH_STATE", cfg["state"])),
        {c.strip() for c in chans.split(",")} if chans else cfg["chans"],
        diz if diz is not None else load_parole(),
        talk=talk,
    )


def main():
    argv = sys.argv[1:]
    net = "azzurra"
    if "--net" in argv:
        i = argv.index("--net")
        net = argv[i + 1]
        del argv[i:i + 2]
    cmd = argv[0] if argv else "daemon"
    if cmd == "selftest":
        selftest()
        return
    if net not in NETS:
        raise SystemExit(f"sillabah: rete sconosciuta {net!r} (usa: {', '.join(NETS)})")
    if cmd == "classifica":
        g = build(net, talk=False, diz=Dizionario([]))
        rows = g.ranking()
        if not rows:
            print("(nessun punto)")
        for i, r in enumerate(rows):
            medaglia = MEDAGLIE[i] if i < len(MEDAGLIE) else " ·"
            print(f"  {medaglia} {r['nick']:<20} {r['points']}")
    elif cmd == "prova":
        diz = load_parole()
        n = int(argv[1]) if len(argv) > 1 else 10
        print(f"dizionario: {len(diz)} forme, {diz.n_corte} entro {LIMITE_MAX} lettere")
        for _ in range(n):
            s, limite, best, nsol = scegli_sillaba(diz)
            print(f"  {s.upper():<4} max {limite}  {nsol:>5} soluzioni  "
                  f"migliore {diz.grafia[best]} ({punteggio(best)})")
    else:
        build(net).run()


if __name__ == "__main__":
    main()
