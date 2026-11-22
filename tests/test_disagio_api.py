"""disagio_api.py and build.py's tolerant translations (vjt/irc-games#10).

Stdlib only, like the jail: a real server on an ephemeral port in a thread,
the db in a tempdir, love.py and the site's translations read where they are.

    python3.11 -m unittest -v tests/test_disagio_api.py
"""
import http.client
import importlib.util
import io
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import disagio_api as D  # noqa: E402

T1 = "0f8fad5b-d9cb-469f-a165-70867728950e"
T2 = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
T3 = "16fd2706-8baf-433b-82eb-8c7fada847da"


class DbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "disagio.db")
        D.init_db(self.db)

    def tearDown(self):
        self.tmp.cleanup()

    def sql(self, q, args=()):
        with D.opendb(self.db) as conn:
            return [tuple(r) for r in conn.execute(q, args).fetchall()]

    def approvata(self, testo, **tradotte):
        with D.opendb(self.db) as conn:
            fid = conn.execute(
                "INSERT INTO frasi (testo, stato, lotto, creata)"
                " VALUES (?, 'approvata', 'storico', 'oggi')", (testo,)).lastrowid
            for lang, t in tradotte.items():
                conn.execute("INSERT INTO traduzioni VALUES (?, ?, ?)", (fid, lang, t))
        return fid

    def candidate(self, *testi, lotto="lotto-segreto", modello="modello-segreto"):
        D.import_candidate(self.db, testi, lotto, modello)
        return [self.sql("SELECT id FROM frasi WHERE testo = ?", (t,))[0][0] for t in testi]


class SchemaAndImport(DbCase):
    def test_schema_wal_and_constraints(self):
        tables = {r[0] for r in self.sql("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertTrue({"frasi", "voti", "traduzioni"} <= tables)
        self.assertEqual(self.sql("PRAGMA journal_mode"), [("wal",)])
        bad = [
            ("INSERT INTO frasi (testo, stato, creata) VALUES ('niente nick', 'candidata', 'x')", ()),
            ("INSERT INTO frasi (testo, stato, creata) VALUES ('{to} boh', 'forse', 'x')", ()),
            ("INSERT INTO voti VALUES (999, ?, 1, NULL, 'x')", (T1,)),  # foreign key
        ]
        for q, args in bad:
            with self.assertRaises(sqlite3.IntegrityError, msg=q):
                self.sql(q, args)
        (fid,) = self.candidate("{to} ha un voto solo")
        with self.assertRaises(sqlite3.IntegrityError):
            self.sql("INSERT INTO voti VALUES (?, ?, 2, NULL, 'x')", (fid, T1))
        D.init_db(self.db)  # twice is fine

    def test_import_storico_is_idempotent_and_brings_translations(self):
        ps = D.love_phrases()
        self.assertGreaterEqual(len(ps), 180)
        with redirect_stdout(io.StringIO()):
            nuove, tradotte = D.import_storico(self.db)
        self.assertEqual(nuove, len(ps))
        rows = self.sql("SELECT testo, stato, lotto FROM frasi ORDER BY id")
        self.assertEqual([r[0] for r in rows], ps)
        self.assertEqual({(r[1], r[2]) for r in rows}, {("approvata", "storico")})
        aligned = [lang for lang in D.LANGS
                   if len(D.site_translation(D.SITE, lang) or ()) == len(ps)]
        self.assertEqual(tradotte, len(ps) * len(aligned))
        if "en" in aligned:
            en = D.site_translation(D.SITE, "en")
            self.assertEqual(
                self.sql("SELECT t.testo FROM traduzioni t JOIN frasi f ON f.id = t.frase_id"
                         " WHERE t.lang = 'en' AND f.testo = ?", (ps[0],)), [(en[0],)])
        self.assertEqual(D.import_storico(self.db), (0, 0))
        self.assertEqual(self.sql("SELECT count(*) FROM frasi"), [(len(ps),)])

    def test_import_candidate_skips_duplicates_and_malformed(self):
        lines = ["{to} uno", "  {to} due  ", "", "{to} uno", "niente nick",
                 "{to} e {from}", "{to} graffa {", "{{to}} finto"]
        got = D.import_candidate(self.db, lines, "zero", "opus", tema="casa")
        self.assertEqual(got, (2, 1, 4))
        self.assertEqual(
            self.sql("SELECT testo, stato, lotto, modello, tema FROM frasi ORDER BY id"),
            [("{to} uno", "candidata", "zero", "opus", "casa"),
             ("{to} due", "candidata", "zero", "opus", "casa")])
        # a candidate already in the pool as approved stays approved
        self.approvata("{to} vecchia")
        self.assertEqual(D.import_candidate(self.db, ["{to} vecchia"], "uno", "fable"), (0, 1, 0))

    def test_classifica_cli_and_per_modello(self):
        a, b = self.candidate("{to} a", "{to} b", lotto="zero", modello="opus")
        (c,) = self.candidate("{to} c", lotto="zero", modello="fable")
        (d,) = self.candidate("{to} d", lotto="uno", modello="opus")
        self.approvata("{to} storica")
        with D.opendb(self.db) as conn:
            for fid, voto, votante in [(a, 1, T1), (a, 1, T2), (b, -1, T1), (c, 1, T1),
                                       (c, -1, T2), (c, -1, T3)]:
                self.assertTrue(D.vota(conn, fid, voto, votante, "127.0.0.1"))
            # approved after the vote: still in the A/B count
            conn.execute("UPDATE frasi SET stato = 'approvata' WHERE id = ?", (a,))
            rows = D.classifica_cli(conn, "zero")
            everything = D.classifica_cli(conn)
        # same score: more thumbs up first
        self.assertEqual([(r["id"], r["su"], r["giu"], r["saldo"]) for r in rows],
                         [(a, 2, 0, 2), (c, 1, 2, -1), (b, 0, 1, -1)])
        self.assertNotIn("{to} storica", [r["testo"] for r in everything])
        self.assertIn(d, [r["id"] for r in everything])
        self.assertEqual(D.per_modello(rows), {
            "opus": {"frasi": 2, "su": 2, "giu": 1, "saldo": 1},
            "fable": {"frasi": 1, "su": 1, "giu": 2, "saldo": -1}})


class ServerCase(DbCase):
    limit = 300

    def setUp(self):
        super().setUp()
        self.srv = D.Server(("127.0.0.1", 0), self.db, stream_sec=0.05,
                            limiter=D.RateLimit(self.limit, 600), log=False)
        self.port = self.srv.server_address[1]
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        super().tearDown()

    def call(self, method, path, body=None, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            c.request(method, path, body=body, headers=headers or {})
            r = c.getresponse()
            raw = r.read()
        finally:
            c.close()
        return r.status, r.headers, raw

    def get(self, path, headers=None):
        status, h, raw = self.call("GET", path, headers=headers)
        return status, h, json.loads(raw)

    def vote(self, fid, voto, votante=T1, ip=None):
        h = {"Content-Type": "application/json"}
        if ip:
            h["X-Forwarded-For"] = ip
        body = json.dumps({"id": fid, "voto": voto, "votante": votante})
        status, _, raw = self.call("POST", "/api/voto", body, h)
        return status, json.loads(raw)


class Api(ServerCase):
    def test_candidate_never_show_model_or_lotto(self):
        ids = self.candidate("{to} uno", "{to} due", "{to} tre")
        self.approvata("{to} approvata")
        status, h, raw = self.call("GET", f"/api/candidate?votante={T1}&n=10")
        self.assertEqual(status, 200)
        self.assertEqual(h["Cache-Control"], "no-store")
        self.assertNotIn(b"segreto", raw)
        doc = json.loads(raw)
        self.assertEqual(sorted(f["id"] for f in doc["candidate"]), sorted(ids))
        for f in doc["candidate"]:
            self.assertEqual(set(f), {"id", "testo"})
        self.assertEqual((doc["restanti"], doc["votate"]), (3, 0))
        # voted ones disappear for that voter only
        self.assertEqual(self.vote(ids[0], 1)[0], 200)
        _, _, doc = self.get(f"/api/candidate?votante={T1}")
        self.assertNotIn(ids[0], [f["id"] for f in doc["candidate"]])
        self.assertEqual((doc["restanti"], doc["votate"]), (2, 1))
        _, _, doc = self.get(f"/api/candidate?votante={T2}")
        self.assertEqual(len(doc["candidate"]), 3)
        _, _, doc = self.get(f"/api/candidate?votante={T2}&n=1")
        self.assertEqual(len(doc["candidate"]), 1)
        for bad in ("", "votante=corto", f"votante={T1}_", f"votante={'a' * 65}",
                    f"votante={T1}&n=tanti"):
            self.assertEqual(self.get("/api/candidate?" + bad)[0], 400, bad)

    def test_vote_is_an_upsert_with_the_forwarded_ip(self):
        (fid,) = self.candidate("{to} indeciso")
        self.assertEqual(self.vote(fid, 1), (200, {"ok": True}))
        self.assertEqual(self.vote(fid, -1, ip="6.6.6.6, 192.0.2.7"), (200, {"ok": True}))
        self.assertEqual(self.sql("SELECT frase_id, votante, voto, ip FROM voti"),
                         [(fid, T1, -1, "192.0.2.7")])
        self.assertEqual(self.vote(fid, 1, votante=T2, ip="bogus"), (200, {"ok": True}))
        self.assertEqual(self.sql("SELECT ip FROM voti WHERE votante = ?", (T2,)),
                         [("127.0.0.1",)])

    def test_vote_validation(self):
        (fid,) = self.candidate("{to} candidata")
        approved = self.approvata("{to} approvata")
        for votante in ("a" * 15, "a" * 65, T1 + "!", "spazi non validi xx", None, 12345678901234567):
            self.assertEqual(self.vote(fid, 1, votante=votante)[0], 400, votante)
        self.assertEqual(self.vote(fid, 1, votante="a" * 16)[0], 200)
        self.assertEqual(self.vote(fid, 1, votante="A-" * 32)[0], 200)
        for voto in (0, 2, -2, True, "1", 1.0, None):
            self.assertEqual(self.vote(fid, voto)[0], 400, voto)
        for bad_id in (True, "1", 1.5, None, 0, 2 ** 70):
            self.assertEqual(self.vote(bad_id, 1)[0], 400, bad_id)
        self.assertEqual(self.vote(approved, 1)[0], 404)
        self.assertEqual(self.vote(999999, 1)[0], 404)
        self.assertEqual(self.call("POST", "/api/voto", b"non json")[0], 400)
        self.assertEqual(self.call("POST", "/api/voto", b"[1, 2]")[0], 400)
        big = json.dumps({"id": fid, "voto": 1, "votante": T1, "x": "y" * 2000})
        self.assertEqual(self.call("POST", "/api/voto", big)[0], 413)
        self.assertEqual(self.sql("SELECT count(*) FROM voti WHERE frase_id = ?", (approved,)),
                         [(0,)])

    def test_classifica_sorted_by_score_without_model(self):
        a, b, c = self.candidate("{to} a", "{to} b", "{to} c")
        self.approvata("{to} fuori gara")
        for fid, voto, votante in [(b, 1, T1), (b, 1, T2), (c, -1, T1), (a, 1, T1), (a, -1, T2)]:
            self.assertEqual(self.vote(fid, voto, votante)[0], 200)
        status, h, raw = self.call("GET", "/api/classifica")
        self.assertEqual((status, h["Cache-Control"]), (200, "no-store"))
        self.assertNotIn(b"segreto", raw)
        cl = json.loads(raw)["classifica"]
        self.assertEqual([(f["id"], f["su"], f["giu"], f["saldo"]) for f in cl],
                         [(b, 2, 0, 2), (a, 1, 1, 0), (c, 0, 1, -1)])
        self.assertEqual(set(cl[0]), {"id", "testo", "su", "giu", "saldo"})

    def test_frase_translated_or_italian(self):
        self.assertEqual(self.get("/api/frase")[0], 404)
        fid = self.approvata("{to} mangia in piedi", en="{to} eats standing up")
        self.candidate("{to} candidata, mai servita")
        self.assertEqual(self.get("/api/frase")[2],
                         {"id": fid, "lang": "it", "testo": "{to} mangia in piedi"})
        self.assertEqual(self.get("/api/frase?lang=en")[2],
                         {"id": fid, "lang": "en", "testo": "{to} eats standing up"})
        self.assertEqual(self.get("/api/frase?lang=fr")[2]["lang"], "it")
        status, h, doc = self.get("/api/frase/ciccio%20bello?lang=en")
        self.assertEqual((status, h["Cache-Control"]), (200, "no-store"))
        self.assertEqual((doc["testo"], doc["nick"]), ("ciccio bello eats standing up", "ciccio bello"))
        self.assertEqual(self.get("/api/frase/" + "x" * 32)[0], 200)
        for bad in ("/api/frase/" + "x" * 33, "/api/frase/", "/api/frase/a%0Ab",
                    "/api/frase?lang=ENG", "/api/frase/x?lang=e"):
            self.assertEqual(self.get(bad)[0], 400, bad)

    def test_stream(self):
        a = self.approvata("{to} primo")
        b = self.approvata("{to} secondo", ru="{to} второй")
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            c.request("GET", "/api/stream?lang=ru")
            r = c.getresponse()
            self.assertEqual(r.status, 200)
            self.assertTrue(r.headers["Content-Type"].startswith("text/event-stream"))
            frames = []
            while len(frames) < 4:
                line = r.fp.readline().decode("utf-8")
                if line.startswith("data: "):
                    frames.append(json.loads(line[6:]))
        finally:
            c.close()
        # never the same phrase twice in a row: with two, they alternate
        self.assertTrue(all(x["id"] != y["id"] for x, y in zip(frames, frames[1:])), frames)
        expected = {a: {"id": a, "lang": "it", "testo": "{to} primo"},
                    b: {"id": b, "lang": "ru", "testo": "{to} второй"}}
        for f in frames:
            self.assertEqual(f, expected[f["id"]])

    def test_health_and_everything_else(self):
        self.assertEqual(self.get("/api/health")[::2], (200, {"ok": True}))
        for path in ("/", "/api", "/api/voto", "/api/frasi.json", "/api/health/x"):
            status, h, doc = self.get(path)
            self.assertEqual(status, 404, path)
            self.assertIn("error", doc)
        self.assertEqual(self.call("POST", "/api/classifica", b"{}")[0], 404)
        # no body: http.server answers 501 without reading it
        status, h, raw = self.call("PUT", "/api/voto")
        self.assertEqual(status, 501)
        self.assertIn("error", json.loads(raw))


class RateLimited(ServerCase):
    limit = 3

    def test_votes_per_ip(self):
        ids = self.candidate("{to} 1", "{to} 2", "{to} 3", "{to} 4")
        for fid in ids[:3]:
            self.assertEqual(self.vote(fid, 1, ip="192.0.2.1")[0], 200)
        self.assertEqual(self.vote(ids[3], 1, ip="192.0.2.1")[0], 429)
        self.assertEqual(self.vote(ids[3], 1, votante=T2, ip="192.0.2.1")[0], 429)
        self.assertEqual(self.vote(ids[3], 1, ip="192.0.2.2")[0], 200)
        # the left side of X-Forwarded-For is the client's: it changes nothing
        self.assertEqual(self.vote(ids[3], 1, ip="10.0.0.9, 192.0.2.1")[0], 429)

    def test_window_slides(self):
        now = [0.0]
        rl = D.RateLimit(2, 600, clock=lambda: now[0])
        self.assertTrue(rl.allow("x") and rl.allow("x"))
        self.assertFalse(rl.allow("x"))
        self.assertTrue(rl.allow("y"))
        now[0] = 600.5
        self.assertTrue(rl.allow("x"))


def load_build():
    spec = importlib.util.spec_from_file_location(
        "disagio_build", ROOT / "web" / "disagio" / "build.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class BuildTolerant(unittest.TestCase):
    ITA = ["{to} uno", "{to} due", "{to} tre"]

    def site(self, root, **pools):
        for lang in load_build().LANGS:
            d = pathlib.Path(root) / lang
            d.mkdir()
            ps = pools.get(lang, [f"{lang} {p}" for p in self.ITA])
            (d / "frasi.js").write_text(
                "// hand made\nwindow.DISAGIO = " + json.dumps(ps, ensure_ascii=False) + ";\n",
                encoding="utf-8")

    def test_a_language_behind_falls_back_to_italian(self):
        build = load_build()
        with tempfile.TemporaryDirectory() as root:
            self.site(root, en=["{to} one", "{to} two"])
            out = io.StringIO()
            with redirect_stdout(out):
                pools = build.check_translations(self.ITA, root=pathlib.Path(root))
        self.assertEqual(pools["en"], ["{to} one", "{to} two", "{to} tre"])
        self.assertEqual(pools["es"], [f"es {p}" for p in self.ITA])
        self.assertEqual(set(pools), set(build.LANGS))
        warnings = out.getvalue().splitlines()
        self.assertEqual(len(warnings), 1, warnings)
        self.assertIn("en", warnings[0])

    def test_still_stops_when_the_order_cannot_line_up(self):
        build = load_build()
        for bad in (self.ITA + ["{to} quattro"], ["{to} one", "no nick", "{to} three"]):
            with tempfile.TemporaryDirectory() as root:
                self.site(root, fr=bad)
                with self.assertRaises(SystemExit):
                    build.check_translations(self.ITA, root=pathlib.Path(root))

    def test_the_real_site_still_builds(self):
        build = load_build()
        ps = build.phrases()
        with redirect_stdout(io.StringIO()):
            pools = build.check_translations(ps)
        for lang in build.LANGS:
            self.assertEqual(len(pools[lang]), len(ps), lang)


if __name__ == "__main__":
    unittest.main()
