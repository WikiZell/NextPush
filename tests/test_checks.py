"""The "does my public address reach NextPush?" check, the GUI's static files and the strict content-security-policy."""
from __future__ import annotations

import json
import re
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import helpers
from helpers import TempCase
from nextpush import checks
from test_admin_api import PASSWORD, AdminCase


class Scripted(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, status: int, body: bytes = b"") -> None:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                outer.seen.append((self.path, self.headers.get("X-Pull-Key")))
                self.send_response(outer.status)
                self.send_header("Content-Length", str(len(outer.body)))
                self.end_headers()
                self.wfile.write(outer.body)

            def log_message(self, *args) -> None:
                pass

        self.status, self.body, self.seen = status, body, []
        super().__init__(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.serve_forever, daemon=True).start()

    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


def doc(name: str = "Home") -> bytes:
    return json.dumps({"nextpush": 1, "name": name, "dav": "/dav", "auth": "basic"}).encode()


class CheckPublic(TempCase):
    def run_check(self, status: int, body: bytes = b"", name: str = "Home", url: str | None = None) -> dict:
        server = Scripted(status, body)
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        result = checks.check_public(url or server.url(), "X-Pull-Key", "secret", name, timeout=3)
        self.assertEqual(server.seen[0] if server.seen else ("/.well-known/nextpush", "secret"), ("/.well-known/nextpush", "secret"))
        return result

    def test_a_working_address_is_reported_ok(self) -> None:
        result = self.run_check(200, doc())
        self.assertEqual((result["ok"], result["level"], result["status"]), (True, "ok", 200))

    def test_the_wrong_server_the_wrong_page_and_a_dead_proxy(self) -> None:
        self.assertEqual(self.run_check(200, doc("Someone else"))["level"], "warn")
        self.assertFalse(self.run_check(200, doc("Someone else"))["ok"])
        self.assertIn("not NextPush", self.run_check(404)["detail"])
        bad_gateway = self.run_check(502)
        self.assertEqual((bad_gateway["ok"], bad_gateway["level"]), (False, "bad"))
        self.assertFalse(self.run_check(200, b"<html>not json</html>")["ok"])

    def test_nothing_listening_and_nonsense_addresses(self) -> None:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        for url in (f"http://127.0.0.1:{port}", "https://nonexistent-host.invalid"):
            result = checks.check_public(url, "", "", timeout=3)
            self.assertFalse(result["ok"], url)
            self.assertEqual(result["level"], "bad")
        self.assertEqual(checks.check_public("", "", "")["level"], "warn")
        self.assertEqual(checks.check_public("ftp://x.example.com", "", "")["level"], "bad")

    def test_plain_http_on_the_internet_is_a_warning_not_a_success(self) -> None:
        self.assertEqual(checks._is_local("192.168.1.5"), True)
        self.assertEqual(checks._is_local("dl.example.com"), False)
        self.assertEqual(checks._is_local("nas"), True)


class CheckApi(AdminCase):
    def test_the_api_needs_a_session_and_the_csrf_header(self) -> None:
        self.assertEqual(self.client.post("/api/check-public", {})[0], 401)
        self.setup_done(public_url="")
        status, body, _ = self.client.post("/api/check-public", {}, csrf=False)
        self.assertEqual(status, 403)
        status, body, _ = self.client.post("/api/check-public", {})
        self.assertEqual((status, body["ok"], body["level"]), (200, False, "warn"))

    def test_it_asks_the_public_address_with_the_access_key_and_never_returns_the_key(self) -> None:
        server = Scripted(200, doc("Home"))
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        self.setup_done(public_url="")
        self.app.settings.update(public_url=server.url())
        status, body, _ = self.client.post("/api/check-public", {})
        self.assertEqual((status, body["ok"]), (200, True), body)
        key = self.app.settings.get("access_key")
        self.assertEqual(server.seen, [("/.well-known/nextpush", key["value"])])
        self.assertNotIn(key["value"], json.dumps(body))

    def test_without_the_key_nothing_is_sent(self) -> None:
        server = Scripted(200, doc("Home"))
        self.addCleanup(lambda: (server.shutdown(), server.server_close()))
        self.setup_done(public_url="")
        self.app.settings.update(public_url=server.url())
        self.client.put("/api/settings", {"access_key_enabled": False})
        self.client.post("/api/check-public", {})
        self.assertEqual(server.seen, [("/.well-known/nextpush", None)])


class GuiFiles(AdminCase):
    def test_every_file_the_page_loads_is_served(self) -> None:
        html = (helpers.ROOT / "nextpush" / "web" / "index.html").read_text(encoding="utf-8")
        refs = re.findall(r'(?:src|href)="([^"#]+)"', html)
        local = [r for r in refs if not r.startswith(("http:", "https:", "#"))]
        self.assertGreaterEqual(len(local), 4)
        for ref in local:
            status, _, response = self.client.get("/" + ref)
            self.assertEqual(status, 200, ref)

    def test_the_content_security_policy_allows_no_inline_code_or_style(self) -> None:
        _, _, response = self.client.get("/")
        policy = response.getheader("Content-Security-Policy")
        self.assertNotIn("unsafe-inline", policy)
        self.assertNotIn("unsafe-eval", policy)
        self.assertIn("script-src 'self'", policy)
        self.assertIn("frame-ancestors 'none'", policy)

    def test_the_gui_sources_use_no_inline_style_or_event_handlers(self) -> None:
        web = helpers.ROOT / "nextpush" / "web"
        for name in ("index.html", "app.js", "theme.js"):
            text = (web / name).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r'\sstyle="', text), f"{name}: style attribute is blocked by the CSP")
            self.assertIsNone(re.search(r'\son[a-z]+="', text), f"{name}: inline event handler")
        html = (web / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<style", html)
        self.assertEqual(re.findall(r"<script(?![^>]*\ssrc=)[^>]*>", html), [], "no inline script")
        self.assertNotIn("setAttribute(\"style\"", (web / "app.js").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
