# Caccia alle suore — spec misurata su `rex` (#2600, EFnet)

Ordine di vjt, #sbiffo 2026-09-06 02:06:

> prendi dal log di #2600 su efnet il comportamento del bot "rex" e ricostruisci il gioco
> delle papere con le stesse semantiche. fai un nuovo sidecar e però accetti anche !bang e
> !bef oltre a .bang e .bef. i messaggi in italiano, abilitalo solo su #sniffo

Emendamento alle 02:10-02:11, dopo la proposta di peluche («e fai che anziché papere siano
suore»): **«SI SI LA SUORA»**, ripetuto **«SI SI SUORE»**. Quindi la meccanica è quella
delle papere di rex, la fauna è un'altra.

## Provenienza dei dati

Non è dedotto dalla documentazione di nessun plugin: è **misurato** sullo scrollback di
`grappa` prod (`grappa:/home/grappa/grappa/runtime/grappa_prod.db`, rete `efnet`,
canale `#2600`), 1034 righe di `rex` fra il 2026-08-22 e oggi. Le righe citate qui sotto
sono verbatim dal db.

## Meccanica misurata

### Comparsa

Una riga sola, arte ASCII + richiamo:

    ・゜゜・。。・゜ ゜\_O< QUACK!
    ・゜゜ ・。。・゜゜\_0< quack!
    ・゜゜・。 。・゜゜\_ø< FLAP FLAP!

Tre cose, tutte volute:

1. **La testa cambia** a ogni comparsa: `O`, `0`, `o`, `ø`, `ö`, `ó`.
2. **Il richiamo cambia**: `QUACK!`, `quack!`, `FLAP FLAP!`.
3. **Dentro la riga ci sono spazi a larghezza zero** (`U+200B`) piazzati a caso, sia
   nell'arte sia dentro la parola del richiamo (`QU​ACK!` sono due pezzi). Serve a rompere
   il copia-incolla: chi copia la riga e la rimanda si porta dietro i caratteri non
   stampabili. vjt, #sniffo 02:07: «così ti prendi pure i non-printing chars».

### Cadenza

131 comparse misurate. Intervallo fra una e l'altra: **mediana 180 minuti**, quartili
59 / 180 / 278, coda fino a 20 ore. Non è un timer fisso: è un'estrazione a caso su una
finestra larga.

### La suora aspetta

Distinzione dettata da vjt (#sniffo 01:56-01:57): il DuckHunt di Rizon fa scappare la
papera dopo due minuti, **rex no** — la sua aspetta che qualcuno spari. Misurato: uccisioni
a `651.941`, `923.825` e `162.149` secondi dalla comparsa. Quindi qui la suora **non se ne
va**, e il tempo che finisce nell'annuncio è tutto il tempo passato dalla comparsa.

### `.bang`

| situazione | risposta di rex |
|---|---|
| c'è la papera | `m you shot a duck in 4.417 seconds! You have killed 15 ducks in #2600.` |
| non c'è | `(Odyss3us) There is no duck! What are you shooting at?` |
| inceppamento | `(m) Your gun jammed! You can try again in 7 seconds.` |

Il singolare è rispettato: `You have killed 1 duck in #2600.`

### `.bef`

| situazione | risposta di rex |
|---|---|
| c'è la papera | `APic you befriended a duck in 11.574 seconds! You have made friends with 15 ducks in #2600.` |
| non c'è | `(sum) You tried befriending a non-existent duck. That's freaking creepy.` |
| rifiuto | `(APic) Who knew ducks could be so picky? You can try again in 7 seconds.` |
| rifiuto (variante) | `(eraser) The duck said no, maybe bribe it with some pizza? Ducks love pizza don't they? You can try again in 7 seconds.` |

### Fallimento e penalità

Inceppamento e rifiuto **non consumano la papera**: resta lì. Chi ha fallito prende
**7 secondi di penalità**, e nella finestra i suoi tentativi sono ignorati in silenzio —
misurato il 08-24 04:01: `.bang` alle :24 (inceppato), `.bang` alle :28 **nessuna
risposta**, `.bang` alle :31 uccisione.

Frequenza dei fallimenti sul campione (piccolo, dichiarato tale): 3 inceppamenti su ~23
colpi con papera presente, 3 rifiuti su ~10 tentativi di amicizia. Implementati come
15% e 30%, parametrici.

### Statistiche

    .ducks    -> maj has killed 37 ducks and befriended 36 ducks in #2600.
    .killers  -> (Invizibul) Duck killer scores in #2600: **invizibul**: 164 • **nme**: 76 • ... (troncata con "...")

Il nome nella classifica è in **grassetto** e minuscolo, i punteggi separati da `•`, e la
riga viene troncata: rex non manda mai un muro di testo.

## Cosa cambia da noi

* **Suore, non papere** (ordine di vjt).
* **Italiano.**
* **`!bang` e `!bef` oltre a `.bang` e `.bef`** (ordine di vjt). Idem per le statistiche.
* **`pew` e `pewpew` sono alias di `bang`** (ordine di vjt, #sbiffo 2026-09-17 11:10), col
  punto come col botto: `.pew`, `!pew`, `.pewpew`, `!pewpew`.
* **Solo su #sniffo** (ordine di vjt). Il canale è parametrico, il default è quello.
* Il conteggio è **per nick e per canale**, come rex. Nessun host, nessun ident, nessun
  IP nello stato: solo il nick, come in tutti gli altri sidecar.

## Cosa NON è stato replicato

`.pun`, `.soup`, i titoli delle URL e il resto del repertorio di rex: quello è un altro
bot, non il gioco.

## La suora dorata (aggiunta nostra, NON è di rex)

Chiesta da peluche (#sbiffo 02:21 «ma c'è la suora dorata?») e approvata da vjt alle 02:22
(«ma si dai», poi «effettivamente la papera d'oro è di duckhunt»). Nel log misurato di rex
**non compare mai**: è dichiaratamente un'aggiunta, non una semantica ricostruita.

* Compare con probabilità `SUORE_GOLDEN` (default 8%), arte e richiamo suoi.
* **Regge da 3 a 6 colpi.** Ogni `.bang`/`.bef` riuscito le toglie una vita e lo annuncia;
  solo l'ultimo la porta via e conta come presa. Vale anche per l'amicizia, altrimenti
  `.bef` sarebbe la scorciatoia che annulla il senso della cosa.
* Chi la colpisce senza finirla **non prende penalità**: ha fatto la sua parte.
* Il punteggio tiene un contatore `golden` a parte, mostrato nella riga finale.

**Niente colore**: vjt, 02:22, «togliamo il colore». E comunque non passerebbe — misurato
`MODE #sniffo` → `+tnrcCl`: quel `c` fa cadere le righe colorate. Resta il grassetto, che
`+c` non tocca. Il `\x0F` di chiusura è obbligatorio o il grassetto cola nello scrollback.
