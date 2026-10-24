"""Anteprima dei link generici di Ambrogio (vjt, #sbiffo 2026-10-24 21:47).

Il punto non e' il parser, e' il "safe": niente indirizzi privati, nemmeno
dietro un redirect, redirect contati, solo HTML, niente byte di controllo
nella riga che finisce nella FIFO.
"""
import http.server
import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import yt_preview as yp  # noqa: E402

PAGE = (b'<html><head><meta charset="utf-8"><title>Titolo\r\nPRIVMSG #x :pwn'
        b'</title><meta property="og:description" content="Descrizione &amp; '
        b'basta\x03"><script>alert(1)</script></head><body>x</body></html>')


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path.startswith("/hop/"):
            n = int(self.path.rsplit("/", 1)[1])
            self.send_response(302)
            self.send_header("Location", f"/hop/{n - 1}" if n else "/page")
            self.end_headers()
        elif self.path == "/evil":
            self.send_response(302)
            self.send_header("Location", "http://internal.test/page")
            self.end_headers()
        elif self.path == "/bin":
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.end_headers()
            self.wfile.write(b"\0" * 10)
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(PAGE)


@pytest.fixture
def server(monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(yp, "LINK_PORTS", {"http": port, "https": 443})

    def resolve(host, p):
        # public.test e' "pubblico" e porta al server di prova; tutto il
        # resto passa dal controllo vero.
        if host == "public.test":
            return ["127.0.0.1"]
        return yp.public_addresses(host, p)
    yield f"http://public.test:{port}", resolve
    srv.shutdown()


def test_title_and_description_with_no_control_bytes(server):
    base, resolve = server
    line = yp.link_preview(base + "/page", resolve=resolve)
    assert "Titolo PRIVMSG #x :pwn" in line
    assert "Descrizione & basta" in line
    tail = line.split(yp.LINK_BADGE, 1)[1]
    assert not any(ord(c) < 32 for c in tail)
    assert "alert" not in line


def test_redirects_are_followed_up_to_the_limit(server):
    base, resolve = server
    assert yp.link_preview(f"{base}/hop/{yp.MAX_REDIRECTS - 1}", resolve=resolve)
    with pytest.raises(yp.LinkRefused):
        yp.link_preview(f"{base}/hop/{yp.MAX_REDIRECTS}", resolve=resolve)


def test_redirect_to_a_private_name_is_refused(server, monkeypatch):
    base, resolve = server
    real = yp.socket.getaddrinfo
    # Solo internal.test e' finto: il connect al server di prova usa il vero.
    monkeypatch.setattr(yp.socket, "getaddrinfo", lambda host, *a, **k: [
        (2, 1, 6, "", ("10.0.0.5", 80))] if host == "internal.test"
        else real(host, *a, **k))
    with pytest.raises(yp.LinkRefused, match="not global"):
        yp.link_preview(base + "/evil", resolve=resolve)


def test_not_html_is_refused(server):
    base, resolve = server
    with pytest.raises(yp.LinkRefused):
        yp.link_preview(base + "/bin", resolve=resolve)


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.1.2.3", "192.168.1.1",
                                "169.254.169.254", "::1", "fd00::1",
                                "::ffff:127.0.0.1", "100.64.0.1"])
def test_private_addresses_are_refused(monkeypatch, ip):
    monkeypatch.setattr(yp.socket, "getaddrinfo", lambda *a, **k: [
        (10 if ":" in ip else 2, 1, 6, "", (ip, 80))])
    with pytest.raises(yp.LinkRefused):
        yp.public_addresses("x.test", 80)


def test_one_private_record_spoils_the_name(monkeypatch):
    monkeypatch.setattr(yp.socket, "getaddrinfo", lambda *a, **k: [
        (2, 1, 6, "", ("8.8.8.8", 80)), (2, 1, 6, "", ("127.0.0.1", 80))])
    with pytest.raises(yp.LinkRefused):
        yp.public_addresses("x.test", 80)


@pytest.mark.parametrize("url", ["ftp://example.com/", "file:///etc/passwd",
                                 "http://example.com:22/", "gopher://x/"])
def test_wrong_scheme_or_port_is_refused_before_any_lookup(monkeypatch, url):
    monkeypatch.setattr(yp.socket, "getaddrinfo", lambda *a, **k: 1 / 0)
    with pytest.raises(yp.LinkRefused):
        yp.page_fetch(url)


def test_first_link_skips_youtube_and_trims_punctuation():
    text = "guarda https://youtu.be/dQw4w9WgXcQ e poi https://example.com/a?b=1)."
    assert yp.first_link(text) == "https://example.com/a?b=1"
    assert yp.first_link("solo https://www.youtube.com/watch?v=dQw4w9WgXcQ") is None
    assert yp.first_link("niente link qui") is None


def test_languorino_matches_the_word_not_its_neighbours():
    assert yp.LANGUORINO_PAT.search("Ambrogio, avverto un leggero Languorino")
    assert yp.LANGUORINO_PAT.search("che languorini stasera")
    assert not yp.LANGUORINO_PAT.search("languore")
    assert all(not yp.CONTROL_PAT.search(x) for x in yp.LANGUORINO)
