"""Passwords, sessions, brute-force limits and the small checks that keep input safe. Standard library only."""
from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Iterable

PBKDF2_ITERATIONS = 600_000
SESSION_IDLE_SECONDS = 12 * 3600
SESSION_MAX_SECONDS = 7 * 24 * 3600
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
MIN_ADMIN_PASSWORD = 10


# ---------------------------------------------------------------------------------------------- passwords

def hash_password(password: str, iterations: int = PBKDF2_ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check; any malformed stored value simply fails."""
    try:
        scheme, iterations, salt_b64, digest_b64 = stored.split("$")
        if scheme != "pbkdf2_sha256":
            return False
        expected = base64.b64decode(digest_b64)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), base64.b64decode(salt_b64), int(iterations))
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, actual)


def constant_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


def generate_password() -> str:
    """A long random password for a user (about 190 bits)."""
    return secrets.token_urlsafe(24)


def valid_username(name: str) -> bool:
    return bool(USERNAME.fullmatch(name or ""))   # fullmatch: "$" would accept a trailing newline


def check_admin_password(password: str) -> str | None:
    """None if acceptable, else the reason."""
    if len(password) < MIN_ADMIN_PASSWORD:
        return f"The password must have at least {MIN_ADMIN_PASSWORD} characters"
    if len(set(password)) < 4:
        return "The password is too repetitive"
    return None


# ---------------------------------------------------------------------------------------------- sessions

@dataclass
class Session:
    token: str
    csrf: str
    created: float
    last_seen: float
    ip: str


class SessionStore:
    """In-memory sessions: an unguessable cookie value plus a CSRF token that the page must send back in a header."""

    def __init__(self, clock=time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}

    def create(self, ip: str) -> Session:
        now = self._clock()
        session = Session(secrets.token_urlsafe(32), secrets.token_urlsafe(24), now, now, ip)
        with self._lock:
            self._purge(now)
            self._sessions[session.token] = session
        return session

    def get(self, token: str | None) -> Session | None:
        if not token:
            return None
        now = self._clock()
        with self._lock:
            session = self._sessions.get(token)
            if not session:
                return None
            if now - session.last_seen > SESSION_IDLE_SECONDS or now - session.created > SESSION_MAX_SECONDS:
                self._sessions.pop(token, None)
                return None
            session.last_seen = now
            return session

    def delete(self, token: str | None) -> None:
        with self._lock:
            self._sessions.pop(token or "", None)

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()

    def _purge(self, now: float) -> None:
        for token in [t for t, s in self._sessions.items() if now - s.last_seen > SESSION_IDLE_SECONDS or now - s.created > SESSION_MAX_SECONDS]:
            self._sessions.pop(token, None)


class LoginLimiter:
    """After 5 failures from one address the address is locked out for 2^n seconds (up to 15 minutes). A success resets it."""

    FREE_ATTEMPTS = 5
    MAX_LOCK = 15 * 60

    def __init__(self, clock=time.time) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._state: dict[str, tuple[int, float]] = {}   # ip -> (failures, locked_until)

    def locked_for(self, ip: str) -> int:
        with self._lock:
            _, until = self._state.get(ip, (0, 0.0))
            return max(0, int(until - self._clock() + 0.999))

    def failure(self, ip: str) -> None:
        with self._lock:
            count, _ = self._state.get(ip, (0, 0.0))
            count += 1
            lock = 0 if count < self.FREE_ATTEMPTS else min(self.MAX_LOCK, 2 ** (count - self.FREE_ATTEMPTS + 1))
            self._state[ip] = (count, self._clock() + lock)
            if len(self._state) > 5000:   # never grow without bound
                for key in list(self._state)[:1000]:
                    self._state.pop(key, None)

    def success(self, ip: str) -> None:
        with self._lock:
            self._state.pop(ip, None)


# ---------------------------------------------------------------------------------------------- client address

def parse_networks(values: Iterable[str]) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    networks = []
    for value in values:
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            continue
    return networks


def _in(ip: str, networks) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(address in network for network in networks)


def client_ip(peer: str, forwarded_for: str | None, trusted: Iterable[str]) -> str:
    """The real client address. ``X-Forwarded-For`` is believed only when the direct peer is a trusted proxy; then the right-most entry that is not itself
    a trusted proxy wins (an attacker can add entries on the left, never on the right)."""
    networks = parse_networks(trusted)
    if not _in(peer, networks) or not forwarded_for:
        return peer
    for entry in reversed([part.strip() for part in forwarded_for.split(",") if part.strip()]):
        try:
            ipaddress.ip_address(entry)
        except ValueError:
            return peer
        if not _in(entry, networks):
            return entry
    return peer
