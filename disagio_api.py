#!/usr/bin/env python3.11
"""disagio.sniffo.org backend: the !disagio pool in sqlite, blind votes, SSE.

Plan in vjt/irc-games#10. Stdlib only: the convento jail has Python 3.11 and
nothing else. One sqlite file in WAL mode and a fresh connection per request,
so no connection is ever shared between ThreadingHTTPServer's threads.

    DISAGIO_DB          sqlite path (required)
    DISAGIO_BIND        host:port to listen on (default 127.0.0.1:8095)
    DISAGIO_STREAM_SEC  seconds between two frames of /api/stream (default 5)

Commands. Run them as the user the daemon runs as (convento): a db, -wal or
-shm created by root is a db the daemon cannot write.

    disagio_api.py serve
    disagio_api.py import-storico
    disagio_api.py import-candidate FILE --lotto X --modello Y [--tema Z]
    disagio_api.py classifica [--lotto X] [--per-modello]

HTTP. JSON with Cache-Control: no-store, except the stream:

    GET  /api/candidate?votante=T&n=10  candidates T has not voted, random order
    POST /api/voto                      {"id": .., "voto": 1|-1, "votante": T}
    GET  /api/classifica                candidates by score
    GET  /api/frase?lang=it             a random approved phrase
    GET  /api/frase/{nick}?lang=it      the same, {to} replaced
    GET  /api/stream?lang=it            SSE, one phrase every DISAGIO_STREAM_SEC
    GET  /api/health

The votes are blind (lotto zero is Opus against Fable): the model that wrote a
phrase shows up in `classifica --per-modello` and nowhere else, and the lotto
never leaves the db either.
"""
import argparse
import ast
import collections
import ipaddress
import json
import os
import pathlib
import re
import socket
import sqlite3
import string
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

HERE = pathlib.Path(__file__).resolve().parent
LOVE = HERE / "love.py"
SITE = HERE / "web" / "disagio"
# The hand translations of the site, <lang>/frasi.js, same order as love.py.
LANGS = ("en", "es", "fr", "pt", "ru", "zh", "ja")

SCHEMA = """
CREATE TABLE IF NOT EXISTS frasi (
    id INTEGER PRIMARY KEY,
    testo TEXT NOT NULL UNIQUE CHECK (instr(testo, '{to}') > 0),
    tema TEXT,
    stato TEXT NOT NULL CHECK (stato IN ('candidata', 'approvata', 'scartata')),
    lotto TEXT,
    modello TEXT,
    creata TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS voti (
    frase_id INTEGER NOT NULL REFERENCES frasi(id),
    votante TEXT NOT NULL,
    voto INTEGER NOT NULL CHECK (voto IN (-1, 1)),
    ip TEXT,
    quando TEXT NOT NULL,
    PRIMARY KEY (frase_id, votante)
);
CREATE TABLE IF NOT EXISTS traduzioni (
    frase_id INTEGER NOT NULL REFERENCES frasi(id),
    lang TEXT NOT NULL,
    testo TEXT NOT NULL,
    PRIMARY KEY (frase_id, lang)
);
CREATE INDEX IF NOT EXISTS frasi_stato ON frasi (stato);
CREATE INDEX IF NOT EXISTS voti_votante ON voti (votante);
"""

VOTANTE = re.compile(r"[A-Za-z0-9-]{16,64}")
LANG = re.compile(r"[a-z]{2}")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")
NICK_MAX = 32
BODY_MAX = 1024
# A POST body is always read, up to this much, even when the answer is an
# error: closing a socket with unread bytes sends a RST, and a RST can take
# the answer down with it before the client reads it.
DRAIN_MAX = 65536
CANDIDATE_MAX = 50


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def opendb(path):
    """One connection, one transaction: committed on success, rolled back on
    error, closed either way."""
    conn = sqlite3.connect(path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init_db(path):
    with opendb(path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)


def valid_phrase(testo):
    """love.py renders with str.format: {to} must be there and be the only
    field, or the bot dies on a KeyError the day the phrase is approved."""
    try:
        fields = {f for _, f, _, _ in string.Formatter().parse(testo) if f is not None}
    except ValueError:
        return False
    return "{to}" in testo and fields == {"to"}


# ---------------------------------------------------------------- imports

def love_phrases(love=LOVE):
    """DISAGIO_PHRASES, read with ast: importing love.py would pull in the bot."""
    tree = ast.parse(pathlib.Path(love).read_text(encoding="utf-8"))
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and getattr(node.targets[0], "id", None) == "DISAGIO_PHRASES"):
            return ast.literal_eval(node.value)
    raise SystemExit(f"DISAGIO_PHRASES not found in {love}")


def site_translation(site, lang):
    f = pathlib.Path(site) / lang / "frasi.js"
    if not f.exists():
        return None
    text = f.read_text(encoding="utf-8")
    return json.loads(text[text.index("["):text.rindex("]") + 1])


def import_storico(db, love=LOVE, site=SITE):
    """love.py's phrases as approved, lotto «storico», with the site's
    translations. Idempotent: a text already in the db is left alone."""
    ps = love_phrases(love)
    nuove = tradotte = 0
    with opendb(db) as conn:
        for p in ps:
            cur = conn.execute(
                "INSERT INTO frasi (testo, stato, lotto, creata)"
                " VALUES (?, 'approvata', 'storico', ?) ON CONFLICT (testo) DO NOTHING",
                (p, now_iso()))
            nuove += cur.rowcount
        ids = [conn.execute("SELECT id FROM frasi WHERE testo = ?", (p,)).fetchone()[0]
               for p in ps]
        for lang in LANGS:
            tr = site_translation(site, lang)
            # The translations line up with love.py by position only: one of a
            # different length cannot be matched, so it waits for build.py.
            if tr is None or len(tr) != len(ps):
                n = "missing" if tr is None else f"{len(tr)} phrases, Italian has {len(ps)}"
                print(f"{lang}: {n}, skipped", file=sys.stderr)
                continue
            for fid, t in zip(ids, tr):
                cur = conn.execute(
                    "INSERT INTO traduzioni (frase_id, lang, testo) VALUES (?, ?, ?)"
                    " ON CONFLICT (frase_id, lang) DO NOTHING", (fid, lang, t))
                tradotte += cur.rowcount
    return nuove, tradotte


def import_candidate(db, lines, lotto, modello, tema=None):
    """One phrase per line. Returns (imported, duplicates, malformed); blank
    lines count as nothing."""
    nuove = doppie = malformate = 0
    with opendb(db) as conn:
        for riga in lines:
            testo = riga.strip()
            if not testo:
                continue
            if not valid_phrase(testo):
                malformate += 1
                continue
            cur = conn.execute(
                "INSERT INTO frasi (testo, tema, stato, lotto, modello, creata)"
                " VALUES (?, ?, 'candidata', ?, ?, ?) ON CONFLICT (testo) DO NOTHING",
                (testo, tema, lotto, modello, now_iso()))
            if cur.rowcount:
                nuove += 1
            else:
                doppie += 1
    return nuove, doppie, malformate


# ---------------------------------------------------------------- queries

def candidate(conn, votante, n):
    unvoted = ("FROM frasi WHERE stato = 'candidata' AND id NOT IN"
               " (SELECT frase_id FROM voti WHERE votante = ?)")
    rows = conn.execute(f"SELECT id, testo {unvoted} ORDER BY random() LIMIT ?",
                        (votante, n)).fetchall()
    restanti = conn.execute(f"SELECT count(*) {unvoted}", (votante,)).fetchone()[0]
    votate = conn.execute("SELECT count(*) FROM voti WHERE votante = ?",
                          (votante,)).fetchone()[0]
    return {"candidate": [{"id": r["id"], "testo": r["testo"]} for r in rows],
            "restanti": restanti, "votate": votate}


def vota(conn, frase_id, voto, votante, ip):
    """Upsert, candidates only. False when the id is not a candidate."""
    cur = conn.execute(
        "INSERT INTO voti (frase_id, votante, voto, ip, quando)"
        " SELECT id, ?, ?, ?, ? FROM frasi WHERE id = ? AND stato = 'candidata'"
        " ON CONFLICT (frase_id, votante) DO UPDATE"
        " SET voto = excluded.voto, ip = excluded.ip, quando = excluded.quando",
        (votante, voto, ip, now_iso(), frase_id))
    return cur.rowcount > 0


def punteggi(conn, where, args=()):
    return conn.execute(
        "SELECT f.id, f.testo, f.stato, f.modello,"
        " coalesce(sum(v.voto = 1), 0) AS su, coalesce(sum(v.voto = -1), 0) AS giu,"
        " coalesce(sum(v.voto), 0) AS saldo"
        " FROM frasi f LEFT JOIN voti v ON v.frase_id = f.id"
        f" WHERE {where} GROUP BY f.id ORDER BY saldo DESC, su DESC, f.id",
        args).fetchall()


def classifica_web(conn):
    return [{"id": r["id"], "testo": r["testo"], "su": r["su"], "giu": r["giu"],
             "saldo": r["saldo"]} for r in punteggi(conn, "f.stato = 'candidata'")]


def classifica_cli(conn, lotto=None):
    """Candidates, plus whatever got votes and has since been approved or
    thrown out: an A/B result must survive the approval."""
    where = "(f.stato = 'candidata' OR f.id IN (SELECT frase_id FROM voti))"
    if lotto is None:
        return punteggi(conn, where)
    return punteggi(conn, where + " AND f.lotto = ?", (lotto,))


def per_modello(rows):
    tot = collections.defaultdict(lambda: {"frasi": 0, "su": 0, "giu": 0, "saldo": 0})
    for r in rows:
        t = tot[r["modello"] or "?"]
        t["frasi"] += 1
        for k in ("su", "giu", "saldo"):
            t[k] += r[k]
    return dict(sorted(tot.items(), key=lambda kv: -kv[1]["saldo"]))


def frase(conn, lang="it", evita=None):
    """A random approved phrase, translated when there is a translation;
    `lang` in the answer says which one it is. Never `evita` unless it is
    the only one."""
    r = conn.execute(
        "SELECT f.id, f.testo, t.testo AS tradotta FROM frasi f"
        " LEFT JOIN traduzioni t ON t.frase_id = f.id AND t.lang = ?"
        " WHERE f.stato = 'approvata' ORDER BY f.id IS ?, random() LIMIT 1",
        (lang, evita)).fetchone()
    if r is None:
        return None
    if r["tradotta"] is not None:
        return {"id": r["id"], "lang": lang, "testo": r["tradotta"]}
    return {"id": r["id"], "lang": "it", "testo": r["testo"]}


# ---------------------------------------------------------------- HTTP

class Bad(Exception):
    def __init__(self, status, msg):
        super().__init__(msg)
        self.status = status


class RateLimit:
    """At most `limit` hits per key in a sliding `window` of seconds."""

    def __init__(self, limit=300, window=600, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self.hits = {}
        self.lock = threading.Lock()

    def allow(self, key):
        now = self.clock()
        with self.lock:
            q = self.hits.setdefault(key, collections.deque())
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            if len(self.hits) > 10000:
                self.hits = {k: v for k, v in self.hits.items()
                             if v and v[-1] > now - self.window}
            return True


class Server(ThreadingHTTPServer):
    daemon_threads = True
    # server_close() would otherwise join every thread, open streams included.
    block_on_close = False

    def __init__(self, addr, db, stream_sec=5.0, limiter=None, max_streams=64, log=True):
        if ":" in addr[0]:
            self.address_family = socket.AF_INET6
        self.db = db
        self.stream_sec = stream_sec
        self.limiter = limiter or RateLimit()
        # One thread per open stream: a cap, or anyone can eat the jail.
        self.streams = threading.BoundedSemaphore(max_streams)
        self.log = log
        super().__init__(addr, Handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "disagio"
    sys_version = ""
    # A socket that sends nothing for this long is dropped. Covers the
    # stream too: a client that stops reading times the write out.
    timeout = 30

    # -- plumbing

    def client_ip(self):
        # The bind is private, only nginx gets here. nginx appends the address
        # it saw (the real one, after cf-realip) to X-Forwarded-For, so the last
        # entry is the one to trust: whatever comes before it, the client wrote.
        headers = getattr(self, "headers", None)
        last = (headers.get("X-Forwarded-For", "") if headers else "").rsplit(",", 1)[-1]
        try:
            return str(ipaddress.ip_address(last.strip()))
        except ValueError:
            return self.client_address[0]

    def log_message(self, fmt, *args):
        if not self.server.log:
            return
        msg = CONTROL.sub(lambda m: f"\\x{ord(m.group()):02x}", fmt % args)
        sys.stdout.write(f"{now_iso()} {self.client_ip()} {msg}\n")
        sys.stdout.flush()

    def reply(self, status, doc):
        body = json.dumps(doc, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def send_error(self, code, message=None, explain=None):
        # http.server's own errors (bad request line, 501 for PUT...) in JSON too.
        self.close_connection = True
        self.reply(code, {"error": message or HTTPStatus(code).phrase})

    def query(self, url):
        try:
            q = parse_qs(url.query, max_num_fields=20)
        except ValueError:
            raise Bad(400, "query troppo lunga")
        return {k: v[-1] for k, v in q.items()}

    def lang(self, q):
        lang = q.get("lang", "it")
        if not LANG.fullmatch(lang):
            raise Bad(400, "lang: due lettere minuscole")
        return lang

    def dispatch(self, fn):
        try:
            fn()
        except Bad as e:
            self.reply(e.status, {"error": str(e)})
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass
        except Exception as e:  # noqa: BLE001 - one bad request must not kill the thread silently
            self.log_message("error: %r", e)
            self.reply(500, {"error": "errore interno"})

    # -- routes

    def do_GET(self):
        self.dispatch(self.get)

    def do_POST(self):
        self.dispatch(self.post)

    def get(self):
        url = urlsplit(self.path)
        path, q = url.path, self.query(url)
        db = self.server.db
        if path == "/api/health":
            with opendb(db) as conn:
                conn.execute("SELECT 1").fetchone()
            return self.reply(200, {"ok": True})
        if path == "/api/candidate":
            votante = q.get("votante", "")
            if not VOTANTE.fullmatch(votante):
                raise Bad(400, "votante: 16-64 caratteri A-Z a-z 0-9 -")
            try:
                n = max(1, min(int(q.get("n", "10")), CANDIDATE_MAX))
            except ValueError:
                raise Bad(400, "n: un numero")
            with opendb(db) as conn:
                doc = candidate(conn, votante, n)
            return self.reply(200, doc)
        if path == "/api/classifica":
            with opendb(db) as conn:
                doc = {"classifica": classifica_web(conn)}
            return self.reply(200, doc)
        if path == "/api/stream":
            return self.stream(self.lang(q))
        m = re.fullmatch(r"/api/frase(?:/([^/]*))?", path)
        if m:
            lang = self.lang(q)
            nick = None
            if m.group(1) is not None:
                nick = unquote(m.group(1)).strip()
                if not nick or len(nick) > NICK_MAX or CONTROL.search(nick):
                    raise Bad(400, f"nick: da 1 a {NICK_MAX} caratteri")
            with opendb(db) as conn:
                f = frase(conn, lang)
            if f is None:
                raise Bad(404, "nessuna frase approvata")
            if nick is not None:
                f["testo"] = f["testo"].replace("{to}", nick)
                f["nick"] = nick
            return self.reply(200, f)
        raise Bad(404, "non c'è")

    def post(self):
        try:
            n = int(self.headers.get("Content-Length", ""))
        except ValueError:
            raise Bad(411, "manca Content-Length")
        if n < 0:
            raise Bad(400, "Content-Length negativo")
        raw = self.rfile.read(min(n, DRAIN_MAX))
        if urlsplit(self.path).path != "/api/voto":
            raise Bad(404, "non c'è")
        if n > BODY_MAX:
            raise Bad(413, f"corpo oltre {BODY_MAX} byte")
        ip = self.client_ip()
        if not self.server.limiter.allow(ip):
            raise Bad(429, "troppi voti, riprova fra un po'")
        try:
            doc = json.loads(raw)
        except ValueError:
            raise Bad(400, "il corpo non è JSON")
        if not isinstance(doc, dict):
            raise Bad(400, "il corpo non è un oggetto")
        fid, voto, votante = doc.get("id"), doc.get("voto"), doc.get("votante")
        if type(fid) is not int or not 0 < fid < 2 ** 63:
            raise Bad(400, "id: un intero")
        if type(voto) is not int or voto not in (1, -1):
            raise Bad(400, "voto: 1 o -1")
        if not isinstance(votante, str) or not VOTANTE.fullmatch(votante):
            raise Bad(400, "votante: 16-64 caratteri A-Z a-z 0-9 -")
        with opendb(self.server.db) as conn:
            ok = vota(conn, fid, voto, votante, ip)
        if not ok:
            raise Bad(404, "non è una candidata")
        self.reply(200, {"ok": True})

    def stream(self, lang):
        # Same shape as cristobot's /mannaggia/stream: a `data: {json}` frame
        # every stream_sec until the client hangs up.
        if not self.server.streams.acquire(blocking=False):
            raise Bad(503, "troppi stream aperti")
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("X-Accel-Buffering", "no")  # nginx: do not buffer
            self.end_headers()
            ultima = None
            while True:
                with opendb(self.server.db) as conn:
                    f = frase(conn, lang, evita=ultima)
                if f is None:
                    chunk = ": nessuna frase approvata\n\n"
                else:
                    ultima = f["id"]
                    chunk = "data: " + json.dumps(f, ensure_ascii=False) + "\n\n"
                self.wfile.write(chunk.encode("utf-8"))
                time.sleep(self.server.stream_sec)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            pass  # the client hung up: the normal way a stream ends
        except Exception as e:  # noqa: BLE001 - headers are out, a 500 can't follow
            self.log_message("stream error: %r", e)
        finally:
            self.server.streams.release()


# ---------------------------------------------------------------- CLI

def print_classifica(rows):
    print(f"{'id':>5} {'su':>4} {'giù':>4} {'saldo':>5}  {'stato':<9}  testo")
    for r in rows:
        print(f"{r['id']:>5} {r['su']:>4} {r['giu']:>4} {r['saldo']:>+5}  {r['stato']:<9}  {r['testo']}")


def print_per_modello(tot):
    print(f"{'modello':<20} {'frasi':>5} {'su':>5} {'giù':>5} {'saldo':>6} {'media':>6}")
    for m, t in tot.items():
        media = t["saldo"] / t["frasi"] if t["frasi"] else 0
        print(f"{m:<20} {t['frasi']:>5} {t['su']:>5} {t['giu']:>5} {t['saldo']:>+6} {media:>+6.2f}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="disagio.sniffo.org: frasi, voti, SSE")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", help="the HTTP daemon")
    sub.add_parser("import-storico", help="love.py's phrases as approved, idempotent")
    p = sub.add_parser("import-candidate", help="one phrase per line, - for stdin")
    p.add_argument("file")
    p.add_argument("--lotto", required=True)
    p.add_argument("--modello", required=True)
    p.add_argument("--tema")
    p = sub.add_parser("classifica")
    p.add_argument("--lotto")
    p.add_argument("--per-modello", action="store_true")
    a = ap.parse_args(argv)

    db = os.environ.get("DISAGIO_DB")
    if not db:
        raise SystemExit("DISAGIO_DB not set")
    init_db(db)

    if a.cmd == "serve":
        bind = os.environ.get("DISAGIO_BIND") or "127.0.0.1:8095"
        host, _, port = bind.rpartition(":")
        srv = Server((host.strip("[]"), int(port)), db,
                     stream_sec=float(os.environ.get("DISAGIO_STREAM_SEC") or 5))
        print(f"{now_iso()} disagio_api on {bind}, db {db}", flush=True)
        try:
            srv.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            srv.server_close()
    elif a.cmd == "import-storico":
        nuove, tradotte = import_storico(db)
        print(f"storico: {nuove} frasi nuove, {tradotte} traduzioni nuove")
    elif a.cmd == "import-candidate":
        if a.file == "-":
            lines = sys.stdin.read().splitlines()
        else:
            lines = pathlib.Path(a.file).read_text(encoding="utf-8").splitlines()
        nuove, doppie, malformate = import_candidate(db, lines, a.lotto, a.modello, a.tema)
        print(f"importate {nuove}, saltate {doppie + malformate}"
              f" ({doppie} doppie, {malformate} senza {{to}} o con altre graffe)")
    elif a.cmd == "classifica":
        with opendb(db) as conn:
            rows = classifica_cli(conn, a.lotto)
        if a.per_modello:
            print_per_modello(per_modello(rows))
        else:
            print_classifica(rows)


if __name__ == "__main__":
    main()
