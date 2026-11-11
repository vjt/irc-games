#!/usr/bin/env python3
"""guardiani — Ambrogio and MikeBongiorno guard vjt on IRCnet.

vjt's order (2026-11-05 03:30): where the two bots hold ops they op vjt, and
they kick whoever deops or kicks vjt or one of them, with a kick message in
the register of «ao?!».

Identity is the MASK, never the nick: vjt is `vjt!antani@due.dita.di.grappa.chat`
and a nick alone on IRCnet is free for anyone to take. Masks are learned from
every prefixed line in the bot log (JOIN, PRIVMSG, NICK...), so the log is
replayed at startup to know who is who before the first event arrives.

One process, several bots: GUARD_LOG / GUARD_FIFO are comma lists paired by
position, and each bot acts only through its own FIFO on what its own log
shows (its ops, its channels). Both bots seeing the same deop both react;
the second kick finds nobody and the server drops it. Harmless.

Bitch mode (vjt, 2026-11-09, after #disagio was taken over): on the channels
in GUARD_BITCH, an op handed to anyone but a protected mask is taken back. Ops
are transitive on IRCnet: one outsider with @ gives the channel to whoever HE
trusts (tom -> ig0r -> gruG -> a Psotnic botnet that kicked everybody in the
same second). Kicking back after a mass-kick is too late; refusing the first
foreign op is not. Server-granted ops (netsplit riding) are taken back too.
Who handed it out decides the rest (vjt, 2026-11-11): an outsider is deopped
and kicked; a protected mask keeps its own op, and that includes vjt, since
tom handing @ to ig0r is exactly how the channel was lost. To op someone new,
put their mask in GUARD_MASKS. The +R list (IRCnet's reop-when-opless) can
be changed only by a protected mask: anyone else's edit is reverted, since
the first thing a taker does is wipe it.
Ops found already in place when a bot joins are left alone: no mass deop.

Env:
  GUARD_LOG    comma list of bot logs
  GUARD_FIFO   comma list of bot FIFOs ('' = mute, for dry runs)
  GUARD_MASKS  comma list of protected user@host (vjt, his friends, the guardians)
  GUARD_BITCH  comma list of channels where bitch mode is on (default none)
"""
import os, random, re, subprocess

LOGS = os.environ.get("GUARD_LOG", "").split(",")
FIFOS = os.environ.get("GUARD_FIFO", "").split(",")
MASKS = {m.strip().lstrip("~").casefold() for m in os.environ.get(
    "GUARD_MASKS",
    "antani@due.dita.di.grappa.chat,"
    # The friends vjt trusts on #disagio (2026-11-11): tom, S~ (shinobi), gosub,
    # nano.
    "tom@tom.dev.irc.it,"
    "uid702845@id-702845.ilkley.irccloud.com,"
    "_gosub@stop.endsub.it,"
    "nano@epstein.bar,"
    "ambrogio@ci.pensa.ambrogio.con.la.grappa.chat,"
    "mike@ti.faccio.le.domande.mentre.sniffo.org").split(",") if m.strip()}

BITCH = {c.strip().casefold() for c in os.environ.get("GUARD_BITCH", "").split(",")
         if c.strip()}
# IRCnet takes at most three parameterised modes per MODE line.
MODES_PER_LINE = 3

KICK_MSGS = ["ao?!", "ao?! ma che fai", "ao?! giù le mani", "ao?! e mo' esci"]

LINE = re.compile(r'^\S+ < :(?P<src>\S+) (?P<cmd>\S+)(?: (?P<args>.*?))?\s*$')
# Channel modes that take an argument; l only when it is set.
ARG_MODES = set("ovbeIRk")


def norm(mask):
    user, _, host = mask.partition("@")
    return f"{user.lstrip('~')}@{host}".casefold()


def split_args(args):
    if args is None:
        return []
    head, sep, trailing = args.partition(" :")
    if args.startswith(":"):
        return [args[1:]]
    out = head.split()
    return out + [trailing] if sep else out


class Guard:
    """What one bot knows: its nick, and who sits where with which ops."""

    def __init__(self, world, fifo):
        self.world, self.fifo = world, fifo
        self.me = None
        self.chans = {}         # chan -> {nick_cf: is_op}

    def op(self, chan):
        return bool(self.me) and self.chans.get(chan, {}).get(self.me.casefold(), False)

    def send(self, line, out):
        if out is not None:
            out.append((self.fifo, line))

    def op_vjt(self, chan, out):
        """Op every protected nick sitting in `chan` without ops."""
        if not self.op(chan):
            return
        for n, is_op in self.chans[chan].items():
            if not is_op and self.world.trusted(n) and n != self.me.casefold():
                self.send(f"RAW:guard MODE {chan} +o {self.world.nicks[n]}", out)

    def modes(self, chan, sign, letter, args, out):
        """Send `sign letter*n args` in batches the server accepts."""
        for i in range(0, len(args), MODES_PER_LINE):
            batch = args[i:i + MODES_PER_LINE]
            self.send(f"RAW:guard MODE {chan} {sign}{letter * len(batch)} {' '.join(batch)}", out)

    def bitch(self, chan, nick, user_actor, foreign_ops, r_changes, out):
        """Take back every op given to an outsider, the op of an outsider who
        gave it, and +R edits that did not come from a protected mask."""
        if chan not in BITCH or not self.op(chan):
            return
        deop = list(dict.fromkeys(foreign_ops))
        if deop and user_actor and nick.casefold() in self.chans.get(chan, {}) \
                and nick not in deop:
            deop.append(nick)
        if deop:
            self.modes(chan, "-", "o", deop, out)
            if user_actor:
                self.punish(chan, nick, out)
        if r_changes and user_actor:
            for sign, mask in r_changes:
                self.send(f"RAW:guard MODE {chan} {'-' if sign == '+' else '+'}R {mask}", out)

    def punish(self, chan, actor, out):
        if self.op(chan) and actor.casefold() in self.chans.get(chan, {}):
            self.send(f"RAW:guard KICK {chan} {actor} :{random.choice(KICK_MSGS)}", out)

    def handle(self, src, cmd, a, out):
        nick, bang, mask = src.partition("!")
        cf = nick.casefold()
        user_actor = bool(bang) and not self.world.trusted_mask(mask)

        if cmd == "001" and a:
            self.me, self.chans = a[0], {}
        elif cmd == "353" and len(a) >= 4:
            chan = a[2].casefold()
            for n in a[3].split():
                bare = n.lstrip("@+")
                self.chans.setdefault(chan, {})[bare.casefold()] = n.startswith("@")
                self.world.nicks.setdefault(bare.casefold(), bare)
        elif cmd == "366" and len(a) >= 2:
            self.op_vjt(a[1].casefold(), out)
        elif cmd == "JOIN" and a:
            chan = a[0].casefold()
            if self.me and cf == self.me.casefold():
                self.chans[chan] = {}
            self.chans.setdefault(chan, {})[cf] = False
            if self.world.trusted(cf):
                self.op_vjt(chan, out)
        elif cmd == "PART" and a:
            self.chans.get(a[0].casefold(), {}).pop(cf, None)
            if self.me and cf == self.me.casefold():
                self.chans.pop(a[0].casefold(), None)
        elif cmd == "QUIT":
            for members in self.chans.values():
                members.pop(cf, None)
        elif cmd == "NICK" and a:
            new = a[0]
            for members in self.chans.values():
                if cf in members:
                    members[new.casefold()] = members.pop(cf)
            if self.me and cf == self.me.casefold():
                self.me = new
        elif cmd == "KICK" and len(a) >= 2:
            chan, target = a[0].casefold(), a[1].casefold()
            victim_trusted = self.world.trusted(target)
            self.chans.get(chan, {}).pop(target, None)
            if self.me and target == self.me.casefold():
                self.chans.pop(chan, None)
            elif victim_trusted and user_actor:
                self.punish(chan, nick, out)
        elif cmd == "MODE" and len(a) >= 2 and a[0][:1] in "#&!+":
            chan = a[0].casefold()
            members = self.chans.setdefault(chan, {})
            sign, args, deopped, opped_me = "+", a[2:], [], False
            foreign_ops, r_changes = [], []
            for c in a[1]:
                if c in "+-":
                    sign = c
                    continue
                if c not in ARG_MODES and not (c == "l" and sign == "+"):
                    continue
                arg = args.pop(0) if args else None
                if c == "R" and arg is not None:
                    r_changes.append((sign, arg))
                    continue
                if c != "o" or arg is None:
                    continue
                members[arg.casefold()] = sign == "+"
                if sign == "-" and self.world.trusted(arg.casefold()):
                    deopped.append(arg)
                if sign == "+" and self.me and arg.casefold() == self.me.casefold():
                    opped_me = True
                elif sign == "+" and not self.world.trusted(arg.casefold()):
                    foreign_ops.append(arg)
            # vjt deopping himself (or a guardian doing it) stays deopped.
            if deopped and user_actor:
                self.punish(chan, nick, out)
            if (deopped and user_actor) or opped_me:
                self.op_vjt(chan, out)
            self.bitch(chan, nick, user_actor, foreign_ops, r_changes, out)


class World:
    """What every bot shares: the network's nick -> mask map."""

    def __init__(self):
        self.masks = {}         # nick_cf -> normalized user@host
        self.nicks = {}         # nick_cf -> nick as spelled

    def trusted_mask(self, mask):
        return norm(mask) in MASKS

    def trusted(self, nick_cf):
        return self.masks.get(nick_cf) in MASKS

    def learn(self, src, cmd, a):
        nick, bang, mask = src.partition("!")
        if not bang:
            return
        self.masks[nick.casefold()] = norm(mask)
        self.nicks[nick.casefold()] = nick
        if cmd == "NICK" and a:
            self.masks[a[0].casefold()] = self.masks.pop(nick.casefold())
            self.nicks[a[0].casefold()] = a[0]


def feed(world, guard, line, out):
    m = LINE.match(line)
    if not m:
        return
    a = split_args(m.group("args"))
    # The mask is learned BEFORE handling, so a JOIN from vjt counts as vjt;
    # a NICK is learned AFTER, so the handler still finds the old nick.
    if m.group("cmd") != "NICK":
        world.learn(m.group("src"), m.group("cmd"), a)
    guard.handle(m.group("src"), m.group("cmd"), a, out)
    if m.group("cmd") == "NICK":
        world.learn(m.group("src"), m.group("cmd"), a)


def write(fifo, line):
    """Non-blocking: with no reader (bot down) skip instead of hanging."""
    if not fifo:
        return
    try:
        fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
    except OSError:
        return
    try:
        os.write(fd, (line + "\n").encode())
    except OSError:
        pass
    finally:
        os.close(fd)


def main():
    world = World()
    guards = {log: Guard(world, fifo) for log, fifo in zip(LOGS, FIFOS)}
    # Replay mute: who is who, and where each bot holds ops, as of now.
    for log, guard in guards.items():
        try:
            with open(log, errors="replace") as f:
                for line in f:
                    feed(world, guard, line, None)
        except FileNotFoundError:
            pass
    p = subprocess.Popen(["tail", "-F", "-n", "0", *LOGS],
                         stdout=subprocess.PIPE, text=True, errors="replace")
    guard = guards[LOGS[0]]
    for line in p.stdout:
        m = re.fullmatch(r"==> (.*) <==\n?", line)
        if m and m.group(1) in guards:
            guard = guards[m.group(1)]
            continue
        out = []
        feed(world, guard, line, out)
        for fifo, cmd in out:
            write(fifo, cmd)


if __name__ == "__main__":
    main()
