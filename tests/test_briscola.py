"""briscola: il motore delle regole e un test per caso limite di
docs/briscola_spec.md (il numero nel nome). Niente FIFO e niente IRC: fifo
vuota, `sent` raccoglie quello che sarebbe uscito."""
import json
import os
import random
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import briscola as B  # noqa: E402
import cah as C  # noqa: E402
import ircgame as G  # noqa: E402
import sillabah as S  # noqa: E402

BOT = "MikeBongiorno"


def L(s):
    return f"12:00:00 < {s}"


def nuovo(tmp_path, seed=1, **kw):
    return B.Briscola("test", "/dev/null", "", tmp_path / "s.json", {"#t"},
                      rng=random.Random(seed), trust=(), **kw)


def partita(tmp_path, nicks=("a", "b"), serie=None, seed=1, **kw):
    """Tavolo aperto, giocatori seduti, prima partita data a now=60."""
    g = nuovo(tmp_path, seed=seed, **kw)
    g.handle(nicks[0], "#t", "!bri" + (f" {serie}" if serie else ""), now=0)
    for n in nicks[1:]:
        g.handle(n, "#t", "jo", now=1)
    g.tick(now=B.LOBBY_S)
    st = g.games["#t"]
    assert st["phase"] == "play"
    return g, st


def chan_lines(g, chan="#t"):
    return [t for (to, t) in g.sent if to == chan]


def gioca_fino_in_fondo(g, st, now=61, chan="#t"):
    """Ognuno gioca la prima carta al suo turno, finche' la partita e' viva."""
    while g.games.get(chan) is st and st["phase"] == "play":
        k = g.turn_key(st)
        assert g.handle(st["nicks"][k], BOT, "pl 1", now=now) is None
        now += 1
    return now


def truccata(st, hands, briscola, deck=(), turn=0):
    """Mani, mazzo e briscola messi a mano, per i casi precisi."""
    st["hands"] = {k: list(h) for k, h in zip(st["players"], hands)}
    st["briscola"] = briscola
    st["deck"] = list(deck)
    st["turn"] = turn
    st["table"] = []


# --- mazzo e regole ---------------------------------------------------------------

def test_mazzo_40_carte_120_punti():
    m = B.mazzo()
    assert len(m) == len(set(m)) == 40
    assert B.punti(m) == 120
    assert {s for _, s in m} == {"denari", "coppe", "spade", "bastoni"}


def test_ordine_di_forza():
    by = sorted(range(1, 11), key=lambda r: -B.FORZA[r])
    assert by == [1, 3, 10, 9, 8, 7, 6, 5, 4, 2]
    assert [B.PUNTI.get(r, 0) for r in by] == [11, 10, 4, 3, 2, 0, 0, 0, 0, 0]


def test_nomi_leggibili():
    assert B.nome((1, "spade")) == "Asso di spade"
    assert B.nome((3, "coppe")) == "3 di coppe"
    assert B.nome((10, "denari")) == "Re di denari"
    assert B.nome((9, "bastoni")) == "Cavallo di bastoni"
    assert B.nome((8, "coppe")) == "Fante di coppe"


@pytest.mark.parametrize("cards,seme,win", [
    ([(7, "spade"), (1, "spade")], "coppe", 1),         # stesso seme, piu' forte
    ([(3, "spade"), (10, "spade")], "coppe", 0),         # il 3 batte il Re
    ([(2, "spade"), (1, "denari")], "coppe", 0),         # altro seme non prende mai
    ([(1, "spade"), (2, "coppe")], "coppe", 1),          # la briscola prende
    ([(2, "coppe"), (1, "spade"), (4, "coppe")], "coppe", 2),  # briscola piu' alta
    ([(4, "spade"), (1, "denari"), (5, "spade"), (6, "bastoni")], "coppe", 2),
])
def test_presa(cards, seme, win):
    assert B.presa(cards, seme) == win


def test_nessun_obbligo_di_rispondere(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    truccata(st, [[(1, "spade")], [(2, "spade"), (4, "denari")]], (5, "coppe"))
    g.handle(st["nicks"][a], "#t", "pl 1", now=61)
    g.handle(st["nicks"][b], "#t", "pl 2", now=62)     # ha spade, cala denari
    assert st["prese"][a] == [(1, "spade"), (4, "denari")]


def test_pesca_prima_chi_prende_ultima_la_briscola(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    bri = (7, "coppe")
    truccata(st, [[(2, "spade"), (4, "spade")], [(1, "spade"), (5, "spade")]], bri,
             deck=[(6, "denari"), bri])
    g.handle(st["nicks"][a], "#t", "pl 1", now=61)
    g.handle(st["nicks"][b], "#t", "pl 1", now=62)     # b prende
    assert st["hands"][b][-1] == (6, "denari"), "chi prende pesca per primo"
    assert st["hands"][a][-1] == bri, "l'ultimo pesca la briscola scoperta"
    assert st["deck"] == [] and g.turn_key(st) == b


@pytest.mark.parametrize("n,deck,giri", [(2, 34, 20), (3, 30, 13), (4, 28, 10)])
def test_distribuzione_e_giri(tmp_path, n, deck, giri):
    g, st = partita(tmp_path, nicks="abcd"[:n])
    assert all(len(h) == 3 for h in st["hands"].values())
    assert len(st["deck"]) == deck
    assert st["deck"][-1] == st["briscola"], "la briscola sotto il mazzo"
    assert deck % n == 0, "nessuno resta a mani diverse"
    gioca_fino_in_fondo(g, st)
    assert st["giri"] == giri
    assert sum(len(p) for p in st["prese"].values()) == (39 if n == 3 else 40)
    assert sum(B.punti(p) for p in st["prese"].values()) == 120


def test_tre_giocatori_senza_il_2_di_coppe(tmp_path):
    """Briscola non a coppe: fuori il 2 di coppe, 10 briscole."""
    for seed in range(50):
        d = tmp_path / str(seed)
        d.mkdir()
        g, st = partita(d, nicks="abc", seed=seed)
        if st["briscola"][1] != "coppe":
            break
    carte = [c for h in st["hands"].values() for c in h] + st["deck"]
    assert len(carte) == len(set(carte)) == 39
    assert (2, "coppe") not in carte and (2, "denari") in carte
    assert sum(c[1] == st["briscola"][1] for c in carte) == 10
    assert any("Fuori il 2 di coppe" in t for t in chan_lines(g))


def test_tre_giocatori_coppe_briscola_via_il_2_di_denari(tmp_path):
    """Briscola a coppe (Malekith 2026-10-20): fuori il 2 di denari."""
    for seed in range(200):
        d = tmp_path / str(seed)
        d.mkdir()
        g, st = partita(d, nicks="abc", seed=seed)
        if st["briscola"][1] == "coppe":
            break
    else:
        pytest.fail("nessun seme coppe in 200 mescolate")
    carte = [c for h in st["hands"].values() for c in h] + st["deck"]
    assert len(carte) == 39
    assert (2, "denari") not in carte and (2, "coppe") in carte
    assert sum(c[1] == "coppe" for c in carte) == 10
    assert any("Fuori il 2 di denari" in t for t in chan_lines(g))


def test_quattro_coppie_alternate(tmp_path):
    g, st = partita(tmp_path, nicks="abcd")
    p = st["players"]
    assert g.team(st, p[0]) == g.team(st, p[2]) != g.team(st, p[1]) == g.team(st, p[3])
    assert any("Coppie:" in t for t in chan_lines(g))


def test_coppie_a_sorte(tmp_path):
    seen = set()
    for seed in range(12):
        d = tmp_path / str(seed)
        d.mkdir()
        g, st = partita(d, nicks="abcd", seed=seed)
        seen.add(frozenset(g.members(st, g.team(st, "a"))))
    assert len(seen) > 1


def chiudi_con(g, st, prese):
    for k, cards in zip(st["players"], prese):
        st["prese"][k] = cards
        st["tricks"][k] = 1
    st["hands"] = {k: [] for k in st["players"]}
    g.end_game("#t", st, 100)


SESSANTUNO = [(1, "denari"), (1, "coppe"), (1, "spade"), (1, "bastoni"),
              (3, "denari"), (10, "denari"), (9, "denari")]


def test_piu_di_60_vince(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    rest = [c for c in B.mazzo() if c not in SESSANTUNO]
    assert B.punti(SESSANTUNO) == 61
    chiudi_con(g, st, [SESSANTUNO, rest])
    assert "#t" not in g.games
    assert g.scores[a]["wins"] == 1 and b not in g.scores
    assert f"{st['nicks'][a]} vince la partita" in " ".join(chan_lines(g))


def test_60_pari_e_patta(tmp_path):
    g, st = partita(tmp_path)
    sessanta = [(1, "denari"), (1, "coppe"), (1, "spade"), (1, "bastoni"),
                (3, "denari"), (10, "denari"), (8, "denari")]
    assert B.punti(sessanta) == 60
    chiudi_con(g, st, [sessanta, [c for c in B.mazzo() if c not in sessanta]])
    assert "Patta" in " ".join(chan_lines(g)) and g.scores == {}
    assert "#t" not in g.games, "partita secca: la patta chiude"


def test_tre_parita_in_testa_patta(tmp_path):
    g, st = partita(tmp_path, nicks="abc")
    x = [(1, "denari"), (3, "denari"), (10, "denari"), (9, "denari"), (8, "denari"),
         (1, "coppe")]                                                          # 41
    y = [(1, "spade"), (3, "spade"), (10, "spade"), (9, "spade"), (8, "spade"),
         (1, "bastoni")]                                                        # 41
    z = [(3, "coppe"), (10, "coppe"), (9, "coppe"), (8, "coppe"), (3, "bastoni"),
         (10, "bastoni")]                                                       # 33
    assert (B.punti(x), B.punti(y), B.punti(z)) == (41, 41, 33)
    chiudi_con(g, st, [x, y, z])
    assert "Patta" in " ".join(chan_lines(g)) and g.scores == {}


def test_tre_vince_chi_ha_piu_punti(tmp_path):
    g, st = partita(tmp_path, nicks="abc")
    x = [(1, "denari"), (3, "denari"), (10, "denari"), (9, "denari"), (8, "denari"),
         (1, "coppe"), (3, "coppe")]                                           # 51
    y = [(1, "spade"), (3, "spade"), (10, "spade"), (9, "spade")]              # 28
    z = [(1, "bastoni"), (3, "bastoni")]                                       # 21
    chiudi_con(g, st, [x, y, z])
    assert g.scores[st["players"][0]]["wins"] == 1 and len(g.scores) == 1


def test_coppia_somma_i_punti_e_vincono_in_due(tmp_path):
    g, st = partita(tmp_path, nicks="abcd")
    p = st["players"]
    half = [c for c in B.mazzo() if c[1] in ("denari", "coppe")]               # 60
    a1 = half[:10] + [(1, "spade")]
    chiudi_con(g, st, [a1, [c for c in B.mazzo() if c[1] == "bastoni"],
                       half[10:], [c for c in B.mazzo() if c[1] == "spade" and c != (1, "spade")]])
    assert g.scores.keys() == {p[0], p[2]}
    assert "vincono la partita" in " ".join(chan_lines(g))


def test_partita_intera_e_conto_in_coda(tmp_path):
    g, st = partita(tmp_path)
    n = len(g.sent)
    gioca_fino_in_fondo(g, st)
    assert "#t" not in g.games
    tail = " ".join(t for to, t in g.sent[n:] if to == "#t")
    assert "Conto:" in tail and "prese" in tail


# --- punti mai a meta' partita ---------------------------------------------------

def test_punti_mai_a_meta_partita(tmp_path):
    g, st = partita(tmp_path, nicks="abc")
    now = 61
    while True:
        k = g.turn_key(st)
        if not st["deck"] and sum(len(h) for h in st["hands"].values()) <= 1:
            break
        g.handle(st["nicks"][k], "#t", "pl 1", now=now)
        g.handle("a", "#t", "!bri stato", now=now)
        now += 1
    assert "#t" in g.games
    for to, t in g.sent:
        assert not re.search(r"(?i)punt|conto", t), t
    assert any(t.startswith("🃏 Partita 1: briscola") for t in chan_lines(g))


# --- comandi, lobby, serie -------------------------------------------------------

def test_jo_solo_a_tavolo_aperto(tmp_path):
    g = nuovo(tmp_path)
    g.handle("a", "#t", "jo", now=0)
    assert "#t" not in g.games and chan_lines(g) == []
    g.handle("a", "#t", "!briscola", now=1)
    g.handle("b", "#t", "JO", now=2)
    g.handle("c", "#t", "!bri entra", now=2)                  # alias muto
    g.handle("d", "#t", "jo jo", now=2)                       # riga intera, no
    assert g.games["#t"]["players"] == ["a", "b", "c"]


def test_quinto_jo_rifiutato(tmp_path):
    g = nuovo(tmp_path)
    g.handle("a", "#t", "!bri", now=0)
    for n in "bcd":
        g.handle(n, "#t", "jo", now=1)
    g.handle("e", "#t", "jo", now=2)
    assert g.games["#t"]["players"] == ["a", "b", "c", "d"]
    assert chan_lines(g)[-1].startswith("e: tavolo pieno")


def test_uno_solo_non_parte(tmp_path):
    g = nuovo(tmp_path)
    g.handle("a", "#t", "!bri", now=0)
    g.tick(now=B.LOBBY_S)
    assert "#t" not in g.games and "Chiuso" in chan_lines(g)[-1]


def test_jo_a_partita_viva(tmp_path):
    g, st = partita(tmp_path)
    g.handle("z", "#t", "jo", now=61)
    assert "z" not in st["players"] and "prossima" in chan_lines(g)[-1]


@pytest.mark.parametrize("cmd", ["!bri 4", "!briscola 2", "!bri start 6"])
def test_serie_pari_rifiutata(tmp_path, cmd):
    g = nuovo(tmp_path)
    g.handle("a", "#t", cmd, now=0)
    assert "#t" not in g.games and "deve essere dispari" in chan_lines(g)[-1]


@pytest.mark.parametrize("cmd", ["!bri 11", "!bri 13"])
def test_serie_fuori_misura(tmp_path, cmd):
    g = nuovo(tmp_path)
    g.handle("a", "#t", cmd, now=0)
    assert "#t" not in g.games and "3, 5, 7 o 9" in chan_lines(g)[-1]


@pytest.mark.parametrize("cmd,n", [("!bri", 1), ("!bri 1", 1), ("!bri 3", 3),
                                   ("!briscola 9", 9), ("!bri start 5", 5)])
def test_serie_valide(tmp_path, cmd, n):
    g = nuovo(tmp_path)
    g.handle("a", "#t", cmd, now=0)
    assert g.games["#t"]["serie"] == n


def vinci(g, st, idx, now):
    """La partita finisce con la squadra del posto `idx` a 120."""
    chiudi_con(g, st, [B.mazzo() if i == idx else [] for i in range(len(st["players"]))])


def test_serie_finisce_alla_maggioranza(tmp_path):
    g, st = partita(tmp_path, serie=5)
    a = st["players"][0]
    for i in range(3):
        assert "#t" in g.games
        vinci(g, st, 0, 100)
        if i < 2:
            assert st["phase"] == "pausa"
            g.tick(now=100 + B.PAUSA_S)
            assert st["phase"] == "play" and st["game_no"] == i + 2
            # i posti restano quelli: a e' sempre al posto 0
            assert st["players"][0] == a
    assert "#t" not in g.games, "3 su 5: finita senza giocare le altre due"
    assert g.scores[a]["wins"] == 3
    assert "la serie" in chan_lines(g)[-1]


def test_serie_stato_mostra_le_vinte(tmp_path):
    g, st = partita(tmp_path, serie=3)
    vinci(g, st, 1, 100)
    g.tick(now=100 + B.PAUSA_S)
    g.handle("x", "#t", "!bri stato", now=101)
    b = st["nicks"][st["players"][1]]
    assert f"{b} 1" in chan_lines(g)[-1] and "al meglio di 3" in chan_lines(g)[-1]


def test_serie_in_tre_la_patta_non_conta(tmp_path):
    g, st = partita(tmp_path, nicks="abc", serie=3)
    half = B.mazzo()
    x = [c for c in half if c[1] == "denari"] + [(1, "coppe")]                 # 41
    y = [c for c in half if c[1] == "spade"] + [(1, "bastoni")]                # 41
    chiudi_con(g, st, [x, y, []])
    assert st["phase"] == "pausa" and sum(st["wins"].values()) == 0
    assert "non conta" in " ".join(chan_lines(g))
    g.tick(now=100 + B.PAUSA_S)
    assert st["game_no"] == 2
    vinci(g, st, 2, 200)
    g.tick(now=200 + B.PAUSA_S)
    vinci(g, st, 2, 300)
    assert "#t" not in g.games and g.scores[st["players"][2]]["wins"] == 2


def test_stop_solo_chi_apre_o_op(tmp_path):
    g, st = partita(tmp_path, nicks=("a", "b"))
    g.handle("b", "#t", "!bri stop", now=61)
    assert "#t" in g.games
    g.track(L(":srv 353 Mike = #t :@z b"))
    g.track(L(":srv 366 Mike #t :End"))
    g.handle("z", "#t", "!bri stop", now=62)
    assert "#t" not in g.games and g.scores == {}
    (tmp_path / "x").mkdir()
    g, st = partita(tmp_path / "x")
    g.handle("a", "#t", "!bri stop", now=61)
    assert "#t" not in g.games, "chi l'ha aperta chiude"


def test_ops_canale_guardato(tmp_path):
    g = B.Briscola("test", "/dev/null", "", tmp_path / "s.json", {"#i"},
                   rng=random.Random(1), opts={"#i": {"ops": True}},
                   trust=(("vjt", "*.openssl.it"),))
    g.handle("a", "#i", "!bri", now=0, host="x.example")
    assert "#i" not in g.games and "solo un op" in chan_lines(g, "#i")[-1]
    g.handle("vjt", "#i", "!bri", now=1, host="nowhere.openssl.it")
    assert "#i" in g.games
    g.handle("b", "#i", "jo", now=2, host="x.example")        # sedersi: tutti
    assert g.games["#i"]["players"] == ["vjt", "b"]
    g.handle("vjt", "#i", "!bri stop", now=3, host="evil.example")
    assert "#i" in g.games, "su ops lo starter senza can_control non chiude"
    g.handle("s", "#i", "!bri stop", now=4, host="staff.azzurra.chat")
    assert "#i" not in g.games


def test_carte_rimanda_la_mano(tmp_path):
    g, st = partita(tmp_path)
    n = len(g.sent)
    g.handle("a", "#t", "!bri carte", now=61)
    new = g.sent[n:]
    assert len(new) == 1 and new[0][0] == "a" and new[0][1].startswith("Mano: 1) ")
    assert new[0][1].endswith(f"— briscola: {st['briscola'][1]}")


def test_top_e_aiuto(tmp_path):
    g = nuovo(tmp_path)
    g.handle("a", "#t", "!bri top", now=0)
    assert "Partite vinte (test): nessuna" in chan_lines(g)[-1]
    g.handle("a", "#t", "!bri boh", now=0)
    assert chan_lines(g)[-1] == B.HELP


def test_load_nets_opzioni(monkeypatch):
    monkeypatch.setenv("BRISCOLA_NETS", "azzurra|/l|/f|#italia:ops,#Sbiffo")
    cfg = B.load_nets()["azzurra"]
    assert cfg["chans"] == {"#italia", "#sbiffo"}
    assert cfg["opts"] == {"#italia": {"ops": True}, "#sbiffo": {}}
    assert str(cfg["state"]).endswith("briscola_state.json")
    with pytest.raises(SystemExit):
        B.parse_opts("#i", ["vm18=22-9"])


# --- i casi di docs/briscola_spec.md ---------------------------------------------

def test_01_timeout_gioca_la_carta_che_vale_meno(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    truccata(st, [[(1, "spade"), (2, "coppe"), (10, "denari")], [(4, "spade")]], (5, "coppe"))
    st["deadline"] = 61
    g.tick(now=61)
    assert st["table"] == [(a, (10, "denari"))], "mai la briscola se ha altro"
    assert "ISON a" in [t for v, _, t in g.out.q if v == "RAW"] or g.ison


def test_01_timeout_a_parita_la_piu_debole(tmp_path):
    assert B.scarto([(7, "spade"), (2, "denari"), (4, "bastoni")], "coppe") == (2, "denari")
    assert B.scarto([(1, "coppe"), (3, "coppe"), (5, "coppe")], "coppe") == (5, "coppe")
    assert B.scarto([(10, "spade"), (8, "spade"), (2, "coppe")], "coppe") == (8, "spade")


def test_01_due_timeout_di_fila_abbandono(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    # a esce e prende sempre: due turni suoi di fila senza giocare
    truccata(st, [[(1, "coppe"), (3, "coppe"), (10, "coppe")],
                  [(2, "spade"), (4, "spade"), (5, "spade")]], (7, "coppe"))
    st["deadline"] = 61
    g.tick(now=61)                                    # primo timeout: cala per lui
    assert st["timeouts"][a] == 1 and len(st["table"]) == 1
    g.handle(b, "#t", "pl 1", now=62)
    assert g.turn_key(st) == a, "a ha preso con la briscola"
    g.tick(now=62 + B.TURN_S)
    assert "#t" not in g.games
    assert "2 turni saltati" in " ".join(chan_lines(g))
    assert g.scores[b]["wins"] == 1, "abbandono dopo il primo giro: conta"


def test_01_timeout_azzerato_quando_gioca(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    st["turn"] = 0
    st["deadline"] = 61
    g.tick(now=61)
    assert st["timeouts"][a] == 1
    # giro dopo: a gioca da se' e il contatore riparte
    while g.turn_key(st) != a:
        g.handle(st["nicks"][g.turn_key(st)], BOT, "pl 1", now=62)
    g.handle(st["nicks"][a], BOT, "pl 1", now=63)
    assert a not in st["timeouts"]


def test_02_abbandono_in_due_vince_l_altro_dopo_il_primo_giro(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    g.handle(st["nicks"][g.turn_key(st)], "#t", "pl 1", now=61)
    g.handle(st["nicks"][g.turn_key(st)], "#t", "pl 1", now=62)
    assert st["giri"] == 1
    g.track(L(f":{a}!x@h PART #t :ciao"), now=63)
    assert "#t" not in g.games
    assert g.scores == {b: {"nick": b, "wins": 1}}
    assert "abbandono" in " ".join(chan_lines(g))


def test_02_abbandono_prima_del_primo_giro_non_conta(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    g.handle(a, "#t", "!bri esci", now=61)
    assert "#t" not in g.games and g.scores == {}
    assert f"{b} vince la partita" in " ".join(chan_lines(g))


def test_02_abbandono_in_quattro_vince_la_coppia_avversaria(tmp_path):
    g, st = partita(tmp_path, nicks="abcd")
    p = st["players"]
    for i in range(4):
        g.handle(st["nicks"][g.turn_key(st)], BOT, "pl 1", now=61 + i)
    g.track(L(f":{p[1]}!x@h QUIT :Quit: bye"), now=70)
    assert "#t" not in g.games
    assert set(g.scores) == {p[0], p[2]}


def test_02_kick_e_abbandono(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    g.track(L(f":op!x@h KICK #t {b} :via"), now=61)
    assert "#t" not in g.games


def test_02_abbandono_in_tre(tmp_path):
    g, st = partita(tmp_path, nicks="abc")
    p = st["players"]
    st["prese"][p[0]] = [(1, "denari")]
    st["giri"] = 1
    g.track(L(f":{p[2]}!x@h PART #t"), now=61)
    assert set(g.scores) == {p[0]}


def test_02_abbandono_in_lobby_si_alza_e_basta(tmp_path):
    g = nuovo(tmp_path)
    g.handle("a", "#t", "!bri", now=0)
    g.handle("b", "#t", "jo", now=1)
    g.handle("b", "#t", "!bri esci", now=2)
    assert g.games["#t"]["players"] == ["a"]
    g.handle("a", "#t", "!bri esci", now=3)
    assert "#t" not in g.games


def test_02_abbandono_fra_due_partite_chiude_la_serie(tmp_path):
    g, st = partita(tmp_path, serie=3)
    vinci(g, st, 0, 100)
    g.track(L(f":{st['players'][1]}!x@h PART #t"), now=101)
    assert "#t" not in g.games and "Serie interrotta" in chan_lines(g)[-1]


def test_03_cambio_nick(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    hand = list(st["hands"][a])
    st["turn"] = 0
    g.track(L(f":{a}!x@h NICK :Anna"), now=61)
    assert st["players"][0] == "anna" and st["hands"]["anna"] == hand
    assert g.turn_key(st) == "anna"
    g.handle("Anna", BOT, "pl 1", now=62)
    assert st["table"] == [("anna", hand[0])]
    g.track(L(f":{b}!x@h NICK :Bruno"), now=63)
    g.handle("Bruno", BOT, "pl 1", now=64)
    assert st["giri"] == 1


def test_04_netsplit_non_e_abbandono(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    st["turn"] = 0
    g.track(L(f":{a}!x@h QUIT :irc.azzurra.chat hub.azzurra.chat"), now=61)
    assert "#t" in g.games and a in st["split"]
    n = len(g.ison)
    g.tick(now=60 + B.TURN_S)
    assert len(st["table"]) == 1 and st["table"][0][0] == a, "caso 1 per lui"
    assert len(g.ison) == n, "niente ISON per chi e' nello split"
    g.track(L(f":{a}!x@h JOIN :#t"), now=151)
    assert a not in st["split"]


def test_04_netsplit_due_timeout_abbandono(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    g.track(L(f":{a}!x@h QUIT :irc.azzurra.chat hub.azzurra.chat"), now=61)
    now = 60
    for _ in range(10):
        if "#t" not in g.games:
            break
        if g.turn_key(st) == b:
            g.handle(b, BOT, "pl 1", now=now)
        else:
            now += B.TURN_S
            g.tick(now=now)
    assert "#t" not in g.games and "turni saltati" in " ".join(chan_lines(g))


def test_04_ison_offline_abbandona(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    st["turn"] = 0
    g.tick(now=60 + B.TURN_S)
    assert g.ison and g.ison[0] == {a: "#t"}
    g.track(L(":srv 303 Mike :"), now=151)
    assert "#t" not in g.games


def test_04_ison_online_resta(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    st["turn"] = 0
    g.tick(now=60 + B.TURN_S)
    g.track(L(f":srv 303 Mike :{a}"), now=151)
    assert "#t" in g.games


def test_05_fuori_turno_e_fuori_mano(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    st["turn"] = 0
    g.handle(b, "#t", "pl 1", now=61)
    assert st["table"] == [] and g.sent[-1] == (b, f"Non tocca a te: tocca a {a}.")
    g.handle(a, BOT, "pl 4", now=61)
    assert st["table"] == [] and g.sent[-1] == (a, "Hai 3 carte: pl 1-3.")
    g.handle(a, BOT, "pl 1 2", now=61)
    assert st["table"] == [] and g.sent[-1][0] == a
    n = len(g.sent)
    g.handle("estraneo", "#t", "pl 1", now=61)
    g.handle("estraneo", BOT, "pl 1", now=61)
    assert len(g.sent) == n, "chi non gioca: silenzio"


def test_05_notice_mai_risposta(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    st["turn"] = 0
    n = len(g.sent)
    g.handle(b, BOT, "pl 1", now=61, notice=True)            # fuori turno
    g.handle(a, BOT, "pl 9", now=61, notice=True)            # fuori mano
    g.handle(a, "#t", "pl 1", now=61, notice=True)           # NOTICE al canale
    assert len(g.sent) == n and st["table"] == []
    g.handle(a, BOT, "pl 1", now=62, notice=True)
    assert len(st["table"]) == 1
    assert not [t for to, t in g.sent[n:] if to == a]


def test_05_sillabah_ignora_pl_in_query():
    for t in ("pl 3", "PL 1", "pl 2 "):
        assert S.CAH_QUERY_PAT.match(t.strip())


def test_06_una_partita_per_nick(tmp_path):
    g = B.Briscola("test", "/dev/null", "", tmp_path / "s.json", {"#t", "#u"},
                   rng=random.Random(1), trust=())
    g.handle("a", "#t", "!bri", now=0)
    g.handle("a", "#u", "!bri", now=0)
    assert "#u" not in g.games
    g.handle("b", "#u", "!bri", now=0)
    g.handle("a", "#u", "jo", now=1)
    assert g.games["#u"]["players"] == ["b"]


def test_06_cah_e_briscola_si_escludono(tmp_path):
    locks = tmp_path / "locks"
    cah = C.Cah("test", "/dev/null", "", tmp_path / "c.json", {"#t"},
                (["n ___"], [f"w{i}" for i in range(40)]), rng=random.Random(1),
                trust=(), locks=str(locks))
    bri = B.Briscola("test", "/dev/null", "", tmp_path / "b.json", {"#t"},
                     rng=random.Random(1), trust=(), locks=str(locks))
    cah.handle("a", "#t", "!cah", now=0)
    bri.handle("b", "#t", "!bri", now=0)
    assert "#t" not in bri.games
    assert chan_lines(bri)[-1] == G.busy_line("Cards Against Humanity")
    cah.handle("a", "#t", "!cah stop", now=1)
    cah.tick(now=1)
    bri.handle("b", "#t", "!bri", now=2)
    assert "#t" in bri.games
    cah.handle("a", "#t", "!cah", now=3)
    assert "#t" not in cah.games and chan_lines(cah)[-1] == G.busy_line("Briscola")
    bri.lock.sync({})


def test_07_riavvio_perde_la_partita_tiene_la_classifica(tmp_path):
    g, st = partita(tmp_path)
    vinci(g, st, 0, 100)
    g.save()
    assert json.loads((tmp_path / "s.json").read_text())["scores"]
    g2 = nuovo(tmp_path)
    assert g2.games == {} and g2.scores == g.scores and g2.sent == []


def test_08_piu_c_righe_senza_codici(tmp_path):
    g, st = partita(tmp_path)
    assert any("\x02" in t for t in chan_lines(g))
    g.track(L(":op!x@h MODE #t +c"), now=61)
    n = len(chan_lines(g))
    g.handle(st["nicks"][g.turn_key(st)], "#t", "pl 1", now=62)
    assert all("\x02" not in t for t in chan_lines(g)[n:])


def test_08_404_ritenta_poi_muto_poi_riprende(tmp_path):
    g, st = partita(tmp_path)
    g.out.flush(now=60, force=True)
    g.track(L(":srv 404 MikeBongiorno #t :Cannot send to channel"), now=61)
    assert st["phase"] == "play", "primo 404: si riprova senza colori"
    g.out.flush(now=61, force=True)
    g.track(L(":srv 404 MikeBongiorno #t :Cannot send to channel"), now=62)
    assert st["phase"] == "muto"
    g.tick(now=62 + B.LOBBY_S - 1)
    assert st["phase"] == "muto" and st["table"] == [], "niente timeout da muti"
    g.tick(now=62 + B.LOBBY_S)
    assert st["phase"] == "play" and "Si riprende" in chan_lines(g)[-1]


def test_08_muto_resta_muto_chiude(tmp_path):
    g, st = partita(tmp_path)
    g.track(L(":op!x@h MODE #t +m"), now=61)
    g.track(L(":srv 404 MikeBongiorno #t :Cannot send to channel"), now=62)
    assert st["phase"] == "muto"
    g.tick(now=62 + B.LOBBY_S)
    assert "#t" not in g.games


# --- righe ------------------------------------------------------------------------

@pytest.mark.parametrize("n", [2, 3, 4])
def test_righe_sotto_i_400_byte(tmp_path, n):
    nicks = [("Nick" + c) * 7 for c in "ABCD"[:n]]
    g, st = partita(tmp_path, nicks=nicks, serie=3)
    g.handle(nicks[0], "#t", "!bri stato", now=60)
    gioca_fino_in_fondo(g, st)
    g.handle(nicks[0], "#t", "!bri stato", now=999)
    g.handle(nicks[0], "#t", "!bri top", now=999)
    assert len(g.sent) > 20
    for to, text in g.sent:
        assert len(f"PRIVMSG {to} :{text}".encode()) < 400, text


def test_mano_in_query_formato(tmp_path):
    g, st = partita(tmp_path)
    a = st["players"][0]
    mine = [t for to, t in g.sent if to == a]
    assert re.fullmatch(r"Mano: 1\) .+  2\) .+  3\) .+ — briscola: \w+", mine[-1])


def test_righe_pulite():
    """Il gioco gira anche su canali grandi: nessuna bestemmia nel sorgente."""
    src = open(B.__file__, encoding="utf-8").read()
    assert not re.search(r"(?i)\b(dio|madonna|cristo|porco|porca|cazz|stronz)", src)
