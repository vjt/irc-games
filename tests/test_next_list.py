"""`!list` in #grappa -> one line pointing at #sbiffo (vjt, #grappa 2026-11-08).

Ambrogio answers in channel, clean register, throttled per nick (10') and per
channel (60'') so listers cannot make it flood. Only #grappa, never a vote,
never during backfill.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import next_counter  # noqa: E402

BEST = ("dio", "madonna", "cristo", "porco", "porca")


def line(nick, chan, text):
    return f"22:02:22 < :{nick}!~u@host.example PRIVMSG {chan} :{text}"


@pytest.fixture
def said(monkeypatch):
    out = []
    monkeypatch.setattr(next_counter, "say", lambda chan, text: out.append((chan, text)))
    next_counter._list_by_nick.clear()
    next_counter._list_by_chan.clear()
    return out


def run(text, nick="Mick85", chan="#grappa", ts=1000.0, talk=True):
    data = next_counter.empty()
    changed = next_counter.process(line(nick, chan, text), data, ts=ts, talk=talk)
    assert changed is False
    assert data["votes"] == {}


@pytest.mark.parametrize("text", ["!list", "!lista", "!LIST", "! list", "!list  ", "!lista film"])
def test_list_points_at_sbiffo(said, text):
    run(text)
    assert said == [("#grappa", "Mick85: il warez sta su #sbiffo")]


def test_line_is_clean(said):
    run("!list")
    assert not any(b in said[0][1].lower() for b in BEST)


@pytest.mark.parametrize("text", ["!listone", "!sito", "ho fatto !list", "list", "!next"])
def test_other_lines_are_not_list(said, text):
    run(text)
    assert all("#sbiffo" not in t for _c, t in said)


def test_only_grappa(said):
    run("!list", chan="#sniffo")
    run("!list", chan="#sbiffo")
    assert said == []


def test_backfill_stays_mute(said):
    run("!list", talk=False)
    assert said == []


def test_bots_ignored(said):
    run("!list", nick="EliteWarez")
    run("!list", nick="Ambrogio")
    assert said == []


def test_throttle_per_nick(said):
    run("!list", ts=1000.0)
    run("!lista", ts=1000.0 + next_counter.LIST_CHAN_SEC + 1)
    assert len(said) == 1
    run("!list", ts=1000.0 + next_counter.LIST_NICK_SEC + 1)
    assert len(said) == 2


def test_throttle_per_channel(said):
    run("!list", nick="a", ts=1000.0)
    run("!list", nick="b", ts=1010.0)
    assert [t for _c, t in said] == ["a: il warez sta su #sbiffo"]
    run("!list", nick="b", ts=1000.0 + next_counter.LIST_CHAN_SEC + 1)
    assert [t for _c, t in said][-1] == "b: il warez sta su #sbiffo"
