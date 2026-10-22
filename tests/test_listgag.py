"""Invarianti del !list warez-gag dopo il restyle verosimile (vjt 2026-09-12).

Il gag deve "sembrare vero": nomi scene plausibili, taglie coerenti col
contenuto, e bestemmie presenti ma non prepotenti — 2 righe su 3, a volte
tutte e tre, mai zero (altrimenti la battuta non atterra) e mai il vecchio
tag CAPS in mezzo a ogni riga.

Poi il wiring `xdcc send #N` -> DCC del rickroll (vjt 2026-09-12): gli XDCC
leecher lo mandano in QUERY al bot, gli umani in canale; entrambi ricevono lo
stesso file (mp4), niente exe. Il numero del pack ora CONTA: in canale l'offerta
porta il titolo del pack #N dell'ultimo dump (stato per-canale); in query, dove
non c'e' dump, un titolo a caso. E il catalogo e' solo film recenti, no software.
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


def test_catalog_is_films_only():
    # vjt 2026-09-12: solo film, no software/giochi. Niente ISO/exe/ROM/keygen
    # nei titoli, e ogni titolo porta un marcatore da film (anno + risoluzione).
    bad = re.compile(r"\b(ISO|OEM|Retail|NoCD|NoSteam|keygen|crack)\b", re.I)
    for title, _sizes in listgag.CATALOG:
        assert not bad.search(title), title
        assert re.search(r"\b(1080p|2160p|720p)\b", title), title
    assert listgag.LEGEND == [], listgag.LEGEND


def test_shape_and_sizes():
    rng = random.Random(1)
    for _ in range(500):
        lines, names = listgag.build_dump(rng)
        packs = [PACK.match(l) for l in lines if l.startswith("#")]
        assert len(packs) == 3 and all(packs), lines
        # i nomi restituiti sono esattamente quelli mostrati, in ordine
        assert [m.group(4) for m in packs] == names, (packs, names)
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
        lines, _names = listgag.build_dump(rng)
        packs = [l for l in lines if l.startswith("#")]
        n = sum(1 for l in packs if BEST.search(l))
        assert 2 <= n <= 3, (n, packs)  # presente ma non prepotente, mai zero
        seen.add(n)
    assert seen == {2, 3}, seen  # entrambi i regimi escono


def test_invite_line():
    rng = random.Random(3)
    lines, _names = listgag.build_dump(rng)
    req = next(l for l in lines if "xdcc send" in l.lower())
    assert "/msg" not in req, req  # niente /msg hard-coded: query e canale ok


def test_find_hint_closes_the_dump():
    rng = random.Random(4)
    lines, _names = listgag.build_dump(rng)
    assert "@find <parola>" in lines[-1], lines[-1]
    # l'hint deve combaciare col trigger vero
    assert listgag.FIND_PAT.match("@find il robot selvaggio")


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
    # `send` opzionale (vjt 2026-09-13): chi scrive `xdcc 1` ha abboccato uguale
    assert listgag.XDCC_PAT.match("xdcc 1").group(1) == "1"
    assert listgag.XDCC_PAT.match("xdcc #1").group(1) == "1"
    assert listgag.XDCC_PAT.match("XDCC  #42  grazie").group(1) == "42"


def test_list_pattern():
    assert listgag.LIST_PAT.match("!list")
    assert listgag.LIST_PAT.match("! List?!")
    # parole dopo ignorate (Sythos 2026-10-22)
    assert listgag.LIST_PAT.match("!list così Sythos è contento")
    assert listgag.LIST_PAT.match("!listone") is None
    assert listgag.LIST_PAT.match("whitelist !list") is None


# l'offer name: stessa regola di bot.py DCC_OFFER_NAME_PAT, limite 120.
DCC_OFFER = re.compile(r"[A-Za-z0-9._-]{1,120}\Z")


def _recording():
    """Sostituisce say/send_dcc/sleep con registratori."""
    calls = {"say": [], "dcc": []}
    listgag.say = lambda chan, msg: calls["say"].append((chan, msg))
    listgag.send_dcc = lambda nick, fname, offer=None: calls["dcc"].append(
        (nick, fname, offer))
    listgag.time.sleep = lambda _s: None
    return calls


def test_dispatch_channel_offers_under_pack_title():
    calls = _recording()
    rng = random.Random(4)
    lf, ld = {}, {}
    # prima un !list popola lo stato per-canale
    listgag.dispatch("mario", "#sbiffo", "!list", rng, lf, ld)
    titles = ld["#sbiffo"]
    assert len(titles) == 3, ld
    # xdcc send #2 -> DCC del payload, offerto col TITOLO del pack #2 + .mp4
    listgag.dispatch("mario", "#sbiffo", "xdcc send #2", rng, lf, ld)
    assert len(calls["dcc"]) == 1, calls
    nick, fname, offer = calls["dcc"][0]
    assert nick == "mario" and fname == listgag.PAYLOAD_FILE, calls
    assert offer == f"{titles[1]}.mp4", (offer, titles)
    assert DCC_OFFER.match(offer), offer


def test_dispatch_channel_out_of_range_no_dcc():
    calls = _recording()
    rng = random.Random(41)
    lf, ld = {}, {}
    listgag.dispatch("mario", "#sbiffo", "!list", rng, lf, ld)
    calls["say"].clear()
    # #9 non esiste: una riga burbera, nessuna DCC
    listgag.dispatch("mario", "#sbiffo", "xdcc send #9", rng, lf, ld)
    assert calls["dcc"] == [], calls
    assert len(calls["say"]) == 1 and "#9" in calls["say"][0][1], calls


def test_dispatch_query_offers_random_title():
    calls = _recording()
    rng = random.Random(42)
    lf, ld = {}, {}
    # query privata al bot: nessun dump, titolo a caso ma offerta valida
    listgag.dispatch("mario", "EliteWarez", "xdcc send #1", rng, lf, ld)
    assert len(calls["dcc"]) == 1, calls
    nick, fname, offer = calls["dcc"][0]
    assert nick == "mario" and fname == listgag.PAYLOAD_FILE, calls
    assert offer.endswith(".mp4") and DCC_OFFER.match(offer), offer


def test_dispatch_list_dumps_only_in_channel():
    calls = _recording()
    rng = random.Random(5)
    lf, ld = {}, {}
    # !list in canale servito -> un dump (piu' righe), niente DCC
    listgag.dispatch("mario", "#sbiffo", "!list", rng, lf, ld)
    assert len(calls["say"]) >= 5 and calls["dcc"] == [], calls
    # !list in query NON dumpa (solo xdcc funziona in query)
    calls["say"].clear()
    listgag.dispatch("mario", "EliteWarez", "!list", rng, lf, ld)
    assert calls["say"] == [] and calls["dcc"] == [], calls


def test_dispatch_ignores_bots_and_unserved_channels():
    calls = _recording()
    rng = random.Random(6)
    lf, ld = {}, {}
    # un altro bot che spara xdcc: niente (o e' flood a due macchine)
    listgag.dispatch("cristobot", "#sbiffo", "xdcc send #1", rng, lf, ld)
    listgag.dispatch("elitewarez", "EliteWarez", "xdcc send #1", rng, lf, ld)
    # canale non servito: muto
    listgag.dispatch("mario", "#grappa", "xdcc send #1", rng, lf, ld)
    assert calls == {"say": [], "dcc": []}, calls


def test_dispatch_channel_addressed_xdcc():
    calls = _recording()
    rng = random.Random(4)
    lf, ld = {}, {}
    listgag.dispatch("mario", "#sbiffo", "!list", rng, lf, ld)
    titles = ld["#sbiffo"]
    # forma addressed `elitewarez: xdcc send #2` -> come la forma nuda (vjt 2026-09-13)
    listgag.dispatch("mario", "#sbiffo", "elitewarez: xdcc send #2", rng, lf, ld)
    assert len(calls["dcc"]) == 1, calls
    nick, fname, offer = calls["dcc"][0]
    assert nick == "mario" and offer == f"{titles[1]}.mp4", (offer, titles)
    # virgola invece dei due punti, e casing misto: sempre valido
    listgag.dispatch("mario", "#sbiffo", "EliteWarez, xdcc send #1", rng, lf, ld)
    assert len(calls["dcc"]) == 2, calls


def test_dispatch_addressed_to_other_nick_ignored():
    calls = _recording()
    rng = random.Random(4)
    lf, ld = {}, {}
    listgag.dispatch("mario", "#sbiffo", "!list", rng, lf, ld)
    # xdcc indirizzato a UN ALTRO bot: non e' roba nostra, muto
    listgag.dispatch("mario", "#sbiffo", "cristobot: xdcc send #2", rng, lf, ld)
    assert calls["dcc"] == [], calls


# ---------------------------------------------------------- broadcast in chan
# vjt 2026-09-14: "se la gente chiede roba a elitewarez ... lo scrive in canale:
# nick ha richiesto file e poi nick ha ricevuto file se lo scaricano", precisato
# subito dopo con "intendo se la chiedono in query". Quindi: la RICHIESTA si
# annuncia solo quando arriva in /msg (in canale l'hanno gia' letta tutti), la
# RICEZIONE sempre, e l'evento di fine transfer lo scrive bot.py in bot.log
# ("scrivi l'evento in bot log", stessa serata).


def test_dcc_done_pat_reads_the_bot_log_line():
    line = ("22:41:03 * DCC_DONE TO=mario "
            "FILE=Dune.Parte.Due.2024.iTA.ENG.2160p.UHD.BluRay.x265-SPARKS.mp4 "
            "BYTES=246254 ACK=246254\n")
    m = listgag.DCC_DONE_PAT.search(line)
    assert m and m["nick"] == "mario" and m["bytes"] == "246254", line
    assert m["file"].endswith(".mp4"), m["file"]
    # una riga PRIVMSG normale non deve mai matchare
    assert listgag.DCC_DONE_PAT.search(
        "22:41:03 < :tizio!~t@host PRIVMSG #sbiffo :DCC_DONE TO=mario") is None


def test_query_request_announced_where_the_nick_was_last_seen():
    # vjt 2026-09-14: "solo sul chan dove e' stata fatta la req, non su
    # entrambi". Una richiesta in query un canale non ce l'ha, quindi si usa
    # l'ultimo canale servito in cui quel nick ha parlato.
    calls = _recording()
    rng = random.Random(42)
    lf, ld, pending = {}, {}, {}
    listgag.last_seen.clear()
    listgag.last_dump_chan = None
    listgag.dispatch("mario", "#sbiffo", "ciao a tutti", rng, lf, ld, pending)
    listgag.dispatch("mario", "EliteWarez", "xdcc send #1", rng, lf, ld, pending)
    assert len(calls["dcc"]) == 1, calls
    offer = calls["dcc"][0][2]
    assert len(calls["say"]) == 1, calls
    chan, msg = calls["say"][0]
    assert chan == "#sbiffo" and "mario" in msg and offer in msg, calls
    assert pending["mario"][0] == offer and pending["mario"][1] == "#sbiffo", pending


def test_query_request_falls_back_to_the_last_dump_channel():
    calls = _recording()
    rng = random.Random(42)
    lf, ld, pending = {}, {}, {}
    listgag.last_seen.clear()
    listgag.last_dump_chan = None
    # `mario` non ha mai parlato: si ripiega sul canale dell'ultimo dump
    listgag.dispatch("tizio", "#sniffo", "!list", rng, lf, ld, pending)
    calls["say"].clear()
    listgag.dispatch("mario", "EliteWarez", "xdcc send #1", rng, lf, ld, pending)
    assert [c for c, _ in calls["say"]] == ["#sniffo"], calls


def test_query_request_with_no_channel_at_all_is_silent():
    calls = _recording()
    rng = random.Random(42)
    lf, ld, pending = {}, {}, {}
    listgag.last_seen.clear()
    listgag.last_dump_chan = None
    # sidecar appena riavviato, nessun contesto: la DCC parte lo stesso, ma non
    # inventiamo un canale in cui annunciarla
    listgag.dispatch("mario", "EliteWarez", "xdcc send #1", rng, lf, ld, pending)
    assert len(calls["dcc"]) == 1 and calls["say"] == [], calls


def test_channel_request_is_not_announced():
    calls = _recording()
    rng = random.Random(4)
    lf, ld, pending = {}, {}, {}
    listgag.dispatch("mario", "#sbiffo", "!list", rng, lf, ld, pending)
    calls["say"].clear()
    listgag.dispatch("mario", "#sbiffo", "xdcc send #2", rng, lf, ld, pending)
    # l'hanno letta tutti mentre la scriveva: niente seconda riga
    assert calls["say"] == [], calls
    assert pending["mario"][1] == "#sbiffo", pending


def test_done_announced_where_the_pack_was_asked():
    calls = _recording()
    rng = random.Random(4)
    lf, ld, pending = {}, {}, {}
    listgag.dispatch("mario", "#sbiffo", "!list", rng, lf, ld, pending)
    listgag.dispatch("mario", "#sbiffo", "xdcc send #2", rng, lf, ld, pending)
    offer = calls["dcc"][0][2]
    calls["say"].clear()
    listgag.announce_done("mario", offer, 3 * 1024 * 1024, rng, pending)
    assert len(calls["say"]) == 1, calls
    chan, msg = calls["say"][0]
    assert chan == "#sbiffo" and "mario" in msg and offer in msg, calls
    assert "3.0MB" in msg, msg
    # consumata: un secondo DCC_DONE non ha piu' un canale da cui pescare
    assert "mario" not in pending, pending


def test_done_without_pending_still_announces_once():
    calls = _recording()
    rng = random.Random(7)
    lf, ld = {}, {}
    listgag.last_seen.clear()
    listgag.last_dump_chan = None
    # sidecar riavviato a meta' transfer: lo stato pendente non c'e' piu', ma
    # il completamento si annuncia SEMPRE (vjt 2026-09-14), in un canale solo
    listgag.dispatch("mario", "#sniffo", "oh", rng, lf, ld, None)
    listgag.announce_done("mario", "Wicked.2024.iTA.mp4", 900, rng, {})
    assert len(calls["say"]) == 1, calls
    chan, msg = calls["say"][0]
    assert chan == "#sniffo" and "Wicked.2024.iTA.mp4" in msg, calls


def test_pending_entries_expire():
    pending = {}
    listgag.remember_offer(pending, "vecchio", "a.mp4", "#sbiffo")
    pending["vecchio"] = ("a.mp4", "#sbiffo",
                          pending["vecchio"][2] - listgag.PENDING_TTL_S - 1)
    listgag.remember_offer(pending, "nuovo", "b.mp4", None)
    assert "vecchio" not in pending and "nuovo" in pending, pending


if __name__ == "__main__":
    test_catalog_is_films_only()
    print("ok: catalogo solo film recenti, no software, LEGEND vuota")
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
    test_list_pattern()
    print("ok: XDCC_PAT ancorato, # opzionale, junk in coda tollerato")
    test_dispatch_channel_offers_under_pack_title()
    print("ok: xdcc #N in canale -> DCC offerto col titolo del pack")
    test_dispatch_channel_out_of_range_no_dcc()
    print("ok: #N fuori range -> riga burbera, nessuna DCC")
    test_dispatch_query_offers_random_title()
    print("ok: xdcc in query -> titolo a caso, offerta valida")
    test_dispatch_list_dumps_only_in_channel()
    print("ok: !list dumpa solo in canale, non in query")
    test_dispatch_ignores_bots_and_unserved_channels()
    print("ok: ignora bot e canali non serviti")
    test_dispatch_channel_addressed_xdcc()
    print("ok: `elitewarez: xdcc send #N` addressed -> come la forma nuda")
    test_dispatch_addressed_to_other_nick_ignored()
    print("ok: xdcc indirizzato ad altro nick -> muto")
    test_dcc_done_pat_reads_the_bot_log_line()
    print("ok: DCC_DONE_PAT legge la riga di bot.log, non i PRIVMSG")
    test_query_request_announced_where_the_nick_was_last_seen()
    print("ok: richiesta in query -> annuncio dove il nick e' stato visto")
    test_query_request_falls_back_to_the_last_dump_channel()
    print("ok: nick mai visto -> canale dell'ultimo dump")
    test_query_request_with_no_channel_at_all_is_silent()
    print("ok: nessun contesto -> DCC si, annuncio no")
    test_channel_request_is_not_announced()
    print("ok: richiesta in canale -> nessun doppione")
    test_done_announced_where_the_pack_was_asked()
    print("ok: DCC_DONE annunciato nel canale della richiesta, stato consumato")
    test_done_without_pending_still_announces_once()
    print("ok: DCC_DONE orfano -> annuncio comunque, in un canale solo")
    test_pending_entries_expire()
    print("ok: le richieste pendenti scadono")
    print("\nTUTTO VERDE, porco dio")
