# irc-games

I giochi da canale che girano su Azzurra e IRCnet: la caccia alle suore e
l'impiccato. Repo privato, ordine di vjt (#sbiffo 2026-09-09 23:36: «per il
codice crea un nuovo repo privato irc-games»).

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

## Deployment

Jail `convento` su m42, `/opt/convento`. Un vhost per bot, via AAAA + PTR:

| bot            | rete           | canali                      | vhost                                  |
|----------------|----------------|-----------------------------|----------------------------------------|
| `Convento`     | IRCnet         | `#cybernet`                 | `sparo.alle.suore.dioschifoso.it`      |
| `Convento`     | Azzurra        | `#sniffo`, `#sbiffo`        | idem                                   |
| `MikeBongiorno`| Azzurra        | `#sniffo`, `#italia`, `#sbiffo` | `ti.faccio.le.domande.mentre.sniffo.org` |

Su Azzurra il vhost è l'rDNS vero, non un grant NickServ: basta AAAA + PTR
coerenti (vjt, #sbiffo 23:38 «non serve nickserv basta che fai un cazzo di AAAA
e PTR / un host vero»). Il PTR si mette con `netcup-cli`: il reverse del /32 è
di netcup, non c'è delega.

`bot.trust` di questi bot è **vuoto**: non prendono ordini da nessuno, sparano
suore e fanno domande.
