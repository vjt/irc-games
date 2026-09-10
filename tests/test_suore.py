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

# la mediana non deve farsi trascinare dal colpo dato dopo mezz'ora
assert suore_stats._mediana([1, 2, 3]) == 2
assert suore_stats._mediana([1, 2, 3, 100]) == 2.5
assert suore_stats._mediana([5]) == 5
print("ok: la mediana regge gli outlier")

print("\nTUTTO VERDE, porco dio")
