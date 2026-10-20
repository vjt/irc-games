#!/usr/bin/env python3
"""briscola — la Briscola a 2, 3 o 4, dietro a MikeBongiorno.
Richiesta di Malekith (staff 2026-10-20), regole chiuse su #bot lo stesso
giorno.

Stessa forma di cah.py: tail del bot.log, match dei PRIVMSG, risposte sulla
FIFO del bot. `bot.py` NON si tocca. Regole e casi limite (numerati, ognuno col
suo test) in docs/briscola_spec.md.

I verbi di tavolo stanno sotto `!briscola` (alias `!bri`): nudi li raccolgono
gli altri giochi degli stessi canali.

  !bri [N]           apre il tavolo (60s); N dispari 3..9 = al meglio di N partite
  jo                 si siede, a tavolo aperto (max 4); alias muto !bri entra/join
  !bri esci          si alza (a partita viva: abbandono)
  !bri stop          chiude (chi l'ha aperta, o ircgame.can_control)
  !bri carte         rimanda la mano in privato
  !bri stato         briscola, mazzo, a chi tocca, la serie. Mai i punti
  !bri top           la classifica storica, partite vinte
  pl N               gioca la carta N della mano: in canale, in query o in
                     NOTICE al bot (in NOTICE: mai risposte)

Le partite stanno in memoria: un riavvio le perde, in silenzio. Su disco solo
la classifica storica (partite vinte per nick, per rete).

CLI:
  briscola.py --net azzurra daemon   il daemon
  briscola.py scopes                 i canali risolti, con le opzioni
Env (formato di CAH_NETS; dopo il canale, opzioni separate da `:`):
  BRISCOLA_HOME  BRISCOLA_NICK
  BRISCOLA_NETS=nome|log|fifo|#chan1:ops,#chan2
  IRCGAME_TRUST='vjt *.openssl.it, vjt due.dita.di.grappa.chat'
  IRCGAME_LOCKS=/var/run/convento/ircgame
"""
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
REPO = Path(os.environ.get("BRISCOLA_HOME", HERE))
BOT_NICK = os.environ.get("BRISCOLA_NICK", "MikeBongiorno")

LOBBY_S = 60         # attesa giocatori all'apertura, e a partita muta
TURN_S = 90          # per giocata (caso 1)
PAUSA_S = 10         # fra una partita e l'altra di una serie
MIN_PLAYERS = 2
MAX_PLAYERS = 4
HAND = 3
TIMEOUT_MAX = 2      # turni saltati di fila: abbandono
SERIE = (3, 5, 7, 9)
TICK = 5
HELP = ("!bri [N] apre (N dispari 3-9: al meglio di N) · jo per sedersi · "
        "!bri esci · stop · carte · stato · top — in gioco: pl N")

# --- le carte ----------------------------------------------------------------

SEMI = ("denari", "coppe", "spade", "bastoni")
# 1 = Asso, 8 = Fante, 9 = Cavallo, 10 = Re
NOMI = {1: "Asso", 8: "Fante", 9: "Cavallo", 10: "Re"}
PUNTI = {1: 11, 3: 10, 10: 4, 9: 3, 8: 2}
# dalla piu' forte alla piu' debole: A 3 R C F 7 6 5 4 2
ORDINE = (1, 3, 10, 9, 8, 7, 6, 5, 4, 2)
FORZA = {r: len(ORDINE) - i for i, r in enumerate(ORDINE)}


def mazzo():
    """Le 40 carte, (valore, seme)."""
    return [(r, s) for s in SEMI for r in range(1, 11)]


def nome(c):
    return f"{NOMI.get(c[0], c[0])} di {c[1]}"


def punti(cards):
    return sum(PUNTI.get(c[0], 0) for c in cards)


def tolta_in_tre(seme):
    """In 3 si toglie un 2 che non sia di briscola: le briscole restano 10
    (Malekith 2026-10-20). Il 2 di coppe, o quello di denari se coppe e'
    briscola."""
    return (2, "denari") if seme == "coppe" else (2, "coppe")


def presa(cards, seme):
    """L'indice della carta che prende, nell'ordine in cui sono state giocate
    (la prima e' quella d'uscita). Nessun obbligo di rispondere: prende la
    briscola piu' alta, se no la piu' alta del seme d'uscita; gli altri semi
    non prendono mai."""
    best = 0
    for i, c in enumerate(cards[1:], 1):
        b = cards[best]
        if c[1] == b[1]:
            if FORZA[c[0]] > FORZA[b[0]]:
                best = i
        elif c[1] == seme:
            best = i
    return best


def scarto(hand, seme):
    """La carta che il bot gioca per chi non gioca (caso 1): quella che vale
    meno, a parita' la piu' debole, mai una briscola se c'e' altro."""
    cands = [c for c in hand if c[1] != seme] or hand
    return min(cands, key=lambda c: (PUNTI.get(c[0], 0), FORZA[c[0]]))


def bold(c):
    return f"\x02{nome(c)}\x02"


# --- configurazione ------------------------------------------------------------

def parse_opts(chan, raw):
    """Le opzioni dopo il canale: per ora solo `ops` (ircgame.can_control).
    Una sconosciuta ferma il sidecar."""
    opts = {}
    for o in filter(None, raw):
        if o == "ops":
            opts["ops"] = True
        else:
            raise SystemExit(f"briscola: opzione sconosciuta per {chan}: {o!r}")
    return opts


def load_nets():
    """Come ircgame.load_nets, con le opzioni per canale (`#italia:ops`) in
    `opts`, {canale: {...}}, e i canali senza opzioni in `chans`."""
    nets = ircgame.load_nets(
        "BRISCOLA_NETS", {"azzurra": {"log": REPO / "bot.log", "fifo": REPO / "bot.send",
                                      "chans": {"#sbiffo"},
                                      "state": REPO / "briscola_state.json"}},
        "briscola_state", REPO)
    for cfg in nets.values():
        chans, opts = set(), {}
        for spec in cfg["chans"]:
            chan, *rest = spec.split(":")
            chans.add(chan)
            opts[chan] = parse_opts(chan, rest)
        cfg["chans"], cfg["opts"] = chans, opts
    return nets


BRI_PAT = re.compile(r'^!\s*(?:briscola|bri)(?:\s+(?P<sub>\S+))?(?:\s+(?P<arg>\S+))?\s*$', re.I)
# `pl N` nudo, come CaH; `!pl` alias muto. Piu' numeri = uso sbagliato.
PLAY_PAT = re.compile(r'^!?\s*pl\s+(?P<nums>[\d\s,]+)$', re.I)
JO_PAT = re.compile(r'^jo$', re.I)

SUB = {"start": "start", "inizia": "start", "join": "join", "entra": "join",
       "quit": "quit", "esci": "quit", "stop": "stop", "top": "top",
       "classifica": "top", "cards": "cards", "carte": "cards", "status": "status",
       "stato": "status"}


class Briscola:
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
        self.games = {}                 # in memoria e basta: caso 7
        self.scores = self.load()       # nick casefold -> {"nick", "wins"}
        self.dirty = False
        self.tracker = Tracker()
        # una partita per canale fra tutti i giochi (IRCGAME_LOCKS), caso 6
        self.lock = ircgame.ChannelLock(net, "Briscola", locks)
        self.out = Outbox(fifo, "briscola", self.tracker)
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

    def send_hand(self, g, key):
        hand = g["hands"].get(key)
        if not hand:
            return
        cards = "  ".join(f"{i}) {nome(c)}" for i, c in enumerate(hand, 1))
        self.say(g["nicks"][key], f"Mano: {cards} — briscola: {g['briscola'][1]}")

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

    def team_points(self, g, team):
        return sum(punti(g["prese"][k]) for k in self.members(g, team))

    def team_tricks(self, g, team):
        return sum(g["tricks"][k] for k in self.members(g, team))

    # --- partita -----------------------------------------------------------

    def player_game(self, key):
        for chan, g in self.games.items():
            if key in g["players"]:
                return chan, g
        return None, None

    def turn_key(self, g):
        return g["players"][g["turn"]]

    def open_lobby(self, chan, nick, serie, now):
        key = nick.casefold()
        self.games[chan] = {
            "phase": "lobby", "chan": chan, "starter": key, "players": [key],
            "nicks": {key: nick}, "serie": serie, "wins": {}, "game_no": 0,
            "hands": {}, "deck": [], "briscola": None, "table": [], "turn": 0,
            "prese": {}, "tricks": {}, "giri": 0, "timeouts": {}, "split": [],
            "deadline": now + LOBBY_S, "was": None,
        }
        al = f"al meglio di {serie} partite" if serie > 1 else "partita secca"
        self.say(chan, f"🃏 Briscola! {nick} apre il tavolo, {al}. jo per sedersi, "
                       f"{LOBBY_S}s, da {MIN_PLAYERS} a {MAX_PLAYERS} giocatori.")

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
        if len(g["players"]) >= MAX_PLAYERS:
            self.say(chan, f"{nick}: tavolo pieno, siamo in {MAX_PLAYERS}. "
                           f"Alla prossima partita.")
            return False
        g["nicks"][key] = nick
        g["players"].append(key)
        self.say(chan, f"➕ {nick} si siede ({len(g['players'])} al tavolo).")
        return True

    def start_series(self, chan, g, now):
        """Fine della lobby: posti a sorte (in 4 sono anche le coppie, che
        restano per tutta la serie), poi la prima partita."""
        self.rng.shuffle(g["players"])
        g["wins"] = {t: 0 for t in self.teams(g)}
        self.new_game(chan, g, now)

    def new_game(self, chan, g, now, lead=()):
        n = len(g["players"])
        deck = mazzo()
        self.rng.shuffle(deck)
        # La briscola si scopre dal mazzo intero; in 3 si toglie poi un 2
        # che non sia di briscola, e il resto si rimescola.
        bri = deck.pop(0)
        if n == 3:
            deck.remove(tolta_in_tre(bri[1]))
            self.rng.shuffle(deck)
        g["hands"] = {k: [] for k in g["players"]}
        for _ in range(HAND):
            for k in g["players"]:
                g["hands"][k].append(deck.pop(0))
        deck.append(bri)                # sotto il mazzo, ultima da pescare
        g.update(deck=deck, briscola=bri, table=[], turn=self.rng.randrange(n),
                 prese={k: [] for k in g["players"]}, tricks={k: 0 for k in g["players"]},
                 giri=0, timeouts={}, phase="play", deadline=now + TURN_S)
        g["game_no"] += 1
        head = f"🃏 Partita {g['game_no']}"
        if g["serie"] > 1:
            head += f" (al meglio di {g['serie']})"
        if n == 4:
            seats = (f"Coppie: {self.team_name(g, 0)} contro {self.team_name(g, 1)}.")
        else:
            seats = f"Al tavolo: {', '.join(g['nicks'][k] for k in g['players'])}."
        tolta = f" Fuori il {nome(tolta_in_tre(bri[1]))}." if n == 3 else ""
        self.emit(chan, list(lead) + [
            f"{head}: in tavola {bold(bri)}, briscola a {bri[1]}.{tolta}", seats,
            f"Esce {g['nicks'][self.turn_key(g)]}: pl N, le carte sono in privato, "
            f"{TURN_S}s a giocata."])
        for k in g["players"]:
            self.send_hand(g, k)

    def play(self, chan, g, key, n, now, quiet=False, auto=False):
        """True se la carta e' giocata. Fuori turno o fuori mano: riga d'uso
        in query (caso 5), mai in NOTICE."""
        if g["phase"] != "play" or key not in g["players"]:
            return False
        nick = g["nicks"][key]
        if self.turn_key(g) != key:
            if not quiet:
                self.say(nick, f"Non tocca a te: tocca a {g['nicks'][self.turn_key(g)]}.")
            return False
        hand = g["hands"][key]
        if not 1 <= n <= len(hand):
            if not quiet:
                self.say(nick, f"Hai {len(hand)} carte: pl 1"
                               + (f"-{len(hand)}." if len(hand) > 1 else "."))
            return False
        c = hand.pop(n - 1)
        g["table"].append((key, c))
        if not auto:
            g["timeouts"].pop(key, None)
        line = (f"⏰ {nick} non gioca, il bot cala {bold(c)}" if auto
                else f"{nick}: {bold(c)}")
        players = g["players"]
        if len(g["table"]) < len(players):
            g["turn"] = (g["turn"] + 1) % len(players)
            g["deadline"] = now + TURN_S
            self.emit(chan, [line, f"tocca a {g['nicks'][self.turn_key(g)]}"])
            if auto:
                self.send_hand(g, key)
            return True
        self.close_trick(chan, g, now, line)
        return True

    def close_trick(self, chan, g, now, line):
        cards = [c for _, c in g["table"]]
        wk, wc = g["table"][presa(cards, g["briscola"][1])]
        g["prese"][wk].extend(cards)
        g["tricks"][wk] += 1
        g["giri"] += 1
        g["table"] = []
        players = g["players"]
        w = players.index(wk)
        # Chi prende pesca per primo, poi gli altri in ordine; la briscola
        # scoperta e' l'ultima carta del mazzo, la prende l'ultimo che pesca.
        if g["deck"]:
            for i in range(len(players)):
                g["hands"][players[(w + i) % len(players)]].append(g["deck"].pop(0))
        g["turn"] = w
        parts = [line, f"✋ prende {g['nicks'][wk]} con {nome(wc)}"]
        left = len(g["hands"][wk])
        if not left:
            self.end_game(chan, g, now, parts)
            return
        parts.append(f"nel mazzo {len(g['deck'])}" if g["deck"]
                     else f"mazzo finito, ultime {left} mani")
        parts.append(f"esce {g['nicks'][wk]}")
        g["deadline"] = now + TURN_S
        self.emit(chan, parts)
        for k in players:
            self.send_hand(g, k)

    def conto(self, g):
        """Il conto di fine partita: punti e prese, di ognuno o di coppia."""
        rows = sorted(self.teams(g), key=lambda t: (-self.team_points(g, t), t))
        return "🃏 Conto: " + " · ".join(
            f"{self.team_name(g, t)} {self.team_points(g, t)} punti, "
            f"{self.team_tricks(g, t)} prese" for t in rows)

    def winner(self, g):
        """La squadra che vince a punti, None = patta (60 a 60, o in 3
        parita' in testa)."""
        pts = {t: self.team_points(g, t) for t in self.teams(g)}
        best = max(pts.values())
        top = [t for t, p in pts.items() if p == best]
        return top[0] if len(top) == 1 else None

    def end_game(self, chan, g, now, lead=()):
        self.result(chan, g, now, self.winner(g), list(lead) + [self.conto(g)])

    def result(self, chan, g, now, win, lead, ranked=True, interrupted=False):
        """Chiude la partita con `win` (None = patta) e manda avanti la serie:
        la patta non conta mai e si rigioca; la serie finisce appena una
        squadra ne vince (N+1)/2."""
        lines = list(lead)
        if win is None:
            lines.append("🤝 Patta.")
        else:
            lines.append(f"🏆 {self.team_name(g, win)} {self.vince(g, win)} la partita!")
            if ranked:
                self.credit(g, win)
            g["wins"][win] = g["wins"].get(win, 0) + 1
        if interrupted:
            self.close(chan, lines + (["Serie interrotta."] if g["serie"] > 1 else []))
        elif g["serie"] == 1:
            self.close(chan, lines)         # partita secca: anche la patta chiude
        elif win is not None and g["wins"][win] >= (g["serie"] + 1) // 2:
            self.close(chan, lines + [f"🎉 {self.team_name(g, win)} {self.vince(g, win)} "
                                      f"la serie: {self.serie_line(g)}."])
        else:
            if win is None:
                lines.append("La patta non conta, si rigioca.")
            if g["serie"] > 1:
                lines.append(f"Serie: {self.serie_line(g)}.")
            lines.append(f"Prossima partita fra {PAUSA_S}s.")
            g["phase"] = "pausa"
            g["deadline"] = now + PAUSA_S
            self.emit(chan, lines)

    def serie_line(self, g):
        return ", ".join(f"{self.team_name(g, t)} {g['wins'].get(t, 0)}" for t in self.teams(g))

    def close(self, chan, lines):
        self.games.pop(chan, None)
        self.emit(chan, lines)

    def abandon(self, chan, g, key, now, why=""):
        """Caso 2. In lobby si alza e basta; fra due partite della serie la
        serie finisce; a partita viva la partita finisce: in 2 e in 4 vince
        l'altra squadra, in 3 chi dei due rimasti ha piu' punti. In classifica
        conta solo dopo il primo giro."""
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
        if g["phase"] == "pausa" or (g["phase"] == "muto" and g["was"] == "pausa"):
            self.close(chan, [lead, "🃏 Serie interrotta."])
            return
        others = [t for t in self.teams(g) if t != self.team(g, key)]
        if len(others) == 1:
            win = others[0]
        else:
            pts = {t: self.team_points(g, t) for t in others}
            best = max(pts.values())
            top = [t for t in others if pts[t] == best]
            win = top[0] if len(top) == 1 else None
        self.result(chan, g, now, win, [lead, "Partita finita per abbandono.", self.conto(g)],
                    ranked=g["giri"] >= 1, interrupted=True)

    def timeout(self, chan, g, now):
        """Caso 1: il bot gioca per lui la carta che vale meno. Al secondo di
        fila, abbandono. Chi non e' in uno split riceve anche un ISON: su +u il
        QUIT non arriva, e chi e' offline se n'e' andato."""
        key = self.turn_key(g)
        g["timeouts"][key] = g["timeouts"].get(key, 0) + 1
        if g["timeouts"][key] >= TIMEOUT_MAX:
            self.abandon(chan, g, key, now, f", {TIMEOUT_MAX} turni saltati")
            return
        if key not in g["split"]:
            self.ison.append({key: chan})
            self.out.raw(f"ISON {g['nicks'][key]}")
        hand = g["hands"][key]
        c = scarto(hand, g["briscola"][1])
        self.play(chan, g, key, hand.index(c) + 1, now, auto=True)

    def status(self, chan, g, now):
        """Mai i punti presi nella partita in corso: si contano a mente."""
        left = max(0, int(g["deadline"] - now))
        ph = g["phase"]
        if ph == "lobby":
            msg = (f"🃏 Tavolo aperto: {', '.join(g['nicks'][k] for k in g['players'])}; "
                   f"{left}s.")
        elif ph == "pausa":
            msg = f"🃏 Prossima partita fra {left}s."
        else:
            msg = (f"🃏 Partita {g['game_no']}: briscola {nome(g['briscola'])} · nel mazzo "
                   f"{len(g['deck'])} · tocca a {g['nicks'][self.turn_key(g)]} ({left}s).")
        if ph != "lobby" and g["serie"] > 1:
            msg += f" Serie al meglio di {g['serie']}: {self.serie_line(g)}."
        self.say(chan, msg)

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
            if m and g and not is_chan(target):
                self.numbers(chan, g, key, m, now, quiet=True)
            return
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
            if g and key in g["players"]:
                self.numbers(chan, g, key, m, now)
            return
        if JO_PAT.match(text):
            if g:
                self.join(chan, g, nick, now)
            return
        m = BRI_PAT.match(text)
        if not m:
            return
        raw, arg = (m.group("sub") or "start"), (m.group("arg") or "")
        serie = None
        if raw.isdigit():
            sub, serie = "start", int(raw)
        else:
            sub = SUB.get(raw.lower())
            if sub == "start" and arg.isdigit():
                serie = int(arg)
        if sub is None:
            self.say(chan, HELP)
        elif sub == "top":
            self.say(chan, f"🏆 [Briscola] Partite vinte ({self.net}): {self.top() or 'nessuna'}")
        elif sub == "start" and not g:
            self.start(chan, nick, host, serie, now)
        elif not g:
            self.say(chan, "Nessuna partita: !bri per aprire il tavolo.")
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

    def start(self, chan, nick, host, serie, now):
        if self.op_gate(chan, nick, host, "apre"):
            return
        if self.player_game(nick.casefold())[0]:
            self.say(chan, f"{nick}: stai gia' giocando altrove.")
            return
        if serie is not None and serie != 1:
            if serie % 2 == 0:
                self.say(chan, f"{nick}: {serie} deve essere dispari (3, 5, 7 o 9).")
                return
            if serie not in SERIE:
                self.say(chan, f"{nick}: al meglio di 3, 5, 7 o 9 partite.")
                return
        no = ircgame.start_refusal(self.lock, self.tracker, chan, now)
        if no:
            self.say(chan, no)
            return
        self.open_lobby(chan, nick, serie or 1, now)

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

    def numbers(self, chan, g, key, m, now, quiet=False):
        nums = [int(x) for x in re.split(r"[\s,]+", m.group("nums").strip()) if x]
        if g["phase"] != "play":
            return
        if len(nums) != 1:
            if not quiet:
                self.say(g["nicks"][key], "Una carta per volta: pl N.")
            return
        self.play(chan, g, key, nums[0], now, quiet=quiet)

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
            if not g:
                return
            if ev[3]:
                # Caso 4: netsplit. Non e' abbandono, il turno aspetta il
                # timeout normale e poi vale il caso 1.
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
        g["deadline"] = now + LOBBY_S
        print(f"briscola: {chan} rifiuta le righe (404), partita in pausa", flush=True)

    def rename(self, old, new_nick):
        """Caso 3: mano, posto e turno passano al nick nuovo."""
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
        for d in (g["hands"], g["prese"], g["tricks"], g["timeouts"]):
            if old in d:
                d[new] = d.pop(old)
        g["table"] = [(new if k == old else k, c) for k, c in g["table"]]
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
                    self.start_series(chan, g, now)
                else:
                    self.close(chan, [f"🃏 Serve almeno un altro giocatore, al tavolo "
                                      f"solo {g['nicks'][g['players'][0]]}. Chiuso."])
            elif ph == "muto":
                if not self.tracker.can_speak(chan, BOT_NICK):
                    self.games.pop(chan, None)
                    print(f"briscola: {chan} ancora muto dopo {LOBBY_S}s, partita chiusa",
                          flush=True)
                elif g["was"] == "pausa":
                    self.new_game(chan, g, now, ["▶️ Si riprende."])
                else:
                    g["phase"], g["deadline"] = "play", now + TURN_S
                    self.say(chan, f"▶️ Si riprende: tocca a {g['nicks'][self.turn_key(g)]}.")
            elif ph == "pausa":
                self.new_game(chan, g, now)
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


def main():
    argv = sys.argv[1:]
    net = "azzurra"
    if "--net" in argv:
        i = argv.index("--net")
        net = argv[i + 1]
        del argv[i:i + 2]
    cmd = argv[0] if argv else "daemon"
    if cmd == "scopes":
        for name, cfg in load_nets().items():
            for chan in sorted(cfg["chans"]):
                print(f"{name}/{chan}" + (" ops" if cfg["opts"].get(chan, {}).get("ops") else ""))
        return
    cfg = load_nets()[net]
    Briscola(net, cfg["log"], cfg["fifo"], cfg["state"], cfg["chans"],
             opts=cfg.get("opts")).run()


if __name__ == "__main__":
    main()
