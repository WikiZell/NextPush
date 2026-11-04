"""Shared test helpers: temp folders, finding the real SFTPGo program, a small HTTP client for the admin API, and a fake WebDAV upstream."""
from __future__ import annotations

import base64
import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from nextpush.sftpgo import find_binary  # noqa: E402


def sftpgo_binary() -> Path | None:
    """The real SFTPGo the integration tests use: NEXTPUSH_SFTPGO, ./bin, ./tools/sftpgo, or the PATH."""
    return find_binary(None, ROOT) or find_binary(ROOT / "tools" / "sftpgo" / ("sftpgo.exe" if os.name == "nt" else "sftpgo"), None)


class TempCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="nextpush-test-"))
        self.addCleanup(lambda: shutil.rmtree(self.tmp, ignore_errors=True))


class Client:
    """A browser stand-in for the admin API: keeps the session cookie and the CSRF token."""

    def __init__(self, port: int, host: str = "127.0.0.1") -> None:
        self.host, self.port = host, port
        self.cookie = ""
        self.csrf = ""

    def request(self, method: str, path: str, body=None, headers=None, raw: bytes | None = None, csrf: bool = True):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=30)
        h = {"Content-Type": "application/json", **(headers or {})}
        if self.cookie:
            h["Cookie"] = self.cookie
        if csrf and self.csrf:
            h.setdefault("X-CSRF-Token", self.csrf)
        payload = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        conn.request(method, path, body=payload, headers=h)
        response = conn.getresponse()
        data = response.read()
        set_cookie = response.getheader("Set-Cookie")
        if set_cookie and set_cookie.startswith("nextpush_session="):
            value = set_cookie.split(";", 1)[0]
            self.cookie = "" if value.endswith("=") else value
        conn.close()
        try:
            parsed = json.loads(data) if data else {}
        except ValueError:
            parsed = {"_raw": data}
        return response.status, parsed, response

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body if body is not None else {}, **kw)

    def put(self, path, body=None, **kw):
        return self.request("PUT", path, body if body is not None else {}, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)


def dav_request(port: int, method: str, path: str, user: str = "", password: str = "", key: tuple[str, str] | None = None, headers=None, body: bytes | None = None,
                host: str = "127.0.0.1"):
    """One raw WebDAV request to the public listener. Returns (status, headers, body) or raises the socket error when the server drops the connection."""
    conn = http.client.HTTPConnection(host, port, timeout=30)
    h = {"Depth": "1", **(headers or {})}
    if user:
        h["Authorization"] = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
    if key:
        h[key[0]] = key[1]
    try:
        conn.request(method, path, body=body, headers=h)
        response = conn.getresponse()
        return response.status, dict(response.getheaders()), response.read()
    finally:
        conn.close()


class FakeUpstream:
    """A stand-in for SFTPGo's WebDAV service: records requests and answers from a table ``self.routes[(method, path)] -> (status, headers, body | iterator)``."""

    def __init__(self) -> None:
        self.requests: list[dict] = []
        self.routes: dict[tuple[str, str], tuple[int, dict, object]] = {}
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args) -> None:
                pass

            def _any(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                outer.requests.append({"method": self.command, "path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body})
                status, headers, payload = outer.routes.get((self.command, self.path.split("?")[0]), (404, {}, b"not found"))
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                if hasattr(payload, "__next__") or hasattr(payload, "read"):   # streamed body without a length: chunked
                    self.send_header("Transfer-Encoding", "chunked")
                    self.end_headers()
                    for part in payload:   # type: ignore[union-attr]
                        self.wfile.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
                    self.wfile.write(b"0\r\n\r\n")
                    return
                if "Content-Length" not in headers:
                    self.send_header("Content-Length", str(len(payload)))   # type: ignore[arg-type]
                self.end_headers()
                if self.command != "HEAD":
                    self.wfile.write(payload)   # type: ignore[arg-type]
                if headers.get("X-Test-Truncate"):   # promised more than was sent, then the connection dies
                    self.close_connection = True

            do_GET = do_HEAD = do_PROPFIND = do_DELETE = do_OPTIONS = _any

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.handle_error = lambda *args: None   # a peer that hangs up mid-stream is expected here
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
