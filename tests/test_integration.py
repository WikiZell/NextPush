"""NextPush against the REAL SFTPGo program (skipped when it is not installed: see docs/TESTING.md). Everything goes through the same HTTP calls the web GUI makes."""
from __future__ import annotations

import os
import re
import threading
import time
import unittest
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

import helpers
from helpers import Client, TempCase, dav_request, sftpgo_binary
from nextpush.app import App
from nextpush.invite import parse_invite

BINARY = sftpgo_binary()
PASSWORD = "a long and unusual admin passphrase"
DAV = "{DAV:}"


def hrefs(body: bytes) -> list[str]:
    return [urllib.parse.unquote(node.text or "") for node in ET.fromstring(body).iter(f"{DAV}href")]


@unittest.skipUnless(BINARY, "the real SFTPGo program was not found (run tools/fetch-sftpgo or set NEXTPUSH_SFTPGO)")
class Stack(unittest.TestCase):
    """One running NextPush + SFTPGo for a whole group of tests (starting SFTPGo is the slow part)."""

    @classmethod
    def setUpClass(cls) -> None:
        import shutil
        import tempfile
        cls.tmp = Path(tempfile.mkdtemp(prefix="nextpush-int-"))
        cls.share = cls.tmp / "media"
        (cls.share / "Films" / "Ranma ½").mkdir(parents=True)
        (cls.share / "Series" / "Show [2026]").mkdir(parents=True)
        (cls.share / "Private").mkdir()
        cls.payload = os.urandom(300_000)
        (cls.share / "Films" / "È quasi magia (1989).mkv").write_bytes(cls.payload)
        (cls.share / "Films" / "Ranma ½" / "Episodio 01 [BDRip].mkv").write_bytes(b"ranma" * 1000)
        (cls.share / "Series" / "Show [2026]" / "s01e01.mkv").write_bytes(b"show" * 1000)
        (cls.share / "Private" / "diary.txt").write_text("top secret", encoding="utf-8")
        (cls.tmp / "outside-secret.txt").write_text("OUTSIDE-SECRET", encoding="utf-8")
        cls.app = App(cls.tmp / "data", binary=BINARY, admin_port=0, public_port=0, admin_host="127.0.0.1", public_host="127.0.0.1", quiet=True)
        import nextpush.admin as admin_module
        import nextpush.security as security
        cls._orig_hash = admin_module.hash_password
        admin_module.hash_password = lambda password: security.hash_password(password, 1000)
        cls.app.start()
        assert cls.app.sftpgo_error == "", cls.app.sftpgo_error
        cls.admin = Client(cls.app.admin_address[1])
        status, body, _ = cls.admin.post("/api/setup", {"token": cls.app.setup_token(), "password": PASSWORD, "server_name": "Test server", "public_url": "https://dl.example.com"})
        assert status == 200, body
        cls.admin.csrf = body["csrf"]
        cls.port = cls.app.public_address[1]
        key = cls.app.settings.get("access_key")
        cls.key = (key["header"], key["value"])
        cls._shutil = shutil

    @classmethod
    def tearDownClass(cls) -> None:
        import nextpush.admin as admin_module
        admin_module.hash_password = cls._orig_hash
        cls.app.stop()
        cls._shutil.rmtree(cls.tmp, ignore_errors=True)

    # -- helpers
    def make_user(self, name: str, folders: list[dict], **extra) -> str:
        status, body, _ = self.admin.post("/api/users", {"username": name, "folders": folders, **extra})
        self.assertEqual(status, 201, body)
        return body["password"]

    def dav(self, method: str, path: str, user: str, password: str, **kw):
        return dav_request(self.port, method, path, user=user, password=password, key=self.key, **kw)

    def drop_user(self, name: str) -> None:
        self.admin.delete(f"/api/users/{name}")


class Setup(Stack):
    def test_sftpgo_runs_only_the_services_we_need_on_loopback(self) -> None:
        import json
        config = json.loads((self.app.data_dir / "sftpgo" / "sftpgo.json").read_text(encoding="utf-8"))
        self.assertEqual(config["sftpd"]["bindings"], [])
        self.assertEqual(config["ftpd"]["bindings"], [])
        self.assertEqual([b["address"] for b in config["webdavd"]["bindings"]], ["127.0.0.1"])
        http_binding = config["httpd"]["bindings"][0]
        self.assertEqual((http_binding["address"], http_binding["enable_web_admin"], http_binding["enable_web_client"]), ("127.0.0.1", False, False))
        self.assertFalse(config["common"]["defender"]["enabled"], "NextPush does the login protection itself")
        status = self.admin.get("/api/state")[1]["sftpgo"]
        self.assertTrue(status["running"] and status["healthy"])

    def test_sftpgos_own_interfaces_are_closed(self) -> None:
        import http.client
        api = self.app.settings.get("api_port")
        for path, expected in (("/web/admin/login", {404}), ("/web/client/login", {404}), ("/api/v2/users", {401}), ("/api/v2/version", {401})):
            conn = http.client.HTTPConnection("127.0.0.1", api, timeout=5)
            conn.request("GET", path)
            self.assertIn(conn.getresponse().status, expected, path)
            conn.close()

    def test_the_private_admin_credentials_are_kept_in_a_private_file(self) -> None:
        path = self.app.data_dir / "sftpgo" / "admin.json"
        self.assertTrue(path.is_file())
        if os.name != "nt":
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(path.read_text(encoding="utf-8"), "\n".join(self.app.log_lines))


class UsersAndAccess(Stack):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.ben_pw = cls.make_user_cls("bob", [{"path": str(cls.share / "Films"), "mode": "pull"}, {"path": str(cls.share / "Series"), "name": "TV", "mode": "read"}])
        cls.eve_pw = cls.make_user_cls("eve", [{"path": str(cls.share / "Series"), "mode": "read"}])

    @classmethod
    def make_user_cls(cls, name: str, folders: list[dict], **extra) -> str:
        status, body, _ = cls.admin.post("/api/users", {"username": name, "folders": folders, **extra})
        assert status == 201, body
        return body["password"]

    def test_creation_returns_a_strong_password_once_and_lists_the_folders(self) -> None:
        self.assertGreaterEqual(len(self.ben_pw), 30)
        status, listing, _ = self.admin.get("/api/users")
        self.assertEqual(status, 200)
        bob = next(u for u in listing["users"] if u["username"] == "bob")
        self.assertEqual([(f["name"], f["mode"]) for f in bob["folders"]], [("Films", "pull"), ("TV", "read")])
        self.assertNotIn("password", str(listing).lower().replace("mode_label", ""), "passwords are never listed")
        again = self.admin.get("/api/users/bob")[1]
        self.assertNotIn(self.ben_pw, str(again))

    def test_each_user_sees_exactly_their_own_virtual_folders(self) -> None:
        status, _, body = self.dav("PROPFIND", "/dav/", "bob", self.ben_pw)
        self.assertEqual(status, 207)
        names = {h.strip("/").split("/")[-1] for h in hrefs(body) if h.rstrip("/") != "/dav"}
        self.assertEqual(names, {"Films", "TV"})
        status, _, body = self.dav("PROPFIND", "/dav/", "eve", self.eve_pw)
        self.assertEqual({h.strip("/").split("/")[-1] for h in hrefs(body) if h.rstrip("/") != "/dav"}, {"Series"})
        self.assertNotIn(str(self.share).encode(), body, "real server paths never reach a client")

    def test_unicode_and_special_names_work_in_listings_and_downloads(self) -> None:
        status, _, body = self.dav("PROPFIND", "/dav/Films/", "bob", self.ben_pw)
        self.assertEqual(status, 207)
        found = hrefs(body)
        self.assertTrue(any(h.endswith("/È quasi magia (1989).mkv") for h in found), found)
        status, _, body = self.dav("GET", "/dav/Films/" + urllib.parse.quote("È quasi magia (1989).mkv"), "bob", self.ben_pw)
        self.assertEqual((status, body), (200, self.payload))
        status, _, body = self.dav("GET", "/dav/Films/" + urllib.parse.quote("Ranma ½") + "/" + urllib.parse.quote("Episodio 01 [BDRip].mkv"), "bob", self.ben_pw)
        self.assertEqual((status, body), (200, b"ranma" * 1000))

    def test_range_and_head_work(self) -> None:
        path = "/dav/Films/" + urllib.parse.quote("È quasi magia (1989).mkv")
        status, headers, body = self.dav("GET", path, "bob", self.ben_pw, headers={"Range": "bytes=1000-1999"})
        self.assertEqual((status, body), (206, self.payload[1000:2000]))
        self.assertTrue(headers["Content-Range"].startswith("bytes 1000-1999/"))
        status, headers, body = self.dav("HEAD", path, "bob", self.ben_pw)
        self.assertEqual((status, body, int(headers["Content-Length"])), (200, b"", len(self.payload)))

    def test_logins_are_checked(self) -> None:
        for user, password in (("bob", "wrong"), ("nobody", self.ben_pw), ("bob", ""), ("BOB", self.ben_pw), ("eve", self.ben_pw)):
            status, _, _ = self.dav("PROPFIND", "/dav/", user, password)
            self.assertEqual(status, 401, (user, password))
        self.assertEqual(dav_request(self.port, "PROPFIND", "/dav/", key=self.key)[0], 401, "no login at all")

    def test_without_the_key_even_the_right_login_gets_nothing(self) -> None:
        import http.client
        with self.assertRaises((http.client.RemoteDisconnected, ConnectionResetError)):
            dav_request(self.port, "PROPFIND", "/dav/", user="bob", password=self.ben_pw)

    def test_a_user_cannot_reach_another_users_folders(self) -> None:
        for path in ("/dav/Films/", "/dav/TV/", "/dav/Private/", "/dav/Series/"):
            status, _, body = self.dav("PROPFIND", path, "eve", self.eve_pw)
            if path == "/dav/Series/":
                self.assertEqual(status, 207)
            else:
                self.assertIn(status, (403, 404), path)
        status, _, body = self.dav("GET", "/dav/Films/" + urllib.parse.quote("È quasi magia (1989).mkv"), "eve", self.eve_pw)
        self.assertIn(status, (403, 404))
        self.assertNotIn(self.payload[:100], body)

    def test_nothing_outside_the_shared_folders_can_be_reached(self) -> None:
        attempts = ["/dav/../outside-secret.txt", "/dav/%2e%2e/outside-secret.txt", "/dav/Films/../../outside-secret.txt", "/dav/Films/%2e%2e/%2e%2e/outside-secret.txt",
                    "/dav/Films/..%2f..%2foutside-secret.txt", "/dav/Films/..%5c..%5coutside-secret.txt", "/dav/Films/%252e%252e/%252e%252e/outside-secret.txt", "/dav/..%2fPrivate/diary.txt",
                    "/dav/Films/....//....//outside-secret.txt", "/dav/Films/%00/../Private/diary.txt"]
        for path in attempts:
            status, _, body = self.dav("GET", path, "bob", self.ben_pw)
            self.assertNotIn(b"OUTSIDE-SECRET", body, path)
            self.assertNotIn(b"top secret", body, path)
            self.assertNotEqual(status, 200, path)

    def test_a_symbolic_link_cannot_be_used_to_leave_the_share(self) -> None:
        link = self.share / "Films" / "escape"
        try:
            os.symlink(self.tmp, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("symbolic links are not available here")
        self.addCleanup(lambda: link.unlink() if link.is_symlink() else None)
        for path in ("/dav/Films/escape/outside-secret.txt", "/dav/Films/escape/"):
            status, _, body = self.dav("GET" if path.endswith(".txt") else "PROPFIND", path, "bob", self.ben_pw)
            self.assertNotIn(b"OUTSIDE-SECRET", body, path)
            self.assertNotIn(b"outside-secret.txt", body if path.endswith("/") else b"", path)

    def test_nobody_can_upload_rename_or_create_folders_even_if_the_gate_were_bypassed(self) -> None:
        """Defence in depth: talk to SFTPGo's WebDAV port directly, as if the access gate and the method filter did not exist."""
        direct = self.app.settings.get("dav_port")
        for method, path, extra in (("PUT", "/dav/Films/new.txt", {"body": b"x"}), ("MKCOL", "/dav/Films/newdir", {}), ("MOVE", "/dav/Films/Ranma%20%C2%BD", {"headers": {"Destination": "/dav/Films/moved"}}),
                                    ("COPY", "/dav/Films/Ranma%20%C2%BD", {"headers": {"Destination": "/dav/Films/copied"}}), ("PROPPATCH", "/dav/Films/", {"body": b"<x/>"})):
            status, _, _ = dav_request(direct, method, path, user="bob", password=self.ben_pw, **extra)
            self.assertIn(status, (403, 405, 400, 501), (method, status))
        self.assertFalse((self.share / "Films" / "new.txt").exists())
        self.assertFalse((self.share / "Films" / "newdir").exists())
        self.assertFalse((self.share / "Films" / "moved").exists())

    def test_delete_works_only_where_the_mode_allows_it(self) -> None:
        victim = self.share / "Films" / "to-delete.mkv"
        victim.write_bytes(b"bye")
        status, _, _ = self.dav("DELETE", "/dav/Films/to-delete.mkv", "bob", self.ben_pw)
        self.assertIn(status, (200, 204))
        self.assertFalse(victim.exists(), "pull mode deletes the real file")
        keep = self.share / "Series" / "Show [2026]" / "s01e01.mkv"
        status, _, _ = self.dav("DELETE", "/dav/TV/" + urllib.parse.quote("Show [2026]") + "/s01e01.mkv", "bob", self.ben_pw)
        self.assertEqual(status, 403)
        status, _, _ = self.dav("DELETE", "/dav/Series/" + urllib.parse.quote("Show [2026]") + "/s01e01.mkv", "eve", self.eve_pw)
        self.assertEqual(status, 403)
        self.assertTrue(keep.exists(), "read mode never deletes")
        status, _, _ = self.dav("DELETE", "/dav/Films/", "bob", self.ben_pw)
        self.assertIn(status, (403, 405), "a whole shared folder cannot be deleted")
        self.assertTrue((self.share / "Films").is_dir())

    def test_the_discovery_document_needs_the_key_and_names_the_server(self) -> None:
        status, _, body = dav_request(self.port, "GET", "/.well-known/nextpush", key=self.key)
        import json
        self.assertEqual((status, json.loads(body)["name"]), (200, "Test server"))
        self.assertNotIn(b"bob", body)

    def test_an_invite_has_the_address_the_key_and_never_the_password(self) -> None:
        status, body, _ = self.admin.get("/api/users/bob/invite")
        self.assertEqual(status, 200)
        data = parse_invite(body["code"])
        self.assertEqual((data["u"], data["user"], data["l"], data["k"]), ("https://dl.example.com/dav", "bob", "Test server", list(self.key)))
        self.assertNotIn(self.ben_pw, body["code"])
        self.assertEqual(self.admin.get("/api/users/nobody/invite")[0], 400)


class Lifecycle(Stack):
    def test_update_reset_disable_expire_and_delete(self) -> None:
        pw = self.make_user("carl", [{"path": str(self.share / "Films"), "mode": "pull"}])
        self.assertEqual(self.dav("PROPFIND", "/dav/Films/", "carl", pw)[0], 207)
        # change the folders: Films is gone, Series is in
        status, body, _ = self.admin.put("/api/users/carl", {"folders": [{"path": str(self.share / "Series"), "mode": "read"}]})
        self.assertEqual(status, 200, body)
        self.assertIn(self.dav("PROPFIND", "/dav/Films/", "carl", pw)[0], (403, 404))
        self.assertEqual(self.dav("PROPFIND", "/dav/Series/", "carl", pw)[0], 207)
        # reset the password
        status, body, _ = self.admin.post("/api/users/carl/password")
        new_pw = body["password"]
        self.assertNotEqual(new_pw, pw)
        self.assertEqual(self.dav("PROPFIND", "/dav/Series/", "carl", pw)[0], 401, "the old password stops working")
        self.assertEqual(self.dav("PROPFIND", "/dav/Series/", "carl", new_pw)[0], 207)
        # disable, then enable
        self.assertEqual(self.admin.put("/api/users/carl", {"enabled": False})[0], 200)
        self.assertIn(self.dav("PROPFIND", "/dav/Series/", "carl", new_pw)[0], (401, 403))
        self.assertEqual(self.admin.put("/api/users/carl", {"enabled": True})[0], 200)
        self.assertEqual(self.dav("PROPFIND", "/dav/Series/", "carl", new_pw)[0], 207)
        # an expiry date in the past locks the account
        self.assertEqual(self.admin.put("/api/users/carl", {"expires": "2001-01-01"})[0], 200)
        self.assertIn(self.dav("PROPFIND", "/dav/Series/", "carl", new_pw)[0], (401, 403))
        self.assertEqual(self.admin.get("/api/users/carl")[1]["user"]["expires"], "2001-01-01")
        self.assertEqual(self.admin.put("/api/users/carl", {"expires": ""})[0], 200)
        self.assertEqual(self.dav("PROPFIND", "/dav/Series/", "carl", new_pw)[0], 207)
        # delete
        self.assertEqual(self.admin.delete("/api/users/carl")[0], 200)
        self.assertEqual(self.dav("PROPFIND", "/dav/Series/", "carl", new_pw)[0], 401)
        self.assertEqual(self.admin.get("/api/users/carl")[0], 400)
        self.assertFalse((self.share / "Series").exists() is False, "deleting a user never touches the files")

    def test_changing_a_folders_mode_takes_effect(self) -> None:
        pw = self.make_user("dora", [{"path": str(self.share / "Films"), "mode": "pull"}])
        self.addCleanup(self.drop_user, "dora")
        tmp = self.share / "Films" / "mode-test.bin"
        tmp.write_bytes(b"1")
        self.admin.put("/api/users/dora", {"folders": [{"path": str(self.share / "Films"), "mode": "read"}]})
        self.assertEqual(self.dav("DELETE", "/dav/Films/mode-test.bin", "dora", pw)[0], 403)
        self.assertTrue(tmp.exists())
        self.admin.put("/api/users/dora", {"folders": [{"path": str(self.share / "Films"), "mode": "pull"}]})
        self.assertIn(self.dav("DELETE", "/dav/Films/mode-test.bin", "dora", pw)[0], (200, 204))
        self.assertFalse(tmp.exists())

    def test_validation_errors_are_clear(self) -> None:
        folder = [{"path": str(self.share / "Films")}]
        cases = [({"username": "Bad Name", "folders": folder}, "user name"), ({"username": "x", "folders": folder}, "user name"), ({"username": "okname", "folders": []}, "at least one folder"),
                 ({"username": "okname", "folders": [{"path": str(self.tmp / "nope")}]}, "does not exist"), ({"username": "okname", "folders": [{"path": str(self.app.data_dir)}]}, "data folder"),
                 ({"username": "okname", "folders": [{"path": os.path.abspath(os.sep)}]}, "whole drive"), ({"username": "okname", "folders": folder, "password": "short"}, "8 characters"),
                 ({"username": "okname", "folders": [{"path": str(self.share / "Films"), "mode": "write"}]}, "mode"), ({"username": "okname", "folders": [{"path": str(self.share / "Films"), "name": "a/b"}]}, "not a valid folder name"),
                 ({"username": "okname", "folders": [{"path": str(self.share / "Films"), "name": "X"}, {"path": str(self.share / "Series"), "name": "x"}]}, "Two folders"),
                 ({"username": "okname", "folders": folder, "expires": "tomorrow"}, "expiry")]
        for body, fragment in cases:
            status, data, _ = self.admin.post("/api/users", body)
            self.assertEqual(status, 400, body)
            self.assertIn(fragment.lower(), data["error"].lower(), body)
        self.assertEqual(self.admin.get("/api/users/okname")[0], 400, "nothing half-created was left behind")

    def test_a_duplicate_user_is_refused(self) -> None:
        self.make_user("erin", [{"path": str(self.share / "Films")}])
        self.addCleanup(self.drop_user, "erin")
        status, data, _ = self.admin.post("/api/users", {"username": "erin", "folders": [{"path": str(self.share / "Films")}]})
        self.assertEqual(status, 400)
        self.assertIn("already exists", data["error"])

    def test_the_speed_limit_is_enforced(self) -> None:
        pw = self.make_user("fred", [{"path": str(self.share / "Films")}], download_kbps=150)
        self.addCleanup(self.drop_user, "fred")
        started = time.time()
        status, _, body = self.dav("GET", "/dav/Films/" + urllib.parse.quote("È quasi magia (1989).mkv"), "fred", pw)
        elapsed = time.time() - started
        self.assertEqual((status, len(body)), (200, len(self.payload)))
        self.assertGreater(elapsed, 1.2, f"300 KB at 150 KB/s should take about 2 s, took {elapsed:.2f}")

    def test_activity_shows_who_is_connected_and_from_where(self) -> None:
        pw = self.make_user("gina", [{"path": str(self.share / "Films")}], download_kbps=60)
        self.addCleanup(self.drop_user, "gina")
        done = threading.Event()

        def slow_download() -> None:
            try:
                self.dav("GET", "/dav/Films/" + urllib.parse.quote("È quasi magia (1989).mkv"), "gina", pw)
            except Exception:   # noqa: BLE001 - being cut off is exactly what this test does to her
                pass
            finally:
                done.set()

        threading.Thread(target=slow_download, daemon=True).start()
        seen = []
        for _ in range(30):
            time.sleep(0.3)
            seen = [c for c in self.admin.get("/api/activity")[1]["connections"] if c["username"] == "gina"]
            if seen:
                break
        self.assertTrue(seen, "the running download appears in the activity list")
        self.assertTrue(seen[0]["address"].startswith("127.0.0.1"), seen[0])
        self.admin.put("/api/users/gina", {"enabled": False})   # disconnects her: the slow download ends
        done.wait(20)

    def test_deleting_the_last_user_of_a_folder_removes_its_sftpgo_folder_object(self) -> None:
        from nextpush.users import folder_object_name
        extra = self.share / "Series" / "Show [2026]"
        self.make_user("hana", [{"path": str(extra)}])
        name = folder_object_name(os.path.realpath(extra))
        self.assertEqual(self.app.api().get_folder(name)["mapped_path"].lower(), os.path.realpath(extra).lower())
        self.admin.delete("/api/users/hana")
        from nextpush.sftpgo import SftpgoApiError
        with self.assertRaises(SftpgoApiError) as raised:
            self.app.api().get_folder(name)
        self.assertEqual(raised.exception.status, 404)


class Throughput(Stack):
    def test_parallel_downloads_are_correct_and_fast_enough(self) -> None:
        big = self.share / "Films" / "big-test.bin"
        data = os.urandom(1024 * 1024) * 64   # 64 MB
        big.write_bytes(data)
        self.addCleanup(lambda: big.unlink() if big.exists() else None)
        pw = self.make_user("ivan", [{"path": str(self.share / "Films")}])
        self.addCleanup(self.drop_user, "ivan")
        results: list[tuple[int, int, bool]] = []

        def fetch() -> None:
            status, _, body = self.dav("GET", "/dav/Films/big-test.bin", "ivan", pw)
            results.append((status, len(body), body == data))

        started = time.time()
        threads = [threading.Thread(target=fetch) for _ in range(4)]
        [t.start() for t in threads]
        [t.join(120) for t in threads]
        elapsed = time.time() - started
        self.assertEqual(results, [(200, len(data), True)] * 4)
        rate = 4 * len(data) / 1e6 / elapsed
        print(f"\n  [throughput] 4 x 64 MB through the proxy in {elapsed:.1f}s = {rate:.0f} MB/s total")
        self.assertGreater(rate, 20, "the proxy must not be the bottleneck of a home connection")


class Resilience(Stack):
    def test_sftpgo_is_restarted_after_a_crash_and_users_survive(self) -> None:
        pw = self.make_user("jade", [{"path": str(self.share / "Films")}])
        self.addCleanup(self.drop_user, "jade")
        self.assertEqual(self.dav("PROPFIND", "/dav/Films/", "jade", pw)[0], 207)
        self.app.supervisor.process.kill()
        self.app.supervisor.process.wait()
        for _ in range(80):
            time.sleep(0.25)
            if self.app.supervisor.restarts >= 1 and self.app.supervisor.healthy():
                break
        self.assertGreaterEqual(self.app.supervisor.restarts, 1)
        self.assertEqual(self.dav("PROPFIND", "/dav/Films/", "jade", pw)[0], 207)
        self.assertEqual(self.admin.get("/api/users/jade")[0], 200)

    def test_the_restart_button(self) -> None:
        before = self.app.supervisor.process.pid
        self.assertEqual(self.admin.post("/api/sftpgo/restart")[0], 200)
        self.assertNotEqual(self.app.supervisor.process.pid, before)
        self.assertTrue(self.app.supervisor.healthy())
        self.assertEqual(self.admin.get("/api/users")[0], 200)


class Persistence(TempCase):
    def test_a_restart_keeps_users_admin_password_key_and_ports(self) -> None:
        share = self.tmp / "m"
        (share / "A").mkdir(parents=True)
        (share / "A" / "f.txt").write_text("hello", encoding="utf-8")
        import nextpush.admin as admin_module
        import nextpush.security as security
        original = admin_module.hash_password
        admin_module.hash_password = lambda password: security.hash_password(password, 1000)
        self.addCleanup(setattr, admin_module, "hash_password", original)
        first = App(self.tmp / "data", binary=BINARY, admin_port=0, public_port=0, admin_host="127.0.0.1", public_host="127.0.0.1", quiet=True)
        first.start()
        admin = Client(first.admin_address[1])
        admin.csrf = admin.post("/api/setup", {"token": first.setup_token(), "password": PASSWORD, "public_url": "https://dl.example.com"})[1]["csrf"]
        pw = admin.post("/api/users", {"username": "kim", "folders": [{"path": str(share / "A")}]})[1]["password"]
        key, ports = first.settings.get("access_key"), (first.settings.get("dav_port"), first.settings.get("api_port"))
        first.stop()
        second = App(self.tmp / "data", binary=BINARY, admin_port=0, public_port=0, admin_host="127.0.0.1", public_host="127.0.0.1", quiet=True)
        second.start()
        self.addCleanup(second.stop)
        self.assertEqual((second.settings.get("access_key"), (second.settings.get("dav_port"), second.settings.get("api_port"))), (key, ports))
        again = Client(second.admin_address[1])
        self.assertFalse(again.get("/api/state")[1]["setup_required"])
        status, body, _ = again.post("/api/login", {"password": PASSWORD})
        self.assertEqual(status, 200)
        again.csrf = body["csrf"]
        self.assertEqual([u["username"] for u in again.get("/api/users")[1]["users"]], ["kim"])
        status, _, body = dav_request(second.public_address[1], "GET", "/dav/A/f.txt", user="kim", password=pw, key=(key["header"], key["value"]))
        self.assertEqual((status, body), (200, b"hello"))


class BruteForce(TempCase):
    """NextPush's own login protection against the real stack. Guessing clients are told apart by their REAL address (the proxy header); the tests use documentation
    addresses instead of 127.0.0.1, which is also the address NextPush itself uses to talk to SFTPGo."""
    GUESSER = {"X-Forwarded-For": "203.0.113.50"}
    OTHER = {"X-Forwarded-For": "203.0.113.77"}

    def setUp(self) -> None:
        super().setUp()
        share = self.tmp / "m"
        (share / "A").mkdir(parents=True)
        self.app = App(self.tmp / "data", binary=BINARY, admin_port=0, public_port=0, admin_host="127.0.0.1", public_host="127.0.0.1", quiet=True)
        self.app.start()
        self.addCleanup(self.app.stop)
        import nextpush.admin as admin_module
        import nextpush.security as security
        original = admin_module.hash_password
        admin_module.hash_password = lambda password: security.hash_password(password, 1000)
        self.addCleanup(setattr, admin_module, "hash_password", original)
        self.admin = Client(self.app.admin_address[1])
        self.admin.csrf = self.admin.post("/api/setup", {"token": self.app.setup_token(), "password": PASSWORD})[1]["csrf"]
        self.pw = self.admin.post("/api/users", {"username": "lea", "folders": [{"path": str(share / "A")}]})[1]["password"]
        self.kim_pw = self.admin.post("/api/users", {"username": "kim", "folders": [{"path": str(share / "A")}], "no_block": True})[1]["password"]
        key = self.app.settings.get("access_key")
        self.pair = (key["header"], key["value"])
        self.port = self.app.public_address[1]

    def dav(self, password: str, headers=None, user: str = "lea") -> int:
        return dav_request(self.port, "PROPFIND", "/dav/", user=user, password=password, key=self.pair, headers=headers)[0]

    def test_sftpgos_own_defender_is_off_because_it_escalates_bans_to_days(self) -> None:
        from nextpush.sftpgo import build_config
        self.assertFalse(build_config(1, 2)["common"]["defender"]["enabled"])

    def test_a_guessing_address_is_locked_listed_and_can_be_unlocked(self) -> None:
        self.assertEqual(self.dav(self.pw, self.GUESSER), 207)
        statuses = [self.dav("wrong-%d" % i, self.GUESSER) for i in range(8)]
        self.assertEqual(statuses, [401] * 8)
        self.assertEqual(self.dav(self.pw, self.GUESSER), 429, "even the right password waits while the address is locked")
        self.assertEqual(self.dav(self.pw, self.OTHER), 207, "another address is not affected")
        status, body, _ = self.admin.get("/api/blocked")
        self.assertEqual((status, [b["ip"] for b in body["blocked"]]), (200, ["203.0.113.50"]))
        self.assertLessEqual(body["blocked"][0]["seconds"], 600)
        for i in range(100):   # a client that keeps trying while locked cannot make the lock longer: those attempts are not even forwarded
            self.dav("again-%d" % i, self.GUESSER)
        self.assertLessEqual(self.admin.get("/api/blocked")[1]["blocked"][0]["seconds"], 600, "the lock is capped at ten minutes, never days")
        self.assertEqual(self.admin.delete("/api/blocked/203.0.113.50")[0], 200)
        self.assertEqual(self.admin.get("/api/blocked")[1]["blocked"], [])
        self.assertEqual(self.dav(self.pw, self.GUESSER), 207, "after unblocking the right password works at once")

    def test_an_exempt_user_is_never_locked_out_and_the_flag_can_be_changed(self) -> None:
        users = {u["username"]: u for u in self.admin.get("/api/users")[1]["users"]}
        self.assertEqual((users["kim"]["no_block"], users["lea"]["no_block"]), (True, False))
        self.assertEqual([self.dav("wrong-%d" % i, self.GUESSER, user="kim") for i in range(30)], [401] * 30)
        self.assertEqual(self.dav(self.kim_pw, self.GUESSER, user="kim"), 207, "still fine after thirty wrong passwords")
        self.assertEqual(self.admin.get("/api/blocked")[1]["blocked"], [])
        self.assertEqual(self.admin.put("/api/users/kim", {"no_block": False})[1]["user"]["no_block"], False)
        [self.dav("wrong-%d" % i, self.GUESSER, user="kim") for i in range(8)]
        self.assertEqual(self.dav(self.kim_pw, self.GUESSER, user="kim"), 429, "no longer exempt")
        self.assertEqual(self.admin.put("/api/users/kim", {"no_block": True})[1]["user"]["no_block"], True)
        self.assertEqual(self.dav(self.kim_pw, self.GUESSER, user="kim"), 207, "exempt again: the lock does not apply to this user")
        self.admin.delete("/api/users/kim")
        self.assertNotIn("kim", self.app.settings.get("no_block_users"), "deleting a user forgets the exemption")

    def test_the_log_says_why_a_login_was_refused_and_locked(self) -> None:
        for i in range(9):
            self.dav("wrong-password", self.GUESSER)
        text = "\n".join(self.app.log_lines)
        self.assertIn("login refused (HTTP 401)", text)
        self.assertIn("locked out for", text)
        self.assertIn("203.0.113.50", text)
        self.assertNotIn(self.pw, text)
        self.assertNotIn("wrong-password", text)


if __name__ == "__main__":
    unittest.main()
