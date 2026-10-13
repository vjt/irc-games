#!/usr/bin/env python3
"""Ricostruisce `dorate_rubate` e `ultime_vite` nello stato dalle righe del Convento.

La rivalita' dorata (puppy, #sbiffo 13/10) si conta da quando la suora tiene
in `nun["hits"]` chi l'ha colpita senza chiuderla. Il pregresso si rifa' dalle
righe che il gioco ha gia' detto in canale, in ordine:

    vjt: Il colpo l'ha presa in pieno, ma la suora dorata regge ancora: ...
    peluche: Ha quasi ceduto, ma la suora dorata regge ancora: ...
    peluche ha steso la SUORA DORATA in 1m 03s! ...

I colpi si accumulano per canale finche' una riga di presa non chiude la
dorata; la chiusura passa da `suore.conta_dorata()`, la stessa del gioco, cosi'
le regole (un furto per vittima, non per colpo; chi la chiude sui colpi propri
non ruba) stanno in un posto solo. Una comparsa nuova azzera i colpi rimasti
appesi: una dorata mai chiusa (stato perso, sidecar rifatto) non regala furti
alla successiva.

Uso (a secco di default, stampa il dict risultante):

    python3 tools/backfill_dorate.py STATE.json azzurra=bot.azzurra.log \\
        ircnet=bot.log [--write]

Ogni log va col nome della SUA rete davanti, quello di `SUORE_NETS`, come in
tools/backfill_soffiate.py; stessi due formati di riga (stream del bridge da
`--from`, o `> [suore] PRIVMSG` grezzo). I log della stessa rete vanno in
ordine cronologico: una dorata comparsa in uno e chiusa nel successivo si
ricuce solo cosi'. Le righe hanno solo l'ora: una riga identica (rete, canale,
ora, testo) gia' vista in un ALTRO log si scarta come doppione.

`--write` sostituisce `dorate_rubate` e `ultime_vite` per INTERO (non somma)
dopo aver copiato lo stato in `STATE.json.bak`. Con il daemon acceso il
prossimo `save()` lo sovrascrive: si fa a sidecar fermo.
"""

import argparse
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import suore  # noqa: E402

STREAM = re.compile(r"^(?P<ts>\S+)\s.*?\bFROM=(?P<from>\S+)\s.*?\bTO=(?P<chan>#\S+)\s+BODY=(?P<body>.*)$")
RAW = re.compile(r"^(?P<ts>\S+)\s+>\s+\[suore\]\s+PRIVMSG\s+(?P<chan>#\S+)\s+:(?P<body>.*)$")
CTRL = re.compile(r"\x03\d{1,2}(?:,\d{1,2})?|[\x02\x03\x0f\x16\x1d\x1f]")
COLPO = re.compile(r"^(?P<nick>[^:\s]+): (?:Il colpo l'ha presa in pieno|Ha quasi ceduto), "
                   r"ma la suora dorata regge ancora")
# Tre verbi: «si e' fatto amico» e' la riga del .bef prima del 13/10.
PRESA = re.compile(r"^(?P<nick>\S+) (?:ha steso|si e' fatto amico|ha fatto amicizia con) "
                   r"la SUORA DORATA in ")
# Le prese normali e la comparsa (`\_☨< AVE MARIA!`) chiudono comunque il giro.
CHIUDE = re.compile(r"^\S+ hai fatto (?:secca|amicizia con) una suora in |\\_\S< ")


def parse(line, frm):
    """(ora, chan, body pulito) oppure None."""
    m = STREAM.search(line)
    if m and m["from"].lower() != frm.lower():
        return None
    m = m or RAW.search(line)
    if not m:
        return None
    body = CTRL.sub("", m["body"]).replace(suore.ZWSP, "").rstrip("\r\n")
    return m["ts"], m["chan"].lower(), body


def rebuild(sources, frm):
    """sources = [(rete, path)]. Torna (stato parziale, conteggi)."""
    data = {}
    appesi = {}          # scope -> colpi sulla dorata ancora in giro
    visti = {}           # (rete, chan, ora, body) -> path che l'ha vista
    seen = {"colpi": 0, "dorate": 0, "colpite_da_altri": 0, "furti": 0,
            "orfani": 0, "doppioni": 0}
    for net, path in sources:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                hit = parse(line, frm)
                if not hit:
                    continue
                ts, chan, body = hit
                key = (net, chan, ts, body)
                if visti.setdefault(key, path) != path:
                    seen["doppioni"] += 1
                    continue
                scope = suore.Net(net, path, None, {}).scope(chan)
                if m := COLPO.search(body):
                    appesi.setdefault(scope, []).append({"nick": suore.canon(m["nick"])})
                    seen["colpi"] += 1
                elif m := PRESA.search(body):
                    colpi = appesi.pop(scope, [])
                    seen["dorate"] += 1
                    suore.conta_dorata(data, scope, m["nick"], colpi)
                    vittime = {h["nick"] for h in colpi} - {suore.canon(m["nick"])}
                    if vittime:
                        seen["colpite_da_altri"] += 1
                        seen["furti"] += len(vittime)
                elif CHIUDE.search(body) and appesi.get(scope):
                    seen["orfani"] += len(appesi.pop(scope))
    seen["orfani"] += sum(len(v) for v in appesi.values())
    return data, seen


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
                    help="sostituisci dorate_rubate e ultime_vite nello stato (con .bak)")
    args = ap.parse_args()

    nuovo, seen = rebuild(args.logs, args.frm)
    nuovo.setdefault("dorate_rubate", {})
    nuovo.setdefault("ultime_vite", {})
    print(json.dumps(nuovo, ensure_ascii=False, indent=1, sort_keys=True))
    print(f"dorate chiuse: {seen['dorate']}, chiuse sui colpi altrui: "
          f"{seen['colpite_da_altri']}, furti (coppie): {seen['furti']}, "
          f"colpi letti: {seen['colpi']}, colpi orfani: {seen['orfani']}, "
          f"righe doppie scartate: {seen['doppioni']}", file=sys.stderr)
    if not args.write:
        print("a secco: niente scritto (--write per applicare)", file=sys.stderr)
        return

    with open(args.state, encoding="utf-8") as fh:
        data = json.load(fh)
    shutil.copy2(args.state, args.state + ".bak")
    data["dorate_rubate"] = nuovo["dorate_rubate"]
    data["ultime_vite"] = nuovo["ultime_vite"]
    tmp = args.state + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1, sort_keys=True)
    os.replace(tmp, args.state)
    print(f"scritto {args.state} (copia in {args.state}.bak)", file=sys.stderr)


if __name__ == "__main__":
    main()
