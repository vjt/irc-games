"""Invarianti del !list warez-gag dopo il restyle verosimile (vjt 2026-09-12).

Il gag deve "sembrare vero": nomi scene plausibili, taglie coerenti col
contenuto, e bestemmie presenti ma non prepotenti — 2 righe su 3, a volte
tutte e tre, mai zero (altrimenti la battuta non atterra) e mai il vecchio
tag CAPS in mezzo a ogni riga.
"""
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import listgag  # noqa: E402

BEST = re.compile(r"dio|madonna|cristo|porco|porca|gesu", re.I)
PACK = re.compile(r"^#(\d)  (\d+)x \[([0-9.]+[MG])\] (\S+)$")

# taglia -> contenuto: nessuna riga deve offrire un ISO a 4 MB o un .nfo a 8 G.
SIZE_BY_TITLE = {t: set(sizes) for t, sizes in listgag.CATALOG + listgag.LEGEND}


def test_shape_and_sizes():
    rng = random.Random(1)
    for _ in range(500):
        lines = listgag.build_dump(rng)
        packs = [PACK.match(l) for l in lines if l.startswith("#")]
        assert len(packs) == 3 and all(packs), lines
        for m in packs:
            name, size = m.group(4), m.group(3)
            assert " " not in name, name
            # il nome parte sempre da un titolo del catalogo, taglia dal suo pool
            title = next(t for t in SIZE_BY_TITLE if name.startswith(t))
            assert size in SIZE_BY_TITLE[title], (name, size)


def test_bestemmia_level():
    rng = random.Random(2)
    seen = set()
    for _ in range(500):
        lines = listgag.build_dump(rng)
        packs = [l for l in lines if l.startswith("#")]
        n = sum(1 for l in packs if BEST.search(l))
        assert 2 <= n <= 3, (n, packs)  # presente ma non prepotente, mai zero
        seen.add(n)
    assert seen == {2, 3}, seen  # entrambi i regimi escono


def test_request_in_channel():
    # vjt 2026-09-12: le richieste si vedono in canale, non in /msg privato.
    rng = random.Random(3)
    lines = listgag.build_dump(rng)
    req = next(l for l in lines if "xdcc send" in l.lower())
    assert "/msg" not in req, req
    assert "in canale" in req.lower() or "in channel" in req.lower(), req


if __name__ == "__main__":
    test_shape_and_sizes()
    print("ok: 3 pack a dump, nomi senza spazi, taglie dal pool del titolo")
    test_bestemmia_level()
    print("ok: 2-3 bestemmie a dump, mai zero, entrambi i regimi")
    test_request_in_channel()
    print("ok: richiesta in canale, niente /msg privato")
    print("\nTUTTO VERDE, porco dio")
