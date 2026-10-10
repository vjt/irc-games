"""`!help` / `!giochi` di MikeBongiorno: una risposta sola (impiccato.py), in
canale, con cooldown per canale, e un testo che non promette comandi che non
esistono.

Niente FIFO e niente IRC: talk=False, `sent` raccoglie le righe.
"""
import os
import random
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import impiccato as I  # noqa: E402
import sillabah as S  # noqa: E402

DOM = [{"q": "Domanda?", "a": "Risposta", "cat": "t"}]


def quiz(tmp_path, chans=("#t",)):
    return I.Impiccato("test", "/dev/null", "", tmp_path / "s.json",
                       set(chans), DOM, talk=False)


def sill(tmp_path, chans=("#t",)):
    return S.Sillabah("test", "/dev/null", "", tmp_path / "x.json", set(chans),
                      S.Dizionario(["riso", "rima"]), talk=False,
                      rng=random.Random(1))


@pytest.mark.parametrize("cmd", ["!help", "!giochi", "!HELP", "! giochi "])
def test_help_va_in_canale(tmp_path, cmd):
    g = quiz(tmp_path)
    assert g.handle("Pippo", "#t", cmd, now=100) is False
    assert g.sent == [("#t", line) for line in I.HELP_LINES]


def test_help_solo_nei_canali_configurati(tmp_path):
    g = quiz(tmp_path)
    g.handle("Pippo", "#altro", "!help", now=100)
    assert g.sent == []


def test_help_non_prende_argomenti_ne_varianti(tmp_path):
    g = quiz(tmp_path)
    for t in ("!help trivial", "!helpme", "!gioco", "help"):
        g.handle("Pippo", "#t", t, now=100)
    assert g.sent == []


def test_cooldown_per_canale(tmp_path):
    g = quiz(tmp_path, chans=("#t", "#u"))
    n = len(I.HELP_LINES)
    g.handle("Pippo", "#t", "!help", now=100)
    g.handle("Pluto", "#t", "!giochi", now=100 + I.HELP_COOLDOWN - 1)
    assert len(g.sent) == n                 # stesso canale, altro nick: muto
    g.handle("Pluto", "#u", "!help", now=101)
    assert len(g.sent) == 2 * n             # un altro canale non aspetta
    g.handle("pippo", "#t", "!help", now=100 + I.HELP_COOLDOWN)
    assert len(g.sent) == 3 * n


def test_help_non_tocca_la_partita(tmp_path):
    g = quiz(tmp_path)
    g.handle("a", "#t", "!trivial t", now=100)
    q = dict(g.game("#t"))
    g.handle("b", "#t", "!help", now=101)
    assert g.game("#t") == q


def test_righe_corte_e_senza_markdown():
    assert 1 <= len(I.HELP_LINES) <= 4
    for line in I.HELP_LINES:
        assert len(line.encode("utf-8")) <= I.MAX_LINE, line
        assert "`" not in line and "*" not in line, line


def test_sillabah_non_risponde_a_help(tmp_path):
    s = sill(tmp_path)
    for t in ("!help", "!giochi"):
        assert s.handle("Pippo", "#t", t, now=0) is False
        assert s.handle("Pippo", "MikeBongiorno", t, now=0) is False
    assert s.sent == []


# Ogni comando del testo, istanziato, deve combaciare col pattern del gioco
# che lo serve. Se qualcuno rinomina un trigger e scorda l'help, salta qui.
COMANDI = {
    "!trivial babbani 5": I.CMD_PAT, "!trivia babbani": I.CMD_PAT,
    "!allegria babbani": I.CMD_PAT, "!trivial": I.CMD_PAT,
    ".h": I.HINT_PAT, ".v": I.VOWEL_PAT, "!stop": I.STOP_PAT,
    "!classifica": I.CLASSIFICA_PAT, "!classifica trivial": I.CLASSIFICA_PAT,
    "!classifica sillabah": S.CLASSIFICA_PAT,
    "!sillabah": S.CMD_PAT, "!sillabah 5": S.CMD_PAT,
    "!sillabah stop": S.CMD_PAT, "-delete": S.DELETE_PAT,
    "!sillaba": S.SILLABA_PAT, "!valori": S.VALORI_PAT,
    "!punti": S.PUNTI_PAT, "!punti Pippo": S.PUNTI_PAT,
}


@pytest.mark.parametrize("cmd,pat", COMANDI.items())
def test_comandi_esistono(cmd, pat):
    assert pat.match(cmd), cmd


def test_classifica_risponde_al_nome_usato_nell_help():
    assert "trivial" in I.MY_GAME and "sillabah" in S.MY_GAME


def test_help_elenca_solo_comandi_noti():
    """Ogni token `!xxx` o `.x` del testo e' fra quelli verificati sopra."""
    noti = {c.split()[0] for c in COMANDI} | {"!help", "!giochi"}
    # via bold e colori: il "10" di \x0310 attaccato al "!" nasconderebbe il comando
    testo = re.sub(r'\x03\d{0,2}(?:,\d{1,2})?|\x02', "", " ".join(I.HELP_LINES))
    trovati = set(re.findall(r'(?<!\w)[!.-][a-z]+', testo))
    assert trovati <= noti, trovati - noti
    # e i comandi principali ci sono tutti
    for c in ("!trivial", "!stop", ".h", ".v", "!sillabah", "!sillaba",
              "!valori", "!punti", "!classifica", "-delete"):
        assert c in trovati, c
