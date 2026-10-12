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
    # Cute ones with Italian and internet pop culture on top (puppy, #sbiffo
    # 2026-10-12 13:01; vjt keeps the old ones, «cringe per definizione»).
    "♥ {from} ha messo {to} come preferito su Spotify, e il blend dice 100% di affinita' ♥",
    "♪ {from} e {to}: una playlist condivisa che si chiama solo \"noi\" ♪ ღღღ",
    "✿ {from} ha dato la password di Netflix a {to}, e non la cambiera' mai ✿",
    "*:・ﾟ✧ {to}, sei il Wordle di {from}: cinque lettere, primo tentativo, tutto verde ✧ﾟ・:*",
    "(h) il gufo di Duolingo minaccia {from}, che ripassa solo per dire \"ti amo\" a {to} in 40 lingue (h)",
    "╰☆╮ {from} ti farebbe una carbonara perfetta {to}: guanciale, pecorino, zero panna ╰☆╮",
    "~<3~ {from} divide l'ultima piadina con {to}, e questo e' amore vero ~<3~",
    "♡♡♡ {to} e' l'unico BeReal che {from} non scatta mai in ritardo ♡♡♡",
    "☆ {from} ti dedica Zitti e buoni {to}, ma poi ti dice parole dolcissime ☆",
    "♪ {from} canta Tuta gold sotto la finestra di {to} ♪ (◕‿◕✿)",
    "♥ {from} ha votato {to} al televoto di Sanremo, mille volte ♥",
    "°˖✧ {from} fa la coreografia di Tuca tuca di Angelina Mango solo per {to} ✧˖°",
    "*-.,_,.-* {to}, per {from} sei meglio di una vittoria al fantasanremo *-,_,.-* :*",
    "((((( {from} ti abbraccia come Annalisa abbraccia il ritornello {to} ))))) ⊂(◕‿◕)⊃",
    "♡ {from} e {to}: piu' coppia di Mahmood e Blanco a Brividi ♡ <333",
    "@>-->-- {from} a {to}: \"si', ma a me mi piace\" --<--<@",
    "✧ {to} manda brividi a {from} come una nota alta all'Ariston ✧ ♥♥♥",
    "♪ {from} ti scrive una barra di Geolier in napoletano stretto {to}: \"si' tu\" ♪ ღღღ",
    "~*:.｡o○ {from} sogna {to} come Elodie sogna un palco tutto rosa ○o｡.:*~ ♥",
    "╰☆╮ {from} disegna {to} come un personaggio di Zerocalcare, con un armadillo che approva ╰☆╮",
    "♥ l'armadillo di Zerocalcare dice a {from}: \"dichiarati a {to}, daje\" ♥",
    "☆ {from} ti ama a cazzo di cane {to}, ma con tutto il cuore ☆ (cit. Boris)",
    "*:・ﾟ✧ {from} a {to}: \"dai dai dai!\" come Rene', ma con gli occhi a cuore ✧ﾟ・:*",
    "(h) per {from}, {to} e' piu' bello di una diretta di Mare Fuori (h)",
    "♡♡♡ {from} canta \"o mar for\" a {to}, stonando con amore ♡♡♡",
    "✿ {from} scapperebbe dal Collegio solo per incontrare {to} ✿",
    "♥ Cannavacciuolo guarda {from} e {to} e dice: \"questo piatto ha l'amore dentro\" ♥",
    "~<3~ {from} ti porta un dolce da Bake Off {to}, cuore di pan di spagna e glassa rosa ~<3~",
    "°o.O {to}, al falo' di confronto {from} sceglierebbe sempre te O.o°",
    "♪ {from} a Temptation Island urlerebbe solo il nome di {to} ♪ ღღღ",
    "☆ {from} accende la luce rossa per {to}: e' la risposta definitiva? si', e' amore ☆",
    "*:・ﾟ✧ Gerry Scotti chiede a {from}: \"accendiamo?\" -- si', il cuore per {to} ✧ﾟ・:*",
    "♥ Borghese conferma: {to} prende 10 da {from}, e niente bonus per nessun altro ♥",
    "((((( {from} e {to}: la location, il menu', il conto... tutto 10 ))))) ⊂(◕‿◕)⊃",
    "♡ {from} fa a {to} piu' imitazioni di Fiorello pur di vederti ridere ♡ <3<3",
    "✧ {from} ti dedica una canzone alla Checco Zalone {to}: stonata, ma vera ✧",
    "╰☆╮ {to} e' il posto fisso nel cuore di {from}, quo vado? da nessuna parte ╰☆╮",
    "*-.,_,.-* {from} smette quando vuole, ma con {to} non vuole smettere mai *-,_,.-* :*:*",
    "~*:.｡o○ {from} e {to} che volano sul Gatto Bus di Totoro ○o｡.:*~ ♥",
    "♥ {from} cucinerebbe per {to} come in un film Ghibli, con l'uovo che brilla ♥",
    "(h) {from} si fa spirito senza volto pur di seguire {to} (h) ✿",
    "♪ {from} regala a {to} un fiore raro dall'isola di Animal Crossing ♪ ღღღ",
    "☆ Tom Nook fa uno sconto a {from}, che ci compra una casetta per {to} ☆",
    "♡♡♡ {from} ha scelto {to}: super efficace! ♡♡♡ ʕ•ᴥ•ʔ",
    "@>-->-- un {to} selvatico appare... {from} lancia una Poke Ball fatta di cuori --<--<@",
    "°˖✧ {from} e {to}: Pikachu e Ash, inseparabili dal primo episodio ✧˖°",
    "~<3~ {from} lascia vincere {to} a Mario Kart, e non e' poco ~<3~",
    "♥ {from} tira una stella a {to} a Mario Kart, invece del guscio blu ♥",
    "*:・ﾟ✧ {from} e {to}: era il loro love story, Taylor's Version ✧ﾟ・:*",
    "✿ {to}, sei l'era preferita di {from}, quella che dura per sempre ✿ ♡",
    "♪ {from} scambia braccialetti dell'amicizia con {to}, ma ci scrive \"amore\" ♪ ღღღ",
    "((((( {from} ha visto il trend TikTok e l'ha rifatto con {to}, malissimo ma felici ))))) ⊂(◕‿◕)⊃",
    "☆ {from} manda a {to} un TikTok di gattini ogni cinque minuti ☆ =^.^=",
    "♥ Bombardiro Crocodilo sgancia cuori su {to}, pilotato da {from} ♥",
    "♡ tung tung tung sahur: e' il cuore di {from} che batte per {to} ♡ <333",
    "*-.,_,.-* {from} a {to}: \"Ballerina Cappuccina e' bella, ma tu di piu'\" *-,_,.-* :*",
    "(h) {from} scrive a {to} un messaggio tutto cuoricini: cringe, ma ok (h)",
    "✧ {from} e {to}: ce la faremo, insieme, anche al lunedi' mattina ✧",
    "╰☆╮ il gatto di casa ha scelto {to}, e {from} ha capito che era quello giusto ╰☆╮",
    "=^.^= {from} e {to} si contendono il gatto sul divano, ma alla fine dormono tutti e tre =^.^= ♥",
    "♪ {from} manda a {to} un vocale di 4 minuti che dice solo \"mi manchi\" ♪ ღღღ",
    "~*:.｡o○ {from} ha messo {to} nei preferiti delle storie Instagram ○o｡.:*~ ♥",
    "♥ {from} mette like a una foto di {to} del 2015, apposta ♥",
    "☆ {from} ha dedicato a {to} il primo posto nello Spotify Wrapped ☆ ♡♡♡",
    "*:・ﾟ✧ {to} e' l'artista piu' ascoltato da {from}, con 87.000 minuti di cuore ✧ﾟ・:*",
    "♡♡♡ {from} risponde alle storie di {to} con il fuoco, sempre ♡♡♡ 🔥",
    "✿ {from} e {to}: due spunte blu e un \"anche io\" ✿ ♥",
    "@>-->-- {from} ha fatto lo screenshot del primo messaggio di {to}, e lo tiene ancora --<--<@",
    "°o.O {from} sta scrivendo... cancella... riscrive... e alla fine manda a {to} un cuore O.o°",
    "♥ {from} e {to} hanno visto Boris tutto insieme, e ora si dicono \"smarmellato\" con amore ♥",
    "((((( {from} ti fa trovare {to} una puntata di Masterchef e un tiramisu' ))))) ⊂(◕‿◕)⊃",
    "((((( {from} prepara a {to} una serata Masterchef e un tiramisu' ))))) ⊂(◕‿◕)⊃",
    "~<3~ {from} preferisce {to} anche alla seconda stagione di qualsiasi serie Netflix ~<3~",
    "☆ {from} aspetta {to} per guardare l'ultima puntata: nessuno spoiler, solo amore ☆",
    "*-.,_,.-* {to}, {from} non skippa mai la tua intro *-,_,.-* :*:*:*",
    "✧ {from} porta {to} a mangiare il gelato al pistacchio, quello vero, verde scuro ✧",
    "(h) {from} fa le storie col filtro cuoricini, ma la protagonista e' sempre {to} (h)",
    "✧ {from} ti porta {to} a mangiare il gelato al pistacchio, quello vero, verde scuro ✧",
    "╰☆╮ {from} cede a {to} l'ultimo arancino, o arancina, basta che sia amore ╰☆╮",
    "♥ {from} e {to}: lo spritz al tramonto, e il resto non conta ♥",
    "*:・ﾟ✧ {from} ha prenotato per {to} un tavolo dove non serve la panna nella carbonara ✧ﾟ・:*",
    "♪ {from} e {to} cantano a squarciagola in macchina l'ultimo tormentone estivo ♪ ღღღ",
    "✿ {to} e' il tormentone estivo di {from}, ma dura tutto l'anno ✿",
    "~*:.｡o○ {from} e {to} al concerto dei Maneskin, con le mani a forma di cuore ○o｡.:*~ ♥",
    "♡♡♡ {from} ti dedica Coraline {to}, ma con il lieto fine ♡♡♡",
    "☆ {from} si mette lo smalto alla Damiano solo per far ridere {to} ☆ (◕‿◕✿)",
    "((((( {from} e {to}: due Funko Pop sullo stesso scaffale, per sempre ))))) ⊂(◕‿◕)⊃",
    "@>-->-- {from} a {to}: \"sei il mio comfort show\" --<--<@",
    "♥ {from} ha fatto a {to} la classifica delle serie: al primo posto ci sei tu ♥",
    "°˖✧ {from} fa il quiz di Buzzfeed per scoprire che personaggio Ghibli e' {to}: sempre Kiki ✧˖°",
    "*-.,_,.-* {from} manda a {to} un meme alle 3 di notte: e' una dichiarazione *-,_,.-* :*",
    "(h) {from} e {to} hanno il loro meme privato che nessun altro capisce (h)",
    "♪ \"quanto ti amo?\" chiede {to}. {from}: \"piu' dell'aperitivo del venerdi'\" ♪ ღღღ",
    "✧ {from} ti tagga {to} in ogni reel di coppie carine ✧ ♡♡",
    "*:・ﾟ✧ {from} ha messo {to} nella squadra Pokemon, slot numero uno ✧ﾟ・:*",
    "♡ per {from}, {to} e' la puntata speciale di Natale di ogni serie ♡ <333",
    "~<3~ {from} guarda le stelle cadenti con {to} e chiede sempre la stessa cosa ~<3~",
    "☆ {from} e {to} giocano a Mario Party, e nessuno dei due si arrabbia: miracolo d'amore ☆",
    "♥ {from} ha scritto il nome di {to} con le pietre su Animal Crossing ♥",
    "*:・ﾟ✧ {from} ti ha messo {to} nella squadra Pokemon, slot numero uno ✧ﾟ・:*",
]

PHRASES = TOOLS_PHRASES + NEW_PHRASES

SLAP_PHRASES = [
    # Rewritten whole, contemporary and kinkier: the old set read as «battute
    # da RSA» (Lucy, puppy; vjt «ascolta puppy e lucy», #sbiffo 2026-10-12 12:59).
    "/me slaps {to} around a bit with a large trout",
    "/me prende il salmone del sushi all you can eat e lo stampa in faccia a {to}, wasabi compreso",
    "/me schiaffeggia {to} con un Labubu, quello falso, il Lafufu",
    "/me molla una sberla a {to} col cavo USB-C sbagliato, quello che carica ma non trasferisce",
    "/me rifila un ceffone a {to}, colpevole di un vocale da sette minuti per dire «si'»",
    "/me piazza in faccia a {to} il boccale del Monsieur Cuisine, programma impasto, velocita' 10",
    "/me schiaffeggia {to} col Kindle aperto su un manuale di crescita personale",
    "/me tira una Switch 2 in faccia a {to}, e i Joy-Con si staccano magneticamente sul colpo",
    "/me spiaccica una pinsa romana sulla guancia di {to}, bordo croccante, idratazione 80%",
    "/me sfila il cestello dell'airfryer e lo stampa sulla guancia di {to}: 200 gradi, 12 minuti",
    "/me tira un papagno a {to}, che ha spoilerato il finale di Severance nei commenti",
    "/me da' un ceffone a {to}, crypto bro con la Lambo come sfondo e il portafoglio a -94%",
    "/me stampa cinque dita in faccia a {to}, che dice «ti mando il link» e poi il link non arriva mai",
    "/me molla a {to} uno schiaffo in reply-all, cosi' lo vede tutta l'azienda",
    "/me rifila una sberla a {to} per le dodici storie del brunch: avocado, mimosa, avocado, mimosa",
    "/me sgancia una sventola su {to}, che sfreccia col monopattino sul marciapiede",
    "/me schiaffeggia {to} che guarda i reel in treno senza cuffie, volume al massimo",
    "/me tira uno sganassone a {to}, che inizia ogni mail con «piccolo appunto»",
    "/me slaps {to} with a large trout, then posts it on BeReal with the front camera",
    "/me prende la borraccia Stanley da 1,2 litri, piena di ghiaccio, e la sbatte in faccia a {to}",
    "/me schiaffeggia {to} con una tavoletta di Dubai chocolate da 18 euro, pistacchio ovunque",
    "/me sventaglia l'astuccio degli AirPods in faccia a {to}, e gli auricolari spariscono per sempre sotto il divano",
    "/me molla un manrovescio a {to}, che nel 2026 scrive ancora «ok boomer»",
    "/me da' un ceffone a {to} col pannello solare del balcone, ancora fissato con le fascette",
    "/me tira uno sganassone a {to}, che chiede in #general su Slack «chi ha preso il mio yogurt dal frigo?»",
    "/me assesta una sberla a {to}, che risponde alle mail di lavoro alle 23 con «no rush»",
    "/me usa gli occhiali smart per filmare in verticale lo schiaffo che rifila a {to}",
    "/me tira una cinquina a {to}, che fa le call in vivavoce al supermercato",
    "/me rifila a {to} un ceffone con una carta Pokemon gradata PSA 10, custodia rigida compresa",
    "/me molla una sberla a {to}, che giura di non usare l'IA e scrive tutto con i trattini lunghi",
    "/me sbatte in fronte a {to} un Tamagotchi rimesso in vendita, morto di fame da mesi",
    "/me rovescia un poke bowl in faccia a {to}: salsa teriyaki sul naso, edamame nei capelli",
    "/me schiaffeggia {to} per l'invito all'aperitivo con «dress code: total white»",
    "/me rifila uno schiaffo a {to} col tablet delle ordinazioni del ristorante giapponese",
    "/me tira una sberla a {to}, che fa domande alla fine del meeting delle 17:58",
    "/me da' a {to} uno schiaffo cosi' forte che Strava lo registra come uscita in bici",
    "/me schiaffeggia {to} col manico del Dyson, modalita' boost",
    "/me molla un manrovescio a {to}, che scrive «Ehi» e poi sparisce per tre giorni",
    "/me rifila una sberla a {to}, che gira il telefono a meta' video perche' era verticale",
    "/me lancia il Roomba come un frisbee contro {to}",
    "/me spalma in faccia a {to} una tazza di ramen istantaneo gusto «spicy challenge»",
    "/me schiaffeggia {to}, che nel 2026 mostra ancora con orgoglio l'NFT della scimmia annoiata",
    "/me tira un ceffone a {to}, che ha chiamato il Wi-Fi di casa «FBI Surveillance Van 3»",
    "/me spiaccica sulla guancia di {to} la vaschetta del Ninja Creami, piena di gelato proteico",
    "/me prende a sberle {to}, che racconta «la mia routine mattutina» e comincia alle 4:30",
    "/me da' uno schiaffo a {to} col caricatore MagSafe, che si attacca alla guancia e non si stacca piu'",
    "/me schiaffeggia {to} che commenta «primo!» sotto i video di YouTube",
    "/me srotola il tappetino da yoga sulla faccia di {to}. Namaste",
    "/me rifila un ceffone a {to}, che incolla in PR il codice di ChatGPT senza leggerlo",
    "/me schiaffeggia {to} con la tessera della palestra mai usata, ancora nel cellophane",
    "/me tira addosso a {to} il Lego Millennium Falcon, 7541 pezzi, tutti finiti sul pavimento",
    "/me molla una sventola a {to}, che visualizza e risponde sei ore dopo con «ahah»",
    "/me schiaffeggia {to} con lo Stitch gigante pescato alla gru del centro commerciale",
    "/me rifila una sberla a {to}, che scrive «buongiorno a tutti» su Teams e aspetta le risposte",
    "/me schiaffeggia {to} con un deck di Magic, sideboard compresa",
    "/me tira un ceffone a {to} con la tastiera meccanica a switch blu, cosi' lo sentono anche i vicini",
    "/me da' uno schiaffo a {to}, che parla dei suoi side project e non ne ha mai finito uno",
    "/me schiaffeggia {to} con una Polaroid istantanea: lo sviluppo avviene direttamente sulla guancia",
    "/me sventola il pass del Comicon, cordino compreso, sulla faccia di {to}",
    "/me schiaffeggia {to}, che ascolta i podcast a 2x e ora parla a 2x pure dal vivo",
    "/me tira una sberla a {to}, che parcheggia la Tesla su due posti per paura dei graffi",
    "/me schiaffeggia {to} con una baguette del Lidl ancora calda, crosta sonora",
    "/me molla un ceffone a {to}, che manda 40 foto del gatto nel gruppo del calcetto",
    "/me rifila un manrovescio a {to}, che ha messo il paywall sulla newsletter dopo il secondo numero",
    "/me schiaffeggia {to} con un pacco Amazon vuoto, dentro solo aria e cuscinetti",
    "/me da' una sberla a {to}, che posta la corsa col Garmin: 3 km, 47 hashtag",
    "/me slaps {to} around with a large trout, the Discord edition, with a reaction emoji on top",
    "/me molla un papagno a {to}, che ha comprato la lampadina smart e ora per accendere la luce serve un aggiornamento firmware",
    "/me tira un papagno a {to}, che ha lasciato una stella su Google perche' il cameriere non ha sorriso",
    "/me rifila un ceffone a {to} con la PS5 Pro in verticale, supporto venduto a parte",
    "/me schiaffeggia {to} col telecomando della smart TV, quello col tasto Netflix che parte da solo",
    "/me molla una sberla a {to}, che convoca trenta persone per decidere la data della prossima riunione",
    "/me schiaffeggia {to} che dice «ti giro la mail» e inoltra un thread da 84 messaggi",
    "/me tira addosso a {to} un cubo di Rubik smart che si risolve da solo via Bluetooth",
    "/me da' un ceffone a {to}, che registra i vocali tenendo il telefono davanti alla bocca come un gelato",
    "/me rifila una sberla a {to}, che mette i piedi sul sedile del Frecciarossa",
    "/me schiaffeggia {to} col bastone da selfie, telescopico, estensione massima",
    "/me molla in faccia a {to} la caraffa filtrante, filtro scaduto da marzo",
    "/me stampa cinque dita in faccia a {to}, che scrive solo «hai un minuto?» e poi non dice per cosa",
    "/me tira una cinquina a {to}, che non salta mai l'intro su Netflix",
    "/me schiaffeggia {to} con un taco al pastor gocciolante, salsa verde compresa",
    "/me rifila uno schiaffo a {to}, che ha il calendario pieno di «focus time» e poi sta tutto il giorno su Reddit",
    "/me ruota il monitor ultrawide da 49 pollici e lo usa come racchetta su {to}",
    "/me schiaffeggia {to}, che ha chiamato il gatto «Kubernetes». Il gatto approva lo schiaffo",
    "/me prende a schiaffi {to} con un sacchetto di capsule Nespresso compatibili",
    "/me prende a sberle {to} con la scatola di un iPhone nuovo, che si stacca dal coperchio in quattordici secondi di suspense",
    "/me schiaffeggia {to}, che risponde a un audio con un audio che risponde a un audio",
    "/me tira un ceffone a {to} col controller dell'Xbox, vibrazione al massimo",
    "/me molla un manrovescio a {to}, che su LinkedIn si definisce «visionary, disruptor, storyteller»",
    "/me schiaffeggia {to} che fa la fila al drive-thru del McDonald's a piedi",
    "/me tira in faccia a {to} un bubble tea da un litro: perle di tapioca nelle orecchie",
    "/me schiaffeggia {to}, che scrive «LOL» senza ridere, «XD» senza scherzare e «k» senza vergogna",
    "/me rifila una sberla a {to} col tappetino del mouse XXL, quello che copre mezza scrivania",
    "/me schiaffeggia {to}, che fa partire il robot aspirapolvere durante la call",
    "/me da' una sventola a {to}, che posta citazioni motivazionali su sfondo tramonto alle 6 del mattino",
    "/me schiaffeggia {to} con un tubo di Pringles, ultimo strato irraggiungibile",
    "/me tira un ceffone a {to}, che disdice il padel delle 21:30 alle 21:25",
    "/me schiaffeggia {to} con lo smartwatch al polso, che intanto misura i battiti di tutti e due",
    "/me molla una sberla a {to}, che risponde «dipende» a tutto, perfino a «che ore sono?»",
    "/me schiaffeggia {to} con un sacchetto di caffe' specialty, tostatura chiara, note di frutti rossi",
    "/me rifila un ceffone a {to}, che nella bio di Tinder scrive «cerco gente vera»",
    "/me infila il Vision Pro e guarda in 3D lo schiaffo che parte verso {to}",
    "/me schiaffeggia {to}, che usa «sinergia» due volte nella stessa frase",
    "/me tira un papagno a {to}, che fa le storie Instagram del panorama col dito davanti all'obiettivo",
    "/me molla una sberla a {to} con la sella del bike sharing, ancora bagnata di pioggia",
]

PERNACCHIO_PHRASES = [
    # Rewritten whole, contemporary and kinkier: the old set read as «battute
    # da RSA» (Lucy, puppy; vjt «ascolta puppy e lucy», #sbiffo 2026-10-12 12:59).
    "{from} guarda il portafoglio crypto di {to}, -94% da aprile, e risponde: PRRRRRRRRT",
    "{from} legge l'hot take di {to} su X, 4 milioni di views e zero ragioni, e spara un PFFFFRRRRRT in quote tweet",
    "{from} sente {to} dire «mi sveglio alle 5 per la mia routine» e replica: BRRRRAAAAP, io alle 5 scoreggio nel sonno",
    "{from} riceve da {to} «ho aperto una partita IVA!» e fa PTHBBBBBT, l'F24 di giugno ride gia'",
    "{from} assaggia il sourdough di {to}, lievito madre di nome Gertrude, e sputa una pernacchia piena di briciole: PFRRRT",
    "{from} mette like al gym selfie di {to} e poi in commento scrive solo: PRRRRRRRRRRT 💪",
    "{from} fa uno screenshot dell'NFT di {to} e glielo rimanda con una pernacchia sopra: tecnicamente adesso e' suo. PTHBBT",
    "{from} ascolta il primo episodio del podcast di {to}, 2 ore e 40 di «no ma infatti», e risponde in mono: BRRRRRAAAP",
    "{from} legge il pitch deck della startup AI di {to}, wrapper di ChatGPT con logo viola, e fa PFFFRRRRRT da 40 milioni di valuation",
    "{from} sente {to} nominare l'ex per la nona volta stasera e lancia un PRRRRRT con eco, come la storia che si ripete",
    "{from} apre lo Spotify Wrapped di {to}: top artist lo-fi beats to cry to. Pernacchia: PTHHHHBBBBBT",
    "{from} guarda {to} che si mette la fascia da padel per il terzo set della settimana e soffia: PRRT PRRT PRRRRRT, punto al vetro",
    "{from} riceve da {to} un vocale da 6 minuti e 12 secondi, lo ascolta a 2x e risponde con un vocale da 3 secondi: PFFFRRRRT",
    "{from} commenta il post LinkedIn di {to} «sono felice di annunciare» con una pernacchia professionale: BRRRAAAP #opentowork",
    "{from} vede {to} che fa reverse di un video del barbecue e lo mette in story con la musica epica: PRRRRRT in loop",
    "{from} ascolta {to} spiegare perche' il cold plunge gli ha cambiato la vita e fa una pernacchia tiepida: PFRRRT",
    "{from} legge la bio di {to}: «founder | speaker | dreamer». Sotto aggiunge: «pernacchiato». PTHBBBT",
    "{from} riceve da {to} il link al suo corso «diventa libero finanziariamente in 30 giorni» e paga in pernacchie: PRRRRRRRRT x 30",
    "{from} guarda {to} fare unboxing di un mouse da 180 euro e soffia nel microfono: BRRRRRRAAAAP, ASMR edition",
    "{from} risponde all'ennesimo «ti mando il calendly» di {to} con uno slot libero per la sua pernacchia: PFFFFRRRRT, ore 15:00-15:01",
    "{from} trova {to} che fa la coda per il cornetto virale da TikTok e da' fiato: PRRRRRT, gusto pistacchio ovviamente",
    "{from} si becca da {to} un thread di 47 tweet «perche' nessuno parla di questo 🧵» e risponde al tweet 1/47: PTHBBBBBT",
    "{from} scopre che {to} ha comprato il dominio .ai per un'idea che non esiste ancora e pernacchia: PRRT.ai",
    "{from} legge la recensione da 1 stella di {to} al ristorante «servizio lento, ero di fretta» e gli tira un BRRRAAAP da 5 stelle",
    "{from} ascolta {to} vantarsi del suo setup home office con 3 monitor per fare 2 call al giorno: PFFFRRRRRRRRT in surround",
    "{from} ascolta {to} dire «io il caffe' lo bevo solo specialty» e fa una pernacchia nel V60: PRRRRRT, note di frutti rossi",
    "{from} riceve da {to} un «ti va un caffe' per fare networking?» e risponde: PTHBBBT, ecco il mio networking",
    "{from} vede {to} con l'Apple Vision Pro in metro e gli soffia una pernacchia in realta' aumentata: BRRRRRAAAAP",
    "{from} legge il piano di {to} «mollo tutto e apro un chiringuito a Fuerteventura» e fa PRRRRT, ci rivediamo a settembre",
    "{from} guarda {to} postare la foto dell'avocado toast con #mindfuleating e commenta con una pernacchia al guacamole: PFRRRRT",
    "{from} sente {to} dire «io faccio digital detox» mentre posta la story del digital detox e lancia un PTHHBBBT offline",
    "{from} sbircia la watchlist di {to}: 300 film salvati, visto zero. Pernacchia in streaming: PRRRRR... buffering... RRRT",
    "{from} scopre che {to} ha 14 abbonamenti attivi e ne usa uno: BRRRRRAAAP, rinnovo automatico",
    "{from} legge l'email di {to} che comincia con «come da mia precedente» e fa una pernacchia passivo-aggressiva: PFFRRT, cordiali saluti",
    "{from} ascolta {to} spiegare il suo second brain su Notion con 400 pagine vuote e pernacchia: PRRRRRT, template gratis",
    "{from} becca {to} che risponde «sto in call» da 3 ore mentre e' su Reddit e soffia: PTHBBBBT, sei in mute?",
    "{from} riceve da {to} il referral code per l'app di fitness e manda indietro il proprio: PRRRRRRRRT, 10% di sconto",
    "{from} vede {to} che compra un monopattino elettrico per fare 400 metri e da' gas: BRRRRRRRAAAAP, 25 km/h di pernacchia",
    "{from} sente {to} pronunciare «sinergia» in una call alle 9 e risponde a microfono aperto: PFFFRRRRRRT",
    "{from} guarda {to} fare la smorfia con le labbra a culo di gallina nel selfie e risponde con la stessa bocca: PRRRRT",
    "{from} legge il commento di {to} sotto un post a caso: «Interessante, cosa ne pensate?». Ecco cosa ne penso: PTHBBBBBBT",
    "{from} trova {to} che dorme col nastro sulla bocca perche' lo dice un biohacker e glielo stacca con una pernacchia: PFFRRRT",
    "{from} guarda {to} misurare il glucosio col sensore sul braccio dopo un grissino e fa BRRRAAAP, picco glicemico di pernacchia",
    "{from} riceve da {to} «ci sentiamo dopo le feste» a febbraio e risponde con la pernacchia di Capodanno: PRRRRRRRRRRT 🎆",
    "{from} sente {to} dire «non guardo serie, solo documentari» e pernacchia in 4K HDR: PTHHHHHBBBBT",
    "{from} vede {to} che prenota il ristorante solo dopo aver controllato se c'e' l'angolo instagrammabile e fa PFRRRT col flash",
    "{from} ascolta {to} parlare di dropshipping a cena e gli spedisce una pernacchia dalla Cina: PRRRRRT, arriva in 45 giorni",
    "{from} scopre che {to} ha messo una frase di Seneca nella bio di Tinder e risponde col suo stoicismo: BRRRRRAAAAP",
    "{from} guarda {to} montare la standing desk e poi usarla con lo sgabello sopra: PFFFRRRRT ergonomico",
    "{from} dedica a {to} una pernacchia in diretta Twitch, con donazione e sintesi vocale: «PRRRRRRRRRT» - 5 euro",
    "{from} legge il post di {to} «oggi il mio bambino interiore ha bisogno di coccole» e gli manda: PRRRT, coccola acustica",
    "{from} ascolta {to} spiegare il trading di opzioni su un'app con i coriandoli e fa BRRRRAAAP, coriandoli liquidati",
    "{from} becca {to} che fa una call di lavoro dalla spiaggia e si sente solo il vento, poi la pernacchia: PFFFFRRRRRRT",
    "{from} legge il «non sono razzista ma» di {to} e interrompe la frase a meta' con una pernacchia: PRRRRRRRRRRRRT",
    "{from} vede {to} che fa la foto alla carbonara con la panna e manda la pernacchia ai carabinieri: PTHBBBT",
    "{from} guarda {to} parcheggiare la Tesla su due posti per non graffiarla e gli fa una pernacchia in autopilot: BRRRRRAAAP",
    "{from} riceve un «ma l'hai letto il mio messaggio?» da {to} e risponde con la spunta blu e una pernacchia: PRRRT ✓✓",
    "{from} vede {to} in fila alle 4 di notte per l'iPhone nuovo, identico al vecchio, e fa PFFFRRRRRT a 120Hz",
    "{from} sente {to} definirsi «early adopter» del metaverso e pernacchia nel suo terreno virtuale vuoto: PRRRRRRT, eco infinita",
    "{from} legge la story motivazionale di {to} alle 6 del mattino e risponde alle 6:01: BRRRRRRAAAAP, grind",
    "{from} ascolta {to} recensire un vino naturale che sa di stalla e risponde con una pernacchia biodinamica: PTHBBBT",
    "{from} scopre {to} a fare un bootcamp di 12 settimane per diventare senior developer e fa PRRRRRT, junior pernacchia",
    "{from} guarda {to} lamentarsi del caro affitti dal suo Airbnb a Lisbona e gli soffia contro: PFFFRRRRRT, nomade digitale",
    "{from} vede {to} che si fa fare il ritratto dall'AI con sei dita e risponde con una pernacchia generata: PRRRRRRRRRT (v2)",
    "{from} sente {to} dire «io non ho la TV» e lancia la pernacchia che ha sempre sognato: BRRRRRRRRRAAAAAAP",
    "{from} risponde al PR di {to} da 4000 righe intitolato «small fix» con una review: PTHBBBBBBT, changes requested",
    "{from} vede {to} fare push --force su main il venerdi' alle 18 e soffia: PRRRRRRRRRRRT, rebase questo",
    "{from} legge il README di {to}: «Blazingly fast 🚀» e fa una pernacchia benchmark: PFRRT in 0.3ms",
    "{from} becca {to} che scrive «LGTM» senza aprire la diff e firma con una pernacchia: PTHBBT, approved",
    "{from} risponde alla riunione di {to} che poteva essere una email con una pernacchia che poteva essere una scoreggia: PRRRRT",
    "{from} guarda {to} riscrivere tutto in Rust un'app che legge un CSV e fa BRRRRAAAAP memory safe",
    "{from} riceve da {to} «ma tu hai provato a spegnere e riaccendere?» e spegne la pernacchia e la riaccende: PRRT... PRRRRRRT",
    "{from} legge la cover letter di {to} scritta da ChatGPT con «delve» tre volte e risponde: PFFFRRRRT, let's delve",
    "{from} vede {to} con l'anello Oura che controlla il punteggio del sonno al bar e pernacchia: PRRRT, readiness 12",
    "{from} assiste al gender reveal di {to} con fumogeno e incendio boschivo e contribuisce: BRRRRRAAAAP rosa e celeste",
    "{from} ascolta {to} che dice «io sono un empatico» e gli soffia una pernacchia piena di compassione: PTHHHBBBT",
    "{from} riceve da {to} la foto della sua colazione proteica: 4 albumi e tristezza. Risposta: PRRRRRRT al cornetto",
    "{from} guarda {to} comprare Labubu falsi a 80 euro e pernacchia in edizione limitata: PFFFRRRRRRT",
    "{from} scopre {to} a chiedere a ChatGPT come rispondere a un messaggio della madre e risponde lui: PRRRRT",
    "{from} legge «sto lavorando su me stesso» sul profilo di {to} da tre anni e chiede lo stato avanzamento lavori: BRRRAAAP",
    "{from} soffia in faccia a {to} una pernacchia talmente umida che il dermatologo la chiamerebbe skincare: PTHBBBBBBT",
    "{from} prende la rincorsa e scarica su {to} una pernacchia col culo, senza mani, senza vergogna: BRRRRRRRRRRAAAAAP",
    "{from} si cala i pantaloni a meta' e fa a {to} la pernacchia d'autore, quella che esce dal basso: PFFFFFFFFRRRRRT",
    "{from} chiude la call con {to} dicendo «grazie a tutti» e dimentica di mutarsi: PTHBBBBT, sentito da 40 persone",
    "{from} sente {to} dire «io ho investito in me stesso» e controlla il rendimento: PRRRRRRRRRT, -100%",
    "{from} risponde allo sfogo di {to} su BeReal con un BeReal della propria pernacchia: PFRRRRT, 2 min late",
    "{from} sorprende {to} a farsi il pre-workout per andare al supermercato e pernacchia caffeinata: BRRRRRRRRAAAAP",
    "{from} legge il messaggio di {to} nel gruppo condominiale scritto TUTTO IN MAIUSCOLO e risponde: PRRRRRRRRT (minuscolo)",
    "{from} vede {to} scrivere «Buongiornissimo» ironico ma non troppo, e pernacchia senza ironia: PTHBBBBBT",
    "{from} guarda {to} che a ogni aperitivo ordina lo spritz «ma non Aperol, Select» e fa PFFFRRRT, con l'oliva",
    "{from} trova {to} che dice di fare intermittent fasting e intanto mangia taralli: PRRRRRRRRT, finestra chiusa",
    "{from} riceve da {to} il manifesto della sua newsletter su Substack e si iscrive solo per disiscriversi: BRRRAAAP, unsubscribe",
    "{from} spara a {to} una pernacchia in Dolby Atmos, coi bassi che fanno vibrare la cassa del vicino: BRRRRRRRRAAAAAAP",
    "{from} si attacca al microfono del karaoke e dedica a {to} una pernacchia stonata in Re minore: PRRR-PRRR-PRRRRRRT",
    "{from} legge il «me lo sentivo» di {to} dopo che e' successa la cosa e risponde: PTHBBT, me lo sentivo anch'io",
    "{from} ascolta {to} spiegare che l'astrologia «in realta' e' psicologia» e fa una pernacchia con Mercurio retrogrado: PRRRRRT",
    "{from} vede {to} che si fa la foto in cucina coi muscoli e il cartone della pizza dietro e pernacchia: PFFRRRT, bulk season",
    "{from} scopre {to} che chiama «side hustle» rivendere roba su Vinted e gli fa una pernacchia senza spese di spedizione: PRRRT",
    "{from} guarda {to} che non sa piu' cucinare senza la friggitrice ad aria e soffia: PFFFFFFFRRRRRRT a 180 gradi",
    "{from} sente {to} dire «io nelle relazioni do il 110%» e calcola la pernacchia restante: PRRRRT, -10%",
    "{from} legge la mail «circling back» di {to} e gli fa girare intorno una pernacchia: PRRRT... PRRRT... PRRRRRRT",
    "{from} si gira, alza una chiappa e dedica a {to} il suo miglior pezzo di musica concreta: BRRRRRRRRRAAAAAAAAP",
    "{from} legge l'opinione di {to} sull'IA che ci rubera' il lavoro e gli fa fare la pernacchia a Claude: PTHBBBBBT, generato in 0.4s",
    "{from} ascolta {to} che si definisce «imprenditore seriale» con 7 startup fallite e gli fa la pernacchia seriale: PRRT PRRT PRRT PRRT PRRT PRRT PRRT",
    "{from} vede {to} che esce dalla palestra e si fa la doccia di profumo invece dell'acqua: PFFFRRRRRT, si sente da qui",
]

SCULACCIA_PHRASES = [
    # Rewritten whole, contemporary and kinkier: the old set read as «battute
    # da RSA» (Lucy, puppy; vjt «ascolta puppy e lucy», #sbiffo 2026-10-12 12:59).
    "/me ripassa la safeword con {to}, poi parte il paddle: «conta ad alta voce, e se sbagli si ricomincia da uno»",
    "/me sculaccia {to} col frustino del sex shop, scontrino ancora attaccato al manico",
    "/me lascia sulle chiappe di {to} un livido a forma di emoji con gli occhi a cuore",
    "/me rifila a {to} una sculacciata per aver risposto «ok.» col punto. Il punto si paga",
    "/me rifila a {to} una sculacciata per aver lasciato la chat su visualizzato da martedi' alle 14:32",
    "/me apre lo Spotify Wrapped di {to}, legge «top 0,1% ascoltatori di Ultimo» e prende il paddle",
    "/me assesta a {to} una sculacciata per ogni vocale da quattro minuti che inizia con «allora, niente, ascolta»",
    "/me usa su {to} il paddle intagliato BRAT al contrario: l'impronta sulla chiappa si legge dritta",
    "/me addomestica il brat {to}: tre sculacciate, una per ogni «e se no?»",
    "/me sculaccia {to} e poi aftercare d'ordinanza: coperta, acqua, crema lenitiva e una puntata di Bake Off",
    "/me sculaccia {to} con praise kink incluso: «brav... ehm, ottimo lavoro, persona meravigliosa»",
    "/me stampa una manata sul culo di {to}, colpevole di aver spoilerato il finale di stagione nel gruppo WhatsApp",
    "/me conta le tab aperte di {to}: 47. Una sculacciata per tab, mentre Chrome si mangia 9 giga",
    "/me legge il post LinkedIn di {to} «cosa mi ha insegnato un bambino di 3 anni sulla leadership» e sfila la cintura",
    "/me stampa cinque dita sul culo di {to} per il ghosting, ma con preavviso via calendario condiviso, perche' qui si ha rispetto",
    "/me mette a {to} un collare con la medaglietta «se mi trovi, sculacciami»",
    "/me sculaccia {to} a tempo su una traccia techno: 140 BPM, nessuna pieta'",
    "/me sottopone {to} a cinquanta sfumature di sculacciate, versione budget: cinquanta sfumature di Decathlon",
    "/me concorda la safeword con {to}: «Jira». Nessuno la dira' mai volentieri",
    "/me molla a {to} una sculacciata cosi' nitida che con l'impronta si sblocca il Touch ID",
    "/me sculaccia {to} perche' manda le foto dello schermo del PC fatte col telefono, storte e col riflesso",
    "/me prende {to} sulle ginocchia per ogni «ci sentiamo» a cui non e' mai seguito niente",
    "/me sculaccia {to} per un like a una storia del 2019 messo alle 3 di notte",
    "/me sfodera un paddle in silicone alimentare, lavabile in lavastoviglie, e lo collauda sulle chiappe di {to}",
    "/me ordina dal sex shop online il primo paddle «consegna in 24h» e lo inaugura su {to}",
    "/me fa assaggiare il paddle a {to} per ogni «ahahah» scritto con la faccia seria",
    "/me fa assaggiare il frustino a {to}, che ha condiviso la posizione in tempo reale e poi ha spento il telefono",
    "/me fa contare a {to} le sculacciate in binario: arrivati a 1010 nessuno sa piu' a che punto si era",
    "/me attacca un gioco di ruolo con {to}: «io sono le HR, tu hai fatto reply-all». Segue sanzione disciplinare",
    "/me assaggia col frustino la chiappa di {to} come un sommelier: «note di ciliegia, finale persistente, ottima struttura»",
    "/me lega {to} con le corde da shibari prese su Temu, che si sciolgono al terzo colpo",
    "/me sculaccia {to} per ogni mail che comincia con «come da mia precedente»",
    "/me molla una chiappata a {to} per ogni notifica Duolingo ignorata; il gufo verde guarda e approva",
    "/me raccoglie il consenso di {to} firmato con SPID, protocollato, e solo allora parte la sculacciata",
    "/me sculaccia {to} con la ring light accesa, perche' l'illuminazione e' tutto",
    "/me arrossa le chiappe di {to} per «io non guardo serie», detto il giorno dopo aver finito Squid Game in una notte",
    "/me usa sulle chiappe di {to} un mouse verticale ergonomico: male al culo, benissimo al polso",
    "/me sculaccia {to} e poi lascia la recensione: «chiappa reattiva, ottimo rimbalzo, cinque stelle»",
    "/me sculaccia {to} che filma il concerto in verticale per tutta la serata invece di guardarlo",
    "/me sculaccia {to} in loop come un TikTok, finche' l'algoritmo non comincia a suggerire paddle",
    "/me punisce {to}, che chiude ogni vocale con «vabbe' poi ti scrivo» e non scrive mai",
    "/me tira fuori il paddle borchiato e chiede a {to}: «verde, giallo o rosso?». Risposta: «verdissimo»",
    "/me prende a sculacciate {to} per «e' solo un gioco», detto subito dopo aver lanciato il controller contro il muro",
    "/me sculaccia {to} e poi passa il cuscino a forma di avocado, perche' l'aftercare e' sacro",
    "/me sistema {to} sul tappetino da yoga in posizione del cane a testa in giu' e sculaccia con spirito zen",
    "/me sculaccia {to}, che ha comprato i biglietti del concerto e ha scordato di prenderne uno in piu'",
    "/me rifila a {to} una sculacciata per il pigiama con le renne indossato a Ferragosto",
    "/me sculaccia {to} e mette tutto in nota spese aziendale: «attivita' di team building»",
    "/me abbassa le luci, mette su «Spanking Lo-fi Beats to Relax To» e si dedica alle chiappe di {to}",
    "/me sculaccia {to} in diretta Twitch: sub-goal raggiunto, una chiappata per ogni donazione",
    "/me sculaccia {to}, colpevole di tenere la Nutella in frigo",
    "/me arrotola il foglio di istruzioni IKEA del letto, quello che nessuno ha mai letto, e sculaccia {to}",
    "/me sculaccia {to} che ha rinominato la rete Wi-Fi di casa e ora non si collega piu' niente",
    "/me collauda sul culo di {to} la borraccia Stanley da 1,2 litri, piena, perche' l'idratazione e' importante",
    "/me stampa cinque dita sul culo di {to} per ogni «facciamo una call veloce» durata un'ora e quaranta",
    "/me chiede a {to} la safeword; risposta «ananas». Pausa, acqua, coccole, e si riparte",
    "/me fa assaggiare il paddle a {to} per i meme mandati nel gruppo di lavoro alle 23:47",
    "/me sculaccia {to} su una playlist chiamata «sexy» che in realta' e' tutta Coldplay",
    "/me usa su {to} un paddle a forma di cuore, cosi' la chiappa dice «ti amo» per tre giorni",
    "/me passa la frusta di velluto sulle chiappe di {to} e poi chiede le cinque stelle come un rider di Deliveroo",
    "/me sculaccia {to}, che giura «zero gelosia» e poi controlla le storie dell'ex su tre piattaforme",
    "/me sculaccia {to} per il terzo caricatore lasciato in hotel quest'anno",
    "/me sculaccia {to} e prende appunti su Notion: «sessione 14, chiappa sinistra piu' collaborativa»",
    "/me molla una chiappata a {to} per l'abbonamento in palestra fatto a gennaio, badge ancora intonso",
    "/me schiocca il frustino da equitazione e sussurra a {to} con accento british: «you've been a very naughty brat»",
    "/me stampa una manata sulle chiappe di {to}, che ha chiesto a ChatGPT come rispondere al messaggio di San Valentino",
    "/me apre il Wrapped di {to}: 23.000 minuti di podcast motivazionali. Sculacciata motivazionale in arrivo",
    "/me sculaccia {to} per il selfie allo specchio della palestra, luce sbagliata, addominali immaginari",
    "/me sculaccia {to} a tempo su «Espresso» di Sabrina Carpenter, colpo secco su ogni «that's that me espresso»",
    "/me tira fuori il paddle per {to}, che si definisce «una persona molto diretta» e poi scrive solo in passivo-aggressivo",
    "/me infila i guanti di pelle, schiocca le dita e sculaccia {to} come in un romance spicy di BookTok",
    "/me arrossa le chiappe di {to} per ogni «cit.» appiccicato a caso dopo una frase banale",
    "/me sculaccia {to} che al primo appuntamento di Tinder ha parlato di crypto per due ore",
    "/me porge a {to} il menu' degustazione: paddle, frustino, spatola di silicone o mano nuda. Si sceglie e si assaggia",
    "/me sculaccia {to} a mano aperta, poi soffia sulla chiappa come su un caffe' troppo caldo",
    "/me sculaccia {to} perche' la bio di Hinge dice «amo viaggiare e ridere»",
    "/me sculaccia {to} e a fine sessione consegna l'attestato: «brat domato, livello 3 su 5»",
    "/me becca {to} online dopo un «sono in modalita' aereo» e prepara il paddle",
    "/me fa assaggiare il frustino a {to}, che ha spoilerato chi muore in House of the Dragon guardando solo il trailer",
    "/me sculaccia {to}, che ordina «quello che prendi tu» e poi mangia dal piatto altrui",
    "/me prende a sculacciate {to} per gli occhiali da sole indossati in discoteca",
    "/me sculaccia {to} per la frase di Bukowski nella bio di Instagram",
    "/me prende la spatola di silicone lilla dell'IKEA e la usa su {to}, perche' anche il kink ha un budget",
    "/me stampa una manata sulle chiappe di {to}, che ha condiviso la password di Netflix con l'ex",
    "/me sculaccia {to} e poi manda un sondaggio di gradimento da 1 a 10, come Trenitalia",
    "/me sculaccia {to} con un frustino tempestato di strass, reparto addio al nubilato",
    "/me tiene {to} per il collare e sculaccia tre volte, una per ogni like messo al profilo dell'ex",
    "/me rifila a {to} una sculacciata per la cartella «Varie» con dentro 4.000 screenshot",
    "/me sculaccia {to} perche' nel sexting usa i congiuntivi giusti e la punteggiatura corretta, che freddezza",
    "/me sculaccia {to} che scrive «sto arrivando» ancora sotto la doccia",
    "/me stampa cinque dita sul culo di {to} per lo stato di WhatsApp «solo emergenze»",
    "/me sculaccia {to} con la racchetta da padel, perche' tanto ormai ce l'hanno tutti",
    "/me sculaccia {to} con metodo: riscaldamento, intensita' crescente, defaticamento e stretching",
    "/me sculaccia {to}, che tiene tutti i gruppi silenziati e poi chiede «ma quando si esce?»",
    "/me sculaccia {to} con un'agenda Moleskine, una botta per ogni buon proposito di gennaio mai iniziato",
    "/me tira fuori il paddle per {to}, che scrive i messaggi in sette righe da tre parole, notifica dopo notifica",
    "/me sculaccia {to} finche' lo smartwatch registra «allenamento HIIT, 400 kcal»",
    "/me sculaccia {to} che ha rivenduto su Vinted il regalo di compleanno",
    "/me fa assaggiare il frustino a {to}, che parla di «energie» e «vibes» come se fosse fisica quantistica",
    "/me sculaccia {to} a mano aperta: «di chi e' questa chiappa?». Risposta corretta: «tua, per i prossimi cinque minuti»",
    "/me stampa una manata sulle chiappe di {to}, che fotografa il cibo finche' si fredda per tutto il tavolo",
    "/me sculaccia {to}, che durante la cena romantica ha messo Olly a volume massimo",
    "/me sculaccia {to} e poi: «Alexa, metti un timer di dieci minuti per l'aftercare»",
    "/me sfodera un paddle in edizione limitata, autografato da una domme di OnlyFans, e lo dedica a {to}",
    "/me sculaccia {to} che risponde alle storie solo con l'emoji del fuoco, senza mai una parola",
    "/me fa assaggiare il paddle a {to} per il «ti amo» mandato nel gruppo sbagliato",
    "/me sculaccia {to} finche' la chiappa vira al rosso Netflix, logo compreso",
    "/me schiocca le dita: {to} si mette in posizione, conta ad alta voce fino a dieci e alla fine riceve un «brava persona»",
    "/me tira fuori il paddle per {to}, che manda la nota vocale e poi la trascrizione, «per comodita'»",
    "/me lascia sul culo di {to} un'impronta cosi' perfetta che ci si potrebbe fare un NFT",
]

DOCCIA_PHRASES = [
    # Rewritten whole, contemporary and kinkier: the old set read as «battute
    # da RSA» (Lucy, puppy; vjt «ascolta puppy e lucy», #sbiffo 2026-10-12 12:59).
    "/me ricicla l'ice bucket challenge del 2014 e la rovescia su {to}, nomina tre amici e scappa",
    "/me trascina {to} sotto la doccia emozionale dell'hotel: luci viola, cromoterapia, e nessuno sa come cazzo si spegne",
    "/me infila {to} sotto il rain shower da 40 cm, che a quanto pare esce solo a 4 gradi",
    "/me regala a {to} la Spa da 19 euro di Groupon: doccia fredda, accappatoio di carta, e un tizio che tossisce in sauna",
    "/me ruba l'idropulitrice al vicino in smart working e lava {to} come il dehors di un kebabbaro",
    "/me prenota a {to} un Airbnb con lo scaldabagno da 30 litri e si fa la doccia prima, tutta, con calma",
    "/me versa su {to} una caraffa di kombucha fatta in casa, con la madre del kombucha che gli si attacca ai capelli",
    "/me butta {to} nella vasca del cold plunge del guru e gli tiene giu' la testa finche' non trova il proprio scopo",
    "/me rovescia su {to} il secchio del ghiaccio dell'after, con dentro tre cicche e un Ceres morto",
    "/me spinge {to} nel fango al festival, al terzo giorno, quando il fango non e' piu' solo fango",
    "/me lascia {to} a Bali in pieno monsone, col cartello «vibes» e l'infradito che galleggia via",
    "/me mette {to} in prima fila allo spettacolo dei delfini a Gardaland: zona splash, sacchetto della spesa come impermeabile",
    "/me fa scendere {to} dal Kamikaze dell'Aquafan senza costume, solo con l'ansia",
    "/me versa addosso a {to} la vaschetta di ghiaccio del brunch, quello da 38 euro con un uovo benedict",
    "/me attacca {to} al soffione dell'Ikea montato al contrario: getto a 90 gradi diretto nelle orecchie",
    "/me accende la doccia smart di {to} da remoto via app: «Alexa, acqua gelata, livello Siberia»",
    "/me spruzza {to} col nebulizzatore da pianta del coworking, perche' ha l'aria secca e lo sguardo da monstera",
    "/me inzuppa {to} con la borraccia da due litri di acqua alcalina, quella con le frasi motivazionali a ogni tacca",
    "/me apre l'irrigatore automatico del condominio mentre {to} e' in call col cliente americano sul prato",
    "/me rovescia sulla testa di {to} un oat flat white extra caldo, poi chiede scusa al latte d'avena",
    "/me centra {to} con un gavettone pieno di Gatorade blu, come al Super Bowl, ma per aver vinto niente",
    "/me getta {to} nella piscina a sfioro del resort, quella con la vista mare e il cloro che ti scioglie le ciglia",
    "/me mette {to} sotto la doccia della palestra low cost: 3 minuti a gettone, acqua tiepida a tratti, un pelo non suo nel piatto",
    "/me fa provare a {to} il bidet giapponese dell'hotel di Tokyo, tutte le funzioni insieme, alla massima pressione",
    "/me rovescia su {to} il secchio delle bottiglie di champagne del prive', con bottiglie e sparkler inclusi",
    "/me trasforma {to} in contenuto con l'idrante della challenge di TikTok: zuppa totale, 3 milioni di views",
    "/me versa su {to} la tisana detox fredda e gli dice che e' wellness, che pure la stitichezza adesso e' un brand",
    "/me sciacqua {to} con l'idropulitrice self-service dell'autolavaggio, programma 4: cera calda e risciacquo osmotico",
    "/me lascia {to} sotto il condizionatore che gocciola sul balcone del vicino, goccia dopo goccia, tutta l'estate",
    "/me sbatte {to} sotto il rubinetto della fontanella pubblica col tappo del dito, getto da idrante dritto in faccia",
    "/me organizza a {to} una «doccia di gong» con bagno sonoro e poi gli tira addosso il secchio d'acqua del gong",
    "/me costringe {to} a fare la doccia al campeggio: ciabatte, gettone, ragno sul soffione e la fila dietro che bussa",
    "/me infila {to} in lavatrice con i calzini spaiati: ciclo eco 2 ore e 59, centrifuga a sorpresa alla fine",
    "/me rovescia su {to} la cassetta degli spritz dimenticati al sole al rooftop, con le fette d'arancia che si attaccano",
    "/me fa lo shampoo a {to} con lo shampoo solido zero waste, che non fa schiuma e lascia i capelli come un tappeto",
    "/me tira addosso a {to} il secchio d'acqua di mare del beach club, quello con la sabbia, i mozziconi e un granchio",
    "/me scarica su {to} la tanica della doccia solare da campeggio, che dopo 8 ore al sole sembra brodo",
    "/me spruzza {to} con la pistola ad acqua elettrica da 60 euro comprata «per i nipoti»",
    "/me manda {to} sotto il nebulizzatore del bar all'aperto ad agosto, quello che bagna solo te e mai il tavolo accanto",
    "/me lancia {to} nella fontana durante la festa di laurea, con la corona d'alloro e i genitori che filmano",
    "/me lava {to} nel lavello del monolocale da 900 euro al mese, perche' la doccia e' nel soppalco",
    "/me attiva gli sprinkler del capannone durante la demo della startup di {to}: «e' un pivot, ragazzi»",
    "/me versa su {to} la sangria della festa in Erasmus, quella fatta col vino in brick e frutta di tre giorni fa",
    "/me lascia cadere {to} nell'infinity pool del rooftop di Milano, tra un aperitivo da 22 euro e un DJ set di nessuno",
    "/me trova a {to} un appartamento Airbnb a Londra con doccia elettrica: getto di saliva, calda 12 secondi",
    "/me apre la cascata della doccia a cassone sopra {to} come nei video «ASMR shower routine», ma con acqua di pozzo",
    "/me schiaffa {to} sotto la cascata del sentiero di trekking da 3 ore, per la foto profilo, ghiacciata",
    "/me spara su {to} il cannone ad acqua dell'Holi Festival con dentro tutti i colori e un pezzo di plastica",
    "/me infila {to} nella vasca dei pesci che mangiano la pelle morta, e i pesci si rifiutano",
    "/me rovescia su {to} il secchio di ghiaccio al rave, in pieno drop, e la folla pensa sia parte dello show",
    "/me mette {to} sotto il getto della doccia emozionale «tempesta tropicale» della spa e poi rompe la manopola",
    "/me regala a {to} un abbonamento al cold plunge del quartiere: 9 euro a seduta, ghiaccio della pescheria",
    "/me versa su {to} il frullato proteico al gusto cookies and cream, sbattuto bene, grumi compresi",
    "/me imbocca {to} con la canna del giardino come al Fyre Festival, tende umide e panino al formaggio",
    "/me rovescia addosso a {to} la vaschetta d'acqua del cane del co-working, con dentro un tennis ball bavoso",
    "/me centra {to} con la caraffa d'acqua della call di team building, in videocamera, con 40 testimoni",
    "/me spruzza {to} col tubo del barbecue di quartiere, per spegnere anche i bollenti spiriti oltre alla griglia",
    "/me bagna {to} con il water del treno Frecciarossa, quello col pulsante che non si sa mai cosa fa",
    "/me lascia {to} fuori dalla tenda al festival dopo il temporale: lo zaino galleggia, la dignita' pure",
    "/me versa su {to} il bicchiere di ghiaccio dell'iced matcha da 7 euro, il matcha se lo beve lui",
    "/me usa {to} come test per il nuovo tubo estensibile visto su Instagram, che si estende soprattutto in faccia",
    "/me sottopone {to} alla doccia di contrasto del biohacker: 30 secondi caldo, 30 gelido, 30 urla",
    "/me inonda {to} con lo scarico del portatile caduto nel lavandino, per solidarieta' ai dati persi",
    "/me tira la catenella della doccia da campo dell'escursione in Islanda sopra {to}: acqua glaciale certificata",
    "/me mette {to} sotto l'arrosatore del campo da padel mentre aspetta il quarto, e il quarto non arriva",
    "/me versa su {to} la cassa di birra artigianale IPA tiepida, note agrumate e retrogusto di vendetta",
    "/me rovescia su {to} il secchio del lavapavimenti del locale alle 3 di notte, con lo straccio e le uova del brunch",
    "/me spinge {to} nella piscina della festa in villa in abito da cerimonia, mentre la sposa urla",
    "/me fa la doccia a {to} con lo spray del sushi bar, quello per tenere fresco il salmone",
    "/me abbassa la leva del secchio rovesciato del parco acquatico e lascia {to} sotto 1000 litri di schiaffo",
    "/me sigilla {to} nella doccia a vapore dell'hotel e la imposta su «hammam turco» per 45 minuti",
    "/me fa provare a {to} la Smart Shower a cartuccia di vitamina C: odora di arance e sa di sconfitta",
    "/me usa la canna dei pompieri della festa della schiuma e copre {to} fino alle orecchie",
    "/me riempie di schiuma {to} al foam party di Ibiza e poi scopre che non era schiuma",
    "/me spinge {to} nel Naviglio durante la Design Week, tra un'installazione e una sedia da 4000 euro",
    "/me apre il rubinetto della doccia dello studentato su {to}: due fili d'acqua, uno caldo e uno marrone",
    "/me lascia {to} nella doccia dell'ostello con la porta che non chiude, sei backpacker e un australiano senza vestiti",
    "/me spruzza {to} col vaporizzatore per stirare della startup tessile, «perche' non e' abbastanza smooth»",
    "/me versa su {to} un bicchiere dell'open bar, poi un altro, poi capisce che e' un modo di finire l'open bar",
    "/me fa cadere su {to} la tanica d'acqua del frigorifero dell'ufficio, quella da 18 litri, bloop bloop bloop",
    "/me sciacqua {to} col getto di cola zero della challenge Mentos, versione deluxe con 12 Mentos",
    "/me getta {to} nel lago di montagna dopo la sauna finlandese, senza costume, con la webcam del rifugio che riprende",
    "/me mette {to} sotto la doccia d'emergenza del laboratorio, quella gialla, e la tira per 15 minuti",
    "/me usa l'idropulitrice del lavaggio camion sull'auto a noleggio con {to} ancora dentro col finestrino abbassato",
    "/me rovescia su {to} un mestolo di brodo di ossa da 14 euro, perche' tanto e' skincare anche quello",
    "/me apre i rubinetti della vasca idromassaggio dell'Airbnb e ci butta {to} con 4 bombe da bagno glitterate",
    "/me fa la doccia a {to} con l'innaffiatoio dei bonsai del CEO, goccia per goccia, in silenzio, per un'ora",
    "/me pianta {to} sotto la grondaia rotta della villetta in affitto per tutta la durata di un temporale estivo",
    "/me intinge {to} nella fontana del centro commerciale con le monetine, e ne riemerge con 3 euro e 40 in tasca",
    "/me lascia {to} sotto il doccino del lavandino del parrucchiere hipster: acqua bollente e playlist indie",
    "/me inaffia {to} con gli spruzzatori del giardino verticale del bistrot fusion, insieme alla menta",
    "/me versa sul MacBook di {to} un vaso di cold brew e poi gli fa la doccia col resto, per coerenza",
    "/me scarica su {to} la cassetta del water che perdeva da tre mesi, che il padrone di casa non ha mai riparato",
    "/me inzuppa {to} col fango della Spartan Race, prima pozza, poi filo spinato, poi medaglia di partecipazione",
    "/me organizza a {to} un battesimo nel cold plunge col guru, che legge Marco Aurelio mentre l'ipotermia avanza",
    "/me prende la bottiglia magnum del podio della gara di kart e la spruzza tutta su {to}, ultima posizione in classifica",
    "/me lava {to} nel lavandino del bagno del treno regionale, quello col sapone che e' solo acqua blu",
    "/me getta {to} sotto la cascata artificiale dell'outlet, quella con le monete e i bambini che fanno pipi'",
    "/me riempie a {to} le scarpe da running in carbonio con l'acqua del laghetto, chilometro 32 della maratona",
    "/me versa su {to} il secchiello di ghiaccio del bar dell'hotel, cubetti dello spessore di un'opinione su X",
    "/me ribalta su {to} la piscina gonfiabile di Decathlon del balcone, insieme al vicino che ci stava dentro",
    "/me mette {to} nel water flussometro dell'autogrill e tira, a mano, tre volte, per sicurezza",
    "/me spara su {to} l'acqua della macchinetta di caffe' dell'ufficio, perche' tanto e' acqua calda con la speranza",
    "/me consegna {to} al monsone di Bangkok con l'ombrello del tuk-tuk, che e' fatto di un sacchetto",
    "/me annaffia {to} con la doccia aerea dell'irrigazione dei vigneti in Franciacorta, insieme al Pinot nero",
]

# !sessua (alias !sessualizza): its own pool (vjt, #sbiffo 2026-10-10 14:08:
# «deve essere un set separato e piu' spinto»). Crude, not graphic.
SESSUA_PHRASES = [
    # Rewritten whole, contemporary and kinkier: the old set read as «battute
    # da RSA» (Lucy, puppy; vjt «ascolta puppy e lucy», #sbiffo 2026-10-12 12:59).
    "/me lega i polsi di {to} alla testiera e sussurra: «la safeword e' \"kubernetes\", tanto nessuno la dice mai volentieri»",
    "/me manda a {to} un vocale di quattro minuti alle 2 di notte: tre di respiro affannato, uno di «dormi?»",
    "/me tira fuori il frustino davanti a {to}: «prima regola del club: si ringrazia dopo ogni colpo»",
    "/me mette il collare a {to}, aggancia il guinzaglio e ordina: «a cuccia, e niente scodinzolare finche' non lo dico io»",
    "/me benda {to} e mormora: «adesso indovina con cosa ti tocco. suggerimento: non e' il telecomando»",
    "/me fa colare cera di candela sulla schiena di {to}: «e' quella al biscotto, cosi' il dolore profuma di Natale»",
    "/me guarda {to} con un ghigno: «modalita' brat attiva? perfetto, faccio il domatore e ho tutta la notte»",
    "/me dopo la sessione avvolge {to} in una coperta con acqua e cioccolato: «aftercare incluso, la recensione a cinque stelle pure»",
    "/me sussurra a {to}: «ottimo lavoro, sei la cosa piu' obbediente che abbia mai legato a un letto»",
    "/me sculaccia {to} con consenso firmato in triplice copia: «una per te, una per me, una per il commercialista»",
    "/me propone a {to} un gioco di ruolo: «io il sistemista, tu il server in produzione, e stanotte niente change freeze»",
    "/me accende da remoto il giocattolo di {to} durante la call su Teams, come d'accordo: «hai il microfono acceso, eh»",
    "/me fa swipe a destra su {to} con tutte e due le mani: «la tua bio dice \"amo viaggiare\". io viaggio tra le tue cosce»",
    "/me risponde al prompt Hinge di {to}: «il modo piu' veloce per conquistarmi? legami al letto e fammi aspettare»",
    "/me apre Grindr, vede {to} a 200 metri e scrive: «ciao. foto? posizione? safeword?»",
    "/me si abbona all'OnlyFans di {to}: «mi rovino, ma il tier da 50 euro col frustino lo voglio»",
    "/me manda a {to} un nude con la luce giusta, l'angolo giusto e la pancia trattenuta da quaranta minuti",
    "/me riceve un nude da {to} e lo salva in una cartella cifrata AES-256 chiamata \"fatture 2019\"",
    "/me spiega a {to}: «non siamo una coppia, non siamo amici, siamo una situationship con una gran collezione di manette»",
    "/me ghosta {to} per tre settimane e poi riappare con: «ehi, ho trovato il tuo plug sotto il mio divano»",
    "/me scrive a {to} alle 2:47: «dormi? io no, e ho le mani libere»",
    "/me elenca le red flag di {to}: «dorme senza niente addosso, ha un cassetto di giocattoli, sa dieci nodi shibari. ah no, sono green flag»",
    "/me si prende l'ick perche' {to} dice \"le parti basse\", poi se lo fa passare a colpi di lingua",
    "/me confessa a {to}: «sono delulu, lo so. ma nella mia testa ti sto gia' sculacciando in quattro posizioni diverse»",
    "/me fissa il culo di {to} e dichiara: «main character energy. il resto del cast e' comparsa»",
    "/me tira fuori le manette pelose rosa: «lo so che fanno cringe, {to}, ma reggono fino a 80 chili»",
    "/me lega {to} e ci attacca un post-it: «aggiornamento in corso, non spegnere, non disturbare»",
    "/me fa sesso telefonico con {to} ma il 5G cade sul piu' bello: «...vengo... vengo... mi senti?... vengo...»",
    "/me ricapitola i ruoli a {to}: «io dom, tu sub, e il vicino del piano di sotto spettatore involontario»",
    "/me negozia con {to} un contratto di dominazione di 14 pagine, poi non trova piu' la chiave delle manette",
    "/me chiede a {to}: «semaforo: verde, giallo o rosso?» e la risposta e' «verde, e accelera»",
    "/me mostra a {to} la cronologia Amazon: «il \"spesso comprati insieme\" ormai e' un manuale BDSM»",
    "/me si schiarisce la voce davanti a {to}: «Alexa, luci basse, Nine Inch Nails, e prenota l'aftercare per le 3»",
    "/me si inginocchia davanti a {to}: «permesso di parlare? no? va bene, la bocca la uso per altro»",
    "/me valuta il culo di {to}: «merita un thread su Reddit, con award e moderatori in lacrime»",
    "/me offre a {to} un pompino con recensione Google: cinque stelle, risposta del titolare e foto del locale",
    "/me sfila la cintura davanti a {to}, la piega in due e la fa schioccare: «no, non sto uscendo»",
    "/me spiega a {to}: «il mio love language e' \"ginocchia sbucciate sul parquet\"»",
    "/me legge la bio di {to}: «\"no ONS\"? tranquillita', ti lascio in manette per due notti»",
    "/me manda a {to} un sexting cosi' spinto che l'autocorrettore chiede la safeword",
    "/me tiene {to} sul bordo per mezz'ora e poi scuote la testa: «no. non ancora. chiedilo meglio»",
    "/me promette a {to}: «edging per quaranta minuti, poi vieni come un deploy di venerdi' alle 18: tutto insieme e senza rollback»",
    "/me sculaccia {to} a ritmo di metronomo: «120 bpm, techno berlinese, e niente pause per bere»",
    "/me ringhia a {to}: «ti scopo cosi' bene che il tuo ex lo vede su Strava»",
    "/me guarda {to} e scrive su Notion: «To do: 1) cena 2) manette 3) aftercare 4) cancellare la cronologia»",
    "/me scrive a {to} «ti sto pensando», poi manda la foto del frustino con la didascalia «anche lui»",
    "/me chiarisce con {to}: «non cerco niente di serio, cerco qualcosa di legato. letteralmente, al letto»",
    "/me prende {to} per il collare e sussurra: «dimmi chi comanda. piu' forte. il vicino non ha sentito»",
    "/me si fa legare da {to} e dopo trenta secondi chiede: «possiamo mettere in pausa? mi prude il naso»",
    "/me fa il brat con {to}: «non lo faccio» «sculacciata» «non lo faccio» «sculacciata» «...continuo a non farlo, eh»",
    "/me apre a {to} il cassetto dei giocattoli: «e' ordinato per misura, colore e senso di colpa»",
    "/me annuncia a {to}: «ho comprato una sex swing su Temu. se cede, il ricovero lo facciamo in camera doppia»",
    "/me fa un giro di shibari su {to} seguendo un tutorial su YouTube: «aspetta, metto a 0.75x»",
    "/me si presenta a {to}: «sono switch: comando io, poi comandi tu, poi comanda chi ha ancora fiato»",
    "/me tiene {to} sulle ginocchia: «hai fatto tutto quello che ho detto, quindi ti meriti il premio. il premio sono io, senza vestiti»",
    "/me rivela a {to}: «i tuoi nudes li ho stampati in A3 e appesi in camera, la mia psicologa dice che e' un progresso»",
    "/me regala a {to} un plug con la coda da volpe: «adesso scodinzola e chiedi per favore»",
    "/me scrive a {to} «WYD?» e alla risposta «niente» manda la foto delle manette: «adesso qualcosa»",
    "/me si siede di fronte a {to}: «parliamo di consenso, limiti e safeword. poi parliamo di quanto forte»",
    "/me lega {to} con le cravatte da ufficio: «finalmente servono a qualcosa»",
    "/me confronta i profili con {to}: «tu foto in montagna col cane, io foto col frustino e il gatto che giudica»",
    "/me giura a {to}: «stanotte vieni piu' volte di quante notifiche Slack ricevo il lunedi' mattina»",
    "/me lascia a {to} una recensione su Glassdoor: «ottimo ambiente, dirigenza esigente, sculacciate puntuali, aftercare eccellente»",
    "/me benda {to}, toglie le manette, le rimette: «scusa, stavo controllando il carrello»",
    "/me fa notare a {to}: «sono le 2 di notte, quindi questo messaggio non conta, e nemmeno la foto delle mie tette»",
    "/me spiega a {to}: «la terapeuta dice basta persone emotivamente indisponibili. tu infatti sei solo fisicamente disponibile, perfetto»",
    "/me spinge {to} contro la porta della doccia: «il box e' in garanzia, i tuoi gemiti no»",
    "/me afferra {to} per i capelli, chiede «troppo?», sente «di piu'» e va a prendere la frusta",
    "/me offre il menu a {to}: «versione sub: regole, punizioni, premi. versione coccole: colazione a letto. niente brunch, mai»",
    "/me mostra a {to} l'app del sonno: «guarda, quel picco alle 3:12 sei tu»",
    "/me rassicura {to}: «non ho red flag, ho una red room. luce soffusa e tappetino lavabile»",
    "/me consegna a {to} l'ovetto vibrante per la cena aziendale, come pattuito: «il telecomando resta a me. buon appetito»",
    "/me avverte {to}: «quando dico \"ci prendiamo un caffe'\" intendo \"ci prendiamo le manette\"»",
    "/me riempie {to} di cosi' tanta praise che a fine sessione arriva la promozione a senior",
    "/me lecca la cera dal petto di {to}: «era cera alimentare, vero? VERO?»",
    "/me propone a {to} un gioco di ruolo: «tu fai l'auditor ISO, io la non conformita' da punire severamente»",
    "/me guarda {to} negli occhi: «voglio sapere tutto di te. tipo la taglia del collare»",
    "/me dichiara a {to}: «sei la mia red flag preferita, rossa come il tuo culo dopo dieci sculacciate»",
    "/me ammette con {to}: «il tuo OnlyFans ha piu' iscritti del mio canale YouTube, ma il frustino vero ce l'ho io»",
    "/me fa colare la cera su {to} e parte il rilevatore di fumo: «ok, questo non era nel contratto»",
    "/me condivide con {to} il calendario: «martedi' scopata, mercoledi' scopata, giovedi' aftercare, venerdi' recupero»",
    "/me porta {to} al guinzaglio con lo smartwatch al polso: «stasera diecimila passi, a quattro zampe»",
    "/me ordina a {to}: «in ginocchio. no, non a cercare il caricatore»",
    "/me chiede a {to}, nell'ordine: consenso, limiti, safeword e password del wifi",
    "/me usa il culo di {to} come sfondo del desktop e spegne la condivisione schermo con riflessi da ninja",
    "/me si mette il collare e porge il guinzaglio a {to}: «stasera mi porti a spasso tu. ma niente parchetto, si vede tutto»",
    "/me fa notare a {to}: «sei cosi' hot che Tinder mi ha passato al Platinum da solo, per pieta'»",
    "/me riscrive a {to} dopo sei mesi di silenzio: «sono una persona emotivamente matura. comunque: dormi?»",
    "/me guarda {to} impugnare il frustino: «slay. adesso usalo, che la posa per Instagram l'hai gia' fatta»",
    "/me propone a {to}: «io ti scopo, tu mi scopi, nessuno pubblica una storia, nessuno cambia la bio»",
    "/me insegna a {to}: «il bondage e' come cablare un rack: nodi ordinati, niente tensione eccessiva, un'etichetta su ogni cosa»",
    "/me promette a {to}: «domattina ti sveglio con la lingua. e non per parlare»",
    "/me manda a {to} il link di un sexy shop: «ho messo due cose nel carrello: una per te, una per me, una per tutti e due. si', sono tre»",
    "/me controlla lo smartwatch di {to}: «140 battiti. bene, adesso ti tolgo tutto il resto»",
    "/me spiega a {to}: «niente sesso tantrico. sesso tecnico: lo facciamo, lo documentiamo, lo rifacciamo perche' la doc era sbagliata»",
    "/me tira fuori un paddle di legno con le iniziali di {to}: «personalizzato su Etsy, tre settimane di attesa, ne valeva la pena»",
    "/me osserva il culo di {to}: «e' il tipo di culo che ti fa firmare un NDA prima di toccarlo»",
    "/me premia {to}: «obbedisci cosi' bene che ti meriti la ricompensa: la mia faccia tra le tue cosce»",
    "/me mette in scena per {to} un dramma erotico: luci basse, manette, e un monologo di dieci minuti su quanto fa impazzire",
    "/me scuote la testa davanti a {to}: «situationship? no grazie. voglio una sub-ationship»",
    "/me fa trovare a {to} il letto con quattro corde ai quattro angoli e un biglietto: «hai detto \"sorprendimi\"»",
    "/me tira il guinzaglio di {to}: «la gelosia non mi interessa. l'unica cosa che voglio possedere e' questo»",
    "/me organizza la serata con {to}: «io porto le corde, tu porti il culo, il vino lo porta chi perde a sasso-carta-forbice»",
    "/me avvisa {to}: «i miei preliminari durano quanto un aggiornamento di Windows: lunghi, imprevedibili, e a un certo punto riavvio tutto»",
    "/me apre un ticket su Jira per {to}: «PRIORITY: blocker. DESCRIZIONE: non ti ho ancora sculacciato oggi»",
    "/me scrive a {to}: «sei la mia notifica preferita. infatti in ufficio ti tengo silenziata, per sicurezza»",
    "/me sibila a {to}: «baciami. poi mordimi. poi chiedimi scusa. poi rifallo»",
    "/me annuncia a {to}: «venire insieme e' sopravvalutato. venire a turno col cronometro e' il futuro»",
    "/me rilascia per {to} la patch notes della serata: «aggiunto supporto manette, risolto bug del reggiseno che non si slaccia»",
    "/me prenota per {to} una stanza con lo specchio sul soffitto: «voglio vederti dall'alto, come un drone con le cattive intenzioni»",
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
