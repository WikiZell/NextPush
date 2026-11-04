"""Where NextPush keeps its data and its settings (one JSON file, written atomically, readable only by the service user)."""
from __future__ import annotations

import json
import os
import secrets
import socket
import threading
from pathlib import Path
from typing import Any

ACCESS_KEY_HEADER = "X-Pull-Key"


def default_data_dir() -> Path:
    """``NEXTPUSH_DATA_DIR``, else a per-platform default that exists or can be created by the current user."""
    override = os.environ.get("NEXTPUSH_DATA_DIR")
    if override:
        return Path(override)
    candidates = ([Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "NextPush", Path(os.environ.get("LOCALAPPDATA", Path.home())) / "NextPush"] if os.name == "nt"
                  else [Path("/var/lib/nextpush"), Path.home() / ".local" / "share" / "nextpush"])
    for candidate in candidates:
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.write_text("x", encoding="utf-8")
            probe.unlink()
            return candidate
        except OSError:
            continue
    return candidates[-1]


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket() as sock:
        sock.bind((host, 0))
        return sock.getsockname()[1]


def write_private(path: Path, text: str) -> None:
    """Atomic write; on POSIX the file is created 0600 (it may hold a key or a password)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(4)}.tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(temporary, path)


DEFAULTS: dict[str, Any] = {
    "version": 1,
    "server_name": "",
    "public_url": "",                     # what clients use, e.g. https://dl.example.com (the reverse proxy's address); the WebDAV address is public_url + "/dav"
    "admin_hash": "",                     # PBKDF2 hash of the admin password; empty until the first-run setup is done
    "admin_host": "0.0.0.0",
    "admin_port": 8095,                   # the web GUI. Never publish this port through the reverse proxy
    "public_host": "0.0.0.0",
    "public_port": 8096,                  # discovery + WebDAV. This is the only port a reverse proxy should point to
    "access_key": {"enabled": True, "header": ACCESS_KEY_HEADER, "value": ""},
    "trusted_proxies": ["127.0.0.0/8", "::1/128", "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"],
    "dav_port": 0,                        # SFTPGo's WebDAV and API ports (loopback only), chosen on first start and then kept
    "api_port": 0,
}


class Settings:
    """Thread-safe settings with atomic saves. ``Settings(None)`` is memory-only (tests)."""

    def __init__(self, data_dir: Path | None) -> None:
        self.data_dir = data_dir
        self._lock = threading.RLock()
        self._data: dict[str, Any] = json.loads(json.dumps(DEFAULTS))
        if data_dir and (data_dir / "nextpush.json").is_file():
            try:
                loaded = json.loads((data_dir / "nextpush.json").read_text(encoding="utf-8"))
            except (OSError, ValueError):
                loaded = {}
            if isinstance(loaded, dict):
                self._merge(loaded)

    def _merge(self, loaded: dict[str, Any]) -> None:
        for key, value in loaded.items():
            if key in DEFAULTS:
                if isinstance(DEFAULTS[key], dict) and isinstance(value, dict):
                    self._data[key] = {**DEFAULTS[key], **value}
                else:
                    self._data[key] = value

    def get(self, key: str) -> Any:
        with self._lock:
            return json.loads(json.dumps(self._data[key]))

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return json.loads(json.dumps(self._data))

    def update(self, **changes: Any) -> None:
        with self._lock:
            for key, value in changes.items():
                if key not in DEFAULTS:
                    raise KeyError(key)
                self._data[key] = value
            self._save()

    def _save(self) -> None:
        if self.data_dir:
            write_private(self.data_dir / "nextpush.json", json.dumps(self._data, indent=2))

    @property
    def configured(self) -> bool:
        return bool(self.get("admin_hash"))

    def ensure_ports_and_key(self) -> None:
        """First start: pick free loopback ports for SFTPGo and make the access key. Later starts keep them."""
        with self._lock:
            changed = False
            for key in ("dav_port", "api_port"):
                if not self._data[key]:
                    self._data[key] = free_port()
                    changed = True
            if not self._data["access_key"].get("value"):
                self._data["access_key"] = {**self._data["access_key"], "value": secrets.token_urlsafe(24)}
                changed = True
            if changed:
                self._save()
