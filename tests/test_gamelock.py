"""Una partita per canale fra giochi diversi (Malekith 2026-10-15): il
lucchetto di ircgame fra processi, il pid morto, GameBot nel canale, e i tre
giochi (CaH, Trivial, Sillabah) che si rifiutano a vicenda."""
import json
import os
import random
import signal
import subprocess
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import cah as C  # noqa: E402
import impiccato as I  # noqa: E402
import ircgame as G  # noqa: E402
import sillabah as S  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def L(s):
    return f"12:00:00 < {s}"


def holder_process(root, chan="#t", game="Altro"):
    """Un processo vero che prende il lucchetto e aspetta: quando stampa
    'preso' lo tiene."""
    code = textwrap.dedent(f"""
        import sys, time
        sys.path.insert(0, {HERE!r})
        import ircgame
        l = ircgame.ChannelLock("azzurra", {game!r}, {str(root)!r})
        assert l.acquire({chan!r}) is None
        print("preso", flush=True)
        time.sleep(60)
    """)
    p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "preso"
    return p


def test_contesa_fra_due_istanze(tmp_path):
    a = G.ChannelLock("azzurra", "Trivial", tmp_path)
    b = G.ChannelLock("azzurra", "Sillabah", tmp_path)
    assert a.acquire("#T") is None
    assert a.acquire("#t") is None                  # gia' suo: ok
    assert b.acquire("#t") == "Trivial"
    assert b.acquire("#altro") is None              # altro canale, libero
    assert G.ChannelLock("ircnet", "X", tmp_path).acquire("#t") is None  # altra rete
    a.release("#t")
    assert b.acquire("#t") is None
    d = json.loads((tmp_path / "azzurra.#t").read_text())
    assert d["game"] == "Sillabah" and d["pid"] == os.getpid()


def test_contesa_fra_processi_e_pid_morto(tmp_path):
    p = holder_process(tmp_path)
    try:
        me = G.ChannelLock("azzurra", "Trivial", tmp_path)
        assert me.acquire("#t") == "Altro"
    finally:
        p.send_signal(signal.SIGKILL)               # crash, niente release
        p.wait()
    # il file resta, col pid morto dentro: il lucchetto si riprende
    assert json.loads((tmp_path / "azzurra.#t").read_text())["pid"] == p.pid
    assert me.acquire("#t") is None
    assert json.loads((tmp_path / "azzurra.#t").read_text())["pid"] == os.getpid()


def test_lucchetto_spento_senza_root(tmp_path, monkeypatch):
    monkeypatch.delenv("IRCGAME_LOCKS", raising=False)
    a, b = G.ChannelLock("azzurra", "A"), G.ChannelLock("azzurra", "B")
    assert a.acquire("#t") is None and b.acquire("#t") is None


def test_sync_molla_e_riprende(tmp_path):
    a = G.ChannelLock("azzurra", "Trivial", tmp_path)
    b = G.ChannelLock("azzurra", "Sillabah", tmp_path)
    a.sync({"#t": {}})                              # partita ripresa da disco
    assert b.acquire("#t") == "Trivial"
    a.sync({})                                      # partita finita
    assert b.acquire("#t") is None


def test_gamebot_nel_canale(tmp_path):
    t, lock = G.Tracker(), G.ChannelLock("azzurra", "Trivial", tmp_path)
    t.feed(L(":srv 353 Mike = #t :@op GameBot uno"))
    t.feed(L(":srv 366 Mike #t :End"))
    assert G.start_refusal(lock, t, "#t") == "C'è già una partita di GameBot in corso."
    assert not lock.held                            # il rifiuto non tiene il lucchetto
    t.feed(L(":GameBot!g@h PART #t :fine"))
    assert G.start_refusal(lock, t, "#t") is None
    lock.release("#t")
    t.feed(L(":GameBot!g@h JOIN :#t"))
    assert "GameBot" in G.start_refusal(lock, t, "#t")
    t.feed(L(":GameBot!g@h QUIT :bye"))
    assert G.start_refusal(lock, t, "#t") is None


# --- i giochi veri --------------------------------------------------------------

def cah(tmp_path):
    return C.Cah("azzurra", "/dev/null", "", tmp_path / "c.json", {"#t"},
                 (["Uno ___."], [f"b{i}" for i in range(40)]),
                 rng=random.Random(1), locks=tmp_path / "locks")


def trivial(tmp_path):
    return I.Impiccato("azzurra", "/dev/null", "", tmp_path / "i.json", {"#t"},
                       [{"q": "Domanda?", "a": "Risposta", "cat": "t"}], talk=False,
                       locks=tmp_path / "locks")


def sillabah(tmp_path):
    return S.Sillabah("azzurra", "/dev/null", "", tmp_path / "s.json", {"#t"},
                      S.Dizionario(["riso", "rima"]), talk=False,
                      rng=random.Random(1), locks=tmp_path / "locks")


def test_giochi_si_rifiutano_a_vicenda(tmp_path):
    c, q, s = cah(tmp_path), trivial(tmp_path), sillabah(tmp_path)
    c.handle("a", "#t", "!cah", now=0)
    assert "#t" in c.games
    q.handle("b", "#t", "!trivial t", now=1)
    assert q.game("#t") is None
    assert q.sent[-1] == ("#t", "C'è già una partita di Cards Against Humanity in corso.")
    s.handle("b", "#t", "!sillabah", now=1)
    assert s.game("#t") is None and "Cards Against Humanity" in s.sent[-1][1]
    c.handle("a", "#t", "!cah stop", now=2)
    c.tick(now=2)                                   # la sync molla il lucchetto
    q.handle("b", "#t", "!trivial t", now=3)
    assert q.game("#t") is not None
    c.handle("a", "#t", "!cah", now=4)
    assert "#t" not in c.games
    assert ("#t", "C'è già una partita di Trivial in corso.") in c.sent
    q.handle("b", "#t", "!stop", now=5)
    q.lock.sync(q.state["games"])
    assert s.handle("b", "#t", "!sillabah", now=6) is True


def test_gamebot_ferma_i_nostri(tmp_path):
    c = cah(tmp_path)
    c.track(L(":srv 353 MikeBongiorno = #t :GameBot a"))
    c.track(L(":srv 366 MikeBongiorno #t :End"))
    c.handle("a", "#t", "!cah", now=0)
    assert "#t" not in c.games
    assert c.sent[-1] == ("#t", "C'è già una partita di GameBot in corso.")
    q = trivial(tmp_path)
    q.track(L(":GameBot!g@h JOIN :#t"))
    q.handle("b", "#t", "!trivial t", now=1)
    assert q.game("#t") is None and "GameBot" in q.sent[-1][1]
