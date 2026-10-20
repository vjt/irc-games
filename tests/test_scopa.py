"""scopa: le regole di docs/scopa_spec.md e i casi limite del motore di tavolo
(tavolo.py, condiviso con la briscola). Niente FIFO e niente IRC: fifo vuota,
`sent` raccoglie quello che sarebbe uscito."""
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
import scopa as P  # noqa: E402
import sillabah as S  # noqa: E402

BOT = "MikeBongiorno"
D, C_, SP, BA = "denari", "coppe", "spade", "bastoni"


def L(s):
    return f"12:00:00 < {s}"


def nuovo(tmp_path, seed=1, rng=None, **kw):
    return P.Scopa("test", "/dev/null", "", tmp_path / "s.json", {"#t"},
                   rng=rng or random.Random(seed), trust=(), **kw)


def partita(tmp_path, nicks=("a", "b"), arg="", seed=1, rng=None, **kw):
    """Tavolo aperto, giocatori seduti, prima smazzata data a now=60."""
    g = nuovo(tmp_path, seed=seed, rng=rng, **kw)
    g.handle(nicks[0], "#t", "!scopa" + (f" {arg}" if arg else ""), now=0)
    for n in nicks[1:]:
        g.handle(n, "#t", "jo", now=1)
    g.tick(now=P.LOBBY_S)
    st = g.games["#t"]
    assert st["phase"] == "play"
    return g, st


def chan_lines(g, chan="#t"):
    return [t for (to, t) in g.sent if to == chan]


def query(g, nick):
    return [t for (to, t) in g.sent if to == nick]


def truccata(st, hands, table, deck=(), turn=0):
    """Mani, tavola e mazzo messi a mano, per i casi precisi."""
    st["hands"] = {k: list(h) for k, h in zip(st["players"], hands)}
    st["table"] = list(table)
    st["deck"] = list(deck)
    st["turn"] = turn


def nick_at(st, i):
    return st["nicks"][st["players"][i]]


def gioca_smazzata(g, st, now=61, chan="#t"):
    """Ognuno gioca la prima carta, con la prima presa possibile, finche' la
    smazzata e' viva. Ritorna l'istante dopo l'ultima giocata."""
    no = st["mano_no"]
    while g.games.get(chan) is st and st["phase"] == "play" and st["mano_no"] == no:
        k = g.turn_key(st)
        caps = P.prese_possibili(st["table"], st["hands"][k][0])
        cmd = P.Scopa.pick_cmd(1, caps[0]) if caps else "pl 1"
        assert g.play(chan, st, k, 1, [i + 1 for i in caps[0]] if caps else [], now), cmd
        now += 1
    return now


# --- mazzo e prese ---------------------------------------------------------------

def test_mazzo_e_valori():
    m = P.mazzo()
    assert len(m) == len(set(m)) == 40
    assert P.nome((8, D)) == "Fante di denari" and P.nome((10, SP)) == "Re di spade"


@pytest.mark.parametrize("table,card,caps", [
    ([(3, D), (4, C_)], (7, SP), [(0, 1)]),                       # somma
    ([(7, D), (3, C_), (4, SP)], (7, BA), [(0,)]),                # stesso valore prima della somma
    ([(7, D), (7, C_), (3, SP), (4, BA)], (7, BA), [(0,), (1,)]),  # due uguali: scelta
    ([(1, D), (2, C_), (3, SP), (4, BA)], (6, D), [(0, 1, 2), (1, 3)]),
    ([(2, D), (5, C_)], (6, D), []),                              # niente
    ([(2, D), (5, C_), (3, SP)], (1, BA), []),                    # niente asso pigliatutto
    ([(1, D), (5, C_)], (1, BA), [(0,)]),                         # l'asso prende l'asso
    ([], (5, D), []),
])
def test_prese_possibili(table, card, caps):
    assert sorted(P.prese_possibili(table, card)) == sorted(caps)


def test_primiera():
    full = [(7, D), (6, C_), (1, SP), (5, BA)]
    assert P.primiera(full) == 21 + 18 + 16 + 15
    assert P.primiera(full + [(2, D), (10, C_)]) == 70, "conta la migliore per seme"
    assert P.primiera([(8, D), (9, C_), (10, SP), (2, BA)]) == 42
    assert P.primiera([(7, D), (7, C_), (7, SP)]) is None, "senza un seme niente primiera"
    assert P.primiera([(r, s) for r in (7, 6, 1, 5, 4, 3, 2, 8, 9, 10) for s in P.tavolo.SEMI]) == 84


def test_solo_pari_e_nessuno():
    assert P.solo({0: 22, 1: 18}) == 0
    assert P.solo({0: 20, 1: 20}) is None
    assert P.solo({0: 5, 1: 5, 2: 0}) is None
    assert P.solo({0: None, 1: 40}) == 1
    assert P.solo({0: None, 1: None}) is None


# --- giocate ------------------------------------------------------------------------

def test_pl_presa_unica_la_fa_il_bot(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    truccata(st, [[(7, SP), (2, D)], [(5, C_)]], [(3, D), (4, C_), (9, BA)])
    g.handle(a, "#t", "pl 1", now=61)
    assert st["prese"][a] == [(3, D), (4, C_), (7, SP)]
    assert st["table"] == [(9, BA)] and g.turn_key(st) == b and st["last"] == a
    assert "prende 3 di denari, 4 di coppe" in chan_lines(g)[-1]
    assert "in tavola: 1) Cavallo di bastoni" in chan_lines(g)[-1]


def test_pl_stesso_valore_ha_la_precedenza(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    truccata(st, [[(7, SP), (2, D)], [(5, C_)]], [(3, D), (4, C_), (7, BA)])
    g.handle(a, "#t", "pl 1", now=61)
    assert st["prese"][a] == [(7, BA), (7, SP)], "il 7 col 7, non 3+4"
    assert st["table"] == [(3, D), (4, C_)]


def test_pl_senza_presa_la_carta_resta(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    truccata(st, [[(9, SP), (2, D)], [(5, C_)]], [(3, D), (4, C_)])
    g.handle(a, BOT, "pl 1", now=61)
    assert st["table"] == [(3, D), (4, C_), (9, SP)] and st["prese"][a] == []
    assert g.turn_key(st) == b and st["last"] is None


def test_pl_ambiguo_elenco_in_query_e_turno_suo(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    truccata(st, [[(5, SP), (6, D), (8, C_)], [(5, C_)]], [(1, D), (2, C_), (3, SP), (4, BA)])
    n = len(g.sent)
    g.handle(a, "#t", "pl 2", now=61)
    assert g.turn_key(st) == a and len(st["hands"][a]) == 3 and len(st["table"]) == 4
    new = g.sent[n:]
    assert len(new) == 1 and new[0][0] == a
    assert new[0][1] == "Col 6 di denari le prese sono piu' d'una: pl 2 2 4 oppure pl 2 1 2 3"
    g.handle(a, "#t", "pl 2 2 4", now=62)
    assert st["prese"][a] == [(2, C_), (4, BA), (6, D)]
    assert st["table"] == [(1, D), (3, SP)] and g.turn_key(st) == b


def test_pl_scelta_in_qualunque_ordine_e_virgole(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    truccata(st, [[(6, D)], [(5, C_)]], [(1, D), (2, C_), (3, SP), (4, BA)], deck=[(9, D)] * 6)
    g.handle(a, BOT, "pl 1 4,2", now=61)
    assert st["prese"][a] == [(2, C_), (4, BA), (6, D)]


def test_pl_due_carte_uguali_si_sceglie(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    truccata(st, [[(7, SP), (2, D)], [(5, C_)]], [(7, D), (7, C_)])
    g.handle(a, "#t", "pl 1", now=61)
    assert g.turn_key(st) == a and "pl 1 1 oppure pl 1 2" in query(g, a)[-1]
    g.handle(a, "#t", "pl 1 2", now=62)
    assert st["prese"][a] == [(7, C_), (7, SP)] and st["table"] == [(7, D)]


@pytest.mark.parametrize("table,cmd,what", [
    ([(3, D), (4, C_), (7, BA)], "pl 1 1 2", "stesso valore"),      # 3+4 ignorando il 7
    ([(3, D), (5, C_), (4, SP)], "pl 1 1 2", "somma non torna"),    # 3+5 != 7
    ([(3, D), (5, C_), (4, SP)], "pl 1 1", "somma non torna"),
    ([(2, D), (9, C_)], "pl 1 1", "non si prende niente"),          # nessuna presa
    ([(3, D), (4, C_)], "pl 1 1 1", "carte da prendere"),           # doppione
    ([(3, D), (4, C_)], "pl 1 1 5", "carte da prendere"),           # fuori tavola
])
def test_pl_scelta_sbagliata_riga_d_uso_turno_suo(tmp_path, table, cmd, what):
    g, st = partita(tmp_path)
    a, b = st["players"]
    truccata(st, [[(7, SP), (2, D)], [(5, C_)]], table)
    g.handle(a, "#t", cmd, now=61)
    assert g.turn_key(st) == a and st["table"] == table and len(st["hands"][a]) == 2
    assert g.sent[-1][0] == a and what in g.sent[-1][1]


def test_pl_fuori_turno_e_fuori_mano(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    st["turn"] = 0
    g.handle(b, "#t", "pl 1", now=61)
    assert g.sent[-1] == (b, f"Non tocca a te: tocca a {a}.")
    g.handle(a, BOT, "pl 4", now=61)
    assert g.sent[-1] == (a, "Hai 3 carte: pl 1-3.")
    n = len(g.sent)
    g.handle("estraneo", "#t", "pl 1 2", now=61)
    g.handle("estraneo", BOT, "pl 1 2", now=61)
    assert len(g.sent) == n, "chi non gioca: silenzio"


def test_notice_mai_risposta_anche_se_ambiguo(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    truccata(st, [[(7, SP), (2, D)], [(5, C_)]], [(7, D), (7, C_)])
    n = len(g.sent)
    g.handle(a, BOT, "pl 1", now=61, notice=True)              # ambigua
    g.handle(a, BOT, "pl 1 1 2", now=61, notice=True)          # sbagliata
    g.handle(b, BOT, "pl 1", now=61, notice=True)              # fuori turno
    assert len(g.sent) == n and g.turn_key(st) == a
    g.handle(a, BOT, "pl 1 1", now=62, notice=True)
    assert st["prese"][a] == [(7, D), (7, SP)]
    assert not [t for to, t in g.sent[n:] if to == a]


# --- scope -----------------------------------------------------------------------

def test_scopa_svuota_la_tavola(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    truccata(st, [[(7, SP), (2, D)], [(5, C_)]], [(3, D), (4, C_)], deck=[(9, D)] * 6)
    g.handle(a, "#t", "pl 1", now=61)
    assert st["scope"][a] == 1 and st["table"] == []
    assert "SCOPA!" in chan_lines(g)[-1] and "tavola vuota" in chan_lines(g)[-1]


def test_ultima_giocata_della_smazzata_non_e_scopa(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    truccata(st, [[], [(7, C_)]], [(7, D)], turn=1)
    g.handle(b, "#t", "pl 1", now=61)
    assert st["scope"][b] == 0
    assert "SCOPA" not in " ".join(chan_lines(g)[-3:])
    assert "scope: nessuna" in " ".join(chan_lines(g))


def test_scopa_a_meta_smazzata_con_mazzo_finito_vale(tmp_path):
    """Mazzo finito ma mani ancora piene: e' scopa, non e' l'ultima giocata."""
    g, st = partita(tmp_path)
    a, b = st["players"]
    truccata(st, [[(7, SP), (2, D)], [(5, C_)]], [(7, D)])
    g.handle(a, "#t", "pl 1", now=61)
    assert st["scope"][a] == 1


def test_avanzi_all_ultimo_che_ha_preso(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    truccata(st, [[(7, SP)], [(2, C_)]], [(7, D), (9, BA), (8, BA)])
    g.handle(a, "#t", "pl 1", now=61)       # a prende il 7
    g.handle(b, "#t", "pl 1", now=62)       # b non prende: fine smazzata
    assert sorted(st["prese"][a]) == sorted([(7, D), (7, SP), (9, BA), (8, BA), (2, C_)])
    assert st["scope"][a] == 0, "gli avanzi non sono scopa"
    assert f"vanno a {a}, ultimo a prendere" in " ".join(chan_lines(g))


def test_nuove_mani_a_mani_finite_tavola_intatta(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    rest = [(r, s) for s in (SP, BA) for r in range(1, 4)]
    truccata(st, [[(9, D)], [(8, C_)]], [(2, D)], deck=rest)
    g.handle(a, "#t", "pl 1", now=61)
    g.handle(b, "#t", "pl 1", now=62)
    assert st["table"] == [(2, D), (9, D), (8, C_)], "in tavola niente di nuovo"
    assert all(len(h) == 3 for h in st["hands"].values()) and st["deck"] == []
    assert g.turn_key(st) == a, "il giro continua"
    assert "3 carte a testa, nel mazzo 0" in chan_lines(g)[-1]
    assert query(g, a)[-1].startswith("Mano: 1) ") and query(g, b)[-1].startswith("Mano: 1) ")


# --- smazzata --------------------------------------------------------------------

@pytest.mark.parametrize("n,deck,giri", [(2, 30, 5), (3, 27, 3), (4, 24, 2)])
def test_distribuzione(tmp_path, n, deck, giri):
    g, st = partita(tmp_path, nicks="abcd"[:n])
    assert all(len(h) == 3 for h in st["hands"].values())
    assert len(st["table"]) == 4 and len(st["deck"]) == deck
    assert deck == giri * 3 * n
    carte = [c for h in st["hands"].values() for c in h] + st["table"] + st["deck"]
    assert len(set(carte)) == 40


@pytest.mark.parametrize("n", [2, 3, 4])
def test_smazzata_intera_conserva_le_carte(tmp_path, n):
    g, st = partita(tmp_path, nicks="abcd"[:n])
    gioca_smazzata(g, st)
    if "#t" in g.games:
        assert st["phase"] == "pausa"
    assert sum(len(p) for p in st["prese"].values()) == 40
    assert "Conto della smazzata 1" in " ".join(chan_lines(g))


class Mescola(random.Random):
    """Il primo mescolamento mette tre Re fra le 4 in tavola."""

    def __init__(self, nplayers):
        super().__init__(7)
        self.calls = 0
        self.n = nplayers

    def shuffle(self, x):
        super().shuffle(x)
        self.calls += 1
        if len(x) == 40 and self.calls == 2:            # il primo mazzo (il primo e' dei posti)
            re_ = [c for c in x if c[0] == 10][:3]
            for c in re_:
                x.remove(c)
            pos = self.n * 3
            x[pos:pos] = re_


@pytest.mark.parametrize("n", [2, 4])
def test_tre_re_in_tavola_si_rimescola(tmp_path, n):
    rng = Mescola(n)
    g, st = partita(tmp_path, nicks="abcd"[:n], rng=rng)
    assert rng.calls >= 3, "rimescolato"
    assert sum(c[0] == 10 for c in st["table"]) < 3
    assert "3 Re in tavola, si rimescola" in " ".join(chan_lines(g))


# --- conto -----------------------------------------------------------------------

def chiudi_con(g, st, prese, scope=None, now=100):
    """Fine smazzata con le prese date, posto per posto."""
    for i, k in enumerate(st["players"]):
        st["prese"][k] = list(prese[i])
        st["scope"][k] = (scope or [0] * len(prese))[i]
    st["table"], st["deck"] = [], []
    st["hands"] = {k: [] for k in st["players"]}
    g.end_round("#t", st, now)


def semi(*ss, ranks=range(1, 11)):
    return [(r, s) for s in ss for r in ranks]


def test_conto_tutto_a_uno(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    chiudi_con(g, st, [P.mazzo(), []], scope=[2, 0])
    assert st["match"] == {0: 6, 1: 0}
    line = " ".join(chan_lines(g))
    assert f"carte a {a} ({a} 40, {b} 0)" in line and f"denari a {a}" in line
    assert f"settebello a {a}" in line and f"scope: {a} 2" in line
    assert f"primiera a {a} ({a} 84, {b} senza un seme)" in line


def test_conto_pari_su_carte_e_denari_nessuno(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    # ognuno 20 carte, 5 denari, stessa primiera; il settebello a b
    x = semi(D, C_, SP, BA, ranks=(1, 2, 3, 4, 5))
    y = [c for c in P.mazzo() if c not in x]
    assert len(x) == len(y) == 20
    assert P.primiera(x) == 16 * 4 and P.primiera(y) == 21 * 4
    chiudi_con(g, st, [x, y])
    line = " ".join(chan_lines(g))
    assert f"carte a nessuno ({a} 20, {b} 20)" in line
    assert f"denari a nessuno ({a} 5, {b} 5)" in line
    assert f"settebello a {b}" in line and f"primiera a {b}" in line
    assert st["match"] == {0: 0, 1: 2}


def test_conto_primiera_pari_nessuno(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    y = [(7, SP), (7, BA), (6, D), (6, C_)]
    x = [c for c in P.mazzo() if c not in y]
    assert P.primiera(x) == P.primiera(y) == 78
    chiudi_con(g, st, [x, y])
    assert f"primiera a nessuno ({a} 78, {b} 78)" in " ".join(chan_lines(g))


def test_conto_in_tre_il_piu_alto_da_solo(tmp_path):
    g, st = partita(tmp_path, nicks="abc")
    x = semi(D) + [(1, C_), (2, C_), (3, C_), (4, C_)]                 # 14 carte, 10 denari
    y = semi(SP) + [(5, C_), (6, C_), (7, C_)]                        # 13
    z = semi(BA) + [(8, C_), (9, C_), (10, C_)]                       # 13
    chiudi_con(g, st, [x, y, z], scope=[0, 1, 1])
    # carte, denari e settebello al primo; primiera: nessuno ha tutti i semi
    p = st["players"]
    line = " ".join(chan_lines(g))
    assert f"carte a {p[0]} ({p[0]} 14, {p[1]} 13, {p[2]} 13)" in line
    assert "primiera a nessuno" in line
    assert st["match"] == {0: 3, 1: 1, 2: 1}


def test_conto_in_tre_pari_in_testa_nessuno(tmp_path):
    g, st = partita(tmp_path, nicks="abc")
    x = semi(D, ranks=range(1, 6)) + semi(C_, ranks=range(1, 10))     # 14 carte, 5 denari
    y = semi(D, ranks=range(6, 11)) + semi(SP, ranks=range(1, 10))    # 14 carte, 5 denari
    z = [c for c in P.mazzo() if c not in x + y]                      # 12
    chiudi_con(g, st, [x, y, z])
    line = " ".join(chan_lines(g))
    assert "carte a nessuno" in line and "denari a nessuno" in line


def test_quattro_coppie_sommano_carte_e_scope(tmp_path):
    g, st = partita(tmp_path, nicks="abcd")
    p = st["players"]
    assert g.team(st, p[0]) == g.team(st, p[2]) != g.team(st, p[1])
    assert any("Coppie:" in t for t in chan_lines(g))
    chiudi_con(g, st, [semi(D), semi(SP), semi(C_), semi(BA)], scope=[1, 0, 2, 0])
    # coppia 0: 20 carte (pari a 20: nessuno), denari, settebello, scope 1+2;
    # primiera: a nessuna coppia ci sono tutti i semi
    line = " ".join(chan_lines(g))
    assert "carte a nessuno" in line and "primiera a nessuno" in line
    assert st["match"] == {0: 1 + 1 + 3, 1: 0}
    assert f"scope: {g.team_name(st, 0)} 3" in line


def test_coppie_a_sorte(tmp_path):
    seen = set()
    for seed in range(12):
        d = tmp_path / str(seed)
        d.mkdir()
        g, st = partita(d, nicks="abcd", seed=seed)
        seen.add(frozenset(g.members(st, g.team(st, "a"))))
    assert len(seen) > 1


# --- partita a 11 / 21 -----------------------------------------------------------

def test_11_chi_arriva_vince(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    st["match"] = {0: 6, 1: 3}
    chiudi_con(g, st, [P.mazzo(), []], scope=[1, 0])               # +5 -> 11
    assert "#t" not in g.games
    assert g.scores == {a: {"nick": a, "wins": 1}}
    assert f"🏆 {a} vince la partita, 11 a 3!" in chan_lines(g)[-1]


def test_sotto_soglia_pausa_e_smazzata_dopo(tmp_path):
    g, st = partita(tmp_path)
    dealer = st["dealer"]
    chiudi_con(g, st, [P.mazzo(), []])
    assert st["phase"] == "pausa" and "Prossima smazzata" in chan_lines(g)[-1]
    g.tick(now=100 + P.PAUSA_S)
    assert st["phase"] == "play" and st["mano_no"] == 2
    assert st["dealer"] == (dealer + 1) % 2, "il mazzo gira"
    assert st["turn"] == (st["dealer"] + 1) % 2
    assert st["match"] == {0: 4, 1: 0}, "i punti restano"


def test_tutti_oltre_vince_il_piu_alto(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    st["match"] = {0: 9, 1: 10}
    chiudi_con(g, st, [semi(D, C_), semi(SP, BA)], scope=[1, 3])
    # a: denari, settebello, 1 scopa = 12; b: 3 scope = 13. Carte pari, primiera nessuno
    assert st["match"] == {0: 12, 1: 13} and "#t" not in g.games
    assert g.scores.keys() == {b}


def test_pari_in_testa_oltre_la_soglia_altra_smazzata(tmp_path):
    g, st = partita(tmp_path)
    st["match"] = {0: 10, 1: 10}
    chiudi_con(g, st, [semi(D, C_), semi(SP, BA)], scope=[0, 2])    # +2 / +2
    assert st["match"] == {0: 12, 1: 12}
    assert "#t" in g.games and st["phase"] == "pausa" and g.scores == {}
    assert "Pari in testa a 12: un'altra smazzata." in " ".join(chan_lines(g))


def test_in_tre_pari_in_testa_e_uno_sotto(tmp_path):
    g, st = partita(tmp_path, nicks="abc")
    st["match"] = {0: 11, 1: 11, 2: 3}
    chiudi_con(g, st, [[], [], []])
    assert "#t" in g.games and st["phase"] == "pausa"
    st["match"] = {0: 11, 1: 11, 2: 12}
    chiudi_con(g, st, [[], [], []])
    assert "#t" not in g.games and set(g.scores) == {st["players"][2]}


def test_a_21(tmp_path):
    g, st = partita(tmp_path, arg="21")
    assert st["target"] == 21 and "partita a 21" in chan_lines(g)[0]
    st["match"] = {0: 15, 1: 0}
    chiudi_con(g, st, [P.mazzo(), []])                               # 20
    assert "#t" in g.games
    st["match"] = {0: 18, 1: 0}
    chiudi_con(g, st, [P.mazzo(), []])                               # 23
    assert "#t" not in g.games


@pytest.mark.parametrize("cmd,target", [("!scopa", 11), ("!SCOPA 21", 21), ("!scopa 11", 11),
                                        ("!scopa start 21", 21), ("!scopa start", 11)])
def test_soglie_valide(tmp_path, cmd, target):
    g = nuovo(tmp_path)
    g.handle("a", "#t", cmd, now=0)
    assert g.games["#t"]["target"] == target


@pytest.mark.parametrize("cmd", ["!scopa 3", "!scopa 5", "!scopa 15", "!scopa start 7",
                                 "!scopa boh", "!scopa start boh"])
def test_altri_argomenti_riga_d_uso(tmp_path, cmd):
    g = nuovo(tmp_path)
    g.handle("a", "#t", cmd, now=0)
    assert "#t" not in g.games and chan_lines(g)[-1] == P.HELP


@pytest.mark.parametrize("cmd", ["!sco", "!sc", "!s", "!scopone", "!scopa21"])
def test_niente_alias_corto(tmp_path, cmd):
    g = nuovo(tmp_path)
    g.handle("a", "#t", cmd, now=0)
    assert "#t" not in g.games and g.sent == []


# --- lobby e comandi -------------------------------------------------------------

def test_quinto_jo_rifiutato(tmp_path):
    g = nuovo(tmp_path)
    g.handle("a", "#t", "!scopa", now=0)
    for n in "bcd":
        g.handle(n, "#t", "jo", now=1)
    g.handle("e", "#t", "jo", now=2)
    assert g.games["#t"]["players"] == ["a", "b", "c", "d"]
    assert chan_lines(g)[-1].startswith("e: tavolo pieno")


def test_uno_solo_non_parte(tmp_path):
    g = nuovo(tmp_path)
    g.handle("a", "#t", "!scopa", now=0)
    g.tick(now=P.LOBBY_S)
    assert "#t" not in g.games and "Chiuso" in chan_lines(g)[-1]


def test_stato_tavola_scope_e_punti_mai_le_carte(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    truccata(st, [[(7, SP), (2, D)], [(5, C_)]], [(3, D), (4, C_)], deck=[(9, D)] * 6)
    g.handle(a, "#t", "pl 1", now=61)
    st["match"] = {0: 4, 1: 2}
    g.handle("x", "#t", "!scopa stato", now=62)
    line = " ".join(chan_lines(g)[-1:])
    assert "tavola vuota" in line and "nel mazzo 6" in line and f"tocca a {b}" in line
    assert f"scope: {a} 1" in line and f"partita a 11: {a} 4, {b} 2" in line
    assert not re.search(r"\bprese\b|carte prese|\d+ carte", line)


def test_stato_in_lobby_e_in_pausa(tmp_path):
    g = nuovo(tmp_path)
    g.handle("a", "#t", "!scopa 21", now=0)
    g.handle("a", "#t", "!scopa stato", now=10)
    assert "Tavolo aperto, partita a 21: a; 50s." in chan_lines(g)[-1]
    (tmp_path / "x").mkdir()
    g, st = partita(tmp_path / "x")
    chiudi_con(g, st, [[], []])
    g.handle("a", "#t", "!scopa stato", now=103)
    assert "Prossima smazzata fra 7s" in chan_lines(g)[-1]


def test_carte_rimanda_la_mano(tmp_path):
    g, st = partita(tmp_path)
    n = len(g.sent)
    g.handle("a", "#t", "!scopa carte", now=61)
    new = g.sent[n:]
    assert len(new) == 1 and new[0][0] == "a"
    assert re.fullmatch(r"Mano: 1\) .+  2\) .+  3\) .+", new[0][1])


def test_top_e_aiuto(tmp_path):
    g = nuovo(tmp_path)
    g.handle("a", "#t", "!scopa top", now=0)
    assert chan_lines(g)[-1] == "🏆 [Scopa] Partite vinte (test): nessuna"
    g.handle("a", "#t", "!scopa stato", now=0)
    assert "!scopa per aprire" in chan_lines(g)[-1]


def test_stop_solo_chi_apre_o_op(tmp_path):
    g, st = partita(tmp_path, nicks=("a", "b"))
    g.handle("b", "#t", "!scopa stop", now=61)
    assert "#t" in g.games
    g.handle("a", "#t", "!scopa stop", now=62)
    assert "#t" not in g.games and g.scores == {}


def test_ops_canale_guardato(tmp_path):
    g = P.Scopa("test", "/dev/null", "", tmp_path / "s.json", {"#i"},
                rng=random.Random(1), opts={"#i": {"ops": True}},
                trust=(("vjt", "*.openssl.it"),))
    g.handle("a", "#i", "!scopa", now=0, host="x.example")
    assert "#i" not in g.games and "solo un op" in chan_lines(g, "#i")[-1]
    g.handle("vjt", "#i", "!scopa", now=1, host="nowhere.openssl.it")
    assert "#i" in g.games


def test_load_nets(monkeypatch):
    monkeypatch.setenv("SCOPA_NETS", "azzurra|/l|/f|#sbiffo,#Bot")
    cfg = P.load_nets()["azzurra"]
    assert cfg["chans"] == {"#sbiffo", "#bot"} and str(cfg["state"]).endswith("scopa_state.json")


# --- timeout ---------------------------------------------------------------------

def test_timeout_carta_che_non_prende_e_vale_meno(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    truccata(st, [[(9, SP), (2, C_), (2, D), (7, BA)], [(5, C_)]], [(5, D), (4, SP)])
    st["deadline"] = 61
    g.tick(now=61)
    # il 9 prende 5+4, il 7 non prende... ne' il 2: il 2 di coppe (non denari)
    assert st["table"][-1] == (2, C_) and st["prese"][a] == []
    assert "il bot cala" in chan_lines(g)[-1]
    assert g.ison and g.ison[0] == {a: "#t"}


def test_timeout_tutte_prendono_quella_che_prende_di_piu(tmp_path):
    # l'asso non piglia tutto: e' la carta libera che vale meno
    hand = [(1, D), (2, C_), (3, SP)]
    assert P.scarto(table=[(3, BA), (6, D)], hand=hand) == ((1, D), ())
    # tutte prendono: il 6 ne prende tre
    card, cap = P.scarto(table=[(1, D), (2, C_), (3, BA), (4, SP)], hand=[(3, SP), (6, C_)])
    assert card == (6, C_) and cap == (0, 1, 2)
    # stessa quantita': quella che prende denari
    card, cap = P.scarto(table=[(2, D), (3, C_)], hand=[(2, C_), (3, D)])
    assert card == (2, C_) and cap == (0,)
    # a parita' di valore fra le libere, non quella di denari
    assert P.scarto(table=[(9, BA)], hand=[(2, D), (2, SP)])[0] == (2, SP)


def test_timeout_autoplay_con_presa(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    truccata(st, [[(6, C_)], [(5, C_)]], [(1, D), (2, C_), (3, SP), (4, BA)], deck=[(9, D)] * 4)
    st["deadline"] = 61
    g.tick(now=61)
    assert len(st["prese"][a]) == 4 and st["table"] == [(4, BA)]


def test_due_timeout_di_fila_abbandono(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    st["turn"] = 0
    now = 60
    for _ in range(10):
        if "#t" not in g.games:
            break
        if g.turn_key(st) == b:
            k = st["hands"][b][0]
            caps = P.prese_possibili(st["table"], k)
            g.play("#t", st, b, 1, [i + 1 for i in caps[0]] if caps else [], now)
        else:
            now += P.TURN_S
            g.tick(now=now)
    assert "#t" not in g.games and "2 turni saltati" in " ".join(chan_lines(g))
    assert g.scores == {b: {"nick": b, "wins": 1}}, "dopo il primo giro conta"


def test_ison_offline_abbandona(tmp_path):
    g, st = partita(tmp_path)
    st["turn"] = 0
    g.tick(now=60 + P.TURN_S)
    g.track(L(":srv 303 Mike :"), now=151)
    assert "#t" not in g.games


# --- abbandono, nick, split --------------------------------------------------------

def test_abbandono_prima_del_primo_giro_non_conta(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    g.handle(a, "#t", "!scopa esci", now=61)
    assert "#t" not in g.games and g.scores == {}
    assert f"{b} vince la partita" in " ".join(chan_lines(g))


def test_abbandono_fra_due_smazzate_vince_l_altro(tmp_path):
    """Fra due smazzate e' sempre la stessa partita: niente «serie interrotta»."""
    g, st = partita(tmp_path)
    a, b = st["players"]
    chiudi_con(g, st, [P.mazzo(), []])
    st["giocate"] = 10
    g.track(L(f":{a}!x@h PART #t"), now=101)
    assert "#t" not in g.games and g.scores == {b: {"nick": b, "wins": 1}}
    assert f"partita a 11: {a} 4, {b} 0" in " ".join(chan_lines(g))


def test_abbandono_in_quattro_vince_la_coppia(tmp_path):
    g, st = partita(tmp_path, nicks="abcd")
    p = st["players"]
    st["giocate"] = 4
    g.track(L(f":{p[1]}!x@h QUIT :Quit: bye"), now=70)
    assert "#t" not in g.games and set(g.scores) == {p[0], p[2]}


def test_abbandono_in_tre_vince_chi_ha_piu_punti(tmp_path):
    g, st = partita(tmp_path, nicks="abc")
    p = st["players"]
    st["giocate"] = 3
    st["match"] = {0: 2, 1: 5, 2: 1}
    g.track(L(f":op!x@h KICK #t {p[2]} :via"), now=61)
    assert set(g.scores) == {p[1]}
    (tmp_path / "x").mkdir()
    g, st = partita(tmp_path / "x", nicks="abc")
    st["giocate"] = 3
    st["match"] = {0: 4, 1: 4, 2: 1}
    g.track(L(f":{st['players'][2]}!x@h PART #t"), now=61)
    assert "#t" not in g.games and g.scores == {}
    assert "Nessun vincitore" in chan_lines(g)[-1]


def test_cambio_nick(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    truccata(st, [[(7, SP), (2, D)], [(5, C_)]], [(3, D), (4, C_), (9, BA)], deck=[(9, D)] * 6)
    g.handle(a, "#t", "pl 1", now=61)
    g.track(L(f":{a}!x@h NICK :Anna"), now=62)
    assert st["players"][0] == "anna" and st["last"] == "anna"
    assert st["prese"]["anna"] == [(3, D), (4, C_), (7, SP)] and "anna" in st["scope"]
    g.handle(b, "#t", "pl 1", now=63)
    g.handle("Anna", BOT, "pl 1", now=64)
    assert st["giocate"] == 3 and g.turn_key(st) == b


def test_netsplit_non_e_abbandono(tmp_path):
    g, st = partita(tmp_path)
    a, b = st["players"]
    st["turn"] = 0
    g.track(L(f":{a}!x@h QUIT :irc.azzurra.chat hub.azzurra.chat"), now=61)
    assert "#t" in g.games and a in st["split"]
    g.tick(now=60 + P.TURN_S)
    assert len(st["hands"][a]) == 2 and not g.ison, "gioca il bot, niente ISON"


def test_404_muto_in_pausa_riprende_con_la_smazzata(tmp_path):
    g, st = partita(tmp_path)
    chiudi_con(g, st, [[], []])
    g.mute("#t", st, 102)
    assert st["phase"] == "muto" and st["was"] == "pausa"
    g.tick(now=102 + P.LOBBY_S)
    assert st["phase"] == "play" and st["mano_no"] == 2


# --- convivenza con gli altri giochi ------------------------------------------------

def test_scopa_e_briscola_si_escludono(tmp_path):
    locks = str(tmp_path / "locks")
    sco = P.Scopa("test", "/dev/null", "", tmp_path / "s.json", {"#t"},
                  rng=random.Random(1), trust=(), locks=locks)
    bri = B.Briscola("test", "/dev/null", "", tmp_path / "b.json", {"#t"},
                     rng=random.Random(1), trust=(), locks=locks)
    sco.handle("a", "#t", "!scopa", now=0)
    bri.handle("b", "#t", "!bri", now=0)
    assert "#t" not in bri.games and chan_lines(bri)[-1] == G.busy_line("Scopa")
    sco.handle("a", "#t", "!scopa stop", now=1)
    sco.tick(now=1)
    bri.handle("b", "#t", "!bri", now=2)
    sco.handle("a", "#t", "!scopa", now=3)
    assert "#t" not in sco.games and chan_lines(sco)[-1] == G.busy_line("Briscola")
    bri.lock.sync({})


@pytest.mark.parametrize("text", ["pl 3 1 4", "PL 2 1", "pl 1 2,3"])
def test_gli_altri_giochi_ignorano_pl_con_la_presa(tmp_path, text):
    diz = S.Dizionario(["riso"])
    sil = S.Sillabah("test", "/dev/null", "", tmp_path / "si.json", {"#t"}, diz,
                     talk=False, rng=random.Random(1))
    assert sil.handle("a", BOT, text, now=1) is False
    assert sil.handle("a", BOT, text, now=1, notice=True) is False
    assert sil.sent == [] and sil.notices == []
    cah = C.Cah("test", "/dev/null", "", tmp_path / "c.json", {"#t"},
                (["n ___"], [f"w{i}" for i in range(40)]), rng=random.Random(1), trust=())
    bri = B.Briscola("test", "/dev/null", "", tmp_path / "b.json", {"#t"},
                     rng=random.Random(1), trust=())
    for gm in (cah, bri):
        gm.handle("a", BOT, text, now=1)
        gm.handle("a", BOT, text, now=1, notice=True)
        gm.handle("a", "#t", text, now=1)
        assert gm.sent == []


def test_riavvio_perde_la_partita_tiene_la_classifica(tmp_path):
    g, st = partita(tmp_path)
    st["match"] = {0: 10, 1: 0}
    chiudi_con(g, st, [P.mazzo(), []])
    g.save()
    assert json.loads((tmp_path / "s.json").read_text())["scores"]
    g2 = nuovo(tmp_path)
    assert g2.games == {} and g2.scores == g.scores


# --- righe ------------------------------------------------------------------------

@pytest.mark.parametrize("n", [2, 3, 4])
def test_righe_sotto_i_400_byte(tmp_path, n):
    nicks = [("Nick" + c) * 7 for c in "ABCD"[:n]]
    g, st = partita(tmp_path, nicks=nicks, arg="21")
    g.handle(nicks[0], "#t", "!scopa stato", now=60)
    now = gioca_smazzata(g, st)
    g.handle(nicks[0], "#t", "!scopa stato", now=now)
    # tavola lunga e prese ambigue
    st["phase"] = "play"
    truccata(st, [[(10, D)]] + [[(9, D)]] * (n - 1),
             [(1, C_), (2, C_), (3, C_), (4, C_), (5, C_), (6, C_), (7, C_), (1, SP), (2, SP),
              (3, SP), (4, SP)])
    g.handle(nicks[0], BOT, "pl 1", now=now)
    g.handle(nicks[0], "#t", "!scopa stato", now=now)
    assert len(g.sent) > 20
    for to, text in g.sent:
        assert len(f"PRIVMSG {to} :{text}".encode()) < 400, text


def test_righe_pulite():
    src = open(P.__file__, encoding="utf-8").read()
    assert not re.search(r"(?i)\b(dio|madonna|cristo|porco|porca|cazz|stronz)", src)
