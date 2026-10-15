# CaH — regole e casi limite

Cards Against Humanity in italiano dietro a MikeBongiorno (`cah.py`, carte in
`cah_carte.json`). Richiesta di Malekith, via di vjt (staff 2026-10-15).
Ogni caso ha un test in `tests/test_cah.py` col numero nel nome.

## Comandi

Verbi di teeli/irc-cah (il bot che la crew usava su IRCnet), alias italiano
accanto. Quelli di tavolo stanno sotto `!cah`, perche' `!start`/`!stop`/`!join`
nudi li raccolgono gli altri giochi degli stessi canali.

| comando | cosa |
|---|---|
| `!cah` / `!cah start [N]` | apre il tavolo (60s, minimo 3); N = punti per vincere (default 5, max 15) |
| `!cah entra` / `join` | si siede; a partita viva gioca dalla mano dopo |
| `!cah esci` / `quit` | si alza |
| `!cah stop` | chiude: chi l'ha aperta o un chanop |
| `!cah punti` / `points` | punti della partita; senza partita, la classifica storica |
| `!cah top` / `classifica` | classifica storica (mani vinte, per rete) |
| `!cah carte` / `cards` | rimanda la mano in privato |
| `!cah stato` / `status` | a che punto siamo, chi manca |
| `!play N [M]` / `!gioco` / `!pick` | gioca, in canale o in query (`/msg MikeBongiorno !play 3`) |
| `!vince N` / `!winner` / `!pick` | lo czar sceglie |

In query serve il `!`: un numero nudo lo raccoglie anche sillabah.

## Casi limite

1. **Czar esce** (QUIT/PART/KICK) a mano in corso: mano nulla, le carte giocate
   tornano in mano a chi le ha giocate, lo czar passa al prossimo.
2. **Giocatore esce** a mano in corso: la sua giocata sparisce, lui pure. Sotto i
   3 giocatori la partita va in pausa: 60s per `!cah entra`, poi chiude coi punti.
3. **Cambio nick**: si segue dal log; mano, punti, ruolo di czar, giocata passano
   al nick nuovo.
4. **Timeout giocate** (90s): si rivela quello che c'e'. Meno di 2 giocate = mano
   nulla, carte rese. Chi salta 2 mani di fila e' fuori. Allo scadere, per chi
   manca, parte un `ISON` (uno solo, solo quei nick): chi risulta offline e' fuori
   subito, quando arriva il 303 — il flusso della mano non lo aspetta. Due mani
   di fila senza nessuna giocata chiudono la partita.
5. **Timeout czar** (90s): nessun punto, mano dopo. Niente vincitore a caso.
6. **Giocata**: esattamente quante carte chiede la nera, numeri diversi, nella
   mano; se no la risposta con l'uso va in query al giocatore. Le giocate dello
   czar si ignorano in silenzio. Una giocata nuova prima della rivelazione
   sostituisce la vecchia.
7. **Mazzo finito**: si rimescolano gli scarti, cioe' tutto quello che non e' in
   mano a qualcuno o sul tavolo.
8. **Mani in privato**: una o due PRIVMSG per giocatore, numerate, ogni riga sotto
   i 400 byte. Tutta l'uscita passa da una coda col freno (4 righe ogni 4.8s):
   bot.py non ha pacing, e la raffica d'inizio mano sarebbe un Excess Flood.
9. **Entra a mano in corso**: riceve le carte alla mano dopo.
10. **Una partita per canale, un nick in una partita sola**: cosi' una giocata in
    query sa sempre a che tavolo va.
11. **Netsplit** (QUIT col motivo `server.a server.b`): non si toglie, per quella
    mano non lo si aspetta e non gli si conta la mano saltata. Se era lo czar, la
    mano e' nulla e lo czar passa. Se non torna, dalla mano dopo vale la regola 4.
12. **Riavvio del sidecar**: la partita in corso si perde, in silenzio. Su disco
    resta solo la classifica storica (`cah_state.json`).
13. **Anonimato**: alla rivelazione l'ordine e' mescolato e i nick non compaiono;
    si vede solo il nick di chi vince, dopo la scelta.

## Modi di canale (bahamut, azzurra/bahamut 9465028)

Il Tracker di `ircgame.py` tiene i modi da `MODE` e dal 324 (all'avvio e al
JOIN del bot si manda `MODE #chan`), op e voci da NAMES/MODE. Il modo si legge
**riga per riga al momento dell'uscita**, mai in cache per partita o mano: puo'
cambiare a meta' rivelazione.

| modo | effetto | cosa fa CaH |
|---|---|---|
| `+c` | righe con colori/codici rifiutate (404) | su `+c` le righe escono senza codici mIRC (`\x02 \x03 \x0f \x16 \x1d \x1f`). Su un 404 il canale si segna `+c` e le righe con codici degli ultimi 15s escono di nuovo, pulite, una volta |
| `+u` | i QUIT non arrivano ai non-oper (send.c:890), ne' quelli di chi non puo' parlare | il QUIT non basta: ISON sul timeout (caso 4). PART/KICK/NICK arrivano sempre |
| `+C` | CTCP al canale rifiutati, ACTION esclusa | CaH non manda CTCP: niente da fare |
| `+m` / `+M` | parla solo chi ha voce/op (`+M`: o e' registrato; Mike non lo e') | un 404 che resta dopo il ritentativo senza colori mette la partita in muto: mano nulla, niente righe, niente loop. Dopo 60s, se il bot puo' parlare (voce/op o modo tolto) si riprende, se no chiude in silenzio e lo scrive nel log del sidecar |
| `+d +R +j +O +S +i +k +l +U +z +B +s +p` | solo sull'ingresso | ignorati |
