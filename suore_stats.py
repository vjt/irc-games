#!/usr/bin/env python3
"""Dal log eventi al JSON che mangia la pagina web.

Il gioco tiene due file: `suore_state.json`, che e' la verita' per le
classifiche in canale, e `suore_events.ndjson`, che e' lo storico. Qui si
derivano le statistiche a fette di tempo — quelle che i contatori aggregati non
possono dare — e si sputa un JSON solo, che la pagina scarica e disegna.

Regole che vale la pena non perdere:

  * **Le classifiche vengono dallo STATO, non dal log.** Il log parte dal
    6 settembre e non copre gli scope dove nessuno stava a guardare, quindi
    sommarlo darebbe numeri diversi da quelli che il bot dice in canale. Il
    log serve per il tempo (giorni, ore, reazioni), lo stato per i punteggi.

  * Il nick canonico e' quello dello stato (`canon()` = lower), ma si mostra
    il casing buono, che e' l'ultimo visto.

  * Se le due fonti litigano non si aggiusta niente in silenzio: la differenza
    finisce in `discrepanza`, cosi' la pagina puo' dirlo invece di mentire.
"""

import collections
import datetime
import json
import os
import re
import sys

# La pagina e' per gente italiana su un canale italiano: le giornate si tagliano
# sull'ora di Roma, non su UTC, o un colpo dell'una di notte finisce ieri.
ROME = datetime.timezone(datetime.timedelta(hours=2))


def _day(ts):
    return datetime.datetime.fromtimestamp(ts, ROME).strftime("%Y-%m-%d")


def _hour(ts):
    return datetime.datetime.fromtimestamp(ts, ROME).hour


def _mediana(valori):
    v = sorted(valori)
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


# Mezz'ora: la grana con cui si dice quando esce la prossima. Deve restare
# uguale a `suore.BUCKET`, o il canale e la pagina direbbero due finestre
# diverse per la stessa suora.
BUCKET = 1800.0


def _prossima(state, scope):
    """Quando esce la prossima suora di uno scope, a scarti di mezz'ora.

    Ordine di vjt (#sniffo 2026-09-10 19:17). Tre stati, e vanno tenuti
    distinti perche' la pagina deve dire tre cose diverse:

      `in_giro`  c'e' una suora in canale adesso — `next_spawn` non esiste
                 nemmeno, spawn() lo cancella apposta;
      `attesa`   nessuna in giro, appuntamento preso: la finestra `da`/`a`;
      `ignota`   scope senza appuntamento (appena aggiunto, o sidecar fermo).

    `da` e' agganciato alla griglia assoluta delle mezz'ore e NON a "adesso":
    il JSON viene riscritto solo quando succede qualcosa, e una finestra
    relativa invecchierebbe in silenzio fra un evento e l'altro.

    Il secondo esatto non esce di qui apposta: pubblicarlo regalerebbe il
    cronometro a chi scripta."""
    if (state.get("nuns") or {}).get(scope):
        return {"stato": "in_giro"}
    at = (state.get("next_spawn") or {}).get(scope)
    if not at:
        return {"stato": "ignota"}
    da = int(at // BUCKET) * BUCKET
    return {"stato": "attesa", "da": da, "a": da + BUCKET}


def _ultima(events, state, scope):
    """L'ultimo evento di uno scope: quando, chi, e se sparata o fatta amica.

    «quando c'e' stata == quando e' stata sparata / fatta amica l'ultima.
    insomma l'ultimo evento» (vjt, #sniffo 19:17) ⇒ vale tanto il `.bang`
    quanto il `.bef`, non i soli colpi.

    Il log e' la fonte buona perche' porta anche il verbo, ma parte dal 6
    settembre: dove non copre si ripiega su `last_taken` dello stato, che il
    verbo non ce l'ha. Meglio un `verbo` nullo dichiarato che una data finta."""
    if events:
        ev = max(events, key=lambda e: e["ts"])
        return {"ts": ev["ts"], "nick": ev["nick"], "verbo": ev["verb"]}
    last = (state.get("last_taken") or {}).get(scope)
    if last and last.get("at") is not None:
        return {"ts": last["at"], "nick": last.get("nick"), "verbo": None}
    return None


def _pct(flop, fatte):
    tentate = flop + fatte
    return round(100 * flop / tentate) if tentate else None


def _bello(scores, c):
    """Dal nick canonico al casing buono, ripescato dalla classifica."""
    row = scores.get(c)
    return row.get("nick", c) if isinstance(row, dict) else c


def _rivalita(soffiate, scores, n=5):
    """Le coppie chi-ha-soffiato-a-chi piu' frequenti (puppy, #sbiffo 13/10),
    con la media dei secondi di scarto. Gli int del formato vecchio (contati
    senza soglia e senza tempi) non si sa cosa contino: si saltano."""
    coppie = [{"chi": _bello(scores, chi), "vittima": _bello(scores, vitt),
               "n": k["n"], "secs": round(k["s"] / k["n"], 1)}
              for chi, vittime in (soffiate or {}).items()
              for vitt, k in vittime.items()
              if isinstance(k, dict) and k.get("n")]
    coppie.sort(key=lambda r: (-r["n"], r["chi"].lower(), r["vittima"].lower()))
    return coppie[:n]


# Quante righe nel grafico delle quote: oltre le sei i colori non si
# distinguono piu' e le linee in fondo sono un pettine schiacciato sullo zero.
TOP_QUOTE = 6


def _giorni(primo, ultimo):
    """Tutti i giorni da `primo` a `ultimo`, compresi, anche quelli vuoti."""
    d = datetime.date.fromisoformat(primo)
    fine = datetime.date.fromisoformat(ultimo)
    out = []
    while d <= fine:
        out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


def _quote(events, scores, n=TOP_QUOTE):
    """Quota cumulata di suore stese, giorno per giorno (peluche, #sbiffo 16/10).

    Per ognuno dei primi `n`: stese sue fino a quel giorno diviso stese di tutti
    fino a quel giorno, in percento, e il posto che aveva quel giorno contando
    tutti quelli del log (pari merito alla maniera del canale, `None` finche'
    non ne ha stesa una). Si vede se il distacco si chiude o si allarga.

    E' una serie storica, quindi viene dal LOG e parte dal 6 settembre: chi ha
    tutto prima non ci compare, e l'ultimo punto puo' non coincidere con la
    classifica dello stato (la differenza e' gia' in `discrepanza`). L'ORDINE
    dei primi `n` invece e' quello dello stato, come la tabella; chi esiste
    solo nel log viene dopo, col casing dell'ultima volta che si e' visto.
    Solo i `.bang`: e' la quota di chi spara."""
    colpi = sorted((e for e in events if e["verb"] == "bang"), key=lambda e: e["ts"])
    if not colpi:
        return None
    visto = {}
    per_giorno = collections.defaultdict(collections.Counter)
    for e in colpi:
        c = e["nick"].lower()
        visto[c] = e["nick"]
        per_giorno[_day(e["ts"])][c] += 1
    giorni = _giorni(min(per_giorno), max(per_giorno))

    def kills(c):
        row = scores.get(c)
        return row.get("kills", 0) if isinstance(row, dict) else 0

    finale = collections.Counter(e["nick"].lower() for e in colpi)
    primi = sorted(finale, key=lambda c: (-kills(c), -finale[c], c))[:n]

    acc = collections.Counter()
    totale, quota, posto = [], {c: [] for c in primi}, {c: [] for c in primi}
    for g in giorni:
        acc.update(per_giorno.get(g, {}))
        tot = sum(acc.values())
        totale.append(tot)
        for c in primi:
            mie = acc[c]
            quota[c].append(round(100 * mie / tot, 1))
            posto[c].append(1 + sum(1 for v in acc.values() if v > mie) if mie else None)
    return {
        "giorni": giorni,
        "totale": totale,
        "nick": [{"nick": _bello(scores, c) if c in scores else visto[c],
                  "quota": quota[c], "posto": posto[c]} for c in primi],
    }


def _scope_stats(events, scores, flops=None, soffiate=None):
    """Statistiche di uno scope. `events` gia' filtrati, `scores` dallo stato."""
    per_day = collections.defaultdict(lambda: {"bang": 0, "bef": 0})
    per_hour = [0] * 24
    # Heatmap giorno della settimana x ora (puppy, #sbiffo 13/10): righe
    # lunedi'..domenica, ora di Roma.
    per_dow_hour = [[0] * 24 for _ in range(7)]
    # ...e chi c'era (puppy, 13/10: «cosi' si capisce chi e' nottambul*»).
    chi_dow_hour = [[collections.Counter() for _ in range(24)] for _ in range(7)]
    notte = collections.Counter()
    per_nick_time = collections.defaultdict(list)
    # Il record personale si misura sui soli colpi, come quello di scope: il
    # `.bef` e' un'altra partita e ci finirebbe dentro con tempi non comparabili.
    per_nick_bang = collections.defaultdict(list)
    best = None

    for ev in sorted(events, key=lambda e: e["ts"]):
        per_day[_day(ev["ts"])][ev["verb"]] += 1
        per_hour[_hour(ev["ts"])] += 1
        quando = datetime.datetime.fromtimestamp(ev["ts"], ROME)
        per_dow_hour[quando.weekday()][quando.hour] += 1
        chi_dow_hour[quando.weekday()][quando.hour][ev["nick"].lower()] += 1
        if quando.hour < 6:
            notte[ev["nick"].lower()] += 1
        r = ev.get("reaction")
        if r is None:
            continue
        # Chiave in lower: nel log il casing e' quello visto sul momento
        # (`Mezmerize` una volta, `mezmerize` un'altra), nello stato e' un'altra
        # scelta ancora. Indicizzare per casing perde i tempi di chi cambia.
        nick = ev["nick"].lower()
        per_nick_time[nick].append(r)
        if ev["verb"] == "bang":
            per_nick_bang[nick].append(r)
            if best is None or r < best["secs"]:
                best = {"nick": ev["nick"], "secs": round(r, 3), "ts": ev["ts"]}

    tabella = []
    for canon, row in scores.items():
        if not isinstance(row, dict):
            continue
        chiave = str(row.get("nick", canon)).lower()
        tempi = per_nick_time.get(chiave, [])
        colpi = per_nick_bang.get(chiave, [])
        tabella.append({
            "nick": row.get("nick", canon),
            "kills": row.get("kills", 0),
            "friends": row.get("friends", 0),
            "golden": row.get("golden", 0),
            # Di quelle, quante convertite: la pagina mette la stella sulla
            # classifica giusta invece di darle tutte ai killer.
            "golden_friends": row.get("golden_friends", 0),
            # Cilecca in percentuale (puppy, #sbiffo 13/10): fucile inceppato
            # e suora che tira dritto, sui tentativi = a segno + a vuoto.
            "cilecca_pct": _pct(
                sum(((flops or {}).get(canon) or {}).values()),
                row.get("kills", 0) + row.get("friends", 0)),
            # Media E mediana: la suora resta li' finche' qualcuno non la vede,
            # quindi un colpo dato dopo venti minuti trascina la media in cielo
            # (misurato: medie da 1255 s con reazioni tipiche da 20). La mediana
            # dice come si gioca davvero, la media dice quanto si e' distratti.
            "media": round(sum(tempi) / len(tempi), 2) if tempi else None,
            "mediana": round(_mediana(tempi), 2) if tempi else None,
            # Chi fa solo `.bef` (puppy, 52 amiche e zero stese) restava con la
            # colonna vuota: il record si ripiega sui suoi tempi d'amicizia
            # (puppy, #sbiffo 13/10). Chi ha almeno una stesa resta sui colpi.
            "record": round(min(colpi or tempi), 2) if tempi else None,
            "eventi": len(tempi),
        })
    tabella.sort(key=lambda r: (-r["kills"], -r["friends"], r["nick"].lower()))

    kills_stato = sum(r["kills"] for r in tabella)
    bang_log = sum(1 for e in events if e["verb"] == "bang")

    return {
        "totali": {
            "kills": kills_stato,
            "friends": sum(r["friends"] for r in tabella),
            "golden": sum(r["golden"] for r in tabella),
            "eventi": len(events),
        },
        "classifica": tabella,
        "record": best,
        "per_giorno": {d: per_day[d] for d in sorted(per_day)},
        "per_ora": per_hour,
        "heatmap": per_dow_hour,
        # Tutti i tiratori per cella, in ordine: i primi tre fanno tooltip e
        # iniziale, il resto serve al filtro per nick della pagina.
        "heatmap_chi": [[[[_bello(scores, n), k] for n, k in c.most_common()]
                         for c in riga] for riga in chi_dow_hour],
        # Chi spara fra mezzanotte e le sei, i primi tre.
        "nottambuli": [[_bello(scores, n), k] for n, k in notte.most_common(3)],
        "rivalita": _rivalita(soffiate, scores),
        "quote": _quote(events, scores),
        # Non e' un errore da correggere: il log e lo stato hanno storie
        # diverse (azzeramenti a mano, annunci mai archiviati). Dichiararlo.
        "discrepanza": bang_log - kills_stato,
    }


# --- storia delle dorate, dal log del bot ------------------------------------
#
# Ogni stella della bacheca racconta la sua dorata (puppy, #sbiffo 13/10): chi
# l'ha colpita, quante vite le restavano dopo ogni colpo, chi ha fatto cilecca
# e chi l'ha chiusa. Il log eventi tiene solo la chiusura; il resto sta nelle
# righe che il gioco ha detto in canale, nel `bot.log` di ogni rete. Le regex
# sono la fotografia dei messaggi di suore.py (tests/test_suore.py le prova
# contro le formule vere: se cambia una riga del gioco, il test diventa rosso).

_RIGA = re.compile(r"^(?P<hms>\d\d:\d\d:\d\d)\s+>\s+\[suore\]\s+PRIVMSG\s+"
                   r"(?P<chan>#\S+)\s+:(?P<body>.*)$")
_CTRL = re.compile(r"\x03\d{1,2}(?:,\d{1,2})?|[\x02\x03\x0f\x16\x1d\x1f]")
_COMPARSA = re.compile(r"\\_(?P<testa>\S)< ")
_TESTE_DORATE = "☩✠✟"
_COLPO = re.compile(r"^(?P<nick>[^:\s]+): (?P<come>Il colpo l'ha presa in pieno|Ha quasi "
                    r"ceduto), ma la suora dorata regge ancora: le rest(?:a|ano) "
                    r"(?P<vite>\d+) vit[ae]")
_FLOP = (
    ("jam", re.compile(r"^(?P<nick>[^:\s]+): (?:Il fucile si e' inceppato|"
                       r"Ti si e' inceppato il fucile)")),
    ("nope", re.compile(r"^(?P<nick>[^:\s]+): (?:Chi l'avrebbe detto che le suore|"
                        r"La suora ha detto no|La suora ti ha lanciato un'occhiataccia)")),
    ("boom", re.compile(r"^(?P<nick>[^:\s]+): l'RPG ti e' esploso in faccia")),
)
# «si e' fatto amico» e' la riga del .bef dorato prima del 13/10.
_PRESA_DORATA = re.compile(r"^(?P<nick>\S+) (?:ha steso|si e' fatto amico|ha fatto "
                           r"amicizia con) la SUORA DORATA in ")
_PRESA = re.compile(r"^\S+ hai fatto (?:secca|amicizia con) una suora in ")
# Lo scarto ammesso fra l'ora del log e quella dell'evento: la riga parte
# verso la FIFO un attimo dopo il colpo, e i tempi del backfill sono arrotondati.
_TOLLERANZA = 3

try:
    from zoneinfo import ZoneInfo
    _LOCALE = ZoneInfo("Europe/Rome")   # il log del jail e' in ora locale, CET o CEST
except Exception:                       # pragma: no cover — tzdata mancante
    _LOCALE = ROME


def _sec(ts):
    t = datetime.datetime.fromtimestamp(ts, _LOCALE)
    return t.hour * 3600 + t.minute * 60 + t.second


def _vicino(a, b):
    d = abs(a - b) % 86400
    return min(d, 86400 - d) <= _TOLLERANZA


def fonti(nets=None):
    """[(rete, log)] da `SUORE_NETS`, lo stesso formato che legge suore.py."""
    nets = os.environ.get("SUORE_NETS", "") if nets is None else nets
    out = []
    for rec in nets.split(";"):
        parti = rec.strip().split("|")
        if len(parti) >= 2 and parti[0] and parti[1]:
            out.append((parti[0].strip(), parti[1].strip()))
    return out


def giri(sources):
    """Dai log alle dorate, una per comparsa: scope, ora della comparsa (in
    secondi del giorno: il log non ha la data), la `storia` come lista di
    [nick, esito, vite rimaste] e, se il log l'ha vista, chi l'ha chiusa e
    quando. `esito` e' `bang`/`bef` per un colpo a segno, `jam`/`nope`/`boom`
    per una cilecca (vite null). Una comparsa normale o una presa normale nello
    stesso canale chiudono la dorata rimasta appesa senza chiusura."""
    out = []
    for net, path in sources:
        aperte = {}
        try:
            fh = open(path, encoding="utf-8", errors="replace")
        except OSError:
            continue
        with fh:
            for line in fh:
                if "[suore]" not in line:
                    continue
                m = _RIGA.match(line)
                if not m:
                    continue
                scope = f"{net}/{m['chan'].lower()}"
                body = _CTRL.sub("", m["body"]).replace("\u200b", "").strip()
                h, mi, se = (int(x) for x in m["hms"].split(":"))
                sec = h * 3600 + mi * 60 + se
                c = _COMPARSA.search(body)
                if c:
                    aperte.pop(scope, None)
                    if c["testa"] in _TESTE_DORATE:
                        aperte[scope] = {"scope": scope, "comparsa": sec, "storia": []}
                        out.append(aperte[scope])
                    continue
                g = aperte.get(scope)
                if g is None:
                    continue
                if x := _COLPO.match(body):
                    g["storia"].append([x["nick"], "bang" if x["come"].startswith("Il")
                                        else "bef", int(x["vite"])])
                elif x := _PRESA_DORATA.match(body):
                    g.update(chi=x["nick"], chiusa=sec, rpg="Con l'RPG." in body)
                    del aperte[scope]
                elif _PRESA.match(body):
                    del aperte[scope]
                else:
                    for kind, rx in _FLOP:
                        if x := rx.match(body):
                            g["storia"].append([x["nick"], kind, None])
                            break
    return out


def abbina(ev, candidati):
    """La dorata del log che corrisponde a un evento dorato, o None.

    Il log ha solo l'ora, quindi si confrontano due orari: la comparsa (`ts`
    meno la reazione) e la chiusura (con lo stesso nick). Basta uno dei due —
    la riga di chiusura arriva nel log DOPO che il gioco ha riscritto il JSON
    — ma se il log dice un altro nick per la chiusura non e' quella."""
    comparsa = _sec(ev["ts"] - ev["reaction"]) if ev.get("reaction") is not None else None
    chiusa = _sec(ev["ts"])
    scelta = None
    for g in candidati:
        if g["scope"] != ev["scope"]:
            continue
        stesso = g.get("chi") is None or g["chi"].lower() == ev["nick"].lower()
        if not stesso:
            continue
        ok_c = comparsa is not None and _vicino(g["comparsa"], comparsa)
        ok_f = g.get("chiusa") is not None and _vicino(g["chiusa"], chiusa)
        if ok_c and ok_f:
            return g
        if (ok_c or ok_f) and scelta is None:
            scelta = g
    return scelta


def _chiave(ev):
    return f"{ev['scope']}|{ev['ts']}"


def storia(g):
    """Dal giro del log a quello che finisce nel JSON. `rpg` se la riga lo
    dice, o se l'ultimo colpo le lasciava piu' di una vita (o nessun colpo:
    una dorata parte da tre): solo il razzo chiude in un colpo solo."""
    colpi = [r for r in g["storia"] if r[1] in ("bang", "bef")]
    out = {"storia": g["storia"]}
    if g.get("rpg") or not colpi or colpi[-1][2] > 1:
        out["rpg"] = True
    return out


def storie(path, events, sources=None):
    """Le storie delle dorate, con la cache accanto al JSON (`suore_storia.json`).

    Il log si rilegge solo quando c'e' una dorata mai vista: costa qualche mega
    di testo, e una dorata esce una volta ogni tanto. La cache e' anche la
    memoria: se il `bot.log` viene ruotato, le storie gia' lette restano. Una
    dorata che il log non copre (prima del 6 settembre, dal backfill) si
    ricorda come null, cosi' non si rilegge tutto a ogni colpo."""
    cache_path = os.path.join(os.path.dirname(path) or ".", "suore_storia.json") if path else None
    cache = {}
    if cache_path and os.path.exists(cache_path):
        try:
            with open(cache_path, encoding="utf-8") as fh:
                cache = json.load(fh)
        except (OSError, ValueError):
            cache = {}
    mancano = [e for e in events if e.get("golden") and _chiave(e) not in cache]
    if not mancano:
        return cache
    sources = fonti() if sources is None else sources
    letti = [s for s in sources if os.path.exists(s[1])]
    if not letti:
        return cache
    tutti = giri(letti)
    for ev in mancano:
        g = abbina(ev, tutti)
        cache[_chiave(ev)] = storia(g) if g else None
    if cache_path:
        _atomic(cache_path, json.dumps(cache, ensure_ascii=False, separators=(",", ":")))
    return cache


def _dorata(e, storie_):
    out = {"ts": e["ts"], "scope": e["scope"], "nick": e["nick"], "verbo": e["verb"]}
    out.update((storie_ or {}).get(_chiave(e)) or {})
    return out


def build(events, state, generated, storie_=None):
    scores = state.get("scores", {})
    scopes = sorted(set(scores) | {e["scope"] for e in events})
    per_scope = {}
    for scope in scopes:
        suoi = [e for e in events if e["scope"] == scope]
        per_scope[scope] = _scope_stats(
            suoi, scores.get(scope, {}),
            (state.get("flops") or {}).get(scope),
            (state.get("soffiate") or {}).get(scope))
        per_scope[scope]["ultima"] = _ultima(suoi, state, scope)
        per_scope[scope]["prossima"] = _prossima(state, scope)

    giorni = sorted({_day(e["ts"]) for e in events})
    return {
        "generato": round(generated, 3),
        "primo_evento": min((e["ts"] for e in events), default=None),
        "ultimo_evento": max((e["ts"] for e in events), default=None),
        "giorni_coperti": len(giorni),
        "eventi_totali": len(events),
        "scopes": per_scope,
        # Una riga per dorata, per la bacheca (puppy, 13/10): chi, dove, quando.
        # Con la `storia` quando il log la copre: vedi `storie()`.
        "dorate": [_dorata(e, storie_) for e in events if e.get("golden")],
    }


# Per tiratore, dalla classifica: nome metrica, chiave, HELP. I tempi in secondi.
_PER_NICK = [
    ("suore_kills_total", "kills", "counter", "Suore stese."),
    ("suore_golden_total", "golden", "counter", "Suore dorate stese."),
    ("suore_friends_total", "friends", "counter", "Suore fatte amiche (.bef)."),
    ("suore_cilecca_ratio", "cilecca_pct", "gauge", "Colpi a vuoto o inceppati, 0-1."),
    ("suore_mediana_seconds", "mediana", "gauge", "Mediana dei tempi di reazione."),
    ("suore_media_seconds", "media", "gauge", "Media dei tempi di reazione."),
    ("suore_record_seconds", "record", "gauge", "Tempo di reazione migliore."),
]


def _lbl(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def metrics(out):
    """Il JSON di `build()` in formato testo Prometheus, per /suore/metrics
    (vjt e puppy, #sbiffo 13/10). Un file statico servito da nginx: niente
    porta nuova, si aggiorna quando si aggiorna il JSON."""
    righe = []
    for nome, chiave, tipo, aiuto in _PER_NICK:
        righe += [f"# HELP {nome} {aiuto}", f"# TYPE {nome} {tipo}"]
        for scope, s in sorted(out["scopes"].items()):
            rete, _, canale = scope.partition("/")
            for r in s["classifica"]:
                v = r.get(chiave)
                if v is None:
                    continue
                if chiave == "cilecca_pct":
                    v = v / 100
                righe.append(f'{nome}{{network="{_lbl(rete)}",channel="{_lbl(canale)}",'
                             f'nick="{_lbl(r["nick"])}"}} {v}')
    righe += ["# HELP suore_events_total Eventi nel registro.",
              "# TYPE suore_events_total counter",
              f"suore_events_total {out['eventi_totali']}"]
    if out.get("ultimo_evento") is not None:
        righe += ["# HELP suore_last_event_timestamp_seconds Ultimo evento, epoch.",
                  "# TYPE suore_last_event_timestamp_seconds gauge",
                  f"suore_last_event_timestamp_seconds {out['ultimo_evento']}"]
    return "\n".join(righe) + "\n"


def _atomic(path, text):
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def write(path, events, state, generated):
    """Scrive il JSON, e accanto `suore_metrics.prom`, in modo atomico.
    Non solleva: e' un accessorio."""
    if not path:
        return
    try:
        out = build(events, state, generated, storie(path, events))
        _atomic(path, json.dumps(out, ensure_ascii=False, indent=1,
                                 sort_keys=True) + "\n")
        _atomic(os.path.join(os.path.dirname(path), "suore_metrics.prom"),
                metrics(out))
    except (OSError, ValueError):
        pass


def main():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import suore_events

    if len(sys.argv) < 3:
        sys.exit("uso: suore_stats.py <suore_events.ndjson> <suore_state.json> "
                 "[out.json]")
    events = suore_events.read(sys.argv[1])
    with open(sys.argv[2], encoding="utf-8") as fh:
        state = json.load(fh)
    import time
    if len(sys.argv) > 3:
        # Stessa strada del gioco: storie dai log di `SUORE_NETS`, scrittura
        # atomica, metriche accanto.
        write(sys.argv[3], events, state, time.time())
        out = json.load(open(sys.argv[3], encoding="utf-8"))
        print(f"scritto {sys.argv[3]}: {out['eventi_totali']} eventi, "
              f"{len(out['scopes'])} scope", file=sys.stderr)
    else:
        print(json.dumps(build(events, state, time.time()), ensure_ascii=False,
                         indent=1, sort_keys=True))


if __name__ == "__main__":
    main()
