"""The admin listener without SFTPGo: setup, sign-in, sessions, CSRF, lock-out, settings, static files and what never leaks."""
from __future__ import annotations

import http.client
import json
import logging
import time
import unittest
from unittest import mock

import helpers
from helpers import Client, TempCase
from nextpush import security
from nextpush.app import App

PASSWORD = "a long and unusual admin passphrase"
FAST_HASH = lambda password, iterations=1000: security.hash_password(password, iterations)   # noqa: E731 - the real cost is for production only


class AdminCase(TempCase):
    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch("nextpush.admin.hash_password", FAST_HASH)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.app = App(self.tmp / "data", binary=None, admin_port=0, public_port=0, admin_host="127.0.0.1", public_host="127.0.0.1", quiet=True)
        self.app.start()
        self.addCleanup(self.app.stop)
        self.client = Client(self.app.admin_address[1])

    def setup_done(self, **extra) -> dict:
        status, body, _ = self.client.post("/api/setup", {"token": self.app.setup_token(), "password": PASSWORD, "server_name": "Home", "public_url": "https://dl.example.com", **extra})
        self.assertEqual(status, 200, body)
        self.client.csrf = body["csrf"]
        return body


class StaticFiles(AdminCase):
    def test_the_page_and_its_files_are_served_with_security_headers(self) -> None:
        for path, ctype in (("/", "text/html"), ("/app.js", "text/javascript"), ("/style.css", "text/css")):
            status, _, response = self.client.get(path)
            self.assertEqual(status, 200, path)
            self.assertTrue(response.getheader("Content-Type").startswith(ctype))
            self.assertEqual(response.getheader("X-Frame-Options"), "DENY")
            self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff")
            self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"))
            self.assertEqual(response.getheader("Cache-Control"), "no-store")

    def test_nothing_else_on_disk_is_reachable(self) -> None:
        for path in ("/../nextpush/admin.py", "/%2e%2e/nextpush/admin.py", "/web/../app.py", "/nextpush.json", "/admin.py", "/index.html/../../app.py", "//etc/passwd", "/app.js/", "/static/app.js", "/.env"):
            status, _, response = self.client.get(path)
            self.assertEqual(status, 404, path)
            body = response.read() if False else b""
        status, parsed, _ = self.client.get("/..%5c..%5cnextpush%5cconfig.py")
        self.assertEqual(status, 404)

    def test_the_page_has_no_inline_scripts(self) -> None:
        html = (helpers.ROOT / "nextpush" / "web" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("<script>", html)
        self.assertNotIn("onclick=", html)


class SetupFlow(AdminCase):
    def test_before_setup_nothing_is_open(self) -> None:
        status, state, _ = self.client.get("/api/state")
        self.assertEqual((state["setup_required"], state["authenticated"]), (True, False))
        self.assertNotIn("csrf", state)
        for path in ("/api/users", "/api/settings", "/api/log", "/api/activity", "/api/fs"):
            self.assertEqual(self.client.get(path)[0], 401, path)
        self.assertEqual(self.client.post("/api/login", {"password": PASSWORD})[0], 409)

    def test_the_wrong_code_is_refused_and_locks_the_address_out(self) -> None:
        for _ in range(5):
            self.assertEqual(self.client.post("/api/setup", {"token": "nope", "password": PASSWORD})[0], 403)
        status, body, _ = self.client.post("/api/setup", {"token": self.app.setup_token(), "password": PASSWORD})
        self.assertEqual(status, 429, "even the right code is refused during the lock-out")
        self.assertIn("seconds", body["error"])
        self.assertFalse(self.app.settings.configured)

    def test_a_missing_or_empty_code_is_refused(self) -> None:
        for token in (None, "", "   "):
            self.assertEqual(self.client.post("/api/setup", {"token": token, "password": PASSWORD})[0], 403)

    def test_weak_passwords_and_bad_addresses_are_refused(self) -> None:
        token = self.app.setup_token()
        for password in ("short", "aaaaaaaaaaaa", ""):
            self.assertEqual(self.client.post("/api/setup", {"token": token, "password": password})[0], 400, password)
        for url in ("http://dl.example.com", "https://dl.example.com/path", "https://user:pw@dl.example.com", "ftp://dl.example.com", "https://dl.example.com?x=1"):
            self.assertEqual(self.client.post("/api/setup", {"token": token, "password": PASSWORD, "public_url": url})[0], 400, url)
        self.assertFalse(self.app.settings.configured)

    def test_a_good_setup_signs_in_and_stores_only_a_hash(self) -> None:
        token = self.app.setup_token()
        self.assertTrue((self.app.data_dir / "setup-code.txt").is_file())
        status, body, response = self.client.post("/api/setup", {"token": token, "password": PASSWORD, "server_name": "Home", "public_url": "dl.example.com/"})
        self.assertEqual(status, 200)
        cookie = response.getheader("Set-Cookie")
        for flag in ("HttpOnly", "SameSite=Strict", "Path=/"):
            self.assertIn(flag, cookie)
        saved = (self.app.data_dir / "nextpush.json").read_text(encoding="utf-8")
        self.assertNotIn(PASSWORD, saved)
        self.assertTrue(self.app.settings.get("admin_hash").startswith("pbkdf2_sha256$"))
        self.assertEqual(self.app.settings.get("public_url"), "https://dl.example.com")
        self.assertFalse((self.app.data_dir / "setup-code.txt").exists(), "the code is single-use")
        self.client.csrf = body["csrf"]
        self.assertEqual(self.client.get("/api/settings")[0], 200)
        self.assertEqual(self.client.post("/api/setup", {"token": token, "password": "another long password"})[0], 409, "setup cannot be run twice")

    def test_the_cookie_is_secure_behind_an_https_proxy(self) -> None:
        _, _, response = self.client.request("POST", "/api/setup", {"token": self.app.setup_token(), "password": PASSWORD}, headers={"X-Forwarded-Proto": "https"})
        self.assertIn("Secure", response.getheader("Set-Cookie"))


class SessionsAndCsrf(AdminCase):
    def setUp(self) -> None:
        super().setUp()
        self.setup_done()

    def other_browser(self) -> Client:
        return Client(self.app.admin_address[1])

    def test_login_and_logout(self) -> None:
        browser = self.other_browser()
        self.assertEqual(browser.post("/api/login", {"password": "wrong password"})[0], 401)
        status, body, _ = browser.post("/api/login", {"password": PASSWORD})
        self.assertEqual(status, 200)
        browser.csrf = body["csrf"]
        self.assertEqual(browser.get("/api/settings")[0], 200)
        stale_cookie = browser.cookie
        self.assertEqual(browser.post("/api/logout")[0], 200)
        replay = self.other_browser()
        replay.cookie = stale_cookie
        self.assertEqual(replay.get("/api/settings")[0], 401, "a signed-out session cannot be reused")

    def test_a_forged_cookie_is_worthless(self) -> None:
        browser = self.other_browser()
        browser.cookie = "nextpush_session=" + "A" * 43
        self.assertEqual(browser.get("/api/settings")[0], 401)

    def test_every_change_needs_the_csrf_token(self) -> None:
        for method, path, body in (("PUT", "/api/settings", {"server_name": "x"}), ("POST", "/api/users", {}), ("DELETE", "/api/users/bob", None), ("POST", "/api/users/bob/password", {}),
                                   ("PUT", "/api/users/bob", {}), ("POST", "/api/admin/password", {}), ("POST", "/api/sftpgo/restart", {}), ("POST", "/api/logout", {})):
            for csrf in ("", "wrong-token"):
                status, _, _ = self.client.request(method, path, body, headers={"X-CSRF-Token": csrf}, csrf=False)
                self.assertEqual(status, 403, (method, path, csrf))
        self.assertEqual(self.client.get("/api/settings")[0], 200, "reading needs no token")

    def test_a_foreign_origin_is_refused(self) -> None:
        for path, body in (("/api/settings", {"server_name": "x"}), ):
            status, _, _ = self.client.request("PUT", path, body, headers={"Origin": "https://evil.example.com"})
            self.assertEqual(status, 403)
        status, _, _ = self.other_browser().request("POST", "/api/login", {"password": PASSWORD}, headers={"Origin": "https://evil.example.com"})
        self.assertEqual(status, 403)
        host = f"127.0.0.1:{self.app.admin_address[1]}"
        status, _, _ = self.client.request("PUT", "/api/settings", {"server_name": "ok"}, headers={"Origin": f"http://{host}"})
        self.assertEqual(status, 200, "the page's own origin is fine")

    def test_five_wrong_passwords_lock_the_address_even_for_the_right_one(self) -> None:
        browser = self.other_browser()
        for _ in range(5):
            browser.post("/api/login", {"password": "wrong password!"})
        status, body, _ = browser.post("/api/login", {"password": PASSWORD})
        self.assertEqual(status, 429)
        self.assertIn("Try again", body["error"])
        self.app.limiter.success("127.0.0.1")
        self.assertEqual(browser.post("/api/login", {"password": PASSWORD})[0], 200)

    def test_changing_the_password_ends_all_other_sessions(self) -> None:
        other = self.other_browser()
        _, body, _ = other.post("/api/login", {"password": PASSWORD})
        other.csrf = body["csrf"]
        self.assertEqual(self.client.post("/api/admin/password", {"current": "not it at all", "new": "a brand new passphrase"})[0], 403)
        self.assertEqual(self.client.post("/api/admin/password", {"current": PASSWORD, "new": "weak"})[0], 400)
        status, body, _ = self.client.post("/api/admin/password", {"current": PASSWORD, "new": "a brand new passphrase"})
        self.assertEqual(status, 200)
        self.client.csrf = body["csrf"]
        self.assertEqual(other.get("/api/settings")[0], 401, "the other browser was signed out")
        self.assertEqual(self.client.get("/api/settings")[0], 200, "this one stays in")
        fresh = self.other_browser()
        self.assertEqual(fresh.post("/api/login", {"password": PASSWORD})[0], 401)
        self.assertEqual(fresh.post("/api/login", {"password": "a brand new passphrase"})[0], 200)


class KeepAlive(AdminCase):
    """A request body the handler does not need must still be consumed, or it is parsed as the next request on the same connection."""

    def test_unread_bodies_do_not_poison_the_connection(self) -> None:
        self.setup_done()
        conn = http.client.HTTPConnection("127.0.0.1", self.app.admin_address[1], timeout=10)
        headers = {"Content-Type": "application/json", "Cookie": self.client.cookie, "X-CSRF-Token": self.client.csrf}
        for method, path in (("POST", "/api/sftpgo/restart"), ("POST", "/api/nothing"), ("DELETE", "/api/users/ghost"), ("GET", "/api/state"), ("POST", "/api/users/ghost/password")):
            conn.request(method, path, body=b'{"ignored": true}', headers=headers)
            response = conn.getresponse()
            response.read()
            self.assertNotEqual(response.status, 501, (method, path))
        conn.request("GET", "/api/state", headers=headers)
        follow_up = conn.getresponse()
        self.assertEqual(follow_up.status, 200, "the connection is still in sync after bodies that nobody read")
        follow_up.read()
        conn.close()

    def test_an_oversized_body_closes_the_connection(self) -> None:
        conn = http.client.HTTPConnection("127.0.0.1", self.app.admin_address[1], timeout=10)
        conn.request("POST", "/api/login", body=b"x" * 70_000, headers={"Content-Type": "application/json"})
        self.assertEqual(conn.getresponse().status, 413)
        conn.close()


class Settings(AdminCase):
    def setUp(self) -> None:
        super().setUp()
        self.setup_done()

    def test_read_and_change(self) -> None:
        status, data, _ = self.client.get("/api/settings")
        self.assertEqual((data["server_name"], data["public_url"], data["dav_path"]), ("Home", "https://dl.example.com", "/dav"))
        self.assertTrue(data["access_key"]["enabled"])
        status, data, _ = self.client.put("/api/settings", {"server_name": "N" * 200, "public_url": "https://other.example.org"})
        self.assertEqual((status, len(data["server_name"]), data["public_url"]), (200, 60, "https://other.example.org"))
        self.assertEqual(self.client.put("/api/settings", {"public_url": "http://other.example.org"})[0], 400)

    def test_the_access_key_can_be_switched_and_rotated(self) -> None:
        old = self.client.get("/api/settings")[1]["access_key"]["value"]
        self.assertEqual(self.client.put("/api/settings", {"access_key_enabled": False})[1]["access_key"]["enabled"], False)
        self.client.put("/api/settings", {"access_key_enabled": True})
        new = self.client.put("/api/settings", {"rotate_key": True})[1]["access_key"]["value"]
        self.assertNotEqual(old, new)
        self.assertGreaterEqual(len(new), 30)
        self.assertEqual(self.app.settings.get("access_key")["value"], new, "the public listener sees the new key at once")

    def test_trusted_proxies_are_validated(self) -> None:
        self.assertEqual(self.client.put("/api/settings", {"trusted_proxies": ["192.168.1.0/24", "10.0.0.5"]})[1]["trusted_proxies"], ["192.168.1.0/24", "10.0.0.5"])
        for bad in (["not a network"], ["1.2.3.4", "x"], "1.2.3.4"):
            self.assertEqual(self.client.put("/api/settings", {"trusted_proxies": bad})[0], 400, bad)


class Hygiene(AdminCase):
    def setUp(self) -> None:
        super().setUp()
        self.setup_done()

    def test_bad_bodies_are_clean_errors(self) -> None:
        self.assertEqual(self.client.request("PUT", "/api/settings", raw=b"{ not json")[0], 400)
        self.assertEqual(self.client.request("PUT", "/api/settings", raw=b"[1,2]")[0], 400)
        self.assertEqual(self.client.request("PUT", "/api/settings", raw=b"\xff\xfe")[0], 400)
        self.assertEqual(self.client.request("PUT", "/api/settings", raw=b"x" * 70_000)[0], 413)
        conn = http.client.HTTPConnection("127.0.0.1", self.app.admin_address[1], timeout=5)
        conn.request("PUT", "/api/settings", body=b"{}", headers={"Content-Length": "abc", "Cookie": self.client.cookie, "X-CSRF-Token": self.client.csrf})
        self.assertEqual(conn.getresponse().status, 400)
        conn.close()

    def test_unknown_routes_and_methods_are_404(self) -> None:
        for method, path in (("GET", "/api/nothing"), ("POST", "/api/state"), ("DELETE", "/api/settings"), ("GET", "/api/users/a/b/c")):
            self.assertEqual(self.client.request(method, path)[0], 404, (method, path))

    def test_internal_errors_never_leak_details(self) -> None:
        with mock.patch.object(self.app.limiter, "locked_for", side_effect=RuntimeError("secret internal detail /home/x")):
            status, body, _ = Client(self.app.admin_address[1]).post("/api/login", {"password": PASSWORD})
        self.assertEqual(status, 500)
        self.assertNotIn("secret internal detail", json.dumps(body))
        self.assertIn("secret internal detail", "\n".join(self.app.log_lines), "but the log has it")

    def test_users_endpoints_explain_a_missing_sftpgo(self) -> None:
        status, body, _ = self.client.get("/api/users")
        self.assertEqual(status, 503)
        self.assertIn("SFTPGo", body["error"])
        state = self.client.get("/api/state")[1]
        self.assertFalse(state["sftpgo"]["healthy"])
        self.assertIn("not found", state["sftpgo"]["error"])

    def test_the_access_key_never_reaches_the_log(self) -> None:
        key = self.app.settings.get("access_key")["value"]
        logging.getLogger("nextpush.test").info("the key is %s, really", key)
        logging.getLogger("nextpush").info("the key is %s, really", key)
        text = "\n".join(self.app.log_lines) + (self.app.data_dir / "nextpush.log").read_text(encoding="utf-8")
        self.assertNotIn(key, text)
        status, body, _ = self.client.get("/api/log")
        self.assertNotIn(key, json.dumps(body))

    def test_the_filesystem_browser_reports_errors(self) -> None:
        self.assertEqual(self.client.get("/api/fs?path=")[0], 200)
        self.assertEqual(self.client.get("/api/fs?path=relative")[0], 400)
        self.assertEqual(self.client.get("/api/fs?path=" + str(self.app.data_dir))[0], 200)

    def test_blocked_addresses_endpoints_are_protected_and_validated(self) -> None:
        self.assertEqual(Client(self.app.admin_address[1]).get("/api/blocked")[0], 401)
        self.assertEqual(Client(self.app.admin_address[1]).delete("/api/blocked/1.2.3.4", csrf=False)[0], 401)
        self.assertEqual(self.client.delete("/api/blocked/abcd")[0], 400, "hex-looking junk that is not an address")
        self.assertEqual(self.client.delete("/api/blocked/1.2.3.4", headers={"X-CSRF-Token": "wrong"}, csrf=False)[0], 403)
        status, body, _ = self.client.get("/api/blocked")
        self.assertEqual((status, body), (200, {"blocked": []}))
        self.app.dav_limiter.failure("203.0.113.1")
        for _ in range(9):
            self.app.dav_limiter.failure("203.0.113.1")
        self.assertEqual([b["ip"] for b in self.client.get("/api/blocked")[1]["blocked"]], ["203.0.113.1"])
        self.assertEqual(self.client.delete("/api/blocked/203.0.113.1")[0], 200)
        self.assertEqual(self.client.get("/api/blocked")[1], {"blocked": []})

    def test_the_settings_file_never_holds_the_admin_password(self) -> None:
        self.assertNotIn(PASSWORD, (self.app.data_dir / "nextpush.json").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
