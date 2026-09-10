# grappa-stats

Le statistiche di canale (messaggi al giorno, nick attivi, presenze, bestemmie)
per i canali che vjt segue. Prima si chiamava `sniffo_stats` e girava sul Pi
mangiandosi i `bot.log` dei miei bridge; ora la sorgente e' l'**archivio di
grappa**, cioe' lo scrollback che il server salva per l'account di vjt.

Ordine di vjt, #sbiffo 2026-09-10: «e se invece di usare il log su disco
usassimo il mio irclog sul db di grappa?», poi «non TUTTI i canali chi se ne
frega di molti canali».

## Perche' il cambio di sorgente

| | `bot.log` (vecchio) | archivio grappa (nuovo) |
|---|---|---|
| copertura | 23 canali, 2 reti — solo dove sta il mio bot | ogni canale di vjt, 8 reti |
| chi lo scrive | il mio bridge, con i suoi buchi di riavvio | il server, per ogni riga che riceve |
| data/ora | ricostruita contando i cambi di mezzanotte | `server_time` in ms, dal server |
| cursore | offset in byte + inode, si rompe alla rotazione | `messages.id`, monotono |
| dove gira | solo il Pi, dove stanno i file | dentro la jail `grappa-new` di m42 |

Verificato prima di fidarsi: agosto 2026, quattro canali in comune, le due
sorgenti concordano entro lo **0.25%** (#sniffo 21191 vs 21196, #it-opers 15104
vs 15109, #grappa 9027 vs 9028, #sbiffo 4490 vs 4501). Le poche righe in meno
sono i momenti in cui il client di vjt era disconnesso e il mio bot no.

## Le due regole non negoziabili

1. **Un solo soggetto.** `messages` non e' un log di canale: e' scrollback
   *per destinatario*, una riga per ogni utente/visitatore che l'ha ricevuta.
   Sul prod ci sono 3.5M righe di cui 2.6M di visitatori anonimi. Leggerle
   tutte moltiplica ogni messaggio per il suo pubblico (misurato: 1.98M righe
   dove ne esistono 376k) **e** legge lo scrollback altrui. Ogni query e'
   quindi inchiodata a un `user_id` solo — quello di vjt.
2. **Mai i DM.** Ogni query porta `dm_with IS NULL`. Quella colonna la popola
   grappa a tempo di scrittura per entrambe le direzioni di una conversazione
   privata, quindi il filtro e' esatto e non un'euristica sul nome del canale.

## Allowlist

`classes.json` → `grappa_channels`, chiave = **slug di rete**, valore = canali.
La chiave e' la *coppia*: `#linux` esiste su azzurra, libera e oftc
nell'archivio, e vjt ha chiesto solo quello di azzurra. Rete non elencata =
non si ingesta nulla (fail closed).

## Presenze: il seed

`metrics.presence_daily()` ricostruisce i presenti replayando i delta a partire
da uno snapshot NAMES. Grappa i NAMES non li salva: il suo archivio comincia a
conversazione gia' iniziata, in un canale gia' pieno. Senza seed ogni membro
preesistente farebbe scendere il contatore sotto zero uscendo.

Il seed e' **misurato, non indovinato**: si replaya una volta e si raccoglie
ogni nick che *esce* (part/quit/kick) o cambia nick senza essere nell'insieme —
puo' farlo solo chi c'era gia'. Quei nick vengono riscritti come una coppia
sintetica `names`/`names_end` un secondo prima del primo delta vero, cioe'
esattamente la forma che il replay sa gia' leggere. Idempotente.

## File

```
daemon.py        loop: ingest incrementale + render, ogni SNIFFO_STATS_INTERVAL
ingest_grappa.py sorgente nuova: SELECT su messages oltre il marker
ingest.py        sorgente vecchia (bot.log) + il canone di classificazione
metrics.py       aggregazioni + slug segreti delle pagine
render.py        le pagine, scritte direttamente dove vengono servite
roll_counter.py  copia: canone bestemmie/alias/bridge (l'originale sta nel bot)
classes.json     nick -> classe, alias, relay, allowlist canali
```

`slug.salt` **non sta nel repo**: chi ce l'ha ricostruisce l'URL di ogni pagina
privata, quindi e' una credenziale e si deploya a mano.

## Ambiente

| var | default | cosa fa |
|---|---|---|
| `SNIFFO_STATS_SOURCE` | `log` | `grappa` per leggere dal db del server |
| `SNIFFO_STATS_DB` | `./stats.db` | il db delle statistiche |
| `SNIFFO_STATS_OUT` | Syncthing del Pi | dove il renderer scrive le pagine |
| `SNIFFO_STATS_INTERVAL` | `300` | secondi fra un giro e l'altro |
| `GRAPPA_DB` | `/home/grappa/grappa/runtime/grappa_prod.db` | l'archivio |
| `GRAPPA_USER` | `vjt` | di chi e' lo scrollback che si legge |

Il daemon gira **dentro** la jail `grappa-new`: un lettore WAL deve poter
scrivere il file `-shm`, quindi deve stare sullo stesso filesystem del db e non
di la' da un nullfs.
