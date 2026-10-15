#!/usr/bin/env python3
"""cah — Cards Against Humanity in italiano, dietro a MikeBongiorno.
Richiesta di Malekith, via di vjt (staff 2026-10-15, «vai»).

Stessa forma di sillabah.py e impiccato.py: tail del bot.log, match dei
PRIVMSG, risposte sulla FIFO del bot. `bot.py` NON si tocca. I casi limite e
la regola per ognuno stanno in docs/cah_spec.md.

I verbi sono quelli di teeli/irc-cah (il bot che la crew usava su IRCnet),
con l'alias italiano accanto. Quelli di tavolo stanno sotto `!cah`: nudi
(`!start`, `!stop`, `!join`) li raccolgono gli altri giochi degli stessi canali.

  !cah [start [N]]   apre il tavolo (60s, almeno 3 giocatori); N = punti per vincere
  !cah join|entra    si siede (a partita viva: dalla mano dopo)
  !cah quit|esci     si alza
  !cah stop          chiude (chi l'ha aperta, o un chanop)
  !cah points|punti  punti della partita (senza partita: la classifica storica)
  !cah top|classifica  la classifica storica, mani vinte
  !cah cards|carte   rimanda le tue carte in privato
  !cah status|stato  a che punto siamo
  !play N [M]        gioca la carta N (e M); alias !gioco, !pick. In canale o in
                     query: il numero e' della TUA mano, non svela niente
  !winner N          lo czar sceglie; alias !vince, !pick

In query vale solo col `!` davanti: un numero nudo in query lo raccoglie anche
sillabah, che risponde a chiunque.

Le partite stanno in memoria: un riavvio le perde, in silenzio. Su disco solo
la classifica storica (mani vinte per nick, per rete).

Carte: cah_carte.json, {"nere": [...], "bianche": [...]}. Nelle nere `___` e'
un buco da riempire; quanti buchi, tante carte si giocano (almeno una).

CLI:
  cah.py --net azzurra           il daemon
  cah.py carte                   conta le carte
Env (formato di SILLABAH_NETS):
  CAH_HOME  CAH_CARTE  CAH_NETS=nome|log|fifo|#chan1,#chan2
"""
import json
import os
import random
import re
import select
import subprocess
import sys
import time
from collections import deque
from pathlib import Path

import ircgame
from ircgame import PRIVMSG_PAT, Outbox, Tracker, is_chan, joined_lines

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("CAH_HOME", HERE))
CARTE = Path(os.environ.get("CAH_CARTE", HERE / "cah_carte.json"))
BOT_NICK = os.environ.get("CAH_NICK", "MikeBongiorno")

LOBBY_S = 60         # attesa giocatori, sia all'apertura sia a partita in pausa
PLAY_S = 90
JUDGE_S = 90
PAUSA_S = 5
MIN_PLAYERS = 3
MIN_SUBS = 2         # sotto, la mano e' nulla: scegliere fra una carta sola non e' un gioco
HAND = 7
GOAL = 5
MAX_GOAL = 15
IDLE_ROUNDS = 2      # mani di fila senza nessuna giocata: chiude
MISSED_MAX = 2       # mani di fila saltate da un giocatore: e' fuori
TICK = 5
BLANK = "___"
HELP = ("!cah [start N] · entra · esci · stop · punti · top · carte · stato"
        " — in gioco: !play N, lo czar !vince N")


def load_nets():
    return ircgame.load_nets(
        "CAH_NETS", {"azzurra": {"log": REPO / "bot.log", "fifo": REPO / "bot.send",
                                 "chans": {"#sbiffo"}, "state": REPO / "cah_state.json"}},
        "cah_state", REPO)


def load_carte(path=None):
    d = json.loads(Path(path or CARTE).read_text(encoding="utf-8"))
    return list(d["nere"]), list(d["bianche"])


def picks(black):
    return max(1, black.count(BLANK))


def fill(black, cards):
    """La nera con le bianche in grassetto al posto dei buchi; senza buchi, in coda."""
    if BLANK not in black:
        return f"{black} \x02{cards[0]}\x02"
    out = black
    for c in cards:
        if not out.startswith(BLANK):
            c = minuscola(c)
        out = out.replace(BLANK, f"\x02{c.rstrip('.')}\x02", 1)
    return out


# Una bianca in mezzo alla frase perde la maiuscola, ma solo se comincia con
# un articolo o simili: «Mike Bongiorno» e «Debian» restano come sono.
MINUSCOLE = re.compile(r"^(?:Il|Lo|La|I|Gli|Le|Un|Uno|Una|L'|Un'|Tua|Tuo|Mio|Mia|"
                       r"Tre|Dieci|Quanto|Novantadue)\b")


def minuscola(c):
    return c[0].lower() + c[1:] if MINUSCOLE.match(c) else c


CAH_PAT = re.compile(r'^!\s*cah(?:\s+(?P<sub>\S+))?(?:\s+(?P<arg>\S+))?\s*$', re.I)
PLAY_PAT = re.compile(
    r'^!\s*(?P<verb>play|gioco|gioca|pick|winner|vince)\s+(?P<nums>[\d\s,]+)$', re.I)

SUB = {"start": "start", "inizia": "start", "join": "join", "entra": "join",
       "quit": "quit", "esci": "quit", "stop": "stop", "points": "points",
       "punti": "points", "top": "top", "classifica": "top", "cards": "cards",
       "carte": "cards", "status": "status", "stato": "status"}


class Cah:
    def __init__(self, net, log, fifo, state_path, chans, carte, rng=None):
        self.net = net
        self.log = str(log)
        self.state_path = Path(state_path)
        self.chans = {c.lower() for c in chans}
        self.nere, self.bianche = carte
        self.rng = rng or random.Random()
        self.games = {}                 # in memoria e basta: vedi la docstring
        self.scores = self.load()       # nick casefold -> {"nick", "points"}
        self.dirty = False
        self.tracker = Tracker()
        self.out = Outbox(fifo, "cah", self.tracker)
        self.ison = deque()             # ISON in volo: {nick casefold: canale}

    @property
    def sent(self):
        """Per le prove: tutto quello che sarebbe uscito, freno a parte."""
        self.out.flush(force=True)
        return self.out.sent

    # --- classifica storica ----------------------------------------------

    def load(self):
        d = ircgame.load_json(self.state_path, {})
        return d["scores"] if isinstance(d.get("scores"), dict) else {}

    def save(self):
        ircgame.save_json(self.state_path, {"scores": self.scores})
        self.dirty = False

    def top(self, n=10):
        rows = sorted(self.scores.values(), key=lambda r: (-r["points"], r["nick"].casefold()))
        return " · ".join(f"{r['nick']} {r['points']}" for r in rows[:n])

    # --- uscita ----------------------------------------------------------

    def say(self, target, text):
        self.out.say(target, text)

    def emit(self, target, parts):
        for line in joined_lines(parts):
            self.say(target, line)

    # --- carte -------------------------------------------------------------

    def draw(self, g, kind):
        """Una carta dal mazzo; a mazzo finito si rimescolano gli scarti, cioe'
        tutto quello che non e' in mano a qualcuno o sul tavolo."""
        deck = g["bdeck"] if kind == "b" else g["wdeck"]
        if not deck:
            busy = {c for h in g["hands"].values() for c in h}
            busy |= {c for s in g["subs"].values() for c in s}
            busy.add(g.get("black"))
            deck.extend(c for c in (self.nere if kind == "b" else self.bianche)
                        if c not in busy)
            self.rng.shuffle(deck)
        return deck.pop()

    def send_hand(self, chan, g, key):
        n = g["picks"]
        head = f"🃏 {chan}: «{g['black']}» — !play " + ("N M" if n > 1 else "N") + ":"
        self.emit(g["nicks"][key],
                  [head] + [f"{i}) {c}" for i, c in enumerate(g["hands"][key], 1)])

    # --- partita -----------------------------------------------------------

    def player_game(self, key):
        for chan, g in self.games.items():
            if key in g["players"] or key in g["pending"]:
                return chan, g
        return None, None

    def czar(self, g):
        return g["players"][g["czar"] % len(g["players"])] if g["players"] else None

    def open_lobby(self, chan, nick, goal, now):
        key = nick.casefold()
        self.games[chan] = {
            "phase": "lobby", "starter": key, "players": [key], "pending": [],
            "nicks": {key: nick}, "scores": {}, "hands": {}, "missed": {}, "absent": [], "split": [],
            "subs": {}, "order": [], "goal": goal, "czar": -1, "round": 0, "idle": 0,
            "black": None, "picks": 1, "wdeck": [], "bdeck": [], "deadline": now + LOBBY_S,
        }
        self.say(chan, f"🃏 Cards Against Humanity! {nick} apre il tavolo, si gioca a "
                       f"{goal} punti. `!cah entra` per sedersi, {LOBBY_S}s, "
                       f"servono {MIN_PLAYERS} giocatori.")

    def join(self, chan, g, nick, now):
        key = nick.casefold()
        if self.player_game(key)[0]:
            if self.player_game(key)[0] != chan:
                self.say(chan, f"{nick}: stai gia' giocando altrove, una partita per volta.")
            return False
        g["nicks"][key] = nick
        if g["phase"] == "lobby":
            g["players"].append(key)
            self.say(chan, f"➕ {nick} si siede ({len(g['players'])} al tavolo).")
            return True
        g["pending"].append(key)
        if g["phase"] == "attesa" and len(g["players"]) + len(g["pending"]) >= MIN_PLAYERS:
            self.new_round(chan, now, [f"➕ {nick} si siede: si riparte."])
        else:
            self.say(chan, f"➕ {nick} si siede, gioca dalla prossima mano.")
        return True

    def void_round(self, g):
        """Mano nulla: le carte giocate tornano in mano a chi le ha giocate."""
        for k, cards in g["subs"].items():
            if k in g["hands"]:
                g["hands"][k].extend(cards)
        g["subs"] = {}

    def pause(self, chan, g, now, lead=()):
        self.void_round(g)
        g["players"].extend(g["pending"])
        g["pending"] = []
        g["phase"] = "attesa"
        g["deadline"] = now + LOBBY_S
        self.emit(chan, list(lead) + [
            f"⏸️ Siamo in {len(g['players'])}, servono {MIN_PLAYERS}: "
            f"`!cah entra` entro {LOBBY_S}s o chiudo."])

    def leave(self, chan, g, key, now, why=""):
        if key in g["pending"]:
            g["pending"].remove(key)
            return
        if key not in g["players"]:
            return
        was_czar = self.czar(g) == key
        if was_czar and g["phase"] in ("play", "judge"):
            self.void_round(g)
        idx = g["players"].index(key)
        g["players"].remove(key)
        g["hands"].pop(key, None)
        g["subs"].pop(key, None)        # la sua giocata se ne va con lui
        if idx <= g["czar"]:
            g["czar"] -= 1
        lead = [f"➖ {g['nicks'].get(key, key)} si alza{why}."]
        if g["phase"] == "lobby":
            if g["players"]:
                self.emit(chan, lead)
            else:
                self.games.pop(chan, None)
                self.emit(chan, lead + ["🃏 Tavolo vuoto, chiuso."])
        elif g["phase"] == "attesa":
            self.emit(chan, lead)
        elif len(g["players"]) + len(g["pending"]) < MIN_PLAYERS:
            self.pause(chan, g, now, lead)
        elif was_czar and g["phase"] in ("play", "judge"):
            self.new_round(chan, now, lead + ["Era lo czar: mano nulla, carte rese."])
        else:
            self.emit(chan, lead)
            self.maybe_judge(chan, g, now)

    def new_round(self, chan, now, lead=()):
        g = self.games[chan]
        lead = list(lead)
        out = [k for k in g["players"] if g["missed"].get(k, 0) >= MISSED_MAX]
        for k in out:
            g["players"].remove(k)
            g["hands"].pop(k, None)
            g["missed"].pop(k, None)
        if out:
            lead.append(f"➖ Fuori per {MISSED_MAX} mani saltate: "
                        f"{', '.join(g['nicks'][k] for k in out)}.")
        g["players"].extend(g["pending"])
        g["pending"] = []
        if len(g["players"]) < MIN_PLAYERS:
            self.pause(chan, g, now, lead)
            return
        g["czar"] = (g["czar"] + 1) % len(g["players"])
        g["round"] += 1
        g["phase"] = "play"
        g["subs"], g["order"], g["absent"] = {}, [], []
        g["black"] = self.draw(g, "b")
        g["picks"] = picks(g["black"])
        g["deadline"] = now + PLAY_S
        for key in g["players"]:
            hand = g["hands"].setdefault(key, [])
            while len(hand) < HAND:
                hand.append(self.draw(g, "w"))
        czar = self.czar(g)
        n = g["picks"]
        self.emit(chan, lead + [
            f"🃏 Mano {g['round']}, czar {g['nicks'][czar]}: «{g['black']}»"
            + (f" [{n} carte]" if n > 1 else "")
            + f" — !play N, le carte sono in privato. {PLAY_S}s."])
        for key in g["players"]:
            if key != czar:
                self.send_hand(chan, g, key)

    def play(self, chan, g, key, nums, now):
        """True se la giocata e' valida. Una giocata nuova prima della
        rivelazione sostituisce la vecchia."""
        if g["phase"] != "play" or key not in g["players"] or key == self.czar(g):
            return False
        nick = g["nicks"][key]
        hand = g["hands"][key]
        hand.extend(g["subs"].pop(key, []))
        if (len(nums) != g["picks"] or len(set(nums)) != len(nums)
                or not all(1 <= n <= len(hand) for n in nums)):
            self.say(nick, f"Servono {g['picks']} numeri diversi fra 1 e {len(hand)}: "
                           f"!play " + ("N M" if g["picks"] > 1 else "N") + ".")
            return False
        cards = [hand[n - 1] for n in nums]
        for c in cards:
            hand.remove(c)
        g["subs"][key] = cards
        g["missed"].pop(key, None)
        if key in g["absent"]:
            g["absent"].remove(key)
        self.maybe_judge(chan, g, now)
        return True

    def maybe_judge(self, chan, g, now, timeout=False):
        if g["phase"] != "play":
            return
        czar = self.czar(g)
        waiting = [k for k in g["players"]
                   if k != czar and k not in g["subs"] and k not in g["absent"]]
        if waiting and not timeout:
            return
        for k in waiting:
            g["missed"][k] = g["missed"].get(k, 0) + 1
        if waiting:
            # Su +u i QUIT non arrivano: chi manca e' forse gia' offline. Un
            # ISON solo per loro, e la risposta (303) si concilia quando arriva.
            self.ison.append({k: chan for k in waiting})
            self.out.raw("ISON " + " ".join(g["nicks"][k] for k in waiting))
        if len(g["subs"]) < MIN_SUBS:
            g["idle"] = g["idle"] + 1 if not g["subs"] else 0
            if g["idle"] >= IDLE_ROUNDS:
                self.end_game(chan, [f"💤 {IDLE_ROUNDS} mani senza giocate, chiudo."])
                return
            self.void_round(g)
            self.new_round(chan, now, [f"⏰ Meno di {MIN_SUBS} giocate, mano nulla."])
            return
        g["idle"] = 0
        g["order"] = list(g["subs"])
        self.rng.shuffle(g["order"])
        g["phase"] = "judge"
        g["deadline"] = now + JUDGE_S
        self.emit(chan, [f"🗳️ {g['nicks'][czar]}, scegli con !vince N ({JUDGE_S}s):"]
                  + [f"{i}) {fill(g['black'], g['subs'][k])}"
                     for i, k in enumerate(g["order"], 1)])

    def winner(self, chan, g, key, n, now):
        if g["phase"] != "judge" or key != self.czar(g):
            return False
        nick = g["nicks"][key]
        if not 1 <= n <= len(g["order"]):
            self.say(chan, f"{nick}: un numero fra 1 e {len(g['order'])}.")
            return False
        w = g["order"][n - 1]
        if w not in g["subs"]:
            self.say(chan, f"{nick}: quella se n'e' andata col suo autore, scegline un'altra.")
            return False
        g["scores"][w] = g["scores"].get(w, 0) + 1
        wnick = g["nicks"][w]
        tot = self.scores.setdefault(w, {"nick": wnick, "points": 0})
        tot["nick"] = wnick
        tot["points"] += 1
        self.dirty = True
        lead = [f"🏆 {wnick} vince la mano ({g['scores'][w]}/{g['goal']}): "
                f"{fill(g['black'], g['subs'][w])}"]
        g["subs"] = {}
        if g["scores"][w] >= g["goal"]:
            self.end_game(chan, lead + [f"🎉 {wnick} vince la partita!"])
        else:
            g["phase"] = "pausa"
            g["deadline"] = now + PAUSA_S
            self.emit(chan, lead)
        return True

    def punti(self, g):
        keys = sorted(set(g["players"]) | set(g["scores"]),
                      key=lambda k: (-g["scores"].get(k, 0), k))
        return " · ".join(f"{g['nicks'].get(k, k)} {g['scores'].get(k, 0)}" for k in keys)

    def end_game(self, chan, lead=()):
        g = self.games.pop(chan, None)
        if g is not None:
            self.emit(chan, list(lead) + [f"🃏 Fine. Punti: {self.punti(g)}"])

    def status(self, chan, g, now):
        left = max(0, int(g["deadline"] - now))
        ph = g["phase"]
        if ph in ("lobby", "attesa"):
            msg = (f"🃏 {'Lobby' if ph == 'lobby' else 'In pausa'}, al tavolo: "
                   f"{', '.join(g['nicks'][k] for k in g['players'])}; {left}s.")
        elif ph == "play":
            czar = self.czar(g)
            w = [g["nicks"][k] for k in g["players"] if k != czar and k not in g["subs"]]
            msg = (f"🃏 Mano {g['round']}: «{g['black']}» — mancano "
                   f"{', '.join(w) or 'nessuno'}, {left}s.")
        elif ph == "judge":
            msg = f"🃏 Sceglie {g['nicks'][self.czar(g)]}, {left}s."
        else:
            msg = f"🃏 Prossima mano fra {left}s."
        self.say(chan, msg)

    # --- ingresso ----------------------------------------------------------

    def handle(self, nick, target, text, now=None):
        now = float(now if now is not None else time.time())
        text = text.strip()
        key = nick.casefold()
        m = PLAY_PAT.match(text)
        if not is_chan(target):
            chan, g = self.player_game(key)
            if m and g:
                self.numbers(chan, g, key, m, now)
            return
        chan = target.lower()
        if chan not in self.chans:
            return
        g = self.games.get(chan)
        if m:
            if g:
                self.numbers(chan, g, key, m, now)
            return
        m = CAH_PAT.match(text)
        if not m:
            return
        sub = SUB.get((m.group("sub") or "start").lower())
        arg = m.group("arg") or ""
        if sub is None:
            self.say(chan, HELP)
        elif sub == "top" or (sub == "points" and not g):
            self.say(chan, f"🏆 [CaH] Mani vinte ({self.net}): {self.top() or 'nessuna'}")
        elif sub == "start" and not g:
            if self.player_game(key)[0]:
                self.say(chan, f"{nick}: stai gia' giocando altrove.")
            else:
                goal = min(MAX_GOAL, max(1, int(arg))) if arg.isdigit() else GOAL
                self.open_lobby(chan, nick, goal, now)
        elif not g:
            self.say(chan, "Nessuna partita: `!cah` per aprire il tavolo.")
        elif sub == "join" or (sub == "start" and g["phase"] in ("lobby", "attesa")):
            self.join(chan, g, nick, now)
        elif sub == "quit":
            self.leave(chan, g, key, now)
        elif sub == "stop":
            if key == g["starter"] or self.tracker.is_op(nick, chan):
                self.end_game(chan, [f"🛑 {nick} chiude il tavolo."])
            else:
                self.say(chan, f"{nick}: chiude chi l'ha aperta, o un op.")
        elif sub == "points":
            self.say(chan, f"🃏 Punti (a {g['goal']}): {self.punti(g)}")
        elif sub == "cards":
            if g["phase"] == "play" and key in g["hands"] and key != self.czar(g):
                self.send_hand(chan, g, key)
        else:
            self.status(chan, g, now)

    def numbers(self, chan, g, key, m, now):
        nums = [int(x) for x in re.split(r"[\s,]+", m.group("nums").strip()) if x]
        verb = m.group("verb").lower()
        if g["phase"] == "judge" and verb in ("winner", "vince", "pick") and len(nums) == 1:
            self.winner(chan, g, key, nums[0], now)
        elif g["phase"] == "play" and verb in ("play", "gioco", "gioca", "pick"):
            self.play(chan, g, key, nums, now)

    # --- chi se ne va ------------------------------------------------------

    def track(self, line, now=None):
        """Gli eventi di presenza dal Tracker, applicati alle partite."""
        ev = self.tracker.feed(line)
        if not ev:
            return
        now = float(now if now is not None else time.time())
        kind = ev[0]
        if kind == "404":
            # Prima si riprova senza colori; se non c'era niente da ripulire
            # il rifiuto e' +m/+M/ban: la partita si ferma, niente loop.
            g = self.games.get(ev[1])
            if not self.out.on_404(ev[1], now) and g and g["phase"] not in ("lobby", "muto"):
                self.mute(ev[1], g, now)
        elif kind == "ison":
            # La risposta all'ISON di maybe_judge: chi non e' online se n'e'
            # andato con un QUIT che +u ci ha nascosto. Le risposte arrivano
            # nell'ordine delle domande.
            asked = self.ison.popleft() if self.ison else {}
            for k, chan in asked.items():
                g = self.games.get(chan)
                if k not in ev[1] and g and (k in g["players"] or k in g["pending"]):
                    self.leave(chan, g, k, now, ", non e' piu' su IRC")
        elif kind == "join":
            if ev[2].casefold() == BOT_NICK.casefold() and ev[1] in self.chans:
                self.out.raw(f"MODE {ev[1]}")       # il 324 dice se e' +c
            g = self.games.get(ev[1])
            if g and ev[2].casefold() in g["split"]:
                g["split"].remove(ev[2].casefold())
        elif kind == "nick":
            self.rename(ev[1].casefold(), ev[2])
        elif kind == "quit":
            key = ev[2].casefold()
            chan, g = self.player_game(key)
            if g:
                (self.absent if ev[3] else self.leave)(chan, g, key, now)
        elif kind in ("part", "kick"):
            g = self.games.get(ev[1])
            if g:
                self.leave(ev[1], g, ev[2].casefold(), now)

    def mute(self, chan, g, now):
        """Il canale rifiuta le nostre righe anche pulite (+m o +M senza voce:
        il bot non ha NickServ). Mano nulla, LOBBY_S secondi per tornare a
        parlare, poi chiusa. In canale non si dice niente: non si puo'."""
        self.void_round(g)
        g["phase"] = "muto"
        g["deadline"] = now + LOBBY_S
        print(f"cah: {chan} rifiuta le righe (404), partita in pausa", flush=True)

    def absent(self, chan, g, key, now):
        """Netsplit: resta al tavolo, ma questa mano non la si aspetta. Se era
        lo czar la mano e' nulla e lo czar passa."""
        if g["phase"] not in ("play", "judge") or key not in g["players"]:
            return
        if key == self.czar(g):
            self.void_round(g)
            self.new_round(chan, now, [f"🌩️ Lo czar {g['nicks'][key]} e' finito in uno "
                                       f"split: mano nulla, carte rese."])
            return
        if key not in g["absent"]:
            g["absent"].append(key)
        if key not in g["split"]:
            g["split"].append(key)
        self.maybe_judge(chan, g, now)

    def rename(self, old, new_nick):
        new = new_nick.casefold()
        _, g = self.player_game(old)
        if not g:
            return
        g["nicks"].pop(old, None)
        g["nicks"][new] = new_nick
        for lst in (g["players"], g["pending"], g["absent"], g["split"], g["order"]):
            for i, k in enumerate(lst):
                if k == old:
                    lst[i] = new
        for d in (g["hands"], g["scores"], g["missed"], g["subs"]):
            if old in d:
                d[new] = d.pop(old)
        if g["starter"] == old:
            g["starter"] = new

    # --- tempo -------------------------------------------------------------

    def tick(self, now=None):
        now = float(now if now is not None else time.time())
        for chan, g in list(self.games.items()):
            if now < g["deadline"]:
                continue
            ph = g["phase"]
            if ph == "lobby":
                if len(g["players"]) >= MIN_PLAYERS:
                    self.new_round(chan, now, ["🃏 Si parte!"])
                else:
                    self.games.pop(chan, None)
                    self.say(chan, f"🃏 Servono {MIN_PLAYERS} giocatori, al tavolo "
                                   f"{len(g['players'])}. Chiuso.")
            elif ph == "muto":
                if self.tracker.can_speak(chan, BOT_NICK):
                    self.new_round(chan, now, ["▶️ Si riprende."])
                else:
                    self.games.pop(chan, None)
                    print(f"cah: {chan} ancora muto dopo {LOBBY_S}s, partita chiusa",
                          flush=True)
            elif ph == "attesa":
                self.end_game(chan, ["⏸️ Nessuno si e' seduto."])
            elif ph == "play":
                self.maybe_judge(chan, g, now, timeout=True)
            elif ph == "judge":
                g["subs"] = {}          # niente punto, le carte giocate sono andate
                self.new_round(chan, now, [f"⏰ {g['nicks'][self.czar(g)]} non ha "
                                           f"scelto: nessun punto."])
            else:
                self.new_round(chan, now)

    def run(self):
        p = subprocess.Popen(["tail", "-F", "-n", "0", self.log],
                             stdout=subprocess.PIPE, text=True, errors="replace")
        time.sleep(1)
        for chan in sorted(self.chans):     # la fotografia di op e modi
            self.out.raw(f"NAMES {chan}")
            self.out.raw(f"MODE {chan}")
        wait = 0.2
        try:
            while True:
                r, _, _ = select.select([p.stdout], [], [], wait)
                now = time.time()
                if r:
                    line = p.stdout.readline()
                    if not line:
                        break
                    self.track(line, now)
                    m = PRIVMSG_PAT.search(line)
                    if (m and m.group("verb") == "PRIVMSG"
                            and not m.group("text").startswith("\x01")):
                        self.handle(m.group("nick"), m.group("target"), m.group("text"), now)
                self.tick(now)
                if self.dirty:
                    self.save()
                wait = min([self.out.flush(now) or TICK]
                           + [g["deadline"] - now for g in self.games.values()])
                wait = max(0.2, wait)
        finally:
            p.terminate()


def main():
    argv = sys.argv[1:]
    net = "azzurra"
    if "--net" in argv:
        i = argv.index("--net")
        net = argv[i + 1]
        del argv[i:i + 2]
    cmd = argv[0] if argv else "daemon"
    if cmd == "carte":
        nere, bianche = load_carte()
        print(f"{len(nere)} nere ({sum(picks(n) == 2 for n in nere)} da due), "
              f"{len(bianche)} bianche")
        return
    cfg = load_nets()[net]
    Cah(net, cfg["log"], cfg["fifo"], cfg["state"], cfg["chans"], load_carte()).run()


if __name__ == "__main__":
    main()
