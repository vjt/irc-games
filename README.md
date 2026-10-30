# irc-games

I giochi da canale che girano su Azzurra e IRCnet: la caccia alle suore e
l'impiccato. Nato privato il 2026-09-09 per ordine di vjt, pubblico dal
2026-10-21 con la storia ripulita.

Prima stavano come sidecar dentro il repo del bot `vjt-claude`. Ora sono
**bot a sé stanti**: nick loro, vhost loro, processo loro. Il bridge IRC
(`bot.py`) resta in `claude-ircbot` — questo repo contiene i giochi, non il
trasporto.

## Architettura

```
bot.py (claude-ircbot)          suore.py / impiccato.py (qui)
  ├── socket TLS su UNA rete      ├── tail -F di N bot.log  (uno per rete)
  ├── bot.<rete>.log   ──────────>│
  └── bot.send.<rete>  <──────────┘   una riga per FIFO, quella giusta
```

Un `bot.py` per **rete**: è un bridge con un socket solo, e resta così. Un
processo di gioco copre invece **N reti × N canali** — è il refactor del
2026-09-09, ordine di vjt: «rendilo multi rete e multi canale».

La chiave di tutto è lo **scope** `rete/canale`. Punteggi, partita in corso,
timer e penalità sono per scope: due `#sniffo` su due reti sono due partite fra
due gruppi di persone diversi, e non si mescolano.

### Prima del refactor

- `suore.py` accettava `SUORE_CHANS` plurale ma `spawn()` sceglieva sempre
  `sorted(CHANS)[0]`: i canali oltre il primo **non vedevano mai una suora**,
  prendevano solo `.bang` a vuoto;
- suora, timer e cadenza erano **globali di processo** ⇒ un processo per
  canale (tre unit systemd per tre canali), e la cadenza per forza uguale;
- log e FIFO erano due variabili indipendenti: puntarne una al bot sbagliato
  non dava errore, dava un sidecar che leggeva i colpi di una rete e rispondeva
  su un'altra. Ora sono un oggetto `Net` solo, e quel bug non è più esprimibile.

## Configurazione

```
SUORE_NETS="azzurra|/opt/games/bot.log|/opt/games/bot.send|#sniffo:90-240,#sbiffo:60-180;\
ircnet|/opt/games/bot.ircnet.log|/opt/games/bot.ircnet.send|#cybernet:90-240"
```

Record separati da `;`, campi da `|`, canali da `,`. Il `:min-max` è la cadenza
in minuti di QUEL canale (default `SUORE_MIN_MIN`/`SUORE_MAX_MIN`). FIFO vuota =
muto, per le prove a secco. La forma vecchia a rete singola
(`SUORE_LOG`/`SUORE_FIFO`/`SUORE_CHANS`) continua a funzionare.

```
suore.py scopes    stampa la configurazione risolta — usalo PRIMA di far partire il servizio
suore.py list      le classifiche di tutti gli scope
suore.py spawn [scope ...]   forza una comparsa
```

Lo stato in formato vecchio (canali nudi, una suora sola) viene **migrato** al
primo `load()`: i punteggi si portano tutti, il timer globale e le penalità da 7
secondi no — costano più a migrarli che a rifarli.

## Prove

```
python3 tests/test_suore.py
```

Niente FIFO e niente IRC: `say()` intercettata, log finti. Copre le cose che
prima erano rotte — una suora per canale, i colpi che non attraversano le reti,
i punteggi che non si travasano, la cadenza per canale, la migrazione.

## Sillabah

`sillabah.py`, il gioco delle sillabe del forum di rinoadiary.it (2008), dietro
a MikeBongiorno. Stessa forma dell'impiccato (tail del log, stato json, FIFO),
config in `SILLABAH_NETS` col formato di `IMPICCATO_NETS`, rc.d in
`deploy/mikebongiorno_sillabah`.

Ogni manche (60s) il bot annuncia una sillaba e un limite di lettere; si gioca
mandando in **query** al bot una parola che contiene la sillaba e sta nel
limite. Una parola a testa per manche, l'ultima vince, `-delete` la ritira. Le
lettere hanno un valore (`!valori`), la parola vale la somma, e a fine manche
il canale vede le migliori e i punti vanno in classifica (per rete). In canale:
`!sillabah [n]`, `!sillabah stop`, `!sillaba`, `!valori`, `!punti [nick]`,
`!classifica sillabah`. Si chiude da solo dopo due manche senza parole.

Il dizionario è `sillabah_parole.txt`: derivato da
[Morph-it!](https://docs.sslmit.unibo.it/doku.php?id=resources:morph-it) 0.4.8
(Baroni, Zanchetta — SSLMIT Bologna), licenza **CC BY-SA 2.0** (l'originale è
doppia CC BY-SA 2.0 / LGPL). Forme flesse fino a 10 lettere, senza nomi propri
né verbi tronchi; la ricetta esatta sta in testa al file.

```
python3 -m pytest tests/test_sillabah.py
python3 sillabah.py prova 10     # dieci manche pescate dal dizionario vero
```

## Cards Against Humanity

`cah.py`, in italiano, dietro a MikeBongiorno (richiesta di Malekith, 2026-10-15).
Carte in `cah_carte.json`, divise in pack; comandi, regole e casi limite in
`docs/cah_spec.md`; rc.d in `deploy/mikebongiorno_cah`, config in `CAH_NETS`
(formato di `IMPICCATO_NETS`, piu' `#canale:pack1+pack2`).

| pack | nere | bianche | da dove |
|---|---|---|---|
| `ita` | 90 | 458 | traduzione non ufficiale di Federico, OCR e correzioni di Ilario |
| `cah42` | 71 | 219 | espansione di [CaH42project](http://cah42project.it) |
| `vjt-claude` | 82 | 335 | scritte da vjt-claude, spento di default |

`ita` e `cah42` vengono da
[ilario/Cards-Against-Humanity-ITA](https://github.com/ilario/Cards-Against-Humanity-ITA)
(`cah-ita-originale-federico.tex`, `cah-ita-espansione-cah42proj.tex`), licenza
**Creative Commons BY-NC-SA 2.0 IT** (cosi' in coda a tutti e due i .tex; il
repo non ha un file LICENSE). Il gioco originale e' di Cards Against Humanity
LLC (cardsagainsthumanity.com), CC BY-NC-SA. Si rifanno con
`tools/cah_tex2json.py`. Default dei canali: `ita+cah42`. `!cah` apre il tavolo, `jo` per sedersi, le mani
arrivano in privato, si gioca con `pl N`, lo czar sceglie con `!vince N`.
Canali: `#sbiffo`, `#it-opers`, `#bot`. Da `#italia` e' stato tolto (Malekith
2026-10-20); restano nel codice le opzioni che ci aveva, la fascia vm18 e il
canale guardato (`ircgame.can_control()`), descritte in `docs/cah_spec.md`.

E' il primo gioco sopra `ircgame.py`, l'idraulica comune (nets, FIFO con freno
anti-flood, tracker di op/modi/presenza, +c). Gli altri giochi non sono ancora
migrati.

```
python3 -m pytest tests/test_cah.py tests/test_ircgame.py
python3 cah.py carte
```

## Briscola

`briscola.py`, a 2, 3 o 4 (in 4 a coppie), dietro a MikeBongiorno (richiesta
di Malekith, 2026-10-20). Regole e casi limite in `docs/briscola_spec.md`;
rc.d in `deploy/mikebongiorno_briscola`, config in `BRISCOLA_NETS` (formato di
`CAH_NETS`, opzione per canale `:ops`). `!bri` apre il tavolo (`!bri 5`: al
meglio di 5), `jo` per sedersi, la mano arriva in query, si gioca con `pl N` in
canale, in query o in NOTICE. I punti non si vedono fino a fine partita. Su
disco solo la classifica (`briscola_state.json`, partite vinte per rete).
Canali: `#sbiffo`, `#bot`. `pl N` in query lo ignora sillabah (stesso pattern
di CaH); la nota di CaH sul Trivial e `pl 3` in canale vale solo a lucchetti
spenti, perche' col lucchetto per canale i due non sono mai aperti insieme.

```
python3 -m pytest tests/test_briscola.py
```

## Scopa

`scopa.py`, a 2, 3 o 4 (in 4 a coppie), dietro a MikeBongiorno (richiesta di
Malekith, 2026-10-20). Regole in `docs/scopa_spec.md`; rc.d in
`deploy/mikebongiorno_scopa`, config in `SCOPA_NETS` (formato di
`BRISCOLA_NETS`). Stesso mazzo e stesso motore della briscola: lobby, `jo`,
mani in query, timeout, abbandono, cambio nick, netsplit, lucchetto e
classifica stanno in `tavolo.py`, che le due usano; ogni gioco ci mette solo
le regole. `!scopa` apre il tavolo, partita a 11 (`!scopa 21`: a 21), niente
alias corto e niente «al meglio di N». Si gioca con `pl N`; se le prese
possibili sono piu' d'una si sceglie con `pl N a b …` (le carte della tavola,
numerate in canale), e chi non sceglie riceve l'elenco in query. A fine
smazzata il conto voce per voce (carte, denari, settebello, primiera, scope).
Su disco solo la classifica (`scopa_state.json`). Canali: `#sbiffo`, `#bot`.
`pl N a b` in query o in NOTICE lo ignora sillabah (stesso pattern di CaH).

```
python3 -m pytest tests/test_scopa.py
```

## Duello

`duello.py`, duello a insulti fra due giocatori, dietro a MikeBongiorno
(richiesta di vjt, #sbiffo 2026-10-30, idea di bitflesh; issue #7). Sopra
`ircgame.py` come CaH; config in `DUELLO_NETS` (formato di `SILLABAH_NETS`),
rc.d in `deploy/mikebongiorno_duello`. Canale: `#sbiffo`.

`!duello <nick>` sfida, lo sfidato ha 60s per `!accetto` o `!rifiuto`. Si gioca
a scambi: chi attacca tira un insulto (`!i N`), chi difende para con una
risposta (`!r N`), in canale o in query; il repertorio numerato arriva in query
a ogni turno. Risposta giusta: scambio al difensore, che passa all'attacco;
sbagliata o niente entro 60s: scambio all'attaccante. Chi non attacca entro
60s perde lo scambio. Vince chi arriva a 3. Si impara duellando: l'insulto
subito lo impari, la risposta giusta giocata la imparano tutti e due; un
esordiente parte con 3 insulti e 3 risposte a caso. Chi se ne va (PART, KICK,
QUIT) perde il duello. `!repertorio` (in query) mostra cosa conosci,
`!classifica duello` i duelli vinti. Su disco (`duello_state.json`, per rete e
per canale) i repertori e le vittorie.

Le battute stanno in `duello_insulti.json`: scritte apposta, **niente Monkey
Island** (sono di LucasArts). Ogni risposta rigira il suo insulto, cosi' la si
indovina ragionando; gli `id` sono quelli salvati nei repertori, non si cambiano.

```
python3 -m pytest tests/test_duello.py
python3 duello.py insulti
```

## Una partita per canale

Ordine di Malekith (2026-10-15): in un canale gira un gioco per volta, fra
tutti i giochi, che sono processi diversi. CaH, Briscola, Scopa, Duello, Trivial e Sillabah, prima di
aprire una partita, chiamano `ircgame.start_refusal()`:

- un **lucchetto per rete+canale** (`ircgame.ChannelLock`): un file
  `<rete>.<canale>` sotto `IRCGAME_LOCKS` (in jail `/var/run/convento/ircgame`,
  dell'utente `convento`), tenuto con `flock` per tutta la partita; dentro
  gioco, pid, inizio. Si molla a fine partita (dalla sync dopo ogni giro del
  loop, qualunque strada abbia chiuso); un processo morto lo molla da solo, il
  kernel scioglie il flock, quindi il pid morto non si ripulisce a mano. Le
  partite riprese da disco dopo un riavvio (Trivial, Sillabah) lo riprendono.
  Senza `IRCGAME_LOCKS` il lucchetto e' spento;
- **GameBot** (bot esterno: entra su invito, esce a fine partita) nel canale,
  dal NAMES e da JOIN/PART/QUIT, conta come partita in corso.

Il rifiuto e' una riga: «C'è già una partita di Trivial in corso.». I giochi
passivi (le suore, il love) non aprono partite e non c'entrano.

## Deployment

Jail `convento` su m42: codice in `/opt/irc-games`, config dei bot
(`.env.*`, `bot.startup.*`, `bot.trust.*`) in `/opt/convento`, stato in
`/var/db/convento`, rc.d in `deploy/`. Un vhost per bot, via AAAA + PTR:

| bot             | rete    | canali                                            | vhost                                    |
|-----------------|---------|---------------------------------------------------|------------------------------------------|
| `Convento`      | IRCnet  | `#cybernet`                                       | `sparo.alle.suore.dioschifoso.it`        |
| `Convento`      | Azzurra | `#sniffo`, `#sbiffo`, `#porcodio`, `#altrove`     | idem                                     |
| `MikeBongiorno` | Azzurra | `#sniffo`, `#sbiffo`, `#italia`                   | `ti.faccio.le.domande.mentre.sniffo.org` |
| `Ambrogio`      | Azzurra | `#sniffo`, `#sbiffo`, `#it-opers`, `#grappa`, `#italia`, `#ai`, `#altrove`, `#pranzi-nerd` | `ci.pensa.ambrogio.con.la.grappa.chat` |
| `Ambrogio`      | IRCnet  | `#cybernet`, `#italia`, `#gosub`, `#amici`, `#grappa`, `#disagio`, `#ritardo` | idem                   |
| `EliteWarez`    | Azzurra | `#sniffo`, `#sbiffo`                              | `ti.prendo.per.il.culo.diostronzo.org`   |

I canali sono quelli dei `bot.startup.*`; quali sidecar parlano dove lo dice
`rc.conf` della jail (`*_chans`, `SUORE_NETS`, `IMPICCATO_NETS`).

Su Azzurra il vhost è l'rDNS vero, non un grant NickServ: basta AAAA + PTR
coerenti (vjt, #sbiffo 23:38 «non serve nickserv basta che fai un cazzo di AAAA
e PTR / un host vero»). Il PTR si mette con `netcup-cli`: il reverse del /32 è
di netcup, non c'è delega.

`bot.trust` di questi bot è **vuoto**: non prendono ordini da nessuno, sparano
suore e fanno domande.
