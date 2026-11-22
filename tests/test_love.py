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
    assert love.target_of("!love \x034,20\x0313,37\x0300,00") is None
    assert love.target_of("!love \x0304nano\x03") == "nano"
    assert love.target_of("!love \x04ff0000,00ff00nano") == "nano"
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
    # !slap e !pernacchio (vjt 2026-10-10): pool proprio, target sempre
    # nominato, niente doppioni, niente frasi prese in prestito. Le /me non
    # dicono piu' chi le manda: il comando sta una riga sopra (Malekith,
    # #sbiffo 2026-10-12 12:59, «vai» di vjt).
    for cmd in ("slap", "pernacchio", "sculaccia"):
        pool = love.POOLS[cmd]
        assert len(pool) == len(set(pool)) >= 20, cmd
        assert not set(pool) & set(love.PHRASES), cmd
        for p in pool:
            assert "{to}" in p, p
            assert p.startswith("/me ") != ("{from}" in p), p
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


def test_the_bot_never_targets_itself():
    # vjt 2026-10-10 13:54: `!sculaccia Ambrogio` bounces on the sender.
    lv = fresh()
    for i, cmd in enumerate(("slap", "pernacchio", "sculaccia", "love")):
        _, text = lv.handle(line("vjt", "#sbiffo", f"!{cmd} AMBROGIO"), now=i * 10)
        assert "Ambrogio" not in text and "AMBROGIO" not in text
        assert "vjt" in text


def test_skip_turns_commands_off_per_channel():
    # vjt 2026-10-10 13:57: on #cybernet TOOLS does !love/!slap, keep the rest.
    skip = love.parse_skip("#Cybernet:love,slap; #x:love")
    assert skip == {"#cybernet": {"love", "slap"}, "#x": {"love"}}
    lv = love.Love(rng=random.Random(1), cooldown=0,
                   chans={"#cybernet", "#italia"}, skip=skip)
    assert lv.handle(line("a", "#cybernet", "!love b"), now=0) is None
    assert lv.handle(line("a", "#cybernet", "!slap b"), now=1) is None
    assert lv.handle(line("a", "#cybernet", "!prrr b"), now=2)
    assert lv.handle(line("a", "#cybernet", "!sculaccia b"), now=3)
    assert lv.handle(line("a", "#italia", "!love b"), now=4)


def test_doccia_and_sessualizza_are_action_pools():
    # vjt 2026-10-10 14:01 (tom's ask); !sessua is the short alias.
    assert love.parse("!doccia tom") == ("doccia", "tom")
    assert love.parse("!sessua tom") == ("sessua", "tom")
    assert love.parse("!sessualizza tom") == ("sessua", "tom")
    for cmd in ("doccia", "sessua"):
        assert all(p.startswith("/me ") and "{to}" in p and "{from}" not in p
                   for p in love.POOLS[cmd])


def test_deck_says_the_whole_pool_before_repeating():
    # vjt, #sbiffo 2026-10-12: «sempre le stesse frasi». One deck per
    # (channel, command): N calls say N different lines, then it reshuffles.
    for cmd, pool in love.POOLS.items():
        # !disagio vive solo su #disagio; gli altri comandi girano anche li'.
        lv = love.Love(rng=random.Random(1), cooldown=0, chans={"#disagio"})
        seen = [lv.handle(line("a", "#disagio", f"!{cmd} b"), now=i)[1]
                for i in range(len(pool))]
        assert len(set(seen)) == len(pool), cmd
        nxt = lv.handle(line("a", "#disagio", f"!{cmd} b"), now=len(pool))[1]
        assert nxt != seen[-1], cmd


def test_decks_are_per_command():
    # A !slap in the middle does not burn cards of the !sessua deck.
    lv = fresh(cooldown=0)
    pool = love.POOLS["sessua"]
    seen = []
    for i in range(len(pool)):
        seen.append(lv.handle(line("a", "#sniffo", "!sessua b"), now=2 * i)[1])
        lv.handle(line("a", "#sniffo", "!slap b"), now=2 * i + 1)
    assert len(set(seen)) == len(pool)


def test_the_round_survives_a_restart(tmp_path):
    # vjt, #sbiffo 2026-11-21 13:31: the RAM deck reshuffled at the m42
    # reboot and four lines came back. A restart keeps the round.
    state = tmp_path / "love.json"
    pool = love.POOLS["disagio"]
    seen = []
    for i in range(len(pool)):
        # A new process every few lines, each one reading the same state.
        if i % 7 == 0:
            lv = love.Love(rng=random.Random(i), cooldown=0,
                           chans={"#disagio"}, state=state)
        seen.append(lv.handle(line("a", "#disagio", "!disagio b"), now=i)[1])
    assert len(set(seen)) == len(pool)
    lv = love.Love(rng=random.Random(0), cooldown=0, chans={"#disagio"},
                   state=state)
    assert lv.handle(line("a", "#disagio", "!disagio b"), now=0)[1] != seen[-1]


def test_a_broken_state_is_a_fresh_round(tmp_path):
    state = tmp_path / "love.json"
    for junk in ("", "[]", '{"said": 3}', "{"):
        state.write_text(junk)
        lv = love.Love(cooldown=0, chans={"#disagio"}, state=state)
        assert lv.handle(line("a", "#disagio", "!disagio b"), now=0)


def test_every_pool_has_at_least_100_lines():
    # vjt, #sbiffo 2026-10-12 12:49: «almeno 100 frasi ognuno».
    for cmd, pool in love.POOLS.items():
        assert len(set(pool)) == len(pool) >= 100, (cmd, len(pool))


def test_prot_pool_and_aliases():
    # vjt, #sbiffo 2026-10-15: !prot, Ambrogio narra chi scorreggia su chi.
    pool = love.POOLS["prot"]
    assert len(pool) == len(set(pool)) == 100
    assert not set(pool) & set(love.PERNACCHIO_PHRASES)
    for p in pool:
        assert "{from}" in p and "{to}" in p and not p.startswith("/me "), p
        out = love.render(p, "katia", "vjt")
        assert "{" not in out and "}" not in out, p
    for alias in ("!prot vjt", "!scorreggia vjt", "!PRAAAT vjt", "!peto vjt", "!brap vjt"):
        assert love.parse(alias) == ("prot", "vjt"), alias
    for t in ("!prot", "!protesta vjt", "!petone vjt"):
        assert love.parse(t) is None, t


def test_vomito_pool_and_alias():
    # vjt, #sbiffo 2026-10-16: !vomito / !vomita, su richiesta di kazam.
    pool = love.POOLS["vomito"]
    assert len(pool) == len(set(pool)) == 100
    for p in pool:
        assert "{from}" in p and "{to}" in p and not p.startswith("/me "), p
        out = love.render(p, "kazam", "vjt")
        assert "{" not in out and "}" not in out, p
    for t in ("!vomito vjt", "!VOMITA vjt"):
        assert love.parse(t) == ("vomito", "vjt"), t
    for t in ("!vomito", "!vomitare vjt"):
        assert love.parse(t) is None, t


def test_disagio_pool():
    # vjt, query cifrata 2026-11-20 22:45: «un centinaio di frasi disagiate».
    # Solo il disagiato in scena: col !disagio nudo mittente e target coincidono.
    pool = love.POOLS["disagio"]
    assert len(pool) == len(set(pool)) >= 100
    for p in pool:
        assert "{to}" in p and "{from}" not in p and not p.startswith("/me "), p
        out = love.render(p, "vjt", "tom")
        assert "{" not in out and "}" not in out, p


def test_disagio_bare_targets_the_caller():
    # «con !disagio il target è chi manda il comando».
    assert love.parse("!disagio") == ("disagio", None)
    assert love.parse("!DISAGIO   ") == ("disagio", None)
    assert love.parse("!disagio tom pure") == ("disagio", "tom")
    for t in ("!disagiato tom", "ciao !disagio"):
        assert love.parse(t) is None, t
    lv = love.Love(rng=random.Random(1), cooldown=0, chans={"#disagio"})
    _, text = lv.handle(line("vjt", "#disagio", "!disagio"), now=0)
    assert "\x02vjt\x02" in text
    _, text = lv.handle(line("vjt", "#disagio", "!disagio tom"), now=1)
    assert "\x02tom\x02" in text and "vjt" not in text


def test_disagio_only_on_disagio():
    # «attivo possibilmente solo su #disagio»: gli altri canali tacciono,
    # e gli altri comandi restano dove sono.
    lv = love.Love(rng=random.Random(1), cooldown=0,
                   chans={"#disagio", "#cybernet", "#sbiffo"})
    assert lv.handle(line("a", "#cybernet", "!disagio b"), now=0) is None
    assert lv.handle(line("a", "#sbiffo", "!disagio"), now=1) is None
    assert lv.handle(line("a", "#cybernet", "!prrr b"), now=2)
    assert lv.handle(line("a", "#DISAGIO", "!disagio b"), now=3)


def test_bare_stays_silent_for_the_others():
    for cmd in ("love", "slap", "pernacchio", "prot", "vomito"):
        assert love.parse(f"!{cmd}") is None, cmd


# --- disagio on join (vjt 2026-11-22: «chi joina si becca il disagio...
# attenzione solo al join flood») ---

ONJOIN_CHANS = {"#disagio", "#sbiffo", "#sniffo"}


def join(nick, chan, colon=False):
    return f"12:00:00 < :{nick}!~id@host.example JOIN {':' if colon else ''}{chan}"


def door(**kw):
    # only={} lascia passare il disagio anche su #sbiffo: qui si prova l'ingresso.
    kw.setdefault("cooldown", 0)
    kw.setdefault("onjoin", {"#disagio", "#sbiffo"})
    kw.setdefault("only", {})
    return love.Love(rng=random.Random(1), chans=ONJOIN_CHANS,
                     join_skip={"mikebongiorno", "vjt-claude"}, **kw)


def disagio_for(nick):
    return {love.render(p, nick, nick) for p in love.POOLS["disagio"]}


def test_join_both_shapes_get_a_disagio_aimed_at_the_newcomer():
    lv = door()
    chan, text = lv.handle(join("tom", "#disagio"), now=0)
    assert chan == "#disagio" and text in disagio_for("tom")
    chan, text = lv.handle(join("Nano", "#DISAGIO", colon=True), now=10)
    assert chan == "#DISAGIO" and text in disagio_for("Nano")


def test_never_the_bot_itself_nor_the_skip_list():
    lv = door()
    for t, nick in enumerate(("MikeBongiorno", "mikebongiorno-123",
                              "VJT-CLAUDE", "vjt-claude_")):
        assert lv.handle(join(nick, "#disagio"), now=t * 10) is None, nick
    # Il bot stesso, anche col suffisso della collisione di bot.py.
    for t, nick in enumerate(("Ambrogio", "AMBROGIO", "Ambrogio-991", "Ambrogio_")):
        lv = door()
        assert lv.handle(join(nick, "#disagio", colon=True), now=t) is None, nick
    # Un prefisso non e' un suffisso: Ambrogione e' un umano.
    assert door().handle(join("Ambrogione", "#disagio"), now=0)


def test_own_join_with_collision_suffix_opens_the_quiet_minute():
    # Ambrogio su Azzurra era «Ambrogio-991» (2026-11-22).
    lv = door()
    own = "12:00:00 < :Ambrogio-991!~ambrogio@ci.pensa.ambrogio.con.la.grappa.chat JOIN :#disagio"
    assert lv.handle(own, now=0) is None
    assert lv.handle(join("tom", "#disagio"), now=30) is None
    assert lv.handle(join("bob", "#sbiffo"), now=30)  # l'altro canale non tace
    _, text = lv.handle(join("tom", "#disagio"), now=61)
    assert text in disagio_for("tom")


def test_one_per_nick_per_channel_every_six_hours():
    lv = door()
    assert lv.handle(join("tom", "#disagio"), now=0)
    assert lv.handle(join("tom", "#disagio"), now=100) is None
    assert lv.handle(join("TOM", "#disagio"), now=21599) is None
    assert lv.handle(join("tom", "#sbiffo"), now=200)  # per canale
    assert lv.handle(join("Tom", "#disagio"), now=21600)
    assert love.JOIN_EVERY == 21600 or "LOVE_JOIN_EVERY" in os.environ


def test_three_a_minute_per_channel():
    lv = door()
    outs = [lv.handle(join(f"n{i}", "#disagio"), now=i * 11) for i in range(5)]
    assert [o is not None for o in outs] == [True, True, True, False, False]
    assert lv.handle(join("n9", "#sbiffo"), now=50)  # l'altro canale ha i suoi 3
    assert lv.handle(join("n5", "#disagio"), now=61)  # quello delle 0 e' uscito


def test_burst_says_troppo_disagio_once_then_silence():
    lv = door()
    outs = [lv.handle(join(f"n{i}", "#disagio"), now=i) for i in range(5)]
    assert all(outs[i][1] in disagio_for(f"n{i}") for i in range(3))
    assert outs[3] is None  # tre al minuto
    assert outs[4] == ("#disagio", outs[4][1]) and outs[4][1] in love.TROPPO_DISAGIO
    # Dentro i 120 s: silenzio, anche una seconda raffica.
    for i in range(5, 15):
        assert lv.handle(join(f"m{i}", "#disagio"), now=i) is None, i
    # L'ultima raffica (t=14) ha allungato il silenzio fino a 134.
    assert lv.handle(join("late", "#disagio"), now=130) is None
    assert lv.handle(join("calm", "#disagio"), now=140)
    # L'altro canale non tace.
    assert lv.handle(join("x", "#sbiffo"), now=20)


def test_second_burst_within_ten_minutes_has_no_troppo():
    lv = door()
    outs = [lv.handle(join(f"a{i}", "#disagio"), now=i) for i in range(5)]
    assert outs[4][1] in love.TROPPO_DISAGIO
    # Raffica dopo il silenzio ma entro 10 minuti: nessun «troppo disagio».
    outs = [lv.handle(join(f"b{i}", "#disagio"), now=300 + i) for i in range(10)]
    assert not any(o and o[1] in love.TROPPO_DISAGIO for o in outs)
    assert all(o is None for o in outs[4:])
    # Passati i 10 minuti la riga torna.
    outs = [lv.handle(join(f"c{i}", "#disagio"), now=700 + i) for i in range(5)]
    assert outs[4][1] in love.TROPPO_DISAGIO


def test_netsplit_comeback_is_not_an_arrival():
    lv = door()
    lv.handle("12:00:00 < :tom!~id@host QUIT :raptor.azzurra.chat ruby.azzurra.chat", now=0)
    lv.handle("12:00:00 < :bob!~id@host QUIT :Quit: vado a casa.", now=0)
    lv.handle("12:00:00 < :eve!~id@host QUIT :Ping timeout: 240 seconds", now=0)
    assert lv.handle(join("tom", "#disagio"), now=100) is None
    assert lv.handle(join("bob", "#disagio"), now=110)
    assert lv.handle(join("eve", "#disagio"), now=120)


def test_join_outside_onjoin_channels_is_ignored():
    lv = door()
    assert lv.handle(join("tom", "#sniffo"), now=0) is None   # in chans, non in onjoin
    lv = door(onjoin={"#disagio", "#grappa"})
    assert lv.handle(join("tom", "#grappa"), now=0) is None   # in onjoin, non in chans
    # Senza LOVE_ONJOIN non saluta nessuno.
    assert love.Love(rng=random.Random(1), cooldown=0, chans=ONJOIN_CHANS,
                     onjoin=set()).handle(join("tom", "#disagio"), now=0) is None
    # LOVE_ONLY vale anche qui: !disagio confinato a #disagio, #sbiffo tace.
    lv = door(only={"disagio": {"#disagio"}})
    assert lv.handle(join("tom", "#sbiffo"), now=0) is None
    assert lv.handle(join("tom", "#disagio"), now=10)
    # E LOVE_SKIP.
    lv = door(skip={"#disagio": {"disagio"}})
    assert lv.handle(join("tom", "#disagio"), now=0) is None


def test_onjoin_shares_deck_and_cooldown_with_the_command():
    lv = door(cooldown=2)
    assert lv.handle(join("tom", "#disagio"), now=100)
    assert lv.handle(line("a", "#disagio", "!disagio"), now=101) is None  # cooldown
    assert lv.handle(line("a", "#disagio", "!disagio"), now=102)
    assert lv.handle(join("bob", "#disagio"), now=103) is None            # cooldown
    # Stesso giro: comandi e ingressi pescano dallo stesso mazzo.
    lv = door()
    pool = love.POOLS["disagio"]
    seen = []
    for i in range(len(pool)):
        if i % 3 == 0:
            text = lv.handle(join(f"n{i}", "#disagio"), now=i * 30)[1]
            text = text.replace(f"\x02n{i}\x02", "\x02a\x02")
        else:
            text = lv.handle(line("a", "#disagio", "!disagio"), now=i * 30)[1]
        seen.append(text)
    assert len(set(seen)) == len(pool)


def test_privmsg_commands_unchanged_by_onjoin():
    lv = door()
    # Una PRIVMSG che contiene una riga di JOIN non e' un ingresso.
    assert lv.handle(line("tom", "#disagio", "< :x!y@z JOIN #disagio"), now=0) is None
    assert lv.handle(line("tom", "#disagio", "x 12:00:00 < :x!y@z JOIN :#disagio"), now=1) is None
    _, text = lv.handle(line("vjt", "#sbiffo", "!love tom"), now=2)
    assert text in {love.render(p, "vjt", "tom") for p in love.PHRASES}
    _, text = lv.handle(line("vjt", "#sbiffo", "!slap AMBROGIO"), now=3)
    assert "vjt" in text and "AMBROGIO" not in text
    # Le righe in uscita del bot non sono ingressi.
    assert lv.handle("12:00:00 > JOIN #disagio", now=4) is None


def test_troppo_disagio_pool():
    pool = love.TROPPO_DISAGIO
    assert 5 <= len(pool) == len(set(pool)) <= 8
    bad = re.compile(r"[\x00-\x1f\x7f{}]")
    for p in pool:
        assert not bad.search(p) and not p.startswith("/me "), p
    assert "troppo" not in love.POOLS and love.parse("!troppo tom") is None
