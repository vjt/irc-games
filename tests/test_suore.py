"""Prova a secco del refactor multi-rete/multi-canale. Nessuna FIFO, nessun IRC:
say() intercettata, log finti. Verifica quello che prima NON funzionava."""
import json, os, sys, tempfile, time

tmp = tempfile.mkdtemp()
os.environ["SUORE_STATE"] = os.path.join(tmp, "state.json")
os.environ["SUORE_EVENTS"] = os.path.join(tmp, "events.ndjson")
os.environ["SUORE_STATS"] = os.path.join(tmp, "stats.json")
os.environ["SUORE_NETS"] = (
    f"azzurra|{tmp}/a.log||#sniffo:90-240,#sbiffo:60-180;"
    f"ircnet|{tmp}/i.log||#cybernet:90-240")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import suore

sent = []
suore.say = lambda net, chan, text: sent.append((net.name, chan, text))

# La dorata regge piu' di un colpo, quindi una comparsa dorata a caso fa saltare
# ogni assert sui punteggi: la prova era verde o rossa a seconda del dado (3 run
# su 5 fallivano). Le comparse dorate si provano dove servono, montando la suora
# a mano; qui il fucile spara e basta.
suore.P_GOLDEN = 0.0

data = suore.empty()
now = time.time()

# 1. ogni canale prende la SUA suora — il difetto vero del vecchio codice
for scope in sorted(suore.SCOPES):
    assert suore.spawn(data, scope, now), scope
assert len(sent) == 3, sent
assert sorted(s[1] for s in sent) == ["#cybernet", "#sbiffo", "#sniffo"], sent
assert {s[0] for s in sent} == {"azzurra", "ircnet"}, sent
print("ok: tre suore, una per canale, sulle due reti giuste")

# 2. il .bang di un canale non tocca la suora dell'altro
sent.clear()
suore.P_JAM = suore.P_NOPE = 0.0
assert suore.handle(data, "azzurra/#sniffo", "vjt", "bang", now + 5)
assert data["nuns"]["azzurra/#sniffo"] is None
assert data["nuns"]["azzurra/#sbiffo"] is not None
assert data["nuns"]["ircnet/#cybernet"] is not None
assert sent[0][:2] == ("azzurra", "#sniffo"), sent
print("ok: il colpo consuma solo la suora del suo canale")

# 3. i punteggi non si mescolano fra reti né fra canali
sent.clear()
suore.handle(data, "ircnet/#cybernet", "vjt", "bang", now + 9)
assert data["scores"]["azzurra/#sniffo"]["vjt"]["kills"] == 1
assert data["scores"]["ircnet/#cybernet"]["vjt"]["kills"] == 1
assert "azzurra/#sbiffo" not in data["scores"]
print("ok: punteggi per scope, niente travaso fra reti")

# 4. la risposta esce dalla FIFO della rete da cui è arrivato il colpo
sent.clear()
line = ("23:34:33 < :peluche!~cb@no.sta.dirme.che.diocane.veneto.it "
        "PRIVMSG #cybernet :.bang")
suore.process(suore.SCOPES["ircnet/#cybernet"][0], line, data, now + 20)
assert sent and sent[0][0] == "ircnet", sent   # la risposta esce da ircnet
# la stessa riga letta dal log di Azzurra non deve fare nulla: #cybernet non
# è un canale di quella rete
sent.clear()
assert not suore.process(suore.SCOPES["azzurra/#sniffo"][0], line, data, now + 21)
assert not sent
print("ok: instradamento per rete, niente colpi cross-network")

# 5. cadenza per canale
suore.schedule(data, "azzurra/#sbiffo", now)
gap = data["next_spawn"]["azzurra/#sbiffo"] - now
assert 60 * 60 <= gap <= 180 * 60, gap
suore.schedule(data, "azzurra/#sniffo", now)
gap = data["next_spawn"]["azzurra/#sniffo"] - now
assert 90 * 60 <= gap <= 240 * 60, gap
print("ok: ogni canale usa la sua finestra")

# 6. migrazione dallo stato vecchio
old = {"scores": {"#sniffo": {"vjt": {"kills": 7, "friends": 2, "nick": "vjt"}}},
       "nun": {"chan": "#sniffo", "at": now, "golden": False, "hp": 1},
       "next_spawn": now + 100, "penalty": {"vjt": now + 3},
       "last_taken": {"#sniffo": {"at": now, "nick": "katia"}}}
open(os.environ["SUORE_STATE"], "w").write(json.dumps(old))
migrated = suore.load()
assert migrated["scores"]["azzurra/#sniffo"]["vjt"]["kills"] == 7
assert migrated["nuns"]["azzurra/#sniffo"]["chan"] == "#sniffo"
assert migrated["last_taken"]["azzurra/#sniffo"]["nick"] == "katia"
assert migrated["next_spawn"] == {} and migrated["penalty"] == {}
assert "nun" not in migrated
again = suore.migrate(json.loads(json.dumps(migrated)))   # idempotente
assert "azzurra/azzurra/#sniffo" not in again["scores"], again["scores"]
print("ok: stato vecchio migrato, e migrare due volte non raddoppia il prefisso")

# 7. il messaggio del colpo dice posizione, medaglia e chi hai scavalcato
suore.P_JAM = suore.P_NOPE = 0.0
scope = "azzurra/#sbiffo"
data = suore.empty()
data["scores"][scope] = {
    "peluche": {"kills": 13, "friends": 0, "nick": "peluche"},
    "morph":   {"kills": 9,  "friends": 2, "nick": "morph"},
    "vjt":     {"kills": 9,  "friends": 0, "nick": "vjt"},
    "alk":     {"kills": 1,  "friends": 0, "nick": "alk"},
}
# a parità di kills vince chi ha più amicizie: morph 2º, vjt 3º
assert [s["nick"] for s in suore.board(data, scope, "kills")] == \
       ["peluche", "morph", "vjt", "alk"]
suore.spawn(data, scope, now)
sent.clear()
assert suore.handle(data, scope, "vjt", "bang", now + 10)
msg = sent[0][2]
assert "🥈 2º, supera morph." in msg, msg
print("ok: il colpo annuncia medaglia, posizione e sorpasso")

# chi resta dov'è non scavalca nessuno, e fuori dal podio niente medaglia
suore.spawn(data, scope, now + 20)
sent.clear()
assert suore.handle(data, scope, "alk", "bang", now + 30)
msg = sent[0][2]
assert " 4º." in msg and "supera" not in msg, msg
assert not any(m in msg for m in suore.MEDALS.values()), msg
print("ok: niente medaglia fuori dal podio, niente sorpasso inventato")

# il .bef ha la coda sua: scala i MARPIONI, non i killer (ordine di vjt 13:17)
data["scores"][scope]["katia"] = {"kills": 0, "friends": 4, "nick": "katia"}
data["scores"][scope]["morph"]["friends"] = 4   # pari merito, ma morph ha piu' kills
suore.spawn(data, scope, now + 40)
sent.clear()
assert suore.handle(data, scope, "katia", "bef", now + 50)
msg = sent[0][2]
assert "🥇 1º, supera morph." in msg, msg
# e non e' la classifica dei killer: katia li' non c'e' proprio (0 kills)
assert "katia" not in [s["nick"] for s in suore.board(data, scope, "kills")]
print("ok: il .bef annuncia la posizione fra i marpioni, non fra i killer")

# --- event log ---------------------------------------------------------------
# Il log eventi si e' scritto da solo per tutta la sessione qui sopra: nessuna
# chiamata esplicita, e' `handle()` che appende. Lo si rilegge adesso.
import suore_events

logged = suore_events.read(suore.EVENTS)
assert logged, "il log eventi e' vuoto: handle() non ha appeso niente"
assert all(set(e) <= set(suore_events.FIELDS) for e in logged), logged[0]
assert all(e["verb"] in ("bang", "bef") for e in logged), logged
assert all(e["reaction"] >= 0 for e in logged), logged
assert {e["scope"] for e in logged} <= set(suore.SCOPES), logged
print(f"ok: il log eventi ha {len(logged)} righe, una per comparsa risolta")

# un colpo a vuoto NON deve lasciare traccia: il log e' delle comparse risolte
prima = len(suore_events.read(suore.EVENTS))
suore.handle(data, scope, "nessuno", "bang", now + 999)   # nessuna suora viva
assert len(suore_events.read(suore.EVENTS)) == prima, "il buco ha scritto un evento"
print("ok: il colpo a vuoto non finisce nel log")

# ROUND-TRIP: la riga annunciata in canale deve tornare indietro identica
# attraverso il parser del backfill. E' il test che tiene insieme le due meta':
# se domani cambia la formula dell'annuncio e non la regex, questo si spacca
# invece di far sparire in silenzio lo storico di ieri.
for verb, golden_wanted in (("bang", False), ("bef", False)):
    suore.spawn(data, scope, now + 1000)
    sent.clear()
    assert suore.handle(data, scope, "peluche", verb, now + 1007)
    riga = sent[0][2]
    back = suore_events.parse(riga)
    assert back, f"il parser non riconosce l'annuncio: {riga!r}"
    assert back["nick"] == "peluche", back
    assert back["verb"] == verb, (back, riga)
    assert back["golden"] is golden_wanted, (back, riga)
    assert back["chan"] == scope.split("/", 1)[1], (back, scope)
    assert abs(back["reaction"] - 7) < 1.5, (back, riga)
print("ok: round-trip annuncio -> parser del backfill, bang e bef")

# la dorata: si annuncia diversa e deve tornare indietro con golden=True
data["nuns"][scope] = {"at": now + 2000, "golden": True, "hp": 1}
sent.clear()
assert suore.handle(data, scope, "bitflesh", "bang", now + 2003)
riga = sent[0][2]
back = suore_events.parse(riga)
assert back and back["golden"] is True, (back, riga)
assert back["verb"] == "bang" and back["nick"] == "bitflesh", back
assert suore_events.read(suore.EVENTS)[-1]["golden"] is True
print("ok: round-trip della suora dorata, golden=True da entrambe le parti")

# i formati di tempo di human_time() devono tornare tutti numeri
assert suore_events.parse_took("37.989 secondi") == 37.989
assert suore_events.parse_took("1 secondo") == 1
assert suore_events.parse_took("1m 05s") == 65
assert suore_events.parse_took("2h 03m 04s") == 7384
assert suore_events.parse_took("una vita") is None
print("ok: i tre formati di human_time() si rileggono come secondi")

# il decimo sotto i 5 secondi (vjt, #sbiffo 2026-09-17 00:56): distingue lo
# zero dal quasi-zero e si rilegge come numero; dalla soglia in su resta intero.
assert suore.human_time(0.44) == "0.4 secondi"
assert suore.human_time(4.96) == "5.0 secondi"
assert suore.human_time(5.4) == "5 secondi"
assert suore_events.parse_took(suore.human_time(0.44)) == 0.4
print("ok: decimi sotto i 5 secondi, interi sopra")

# --- json per la pagina ------------------------------------------------------
# Anche questo si e' scritto da solo: handle() lo rifa' a ogni comparsa risolta.
import suore_stats

stats = json.load(open(suore.STATS, encoding="utf-8"))
assert stats["eventi_totali"] == len(suore_events.read(suore.EVENTS)), stats
assert set(stats["scopes"]) >= {e["scope"] for e in logged}, stats["scopes"].keys()
uno = stats["scopes"][scope]
# le classifiche vengono dallo STATO, non dal log: e' quello che dice il bot
assert uno["totali"]["kills"] == sum(
    v["kills"] for v in data["scores"][scope].values()), uno["totali"]
assert uno["classifica"] == sorted(
    uno["classifica"], key=lambda r: (-r["kills"], -r["friends"], r["nick"].lower()))
assert uno["record"]["secs"] > 0 and uno["record"]["nick"], uno["record"]
assert len(uno["per_ora"]) == 24 and sum(uno["per_ora"]) == uno["totali"]["eventi"]
print("ok: il json della pagina si riscrive da solo e quadra col log")

# il record personale sta in riga, ed e' il minimo sui SOLI colpi: il .bef e'
# un'altra partita e ci entrerebbe con tempi non comparabili. Il casing del log
# non conta: chi spara come `Tizio` e come `tizio` e' la stessa persona.
eventi = [
    {"ts": 100, "scope": "x/#y", "nick": "Tizio", "verb": "bang", "reaction": 30.0},
    {"ts": 200, "scope": "x/#y", "nick": "tizio", "verb": "bang", "reaction": 4.5},
    {"ts": 300, "scope": "x/#y", "nick": "Tizio", "verb": "bef", "reaction": 0.5},
    {"ts": 400, "scope": "x/#y", "nick": "caio", "verb": "bef", "reaction": 1.0},
]
punteggi = {"tizio": {"nick": "Tizio", "kills": 2, "friends": 1},
            "caio": {"nick": "caio", "kills": 0, "friends": 1}}
righe = {r["nick"]: r for r in
         suore_stats._scope_stats(eventi, punteggi)["classifica"]}
assert righe["Tizio"]["record"] == 4.5, righe["Tizio"]
assert righe["caio"]["record"] == 1.0, righe["caio"]   # solo .bef: ripiega sui tempi d amicizia
assert righe["Tizio"]["eventi"] == 3, righe["Tizio"]
print("ok: il record personale e' il minimo sui colpi, e ignora il casing")

# la mediana non deve farsi trascinare dal colpo dato dopo mezz'ora
assert suore_stats._mediana([1, 2, 3]) == 2
assert suore_stats._mediana([1, 2, 3, 100]) == 2.5
assert suore_stats._mediana([5]) == 5
print("ok: la mediana regge gli outlier")

# --- ultima suora e finestra della prossima ----------------------------------
# Ordine di vjt (#sniffo 2026-09-10 19:17-19:19): per ogni canale quando c'e'
# stata l'ultima e quando arriva la prossima, "a scarti di mezz'ora" nella
# forma «tra due ore» / «entro mezz'ora» — mai un orario, mai il secondo.
assert suore.fra_quanto(0, 0) == "entro mezz'ora"
assert suore.fra_quanto(600, 0) == "entro mezz'ora"      # arrotonda a zero
assert suore.fra_quanto(-9999, 0) == "entro mezz'ora"    # finestra gia' aperta
assert suore.fra_quanto(1800, 0) == "tra mezz'ora"
assert suore.fra_quanto(3600, 0) == "tra un'ora"
assert suore.fra_quanto(5400, 0) == "tra un'ora e mezza"
assert suore.fra_quanto(7200, 0) == "tra due ore"
assert suore.fra_quanto(3600 * 7, 0) == "tra 7 ore"      # oltre la tabella
assert suore.fra_quanto(3600 * 7 + 1800, 0) == "tra 7 ore e mezza"
print("ok: la prossima si dice a mezz'ore, in italiano e mai in orario")

# La finestra e' agganciata alla griglia assoluta delle mezz'ore, cosi' il
# canale e la pagina leggono lo stesso numero. E il secondo esatto non esce.
prova = {"nuns": {}, "next_spawn": {"x/#y": 4000.0}, "last_taken": {}}
assert suore.next_window(prova, "x/#y") == (3600.0, 5400.0)
assert suore.next_window(prova, "x/#z") is None          # scope senza appuntamento
prova["nuns"]["x/#y"] = {"at": 1}
assert suore.next_window(prova, "x/#y") is None          # ce n'e' una in giro
print("ok: la finestra sta sulla griglia, e tace se la suora e' gia' li'")

# La coda di `.suore`: last_taken vale per il .bang come per il .bef, ed e'
# quello che vjt intende per "l'ultimo evento".
stato = {"nuns": {}, "next_spawn": {"x/#y": 7200.0},
         "last_taken": {"x/#y": {"at": 0.0, "nick": "Tizio"}}}
riga = suore.when_line(stato, "x/#y", 3600.0)
assert "Tizio" in riga and "1h 00m 00s fa" in riga, riga
assert "tra un'ora" in riga, riga
mia = suore.when_line(stato, "x/#y", 3600.0, "TIZIO")
assert "te la sei presa tu" in mia and "Tizio" not in mia, mia
stato["nuns"]["x/#y"] = {"at": 1}
assert "ADESSO" in suore.when_line(stato, "x/#y", 3600.0)
vuoto = suore.when_line({"nuns": {}, "next_spawn": {}, "last_taken": {}}, "x/#y", 0)
assert "nessuna" in vuoto and "calendario" in vuoto, vuoto
print("ok: la coda di .suore dice l'ultima presa e la prossima finestra")

# Il JSON per la pagina porta gli stessi due fatti, e il verbo dell'ultimo
# evento: la pagina deve poter dire "sparata" o "fatta amica".
s_json = suore_stats.build(eventi, {"scores": {"x/#y": punteggi},
                                    "nuns": {},
                                    "next_spawn": {"x/#y": 7200.0},
                                    "last_taken": {}}, 0.0)["scopes"]["x/#y"]
assert s_json["ultima"] == {"ts": 400, "nick": "caio", "verbo": "bef"}, s_json["ultima"]
assert s_json["prossima"] == {"stato": "attesa", "da": 7200.0, "a": 9000.0}, s_json["prossima"]
# stessa griglia del canale: se le due costanti divergono, questo salta
assert suore_stats.BUCKET == suore.BUCKET
print("ok: il json porta ultima+prossima con la stessa grana del canale")

# /suore/metrics: lo stesso JSON in testo Prometheus, rete e canale separati.
prom = suore_stats.metrics(suore_stats.build(
    eventi, {"scores": {"x/#y": punteggi}}, 0.0))
assert "# TYPE suore_kills_total counter" in prom, prom
assert 'suore_kills_total{network="x",channel="#y",nick="caio"}' in prom, prom
assert f"suore_events_total {len(eventi)}\n" in prom, prom
assert suore_stats._lbl('a"b\\c') == 'a\\"b\\\\c'
print("ok: metrics in formato Prometheus")

# --- la previsione in coda anche al !bang e al !bef --------------------------
# Ordine di vjt (#sniffo 2026-09-10 20:40): «facciamo che la previsione della
# prossima suora appare anche nella risposta al !bang e al !bef. stessa
# granularita'» — stessa mezz'ora di `.suore`, e per forza: e' lo stesso numero.
d2 = suore.empty()
sc = "azzurra/#sniffo"
d2["next_spawn"][sc] = 7200.0
sent.clear()
assert suore.handle(d2, sc, "tizio", "bang", 0.0)   # a vuoto: conta la cilecca
assert "tra due ore" in sent[-1][2], sent
sent.clear()
assert suore.handle(d2, sc, "tizio", "bef", 0.0)
assert "tra due ore" in sent[-1][2], sent

# Sulla presa la finestra e' quella NUOVA, fissata da schedule() un attimo prima:
# la riga non deve annunciare l'appuntamento appena consumato.
sent.clear()
d2["nuns"][sc] = {"chan": "#sniffo", "at": 0.0, "golden": False, "hp": 1}
assert suore.handle(d2, sc, "tizio", "bang", 60.0)
attesa = suore.fra_quanto(suore.next_window(d2, sc)[0], 60.0)
assert attesa in sent[-1][2], (attesa, sent)

# Dove la suora c'e' ancora la coda deve sparire da se': dorata che incassa e
# fucile inceppato non sono un buco da riempire con un appuntamento.
sent.clear()
d2["nuns"][sc] = {"chan": "#sniffo", "at": 60.0, "golden": True, "hp": 3}
assert suore.handle(d2, sc, "tizio", "bang", 61.0)
assert "regge ancora" in sent[-1][2] and "La prossima" not in sent[-1][2], sent
sent.clear()
suore.P_JAM = 1.0
assert suore.handle(d2, sc, "caio", "bang", 62.0)
assert "inceppato" in sent[-1][2] and "La prossima" not in sent[-1][2], sent
suore.P_JAM = 0.0
print("ok: bang e bef dicono la prossima, e tacciono se la suora e' li'")

# --- .chiestato: chi ha fatto l'ultima cosa all'ultima suora -----------------
# Ordine di vjt (#sbiffo 2026-09-10 23:36): «il comando .chiestato !chiestato
# che mostra l'ultimo attore» / «dell'ultima operazione sull'ultima suora».
d3 = suore.empty()
sc = "azzurra/#sniffo"

# A canale vergine non si inventa un colpevole.
sent.clear()
assert not suore.handle(d3, sc, "tizio", "chiestato", 0.0)
assert "non e' stato nessuno" in sent[-1][2], sent

# Dopo un bang: chi, cosa, e da quanto. Il verbo arriva da last_taken, che ora
# lo scrive: senza, la riga direbbe chi ma mai cosa.
d3["nuns"][sc] = {"chan": "#sniffo", "at": 0.0, "golden": False, "hp": 1}
assert suore.handle(d3, sc, "Peluche", "bang", 10.0)
assert d3["last_taken"][sc]["cmd"] == "bang", d3["last_taken"]
sent.clear()
assert not suore.handle(d3, sc, "tizio", "chiestato", 70.0)
riga = sent[-1][2]
assert "l'ha fatta fuori Peluche" in riga and "1m 00s fa" in riga, riga

# Il bef ha il suo verbo, e la dorata si dichiara.
d3["nuns"][sc] = {"chan": "#sniffo", "at": 100.0, "golden": True, "hp": 1}
assert suore.handle(d3, sc, "katia", "bef", 110.0)
sent.clear()
assert not suore.handle(d3, sc, "tizio", "chiestato", 110.0)
riga = sent[-1][2]
assert "se l'e' fatta amica katia" in riga and "(era dorata)" in riga, riga

# Stato vecchio, salvato prima che last_taken imparasse il verbo: si dice chi
# l'ha presa e si tace su cosa, invece di indovinare.
d3["last_taken"][sc] = {"at": 200.0, "nick": "alk"}
sent.clear()
assert not suore.handle(d3, sc, "tizio", "chiestato", 200.0)
riga = sent[-1][2]
assert "se l'e' presa alk" in riga and "fatta fuori" not in riga, riga

# E' una domanda, non una giocata: non consuma la suora in giro.
d3["nuns"][sc] = {"chan": "#sniffo", "at": 300.0, "golden": False, "hp": 1}
assert not suore.handle(d3, sc, "tizio", "chiestato", 310.0)
assert d3["nuns"][sc] is not None, d3["nuns"]

# E la scrive il parser, con tutti e due i prefissi.
assert suore.CMD_PAT.match(".chiestato").group("cmd").lower() == "chiestato"
assert suore.CMD_PAT.match("!CHIESTATO").group("cmd").lower() == "chiestato"
assert suore.CMD_PAT.match(".chiestatone") is None

# L'alias `.chiesato` (Sythos #sbiffo 2026-09-11 23:25, vjt «mettiamo alias»):
# stessa risposta, e il vecchio nome NON deve essere mangiato dal ramo corto
# dell'alternanza.
assert suore.CMD_PAT.match(".chiesato").group("cmd").lower() == "chiesato"
assert suore.CMD_PAT.match("!CHIESATO").group("cmd").lower() == "chiesato"
assert suore.CMD_PAT.match(".chiestato").group("cmd").lower() == "chiestato"
assert suore.CMD_PAT.match(".chiesatone") is None
sent.clear()
assert not suore.handle(d3, sc, "tizio", "chiesato", 320.0)
assert "se l'e' presa alk" in sent[-1][2], sent[-1][2]
assert d3["nuns"][sc] is not None, d3["nuns"]
print("ok: .chiestato dice chi ha fatto cosa all'ultima suora, e non gioca")

# --- pew / pewpew, alias di bang (ordine di vjt, #sbiffo 17/9 11:10) ---------
# Il punto e' che arrivino a handle() gia' normalizzati in "bang", col punto
# come col botto, e che `.pewpewpew` NON passi: la coda vuole uno spazio.
for testo in (".pew", "!pew", ".pewpew", "!PewPew", ".pew a caso"):
    m = suore.CMD_PAT.match(testo)
    assert m, testo
    c = m.group("cmd").lower()
    assert suore.ALIAS.get(c, c) == "bang", (testo, c)
for testo in (".pewpewpew", ".pewa", "pew"):
    assert not suore.CMD_PAT.match(testo), testo

d4 = suore.empty()
sc = "azzurra/#sniffo"
suore.P_JAM = suore.P_NOPE = 0.0
d4["nuns"][sc] = {"chan": "#sniffo", "at": 0.0, "golden": False, "hp": 1}
sent.clear()
riga = ("11:11:11 < :vjt!~vjt@due.dita.di.grappa.chat "
        "PRIVMSG #sniffo :.pewpew")
assert suore.process(suore.SCOPES[sc][0], riga, d4, 5.0)
assert d4["nuns"][sc] is None, d4["nuns"]
assert d4["scores"][sc]["vjt"]["kills"] == 1, d4["scores"]
print("ok: pew e pewpew sparano come bang")


# --- late_by: chi ha steso la suora non l'ha "mancata" (vjt, #sbiffo 18/9) ---
# Il ramo «non c'e' nessuna suora» attaccava sempre «l'hai mancata per X: se
# l'e' presa <nick>», anche quando <nick> era chi stava sparando: due bugie in
# una riga. Ora quel caso dice cos'ha fatto e quanto fa.
d5 = suore.empty()
sc = "azzurra/#sbiffo"
suore.P_JAM = suore.P_NOPE = 0.0

d5["last_taken"][sc] = {"at": 100.0, "nick": "vjt", "cmd": "bang", "golden": True}
sent.clear()
assert not suore.handle(d5, sc, "vjt", "bang", 130.0)
riga = sent[-1][2]
assert "L'hai stesa tu 30 secondi fa." in riga, riga
assert "mancata" not in riga, riga

# Il casing non c'entra: lo stato tiene quello visto al colpo, il nick IRC e'
# case-insensitive.
sent.clear()
assert not suore.handle(d5, sc, "VJT", "bang", 130.0)
assert "L'hai stesa tu" in sent[-1][2], sent[-1][2]

# Un altro che spara tardi resta col messaggio di prima: li' ha mancato davvero.
sent.clear()
assert not suore.handle(d5, sc, "peluche", "bang", 130.0)
riga = sent[-1][2]
assert "L'hai mancata per 30 secondi: se l'e' presa vjt." in riga, riga

# Col `.bef` il verbo cambia, e vale anche per il ramo «suora inesistente».
d5["last_taken"][sc] = {"at": 200.0, "nick": "katia", "cmd": "bef", "golden": False}
sent.clear()
assert not suore.handle(d5, sc, "katia", "bef", 210.0)
assert "Te la sei fatta amica tu 10 secondi fa." in sent[-1][2], sent[-1][2]

# Stato vecchio senza verbo: si dice che e' roba tua, non si inventa il come.
d5["last_taken"][sc] = {"at": 300.0, "nick": "alk"}
sent.clear()
assert not suore.handle(d5, sc, "alk", "bang", 310.0)
assert "Te la sei presa tu 10 secondi fa." in sent[-1][2], sent[-1][2]

# Oltre la finestra tace come prima, anche per chi l'ha presa: dopo due minuti
# non e' piu' una coda utile, e' rumore su ogni colpo a canale spento.
d5["last_taken"][sc] = {"at": 400.0, "nick": "vjt", "cmd": "bang"}
sent.clear()
assert suore.handle(d5, sc, "vjt", "bang", 400.0 + suore.LATE_WINDOW + 1)   # fuori finestra: a vuoto, si conta
riga = sent[-1][2]
assert "stesa" not in riga and "mancata" not in riga, riga
print("ok: chi ha steso la suora non se la sente dire 'mancata'")


# le dorate si spartiscono per partita: le convertite vanno ai marpioni, le
# stese ai killer, e una non compare nella riga dell'altra (puppy, 13/10)
d3 = {"scores": {"t/#x": {
    "a": {"nick": "a", "kills": 4, "friends": 0, "golden": 2},
    "b": {"nick": "b", "kills": 1, "friends": 5, "golden": 3, "golden_friends": 2},
}}}
assert suore.dorate(d3["scores"]["t/#x"]["b"], "kills") == 1
assert suore.dorate(d3["scores"]["t/#x"]["b"], "friends") == 2
killers, marp = suore.leaderboard(d3, "t/#x"), suore.marpioni(d3, "t/#x")
assert "di cui 2 ⭐" in killers and "di cui 1 ⭐" in killers, killers
assert "(⭐ = dorate)" in marp and "di cui 2 ⭐" in marp, marp
print("ok: dorate divise fra killer e marpioni")


# le figuracce diventano una percentuale sui tentativi: 1 jam su 1+3 colpi = 25%
st = suore_stats.build([], {"scores": {"t/#x": {"pippo": {"nick": "Pippo", "kills": 3, "friends": 0}}},
                            "flops": {"t/#x": {"pippo": {"jam": 1, "nope": 0}}}}, 0)
r = st["scopes"]["t/#x"]["classifica"][0]
assert r["cilecca_pct"] == 25, r
print("ok: percentuali delle figuracce")

# gradi: due scale, stesse soglie, la promozione si annuncia sul colpo che la fa
assert suore.grado(0, "kills") == "chierichetto"
assert suore.grado(4, "kills") == "chierichetto"
assert suore.grado(5, "kills") == "sagrestano"
assert suore.grado(20, "kills") == "diacono"
assert suore.grado(9999, "kills") == "antipapa"
assert suore.grado(15, "friends") == "corteggiante"
assert suore.promosso(4, 5, "kills") == " Nuovo grado: sagrestano!"
assert suore.promosso(5, 6, "kills") == ""
print("ok: gradi e promozioni")

# L'RPG non c'e' piu' (vjt, #sbiffo 2026-10-21), ma gli stati vecchi hanno
# ancora `rpg_lost`: le stese perse per l'esplosione stanno nel log e non nello
# stato, e la discrepanza della storia deve continuare a tornare.
suore.P_JAM = suore.P_NOPE = 0.0
sc = "azzurra/#sbiffo"
st = suore_stats._scope_stats([{"ts": 1.0, "scope": sc, "nick": "Bomber", "verb": "bang"}] * 3,
                              {"bomber": {"kills": 2, "friends": 0, "nick": "Bomber", "rpg_lost": 1}})
assert st["discrepanza"] == 0, st["discrepanza"]
# e il caricamento butta le chiavi dell'RPG dagli stati vecchi
vecchio = dict(suore.empty(), rpg={sc: {"vjt": True}},
               rpg_ground={sc: {"nick": "vjt", "at": 1.0}})
open(os.environ["SUORE_STATE"], "w").write(json.dumps(vecchio))
caricato = suore.load()
assert "rpg" not in caricato and "rpg_ground" not in caricato, caricato
assert not suore.CMD_PAT.match(".take")
print("ok: niente RPG, la storia con rpg_lost torna")

# colpo a vuoto per sondare il timer = cilecca (Malekith, #sbiffo 13/10);
# chi l'ha mancata di poco invece no
dv = suore.empty()
sv = "azzurra/#sbiffo"
sent.clear()
assert suore.handle(dv, sv, "Sonda", "bef", 20000.0)
assert dv["flops"][sv]["sonda"]["vuoto"] == 1, dv["flops"]
assert "Robaccia" in sent[-1][2], sent
dv["last_taken"] = {sv: {"nick": "altro", "at": 20100.0, "cmd": "bang"}}
assert not suore.handle(dv, sv, "Tardi", "bang", 20110.0)
assert "tardi" not in dv["flops"][sv], dv["flops"]
print("ok: colpo a vuoto contato, mancata di poco no")


# rivalita': chi te l'ha soffiata sotto il naso (puppy, #sbiffo 13/10);
# chi spara sulla propria non conta, e oltre i 3 secondi non e' soffiata
assert "soffiate" not in dv, dv.get("soffiate")       # mancata di 10s: no
dv["last_taken"][sv] = {"nick": "altro", "at": 20150.0, "cmd": "bang"}
suore.handle(dv, sv, "Tardi", "bang", 20152.5)
assert dv["soffiate"][sv] == {"altro": {"tardi": {"n": 1, "s": 2.5}}}, dv["soffiate"]
suore.handle(dv, sv, "tardi", "bang", 20153.0)           # 3.0 esatti: conta
assert dv["soffiate"][sv]["altro"]["tardi"] == {"n": 2, "s": 5.5}, dv["soffiate"]
suore.handle(dv, sv, "tardi", "bang", 20153.1)           # 3.1: no
assert dv["soffiate"][sv]["altro"]["tardi"]["n"] == 2, dv["soffiate"]
dv["last_taken"][sv] = {"nick": "Tardi", "at": 20200.0, "cmd": "bang"}
suore.handle(dv, sv, "tardi", "bang", 20201.0)
assert "tardi" not in dv["soffiate"][sv], dv["soffiate"]
# int del formato vecchio: ignorato dalle stats, sovrascritto al primo colpo
dv["soffiate"][sv]["vecchio"] = {"tardi": 7}
dv["scores"][sv] = {"altro": {"nick": "Altro", "kills": 1, "friends": 0}}
st = suore_stats.build([{"ts": 1760356800.0, "scope": sv, "nick": "Altro",
                         "verb": "bang", "golden": False, "reaction": 3.0}], dv, 0)
assert st["scopes"][sv]["rivalita"] == [
    {"chi": "Altro", "vittima": "tardi", "n": 2, "secs": 2.8}], st["scopes"][sv]["rivalita"]
dv["last_taken"][sv] = {"nick": "vecchio", "at": 20300.0, "cmd": "bang"}
suore.handle(dv, sv, "tardi", "bang", 20301.0)
assert dv["soffiate"][sv]["vecchio"]["tardi"] == {"n": 1, "s": 1.0}, dv["soffiate"]
hm = st["scopes"][sv]["heatmap"]
assert len(hm) == 7 and all(len(r) == 24 for r in hm) and sum(map(sum, hm)) == 1
assert hm[0][14] == 1, hm   # lunedi' 13/10/2025 12:00 UTC = 14 a Roma (+2)
print("ok: rivalita' e heatmap")


# rivalita' dorata (puppy, #sbiffo 13/10): chi chiude la dorata sui colpi
# altrui ruba una volta a ciascuno, non una per colpo; sui colpi propri niente
dg = suore.empty()
sg = "azzurra/#sbiffo"
suore.P_JAM = suore.P_NOPE = 0.0
dg["nuns"][sg] = {"chan": "#sbiffo", "at": 30000.0, "golden": True, "hp": 4}
suore.handle(dg, sg, "vjt", "bang", 30001.0)
suore.handle(dg, sg, "VJT", "bang", 30002.0)
suore.handle(dg, sg, "puppy", "bef", 30003.0)
assert dg["nuns"][sg]["hits"] == [{"nick": "vjt", "cmd": "bang"}, {"nick": "vjt", "cmd": "bang"},
                                  {"nick": "puppy", "cmd": "bef"}], dg["nuns"][sg]
assert "dorate_rubate" not in dg, "contato prima della chiusura"
suore.handle(dg, sg, "Malekith", "bang", 30004.0)
assert dg["nuns"][sg] is None
assert dg["dorate_rubate"][sg] == {"malekith": {"puppy": 1, "vjt": 1}}, dg["dorate_rubate"]
assert dg["ultime_vite"][sg] == {"malekith": 1}, dg["ultime_vite"]
# chiusa da chi l'aveva gia' colpita, con un altro in mezzo: ruba solo all'altro
dg["nuns"][sg] = {"chan": "#sbiffo", "at": 30100.0, "golden": True, "hp": 3}
suore.handle(dg, sg, "malekith", "bang", 30101.0)
suore.handle(dg, sg, "vjt", "bang", 30102.0)
suore.handle(dg, sg, "Malekith", "bang", 30103.0)
assert dg["dorate_rubate"][sg]["malekith"] == {"puppy": 1, "vjt": 2}, dg["dorate_rubate"]
assert dg["ultime_vite"][sg] == {"malekith": 2}, dg["ultime_vite"]
# tutta da solo: niente furto, niente ultima vita
dg["nuns"][sg] = {"chan": "#sbiffo", "at": 30200.0, "golden": True, "hp": 2}
suore.handle(dg, sg, "vjt", "bang", 30201.0)
suore.handle(dg, sg, "vjt", "bang", 30202.0)
assert "vjt" not in dg["dorate_rubate"][sg] and "vjt" not in dg["ultime_vite"][sg], dg
# dorata in giro da prima di `hits`, e stato senza le chiavi: non si rompe
dh = suore.empty()
dh["nuns"][sg] = {"chan": "#sbiffo", "at": 30300.0, "golden": True, "hp": 1}
assert suore.handle(dh, sg, "vjt", "bang", 30301.0)
assert "dorate_rubate" not in dh and "ultime_vite" not in dh, dh
print("ok: rivalita' dorata")

# backfill: le righe del canale rifanno lo stesso conto
import importlib.util
spec = importlib.util.spec_from_file_location(
    "backfill_dorate", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    "..", "tools", "backfill_dorate.py"))
bf = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bf)
righe = [
    "10:00:00 > [suore] PRIVMSG #sbiffo :\x02vjt: Il colpo l'ha presa in pieno, ma la suora dorata regge ancora: le restano 2 vite.\x0f",
    "10:00:01 < :vjt!~v@x PRIVMSG #sbiffo :puppy: Ha quasi ceduto, ma la suora dorata regge ancora",
    "10:00:02 > [suore] PRIVMSG #sbiffo :puppy: Ha quasi ceduto, ma la suora dorata regge ancora: le restano 1 vita.",
    "10:00:03 > [suore] PRIVMSG #sbiffo :\x02Malekith ha steso la SUORA DORATA in 3 secondi!\x0f Ne ha stese 1",
    "11:00:00 > [suore] PRIVMSG #sbiffo :sarahbean: Il colpo l'ha presa in pieno, ma la suora dorata regge ancora: le restano 4 vite.",
    "12:00:00 > [suore] PRIVMSG #sbiffo :\x02\u25e6\u25e6\\_\u2629< ALLELUIA!\x0f",
    "12:00:05 > [suore] PRIVMSG #sbiffo :vjt si e' fatto amico la SUORA DORATA in 5 secondi! Ne ha convertite 1",
]
logf = os.path.join(tmp, "bf.log")
open(logf, "w", encoding="utf-8").write("\n".join(righe) + "\n")
ricostruito, conti = bf.rebuild([("azzurra", logf)], "Convento")
assert ricostruito["dorate_rubate"] == {sg: {"malekith": {"puppy": 1, "vjt": 1}}}, ricostruito
assert ricostruito["ultime_vite"] == {sg: {"malekith": 1}}, ricostruito
assert conti["dorate"] == 2 and conti["orfani"] == 1 and conti["colpi"] == 3, conti
# lo stesso log due volte: i doppioni si scartano
open(logf + "2", "w", encoding="utf-8").write(open(logf, encoding="utf-8").read())
_, conti = bf.rebuild([("azzurra", logf), ("azzurra", logf + "2")], "Convento")
assert conti["doppioni"] == 6 and conti["dorate"] == 2, conti
print("ok: backfill delle dorate")

# storia di ogni dorata dal log del bot (puppy, 13/10): colpi, vite, cilecche
import datetime as _dt
Z = suore_stats._LOCALE
def _ts(h, m, sec):
    return _dt.datetime(2026, 10, 13, h, m, sec, tzinfo=Z).timestamp()
B = "\x02"; O = "\x0f"
righe = [
    "09:00:00 > [suore] PRIVMSG #sbiffo :\u25e6\u25e6\\_\u2020< AVE MARIA!",
    "09:00:05 > [suore] PRIVMSG #sbiffo :vjt hai fatto secca una suora in 5 secondi! Ne hai stese 3 in #sbiffo.",
    f"10:00:00 > [suore] PRIVMSG #sbiffo :{B}\u25e6\u200b\u25e6\\_\u2629< ALL\u200bELUIA!{O}",
    f"10:00:10 > [suore] PRIVMSG #sbiffo :{B}vjt: Il colpo l'ha presa in pieno, ma la suora dorata regge ancora: le restano 3 vite.{O}",
    "10:00:11 < :vjt!~v@x PRIVMSG #sbiffo :puppy: Ha quasi ceduto, ma la suora dorata regge ancora: le restano 9 vite.",
    "10:00:12 > [suore] PRIVMSG #sbiffo :" + suore.JAMS[1].format(nick="Malekith", p=7),
    "10:00:12 > [suore] PRIVMSG #sniffo :" + suore.JAMS[0].format(nick="altrove", p=7),
    "10:00:13 > [suore] PRIVMSG #sbiffo :" + suore.NOPES[2].format(nick="puppy", p=7),
    f"10:00:14 > [suore] PRIVMSG #sbiffo :{B}puppy: Ha quasi ceduto, ma la suora dorata regge ancora: le resta 2 vite.{O}",
    f"10:00:15 > [suore] PRIVMSG #sbiffo :{B}vjt: Il colpo l'ha presa in pieno, ma la suora dorata regge ancora: le resta 1 vita.{O}",
    f"10:00:20 > [suore] PRIVMSG #sbiffo :{B}Malekith ha steso la SUORA DORATA in 20 secondi!{O} Ne ha stese 4 in #sbiffo, di cui 1 dorata.",
    # dorata chiusa col razzo, la riga di chiusura non ancora nel log
    f"11:00:00 > [suore] PRIVMSG #sbiffo :{B}\u25e6\\_\u2720< GLORIA IN EXCELSIS!{O}",
    f"11:00:02 > [suore] PRIVMSG #sbiffo :{B}vjt: Il colpo l'ha presa in pieno, ma la suora dorata regge ancora: le restano 4 vite.{O}",
    # riga storica: l'RPG e' stato tolto il 21/10, i log vecchi ce l'hanno
    "11:00:03 > [suore] PRIVMSG #sbiffo :alk: l'RPG ti e' esploso in faccia, porco dio. Ci rimetti "
    "una suora (ne hai 3). Sei fuori per questa suora, riprova fra 7 secondi.",
]
ircnet = [
    f"12:00:00 > [suore] PRIVMSG #cybernet :{B}\\_\u271f< ALLELUIA!{O}",
    f"12:00:04 > [suore] PRIVMSG #cybernet :{B}peluche si e' fatto amico la SUORA DORATA in 4 secondi!{O} Ne ha convertite 2 in #cybernet, di cui 1 dorata. Con l'RPG.",
]
la, li = os.path.join(tmp, "storia_a.log"), os.path.join(tmp, "storia_i.log")
open(la, "w", encoding="utf-8").write("\n".join(righe) + "\n")
open(li, "w", encoding="utf-8").write("\n".join(ircnet) + "\n")
src = suore_stats.fonti(f"azzurra|{la}|x|#sbiffo:1-2;ircnet|{li}|y|#cybernet:1-2")
assert src == [("azzurra", la), ("ircnet", li)], src
gg = suore_stats.giri(src)
assert len(gg) == 3, gg
assert gg[0]["storia"] == [["vjt", "bang", 3], ["Malekith", "jam", None], ["puppy", "nope", None],
                           ["puppy", "bef", 2], ["vjt", "bang", 1]], gg[0]
assert gg[0]["chi"] == "Malekith" and gg[0]["comparsa"] == 36000, gg[0]
ev_d = [
    {"ts": _ts(10, 0, 20), "scope": sg, "nick": "malekith", "verb": "bang", "golden": True, "reaction": 20.0},
    # chiusa col razzo, la riga di chiusura non c'e' ancora: basta la comparsa
    {"ts": _ts(11, 0, 9), "scope": sg, "nick": "alk", "verb": "bang", "golden": True, "reaction": 9.0},
    # niente reazione (backfill vecchio): basta la chiusura
    {"ts": _ts(12, 0, 4), "scope": "ircnet/#cybernet", "nick": "peluche", "verb": "bef", "golden": True},
    # fuori dal log: si ricorda come null
    {"ts": _ts(8, 0, 0), "scope": sg, "nick": "vjt", "verb": "bang", "golden": True, "reaction": 60.0},
    {"ts": _ts(9, 0, 5), "scope": sg, "nick": "vjt", "verb": "bang", "golden": False, "reaction": 5.0},
]
outp = os.path.join(tmp, "storia", "stats.json")
os.makedirs(os.path.dirname(outp))
cache = suore_stats.storie(outp, ev_d, src)
assert len(cache) == 4, cache
d_out = suore_stats.build(ev_d, {}, 0, cache)["dorate"]
assert len(d_out) == 4
assert d_out[0]["storia"][0] == ["vjt", "bang", 3] and "rpg" not in d_out[0], d_out[0]
assert d_out[1]["storia"] == [["vjt", "bang", 4], ["alk", "boom", None]] and d_out[1]["rpg"], d_out[1]
assert d_out[2]["storia"] == [] and d_out[2]["rpg"], d_out[2]
assert "storia" not in d_out[3], d_out[3]
# un altro nick nella chiusura del log: non e' quella dorata
assert suore_stats.abbina(dict(ev_d[0], nick="vjt", reaction=None), gg) is None
# la cache basta: senza log si ritrova tutto, e il log non si rilegge
assert suore_stats.storie(outp, ev_d, []) == cache
# write() passa di li', con le fonti da SUORE_NETS
os.environ["SUORE_NETS_BAK"] = os.environ["SUORE_NETS"]
os.environ["SUORE_NETS"] = f"azzurra|{la}|x|#sbiffo:1-2"
outw = os.path.join(tmp, "storia2", "stats.json")
os.makedirs(os.path.dirname(outw))
suore_stats.write(outw, ev_d[:1], {}, 0)
os.environ["SUORE_NETS"] = os.environ.pop("SUORE_NETS_BAK")
assert json.load(open(outw))["dorate"][0]["storia"][-1] == ["vjt", "bang", 1]
print("ok: storia delle dorate")

# --- corsa al primo posto per giorno (peluche, #sbiffo 16/10) ----------------
# Per i primi N della classifica, le stese cumulate e il posto in classifica
# giorno per giorno, e il capo di ogni giorno. Solo `.bang`: conta chi spara.
# I giorni senza colpi ci sono, l'asse e' il tempo. Il giorno si taglia sull'ora di Roma.
def _roma(y, m, d, h):
    return _dt.datetime(y, m, d, h, tzinfo=suore_stats.ROME).timestamp()

qe = [
    {"ts": _roma(2026, 9, 6, 12), "scope": "x/#q", "nick": "tizio", "verb": "bang"},
    {"ts": _roma(2026, 9, 6, 13), "scope": "x/#q", "nick": "Tizio", "verb": "bang"},
    # 00:30 di Roma del 7 = 22:30 UTC del 6: e' il 7
    {"ts": _roma(2026, 9, 7, 0) + 1800, "scope": "x/#q", "nick": "caio", "verb": "bang"},
    {"ts": _roma(2026, 9, 9, 10), "scope": "x/#q", "nick": "caio", "verb": "bang"},
    {"ts": _roma(2026, 9, 9, 11), "scope": "x/#q", "nick": "caio", "verb": "bang"},
    {"ts": _roma(2026, 9, 9, 12), "scope": "x/#q", "nick": "sempronio", "verb": "bef"},
    {"ts": _roma(2026, 9, 9, 13), "scope": "x/#q", "nick": "solo_log", "verb": "bang"},
]
qs = {"tizio": {"nick": "TiZio", "kills": 2}, "caio": {"nick": "Caio", "kills": 9},
      "sempronio": {"nick": "Sempronio", "kills": 0, "friends": 1},
      "zero": {"nick": "Zero", "kills": 50}}   # tutto prima del log: niente riga
q = suore_stats._quote(qe, qs)
assert q["giorni"] == ["2026-09-06", "2026-09-07", "2026-09-08", "2026-09-09"], q["giorni"]
assert q["totale"] == [2, 3, 3, 6], q["totale"]
# ordine dalla classifica dello STATO, casing dello stato; chi c'e' solo nel log
# viene dopo, col casing dell'ultima volta che si e' visto
assert [r["nick"] for r in q["nick"]] == ["Caio", "TiZio", "solo_log"], q["nick"]
caio, tizio, solo = q["nick"]
# posto a pari merito come in canale; chi non ha ancora steso niente non ha posto
assert caio["posto"] == [None, 2, 2, 1], caio
assert tizio["posto"] == [1, 1, 1, 2], tizio
assert solo["posto"] == [None, None, None, 3], solo
assert solo["stese"] == [0, 0, 0, 1], solo
# la quota sul totale non c'e' piu' (peluche, 16/10: «colpi» al posto di «quota %»)
assert all("quota" not in r for r in q["nick"]), q["nick"]
# Contro il capo (peluche, #sbiffo 16/10): servono le stese crude di ognuno e
# il capo di ogni giorno, chi e quante. Il capo puo' stare fuori dai primi N
# (qui con n=1: fino al 9 comanda TiZio, ma la riga c'e' solo per Caio).
assert caio["stese"] == [0, 1, 1, 3], caio
assert tizio["stese"] == [2, 2, 2, 2], tizio
assert q["capo"] == [{"stese": 2, "chi": ["TiZio"]}] * 3 + [{"stese": 3, "chi": ["Caio"]}], q["capo"]
assert suore_stats._quote(qe, qs, n=1)["capo"] == q["capo"]
# a pari merito in cima comandano in due, in ordine di nick
pari = [{"ts": _roma(2026, 9, 6, 12), "scope": "x/#q", "nick": n, "verb": "bang"}
        for n in ("zeta", "Alfa")]
assert suore_stats._quote(pari, {})["capo"] == [{"stese": 1, "chi": ["Alfa", "zeta"]}]
assert [r["nick"] for r in suore_stats._quote(qe, qs, n=1)["nick"]] == ["Caio"]
# niente colpi nel log, niente grafico
assert suore_stats._quote([e for e in qe if e["verb"] == "bef"], qs) is None
qb = suore_stats.build(qe, {"scores": {"x/#q": qs}}, 0)["scopes"]["x/#q"]
assert qb["quote"] == q, qb.get("quote")
# Amiche e tutte le prese (puppy, #sbiffo 19/10): stessa forma, altri verbi,
# ordine dalla colonna dello stato che corrisponde.
qa = suore_stats._quote(qe, qs, verbi=("bef",))
assert [r["nick"] for r in qa["nick"]] == ["Sempronio"], qa["nick"]
assert qa["nick"][0]["stese"] == [1], qa
assert qa["giorni"] == ["2026-09-09"], qa["giorni"]
qt = suore_stats._quote(qe, qs, verbi=("bang", "bef"))
assert qt["totale"] == [2, 3, 3, 7], qt["totale"]
assert [r["nick"] for r in qt["nick"]] == ["Caio", "TiZio", "Sempronio", "solo_log"], qt["nick"]
assert qb["quote_amiche"] == qa and qb["quote_tutte"] == qt
print("ok: stese cumulate, posto e capo per giorno")

# --- .stat: il link alla pagina del canale (Sythos, #sbiffo 2026-10-25) -------
sent.clear()
assert not suore.handle(suore.empty(), "azzurra/#sbiffo", "Sythos", "stat", 0.0)
assert sent[-1][2] == "Sythos: classifica di #sbiffo su https://dioschifoso.it/suore/?sbiffo", sent
assert suore.CMD_PAT.match("!stats").group("cmd") == "stats"
assert suore.CMD_PAT.match(".stat").group("cmd") == "stat"
assert not suore.CMD_PAT.match("!statico")
# Nome corto doppio fra le reti: serve rete/canale, come scrive la pagina.
suore.SCOPES["ircnet/#sbiffo"] = suore.SCOPES["ircnet/#cybernet"]
assert suore.stat_url("azzurra/#sbiffo").endswith("?azzurra/sbiffo")
del suore.SCOPES["ircnet/#sbiffo"]
print("ok: .stat da' il link del canale")

print("\nTUTTO VERDE, porco dio")
