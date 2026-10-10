#!/usr/bin/env python3
"""love — the TOOLS `!love` command, behind Ambrogio.

vjt order (2026-10-07): "aggiungi il comando !love ad ambrogio, così come
funziona su TOOLS su ircnet, usa quelle frasi lì ma generane anche di altre".

What TOOLS does, measured on vjt's grappa scrollback of IRCnet (#cybernet,
#gosub — 175 invocations, 173 TOOLS lines in the 15 s after them):

  * `!love <nick>` -> ONE line in channel, a phrase drawn at random from a
    fixed pool, with the caller and the target substituted in. Uniform draw,
    repeats included (the same "sogna ... ogni notte" came out three times in
    a row for the same pair, days apart).
  * The target is the FIRST word after the command, verbatim, whatever it is:
    `!love inverno nucleare` loved `inverno`, `!love nano nudo` loved `nano`.
    It does not have to be a nick in the channel (`tutto_il_canale`, `TOOLS`
    itself, `amore_saffico_tra_...` all went through), and loving yourself
    works (`peluche` -> `peluche`).
  * Bare `!love` gets nothing back.
  * The command is case-insensitive (`!LOVE katia` answered).
  * Some phrases name only the target ("°o.O {to} sei il mio tesoro"), some
    flip the order ("♥ {to} sei il mio dolce sogno {from} ♥"). Templates
    below keep each one exactly as TOOLS printed it.

Two things on top, both cheap: a short per-channel cooldown against floods,
and no immediate repeat of the phrase just said in that channel (TOOLS did
repeat — that was the part nobody would miss).

`!slap` and `!pernacchio` (vjt, #sbiffo 2026-10-10 12:44, on katia's ask:
"tipo !love però pernacchio") are the same command with another pool: same
target rule, same cooldown, same no-repeat. One daemon serves all three.

Shape is the house sidecar shape (yt_preview.py, next_counter.py): tail
bot.log, match, write verbs into the bot.send FIFO, non-blocking, so a dead
bot costs a skipped line and not a wedged daemon. Stateless: nothing on disk.

Usage:
  love.py                        # daemon (tail bot.log)
  love.py --once FROM TO [CMD]   # print one line (CMD default love), send nothing

Env:
  LOVE_LOG       bot.log to tail       (default: the vjt-claude repo one)
  LOVE_FIFO      bot.send to write     ('' disables talking — dry runs mute)
  LOVE_CHANS     comma separated list  (default: #sniffo,#sbiffo)
  LOVE_COOLDOWN  seconds between two lines in the same channel (default 2)
"""

import os
import random
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path("/home/vjt/code/IRC/vjt-claude")
LOG = os.environ.get("LOVE_LOG", str(ROOT / "bot.log"))
FIFO = os.environ.get("LOVE_FIFO", str(ROOT / "bot.send"))
CHANNELS = {c.strip().lower() for c in os.environ.get(
    "LOVE_CHANS", "#sniffo,#sbiffo").split(",") if c.strip()}
# TOOLS answered three `!love` 3 s apart, so the cooldown has to stay under
# that or it stops being the same command. It exists for paste floods.
COOLDOWN_SEC = float(os.environ.get("LOVE_COOLDOWN", "2"))

# A target is echoed back into the channel: cap it so a 400-byte "nick" does
# not wrap the line into two.
TARGET_MAX = 64

# The pool TOOLS draws from, every distinct template it printed in the
# scrollback, verbatim. `{from}` = who typed the command, `{to}` = the target.
TOOLS_PHRASES = [
    "╰☆╮ {from} ti manda la luna e le stelle {to} ╰☆╮",
    "♡ {from} ti pensa 24h su 24 {to} ♡",
    "~*:.｡o○ {from} sogna {to} ogni notte ○o｡.:*~ ♥",
    "((((( {from} ti abbraccia forte {to} ))))) ⊂(◕‿◕)⊃",
    "♪ {from} canta sottovoce: ti voglio bene {to} ♪ ღღღ",
    "♡♡♡ {from} pensa a {to} 24/7 ♡♡♡ <333",
    "°˖✧ {from} ti manda un arcobaleno d’amore {to} ✧˖°",
    "♥ {to} sei il mio dolce sogno {from} ♥",
    "*-.,_,.-* {from} ti dedica baci infiniti {to} *-,_,.-* :* :* :*",
    "~<3~ {from} ti bacia ovunque {to} ~<3~ :*:*:*",
    "*:・ﾟ✧ {from} ti coccola {to} ✧ﾟ・:*",
    "♥ {from} x {to} = amore eterno forever ♥ <3<3<3",
    "(h) {from} ti tiene per mano {to} (h)",
    "°¤.¸¸.•´¯`» {from} ama tanto {to} «`¯´•.¸¸.¤°",
    "*:・ﾟ✧ {from} ti coccola piano piano {to} ✧ﾟ・:* (｡♥‿♥｡)",
    "<3<3<3 {to} = la persona più speciale per {from} <3<3<3",
    "♥♥♥♥♥ {to} fai sciogliere {from} ♥♥♥♥♥",
    "✿ {to} è il mio angioletto {from} ✿",
    "✿ღ {from} bacia sul nasino {to} ღ✿",
    "((¯`v´¯)) {from} abbraccia forte forte {to} ((¯`v´¯))",
    "^_^ {from} sorride solo pensando a te {to} ^_^ <333",
    "°¤.¸¸.•´¯`» {from} ti ama alla follia {to} «`¯´•.¸¸.¤°",
    "♪ღ♪ {from} sussurra: sei speciale {to} ♪ღ♪",
    "~<3~ {from} + {to} = amore eterno ~<3~",
    "^_^ {from} sorride solo per {to} ^_^",
    "☆ {from} ti regala la luna {to} ☆",
    "°o.O {from} ti bacia dolcemente {to} O.o°",
    "((¯`v´¯)) {from} ti stringe forte e non ti molla {to} ((¯`v´¯))",
    "☆ {from} ti regala un arcobaleno di baci {to} ☆ ♡♡♡",
    "✿ღ {from} ti bacia sul nasino gelido {to} ღ✿ :*",
    "{from} ti manda un diluvio di cuoricini {to} <333333333333 <3 <3 <3",
    "*-.,_,.-* {from} ti dedica un bacio volante {to} *-,_,.-*",
    "♥♥♥♥♥ {to} fai sciogliere il cuore di {from} ♥♥♥♥♥",
    "♥ {from} x {to} forever & ever ♥",
    "♪ {from} canta per {to}: ti voglio beneeee ♪",
    "♪ღ♪ {from} sussurra: ti amo tanto {to} ♪ღ♪",
    "♥ {to} sei il mio dolce pensiero {from} ♥ ^_^ ♥",
    "~*:.｡o○ {to} fai battere il cuoricino di {from} ○o｡.:*~",
    "°˖✧ {from} ti copre di coccole {to} ✧˖° (づ｡◕‿‿◕｡)づ",
    "{from} ti manda un mare di cuoricini {to} <333333333333333",
    "°o.O {to} sei il mio tesoro O.o° ♥♥♥",
]

# New ones, same frames and same sugar, with the IRC of the house mixed in.
NEW_PHRASES = [
    "@>-->-- {from} ti da' l'op sul suo cuore {to} --<--<@",
    "♥ {from} ha messo {to} in autojoin, per sempre ♥",
    "(h) {from} ti ha messo nella notify list del cuore {to} (h)",
    "✿ {from} ha fatto /ignore a tutto il canale tranne che a {to} ✿",
    "*:・ﾟ✧ {from} ti fa DCC SEND di un file di baci da 4 giga {to} ✧ﾟ・:*",
    "°o.O netsplit o no, {from} resta sempre sul server di {to} O.o°",
    "♡♡♡ {to} sei l'unica query che {from} non chiude mai ♡♡♡",
    "~<3~ {from} ti da' il +v perenne {to}, cosi' parli anche nel suo +m ~<3~",
    "╰☆╮ {from} ha registrato {to} su NickServ col suo cuore come password ╰☆╮",
    "☆ {from} e {to}: ping? pong! ping? pong! per sempre ☆ ♡♡♡",
    "((((( {from} non va mai in ping timeout quando ci sei tu {to} ))))) ⊂(◕‿◕)⊃",
    "♪ {from} ti dedica il motd del suo cuore {to} ♪ ღღღ",
    "°˖✧ {from} ha cambiato il topic in: {to} <3 ✧˖°",
    "^_^ {from} fa /away solo quando pensa a {to} ^_^ <333",
    "♥♥♥♥♥ {to} hai fatto andare in flood il cuore di {from} ♥♥♥♥♥",
    "*-.,_,.-* {from} ti manda un CTCP BACIO {to} *-,_,.-* :* :* :*",
    "°¤.¸¸.•´¯`» {from} ti mette il +b a tutti i pretendenti {to} «`¯´•.¸¸.¤°",
    "✿ღ {from} ti cede il suo founder sul canale del cuore {to} ღ✿",
    "~*:.｡o○ {from} sogna di fare /join #{to} ogni notte ○o｡.:*~ ♥",
    "♡ {from} ha scritto {to} nel cuore con i colori mIRC ♡",
    "(h) {from} ti tiene per mano anche durante i netsplit {to} (h)",
    "♥ {to} sei il mio vhost preferito {from} ♥",
    "<3<3<3 {to} = l'unico ChanServ che {from} ascolta <3<3<3",
    "((¯`v´¯)) {from} ti fa /whois ogni cinque minuti, giusto per sapere come stai {to} ((¯`v´¯))",
    "°o.O {from} ti scrive in query alle 3 di notte {to} O.o°",
    "*:・ﾟ✧ {from} ti mette in bot.trust senza nemmeno chiedere {to} ✧ﾟ・:* (｡♥‿♥｡)",
    "☆ {from} ti offre un caffe' corretto sambuca {to} ☆ ♡♡♡",
    "~<3~ {from} ti porta la grappa a letto {to} ~<3~ :*:*:*",
    "♪ღ♪ {from} sussurra: senza di te sono un canale vuoto {to} ♪ღ♪",
    "╰☆╮ {from} ti regala un vhost tutto tuo {to} ╰☆╮",
    "♥ {from} x {to} = amore eterno, anche dopo il /quit ♥ <3<3<3",
    "°˖✧ {from} ti ha fatto il backup nel cuore, RAID 1 {to} ✧˖°",
    "{from} ti manda un flood di cuoricini {to} <3<3<3<3<3<3<3<3<3<3 (excess flood)",
    "✿ {from} ha messo {to} come ultima riga del suo bot.startup ✿",
    "(｡♥‿♥｡) {from} si e' scaricato {to} via XDCC, pack #1 (｡♥‿♥｡)",
    "°¤.¸¸.•´¯`» {from} rifarebbe tutto dal 1997 solo per conoscere {to} «`¯´•.¸¸.¤°",
    "♡♡♡ {from} legge il backlog solo per vedere cosa ha scritto {to} ♡♡♡ <333",
    "~*:.｡o○ {to} sei il kline che {from} non vuole mai togliere ○o｡.:*~",
    "^_^ {from} ti ha scritto un bot solo per dirti <3 {to} ^_^",
    "((((( {from} ti abbraccia piu' forte di un /kill {to} ))))) ⊂(◕‿◕)⊃",
    "☆ {from} ti dedica una supercazzola d'amore con scappellamento a destra {to} ☆",
    "♥♥♥♥♥ {to} fai crashare il cuore di {from}, core dumped ♥♥♥♥♥",
    "*-.,_,.-* {from} ti manda un bacio in base64 {to}: PDM= *-,_,.-*",
    "°o.O {from} ti vuole bene come irssi vuole bene a screen {to} O.o°",
    "✿ღ {from} ti mette +o, +v e +h, cosi' non ti manca niente {to} ღ✿",
]

PHRASES = TOOLS_PHRASES + NEW_PHRASES

SLAP_PHRASES = [
    "/me slaps {to} around a bit with a large trout, on behalf of {from}",
    "/me prende una trota da tre chili e la stampa in faccia a {to}, da parte di {from}",
    "/me (╯°□°)╯ tira un ceffone a {to} che fa netsplit da solo, da parte di {from}",
    "/me schiaffeggia {to} con un baccala' sotto sale, stagionato 1997, da parte di {from}",
    "/me rifila a {to} uno schiaffo a cinque dita, una per ogni ping timeout, da parte di {from}",
    "/me prende a sberle {to} con la tastiera, layout IT, tasto Invio compreso, da parte di {from}",
    "/me colpisce {to} con un /kill a mano aperta, da parte di {from}",
    "/me schiaffeggia {to} col manuale di bahamut, edizione rilegata, da parte di {from}",
    "/me tira a {to} uno sganassone che gli fa cambiare nick, da parte di {from}",
    "/me schiaffeggia {to} con un modem 56k, ancora attaccato al doppino, da parte di {from}",
    "/me da' a {to} un ceffone in base64: Q0VGRk9ORQ==, da parte di {from}",
    "/me prende un floppy da 1.44 e lo sbatte in fronte a {to}, da parte di {from}",
    "/me schiaffeggia {to} con un cavo seriale null-modem, da parte di {from}",
    "/me prende {to} a sberle a ritmo di excess flood, da parte di {from}",
    "/me schiaffeggia {to} con una trota del Brenta, che fa piu' male, da parte di {from}",
    "/me tira a {to} un manrovescio che neanche un K-line, da parte di {from}",
    "/me prende {to} a schiaffi col motd di Azzurra stampato in A3, da parte di {from}",
    "/me schiaffeggia {to} con un 'porco dio' a mano aperta, da parte di {from}",
    "/me assesta a {to} uno schiaffo con rimbalzo, roba da ChanServ, da parte di {from}",
    "/me sventola una sardina sotto il naso di {to} e poi gliela tira, da parte di {from}",
    "/me schiaffeggia {to} con la riga di comando, opzione -f, da parte di {from}",
    "/me sbatte {to} contro il muro dei banlist, da parte di {from}",
    "/me da' a {to} uno schiaffo cosi' forte che gli si resetta il bouncer, da parte di {from}",
    "/me prende a sberle {to} con una copia di linux&c del 1999, da parte di {from}",
    "/me schiaffeggia {to} con un salmone affumicato, e poi se lo mangia, da parte di {from}",
]

PERNACCHIO_PHRASES = [
    "{from} fa a {to} una pernacchia: PRRRRRRRRRRRRRT",
    "{from} -> {to}: PFFFFFFFRRRRRRRRRRRT 💨",
    "{from} gonfia le guance e spara a {to} una pernacchia da stadio: BRRRRRRRRRRRRRRRRT",
    "{from} dedica a {to} una pernacchia napoletana, di quelle di Eduardo: PPPRRRRRR",
    "{from} fa a {to} una pernacchia in stereo: PRRRT (sx) PRRRT (dx)",
    "{from} manda a {to} un CTCP PERNACCHIO: PRRRRRRRRRRRRT",
    "{from} fa a {to} una pernacchia da 4 giga via DCC SEND: PRRRRRRRR...RRRRT",
    "{from} risponde a {to} con una pernacchia in excess flood: PRT PRT PRT PRT PRT (excess flood)",
    "{from} fa a {to} una pernacchia cosi' lunga che va in ping timeout: PRRRRRRRRRRRRRRRRRRRRRRRRR",
    "{from} si mette la mano sotto l'ascella e fa a {to}: PFRRRT PFRRRT",
    "{from} soffia sul braccio una pernacchia per {to}: PFFFFFRRRRRT",
    "{from} fa una pernacchia a {to} e poi si scusa. No, non si scusa: PRRRRRT",
    "{from} -> {to}: 🎺 PRRRRRRRRRT 🎺",
    "{from} fa a {to} una pernacchia in base64: UFJSUlJSUlJU",
    "{from} saluta {to} alla romana: AAAAH... PRRRRRRRRRRT",
    "{from} fa a {to} una pernacchia con scappellamento a destra: PRRRT come se fosse antani",
    "{from} sfoggia per {to} la pernacchia del Marchese del Grillo: io so' io e tu PRRRRRT",
    "{from} fa a {to} la pernacchia del modem 56k: PSHHHHHHKRRRRRRRRRT",
    "{from} fa a {to} una pernacchia con eco: PRRRT... prrrt... prt...",
    "{from} regala a {to} una pernacchia in +v, cosi' si sente anche nel +m: PRRRRRRRT",
    "{from} si gira verso {to}, chiude gli occhi e... PPPPPRRRRRRRRRRRRRT",
    "{from} fa a {to} una pernacchia in Do maggiore: PRRRT-PRRRT-PRRRRRRRT",
    "{from} sputacchia addosso a {to} una pernacchia bagnata: PFFTHHHHHPPPT",
    "{from} fa a {to} una pernacchia a doppio canale, IPv4 e IPv6: PRRRT::PRRRT",
    "{from} chiude la query con {to} cosi': PRRRRRRRRRRRRRRRRRRRRT",
]

# command -> phrase pool. All three share the target rule and the cooldown.
POOLS = {"love": PHRASES, "slap": SLAP_PHRASES, "pernacchio": PERNACCHIO_PHRASES}
# alias -> command (vjt, #sbiffo 2026-10-10 13:40): people type the feminine.
ALIASES = {"pernacchia": "pernacchio", "prrr": "pernacchio"}

PRIVMSG_PAT = re.compile(
    r'< :(?P<nick>[^!@\s]+)!\S+\s+PRIVMSG\s+(?P<chan>#\S+)\s+:(?P<text>.*?)$')
# `!love` then whitespace or end: `!lovely` is not the command. Only the first
# word is the target, like TOOLS ("!love inverno nucleare" -> inverno).
CMD_PAT = re.compile(
    r'^\s*!(?P<cmd>' + '|'.join([*POOLS, *ALIASES]) + r')(?:\s+(?P<to>\S+).*)?\s*$', re.I)
CTRL = re.compile(r'[\x00-\x1f\x7f]')


def parse(text):
    """`!<cmd> <who> [...]` -> (cmd, who), or None (not a command, or bare)."""
    m = CMD_PAT.match(text)
    if not m or not m.group("to"):
        return None
    to = CTRL.sub("", m.group("to"))[:TARGET_MAX]
    cmd = m.group("cmd").lower()
    return (ALIASES.get(cmd, cmd), to) if to else None


def target_of(text):
    """`!love <who> [...]` -> who, or None (not the command, or bare)."""
    p = parse(text)
    return p[1] if p else None


def render(phrase, frm, to):
    # str.format substitutes values without re-parsing them, so a `{` in a
    # nick is printed as-is.
    # Nicks in bold (vjt, #sbiffo 2026-10-10 13:44).
    return phrase.format(**{"from": f"\x02{frm}\x02", "to": f"\x02{to}\x02"})


def pick(rng, last=None, pool=PHRASES):
    """A random phrase, never the one said last in that channel."""
    while True:
        p = rng.choice(pool)
        if p != last or len(pool) == 1:
            return p


def say(chan, text):
    """One line to the bot FIFO. Non-blocking open: no reader means ENXIO and a
    skipped line, not a daemon wedged forever on a dead bot."""
    if not FIFO:
        return
    try:
        fd = os.open(FIFO, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return
    try:
        # SAY:love — origin tag, log-only; see bot.py process_cmd. A `/me `
        # phrase (the !slap pool) goes out as a CTCP ACTION, like a real slap.
        verb = "ACT" if text.startswith("/me ") else "SAY"
        text = text[4:] if verb == "ACT" else text
        os.write(fd, f"{verb}:love {chan} {text}\n".encode())
    except OSError:
        pass
    finally:
        os.close(fd)


class Love:
    """The per-channel memory: last line time and last phrase. In RAM only —
    a restart forgets it, and nothing about that is worth a file."""

    def __init__(self, rng=None, cooldown=COOLDOWN_SEC, chans=None):
        self.rng = rng or random.Random()
        self.cooldown = cooldown
        self.chans = CHANNELS if chans is None else chans
        self.last_at = {}
        self.last_phrase = {}

    def handle(self, line, now=None):
        """A bot.log line -> (chan, text) to say, or None."""
        m = PRIVMSG_PAT.search(line)
        if not m:
            return None
        chan = m.group("chan")
        if chan.lower() not in self.chans:
            return None
        cmd = parse(m.group("text"))
        if not cmd:
            return None
        cmd, to = cmd
        key = chan.lower()
        now = time.time() if now is None else now
        if now - self.last_at.get(key, float("-inf")) < self.cooldown:
            return None
        phrase = pick(self.rng, self.last_phrase.get(key), POOLS[cmd])
        self.last_at[key] = now
        self.last_phrase[key] = phrase
        return chan, render(phrase, m.group("nick"), to)


def daemon():
    love = Love()
    p = subprocess.Popen(["tail", "-F", "-n", "0", LOG],
                         stdout=subprocess.PIPE, text=True, errors="replace")
    for line in p.stdout:
        out = love.handle(line)
        if out:
            say(*out)


def main():
    argv = sys.argv[1:]
    if argv[:1] == ["--once"] and len(argv) in (3, 4):
        pool = POOLS[argv[3].lower()] if len(argv) == 4 else PHRASES
        print(render(pick(random.Random(), pool=pool), argv[1], argv[2]))
        return 0
    daemon()
    return 0


if __name__ == "__main__":
    sys.exit(main())
