#!/usr/bin/env python3
"""Ricostruisce l'event log delle suore dagli annunci archiviati da grappa.

Il gioco, fino a oggi, teneva solo contatori aggregati: appena un colpo finisce
dentro `kills` l'ora, il canale e il tempo di reazione sono persi. Gli annunci
pero' sono stati detti in canale, e il DB di grappa li ha conservati **con il
timestamp**, che e' esattamente il pezzo che al `bot.log` manca (li' c'e' solo
`HH:MM:SS`, senza data).

Uso, in due tempi, perche' il DB sta su m42 e questo gira dove ti pare:

    ssh -A root@m42 "jexec grappa sqlite3 -readonly \\
        /home/grappa/grappa/runtime/grappa_prod.db" < tools/backfill_suore.sql \\
        > suore_raw.json

    python3 tools/backfill_suore.py suore_raw.json > suore_events.ndjson

Il DB e' quello **vivo di produzione**: si legge `-readonly` e basta.

Due avvertenze che vanno lette prima di credere al risultato:

  * lo storico e' quello che il client di vjt ha visto. I canali dove non e'
    entrato, o le finestre precedenti alla sua presenza, non ci sono — e non
    si vede la differenza fra "non e' successo" e "nessuno lo ha registrato".

  * `ts` e' l'ora dell'**annuncio**, cioe' spawn + reazione, non l'ora dello
    spawn. Il momento dello spawn si ricava sottraendo `reaction`, dove
    `reaction` c'e'.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import suore_events  # noqa: E402

# `network_id` come sta nel DB di grappa. Non e' derivabile: e' l'id della rete
# nell'installazione di vjt, va letto da `networks` se un giorno cambia.
NETWORKS = {1: "azzurra", 5: "ircnet"}


def convert(rows):
    events, skipped = [], 0
    for row in rows:
        parsed = suore_events.parse(row["body"])
        if not parsed:
            continue
        net = NETWORKS.get(row["network_id"])
        if not net:
            skipped += 1
            continue
        # Lo scope viene dal canale NOMINATO nell'annuncio, non da quello in cui
        # la riga e' stata letta: e' il gioco a dichiararlo, e cosi' una riga
        # rilanciata altrove non finisce nello scope sbagliato.
        events.append({
            "ts": round(row["server_time"] / 1000.0, 3),
            "scope": f"{net}/{parsed['chan']}",
            "nick": parsed["nick"],
            "verb": parsed["verb"],
            "golden": parsed["golden"],
            "reaction": parsed["reaction"],
            "backfilled": True,
        })
        if parsed["reaction"] is None:
            events[-1].pop("reaction")
    events.sort(key=lambda e: e["ts"])
    return events, skipped


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "-"
    fh = sys.stdin if src == "-" else open(src, encoding="utf-8")
    with fh:
        rows = json.load(fh)

    events, skipped = convert(rows)

    # Stesso annuncio archiviato due volte: puo' succedere se la query prende
    # piu' di un destinatario. Meglio accorgersene che contare doppio.
    seen, dupes = set(), 0
    for ev in events:
        key = (ev["ts"], ev["scope"], ev["nick"], ev["verb"])
        if key in seen:
            dupes += 1
        seen.add(key)

    for ev in events:
        print(json.dumps(ev, ensure_ascii=False))

    tally = {}
    for ev in events:
        slot = tally.setdefault(ev["scope"], {"bang": 0, "bef": 0, "golden": 0})
        slot[ev["verb"]] += 1
        slot["golden"] += int(ev["golden"])
    print(f"righe lette: {len(rows)}, eventi: {len(events)}, "
          f"reti ignote: {skipped}, doppioni: {dupes}", file=sys.stderr)
    for scope in sorted(tally):
        s = tally[scope]
        print(f"  {scope:22} bang={s['bang']} bef={s['bef']} golden={s['golden']}",
              file=sys.stderr)


if __name__ == "__main__":
    main()
