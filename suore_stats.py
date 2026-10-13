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
        # Non e' un errore da correggere: il log e lo stato hanno storie
        # diverse (azzeramenti a mano, annunci mai archiviati). Dichiararlo.
        "discrepanza": bang_log - kills_stato,
    }


def build(events, state, generated):
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
        "dorate": [{"ts": e["ts"], "scope": e["scope"], "nick": e["nick"],
                    "verbo": e["verb"]} for e in events if e.get("golden")],
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
        out = build(events, state, generated)
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
    out = build(events, state, time.time())
    blob = json.dumps(out, ensure_ascii=False, indent=1, sort_keys=True)
    if len(sys.argv) > 3:
        with open(sys.argv[3], "w", encoding="utf-8") as fh:
            fh.write(blob + "\n")
        print(f"scritto {sys.argv[3]}: {out['eventi_totali']} eventi, "
              f"{len(out['scopes'])} scope", file=sys.stderr)
    else:
        print(blob)


if __name__ == "__main__":
    main()
