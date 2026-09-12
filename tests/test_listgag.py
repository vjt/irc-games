"""Invarianti del !list warez-gag dopo il restyle verosimile (vjt 2026-09-12).

Il gag deve "sembrare vero": nomi scene plausibili, taglie coerenti col
contenuto, e bestemmie presenti ma non prepotenti — 2 righe su 3, a volte
tutte e tre, mai zero (altrimenti la battuta non atterra) e mai il vecchio
tag CAPS in mezzo a ogni riga.

Poi il wiring `xdcc send #N` -> DCC del rickroll (vjt 2026-09-12): gli XDCC
leecher lo mandano in QUERY al bot, gli umani in canale; entrambi ricevono lo
stesso file (mp4), niente exe, il numero del pack e' decorativo.
"""
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import listgag  # noqa: E402

BEST = re.compile(r"dio|madonna|cristo|porco|porca|gesu", re.I)
PACK = re.compile(r"^#(\d)  (\d+)x \[([0-9.]+[MG])\] (\S+)$")
# la stessa regola di bot.py: il payload deve poter passare come nome DCC.
DCC_NAME = re.compile(r"[A-Za-z0-9._-]{1,80}\Z")

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


def test_invite_line():
    rng = random.Random(3)
    lines = listgag.build_dump(rng)
    req = next(l for l in lines if "xdcc send" in l.lower())
    assert "/msg" not in req, req  # niente /msg hard-coded: query e canale ok


def test_payload_is_a_valid_dcc_name():
    # se il nome non passa DCC_NAME_PAT, bot.py rifiuta ogni transfer.
    assert DCC_NAME.match(listgag.PAYLOAD_FILE), listgag.PAYLOAD_FILE


def test_privmsg_pat_captures_channel_and_query():
    chan = "< :mario!~m@host PRIVMSG #sbiffo :xdcc send #2"
    query = "< :mario!~m@host PRIVMSG EliteWarez :xdcc send #2"
    mc = listgag.PRIVMSG_PAT.search(chan)
    mq = listgag.PRIVMSG_PAT.search(query)
    assert mc and mc["target"] == "#sbiffo" and mc["nick"] == "mario"
    assert mq and mq["target"] == "EliteWarez" and mq["text"] == "xdcc send #2"


def test_xdcc_pattern():
    assert listgag.XDCC_PAT.match("xdcc send #3").group(1) == "3"
    assert listgag.XDCC_PAT.match("XDCC SEND 12 please").group(1) == "12"
    assert listgag.XDCC_PAT.match("  xdcc  send  #7  ").group(1) == "7"
    assert listgag.XDCC_PAT.match("xdcc list") is None
    assert listgag.XDCC_PAT.match("please xdcc send #1") is None  # anchored


def _recording():
    """Sostituisce say/send_dcc/sleep con registratori."""
    calls = {"say": [], "dcc": []}
    listgag.say = lambda chan, msg: calls["say"].append((chan, msg))
    listgag.send_dcc = lambda nick, fname: calls["dcc"].append((nick, fname))
    listgag.time.sleep = lambda _s: None
    return calls


def test_dispatch_channel_and_query_serve_the_payload():
    calls = _recording()
    rng = random.Random(4)
    lf = {}
    # xdcc send #N in un canale servito -> DCC del payload al richiedente
    listgag.dispatch("mario", "#sbiffo", "xdcc send #2", rng, lf)
    assert calls["dcc"] == [("mario", listgag.PAYLOAD_FILE)], calls
    # stessa cosa in query privata al bot (target = nick, non canale)
    calls["dcc"].clear()
    listgag.dispatch("mario", "EliteWarez", "xdcc send #1", rng, lf)
    assert calls["dcc"] == [("mario", listgag.PAYLOAD_FILE)], calls


def test_dispatch_list_dumps_only_in_channel():
    calls = _recording()
    rng = random.Random(5)
    lf = {}
    # !list in canale servito -> un dump (piu' righe), niente DCC
    listgag.dispatch("mario", "#sbiffo", "!list", rng, lf)
    assert len(calls["say"]) >= 5 and calls["dcc"] == [], calls
    # !list in query NON dumpa (solo xdcc funziona in query)
    calls["say"].clear()
    listgag.dispatch("mario", "EliteWarez", "!list", rng, lf)
    assert calls["say"] == [] and calls["dcc"] == [], calls


def test_dispatch_ignores_bots_and_unserved_channels():
    calls = _recording()
    rng = random.Random(6)
    lf = {}
    # un altro bot che spara xdcc: niente (o e' flood a due macchine)
    listgag.dispatch("cristobot", "#sbiffo", "xdcc send #1", rng, lf)
    listgag.dispatch("elitewarez", "EliteWarez", "xdcc send #1", rng, lf)
    # canale non servito: muto
    listgag.dispatch("mario", "#grappa", "xdcc send #1", rng, lf)
    assert calls == {"say": [], "dcc": []}, calls


if __name__ == "__main__":
    test_shape_and_sizes()
    print("ok: 3 pack a dump, nomi senza spazi, taglie dal pool del titolo")
    test_bestemmia_level()
    print("ok: 2-3 bestemmie a dump, mai zero, entrambi i regimi")
    test_invite_line()
    print("ok: riga invito senza /msg hard-coded")
    test_payload_is_a_valid_dcc_name()
    print("ok: PAYLOAD_FILE passa DCC_NAME_PAT di bot.py")
    test_privmsg_pat_captures_channel_and_query()
    print("ok: PRIVMSG_PAT cattura target canale e query")
    test_xdcc_pattern()
    print("ok: XDCC_PAT ancorato, # opzionale, junk in coda tollerato")
    test_dispatch_channel_and_query_serve_the_payload()
    print("ok: xdcc in canale e in query -> DCC del payload")
    test_dispatch_list_dumps_only_in_channel()
    print("ok: !list dumpa solo in canale, non in query")
    test_dispatch_ignores_bots_and_unserved_channels()
    print("ok: ignora bot e canali non serviti")
    print("\nTUTTO VERDE, porco dio")
