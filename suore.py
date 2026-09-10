#!/usr/bin/env python3
"""suore — caccia alle suore, il DuckHunt di rex con un'altra fauna.

Ordine di vjt (#sbiffo 2026-09-06 02:06): «prendi dal log di #2600 su efnet il
comportamento del bot "rex" e ricostruisci il gioco delle papere con le stesse
semantiche. fai un nuovo sidecar e però accetti anche !bang e !bef oltre a
.bang e .bef. i messaggi in italiano, abilitalo solo su #sniffo». Emendamento
alle 02:10, su proposta di peluche: «SI SI LA SUORA» / «SI SI SUORE».

La spec completa, con le righe di rex verbatim da cui è misurata ogni
semantica, sta in docs/suore_spec.md. Qui il riassunto operativo:

  * Ogni tanto compare una suora: una riga di arte ASCII con testa e richiamo
    variabili e spazi a larghezza zero piazzati a caso dentro. Gli U+200B non
    sono decorazione: rompono il copia-incolla, che è il modo in cui un umano
    baro rimanderebbe la riga per fingere di aver visto la suora.
  * La suora NON scappa. È la differenza fra rex e il DuckHunt di Rizon che
    vjt ha tenuto a segnalare (#sniffo 01:56): «rex aspetta», e il tempo
    annunciato è tutto il tempo passato dalla comparsa (misurate uccisioni a
    923 secondi).
  * `.bang` / `!bang` spara, `.bef` / `!bef` fa amicizia. Il primo che arriva
    si prende la suora; chi arriva dopo trova il canale vuoto.
  * Sparare o fare amicizia può fallire: fucile inceppato, suora schizzinosa.
    Il fallimento NON consuma la suora e costa 7 secondi di penalità, durante
    i quali i tentativi di quel nick sono ignorati IN SILENZIO — misurato su
    rex, ed è la scelta giusta comunque: rispondere a ogni tentativo dentro la
    penalità trasformerebbe il gioco in un flood.

Stessa forma degli altri sidecar (next_counter.py, cena_counter.py): tail del
bot.log, match dei PRIVMSG in canale, stato su json, risposte scritte sulla
FIFO del bot. `bot.py` NON viene toccato.

Nello stato finisce SOLO il nick. Mai host, mai ident, mai IP.

MULTI-RETE E MULTI-CANALE (refactor, ordine di vjt #sbiffo 2026-09-09 23:34:
«rendilo multi rete e multi canale»). Un solo processo copre N reti × N canali:

  * ogni RETE è una coppia log+FIFO, cioè un `bot.py` diverso. Il daemon tiene
    un `tail -F` per rete e risponde sulla FIFO da cui è arrivato il colpo —
    leggere i colpi di una rete e rispondere su un'altra era il modo più facile
    di sbagliare, e ora non è più esprimibile;
  * ogni CANALE ha la SUA suora e il SUO timer. Prima erano globali e `spawn()`
    sceglieva `sorted(CHANS)[0]`: i canali oltre il primo non vedevano mai una
    comparsa e prendevano solo `.bang` a vuoto. Il workaround era un processo
    per canale (tre unit systemd per tre canali);
  * la chiave di tutto — punteggi, suora, timer, penalità — è lo SCOPE
    `rete/canale`. Due `#sniffo` su due reti sono due partite fra due gruppi di
    persone diversi, e i punteggi non si mescolano.

CLI:
  suore.py                 esegue il daemon
  suore.py list            stampa le classifiche di tutti gli scope e esce
  suore.py spawn [scope]   forza una comparsa subito (per provarlo a mano)
  suore.py scopes          stampa la configurazione risolta e esce

Env:
  SUORE_NETS    reti e canali, la forma nuova. Record separati da ';':
                  nome|path/bot.log|path/bot.send|#chan[:min-max],#chan2[:min-max]
                Il `min-max` è in minuti e vale per quel canale; se manca valgono
                SUORE_MIN_MIN/SUORE_MAX_MIN. FIFO vuota = muto (prova a secco).
                Esempio:
                  azzurra|/opt/x/bot.log|/opt/x/bot.send|#sniffo:90-240,#sbiffo:60-180;
                  ircnet|/opt/x/bot.ircnet.log|/opt/x/bot.ircnet.send|#cybernet
  SUORE_NET     nome della rete nella forma vecchia (default 'azzurra'). È anche
                la rete a cui load() attribuisce uno stato in formato vecchio
  SUORE_CHANS   forma vecchia: canali di quella rete, separati da virgola
  SUORE_LOG     forma vecchia: bot.log da seguire (default bot.log, cioè Azzurra)
  SUORE_FIFO    forma vecchia: FIFO del bot ('' = muto)
  SUORE_STATE   path dello stato json
  SUORE_MIN_MIN minuti minimi fra due comparse (default 45)
  SUORE_MAX_MIN minuti massimi fra due comparse (default 180)
  SUORE_JAM     probabilità di inceppamento del fucile (default 0.15)
  SUORE_NOPE    probabilità di rifiuto della suora (default 0.30)
"""
import json, os, random, re, select, subprocess, sys, time
from pathlib import Path

# La casa del gioco: dove stanno stato e default. Era hardcoded sul checkout del
# bot su questo Pi; ora il codice vive in un repo suo (irc-games) e gira in una
# jail su m42, quindi il default è la directory del file e SUORE_HOME la sposta.
REPO = Path(os.environ.get("SUORE_HOME", os.path.dirname(os.path.abspath(__file__))))

STATE = Path(os.environ.get("SUORE_STATE", str(REPO / "suore_state.json")))

MIN_GAP = float(os.environ.get("SUORE_MIN_MIN", "45")) * 60
MAX_GAP = float(os.environ.get("SUORE_MAX_MIN", "180")) * 60
P_JAM = float(os.environ.get("SUORE_JAM", "0.15"))
P_NOPE = float(os.environ.get("SUORE_NOPE", "0.30"))


class Net:
    """Una rete: il log da cui arrivano i colpi, la FIFO su cui si risponde, e i
    canali abilitati con la loro cadenza.

    Log e FIFO devono essere quelli dello STESSO `bot.py`. Tenerli in un oggetto
    solo, invece che in due variabili globali indipendenti, è metà del punto del
    refactor: prima erano due env separate e puntarne una al bot sbagliato non
    dava errore, dava un sidecar che leggeva i colpi di una rete e rispondeva
    su un'altra."""

    def __init__(self, name, log, fifo, chans):
        self.name = name
        self.log = log
        self.fifo = fifo
        self.chans = chans          # {'#chan': (min_gap, max_gap)} in secondi

    def scope(self, chan):
        return f"{self.name}/{chan}"

    def __repr__(self):
        return f"<Net {self.name} {sorted(self.chans)}>"


def _parse_chan(spec):
    """`#chan` oppure `#chan:90-240` (minuti). Torna (nome, (min, max))."""
    chan, _, gaps = spec.strip().partition(":")
    chan = chan.strip().lower()
    if not gaps.strip():
        return chan, (MIN_GAP, MAX_GAP)
    lo, _, hi = gaps.partition("-")
    return chan, (float(lo) * 60, float(hi) * 60)


def load_nets():
    """SUORE_NETS se c'è, altrimenti la forma vecchia a rete singola.

    La retrocompatibilità non è cortesia: le tre unit che girano adesso
    (#sniffo, #sbiffo, #cybernet) sono scritte nella forma vecchia e devono
    continuare a partire mentre il deployment nuovo viene provato."""
    raw = os.environ.get("SUORE_NETS", "").strip()
    if not raw:
        chans = dict(_parse_chan(c) for c in
                     os.environ.get("SUORE_CHANS", "#sniffo").split(",") if c.strip())
        return [Net(os.environ.get("SUORE_NET", "azzurra"),
                    os.environ.get("SUORE_LOG", str(REPO / "bot.log")),
                    os.environ.get("SUORE_FIFO", str(REPO / "bot.send")),
                    chans)]
    nets = []
    for record in raw.split(";"):
        record = record.strip()
        if not record:
            continue
        parts = record.split("|")
        if len(parts) != 4:
            raise SystemExit(f"suore: record malformato in SUORE_NETS: {record!r} "
                             "— serve nome|log|fifo|canali")
        name, log, fifo, chanspec = (p.strip() for p in parts)
        chans = dict(_parse_chan(c) for c in chanspec.split(",") if c.strip())
        if not chans:
            raise SystemExit(f"suore: la rete {name!r} non ha canali")
        nets.append(Net(name, log, fifo, chans))
    return nets


NETS = load_nets()
# Scope -> (Net, canale). È l'indice su cui gira tutto il resto: il gioco
# ragiona per scope e non per canale, così due `#sniffo` su due reti restano
# due partite distinte.
SCOPES = {net.scope(chan): (net, chan) for net in NETS for chan in net.chans}
# Il nome della rete a cui attribuire uno stato salvato nel formato vecchio,
# che i canali li teneva nudi (`#sniffo`) perché di rete ce n'era una sola.
LEGACY_NET = os.environ.get("SUORE_NET", NETS[0].name)

# Penalità dopo un fallimento, in secondi. Il numero è di rex e compare nel
# testo del messaggio: cambiarlo qui senza cambiarlo là sarebbe una bugia.
PENALTY = 7

# Ban dal gioco: `nick canonico -> epoch di scadenza`. Sta qui e non nello
# stato perche' e' una decisione, non un contatore: lo stato si puo' perdere,
# la decisione no (ordine di vjt, #sbiffo 2026-09-09 02:27: «bannalo dal gioco
# per 72 ore e diglielo. ban in config»).
#
# deadbeef: beccato con lo script. Su ordine di vjt ho postato a mano su
# #sniffo (02:26:16) una riga di comparsa identica a quella del sidecar ma
# scritta da me — il gioco non ha mai visto nessuna suora. Cinque secondi dopo
# e' arrivato il suo `.bef`, poi `!bang`, contro una suora inesistente.
#
# 2026-09-10 09:15 UTC: allungato a 72h da adesso su ordine di vjt (#sbiffo
# 11:14 Rome, «allunga il ban di deadbeef a 72h da adesso, sta ancora
# scriptando»).
#
# `deadbeef_` NON va bannato, e non e' una svista: vjt (#sbiffo 11:18) dice che
# quello e' il suo client grappa, dove non scripta. Quindi la separazione dei
# nick, che per la classifica e' un difetto, qui e' esattamente cio' che serve:
# `canon()` deve continuare a NON normalizzare l'underscore, o il ban del
# client scriptato si porterebbe dietro anche l'altro.
BANS = {
    "deadbeef": 1789290917,   # fino al 2026-09-13 09:15 UTC (11:15 Rome)
}

ZWSP = "​"

# Le teste, come rex le varia a ogni comparsa. La croce è la suora.
HEADS = ["†", "✝", "☨", "†", "✞"]
CALLS = ["AVE MARIA!", "ave maria!", "ORA PRO NOBIS!", "SANTA SUBITO!", "SIA LODATO!"]

# La suora dorata. NON è di rex: nel log di #2600 non compare mai, e la spec
# lo dice. È un'aggiunta chiesta da peluche e approvata da vjt (#sbiffo
# 2026-09-06 02:21-02:22, «ma si dai»). Regge più di un colpo: ogni tentativo
# riuscito le toglie una vita, e solo l'ultimo la conta come presa. Vale sia
# per il fucile che per l'amicizia — altrimenti `.bef` sarebbe la scorciatoia
# che azzera il senso di averla messa.
P_GOLDEN = float(os.environ.get("SUORE_GOLDEN", "0.08"))
GOLDEN_HP = (3, 6)
GOLDEN_HEADS = ["☩", "✠", "✟"]
GOLDEN_CALLS = ["AVE MARIA GRATIA PLENA!", "ALLELUIA!", "GLORIA IN EXCELSIS!"]

# La coda di fondo. In rex erano `・゜゜・。。・゜ ゜`, cioè l'acqua dello stagno
# sotto le papere: sotto una suora non ha senso, segnalato da peluche (#sbiffo
# 2026-09-06 14:10). Al suo posto i grani del rosario che le pendono davanti.
TRAIL = "◦◦•◦◦•◦◦"


def sprinkle(s, n=3):
    """Infila n spazi a larghezza zero in posizioni casuali.

    Non è un vezzo: è il motivo per cui la riga di rex non si può copiare e
    rimandare per finta. Le posizioni sono interne (mai in testa o in coda),
    così la riga resta identica all'occhio."""
    out = list(s)
    for _ in range(n):
        if len(out) < 3:
            break
        out.insert(random.randrange(1, len(out)), ZWSP)
    return "".join(out)


def spawn_line(golden=False):
    trail = TRAIL
    if golden:
        head, call = random.choice(GOLDEN_HEADS), random.choice(GOLDEN_CALLS)
        # SOLO grassetto, niente colore: ordine di vjt (#sbiffo 02:22
        # «togliamo il colore»), e comunque misurato che non passerebbe —
        # `MODE #sniffo` risponde `+tnrcCl`, e quel `c` fa cadere le righe
        # colorate. Il `\x0F` finale chiude, o il grassetto cola nello
        # scrollback di chi legge.
        body = sprinkle(f"{trail}\\_{head}< {call}", n=random.randint(2, 4))
        return f"\x02{body}\x0F"
    head = random.choice(HEADS)
    call = random.choice(CALLS)
    return sprinkle(f"{trail}\\_{head}< {call}", n=random.randint(2, 4))


# NON ancorata a inizio riga: in bot.log ogni riga porta davanti l'orario e il
# marcatore di direzione (`02:15:43 < :vjt!...`). Un `^:` qui non matcherebbe
# mai — costato una comparsa sprecata su #sbiffo alla prima prova.
PRIVMSG_PAT = re.compile(
    r':(?P<nick>[^!\s]+)!\S+\s+'
    r'PRIVMSG\s+(?P<chan>#\S+)\s+:(?P<text>.*?)$',
)

# `.bang` / `!bang` / `.bef` / `!bef`, e le statistiche. Niente argomenti:
# rex non ne accetta, e un argomento è quasi sempre un typo di qualcun altro.
#
# L'eccezione è `classifica`, che il canale condivide con l'impiccato: due
# sidecar rispondevano allo stesso `!classifica` e sembrava una classifica
# mischiata (morph, #sbiffo 20:16). Schema deciso da vjt: nudo rispondono
# tutti, con l'argomento risponde solo il gioco nominato. Nessun sidecar sa
# degli altri, quindi ognuno riconosce solo il PROPRIO nome e tace sugli
# altri — coordinamento zero, che è l'unica cosa che regge a processi separati.
# La coda dopo il comando si ignora, non fa cadere la riga (ordine di vjt in DM
# 2026-09-09 16:59: «va bene accettare .bang qualsiasi cose»). L'ancora a fine
# riga di prima faceva sparire in silenzio ogni `.bang suora di merda`: misurato
# su peluche (#sbiffo 12:07:22 con la coda, nessuna risposta; 12:07:27 nudo,
# risposta), e spiega anche i `!bef` di deadbeef e katia finiti nel vuoto il 2/9.
# Il separatore resta obbligatorio: `.bangalore` non e' `.bang`.
CMD_PAT = re.compile(
    r'^\s*[.!](?P<cmd>bang|bef|suore|killers)(?:\s.*)?$', re.IGNORECASE)
CLASSIFICA_PAT = re.compile(
    r'^\s*[.!]\s*classifica(?:\s+(?P<game>\S+))?\s*$', re.IGNORECASE)
MY_GAME = ("suore", "suora", "sparasuore")
MEDAGLIE = ("🥇", "🥈", "🥉")


def empty():
    # Tutto è indicizzato per SCOPE (`rete/canale`), non per canale:
    #   scores      punteggi, l'unica cosa che non si può perdere
    #   nuns        la suora in giro in quello scope (None = nessuna)
    #   next_spawn  quando esce la prossima, per scope
    #   penalty     i 7 secondi dopo un fallimento, per scope e per nick
    #   last_taken  chi ha preso l'ultima e quando. Serve solo a dire di quanto
    #               sei arrivato tardi (RFE di katia, #sbiffo 9/9 02:17, via di
    #               vjt in DM alle 16:59) — non è punteggio, perderlo non rompe.
    return {"scores": {}, "nuns": {}, "next_spawn": {}, "penalty": {},
            "last_taken": {}}


def migrate(data):
    """Stato del formato a canale nudo -> formato a scope.

    Il vecchio teneva UNA suora, UN timer e i punteggi per canale nudo, perché
    di rete ce n'era una sola per processo. Le chiavi `#chan` diventano
    `LEGACY_NET/#chan`; quelle che hanno già uno slash si lasciano stare, così
    la migrazione è idempotente e girare due volte non raddoppia il prefisso.

    Cosa NON si porta dietro, per scelta: il `next_spawn` globale (il daemon ne
    rifà uno per scope al primo giro, e un timer perso costa al massimo una
    comparsa) e le penalità (durano 7 secondi: migrarle è lavoro per niente).
    I punteggi invece si portano tutti — quelli sono la partita."""
    def rekey(d):
        return {(k if "/" in k else f"{LEGACY_NET}/{k}"): v for k, v in d.items()}

    if "nun" in data:                       # marcatore del formato vecchio
        nun = data.pop("nun")
        nuns = data.setdefault("nuns", {})
        if nun and nun.get("chan"):
            chan = nun["chan"]
            nuns[chan if "/" in chan else f"{LEGACY_NET}/{chan}"] = nun
    if not isinstance(data.get("next_spawn"), dict):
        data["next_spawn"] = {}
    if data.get("penalty") and not all(isinstance(v, dict)
                                       for v in data["penalty"].values()):
        data["penalty"] = {}
    data["scores"] = rekey(data.get("scores", {}))
    data["last_taken"] = rekey(data.get("last_taken", {}))
    data["nuns"] = rekey(data.get("nuns", {}))
    return data


def load():
    try:
        data = json.loads(STATE.read_text())
    except (OSError, ValueError):
        return empty()
    data = migrate(data)
    for k, v in empty().items():
        data.setdefault(k, v)
    return data


def save(data):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, sort_keys=True))
    tmp.replace(STATE)


def canon(nick):
    return nick.strip().lower()


def score_for(data, scope, nick):
    chan_scores = data["scores"].setdefault(scope, {})
    return chan_scores.setdefault(canon(nick), {"kills": 0, "friends": 0, "nick": nick})


# Il podio si vede anche nel messaggio del singolo colpo, non solo su
# `.classifica` (ordine di vjt, #sbiffo 2026-09-10 13:13).
MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}


def board(data, scope, field="kills"):
    """Una delle due classifiche, ordinata come la riga che la stampa: killer
    su `kills`, marpioni su `friends`, e l'altro campo come spareggio. Chi ha
    zero non c'è — sono due partite diverse, non due colonne della stessa.

    È la SOLA fonte d'ordinamento: `leaderboard()`, `marpioni()` e la coda del
    messaggio del colpo passano tutte di qui, o la posizione annunciata subito
    dopo il tiro non combacia con quella che si legge su `.classifica`."""
    other = "friends" if field == "kills" else "kills"
    scores = data["scores"].get(scope, {})
    rank = sorted(scores.values(), key=lambda s: (-s[field], -s[other], s["nick"]))
    return [s for s in rank if s[field]]


def above(data, scope, nick, field="kills"):
    """Chi sta STRETTAMENTE sopra `nick`, per canon. Chiamata prima e dopo il
    colpo, la differenza dei due insiemi è esattamente chi è stato scavalcato:
    non serve ricostruire l'ordine vecchio. Chi non è ancora in classifica non
    fa `break` e si ritrova sotto tutti, che è la verità."""
    me = canon(nick)
    out = []
    for s in board(data, scope, field):
        if canon(s["nick"]) == me:
            break
        out.append(s)
    return out


def join_nicks(nicks):
    if len(nicks) < 2:
        return "".join(nicks)
    return ", ".join(nicks[:-1]) + " e " + nicks[-1]


def standing(data, scope, nick, passed, field="kills"):
    """La coda del messaggio: medaglia se sei sul podio, posizione, e chi hai
    scavalcato con QUESTO colpo. Stringa vuota se non sei in classifica, così
    il chiamante la concatena senza controlli.

    `field` sceglie la classifica: il `.bang` scala i killer, il `.bef` i
    marpioni — ognuno vede la propria, mai quella dell'altro gioco."""
    me = canon(nick)
    rank = board(data, scope, field)
    pos = next((i + 1 for i, s in enumerate(rank) if canon(s["nick"]) == me), None)
    if pos is None:
        return ""
    medal = MEDALS.get(pos)
    out = f" {medal} {pos}º" if medal else f" {pos}º"
    if passed:
        out += f", scavalcato {join_nicks([s['nick'] for s in passed])}"
    return out + "."


def say(net, chan, text):
    """Una riga sulla FIFO di QUELLA rete. Apertura non bloccante: senza lettore
    si prende ENXIO e si salta, invece di piantare il daemon su un bot morto.

    La FIFO arriva dall'oggetto Net, non da una globale: la risposta esce per
    forza dal bot da cui è arrivato il colpo."""
    if not net.fifo:
        return
    try:
        fd = os.open(net.fifo, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return
    try:
        # SAY:suore — tag di origine, solo per il log; vedi bot.py process_cmd.
        os.write(fd, f"SAY:suore {chan} {text}\n".encode())
    except OSError:
        pass
    finally:
        os.close(fd)


def plural(n, one, many):
    return one if n == 1 else many


def human_time(secs):
    """Il tempo di presa in forma leggibile. Ordine di peluche (#sbiffo
    2026-09-07 13:17-13:18), via di vjt («toca toca» / «fallo el mejor»):
    «i secondi non sono leggibili sopra i 60, e i millisecondi sono inutili».

    I millesimi erano il canone di rex (`you shot a duck in 4.417 seconds!`) e
    il tiebreak fra due che sparano subito, ma «i millisecondi sono inutili»
    non aveva eccezioni: peluche l'ha ribadito su #sbiffo il 2026-09-07 21:59
    («togli i millisecondi sotto al minuto», implicito nell'ordine di prima).
    Quindi via ovunque: interi sotto il minuto, h/m/s sopra.

    Ore e minuti a zero spariscono, come chiesto: mai un `0h` davanti."""
    if round(secs) < 60:         # sull'intero: 59.6 e' gia' un minuto
        return f"{round(secs)} secondi"
    secs = int(round(secs))
    h, rest = divmod(secs, 3600)
    m, s = divmod(rest, 60)
    if h:
        return f"{h}h {m:02d}m {s:02d}s"
    return f"{m}m {s:02d}s"


def schedule(data, scope, now, immediate=False):
    """Il prossimo appuntamento di QUELLO scope, con la cadenza di quel canale.

    Ogni canale ha la sua finestra (#sniffo 90-240, #sbiffo 60-180 per ordine di
    vjt del 6/9): con un timer solo per processo la cadenza era per forza uguale
    per tutti, ed è il motivo per cui c'era un processo per canale."""
    net, chan = SCOPES[scope]
    lo, hi = net.chans[chan]
    data["next_spawn"][scope] = now if immediate else now + random.uniform(lo, hi)


def spawn(data, scope, now):
    """Fa comparire una suora in UNO scope.

    Una sola per volta PER CANALE, come rex: finché quella in giro non viene
    presa non se ne aggiungono altre nello stesso canale, altrimenti `.bang` non
    saprebbe a quale rispondere. Canali diversi sono partite diverse e possono
    averne una ciascuno — prima non potevano, e i canali oltre il primo non
    vedevano mai una suora."""
    if scope not in SCOPES or data["nuns"].get(scope):
        return False
    net, chan = SCOPES[scope]
    golden = random.random() < P_GOLDEN
    hp = random.randint(*GOLDEN_HP) if golden else 1
    data["nuns"][scope] = {"chan": chan, "at": now, "golden": golden, "hp": hp}
    say(net, chan, spawn_line(golden))
    # L'attesa parte da quando questa viene PRESA, non da adesso: se il timer
    # girasse mentre la suora e' li' in attesa (rex non la fa scappare), dopo
    # ore di sit-in `next_spawn` sarebbe nel passato e la successiva uscirebbe
    # nello stesso secondo della presa. Vedi handle().
    data["next_spawn"].pop(scope, None)
    return True


def on_penalty(data, scope, nick, now):
    until = data["penalty"].get(scope, {}).get(canon(nick), 0)
    return now < until


def punish(data, scope, nick, now):
    # Per scope: la penalità è una conseguenza dentro una partita, e su un'altra
    # rete quel nick può anche essere un'altra persona.
    data["penalty"].setdefault(scope, {})[canon(nick)] = now + PENALTY


JAMS = [
    "{nick}: Il fucile si e' inceppato! Riprova fra {p} secondi.",
    "{nick}: Ti si e' inceppato il fucile, porco dio. Riprova fra {p} secondi.",
]
NOPES = [
    "{nick}: Chi l'avrebbe detto che le suore fossero cosi' schizzinose? Riprova fra {p} secondi.",
    "{nick}: La suora ha detto no. Magari corrompila con una pizza, alle suore piace la pizza no? Riprova fra {p} secondi.",
    "{nick}: La suora ti ha guardato male e ha tirato dritto. Riprova fra {p} secondi.",
]


LATE_WINDOW = 120.0


def late_by(data, scope, now):
    """La coda «l'hai mancata per X» quando spari a suora gia' presa.

    RFE di katia (#sbiffo 2026-09-09 02:17: «quando manchi la suora di poco,
    dire di quanto l'hai mancata»), via di vjt in DM alle 16:59. Sopra i due
    minuti tace: chi arriva dopo mezz'ora non l'ha mancata di poco, ha solo
    sparato al vuoto, e la riga diventerebbe rumore fisso su ogni `.bang` a
    canale spento."""
    last = data.get("last_taken", {}).get(scope)
    if not last:
        return ""
    gap = now - last.get("at", 0)
    if gap < 0 or gap > LATE_WINDOW:
        return ""
    return (f" L'hai mancata per {human_time(gap)}: se l'e' presa "
            f"{last.get('nick', 'qualcuno')}.")


def handle(data, scope, nick, cmd, now):
    """Applica un comando dentro UNO scope. Ritorna True se lo stato e' cambiato."""
    net, chan = SCOPES[scope]
    nun = data["nuns"].get(scope)
    here = bool(nun)

    if cmd in ("suore",):
        s = score_for(data, scope, nick)
        # Le dorate stanno nello stato da sempre ma non si vedevano da nessuna
        # parte (ordine di vjt, 7/9): il conto totale le comprende gia', qui si
        # dichiara solo quante di quelle erano dorate. Zero dorate = riga
        # invariata, che e' il caso di quasi tutti.
        gold = s.get("golden", 0)
        di_cui = (f", di cui {gold} {plural(gold, 'dorata', 'dorate')}"
                  if gold else "")
        say(net, chan, f"{nick} ha fatto fuori {s['kills']} "
                       f"{plural(s['kills'], 'suora', 'suore')}{di_cui} e ne ha "
                       f"fatte amiche {s['friends']} in {chan}.")
        return True  # score_for puo' aver creato la riga del nick

    if cmd in ("classifica", "killers"):
        # Due righe per ordine di vjt (#sbiffo 9/9, 01:32): «una per i machi e
        # una per i loffi». La seconda esce solo se un loffio esiste davvero.
        say(net, chan, leaderboard(data, scope))
        loffi = marpioni(data, scope)
        if loffi:
            say(net, chan, loffi)
        return False

    # Il ban vale sul fucile e sull'amicizia, non sulle statistiche: chi e'
    # bannato puo' ancora leggere la classifica, non giocarci. Parla ogni
    # volta, apposta — «diglielo» era parte dell'ordine.
    until = BANS.get(canon(nick), 0)
    if now < until:
        say(net, chan, f"{nick}: sei bannato dal gioco per aver scriptato — beccato "
                       f"con una suora finta che il sidecar non ha mai emesso. "
                       f"Rientri fra {human_time(until - now)}.")
        return False

    if on_penalty(data, scope, nick, now):
        # Silenzio voluto: vedi il commento in testa al file.
        return False

    if not here:
        if cmd == "bang":
            say(net, chan, f"{nick}: Non c'e' nessuna suora! A cosa stai sparando?"
                           f"{late_by(data, scope, now)}")
        else:
            say(net, chan, f"{nick}: Hai provato a fare amicizia con una suora "
                           f"inesistente. Robaccia da denuncia."
                           f"{late_by(data, scope, now)}")
        return False

    if cmd == "bang" and random.random() < P_JAM:
        punish(data, scope, nick, now)
        say(net, chan, random.choice(JAMS).format(nick=nick, p=PENALTY))
        return True
    if cmd == "bef" and random.random() < P_NOPE:
        punish(data, scope, nick, now)
        say(net, chan, random.choice(NOPES).format(nick=nick, p=PENALTY))
        return True

    # La dorata incassa: il colpo va a segno ma non la porta via, e chi l'ha
    # colpita NON si prende la penalità — ha fatto la sua parte, tocca al
    # canale finire il lavoro.
    if nun.get("hp", 1) > 1:
        nun["hp"] -= 1
        verbo = "Il colpo l'ha presa in pieno" if cmd == "bang" else "Ha quasi ceduto"
        say(net, chan, f"\x02{nick}: {verbo}, ma la suora dorata regge ancora: "
                       f"le restano {nun['hp']} "
                       f"{plural(nun['hp'], 'vita', 'vite')}.\x0F")
        return True

    took = human_time(now - nun["at"])
    golden = nun.get("golden", False)
    data["nuns"][scope] = None
    # Chi l'ha presa e quando: lo legge late_by() per dire agli altri di quanto
    # sono arrivati tardi. Nick col casing visto ora, non canonicalizzato.
    data.setdefault("last_taken", {})[scope] = {"at": now, "nick": nick}
    schedule(data, scope, now)   # l'attesa parte da qui, non dalla comparsa
    s = score_for(data, scope, nick)
    s["nick"] = nick          # il casing buono e' l'ultimo visto
    if golden:
        s["golden"] = s.get("golden", 0) + 1
    # Il prima e il dopo si misurano attorno all'incremento: `s` è già dentro
    # `data`, quindi la seconda chiamata vede il punto appena fatto. La
    # classifica non si ricalcola a mano da nessuna parte.
    field = "kills" if cmd == "bang" else "friends"
    was_above = {canon(x["nick"]) for x in above(data, scope, nick, field)}
    s[field] += 1
    now_above = {canon(x["nick"]) for x in above(data, scope, nick, field)}
    jumped = was_above - now_above
    passed = [x for x in board(data, scope, field) if canon(x["nick"]) in jumped]
    tail = standing(data, scope, nick, passed, field)
    if cmd == "bang":
        if golden:
            say(net, chan, f"\x02{nick} ha steso la SUORA DORATA in {took}!\x0F "
                           f"Ne ha stese {s['kills']} in {chan}, di cui "
                           f"{s['golden']} "
                           f"{plural(s['golden'], 'dorata', 'dorate')}.{tail}")
        else:
            say(net, chan, f"{nick} hai fatto secca una suora in {took}! "
                           f"Ne hai stese {s['kills']} in {chan}.{tail}")
    else:
        if golden:
            say(net, chan, f"\x02{nick} si e' fatto amico la SUORA DORATA in "
                           f"{took}!\x0F Ne ha convertite {s['friends']} in "
                           f"{chan}, di cui {s['golden']} "
                           f"{plural(s['golden'], 'dorata', 'dorate')}.{tail}")
        else:
            say(net, chan, f"{nick} hai fatto amicizia con una suora in {took}! "
                           f"Ora sei amico di {s['friends']} "
                           f"{plural(s['friends'], 'suora', 'suore')} in {chan}.{tail}")
    return True


def leaderboard(data, scope, limit=12, maxlen=380):
    """Una riga sola, come rex: i primi in grassetto, troncati con '...'.

    Il tetto e' in byte utili, non in voci: un nick lungo ne vale tre corti, e
    il server taglia a 512 la riga intera comprese le intestazioni."""
    # Solo chi ha sparato: e' la classifica dei killer, come `.killers` di rex.
    # Chi fa solo amicizia non e' ultimo in classifica, e' in un'altra partita.
    rank = board(data, scope, "kills")[:limit]
    if not rank:
        return ("🔫 [Sparasuore] Classifica vuota: nessuno ha ancora sparato "
                "a una suora. Che noia.")
    # Le dorate si mostrano solo se in classifica ce n'e' almeno una: la legenda
    # costa byte alla riga (il tetto e' `maxlen`), e su un canale dove nessuno ne
    # ha ancora stesa una sarebbe legenda per un simbolo che non compare.
    # Formato `3, di cui 1 ★`: il `3★1` di prima non si capiva (vjt, 7/9) —
    # due numeri attaccati si leggono come un punteggio composto, non come
    # "tre suore, una delle quali dorata".
    # Prefisso `[Sparasuore]` per ordine di vjt (#sbiffo 20:28): su `!classifica`
    # nudo parlano piu' giochi di fila e senza etichetta le righe si leggono
    # come una classifica sola. Il canale non lo dice piu' nessuno: la riga
    # esce nel canale a cui si riferisce.
    # Medaglie ai primi tre e puntino agli altri, come la hall of fame
    # dell'impiccato: le due classifiche escono di fila sullo stesso
    # `!classifica` e devono somigliarsi (vjt, #sbiffo 20:28).
    has_gold = any(s.get("golden", 0) for s in rank)
    head = ("🔫 [Sparasuore] Classifica (⭐ = dorate): " if has_gold
            else "🔫 [Sparasuore] Classifica: ")
    return _rank_line(head, rank, "kills", maxlen, gold=True)


def marpioni(data, scope, limit=12, maxlen=380):
    """La riga gemella di chi le suore se le fa amiche (ordine di vjt, 9/9).

    Si chiamava «Crumiri» fino al 2026-09-09 16:59: rinominata su proposta di
    `deadbeef_` (#ai 14:45, «chi fa .bef non vuole farsi amiche le suore ma
    sedurle») e via di vjt in DM («marpioni ok»). La parola l'aveva scelta lui,
    quindi solo lui poteva cambiarla.

    Sta separata dalla classifica dei killer per la ragione scritta la': chi
    fa amicizia non e' ultimo fra i killer, e' in un'altra partita — e due
    partite diverse vogliono due righe diverse, non una colonna in piu'.
    Torna None quando nessuno ha ancora convertito una suora: una riga che
    dice "nessun marpione" e' rumore su ogni `.classifica` di un canale che
    gioca solo a sparare."""
    rank = board(data, scope, "friends")[:limit]
    if not rank:
        return None
    return _rank_line("😏 [Sparasuore] Marpioni: ", rank, "friends", maxlen)


def _rank_line(head, rank, field, maxlen, gold=False):
    """Il corpo comune alle due righe: medaglia, nick in grassetto, numero.

    Il tetto e' in byte utili e non in voci, quindi si tronca con '…' appena
    il pezzo successivo sfora: un nick lungo ne vale tre corti."""
    # Il parimerito prende la STESSA medaglia (vjt, #sbiffo 9/9 01:34: «tra i
    # marpioni deadbeef_ e katia sono a parimerito»): la medaglia la decide il
    # punteggio, non la posizione nella lista. Due a pari sono due ori, e il
    # terzo punteggio distinto prende il bronzo — piazzamento denso, come si fa
    # sul podio vero. E dopo due ori si va a BRONZO, non ad argento (vjt,
    # 01:35: «e poi deadbeef il bronzo»): la posizione e' quanta gente hai
    # davvero davanti, quindi due primi bruciano il secondo posto. L'ordine
    # dentro il gruppo resta quello del sort, che ha gia' i suoi spareggi:
    # qui si tolgono solo le medaglie diverse a chi ha lo stesso punteggio.
    parts, out = [], head
    for s in rank:
        i = sum(1 for altro in rank if altro[field] > s[field])
        medaglia = MEDAGLIE[i] if i < len(MEDAGLIE) else "·"
        dorate = s.get("golden", 0) if gold else 0
        piece = (f"{medaglia} \x02{s['nick']}\x02 {s[field]}"
                 + (f", di cui {dorate} ⭐" if dorate else ""))
        if len(out) + len(piece) + 3 > maxlen:
            parts.append("…")
            break
        parts.append(piece)
        out = head + " · ".join(parts)
    return head + " · ".join(parts)


def process(net, line, data, now, talk=True):
    """Una riga del log di UNA rete. Il canale si risolve a scope dentro quella
    rete: lo stesso `#sniffo` su un'altra rete è un altro scope, e una riga
    letta dal log sbagliato non può più finire nella partita giusta."""
    m = PRIVMSG_PAT.search(line)
    if not m:
        return False
    nick, chan, text = m.group("nick"), m.group("chan").lower(), m.group("text")
    if chan not in net.chans:
        return False
    scope = net.scope(chan)
    k = CLASSIFICA_PAT.match(text)
    if k:
        game = (k.group("game") or "").lower()
        # Nudo = tutti; con un nome che non è il mio taccio e lascia parlare
        # chi si chiama così. Non rispondo "gioco sconosciuto": non so quali
        # altri sidecar sono vivi, e un errore mio su un comando altrui è
        # rumore garantito.
        if game and game not in MY_GAME:
            return False
        return handle(data, scope, nick, "classifica", now) if talk else False
    c = CMD_PAT.match(text)
    if not c:
        return False
    if not talk:
        return False
    return handle(data, scope, nick, c.group("cmd").lower(), now)


def daemon():
    data = load()
    now = time.time()
    dirty = False
    for scope in SCOPES:
        # Ogni scope parte col suo appuntamento. Uno scope nuovo (canale appena
        # aggiunto alla config) si programma da solo al primo giro, senza che
        # nessuno debba toccare lo stato a mano.
        if not data["next_spawn"].get(scope) and not data["nuns"].get(scope):
            schedule(data, scope, now)
            dirty = True
    if dirty:
        save(data)

    # Un tail per rete. `tail -F` e non `-f`: i bot.log ruotano, e con -f dopo
    # una rotazione il sidecar resta attaccato all'inode morto e ammutolisce.
    tails = {}
    for net in NETS:
        p = subprocess.Popen(["tail", "-F", "-n", "0", net.log],
                             stdout=subprocess.PIPE, text=True, errors="replace")
        tails[p.stdout] = (net, p)

    while True:
        # Timeout corto: i tail sono bloccanti, ma la comparsa e' a tempo e deve
        # scattare anche su un canale muto da ore.
        ready, _, _ = select.select(list(tails), [], [], 5.0)
        now = time.time()
        dirty = False
        for stream in ready:
            net, _p = tails[stream]
            line = stream.readline()
            if not line:
                # Quel tail è morto: senza di lui quella rete è cieca, e un
                # sidecar mezzo sordo che finge di funzionare è peggio di uno
                # spento. Esce, e systemd lo rialza con tutti i tail nuovi.
                raise SystemExit(f"suore: il tail di {net.name} ({net.log}) è morto")
            if process(net, line, data, now):
                dirty = True
        for scope, at in list(data["next_spawn"].items()):
            if at and now >= at and spawn(data, scope, now):
                dirty = True
        if dirty:
            save(data)


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    if arg == "scopes":
        for net in NETS:
            print(f"{net.name}: log={net.log} fifo={net.fifo or '(muto)'}")
            for chan, (lo, hi) in sorted(net.chans.items()):
                print(f"  {net.scope(chan)}  cadenza {lo/60:g}-{hi/60:g} min")
    elif arg == "list":
        data = load()
        for scope in sorted(SCOPES):
            print(f"— {scope}")
            print(leaderboard(data, scope))
            loffi = marpioni(data, scope)
            if loffi:
                print(loffi)
    elif arg == "spawn":
        data = load()
        # Senza argomento spara in tutti gli scope: è il comando delle prove a
        # mano, e con più canali «spawn» senza dire dove non ha un default ovvio.
        wanted = sys.argv[2:] or sorted(SCOPES)
        for scope in wanted:
            if scope not in SCOPES:
                raise SystemExit(f"suore: scope sconosciuto {scope!r} — "
                                 f"ce ne sono {sorted(SCOPES)}")
            spawn(data, scope, time.time())
        save(data)
    else:
        daemon()


if __name__ == "__main__":
    main()
