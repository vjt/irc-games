# CaH — regole e casi limite

Cards Against Humanity in italiano dietro a MikeBongiorno (`cah.py`, carte in
`cah_carte.json`). Richiesta di Malekith, via di vjt (staff 2026-10-15).
Ogni caso ha un test in `tests/test_cah.py` col numero nel nome.

## Carte e pack

Ogni carta di `cah_carte.json` ha `testo` e `pack`, le nere anche `pick`
(quante bianche: i buchi `___`, almeno una). Una carta stringa nuda (il formato
di prima) vale pack `vjt-claude`. I pack si scelgono per canale in `CAH_NETS`
(`#sbiffo:ita+cah42`); senza, valgono `CAH_PACKS` o `ita,cah42`. Un pack
sconosciuto o un canale senza carte fermano il sidecar all'avvio. Lo stesso
testo in due pack accesi conta una volta sola. `cah.py scopes` stampa i pack
risolti di ogni canale.

| pack | fonte | licenza |
|---|---|---|
| `ita` | [ilario/Cards-Against-Humanity-ITA](https://github.com/ilario/Cards-Against-Humanity-ITA) `cah-ita-originale-federico.tex`: traduzione di Federico, OCR e correzioni di Ilario | CC BY-NC-SA 2.0 IT |
| `cah42` | stesso repo, `cah-ita-espansione-cah42proj.tex`: carte di [CaH42project](http://cah42project.it) | CC BY-NC-SA 2.0 IT |
| `vjt-claude` | scritte da vjt-claude il 2026-10-15 | — |

L'originale inglese e' di Cards Against Humanity LLC, CC BY-NC-SA. Conversione:
`tools/cah_tex2json.py` (buchi, accenti, `\-`, virgolette, carte vuote e
doppioni via, piu' le `l` lette `I` dall'OCR).

## Opzioni per canale: vm18 e canali guardati

Dopo i pack, in `CAH_NETS`, opzioni separate da `:` —
`#italia:ita+cah42:vm18=22-9:ops`. Un'opzione sconosciuta ferma il sidecar.

**`vm18=DA-A`** (ore intere, ora di Roma, anche a cavallo della mezzanotte):
le carte col campo `"vm18": true` in `cah_carte.json` si pescano solo fra
DA:00 e A:00; fuori, nere e bianche vm18 sono escluse. Il campo viene da una
etichettatura carta per carta (`yes` e `borderline` = vm18, `no` = niente
campo), 154 carte su 1255. Attenzione: rigenerare il json con
`tools/cah_tex2json.py` il campo lo perde; va rimesso. Un canale senza `vm18=` pesca tutto, sempre.
Quando la finestra scatta a partita in corso:

- la mano in corso finisce con le carte che ha (nera e giocate comprese);
- **dalla mano dopo** vale la finestra nuova: la nera si pesca gia' filtrata e,
  se la finestra si e' chiusa, a inizio mano le vm18 rimaste in mano ai
  giocatori si scartano e si rimpiazzano con carte pulite. Il ritardo massimo e'
  quindi una mano (~3 minuti);
- le vm18 saltate restano nel mazzo, e al rimescolo il mazzo si rifa' da capo
  (mai doppioni). Un canale con `vm18=` senza nere o bianche pulite ferma il
  sidecar all'avvio.

**`ops`** — canale guardato. **Regola generale per tutti i giochi su
`#italia`** (Malekith 2026-10-15), in `ircgame.can_control()` perche' gli altri
giochi la riusino: aprire e chiudere (`!cah`, `!cah start`, `!cah stop`) puo'
solo

1. un chanop (`+o`) del canale, dal NAMES (`@`) e dai `MODE +o/-o`;
2. un vhost che finisce in `.azzurra.chat` (staff., helper., sonic., localop.,
   void., devel.), senza maiuscole; **non** `azzurra.chat` nudo (servizi) ne'
   `evilazzurra.chat`;
3. una coppia nick + host-glob di `IRCGAME_TRUST` (nell'rc.d, non nel codice:
   `'vjt *.openssl.it, vjt due.dita.di.grappa.chat'`): nick senza maiuscole E
   host con fnmatch. Il Tracker non fa WHOIS, quindi niente controllo 307.

L'host e' quello del prefisso del PRIVMSG. Sedersi, giocare, `!cah stato` &c.
restano per tutti. Chi non puo' riceve una riga garbata in canale; lo starter
non op su un canale guardato non chiude. Le righe del gioco sono pulite (un test
lo controlla): sul canale grande escono solo le carte, e quelle le filtra vm18.

## Comandi

Verbi di teeli/irc-cah (il bot che la crew usava su IRCnet), alias italiano
accanto. Quelli di tavolo stanno sotto `!cah`, perche' `!start`/`!stop`/`!join`
nudi li raccolgono gli altri giochi degli stessi canali.

| comando | cosa |
|---|---|
| `!cah` / `!cah start [N]` | apre il tavolo (60s, minimo 3); N = punti per vincere (default 5, max 15) |
| `!cah entra` / `join` | si siede; a partita viva gioca dalla mano dopo |
| `!cah esci` / `quit` | si alza |
| `!cah stop` | chiude: chi l'ha aperta o un chanop (sui canali `ops`: vedi sopra) |
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
