"""Invarianti del !love di Ambrogio, rifatto su quello di TOOLS (vjt 2026-10-07).

Il comportamento di riferimento e' quello misurato sullo scrollback grappa di
IRCnet: target = prima parola dopo il comando, `!love` nudo = silenzio, una
riga sola con mittente e target sostituiti, comando case-insensitive.
"""
import os
import random
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import love  # noqa: E402

CHANS = {"#sniffo", "#sbiffo"}


def line(nick, chan, text):
    return f"12:00:00 < :{nick}!~id@host.example PRIVMSG {chan} :{text}"


def fresh(seed=1, cooldown=2):
    return love.Love(rng=random.Random(seed), cooldown=cooldown, chans=CHANS)


def test_every_phrase_renders_with_the_target():
    # Ogni template deve formattare (niente graffe spaiate) e nominare il
    # target; il mittente puo' mancare, TOOLS ne aveva uno cosi'.
    for p in love.PHRASES:
        out = love.render(p, "aNNuCCia", "nano")
        assert "{" not in out and "}" not in out, p
        assert "nano" in out, p
    tools_from = sum("{from}" in p for p in love.TOOLS_PHRASES)
    assert tools_from == len(love.TOOLS_PHRASES) - 1, tools_from
    # le nuove portano sempre entrambi
    for p in love.NEW_PHRASES:
        assert "{from}" in p and "{to}" in p, p


def test_pools_are_distinct_and_sized():
    assert len(love.TOOLS_PHRASES) == len(set(love.TOOLS_PHRASES)) == 41
    assert len(set(love.NEW_PHRASES)) == len(love.NEW_PHRASES) >= 40
    assert not set(love.TOOLS_PHRASES) & set(love.NEW_PHRASES)


def test_target_is_the_first_word():
    assert love.target_of("!love inverno nucleare") == "inverno"
    assert love.target_of("!love nano nudo") == "nano"
    assert love.target_of("!love |Gogeta| ") == "|Gogeta|"
    assert love.target_of("!LOVE katia") == "katia"
    assert love.target_of("  !love  Sh|m0d4") == "Sh|m0d4"


def test_not_the_command():
    for t in ("!love", "!love   ", "!lovely nano", "ciao !love nano",
              "love nano", "!lov nano"):
        assert love.target_of(t) is None, t


def test_target_sanitized_and_capped():
    assert love.target_of("!love na\x02no") == "nano"
    assert len(love.target_of("!love " + "x" * 500)) == love.TARGET_MAX


def test_handle_answers_in_the_same_channel_with_both_nicks():
    lv = fresh()
    seen = set()
    for i in range(200):
        out = lv.handle(line("tom", "#sbiffo", "!love nano"), now=i * 10)
        assert out, i
        chan, text = out
        assert chan == "#sbiffo"
        assert "nano" in text
        seen.add(text)
    # estrazione davvero casuale: in 200 colpi escono tante frasi diverse
    assert len(seen) > 50, len(seen)


def test_other_channels_and_lines_are_ignored():
    lv = fresh()
    assert lv.handle(line("tom", "#grappa", "!love nano"), now=0) is None
    assert lv.handle(line("tom", "#sniffo", "!love"), now=0) is None
    assert lv.handle(line("tom", "#sniffo", "ti amo nano"), now=0) is None
    assert lv.handle("12:00:00 > PRIVMSG #sniffo :!love nano", now=0) is None
    # il canale si confronta senza badare al case
    assert lv.handle(line("tom", "#SNIFFO", "!love nano"), now=0)


def test_cooldown_is_per_channel():
    lv = fresh(cooldown=2)
    assert lv.handle(line("a", "#sniffo", "!love b"), now=100)
    assert lv.handle(line("a", "#sniffo", "!love b"), now=101) is None
    assert lv.handle(line("a", "#sbiffo", "!love b"), now=101)
    # TOOLS rispondeva a colpi distanti 3 s: il cooldown deve lasciarli passare
    assert lv.handle(line("a", "#sniffo", "!love b"), now=103)


def test_no_immediate_repeat_in_a_channel():
    lv = fresh(cooldown=0)
    prev = None
    for i in range(500):
        _, text = lv.handle(line("a", "#sniffo", "!love b"), now=i)
        assert text != prev, (i, text)
        prev = text


def test_self_love_and_braces_in_nick():
    lv = fresh(cooldown=0)
    _, text = lv.handle(line("peluche", "#sniffo", "!love peluche"), now=0)
    assert "peluche" in text
    _, text = lv.handle(line("x", "#sniffo", "!love {to}{from}"), now=1)
    assert "{to}{from}" in text


def test_no_control_bytes_reach_the_fifo():
    # Le frasi non portano colori ne' a capo: una riga, una PRIVMSG.
    bad = re.compile(r"[\x00-\x1f\x7f]")
    for pool in love.POOLS.values():
        for p in pool:
            assert not bad.search(p), repr(p)


def test_slap_and_pernacchio_pools_render_with_both_nicks():
    # !slap e !pernacchio (vjt 2026-10-10): pool proprio, mittente e target
    # sempre nominati, niente doppioni, niente frasi prese in prestito.
    for cmd in ("slap", "pernacchio", "sculaccia"):
        pool = love.POOLS[cmd]
        assert len(pool) == len(set(pool)) >= 20, cmd
        assert not set(pool) & set(love.PHRASES), cmd
        for p in pool:
            assert "{from}" in p and "{to}" in p, p
            out = love.render(p, "katia", "vjt")
            assert "{" not in out and "}" not in out, p


def test_parse_knows_the_three_commands():
    assert love.parse("!slap nano nudo") == ("slap", "nano")
    assert love.parse("!PERNACCHIO vjt") == ("pernacchio", "vjt")
    assert love.parse("!love katia") == ("love", "katia")
    for t in ("!slap", "!pernacchio  ", "!slapped nano", "!pernacchione vjt",
              "ciao !slap nano"):
        assert love.parse(t) is None, t


def test_each_command_draws_from_its_own_pool():
    lv = fresh(cooldown=0)
    for i, cmd in enumerate(("slap", "pernacchio", "love") * 30):
        _, text = lv.handle(line("katia", "#sbiffo", f"!{cmd} vjt"), now=i)
        assert text in {love.render(p, "katia", "vjt")
                        for p in love.POOLS[cmd]}, (cmd, text)


def test_cooldown_is_shared_across_commands():
    # Un canale, un cooldown: alternare i comandi non aggira l'anti-flood.
    lv = fresh(cooldown=2)
    assert lv.handle(line("a", "#sbiffo", "!love b"), now=100)
    assert lv.handle(line("a", "#sbiffo", "!slap b"), now=101) is None
    assert lv.handle(line("a", "#sbiffo", "!pernacchio b"), now=101) is None
    assert lv.handle(line("a", "#sbiffo", "!pernacchio b"), now=102)


if __name__ == "__main__":
    test_every_phrase_renders_with_the_target()
    print("ok: ogni frase si formatta e nomina il target")
    test_pools_are_distinct_and_sized()
    print("ok: 41 frasi di TOOLS + nuove, nessun doppione")
    test_target_is_the_first_word()
    print("ok: target = prima parola, comando case-insensitive")
    test_not_the_command()
    print("ok: !love nudo, !lovely e menzioni in mezzo alla riga -> niente")
    test_target_sanitized_and_capped()
    print("ok: target ripulito e tagliato")
    test_handle_answers_in_the_same_channel_with_both_nicks()
    print("ok: risposta nello stesso canale, estrazione varia")
    test_other_channels_and_lines_are_ignored()
    print("ok: canali fuori lista e righe non-comando ignorate")
    test_cooldown_is_per_channel()
    print("ok: cooldown per canale, i 3 s di TOOLS passano")
    test_no_immediate_repeat_in_a_channel()
    print("ok: mai la stessa frase due volte di fila nello stesso canale")
    test_self_love_and_braces_in_nick()
    print("ok: autoamore e graffe nel nick")
    test_no_control_bytes_reach_the_fifo()
    print("ok: niente byte di controllo nelle frasi")
    test_slap_and_pernacchio_pools_render_with_both_nicks()
    print("ok: !slap e !pernacchio, pool propri e completi")
    test_parse_knows_the_three_commands()
    print("ok: parse riconosce i tre comandi e basta")
    test_each_command_draws_from_its_own_pool()
    print("ok: ogni comando pesca dal suo pool")
    test_cooldown_is_shared_across_commands()
    print("ok: cooldown condiviso fra i comandi")
    print("\nTUTTO VERDE, porco dio")


def test_pernacchio_aliases():
    for alias in ("!pernacchia vjt", "!prrr vjt", "!PRRR vjt"):
        assert love.parse(alias) == ("pernacchio", "vjt")
    assert love.parse("!prrrr vjt") is None


def test_slap_is_an_action_and_nicks_are_bold():
    # vjt 2026-10-10: !slap esce come CTCP ACTION, i nick in grassetto.
    for p in love.POOLS["slap"]:
        assert p.startswith("/me "), p
    out = love.render("{from} -> {to}", "katia", "vjt")
    assert out == "\x02katia\x02 -> \x02vjt\x02"


def test_sculaccia_is_a_ctcp_action_pool():
    # !sculaccia (vjt 2026-10-10 13:51): every phrase is a /me, like !slap.
    assert love.parse("!sculaccia vjt") == ("sculaccia", "vjt")
    assert all(p.startswith("/me ") for p in love.POOLS["sculaccia"])
