"""The public listener against a fake SFTPGo: the access-key gate, the method filter, discovery, streaming, Range, client addresses and error handling."""
from __future__ import annotations

import http.client
import socket
import threading
import time
import unittest

import helpers
from helpers import FakeUpstream, TempCase, dav_request
from nextpush import public
from nextpush.config import Settings
from nextpush.security import LoginLimiter

KEY = ("X-Pull-Key", "k3y-for-the-tests-0123456789")


class StubApp:
    def __init__(self, dav_port: int, key_enabled: bool = True) -> None:
        self.settings = Settings(None)
        self.settings.update(dav_port=dav_port, server_name="Test server", access_key={"enabled": key_enabled, "header": KEY[0], "value": KEY[1]})
        self.public_dropped = 0
        self.last_refusal_log: dict[str, float] = {}
        self.dav_limiter = LoginLimiter(free_attempts=8, max_lock=600)
        self._users = StubUsers()

    def users(self) -> "StubUsers":   # a method, like App.users
        return self._users


class StubUsers:
    """What UserService.get returns: names and modes (the real one also holds real paths, which must never be sent)."""

    def get(self, username: str) -> dict:
        if username == "broken":
            raise RuntimeError("SFTPGo is restarting")
        folders = {"bob": [{"name": "Incoming", "path": "/srv/secret/media", "mode": "pull"}, {"name": "Anime", "path": "/srv/secret/anime", "mode": "read"}],
                   "viewer": [{"name": "Films", "path": "/srv/secret/films", "mode": "read"}]}
        return {"username": username, "folders": folders.get(username, [])}


class ProxyCase(TempCase):
    key_enabled = True

    def setUp(self) -> None:
        super().setUp()
        self.upstream = FakeUpstream()
        self.addCleanup(self.upstream.stop)
        self.app = StubApp(self.upstream.port, self.key_enabled)
        self.server = public.PublicServer(("127.0.0.1", 0), public.make_handler(self.app))
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(lambda: (self.server.shutdown(), self.server.server_close()))

    def call(self, method: str, path: str, key=KEY, **kw):
        return dav_request(self.port, method, path, key=key, **kw)

    def dropped(self, method: str, path: str, key=KEY, **kw) -> bool:
        """True if the server closed the connection without any HTTP answer."""
        try:
            self.call(method, path, key=key, **kw)
        except (http.client.RemoteDisconnected, ConnectionResetError, http.client.BadStatusLine, ConnectionAbortedError):
            return True
        return False


class Gate(ProxyCase):
    def test_strangers_get_no_answer_at_all(self) -> None:
        for kwargs in ({"key": None}, {"key": (KEY[0], "wrong")}, {"key": ("X-Other", KEY[1])}, {"key": (KEY[0], "")}):
            self.assertTrue(self.dropped("GET", "/.well-known/nextpush", **kwargs), kwargs)
            self.assertTrue(self.dropped("PROPFIND", "/dav/", **kwargs), kwargs)
        self.assertEqual(self.upstream.requests, [], "nothing reached SFTPGo")
        self.assertGreaterEqual(self.app.public_dropped, 8)

    def test_a_raw_scanner_sees_nothing_not_even_a_status_line(self) -> None:
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
            self.assertEqual(sock.recv(1024), b"")

    def test_only_read_list_and_delete_methods_pass(self) -> None:
        for method in ("PUT", "POST", "MKCOL", "MOVE", "COPY", "PROPPATCH", "LOCK", "UNLOCK", "PATCH", "TRACE", "CONNECT"):
            self.assertTrue(self.dropped(method, "/dav/x"), method)
        self.assertEqual(self.upstream.requests, [])

    def test_the_key_is_compared_exactly(self) -> None:
        self.upstream.routes[("PROPFIND", "/dav/")] = (207, {}, b"<ok/>")
        self.assertEqual(self.call("PROPFIND", "/dav/")[0], 207)
        self.assertTrue(self.dropped("PROPFIND", "/dav/", key=(KEY[0], KEY[1] + " ")))
        self.assertTrue(self.dropped("PROPFIND", "/dav/", key=(KEY[0], KEY[1][:-1])))
        self.assertEqual(self.call("PROPFIND", "/dav/", key=(KEY[0].lower(), KEY[1]))[0], 207, "header names are case-insensitive")


class Discovery(ProxyCase):
    def test_the_document_names_the_server_and_nothing_else(self) -> None:
        status, headers, body = self.call("GET", "/.well-known/nextpush")
        import json
        document = json.loads(body)
        self.assertEqual(status, 200)
        self.assertEqual(document, {"nextpush": 1, "name": "Test server", "version": document["version"], "dav": "/dav", "auth": "basic", "me": "/.well-known/nextpush/me"})
        self.assertNotIn("server", {k.lower() for k in headers}, "no software banner")
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_unknown_paths_are_a_plain_404_with_no_details(self) -> None:
        for path in ("/", "/admin", "/api/users", "/dav2", "/.git/config", "/../etc/passwd"):
            status, _, body = self.call("GET", path)
            self.assertEqual(status, 404, path)
            self.assertNotIn(b"nextpush", body.lower())
        self.assertEqual(self.upstream.requests, [])


class WhoAmI(ProxyCase):
    """/.well-known/nextpush/me: the folders of the signed-in user and whether each allows deleting after the copy. Names and modes only."""

    def setUp(self) -> None:
        super().setUp()
        self.upstream.routes[("PROPFIND", "/dav/")] = (207, {}, b"<multistatus/>")

    def me(self, user="bob", password="right", **kw):
        return self.call("GET", "/.well-known/nextpush/me", user=user, password=password, **kw)

    def test_a_signed_in_user_sees_their_folders_and_modes_but_never_real_paths(self) -> None:
        import json
        status, headers, body = self.me()
        self.assertEqual(status, 200)
        document = json.loads(body)
        self.assertEqual(document, {"nextpush": 1, "user": "bob", "folders": [{"name": "Incoming", "mode": "pull"}, {"name": "Anime", "mode": "read"}], "can_delete": True})
        self.assertNotIn(b"secret", body)
        self.assertEqual(headers.get("Cache-Control"), "no-store")
        self.assertEqual(json.loads(self.me(user="viewer")[2])["can_delete"], False)

    def test_the_login_is_checked_by_the_file_service_with_the_callers_own_credentials(self) -> None:
        self.me(headers={"X-Forwarded-For": "198.51.100.4"})
        request = self.upstream.requests[-1]
        self.assertEqual((request["method"], request["path"], request["headers"]["depth"]), ("PROPFIND", "/dav/", "0"))
        self.assertTrue(request["headers"]["authorization"].startswith("Basic "))
        self.assertNotIn(KEY[0].lower(), request["headers"], "the access key never reaches SFTPGo")

    def test_without_a_login_or_with_a_wrong_one_nothing_is_revealed(self) -> None:
        self.assertEqual(self.call("GET", "/.well-known/nextpush/me")[0], 401)
        self.upstream.routes[("PROPFIND", "/dav/")] = (401, {}, b"bad")
        status, _, body = self.me(password="wrong")
        self.assertEqual(status, 401)
        self.assertNotIn(b"folders", body)

    def test_wrong_passwords_here_count_toward_the_same_lock_out(self) -> None:
        self.upstream.routes[("PROPFIND", "/dav/")] = (401, {}, b"bad")
        self.assertEqual([self.me(password="x", headers={"X-Forwarded-For": "203.0.113.20"})[0] for _ in range(8)], [401] * 8)
        self.assertEqual(self.me(password="x", headers={"X-Forwarded-For": "203.0.113.20"})[0], 429)
        self.assertEqual(self.call("PROPFIND", "/dav/", user="bob", password="x", headers={"X-Forwarded-For": "203.0.113.20"})[0], 429, "the WebDAV path is locked too")

    def test_the_key_gate_and_the_method_filter_apply(self) -> None:
        self.assertTrue(self.dropped("GET", "/.well-known/nextpush/me", key=None, user="bob", password="right"))
        self.assertTrue(self.dropped("POST", "/.well-known/nextpush/me", user="bob", password="right"))

    def test_a_folder_list_that_cannot_be_read_is_unknown_not_guessed(self) -> None:
        import json
        document = json.loads(self.me(user="broken")[2])
        self.assertEqual((document["folders"], document["can_delete"]), ([], None))

    def test_a_dead_file_service_is_a_clean_502(self) -> None:
        self.upstream.stop()
        self.assertEqual(self.me()[0], 502)


class Proxying(ProxyCase):
    def test_propfind_with_body_and_login_reaches_the_upstream(self) -> None:
        self.upstream.routes[("PROPFIND", "/dav/")] = (207, {"Content-Type": "application/xml"}, b"<multistatus/>")
        status, headers, body = self.call("PROPFIND", "/dav/", user="bob", password="secret", body=b"<propfind/>", headers={"Content-Type": "application/xml", "Depth": "0"})
        self.assertEqual((status, body), (207, b"<multistatus/>"))
        seen = self.upstream.requests[0]
        self.assertEqual(seen["body"], b"<propfind/>")
        self.assertTrue(seen["headers"]["authorization"].startswith("Basic "), "the login is passed on")
        self.assertEqual(seen["headers"]["depth"], "0")

    def test_the_access_key_and_forwarding_headers_do_not_reach_sftpgo(self) -> None:
        self.upstream.routes[("GET", "/dav/f")] = (200, {}, b"x")
        self.call("GET", "/dav/f", headers={"X-Forwarded-Host": "evil", "Forwarded": "for=evil", "X-Real-IP": "6.6.6.6"})
        headers = self.upstream.requests[0]["headers"]
        for name in (KEY[0].lower(), "x-forwarded-host", "forwarded", "x-real-ip"):
            self.assertNotIn(name, headers)
        self.assertEqual(headers["host"], f"127.0.0.1:{self.upstream.port}")

    def test_the_real_client_address_is_passed_and_cannot_be_forged(self) -> None:
        self.upstream.routes[("GET", "/dav/f")] = (200, {}, b"x")
        self.call("GET", "/dav/f")
        self.assertEqual(self.upstream.requests[-1]["headers"]["x-forwarded-for"], "127.0.0.1")
        self.app.settings.update(trusted_proxies=[])   # nobody is a trusted proxy: the header must be ignored
        self.call("GET", "/dav/f", headers={"X-Forwarded-For": "6.6.6.6"})
        self.assertEqual(self.upstream.requests[-1]["headers"]["x-forwarded-for"], "127.0.0.1")
        self.app.settings.update(trusted_proxies=["127.0.0.0/8"])
        self.call("GET", "/dav/f", headers={"X-Forwarded-For": "6.6.6.6, 198.51.100.9"})
        self.assertEqual(self.upstream.requests[-1]["headers"]["x-forwarded-for"], "198.51.100.9")

    def test_range_requests_and_their_answers_are_passed_through(self) -> None:
        self.upstream.routes[("GET", "/dav/big.mkv")] = (206, {"Content-Range": "bytes 0-9/1000", "Accept-Ranges": "bytes"}, b"0123456789")
        status, headers, body = self.call("GET", "/dav/big.mkv", headers={"Range": "bytes=0-9"})
        self.assertEqual((status, body, headers["Content-Range"], headers["Content-Length"]), (206, b"0123456789", "bytes 0-9/1000", "10"))
        self.assertEqual(self.upstream.requests[0]["headers"]["range"], "bytes=0-9")

    def test_head_has_headers_and_no_body(self) -> None:
        self.upstream.routes[("HEAD", "/dav/f")] = (200, {"Content-Length": "123456", "ETag": '"abc"'}, b"")
        status, headers, body = self.call("HEAD", "/dav/f")
        self.assertEqual((status, body, headers["Content-Length"], headers["ETag"]), (200, b"", "123456", '"abc"'))

    def test_delete_and_error_statuses_are_passed_through(self) -> None:
        self.upstream.routes[("DELETE", "/dav/f")] = (204, {}, b"")
        self.upstream.routes[("DELETE", "/dav/locked")] = (403, {}, b'{"message":"denied"}')
        self.assertEqual(self.call("DELETE", "/dav/f")[0], 204)
        status, _, body = self.call("DELETE", "/dav/locked")
        self.assertEqual((status, body), (403, b'{"message":"denied"}'))
        self.assertEqual(self.call("GET", "/dav/missing")[0], 404)

    def test_the_server_banner_of_sftpgo_is_removed(self) -> None:
        self.upstream.routes[("GET", "/dav/f")] = (200, {"Server": "SFTPGo/2.7.6", "WWW-Authenticate": 'Basic realm="SFTPGo"'}, b"x")
        _, headers, _ = self.call("GET", "/dav/f")
        self.assertNotIn("server", {k.lower() for k in headers})
        self.assertIn("WWW-Authenticate", headers)

    def test_a_chunked_answer_without_a_length_is_streamed(self) -> None:
        self.upstream.routes[("GET", "/dav/stream")] = (200, {}, iter([b"a" * 1000, b"b" * 1000, b"c" * 5]))
        status, headers, body = self.call("GET", "/dav/stream")
        self.assertEqual((status, body), (200, b"a" * 1000 + b"b" * 1000 + b"c" * 5))
        self.assertEqual(headers.get("Connection"), "close")

    def test_a_large_file_streams_correctly_and_quickly(self) -> None:
        payload = bytes(range(256)) * (8 * 1024 * 1024 // 256) * 4   # 32 MB
        self.upstream.routes[("GET", "/dav/large.bin")] = (200, {}, payload)
        started = time.time()
        status, _, body = self.call("GET", "/dav/large.bin")
        self.assertEqual(status, 200)
        self.assertEqual(len(body), len(payload))
        self.assertEqual(body[-1000:], payload[-1000:])
        self.assertLess(time.time() - started, 20)

    def test_many_requests_over_one_connection_work(self) -> None:
        self.upstream.routes[("PROPFIND", "/dav/")] = (207, {}, b"<m/>")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        for _ in range(5):
            conn.request("PROPFIND", "/dav/", headers={KEY[0]: KEY[1], "Depth": "0"})
            response = conn.getresponse()
            self.assertEqual((response.status, response.read()), (207, b"<m/>"))
        conn.close()

    def test_a_client_that_vanishes_mid_download_does_not_hurt_the_server(self) -> None:
        self.upstream.routes[("GET", "/dav/huge")] = (200, {}, b"z" * (24 * 1024 * 1024))
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(f"GET /dav/huge HTTP/1.1\r\nHost: x\r\n{KEY[0]}: {KEY[1]}\r\n\r\n".encode())
            sock.recv(4096)
        time.sleep(0.5)
        self.upstream.routes[("GET", "/dav/f")] = (200, {}, b"still fine")
        self.assertEqual(self.call("GET", "/dav/f")[2], b"still fine")

    def test_an_upstream_that_ends_early_makes_the_client_fail_fast_instead_of_hanging(self) -> None:
        self.upstream.routes[("GET", "/dav/cut")] = (200, {"Content-Length": "1000000", "X-Test-Truncate": "1"}, b"only a little")
        started = time.time()
        with self.assertRaises((http.client.IncompleteRead, http.client.RemoteDisconnected, ConnectionResetError)):
            self.call("GET", "/dav/cut")
        self.assertLess(time.time() - started, 5, "no waiting for a timeout")

    def test_bodies_on_requests_that_do_not_need_them_are_consumed(self) -> None:
        self.upstream.routes[("GET", "/dav/f")] = (200, {}, b"fine")
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        for path in ("/.well-known/nextpush", "/dav/f", "/nothing", "/.well-known/nextpush"):
            conn.request("GET", path, body=b"surprise body", headers={KEY[0]: KEY[1]})
            response = conn.getresponse()
            response.read()
            self.assertIn(response.status, (200, 404), path)
            if response.getheader("Connection", "").lower() == "close":
                conn.close()
                conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.close()

    def test_a_dead_upstream_is_a_clean_502(self) -> None:
        self.app.settings.update(dav_port=1)   # nothing listens there
        status, _, body = self.call("GET", "/dav/f")
        self.assertEqual(status, 502)
        self.assertNotIn(b"127.0.0.1", body)

    def test_request_bodies_are_limited_and_must_have_a_length(self) -> None:
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:   # only the headers: the answer must come without waiting for 1 MB of body
            sock.sendall(f"PROPFIND /dav/ HTTP/1.1\r\nHost: x\r\n{KEY[0]}: {KEY[1]}\r\nContent-Length: 1000001\r\n\r\n".encode())
            self.assertIn(b" 413 ", sock.recv(4096).split(b"\r\n")[0] + b" ")
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(f"PROPFIND /dav/ HTTP/1.1\r\nHost: x\r\n{KEY[0]}: {KEY[1]}\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n".encode())
            self.assertIn(b" 411 ", sock.recv(4096).split(b"\r\n")[0] + b" ")

    def test_paths_are_passed_unchanged_so_the_upstream_decides(self) -> None:
        self.upstream.routes[("GET", "/dav/Ranma%20%C2%BD/ep.mkv")] = (200, {}, b"x")
        self.call("GET", "/dav/Ranma%20%C2%BD/ep.mkv?x=1")
        self.assertEqual(self.upstream.requests[0]["path"], "/dav/Ranma%20%C2%BD/ep.mkv?x=1")


class RefusalLog(ProxyCase):
    def test_a_refused_login_is_logged_once_a_minute_per_address_without_secrets(self) -> None:
        self.upstream.routes[("PROPFIND", "/dav/")] = (401, {}, b"no")
        with self.assertLogs("nextpush.public", level="WARNING") as captured:
            for _ in range(5):
                self.call("PROPFIND", "/dav/", user="bob", password="very-secret-password")
        self.assertEqual(len(captured.output), 1, "one line per address per minute")
        self.assertIn("login refused (HTTP 401)", captured.output[0])
        self.assertNotIn("very-secret-password", captured.output[0])
        self.assertNotIn(KEY[1], captured.output[0])

    def test_a_403_on_a_listing_is_logged_but_not_on_a_delete(self) -> None:
        self.upstream.routes[("PROPFIND", "/dav/")] = (403, {}, b"x")
        self.upstream.routes[("DELETE", "/dav/f")] = (403, {}, b"x")
        with self.assertLogs("nextpush.public", level="WARNING") as captured:
            self.call("PROPFIND", "/dav/", user="bob", password="x")
        self.assertIn("login refused (HTTP 403)", captured.output[0])
        self.app.last_refusal_log.clear()
        with self.assertNoLogs("nextpush.public", level="WARNING"):
            self.call("DELETE", "/dav/f", user="bob", password="x")

    def test_requests_without_a_login_and_good_answers_are_not_logged(self) -> None:
        self.upstream.routes[("PROPFIND", "/dav/")] = (401, {}, b"no")
        self.upstream.routes[("GET", "/dav/ok")] = (200, {}, b"fine")
        with self.assertNoLogs("nextpush.public", level="WARNING"):
            self.call("PROPFIND", "/dav/")                       # no Authorization header at all
            self.call("GET", "/dav/ok", user="bob", password="x")  # accepted


class LoginProtection(ProxyCase):
    """Wrong passwords are counted per REAL address; after 8, that address gets a clear 429 and SFTPGo is not contacted until the lock ends."""

    def setUp(self) -> None:
        super().setUp()
        self.upstream.routes[("PROPFIND", "/dav/")] = (401, {}, b"bad login")
        self.upstream.routes[("GET", "/dav/ok")] = (200, {}, b"fine")

    def guess(self, ip: str, user: str = "bob", password: str = "wrong") -> int:
        return self.call("PROPFIND", "/dav/", user=user, password=password, headers={"X-Forwarded-For": ip})[0]

    def test_eight_wrong_passwords_then_a_clear_429_that_spares_the_upstream(self) -> None:
        self.assertEqual([self.guess("203.0.113.5") for _ in range(8)], [401] * 8)
        before = len(self.upstream.requests)
        status, headers, body = self.call("PROPFIND", "/dav/", user="bob", password="wrong", headers={"X-Forwarded-For": "203.0.113.5"})
        self.assertEqual(status, 429)
        self.assertIn("Too many wrong passwords", body.decode())
        self.assertGreaterEqual(int(headers["Retry-After"]), 1)
        self.assertEqual(len(self.upstream.requests), before, "SFTPGo is not contacted while the address is locked")
        self.assertEqual(self.call("GET", "/dav/ok", user="bob", password="right", headers={"X-Forwarded-For": "203.0.113.5"})[0], 429, "even a correct login waits")

    def test_other_addresses_and_requests_without_a_login_are_not_affected(self) -> None:
        [self.guess("203.0.113.5") for _ in range(9)]
        self.assertEqual(self.guess("203.0.113.6"), 401)
        self.assertEqual(self.call("GET", "/.well-known/nextpush", headers={"X-Forwarded-For": "203.0.113.5"})[0], 200, "discovery needs no login")
        self.assertEqual(self.call("PROPFIND", "/dav/", headers={"X-Forwarded-For": "203.0.113.5"})[0], 401, "no Authorization header: just the challenge")

    def test_a_good_login_resets_the_count(self) -> None:
        [self.guess("203.0.113.7") for _ in range(7)]
        self.assertEqual(self.call("GET", "/dav/ok", user="bob", password="right", headers={"X-Forwarded-For": "203.0.113.7"})[0], 200)
        self.assertEqual([self.guess("203.0.113.7") for _ in range(7)], [401] * 7, "the count started again")

    def test_an_exempt_user_is_never_locked_out_and_never_counted(self) -> None:
        self.app.settings.update(no_block_users=["kim"])
        self.assertEqual([self.guess("203.0.113.8", user="kim") for _ in range(40)], [401] * 40)
        self.assertEqual(self.guess("203.0.113.8", user="bob"), 401, "the address is not penalised for the exempt user's failures")

    def test_the_lock_ends_and_the_address_can_be_unlocked_by_the_administrator(self) -> None:
        [self.guess("203.0.113.9") for _ in range(8)]
        self.assertEqual(self.guess("203.0.113.9"), 429)
        self.assertEqual([ip for ip, _ in self.app.dav_limiter.snapshot()], ["203.0.113.9"])
        self.app.dav_limiter.success("203.0.113.9")
        self.assertEqual(self.guess("203.0.113.9"), 401)

    def test_junk_authorization_headers_do_not_crash_it(self) -> None:
        for value in ("Basic", "Basic !!!", "Bearer abc", "Basic " + "A" * 5000, ""):
            status = self.call("PROPFIND", "/dav/", headers={"Authorization": value})[0]
            self.assertIn(status, (401, 429), value[:20])


class WithoutKey(ProxyCase):
    key_enabled = False

    def test_when_the_key_is_off_only_the_method_filter_applies(self) -> None:
        self.upstream.routes[("PROPFIND", "/dav/")] = (207, {}, b"<m/>")
        self.assertEqual(self.call("PROPFIND", "/dav/", key=None)[0], 207)
        self.assertTrue(self.dropped("PUT", "/dav/x", key=None))


if __name__ == "__main__":
    unittest.main()
