"""Invarianti di Sillabah: parsing del log, validazione, punteggi, manche,
stop per silenzio, scelta della sillaba sul dizionario vero.

Niente FIFO e niente IRC: talk=False, e `sent` raccoglie quello che il bot
avrebbe detto.
"""
import os
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import sillabah as S  # noqa: E402

BOT = "MikeBongiorno"

# Un dizionario finto ma con abbastanza parole in RI da reggere la scelta
# della sillaba: tutte le ri?? piu' qualche parola vera.
FINTE = [f"ri{chr(97 + i)}{chr(97 + j)}" for i in range(26) for j in range(26)]
VERE = ["riso", "rima", "ridere", "perché", "più", "zaino", "rizzo", "giri",
        "arrivo", "rischiare", "papà", "papa"]


@pytest.fixture
def diz():
    return S.Dizionario(FINTE + VERE)


@pytest.fixture(scope="module")
def vero():
    return S.load_parole()


def gioco(diz, tmp_path, chans=("#t",), seed=1):
    return S.Sillabah("test", "/dev/null", "", tmp_path / "s.json", set(chans),
                      diz, talk=False, rng=random.Random(seed))


def forza(g, chan, sillaba="ri", limite=7):
    """Fissa la manche: la sillaba vera e' a caso, i test vogliono la loro."""
    m = g.game(chan)
    m["sillaba"], m["limite"] = sillaba, limite
    m["best"] = None


def log_line(nick, target, text, d="<"):
    return f"21:00:00 {d} :{nick}!~id@host.example PRIVMSG {target} :{text}"


# ------------------------------------------------------------------ parsing

def test_privmsg_parses_channel_and_query():
    m = S.PRIVMSG_PAT.search(log_line("pippo", "#sbiffo", "!sillabah"))
    assert (m["nick"], m["target"], m["text"]) == ("pippo", "#sbiffo", "!sillabah")
    m = S.PRIVMSG_PAT.search(log_line("pippo", BOT, "riso"))
    assert (m["nick"], m["target"], m["text"]) == ("pippo", BOT, "riso")


def test_notice_in_query_is_input_but_not_from_services_or_channels():
    def m(nick, target, text="riso"):
        return S.PRIVMSG_PAT.search(
            log_line(nick, target, text).replace(" PRIVMSG ", " NOTICE "))
    assert S.is_input(m("pippo", BOT))
    assert m("pippo", BOT)["text"] == "riso"
    assert not S.is_input(m("NickServ", BOT, "This nickname is registered"))
    assert not S.is_input(m("pippo", "#sbiffo"))
    assert S.is_input(S.PRIVMSG_PAT.search(log_line("pippo", "#sbiffo", "!sillabah")))


def test_own_outbound_lines_are_not_input():
    # bot.py logga le nostre righe con '>': rileggerle come input farebbe
    # giocare il bot contro se stesso.
    out = "21:00:00 > [sillabah] PRIVMSG pippo :✅ Parola accettata: RISO (4 punti)."
    assert S.PRIVMSG_PAT.search(out) is None


def test_handle_routes_query_vs_channel(diz, tmp_path):
    g = gioco(diz, tmp_path)
    assert g.handle("a", "#t", "!sillabah", now=0) is True
    forza(g, "#t")
    # target che non e' un canale = query al bot, qualunque sia il suo nick
    assert g.handle("a", "MikeBongiorno_", "riso", now=1) is True
    assert g.game("#t")["parole"]["a"]["forma"] == "riso"


def test_ctcp_in_query_ignored(diz, tmp_path):
    g = gioco(diz, tmp_path)
    g.handle("a", "#t", "!sillabah", now=0)
    g.sent.clear()
    assert g.handle("a", BOT, "\x01VERSION\x01", now=1) is False
    assert g.sent == []


def test_load_nets_from_env(monkeypatch):
    monkeypatch.setenv("SILLABAH_NETS",
                       "azzurra|/x/a.log|/x/a.send|#SBIFFO,#sniffo; ircnet|/x/i.log||#cybernet")
    nets = S.load_nets()
    assert nets["azzurra"]["chans"] == {"#sbiffo", "#sniffo"}
    assert nets["azzurra"]["state"].name == "sillabah_state.json"
    assert nets["ircnet"]["state"].name == "sillabah_state.ircnet.json"
    assert nets["ircnet"]["fifo"] == Path("")


def test_load_nets_rejects_garbage(monkeypatch):
    monkeypatch.setenv("SILLABAH_NETS", "azzurra|/x/a.log|#sbiffo")
    with pytest.raises(SystemExit):
        S.load_nets()


# ------------------------------------------------------------- punteggio

def test_letter_values_match_the_original():
    assert S.VALORI == {
        "a": 1, "b": 3, "c": 1, "d": 3, "e": 1, "f": 3, "g": 3, "h": 6,
        "i": 1, "j": 8, "k": 8, "l": 2, "m": 2, "n": 2, "o": 1, "p": 3,
        "q": 6, "r": 1, "s": 1, "t": 1, "u": 3, "v": 3, "w": 8, "x": 8,
        "y": 8, "z": 5}


def test_scoring_sums_letters_and_folds_accents():
    assert S.punteggio("riso") == 4
    assert S.punteggio("zaino") == 5 + 1 + 1 + 2 + 1
    assert S.punteggio(S.norm_parola("Perché")) == S.punteggio("perche") == 13
    assert S.punteggio(S.norm_parola("PIÙ")) == 3 + 1 + 3


def test_valori_line_lists_every_letter():
    line = S.riga_valori()
    for c in S.VALORI:
        assert f" {c}" in line, c
    assert len(line.encode()) <= S.MAX_LINE


# ------------------------------------------------------------ validazione

@pytest.mark.parametrize("testo,motivo", [
    ("", "parola vuota"),
    ("riso nero", "una parola sola"),
    ("ri5o", "solo lettere"),
    ("ri-so", "solo lettere"),
    ("zaino", "non contiene RI"),
    ("rischiare", "troppo lunga: 9 lettere, il limite e' 7"),
    ("rimx", None),          # finta ma nel dizionario
    ("rixyz", "non e' nel dizionario"),
])
def test_valuta(diz, testo, motivo):
    forma, perche = S.valuta(diz, "ri", 7, testo)
    if motivo is None:
        assert forma == testo and perche is None
    else:
        assert forma is None and motivo in perche


def test_valuta_accents_and_case(diz):
    assert S.valuta(diz, "ri", 7, "RISO") == ("riso", None)
    # accentata scritta senza accento, con l'accento, e con l'apostrofo IRC
    for t in ("perche", "perché", "PERCHÈ", "perche'"):
        assert S.valuta(diz, "rc", 7, t) == ("perche", None), t
    assert S.valuta(diz, "pi", 5, "piu'") == ("piu", None)
    # papa/papà: stessa forma, si mostra la prima in ordine alfabetico
    assert diz.grafia["papa"] == "papa"


def test_limit_counts_letters_not_bytes(diz):
    # 'perché' sono 6 lettere e 7 byte: il limite e' in lettere.
    assert S.valuta(diz, "rc", 6, "perché") == ("perche", None)


# ------------------------------------------------ parola, sostituzione, delete

def test_accept_replace_delete(diz, tmp_path):
    g = gioco(diz, tmp_path)
    g.handle("a", "#t", "!sillabah", now=0)
    forza(g, "#t")
    g.sent.clear()

    assert g.handle("Pippo", BOT, "riso", now=1) is True
    assert g.sent[-1] == ("Pippo", "✅ Parola accettata: RISO (4 punti).")
    assert set(g.game("#t")["parole"]) == {"pippo"}

    assert g.handle("pippo", BOT, "rima", now=5) is True
    assert "sostituisce RISO" in g.sent[-1][1], g.sent[-1]
    assert g.game("#t")["parole"]["pippo"]["forma"] == "rima"
    assert len(g.game("#t")["parole"]) == 1

    # una parola sbagliata NON cancella quella buona gia' in gioco
    assert g.handle("pippo", BOT, "zaino", now=10) is False
    assert g.sent[-1] == ("pippo", "❌ non contiene RI.")
    assert g.game("#t")["parole"]["pippo"]["forma"] == "rima"

    assert g.handle("pippo", BOT, "-delete", now=15) is True
    assert g.sent[-1] == ("pippo", "🗑️ Ritirata: RIMA.")
    assert g.game("#t")["parole"] == {}

    assert g.handle("pippo", BOT, "-delete", now=20) is False
    assert g.sent[-1] == ("pippo", "Non avevi parole in gioco.")


def test_query_without_game_or_during_pause(diz, tmp_path):
    g = gioco(diz, tmp_path)
    assert g.handle("a", BOT, "riso", now=0) is False
    assert "Nessuna partita" in g.sent[-1][1]
    g.handle("a", "#t", "!sillabah", now=10)
    forza(g, "#t")
    g.handle("a", BOT, "riso", now=11)
    g.tick(now=10 + S.MANCHE_S)                 # fine manche -> pausa
    assert g.game("#t")["fase"] == "pausa"
    assert g.handle("b", BOT, "rima", now=10 + S.MANCHE_S + 1) is False
    assert g.sent[-1][0] == "b" and "Manche chiusa" in g.sent[-1][1]


def test_channel_commands_in_query_are_silent(diz, tmp_path):
    g = gioco(diz, tmp_path)
    g.handle("a", "#t", "!sillabah", now=0)
    g.sent.clear()
    assert g.handle("a", BOT, "!trivial nerd", now=1) is False
    assert g.sent == []


def test_query_reply_throttle(diz, tmp_path):
    g = gioco(diz, tmp_path)
    g.handle("a", "#t", "!sillabah", now=0)
    forza(g, "#t")
    g.sent.clear()
    # stesso nick dentro QUERY_GAP: la parola conta, la risposta no
    g.handle("a", BOT, "riso", now=1.0)
    g.handle("a", BOT, "rima", now=1.5)
    assert len(g.sent) == 1, g.sent
    assert g.game("#t")["parole"]["a"]["forma"] == "rima"
    # tetto globale: piu' di N risposte nella finestra non escono
    n, window = S.QUERY_BUDGET
    g.sent.clear()
    for i in range(n + 5):
        g.handle(f"n{i}", BOT, "riso", now=100.0 + i * 0.1)
    assert len(g.sent) == n, len(g.sent)
    assert len(g.game("#t")["parole"]) == n + 5 + 1   # contano tutte
    # passata la finestra si risponde di nuovo
    g.handle("altro", BOT, "riso", now=100.0 + window + 1)
    assert g.sent[-1][0] == "altro"


# ----------------------------------------------------------- fine manche

def test_manche_end_ranking_and_scores(diz, tmp_path):
    g = gioco(diz, tmp_path)
    g.handle("a", "#t", "!sillabah 3", now=0)
    forza(g, "#t")
    m = g.game("#t")
    m["best"] = "rizzo"
    g.handle("uno", BOT, "riso", now=1)         # 4
    g.handle("due", BOT, "rizzo", now=2)        # 1+1+5+5+1 = 13
    g.handle("tre", BOT, "rima", now=3)         # 1+1+2+1 = 5
    g.handle("quattro", BOT, "rimb", now=4)     # 1+1+2+3 = 7
    g.handle("cinque", BOT, "risa", now=0.5)    # 4, ma prima di `uno`
    g.sent.clear()
    assert g.tick(now=S.MANCHE_S) is True
    righe = [t for _, t in g.sent]
    assert all(c == "#t" for c, _ in g.sent)
    assert len(righe) <= 2, righe
    esito = righe[0].replace(S.ZWSP, "")
    assert esito.startswith("🏁 Manche 1, RI max 7:"), esito
    # ordine: punti, poi chi e' arrivato prima
    ordine = ["due RIZZO 13", "quattro RIMB 7", "tre RIMA 5", "cinque RISA 4", "uno RISO 4"]
    pos = [esito.index(x) for x in ordine]
    assert pos == sorted(pos), esito
    assert "🥇 due RIZZO 13" in esito
    assert "💡 Il massimo era RIZZO (13)" in esito
    # i nick escono con lo spazio a larghezza zero: niente highlight a raffica
    assert "due" not in righe[0] and "quattro" not in righe[0]
    for r in righe:
        assert len(r.encode()) <= 400, len(r.encode())
    # classifica persistente
    sc = g.state["scores"]
    assert sc["due"]["points"] == 13 and sc["uno"]["points"] == 4
    assert g.game("#t")["fase"] == "pausa"
    assert g.game("#t")["parole"] == {}


def test_manche_end_caps_at_top(diz, tmp_path):
    g = gioco(diz, tmp_path)
    g.handle("a", "#t", "!sillabah", now=0)
    forza(g, "#t")
    for i in range(S.TOP + 3):
        g.handle(f"giocatore{i}", BOT, "riso", now=1 + i * 2)
    g.sent.clear()
    g.tick(now=S.MANCHE_S)
    esito = g.sent[0][1]
    assert f"(+3)" in esito, esito
    assert esito.count("RISO") == S.TOP
    assert len(g.sent) <= 2


def test_next_manche_after_pause_and_game_limit(diz, tmp_path):
    g = gioco(diz, tmp_path)
    g.handle("a", "#t", "!sillabah 2", now=0)
    forza(g, "#t")
    g.handle("a", BOT, "riso", now=1)
    t = S.MANCHE_S
    g.tick(now=t)
    assert g.game("#t")["fase"] == "pausa"
    g.sent.clear()
    g.tick(now=t + S.PAUSA_S)
    assert g.game("#t")["fase"] == "manche" and g.game("#t")["count"] == 2
    assert g.sent[-1][1].startswith("🔤 [2/2] Sillaba "), g.sent[-1]
    forza(g, "#t")
    g.handle("b", BOT, "rizzo", now=t + S.PAUSA_S + 1)
    g.sent.clear()
    g.tick(now=t + S.PAUSA_S + S.MANCHE_S)
    assert g.game("#t") is None
    tutto = " ".join(x for _, x in g.sent).replace(S.ZWSP, "")
    assert "Partita finita dopo 2 manche" in tutto, tutto
    assert "🥇 b 13" in tutto and "🥈 a 4" in tutto, tutto


def test_syllables_not_repeated_within_a_game(diz, tmp_path):
    g = gioco(diz, tmp_path)
    g.handle("a", "#t", "!sillabah 5", now=0)
    t = 0
    viste = []
    for _ in range(4):
        viste.append(g.game("#t")["sillaba"])
        g.game("#t")["idle"] = 0            # manche vuote, ma niente idle stop
        t += S.MANCHE_S
        g.tick(now=t)
        t += S.PAUSA_S
        g.tick(now=t)
        if g.game("#t") is None:
            break
    assert len(viste) == len(set(viste)), viste


# ------------------------------------------------------------- idle stop

def test_idle_stop_after_empty_manches(diz, tmp_path):
    g = gioco(diz, tmp_path)
    g.handle("a", "#t", "!sillabah", now=0)
    t = 0
    for i in range(S.IDLE_MANCHE):
        assert g.game("#t") is not None, f"chiusa troppo presto, manche {i + 1}"
        t += S.MANCHE_S
        g.tick(now=t)
        if g.game("#t"):
            t += S.PAUSA_S
            g.tick(now=t)
    assert g.game("#t") is None
    assert any("manche senza parole, chiudo" in x for _, x in g.sent), g.sent


def test_a_word_resets_the_idle_counter(diz, tmp_path):
    g = gioco(diz, tmp_path)
    g.handle("a", "#t", "!sillabah", now=0)
    t = S.MANCHE_S
    g.tick(now=t)                         # manche 1 vuota
    assert g.game("#t")["idle"] == 1
    t += S.PAUSA_S
    g.tick(now=t)                         # manche 2
    forza(g, "#t")
    g.handle("a", BOT, "riso", now=t + 1)
    t += S.MANCHE_S
    g.tick(now=t)
    assert g.game("#t") is not None and g.game("#t")["idle"] == 0


def test_stale_game_dropped_silently(diz, tmp_path):
    g = gioco(diz, tmp_path)
    g.handle("a", "#t", "!sillabah", now=0)
    g.sent.clear()
    assert g.tick(now=S.MANCHE_S + S.STALE_S + 1) is True
    assert g.game("#t") is None and g.sent == []


# --------------------------------------------------------- comandi canale

def test_stop_does_not_score_the_running_manche(diz, tmp_path):
    g = gioco(diz, tmp_path)
    g.handle("a", "#t", "!sillabah", now=0)
    forza(g, "#t")
    g.handle("a", BOT, "riso", now=1)
    assert g.handle("a", "#t", "!stop", now=2) is False      # quello e' del trivial
    assert g.game("#t") is not None
    assert g.handle("a", "#t", "!sillabah stop", now=2) is True
    assert g.game("#t") is None
    assert g.state["scores"] == {}
    assert "🛑 Chiuso" in g.sent[-1][1]


def test_one_game_per_network(diz, tmp_path):
    g = gioco(diz, tmp_path, chans=("#uno", "#due"))
    assert g.handle("a", "#uno", "!sillabah", now=0) is True
    assert g.handle("b", "#due", "!sillabah", now=1) is False
    assert g.game("#due") is None
    assert g.sent[-1][0] == "#due" and "altro canale" in g.sent[-1][1]
    assert "#uno" not in g.sent[-1][1]


def test_restart_command_reannounces(diz, tmp_path):
    g = gioco(diz, tmp_path)
    g.handle("a", "#t", "!sillabah 3", now=0)
    s = g.game("#t")["sillaba"]
    assert g.handle("a", "#t", "!sillabah 20", now=30) is False
    assert g.game("#t")["limit_manche"] == 3
    assert g.game("#t")["sillaba"] == s
    assert "30s" in g.sent[-1][1], g.sent[-1]


def test_manche_count_capped(diz, tmp_path):
    g = gioco(diz, tmp_path)
    g.handle("a", "#t", "!sillabah 999", now=0)
    assert g.game("#t")["limit_manche"] == S.MAX_MANCHE


def test_sillaba_valori_punti_classifica(diz, tmp_path):
    g = gioco(diz, tmp_path)
    g.handle("a", "#t", "!sillaba", now=0)
    assert "Nessuna partita" in g.sent[-1][1]
    g.handle("a", "#t", "!classifica", now=0)
    assert "[Sillabah] Classifica vuota" in g.sent[-1][1]
    g.handle("a", "#t", "!valori", now=0)
    assert g.sent[-1][1].startswith("🔢 Valori:")

    g.handle("a", "#t", "!sillabah 1", now=0)
    forza(g, "#t", "ri", 6)
    g.handle("a", "#t", "!sillaba", now=20)
    assert "Sillaba RI, max 6 lettere, 40s" in g.sent[-1][1], g.sent[-1]
    g.handle("Pippo", BOT, "rizzo", now=21)
    g.tick(now=S.MANCHE_S)

    g.sent.clear()
    g.handle("pippo", "#t", "!punti", now=100)
    assert g.sent[-1][1] == "🧮 [Sillabah] Pippo: 13 punti, 1° su 1."
    g.handle("x", "#t", "!punti nessuno", now=100)
    assert "nessuno: nessun punto" in g.sent[-1][1]
    g.handle("x", "#t", "!classifica sillabah", now=100)
    assert "[Sillabah] Classifica (test)" in g.sent[-1][1]
    g.sent.clear()
    g.handle("x", "#t", "!classifica trivial", now=100)
    assert g.sent == []


def test_other_channels_ignored(diz, tmp_path):
    g = gioco(diz, tmp_path)
    assert g.handle("a", "#altro", "!sillabah", now=0) is False
    assert g.sent == []


def test_state_roundtrip(diz, tmp_path):
    g = gioco(diz, tmp_path)
    g.handle("a", "#t", "!sillabah", now=0)
    forza(g, "#t")
    g.handle("a", BOT, "riso", now=1)
    S.save_state(g.state_path, g.state)
    g2 = gioco(diz, tmp_path)
    assert g2.game("#t")["parole"]["a"]["forma"] == "riso"
    g2.tick(now=S.MANCHE_S)
    assert g2.state["scores"]["a"]["points"] == 4


# ----------------------------------------------- dizionario vero e sillabe

def test_real_dictionary_shape(vero):
    assert len(vero) > 100_000
    assert max(len(f) for f in vero.grafia) <= 15
    assert S.LIMITE_MAX <= 15, "il dizionario e' tagliato a 15 lettere"
    for w in ("casa", "abate", "perche", "piu", "citta", "gatto"):
        assert w in vero, w
    # niente nomi propri, niente verbi tronchi
    for w in ("abbado", "vorrebber", "zigzaghin"):
        assert w not in vero, w


def test_syllable_choice_is_always_solvable(vero):
    rng = random.Random(42)
    for _ in range(200):
        s, limite, best, nsol = S.scegli_sillaba(vero, rng)
        assert len(s) in S.SILLABA_LEN
        assert S.LIMITE_MIN <= limite <= S.LIMITE_MAX
        assert limite >= len(s) + 2
        sol = vero.soluzioni(s, limite)
        assert nsol == len(sol) >= S.MIN_SOLUZIONI
        assert best in sol
        assert S.punteggio(best) == max(S.punteggio(f) for f in sol)


def test_syllable_choice_respects_exclusions():
    # 22 parole x+RI+y con x e y tutte diverse: solo RI ha abbastanza
    # soluzioni, ogni altra sottostringa compare una volta sola.
    xs, ys = "abcdefghjklmnopqstuvw", "zyxwvutsqponmlkjhgfedc"
    solo_ri = S.Dizionario([x + "ri" + y for x, y in zip(xs, ys)] + ["zriz"])
    assert len(solo_ri.soluzioni("ri", 5)) >= S.MIN_SOLUZIONI
    s, *_ = S.scegli_sillaba(solo_ri, random.Random(3))
    assert s == "ri"
    assert S.scegli_sillaba(solo_ri, random.Random(3), escludi={"ri"}) is None


def test_selftest_runs(capsys):
    S.selftest()
    assert "selftest ok" in capsys.readouterr().out


def test_notice_in_query_gets_notice_back(diz, tmp_path):
    g = gioco(diz, tmp_path)
    g.handle("pippo", BOT, "!valori", now=0, notice=True)
    assert [t for t, _ in g.notices] == ["pippo"] and not g.sent
    g.handle("pluto", BOT, "!valori", now=0)
    assert [t for t, _ in g.sent] == ["pluto"] and len(g.notices) == 1


def test_status_line_during_manche(diz, tmp_path):
    g = gioco(diz, tmp_path)
    g.handle("a", "#t", "!sillabah 3", now=0)
    forza(g, "#t")
    g.sent.clear()
    g.tick(now=S.STATUS_GAP)                    # nessuna parola: zitto
    assert g.sent == []
    g.handle("uno", BOT, "riso", now=16)
    g.handle("due", BOT, "rima", now=17)
    g.sent.clear()
    g.tick(now=18)
    assert g.sent == [("#t", f"📝 2 parole in gioco, mancano {S.MANCHE_S - 18}s.")]
    g.sent.clear()
    g.tick(now=25)                              # numero uguale: zitto
    g.handle("tre", BOT, "rimb", now=26)
    g.sent.clear()
    g.tick(now=28)                              # troppo presto dall'ultima
    assert g.sent == []
    g.tick(now=33)
    assert [t for _, t in g.sent] == [f"📝 3 parole in gioco, mancano {S.MANCHE_S - 33}s."]
    g.handle("quattro", BOT, "risa", now=40)
    g.sent.clear()
    g.tick(now=S.MANCHE_S - S.STATUS_MIN_LEFT + 1)  # a ridosso della scadenza: zitto
    assert g.sent == []
