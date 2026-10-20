"""tavolo — il motore di tavolo comune ai giochi di carte di MikeBongiorno.

Estratto da briscola.py quando e' arrivata la scopa (stesso mazzo, stesso
tavolo: docs/scopa_spec.md). Qui c'e' quello che non dipende dalle regole:

  le carte         SEMI, mazzo(), nome(), bold()
  load_nets()      *_NETS con le opzioni per canale (`#italia:ops`)
  Tavolo           lobby (`jo`, max 4), posti e coppie, giocate `pl N` in
                   canale / query / NOTICE, timeout di turno con ISON,
                   abbandono, cambio nick, netsplit, 404/+c/+m, lucchetto per
                   canale, classifica storica su disco, il loop sul bot.log

Ogni gioco e' una sottoclasse che mette le regole negli agganci:
  lobby_line, start_param, start_match, new_round, numbers, autoplay,
  status, abandon_between, abandon_standing, abandoned.
"""
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

TICK = 5

# --- le carte ----------------------------------------------------------------

SEMI = ("denari", "coppe", "spade", "bastoni")
# 1 = Asso, 8 = Fante, 9 = Cavallo, 10 = Re
NOMI = {1: "Asso", 8: "Fante", 9: "Cavallo", 10: "Re"}


def mazzo():
    """Le 40 carte, (valore, seme)."""
    return [(r, s) for s in SEMI for r in range(1, 11)]


def nome(c):
    return f"{NOMI.get(c[0], c[0])} di {c[1]}"


def bold(c):
    return f"\x02{nome(c)}\x02"


# --- configurazione ------------------------------------------------------------

def parse_opts(game, chan, raw):
    """Le opzioni dopo il canale: per ora solo `ops` (ircgame.can_control).
    Una sconosciuta ferma il sidecar."""
    opts = {}
    for o in filter(None, raw):
        if o == "ops":
            opts["ops"] = True
        else:
            raise SystemExit(f"{game}: opzione sconosciuta per {chan}: {o!r}")
    return opts


def load_nets(game, env, repo):
    """Come ircgame.load_nets, con le opzioni per canale (`#italia:ops`) in
    `opts`, {canale: {...}}, e i canali senza opzioni in `chans`. Lo stato e'
    `<game>_state[.<rete>].json`."""
    nets = ircgame.load_nets(
        env, {"azzurra": {"log": repo / "bot.log", "fifo": repo / "bot.send",
                          "chans": {"#sbiffo"},
                          "state": repo / f"{game}_state.json"}},
        f"{game}_state", repo)
    for cfg in nets.values():
        chans, opts = set(), {}
        for spec in cfg["chans"]:
            chan, *rest = spec.split(":")
            chans.add(chan)
            opts[chan] = parse_opts(game, chan, rest)
        cfg["chans"], cfg["opts"] = chans, opts
    return nets


# `pl N` nudo, come CaH; `!pl` alias muto. Quanti numeri servono lo dice il gioco.
PLAY_PAT = re.compile(r'^!?\s*pl\s+(?P<nums>[\d\s,]+)$', re.I)
JO_PAT = re.compile(r'^jo$', re.I)

SUB = {"start": "start", "inizia": "start", "join": "join", "entra": "join",
       "quit": "quit", "esci": "quit", "stop": "stop", "top": "top",
       "classifica": "top", "cards": "cards", "carte": "cards", "status": "status",
       "stato": "status"}


def play_numbers(m):
    return [int(x) for x in re.split(r"[\s,]+", m.group("nums").strip()) if x]


class Tavolo:
    # da riempire nella sottoclasse
    GAME = "Tavolo"             # il nome in canale e nel lucchetto
    TAG = "tavolo"              # il tag sulla FIFO e nel log
    CMD = "!tavolo"             # il comando da dire nelle righe
    CMD_PAT = None              # `!gioco [sub] [arg]`
    HELP = ""
    BOT_NICK = "MikeBongiorno"
    LOBBY_S = 60                # attesa giocatori all'apertura, e a partita muta
    TURN_S = 90                 # per giocata
    PAUSA_S = 10                # fra una mano (partita, smazzata) e l'altra
    MIN_PLAYERS = 2
    MAX_PLAYERS = 4
    TIMEOUT_MAX = 2             # turni saltati di fila: abbandono
    # i dizionari per giocatore che il cambio nick sposta
    PER_PLAYER = ("hands", "timeouts")

    def __init__(self, net, log, fifo, state_path, chans, rng=None, opts=None,
                 trust=None, locks=None):
        self.net = net
        self.log = str(log)
        self.state_path = Path(state_path)
        self.chans = {c.lower() for c in chans}
        self.opts = {c.lower(): dict(o) for c, o in (opts or {}).items()}
        # nick + host-glob che comandano i canali `ops` (IRCGAME_TRUST)
        self.trust = ircgame.load_trust() if trust is None else list(trust)
        self.rng = rng or random.Random()
        self.games = {}                 # in memoria e basta: un riavvio le perde
        self.scores = self.load()       # nick casefold -> {"nick", "wins"}
        self.dirty = False
        self.tracker = Tracker()
        # una partita per canale fra tutti i giochi (IRCGAME_LOCKS)
        self.lock = ircgame.ChannelLock(net, self.GAME, locks)
        self.out = Outbox(fifo, self.TAG, self.tracker)
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
        rows = sorted(self.scores.values(), key=lambda r: (-r["wins"], r["nick"].casefold()))
        return " · ".join(f"{r['nick']} {r['wins']}" for r in rows[:n])

    def credit(self, g, team):
        for k in self.members(g, team):
            nick = g["nicks"][k]
            row = self.scores.setdefault(k, {"nick": nick, "wins": 0})
            row["nick"] = nick
            row["wins"] += 1
        self.dirty = True

    # --- uscita ----------------------------------------------------------

    def say(self, target, text):
        self.out.say(target, text)

    def emit(self, target, parts):
        for line in joined_lines(parts):
            self.say(target, line)

    def hand_line(self, g, hand):
        return "Mano: " + "  ".join(f"{i}) {nome(c)}" for i, c in enumerate(hand, 1))

    def send_hand(self, g, key):
        hand = g["hands"].get(key)
        if hand:
            self.say(g["nicks"][key], self.hand_line(g, hand))

    # --- squadre -----------------------------------------------------------

    def team(self, g, key):
        """In 4 le coppie sono i posti pari e i dispari (A1 B1 A2 B2); in 2 e 3
        ognuno e' squadra da se', col numero del suo posto."""
        i = g["players"].index(key)
        return i % 2 if len(g["players"]) == 4 else i

    def teams(self, g):
        return sorted({self.team(g, k) for k in g["players"]})

    def members(self, g, team):
        return [k for k in g["players"] if self.team(g, k) == team]

    def team_name(self, g, team):
        return " e ".join(g["nicks"][k] for k in self.members(g, team))

    def vince(self, g, team):
        return "vincono" if len(self.members(g, team)) > 1 else "vince"

    def seats_line(self, g):
        if len(g["players"]) == 4:
            return f"Coppie: {self.team_name(g, 0)} contro {self.team_name(g, 1)}."
        return f"Al tavolo: {', '.join(g['nicks'][k] for k in g['players'])}."

    # --- partita -----------------------------------------------------------

    def player_game(self, key):
        for chan, g in self.games.items():
            if key in g["players"]:
                return chan, g
        return None, None

    def turn_key(self, g):
        return g["players"][g["turn"]]

    def open_lobby(self, chan, nick, param, now):
        key = nick.casefold()
        g = self.games[chan] = {
            "phase": "lobby", "chan": chan, "starter": key, "players": [key],
            "nicks": {key: nick}, "hands": {}, "deck": [], "table": [], "turn": 0,
            "timeouts": {}, "split": [], "deadline": now + self.LOBBY_S, "was": None,
        }
        self.say(chan, self.lobby_line(g, nick, param))

    def join(self, chan, g, nick, now):
        key = nick.casefold()
        where = self.player_game(key)[0]
        if where:
            if where != chan:
                self.say(chan, f"{nick}: stai gia' giocando altrove, una partita per volta.")
            return False
        if g["phase"] != "lobby":
            self.say(chan, f"{nick}: partita in corso, alla prossima ci si siede con jo.")
            return False
        if len(g["players"]) >= self.MAX_PLAYERS:
            self.say(chan, f"{nick}: tavolo pieno, siamo in {self.MAX_PLAYERS}. "
                           f"Alla prossima partita.")
            return False
        g["nicks"][key] = nick
        g["players"].append(key)
        self.say(chan, f"➕ {nick} si siede ({len(g['players'])} al tavolo).")
        return True

    def close(self, chan, lines):
        self.games.pop(chan, None)
        self.emit(chan, lines)

    def abandon(self, chan, g, key, now, why=""):
        """In lobby si alza e basta; a partita viva la partita finisce: in 2 e
        in 4 vince l'altra squadra, in 3 chi dei due rimasti sta meglio
        (abandon_standing; pari = nessuno). Il resto lo decide il gioco
        (abandon_between, abandoned)."""
        if key not in g["players"]:
            return
        nick = g["nicks"].get(key, key)
        lead = f"➖ {nick} si alza{why}."
        if g["phase"] == "lobby":
            g["players"].remove(key)
            if g["players"]:
                self.say(chan, lead)
            else:
                self.close(chan, [lead, "🃏 Tavolo vuoto, chiuso."])
            return
        if self.abandon_between(chan, g, lead):
            return
        others = [t for t in self.teams(g) if t != self.team(g, key)]
        if len(others) == 1:
            win = others[0]
        else:
            pts = {t: self.abandon_standing(g, t) for t in others}
            best = max(pts.values())
            top = [t for t in others if pts[t] == best]
            win = top[0] if len(top) == 1 else None
        self.abandoned(chan, g, now, win, lead)

    def timeout(self, chan, g, now):
        """Il bot gioca per lui (autoplay). Al TIMEOUT_MAX di fila, abbandono.
        Chi non e' in uno split riceve anche un ISON: su +u il QUIT non arriva,
        e chi e' offline se n'e' andato."""
        key = self.turn_key(g)
        g["timeouts"][key] = g["timeouts"].get(key, 0) + 1
        if g["timeouts"][key] >= self.TIMEOUT_MAX:
            self.abandon(chan, g, key, now, f", {self.TIMEOUT_MAX} turni saltati")
            return
        if key not in g["split"]:
            self.ison.append({key: chan})
            self.out.raw(f"ISON {g['nicks'][key]}")
        self.autoplay(chan, g, key, now)

    # --- ingresso ----------------------------------------------------------

    def handle(self, nick, target, text, now=None, host="", notice=False):
        """`notice`: la riga e' una NOTICE. Vale solo come giocata in privato
        (`pl N` al bot), e non riceve mai risposta (RFC 1459)."""
        now = float(now if now is not None else time.time())
        text = text.strip()
        key = nick.casefold()
        m = PLAY_PAT.match(text)
        if notice:
            chan, g = self.player_game(key)
            if m and g and not is_chan(target) and g["phase"] == "play":
                self.numbers(chan, g, key, play_numbers(m), now, quiet=True)
            return
        if not is_chan(target):
            chan, g = self.player_game(key)
            if m and g and g["phase"] == "play":
                self.numbers(chan, g, key, play_numbers(m), now)
            return
        chan = target.lower()
        if chan not in self.chans:
            return
        g = self.games.get(chan)
        if m:
            if g and key in g["players"] and g["phase"] == "play":
                self.numbers(chan, g, key, play_numbers(m), now)
            return
        if JO_PAT.match(text):
            if g:
                self.join(chan, g, nick, now)
            return
        m = self.CMD_PAT.match(text)
        if not m:
            return
        raw, arg = (m.group("sub") or "start"), (m.group("arg") or "")
        if raw.isdigit():
            sub, arg = "start", raw
        else:
            sub = SUB.get(raw.lower())
        if sub is None:
            self.say(chan, self.HELP)
        elif sub == "top":
            self.say(chan, f"🏆 [{self.GAME}] Partite vinte ({self.net}): "
                           f"{self.top() or 'nessuna'}")
        elif sub == "start" and not g:
            self.start(chan, nick, host, arg, now)
        elif not g:
            self.say(chan, f"Nessuna partita: {self.CMD} per aprire il tavolo.")
        elif sub == "join" or (sub == "start" and g["phase"] == "lobby"):
            self.join(chan, g, nick, now)
        elif sub == "quit":
            self.abandon(chan, g, key, now)
        elif sub == "stop":
            if self.op_gate(chan, nick, host, "chiude"):
                pass
            elif (key == g["starter"] or self.opts.get(chan, {}).get("ops")
                  or ircgame.can_control(self.tracker, chan, nick, host, self.trust)):
                self.close(chan, [f"🛑 {nick} chiude il tavolo."])
            else:
                self.say(chan, f"{nick}: chiude chi l'ha aperta, o un op.")
        elif sub == "cards":
            if key in g["players"] and g["phase"] in ("play", "muto"):
                self.send_hand(g, key)
        else:
            self.status(chan, g, now)

    def start(self, chan, nick, host, arg, now):
        if self.op_gate(chan, nick, host, "apre"):
            return
        if self.player_game(nick.casefold())[0]:
            self.say(chan, f"{nick}: stai gia' giocando altrove.")
            return
        ok, param = self.start_param(chan, nick, arg)
        if not ok:
            return
        no = ircgame.start_refusal(self.lock, self.tracker, chan, now)
        if no:
            self.say(chan, no)
            return
        self.open_lobby(chan, nick, param, now)

    def op_gate(self, chan, nick, host, verb):
        """Sui canali `ops` il tavolo lo apre e lo chiude solo un chanop o lo
        staff (ircgame.can_control): True (con la risposta gia' in coda) se
        `nick` va fermato."""
        if (not self.opts.get(chan, {}).get("ops")
                or ircgame.can_control(self.tracker, chan, nick, host, self.trust)):
            return False
        self.say(chan, f"{nick}: qui il tavolo lo {verb} solo un op o lo staff. Per giocare: "
                       f"jo quando c'e' un tavolo aperto.")
        return True

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
            asked = self.ison.popleft() if self.ison else {}
            for k, chan in asked.items():
                g = self.games.get(chan)
                if k not in ev[1] and g and k in g["players"]:
                    self.abandon(chan, g, k, now, ", non e' piu' su IRC")
        elif kind == "join":
            if ev[2].casefold() == self.BOT_NICK.casefold() and ev[1] in self.chans:
                self.out.raw(f"MODE {ev[1]}")       # il 324 dice se e' +c
            g = self.games.get(ev[1])
            if g and ev[2].casefold() in g["split"]:
                g["split"].remove(ev[2].casefold())
        elif kind == "nick":
            self.rename(ev[1].casefold(), ev[2])
        elif kind == "quit":
            key = ev[2].casefold()
            chan, g = self.player_game(key)
            if not g:
                return
            if ev[3]:
                # Netsplit: non e' abbandono, il turno aspetta il timeout
                # normale e poi gioca il bot.
                if key not in g["split"]:
                    g["split"].append(key)
            else:
                self.abandon(chan, g, key, now)
        elif kind in ("part", "kick"):
            g = self.games.get(ev[1])
            if g:
                self.abandon(ev[1], g, ev[2].casefold(), now)

    def mute(self, chan, g, now):
        """Il canale rifiuta le nostre righe anche pulite (+m o +M senza voce).
        Il turno si ferma, LOBBY_S secondi per tornare a parlare, poi chiusa.
        In canale non si dice niente: non si puo'."""
        g["was"] = g["phase"]
        g["phase"] = "muto"
        g["deadline"] = now + self.LOBBY_S
        print(f"{self.TAG}: {chan} rifiuta le righe (404), partita in pausa", flush=True)

    def rename(self, old, new_nick):
        """Mano, posto e turno passano al nick nuovo."""
        new = new_nick.casefold()
        _, g = self.player_game(old)
        if not g:
            return
        g["nicks"].pop(old, None)
        g["nicks"][new] = new_nick
        for lst in (g["players"], g["split"]):
            for i, k in enumerate(lst):
                if k == old:
                    lst[i] = new
        for name in self.PER_PLAYER:
            d = g.get(name, {})
            if old in d:
                d[new] = d.pop(old)
        if g["starter"] == old:
            g["starter"] = new
        self.renamed(g, old, new)

    def renamed(self, g, old, new):
        """Aggancio: quello che il gioco tiene per nick fuori da PER_PLAYER."""

    # --- tempo -------------------------------------------------------------

    def tick(self, now=None):
        now = float(now if now is not None else time.time())
        for chan, g in list(self.games.items()):
            if now < g["deadline"]:
                continue
            ph = g["phase"]
            if ph == "lobby":
                if len(g["players"]) >= self.MIN_PLAYERS:
                    self.start_match(chan, g, now)
                else:
                    self.close(chan, [f"🃏 Serve almeno un altro giocatore, al tavolo "
                                      f"solo {g['nicks'][g['players'][0]]}. Chiuso."])
            elif ph == "muto":
                if not self.tracker.can_speak(chan, self.BOT_NICK):
                    self.games.pop(chan, None)
                    print(f"{self.TAG}: {chan} ancora muto dopo {self.LOBBY_S}s, "
                          f"partita chiusa", flush=True)
                elif g["was"] == "pausa":
                    self.new_round(chan, g, now, ["▶️ Si riprende."])
                else:
                    g["phase"], g["deadline"] = "play", now + self.TURN_S
                    self.say(chan, f"▶️ Si riprende: tocca a {g['nicks'][self.turn_key(g)]}.")
            elif ph == "pausa":
                self.new_round(chan, g, now)
            elif ph == "play":
                self.timeout(chan, g, now)
        self.lock.sync(self.games)      # qualunque strada abbia chiuso, si molla

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
                    if m and not m.group("text").startswith("\x01"):
                        self.handle(m.group("nick"), m.group("target"), m.group("text"),
                                    now, host=m.group("host"),
                                    notice=m.group("verb") == "NOTICE")
                self.tick(now)
                if self.dirty:
                    self.save()
                wait = min([self.out.flush(now) or TICK]
                           + [g["deadline"] - now for g in self.games.values()])
                wait = max(0.2, wait)
        finally:
            p.terminate()

    # --- agganci del gioco ---------------------------------------------------

    def lobby_line(self, g, nick, param):
        raise NotImplementedError

    def start_param(self, chan, nick, arg):
        """(True, parametro) per aprire, (False, None) con la riga gia' in coda."""
        raise NotImplementedError

    def start_match(self, chan, g, now):
        raise NotImplementedError

    def new_round(self, chan, g, now, lead=()):
        raise NotImplementedError

    def numbers(self, chan, g, key, nums, now, quiet=False):
        raise NotImplementedError

    def autoplay(self, chan, g, key, now):
        raise NotImplementedError

    def status(self, chan, g, now):
        raise NotImplementedError

    def abandon_between(self, chan, g, lead):
        """True se l'abbandono l'ha gia' chiuso il gioco (es. fra due partite)."""
        return False

    def abandon_standing(self, g, team):
        raise NotImplementedError

    def abandoned(self, chan, g, now, win, lead):
        raise NotImplementedError


def main(cls, nets_fn):
    argv = sys.argv[1:]
    net = "azzurra"
    if "--net" in argv:
        i = argv.index("--net")
        net = argv[i + 1]
        del argv[i:i + 2]
    cmd = argv[0] if argv else "daemon"
    if cmd == "scopes":
        for name, cfg in nets_fn().items():
            for chan in sorted(cfg["chans"]):
                print(f"{name}/{chan}" + (" ops" if cfg["opts"].get(chan, {}).get("ops") else ""))
        return
    cfg = nets_fn()[net]
    cls(net, cfg["log"], cfg["fifo"], cfg["state"], cfg["chans"],
        opts=cfg.get("opts")).run()
