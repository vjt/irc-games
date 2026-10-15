"""ircgame: parsing del bot.log, tracker di op/voci/modi/presenza, Outbox
col freno e col +c deciso al momento dell'uscita. Niente FIFO vera."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import ircgame as G  # noqa: E402


def L(s):
    return f"12:00:00 < {s}"


def test_load_nets(monkeypatch, tmp_path):
    monkeypatch.setenv("X_NETS", "azzurra|/a.log|/a.send|#A,#b ; ircnet|/i.log|/i.send|#c")
    n = G.load_nets("X_NETS", {}, "x_state", tmp_path)
    assert n["azzurra"]["chans"] == {"#a", "#b"}
    assert n["azzurra"]["state"] == tmp_path / "x_state.json"
    assert n["ircnet"]["state"] == tmp_path / "x_state.ircnet.json"
    monkeypatch.setenv("X_NETS", "")
    assert G.load_nets("X_NETS", {"d": 1}, "x", tmp_path) == {"d": 1}


def test_privmsg_pat():
    m = G.PRIVMSG_PAT.search(L(":vjt!~a@host PRIVMSG MikeBongiorno :!play 3"))
    assert m["nick"] == "vjt" and m["target"] == "MikeBongiorno" and m["text"] == "!play 3"
    assert G.PRIVMSG_PAT.search("12:00:00 > PRIVMSG #t :nostra") is None


def test_joined_lines_under_limit():
    lines = G.joined_lines(["x" * 100] * 9)
    assert len(lines) > 1 and all(len(s.encode()) <= G.MAX_LINE for s in lines)


def test_strip_codes():
    assert G.strip_codes("\x02a\x02 \x0304,1b\x03 \x0312c \x0fd\x16e\x1df\x1fg") == "a b c defg"


def test_json_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    assert G.load_json(p, {"x": 1}) == {"x": 1}
    G.save_json(p, {"a": "è"})
    assert G.load_json(p, None) == {"a": "è"}


def test_tracker_names_ops_voices():
    t = G.Tracker()
    t.feed(L(":srv 353 Mike = #T :Mike @Op +Voce altro"))
    assert t.feed(L(":srv 366 Mike #T :End of /NAMES list.")) == ("names", "#t")
    assert t.members["#t"] == {"mike", "op", "voce", "altro"}
    assert t.is_op("OP", "#t") and not t.is_op("altro", "#t")
    assert t.voices["#t"] == {"voce"}
    # un NAMES nuovo rifa' la fotografia
    t.feed(L(":srv 353 Mike = #t :@altro"))
    t.feed(L(":srv 366 Mike #t :End"))
    assert t.ops["#t"] == {"altro"} and t.members["#t"] == {"altro"}


def test_tracker_presence_events():
    t = G.Tracker()
    t.feed(L(":srv 353 Mike = #t :@a b"))
    t.feed(L(":srv 366 Mike #t :End"))
    assert t.feed(L(":c!x@h JOIN :#T")) == ("join", "#t", "c")
    assert "c" in t.members["#t"]
    assert t.feed(L(":a!x@h NICK :A2")) == ("nick", "a", "A2")
    assert t.is_op("a2", "#t") and "a2" in t.members["#t"]
    assert t.feed(L(":b!x@h PART #t :ciao")) == ("part", "#t", "b")
    assert t.feed(L(":A2!x@h KICK #t c :fuori")) == ("kick", "#t", "c")
    assert t.members["#t"] == {"a2"}
    assert t.feed(L(":A2!x@h QUIT :Ping timeout")) == ("quit", None, "A2", False)
    assert not t.is_op("a2", "#t")


def test_tracker_netsplit():
    t = G.Tracker()
    ev = t.feed(L(":x!y@h QUIT :irc.azzurra.chat hub.azzurra.chat"))
    assert ev == ("quit", None, "x", True)
    assert t.feed(L(":x!y@h QUIT :Quit: a dopo"))[3] is False


def test_tracker_ison():
    t = G.Tracker()
    assert t.feed(L(":srv 303 Mike :Alfa beta")) == ("ison", {"alfa", "beta"})
    assert t.feed(L(":srv 303 Mike :")) == ("ison", set())


def test_tracker_modes():
    t = G.Tracker()
    t.feed(L(":srv 324 Mike #t +ntc"))
    assert t.has_mode("#T", "c")
    t.feed(L(":op!x@h MODE #t -c+l 10"))
    assert not t.has_mode("#t", "c") and t.has_mode("#t", "l")
    t.feed(L(":op!x@h MODE #t +bc *!*@x"))
    assert t.has_mode("#t", "c")
    assert t.feed(L(":srv 404 Mike #u :Cannot send to channel")) == ("404", "#u")
    assert t.has_mode("#u", "c")


def test_tracker_can_speak():
    t = G.Tracker()
    t.feed(L(":srv 324 Mike #t +nt"))
    assert t.can_speak("#t", "Mike")
    t.feed(L(":op!x@h MODE #t +M"))
    assert not t.can_speak("#t", "Mike")
    t.feed(L(":op!x@h MODE #t +v Mike"))
    assert t.can_speak("#t", "Mike")
    t.feed(L(":op!x@h MODE #t -v+m Mike"))
    assert not t.can_speak("#t", "Mike")
    t.feed(L(":op!x@h MODE #t +o Mike"))
    assert t.can_speak("#t", "Mike")


def test_outbox_rate_limit():
    o = G.Outbox("", "t", G.Tracker(), burst=3, gap=1.0)
    for i in range(5):
        o.say("#t", f"r{i}")
    assert o.flush(now=0) == 1.0 and len(o.sent) == 3
    assert o.flush(now=1) == 1.0 and len(o.sent) == 3
    assert o.flush(now=3.5) is None and len(o.sent) == 5


def test_outbox_strip_decided_at_send_time():
    t = G.Tracker()
    o = G.Outbox("", "t", t)
    o.say("#t", "\x02uno\x02")
    t.feed(L(":op!x@h MODE #t +c"))          # il modo cambia con la riga in coda
    o.say("#t", "\x02due\x02")
    o.say("nick", "\x02query\x02")           # le query non hanno modi
    o.flush(now=0, force=True)
    assert o.sent == [("#t", "uno"), ("#t", "due"), ("nick", "\x02query\x02")]
    t.feed(L(":op!x@h MODE #t -c"))
    o.say("#t", "\x02tre\x02")
    o.flush(now=1, force=True)
    assert o.sent[-1] == ("#t", "\x02tre\x02")


def test_outbox_404_resends_stripped_once():
    t = G.Tracker()
    o = G.Outbox("", "t", t)
    o.say("#t", "\x02bold\x02")
    o.say("#t", "liscia")
    o.flush(now=0, force=True)
    ev = t.feed(L(":srv 404 Mike #t :Cannot send to channel"))
    assert o.on_404(ev[1], now=1) == 1        # solo la riga con codici
    o.flush(now=1, force=True)
    assert o.sent[-1] == ("#t", "bold")
    # il secondo 404 non ha piu' niente da ripulire: niente loop
    assert o.on_404("#t", now=2) == 0
    # una riga troppo vecchia non si rimanda
    t.feed(L(":op!x@h MODE #t -c"))
    o.say("#t", "\x02vecchia\x02")
    o.flush(now=10, force=True)
    assert o.on_404("#t", now=10 + o.RESEND_S + 1) == 0
