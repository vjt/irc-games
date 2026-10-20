#!/usr/bin/env python3
"""scopa — la Scopa a 2, 3 o 4, dietro a MikeBongiorno.
Richiesta di Malekith (#bot 2026-10-20), regole chiuse su #bot lo stesso
giorno: docs/scopa_spec.md.

Stesso mazzo e stesso motore di tavolo della briscola (tavolo.py): lobby,
`jo`, mani in query, timeout, abbandono, cambio nick, netsplit, ISON,
lucchetto per canale, classifica. Qui solo le regole della scopa.

I verbi stanno sotto `!scopa`, senza alias corto.

  !scopa [21]        apre il tavolo (60s): partita a 11, o a 21
  jo                 si siede, a tavolo aperto (max 4)
  !scopa esci        si alza (a partita viva: abbandono)
  !scopa stop        chiude (chi l'ha aperta, o ircgame.can_control)
  !scopa carte       rimanda la mano in privato
  !scopa stato       tavola, a chi tocca, mazzo, scope, punti della partita
  !scopa top         la classifica storica, partite vinte
  pl N               gioca la carta N; se la presa possibile e' una, la fa il bot
  pl N a b ...       gioca la carta N e prende le carte a, b ... della tavola
                     (in canale, in query o in NOTICE al bot; in NOTICE mai
                     risposte)

Le partite stanno in memoria: un riavvio le perde, in silenzio. Su disco solo
la classifica storica (partite vinte per nick, per rete).

CLI:
  scopa.py --net azzurra daemon   il daemon
  scopa.py scopes                 i canali risolti, con le opzioni
Env (formato di BRISCOLA_NETS):
  SCOPA_HOME  SCOPA_NICK
  SCOPA_NETS=nome|log|fifo|#chan1:ops,#chan2
  IRCGAME_TRUST='vjt *.openssl.it, vjt due.dita.di.grappa.chat'
  IRCGAME_LOCKS=/var/run/convento/ircgame
"""
import os
import re
from itertools import combinations
from pathlib import Path

import tavolo
from ircgame import MAX_LINE, joined_lines
from tavolo import bold, mazzo, nome

HERE = Path(__file__).resolve().parent
REPO = Path(os.environ.get("SCOPA_HOME", HERE))
BOT_NICK = os.environ.get("SCOPA_NICK", "MikeBongiorno")

LOBBY_S = 60         # attesa giocatori all'apertura, e a partita muta
TURN_S = 90          # per giocata
PAUSA_S = 10         # fra una smazzata e l'altra: il tempo di leggere il conto
MIN_PLAYERS = 2
MAX_PLAYERS = 4
HAND = 3
IN_TAVOLA = 4
TIMEOUT_MAX = 2      # turni saltati di fila: abbandono
SOGLIE = (11, 21)
HELP = ("!scopa [21] apre (a 11, o a 21) · jo per sedersi · !scopa esci · stop · "
        "carte · stato · top — in gioco: pl N, o pl N a b per scegliere la presa")

# --- le regole ----------------------------------------------------------------

SETTEBELLO = (7, "denari")
# il valore in primiera, per carta: le figure 10
PRIMIERA = {7: 21, 6: 18, 1: 16, 5: 15, 4: 14, 3: 13, 2: 12, 8: 10, 9: 10, 10: 10}


def prese_possibili(table, card):
    """Le prese possibili con `card`, come tuple di indici (da 0) della
    tavola. Lo stesso valore e' obbligo e viene prima di ogni somma: se c'e',
    le prese sono solo quelle di una carta. Se no, ogni gruppo di due o piu'
    carte che fa il valore. Nessuna presa: lista vuota, la carta resta in
    tavola."""
    v = card[0]
    same = [(i,) for i, c in enumerate(table) if c[0] == v]
    if same:
        return same
    out = []
    for r in range(2, len(table) + 1):
        for combo in combinations(range(len(table)), r):
            if sum(table[i][0] for i in combo) == v:
                out.append(combo)
    return out


def primiera(cards):
    """Il totale di primiera: la carta migliore di ogni seme. None senza un
    seme: chi non ce li ha tutti e quattro non fa primiera."""
    best = {}
    for r, s in cards:
        best[s] = max(best.get(s, 0), PRIMIERA[r])
    return sum(best.values()) if len(best) == 4 else None


def solo(vals):
    """La chiave del valore piu' alto, se e' uno solo; pari (o nessuno): None.
    `vals` e' {squadra: numero o None}; None non concorre."""
    vals = {t: v for t, v in vals.items() if v is not None}
    if not vals:
        return None
    best = max(vals.values())
    top = [t for t, v in vals.items() if v == best]
    return top[0] if len(top) == 1 else None


def scarto(table, hand):
    """La giocata del bot per chi non gioca: (carta, presa). La carta che non
    prende niente e vale meno (a parita', non di denari); se tutte prendono,
    quella che prende di piu' (a parita', piu' denari, poi il settebello)."""
    cands = [(c, prese_possibili(table, c)) for c in hand]
    free = [c for c, caps in cands if not caps]
    if free:
        return min(free, key=lambda c: (c[0], c[1] == "denari")), ()

    def peso(cap):
        taken = [table[i] for i in cap]
        return (len(taken), sum(c[1] == "denari" for c in taken), SETTEBELLO in taken)

    c, caps = max(cands, key=lambda cc: max(peso(cap) for cap in cc[1]))
    return c, max(caps, key=peso)


def tavola_line(table):
    if not table:
        return "tavola vuota"
    return "in tavola: " + "  ".join(f"{i}) {nome(c)}" for i, c in enumerate(table, 1))


# --- configurazione ------------------------------------------------------------

def parse_opts(chan, raw):
    return tavolo.parse_opts("scopa", chan, raw)


def load_nets():
    return tavolo.load_nets("scopa", "SCOPA_NETS", REPO)


SCOPA_PAT = re.compile(r'^!\s*scopa(?:\s+(?P<sub>\S+))?(?:\s+(?P<arg>\S+))?\s*$', re.I)


class Scopa(tavolo.Tavolo):
    GAME = "Scopa"
    TAG = "scopa"
    CMD = "!scopa"
    CMD_PAT = SCOPA_PAT
    HELP = HELP
    BOT_NICK = BOT_NICK
    LOBBY_S, TURN_S, PAUSA_S = LOBBY_S, TURN_S, PAUSA_S
    MIN_PLAYERS, MAX_PLAYERS, TIMEOUT_MAX = MIN_PLAYERS, MAX_PLAYERS, TIMEOUT_MAX
    PER_PLAYER = ("hands", "prese", "scope", "timeouts")

    # --- squadre -----------------------------------------------------------

    def team_cards(self, g, team):
        return [c for k in self.members(g, team) for c in g["prese"][k]]

    def team_scope(self, g, team):
        return sum(g["scope"][k] for k in self.members(g, team))

    def partita_line(self, g):
        return (f"partita a {g['target']}: "
                + ", ".join(f"{self.team_name(g, t)} {g['match'].get(t, 0)}"
                            for t in self.teams(g)))

    # --- partita -----------------------------------------------------------

    def lobby_line(self, g, nick, target):
        g.update(target=target, match={}, mano_no=0, dealer=0, prese={}, scope={},
                 last=None, giocate=0)
        return (f"🃏 Scopa! {nick} apre il tavolo, partita a {target}. jo per sedersi, "
                f"{LOBBY_S}s, da {MIN_PLAYERS} a {MAX_PLAYERS} giocatori.")

    def start_param(self, chan, nick, arg):
        """Solo la soglia: niente «al meglio di N», la serie e' della briscola
        (Malekith 2026-10-20). Qualunque altro argomento: riga d'uso."""
        if not arg:
            return True, SOGLIE[0]
        if arg.isdigit() and int(arg) in SOGLIE:
            return True, int(arg)
        self.say(chan, HELP)
        return False, None

    def start_match(self, chan, g, now):
        """Fine della lobby: posti a sorte (in 4 sono anche le coppie, che
        restano per tutta la partita) e il mazziere, poi la prima smazzata."""
        self.rng.shuffle(g["players"])
        g["match"] = {t: 0 for t in self.teams(g)}
        g["dealer"] = self.rng.randrange(len(g["players"]))
        self.new_round(chan, g, now)

    def new_round(self, chan, g, now, lead=()):
        """Una smazzata: 3 a testa e 4 scoperte; con 3 o 4 Re in tavola si
        rimescola e si ridistribuisce. Esce chi siede dopo il mazziere, e il
        mazzo gira di un posto a ogni smazzata."""
        players = g["players"]
        n = len(players)
        if g["mano_no"]:
            g["dealer"] = (g["dealer"] + 1) % n
        lead = list(lead)
        while True:
            deck = mazzo()
            self.rng.shuffle(deck)
            hands = {k: [deck.pop(0) for _ in range(HAND)] for k in players}
            table = [deck.pop(0) for _ in range(IN_TAVOLA)]
            if sum(c[0] == 10 for c in table) < 3:
                break
            lead.append(f"🔄 {sum(c[0] == 10 for c in table)} Re in tavola, si rimescola.")
        g.update(deck=deck, hands=hands, table=table, turn=(g["dealer"] + 1) % n,
                 prese={k: [] for k in players}, scope={k: 0 for k in players},
                 last=None, timeouts={}, phase="play", deadline=now + TURN_S)
        g["mano_no"] += 1
        self.emit(chan, lead + [
            f"🃏 Smazzata {g['mano_no']}, {tavola_line(table)}.", self.seats_line(g),
            f"Esce {g['nicks'][self.turn_key(g)]}: pl N, o pl N a b per scegliere la presa; "
            f"le carte sono in privato, {TURN_S}s a giocata."])
        for k in players:
            self.send_hand(g, k)

    def numbers(self, chan, g, key, nums, now, quiet=False):
        self.play(chan, g, key, nums[0], nums[1:], now, quiet=quiet)

    def play(self, chan, g, key, n, picks, now, quiet=False, auto=False):
        """True se la carta e' giocata. Fuori turno, fuori mano, presa
        ambigua o sbagliata: riga in query e il turno resta suo; in NOTICE
        mai risposte."""
        if g["phase"] != "play" or key not in g["players"]:
            return False
        nick = g["nicks"][key]

        def no(text):
            if not quiet:
                self.say(nick, text)
            return False

        if self.turn_key(g) != key:
            return no(f"Non tocca a te: tocca a {g['nicks'][self.turn_key(g)]}.")
        hand, table = g["hands"][key], g["table"]
        if not 1 <= n <= len(hand):
            return no(f"Hai {len(hand)} carte: pl 1" + (f"-{len(hand)}." if len(hand) > 1 else "."))
        card = hand[n - 1]
        caps = prese_possibili(table, card)
        if picks:
            sel = tuple(sorted(p - 1 for p in picks))
            if len(set(sel)) != len(sel) or not all(0 <= i < len(table) for i in sel):
                return no(f"In tavola ci sono {len(table)} carte: pl {n} e i numeri "
                          f"delle carte da prendere.")
            if sel not in caps:
                return no(self.wrong_pick(table, card, n, caps))
        elif len(caps) > 1:
            if not quiet:
                self.ambiguous(nick, card, n, caps)
            return False
        else:
            sel = caps[0] if caps else ()
        hand.pop(n - 1)
        if not auto:
            g["timeouts"].pop(key, None)
        g["giocate"] += 1
        last_play = not g["deck"] and not any(g["hands"].values())
        who = f"⏰ {nick} non gioca, il bot cala" if auto else f"{nick}:"
        parts = []
        if sel:
            taken = [table[i] for i in sel]
            g["table"] = table = [c for i, c in enumerate(table) if i not in sel]
            g["prese"][key].extend(taken + [card])
            g["last"] = key
            parts.append(f"{who} {bold(card)} prende {', '.join(nome(c) for c in taken)}")
            if not table and not last_play:
                g["scope"][key] += 1
                parts.append("🧹 SCOPA!")
        else:
            table.append(card)
            parts.append(f"{who} {bold(card)}")
        self.next_turn(chan, g, key, now, parts, quiet)
        return True

    def wrong_pick(self, table, card, n, caps):
        same = [i for i, c in enumerate(table) if c[0] == card[0]]
        if same:
            return (f"In tavola c'e' {nome(table[same[0]])}: lo stesso valore si prende "
                    f"per forza, prima di ogni somma. "
                    + " oppure ".join(f"pl {n} {i + 1}" for i in same) + ".")
        if not caps:
            return f"Col {nome(card)} non si prende niente: pl {n} e la carta resta in tavola."
        return (f"La somma non torna: col {nome(card)} fa {card[0]}. "
                + " oppure ".join(self.pick_cmd(n, cap) for cap in caps) + ".")

    @staticmethod
    def pick_cmd(n, cap):
        return f"pl {n} " + " ".join(str(i + 1) for i in cap)

    def ambiguous(self, nick, card, n, caps):
        """Piu' prese possibili e nessuna scelta: l'elenco in query."""
        head = f"Col {nome(card)} le prese sono piu' d'una: "
        opts = [self.pick_cmd(n, cap) for cap in caps]
        lines = joined_lines(opts, limit=MAX_LINE - len(head.encode()), sep=" oppure ")
        self.say(nick, head + lines[0])
        for line in lines[1:]:
            self.say(nick, line)

    def next_turn(self, chan, g, key, now, parts, quiet):
        """Il turno passa; a mani finite si ridistribuisce, a mazzo finito la
        smazzata si chiude. La mano aggiornata va in query a chi ha giocato
        (i numeri scorrono), tranne a chi gioca in NOTICE: a lui mai righe."""
        players = g["players"]
        g["turn"] = (g["turn"] + 1) % len(players)
        if not any(g["hands"].values()):
            if not g["deck"]:
                self.end_round(chan, g, now, parts)
                return
            # mani finite, c'e' mazzo: 3 a testa, in tavola niente
            for _ in range(HAND):
                for i in range(len(players)):
                    k = players[(g["turn"] + i) % len(players)]
                    g["hands"][k].append(g["deck"].pop(0))
            parts += [tavola_line(g["table"]), f"3 carte a testa, nel mazzo {len(g['deck'])}",
                      f"tocca a {g['nicks'][self.turn_key(g)]}"]
            g["deadline"] = now + TURN_S
            self.emit(chan, parts)
            for k in players:
                self.send_hand(g, k)
            return
        parts.append(tavola_line(g["table"]))
        parts.append(f"tocca a {g['nicks'][self.turn_key(g)]}")
        g["deadline"] = now + TURN_S
        self.emit(chan, parts)
        if g["hands"][key] and not quiet:
            self.send_hand(g, key)

    # --- fine smazzata -----------------------------------------------------

    def conto(self, g):
        """I punti della smazzata, voce per voce: (righe, {squadra: punti})."""
        teams = self.teams(g)
        cards = {t: self.team_cards(g, t) for t in teams}
        pts = {t: 0 for t in teams}
        voci = []

        def voce(label, vals, show):
            win = solo(vals)
            if win is not None:
                pts[win] += 1
            detail = ", ".join(f"{self.team_name(g, t)} {show(vals[t])}" for t in teams)
            to = self.team_name(g, win) if win is not None else "nessuno"
            voci.append(f"{label} a {to} ({detail})")

        voce("carte", {t: len(c) for t, c in cards.items()}, str)
        voce("denari", {t: sum(s == "denari" for _, s in c) for t, c in cards.items()}, str)
        bello = next((t for t, c in cards.items() if SETTEBELLO in c), None)
        if bello is not None:
            pts[bello] += 1
        voci.append(f"settebello a {self.team_name(g, bello) if bello is not None else 'nessuno'}")
        voce("primiera", {t: primiera(c) for t, c in cards.items()},
             lambda v: "senza un seme" if v is None else str(v))
        sc = {t: self.team_scope(g, t) for t in teams}
        for t in teams:
            pts[t] += sc[t]
        voci.append("scope: " + (", ".join(f"{self.team_name(g, t)} {sc[t]}" for t in teams
                                          if sc[t]) or "nessuna"))
        return voci, pts

    def end_round(self, chan, g, now, lead=()):
        """Mazzo finito: le carte rimaste in tavola vanno a chi ha preso per
        ultimo (non e' scopa), poi il conto e la partita."""
        lines = list(lead)
        if g["table"]:
            if g["last"] is not None:
                g["prese"][g["last"]].extend(g["table"])
                lines.append(f"Le {len(g['table'])} in tavola vanno a {g['nicks'][g['last']]}, "
                             f"ultimo a prendere.")
            g["table"] = []
        voci, pts = self.conto(g)
        for t, p in pts.items():
            g["match"][t] = g["match"].get(t, 0) + p
        voci[0] = f"🃏 Conto della smazzata {g['mano_no']}: {voci[0]}"
        lines += voci
        lines.append("Punti: " + ", ".join(f"{self.team_name(g, t)} +{pts[t]}"
                                           for t in self.teams(g))
                     + f" · {self.partita_line(g)}.")
        win = self.winner(g)
        if win is not None:
            self.credit(g, win)
            self.close(chan, lines + [f"🏆 {self.team_name(g, win)} {self.vince(g, win)} "
                                      f"la partita, {g['match'][win]} a "
                                      f"{max(v for t, v in g['match'].items() if t != win)}!"])
            return
        top = max(g["match"].values())
        if top >= g["target"]:
            lines.append(f"Pari in testa a {top}: un'altra smazzata.")
        lines.append(f"Prossima smazzata fra {PAUSA_S}s.")
        g["phase"] = "pausa"
        g["deadline"] = now + PAUSA_S
        self.emit(chan, lines)

    def winner(self, g):
        """Chi e' alla soglia o oltre e da solo in testa; pari in testa, o
        nessuno alla soglia: None, si gioca ancora."""
        win = solo(g["match"])
        return win if win is not None and g["match"][win] >= g["target"] else None

    # --- abbandono e timeout ---------------------------------------------------

    def abandon_standing(self, g, team):
        """In 3 vince chi dei due rimasti ha piu' punti nella partita."""
        return g["match"].get(team, 0)

    def abandoned(self, chan, g, now, win, lead):
        """La partita finisce (anche fra due smazzate: e' sempre la stessa
        partita). In classifica conta solo dopo il primo giro di carte."""
        lines = [lead, "Partita finita per abbandono.", f"🃏 {self.partita_line(g)}."]
        if win is None:
            lines.append("🤝 Nessun vincitore.")
        else:
            lines.append(f"🏆 {self.team_name(g, win)} {self.vince(g, win)} la partita!")
            if g["giocate"] >= len(g["players"]):
                self.credit(g, win)
        self.close(chan, lines)

    def autoplay(self, chan, g, key, now):
        """Al timeout il bot gioca la carta che non prende niente e vale
        meno; se tutte prendono, quella che prende di piu'."""
        hand = g["hands"][key]
        card, cap = scarto(g["table"], hand)
        self.play(chan, g, key, hand.index(card) + 1, [i + 1 for i in cap], now, auto=True)

    def renamed(self, g, old, new):
        if g.get("last") == old:
            g["last"] = new

    def status(self, chan, g, now):
        """Tavola, turno, mazzo, scope e punti della partita (al tavolo vero si
        segnano). Le carte prese no: si contano a mente."""
        left = max(0, int(g["deadline"] - now))
        ph = g["phase"]
        if ph == "lobby":
            self.say(chan, f"🃏 Tavolo aperto, partita a {g['target']}: "
                           f"{', '.join(g['nicks'][k] for k in g['players'])}; {left}s.")
            return
        if ph == "pausa":
            self.emit(chan, [f"🃏 Prossima smazzata fra {left}s", f"{self.partita_line(g)}."])
            return
        sc = ", ".join(f"{self.team_name(g, t)} {self.team_scope(g, t)}"
                       for t in self.teams(g) if self.team_scope(g, t))
        self.emit(chan, [f"🃏 Smazzata {g['mano_no']}, {tavola_line(g['table'])}",
                         f"nel mazzo {len(g['deck'])}",
                         f"tocca a {g['nicks'][self.turn_key(g)]} ({left}s)",
                         f"scope: {sc or 'nessuna'}", f"{self.partita_line(g)}."])


def main():
    tavolo.main(Scopa, load_nets)


if __name__ == "__main__":
    main()
