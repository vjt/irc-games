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
assert "🥈 2º, scavalcato morph." in msg, msg
print("ok: il colpo annuncia medaglia, posizione e sorpasso")

# chi resta dov'è non scavalca nessuno, e fuori dal podio niente medaglia
suore.spawn(data, scope, now + 20)
sent.clear()
assert suore.handle(data, scope, "alk", "bang", now + 30)
msg = sent[0][2]
assert " 4º." in msg and "scavalcato" not in msg, msg
assert not any(m in msg for m in suore.MEDALS.values()), msg
print("ok: niente medaglia fuori dal podio, niente sorpasso inventato")

# il .bef ha la coda sua: scala i MARPIONI, non i killer (ordine di vjt 13:17)
data["scores"][scope]["katia"] = {"kills": 0, "friends": 4, "nick": "katia"}
data["scores"][scope]["morph"]["friends"] = 4   # pari merito, ma morph ha piu' kills
suore.spawn(data, scope, now + 40)
sent.clear()
assert suore.handle(data, scope, "katia", "bef", now + 50)
msg = sent[0][2]
assert "🥇 1º, scavalcato morph." in msg, msg
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
assert righe["caio"]["record"] is None, righe["caio"]
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

# --- la previsione in coda anche al !bang e al !bef --------------------------
# Ordine di vjt (#sniffo 2026-09-10 20:40): «facciamo che la previsione della
# prossima suora appare anche nella risposta al !bang e al !bef. stessa
# granularita'» — stessa mezz'ora di `.suore`, e per forza: e' lo stesso numero.
d2 = suore.empty()
sc = "azzurra/#sniffo"
d2["next_spawn"][sc] = 7200.0
sent.clear()
assert not suore.handle(d2, sc, "tizio", "bang", 0.0)
assert "tra due ore" in sent[-1][2], sent
sent.clear()
assert not suore.handle(d2, sc, "tizio", "bef", 0.0)
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
assert not suore.handle(d5, sc, "vjt", "bang", 400.0 + suore.LATE_WINDOW + 1)
riga = sent[-1][2]
assert "stesa" not in riga and "mancata" not in riga, riga
print("ok: chi ha steso la suora non se la sente dire 'mancata'")

print("\nTUTTO VERDE, porco dio")
