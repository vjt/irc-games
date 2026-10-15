#!/usr/bin/env python3
"""impiccato — quiz a maschera sul modello di ShBot (#cybernet, IRCnet).

Ordine di vjt: #sbiffo 2026-08-24 21:20 «confermo; e il bot delle domande tipo
l'impiccato», dettagli alle 21:27 («sidecar si; le domande GENERALE, un gran
bel catalogo incluse le bestemmie; si persistente per nick») e alle 21:29
(«anche su cybernet ovviamente»).

Stessa forma di next_counter.py / cena_counter.py: tail del bot.log, match dei
PRIVMSG in canale, stato su json, risposte scritte sulla FIFO del bot. `bot.py`
NON viene toccato.

DUE RETI, DUE PROCESSI. Azzurra e IRCnet sono istanze separate di bot.py con
log e FIFO propri, quindi il sidecar e' parametrico sulla rete e se ne lancia
uno per ciascuna. I punteggi sono per-rete di proposito: su IRCnet non esistono
i services, un nick li' non prova niente (vedi feedback_spoofed_identity_refusal)
e mescolare le classifiche regalerebbe a chiunque i punti di un omonimo.

Meccanica, MISURATA sullo scrollback di ShBot e non dedotta — la spec completa
con le righe di log sta in docs/impiccato_spec.md:

  * Ogni giro sono due righe: la domanda, poi la maschera della risposta. Un
    `-` per ogni lettera, gli spazi fra le parole restano visibili (la
    lunghezza di ogni parola e' informazione data in partenza).
  * Lettera nuda in canale: se c'e', si scoprono TUTTE le sue occorrenze e la
    maschera viene ripubblicata. Se non c'e', SILENZIO — niente forca, niente
    contatore di errori, niente eliminazione. Dell'impiccato c'e' solo la
    maschera, ed e' voluto: cosi' il gioco non ha perdenti e il canale non si
    riempie di "hai sbagliato".
  * Parola intera giusta: scopre quella parola (ShBot lo fa — `crittografia`
    alle 18:16:34 UTC ha portato la maschera a `Crittografia -----------`).
  * Risposta intera giusta: `Esatto <nick>, la risposta era: <X>!!!` e la
    domanda dopo parte subito.
  * `.v` scopre tutte le vocali, `.h` scopre UNA lettera a caso fra quelle
    ancora coperte.
  * Il case della risposta e' preservato: `ReplicaSet`, `Token JWT`.

Quello che ShBot fa e qui NON e' replicato, perche' non l'ho misurato: il
comando che mostra il punteggio. `!classifica` e' roba mia, non sua.

Aggiunte mie, dichiarate: il gioco si ferma da solo dopo IDLE_STOP secondi di
silenzio invece di sparare domande per sempre a canale vuoto, e si riavvia con
`!trivial <set>`. Un bot che parla in eterno in un canale e' un flood, non un
gioco.

I SET A TEMA sono ordine di vjt (#sbiffo 21:57-21:59, «tipo il trivial
pursuit»): si gioca con `!trivial <set>` e il trigger nudo elenca soltanto,
perche' «si inizia un gioco solo scegliendo il set di domande a tema». Vedi
SET_ALIASES e resolve_set(). `!impiccati` resta accettato come alias.

CLI:
  impiccato.py --net azzurra            avvia il daemon su Azzurra (#sbiffo)
  impiccato.py --net ircnet             avvia il daemon su IRCnet (#cybernet)
  impiccato.py --net azzurra classifica stampa la classifica e esce
  impiccato.py --net azzurra selftest   esercita la meccanica senza rete ne' FIFO

Env override (un dry run non deve poter parlare):
  IMPICCATO_LOG    path del bot.log da seguire
  IMPICCATO_FIFO   path della FIFO del bot ('' zittisce del tutto)
  IMPICCATO_STATE  path dello stato json
  IMPICCATO_CHANS  canali, separati da virgola
"""
import json
import os
import random
import re
import select
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

import ircgame

# La casa del gioco: dove stanno domande e stato. Era la directory del file, che
# andava bene finche' il codice viveva nel checkout del bot; ora il gioco sta in
# un repo suo e gira in una jail su m42, dove il codice e i dati non sono nello
# stesso posto. IMPICCATO_HOME li separa.
REPO = Path(os.environ.get("IMPICCATO_HOME", Path(__file__).resolve().parent))
DOMANDE = Path(os.environ.get("IMPICCATO_DOMANDE", REPO / "impiccato_domande.json"))


def _default_nets():
    """Le reti storiche, quando la config non arriva dall'ambiente.

    Una voce per rete: sono processi di `bot.py` distinti, con log, FIFO e canali
    separati, quindi anche stato separato."""
    return {
        "azzurra": {
            "log": REPO / "bot.log",
            "fifo": REPO / "bot.send",
            "state": REPO / "impiccato_state.json",
            # #sniffo aggiunto per ordine di vjt (#sbiffo 2026-08-24 21:54),
            # #italia per ordine di vjt (#sniffo 2026-08-29 21:43).
            # Le partite sono per canale: vedi load_state().
            "chans": {"#sbiffo", "#sniffo", "#italia"},
        },
        "ircnet": {
            "log": REPO / "bot.ircnet.log",
            "fifo": REPO / "bot.send.ircnet",
            "state": REPO / "impiccato_state.ircnet.json",
            "chans": {"#cybernet"},
        },
    }


def load_nets():
    """`IMPICCATO_NETS` se c'e', altrimenti le reti storiche.

    Stesso formato di SUORE_NETS, apposta — i due giochi si configurano allo
    stesso modo e chi legge una unit sa gia' leggere l'altra:

        nome|path/bot.log|path/bot.send|#chan1,#chan2 ; nome2|...

    Lo stato di ogni rete e' `impiccato_state.<rete>.json` sotto REPO, tranne
    che per 'azzurra', che tiene il nome storico senza suffisso: rinominarlo
    perderebbe la classifica di #sniffo, #sbiffo e #italia.

    Il gioco resta UN PROCESSO PER RETE (il template systemd/rc.d fa il resto):
    il suo loop e' per rete, con stato e tail suoi. Non e' il caso delle suore,
    dove il processo era per CANALE — quello si', era il difetto."""
    raw = os.environ.get("IMPICCATO_NETS", "").strip()
    if not raw:
        return _default_nets()
    nets = {}
    for record in raw.split(";"):
        record = record.strip()
        if not record:
            continue
        parts = record.split("|")
        if len(parts) != 4:
            raise SystemExit(f"impiccato: record malformato in IMPICCATO_NETS: "
                             f"{record!r} — serve nome|log|fifo|canali")
        name, log, fifo, chanspec = (p.strip() for p in parts)
        chans = {c.strip().lower() for c in chanspec.split(",") if c.strip()}
        if not chans:
            raise SystemExit(f"impiccato: la rete {name!r} non ha canali")
        suffix = "" if name == "azzurra" else f".{name}"
        nets[name] = {"log": Path(log), "fifo": Path(fifo), "chans": chans,
                      "state": REPO / f"impiccato_state{suffix}.json"}
    return nets


NETS = load_nets()

# Silenzio in canale dopo il quale la partita si chiude da sola.
IDLE_STOP = 600
# Ogni quanto il loop si sveglia comunque, per poter far scadere l'idle anche
# quando sul log non passa piu' niente.
TICK = 20
# Le lettere che `.v` scopre in un colpo solo.
VOCALI = set("aeiou")
# Quante domande dura una partita se nessuno chiede altro. Ordine di vjt (DM
# 2026-08-25 16:07): «terminare la partita di default a 30 domande a meno che
# non si passi altro limite ad inizio partita». Senza un fondo la partita
# durava finche' il canale non si stancava, e il `!stop` era l'unica uscita.
# Portato da 30 a 10 su suo ordine (#sbiffo 2026-08-25 19:56): 30 domande sono
# una maratona, il canale si stanca prima del traguardo.
DEFAULT_LIMIT = 10
# Tetto in BYTE di una riga accorpata. Il flood non si misura in caratteri, si
# misura in PRIVMSG: saBOTage ha kickato il bot da #italia (2026-09-03 21:03,
# «flood») perche' una risposta indovinata ne sparava TRE di fila — esito,
# domanda nuova, maschera — e chi gioca risponde ogni tre secondi, cioe' una
# riga al secondo tonda. Accorpando, la stessa partita ne manda una sola.
# 380 e non 400: il margine copre il prefisso `PRIVMSG <chan> :` che il server
# conta dentro i 512 della riga IRC.
MAX_LINE = 380

# Le righe che spostano gli @ di un canale. Il gioco non chiede mai a nessuno
# chi e' op: lo ricostruisce dal log, come farebbe un client — NAMES (353/366)
# per la fotografia, MODE/NICK/PART/KICK/QUIT per i movimenti. All'avvio
# chiede un NAMES per canale, sennò fino al prossimo join non saprebbe niente.
EVENT_PAT = re.compile(
    r'< :(?P<src>[^!\s]+)(?:!\S+)?\s+(?P<cmd>\S+)\s*(?P<rest>.*?)\s*$')
# I modi di canale che si mangiano un argomento sia col + sia col - (bahamut).
# `l` lo prende solo col +, e va trattato a parte.
MODE_ARGS = set("ovhbeIkqa")

PRIVMSG_PAT = re.compile(
    r'< :(?P<nick>[^!@\s]+)!(?P<ident>[^@\s]+)@(?P<host>\S+)\s+'
    r'PRIVMSG\s+(?P<chan>#\S+)\s+:(?P<text>.*?)$'
)

# Il trigger NON e' `!impiccato`: su #cybernet lo raccoglie anche TeRmoLiNo,
# che ha un impiccato suo, e partivano due partite in parallelo con le lettere
# nude che cadevano in tutte e due (misurato in canale alle 21:37, vjt: «cambio
# trigger», e il nome e' suo: «usiamo !impiccati», 21:39). Non collide con
# nessuno degli altri bot del canale.
#
# 21:58, sempre vjt: «e cambiamo il trigger in !trivial». `!impiccati` resta
# come alias e basta: cambiare il nome sotto il naso di chi sta giocando su
# #cybernet significa un bot che smette di rispondere senza dire perche'.
# Il numero finale e' il limite di domande della partita: `!trivial nerd 50`.
# Il gruppo `set` e' lazy, quindi le cifre in coda finiscono in `limit` e non
# dentro il nome del set.
#
# `!trivia` e' alias di `!trivial` (ordine di vjt, #sniffo 2026-08-25 23:23,
# dopo l'obiezione di peluche: in inglese il quiz e' `trivia`, `trivial` vuol
# dire banale). La `l` finale resta opzionale nel pattern invece di aggiungere
# una terza alternativa: una `?` non puo' divergere dal ramo accanto.
#
# `!allegria` e' il terzo alias (ordine di vjt, #sbiffo 2026-09-09 23:51: «serve
# il trigger !allegria», «alias di !trivial»): quando il quiz lo conduce
# MikeBongiorno, il comando che apre la partita e' il suo grido.
CMD_PAT = re.compile(
    r'^!\s*(?:trivial?|impiccati|allegria)'
    r'(?:\s+(?P<set>.+?))?(?:\s+(?P<limit>\d{1,3}))?\s*$',
    re.IGNORECASE)
# `classifica` e' un nome che il canale condivide con lo sparasuore: nudo
# rispondono tutti i giochi vivi, con l'argomento risponde solo quello
# nominato (schema di vjt, #sbiffo 20:24). Ogni sidecar riconosce solo il
# proprio nome e tace sugli altri: nessuno sa chi altro e' in ascolto.
CLASSIFICA_PAT = re.compile(
    r'^!\s*classifica(?:\s+(?P<game>\S+))?\s*$', re.IGNORECASE)
MY_GAME = ("impiccato", "impiccati", "trivia", "trivial", "allegria")
# `!impiccati stop` va accettato quanto `!stop`: gosub ha scritto la forma
# lunga al primo giro (#cybernet 21:38) e cadeva nel vuoto, che si legge come
# un bot che ignora chi vuole chiudere.
STOP_PAT = re.compile(
    r'^!\s*(?:stop|basta|(?:trivial?|impiccati|allegria)\s+stop)\s*$',
    re.IGNORECASE)

# I set a tema. Ordine di vjt (#sbiffo 2026-08-24 21:57-21:59): «facciamo in
# modo che il set di domande si possa scegliere all'inizio del gioco, tipo il
# trivial pursuit», «!trivial tutte per inserirle tutte», «!trivial nerd per i
# kit di domande nerd», «!trivial babbani per il resto non-nerd».
#
# `nerd` e' l'elenco esplicito; `babbani` e' il suo complemento calcolato a
# runtime, cosi' una categoria nuova nel JSON finisce automaticamente da una
# parte e non sparisce dal gioco perche' nessuno si e' ricordato di elencarla.
NERD_CATS = {"unix", "rete", "sicurezza", "programmazione", "web", "irc",
             "devops", "storia", "db"}
# Fuori da `babbani` anche se non sono nerd: ordine di vjt (#italia 2026-08-29
# 21:46, «nel babbani set non ci vanno le bestemmie») dopo che `goliardia` e'
# uscita su un canale ufficiale. Restano raggiungibili con `!trivial goliardia`
# e dentro `tutte`: e' il complemento che le esclude, non il gioco.
BABBANI_EXCLUDE = {"goliardia"}
# Opt-out per canale: categorie che in QUEL canale non escono mai, nemmeno con
# `!trivial tutte` e nemmeno chiedendole per nome — la richiesta viene negata.
# Ordine di vjt (#italia 2026-09-03 20:48-20:49) dopo che `!trivial tutte` ha
# rimesso in gioco le bestemmie su un canale ufficiale: togliere una categoria
# dal complemento `babbani` non basta, `tutte` la ripescava lo stesso.
CHAN_EXCLUDE = {"#italia": {"goliardia"}}
# Nomi alternativi accettati per un set. Chi scrive `!trivial bestemmie` non
# deve trovare il vuoto.
SET_ALIASES = {
    "tutto": "tutte", "all": "tutte", "misto": "tutte",
    "bestemmie": "goliardia", "goliardiche": "goliardia",
    "tech": "nerd", "tecniche": "nerd", "informatica": "nerd",
    "babbano": "babbani", "non-nerd": "babbani",
    "generale": "cultura", "scarpe": "moda", "vestiti": "moda",
    "religione": "religioni", "animali": "natura", "cibo": "cucina",
}
# `!help` / `!giochi`: l'elenco dei comandi di TUTTI i giochi di MikeBongiorno,
# in canale, con cooldown per canale. Richiesta del founder di #italia, via
# libera di vjt (2026-10-10): «un comando !help per i giochi». Prima andava
# in NOTICE al nick, ma grappa le butta nel tab server: in canale (vjt+Malekith
# 2026-10-11), e il cooldown tiene a bada chi lo martella. Risponde
# SOLO questo sidecar: sillabah.py sta sullo stesso log e la stessa FIFO, e se
# rispondessero tutti e due arriverebbe l'help doppio. Per questo il testo
# copre anche Sillabah, e tests/test_help.py controlla che ogni comando
# elencato qui combaci col pattern vero del gioco a cui appartiene.
# `!giochi` e' alias secco: con tre righe in tutto, spezzare "elenco dei
# giochi" da "elenco dei comandi" darebbe due risposte quasi uguali.
# Niente backtick: IRC li mostra cosi' come sono.
HELP_PAT = re.compile(r'^!\s*(?:help|giochi)\s*$', re.IGNORECASE)
HELP_LINES = (
    "\x02Giochi di MikeBongiorno\x02: \x02Trivial\x02 (quiz a "
    "impiccato) e \x02Sillabah\x02 (parole con la sillaba). "
    "\x02\x0310!classifica\x03\x02 [trivial|sillabah] per i punti.",
    "\x02Trivial\x02: \x02\x0310!trivial\x03\x02 <set> [n] apre una "
    "partita da n domande (alias \x02\x0310!trivia\x03\x02, \x02\x0310!allegria\x03\x02), "
    "\x02\x0310!trivial\x03\x02 da solo elenca i set. In gioco: una lettera o la "
    "risposta intera, \x02\x0310.h\x03\x02 scopre una lettera, \x02\x0310.v\x03\x02 le "
    "vocali, \x02\x0310!skip\x03\x02 (solo op) salta la domanda, "
    "\x02\x0310!stop\x03\x02 chiude.",
    "\x02Sillabah\x02: \x02\x0310!sillabah\x03\x02 [n] apre n manche, "
    "\x02\x0310!sillabah stop\x03\x02 chiude. Le parole si mandano \x02in query\x02 o \x02/notice\x02 "
    "al bot, \x02\x0310-delete\x03\x02 ritira la tua. \x02\x0310!sillaba\x03\x02 la manche in "
    "corso, \x02\x0310!valori\x03\x02 i punti delle lettere, \x02\x0310!punti\x03\x02 [nick] "
    "il punteggio.",
)
# Secondi fra due help allo stesso nick: chi martella `!help` non fa floodare
# il canale (le righe di help escono dalla stessa coda di quelle di gioco).
HELP_COOLDOWN = 30
# `!skip` salta la domanda in corso, e lo puo' fare solo chi ha @ in QUEL
# canale. Richiesta di Malekith (staff 2026-10-14, «bindato @»), via
# libera di vjt («si solo op»). Aperto a tutti sarebbe il modo di bruciare la
# partita agli altri: una domanda difficile la salta chi non la sa.
SKIP_PAT = re.compile(r'^!\s*skip\s*$', re.IGNORECASE)
HINT_PAT = re.compile(r'^\.\s*h\s*$', re.IGNORECASE)
VOWEL_PAT = re.compile(r'^\.\s*v\s*$', re.IGNORECASE)

NICK_ALIASES = {
    "vjt`afk": "vjt", "vjt`zzz": "vjt", "vjt42": "vjt", "vjt_": "vjt",
}


def canon_nick(n):
    return NICK_ALIASES.get(n.casefold(), n)


def fold(s):
    """Casefold + via gli accenti: 'È' e 'e' sono la stessa lettera per il
    gioco, altrimenti indovinare 'perche'' diventa un terno al lotto."""
    d = unicodedata.normalize("NFD", s)
    return "".join(c for c in d if not unicodedata.combining(c)).casefold()


def norm_answer(s):
    """Forma di confronto per un tentativo intero: niente accenti, niente
    punteggiatura, spazi collassati."""
    s = fold(s)
    s = re.sub(r'[^0-9a-z]+', ' ', s)
    return " ".join(s.split())


CONTORNO = 2   # parole in piu' tollerate intorno alla risposta giusta


def contoured(guess, accepted):
    """La risposta giusta, intera e di fila, con al massimo CONTORNO parole
    intorno: `lo strato rete` e' `rete`. Di piu' e' un tema, non una risposta."""
    gw = guess.split()
    for a in accepted:
        aw = a.split()
        if len(gw) - len(aw) > CONTORNO:
            continue
        if any(gw[i:i + len(aw)] == aw for i in range(len(gw) - len(aw) + 1)):
            return True
    return False


def is_hidden(ch):
    """Solo gli alfanumerici si nascondono. Spazi, apostrofi e trattini
    restano visibili: sono la struttura della risposta, non il contenuto."""
    return ch.isalnum()


# ---------------------------------------------------------------- catalogo

def load_domande():
    doc = json.loads(DOMANDE.read_text(encoding="utf-8"))
    out = []
    for d in doc["domande"]:
        q, a = d["q"].strip(), d["a"].strip()
        if q and a and any(is_hidden(c) for c in a):
            # `alt`: altre risposte da accettare. Una domanda tecnica ha spesso
            # due nomi giusti -- `Coda` e `fifo`, `Stack` e `pila` -- e tom ha
            # risposto `fifo` alle 21:40:32 su #cybernet trovando il silenzio.
            # Ignorare una risposta corretta e' peggio di non fare la domanda.
            out.append({"q": q, "a": a, "cat": d.get("cat", ""),
                        "alt": [x.strip() for x in d.get("alt", []) if x.strip()]})
    if not out:
        raise SystemExit("impiccato: catalogo vuoto")
    return out


# ------------------------------------------------------------------ stato

def empty_state():
    return {"scores": {}, "games": {}, "asked": []}


def load_state(path):
    """Lo stato tiene UNA partita per canale, non una per rete.

    Ordine di vjt (#sbiffo 2026-08-24 21:54): «abilitiamo il gioco anche su
    sniffo». Con la vecchia chiave `game` singola un `!impiccati` sul secondo
    canale sovrascriveva la partita viva del primo, che dal di la' si legge
    come un bot che si e' impiccato da solo. Le partite vecchie vengono
    migrate nel dizionario, non buttate: qualcuno potrebbe averla in corso.
    """
    if path.exists():
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(d.get("scores"), dict):
                games = d.get("games")
                if not isinstance(games, dict):
                    games = {}
                    old = d.get("game")
                    if isinstance(old, dict) and old.get("chan"):
                        games[old["chan"]] = old
                d["games"] = games
                d.pop("game", None)
                d.setdefault("asked", [])
                return d
        except Exception:
            pass
    return empty_state()


def save_state(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


# ----------------------------------------------------------------- maschera

def mask_of(answer, revealed):
    """La risposta con le lettere non ancora scoperte sostituite da '-'.

    `revealed` contiene caratteri gia' normalizzati con fold(), cosi' una 'E'
    scoperta scopre anche la 'e' e la 'è'. Quello che si STAMPA e' pero'
    sempre il carattere originale della risposta, che e' come ShBot conserva
    'ReplicaSet' e 'Token JWT'.
    """
    out = []
    for ch in answer:
        if not is_hidden(ch):
            out.append(ch)
        elif fold(ch) in revealed:
            out.append(ch)
        else:
            out.append("-")
    return "".join(out)


def solved(answer, revealed):
    return all(not is_hidden(c) or fold(c) in revealed for c in answer)


def hidden_letters(answer, revealed):
    return sorted({fold(c) for c in answer if is_hidden(c) and fold(c) not in revealed})


def half_shown(answer, revealed):
    """True se almeno meta' delle lettere della risposta e' gia' scoperta.

    Il tetto agli aiuti (RFE di morph, #sbiffo): oltre meta' risposta niente
    piu' lettere nude, `.h` o `.v`, solo la risposta intera. Un numero fisso
    di aiuti non regge con le risposte corte, la meta' si'. Si contano le
    posizioni, non le lettere distinte: e' meta' di quello che si vede.
    """
    hidden = [fold(c) for c in answer if is_hidden(c)]
    return 2 * sum(1 for c in hidden if c in revealed) >= len(hidden)


MEDAGLIE = ("🥇", "🥈", "🥉")
ZWSP = "​"


def sprinkle(s, n=3):
    """Infila n spazi a larghezza zero in posizioni casuali.

    Non è un vezzo: è il motivo per cui la riga di rex non si può copiare e
    rimandare per finta. Le posizioni sono interne (mai in testa o in coda),
    così la riga resta identica all'occhio."""
    out = list(s)
    for _ in range(n):
        if len(out) < 2:
            break
        out.insert(random.randrange(1, len(out)), ZWSP)
    return "".join(out)


def podio(rows, top=10):
    """Riga di punteggio col podio: medaglie ai primi tre, puntino agli altri.

    Stessa forma per il punteggio di partita e per la hall of fame (ordine di
    vjt, #sbiffo 16:25) — una funzione sola, cosi' non divergono al prossimo
    ritocco."""
    return " · ".join(
        f"{MEDAGLIE[i] if i < len(MEDAGLIE) else '·'} {sprinkle(r['nick'])} {r['points']}"
        for i, r in enumerate(rows[:top]))


def joined_lines(parts, limit=MAX_LINE, sep="  ·  "):
    """Impacchetta le parti nel minor numero di righe che stia sotto `limit`.

    Il taglio si fa sul confine fra parti, MAI dentro una: una domanda tagliata
    a meta' e' peggio di due righe (vedi feedback_no_orphan_line_tails). Una
    parte piu' lunga del limite da sola resta lunga — la spezza il bot, e li'
    il flood non c'entra: e' una riga sola comunque.

    Il limite si misura in BYTE, non in caratteri: le emoji del gioco ne
    pesano quattro l'una e a contare i caratteri si sfora senza accorgersene.
    """
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


# -------------------------------------------------------------------- gioco

class Impiccato:
    def __init__(self, net, log, fifo, state_path, chans, domande, talk=True,
                 locks=None):
        self.net = net
        self.log = str(log)
        self.fifo = str(fifo) if fifo else ""
        self.state_path = state_path
        self.chans = chans
        self.domande = domande
        self.talk = talk
        self.state = load_state(state_path)
        self.sent = []          # solo per il selftest
        self.help_seen = {}     # canale casefold -> ultimo help mandato
        self.ops = {}           # canale casefold -> nick casefold con @
        self._names = set()     # canali con un NAMES a meta' (fra 353 e 366)
        # Una partita per canale fra tutti i giochi (ircgame, IRCGAME_LOCKS);
        # il Tracker serve solo a vedere se c'e' GameBot nel canale.
        self.tracker = ircgame.Tracker()
        self.lock = ircgame.ChannelLock(net, "Trivial", locks)

    # --- uscita ---------------------------------------------------------

    def say(self, chan, text):
        self.sent.append((chan, text))
        self._fifo("SAY", chan, text)

    def _fifo(self, verb, target, text):
        if not self.talk or not self.fifo:
            return
        try:
            fd = os.open(self.fifo, os.O_WRONLY | os.O_NONBLOCK)
        except OSError:
            return          # nessun lettore sulla FIFO: si salta, non si appende
        try:
            # SAY:impiccato — the tag rides on the FIFO verb and never reaches
            # the wire; bot.py stamps it into bot.log so the stats count this
            # line as deterministic code, not as the model talking.
            os.write(fd, f"{verb}:impiccato {target} {text}\n".encode("utf-8"))
        except OSError:
            pass
        finally:
            os.close(fd)

    def emit(self, chan, parts):
        """Manda le parti accorpate: una riga sola se ci stanno."""
        for line in joined_lines(parts):
            self.say(chan, line)

    # --- ciclo delle domande --------------------------------------------

    def pick(self, cats=None, exclude=()):
        """Una domanda del tema, MAI una gia' uscita in questa partita.

        Due memorie diverse, e vanno tenute separate. `exclude` sono le
        domande della partita in corso e non si perdona mai: ordine di vjt
        (DM 2026-08-25 16:07) «non dovrebbe ri-mostrare domande gia'
        presentate in una stessa partita» — prima l'unica memoria era quella
        lunga, che su un set corto si azzerava e faceva ricomparire la stessa
        domanda a meta' partita. `asked` invece e' la varieta' FRA partite, e
        li' il riciclo va bene: quando il tema e' esaurito si pesca lo stesso.

        Torna None quando il tema non ha piu' domande nuove per questa
        partita: li' la partita e' finita, non c'e' niente da riciclare.
        """
        tema = [d for d in self.domande if cats is None or d["cat"] in cats]
        if not tema:
            tema = self.domande
        seen = set(exclude)
        # Non basta escludere la DOMANDA: nel catalogo 202 domande su 667
        # condividono la risposta con un'altra ("Quale processo ha PID 1?" e
        # "Quale processo adotta gli orfani?" fanno entrambe `init`), e in un
        # gioco a maschera due domande con la stessa risposta SONO la stessa
        # domanda — stessa fila di trattini, stessa parola da scrivere.
        # Misurato in canale alle 16:17-16:18: `IRCv3` uscito due volte in
        # trenta secondi, a domande diverse.
        seen_a = {norm_answer(d["a"]) for d in self.domande if d["q"] in seen}
        fresh = [d for d in tema
                 if d["q"] not in seen and norm_answer(d["a"]) not in seen_a]
        if not fresh:
            return None
        asked = set(self.state.get("asked", []))
        pool = [d for d in fresh if d["q"] not in asked] or fresh
        return random.choice(pool)

    def game(self, chan):
        """La partita viva in QUESTO canale, o None. Ogni canale ha la sua."""
        return self.state["games"].get(chan)

    # --- set a tema ------------------------------------------------------

    def cats(self):
        """Le categorie che esistono davvero nel catalogo caricato."""
        return {d["cat"] for d in self.domande if d["cat"]}

    def chan_exclude(self, chan):
        """Le categorie vietate in questo canale. Nessun canale = nessun veto."""
        return CHAN_EXCLUDE.get((chan or "").casefold(), set())

    def resolve_set(self, name, chan=None):
        """Nome del set -> insieme di categorie, o None se non esiste.

        `tutte` e' tutto, `nerd` e' NERD_CATS, `babbani` e' il complemento, e
        ogni singola categoria vale da sola come set.

        Con `chan` il veto del canale si applica DOPO: un set che esiste ma che
        li' resta vuoto torna l'insieme vuoto, non None. I due casi vanno detti
        diversi a chi gioca — «non esiste» e «qui no» non sono la stessa cosa.
        """
        key = fold(name).strip()
        key = SET_ALIASES.get(key, key)
        cats = self.cats()
        if key == "tutte":
            got = cats
        elif key == "nerd":
            got = cats & NERD_CATS
        elif key == "babbani":
            got = cats - NERD_CATS - BABBANI_EXCLUDE
        else:
            got = {c for c in cats if fold(c) == key}
        if not got:
            return None
        return got - self.chan_exclude(chan)

    def set_names(self, chan=None):
        """I set da mostrare a chi scrive `!trivial` e basta."""
        cats = self.cats() - self.chan_exclude(chan)
        out = ["tutte"]
        if cats & NERD_CATS:
            out.append("nerd")
        if cats - NERD_CATS - BABBANI_EXCLUDE:
            out.append("babbani")
        return out + sorted(cats)

    def elenco_set(self, chan):
        # Due righe e non una: col catalogo vero l'elenco dei set da solo vale
        # gia' 180 byte, e l'help attaccato in coda faceva 404 — oltre il
        # limite di riga, quindi troncato proprio dove c'e' scritto come si
        # gioca. Misurato, non stimato.
        self.say(chan, f"📚 Set: {' · '.join(self.set_names(chan))}.")
        self.say(chan, f"🎯 Si parte con `!trivial <set>` — es. `!trivial babbani`. "
                       f"Partita da {DEFAULT_LIMIT} domande, `!trivial babbani 25` "
                       f"per cambiarne il numero. In gioco: `.h` una lettera, "
                       f"`.v` le vocali, `!stop` chiude, `!classifica` i punti.")

    def start_round(self, chan, setname="tutte", now=None, limit=None, lead=()):
        """Apre una partita nuova nel canale e mette in gioco la prima domanda.

        `asked` e `count` vivono DENTRO la partita: sono la memoria che vjt ha
        chiesto, e muoiono con lei. Il limite si fissa qui e non si tocca piu':
        cambiarlo a meta' partita significa spostare il traguardo a chi sta
        gia' correndo.
        """
        self.state["games"][chan] = {
            "chan": chan,
            "set": setname,
            "limit": max(1, int(limit)) if limit else DEFAULT_LIMIT,
            "count": 0,
            "asked": [],
            "q": "",
            "a": "",
            "revealed": [],
            "alt": [],
            "last": int(now if now is not None else time.time()),
        }
        self.next_question(chan, now=now, lead=lead)

    def next_question(self, chan, now=None, lead=()):
        """La domanda successiva, o la fine della partita.

        Due modi di finire, e vanno detti diversi: il limite raggiunto e' il
        traguardo, il set esaurito e' un catalogo troppo corto per il limite
        chiesto. Chi gioca deve capire quale dei due gli e' capitato.

        `lead` sono le parti che precedono la domanda sulla STESSA riga —
        l'esito della risposta appena indovinata. Va passato fin dentro
        `end_game`, sennò l'ultima vittoria della partita si perde per strada.
        """
        lead = list(lead)
        g = self.state["games"][chan]
        limit = int(g.get("limit", DEFAULT_LIMIT))
        if int(g.get("count", 0)) >= limit:
            self.end_game(chan, reason="limite", lead=lead)
            return
        cats = self.resolve_set(g.get("set", "tutte"), chan)
        # Il ripiego a "tutto il catalogo" rispetta il veto del canale, sennò
        # una partita aperta prima dell'opt-out se lo mangerebbe al primo giro.
        cats = cats or (self.cats() - self.chan_exclude(chan))
        d = self.pick(cats, exclude=g.get("asked", []))
        if d is None:
            self.end_game(chan, reason="esaurite", lead=lead)
            return
        g["q"], g["a"] = d["q"], d["a"]
        g["alt"], g["revealed"] = d.get("alt", []), []
        g.pop("capped", None)
        g["asked"] = list(g.get("asked", [])) + [d["q"]]
        g["count"] = int(g.get("count", 0)) + 1
        g["last"] = int(now if now is not None else time.time())
        # La memoria lunga resta troncata al catalogo: e' varieta' fra partite,
        # non un archivio.
        self.state["asked"] = (self.state.get("asked", []) + [d["q"]])[-len(self.domande):]
        self.emit(chan, lead + [f"❓ [{g['count']}/{limit}] {d['q']}",
                                mask_of(d["a"], set())])

    def end_game(self, chan, reason="limite", lead=()):
        """Chiude la partita e stampa il punteggio DI QUESTA partita.

        La hall of fame resta su `!classifica`: a fine partita chi ha giocato
        vuole sapere chi ha vinto adesso, e la classifica generale — dove uno
        che gioca da una settimana e' irraggiungibile — se la mangia (ordine di
        vjt, #sbiffo 16:20).
        """
        g = self.state["games"].pop(chan, None)
        if not g:
            return
        n = int(g.get("count", 0))
        head = (f"🏁 Partita finita: {n} domande."
                if reason == "limite"
                else f"🏁 Finite le domande del set dopo {n}.")
        rows = sorted(g.get("points", {}).values(),
                      key=lambda r: (-r["points"], r["nick"].casefold()))
        tail = (f"🏆 Punteggio partita: {podio(rows)} — "
                f"`!classifica` per la hall of fame."
                if rows
                else "😴 Punteggio partita: nessuno ha indovinato niente.")
        self.emit(chan, list(lead) + [f"{head} `!trivial <set> [n]` per un'altra.",
                                      tail])

    def stop_round(self, chan, reason="idle"):
        """reason: 'idle' = scaduta per silenzio, 'richiesto' = qualcuno ha
        chiesto !stop, 'quiet' = chiudi e non dire niente. Tenerle distinte
        conta: dire "nessuno risponde piu'" a chi ha appena scritto !stop e'
        una bugia in faccia a chi stava rispondendo eccome."""
        g = self.state["games"].pop(chan, None)
        if not g or reason == "quiet":
            return
        head = ("💤 Nessuno risponde piu'." if reason == "idle" else "🛑 Chiuso.")
        self.say(chan, f"{head} La risposta era: {g['a']}. "
                       f"`!trivial <set> [n]` per ripartire.")

    def win(self, chan, nick, now=None):
        g = self.state["games"][chan]
        answer = g["a"]
        key = canon_nick(nick).casefold()
        row = self.state["scores"].setdefault(key, {"nick": canon_nick(nick), "points": 0})
        row["nick"] = canon_nick(nick)
        row["points"] += 1
        # Punteggio DELLA partita, separato dalla hall of fame: a fine partita
        # interessa chi ha vinto stasera, non chi gioca da una settimana
        # (ordine di vjt, #sbiffo 16:20). Vive dentro `g`, quindi muore con lei.
        pts = g.setdefault("points", {})
        pts[key] = {"nick": canon_nick(nick), "points": int(pts.get(key, {}).get("points", 0)) + 1}
        # L'esito NON esce da solo: viaggia in testa alla riga della domanda
        # nuova. Tre PRIVMSG per ogni risposta indovinata sono il flood che si
        # e' preso il kick da #italia — vedi MAX_LINE.
        # La partita NON si ricrea a ogni vittoria: e' la stessa che va avanti,
        # e con lei il conteggio e l'elenco delle domande gia' uscite. Si resta
        # nel tema scelto all'inizio, che vive dentro `g`.
        self.next_question(
            chan, now=now,
            lead=[f"✅ Esatto {canon_nick(nick)}, la risposta era: {answer}!!!"])

    def reveal(self, chan, letters):
        """Scopre un insieme di lettere. True se almeno una era coperta."""
        g = self.state["games"][chan]
        rev = set(g["revealed"])
        want = {fold(c) for c in letters}
        present = {fold(c) for c in g["a"] if is_hidden(c)}
        new = (want & present) - rev
        if not new:
            return False
        rev |= new
        g["revealed"] = sorted(rev)
        return True

    def after_reveal(self, chan, nick, now=None):
        """Ripubblica la maschera e chiude il giro se e' venuta fuori intera."""
        g = self.state["games"][chan]
        if solved(g["a"], set(g["revealed"])):
            self.win(chan, nick, now=now)
        else:
            self.say(chan, mask_of(g["a"], set(g["revealed"])))

    def capped(self, chan):
        """True se gli aiuti di questa domanda sono finiti. Lo dice una volta
        sola per domanda: chi continua a sparare lettere dopo prende silenzio,
        non una riga ciascuna — e' proprio il flood che il tetto toglie."""
        g = self.state["games"][chan]
        if not half_shown(g["a"], set(g["revealed"])):
            return False
        if not g.get("capped"):
            g["capped"] = True
            self.say(chan, "✋ Meta' risposta e' scoperta: basta lettere e aiuti, "
                           "ora solo la risposta intera.")
        return True

    def classifica(self, chan):
        rows = sorted(self.state["scores"].values(),
                      key=lambda r: (-r["points"], r["nick"].casefold()))
        if not rows:
            self.say(chan, "🗒️ [Trivial] Classifica vuota. "
                           "`!trivial <set> [n]` per aprire le danze.")
            return
        # `[Trivial]` per ordine di vjt (#sbiffo 20:28): su `!classifica` nudo
        # questa riga esce accanto a quella dello sparasuore, e senza etichetta
        # le due si leggono come una classifica sola.
        self.say(chan, f"🏆 [Trivial] Classifica ({self.net}): {podio(rows)}")

    def help(self, chan, now):
        """HELP_LINES in `chan`, al massimo una volta ogni HELP_COOLDOWN
        secondi per canale. Il cooldown sta in memoria: un riavvio lo
        azzera, e va bene cosi' — non e' stato di gioco."""
        key = chan.casefold()
        last = self.help_seen.get(key)
        if last is not None and now - last < HELP_COOLDOWN:
            return
        self.help_seen[key] = now
        for line in HELP_LINES:
            self.say(chan, line)

    # --- chi e' op -------------------------------------------------------

    def is_op(self, nick, chan):
        return nick.casefold() in self.ops.get(chan.casefold(), set())

    def track(self, line):
        """Aggiorna gli @ dei canali da una riga del log. Mai stato su disco:
        un riavvio lo ricostruisce col NAMES che run() chiede all'avvio."""
        self.tracker.feed(line)
        m = EVENT_PAT.search(line)
        if not m:
            return
        src, cmd, rest = m.group("src").casefold(), m.group("cmd").upper(), m.group("rest")
        if cmd == "353":
            # `<me> = #chan :@a +b c` — la fotografia arriva a pezzi, e il
            # primo pezzo dopo un 366 la ricomincia da capo.
            mm = re.match(r'\S+\s+[=*@]\s+(\S+)\s+:(.*)$', rest)
            if not mm:
                return
            key = mm.group(1).casefold()
            if key not in self._names:
                self._names.add(key)
                self.ops[key] = set()
            for n in mm.group(2).split():
                if n.startswith("@"):
                    self.ops[key].add(n.lstrip("@%+").casefold())
        elif cmd == "366":
            parts = rest.split()
            if len(parts) >= 2:
                self._names.discard(parts[1].casefold())
        elif cmd == "MODE":
            parts = rest.split()
            if len(parts) < 2 or not parts[0].startswith("#"):
                return
            key, modes, args = parts[0].casefold(), parts[1], parts[2:]
            sign = "+"
            for c in modes:
                if c in "+-":
                    sign = c
                    continue
                if c in MODE_ARGS or (c == "l" and sign == "+"):
                    arg = args.pop(0) if args else ""
                    if c == "o" and arg:
                        got = self.ops.setdefault(key, set())
                        (got.add if sign == "+" else got.discard)(arg.casefold())
        elif cmd == "NICK":
            new = rest.lstrip(":").split()[0].casefold() if rest.strip(": ") else ""
            for got in self.ops.values():
                if src in got:
                    got.discard(src)
                    if new:
                        got.add(new)
        elif cmd == "PART":
            parts = rest.split()
            if parts:
                self.ops.get(parts[0].casefold(), set()).discard(src)
        elif cmd == "KICK":
            parts = rest.split()
            if len(parts) >= 2:
                self.ops.get(parts[0].casefold(), set()).discard(parts[1].casefold())
        elif cmd == "QUIT":
            for got in self.ops.values():
                got.discard(src)

    def skip(self, chan, nick, now=None):
        """Butta la domanda in corso e passa alla prossima. Conta fra le
        domande della partita: e' uscita, l'hanno vista tutti."""
        g = self.state["games"][chan]
        self.next_question(
            chan, now=now,
            lead=[f"⏭️ Saltata da {canon_nick(nick)}, la risposta era: {g['a']}."])

    # --- ingresso --------------------------------------------------------

    def handle(self, nick, chan, text, now=None):
        """Una riga di canale. True se lo stato e' cambiato (il chiamante salva)."""
        if chan not in self.chans:
            return False
        text = text.strip()
        if not text:
            return False
        now = int(now if now is not None else time.time())

        if HELP_PAT.match(text):
            self.help(chan, now)
            return False

        k = CLASSIFICA_PAT.match(text)
        if k:
            game = (k.group("game") or "").lower()
            if not game or game in MY_GAME:
                self.classifica(chan)
            return False

        g = self.game(chan)

        # STOP prima di CMD: `!trivial stop` combacia anche con CMD_PAT, che
        # lo leggerebbe come il nome di un set e risponderebbe "sconosciuto"
        # a chi voleva solo chiudere.
        if STOP_PAT.match(text):
            if g:
                self.stop_round(chan, reason="richiesto")
                return True
            return False

        m = CMD_PAT.match(text)
        if m:
            want = (m.group("set") or "").strip()
            lim = m.group("limit")
            if g:
                # Partita gia' viva: si ripubblica e basta. Cambiare tema o
                # limite a meta' round butterebbe via la maschera di chi sta
                # giocando e sposterebbe il traguardo sotto i suoi piedi.
                g["last"] = now
                self.emit(chan, [
                    f"❓ [{g.get('count', 1)}/{g.get('limit', DEFAULT_LIMIT)}] {g['q']}",
                    mask_of(g["a"], set(g["revealed"]))])
                return True
            # `!trivial 50`: il lazy del gruppo `set` si mangia il numero se e'
            # l'unica cosa scritta, quindi il limite nudo arriva qui dentro
            # `want`, non dentro `lim`.
            if want.isdigit() and not lim:
                want, lim = "", want
            if not want and lim:
                # `!trivial 50`: il numero c'e' ma il set no. Dirlo, invece di
                # rispondere "set sconosciuto: 50" a chi ha solo scordato un
                # pezzo.
                self.say(chan, f"Il set va prima del numero: `!trivial <set> {lim}`. "
                               f"Ci sono: {' · '.join(self.set_names(chan))}.")
                return False
            if not want:
                # Ordine di vjt: «si inizia un gioco solo scegliendo il set di
                # domande a tema». Niente set, niente partita: si elenca.
                self.elenco_set(chan)
                return False
            cats = self.resolve_set(want, chan)
            if cats is None:
                self.say(chan, f"🤨 Set sconosciuto: {want}. "
                               f"Ci sono: {' · '.join(self.set_names(chan))}.")
                return False
            if not cats:
                # Esiste, ma qui e' vietato: negare e dirlo, invece di aprire
                # una partita che finirebbe "esaurite" alla prima domanda.
                self.say(chan, f"🚫 Il set {fold(want)} qui non si gioca. "
                               f"Ci sono: {' · '.join(self.set_names(chan))}.")
                return False
            no = ircgame.start_refusal(self.lock, self.tracker, chan, now)
            if no:
                self.say(chan, no)
                return False
            n = sum(1 for d in self.domande if d["cat"] in cats)
            limit = max(1, int(lim)) if lim else DEFAULT_LIMIT
            # Il limite annunciato e' quello vero: se il set ha meno domande
            # del limite chiesto, la partita finira' li' e dirlo prima evita
            # che sembri un troncamento arbitrario.
            # Anche l'apertura viaggia in testa alla prima domanda, non su una
            # riga sua: due PRIVMSG in fila all'avvio sono meta' del flood.
            self.start_round(
                chan, setname=want, now=now, limit=limit,
                lead=[f"🎲 Trivial, set {fold(want)}: {n} domande, "
                      f"partita da {min(limit, n)}. "
                      f"`.h` una lettera, `.v` le vocali, `!stop` chiude."])
            return True

        # Da qui in poi serve una partita viva in QUESTO canale.
        if not g:
            return False

        if SKIP_PAT.match(text):
            # Chi non e' op prende silenzio: una riga di rifiuto a testa e'
            # flood, e l'help dice gia' che il comando e' degli op.
            if not self.is_op(nick, chan):
                return False
            self.skip(chan, nick, now=now)
            return True

        if VOWEL_PAT.match(text):
            g["last"] = now
            if self.capped(chan):
                return True
            if self.reveal(chan, VOCALI):
                self.after_reveal(chan, nick, now=now)
            return True

        if HINT_PAT.match(text):
            g["last"] = now
            if self.capped(chan):
                return True
            left = hidden_letters(g["a"], set(g["revealed"]))
            if left:
                self.reveal(chan, {random.choice(left)})
                self.after_reveal(chan, nick, now=now)
            return True

        guess = norm_answer(text)
        accepted = {norm_answer(g["a"])}
        accepted |= {norm_answer(x) for x in g.get("alt", [])}
        accepted.discard("")

        # Lettera nuda: un solo carattere alfanumerico. Salvo che sia proprio
        # una risposta buona (`3` per lo strato OSI): quella vince.
        if len(text) == 1 and is_hidden(text) and guess not in accepted:
            g["last"] = now
            if self.capped(chan):
                return True
            if self.reveal(chan, {text}):
                self.after_reveal(chan, nick, now=now)
            # Lettera assente: silenzio, come ShBot.
            return True

        # Tentativo intero.
        if not guess:
            return False
        if guess in accepted or contoured(guess, accepted):
            g["last"] = now
            # Vinta con un sinonimo: si scopre comunque tutto, cosi' la riga
            # finale mostra la risposta canonica e non la maschera monca.
            g["revealed"] = sorted({fold(c) for c in g["a"] if is_hidden(c)})
            self.win(chan, nick, now=now)
            return True

        # Parola giusta dentro la risposta: la si scopre, come fa ShBot.
        words = {norm_answer(w) for w in g["a"].split()}
        words.discard("")
        if guess in words:
            g["last"] = now
            target = next(w for w in g["a"].split() if norm_answer(w) == guess)
            if self.reveal(chan, {c for c in target if is_hidden(c)}):
                self.after_reveal(chan, nick, now=now)
                return True
        return False

    def tick(self, now=None):
        """Fa scadere le partite ferme da troppo, canale per canale.
        True se lo stato cambia."""
        now = int(now if now is not None else time.time())
        stale = [c for c, g in self.state["games"].items()
                 if now - int(g.get("last", now)) >= IDLE_STOP]
        for chan in stale:
            self.stop_round(chan)
        return bool(stale)

    # --- daemon ----------------------------------------------------------

    def run(self):
        p = subprocess.Popen(["tail", "-F", "-n", "0", self.log],
                             stdout=subprocess.PIPE, text=True, errors="replace")
        # Il tail parte PRIMA del NAMES, sennò la risposta puo' arrivare nel
        # log prima che qualcuno la stia leggendo.
        time.sleep(1)
        for chan in sorted(self.chans):
            self._fifo("RAW", "NAMES", chan)
        try:
            while True:
                r, _, _ = select.select([p.stdout], [], [], TICK)
                if r:
                    line = p.stdout.readline()
                    if not line:
                        break
                    self.track(line)
                    m = PRIVMSG_PAT.search(line)
                    if m and self.handle(m.group("nick"), m.group("chan"),
                                         m.group("text")):
                        save_state(self.state_path, self.state)
                elif self.tick():
                    save_state(self.state_path, self.state)
                # i lucchetti seguono le partite, qualunque strada le chiuda
                self.lock.sync(self.state["games"])
        finally:
            p.terminate()


# ------------------------------------------------------------------- selftest

def selftest():
    """Esercita la meccanica senza toccare rete, FIFO o stato su disco."""
    import tempfile
    dom = [{"q": "Domanda di prova?", "a": "Token JWT", "cat": "t"}]
    with tempfile.TemporaryDirectory() as td:
        st = Path(td) / "s.json"
        g = Impiccato("test", "/dev/null", "", st, {"#t"}, dom, talk=False)

        g.handle("a", "#t", "!trivial t")
        # Apertura, domanda e maschera viaggiano sulla stessa riga: UN PRIVMSG,
        # non tre. E' il fix del kick per flood — vedi MAX_LINE.
        assert len(g.sent) == 1, g.sent
        assert g.sent[0][1].startswith("🎲 Trivial, set t:"), g.sent[0]
        assert g.sent[0][1].endswith("❓ [1/10] Domanda di prova?  ·  ----- ---"), g.sent[0]

        g.sent.clear()
        g.handle("a", "#t", "z")            # lettera assente -> silenzio
        assert g.sent == [], g.sent

        g.handle("a", "#t", "t")            # scopre tutte le T, case preservato
        assert g.sent[-1] == ("#t", "T---- --T"), g.sent[-1]

        g.sent.clear()
        g.handle("a", "#t", ".v")           # vocali
        assert g.sent[-1] == ("#t", "To-e- --T"), g.sent[-1]

        g.sent.clear()
        g.handle("b", "#t", "token")        # parola giusta -> la scopre
        assert g.sent[-1] == ("#t", "Token --T"), g.sent[-1]

        g.sent.clear()
        g.handle("b", "#t", "Token JWT")    # risposta intera -> vittoria
        assert g.state["scores"]["b"]["points"] == 1
        # Catalogo di UNA domanda: non c'e' niente di nuovo da chiedere, quindi
        # la partita finisce invece di ripresentare la stessa.
        assert g.game("#t") is None, g.game("#t")
        # Esito + fine partita + punteggio: tutto su UNA riga, e l'esito NON si
        # perde per strada solo perche' la partita e' finita li'.
        assert len(g.sent) == 1, g.sent
        assert "✅ Esatto b, la risposta era: Token JWT!!!" in g.sent[0][1], g.sent[0]
        assert "Finite le domande" in g.sent[0][1], g.sent[0]
        assert "🏆 Punteggio partita" in g.sent[0][1], g.sent[0]

        # sinonimo accettato: la risposta canonica esce comunque intera
        g6 = Impiccato("test", "/dev/null", "", st, {"#t"},
                       [{"q": "q", "a": "Coda", "alt": ["fifo"], "cat": "t"}], talk=False)
        g6.handle("a", "#t", "!trivial t")
        g6.sent.clear()
        g6.handle("c", "#t", "fifo")
        assert "✅ Esatto c, la risposta era: Coda!!!" in g6.sent[0][1], g6.sent[0]
        assert g6.state["scores"]["c"]["points"] == 1

        # Un sinonimo di UN carattere vince, non finisce nel ramo lettera:
        # puppy ha scritto `3` alla domanda sullo strato OSI e ha trovato il
        # silenzio, con `3` in `alt` (#sbiffo, 2026-10-13 11:11).
        osi = [{"q": "q", "a": "rete", "alt": ["3", "network"], "cat": "t"}]
        g7 = Impiccato("test", "/dev/null", "", st, {"#t"}, osi, talk=False)
        g7.handle("a", "#t", "!trivial t")
        g7.sent.clear()
        g7.handle("d", "#t", "3")
        assert "✅ Esatto d, la risposta era: rete!!!" in g7.sent[0][1], g7.sent

        # La risposta giusta con due parole di contorno vince lo stesso:
        # `lo strato rete` e' `rete`. Tre parole in piu' no, e' un tema.
        g8 = Impiccato("test", "/dev/null", "", st, {"#t"}, osi, talk=False)
        g8.handle("a", "#t", "!trivial t")
        g8.sent.clear()
        assert not g8.handle("e", "#t", "secondo me non e' la rete")
        g8.handle("e", "#t", "lo strato rete")
        assert "✅ Esatto e, la risposta era: rete!!!" in g8.sent[-1][1], g8.sent

        # L'accorpamento taglia SOLO fra le parti, mai dentro una: una domanda
        # lunga resta intera anche a costo di una riga sua.
        lunga = "x" * 300
        assert joined_lines(["a", "b"]) == ["a  ·  b"]
        assert joined_lines([lunga, lunga]) == [lunga, lunga]
        assert joined_lines(["", "  ", "solo"]) == ["solo"]
        # Il conto e' in byte, non in caratteri: 200 emoji da 4 byte l'una non
        # stanno su una riga sola, anche se sono 200 "caratteri".
        emo = "🏆" * 100
        assert len(joined_lines([emo, emo])) == 2, joined_lines([emo, emo])

        # `.h` scopre una lettera e non due
        g2 = Impiccato("test", "/dev/null", "", st, {"#t"}, dom, talk=False)
        g2.handle("a", "#t", "!trivial t")
        g2.handle("a", "#t", ".h")
        assert len(g2.game("#t")["revealed"]) == 1, g2.game("#t")["revealed"]

        # accenti: 'e' scopre 'è'
        g3 = Impiccato("test", "/dev/null", "", st, {"#t"},
                       [{"q": "q", "a": "Perché", "cat": "t"}], talk=False)
        g3.handle("a", "#t", "!trivial t")
        g3.sent.clear()
        g3.handle("a", "#t", "e")
        # P-e-r-c-h-é: la 'e' nuda scopre sia la 'e' sia la 'é', e ognuna
        # viene ristampata col proprio accento.
        assert g3.sent[-1] == ("#t", "-e---é"), g3.sent[-1]

        # idle: la partita scade e lo dice
        g4 = Impiccato("test", "/dev/null", "", st, {"#t"}, dom, talk=False)
        g4.handle("a", "#t", "!trivial t", now=1000)
        assert g4.tick(now=1000 + IDLE_STOP) is True
        assert g4.game("#t") is None

        # canale sbagliato: non risponde
        g5 = Impiccato("test", "/dev/null", "", st, {"#t"}, dom, talk=False)
        assert g5.handle("a", "#altro", "!trivial t") is False
        assert g5.sent == []

        # due canali, due partite indipendenti: aprire la seconda non deve
        # ammazzare la prima (ordine di vjt: il gioco anche su #sniffo).
        g7 = Impiccato("test", "/dev/null", "", st, {"#uno", "#due"},
                       [{"q": "q", "a": "Coda", "cat": "t"}], talk=False)
        g7.handle("a", "#uno", "!trivial t", now=1000)
        g7.handle("a", "#uno", "c", now=1000)
        g7.handle("b", "#due", "!trivial t", now=1000)
        assert g7.game("#uno") and g7.game("#due"), g7.state["games"]
        assert g7.game("#uno")["revealed"] == ["c"], g7.game("#uno")
        assert g7.game("#due")["revealed"] == [], g7.game("#due")
        # una lettera su #due non tocca la maschera di #uno
        g7.sent.clear()
        g7.handle("b", "#due", "o", now=1000)
        assert g7.sent == [("#due", "-o--")], g7.sent
        # e chi vince su #due non chiude la partita di #uno
        g7.handle("b", "#due", "Coda", now=1000)
        assert g7.game("#uno")["revealed"] == ["c"], g7.game("#uno")
        assert g7.state["scores"]["b"]["points"] == 1
        # idle: scade solo il canale fermo. #due ha esaurito il catalogo con la
        # vittoria, quindi si riapre per avere due partite vive da confrontare.
        g7.handle("b", "#due", "!trivial t", now=1000 + IDLE_STOP)
        assert g7.game("#due") is not None, g7.state["games"]
        assert g7.tick(now=1000 + IDLE_STOP) is True
        assert g7.game("#uno") is None and g7.game("#due") is not None

        # --- set a tema (ordine di vjt, #sbiffo 21:57-21:59) ---
        mix = [{"q": "qn", "a": "Coda", "cat": "unix"},
               {"q": "qb", "a": "Scarpa", "cat": "moda"}]
        g8 = Impiccato("test", "/dev/null", "", st, {"#t"}, mix, talk=False)

        # `!trivial` nudo NON parte: elenca i set e basta.
        assert g8.handle("a", "#t", "!trivial") is False
        assert g8.game("#t") is None, "senza set non si gioca"
        # Due righe: l'elenco e poi l'help, che deve nominare limite e comandi.
        assert "Set:" in g8.sent[-2][1], g8.sent[-2]
        assert g8.sent[-2][1].startswith("📚"), g8.sent[-2]
        assert "babbani" in g8.sent[-2][1] and "nerd" in g8.sent[-2][1], g8.sent[-2]
        help_line = g8.sent[-1][1]
        assert str(DEFAULT_LIMIT) in help_line, help_line
        for tok in ("!trivial babbani 25", ".h", ".v", "!stop", "!classifica"):
            assert tok in help_line, (tok, help_line)
        # Nessuna delle due deve sforare la riga IRC.
        for _, t in g8.sent[-2:]:
            assert len(t.encode()) <= 400, (len(t.encode()), t)

        # set sconosciuto: lo dice, non parte.
        g8.sent.clear()
        assert g8.handle("a", "#t", "!trivial pokemon") is False
        assert g8.game("#t") is None
        assert "sconosciuto" in g8.sent[-1][1], g8.sent[-1]

        # `babbani` = complemento di NERD_CATS: qui pesca solo la moda.
        g8.sent.clear()
        assert g8.handle("a", "#t", "!trivial babbani") is True
        assert g8.game("#t")["a"] == "Scarpa", g8.game("#t")
        assert g8.sent[-1][1].endswith("------"), g8.sent[-1]

        # il tema regge fra una domanda e l'altra: con due domande di moda,
        # dopo la vittoria la partita resta di la' e non sborda nel nerd.
        moda2 = mix + [{"q": "qb2", "a": "Cappello", "cat": "moda"}]
        g8b = Impiccato("test", "/dev/null", "", st, {"#t"}, moda2, talk=False)
        g8b.handle("a", "#t", "!trivial babbani")
        prima = g8b.game("#t")["a"]
        g8b.handle("b", "#t", prima)
        assert g8b.game("#t")["set"] == "babbani", g8b.game("#t")
        assert g8b.game("#t")["a"] in {"Scarpa", "Cappello"}, g8b.game("#t")
        assert g8b.game("#t")["a"] != prima, "domanda ripetuta nella stessa partita"
        assert g8b.game("#t")["count"] == 2, g8b.game("#t")

        # `nerd` pesca solo dall'altra sponda, `tutte` da entrambe.
        g9 = Impiccato("test", "/dev/null", "", st, {"#t"}, mix, talk=False)
        g9.handle("a", "#t", "!trivial nerd")
        assert g9.game("#t")["a"] == "Coda", g9.game("#t")
        assert g9.resolve_set("tutte") == {"unix", "moda"}
        assert g9.resolve_set("bestemmie") is None, "alias verso una cat assente = None"
        assert g9.resolve_set("scarpe") == {"moda"}, "alias scarpe -> moda"

        # Opt-out per canale: su #italia la goliardia non esce nemmeno da
        # `tutte`, e chiederla per nome viene negato — non e' un set che non
        # esiste, e' un set che li' non si gioca.
        gol = mix + [{"q": "qg", "a": "Porco", "cat": "goliardia"}]
        g9b = Impiccato("test", "/dev/null", "", st, {"#italia", "#t"}, gol,
                        talk=False)
        assert g9b.resolve_set("tutte", "#t") == {"unix", "moda", "goliardia"}
        assert g9b.resolve_set("tutte", "#italia") == {"unix", "moda"}
        assert g9b.resolve_set("bestemmie", "#t") == {"goliardia"}
        assert g9b.resolve_set("bestemmie", "#italia") == set(), "esiste ma qui no"
        assert g9b.resolve_set("pokemon", "#italia") is None, "sconosciuto != vietato"
        assert "goliardia" not in g9b.set_names("#italia")
        g9b.sent.clear()
        assert g9b.handle("a", "#italia", "!trivial bestemmie") is False
        assert g9b.game("#italia") is None
        assert "non si gioca" in g9b.sent[-1][1], g9b.sent[-1]
        # e con `tutte` la partita parte, ma la goliardia non ci finisce dentro.
        for _ in range(6):
            g9b.handle("a", "#italia", "!trivial tutte 3")
            assert g9b.game("#italia")["a"] != "Porco", g9b.game("#italia")
            g9b.handle("a", "#italia", "!stop")
        # sugli altri canali resta tutto com'era.
        assert g9b.handle("a", "#t", "!trivial bestemmie") is True
        assert g9b.game("#t")["a"] == "Porco", g9b.game("#t")
        g9b.handle("a", "#t", "!stop")

        # `!trivial stop` chiude, non viene letto come nome di un set.
        g9.sent.clear()
        assert g9.handle("a", "#t", "!trivial stop") is True
        assert g9.game("#t") is None
        assert "🛑 Chiuso." in g9.sent[0][1], g9.sent[0]

        # `!impiccati <set>` resta valido: chi giocava su #cybernet non si
        # trova il bot muto.
        g10 = Impiccato("test", "/dev/null", "", st, {"#t"}, mix, talk=False)
        assert g10.handle("a", "#t", "!impiccati nerd") is True
        assert g10.game("#t")["a"] == "Coda"

        # --- niente ripetizioni + limite di partita (vjt, DM 2026-08-25 16:07) ---
        many = [{"q": f"q{i}", "a": f"Risposta{i}", "cat": "t"} for i in range(40)]

        # Nessuna domanda esce due volte nella stessa partita, e la partita si
        # chiude da sola all'ultimo giro senza che nessuno dica `!stop`.
        g11 = Impiccato("test", "/dev/null", "", st, {"#t"}, many, talk=False)
        g11.handle("a", "#t", "!trivial t")
        assert g11.game("#t")["limit"] == DEFAULT_LIMIT, g11.game("#t")
        viste = []
        for _ in range(DEFAULT_LIMIT):
            gg = g11.game("#t")
            assert gg is not None, f"partita chiusa dopo {len(viste)}"
            viste.append(gg["q"])
            g11.handle("b", "#t", gg["a"])
        assert len(set(viste)) == DEFAULT_LIMIT, "domanda ripetuta nella partita"
        assert g11.game("#t") is None, "la partita deve chiudersi al limite"
        assert any(f"Partita finita: {DEFAULT_LIMIT} domande." in t
                   for _, t in g11.sent[-3:]), g11.sent[-3:]

        # Due domande diverse con la STESSA risposta valgono per una sola: la
        # maschera e la parola da scrivere sono identiche, quindi al giocatore
        # sembra la domanda ripetuta (visto in canale: `IRCv3` due volte in 30
        # secondi, vjt «non dobbiamo scegliere domande con la stessa risposta»).
        gemelle = [{"q": "q1", "a": "Init", "cat": "t"},
                   {"q": "q2", "a": "init", "cat": "t"},
                   {"q": "q3", "a": "Fork", "cat": "t"}]
        g11b = Impiccato("test", "/dev/null", "", st, {"#t"}, gemelle, talk=False)
        g11b.handle("a", "#t", "!trivial t 3")
        risposte = []
        while g11b.game("#t"):
            risposte.append(norm_answer(g11b.game("#t")["a"]))
            g11b.handle("b", "#t", g11b.game("#t")["a"])
        assert len(risposte) == 2, risposte
        assert len(set(risposte)) == 2, risposte

        # Limite esplicito a inizio partita: `!trivial t 3`.
        g12 = Impiccato("test", "/dev/null", "", st, {"#t"}, many, talk=False)
        assert g12.handle("a", "#t", "!trivial t 3") is True
        assert g12.game("#t")["limit"] == 3, g12.game("#t")
        assert g12.game("#t")["set"] == "t", "il numero non deve finire nel set"
        for _ in range(3):
            g12.handle("b", "#t", g12.game("#t")["a"])
        assert g12.game("#t") is None, "il limite chiesto deve valere"

        # Punteggio DI PARTITA a fine giro, non la hall of fame (vjt 16:20).
        # `b` arriva alla seconda partita con 3 punti gia' in classifica
        # generale: quelli non devono comparire nel riepilogo della partita.
        g12b = Impiccato("test", "/dev/null", "", st, {"#t"}, many, talk=False)
        for _ in range(2):                      # prima partita, 2 punti a `b`
            g12b.handle("a", "#t", "!trivial t 1")
            g12b.handle("b", "#t", g12b.game("#t")["a"])
        g12b.handle("a", "#t", "!trivial t 3")  # terza partita: 2 a `c`, 1 a `d`
        g12b.handle("c", "#t", g12b.game("#t")["a"])
        g12b.handle("c", "#t", g12b.game("#t")["a"])
        g12b.sent.clear()
        g12b.handle("d", "#t", g12b.game("#t")["a"])
        fine = [t for _, t in g12b.sent if "Punteggio partita" in t]
        assert len(fine) == 1, g12b.sent
        assert "🥇 c 2" in fine[0] and "🥈 d 1" in fine[0], fine[0]
        assert " b " not in fine[0], ("la hall of fame non entra qui", fine[0])
        assert g12b.state["scores"]["b"]["points"] == 2, g12b.state["scores"]

        # Il limite non si sposta a partita in corso: `!trivial t 99` ripubblica.
        g13 = Impiccato("test", "/dev/null", "", st, {"#t"}, many, talk=False)
        g13.handle("a", "#t", "!trivial t 5")
        g13.handle("a", "#t", "!trivial t 99")
        assert g13.game("#t")["limit"] == 5, g13.game("#t")

        # Set piu' corto del limite: finisce le domande e lo dice diverso.
        g14 = Impiccato("test", "/dev/null", "", st, {"#t"}, many[:2], talk=False)
        g14.handle("a", "#t", "!trivial t 10")
        for _ in range(2):
            g14.handle("b", "#t", g14.game("#t")["a"])
        assert g14.game("#t") is None
        assert any("Finite le domande del set dopo 2." in t for _, t in g14.sent[-3:]), g14.sent[-3:]

        # Numero senza set: lo dice invece di rispondere "set sconosciuto: 50".
        g15 = Impiccato("test", "/dev/null", "", st, {"#t"}, many, talk=False)
        assert g15.handle("a", "#t", "!trivial 50") is False
        assert g15.game("#t") is None
        assert "prima del numero" in g15.sent[-1][1], g15.sent[-1]

        # `!help` / `!giochi`: in canale, cooldown per canale.
        gh = Impiccato("test", "/dev/null", "", st, {"#t"}, dom, talk=False)
        assert gh.handle("Pippo", "#t", "!help", now=1000) is False
        assert gh.sent == [("#t", t) for t in HELP_LINES]
        gh.handle("Pluto", "#t", "!giochi", now=1000 + HELP_COOLDOWN - 1)
        assert len(gh.sent) == len(HELP_LINES)
        gh.handle("Pippo", "#t", "!giochi", now=1000 + HELP_COOLDOWN)
        assert len(gh.sent) == 2 * len(HELP_LINES)
        assert all(len(t.encode("utf-8")) <= MAX_LINE and "`" not in t
                   for t in HELP_LINES)

        # migrazione: la vecchia chiave `game` singola finisce nel dizionario
        old = Path(td) / "old.json"
        old.write_text(json.dumps({"scores": {}, "asked": [],
                                   "game": {"chan": "#x", "q": "q", "a": "Coda",
                                            "revealed": [], "alt": [], "last": 1}}),
                       encoding="utf-8")
        assert load_state(old)["games"]["#x"]["a"] == "Coda"

        # tetto agli aiuti: oltre meta' risposta lettere, `.h` e `.v` tacciono,
        # l'avviso esce una volta sola, la risposta intera vince ancora.
        gc = Impiccato("test", "/dev/null", "", st, {"#t"},
                       [{"q": "q", "a": "Abcdef", "cat": "t"},
                        {"q": "q2", "a": "Ghijkl", "cat": "t"}], talk=False)
        gc.handle("a", "#t", "!trivial t")
        first = gc.game("#t")["a"]
        for c in first[:3]:
            gc.handle("a", "#t", c)
        gc.sent.clear()
        gc.handle("a", "#t", first[3])
        gc.handle("a", "#t", ".h")
        gc.handle("a", "#t", ".v")
        assert len(gc.sent) == 1 and gc.sent[0][1].startswith("✋"), gc.sent
        assert len(gc.game("#t")["revealed"]) == 3, gc.game("#t")["revealed"]
        gc.handle("a", "#t", first)
        assert gc.game("#t")["count"] == 2 and not gc.game("#t").get("capped")
        gc.handle("a", "#t", ".h")
        assert len(gc.game("#t")["revealed"]) == 1, gc.game("#t")["revealed"]

        # `!skip`: solo chi ha @ nel canale, ricostruito dal log.
        gs = Impiccato("test", "/dev/null", "", st, {"#t"},
                       [{"q": "q", "a": "Abcdef", "cat": "t"},
                        {"q": "q2", "a": "Ghijkl", "cat": "t"}], talk=False)
        gs.handle("op", "#t", "!trivial t")
        first = gs.game("#t")["a"]
        assert gs.handle("op", "#t", "!skip") is False, "senza NAMES nessuno e' op"
        gs.track("12:00:00 < :srv 353 Mike = #T :Mike @Op +voce altro")
        gs.track("12:00:00 < :srv 366 Mike #T :End of /NAMES list.")
        assert gs.is_op("OP", "#t") and not gs.is_op("voce", "#t")
        gs.sent.clear()
        assert gs.handle("voce", "#t", "!skip") is False and gs.sent == []
        assert gs.handle("op", "#t", "!skip") is True
        line = gs.sent[-1][1]
        assert f"Saltata da op, la risposta era: {first}." in line, line
        assert gs.game("#t")["count"] == 2 and gs.game("#t")["a"] != first
        # MODE a piu' argomenti, NICK, PART: gli @ si spostano con la gente.
        gs.track("12:00:01 < :X!x@h MODE #t +bo-o *!*@h voce Op")
        assert gs.is_op("voce", "#t") and not gs.is_op("op", "#t")
        gs.track("12:00:02 < :voce!v@h NICK :voce2")
        assert gs.is_op("voce2", "#t") and not gs.is_op("voce", "#t")
        gs.track("12:00:03 < :voce2!v@h PART #t :ciao")
        assert not gs.is_op("voce2", "#t")
        # un NAMES nuovo rifa' la fotografia, non si somma alla vecchia
        gs.track("12:00:04 < :srv 353 Mike = #t :@altro")
        gs.track("12:00:04 < :srv 366 Mike #t :End")
        assert gs.ops["#t"] == {"altro"}, gs.ops

    print("selftest ok")


# ----------------------------------------------------------------------- main

def build(net, talk=True):
    cfg = NETS[net]
    chans = os.environ.get("IMPICCATO_CHANS")
    return Impiccato(
        net,
        os.environ.get("IMPICCATO_LOG", cfg["log"]),
        os.environ.get("IMPICCATO_FIFO", cfg["fifo"]),
        Path(os.environ.get("IMPICCATO_STATE", cfg["state"])),
        {c.strip() for c in chans.split(",")} if chans else cfg["chans"],
        load_domande(),
        talk=talk,
    )


def main():
    argv = sys.argv[1:]
    net = "azzurra"
    if "--net" in argv:
        i = argv.index("--net")
        net = argv[i + 1]
        del argv[i:i + 2]
    if net not in NETS:
        raise SystemExit(f"impiccato: rete sconosciuta {net!r} (usa: {', '.join(NETS)})")

    cmd = argv[0] if argv else "daemon"
    if cmd == "selftest":
        selftest()
    elif cmd == "classifica":
        g = build(net, talk=False)
        rows = sorted(g.state["scores"].values(),
                      key=lambda r: (-r["points"], r["nick"].casefold()))
        if not rows:
            print("(nessun punto)")
        for i, r in enumerate(rows):
            medaglia = MEDAGLIE[i] if i < len(MEDAGLIE) else " ·"
            print(f"  {medaglia} {r['nick']:<20} {r['points']}")
    else:
        build(net).run()


if __name__ == "__main__":
    main()
