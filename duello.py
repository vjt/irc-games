#!/usr/bin/env python3
"""duello — duello a insulti fra due giocatori, dietro a MikeBongiorno.
Richiesta di vjt (#sbiffo, 2026-10-30, idea di bitflesh), issue #7.

Stessa forma di cah.py: tail del bot.log, match dei PRIVMSG, risposte sulla
FIFO del bot, l'idraulica in ircgame.py. `bot.py` NON si tocca.

Le battute sono in duello_insulti.json: testi italiani originali di
The Secret of Monkey Island (LucasArts), su ordine di vjt (#sbiffo
2026-10-30) — sostituiscono le coppie sintetiche del primo branch.

  !duello <nick>   sfida <nick>; lui ha ACCEPT_S secondi per !accetto o !rifiuto
                   (lo sfidante ritira la sfida con !rifiuto)
  !duello          una riga d'aiuto, o lo stato del duello in corso
  !i N             chi attacca tira l'insulto N del suo repertorio
  !r N             chi difende para con la risposta N del suo repertorio
                   (si gioca in canale: le opzioni numerate le stampa li' il bot,
                   ordine di vjt #sbiffo 2026-10-30 — in query non le leggeva nessuno)
  !repertorio      in query: cosa conosci (in canale: risposta in query)
  !classifica [duello]   duelli vinti nel canale

Regole (issue #7):
  * Si gioca a scambi: chi attacca sceglie un insulto, chi difende una
    risposta. Giusta: lo scambio e' del difensore, che passa all'attacco.
    Sbagliata, o niente entro TURN_S: lo scambio e' dell'attaccante, che
    continua. Vince chi arriva a WIN scambi.
  * Aggiunta mia, dichiarata: l'attaccante che non attacca entro TURN_S
    perde lo scambio, e l'attacco passa all'altro. Senza, un duello con
    l'attaccante sparito non finirebbe mai.
  * Si impara duellando: chi subisce un insulto lo impara; una risposta
    giusta giocata la imparano tutti e due. Un esordiente parte con START_N
    insulti e START_N risposte a caso (non per forza della stessa coppia).
  * Chi difende vede tutte le risposte che conosce, comprese quelle che non
    c'entrano: gli insulti imparati senza risposta sono esche.

Presenza (come cah e tavolo): PART, KICK e QUIT di un duellante = abbandono,
il duello va all'altro e conta come vinto. A sfida non ancora accettata, la
sfida decade e basta. Netsplit: niente abbandono, corre il timeout del turno.
Cambio nick: il duello continua col nick nuovo, e il repertorio resta quello
del nick con cui si e' cominciato. Al timeout di un turno un ISON: su +u i
QUIT non arrivano, chi e' offline ha abbandonato.

Su disco (stato per rete, dentro per canale): il repertorio di ogni nick e i
duelli vinti. Il duello in corso sta in memoria: un riavvio lo perde.

CLI:
  duello.py --net azzurra daemon   il daemon
  duello.py insulti                le coppie, contate e controllate
Env (formato di SILLABAH_NETS):
  DUELLO_HOME  DUELLO_INSULTI  DUELLO_NICK
  DUELLO_NETS=nome|log|fifo|#chan1,#chan2
  IRCGAME_LOCKS=/var/run/convento/ircgame
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
REPO = Path(os.environ.get("DUELLO_HOME", HERE))
INSULTI = Path(os.environ.get("DUELLO_INSULTI", HERE / "duello_insulti.json"))
BOT_NICK = os.environ.get("DUELLO_NICK", "MikeBongiorno")

ACCEPT_S = 60        # per !accetto o !rifiuto
TURN_S = 60          # per tirare l'insulto, e per parare
WIN = 3              # scambi per vincere il duello
START_N = 3          # insulti e risposte di un esordiente
MIN_COPPIE = 20
TICK = 5
HELP = ("⚔️ Duello a insulti: !duello <nick> sfida, !accetto o !rifiuto · in gioco "
        "!i N insulta, !r N para, tutto in canale · !repertorio in query · !classifica duello")

CMD_PAT = re.compile(r'^!\s*duello(?:\s+(?P<arg>\S+))?\s*$', re.I)
ACCETTO_PAT = re.compile(r'^!\s*accetto\s*$', re.I)
RIFIUTO_PAT = re.compile(r'^!\s*rifiuto\s*$', re.I)
PLAY_PAT = re.compile(r'^!\s*(?P<verb>[ir])\s+(?P<n>\d+)\s*$', re.I)
REPERTORIO_PAT = re.compile(r'^!\s*repertorio\s*$', re.I)
# Stesso schema degli altri giochi: nudo rispondono tutti, con l'argomento
# solo quello nominato.
CLASSIFICA_PAT = re.compile(r'^!\s*classifica(?:\s+(?P<game>\S+))?\s*$', re.I)
MY_GAME = ("duello", "duelli", "insulti")


def load_nets():
    return ircgame.load_nets(
        "DUELLO_NETS", {"azzurra": {"log": REPO / "bot.log", "fifo": REPO / "bot.send",
                                    "chans": {"#sbiffo"},
                                    "state": REPO / "duello_state.json"}},
        "duello_state", REPO)


def load_coppie(path=None):
    """Le coppie del json, [{"id", "insulto", "risposta"}]. Un id doppio o una
    coppia monca fermano il sidecar: i repertori su disco stanno per id."""
    d = json.loads(Path(path or INSULTI).read_text(encoding="utf-8"))
    out, seen = [], set()
    for c in d["coppie"]:
        if not (c.get("id") and c.get("insulto") and c.get("risposta")):
            raise SystemExit(f"duello: coppia monca {c!r}")
        if c["id"] in seen:
            raise SystemExit(f"duello: id doppio {c['id']!r}")
        seen.add(c["id"])
        out.append({k: c[k] for k in ("id", "insulto", "risposta")})
    return out


class Duello:
    def __init__(self, net, log, fifo, state_path, chans, coppie, rng=None, locks=None):
        self.net = net
        self.log = str(log)
        self.state_path = Path(state_path)
        self.chans = {c.lower() for c in chans}
        if len(coppie) < START_N:
            raise SystemExit(f"duello: servono almeno {START_N} coppie, ce ne sono {len(coppie)}")
        self.coppie = {c["id"]: c for c in coppie}
        self.ids = [c["id"] for c in coppie]
        self.rng = rng or random.Random()
        self.state = self.load()
        self.dirty = False
        self.games = {}                 # in memoria: canale -> duello
        self.tracker = Tracker()
        self.lock = ircgame.ChannelLock(net, "Duello", locks)
        self.out = Outbox(fifo, "duello", self.tracker)
        self.ison = deque()             # ISON in volo: {nick casefold: canale}

    @property
    def sent(self):
        """Per le prove: tutto quello che sarebbe uscito, freno a parte."""
        self.out.flush(force=True)
        return self.out.sent

    # --- disco ---------------------------------------------------------------

    def load(self):
        d = ircgame.load_json(self.state_path, {})
        return d if isinstance(d.get("canali"), dict) else {"canali": {}}

    def save(self):
        ircgame.save_json(self.state_path, self.state)
        self.dirty = False

    def scope(self, chan):
        return self.state["canali"].setdefault(chan, {"repertori": {}, "vinti": {}})

    def repertorio(self, chan, rkey, nick):
        """Il repertorio di `rkey` nel canale; a un esordiente START_N insulti
        e START_N risposte a caso."""
        reps = self.scope(chan)["repertori"]
        r = reps.get(rkey)
        if r is None:
            r = reps[rkey] = {"nick": nick,
                              "insulti": self.rng.sample(self.ids, START_N),
                              "risposte": self.rng.sample(self.ids, START_N)}
            self.dirty = True
        # gli id spariti dal json non si mostrano piu'
        r["insulti"] = [i for i in r["insulti"] if i in self.coppie]
        r["risposte"] = [i for i in r["risposte"] if i in self.coppie]
        return r

    def impara(self, chan, rkey, kind, cid):
        r = self.scope(chan)["repertori"][rkey]
        field = "insulto" if kind == "insulti" else "risposta"
        if self.coppie[cid][field] not in {self.coppie[c][field] for c in r[kind]
                                           if c in self.coppie}:
            r[kind].append(cid)
            self.dirty = True

    def classifica(self, chan, n=10):
        rows = sorted(self.scope(chan)["vinti"].values(),
                      key=lambda r: (-r["vinti"], r["nick"].casefold()))
        return " · ".join(f"{r['nick']} {r['vinti']}" for r in rows[:n])

    # --- uscita --------------------------------------------------------------

    def say(self, target, text):
        self.out.say(target, text)

    def emit(self, target, parts):
        for line in joined_lines(parts):
            self.say(target, line)

    # --- duello --------------------------------------------------------------

    def player_game(self, key):
        for chan, g in self.games.items():
            if key in (g["a"], g["b"]):
                return chan, g
        return None, None

    def other(self, g, key):
        return g["b"] if key == g["a"] else g["a"]

    def rep(self, g, key):
        return self.scope(g["chan"])["repertori"][g["rep"][key]]

    def punteggio(self, g):
        return " – ".join(f"{g['nicks'][k]} {g['score'][k]}" for k in (g["a"], g["b"]))

    def send_attack(self, g):
        key = g["att"]
        ins = self.rep(g, key)["insulti"]
        self.emit(g["chan"],
                  [f"⚔️ \x02{g['nicks'][key]}\x02 attacca ({self.punteggio(g)}), {TURN_S}s — !i N:"]
                  + [f"{i}) {self.coppie[c]['insulto']}" for i, c in enumerate(ins, 1)])

    def send_defense(self, g):
        key = g["def"]
        ris = self.rep(g, key)["risposte"]
        self.emit(g["chan"],
                  [f"🛡️ \x02{g['nicks'][key]}\x02 para con !r N, {TURN_S}s:"]
                  + [f"{i}) {self.coppie[c]['risposta']}" for i, c in enumerate(ris, 1)])

    def sfida(self, chan, nick, target, now):
        key, tkey = nick.casefold(), target.casefold()
        g = self.games.get(chan)
        if g:
            self.say(chan, f"{nick}: c'è già un duello qui, {g['nicks'][g['a']]} "
                           f"contro {g['nicks'][g['b']]}.")
            return
        if tkey == key:
            self.say(chan, f"{nick}: contro te stesso perdi comunque.")
            return
        if tkey == BOT_NICK.casefold():
            self.say(chan, f"{nick}: io presento, non duello.")
            return
        here = self.tracker.members.get(chan)
        if here is not None and tkey not in here:
            self.say(chan, f"{nick}: {target} non è nel canale.")
            return
        for k, n in ((key, nick), (tkey, target)):
            if self.player_game(k)[0]:
                self.say(chan, f"{nick}: {n} sta già duellando.")
                return
        no = ircgame.start_refusal(self.lock, self.tracker, chan, now)
        if no:
            self.say(chan, no)
            return
        self.games[chan] = {
            "phase": "sfida", "chan": chan, "a": key, "b": tkey,
            "nicks": {key: nick, tkey: target}, "rep": {key: key, tkey: tkey},
            "score": {key: 0, tkey: 0}, "att": key, "def": tkey, "insulto": None,
            "deadline": now + ACCEPT_S,
        }
        self.say(chan, f"⚔️ {nick} sfida {target} a duello di insulti! {target}: "
                       f"!accetto o !rifiuto entro {ACCEPT_S}s.")

    def accetta(self, chan, g, now):
        for k in (g["a"], g["b"]):
            self.repertorio(chan, g["rep"][k], g["nicks"][k])
        g["phase"] = "attacco"
        g["deadline"] = now + TURN_S
        self.say(chan, f"⚔️ {g['nicks'][g['b']]} accetta! Vince chi arriva a {WIN} scambi. "
                       f"Attacca {g['nicks'][g['att']]}.")
        self.send_attack(g)

    def insulta(self, chan, g, key, n, now):
        if g["phase"] != "attacco" or key != g["att"]:
            return
        ins = self.rep(g, key)["insulti"]
        if not 1 <= n <= len(ins):
            self.say(chan, f"{g['nicks'][key]}: un numero fra 1 e {len(ins)}: !i N.")
            return
        cid = ins[n - 1]
        g["insulto"] = cid
        g["phase"] = "difesa"
        g["deadline"] = now + TURN_S
        self.impara(chan, g["rep"][g["def"]], "insulti", cid)
        self.say(chan, f"🗡️ \x02{g['nicks'][key]}\x02: «{self.coppie[cid]['insulto']}»")
        self.send_defense(g)

    def para(self, chan, g, key, n, now):
        if g["phase"] != "difesa" or key != g["def"]:
            return
        ris = self.rep(g, key)["risposte"]
        if not 1 <= n <= len(ris):
            self.say(chan, f"{g['nicks'][key]}: un numero fra 1 e {len(ris)}: !r N.")
            return
        cid = ris[n - 1]
        lead = [f"🛡️ \x02{g['nicks'][key]}\x02: «{self.coppie[cid]['risposta']}»"]
        # Si confronta il testo, non l'id: nel copione MI le coppie di Carla
        # riusano le risposte delle altre, e la stessa battuta para entrambe.
        giusta = g["insulto"]
        if self.coppie[cid]["risposta"] == self.coppie[giusta]["risposta"]:
            for k in (g["a"], g["b"]):
                self.impara(chan, g["rep"][k], "risposte", giusta)
            self.scambio(chan, g, key, now, lead + [
                f"✅ Parata! Lo scambio è di \x02{g['nicks'][key]}\x02, che passa all'attacco."])
        else:
            att = g["att"]
            self.scambio(chan, g, att, now, lead + [
                f"❌ Non c'entra niente: lo scambio è di \x02{g['nicks'][att]}\x02."])

    def scambio(self, chan, g, winner, now, lead):
        """Lo scambio a `winner`, che (vinto o no il duello) attacca il prossimo."""
        g["score"][winner] += 1
        if g["score"][winner] >= WIN:
            self.vittoria(chan, g, winner, lead)
            return
        g["att"], g["def"] = winner, self.other(g, winner)
        g["insulto"] = None
        g["phase"] = "attacco"
        g["deadline"] = now + TURN_S
        self.emit(chan, lead + [f"({self.punteggio(g)})"])
        self.send_attack(g)

    def vittoria(self, chan, g, winner, lead=()):
        nick = g["nicks"][winner]
        rkey = g["rep"][winner]
        row = self.scope(chan)["vinti"].setdefault(rkey, {"nick": nick, "vinti": 0})
        row["nick"] = nick
        row["vinti"] += 1
        self.dirty = True
        self.games.pop(chan, None)
        self.emit(chan, list(lead) + [f"🏆 \x02{nick}\x02 vince il duello! "
                                      f"({self.punteggio(g)})"])

    def abbandono(self, chan, g, key, why=""):
        """A sfida aperta la sfida decade; a duello vivo vince l'altro."""
        nick = g["nicks"].get(key, key)
        if g["phase"] == "sfida":
            self.games.pop(chan, None)
            self.say(chan, f"⚔️ {nick} se ne va{why}: sfida annullata.")
            return
        self.vittoria(chan, g, self.other(g, key), [f"🏳️ {nick} abbandona{why}."])

    def stato(self, chan, g, now):
        left = max(0, int(g["deadline"] - now))
        if g["phase"] == "sfida":
            self.say(chan, f"⚔️ {g['nicks'][g['a']]} ha sfidato {g['nicks'][g['b']]}, "
                           f"che ha ancora {left}s per !accetto.")
        else:
            who = g["att"] if g["phase"] == "attacco" else g["def"]
            verb = "attaccare" if g["phase"] == "attacco" else "parare"
            self.say(chan, f"⚔️ {self.punteggio(g)} — tocca a {g['nicks'][who]} "
                           f"{verb}, {left}s.")

    def mostra_repertorio(self, nick):
        key = nick.casefold()
        parts = []
        for chan, sc in sorted(self.state["canali"].items()):
            r = sc["repertori"].get(key)
            if not r:
                continue
            parts += [f"📜 {chan}, insulti ({len(r['insulti'])}):"]
            parts += [f"{i}) {self.coppie[c]['insulto']}"
                      for i, c in enumerate((c for c in r["insulti"] if c in self.coppie), 1)]
            parts += [f"📜 {chan}, risposte ({len(r['risposte'])}):"]
            parts += [f"{i}) {self.coppie[c]['risposta']}"
                      for i, c in enumerate((c for c in r["risposte"] if c in self.coppie), 1)]
        if not parts:
            self.say(nick, "Non conosci ancora niente: sfida qualcuno con !duello <nick> "
                           "in canale, si parte con tre insulti e tre risposte.")
            return
        self.emit(nick, parts)

    # --- ingresso ------------------------------------------------------------

    def handle(self, nick, target, text, now=None, host="", notice=False):
        """Le NOTICE non contano e non hanno risposta (RFC 1459)."""
        if notice:
            return
        now = float(now if now is not None else time.time())
        text = text.strip()
        key = nick.casefold()
        m = PLAY_PAT.match(text)
        if not is_chan(target):
            if REPERTORIO_PAT.match(text):
                self.mostra_repertorio(nick)
            return
        chan = target.lower()
        if chan not in self.chans:
            return
        g = self.games.get(chan)
        if m:
            if g:
                self.gioca(chan, g, key, m, now)
            return
        if REPERTORIO_PAT.match(text):
            self.mostra_repertorio(nick)
            return
        k = CLASSIFICA_PAT.match(text)
        if k:
            game = (k.group("game") or "").lower()
            if not game or game in MY_GAME:
                self.say(chan, f"🏆 [Duello] Duelli vinti ({chan}): "
                               f"{self.classifica(chan) or 'nessuno'}")
            return
        if ACCETTO_PAT.match(text):
            if g and g["phase"] == "sfida" and key == g["b"]:
                self.accetta(chan, g, now)
            return
        if RIFIUTO_PAT.match(text):
            if g and g["phase"] == "sfida" and key in (g["a"], g["b"]):
                self.games.pop(chan, None)
                if key == g["b"]:
                    self.say(chan, f"⚔️ {nick} rifiuta il duello. Prudente.")
                else:
                    self.say(chan, f"⚔️ {nick} ritira la sfida.")
            return
        m = CMD_PAT.match(text)
        if not m:
            return
        arg = m.group("arg")
        if arg:
            self.sfida(chan, nick, arg, now)
        elif g:
            self.stato(chan, g, now)
        else:
            self.say(chan, HELP)

    def gioca(self, chan, g, key, m, now):
        n = int(m.group("n"))
        if m.group("verb").lower() == "i":
            self.insulta(chan, g, key, n, now)
        else:
            self.para(chan, g, key, n, now)

    # --- chi se ne va ----------------------------------------------------------

    def track(self, line, now=None):
        """Gli eventi di presenza dal Tracker, applicati ai duelli."""
        ev = self.tracker.feed(line)
        if not ev:
            return
        kind = ev[0]
        if kind == "404":
            # Prima si riprova senza colori; se non c'era niente da ripulire il
            # rifiuto e' +m/+M/ban: il duello si chiude, in canale non si puo' dire.
            if not self.out.on_404(ev[1], now) and self.games.pop(ev[1], None):
                print(f"duello: {ev[1]} rifiuta le righe (404), duello chiuso", flush=True)
        elif kind == "ison":
            asked = self.ison.popleft() if self.ison else {}
            for k, chan in asked.items():
                g = self.games.get(chan)
                if k not in ev[1] and g and k in (g["a"], g["b"]):
                    self.abbandono(chan, g, k, ", non è più su IRC")
        elif kind == "join":
            if ev[2].casefold() == BOT_NICK.casefold() and ev[1] in self.chans:
                self.out.raw(f"MODE {ev[1]}")       # il 324 dice se e' +c
        elif kind == "nick":
            self.rename(ev[1].casefold(), ev[2])
        elif kind == "quit":
            # Netsplit (ev[3]): non e' abbandono, corre il timeout del turno.
            chan, g = self.player_game(ev[2].casefold())
            if g and not ev[3]:
                self.abbandono(chan, g, ev[2].casefold())
        elif kind in ("part", "kick"):
            g = self.games.get(ev[1])
            if g and ev[2].casefold() in (g["a"], g["b"]):
                self.abbandono(ev[1], g, ev[2].casefold())

    def rename(self, old, new_nick):
        """Il duello passa al nick nuovo; il repertorio resta quello di prima."""
        new = new_nick.casefold()
        _, g = self.player_game(old)
        if not g or new == old:
            if g:
                g["nicks"][old] = new_nick
            return
        for f in ("a", "b", "att", "def"):
            if g[f] == old:
                g[f] = new
        for d in (g["nicks"], g["rep"], g["score"]):
            d[new] = d.pop(old)
        g["nicks"][new] = new_nick

    # --- tempo -----------------------------------------------------------------

    def tick(self, now=None):
        now = float(now if now is not None else time.time())
        for chan, g in list(self.games.items()):
            if now < g["deadline"]:
                continue
            if g["phase"] == "sfida":
                self.games.pop(chan, None)
                self.say(chan, f"⚔️ {g['nicks'][g['b']]} non risponde: sfida scaduta.")
                continue
            if g["phase"] == "attacco":
                late, winner = g["att"], g["def"]
                lead = [f"⏰ {g['nicks'][late]} non attacca: lo scambio è di "
                        f"\x02{g['nicks'][winner]}\x02, che passa all'attacco."]
            else:
                late, winner = g["def"], g["att"]
                lead = [f"⏰ {g['nicks'][late]} non para: lo scambio è di "
                        f"\x02{g['nicks'][winner]}\x02."]
            # Su +u i QUIT non arrivano: chi tace forse e' gia' offline.
            self.ison.append({late: chan})
            self.out.raw(f"ISON {g['nicks'][late]}")
            self.scambio(chan, g, winner, now, lead)
        self.lock.sync(self.games)      # qualunque strada abbia chiuso, si molla

    def run(self):
        p = subprocess.Popen(["tail", "-F", "-n", "0", self.log],
                             stdout=subprocess.PIPE, text=True, errors="replace")
        time.sleep(1)
        for chan in sorted(self.chans):     # la fotografia di presenti e modi
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
    if cmd == "insulti":
        coppie = load_coppie()
        print(f"{len(coppie)} coppie (minimo {MIN_COPPIE})")
        for c in coppie:
            print(f"{c['id']}: {c['insulto']} -> {c['risposta']}")
        return
    nets = load_nets()
    if net not in nets:
        raise SystemExit(f"duello: rete sconosciuta {net!r} (usa: {', '.join(nets)})")
    cfg = nets[net]
    Duello(net, cfg["log"], cfg["fifo"], cfg["state"], cfg["chans"], load_coppie()).run()


if __name__ == "__main__":
    main()
