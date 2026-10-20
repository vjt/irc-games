# Scopa — regole e casi limite (BOZZA)

Scopa dietro a MikeBongiorno (`scopa.py`), stesso mazzo e stesso motore di
tavolo della briscola (`docs/briscola_spec.md`). Richiesta di Malekith, #bot
2026-10-20. Bozza: niente codice finche' Malekith non chiude le domande in fondo.

## Mazzo e valori

Le stesse 40 carte della briscola. Per la presa conta il **valore**: Asso 1,
2…7 col loro numero, Fante 8, Cavallo 9, Re 10.

## Giocatori

- **2**: testa a testa.
- **3**: ognuno per se'. Il mazzo da 40 basta: 9 + 4 in tavola, 27 = 3 giri.
- **4**: coppie tirate a sorte, sedute alternate, come la briscola.
- **5+**: il `jo` del quinto e' rifiutato, niente panchina (come la briscola).

Conti: in 2, 6 + 4 in tavola, 30 = 5 giri; in 4, 12 + 4, 24 = 2 giri.

## Smazzata

1. Si mescola, 3 carte a testa, 4 scoperte in tavola.
2. A turno ognuno gioca una carta. Se in tavola c'e' una carta dello **stesso
   valore**, la prende (obbligo, e ha la precedenza su ogni somma). Se no, puo'
   prendere un gruppo di carte la cui **somma** fa il valore giocato. Se non
   prende niente, la carta resta in tavola.
3. Se la presa svuota la tavola e' **scopa**: un punto. L'ultima giocata della
   smazzata non vale scopa.
4. Finite le mani, se c'e' mazzo, 3 carte a testa (in tavola niente).
5. A mazzo finito, le carte rimaste in tavola vanno a chi ha preso per ultimo.

## Punti della smazzata

| punto | a chi |
|---|---|
| carte | chi ne ha prese piu' di 20 (in 3: il piu' alto, solo) |
| denari | chi ha preso piu' denari (in 2/4: piu' di 5) |
| settebello | chi ha il 7 di denari |
| primiera | il totale piu' alto, una carta per seme, la migliore: 7=21, 6=18, Asso=16, 5=15, 4=14, 3=13, 2=12, figure=10. Senza un seme non si fa primiera |
| scope | una ciascuna |

Pari su carte, denari o primiera: il punto non va a nessuno. Il conto si
stampa in canale a fine smazzata, voce per voce.

## Partita e serie

- La **partita** va a **11** punti (domanda aperta 1): si gioca smazzata dopo
  smazzata. Se a fine smazzata piu' giocatori (o coppie) sono a 11 o piu', vince
  il piu' alto; pari in testa = un'altra smazzata.
- `!scopa` = partita secca, `!scopa N` = al meglio di N partite, N dispari
  3…9; N pari non parte, riga «N deve essere dispari» (Malekith 2026-10-20,
  stessa regola della briscola).

## Comandi

Sotto `!scopa` (niente alias corto: domanda aperta 4).

| comando | cosa |
|---|---|
| `!scopa [N]` | apre il tavolo (60s) |
| `jo` | si siede. Max 4 |
| `!scopa esci` / `stop` / `carte` / `stato` / `top` | come la briscola |
| `pl N` | gioca la carta N della mano. Se la presa e' una sola possibile, la fa il bot |
| `pl N a b …` | gioca la carta N e prende le carte a, b … della tavola (numerate in canale). Serve solo quando le prese possibili sono piu' d'una |

Se le prese possibili sono piu' d'una e manca la scelta, il bot risponde in
query con l'elenco (`pl 3 1 4` oppure `pl 3 2`), e il turno resta suo. Una
scelta sbagliata (somma che non torna, carta dello stesso valore ignorata):
riga d'uso, turno suo.

## In canale e in privato

- **In canale**, dopo ogni giocata: la tavola numerata, cosa ha preso chi,
  «SCOPA!» quando c'e'. A fine smazzata il conto voce per voce e il totale.
- `!scopa stato`: tavola, a chi tocca, carte nel mazzo, punti della partita
  (quelli si sanno, al tavolo vero si segnano). Le scope fatte si vedono, il
  conto delle carte prese no.
- **In query**: la mano numerata, come la briscola.

## Casi limite

Gli stessi della briscola (timeout di turno, abbandono, cambio nick, netsplit,
fuori turno, un tavolo per canale, riavvio, modi di canale), con una regola in
piu': al **timeout** il bot gioca per lui la carta che non prende niente e vale
meno; se tutte prendono, quella che prende di piu'.

## Domande aperte (per Malekith)

1. **A quanto si va**: 11 (classico) o 21?
2. **Re in tavola**: con 3 o 4 Re fra le 4 carte iniziali si rimescola (regola
   di molti tavoli) o niente?
3. **Asso pigliatutto** (l'Asso prende tutta la tavola, senza scopa): si o no?
   Di default no.
4. **Alias corto** di `!scopa`: serve? `!sc` e simili si pestano con altro.
