"""Invarianti del !list warez-gag dopo il restyle verosimile (vjt 2026-09-12).

Il gag deve "sembrare vero": nomi scene plausibili, taglie coerenti col
contenuto, e bestemmie presenti ma non prepotenti — 2 righe su 3, a volte
tutte e tre, mai zero (altrimenti la battuta non atterra) e mai il vecchio
tag CAPS in mezzo a ogni riga.

Poi il wiring `xdcc send #N` in canale -> DCC del payload rickroll (vjt
2026-09-12: "vogliamo vederli in canale"): #N mappa sull'ultimo dump del
canale, la categoria del pack sceglie il file, un #N inesistente prende una
riga burbera e nessuna DCC.
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
SIZE_BY_TITLE = {t: set(sizes) for t, sizes, _cat in listgag.CATALOG + listgag.LEGEND}


def test_shape_and_sizes():
    rng = random.Random(1)
    for _ in range(500):
        lines, cats = listgag.build_dump(rng)
        packs = [PACK.match(l) for l in lines if l.startswith("#")]
        assert len(packs) == 3 and all(packs), lines
        assert len(cats) == 3 and all(c in ("film", "soft") for c in cats), cats
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
        lines, _cats = listgag.build_dump(rng)
        packs = [l for l in lines if l.startswith("#")]
        n = sum(1 for l in packs if BEST.search(l))
        assert 2 <= n <= 3, (n, packs)  # presente ma non prepotente, mai zero
        seen.add(n)
    assert seen == {2, 3}, seen  # entrambi i regimi escono


def test_request_in_channel():
    # vjt 2026-09-12: le richieste si vedono in canale, non in /msg privato.
    rng = random.Random(3)
    lines, _cats = listgag.build_dump(rng)
    req = next(l for l in lines if "xdcc send" in l.lower())
    assert "/msg" not in req, req
    assert "in canale" in req.lower() or "in channel" in req.lower(), req


def test_every_category_has_a_payload():
    # ogni categoria che il catalogo puo' emettere deve avere un file mappato,
    # altrimenti un `xdcc send #N` legittimo esplode con KeyError.
    cats = {cat for _t, _s, cat in listgag.CATALOG + listgag.LEGEND}
    assert cats <= set(listgag.PAYLOAD), (cats, listgag.PAYLOAD)


def _recording(monkey_target):
    """Sostituisce say/send_dcc con un registratore, azzera i gap di riga."""
    calls = {"say": [], "dcc": []}
    listgag.say = lambda chan, msg: calls["say"].append((chan, msg))
    listgag.send_dcc = lambda nick, fname: calls["dcc"].append((nick, fname))
    listgag.time.sleep = lambda _s: None
    return calls


def test_xdcc_maps_pack_to_payload():
    calls = _recording(listgag)
    rng = random.Random(4)
    last_fired, last_dump = {}, {}
    # un !list scrive last_dump[chan] = cats
    listgag.handle("mario", "#sbiffo", rng, last_fired, last_dump)
    cats = last_dump["#sbiffo"]
    assert len(cats) == 3
    # ogni #N valido serve il payload della categoria del pack, a nessun altro
    for n in (1, 2, 3):
        calls["dcc"].clear()
        listgag.handle_xdcc("mario", "#sbiffo", n, rng, last_dump)
        assert calls["dcc"] == [("mario", listgag.PAYLOAD[cats[n - 1]])], (n, calls)


def test_xdcc_out_of_range_and_cold():
    calls = _recording(listgag)
    rng = random.Random(5)
    # nessun dump ancora: niente DCC, una riga burbera che nomina #N
    listgag.handle_xdcc("gino", "#sbiffo", 2, rng, {})
    assert calls["dcc"] == [], calls
    assert len(calls["say"]) == 1 and calls["say"][0][0] == "#sbiffo", calls["say"]
    # dump presente ma #N fuori dai 3 pack: idem, niente DCC
    calls["say"].clear()
    last_dump = {"#sbiffo": ["film", "soft", "film"]}
    for bad in (0, 4, 99):
        calls["dcc"].clear()
        listgag.handle_xdcc("gino", "#sbiffo", bad, rng, last_dump)
        assert calls["dcc"] == [], (bad, calls)


def test_xdcc_pattern():
    m = listgag.XDCC_PAT.match("xdcc send #3")
    assert m and m.group(1) == "3"
    assert listgag.XDCC_PAT.match("XDCC SEND 12 please").group(1) == "12"
    assert listgag.XDCC_PAT.match("  xdcc  send  #7  ").group(1) == "7"
    assert listgag.XDCC_PAT.match("xdcc list") is None
    assert listgag.XDCC_PAT.match("please xdcc send #1") is None  # anchored


if __name__ == "__main__":
    test_shape_and_sizes()
    print("ok: 3 pack a dump, nomi senza spazi, taglie dal pool, cats film/soft")
    test_bestemmia_level()
    print("ok: 2-3 bestemmie a dump, mai zero, entrambi i regimi")
    test_request_in_channel()
    print("ok: richiesta in canale, niente /msg privato")
    test_every_category_has_a_payload()
    print("ok: ogni categoria ha un payload mappato")
    test_xdcc_maps_pack_to_payload()
    print("ok: xdcc send #N -> DCC del payload della categoria del pack")
    test_xdcc_out_of_range_and_cold()
    print("ok: #N fuori range o senza dump -> riga burbera, nessuna DCC")
    test_xdcc_pattern()
    print("ok: XDCC_PAT ancorato, # opzionale, junk in coda tollerato")
    print("\nTUTTO VERDE, porco dio")
