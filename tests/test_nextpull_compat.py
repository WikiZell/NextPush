"""The real NextPull client (its connection code and the real rclone it drives) against a real NextPush + SFTPGo.

Needs: the real SFTPGo (see test_integration), a NextPull checkout (``NEXTPULL_DIR``, or a sibling folder called NextPull) and its rclone. Skipped otherwise."""
from __future__ import annotations

import os
import sys
import unittest
import urllib.parse
from pathlib import Path

import helpers
from helpers import Client, TempCase, sftpgo_binary
from nextpush.app import App

BINARY = sftpgo_binary()
NEXTPULL = Path(os.environ.get("NEXTPULL_DIR") or helpers.ROOT.parent / "NextPull")
PASSWORD = "a long and unusual admin passphrase"


def load_nextpull():
    """NextPull's modules have flat names (connections, nc_client, rclone_runner...): put its folder first on the path, once."""
    if not (NEXTPULL / "connections.py").is_file():
        return None
    if str(NEXTPULL) not in sys.path:
        sys.path.insert(0, str(NEXTPULL))
    try:
        import connections
        import nc_client
        import rclone_runner
        import store
        return connections, nc_client, rclone_runner, store
    except Exception:   # noqa: BLE001 - not importable on this platform: skip
        return None


NP = load_nextpull() if BINARY else None
RCLONE = NP[2].find_rclone("", [NEXTPULL]) if NP else None


@unittest.skipUnless(BINARY and NP and RCLONE, "needs the real SFTPGo, a NextPull checkout and its rclone")
class NextPullAgainstNextPush(TempCase):
    def setUp(self) -> None:
        super().setUp()
        import nextpush.admin as admin_module
        import nextpush.security as security
        original = admin_module.hash_password
        admin_module.hash_password = lambda password: security.hash_password(password, 1000)
        self.addCleanup(setattr, admin_module, "hash_password", original)
        self.share = self.tmp / "media"
        (self.share / "Films" / "Ranma ½").mkdir(parents=True)
        (self.share / "Films" / "Ranma ½" / "Episodio 01 [BDRip].mkv").write_bytes(b"ranma" * 2000)
        (self.share / "Films" / "È quasi magia (1989).mkv").write_bytes(os.urandom(50_000))
        (self.share / "Films" / "it's a file (1).mkv").write_bytes(b"apos" * 500)
        self.app = App(self.tmp / "data", binary=BINARY, admin_port=0, public_port=0, admin_host="127.0.0.1", public_host="127.0.0.1", quiet=True)
        self.app.start()
        self.addCleanup(self.app.stop)
        self.admin = Client(self.app.admin_address[1])
        self.public_url = f"http://127.0.0.1:{self.app.public_address[1]}"
        self.admin.csrf = self.admin.post("/api/setup", {"token": self.app.setup_token(), "password": PASSWORD, "server_name": "Home server", "public_url": self.public_url})[1]["csrf"]
        self.passwords = {}
        for name, mode in (("bob", "pull"), ("viewer", "read")):
            status, body, _ = self.admin.post("/api/users", {"username": name, "folders": [{"path": str(self.share / "Films"), "mode": mode}]})
            self.assertEqual(status, 201, body)
            self.passwords[name] = body["password"]

    def connection(self, name: str, **override) -> dict:
        connections, *_ = NP
        invite = self.admin.get(f"/api/users/{name}/invite")[1]["code"]
        fields = connections.parse_invite(invite)   # NextPull's own parser reads NextPush's code
        record = connections.clean_connection({**fields, "password": self.passwords[name], **override})
        return {**record, "type": "sftpgo"}

    def test_nextpull_reads_the_invite_lists_folders_and_logs_in(self) -> None:
        connections, nc_client, *_ = NP
        record = self.connection("bob")
        self.assertEqual((record["server"], record["user"], record["label"], record["header_name"]), (self.public_url + "/dav", "bob", "Home server", "X-Pull-Key"))
        session = connections.make_session(record)
        self.assertEqual([e.name for e in session.list_dir("")], ["Films"])
        self.assertEqual([e.name for e in session.list_dir("Films")], ["Ranma ½", "it's a file (1).mkv", "È quasi magia (1989).mkv"])
        self.assertTrue(session.check_folder("Films"))
        self.assertEqual(session.user_info()["display_name"], "bob")

    def test_a_wrong_password_a_missing_key_and_a_wrong_key_are_reported_clearly(self) -> None:
        connections, nc_client, *_ = NP
        with self.assertRaisesRegex(nc_client.NextcloudError, "rejected the saved login"):
            connections.make_session(self.connection("bob", password="wrong password!")).list_dir("")
        for key in ({"header_name": "", "header_value": ""}, {"header_value": "not-the-key"}):
            with self.assertRaisesRegex(nc_client.NextcloudError, "Cannot reach the server"):
                connections.make_session(self.connection("bob", **key)).list_dir("")

    def test_discovery_finds_nextpush_when_the_key_is_off_and_nothing_when_it_is_on(self) -> None:
        connections, *_ = NP
        self.assertEqual(connections.discover(self.public_url, timeout=5)["type"], "unknown", "the key gate hides the server from strangers, discovery included")
        self.admin.put("/api/settings", {"access_key_enabled": False})
        found = connections.discover(self.public_url, timeout=5)
        self.assertEqual((found["ok"], found["type"], found["product"], found["name"], found["server"]), (True, "sftpgo", "NextPush", "Home server", self.public_url + "/dav"))

    def run_nextpull(self, name: str, mode: str):
        _, _, runner, store = NP
        history = store.HistoryDb(None)
        self.addCleanup(history.close)
        job = store.clean_job({"name": "t", "source": "Films", "dest": str(self.tmp / f"dest-{name}-{mode}"), "mode": mode, "min_age_minutes": 0, "schedule": {"days": [0], "time": "02:00", "stop_by": ""}})
        run_id = history.start_run(job, "manual")
        run = runner.RcloneRun(rclone=RCLONE, job=job, creds=self.connection(name), run_id=run_id, history=history, log_path=self.tmp / "logs" / f"{name}-{mode}.jsonl",
                               config_path=self.tmp / "rclone.empty.conf", keep_awake=False)
        return run.run(), Path(job["dest"])

    def test_a_copy_through_the_whole_chain_is_byte_exact(self) -> None:
        result, dest = self.run_nextpull("bob", "copy")
        self.assertEqual(result["state"], "ok", result)
        self.assertEqual((dest / "Ranma ½" / "Episodio 01 [BDRip].mkv").read_bytes(), b"ranma" * 2000)
        self.assertEqual((dest / "È quasi magia (1989).mkv").read_bytes(), (self.share / "Films" / "È quasi magia (1989).mkv").read_bytes())
        self.assertTrue((dest / "it's a file (1).mkv").is_file())
        self.assertEqual(len(list((self.share / "Films").rglob("*.mkv"))), 3, "a copy leaves the server alone")

    def test_a_move_with_the_pull_mode_deletes_what_it_downloaded_and_keeps_the_folder(self) -> None:
        result, dest = self.run_nextpull("bob", "move")
        self.assertEqual(result["state"], "ok", result)
        self.assertEqual(result["files"], 3)
        self.assertEqual([p for p in (self.share / "Films").rglob("*") if p.is_file()], [], "every source file was removed after its verified copy")
        self.assertTrue((self.share / "Films").is_dir(), "the shared folder itself is never removed")
        self.assertTrue((dest / "Ranma ½" / "Episodio 01 [BDRip].mkv").is_file())

    def test_a_move_with_the_read_only_mode_downloads_but_cannot_delete(self) -> None:
        result, dest = self.run_nextpull("viewer", "move")
        self.assertIn(result["state"], ("partial", "failed"), result)
        self.assertTrue((dest / "it's a file (1).mkv").is_file(), "the files were still downloaded")
        self.assertEqual(len(list((self.share / "Films").rglob("*.mkv"))), 3, "nothing was deleted on the server")
        self.assertIn("not deleted", result["message"])

    def test_the_secrets_never_appear_in_nextpulls_run_log(self) -> None:
        result, _ = self.run_nextpull("bob", "copy")
        text = "".join(p.read_text(encoding="utf-8", errors="replace") for p in (self.tmp / "logs").glob("*.jsonl"))
        self.assertNotIn(self.passwords["bob"], text)
        self.assertNotIn(self.app.settings.get("access_key")["value"], text)


if __name__ == "__main__":
    unittest.main()
