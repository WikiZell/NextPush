"""Runs the SFTPGo program as a child process and talks to its REST API.

SFTPGo is **unmodified and separate** (see THIRD-PARTY-NOTICES.md): NextPush writes its configuration, starts it, restarts it if it dies, and drives it
through the documented REST API. Only its WebDAV service and its REST API are enabled, both on the loopback address; its own web admin and web client are off.
"""
from __future__ import annotations

import collections
import http.client
import json
import logging
import os
import secrets
import shutil
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Any

from .config import Settings, write_private

log = logging.getLogger("nextpush.sftpgo")
CREATE_NO_WINDOW = 0x08000000
DAV_PREFIX = "/dav"


class SftpgoError(RuntimeError):
    """SFTPGo is missing, did not start, or answered with an error. The message is fit for the administrator."""


class SftpgoApiError(SftpgoError):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(f"SFTPGo answered {status}: {message}")
        self.status = status


def find_binary(explicit: str | os.PathLike[str] | None = None, app_dir: Path | None = None) -> Path | None:
    """--sftpgo / NEXTPUSH_SFTPGO, then ``bin/`` next to the program, then the PATH."""
    name = "sftpgo.exe" if os.name == "nt" else "sftpgo"
    candidates: list[Path] = []
    for value in (explicit, os.environ.get("NEXTPUSH_SFTPGO")):
        if value:
            candidates.append(Path(value))
    if app_dir:
        candidates += [app_dir / "bin" / name, app_dir / name]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    found = shutil.which("sftpgo")
    return Path(found) if found else None


def find_resources(binary: Path | None) -> Path | None:
    """SFTPGo reads its e-mail templates at start-up even when nothing uses them, so the folder that ships with it must be found: next to the program (our bundle),
    or where the Linux packages put it."""
    candidates = ([binary.parent / "templates"] if binary else []) + [Path("/usr/share/sftpgo/templates"), Path("/usr/local/share/sftpgo/templates")]
    return next((c for c in candidates if c.is_dir()), None)


def build_config(dav_port: int, api_port: int, templates: Path | None = None) -> dict[str, Any]:
    """The whole SFTPGo configuration NextPush needs: WebDAV under /dav and the REST API on loopback, everything else off. NextPush does the login protection itself."""
    loopback = ["127.0.0.1", "::1"]
    return {
        "common": {"idle_timeout": 15, "defender": {"enabled": False}},   # NextPush protects the logins itself (see public.py); SFTPGo's own defender escalates bans to days
        "sftpd": {"bindings": []},
        "ftpd": {"bindings": []},
        "webdavd": {"bindings": [{"port": dav_port, "address": "127.0.0.1", "enable_https": False, "prefix": DAV_PREFIX, "proxy_allowed": loopback,
                                  "client_ip_proxy_header": "X-Forwarded-For", "client_ip_header_depth": 0}]},
        "httpd": {"bindings": [{"port": api_port, "address": "127.0.0.1", "enable_web_admin": False, "enable_web_client": False, "enable_rest_api": True, "render_openapi": False}]},
        "data_provider": {"driver": "sqlite", "name": "sftpgo.db", "create_default_admin": True},
        "telemetry": {"bind_port": 0},
        **({"smtp": {"templates_path": str(templates)}} if templates else {}),
    }


class Supervisor:
    """Start / watch / restart SFTPGo. ``start()`` returns only when its REST API is healthy."""

    def __init__(self, settings: Settings, data_dir: Path, binary: Path | None) -> None:
        self.settings, self.data_dir, self.binary = settings, data_dir, binary
        self.dir = data_dir / "sftpgo"
        self.process: subprocess.Popen[bytes] | None = None
        self.lines: collections.deque[str] = collections.deque(maxlen=500)
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._watcher: threading.Thread | None = None
        self.restarts = 0
        self.version = ""

    # -- credentials of the SFTPGo admin account that only NextPush uses
    def admin_credentials(self) -> tuple[str, str]:
        path = self.dir / "admin.json"
        if path.is_file():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data["username"], data["password"]
        creds = {"username": "nextpush-" + secrets.token_hex(3), "password": secrets.token_urlsafe(32)}
        write_private(path, json.dumps(creds))
        return creds["username"], creds["password"]

    @property
    def api_url(self) -> str:
        return f"http://127.0.0.1:{self.settings.get('api_port')}"

    @property
    def dav_url(self) -> str:
        return f"http://127.0.0.1:{self.settings.get('dav_port')}"

    def write_config(self) -> Path:
        self.dir.mkdir(parents=True, exist_ok=True)
        path = self.dir / "sftpgo.json"
        path.write_text(json.dumps(build_config(self.settings.get("dav_port"), self.settings.get("api_port"), find_resources(self.binary)), indent=2), encoding="utf-8")
        (self.data_dir / "empty-root").mkdir(parents=True, exist_ok=True)
        return path

    # -- process
    def start(self, wait: float = 40.0) -> None:
        with self._lock:
            if self.binary is None:
                raise SftpgoError("The SFTPGo program was not found. Put it in the 'bin' folder next to NextPush, or set NEXTPUSH_SFTPGO to its path (see the deployment guide).")
            self._stop.clear()
            self._spawn()
            self._wait_healthy(wait)
            if self._watcher is None or not self._watcher.is_alive():
                self._watcher = threading.Thread(target=self._watch, name="sftpgo-watch", daemon=True)
                self._watcher.start()

    def _spawn(self) -> None:
        self.write_config()
        user, password = self.admin_credentials()
        env = {**os.environ, "SFTPGO_DEFAULT_ADMIN_USERNAME": user, "SFTPGO_DEFAULT_ADMIN_PASSWORD": password, "SFTPGO_LOG_FILE_PATH": "", "SFTPGO_LOG_LEVEL": "info"}
        flags = CREATE_NO_WINDOW if os.name == "nt" else 0
        self.process = subprocess.Popen([str(self.binary), "serve", "-c", str(self.dir)], cwd=str(self.dir), env=env, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=flags)
        threading.Thread(target=self._pump, args=(self.process,), name="sftpgo-log", daemon=True).start()
        log.info("SFTPGo started (pid %s)", self.process.pid)

    def _pump(self, process: subprocess.Popen[bytes]) -> None:
        """Keep the last lines for the GUI and a size-capped log file; never let the pipe fill up (SFTPGo would block)."""
        path = self.dir / "sftpgo.log"
        try:
            if path.is_file() and path.stat().st_size > 2_000_000:
                path.replace(path.with_suffix(".log.1"))
            handle = open(path, "a", encoding="utf-8", errors="replace")
        except OSError:
            handle = None
        try:
            assert process.stdout is not None
            for raw in iter(process.stdout.readline, b""):
                line = raw.decode("utf-8", errors="replace").rstrip()
                self.lines.append(line)
                if handle:
                    handle.write(line + "\n")
                    handle.flush()
        finally:
            if handle:
                handle.close()
            try:
                process.stdout.close()   # type: ignore[union-attr]
            except (OSError, AttributeError):
                pass

    def _wait_healthy(self, wait: float) -> None:
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                tail = " | ".join(list(self.lines)[-3:])
                raise SftpgoError(f"SFTPGo stopped right after starting (exit code {self.process.returncode}). {tail[:300]}")
            if self.healthy():
                return
            time.sleep(0.25)
        raise SftpgoError("SFTPGo did not become ready in time")

    def healthy(self) -> bool:
        try:
            conn = http.client.HTTPConnection("127.0.0.1", self.settings.get("api_port"), timeout=2)
            conn.request("GET", "/healthz")
            ok = conn.getresponse().status == 200
            conn.close()
            return ok
        except OSError:
            return False

    def running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _watch(self) -> None:
        recent: collections.deque[float] = collections.deque(maxlen=5)
        while not self._stop.wait(3.0):
            if self.running():
                continue
            now = time.monotonic()
            recent.append(now)
            if len(recent) == 5 and now - recent[0] < 60:
                log.error("SFTPGo keeps crashing; waiting a minute before trying again")
                if self._stop.wait(60):
                    return
            with self._lock:
                if self._stop.is_set():
                    return
                try:
                    log.warning("SFTPGo is not running: restarting it")
                    self._spawn()
                    self.restarts += 1
                    self._wait_healthy(30)
                except SftpgoError as error:
                    log.error("restart failed: %s", error)

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            process, self.process = self.process, None
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(5)
        if process and process.stdout:
            try:
                process.stdout.close()
            except OSError:
                pass
        if self._watcher and self._watcher.is_alive() and self._watcher is not threading.current_thread():
            self._watcher.join(timeout=5)

    def restart(self) -> None:
        with self._lock:
            process = self.process
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(10)
                except subprocess.TimeoutExpired:
                    process.kill()
            self._spawn()
            self._wait_healthy(30)


class Api:
    """The few SFTPGo REST calls NextPush needs. Bearer token from the private admin account, renewed when it expires."""

    def __init__(self, base_url: str, username: str, password: str, timeout: float = 20.0) -> None:
        parts = urllib.parse.urlsplit(base_url)
        self.host, self.port, self.timeout = parts.hostname or "127.0.0.1", parts.port or 80, timeout
        self._creds = (username, password)
        self._token = ""
        self._lock = threading.Lock()

    def _login(self) -> None:
        import base64
        conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
        auth = base64.b64encode(f"{self._creds[0]}:{self._creds[1]}".encode()).decode()
        conn.request("GET", "/api/v2/token", headers={"Authorization": "Basic " + auth})
        response = conn.getresponse()
        body = response.read()
        conn.close()
        if response.status != 200:
            raise SftpgoApiError(response.status, "the private SFTPGo admin login was refused (was the data folder copied from another install?)")
        self._token = json.loads(body)["access_token"]

    def request(self, method: str, path: str, body: Any = None, query: dict[str, str] | None = None) -> Any:
        for attempt in (1, 2):
            with self._lock:
                if not self._token:
                    self._login()
                token = self._token
            url = "/api/v2" + path + (("?" + urllib.parse.urlencode(query)) if query else "")
            conn = http.client.HTTPConnection(self.host, self.port, timeout=self.timeout)
            try:
                conn.request(method, url, body=json.dumps(body).encode() if body is not None else None, headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"})
                response = conn.getresponse()
                raw = response.read()
            except OSError as error:
                raise SftpgoError(f"Cannot reach SFTPGo ({error})") from error
            finally:
                conn.close()
            if response.status == 401 and attempt == 1:
                with self._lock:
                    self._token = ""
                continue
            data = json.loads(raw) if raw else {}
            if response.status >= 400:
                raise SftpgoApiError(response.status, str(data.get("message") or data.get("error") or raw[:120]) if isinstance(data, dict) else "error")
            return data
        raise SftpgoApiError(401, "unauthorized")

    # users
    def list_users(self) -> list[dict[str, Any]]:
        users: list[dict[str, Any]] = []
        offset = 0
        while True:
            page = self.request("GET", "/users", query={"limit": "100", "offset": str(offset)})
            users += page
            if len(page) < 100:
                return users
            offset += 100

    def get_user(self, name: str) -> dict[str, Any]:
        return self.request("GET", "/users/" + urllib.parse.quote(name, safe=""))

    def create_user(self, body: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/users", body)

    def update_user(self, name: str, body: dict[str, Any], disconnect: bool = False) -> Any:
        return self.request("PUT", "/users/" + urllib.parse.quote(name, safe=""), body, {"disconnect": "1" if disconnect else "0"})

    def delete_user(self, name: str) -> Any:
        return self.request("DELETE", "/users/" + urllib.parse.quote(name, safe=""))

    # folders
    def get_folder(self, name: str) -> dict[str, Any]:
        return self.request("GET", "/folders/" + urllib.parse.quote(name, safe=""))

    def create_folder(self, name: str, mapped_path: str, description: str = "") -> Any:
        return self.request("POST", "/folders", {"name": name, "mapped_path": mapped_path, "description": description})

    def delete_folder(self, name: str) -> Any:
        return self.request("DELETE", "/folders/" + urllib.parse.quote(name, safe=""))

    # status
    def connections(self) -> list[dict[str, Any]]:
        return self.request("GET", "/connections") or []

    def version(self) -> dict[str, Any]:
        return self.request("GET", "/version")
