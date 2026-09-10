"""Prova a secco del refactor multi-rete/multi-canale. Nessuna FIFO, nessun IRC:
say() intercettata, log finti. Verifica quello che prima NON funzionava."""
import json, os, sys, tempfile, time

tmp = tempfile.mkdtemp()
os.environ["SUORE_STATE"] = os.path.join(tmp, "state.json")
os.environ["SUORE_NETS"] = (
    f"azzurra|{tmp}/a.log||#sniffo:90-240,#sbiffo:60-180;"
    f"ircnet|{tmp}/i.log||#cybernet:90-240")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import suore

sent = []
suore.say = lambda net, chan, text: sent.append((net.name, chan, text))

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
assert [s["nick"] for s in suore.killers_rank(data, scope)] == \
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

# il .bef non tocca la classifica dei killer: nessuna coda appiccicata
suore.spawn(data, scope, now + 40)
sent.clear()
assert suore.handle(data, scope, "katia", "bef", now + 50)
assert "º" not in sent[0][2], sent[0][2]
print("ok: l'amicizia resta un'altra partita, niente posizione appiccicata")

print("\nTUTTO VERDE, porco dio")
