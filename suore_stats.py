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


def _scope_stats(events, scores, taglines=None):
    """Statistiche di uno scope. `events` gia' filtrati, `scores` dallo stato."""
    per_day = collections.defaultdict(lambda: {"bang": 0, "bef": 0})
    per_hour = [0] * 24
    per_nick_time = collections.defaultdict(list)
    # Il record personale si misura sui soli colpi, come quello di scope: il
    # `.bef` e' un'altra partita e ci finirebbe dentro con tempi non comparabili.
    per_nick_bang = collections.defaultdict(list)
    best = None

    for ev in sorted(events, key=lambda e: e["ts"]):
        per_day[_day(ev["ts"])][ev["verb"]] += 1
        per_hour[_hour(ev["ts"])] += 1
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
            # L'ultima figuraccia (fucile inceppato, suora che tira dritto),
            # chiave canonica come `scores`.
            "tagline": (taglines or {}).get(canon),
            # Media E mediana: la suora resta li' finche' qualcuno non la vede,
            # quindi un colpo dato dopo venti minuti trascina la media in cielo
            # (misurato: medie da 1255 s con reazioni tipiche da 20). La mediana
            # dice come si gioca davvero, la media dice quanto si e' distratti.
            "media": round(sum(tempi) / len(tempi), 2) if tempi else None,
            "mediana": round(_mediana(tempi), 2) if tempi else None,
            "record": round(min(colpi), 2) if colpi else None,
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
            (state.get("taglines") or {}).get(scope))
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
    }


def write(path, events, state, generated):
    """Scrive il JSON in modo atomico. Non solleva: e' un accessorio."""
    if not path:
        return
    try:
        blob = json.dumps(build(events, state, generated),
                          ensure_ascii=False, indent=1, sort_keys=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(blob + "\n")
        os.replace(tmp, path)
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
