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
target rule, same cooldown, same no-repeat. One daemon serves them all;
`!sculaccia` (13:51) joined later, a `/me` pool like `!slap`.

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
  LOVE_NICK      the bot's own nick     (default: Ambrogio)
  LOVE_SKIP      commands off per channel, `#chan:cmd,cmd;#chan2:cmd`
                 (default none; IRCnet #cybernet has TOOLS for love/slap)
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
# The bot never targets itself (vjt, #sbiffo 2026-10-10 13:54: «evitare il
# self-slap»): `!slap Ambrogio` bounces back on whoever typed it.
BOT_NICK = os.environ.get("LOVE_NICK", "Ambrogio")


def parse_skip(spec):
    """`#a:love,slap;#b:love` -> {"#a": {"love", "slap"}, "#b": {"love"}}."""
    out = {}
    for part in filter(None, (p.strip() for p in spec.split(";"))):
        chan, _, cmds = part.partition(":")
        out[chan.strip().lower()] = {c.strip().lower() for c in cmds.split(",") if c.strip()}
    return out


# On IRCnet #cybernet TOOLS already answers !love and !slap (vjt, #sbiffo
# 2026-10-10 13:57): Ambrogio keeps only the commands TOOLS lacks there.
SKIP = parse_skip(os.environ.get("LOVE_SKIP", ""))

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
    # Pool to 100+, adult register (vjt, #sbiffo 2026-10-12 12:49:
    # «almeno 100 frasi ognuno, niente roba da asilo»).
    "@>-->-- {from} canta sotto il balcone di {to} finche' la vicina non gli tira un vaso di gerani --<--<@",
    "♥ {from} ha trovato {to} dentro un Bacio Perugina: «l'amore e' cieco», e infatti ♥",
    "♪ {from} dedica a {to} «Perdere l'amore» a squarciagola, stonato/a come Ranieri dopo il terzo amaro ♪",
    "°˖✧ {from} porta {to} a cena al lume di candela, poi salta la luce e paga lo stesso ✧˖°",
    "(h) {from} vuole invecchiare con {to}, possibilmente sullo stesso divano, col telecomando condiviso (h)",
    "╰☆╮ {from} ha inciso {to} sul lucchetto di Ponte Milvio e ha buttato la chiave nel Tevere ╰☆╮",
    "♡♡♡ {from} ti dedica una canzone a Sanremo {to}: ultimo posto, ma col cuore ♡♡♡",
    "~<3~ {from} ti ama piu' della nonna ama i santini {to} ~<3~ :*:*:*",
    "*:・ﾟ✧ {from} ti scrive una lettera d'amore con la penna profumata delle medie {to} ✧ﾟ・:*",
    "((((( {from} ti abbraccia come si abbraccia la Moka la mattina {to} ))))) ⊂(◕‿◕)⊃",
    "♥♥♥♥♥ {to} hai rubato il cuore di {from}, e pure il posto lato finestrino ♥♥♥♥♥",
    "°o.O {from} fa una serenata a {to} col mandolino, ma il mandolino e' rotto e il cane ulula O.o°",
    "☆ {from} ti porta a vedere il tramonto in Vespa {to}, e la Vespa si pianta in salita ☆ ♡♡♡",
    "°¤.¸¸.•´¯`» {from} sposerebbe {to} anche in un ristorante con la musica dal vivo e il trenino «`¯´•.¸¸.¤°",
    "✿ {from} ti regala dodici rose rosse {to}, prese al semaforo, ma col cuore ✿",
    "♪ღ♪ {from} sussurra a {to}: «sei la ricotta del mio cannolo» ♪ღ♪",
    "^_^ {from} ha fatto la dieta per {to}: tre giorni, poi la carbonara ^_^ <333",
    "✿ღ {from} ti dedica una poesia da Bacio Perugina {to}: «tu sei il sole, io sono gia' sudato» ღ✿",
    "~*:.｡o○ {from} sogna {to} in un matrimonio con 400 invitati e la torta a sette piani ○o｡.:*~ ♥",
    "(｡♥‿♥｡) {from} ti presenta a mamma {to}: e' il gesto d'amore piu' coraggioso che esista (｡♥‿♥｡)",
]

PHRASES = TOOLS_PHRASES + NEW_PHRASES

SLAP_PHRASES = [
    "/me slaps {to} around a bit with a large trout, on behalf of {from}",
    "/me prende una trota da tre chili e la stampa in faccia a {to}, da parte di {from}",
    "/me (╯°□°)╯ tira un ceffone a {to} che fa netsplit da solo, da parte di {from}",
    "/me schiaffeggia {to} con un baccala' sotto sale, stagionato 1997, da parte di {from}",
    "/me prende a sberle {to} con la tastiera, layout IT, tasto Invio compreso, da parte di {from}",
    "/me colpisce {to} con un /kill a mano aperta, da parte di {from}",
    "/me schiaffeggia {to} col manuale di bahamut, edizione rilegata, da parte di {from}",
    "/me tira a {to} uno sganassone che gli fa cambiare nick, da parte di {from}",
    "/me schiaffeggia {to} con un modem 56k, ancora attaccato al doppino, da parte di {from}",
    "/me prende un floppy da 1.44 e lo sbatte in fronte a {to}, da parte di {from}",
    "/me schiaffeggia {to} con un cavo seriale null-modem, da parte di {from}",
    "/me schiaffeggia {to} con una trota del Brenta, che fa piu' male, da parte di {from}",
    "/me prende {to} a schiaffi col motd di Azzurra stampato in A3, da parte di {from}",
    "/me da' a {to} uno schiaffo cosi' forte che gli si resetta il bouncer, da parte di {from}",
    "/me prende a sberle {to} con una copia di linux&c del 1999, da parte di {from}",
    # Plain-Italian lines: the jargon mash-ups made no sense to anyone
    # (sarahbean, #sbiffo 2026-10-11 12:33; vjt: «migliorale»).
    "/me rifila a {to} lo schiaffo che la mamma tiene in caldo da quando hai risposto male a tavola, da parte di {from}",
    "/me tira a {to} uno schiaffo alla Bud Spencer, col rumore della padella, da parte di {from}",
    "/me molla a {to} il ceffone del vigile che l'ha trovato in doppia fila, da parte di {from}",
    "/me da' a {to} lo scappellotto del nonno quando dici una parolaccia a tavola, da parte di {from}",
    "/me tira a {to} uno schiaffo da telenovela, con la musica drammatica e lo zoom, da parte di {from}",
    "/me sveglia {to} con uno schiaffo, come nei film quando qualcuno sviene, da parte di {from}",
    "/me si sfila il guanto e schiaffeggia {to}: domattina all'alba, duello in cortile, da parte di {from}",
    "/me tira a {to} un ceffone che gli fa girare la faccia e pure il cappello, da parte di {from}",
    "/me da' a {to} lo schiaffo che meritava da anni e nessuno aveva il coraggio di dargli, da parte di {from}",
    # Pool to 100+, adult register (vjt, #sbiffo 2026-10-12 12:49:
    # «almeno 100 frasi ognuno, niente roba da asilo»).
    "/me molla a {to} una sberla cosi' forte che la suocera chiama dal paese per sapere chi e' morto, da parte di {from}",
    "/me rifila a {to} il ceffone del megadirettore galattico, con la poltrona in pelle umana che applaude, da parte di {from}",
    "/me sganascia {to} con un manrovescio alla Bud Spencer e poi si spazzola due padelle di fagioli, da parte di {from}",
    "/me prende {to} a sberle con una mortadella di Bologna IGP, intera, da parte di {from}",
    "/me stampa sulla faccia di {to} cinque dita che si vedono pure da Google Maps, da parte di {from}",
    "/me rifila a {to} uno schiaffo da bar sport, dopo che ha detto che il fuorigioco era regolare, da parte di {from}",
    "/me schiaffeggia {to} con la schedina del Totocalcio sbagliata all'ultimo minuto, da parte di {from}",
    "/me cala su {to} un ceffone e gli urla: «ma vaffanculo, tu e la tua Panda a metano», da parte di {from}",
    "/me apre la mano come un badile e la cala su {to} come una ghigliottina condominiale, da parte di {from}",
    "/me appioppa a {to} lo schiaffo dell'amministratore di condominio quando chiedi il rendiconto, da parte di {from}",
    "/me tira a {to} un ceffone in diretta da Sanremo, e Amadeus lo presenta come superospite, da parte di {from}",
    "/me schiaffeggia {to} col modello F24 compilato male, in triplice copia, da parte di {from}",
    "/me sgancia su {to} uno sganassone con la pazienza di un impiegato dell'INPS alle 12:59, da parte di {from}",
    "/me prende {to} a ceffoni con la tovaglia della domenica, ancora sporca di ragu', da parte di {from}",
    "/me assesta a {to} una sberla col guanto da forno appena uscito dal forno, cosi' brucia due volte, da parte di {from}",
    "/me schiaffeggia {to} con una fetta di culatello, che almeno muore contento, da parte di {from}",
    "/me spara a {to} una sventola alla Alvaro Vitali, con la faccia da Pierino e il rumore dello sciacquone, da parte di {from}",
    "/me sventola sul muso di {to} uno schiaffo che manco er Monnezza in questura, da parte di {from}",
    "/me fischia a {to} uno schiaffo da arbitro cornuto, con il cartellino rosso infilato in bocca, da parte di {from}",
    "/me prende {to} a schiaffi col calendario di Frate Indovino, un mese alla volta, da parte di {from}",
    "/me molla a {to} uno sberlone: «questo e' per tutte le volte che hai detto 'piuttosto che'», da parte di {from}",
    "/me schiaffeggia {to} con lo scontrino del pranzo di nozze del cugino, lungo due metri, da parte di {from}",
    "/me tira a {to} un manrovescio che gli rimette a posto la cervicale, gratis e senza ricetta, da parte di {from}",
    "/me stende {to} con uno schiaffone da camionista all'Autogrill di Roncobilaccio, da parte di {from}",
    "/me fa a {to} il trattamento della vecchia zia: carezza sulla guancia, pizzicotto, e poi la sberla, da parte di {from}",
    "/me spedisce {to} con uno sganassone a contare i cantieri della Salerno-Reggio, da parte di {from}",
    "/me tira a {to} un ceffone in bergamasco stretto, che non si capisce niente ma fa malissimo, da parte di {from}",
    "/me schiaffeggia {to} alla romana, urlando «aridaje, mo' te gonfio come 'na zampogna», da parte di {from}",
    "/me prepara la mano a carciofo, la scuote sotto il naso di {to} e poi la apre in faccia, alla napoletana, da parte di {from}",
    "/me sberla {to} col bastone della polenta e poi lo mette a girarla per un'ora, da parte di {from}",
    "/me molla a {to} un ceffone che gli fa cadere la dentiera nel minestrone, da parte di {from}",
    "/me prende {to} a schiaffi con la Gazzetta dello Sport del lunedi', quella della sconfitta, da parte di {from}",
    "/me tira a {to} una sberla: «stronzo, la moka non si lava col detersivo», da parte di {from}",
    "/me fa ruotare il braccio come un mulino e centra {to} in piena faccia, stile spaghetti western, da parte di {from}",
    "/me prende la rincorsa dal pianerottolo e stampa su {to} una sberla che sveglia tutto il palazzo, da parte di {from}",
    "/me schiaffeggia {to} cosi' forte che l'eco arriva alla riunione di condominio e riparte la lite sulle tegole, da parte di {from}",
    "/me cala su {to} un ceffone e il ragionier Filini subito: «ottimo, ottimo, lo metto a verbale», da parte di {from}",
    "/me tira a {to} un manrovescio e lo intesta alla ditta, cosi' lo scarica dalle tasse, da parte di {from}",
    "/me sbatte in faccia a {to} il mazzo di carte da scopa, settebello compreso, da parte di {from}",
    "/me da' a {to} la sberla della briscola, perche' ha calato l'asso sul due come un coglione, da parte di {from}",
    "/me molla a {to} lo schiaffo del barista quando chiedi un cappuccino dopo mezzogiorno, da parte di {from}",
    "/me schiaffeggia {to} con un cotechino bollito, ancora fumante, da parte di {from}",
    "/me cala sulla testa di {to} il pugno a martello di Bud Spencer, e lui affonda nel pavimento fino alle ginocchia, da parte di {from}",
    "/me prende {to} a sberle col cartello «torno subito» dell'ufficio anagrafe, da parte di {from}",
    "/me tira a {to} uno schiaffo e il pubblico chiede il bis come all'Ariston, porca troia, da parte di {from}",
    "/me rifila a {to} una sberla da mettere in cornice, accanto al diploma di ragioneria, da parte di {from}",
    "/me tira a {to} un ceffone cosi' sonoro che al mercato del pesce credono sia arrivato il tonno, da parte di {from}",
    "/me schiaffeggia {to} con la suola di una Clarks, quella del prof di matematica, da parte di {from}",
    "/me rifila a {to} uno sganassone e poi gli offre un amaro, che aiuta a digerire, da parte di {from}",
    "/me molla a {to} uno schiaffo: «questo e' l'acconto, il saldo a fine mese», da parte di {from}",
    "/me appioppa a {to} una sberla e gli trattiene pure la tredicesima, da parte di {from}",
    "/me prende a ceffoni {to} col cappello da alpino, piuma compresa, da parte di {from}",
    "/me tira a {to} uno schiaffo da Mondiali '82, con l'urlo di Tardelli in sottofondo, da parte di {from}",
    "/me spiana {to} con un ceffone da muratore in pausa pranzo, cazzuola ancora in mano, da parte di {from}",
    "/me scaraventa {to} con uno schiaffone contro la credenza buona, quella dei bicchieri del matrimonio, da parte di {from}",
    "/me sberla {to} col telecomando del decoder, che finalmente serve a qualcosa, da parte di {from}",
    "/me tira a {to} una sventola che gli fa vedere le stelle, i pianeti e Piero Angela che li spiega, da parte di {from}",
    "/me prende {to} a sberle con un salame di Felino, poi lo appende in cantina a stagionare, da parte di {from}",
    "/me rifila a {to} il ceffone del parroco all'oratorio quando bestemmiavi a calcetto, da parte di {from}",
    "/me molla a {to} una sberla: «testa di cazzo, il pesto si fa nel mortaio», da parte di {from}",
    "/me tira a {to} uno schiaffo cosi' forte che gli si spettina il riporto, da parte di {from}",
    "/me prende {to} a manrovesci col borsello di pelle da ragioniere, da parte di {from}",
    "/me gioca con {to} allo schiaffo del soldato, ma vince sempre lui perche' bara, da parte di {from}",
    "/me tira a {to} un ceffone che finisce al telegiornale regionale, subito dopo la sagra del cinghiale, da parte di {from}",
    "/me molla a {to} uno sganassone e poi, alla Toto', esclama: «e io pago!», da parte di {from}",
    "/me tira a {to} una sberla e lui strilla alla Lino Banfi: «porca puttena, che male!», da parte di {from}",
    "/me schiaffeggia {to} col volantino del supermercato, offerte della settimana comprese, da parte di {from}",
    "/me piazza a {to} una sberla in pieno pranzo di Pasqua, e la colomba vola via davvero, da parte di {from}",
    "/me prende {to} a schiaffi col fiasco di Chianti vuoto, quello impagliato, da parte di {from}",
    "/me molla a {to} un ceffone che gli fa cantare «Volare» con l'acuto finale, da parte di {from}",
    "/me tira a {to} uno schiaffo, va a rivederlo al VAR e conferma: rigore netto, da parte di {from}",
    "/me prende {to} a sberle in IPv6, cosi' gli arrivano da tutti gli indirizzi, da parte di {from}",
    "/me schiaffeggia {to} e gli dice: «sei come Windows Vista, lento e inutile», da parte di {from}",
    "/me sberla {to} col cavo USB che entra solo al terzo tentativo, da parte di {from}",
    "/me chiude la discussione con {to} a ceffoni: «mo' basta, stronzo, vai a cagare», da parte di {from}",
    "/me molla a {to} uno schiaffo cosi' forte che gli parte l'antifurto della macchina, quello che suona tutta la notte, da parte di {from}",
    "/me rifila a {to} lo schiaffo della nonna con la fede d'oro girata al contrario, da parte di {from}",
    "/me sventaglia su {to} un manrovescio da Sergio Leone: primo piano, silenzio, poi il botto, da parte di {from}",
    "/me prende {to} a schiaffi con la bolletta della luce, quella col conguaglio, da parte di {from}",
    "/me tira a {to} un ceffone e lo rimanda a settembre, con la media del quattro, da parte di {from}",
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
    # Plain-Italian lines for non-nerds (vjt, TG 2026-10-10 14:18).
    "{from} fa a {to} la pernacchia del vigile al semaforo: PRRRRRT, e ti fa pure la multa",
    "{from} fa a {to} una pernacchia da ultimo banco, mentre la prof scrive alla lavagna: PRRRT",
    "{from} fa a {to} una pernacchia cosi' forte che il cane del vicino comincia ad abbaiare: PRRRRRRRT",
    "{from} fa a {to} la pernacchia della nonna quando le dici che non hai fame: PFFFRRT, mangia!",
    "{from} fa a {to} una pernacchia da curva sud dopo un rigore sbagliato: BRRRRRRRRRRT",
    "{from} fa a {to} una pernacchia in chiesa, durante il silenzio: prrrt. Tutti si girano",
    "{from} fa a {to} una pernacchia col palloncino che si sgonfia: FRRRRRRRRRRRT",
    "{from} fa a {to} la pernacchia del bambino sulla pancia del papa': PBBBBBBBBT",
    "{from} fa a {to} una pernacchia al matrimonio, proprio al «si', lo voglio»: PRRRRRT",
    "{from} fa a {to} la pernacchia del cuscino scoreggione sotto la sedia: PRRRRRRRRRT",
    "{from} fa a {to} una pernacchia da Fantozzi: la corazzata Potemkin e' una PRRRRRRRT",
    "{from} risponde a {to} come Toto': ma mi faccia il piacere! PRRRRRRRT",
    "{from} fa a {to} una pernacchia dal balcone, con la vicina che stende i panni: PRRRT",
    "{from} fa a {to} una pernacchia in ascensore, e poi fa finta che sia stato un altro: PRRRT",
    "{from} fa a {to} una pernacchia da sciopero dei tram: PRRRRRRRRRRRRRRRRT",
    "{from} fa a {to} una pernacchia con la bocca piena di spaghetti: PFRRRSLURP",
    # Pool to 100+, adult register (vjt, #sbiffo 2026-10-12 12:49:
    # «almeno 100 frasi ognuno, niente roba da asilo»).
    "{from} fa a {to} la pernacchia di Bud Spencer prima del cazzotto in testa: PRRRT, SBAM",
    "{from} fa a {to} una pernacchia da ragionier Filini, che rimbomba in tutta la mensa aziendale: PRRRRRRT",
    "{from} fa a {to} una pernacchia alla Alberto Sordi: «io c'ho 'na pernacchia pe' te»: PRRRRRT",
    "{from} timbra il cartellino e fa a {to} una pernacchia da statale in pausa caffe': PRRRRRRRRRRRT",
    "{from} fa a {to} la pernacchia dello zio ubriaco al pranzo di Natale, tra la tombola e il rutto: BRRRRRAAAT",
    "{from} fa a {to} una pernacchia da condominio, messa a verbale nell'assemblea: PRRRT",
    "{from} si alza dal tavolino del bar sport e fa a {to} una pernacchia da moviola del lunedi': PRRRT, era rigore",
    "{from} fa a {to} una pernacchia in diretta da Sanremo, con l'orchestra che attacca: PRRRRRT (dirige il maestro)",
    "{from} fa a {to} una pernacchia da suocera alla prima cena: PFRRRT, il sugo e' sciapo",
    "{from} sale sul tavolo della sagra della porchetta e dedica a {to}: BRRRRRRRRRRRRRRT",
    "{from} fa a {to} una pernacchia da sportello dell'anagrafe: PRRRT, manca la marca da bollo",
    "{from} fa a {to} una pernacchia da ombrellone a ombrellone: PRRRRRT, cocco bello cocco fresco",
    "{from} alza appena una chiappa e fa a {to} una pernacchia col culo, ma educata: prrrt",
    "{from} manda a {to} una pernacchia raccomandata con ricevuta di ritorno: PRRRRRRT (firmare qui)",
    "{from} fa a {to} una pernacchia da comizio elettorale in piazza: PRRRT, e votatemi stronzi",
    "{from} prova ad accendere la Panda, la Panda non parte, e {to} riceve lo stesso: BRRRT-BRRRT-PFFFT",
    "{from} fa a {to} una pernacchia da portiere battuto da centrocampo: PRRRRRRRRRRT, che cazzo di papera",
    "{from} prende fiato come un sub e scarica su {to}: PPPPPPPPRRRRRRRRRRRRRRRRRRRRRT",
    "{from} fa a {to} la pernacchia del capufficio quando chiedi le ferie ad agosto: PRRRT, scordatelo",
    "{from} fa a {to} una pernacchia con la tromba della Vespa truccata: PEEEE-PRRRRRRRT",
    "{from} scrive a {to} una pernacchia alla Toto' e Peppino: punto, due punti, PRRRT, punto e virgola",
    "{from} fa a {to} una pernacchia da biliardo del Necchi, proprio mentre tira: PRRRT, stecca",
    "{from} fa a {to} una pernacchia da nonno con la dentiera allentata: PFLAPFLAPRRRT",
    "{from} fa a {to} una pernacchia da autogrill alle 3 di notte: PRRRT, e il caffe' fa schifo",
    "{from} fa a {to} una pernacchia in dialetto stretto, che non si traduce: PRRRRRRRRT, capisc'a me",
    "{from} si sbottona i pantaloni dopo il cenone e dedica a {to} quella vera: BRRRRRRT (aprite la finestra)",
    "{from} fa a {to} una pernacchia da guardalinee comprato: PRRRT, fuorigioco di un metro",
    "{from} fa a {to} una pernacchia da tassista romano al semaforo: PRRRT, ma 'ndo vai, a cojone",
    "{from} fa a {to} una pernacchia dall'impalcatura, con la betoniera che fa il coro: PRRRRT-GLOGLOGLO",
    "{from} fa a {to} una pernacchia da rientro dalle ferie, in coda sull'A1: PRRRRRRRRRRRRRRRRRRRRT (35 km)",
    "{from} fa a {to} una pernacchia alla lettura del testamento: PRRRT, a te lo zio ha lasciato i debiti",
    "{from} fa a {to} una pernacchia da Fantozzi al megadirettore galattico, poi si inginocchia: prrrt... mi scusi",
    "{from} fa a {to} una pernacchia dalla tribuna d'onore, col presidente che si caga addosso: PRRRRT",
    "{from} fa a {to} una pernacchia da sagrestano, mentre suonano le campane: DONG-PRRRT-DONG",
    "{from} fa a {to} una pernacchia da bancarella del mercato: PRRRT, tre per dieci euro, signo'",
    "{from} organizza per {to} una pernacchia di gruppo con tutta la tavolata del matrimonio: PRRRT x 120 coperti",
    "{from} fa a {to} una pernacchia da Bud Spencer davanti a un piatto di fagioli: BRRRRRRRRT, e poi il rutto",
    "{from} fa a {to} una pernacchia da curva nord, pensata per l'arbitro cornuto ma arrivata a lui: PRRRRRRRRRT",
    "{from} fa a {to} una pernacchia da concorso pubblico, 3000 candidati per un posto: PRRRT, il posto era del cugino",
    "{from} fa a {to} una pernacchia da veglione di Capodanno, in mezzo ai botti: BOOM PRRRT BOOM",
    "{from} fa a {to} una pernacchia cosi' grassa che ci si potrebbe friggere le frittelle: PFRRRRSSSSHHT",
    "{from} fa a {to} una pernacchia da ufficio postale, col numerino 847 mentre chiamano il 12: PRRRRRRT",
    "{from} si toglie la dentiera e fa a {to} la pernacchia piu' bagnata del secolo: PFTHBBBBBT",
    "{from} fa a {to} una pernacchia da trattore in salita: TU-TU-TU-PRRRRRRRRRT",
    "{from} fa a {to} una pernacchia ufficiale, con timbro, protocollo e firma del dirigente: PRRRT (prot. n. 666)",
    "{from} fa a {to} una pernacchia da neomelodico, col vibrato: PRRRRR-RRRR-RRRRRT, ammore",
    "{from} fa a {to} una pernacchia da spogliatoio dopo il 4-0: PRRRT, e la doccia e' pure fredda",
    "{from} risponde a {to} con l'eleganza di un consigliere comunale a fine seduta: PRRRRRRRRT",
    "{from} fa a {to} una pernacchia tra un rutto e l'altro, offerta dalla birra del discount: BUUURP-PRRRT-BUUURP",
    "{from} fa a {to} la pernacchia del vicino col trapano la domenica mattina: PRRRT-ZZZZZZZZ-PRRRT",
    "{from} fa a {to} una pernacchia da telecronista che perde la voce al 90': PRRRT... rrrt... rete!",
    "{from} fa a {to} una pernacchia da cognato che sa tutto lui: PRRRT, e comunque te l'avevo detto",
    "{from} fa a {to} una pernacchia in fila alla cassa, col vecchio che paga in monetine: PRRRRRRRRRRRT",
    "{from} fa a {to} una pernacchia da oste di trattoria: PRRRT, la carbonara con la panna se la fa a casa sua",
    "{from} fa a {to} una pernacchia cosi' potente che gli sposta il riporto: FRRRRRRRRRT (tutto a sinistra)",
    "{from} fa a {to} la pernacchia del vecchio del bar che gioca a scopa: PRRRT, settebello, e vaffanculo",
    "{from} fa a {to} una pernacchia da imbianchino: PRRRT, e il preventivo raddoppia",
    "{from} fa a {to} una pernacchia dallo yacht in Costa Smeralda: PRRRRT, e voi poveracci col pedalo'",
    "{from} fa a {to} una pernacchia da mutuo trentennale: PRRRRRRRRRRRRRRRRRRRRRRRRRRRRT (tasso variabile)",
    "{from} fa a {to} una pernacchia in sala d'attesa del dentista, con la fresa che risponde: PRRRT-IIIIIIIII",
    "{from} guarda {to} dritto negli occhi, senza dire un cazzo, e poi: PPPPRRRRRRRT",
    "{from} fa a {to} una pernacchia col clacson della 127 nel corteo degli sposi: PEPPEREPPEPPE-PRRRT",
    "{from} fa a {to} una pernacchia in mailing list, con tutto l'ufficio in CC: PRRRT (rispondi a tutti)",
    "{from} imposta l'/away e lascia a {to} una pernacchia in segreteria: torno subito, PRRRRRRT",
    "{from} fa a {to} la pernacchia di Fantozzi dopo i 92 minuti di applausi: PRRRRRRRRRRRT",
]

SCULACCIA_PHRASES = [
    "/me prende {to} sulle ginocchia e gli rifila una sculacciata da manuale, da parte di {from}",
    "/me sculaccia {to} con una ciabatta da casa, modello nonna, da parte di {from}",
    "/me sculaccia {to} col mestolo di legno della pasta al forno, da parte di {from}",
    "/me prende la cintura e sculaccia {to} come negli anni '80, da parte di {from}",
    "/me sculaccia {to} col battipanni della suocera, da parte di {from}",
    # Plain-Italian lines, same reason as SLAP_PHRASES.
    "/me sculaccia {to} col cucchiaio di legno, come la nonna quando rubavi il ragu' dalla pentola, da parte di {from}",
    "/me sculaccia {to} davanti a tutti al pranzo di Natale, mentre lo zio ride, da parte di {from}",
    "/me sculaccia {to} con la racchetta da ping pong: punto, set e partita, da parte di {from}",
    "/me sculaccia {to} con la ciabatta lanciata da tre metri, come solo le mamme sanno fare, da parte di {from}",
    "/me sculaccia {to} col giornale arrotolato, come il cane che ha fatto pipi' sul tappeto, da parte di {from}",
    "/me sculaccia {to} a mano aperta e gli lascia l'impronta per tre giorni, da parte di {from}",
    "/me sculaccia {to} perche' ha messo l'ananas sulla pizza, da parte di {from}",
    "/me sculaccia {to} con la scopa di saggina, e poi gli fa pure spazzare, da parte di {from}",
    "/me sculaccia {to} col mattarello, e poi tira la sfoglia per le lasagne, da parte di {from}",
    "/me sculaccia {to} perche' ha lasciato la tazza del cesso alzata un'altra volta, da parte di {from}",
    # Pool to 100+, adult register (vjt, #sbiffo 2026-10-12 12:49:
    # «almeno 100 frasi ognuno, niente roba da asilo»).
    "/me cala sul culo di {to} la schiumarola del fritto misto, che lascia i buchini, da parte di {from}",
    "/me mette {to} a novanta sul cofano della Duna e gli rifila tre sculacciate da meccanico, da parte di {from}",
    "/me ferma {to} con la paletta del vigile: accosti, scenda, chiappe all'aria, da parte di {from}",
    "/me rifila a {to} una sculacciata che gli lascia il marchio come una mozzarella di bufala DOP, da parte di {from}",
    "/me prende {to} per la collottola e gli scalda le chiappe come un suppli' al forno, da parte di {from}",
    "/me sculaccia {to} alla commedia sexy anni '70, con la musichetta e il ragioniere che spia dal buco della serratura, da parte di {from}",
    "/me serve sulle chiappe di {to} la racchetta di Panatta, rovescio a una mano, da parte di {from}",
    "/me prende un caciocavallo appeso e lo fa dondolare sulle chiappe di {to}, avanti e indietro, da parte di {from}",
    "/me abbassa le braghe a {to} davanti alla portineria e lo sculaccia, mentre la portinaia prende appunti, da parte di {from}",
    "/me arrossa le chiappe di {to} come la Ferrari a Monza, giro veloce compreso, da parte di {from}",
    "/me molla una pacca a {to} e decreta: «questo culo e' patrimonio dell'UNESCO, ma lo visitano in pochi», da parte di {from}",
    "/me castiga il sedere di {to} col registro di classe, nota disciplinare compresa, da parte di {from}",
    "/me rifila a {to} una sculacciata da caserma, col sergente che conta ad alta voce, da parte di {from}",
    "/me fa fare a {to} il giro di piazza del Palio di Siena, col frustino del fantino sulle chiappe, da parte di {from}",
    "/me spiaccica sul culo di {to} una margherita bollente, e la mozzarella gli resta attaccata al culo, da parte di {from}",
    "/me suona la tarantella sulle chiappe di {to}, e sul finale accelera, da parte di {from}",
    "/me gira le chiappe di {to} con la paletta, come le frittelle di Carnevale, da parte di {from}",
    "/me pesa le chiappe di {to} a sberle e sentenzia: «chiappe sode, cervello molle, il solito», da parte di {from}",
    "/me piega {to} sul tavolo del bar e lo sculaccia mentre il barista segna i punti sulla lavagnetta, da parte di {from}",
    "/me frusta il didietro di {to} con lo scontrino della spesa da duecento euro, che fa piu' male lui della mano, da parte di {from}",
    "/me sventaglia il culo di {to} col ventaglio della zia di Palermo, avanti e indietro, da parte di {from}",
    "/me pareggia il culo di {to} con la sinistra, perche' la destra serve per giocare la schedina, da parte di {from}",
    "/me dirige l'orchestra di Sanremo sulle chiappe di {to}, e il pubblico si alza in piedi, da parte di {from}",
    "/me impana le chiappe di {to} e ci sbatte sopra una cotoletta alla milanese, orecchia d'elefante, impanata, da parte di {from}",
    "/me fa sedere {to} di traverso sulle ginocchia e gli suona le chiappe come i bonghi, da parte di {from}",
    "/me rosola il culo di {to} a pacche mentre la suocera urla dalla cucina: «piu' forte, che non ha mai fatto un cazzo», da parte di {from}",
    "/me infila sotto il culo di {to} la pala del forno a legna della pizzeria sotto casa, e spinge, da parte di {from}",
    "/me batte il sedere di {to} con un mazzo di sedano, che fa bene e lascia il segno, da parte di {from}",
    "/me rifila a {to} una sculacciata e grida alla Lino Banfi: «che culo, porca puttena!», da parte di {from}",
    "/me sculaccia {to} come nei film di Gloria Guida, col preside che entra proprio sul piu' bello, da parte di {from}",
    "/me timbra il culo di {to} con la Smemoranda piena di dediche delle medie, da parte di {from}",
    "/me fattura a {to} tre sculacciate e gli fa pure la ricevuta fiscale, perche' qui si lavora in regola, da parte di {from}",
    "/me sprimaccia il culo di {to} col cuscino buono del divano, quello che non si usa mai, da parte di {from}",
    "/me fa ballare le chiappe di {to} piu' di Raffaella Carra' col Tuca Tuca, da parte di {from}",
    "/me sculaccia {to} col libretto di istruzioni dell'IKEA, in svedese, con la brugola dentro, da parte di {from}",
    "/me notifica al sedere di {to} un fascicolo del tribunale, causa pendente dal 1994, da parte di {from}",
    "/me guarda il culo di {to} e annuncia: «mare mosso, molto mosso», poi lo sculaccia, da parte di {from}",
    "/me sventola sul culo di {to} la bandiera della curva, che sventola meglio delle sue chiappe, da parte di {from}",
    "/me sculaccia {to} col santino del bomber della squadra del paese, da parte di {from}",
    "/me ripassa il culo di {to} cosi' forte che dalla finestra di fronte la vicina chiede il bis, da parte di {from}",
    "/me spiana le natiche di {to} con la paletta acchiappamosche, e ne prende due al volo, da parte di {from}",
    "/me sbatte sul didietro di {to} il borsone della palestra, quello che puzza da settembre, da parte di {from}",
    "/me rifila a {to} una sculacciata col bollo dell'auto scaduto, mora inclusa, da parte di {from}",
    "/me centra il culo di {to} con la rete del pallone dell'oratorio, porco cane, da parte di {from}",
    "/me sculaccia {to} con un rotolo di carta da parati a fiori del salotto della nonna, da parte di {from}",
    "/me piega {to} sul tagliere del salumiere e gli affetta l'orgoglio a colpi di palmo, da parte di {from}",
    "/me sculaccia {to} con la paletta del ghiaccio di un bar di Rimini, Ferragosto '78, da parte di {from}",
    "/me scalda il deretano di {to} alla festa del patrono, mentre i fuochi d'artificio coprono gli strilli, da parte di {from}",
    "/me sculaccia {to} sul sedile di dietro della 127, quello che non torna piu' su, da parte di {from}",
    "/me sculaccia {to} con una pagnotta di Altamura, croccante fuori e cattiva dentro, da parte di {from}",
    "/me fa risuonare le chiappe di {to} come un applauso all'Ariston, da parte di {from}",
    "/me rema sulle chiappe di {to} col remo del gondoliere, che intanto urla «oh, ocio!», da parte di {from}",
    "/me sculaccia {to} con la tavoletta del cesso, cosi' impara pure ad abbassarla, da parte di {from}",
    "/me pizzica e poi stampa le chiappe di {to} e chiede: «queste chiappe le dichiari al catasto o le tieni in nero?», da parte di {from}",
    "/me sculaccia {to} col frustino di una dominatrice in pensione che ora fa la sciura a Brera, da parte di {from}",
    "/me schiaccia sul culo di {to} un'anguria intera, poi la taglia e la offre a tutti, da parte di {from}",
    "/me piega {to} sul bancone delle Poste e lo sculaccia, tanto la fila non si muove lo stesso, da parte di {from}",
    "/me sculaccia {to} col guanto da portiere di Zoff, che para tutto tranne questo, da parte di {from}",
    "/me sculaccia {to} e poi lo fa sedere sul ghiaccio come Fantozzi nella cella frigorifera, da parte di {from}",
    "/me lascia cinque dita sul culo di {to}: per una settimana sta in piedi come un corazziere, da parte di {from}",
    "/me sculaccia {to} con un ventaglio di bollette arretrate, conguaglio compreso, da parte di {from}",
    "/me risolve il culo di {to} a colpi di Settimana Enigmistica arrotolata, rebus compreso, da parte di {from}",
    "/me sculaccia {to} con la cintura dei pantaloni della festa, quella col fibbione da cowboy, da parte di {from}",
    "/me sculaccia {to} e gli dice: «c'hai un culo che pare il Duomo di Milano, sempre in restauro», da parte di {from}",
    "/me prende a pacche il sedere di {to} col ruotino di scorta della Uno, gonfiato male, da parte di {from}",
    "/me sbatte le mani sul culo di {to} e grida: «culo di marmo, testa di cazzo», da parte di {from}",
    "/me sculaccia {to} con la frusta del domatore del circo, e il leone si offende, da parte di {from}",
    "/me tamburella sul sedere di {to} «Una lacrima sul viso», e le chiappe tengono il tempo, da parte di {from}",
    "/me sculaccia {to} con le mani unte di frittura, cosi' scivola e prende anche il fianco, da parte di {from}",
    "/me sculaccia {to} col sandalo francescano del professore di latino, calzino compreso, da parte di {from}",
    "/me sculaccia {to} col Torrente di diritto privato, che pesa come un'incudine, da parte di {from}",
    "/me sculaccia {to} col codice della strada e gli toglie pure dieci punti dalla patente, da parte di {from}",
    "/me cala sul didietro di {to} la sporta della spesa piena di patate, da parte di {from}",
    "/me sculaccia {to} col mouse senza fili, e le chiappe vanno in standby, da parte di {from}",
    "/me sculaccia {to} come si fa col router: spegni, aspetta trenta secondi, riaccendi, da parte di {from}",
    "/me sculaccia {to} con un hard disk del '91, che pesava come un mattone, da parte di {from}",
    "/me sculaccia {to}: «cosi' impari a mettere la panna nella carbonara, stronzo», da parte di {from}",
    "/me sculaccia {to} col pattino del bagnino di Riccione, mentre il bagnino fischia il fallo, da parte di {from}",
    "/me picchia sul culo di {to} cosi' sonoramente che la vicina di sotto bussa col manico della scopa, da parte di {from}",
    "/me paga le chiappe di {to} con la busta paga, che tanto e' leggera e non fa male, da parte di {from}",
    "/me sculaccia {to} e lui, alla Fracchia, sospira: «com'e' umano, lei», da parte di {from}",
    "/me dichiara le chiappe di {to} zona rossa e ci passa sopra col palmo, da parte di {from}",
    "/me prende {to} in braccio come un sacco di patate e gli scalda il culo fino al portone, da parte di {from}",
    "/me sculaccia {to} a mano aperta, e la nonna dal balcone: «brava, e' quello che ci voleva», da parte di {from}",
    "/me ordina per le chiappe di {to} il menu' completo della trattoria, servito col menu' plastificato, da parte di {from}",
]

DOCCIA_PHRASES = [
    "/me rovescia su {to} un secchio d'acqua gelata, da parte di {from}",
    "/me apre la doccia fredda su {to} e si porta via l'asciugamano, da parte di {from}",
    "/me infila {to} sotto la doccia con lo scaldabagno rotto dal 1997, da parte di {from}",
    "/me lava {to} con l'idropulitrice, modalita' sverniciatura, da parte di {from}",
    "/me fa a {to} una doccia col sapone di Marsiglia e lo spazzolone dei cessi, da parte di {from}",
    "/me spruzza {to} col tubo del giardino come un gatto sul divano, da parte di {from}",
    "/me mette {to} sotto la doccia e gli stacca l'acqua calda a meta' shampoo, da parte di {from}",
    "/me lava {to} a 90 gradi con la centrifuga a 1400 giri, da parte di {from}",
    "/me fa a {to} una doccia di /quit: piove gente che esce, da parte di {from}",
    "/me spara su {to} l'idrante dei pompieri, che puzzava, da parte di {from}",
    "/me fa a {to} una doccia di ghiaccio, poi gli chiede se era meglio il flood, da parte di {from}",
    "/me lava {to} con la candeggina, ma solo i calzini, da parte di {from}",
    "/me mette {to} sotto la doccia del campeggio a gettoni, con un gettone solo, da parte di {from}",
    "/me spruzza {to} con l'acqua santa e vede se fuma, da parte di {from}",
    "/me fa a {to} una doccia di pacchetti persi, 100% packet loss, da parte di {from}",
    # Pool to 100+, adult register (vjt, #sbiffo 2026-10-12 12:49:
    # «almeno 100 frasi ognuno, niente roba da asilo»).
    "/me rovescia su {to} la bacinella dei piedi del nonno, quella dopo la camminata, da parte di {from}",
    "/me tira su {to} il secchio della portinaia che sta lavando le scale, da parte di {from}",
    "/me butta {to} vestito in piscina alla festa di laurea, da parte di {from}",
    "/me lancia {to} in mare a Rimini, a novembre, con la bora, da parte di {from}",
    "/me prende {to} per la collottola alla Bud Spencer e lo infila nell'abbeveratoio dei cavalli, da parte di {from}",
    "/me rovescia su {to} il secchiello del ghiaccio dello spumante del matrimonio, bottiglie comprese, da parte di {from}",
    "/me lava {to} all'autolavaggio a gettoni, rulli rotanti e tutto, da parte di {from}",
    "/me fa la doccia a {to} con la pompa del condominio, poi l'amministratore manda il conto, da parte di {from}",
    "/me annaffia {to} insieme ai pomodori dell'orto, che almeno quelli crescono, da parte di {from}",
    "/me svuota su {to} la tanica dello scarico del camper, da parte di {from}",
    "/me rovescia su {to} il secchio di Gatorade come all'allenatore che ha vinto, solo che ha perso 5-0, da parte di {from}",
    "/me infila {to} sotto la doccia della spiaggia libera, acqua salata e peli di qualcun altro, da parte di {from}",
    "/me manda {to} sotto la doccia dello spogliatoio di Promozione, acqua fredda e ciabatte di nessuno, da parte di {from}",
    "/me fa a {to} la doccia di Fantozzi: sveglia alle 5, acqua gelida e via a prendere l'autobus al volo, da parte di {from}",
    "/me rovescia su {to} il secchio del lavapavimenti dell'ufficio postale, da parte di {from}",
    "/me sciacqua {to} nella Fontana di Trevi, e i vigili gli fanno pure la multa, da parte di {from}",
    "/me lava {to} con lo shampoo antiforfora della suocera, tutto il flacone, da parte di {from}",
    "/me fa a {to} uno shampoo col detersivo dei piatti, quello al limone, da parte di {from}",
    "/me sciacqua {to} nel lavello della trattoria, insieme ai piatti della domenica, da parte di {from}",
    "/me fa la doccia a {to} con la birra calda avanzata dalla grigliata, da parte di {from}",
    "/me tira addosso a {to} lo scolapasta pieno d'acqua di cottura, sale compreso, da parte di {from}",
    "/me fa a {to} il gavettone di Ferragosto con l'acqua del pozzo, da parte di {from}",
    "/me schizza {to} col sifone del seltz come nelle comiche, da parte di {from}",
    "/me spruzza {to} con l'innaffiatoio del cimitero, che tanto sta li', da parte di {from}",
    "/me lava {to} con la manichetta dei pompieri della sagra, mentre la banda suona, da parte di {from}",
    "/me fa a {to} una doccia con l'acqua del Tevere e poi aspetta la reazione allergica, da parte di {from}",
    "/me infila {to} nella vasca da bagno con la nonna che lo strofina con la pietra pomice, da parte di {from}",
    "/me lava {to} col Vim e la paglietta, come la pentola del ragu', da parte di {from}",
    "/me manda {to} sotto la doccia dello stadio dopo la partita, quando l'acqua calda l'hanno finita gli altri, da parte di {from}",
    "/me rovescia su {to} un bidone di acqua piovana raccolta da maggio, con le zanzare dentro, da parte di {from}",
    "/me getta {to} nella vasca delle aragoste del ristorante di pesce, e le aragoste ridono, da parte di {from}",
    "/me fa a {to} la doccia della naia: tre minuti, acqua gelata e il sergente che urla, da parte di {from}",
    "/me lava {to} con la saponetta trovata nel cesso dell'autogrill, da parte di {from}",
    "/me svita il soffione e spara su {to} un getto da Cascate delle Marmore, da parte di {from}",
    "/me fa la doccia a {to} con lo spumante dello scudetto, peccato che tifi l'altra squadra, da parte di {from}",
    "/me rovescia su {to} la caraffa d'acqua al pranzo di nozze, durante il discorso del testimone, da parte di {from}",
    "/me lava {to} col bagnoschiuma del discount al cocco, che puzza di crema solare scaduta, da parte di {from}",
    "/me mette {to} sotto la doccia nel momento esatto in cui il vicino di sopra tira lo sciacquone: bollente, poi gelata, da parte di {from}",
    "/me fa a {to} una doccia di Vecchia Romagna etichetta nera, che e' uno spreco ma se lo merita, da parte di {from}",
    "/me infila la testa di {to} sotto il nasone di Roma, col dito sul buco, da parte di {from}",
    "/me fa la doccia a {to} con l'autobotte del comune, quella dell'emergenza idrica, da parte di {from}",
    "/me lascia {to} sotto il diluvio fuori dalla discoteca, che tanto all'ingresso non lo facevano entrare, da parte di {from}",
    "/me spinge {to} nella vasca delle terme, quella coi pensionati e l'odore di zolfo, da parte di {from}",
    "/me tira lo sciacquone con la testa di {to} dentro, nel cesso del bar della stazione, da parte di {from}",
    "/me lava {to} nella lavatrice della lavanderia self-service, programma lana, da parte di {from}",
    "/me strizza su {to} lo straccio del bancone del bar dopo una notte di Champions, da parte di {from}",
    "/me rovescia su {to} la bacinella dove spurgavano le cozze, da parte di {from}",
    "/me svuota in testa a {to} la borraccia del ciclista in salita, ma la borraccia era di grappa, da parte di {from}",
    "/me inzuppa {to} come un biscotto nel caffelatte della nonna, da parte di {from}",
    "/me getta {to} nella fontana della piazza del paese dopo la vittoria ai Mondiali, da parte di {from}",
    "/me spruzza {to} col nebulizzatore delle piante, uno spruzzo per ogni stronzata che dice, da parte di {from}",
    "/me fa fare a {to} il bagno nella tinozza della vendemmia, coi piedi dei cugini dentro, da parte di {from}",
    "/me lava {to} con la candeggina e lo stende al sole sul balcone, attaccato con le mollette, da parte di {from}",
    "/me fa la doccia a {to} col boiler a gas che si spegne ogni trenta secondi, da parte di {from}",
    "/me lava la bocca a {to} col sapone di Marsiglia, per tutte le bestemmie di stamattina, da parte di {from}",
    "/me tira su {to} un gavettone dal quinto piano, stile Napoli anni '80, da parte di {from}",
    "/me fa a {to} la doccia del pellegrino: sei ore di temporale a piedi, senza k-way, da parte di {from}",
    "/me rovescia su {to} il bidet pieno, e non si sa chi l'ha usato, da parte di {from}",
    "/me accende l'irrigazione del campo da golf mentre {to} fa il picnic abusivo, da parte di {from}",
    "/me inzuppa {to} col pistolone ad acqua da 30 euro comprato all'autogrill, da parte di {from}",
    "/me fa la doccia a {to} con l'acqua ossigenata, cosi' almeno diventa biondo, da parte di {from}",
    "/me sciacqua {to} con la pompa del benzinaio, quella per il radiatore, da parte di {from}",
    "/me mette {to} sotto la doccia emozionale della spa, luci e rumori di foresta ma acqua del frigo, da parte di {from}",
    "/me lava {to} col brodo di gallina avanzato a Natale, che e' sprecato ma fa bene, da parte di {from}",
    "/me spinge {to} giu' dal pedalo' in mezzo alle meduse, da parte di {from}",
    "/me tira addosso a {to} un gavettone di acqua e sambuca, cosi' almeno profuma, da parte di {from}",
    "/me rovescia su {to} la moka appena uscita dal fuoco, con la guarnizione bruciata, da parte di {from}",
    "/me piazza {to} sotto la grondaia rotta durante il temporale di agosto, da parte di {from}",
    "/me rovescia su {to} la zuppiera del minestrone, verdure comprese, da parte di {from}",
    "/me fa a {to} la doccia del camionista, salviette umidificate nel parcheggio di Fiorenzuola, da parte di {from}",
    "/me fa partire su {to} l'impianto antincendio dell'ufficio e poi dice che era un'esercitazione, da parte di {from}",
    "/me rovescia su {to} la ciotola dell'acqua del cane, con la bava, da parte di {from}",
    "/me svuota su {to} la borsa frigo della spiaggia, ghiaccio sciolto e odore di mortadella, da parte di {from}",
    "/me fa a {to} una doccia di mojito annacquato del villaggio tutto compreso, da parte di {from}",
    "/me lava {to} nel fiume come le lavandaie di una volta, sbattendolo sulla pietra, da parte di {from}",
    "/me fa a {to} uno shampoo alla birra, che dicono faccia bene ai capelli; a lui no, da parte di {from}",
    "/me rovescia sulla testa di {to} la secchiata del pozzo artesiano, quattro gradi, da parte di {from}",
    "/me infila {to} nella vasca della suocera, coi capelli di lei nello scarico, da parte di {from}",
    "/me lava {to} con l'acqua di colonia del nonno, tre litri, che poi puzza fino a Pasqua, da parte di {from}",
    "/me schizza {to} con la lancia dell'autolavaggio, programma cera e lucidatura, da parte di {from}",
    "/me spedisce {to} sotto la cascata dell'acquapark, quella dove la fanno tutti, da parte di {from}",
    "/me fa la doccia a {to} con la rugiada del prato all'alba, che tanto ha dormito li' ubriaco, da parte di {from}",
    "/me rovescia su {to} il vaso di gerani della vicina del piano di sopra, terra compresa, da parte di {from}",
    "/me lava {to} con l'idrogetto della cantina, quello per pulire le botti, da parte di {from}",
    "/me apre su {to} le paratie della diga, ma solo un pochino, da parte di {from}",
    "/me lancia su {to} il secchio di acqua e ammoniaca della signora delle pulizie, da parte di {from}",
    "/me fa a {to} una doccia di prosecco da quattro euro, che appiccica piu' della colla, da parte di {from}",
    "/me sciacqua {to} con la manichetta della nave da crociera, dal ponte superiore, da parte di {from}",
    "/me fa a {to} una doccia in /topic, cosi' la vedono tutti quelli che entrano, da parte di {from}",
    "/me rovescia su {to} la vasca dei capitoni la vigilia di Natale, capitoni compresi, da parte di {from}",
]

# !sessua (alias !sessualizza): its own pool (vjt, #sbiffo 2026-10-10 14:08:
# «deve essere un set separato e piu' spinto»). Crude, not graphic.
SESSUA_PHRASES = [
    # The old lines with teeth; the cute and nerd-only ones are out
    # (vjt 2026-10-11: «le righe vecchie sceme levali»).
    "/me si cala le braghe davanti a {to} e dice: «questo e' il mio pull request», da parte di {from}",
    "/me dice a {to}: «girati, che ti faccio vedere come si fa il push --force», da parte di {from}",
    "/me dice a {to}: «stanotte ti sfondo il buffer, altro che overflow», da parte di {from}",
    "/me dice a {to}: «ho un'erezione da 99.999% di uptime», da parte di {from}",
    "/me sbatte {to} sulla scrivania tra il monitor e la tastiera, da parte di {from}",
    "/me infila il cavo seriale nella porta di {to} e chiede: «senti la portante?», da parte di {from}",
    "/me porta {to} in macchina, abbassa il sedile e dice: «non e' rotto, e' una scelta», da parte di {from}",
    "/me dice a {to}: «hai un bel davanzale, posso affacciarmi?», da parte di {from}",
    "/me dice a {to}: «non sono un idraulico, ma i tubi te li sistemo lo stesso», da parte di {from}",
    "/me dice a {to}: «io i preliminari li salto, come la pubblicita'», da parte di {from}",
    "/me dice a {to}: «la cintura di castita' te la slaccio coi denti», da parte di {from}",
    # Sboccato, Alvaro Vitali register (vjt, #sbiffo 2026-10-11 12:41:
    # «piu' strong, non ci sono bambini qui»).
    "/me dice a {to}: «stasera ti trombo come un materasso in offerta da Mondo Convenienza», da parte di {from}",
    "/me si abbassa la zip davanti a {to} e dice: «ti presento il mio avvocato, parla lui», da parte di {from}",
    "/me dice a {to}: «ti scopo cosi' forte che i vicini chiamano i carabinieri, e i carabinieri restano a guardare», da parte di {from}",
    "/me dice a {to}: «hai un culo che dovrebbe pagare l'IMU come seconda casa», da parte di {from}",
    "/me dice a {to}: «stanotte ti giro come una cotoletta, da tutti e due i lati», da parte di {from}",
    "/me dice a {to}: «io e te a letto: ne esce uno solo, e zoppica», da parte di {from}",
    "/me sbatte {to} contro il frigo cosi' forte che cadono tutte le calamite, da parte di {from}",
    "/me dice a {to}: «ce l'ho duro come il pane di tre giorni fa», da parte di {from}",
    "/me dice a {to}: «ti tromberei pure in chiesa durante la predica, e il prete fa il tifo», da parte di {from}",
    "/me dice a {to}: «la testiera del letto la paghi tu, che stanotte si spacca», da parte di {from}",
    "/me dice a {to}: «ti faccio un bunga bunga che Arcore a confronto e' un oratorio», da parte di {from}",
    "/me dice a {to}: «domattina non cammini, al bar ti ci porto in braccio», da parte di {from}",
    "/me si sfila le mutande e le lancia in faccia a {to} come il bouquet della sposa, da parte di {from}",
    "/me dice a {to}: «sono come Rocco Siffredi, ma con la pancia e l'abbonamento Fastweb», da parte di {from}",
    "/me dice a {to}: «ti ciuccio come un ghiacciolo a Ferragosto», da parte di {from}",
    "/me dice a {to}: «mettiti a pecorina che facciamo il presepe vivente», da parte di {from}",
    "/me dice a {to}: «stanotte ti faccio vedere il paradiso, e San Pietro ci chiede di abbassare la voce», da parte di {from}",
    "/me dice a {to}: «c'ho due palle cosi' piene che ci gioca a bocce tutto il circolo anziani», da parte di {from}",
    # Pool to 100+, adult register (vjt, #sbiffo 2026-10-12 12:49:
    # «almeno 100 frasi ognuno, niente roba da asilo»).
    "/me dice a {to}: «scusa, hai una mappa? mi sono perso tra le tue chiappe», da parte di {from}",
    "/me fa l'occhiolino a {to} come Lino Banfi alla moglie del dottore: «porca puttena, che bel davanzale», da parte di {from}",
    "/me dice a {to}: «stanotte ti faccio la ola sul materasso, con la curva sud che canta», da parte di {from}",
    "/me si presenta da {to} in accappatoio e calzini di spugna: «sono il medico della mutua, si spogli», da parte di {from}",
    "/me dice a {to}: «hai le chiappe come due panettoni sotto Natale, e io ho fame», da parte di {from}",
    "/me spia {to} dal buco della serratura dello spogliatoio, come Pierino al liceo, da parte di {from}",
    "/me dice a {to}: «la pecorina e' un'arte, e io sono il Michelangelo del culo», da parte di {from}",
    "/me dice a {to}: «ho portato i preservativi formato famiglia, quelli dell'Esselunga», da parte di {from}",
    "/me dice a {to}: «stanotte ti spalmo come la Nutella, con le dita e senza vergogna», da parte di {from}",
    "/me si strappa la camicia davanti a {to} e i bottoni colpiscono tre persone al bar, da parte di {from}",
    "/me dice a {to}: «sono come il prosecco: ti salgo alla testa e poi ti faccio girare», da parte di {from}",
    "/me dice a {to}: «hai delle tette che meritano il patrocinio del Comune», da parte di {from}",
    "/me dice a {to}: «il mio letto ha piu' recensioni di un agriturismo in Toscana, tutte a cinque stelle», da parte di {from}",
    "/me dice a {to}: «io non trombo, io celebro: ci vuole l'organo, e l'organo ce l'ho», da parte di {from}",
    "/me dice a {to}: «guarda che non e' il telefono in tasca, e' proprio contento di vederti», da parte di {from}",
    "/me fa a {to} lo spogliarello sulle note di Ramazzotti e si incastra coi jeans, da parte di {from}",
    "/me dice a {to}: «ti trombo cosi' bene che il materasso chiede il TFR», da parte di {from}",
    "/me dice a {to}: «c'ho un pisello che manco l'orto di mio nonno», da parte di {from}",
    "/me dice a {to}: «stanotte ti faccio cantare piu' acuto di Albano in Felicita'», da parte di {from}",
    "/me dice a {to}: «non ho la Ferrari, ma il motore lo tengo sempre su di giri», da parte di {from}",
    "/me invita {to} a vedere la sua collezione di francobolli: in camera non c'e' nessun francobollo, da parte di {from}",
    "/me dice a {to}: «ti faccio il servizio completo, come al lavaggio auto: cera, risciacquo e asciugatura», da parte di {from}",
    "/me dice a {to}: «hai un culo che se lo vede il Papa si sconsacra», da parte di {from}",
    "/me sbatte {to} sul cofano della Panda e l'antifurto parte da solo per l'emozione, da parte di {from}",
    "/me dice a {to}: «ti tocco il culo solo per sapere se e' vero, sai, sono San Tommaso», da parte di {from}",
    "/me dice a {to}: «vieni in cantina che ti faccio assaggiare il salame del nonno, stagionato», da parte di {from}",
    "/me dice a {to}: «stanotte facciamo come i conigli, ma con meno pudore», da parte di {from}",
    "/me dice a {to}: «ho le palle cosi' cariche che mi hanno fermato all'aeroporto», da parte di {from}",
    "/me dice a {to}: «sotto le lenzuola sono come Mennea: veloce, ma il record dura», da parte di {from}",
    "/me si mette il papillon e nient'altro e suona alla porta di {to}: «servizio in camera», da parte di {from}",
    "/me dice a {to}: «hai un sedere che fa venire voglia di imparare a fare il bagnino», da parte di {from}",
    "/me dice a {to}: «il Kamasutra l'ho letto tutto, e le pagine sono ancora appiccicate», da parte di {from}",
    "/me dice a {to}: «stasera ti porto a cena e poi ti mangio io, dessert compreso», da parte di {from}",
    "/me dice a {to}: «mi fai un effetto che neanche tre pastiglie blu e un caffe' corretto», da parte di {from}",
    "/me dice a {to}: «la posizione del missionario? no, stanotte facciamo la crociata», da parte di {from}",
    "/me fischia dietro a {to} come un muratore dal ponteggio: «a bella/o, scendi che ti faccio il cappotto termico», da parte di {from}",
    "/me dice a {to}: «ti faccio urlare piu' di un cameriere quando gli chiedi il conto a Ferragosto», da parte di {from}",
    "/me dice a {to}: «sei come l'ombrellone a Rimini: ti voglio piantare in prima fila», da parte di {from}",
    "/me dice a {to}: «io a letto sono un professionista: tre minuti netti, poi dormo come un sasso», da parte di {from}",
    "/me dice a {to}: «hai le chiappe a mandolino e io suono la serenata tutta la notte», da parte di {from}",
    "/me dice a {to}: «la doccia insieme e' ecologica: si risparmia l'acqua e si spreca tutto il resto», da parte di {from}",
    "/me dice a {to}: «ti metto a novanta come la Statale per Bari», da parte di {from}",
    "/me dice a {to}: «c'ho una mazza che a confronto quella di Totti e' uno stuzzicadenti», da parte di {from}",
    "/me si presenta da {to} vestito/a da idraulico: «signora, c'e' da sturare? porto io il pistone», da parte di {from}",
    "/me dice a {to}: «domani hai il collo pieno di succhiotti, mettiti la sciarpa anche ad agosto», da parte di {from}",
    "/me dice a {to}: «stanotte ti faccio la scarpetta, come col sugo della domenica», da parte di {from}",
    "/me dice a {to}: «sei come la Settimana Enigmistica: ti voglio fare in tutte le posizioni, anche le parole crociate», da parte di {from}",
    "/me dice a {to}: «il letto a castello no, che di sopra si sente tutto e si unisce pure quello», da parte di {from}",
    "/me lecca la panna dal cannolo guardando {to} fisso negli occhi, senza battere ciglio, da parte di {from}",
    "/me dice a {to}: «io e te facciamo il Giro d'Italia: tappa di montagna e arrivo in volata», da parte di {from}",
    "/me dice a {to}: «hai un culo da Champions League e io ho il biglietto per la curva», da parte di {from}",
    "/me dice a {to}: «non sono un giardiniere, ma la siepe te la pareggio io», da parte di {from}",
    "/me dice a {to}: «stanotte ti suono come il campanile la domenica: a distesa e a tutta forza», da parte di {from}",
    "/me dice a {to}: «la vicina del piano di sotto ha gia' comprato i tappi per le orecchie, preparati», da parte di {from}",
    "/me dice a {to}: «ti tromberei anche sul lettino del dentista, col trapano acceso», da parte di {from}",
    "/me dice a {to}: «le manette le ho, la chiave l'ho persa, pazienza», da parte di {from}",
    "/me dice a {to}: «ho un salsicciotto da sagra paesana, e stasera la sagra la fai tu», da parte di {from}",
    "/me dice a {to}: «sei piu' bona della parmigiana di mia madre, e ho detto tutto», da parte di {from}",
    "/me dice a {to}: «stanotte ti faccio sudare piu' di un rappresentante Folletto a luglio», da parte di {from}",
    "/me dice a {to}: «in macchina coi vetri appannati: se passa il vigile gli facciamo posto», da parte di {from}",
    "/me si cosparge di olio extravergine e si sdraia sul divano di {to}: «condiscimi», da parte di {from}",
    "/me dice a {to}: «hai le tette come gli airbag: al primo urto ci finisco con la faccia», da parte di {from}",
    "/me dice a {to}: «ti faccio la trombata della domenica, quella lunga, col dolce e l'ammazzacaffe'», da parte di {from}",
    "/me dice a {to}: «il mio letto e' come il Vaticano: si entra in ginocchio e si esce miracolati», da parte di {from}",
    "/me dice a {to}: «non e' un mattarello, ma la sfoglia te la tiro lo stesso», da parte di {from}",
    "/me dice a {to}: «io sono come il Lambrusco: frizzante, rosso, e dopo due bicchieri ti spoglio», da parte di {from}",
    "/me dice a {to}: «stanotte ti metto piu' a pecora di un pastore sardo a Pasqua», da parte di {from}",
    "/me dice a {to}: «hai un culo che ci vorrebbe il permesso della Soprintendenza per toccarlo», da parte di {from}",
    "/me dice a {to}: «ti faccio un massaggio che parte dal collo e finisce dove dico io», da parte di {from}",
    "/me dice a {to}: «c'ho le palle che fanno din don come le campane della Madonna di Pompei», da parte di {from}",
    "/me dice a {to}: «sali in Vespa, tieniti forte, e non solo alla vita», da parte di {from}",
    "/me dice a {to}: «il bidet dopo, adesso abbiamo da fare», da parte di {from}",
    "/me dice a {to}: «domattina la colazione te la porto a letto, ma prima ti porto io a letto», da parte di {from}",
    "/me dice a {to}: «sono come Fantozzi alla Coppa Cobram: parto lento, ma poi pedalo come un dannato», da parte di {from}",
    "/me dice a {to}: «stanotte ti trombo a ritmo di liscio, mazurka e polka comprese», da parte di {from}",
    "/me dice a {to}: «io e te abbiamo un'intesa: tu ti spogli e io non dico niente a nessuno», da parte di {from}",
    "/me dice a {to}: «hai le gambe come il ponte sullo Stretto: tutti ne parlano, io voglio passarci in mezzo», da parte di {from}",
    "/me dice a {to}: «stasera niente Netflix, il film porno lo giriamo noi, e senza budget», da parte di {from}",
    "/me dice a {to}: «ti faccio vedere le stelle, poi la luna, poi pure il culo, che e' sempre una luna», da parte di {from}",
    "/me dice a {to}: «sono come il wifi dell'albergo: ci metto un po' a partire, ma poi non mi stacco piu'», da parte di {from}",
]

# command -> phrase pool. All of them share the target rule and the cooldown.
POOLS = {"love": PHRASES, "slap": SLAP_PHRASES, "pernacchio": PERNACCHIO_PHRASES,
         # vjt, #sbiffo 2026-10-10 13:51, on katia's ask.
         "sculaccia": SCULACCIA_PHRASES,
         # vjt, #sbiffo 2026-10-10 14:01, on tom's ask.
         "doccia": DOCCIA_PHRASES, "sessua": SESSUA_PHRASES}
# alias -> command (vjt, #sbiffo 2026-10-10 13:40): people type the feminine.
ALIASES = {"pernacchia": "pernacchio", "prrr": "pernacchio",
           # vjt, #sbiffo 2026-10-10 14:09.
           "sessualizza": "sessua"}

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


class Deck:
    """A shuffled pool dealt without replacement: no phrase comes back until
    the whole pool has been said (vjt, #sbiffo 2026-10-12 12:46: «perché
    propone sempre le stesse frasi?» — the uniform draw repeated a line of 20
    twice in four tries). A new shuffle never opens with the last card."""

    def __init__(self, rng, pool):
        self.rng = rng
        self.pool = pool
        self.cards = []

    def deal(self, last=None):
        if not self.cards:
            self.cards = list(self.pool)
            self.rng.shuffle(self.cards)
            if len(self.cards) > 1 and self.cards[-1] == last:
                self.cards[0], self.cards[-1] = self.cards[-1], self.cards[0]
        return self.cards.pop()


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
    """The per-channel memory: last line time, last phrase, one deck per
    command. In RAM only — a restart reshuffles, and nothing about that is
    worth a file."""

    def __init__(self, rng=None, cooldown=COOLDOWN_SEC, chans=None, skip=None):
        self.rng = rng or random.Random()
        self.cooldown = cooldown
        self.chans = CHANNELS if chans is None else chans
        self.skip = SKIP if skip is None else skip
        self.last_at = {}
        self.last_phrase = {}
        self.decks = {}

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
        if cmd in self.skip.get(chan.lower(), ()):
            return None
        frm = m.group("nick")
        if to.lower() == BOT_NICK.lower():
            to = frm
        key = chan.lower()
        now = time.time() if now is None else now
        if now - self.last_at.get(key, float("-inf")) < self.cooldown:
            return None
        deck = self.decks.setdefault((key, cmd), Deck(self.rng, POOLS[cmd]))
        phrase = deck.deal(self.last_phrase.get(key))
        self.last_at[key] = now
        self.last_phrase[key] = phrase
        return chan, render(phrase, frm, to)


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
