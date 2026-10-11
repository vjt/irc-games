#!/usr/bin/env python3
"""Rigenera sillabah_parole.txt da Morph-it! piu' le aggiunte a mano.

    python3 tools/sillabah_dizionario.py morph-it_048.txt > sillabah_parole.txt

Tre fonti, nell'ordine:

  1. Morph-it! 0.4.8, filtrato come prima: forme flesse, niente nomi propri,
     simboli, punteggiatura, abbreviazioni, niente forme verbali tronche.
  2. I pronomi attaccati: imperativo (`mangialo`, `dammelo`, `portatemelo`,
     2a singolare, 1a e 2a plurale), infinito e gerundio coi pronomi semplici
     (`mangiarlo`, `porci`, `lavandosi`, `andarsene`). Morph-it ne ha una
     parte sparsa, non la serie: qui si generano tutti. Il dizionario non sa
     se un verbo regge il complemento, quindi passa anche `dormilo`: meglio
     una parola strana accettata che una giusta rifiutata. Ma i doppi
     (`mangiartelo`) sull'infinito e sul gerundio no: triplicano il
     dizionario e la riga di fine manche si riempie di mostri.
  3. sillabah_aggiunte.txt: le straniere d'uso comune che Morph-it (2009) non
     ha. Il regolamento originale le ammette («Winchester, western, weekend»).

Taglio a LIMITE_MAX lettere, contate come caratteri (non byte)."""

import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(HERE))
from sillabah import LIMITE_MAX  # noqa: E402

AGGIUNTE = HERE / "sillabah_aggiunte.txt"
PAROLA = re.compile(r"^[a-zàáèéìíòóùú]+$")
VERBO = re.compile(r"^(VER|AUX|MOD|CAU|ASP)")
TRONCA = re.compile(r"[b-df-hj-np-tv-z]$")
IMPERATIVO = re.compile(r"^(VER|AUX|MOD|CAU|ASP):impr\+pres\+(2\+s|1\+p|2\+p)$")
INFINITO = re.compile(r"^(VER|AUX|MOD|CAU|ASP):inf\+pres$")
GERUNDIO = re.compile(r"^(VER|AUX|MOD|CAU|ASP):ger\+pres$")

SEMPLICI = ["mi", "ti", "ci", "vi", "lo", "la", "li", "le", "ne", "gli"]
DOPPI = [a + b for a in ("me", "te", "ce", "ve", "glie")
         for b in ("lo", "la", "li", "le", "ne")]
CLITICI = SEMPLICI + DOPPI
# Infinito e gerundio: solo i semplici, il riflessivo e il suo -sene.
SEMPLICI_INF = SEMPLICI + ["si", "sene"]
# La 1a plurale prende solo i semplici, senza la 1a singolare (portiamomi),
# piu' -cene (andiamocene): i doppi (diseredamomeli) sono quasi tutti mostri.
NOI = [c for c in SEMPLICI if c != "mi"] + ["cene"]
# I monosillabi raddoppiano la consonante del pronome (dammi, dillo, fallo,
# stacci, vattene), tranne gli e glie- (dagli, diglielo).
MONOSILLABI = ["da", "di", "fa", "sta", "va"]

INTESTAZIONE = f"""\
# sillabah_parole.txt — il dizionario di Sillabah. Dati, non codice.
# GENERATO da tools/sillabah_dizionario.py: non si edita a mano, si rigenera.
#
# Derivato da Morph-it! 0.4.8 (23 febbraio 2009), lessico morfologico libero
# dell'italiano di Marco Baroni ed Eros Zanchetta, SSLMIT Universita' di Bologna.
#   pagina:   https://docs.sslmit.unibo.it/doku.php?id=resources:morph-it
#   archivio: https://docs.sslmit.unibo.it/lib/exe/fetch.php?media=resources:morph-it.tgz
#             sha256 b137343dc095e038ebfaef7c743185e85ec35f5322a1b8629b600e6e898c7024
#   file:     current_version/morph-it_048.txt (ISO-8859-1)
#
# Licenza dell'originale: doppia, Creative Commons Attribution-ShareAlike 2.0
# e GNU LGPL. Questa lista e' un'opera derivata e si redistribuisce sotto
# CC BY-SA 2.0 (https://creativecommons.org/licenses/by-sa/2.0/), con
# attribuzione a Morph-it! (Baroni, Zanchetta) come sopra.
#
# Cosa contiene: le forme flesse di Morph-it (niente nomi propri, simboli,
# abbreviazioni, forme verbali tronche), imperativo, infinito e gerundio con i
# pronomi attaccati generati dallo script, le straniere di sillabah_aggiunte.txt.
# Solo minuscole senza apostrofi, al piu' {LIMITE_MAX} lettere (LIMITE_MAX).
#
# Una parola per riga. Le righe che iniziano con '#' sono commenti.
"""


def morphit(path):
    forme, imperativi, infiniti, gerundi = set(), set(), set(), set()
    with open(path, encoding="latin-1") as fh:
        for riga in fh:
            campi = riga.rstrip("\n").split("\t")
            if len(campi) < 3:
                continue
            forma, _, tag = campi[:3]
            if re.match(r"^(NPR|SYM|PON|SENT|ABL)", tag):
                continue
            if VERBO.match(tag) and TRONCA.search(forma):
                continue
            if not PAROLA.match(forma):
                continue
            forme.add(forma)
            if IMPERATIVO.match(tag):
                imperativi.add(forma)
            elif INFINITO.match(tag):
                infiniti.add(forma)
            elif GERUNDIO.match(tag):
                gerundi.add(forma)
    return forme, imperativi, infiniti, gerundi


def con_pronomi(imperativi, infiniti, gerundi):
    out = set()
    # L'infinito perde la -e (mangiar-lo), quelli in -rre anche una r (por-lo).
    for inf in infiniti:
        if inf.endswith("e"):
            radice = inf[:-2] if inf.endswith("rre") else inf[:-1]
            out.update(radice + c for c in SEMPLICI_INF)
    for ger in gerundi:
        out.update(ger + c for c in SEMPLICI_INF)
    for imp in imperativi:
        if imp in MONOSILLABI:
            continue
        out.update(imp + c for c in (NOI if imp.endswith("amo") else CLITICI))
    for m in MONOSILLABI:
        for c in CLITICI:
            out.add(m + c if c.startswith("gl") else m + c[0] + c)
    return out


def aggiunte(path=AGGIUNTE):
    out = set()
    for riga in path.read_text(encoding="utf-8").splitlines():
        riga = riga.strip().lower()
        if riga and not riga.startswith("#"):
            if not PAROLA.match(riga):
                raise SystemExit(f"aggiunta non valida: {riga!r}")
            out.add(riga)
    return out


def main(argv):
    if len(argv) != 2:
        raise SystemExit(__doc__)
    forme, imperativi, infiniti, gerundi = morphit(argv[1])
    tutte = forme | con_pronomi(imperativi, infiniti, gerundi) | aggiunte()
    sys.stdout.write(INTESTAZIONE)
    for f in sorted(f for f in tutte if len(f) <= LIMITE_MAX):
        sys.stdout.write(f + "\n")


if __name__ == "__main__":
    main(sys.argv)
