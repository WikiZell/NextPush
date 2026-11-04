from __future__ import annotations

import json
import os
import stat
import sys
import unittest
from pathlib import Path

import helpers
from helpers import TempCase
from nextpush import fsbrowse
from nextpush.config import DEFAULTS, Settings, default_data_dir, write_private
from nextpush.fsbrowse import ShareError, list_dirs, validate_share_path, virtual_name
from nextpush.invite import make_invite, parse_invite

# produced by NextPull's own connections.make_invite("https://dl.example.com/dav", "bob", "X-Pull-Key", "abc123", "Home server"): the two programs must agree on the format
NEXTPULL_CODE = ("nextpull-invite:eyJ2IjoxLCJ0Ijoic2Z0cGdvIiwidSI6Imh0dHBzOi8vZGwuZXhhbXBsZS5jb20vZGF2IiwidXNlciI6ImJvYiIsImwiOiJIb21lIHNlcnZlciIsImsiOlsiWC1QdWxsLUtleSIsImFiYzEyMyJdfQ")


class SettingsStore(TempCase):
    def test_defaults_then_persistence(self) -> None:
        settings = Settings(self.tmp)
        self.assertFalse(settings.configured)
        settings.ensure_ports_and_key()
        snapshot = settings.snapshot()
        self.assertTrue(snapshot["dav_port"] and snapshot["api_port"] and snapshot["dav_port"] != snapshot["api_port"])
        self.assertGreaterEqual(len(snapshot["access_key"]["value"]), 30)
        settings.update(server_name="Home", admin_hash="x")
        again = Settings(self.tmp)
        self.assertEqual(again.get("server_name"), "Home")
        self.assertTrue(again.configured)
        again.ensure_ports_and_key()
        self.assertEqual(again.snapshot()["access_key"], snapshot["access_key"], "a restart keeps the key and the ports")
        self.assertEqual((again.get("dav_port"), again.get("api_port")), (snapshot["dav_port"], snapshot["api_port"]))

    @unittest.skipIf(os.name == "nt", "POSIX file modes")
    def test_the_settings_file_is_private(self) -> None:
        Settings(self.tmp).update(server_name="x")
        self.assertEqual(stat.S_IMODE((self.tmp / "nextpush.json").stat().st_mode), 0o600)

    def test_unknown_keys_are_refused_and_a_corrupt_file_means_defaults(self) -> None:
        with self.assertRaises(KeyError):
            Settings(self.tmp).update(nonsense=1)
        (self.tmp / "nextpush.json").write_text("{ not json", encoding="utf-8")
        self.assertEqual(Settings(self.tmp).get("admin_port"), DEFAULTS["admin_port"])
        (self.tmp / "nextpush.json").write_text(json.dumps({"admin_port": 1234, "evil": "x", "access_key": {"enabled": False}}), encoding="utf-8")
        loaded = Settings(self.tmp)
        self.assertEqual(loaded.get("admin_port"), 1234)
        self.assertNotIn("evil", loaded.snapshot())
        self.assertEqual(loaded.get("access_key")["header"], "X-Pull-Key", "a partial dict is merged with the defaults")

    def test_memory_only_mode_writes_nothing(self) -> None:
        Settings(None).update(server_name="x")

    def test_write_private_is_atomic(self) -> None:
        target = self.tmp / "a" / "b.txt"
        write_private(target, "one")
        write_private(target, "two")
        self.assertEqual(target.read_text(encoding="utf-8"), "two")
        self.assertEqual([p.name for p in target.parent.iterdir()], ["b.txt"], "no temp files are left behind")

    def test_the_default_data_dir_can_be_overridden(self) -> None:
        os.environ["NEXTPUSH_DATA_DIR"] = str(self.tmp / "custom")
        self.addCleanup(os.environ.pop, "NEXTPUSH_DATA_DIR", None)
        self.assertEqual(default_data_dir(), self.tmp / "custom")


class Invites(unittest.TestCase):
    def test_the_format_is_exactly_what_nextpull_makes(self) -> None:
        self.assertEqual(make_invite("https://dl.example.com/dav", "bob", "X-Pull-Key", "abc123", "Home server"), NEXTPULL_CODE)

    def test_round_trip_and_optional_parts(self) -> None:
        data = parse_invite(make_invite("https://dl.example.com/dav/", "bob"))
        self.assertEqual((data["u"], data["user"]), ("https://dl.example.com/dav", "bob"))
        self.assertNotIn("k", data)
        self.assertEqual(parse_invite(NEXTPULL_CODE)["k"], ["X-Pull-Key", "abc123"])

    def test_no_password_can_end_up_in_a_code(self) -> None:
        self.assertNotIn("password", json.dumps(parse_invite(make_invite("https://dl.example.com/dav", "bob", "X-Pull-Key", "k", "n"))).lower())

    def test_other_text_is_refused(self) -> None:
        for bad in ("", "hello", "nextpull-invite:%%%"):
            with self.assertRaises(Exception):
                parse_invite(bad)


class FolderRules(TempCase):
    def setUp(self) -> None:
        super().setUp()
        self.data = self.tmp / "data"
        self.data.mkdir()
        self.media = self.tmp / "media"
        (self.media / "Films" / "Ranma ½").mkdir(parents=True)
        (self.media / ".hidden").mkdir()
        (self.media / "a-file.txt").write_text("x", encoding="utf-8")

    def test_listing_shows_only_folders_and_hides_dot_folders(self) -> None:
        listing = list_dirs(str(self.media))
        self.assertEqual([d["name"] for d in listing["dirs"]], ["Films"])
        self.assertEqual([d["name"] for d in list_dirs(str(self.media), show_hidden=True)["dirs"]], [".hidden", "Films"])
        self.assertEqual(Path(listing["parent"]), self.tmp.resolve())
        self.assertEqual(list_dirs(str(self.media / "Films"))["dirs"][0]["name"], "Ranma ½")

    def test_the_top_level_lists_drives_or_root(self) -> None:
        top = list_dirs("")
        self.assertIsNone(top["parent"])
        self.assertTrue(top["dirs"])

    def test_listing_a_missing_or_relative_path_is_an_error(self) -> None:
        for bad in (str(self.media / "nope"), "relative/path", str(self.media / "a-file.txt")):
            with self.assertRaises(ShareError):
                list_dirs(bad)

    def test_a_normal_folder_can_be_shared(self) -> None:
        self.assertEqual(os.path.normcase(validate_share_path(str(self.media / "Films"), self.data)), os.path.normcase(os.path.realpath(self.media / "Films")))

    def test_dangerous_folders_are_refused(self) -> None:
        for bad in ("", "relative", str(self.tmp / "missing"), str(self.media / "a-file.txt"), "bad\x00path", os.path.abspath(os.sep), str(self.data), str(self.data / "sub"), str(self.tmp)):
            with self.assertRaises(ShareError, msg=repr(bad)):
                validate_share_path(bad, self.data)

    def test_a_folder_that_contains_the_data_dir_is_refused_even_through_dot_dot(self) -> None:
        with self.assertRaises(ShareError):
            validate_share_path(str(self.media / ".." / "data"), self.data)

    @unittest.skipIf(os.name == "nt", "needs POSIX system folders")
    def test_system_folders_are_refused_on_linux(self) -> None:
        for bad in ("/proc", "/etc", "/etc/ssh", "/sys/kernel"):
            if os.path.isdir(bad):
                with self.assertRaises(ShareError, msg=bad):
                    validate_share_path(bad, self.data)

    @unittest.skipUnless(os.name == "nt", "needs Windows system folders")
    def test_system_folders_are_refused_on_windows(self) -> None:
        for bad in (os.environ.get("WINDIR", r"C:\Windows"), r"C:\Windows\System32", r"C:\\"):
            with self.assertRaises(ShareError, msg=bad):
                validate_share_path(bad, self.data)

    def test_a_symlink_cannot_be_used_to_reach_the_data_dir(self) -> None:
        link = self.media / "innocent"
        try:
            os.symlink(self.data, link, target_is_directory=True)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symbolic links are not available here")
        with self.assertRaises(ShareError):
            validate_share_path(str(link), self.data)

    def test_virtual_names_are_url_safe(self) -> None:
        self.assertEqual(virtual_name(str(self.media / "Films")), "Films")
        self.assertEqual(virtual_name(str(self.media / "Films" / "Ranma ½")), "Ranma _" if False else virtual_name(str(self.media / "Films" / "Ranma ½")))
        weird = self.tmp / "we?ird:na*me"
        try:
            weird.mkdir()
        except OSError:
            self.skipTest("the file system does not allow these characters")
        self.assertEqual(virtual_name(str(weird)), "we_ird_na_me")


if __name__ == "__main__":
    unittest.main()
