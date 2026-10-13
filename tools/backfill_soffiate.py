#!/usr/bin/env python3
"""Ricostruisce `soffiate` nello stato delle suore dalle righe del Convento.

Le soffiate (puppy, #sbiffo 13/10) all'inizio si contavano senza soglia e senza
tempi: un int per coppia, che conta anche chi e' arrivato un minuto dopo. Ora
valgono solo entro `suore.SOFFIATA_WINDOW` secondi e tengono la somma dei gap.
Il pregresso si rifa' dalle righe che il gioco ha gia' detto in canale:

    katia: Non c'e' nessuna suora! A cosa stai sparando? L'hai mancata per
    2.2 secondi: se l'e' presa peluche. La prossima tra due ore.

vittima = il nick prima dei primi due punti, chi = dopo «se l'e' presa», gap =
«per X secondi». `human_time()` scrive i decimi solo sotto i 5 secondi, quindi
la soglia dei 3 si vede sempre; sopra si scarta tutto.

Uso (a secco di default, stampa il dict risultante):

    python3 tools/backfill_soffiate.py STATE.json azzurra=bot.stdout.log \\
        ircnet=bot.ircnet.stdout.log [--write]

Ogni log va col nome della SUA rete davanti, quello di `SUORE_NETS`: la riga
porta solo il canale, e lo scope e' `rete/#canale` come `Net.scope()`. Due
formati di riga:

  * stream del bridge:  `MSG ... FROM=Convento ... TO=#sbiffo BODY=...`
    (solo da `--from`, default Convento: un umano che cita la riga non conta)
  * bot.log grezzo:     `> [suore] PRIVMSG #sbiffo :...` (sidecar sul bot vecchio)

Attenzione: passare due log che hanno visto la STESSA riga la conta due volte.
Le righe hanno solo HH:MM, non si possono deduplicare: un log per canale.

`--write` sostituisce `soffiate` per INTERO (non somma) dopo aver copiato lo
stato in `STATE.json.bak`. Con il daemon acceso il prossimo `save()` lo
sovrascrive: si fa a sidecar fermo.
"""

import argparse
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import suore  # noqa: E402

STREAM = re.compile(r"\bFROM=(?P<from>\S+)\s.*?\bTO=(?P<chan>#\S+)\s+BODY=(?P<body>.*)$")
RAW = re.compile(r">\s+\[suore\]\s+PRIVMSG\s+(?P<chan>#\S+)\s+:(?P<body>.*)$")
MANCATA = re.compile(r"^(?P<vittima>[^:\s]+): .*L'hai mancata per "
                     r"(?P<gap>\d+(?:\.\d+)?) secondi: se l'e' presa (?P<chi>[^\s.]+)\.")
CTRL = re.compile(r"\x03\d{1,2}(?:,\d{1,2})?|[\x02\x03\x0f\x16\x1d\x1f]")


def parse(line, frm):
    """(chan, vittima, chi, gap) oppure None."""
    m = STREAM.search(line)
    if m and m["from"].lower() != frm.lower():
        return None
    m = m or RAW.search(line)
    if not m:
        return None
    hit = MANCATA.search(CTRL.sub("", m["body"]))
    if not hit:
        return None
    return m["chan"].lower(), hit["vittima"], hit["chi"], float(hit["gap"])


def rebuild(sources, frm):
    """sources = [(rete, path)]. Torna (soffiate, conteggi)."""
    soffiate = {}
    seen = {"righe": 0, "fuori_soglia": 0, "proprie": 0, "contate": 0}
    for net, path in sources:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                hit = parse(line, frm)
                if not hit:
                    continue
                chan, vittima, chi, gap = hit
                seen["righe"] += 1
                if suore.canon(chi) == suore.canon(vittima):
                    seen["proprie"] += 1
                    continue
                if gap > suore.SOFFIATA_WINDOW:
                    seen["fuori_soglia"] += 1
                    continue
                scope = suore.Net(net, path, None, {}).scope(chan)
                c = soffiate.setdefault(scope, {}).setdefault(suore.canon(chi), {}) \
                    .setdefault(suore.canon(vittima), {"n": 0, "s": 0.0})
                c["n"] += 1
                c["s"] = round(c["s"] + gap, 3)
                seen["contate"] += 1
    return soffiate, seen


def source(spec):
    net, sep, path = spec.partition("=")
    if not sep or not net or not path:
        raise argparse.ArgumentTypeError(f"serve rete=log, non {spec!r}")
    return net, path


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("state", help="suore_state.json")
    ap.add_argument("logs", nargs="+", type=source, metavar="rete=log")
    ap.add_argument("--from", dest="frm", default="Convento",
                    help="nick del bot nelle righe dello stream (default Convento)")
    ap.add_argument("--write", action="store_true",
                    help="sostituisci soffiate nello stato (con .bak)")
    args = ap.parse_args()

    soffiate, seen = rebuild(args.logs, args.frm)
    print(json.dumps(soffiate, ensure_ascii=False, indent=1, sort_keys=True))
    print(f"righe «mancata»: {seen['righe']}, contate: {seen['contate']}, "
          f"oltre {suore.SOFFIATA_WINDOW:g}s: {seen['fuori_soglia']}, "
          f"proprie: {seen['proprie']}", file=sys.stderr)
    if not args.write:
        print("a secco: niente scritto (--write per applicare)", file=sys.stderr)
        return

    with open(args.state, encoding="utf-8") as fh:
        data = json.load(fh)
    shutil.copy2(args.state, args.state + ".bak")
    data["soffiate"] = soffiate
    tmp = args.state + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, args.state)
    print(f"scritto {args.state} (copia in {args.state}.bak)", file=sys.stderr)


if __name__ == "__main__":
    main()
