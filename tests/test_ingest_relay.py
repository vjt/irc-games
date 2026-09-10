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


def resolve(text, nick="Gazzurbo", direction="<"):
    return ingest.resolve(cfg, {"direction": direction, "nick": nick, "text": text,
                                "is_action": False, "origin": None})


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

check("riga del relay senza prefisso: resta il relay, bot classico",
      resolve("il bridge parla da solo")[:3], ("Gazzurbo", "classic", "irc"))

check("un umano qualsiasi non passa dall'unwrap",
      resolve("ciao", nick="vjt")[:3], ("vjt", "human", "irc"))

# --- lo zero-width dentro al nick (anti-highlight) va via lo stesso
check("zero-width nel nick, con e senza colore",
      resolve("\x0350<\x02M​ez\x02> \x0fciao")[:3],
      ("Mez", "human", "telegram"))

# --- strip_formatting non deve mangiarsi il testo
check("strip_formatting toglie i codici e lascia il resto",
      ingest.strip_formatting("\x0350<\x02Nick\x02> \x0ftesto \x1dvero\x0f"),
      "<Nick> testo vero")

print("\nTUTTO VERDE, porco dio")
