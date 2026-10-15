"""cah: la meccanica di Cards Against Humanity, un test per caso limite di
docs/cah_spec.md (il numero nel nome). Niente FIFO e niente IRC: fifo vuota,
`sent` raccoglie quello che sarebbe uscito."""
import json
import os
import random
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cah as C  # noqa: E402
import ircgame as G  # noqa: E402

BOT = "MikeBongiorno"
NERE = ["Uno ___.", "Due ___ e ___.", "Senza buchi"]
BIANCHE = [f"bianca {i}" for i in range(60)]


def L(s):
    return f"12:00:00 < {s}"


def nuovo(tmp_path, nere=NERE, bianche=BIANCHE, seed=1):
    return C.Cah("test", "/dev/null", "", tmp_path / "s.json", {"#t"},
                 (list(nere), list(bianche)), rng=random.Random(seed))


def partita(tmp_path, nicks=("a", "b", "c", "d"), **kw):
    """Tavolo aperto, giocatori seduti, prima mano partita a now=60."""
    g = nuovo(tmp_path, **kw)
    g.handle(nicks[0], "#t", "!cah", now=0)
    for n in nicks[1:]:
        g.handle(n, "#t", "!cah entra", now=1)
    g.tick(now=C.LOBBY_S)
    assert g.games["#t"]["phase"] == "play"
    return g, g.games["#t"]


def gioca_tutti(g, st, now, chan="#t"):
    czar = g.czar(st)
    for k in list(st["players"]):
        if k != czar:
            nums = " ".join(str(i) for i in range(1, st["picks"] + 1))
            g.handle(st["nicks"][k], chan, f"!play {nums}", now=now)


def chan_lines(g, chan="#t"):
    return [t for (to, t) in g.sent if to == chan]


# --- base -------------------------------------------------------------------

def test_carte_vere():
    nere, bianche = C.load_carte()
    for pack, nn, nb in (("ita", 90, 458), ("cah42", 71, 219), ("vjt-claude", 82, 335)):
        assert sum(c["pack"] == pack for c in nere) == nn, pack
        assert sum(c["pack"] == pack for c in bianche) == nb, pack
    for pack in ("ita", "cah42", "vjt-claude"):
        testi = [c["testo"] for c in bianche if c["pack"] == pack]
        assert len(set(testi)) == len(testi), pack
    assert all(c["testo"].strip() and "\\" not in c["testo"] for c in nere + bianche)
    assert all(c["pick"] == C.picks(c["testo"]) and 1 <= c["pick"] <= 3 for c in nere)
    assert all(len(c["testo"].encode()) < 300 for c in nere + bianche)


def test_carte_formato_vecchio(tmp_path):
    """Stringhe nude (il json di prima): pack LEGACY_PACK, pick dai buchi."""
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"nere": ["Due ___ e ___."],
                             "bianche": ["x", {"testo": "y", "pack": "z"}]}))
    nere, bianche = C.load_carte(p)
    assert nere == [{"testo": "Due ___ e ___.", "pack": C.LEGACY_PACK, "pick": 2,
                     "vm18": False}]
    assert [c["pack"] for c in bianche] == [C.LEGACY_PACK, "z"]


def test_pack_per_canale(tmp_path):
    nere = [{"testo": f"n{p} ___", "pack": p, "pick": 1} for p in "ab"]
    bianche = [{"testo": f"w{p}{i}", "pack": p} for p in "ab" for i in range(40)]
    g = C.Cah("test", "/dev/null", "", tmp_path / "s.json",
              {"#a": ("a",), "#ab": ("a", "b")}, (nere, bianche), rng=random.Random(1))
    assert g.decks["#a"] == (["na ___"], [f"wa{i}" for i in range(40)])
    assert len(g.decks["#ab"][1]) == 80
    g.handle("x", "#a", "!cah", now=0)
    for n in "yz":
        g.handle(n, "#a", "!cah entra", now=1)
    g.tick(now=C.LOBBY_S)
    st = g.games["#a"]
    assert st["black"] == "na ___"
    assert all(c.startswith("wa") for h in st["hands"].values() for c in h)


def test_pack_sconosciuto_o_vuoto(tmp_path):
    nere, bianche = [{"testo": "n ___", "pack": "a"}], [{"testo": "w", "pack": "a"}]
    with pytest.raises(SystemExit, match="sconosciuti"):
        C.Cah("t", "/dev/null", "", tmp_path / "s.json", {"#a": ("nope",)}, (nere, bianche))
    with pytest.raises(SystemExit, match="nessuna carta"):
        C.Cah("t", "/dev/null", "", tmp_path / "s.json", {"#a": ("a",)}, (nere, []))


def test_load_nets_pack(monkeypatch):
    monkeypatch.setenv("CAH_NETS", "azzurra|/l|/f|#sbiffo,#It-Opers:vjt-claude+ita")
    monkeypatch.delenv("CAH_PACKS", raising=False)
    chans = C.load_nets()["azzurra"]["chans"]
    assert chans == {"#sbiffo": C.DEFAULT_PACKS, "#it-opers": ("vjt-claude", "ita")}
    monkeypatch.setenv("CAH_PACKS", "cah42")
    assert C.load_nets()["azzurra"]["chans"]["#sbiffo"] == ("cah42",)


def test_pick_tre(tmp_path):
    g, st = partita(tmp_path, nere=["___ + ___ = ___"])
    assert st["picks"] == 3
    gioca_tutti(g, st, now=61)
    assert st["phase"] == "judge"


def test_lobby_needs_three(tmp_path):
    g = nuovo(tmp_path)
    g.handle("a", "#t", "!cah", now=0)
    g.handle("b", "#t", "!cah join", now=1)
    g.tick(now=C.LOBBY_S)
    assert "#t" not in g.games
    assert "Servono" in chan_lines(g)[-1]


def test_round_flow_and_winner(tmp_path):
    g, st = partita(tmp_path)
    assert g.czar(st) == "a"
    for k in ("b", "c", "d"):
        assert len(st["hands"][k]) == C.HAND
        assert any(to == k for to, _ in g.sent), "mano in privato"
    assert not any(to == "a" for to, _ in g.sent), "lo czar non riceve la mano"
    gioca_tutti(g, st, now=61)
    assert st["phase"] == "judge"
    g.handle("a", "#t", "!vince 1", now=62)
    w = [k for k in st["scores"]]
    assert len(w) == 1 and g.scores[w[0]]["points"] == 1
    assert "vince la mano" in chan_lines(g)[-1]
    g.tick(now=62 + C.PAUSA_S)
    assert g.czar(st) == "b" and st["phase"] == "play"
    # mani rifornite
    assert all(len(st["hands"][k]) == C.HAND for k in st["players"])


def test_goal_ends_game_and_scores_persist(tmp_path):
    g, st = partita(tmp_path)
    st["goal"] = 1
    gioca_tutti(g, st, now=61)
    g.handle("a", "#t", "!winner 1", now=62)
    assert "#t" not in g.games
    g.save()
    assert json.loads((tmp_path / "s.json").read_text())["scores"]


def test_stop_only_starter_or_op(tmp_path):
    g, st = partita(tmp_path)
    g.handle("b", "#t", "!cah stop", now=61)
    assert "#t" in g.games
    g.track(L(":srv 353 Mike = #t :@b c"))
    g.track(L(":srv 366 Mike #t :End"))
    g.handle("b", "#t", "!cah stop", now=62)
    assert "#t" not in g.games


# --- i casi di docs/cah_spec.md -------------------------------------------------

def test_01_czar_leaves_round_void_cards_back(tmp_path):
    g, st = partita(tmp_path)
    before = list(st["hands"]["b"])
    g.handle("b", "#t", "!play 1", now=61)
    g.track(L(":a!x@h PART #t :ciao"), now=62)
    assert "a" not in st["players"]
    assert sorted(st["hands"]["b"]) == sorted(before), "carta resa"
    assert st["phase"] == "play" and g.czar(st) == "b"


def test_01_czar_quit_and_kick(tmp_path):
    g, st = partita(tmp_path, nicks=("a", "b", "c", "d", "e"))
    g.track(L(":a!x@h QUIT :Quit: bye"), now=61)
    assert "a" not in st["players"] and g.czar(st) == "b"
    g.track(L(":op!x@h KICK #t b :via"), now=62)
    assert "b" not in st["players"] and g.czar(st) == "c"


def test_02_player_leaves_submission_dropped(tmp_path):
    g, st = partita(tmp_path)
    g.handle("b", "#t", "!play 1", now=61)
    g.handle("b", "#t", "!cah esci", now=62)
    assert "b" not in st["players"] and "b" not in st["subs"]
    assert st["phase"] == "play"


def test_02_under_three_pauses_then_ends(tmp_path):
    g, st = partita(tmp_path, nicks=("a", "b", "c"))
    g.handle("c", "#t", "!play 1", now=61)
    g.track(L(":b!x@h PART #t"), now=62)
    assert st["phase"] == "attesa"
    assert st["subs"] == {} and len(st["hands"]["c"]) == C.HAND, "carte rese"
    g.tick(now=62 + C.LOBBY_S)
    assert "#t" not in g.games
    assert "Punti" in chan_lines(g)[-1]


def test_02_pause_resumes_on_join(tmp_path):
    g, st = partita(tmp_path, nicks=("a", "b", "c"))
    g.track(L(":b!x@h PART #t"), now=62)
    g.handle("z", "#t", "!cah entra", now=63)
    assert st["phase"] == "play" and "z" in st["players"]


def test_03_nick_change_follows(tmp_path):
    g, st = partita(tmp_path)
    hand = list(st["hands"]["b"])
    st["scores"]["b"] = 2
    g.track(L(":a!x@h NICK :Anna"), now=61)
    g.track(L(":b!x@h NICK :Bruno"), now=61)
    assert g.czar(st) == "anna" and st["nicks"]["anna"] == "Anna"
    assert st["hands"]["bruno"] == hand and st["scores"]["bruno"] == 2
    g.handle("Bruno", "#t", "!play 1", now=62)
    assert "bruno" in st["subs"]


def test_04_timeout_reveals_with_what_is_in(tmp_path):
    g, st = partita(tmp_path)
    g.handle("b", "#t", "!play 1", now=61)
    g.handle("c", "#t", "!play 1", now=61)
    g.tick(now=60 + C.PLAY_S)
    assert st["phase"] == "judge" and len(st["order"]) == 2
    assert st["missed"]["d"] == 1


def test_04_under_two_subs_void(tmp_path):
    g, st = partita(tmp_path)
    before = list(st["hands"]["b"])
    g.handle("b", "#t", "!play 1", now=61)
    g.tick(now=60 + C.PLAY_S)
    assert st["phase"] == "play" and st["round"] == 2
    assert sorted(st["hands"]["b"]) == sorted(before)


def test_04_two_missed_rounds_removed(tmp_path):
    g, st = partita(tmp_path, nicks=("a", "b", "c", "d", "e"))
    now = 60
    for _ in range(2):
        for k in list(st["players"]):
            if k not in (g.czar(st), "e"):
                g.handle(k, "#t", "!play 1", now=now + 1)
        g.tick(now=now + C.PLAY_S)
        now += C.PLAY_S
        assert st["phase"] == "judge"
        g.handle(st["nicks"][g.czar(st)], "#t", "!vince 1", now=now)
        g.tick(now=now + C.PAUSA_S)
        now += C.PAUSA_S
    assert "e" not in st["players"]


def test_04_ison_removes_offline_now(tmp_path):
    g, st = partita(tmp_path)
    g.handle("b", "#t", "!play 1", now=61)
    g.handle("c", "#t", "!play 1", now=61)
    g.tick(now=60 + C.PLAY_S)
    assert g.ison and g.ison[0] == {"d": "#t"}
    g.track(L(":srv 303 Mike :"), now=151)       # d non e' online
    assert "d" not in st["players"]
    assert not g.ison


def test_04_ison_online_stays(tmp_path):
    g, st = partita(tmp_path)
    g.handle("b", "#t", "!play 1", now=61)
    g.handle("c", "#t", "!play 1", now=61)
    g.tick(now=60 + C.PLAY_S)
    g.track(L(":srv 303 Mike :d"), now=151)
    assert "d" in st["players"]


def test_04_idle_rounds_close(tmp_path):
    g, st = partita(tmp_path)
    g.tick(now=60 + C.PLAY_S)
    g.tick(now=60 + 2 * C.PLAY_S)
    assert "#t" not in g.games


def test_05_czar_timeout_no_point(tmp_path):
    g, st = partita(tmp_path)
    gioca_tutti(g, st, now=61)
    g.tick(now=61 + C.JUDGE_S)
    assert st["scores"] == {} and st["phase"] == "play" and g.czar(st) == "b"
    assert g.scores == {}


def test_06_pick_two_validation(tmp_path):
    g, st = partita(tmp_path, nere=["Due ___ e ___."])
    assert st["picks"] == 2
    for bad in ("!play 1", "!play 1 1", "!play 1 9", "!play 1 2 3"):
        g.handle("b", BOT, bad, now=61)
        assert "b" not in st["subs"]
        assert g.sent[-1][0] == "b" and "Servono 2" in g.sent[-1][1]
    g.handle("b", BOT, "!play 2 1", now=62)
    assert len(st["subs"]["b"]) == 2


def test_06_czar_ignored_and_resubmit(tmp_path):
    g, st = partita(tmp_path)
    n = len(g.sent)
    g.handle("a", "#t", "!play 1", now=61)
    assert "a" not in st["subs"] and len(g.sent) == n
    first = st["hands"]["b"][0]
    g.handle("b", "#t", "!play 1", now=61)
    assert st["subs"]["b"] == [first]
    g.handle("b", BOT, "!play 2", now=62)
    assert st["subs"]["b"] != [first] and first in st["hands"]["b"]
    assert len(st["hands"]["b"]) == C.HAND - 1


def test_07_deck_reshuffle_excludes_hands(tmp_path):
    g, st = partita(tmp_path, bianche=[f"w{i}" for i in range(30)])
    now = 60
    for _ in range(6):
        gioca_tutti(g, st, now=now + 1)
        g.handle(st["nicks"][g.czar(st)], "#t", "!vince 1", now=now + 2)
        if "#t" not in g.games:
            break
        g.tick(now=now + 2 + C.PAUSA_S)
        now += 10
        cards = [c for h in st["hands"].values() for c in h]
        assert len(cards) == len(set(cards)), "nessuna carta in due mani"


def test_08_hand_lines_short(tmp_path):
    lunghe = [("lunga " * 12) + str(i) for i in range(40)]
    g, st = partita(tmp_path, bianche=lunghe)
    per = {}
    for to, text in g.sent:
        assert len(f"PRIVMSG {to} :{text}".encode()) < 400
        if to != "#t":
            per[to] = per.get(to, 0) + 1
    assert per and all(n <= 2 for n in per.values())


def test_08_outbox_rate_limited(tmp_path):
    g, st = partita(tmp_path, nicks=("a", "b", "c", "d", "e", "f"))
    assert len(g.out.sent) == 0
    g.out.flush(now=0)
    assert len(g.out.sent) == G.Outbox(None, "x", None).burst


def test_09_join_mid_round_next_round(tmp_path):
    g, st = partita(tmp_path)
    g.handle("z", "#t", "!cah entra", now=61)
    assert "z" in st["pending"] and "z" not in st["hands"]
    g.handle("z", BOT, "!play 1", now=62)
    assert "z" not in st["subs"]
    gioca_tutti(g, st, now=63)
    g.handle("a", "#t", "!vince 1", now=64)
    g.tick(now=64 + C.PAUSA_S)
    assert "z" in st["players"] and len(st["hands"]["z"]) == C.HAND


def test_10_one_game_per_nick(tmp_path):
    g = C.Cah("test", "/dev/null", "", tmp_path / "s.json", {"#t", "#u"},
              (NERE, BIANCHE), rng=random.Random(1))
    g.handle("a", "#t", "!cah", now=0)
    g.handle("a", "#u", "!cah", now=0)
    assert "#u" not in g.games
    g.handle("b", "#u", "!cah", now=0)
    g.handle("a", "#u", "!cah entra", now=1)
    assert "a" not in g.games["#u"]["players"]


def test_11_netsplit_absent_not_removed(tmp_path):
    g, st = partita(tmp_path)
    g.handle("b", "#t", "!play 1", now=61)
    g.handle("c", "#t", "!play 1", now=61)
    g.track(L(":d!x@h QUIT :irc.azzurra.chat hub.azzurra.chat"), now=62)
    assert "d" in st["players"]
    assert st["phase"] == "judge", "non si aspetta chi e' nello split"
    assert "d" not in st["missed"]
    g.handle("a", "#t", "!vince 1", now=63)
    g.tick(now=63 + C.PAUSA_S)
    assert st["absent"] == [], "assente per una mano sola"


def test_11_netsplit_czar_voids(tmp_path):
    g, st = partita(tmp_path)
    g.track(L(":a!x@h QUIT :irc.azzurra.chat hub.azzurra.chat"), now=62)
    assert "a" in st["players"] and g.czar(st) == "b" and st["round"] == 2


def test_12_restart_loses_game_keeps_scores(tmp_path):
    g, st = partita(tmp_path)
    gioca_tutti(g, st, now=61)
    g.handle("a", "#t", "!vince 1", now=62)
    g.save()
    g2 = nuovo(tmp_path)
    assert g2.games == {} and g2.scores == g.scores
    assert g2.sent == []


def test_13_reveal_anonymous_shuffled(tmp_path):
    g, st = partita(tmp_path, nicks=("a", "bob", "carla", "dino", "elena"))
    n = len(g.sent)
    gioca_tutti(g, st, now=61)
    reveal = " ".join(t for to, t in g.sent[n:] if to == "#t")
    for nick in ("bob", "carla", "dino", "elena"):
        assert nick not in reveal
    orders = set()
    for seed in range(6):
        d = tmp_path / str(seed)
        d.mkdir()
        g2, st2 = partita(d, nicks=("a", "bob", "carla", "dino", "elena"), seed=seed)
        gioca_tutti(g2, st2, now=61)
        orders.add(tuple(st2["order"]))
    assert len(orders) > 1
    g.handle("a", "#t", "!vince 1", now=62)
    assert st["nicks"][st["order"][0]] in chan_lines(g)[-1]


# --- +c, +m/+M ------------------------------------------------------------------

def test_plus_c_between_rounds(tmp_path):
    g, st = partita(tmp_path)
    gioca_tutti(g, st, now=61)
    g.handle("a", "#t", "!vince 1", now=62)
    assert "\x02" in chan_lines(g)[-1]
    g.track(L(":op!x@h MODE #t +c"), now=63)
    g.tick(now=62 + C.PAUSA_S)
    gioca_tutti(g, st, now=70)
    g.handle(st["nicks"][g.czar(st)], "#t", "!vince 1", now=71)
    assert all("\x02" not in t for t in chan_lines(g)[-2:])


def test_plus_c_mid_reveal(tmp_path):
    g, st = partita(tmp_path, nicks=("a", "b", "c", "d", "e", "f"))
    g.out.flush(now=0, force=True)
    gioca_tutti(g, st, now=61)               # la rivelazione e' in coda, non uscita
    assert g.out.q
    g.track(L(":op!x@h MODE #t +c"), now=61)
    g.out.flush(now=61, force=True)
    assert all("\x02" not in t for t in chan_lines(g))


def test_404_retry_then_mute(tmp_path):
    g, st = partita(tmp_path)
    gioca_tutti(g, st, now=61)
    g.out.flush(now=61, force=True)
    g.track(L(":srv 404 MikeBongiorno #t :Cannot send to channel"), now=62)
    assert st["phase"] == "judge", "primo 404: si riprova senza colori"
    n = len(g.out.sent)
    g.out.flush(now=62, force=True)
    resent = g.out.sent[n:]
    assert resent and all(to == "#t" and "\x02" not in t for to, t in resent)
    g.track(L(":srv 404 MikeBongiorno #t :Cannot send to channel"), now=63)
    assert st["phase"] == "muto"
    n = len(g.sent)
    g.track(L(":srv 404 MikeBongiorno #t :Cannot send to channel"), now=64)
    assert len(g.sent) == n, "niente loop"
    # nessun +m/+M nella tabella: allo scadere si riprova a parlare
    g.tick(now=63 + C.LOBBY_S)
    assert st["phase"] == "play"


def test_mute_resumes_when_voiced(tmp_path):
    g, st = partita(tmp_path)
    g.track(L(":op!x@h MODE #t +M"), now=61)
    g.track(L(":srv 404 MikeBongiorno #t :Cannot send to channel"), now=62)
    assert st["phase"] == "muto"
    g.track(L(f":op!x@h MODE #t +v {BOT}"), now=63)
    g.tick(now=62 + C.LOBBY_S)
    assert st["phase"] == "play"


def test_mute_ends_if_still_muted(tmp_path):
    g, st = partita(tmp_path)
    g.track(L(":op!x@h MODE #t +m"), now=61)
    g.track(L(":srv 404 MikeBongiorno #t :Cannot send to channel"), now=62)
    g.tick(now=62 + C.LOBBY_S)
    assert "#t" not in g.games


@pytest.mark.parametrize("text", ["!cah boh", "!cah"])
def test_help_and_status(tmp_path, text):
    g = nuovo(tmp_path)
    g.handle("a", "#t", text, now=0)
    assert chan_lines(g)


# --- finestra vm18 e canali guardati (#italia, Malekith 2026-10-15) ------------

VM_NERE = ([{"testo": f"Nera {i} ___.", "pack": "p"} for i in range(3)]
           + [{"testo": f"Nera vm18 {i} ___.", "pack": "p", "vm18": True} for i in range(3)])
VM_BIANCHE = ([{"testo": f"ok {i}", "pack": "p"} for i in range(40)]
              + [{"testo": f"vm {i}", "pack": "p", "vm18": True} for i in range(40)])


def guardato(tmp_path, hour, opts=None):
    """Un canale #i con la finestra vm18 22-9; `hour` e' una lista di un
    elemento, l'orologio iniettato la legge a ogni chiamata."""
    opts = {"#i": {"vm18": (22, 9)}} if opts is None else opts
    return C.Cah("test", "/dev/null", "", tmp_path / "s.json", {"#i": None},
                 (VM_NERE, VM_BIANCHE), rng=random.Random(3), opts=opts,
                 clock=lambda now: hour[0], trust=TRUST)


def apri(g, chan="#i", nicks=("a", "b", "c", "d"), now=0):
    g.handle(nicks[0], chan, "!cah", now=now)
    for n in nicks[1:]:
        g.handle(n, chan, "!cah entra", now=now + 1)
    g.tick(now=now + C.LOBBY_S)
    return g.games[chan]


def tutte_le_carte(st):
    return [c for h in st["hands"].values() for c in h] + [st["black"]]


def test_vm18_carte_vere_marcate():
    nere, bianche = C.load_carte()
    assert sum(c["vm18"] for c in nere + bianche) == 154


@pytest.mark.parametrize("h,safe", [(8, False), (9, True), (12, True), (21, True),
                                    (22, False), (23, False), (0, False)])
def test_vm18_finestra_ore(tmp_path, h, safe):
    g = guardato(tmp_path, [h])
    assert g.safe("#i", 0) is safe


def test_vm18_di_giorno_mai(tmp_path):
    for seed in range(20):
        g = guardato(tmp_path, [15])
        g.rng = random.Random(seed)
        st = apri(g)
        for r in range(6):            # piu' mani, mazzo che si rimescola
            assert not set(tutte_le_carte(st)) & g.vm18
            g.new_round("#i", 100 + r)


def test_vm18_di_notte_tutto(tmp_path):
    seen = set()
    for seed in range(10):
        g = guardato(tmp_path, [23])
        g.rng = random.Random(seed)
        seen |= set(tutte_le_carte(apri(g)))
    assert seen & g.vm18 and seen - g.vm18


def test_vm18_cambio_finestra_alla_mano_dopo(tmp_path):
    hour = [23]
    g = guardato(tmp_path, hour)
    for seed in range(50):
        g.rng = random.Random(seed)
        g.games.clear()
        st = apri(g)
        if set(c for h in st["hands"].values() for c in h) & g.vm18:
            break
    else:
        pytest.fail("nessuna vm18 servita di notte")
    hour[0] = 9                     # scatta la finestra a meta' partita
    g.new_round("#i", 200)
    assert not set(tutte_le_carte(st)) & g.vm18
    assert all(len(h) == C.HAND for h in st["hands"].values())


def test_vm18_canale_senza_finestra_invariato(tmp_path):
    g = guardato(tmp_path, [12], opts={})
    assert g.safe("#i", 0) is False


def test_vm18_pool_sicuro_vuoto_ferma(tmp_path):
    with pytest.raises(SystemExit):
        C.Cah("test", "/dev/null", "", tmp_path / "s.json", {"#i": None},
              (VM_NERE[3:], VM_BIANCHE), opts={"#i": {"vm18": (22, 9)}})


def test_parse_opts():
    assert C.parse_opts("#i", ["vm18=22-9", "ops"]) == {"vm18": (22, 9), "ops": True}
    for bad in (["vm18=25-9"], ["vm18"], ["boh"], ["ops=1"]):
        with pytest.raises(SystemExit):
            C.parse_opts("#i", bad)


def test_load_nets_opzioni(monkeypatch):
    monkeypatch.setenv("CAH_NETS", "azzurra|/l|/f|#italia:ita+cah42:vm18=22-9:ops,#sbiffo:ita")
    cfg = C.load_nets()["azzurra"]
    assert cfg["chans"] == {"#italia": ("ita", "cah42"), "#sbiffo": ("ita",)}
    assert cfg["opts"] == {"#italia": {"vm18": (22, 9), "ops": True}, "#sbiffo": {}}


TRUST = (("vjt", "*.openssl.it"), ("vjt", "2a03:4000:2:33c::42"),
         ("vjt", "due.dita.di.grappa.chat"))


def ops_chan(tmp_path):
    return guardato(tmp_path, [23], opts={"#i": {"ops": True}})


def test_ops_non_op_non_apre(tmp_path):
    g = ops_chan(tmp_path)
    g.handle("a", "#i", "!cah", now=0, host="x.example")
    assert "#i" not in g.games
    line = chan_lines(g, "#i")[-1]
    assert line.startswith("a: ") and "op" in line


def test_ops_op_apre_tutti_giocano_solo_op_chiude(tmp_path):
    g = ops_chan(tmp_path)
    g.track(L(":srv 353 Mike = #i :@a b c d"))
    g.track(L(":srv 366 Mike #i :End"))
    st = apri(g)
    assert st["phase"] == "play" and len(st["players"]) == 4
    g.track(L(":a!x@h MODE #i -o a"))       # lo starter senza @ non chiude piu'
    g.handle("a", "#i", "!cah stop", now=70)
    assert "#i" in g.games
    g.track(L(":z!x@h MODE #i +o b"))
    g.handle("b", "#i", "!cah stop", now=71)  # un op che non l'ha aperta, si'
    assert "#i" not in g.games


def test_ops_host_azzurra_chat(tmp_path):
    g = ops_chan(tmp_path)
    g.handle("morph", "#i", "!cah", now=0, host="staff.azzurra.chat")
    assert "#i" in g.games
    g.handle("h", "#i", "!cah stop", now=1, host="Foo.HELPER.Azzurra.Chat")
    assert "#i" not in g.games
    for i, host in enumerate(("azzurra.chat", "evilazzurra.chat",
                              "staff.azzurra.chat.evil.org")):
        g.handle("e", "#i", "!cah", now=2 + i, host=host)
        assert "#i" not in g.games, host


def test_ops_trusted_nick_e_host(tmp_path):
    g = ops_chan(tmp_path)
    g.handle("vjt", "#i", "!cah", now=0, host="nowhere.OpenSSL.it")
    assert "#i" in g.games
    g.handle("VJT", "#i", "!cah stop", now=1, host="due.dita.di.grappa.chat")
    assert "#i" not in g.games
    g.handle("vjt", "#i", "!cah", now=2, host="evil.example")      # host sbagliato
    g.handle("mallory", "#i", "!cah", now=3, host="x.openssl.it")  # nick sbagliato
    assert "#i" not in g.games


def test_ops_righe_pulite():
    """#italia e' pubblico: nessuna bestemmia nelle righe del gioco."""
    import re as _re
    src = open(C.__file__, encoding="utf-8").read()
    assert not _re.search(r"(?i)\b(dio|madonna|cristo|porco|porca|cazz|stronz)", src)


def test_vm18_fascia_detta_all_apertura(tmp_path):
    g = guardato(tmp_path, [15])
    g.handle("a", "#i", "!cah", now=0)
    assert "Fascia protetta fino alle 22: niente carte vm18." in chan_lines(g, "#i")[-1]
    g = guardato(tmp_path, [23])
    g.handle("a", "#i", "!cah", now=0)
    assert ("Dopo le 22: mazzo completo, carte vm18 comprese. Fascia protetta dalle 9."
            in chan_lines(g, "#i")[-1])


def test_vm18_fascia_senza_finestra_zitta(tmp_path):
    g = guardato(tmp_path, [15], opts={})
    g.handle("a", "#i", "!cah", now=0)
    assert not any("vm18" in t for t in chan_lines(g, "#i"))
    g.handle("a", "#i", "!cah boh", now=1)
    assert "vm18" not in chan_lines(g, "#i")[-1]


def test_vm18_cambio_fascia_annunciato_una_volta(tmp_path):
    hour = [21]
    g = guardato(tmp_path, hour)
    st = apri(g)
    n = len(chan_lines(g, "#i"))
    g.tick(now=61)
    assert len(chan_lines(g, "#i")) == n             # niente cambio, niente riga
    hour[0] = 22
    g.tick(now=62)
    g.tick(now=63)
    new = chan_lines(g, "#i")[n:]
    assert sum("Sono le 22" in t for t in new) == 1
    hour[0] = 9
    g.tick(now=64)
    assert "Sono le 9: fascia protetta fino alle 22" in chan_lines(g, "#i")[-1]
    assert st["safe"] is True


def test_vm18_cambio_fascia_senza_partita_zitto(tmp_path):
    hour = [21]
    g = guardato(tmp_path, hour)
    hour[0] = 22
    g.tick(now=1)
    assert chan_lines(g, "#i") == []


def test_vm18_help_dice_la_fascia(tmp_path):
    g = guardato(tmp_path, [15])
    g.handle("a", "#i", "!cah boh", now=0)
    assert chan_lines(g, "#i")[-1].endswith("carte vm18 solo dalle 22 alle 9")
