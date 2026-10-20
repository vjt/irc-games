#!/usr/bin/env python3
"""briscola — la Briscola a 2, 3 o 4, dietro a MikeBongiorno.
Richiesta di Malekith (staff 2026-10-20), regole chiuse su #bot lo stesso
giorno.

Stessa forma di cah.py: tail del bot.log, match dei PRIVMSG, risposte sulla
FIFO del bot. `bot.py` NON si tocca. Regole e casi limite (numerati, ognuno col
suo test) in docs/briscola_spec.md. Il motore di tavolo (lobby, turni,
abbandoni, nick, netsplit, lucchetto, loop) e' in tavolo.py, con la scopa.

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
import re
from pathlib import Path

import tavolo
from tavolo import NOMI, SEMI, bold, mazzo, nome  # noqa: F401  (le prove li usano da qui)

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
HELP = ("!bri [N] apre (N dispari 3-9: al meglio di N) · jo per sedersi · "
        "!bri esci · stop · carte · stato · top — in gioco: pl N")

# --- le carte ----------------------------------------------------------------

PUNTI = {1: 11, 3: 10, 10: 4, 9: 3, 8: 2}
# dalla piu' forte alla piu' debole: A 3 R C F 7 6 5 4 2
ORDINE = (1, 3, 10, 9, 8, 7, 6, 5, 4, 2)
FORZA = {r: len(ORDINE) - i for i, r in enumerate(ORDINE)}


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


# --- configurazione ------------------------------------------------------------

def parse_opts(chan, raw):
    return tavolo.parse_opts("briscola", chan, raw)


def load_nets():
    return tavolo.load_nets("briscola", "BRISCOLA_NETS", REPO)


BRI_PAT = re.compile(r'^!\s*(?:briscola|bri)(?:\s+(?P<sub>\S+))?(?:\s+(?P<arg>\S+))?\s*$', re.I)


class Briscola(tavolo.Tavolo):
    GAME = "Briscola"
    TAG = "briscola"
    CMD = "!bri"
    CMD_PAT = BRI_PAT
    HELP = HELP
    BOT_NICK = BOT_NICK
    LOBBY_S, TURN_S, PAUSA_S = LOBBY_S, TURN_S, PAUSA_S
    MIN_PLAYERS, MAX_PLAYERS, TIMEOUT_MAX = MIN_PLAYERS, MAX_PLAYERS, TIMEOUT_MAX
    PER_PLAYER = ("hands", "prese", "tricks", "timeouts")

    def hand_line(self, g, hand):
        return super().hand_line(g, hand) + f" — briscola: {g['briscola'][1]}"

    # --- squadre -----------------------------------------------------------

    def team_points(self, g, team):
        return sum(punti(g["prese"][k]) for k in self.members(g, team))

    def team_tricks(self, g, team):
        return sum(g["tricks"][k] for k in self.members(g, team))

    # --- partita -----------------------------------------------------------

    def lobby_line(self, g, nick, serie):
        g.update(serie=serie, wins={}, game_no=0, briscola=None, prese={}, tricks={}, giri=0)
        al = f"al meglio di {serie} partite" if serie > 1 else "partita secca"
        return (f"🃏 Briscola! {nick} apre il tavolo, {al}. jo per sedersi, "
                f"{LOBBY_S}s, da {MIN_PLAYERS} a {MAX_PLAYERS} giocatori.")

    def start_param(self, chan, nick, arg):
        serie = int(arg) if arg.isdigit() else None
        if serie is not None and serie != 1:
            if serie % 2 == 0:
                self.say(chan, f"{nick}: {serie} deve essere dispari (3, 5, 7 o 9).")
                return False, None
            if serie not in SERIE:
                self.say(chan, f"{nick}: al meglio di 3, 5, 7 o 9 partite.")
                return False, None
        return True, serie or 1

    def start_match(self, chan, g, now):
        """Fine della lobby: posti a sorte (in 4 sono anche le coppie, che
        restano per tutta la serie), poi la prima partita."""
        self.rng.shuffle(g["players"])
        g["wins"] = {t: 0 for t in self.teams(g)}
        self.new_round(chan, g, now)

    def new_round(self, chan, g, now, lead=()):
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
        tolta = f" Fuori il {nome(tolta_in_tre(bri[1]))}." if n == 3 else ""
        self.emit(chan, list(lead) + [
            f"{head}: in tavola {bold(bri)}, briscola a {bri[1]}.{tolta}", self.seats_line(g),
            f"Esce {g['nicks'][self.turn_key(g)]}: pl N, le carte sono in privato, "
            f"{TURN_S}s a giocata."])
        for k in g["players"]:
            self.send_hand(g, k)

    def numbers(self, chan, g, key, nums, now, quiet=False):
        if len(nums) != 1:
            if not quiet:
                self.say(g["nicks"][key], "Una carta per volta: pl N.")
            return
        self.play(chan, g, key, nums[0], now, quiet=quiet)

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

    # --- abbandono e timeout (casi 1 e 2) -----------------------------------

    def abandon_between(self, chan, g, lead):
        """Caso 2: fra due partite della serie la serie finisce."""
        if g["phase"] == "pausa" or (g["phase"] == "muto" and g["was"] == "pausa"):
            self.close(chan, [lead, "🃏 Serie interrotta."])
            return True
        return False

    def abandon_standing(self, g, team):
        """In 3 vince chi dei due rimasti ha piu' punti."""
        return self.team_points(g, team)

    def abandoned(self, chan, g, now, win, lead):
        """In classifica conta solo dopo il primo giro."""
        self.result(chan, g, now, win, [lead, "Partita finita per abbandono.", self.conto(g)],
                    ranked=g["giri"] >= 1, interrupted=True)

    def autoplay(self, chan, g, key, now):
        """Caso 1: il bot gioca per lui la carta che vale meno."""
        hand = g["hands"][key]
        c = scarto(hand, g["briscola"][1])
        self.play(chan, g, key, hand.index(c) + 1, now, auto=True)

    def renamed(self, g, old, new):
        g["table"] = [(new if k == old else k, c) for k, c in g["table"]]

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


def main():
    tavolo.main(Briscola, load_nets)


if __name__ == "__main__":
    main()
