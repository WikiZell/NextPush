from __future__ import annotations

import unittest

import helpers  # noqa: F401  (puts the project on sys.path)
from nextpush import security
from nextpush.security import LoginLimiter, SessionStore, check_admin_password, client_ip, hash_password, valid_username, verify_password

FAST = 1000   # iterations: the real count is slow on purpose


class Passwords(unittest.TestCase):
    def test_round_trip_and_salting(self) -> None:
        one, two = hash_password("correct horse battery", FAST), hash_password("correct horse battery", FAST)
        self.assertNotEqual(one, two, "every hash has its own salt")
        self.assertTrue(verify_password("correct horse battery", one))
        self.assertFalse(verify_password("correct horse batterY", one))
        self.assertFalse(verify_password("", one))

    def test_garbage_in_the_store_never_verifies_and_never_raises(self) -> None:
        for stored in ("", "x", "a$b$c$d", "pbkdf2_sha256$notanumber$AA==$AA==", "md5$1$AA==$AA==", "pbkdf2_sha256$1000$!!!$!!!"):
            self.assertFalse(verify_password("anything", stored), stored)

    def test_the_default_cost_is_high(self) -> None:
        self.assertGreaterEqual(security.PBKDF2_ITERATIONS, 600_000)
        self.assertIn(f"${security.PBKDF2_ITERATIONS}$", hash_password("x"))

    def test_admin_password_policy(self) -> None:
        self.assertIsNotNone(check_admin_password("short"))
        self.assertIsNotNone(check_admin_password("aaaaaaaaaaaa"))
        self.assertIsNone(check_admin_password("a long enough passphrase"))

    def test_generated_passwords_are_long_and_different(self) -> None:
        a, b = security.generate_password(), security.generate_password()
        self.assertNotEqual(a, b)
        self.assertGreaterEqual(len(a), 30)

    def test_user_names(self) -> None:
        for good in ("bob", "bob.smith", "a1", "x-y_z", "0day"):
            self.assertTrue(valid_username(good), good)
        for bad in ("", "a", "Bob", "-bob", ".bob", "bob smith", "bob/../x", "bob\n", "é", "x" * 33, "bob;drop", "../x"):
            self.assertFalse(valid_username(bad), repr(bad))


class Sessions(unittest.TestCase):
    def test_idle_and_absolute_expiry(self) -> None:
        now = [1000.0]
        store = SessionStore(clock=lambda: now[0])
        session = store.create("1.2.3.4")
        self.assertIsNotNone(store.get(session.token))
        now[0] += security.SESSION_IDLE_SECONDS - 1
        self.assertIsNotNone(store.get(session.token), "activity renews the idle timer")
        now[0] += security.SESSION_IDLE_SECONDS + 1
        self.assertIsNone(store.get(session.token))
        second = store.create("1.2.3.4")
        for _ in range(20):   # always active, but older than the absolute limit
            now[0] += security.SESSION_MAX_SECONDS / 10
            store.get(second.token)
        self.assertIsNone(store.get(second.token))

    def test_tokens_are_unguessable_and_deletable(self) -> None:
        store = SessionStore()
        a, b = store.create("x"), store.create("x")
        self.assertNotEqual(a.token, b.token)
        self.assertNotEqual(a.csrf, a.token)
        self.assertGreaterEqual(len(a.token), 40)
        store.delete(a.token)
        self.assertIsNone(store.get(a.token))
        self.assertIsNone(store.get(None))
        self.assertIsNone(store.get("forged"))
        store.clear()
        self.assertIsNone(store.get(b.token))


class Lockout(unittest.TestCase):
    def test_five_free_attempts_then_growing_locks_and_a_reset(self) -> None:
        now = [0.0]
        limiter = LoginLimiter(clock=lambda: now[0])
        for _ in range(4):
            limiter.failure("a")
            self.assertEqual(limiter.locked_for("a"), 0)
        limiter.failure("a")                      # fifth: locked 2 s
        self.assertEqual(limiter.locked_for("a"), 2)
        now[0] += 3
        limiter.failure("a")                      # sixth: 4 s
        self.assertEqual(limiter.locked_for("a"), 4)
        for _ in range(30):
            now[0] += 1000
            limiter.failure("a")
        self.assertLessEqual(limiter.locked_for("a"), LoginLimiter.MAX_LOCK)
        self.assertEqual(limiter.locked_for("someone-else"), 0, "one address never locks another")
        limiter.success("a")
        self.assertEqual(limiter.locked_for("a"), 0)


class LimiterParameters(unittest.TestCase):
    def test_custom_free_attempts_cap_and_snapshot(self) -> None:
        now = [0.0]
        limiter = LoginLimiter(clock=lambda: now[0], free_attempts=8, max_lock=600)
        for _ in range(7):
            limiter.failure("a")
        self.assertEqual((limiter.locked_for("a"), limiter.snapshot()), (0, []))
        limiter.failure("a")
        self.assertEqual(limiter.snapshot(), [("a", 2)])
        for _ in range(40):
            now[0] += 700
            limiter.failure("a")
        self.assertLessEqual(limiter.locked_for("a"), 600, "the lock never exceeds the cap")
        limiter.failure("b")
        self.assertEqual([ip for ip, _ in limiter.snapshot()], ["a"], "only locked addresses are listed")
        now[0] += 10_000
        self.assertEqual(limiter.snapshot(), [])


class ClientAddress(unittest.TestCase):
    TRUSTED = ["127.0.0.0/8", "192.168.0.0/16"]

    def test_a_direct_client_cannot_forge_the_header(self) -> None:
        self.assertEqual(client_ip("203.0.113.7", "1.2.3.4", self.TRUSTED), "203.0.113.7")

    def test_behind_a_trusted_proxy_the_rightmost_untrusted_entry_wins(self) -> None:
        self.assertEqual(client_ip("192.168.1.20", "198.51.100.9", self.TRUSTED), "198.51.100.9")
        self.assertEqual(client_ip("192.168.1.20", "6.6.6.6, 198.51.100.9", self.TRUSTED), "198.51.100.9", "entries added on the left by the client are ignored")
        self.assertEqual(client_ip("192.168.1.20", "198.51.100.9, 192.168.1.30", self.TRUSTED), "198.51.100.9", "trusted hops on the right are skipped")

    def test_junk_falls_back_to_the_peer(self) -> None:
        for junk in ("", "not-an-ip", "1.2.3.4, evil"):
            self.assertEqual(client_ip("192.168.1.20", junk, self.TRUSTED), "192.168.1.20", junk)
        self.assertEqual(client_ip("192.168.1.20", "10.0.0.5", ["not a network", *self.TRUSTED]), "10.0.0.5")


if __name__ == "__main__":
    unittest.main()
