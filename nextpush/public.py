"""The public listener: the **only** port a reverse proxy should point to.

* Every request must carry the access key header (when it is enabled) and use a read/list/delete method; anything else gets **no answer at all** (the connection is
  closed), so a stranger cannot even tell what is listening.
* ``/.well-known/nextpush`` returns a small discovery document (name, WebDAV path, sign-in kind). No user names, no folder names.
* ``/.well-known/nextpush/me`` tells a signed-in user which of their folders are download-only and which also allow deleting after the copy (names and modes only,
  never real paths), so NextPull can explain "Move" before the user tries it. The login is checked by SFTPGo itself and counts toward the same lock-out as every other login.
* ``/dav/...`` is proxied to SFTPGo's WebDAV service on loopback, streaming (never buffering a whole file), with ``Range`` passed through.
"""
from __future__ import annotations

import base64
import http.client
import json
import logging
import socket
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

from . import __version__
from .security import client_ip, constant_eq
from .sftpgo import DAV_PREFIX

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger("nextpush.public")
ALLOWED_METHODS = {"GET", "HEAD", "OPTIONS", "PROPFIND", "DELETE"}
HOP_BY_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade"}
ME_PATH = "/.well-known/nextpush/me"
STRIP_REQUEST = HOP_BY_HOP | {"host", "x-forwarded-for", "x-real-ip", "forwarded", "x-forwarded-host"}
CHUNK = 256 * 1024
MAX_BODY = 1_000_000


def basic_username(header: str | None) -> str | None:
    """The user name inside a Basic Authorization header, or None when there is no (readable) login."""
    if not header or not header.lower().startswith("basic "):
        return None
    try:
        return base64.b64decode(header[6:].strip(), validate=True).decode("utf-8").split(":", 1)[0]
    except (ValueError, UnicodeDecodeError):
        return None


class PublicServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 64

    def handle_error(self, request, client_address) -> None:   # noqa: ANN001
        """A client that resets or times out is normal on the internet: no traceback on the console. Real bugs are still logged."""
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionError, TimeoutError, socket.timeout)):
            return
        log.error("request from %s failed: %r", client_address[0], error, exc_info=True)


def make_handler(app: "App") -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 600

        # -- no Server banner, quiet logging
        def version_string(self) -> str:
            return ""

        def send_response(self, code: int, message: str | None = None) -> None:
            self.send_response_only(code, message)
            self.send_header("Date", self.date_time_string())

        def log_message(self, format: str, *args) -> None:   # noqa: A002 - signature fixed by the base class
            pass

        # -- the gate: runs before any do_* method; returning False means "send nothing"
        def parse_request(self) -> bool:
            if not super().parse_request():
                return False
            key = app.settings.get("access_key")
            if key["enabled"] and not constant_eq(self.headers.get(key["header"], ""), key["value"]):
                app.public_dropped += 1
                self.close_connection = True
                return False
            if self.command not in ALLOWED_METHODS:
                app.public_dropped += 1
                self.close_connection = True
                return False
            return True

        def _note_locked(self, ip: str, wait: int) -> None:
            now = time.monotonic()
            if now - app.last_refusal_log.get("locked:" + ip, -1e9) >= 60:
                app.last_refusal_log["locked:" + ip] = now
                log.warning("address %s is locked out for %s more seconds after too many wrong passwords (Activity -> Blocked addresses to lift it)", ip, wait)

        def _note_refusal(self, status: int) -> None:
            """A refused login used to be invisible (SFTPGo logs at info level). One line per address per minute, so a scan cannot fill the log."""
            if "Authorization" not in self.headers or not (status == 401 or (status == 403 and self.command in ("PROPFIND", "GET", "HEAD", "OPTIONS"))):
                return
            ip, now = self._ip(), time.monotonic()
            if now - app.last_refusal_log.get(ip, -1e9) < 60:
                return
            if len(app.last_refusal_log) > 2000:
                app.last_refusal_log.clear()
            app.last_refusal_log[ip] = now
            log.warning("login refused (HTTP %s) for %s from %s", status, self.command, ip)

        def _ip(self) -> str:
            return client_ip(self.client_address[0], self.headers.get("X-Forwarded-For"), app.settings.get("trusted_proxies"))

        def _json(self, status: int, payload: dict, close: bool = False) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            if close:
                self.send_header("Connection", "close")
                self.close_connection = True
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _read_body(self) -> bytes | None:
            """The request body, always read (bytes left on a keep-alive connection would be parsed as the next request). None = an error was already answered."""
            if self.headers.get("Transfer-Encoding"):
                self._json(411, {"error": "length required"}, close=True)
                return None
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self._json(400, {"error": "bad length"}, close=True)
                return None
            if length < 0 or length > MAX_BODY:
                self._json(413, {"error": "request too large"}, close=True)
                return None
            return self.rfile.read(length) if length else b""

        def _handle(self) -> None:
            body = self._read_body()
            if body is None:
                return
            path = urllib.parse.urlsplit(self.path).path
            if path == "/.well-known/nextpush" and self.command in ("GET", "HEAD"):
                return self._json(200, {"nextpush": 1, "name": app.settings.get("server_name"), "version": __version__, "dav": DAV_PREFIX, "auth": "basic", "me": ME_PATH})
            if path == ME_PATH and self.command == "GET":
                return self._me()
            if path == DAV_PREFIX or path.startswith(DAV_PREFIX + "/"):
                return self._proxy(body)
            self._json(404, {"error": "not found"}, close=True)

        do_GET = do_HEAD = do_OPTIONS = do_PROPFIND = do_DELETE = _handle

        def _locked_reply(self, ip: str, wait: int) -> None:
            self._note_locked(ip, wait)
            self.send_response(429)
            payload = json.dumps({"error": f"Too many wrong passwords from this address. Try again in {wait} seconds."}).encode()
            for name, value in (("Content-Type", "application/json"), ("Content-Length", str(len(payload))), ("Retry-After", str(wait)), ("Connection", "close")):
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(payload)
            self.close_connection = True

        def _me(self) -> None:
            """Who am I and what may I do? SFTPGo checks the password (a PROPFIND on the share root with the caller's own login); only then are the user's folder
            names and modes returned. Wrong passwords are counted and locked out exactly like on the WebDAV path."""
            header = self.headers.get("Authorization")
            username = basic_username(header)
            if username is None:
                return self._json(401, {"error": "sign in with your NextPush user name and password"})
            tracked = username not in app.settings.get("no_block_users")
            ip = self._ip()
            if tracked:
                wait = app.dav_limiter.locked_for(ip)
                if wait:
                    return self._locked_reply(ip, wait)
            upstream = http.client.HTTPConnection("127.0.0.1", app.settings.get("dav_port"), timeout=30)
            try:
                try:
                    upstream.request("PROPFIND", DAV_PREFIX + "/", headers={"Authorization": header, "Depth": "0", "Content-Length": "0", "Host": f"127.0.0.1:{app.settings.get('dav_port')}", "X-Forwarded-For": ip})
                    response = upstream.getresponse()
                    response.read()
                except OSError:
                    return self._json(502, {"error": "the file service is not available"}, close=True)
            finally:
                upstream.close()
            self._note_refusal(response.status)
            if tracked:
                if response.status == 401:
                    app.dav_limiter.failure(ip)
                elif response.status < 400:
                    app.dav_limiter.success(ip)
            if response.status >= 400:
                return self._json(401 if response.status == 401 else 403, {"error": "the login was not accepted"})
            try:
                folders = [{"name": item["name"], "mode": item["mode"]} for item in app.users().get(username)["folders"]]
            except Exception:   # the login is fine but the folder list could not be read: say "unknown" instead of guessing
                log.warning("could not read the folders of %s for /me", username, exc_info=True)
                return self._json(200, {"nextpush": 1, "user": username, "folders": [], "can_delete": None})
            return self._json(200, {"nextpush": 1, "user": username, "folders": folders, "can_delete": any(item["mode"] == "pull" for item in folders)})

        def _proxy(self, body: bytes) -> None:
            body = body or None
            username = basic_username(self.headers.get("Authorization"))
            tracked = username is not None and username not in app.settings.get("no_block_users")   # a request with a login, from a user that is not exempt
            ip = self._ip()
            if tracked:
                wait = app.dav_limiter.locked_for(ip)
                if wait:
                    return self._locked_reply(ip, wait)
            headers = {k: v for k, v in self.headers.items() if k.lower() not in STRIP_REQUEST and k.lower() != app.settings.get("access_key")["header"].lower()}
            headers["Host"] = f"127.0.0.1:{app.settings.get('dav_port')}"
            headers["X-Forwarded-For"] = self._ip()
            upstream = http.client.HTTPConnection("127.0.0.1", app.settings.get("dav_port"), timeout=600)
            try:
                try:
                    upstream.request(self.command, self.path, body=body, headers=headers)
                    response = upstream.getresponse()
                except OSError:
                    return self._json(502, {"error": "the file service is not available"}, close=True)
                self._note_refusal(response.status)
                if tracked:
                    if response.status == 401:
                        app.dav_limiter.failure(ip)
                    elif response.status < 400:
                        app.dav_limiter.success(ip)
                self.send_response(response.status)
                for name, value in response.getheaders():
                    if name.lower() not in HOP_BY_HOP and name.lower() not in ("server", "date", "content-length"):
                        self.send_header(name, value)
                has_body = self.command != "HEAD" and response.status not in (204, 304) and not 100 <= response.status < 200
                upstream_length = response.getheader("Content-Length")
                if upstream_length is not None:
                    self.send_header("Content-Length", upstream_length)
                elif has_body:
                    self.send_header("Connection", "close")
                    self.close_connection = True
                self.end_headers()
                if not has_body:
                    return
                sent = 0
                try:
                    while True:
                        chunk = response.read(CHUNK)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        sent += len(chunk)
                except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
                    self.close_connection = True   # the client went away: stop pulling from SFTPGo
                    return
                if upstream_length is not None and sent != int(upstream_length):
                    # SFTPGo ended early (user disabled, restart...): the client was promised more bytes. Cut the connection so it sees a failed download
                    # at once instead of waiting for the rest until a timeout.
                    self.close_connection = True
                    try:
                        self.connection.shutdown(socket.SHUT_RDWR)
                    except OSError:
                        pass
            finally:
                upstream.close()

    return Handler
