"""duello: la meccanica del duello a insulti (issue #7). Niente FIFO e niente
IRC: fifo vuota, `sent` raccoglie quello che sarebbe uscito; i log sono finti."""
import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import duello as D  # noqa: E402
import sillabah as S  # noqa: E402

BOT = "MikeBongiorno"
COPPIE = [{"id": f"c{i}", "insulto": f"insulto {i}", "risposta": f"risposta {i}"}
          for i in range(10)]


def L(s):
    return f"12:00:00 < {s}"


def nuovo(tmp_path, coppie=COPPIE, seed=1, chans=("#t",)):
    return D.Duello("test", "/dev/null", "", tmp_path / "s.json", set(chans),
                    list(coppie), rng=random.Random(seed))


def duello(tmp_path, **kw):
    """a sfida b, b accetta a now=1: tocca ad a attaccare."""
    g = nuovo(tmp_path, **kw)
    g.handle("a", "#t", "!duello b", now=0)
    g.handle("b", "#t", "!accetto", now=1)
    st = g.games["#t"]
    assert st["phase"] == "attacco" and st["att"] == "a"
    return g, st


def chan_lines(g, chan="#t"):
    return [t for (to, t) in g.sent if to == chan]


def query(g, nick):
    return [t for (to, t) in g.sent if to == nick]


def rep(g, nick, chan="#t"):
    return g.state["canali"][chan]["repertori"][nick]


def truccato(g, nick, insulti, risposte):
    r = rep(g, nick)
    r["insulti"], r["risposte"] = list(insulti), list(risposte)


# --- contenuto ---------------------------------------------------------------------

def test_insulti_veri_almeno_venti():
    coppie = D.load_coppie()
    assert len(coppie) >= D.MIN_COPPIE
    assert len({c["id"] for c in coppie}) == len(coppie)
    # Gli insulti sono tutti distinti; le risposte NO, per disegno: nel gioco
    # originale piu insulti condividono la stessa risposta (es. la stessa
    # battuta para sia il contadino sia la mungitura di Carla). Il vecchio
    # divieto sulle firme Monkey Island e l'unicita su tutti i testi
    # appartenevano alla politica "testo originale" del primo branch,
    # sostituita dai testi MI veri su ordine di vjt (#sbiffo 2026-10-30).
    assert len({c["insulto"] for c in coppie}) == len(coppie)


def test_ogni_riga_sta_in_una_riga_irc():
    for c in D.load_coppie():
        for t in (c["insulto"], c["risposta"]):
            line = f"PRIVMSG #sbiffo :🛡️ \x02UnNickLungo\x02: «{t}»"
            assert len(line.encode()) < 400, t


def test_id_doppio_ferma(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"coppie": [COPPIE[0], COPPIE[0]]}))
    with pytest.raises(SystemExit):
        D.load_coppie(p)


# --- sfida -------------------------------------------------------------------------

def test_sfida_e_accetto(tmp_path):
    g, st = duello(tmp_path)
    lines = chan_lines(g)
    assert "a sfida b" in lines[0] and "60s" in lines[0]
    assert "b accetta" in lines[1]
    # l'esordiente ha START_N insulti e START_N risposte
    for n in ("a", "b"):
        assert len(rep(g, n)["insulti"]) == D.START_N
        assert len(rep(g, n)["risposte"]) == D.START_N
    # il repertorio dell'attaccante arriva in canale, numerato; niente query
    q = " ".join(chan_lines(g)[2:])
    assert "\x02a\x02 attacca" in q and "!i N" in q and "1) insulto" in q and "3) insulto" in q
    assert query(g, "a") == [] and query(g, "b") == []


def test_solo_lo_sfidato_accetta(tmp_path):
    g = nuovo(tmp_path)
    g.handle("a", "#t", "!duello b", now=0)
    g.handle("a", "#t", "!accetto", now=1)
    g.handle("c", "#t", "!accetto", now=1)
    assert g.games["#t"]["phase"] == "sfida"


def test_rifiuto_e_ritiro(tmp_path):
    g = nuovo(tmp_path)
    g.handle("a", "#t", "!duello b", now=0)
    g.handle("b", "#t", "!rifiuto", now=1)
    assert "#t" not in g.games and "rifiuta" in chan_lines(g)[-1]
    g.handle("a", "#t", "!duello b", now=2)
    g.handle("a", "#t", "!rifiuto", now=3)
    assert "#t" not in g.games and "ritira" in chan_lines(g)[-1]


def test_sfida_scade(tmp_path):
    g = nuovo(tmp_path)
    g.handle("a", "#t", "!duello b", now=0)
    g.tick(now=D.ACCEPT_S - 1)
    assert "#t" in g.games
    g.tick(now=D.ACCEPT_S)
    assert "#t" not in g.games and "scaduta" in chan_lines(g)[-1]
    assert g.state["canali"].get("#t", {}).get("vinti", {}) == {}


def test_sfide_sbagliate(tmp_path):
    g = nuovo(tmp_path)
    g.handle("a", "#t", "!duello a", now=0)
    g.handle("a", "#t", f"!duello {BOT}", now=0)
    assert g.games == {}
    # chi non e' nel canale (quando il NAMES l'ha detto) non si sfida
    g.track(L(f":srv 353 {BOT} = #t :@{BOT} a b"))
    g.track(L(f":srv 366 {BOT} #t :End of /NAMES list."))
    g.handle("a", "#t", "!duello zorro", now=0)
    assert g.games == {} and "non è nel canale" in chan_lines(g)[-1]
    g.handle("a", "#t", "!duello b", now=0)
    assert "#t" in g.games
    g.handle("c", "#t", "!duello a", now=1)
    assert g.games["#t"]["b"] == "b" and "c'è già un duello" in chan_lines(g)[-1]


def test_un_duello_per_volta_fra_canali(tmp_path):
    g = nuovo(tmp_path, chans=("#t", "#u"))
    g.handle("a", "#t", "!duello b", now=0)
    g.handle("c", "#u", "!duello a", now=0)
    assert "#u" not in g.games and "sta già duellando" in chan_lines(g, "#u")[-1]


def test_lucchetto_condiviso(tmp_path):
    locks = tmp_path / "locks"
    g = D.Duello("test", "/dev/null", "", tmp_path / "s.json", {"#t"}, COPPIE,
                 rng=random.Random(1), locks=str(locks))
    h = D.Duello("test", "/dev/null", "", tmp_path / "s2.json", {"#t"}, COPPIE,
                 rng=random.Random(1), locks=str(locks))
    g.handle("a", "#t", "!duello b", now=0)
    h.handle("c", "#t", "!duello d", now=0)
    assert "#t" not in h.games and "Duello" in chan_lines(h)[-1]
    g.handle("b", "#t", "!rifiuto", now=1)
    g.tick(now=2)                       # la sync molla il lucchetto
    h.handle("c", "#t", "!duello d", now=3)
    assert "#t" in h.games


# --- scambi --------------------------------------------------------------------------

def test_risposta_giusta_passa_l_attacco_e_si_impara(tmp_path):
    g, st = duello(tmp_path)
    truccato(g, "a", ["c1", "c2"], ["c5"])
    truccato(g, "b", ["c3"], ["c7", "c1"])
    g.handle("a", "#t", "!i 1", now=2)
    assert st["phase"] == "difesa"
    assert "c1" in rep(g, "b")["insulti"]          # chi subisce, impara
    lines = chan_lines(g)
    i = lines.index("🗡️ \x02a\x02: «insulto 1»")
    qb = " ".join(lines[i + 1:])
    assert "\x02b\x02 para" in qb and "1) risposta 7" in qb and "2) risposta 1" in qb
    assert query(g, "b") == []
    g.handle("b", BOT, "!r 2", now=3)               # in query non conta
    assert st["phase"] == "difesa"
    g.handle("b", "#t", "!r 2", now=3)
    assert st["score"] == {"a": 0, "b": 1}
    assert st["att"] == "b" and st["phase"] == "attacco"
    assert "c1" in rep(g, "a")["risposte"] and "c1" in rep(g, "b")["risposte"]
    out = " ".join(chan_lines(g))
    assert "«risposta 1»" in out and "Parata" in out


def test_risposta_sbagliata_attaccante_continua(tmp_path):
    g, st = duello(tmp_path)
    truccato(g, "a", ["c1"], [])
    truccato(g, "b", [], ["c7"])
    g.handle("a", "#t", "!i 1", now=2)
    g.handle("b", "#t", "!r 1", now=3)
    assert st["score"] == {"a": 1, "b": 0} and st["att"] == "a"
    assert "c7" not in rep(g, "a")["risposte"]     # la risposta sbagliata non si impara
    assert "c1" not in rep(g, "b")["risposte"]     # e quella giusta nemmeno, se non giocata


def test_numero_fuori_repertorio(tmp_path):
    g, st = duello(tmp_path)
    g.handle("a", "#t", "!i 9", now=2)
    assert st["phase"] == "attacco" and "a: un numero fra 1 e 3" in chan_lines(g)[-1]


def test_fuori_turno_non_conta(tmp_path):
    g, st = duello(tmp_path)
    g.handle("b", "#t", "!i 1", now=2)
    g.handle("a", "#t", "!r 1", now=2)
    g.handle("c", "#t", "!i 1", now=2)
    assert st["phase"] == "attacco" and st["score"] == {"a": 0, "b": 0}


def test_timeout_difesa_e_attacco(tmp_path):
    g, st = duello(tmp_path)
    g.handle("a", "#t", "!i 1", now=2)
    g.tick(now=2 + D.TURN_S)
    assert st["score"] == {"a": 1, "b": 0} and st["att"] == "a"
    assert "non para" in " ".join(chan_lines(g))
    g.tick(now=2 + 2 * D.TURN_S)                    # a non attacca: scambio a b
    assert st["score"] == {"a": 1, "b": 1} and st["att"] == "b"
    assert "non attacca" in " ".join(chan_lines(g))
    # chi tace riceve un ISON (su +u i QUIT non arrivano)
    assert list(g.ison) == [{"b": "#t"}, {"a": "#t"}]


def test_vince_a_tre_e_si_salva(tmp_path):
    g, st = duello(tmp_path)
    truccato(g, "a", ["c1"], [])
    truccato(g, "b", [], ["c9"])
    for i in range(D.WIN):
        g.handle("a", "#t", "!i 1", now=2 + i)
        g.handle("b", "#t", "!r 1", now=2 + i)
    assert "#t" not in g.games
    assert "a" in chan_lines(g)[-1] and "vince il duello" in chan_lines(g)[-1]
    assert g.dirty
    g.save()
    d = json.loads((tmp_path / "s.json").read_text())
    assert d["canali"]["#t"]["vinti"]["a"] == {"nick": "a", "vinti": 1}
    assert "c1" in d["canali"]["#t"]["repertori"]["b"]["insulti"]
    # riletto da disco, il repertorio torna
    h = nuovo(tmp_path)
    assert h.state == d
    h.handle("x", "#t", "!classifica duello", now=10)
    h.handle("x", "#t", "!classifica", now=10)
    h.handle("x", "#t", "!classifica sillabah", now=10)
    assert chan_lines(h) == ["🏆 [Duello] Duelli vinti (#t): a 1"] * 2


def test_repertorio_per_canale(tmp_path):
    g = nuovo(tmp_path, chans=("#t", "#u"))
    g.handle("a", "#t", "!duello b", now=0)
    g.handle("b", "#t", "!accetto", now=1)
    assert "a" in g.state["canali"]["#t"]["repertori"]
    assert "a" not in g.state["canali"].get("#u", {}).get("repertori", {})


def test_repertorio_in_query(tmp_path):
    g = nuovo(tmp_path)
    g.handle("z", BOT, "!repertorio", now=0)
    assert "Non conosci ancora niente" in query(g, "z")[-1]
    g, st = duello(tmp_path)
    g.handle("a", BOT, "!repertorio", now=2)
    q = " ".join(query(g, "a"))
    assert "insulti (3)" in q and "risposte (3)" in q
    g.handle("b", "#t", "!repertorio", now=2)       # in canale: risposta in query
    assert any("insulti (3)" in t for t in query(g, "b"))
    assert not any("insulti (" in t for t in chan_lines(g))


# --- presenza ----------------------------------------------------------------------

@pytest.mark.parametrize("ev", [
    ":b!u@h PART #t :ciao",
    f":op!u@h KICK #t b :fuori",
    ":b!u@h QUIT :Ping timeout",
])
def test_abbandono_e_sconfitta(tmp_path, ev):
    g, st = duello(tmp_path)
    g.track(L(ev), now=5)
    assert "#t" not in g.games
    assert g.state["canali"]["#t"]["vinti"]["a"]["vinti"] == 1
    assert "abbandona" in " ".join(chan_lines(g))


def test_abbandono_a_sfida_aperta_annulla(tmp_path):
    g = nuovo(tmp_path)
    g.handle("a", "#t", "!duello b", now=0)
    g.track(L(":b!u@h PART #t"), now=1)
    assert "#t" not in g.games and "annullata" in chan_lines(g)[-1]
    assert g.state["canali"].get("#t", {}).get("vinti", {}) == {}


def test_netsplit_non_e_abbandono(tmp_path):
    g, st = duello(tmp_path)
    g.track(L(":b!u@h QUIT :irc.a.net irc.b.net"), now=5)
    assert g.games["#t"] is st


def test_part_di_un_altro_non_conta(tmp_path):
    g, st = duello(tmp_path)
    g.track(L(":c!u@h PART #t"), now=5)
    g.track(L(":b!u@h PART #altro"), now=5)
    assert g.games["#t"] is st


def test_ison_negativo_e_abbandono(tmp_path):
    g, st = duello(tmp_path)
    g.tick(now=1 + D.TURN_S)                        # a non attacca: ISON a
    g.track(L(f":srv 303 {BOT} :b"), now=1 + D.TURN_S)
    assert "#t" not in g.games
    assert g.state["canali"]["#t"]["vinti"]["b"]["vinti"] == 1


def test_cambio_nick(tmp_path):
    g, st = duello(tmp_path)
    truccato(g, "a", ["c1"], [])
    truccato(g, "b", [], ["c1"])
    g.track(L(":a!u@h NICK :Alfa"), now=2)
    assert st["att"] == "alfa" and st["nicks"]["alfa"] == "Alfa"
    g.handle("Alfa", "#t", "!i 1", now=3)
    g.handle("b", "#t", "!r 1", now=4)
    assert st["score"] == {"alfa": 0, "b": 1}
    # il repertorio resta quello del nick con cui si e' cominciato
    assert "c1" in rep(g, "a")["risposte"] and "alfa" not in g.state["canali"]["#t"]["repertori"]
    g.track(L(":Alfa!u@h PART #t"), now=5)
    assert g.state["canali"]["#t"]["vinti"]["b"]["vinti"] == 1


def test_404_senza_colori_da_ripulire_chiude(tmp_path):
    g, st = duello(tmp_path)
    g.sent                                          # svuota la coda
    g.out.recent.clear()
    g.track(L(f":srv 404 {BOT} #t :Cannot send to channel"), now=5)
    assert "#t" not in g.games


# --- convivenza con gli altri giochi ------------------------------------------------------

def test_notice_ignorate(tmp_path):
    g, st = duello(tmp_path)
    n = len(g.sent)
    g.handle("a", BOT, "!i 1", now=2, notice=True)
    assert st["phase"] == "attacco" and len(g.sent) == n


def test_non_risponde_a_help(tmp_path):
    g = nuovo(tmp_path)
    for t in ("!help", "!giochi"):
        g.handle("a", "#t", t, now=0)
        g.handle("a", BOT, t, now=0)
    assert g.sent == []


def test_duello_nudo_aiuto_e_stato(tmp_path):
    g = nuovo(tmp_path)
    g.handle("a", "#t", "!duello", now=0)
    assert chan_lines(g)[-1] == D.HELP
    g, st = duello(tmp_path)
    g.handle("x", "#t", "!duello", now=2)
    assert "tocca a a attaccare" in chan_lines(g)[-1]


def test_sillabah_tace_sui_comandi_in_query(tmp_path):
    s = S.Sillabah("test", "/dev/null", "", tmp_path / "x.json", {"#t"},
                   S.Dizionario(["riso"]), talk=False, rng=random.Random(1))
    for t in ("!i 1", "!r 2", "!repertorio"):
        s.handle("a", BOT, t, now=0)
    assert s.sent == [] and s.notices == []


def test_righe_corte(tmp_path):
    g, st = duello(tmp_path, coppie=D.load_coppie())
    for i in range(6):
        k = st["att"] if st["phase"] == "attacco" else st["def"]
        g.handle(st["nicks"][k], "#t", "!i 1" if st["phase"] == "attacco" else "!r 1",
                 now=2 + i)
        if "#t" not in g.games:
            break
    g.handle("a", BOT, "!repertorio", now=20)
    assert len(g.sent) > 5
    for to, text in g.sent:
        assert len(f"PRIVMSG {to} :{text}".encode()) < 400, text


def test_risposta_uguale_di_un_altra_coppia_para(tmp_path):
    coppie = COPPIE[:9] + [{"id": "c9", "insulto": "insulto 9", "risposta": "risposta 1"}]
    g = D.Duello("test", "/dev/null", "", tmp_path / "s.json", {"#t"}, coppie,
                 rng=random.Random(1))
    g.handle("a", "#t", "!duello b", now=0)
    g.handle("b", "#t", "!accetto", now=1)
    st = g.games["#t"]
    truccato(g, "a", ["c1"], [])
    truccato(g, "b", [], ["c9"])
    g.handle("a", "#t", "!i 1", now=2)
    g.handle("b", "#t", "!r 1", now=3)
    assert st["score"] == {"a": 0, "b": 1}
    assert "c1" in rep(g, "b")["risposte"]
