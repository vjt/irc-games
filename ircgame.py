"""ircgame — l'idraulica comune dei giochi da canale.

Estratta da impiccato.py e sillabah.py (stesse righe, dove si poteva) per
cah.py, che e' il primo a usarla. Gli altri giochi NON sono migrati: e' un
passo a parte, dopo la review (Malekith, 2026-10-15).

Cosa c'e':
  load_nets()      il formato `nome|log|fifo|#chan1,#chan2 ; ...` di *_NETS
  PRIVMSG_PAT      una riga `<` del bot.log con PRIVMSG/NOTICE
  joined_lines()   accorpa parti in righe sotto MAX_LINE byte
  load_json/save_json  stato su disco, scrittura atomica
  strip_codes()    via i codici mIRC (grassetto, colori, reverse, ...)
  Tracker          dal bot.log: chi e' op, i modi di canale (+c), e gli
                   eventi di presenza (JOIN/PART/KICK/QUIT/NICK, netsplit)
  Outbox           la coda verso la FIFO: freno anti-flood, e sui canali +c
                   le righe escono senza codici (anche a posteriori, sul 404)
"""
import json
import os
import re
import time
from collections import deque
from pathlib import Path

# 380 e non 400: il margine copre il prefisso `PRIVMSG <chan> :` che il server
# conta dentro i 512 della riga IRC (vedi impiccato.py).
MAX_LINE = 380


def load_nets(env, default, state_base, home):
    """`env` nel formato di SUORE_NETS/IMPICCATO_NETS/SILLABAH_NETS:

        nome|path/bot.log|path/bot.send|#chan1,#chan2 ; nome2|...

    Vuoto: `default`. Lo stato di ogni rete e' `<state_base>.<rete>.json`
    sotto `home`, tranne 'azzurra' che non ha suffisso."""
    raw = os.environ.get(env, "").strip()
    if not raw:
        return default
    nets = {}
    for record in filter(None, (r.strip() for r in raw.split(";"))):
        parts = [p.strip() for p in record.split("|")]
        if len(parts) != 4:
            raise SystemExit(f"{env}: record malformato: {record!r} — serve nome|log|fifo|canali")
        name, log, fifo, chanspec = parts
        chans = {c.strip().lower() for c in chanspec.split(",") if c.strip()}
        if not chans:
            raise SystemExit(f"{env}: la rete {name!r} non ha canali")
        suffix = "" if name == "azzurra" else f".{name}"
        nets[name] = {"log": Path(log), "fifo": Path(fifo), "chans": chans,
                      "state": Path(home) / f"{state_base}{suffix}.json"}
    return nets


# Il target e' un canale o il bot stesso (query). Le righe '>' sono le nostre e
# non combaciano: il pattern vuole '<'.
PRIVMSG_PAT = re.compile(
    r'< :(?P<nick>[^!@\s]+)!(?P<ident>[^@\s]+)@(?P<host>\S+)\s+'
    r'(?P<verb>PRIVMSG|NOTICE)\s+(?P<target>\S+)\s+:(?P<text>.*?)$')
EVENT_PAT = re.compile(r'< :(?P<src>[^!\s]+)(?:!\S+)?\s+(?P<cmd>\S+)\s*(?P<rest>.*?)\s*$')
# Il QUIT di un netsplit ha per motivo i due server: «irc.a.net irc.b.net».
SPLIT_PAT = re.compile(r'^:?[\w-]+(?:\.[\w-]+)+ [\w-]+(?:\.[\w-]+)+$')
# I modi di canale che si mangiano un argomento sia col + sia col - (bahamut).
# `l` lo prende solo col +.
MODE_ARGS = set("ovhbeIkqa")
# \x02 grassetto, \x03 colore (con fg[,bg]), \x0f reset, \x16 reverse,
# \x1d corsivo, \x1f sottolineato.
CODES_PAT = re.compile(r'\x03(?:\d{1,2}(?:,\d{1,2})?)?|[\x02\x0f\x16\x1d\x1f]')


def is_chan(target):
    return target.startswith(("#", "&"))


def strip_codes(text):
    return CODES_PAT.sub("", text)


def joined_lines(parts, limit=MAX_LINE, sep="  ·  "):
    """Le parti nel minor numero di righe sotto `limit` BYTE, tagliando solo
    fra una parte e l'altra (copia di quella dell'impiccato)."""
    out, cur = [], ""
    for p in parts:
        p = (p or "").strip()
        if not p:
            continue
        cand = f"{cur}{sep}{p}" if cur else p
        if cur and len(cand.encode("utf-8")) > limit:
            out.append(cur)
            cur = p
        else:
            cur = cand
    if cur:
        out.append(cur)
    return out


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def save_json(path, data):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def fifo_write(fifo, line):
    """Una riga sulla FIFO del bot. Nessun lettore: si salta, non si accoda."""
    if not fifo:
        return
    try:
        fd = os.open(str(fifo), os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return
    try:
        os.write(fd, line.encode("utf-8"))
    except OSError:
        pass
    finally:
        os.close(fd)


class Tracker:
    """Quello che un client sa dei suoi canali, ricostruito dal bot.log: chi
    c'e', chi e' op o voce, che modi ha il canale. Mai su disco: all'avvio il
    gioco chiede NAMES e MODE, e la fotografia si rifa'.

    feed(line) ritorna l'evento che interessa ai giochi, o None:
      ("join", chan, nick) ("part", chan, nick) ("kick", chan, nick)
      ("quit", None, nick, split) ("nick", old, new) ("404", chan)
      ("names", chan)  — fine di un NAMES, `members` e' fresco
      ("ison", {nick casefold online})  — risposta 303 a un ISON
    I nick negli eventi sono come scritti; nelle tabelle, casefold.

    Il QUIT da solo non basta: su bahamut +u (MODE_NOSPAM) i QUIT non
    arrivano ai non-oper, e nemmeno quelli di chi non puo' parlare (+m senza
    voce, +M non registrato). PART, KICK e NICK arrivano sempre. Chi gioca,
    quando qualcuno tace, chiede un ISON e guarda l'evento "ison"."""

    def __init__(self):
        self.members = {}       # canale casefold -> nick casefold presenti
        self.ops = {}           # canale casefold -> nick casefold con @
        self.voices = {}        # canale casefold -> nick casefold con +
        self.modes = {}         # canale casefold -> set di lettere di modo
        self._names = set()     # canali con un NAMES a meta' (fra 353 e 366)

    def _tables(self):
        return (self.members, self.ops, self.voices)

    def is_op(self, nick, chan):
        return nick.casefold() in self.ops.get(chan.casefold(), set())

    def has_mode(self, chan, mode):
        return mode in self.modes.get(chan.casefold(), set())

    def can_speak(self, chan, nick):
        """+m o +M (solo i registrati parlano) zittiscono chi non e' op o voce.
        Il nick di un bot senza NickServ non e' mai registrato."""
        key = chan.casefold()
        if not (self.has_mode(key, "m") or self.has_mode(key, "M")):
            return True
        n = nick.casefold()
        return n in self.ops.get(key, set()) or n in self.voices.get(key, set())

    def feed(self, line):
        m = EVENT_PAT.search(line)
        if not m:
            return None
        src_nick, cmd, rest = m.group("src"), m.group("cmd").upper(), m.group("rest")
        src = src_nick.casefold()
        parts = rest.split()
        if cmd == "353":
            # `<me> = #chan :@a +b c` — la fotografia arriva a pezzi, e il
            # primo pezzo dopo un 366 la ricomincia da capo.
            mm = re.match(r'\S+\s+[=*@]\s+(\S+)\s+:(.*)$', rest)
            if mm:
                key = mm.group(1).casefold()
                if key not in self._names:
                    self._names.add(key)
                    for t in self._tables():
                        t[key] = set()
                for n in mm.group(2).split():
                    bare = n.lstrip("@%+").casefold()
                    self.members[key].add(bare)
                    if "@" in n[:len(n) - len(bare)]:
                        self.ops[key].add(bare)
                    if "+" in n[:len(n) - len(bare)]:
                        self.voices[key].add(bare)
        elif cmd == "366" and len(parts) >= 2:
            key = parts[1].casefold()
            self._names.discard(key)
            self.members.setdefault(key, set())
            return ("names", parts[1].lower())
        elif cmd == "303" and parts:
            # RPL_ISON `<me> :a b c`: quelli online fra quelli chiesti.
            return ("ison", {n.casefold() for n in rest.split(":", 1)[-1].split()})
        elif cmd == "324" and len(parts) >= 3:
            # RPL_CHANNELMODEIS `<me> #chan +ntc [args]`: la fotografia dei modi.
            self.modes[parts[1].casefold()] = set(parts[2].lstrip("+"))
        elif cmd == "404" and len(parts) >= 2:
            # ERR_CANNOTSENDTOCHAN: su bahamut di solito e' +c e una riga
            # colorata. Si segna +c; il resto lo fa l'Outbox.
            self.modes.setdefault(parts[1].casefold(), set()).add("c")
            return ("404", parts[1].lower())
        elif cmd == "MODE" and len(parts) >= 2 and is_chan(parts[0]):
            key, args, sign = parts[0].casefold(), parts[2:], "+"
            got_modes = self.modes.setdefault(key, set())
            for c in parts[1].lstrip(":"):
                if c in "+-":
                    sign = c
                elif c in MODE_ARGS or (c == "l" and sign == "+"):
                    arg = args.pop(0) if args else ""
                    if c in "ov" and arg:
                        got = (self.ops if c == "o" else self.voices).setdefault(key, set())
                        (got.add if sign == "+" else got.discard)(arg.casefold())
                    elif c in "lk":
                        (got_modes.add if sign == "+" else got_modes.discard)(c)
                else:
                    (got_modes.add if sign == "+" else got_modes.discard)(c)
        elif cmd == "JOIN" and parts:
            chan = parts[0].lstrip(":").lower()
            self.members.setdefault(chan, set()).add(src)
            return ("join", chan, src_nick)
        elif cmd == "NICK" and parts:
            new_nick = parts[0].lstrip(":")
            for t in self._tables():
                for got in t.values():
                    if src in got:
                        got.discard(src)
                        got.add(new_nick.casefold())
            return ("nick", src_nick, new_nick)
        elif cmd == "QUIT":
            for t in self._tables():
                for got in t.values():
                    got.discard(src)
            return ("quit", None, src_nick, bool(SPLIT_PAT.match(rest)))
        elif cmd in ("PART", "KICK") and parts:
            chan = parts[0].lower()
            gone_nick = src_nick if cmd == "PART" else (parts[1] if len(parts) > 1 else "")
            if not gone_nick:
                return None
            for t in self._tables():
                t.get(chan, set()).discard(gone_nick.casefold())
            return (cmd.lower(), chan, gone_nick)
        return None


class Outbox:
    """La coda verso la FIFO, col freno: bot.py non ha pacing in uscita, e una
    raffica di righe (a CaH: una mano in privato per giocatore) e' un Excess
    Flood che butta giu' il bot per tutti. Al piu' `burst` righe ogni
    `burst*gap` secondi.

    Il +c si decide riga per riga NEL MOMENTO in cui la riga esce, dalla
    tabella viva del Tracker: il modo puo' cambiare mentre la riga e' in coda.
    Le righe con codici uscite su un canale restano in `recent` per RESEND_S
    secondi: se il server risponde 404 escono di nuovo, ripulite, una volta.

    fifo vuota = muto (prove): la coda gira uguale, `sent` dice cosa sarebbe
    uscito."""

    RESEND_S = 15

    def __init__(self, fifo, tag, tracker, burst=4, gap=1.2):
        self.fifo = str(fifo) if fifo else ""
        self.tag = tag
        self.tracker = tracker
        self.burst, self.gap = burst, gap
        self.q = deque()
        self.sent = []                  # (target, testo) come e' uscito
        self._ts = deque()
        self.recent = {}                # canale -> deque di (istante, testo)

    def say(self, target, text):
        self.q.append(("SAY", target, text))

    def raw(self, text):
        self.q.append(("RAW", "", text))

    def on_404(self, chan, now=None):
        """Il canale ha rifiutato una nostra riga: le righe con codici degli
        ultimi RESEND_S secondi tornano in coda, e usciranno pulite (il
        Tracker ha gia' segnato +c). Ritorna quante: zero vuol dire che il
        rifiuto non era per i colori (+m, +M, ban) e ritentare non serve."""
        now = time.time() if now is None else now
        n = 0
        for ts, text in self.recent.pop(chan.lower(), ()):
            if now - ts <= self.RESEND_S:
                self.say(chan, text)
                n += 1
        return n

    def flush(self, now=None, force=False):
        """Manda quello che il freno permette (`force`: tutto, per le prove).
        Ritorna fra quanti secondi riprovare, None a coda vuota."""
        now = time.time() if now is None else now
        while self.q:
            while self._ts and now - self._ts[0] > self.burst * self.gap:
                self._ts.popleft()
            if len(self._ts) >= self.burst and not force:
                return self.gap
            self._ts.append(now)
            verb, target, text = self.q.popleft()
            if verb == "RAW":
                fifo_write(self.fifo, f"RAW:{self.tag} {text}\n")
                continue
            if is_chan(target):
                if self.tracker.has_mode(target, "c"):
                    text = strip_codes(text)
                elif text != strip_codes(text):
                    self.recent.setdefault(target.lower(), deque()).append((now, text))
            self.sent.append((target, text))
            fifo_write(self.fifo, f"SAY:{self.tag} {target} {text}\n")
        return None
