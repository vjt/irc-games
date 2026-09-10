"""Unwrap del relay Telegram quando il prefisso arriva COLORATO.

Gazzurbo dipinge il prefisso, quindi la riga bridged non e' "<Nick> testo" ma
"\x0350<\x02Nick\x02> \x0ftesto": BRIDGE_PREFIX_PAT e' ancorata su '<' nudo e
non matchava, il relay restava classificato bot classico e finiva in classifica
(#endsummercamp, segnalato da vjt il 2026-09-10). Misurato sull'archivio prod:
4643 righe su 5000 col codice colore.
"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "grappa_stats"))
import ingest

cfg = ingest.load_config()


def resolve(text, nick="Gazzurbo", direction="<", target="#endsummercamp"):
    return ingest.resolve(cfg, {"direction": direction, "nick": nick, "text": text,
                                "target": target, "is_action": False,
                                "origin": None})


def check(label, got, want):
    assert got == want, "%s: %r != %r" % (label, got, want)
    print("ok:", label)


# --- il caso che si era rotto: colore sul prefisso + bold sul nick
check("prefisso colorato: unwrap all'umano di dentro, transport telegram",
      resolve("\x0350<\x02MG95Tux\x02> \x0fCi porta noia")[:3],
      ("MG95Tux", "human", "telegram"))

check("colore con background fg,bg",
      resolve("\x0303,01<\x02chic_luke\x02> \x0fUniVR")[:3],
      ("chic_luke", "human", "telegram"))

check("\\x03 nudo senza cifre",
      resolve("\x03<pippo> ciao")[:3], ("pippo", "human", "telegram"))

# --- e quello che gia' funzionava, che non deve rompersi
check("prefisso nudo, come prima",
      resolve("<plainnick> ciao")[:3], ("plainnick", "human", "telegram"))

cfg["relay_carry"].clear()  # niente coda in sospeso da ereditare
check("riga del relay senza prefisso e senza testa: resta il relay, bot classico",
      resolve("il bridge parla da solo")[:3], ("Gazzurbo", "classic", "irc"))

check("un umano qualsiasi non passa dall'unwrap",
      resolve("ciao", nick="vjt")[:3], ("vjt", "human", "irc"))

# --- lo zero-width dentro al nick (anti-highlight) va via lo stesso
check("zero-width nel nick, con e senza colore",
      resolve("\x0350<\x02M​ez\x02> \x0fciao")[:3],
      ("Mez", "human", "telegram"))

# --- code dei messaggi lunghi spezzati da matterbridge (ordine di vjt 2026-09-10
#     23:46: «va bene ok aggiungi»). Solo la testa tiene il prefisso, la coda
#     arriva nuda ~60ms dopo e porta lei il marcatore "(more)".
cfg["relay_carry"].clear()
check("la testa dello split srotola normale",
      resolve("\x0350<\x02packetfilter\x02> \x0fDomanda stupida: ma se")[:3],
      ("packetfilter", "human", "telegram"))
check("la coda nuda va a chi era la testa",
      resolve("mercato dei PC? maggiore consapevolezza (more)")[:3],
      ("packetfilter", "human", "telegram"))
check("anche la coda della coda",
      resolve("e poi il resto")[:3], ("packetfilter", "human", "telegram"))
check("una testa nuova sposta la carry",
      resolve("<Besrixa> altro")[:3], ("Besrixa", "human", "telegram"))
check("e la coda dopo va a lei, non al precedente",
      resolve("coda di Besrixa")[:3], ("Besrixa", "human", "telegram"))

check("la carry e' per canale: altro canale, nessuna eredita'",
      resolve("coda orfana", target="#altro")[:3],
      ("Gazzurbo", "classic", "irc"))

cfg["relay_carry"].clear()
check("coda senza testa: resta il relay, niente nick inventato",
      resolve("coda orfana")[:3], ("Gazzurbo", "classic", "irc"))

check("il messaggio di sistema del bridge non va all'umano precedente",
      (resolve("<pippo> ciao"),
       resolve("<vjt’s IRC bridge> joined chat"))[1][:3],
      ("Gazzurbo", "classic", "irc"))

# --- strip_formatting non deve mangiarsi il testo
check("strip_formatting toglie i codici e lascia il resto",
      ingest.strip_formatting("\x0350<\x02Nick\x02> \x0ftesto \x1dvero\x0f"),
      "<Nick> testo vero")

print("\nTUTTO VERDE, porco dio")
