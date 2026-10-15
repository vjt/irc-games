#!/usr/bin/env python3
"""Converte i mazzi LaTeX di ilario/Cards-Against-Humanity-ITA in carte per
cah_carte.json.

Ogni carta e' un `\\carta{...}`; nel file le bianche (RISPOSTE) vengono prima,
le nere (DOMANDE) dopo l'intestazione «DOMANDE». Il buco `\\puntini` (o
`\\_\\_\\_`) diventa `___`. Le carte vuote si scartano, i doppioni pure (anche
fra un file e l'altro: vince il primo).

Uso (stampa il json delle carte, non tocca cah_carte.json):

    python3 tools/cah_tex2json.py ita=cah-ita-originale-federico.tex \\
        cah42=cah-ita-espansione-cah42proj.tex
"""
import json
import re
import sys

BLANK = "___"


def strip_comments(tex):
    # `%` apre un commento se non e' scappato (`\\%`).
    return "\n".join(re.sub(r"(?<!\\)%.*$", "", l) for l in tex.splitlines())


def cards(tex):
    """[(tipo, testo)] nell'ordine del file; tipo 'b' bianca, 'n' nera."""
    tex = strip_comments(tex)
    split = tex.find("DOMANDE")
    out = []
    for m in re.finditer(r"\\carta\{", tex):
        i, depth = m.end(), 1
        j = i
        while depth:
            c = tex[j]
            if c == "\\":
                j += 2
                continue
            depth += {"{": 1, "}": -1}.get(c, 0)
            j += 1
        out.append(("n" if 0 <= split < m.start() else "b", clean(tex[i:j - 1])))
    return out


def clean(s):
    s = re.sub(r"\\puntini\b\s*|(?:\\_){3,}\s*", f" {BLANK} ", s)
    s = s.replace("\\-", "").replace("\\%", "%").replace("\\&", "&")
    s = s.replace("``", "«").replace("''", "»")
    s = re.sub(r"\\\\", " ", s)
    s = re.sub(r"\\[a-zA-Z]+\*?", " ", s)          # comandi rimasti: via
    s = s.replace("{", "").replace("}", "").replace("~", " ")
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(rf"{BLANK} ([.,:;?!])", rf"{BLANK}\1", s)
    # Il testo di Federico viene da un OCR (vedi il README del repo): la `l`
    # letta come `I`. In italiano una `I` maiuscola dentro la parola o
    # davanti all'apostrofo non esiste; il resto e' un elenco a mano.
    s = re.sub(r"(?<=[a-z])I(?=[a-z])", "l", s)
    s = re.sub(r"\bI'", "l'", s)
    for bad, good in OCR.items():
        s = s.replace(bad, good)
    return s


OCR = {" Ii ": " li ", "Iobotomia": "lobotomia"}


def main():
    seen, nere, bianche, stats = set(), [], [], {}
    for arg in sys.argv[1:]:
        pack, path = arg.split("=", 1)
        st = stats[pack] = {"nere": 0, "bianche": 0, "vuote": 0, "doppie": 0}
        for kind, text in cards(open(path, encoding="utf-8").read()):
            if not text or text == BLANK:
                st["vuote"] += 1
                continue
            key = (kind, re.sub(r"\W+", " ", text.casefold()).strip())
            if key in seen:
                st["doppie"] += 1
                continue
            seen.add(key)
            if kind == "n":
                st["nere"] += 1
                nere.append({"testo": text, "pack": pack,
                             "pick": max(1, text.count(BLANK))})
            else:
                st["bianche"] += 1
                bianche.append({"testo": text, "pack": pack})
    print(json.dumps(stats), file=sys.stderr)
    json.dump({"nere": nere, "bianche": bianche}, sys.stdout, ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
