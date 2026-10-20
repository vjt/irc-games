# Briscola — regole e casi limite

Briscola dietro a MikeBongiorno (`briscola.py`, stesso sidecar degli altri
giochi). Richiesta di Malekith, staff 2026-10-20; domande chiuse su #bot lo
stesso giorno. Ogni caso limite ha un test in
`tests/test_briscola.py` col numero nel nome.

## Mazzo e punti

40 carte italiane, quattro semi: **denari, coppe, spade, bastoni**. Per seme:
Asso, 2, 3, 4, 5, 6, 7, Fante, Cavallo, Re.

| carta | punti | forza (alta → bassa) |
|---|---|---|
| Asso | 11 | 1 |
| 3 | 10 | 2 |
| Re | 4 | 3 |
| Cavallo | 3 | 4 |
| Fante | 2 | 5 |
| 7, 6, 5, 4, 2 | 0 | 6 … 10 |

Totale 120. Vince chi fa **piu' di 60**; 60 pari e' patta.

## Giocatori

- **2**: testa a testa.
- **4**: coppie tirate a sorte, sedute alternate (A1 B1 A2 B2): i compagni
  non giocano mai uno dopo l'altro. I punti sono di coppia.
- **3**: ognuno per se'. Si scopre prima la briscola dal mazzo intero, poi si
  toglie il **2 di coppe** (il **2 di denari** se coppe e' briscola: le briscole
  restano sempre 10, Malekith 2026-10-20), poi si distribuisce. 39 carte, 3 a
  testa, 30 da pescare = 10 giri. Vince chi fa piu' punti (su 120); parita' in testa = patta.
- **5+**: il tavolo si chiude a 4, il `jo` del quinto e' rifiutato con una riga.
  Niente panchina: alla partita dopo rientra da solo con `jo`.
- **1** a tavolo chiuso: non si parte.

## Svolgimento

1. Si mescola, 3 carte a testa. La carta dopo si scopre: il suo seme e' la
   **briscola** e resta sotto il mazzo, ultima da pescare.
2. Il primo di mano (a sorte alla prima mano, poi chi ha preso) gioca una carta;
   gli altri seguono in ordine. **Nessun obbligo di rispondere al seme.**
3. Prende la briscola piu' alta; se non ce ne sono, la piu' alta del seme
   d'uscita. Le carte di altri semi non prendono mai.
4. Chi prende pesca per primo, poi gli altri in ordine, ed esce alla mano dopo.
   L'ultimo a pescare prende la briscola scoperta.
5. Finito il mazzo si giocano le carte rimaste senza pescare. Poi si contano i
   punti.

Conti: in 2, 34 carte da pescare = 17 giri; in 4, 28 = 7 giri. Nessuno resta a
mani diverse.

## Comandi

Stesso schema di CaH, sotto `!briscola` (alias `!bri`), perche' i verbi nudi
li raccolgono gli altri giochi.

| comando | cosa |
|---|---|
| `!briscola` / `!bri` | apre il tavolo (60s), partita secca |
| `!briscola N` / `!bri N` | al meglio di N partite (N dispari, 3…9): finisce appena qualcuno ne vince (N+1)/2. N pari: non parte, riga «N deve essere dispari». In 3, se una partita e' patta non conta e si rigioca |
| `jo` | si siede (riga intera, solo a tavolo aperto). Max 4 |
| `!bri esci` | si alza (a partita viva = abbandono, caso 2) |
| `!bri stop` | chiude: chi l'ha aperta o chi passa `ircgame.can_control()` |
| `pl N` | gioca la carta N della mano, in canale, in query o in NOTICE (come CaH) |
| `!bri carte` | rimanda la mano in privato |
| `!bri stato` | briscola, carte nel mazzo, a chi tocca, partite vinte nella serie. **Mai** i punti presi nella partita in corso |
| `!bri top` | classifica storica (partite vinte, per rete) |

## In canale e in privato

- **In canale**: la briscola all'inizio, ogni carta giocata col nick, chi prende
  (senza i punti: si contano a mente), quante carte restano. A fine partita, in
  coda, il conto dei punti di ognuno (o di coppia) e le prese.
- **In query**: la mano numerata dopo ogni pesca (una riga sotto i 400 byte),
  es. `Mano: 1) Asso di spade  2) Re di coppe  3) 7 di denari — briscola: coppe`.
- Uscita dalla coda col freno di `ircgame.Outbox`, come CaH.
- Righe pulite: nessuna bestemmia, il gioco gira anche su `#italia`.

## Casi limite

1. **Timeout di turno** (90s): per lui gioca il bot la carta che vale meno
   (a parita', la piu' debole; mai una briscola se ne ha un'altra). Due timeout
   di fila = abbandono (caso 2).
2. **Abbandono** (QUIT/PART/KICK, `!bri esci`, doppio timeout): la partita
   finisce. In 2 vince l'altro; in 4 vince la coppia avversaria. In classifica
   conta come vittoria solo se l'abbandono arriva dopo il primo giro.
3. **Cambio nick**: mano, posto e turno passano al nick nuovo.
4. **Netsplit** (QUIT `server.a server.b`): non e' abbandono subito; il turno
   aspetta il timeout normale, poi caso 1.
5. **Giocata fuori turno**: ignorata, riga d'uso in query. Numero fuori mano:
   idem.
6. **Una partita per canale, un nick in una partita sola** (`ChannelLock`): una
   giocata in query sa a che tavolo va. Un canale con CaH aperto rifiuta la
   briscola e viceversa (`busy_line`).
7. **Riavvio del sidecar**: la partita si perde in silenzio; su disco resta solo
   `briscola_state.json` con la classifica.
8. **Modi di canale**: stessa tabella di CaH (`+c`, `+u`, `+m/+M`).

## Deciso (Malekith 2026-10-20)

- In 3 si gioca, senza un 2. Il quinto: rifiutato, niente panchina.
- Partita secca o al meglio di N dispari, a comando.
- I punti restano a mente, il conto esce in coda alla partita.
- **Carte passate in query fra compagni** (Malekith 2026-10-20): non si possono
  impedire, nessun bot vede le query fra due nick. Come i segni al tavolo vero:
  si gioca con chi ci si fida.
- **Canali**: `#sbiffo` e `#italia` (con `ops`). Su `#it-opers` Mike non c'e'.
- **Scopa** in perimetro (Malekith 2026-10-20): spec sua dopo questa, stesso
  mazzo e stesso motore di tavolo.
