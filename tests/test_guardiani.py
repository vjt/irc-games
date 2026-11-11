"""Invarianti dei guardiani (vjt 2026-11-05): Ambrogio e Mike oppano vjt dove
sono op e kickano chi deoppa o kicka vjt o loro. L'identita' e' la mask, mai
il nick.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import guardiani as g  # noqa: E402

VJT = "vjt!antani@due.dita.di.grappa.chat"
AMB = "Ambrogio!~ambrogio@ci.pensa.ambrogio.con.la.grappa.chat"
EVIL = "pippo!~pippo@evil.example"
FAKE = "vjt!~antani@evil.example"
TOM = "tom!tom@tom.dev.irc.it"
SHINOBI = "S~!uid702845@id-702845.ilkley.irccloud.com"
GOSUB = "gosub!_gosub@stop.endsub.it"


def L(src, rest):
    return f"03:00:00 < :{src} {rest}"


def setup(names="@Ambrogio pippo"):
    world = g.World()
    guard = g.Guard(world, "fifo")
    for line in [
        L("irc.example", "001 Ambrogio :Welcome Ambrogio!~ambrogio@ci.pensa.ambrogio.con.la.grappa.chat"),
        L(AMB, "JOIN #c"),
        L("irc.example", f"353 Ambrogio = #c :{names}"),
        L("irc.example", "366 Ambrogio #c :End"),
        L(EVIL, "PRIVMSG #c :ciao"),
    ]:
        g.feed(world, guard, line, None)
    return world, guard


def run(world, guard, line):
    out = []
    g.feed(world, guard, line, out)
    return [cmd for _, cmd in out]


def test_ops_vjt_on_join():
    w, gd = setup()
    assert run(w, gd, L(VJT, "JOIN #c")) == ["RAW:guard MODE #c +o vjt"]


def test_impostor_nick_is_not_vjt():
    w, gd = setup()
    assert run(w, gd, L(FAKE, "JOIN #c")) == []


def test_no_op_without_ops():
    w, gd = setup(names="Ambrogio pippo")
    assert run(w, gd, L(VJT, "JOIN #c")) == []


def test_deop_vjt_kicks_and_reops():
    w, gd = setup()
    run(w, gd, L(VJT, "JOIN #c"))
    run(w, gd, L(AMB, "MODE #c +o vjt"))
    out = run(w, gd, L(EVIL, "MODE #c -o vjt"))
    assert out[0].startswith("RAW:guard KICK #c pippo :ao?!")
    assert out[1] == "RAW:guard MODE #c +o vjt"


def test_mode_args_are_walked():
    w, gd = setup()
    run(w, gd, L(VJT, "JOIN #c"))
    run(w, gd, L(AMB, "MODE #c +o vjt"))
    out = run(w, gd, L(EVIL, "MODE #c +b-o *!*@x vjt"))
    assert any(c.startswith("RAW:guard KICK #c pippo") for c in out)


def test_vjt_deops_himself_stays_deopped():
    w, gd = setup()
    run(w, gd, L(VJT, "JOIN #c"))
    run(w, gd, L(AMB, "MODE #c +o vjt"))
    assert run(w, gd, L(VJT, "MODE #c -o vjt")) == []


def test_kick_of_vjt_is_punished():
    w, gd = setup()
    run(w, gd, L(VJT, "JOIN #c"))
    out = run(w, gd, L(EVIL, "KICK #c vjt :fuori"))
    assert len(out) == 1 and out[0].startswith("RAW:guard KICK #c pippo :")


def test_vjt_kicking_a_guardian_is_fine():
    w, gd = setup(names="@Ambrogio @MikeBongiorno")
    g.feed(w, gd, L("MikeBongiorno!~mike@ti.faccio.le.domande.mentre.sniffo.org", "PRIVMSG #c :x"), None)
    run(w, gd, L(VJT, "JOIN #c"))
    assert run(w, gd, L(VJT, "KICK #c MikeBongiorno :vai")) == []


def test_kick_of_partner_is_punished():
    w, gd = setup(names="@Ambrogio @MikeBongiorno pippo")
    g.feed(w, gd, L("MikeBongiorno!~mike@ti.faccio.le.domande.mentre.sniffo.org", "PRIVMSG #c :x"), None)
    out = run(w, gd, L(EVIL, "KICK #c MikeBongiorno :via"))
    assert out and out[0].startswith("RAW:guard KICK #c pippo")


def test_getting_ops_ops_vjt_already_there():
    w, gd = setup(names="Ambrogio pippo")
    run(w, gd, L(VJT, "JOIN #c"))
    assert run(w, gd, L(EVIL, "MODE #c +o Ambrogio")) == ["RAW:guard MODE #c +o vjt"]


def test_nick_change_keeps_identity():
    w, gd = setup()
    run(w, gd, L(VJT, "JOIN #c"))
    run(w, gd, L(AMB, "MODE #c +o vjt"))
    run(w, gd, L(VJT, "NICK vjt`zZz"))
    out = run(w, gd, L(EVIL, "MODE #c -o vjt`zZz"))
    assert out[-1] == "RAW:guard MODE #c +o vjt`zZz"


def test_server_mode_is_not_punished():
    w, gd = setup()
    run(w, gd, L(VJT, "JOIN #c"))
    run(w, gd, L(AMB, "MODE #c +o vjt"))
    assert run(w, gd, L("irc.example", "MODE #c -o vjt")) == []


# Bitch mode (vjt 2026-11-09, dopo il takeover di #disagio): op dati da fuori
# a gente di fuori si riprendono, insieme all'op di chi li ha dati.

def bitch_setup(monkeypatch, names="@Ambrogio @pippo tizio caio sempronio"):
    monkeypatch.setattr(g, "BITCH", {"#c"})
    return setup(names=names)


def test_bitch_foreign_op_reverted_opper_deopped_and_kicked(monkeypatch):
    w, gd = bitch_setup(monkeypatch)
    out = run(w, gd, L(EVIL, "MODE #c +o tizio"))
    assert out[0] == "RAW:guard MODE #c -oo tizio pippo"
    assert out[1].startswith("RAW:guard KICK #c pippo :ao?!")
    assert len(out) == 2


def test_bitch_inert_off_list(monkeypatch):
    monkeypatch.setattr(g, "BITCH", {"#altro"})
    w, gd = setup(names="@Ambrogio @pippo tizio")
    assert run(w, gd, L(EVIL, "MODE #c +o tizio")) == []


def test_bitch_inert_without_ops(monkeypatch):
    w, gd = bitch_setup(monkeypatch, names="Ambrogio @pippo tizio")
    assert run(w, gd, L(EVIL, "MODE #c +o tizio")) == []


def test_bitch_outsider_opped_by_vjt_is_reverted_vjt_untouched(monkeypatch):
    w, gd = bitch_setup(monkeypatch)
    run(w, gd, L(VJT, "JOIN #c"))
    run(w, gd, L(AMB, "MODE #c +o vjt"))
    assert run(w, gd, L(VJT, "MODE #c +o tizio")) == ["RAW:guard MODE #c -o tizio"]


def test_bitch_outsider_opped_by_friend_is_reverted_friend_untouched(monkeypatch):
    w, gd = bitch_setup(monkeypatch, names="@Ambrogio @tom tizio")
    g.feed(w, gd, L(TOM, "PRIVMSG #c :x"), None)
    assert run(w, gd, L(TOM, "MODE #c +o tizio")) == ["RAW:guard MODE #c -o tizio"]


def test_bitch_friend_opping_friend_stays(monkeypatch):
    w, gd = bitch_setup(monkeypatch, names="@Ambrogio @tom gosub")
    g.feed(w, gd, L(TOM, "PRIVMSG #c :x"), None)
    g.feed(w, gd, L(GOSUB, "PRIVMSG #c :x"), None)
    assert run(w, gd, L(TOM, "MODE #c +o gosub")) == []


def test_friends_are_opped_on_join():
    for src in (TOM, SHINOBI, GOSUB):
        w, gd = setup()
        nick = src.partition("!")[0]
        assert run(w, gd, L(src, "JOIN #c")) == [f"RAW:guard MODE #c +o {nick}"]


def test_friend_from_another_host_is_an_outsider():
    w, gd = setup()
    assert run(w, gd, L("tom!~tom@2a01-239-2fa.example", "JOIN #c")) == []


def test_bitch_outsider_opping_vjt_is_fine(monkeypatch):
    w, gd = bitch_setup(monkeypatch)
    run(w, gd, L(VJT, "JOIN #c"))
    assert run(w, gd, L(EVIL, "MODE #c +o vjt")) == []


def test_bitch_server_op_reverted(monkeypatch):
    w, gd = bitch_setup(monkeypatch)
    assert run(w, gd, L("irc.example", "MODE #c +o tizio")) == ["RAW:guard MODE #c -o tizio"]


def test_bitch_server_op_of_vjt_stays(monkeypatch):
    w, gd = bitch_setup(monkeypatch)
    run(w, gd, L(VJT, "JOIN #c"))
    assert run(w, gd, L("irc.example", "MODE #c +o vjt")) == []


def test_bitch_batches_three_per_line(monkeypatch):
    w, gd = bitch_setup(monkeypatch)
    out = run(w, gd, L(EVIL, "MODE #c +ooo tizio caio sempronio"))
    assert out[:2] == ["RAW:guard MODE #c -ooo tizio caio sempronio",
                       "RAW:guard MODE #c -o pippo"]
    assert out[2].startswith("RAW:guard KICK #c pippo :")


def test_bitch_r_edit_reverted(monkeypatch):
    w, gd = bitch_setup(monkeypatch)
    out = run(w, gd, L(EVIL, "MODE #c -R+R *!*@buono *!*@cattivo"))
    assert out == ["RAW:guard MODE #c +R *!*@buono", "RAW:guard MODE #c -R *!*@cattivo"]


def test_bitch_r_edit_by_vjt_stays(monkeypatch):
    w, gd = bitch_setup(monkeypatch)
    run(w, gd, L(VJT, "JOIN #c"))
    assert run(w, gd, L(VJT, "MODE #c +R *!*@amico")) == []


def test_bitch_guardian_own_deop_does_not_loop(monkeypatch):
    w, gd = bitch_setup(monkeypatch)
    run(w, gd, L(EVIL, "MODE #c +o tizio"))
    assert run(w, gd, L(AMB, "MODE #c -oo tizio pippo")) == []
