"""Ties everything together: settings, SFTPGo, the two listeners, the user service and logging."""
from __future__ import annotations

import collections
import logging
import logging.handlers
import os
import secrets
import threading
from pathlib import Path
from typing import Any

from . import admin, public, sftpgo
from .config import Settings, write_private
from .security import LoginLimiter, SessionStore
from .users import UserService

log = logging.getLogger("nextpush")


class _Redact(logging.Filter):
    """Nothing registered as secret (the access key, the private SFTPGo admin password) is ever written to a log."""

    def __init__(self) -> None:
        super().__init__()
        self.secrets: set[str] = set()

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        for secret in self.secrets:
            if secret and secret in message:
                message = message.replace(secret, "<hidden>")
        record.msg, record.args = message, None
        return True


class _Ring(logging.Handler):
    def __init__(self, ring: collections.deque[str]) -> None:
        super().__init__()
        self.ring = ring

    def emit(self, record: logging.LogRecord) -> None:
        self.ring.append(f"{self.format(record)}")


class App:
    def __init__(self, data_dir: Path, *, binary: Path | None = None, admin_port: int | None = None, public_port: int | None = None, admin_host: str | None = None,
                 public_host: str | None = None, quiet: bool = False) -> None:
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.settings = Settings(self.data_dir)
        overrides = {key: value for key, value in (("admin_port", admin_port), ("public_port", public_port), ("admin_host", admin_host), ("public_host", public_host)) if value is not None}
        if overrides:
            self.settings.update(**overrides)
        self.settings.ensure_ports_and_key()
        self.sessions = SessionStore()
        self.limiter = LoginLimiter()
        self.supervisor = sftpgo.Supervisor(self.settings, self.data_dir, binary)
        self.public_dropped = 0
        self.last_refusal_log: dict[str, float] = {}
        self.dav_limiter = LoginLimiter(free_attempts=8, max_lock=600)   # wrong passwords on the public port: 8 free tries, then 2 s, 4 s, ... at most 10 minutes
        self.sftpgo_error = ""
        self.log_lines: collections.deque[str] = collections.deque(maxlen=500)
        self._redact = _Redact()
        self._setup_logging(quiet)
        self._api: sftpgo.Api | None = None
        self._users: UserService | None = None
        self._servers: list[Any] = []
        self._threads: list[threading.Thread] = []
        self.admin_address: tuple[str, int] = ("", 0)
        self.public_address: tuple[str, int] = ("", 0)

    # ------------------------------------------------------------------------------------------ logging
    def _setup_logging(self, quiet: bool) -> None:
        logger = logging.getLogger("nextpush")
        for handler in list(logger.handlers):
            if getattr(handler, "_nextpush", False):
                logger.removeHandler(handler)
                handler.close()
        logger.setLevel(logging.INFO)
        logger.propagate = False
        fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
        handlers: list[logging.Handler] = [logging.handlers.RotatingFileHandler(self.data_dir / "nextpush.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"), _Ring(self.log_lines)]
        if not quiet:
            handlers.append(logging.StreamHandler())
        for handler in handlers:
            handler.setFormatter(fmt)
            handler.addFilter(self._redact)
            handler._nextpush = True   # type: ignore[attr-defined]
            logger.addHandler(handler)
        self._redact.secrets.add(self.settings.get("access_key")["value"])

    # ------------------------------------------------------------------------------------------ setup code
    @property
    def _token_path(self) -> Path:
        return self.data_dir / "setup-code.txt"

    def setup_token(self) -> str:
        """The one-time code that allows the first administrator to be created. Empty once setup is done."""
        if self.settings.configured:
            return ""
        if not self._token_path.is_file():
            write_private(self._token_path, secrets.token_urlsafe(9))
        return self._token_path.read_text(encoding="utf-8").strip()

    def consume_setup_token(self) -> None:
        try:
            self._token_path.unlink()
        except OSError:
            pass

    # ------------------------------------------------------------------------------------------ pieces
    def api(self) -> sftpgo.Api:
        if self._api is None:
            user, password = self.supervisor.admin_credentials()
            self._redact.secrets.add(password)
            self._api = sftpgo.Api(self.supervisor.api_url, user, password)
        if not self.supervisor.running() and self.sftpgo_error:
            raise sftpgo.SftpgoError(self.sftpgo_error)
        return self._api

    def users(self) -> UserService:
        if self._users is None:
            self._users = UserService(self.api(), self.data_dir, self.settings)
        return self._users

    def sftpgo_status(self) -> dict[str, Any]:
        return {"running": self.supervisor.running(), "healthy": self.supervisor.running() and self.supervisor.healthy(), "restarts": self.supervisor.restarts, "error": self.sftpgo_error}

    # ------------------------------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        """Start SFTPGo (a missing or broken SFTPGo does not stop the GUI: it explains the problem) and both listeners."""
        try:
            self.supervisor.start()
            self.sftpgo_error = ""
        except sftpgo.SftpgoError as error:
            self.sftpgo_error = str(error)
            log.error("%s", error)
        for name, module, host_key, port_key in (("admin", admin, "admin_host", "admin_port"), ("public", public, "public_host", "public_port")):
            handler = module.make_handler(self)
            server_cls = admin.AdminServer if module is admin else public.PublicServer
            server = server_cls((self.settings.get(host_key), self.settings.get(port_key)), handler)
            thread = threading.Thread(target=server.serve_forever, name=f"nextpush-{name}", daemon=True)
            thread.start()
            self._servers.append(server)
            self._threads.append(thread)
            if module is admin:
                self.admin_address = server.server_address[:2]
            else:
                self.public_address = server.server_address[:2]
        log.info("NextPush ready: admin on port %s, public on port %s", self.admin_address[1], self.public_address[1])

    def stop(self) -> None:
        for server in self._servers:
            server.shutdown()
            server.server_close()
        self._servers.clear()
        self.supervisor.stop()
        logger = logging.getLogger("nextpush")
        for handler in list(logger.handlers):
            if getattr(handler, "_nextpush", False):
                logger.removeHandler(handler)
                handler.close()

    def wait(self) -> None:
        try:
            while any(thread.is_alive() for thread in self._threads):
                for thread in self._threads:
                    thread.join(0.5)
        except KeyboardInterrupt:
            pass
