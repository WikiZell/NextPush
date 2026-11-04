"""The admin listener: the web GUI and its JSON API. Never publish this port through the reverse proxy.

Security model: a first-run **setup code** (printed on the console, kept in a file readable only by the service user) lets the first person choose the admin password;
after that every call needs the session cookie (HttpOnly, SameSite=Strict), every change needs the CSRF header, a wrong ``Origin`` is refused, and wrong passwords
lock the address out for longer and longer.
"""
from __future__ import annotations

import json
import logging
import os
import re
import socket
import sys
import traceback
import urllib.parse
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from . import __version__, fsbrowse
from .config import ACCESS_KEY_HEADER
from .security import check_admin_password, client_ip, constant_eq, hash_password, verify_password
from .sftpgo import SftpgoApiError, SftpgoError
from .users import UserError

if TYPE_CHECKING:
    from .app import App

log = logging.getLogger("nextpush.admin")
COOKIE = "nextpush_session"
WEB_DIR = Path(__file__).resolve().parent / "web"
STATIC = {"/": ("index.html", "text/html; charset=utf-8"), "/index.html": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
          "/style.css": ("style.css", "text/css; charset=utf-8")}
MAX_JSON = 64 * 1024
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer", "Cache-Control": "no-store",
    "Content-Security-Policy": "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
}


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


class AdminServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32

    def handle_error(self, request, client_address) -> None:   # noqa: ANN001
        """A client that resets or times out is normal on the internet: no traceback on the console. Real bugs are still logged."""
        error = sys.exc_info()[1]
        if isinstance(error, (ConnectionError, TimeoutError, socket.timeout)):
            return
        log.error("request from %s failed: %r", client_address[0], error, exc_info=True)


def valid_public_url(raw: str) -> str:
    """https://host[:port] (http only for local-network hosts), no path, no credentials. Empty means "not set"."""
    text = str(raw or "").strip().rstrip("/")
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    parts = urllib.parse.urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.path not in ("", "/") or parts.query or parts.fragment:
        raise ApiError(400, "The public address must look like https://dl.example.com (no path)")
    if parts.scheme == "http":
        import ipaddress
        host = parts.hostname
        try:
            private = ipaddress.ip_address(host).is_private or ipaddress.ip_address(host).is_loopback
        except ValueError:
            private = host == "localhost" or host.endswith((".local", ".lan")) or "." not in host
        if not private:
            raise ApiError(400, "Use https:// for a public address: passwords would travel unencrypted otherwise")
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def make_handler(app: "App") -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"
        timeout = 60

        def version_string(self) -> str:
            return "NextPush"

        def log_message(self, format: str, *args) -> None:   # noqa: A002
            pass

        # ------------------------------------------------------------------------------------ plumbing
        def _ip(self) -> str:
            return client_ip(self.client_address[0], self.headers.get("X-Forwarded-For"), app.settings.get("trusted_proxies"))

        def _secure_cookie(self) -> bool:
            return (self.headers.get("X-Forwarded-Proto") or "").lower() == "https"

        def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for name, value in {**SECURITY_HEADERS, **(extra or {})}.items():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, payload: Any, extra: dict[str, str] | None = None) -> None:
            self._send(status, json.dumps(payload).encode("utf-8"), "application/json", extra)

        def _read_raw(self) -> bytes:
            """Read the request body **always**, whether or not the handler wants it: bytes left on a keep-alive connection would be parsed as the next request."""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self.close_connection = True
                raise ApiError(400, "Bad request length") from None
            if length < 0 or length > MAX_JSON:
                self.close_connection = True
                raise ApiError(413, "Request too large")
            return self.rfile.read(length) if length else b""

        def _body(self) -> dict[str, Any]:
            raw = self._raw
            if not raw:
                return {}
            try:
                data = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise ApiError(400, "The request is not valid JSON") from None
            if not isinstance(data, dict):
                raise ApiError(400, "The request must be a JSON object")
            return data

        def _session(self):
            cookies = SimpleCookie(self.headers.get("Cookie") or "")
            morsel = cookies.get(COOKIE)
            return app.sessions.get(morsel.value if morsel else None)

        def _origin_ok(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True
            return urllib.parse.urlsplit(origin).netloc.lower() == (self.headers.get("Host") or "").lower()

        def _require_admin(self, mutating: bool):
            session = self._session()
            if not session:
                raise ApiError(401, "Sign in first")
            if mutating:
                if not self._origin_ok():
                    raise ApiError(403, "Request refused (wrong origin)")
                if not constant_eq(self.headers.get("X-CSRF-Token", ""), session.csrf):
                    raise ApiError(403, "Request refused (missing or wrong CSRF token)")
            return session

        def _set_cookie(self, token: str, max_age: int) -> dict[str, str]:
            flags = "; HttpOnly; SameSite=Strict; Path=/" + ("; Secure" if self._secure_cookie() else "")
            return {"Set-Cookie": f"{COOKIE}={token}; Max-Age={max_age}{flags}"}

        # ------------------------------------------------------------------------------------ dispatch
        def _dispatch(self) -> None:
            parsed = urllib.parse.urlsplit(self.path)
            path = parsed.path
            self._raw = b""
            try:
                self._raw = self._read_raw()
                if self.command in ("GET", "HEAD") and path in STATIC:
                    name, ctype = STATIC[path]
                    return self._send(200, (WEB_DIR / name).read_bytes(), ctype)
                if not path.startswith("/api/"):
                    return self._send(404, b'{"error":"not found"}', "application/json")
                route = (ROUTES[(self.command, path)], []) if (self.command, path) in ROUTES else self._pattern_route(path)
                if route is None:
                    raise ApiError(404, "Unknown API call")
                handler, args = route
                self._query = urllib.parse.parse_qs(parsed.query)
                result = handler(self, *args)
                if isinstance(result, tuple):
                    status, payload, extra = result
                    return self._json(status, payload, extra)
                self._json(200, result)
            except ApiError as error:
                self._json(error.status, {"error": error.message})
            except (UserError, fsbrowse.ShareError) as error:
                self._json(400, {"error": str(error)})
            except SftpgoApiError as error:
                self._json(502 if error.status >= 500 else 400, {"error": f"The file service refused the change: {error}"})
            except SftpgoError as error:
                self._json(503, {"error": str(error)})
            except Exception:   # noqa: BLE001 - never leak internals to the browser
                log.error("admin API crashed:\n%s", traceback.format_exc())
                self._json(500, {"error": "Something went wrong. See the NextPush log."})

        def _pattern_route(self, path: str):
            for method, pattern, handler in PATTERNS:
                if method == self.command:
                    match = pattern.match(path)
                    if match:
                        return handler, [urllib.parse.unquote(group) for group in match.groups()]
            return None

        do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = _dispatch

        # ------------------------------------------------------------------------------------ API
        def api_state(self):
            session = self._session()
            out: dict[str, Any] = {"version": __version__, "setup_required": not app.settings.configured, "authenticated": bool(session)}
            if session:
                s = app.settings
                out.update({"csrf": session.csrf, "server_name": s.get("server_name"), "public_url": s.get("public_url"), "access_key_enabled": s.get("access_key")["enabled"],
                            "sftpgo": app.sftpgo_status(), "public_port": s.get("public_port"), "admin_port": s.get("admin_port")})
            return out

        def api_setup(self):
            if app.settings.configured:
                raise ApiError(409, "NextPush is already set up")
            ip = self._ip()
            wait = app.limiter.locked_for(ip)
            if wait:
                raise ApiError(429, f"Too many attempts. Try again in {wait} seconds")
            data = self._body()
            token = app.setup_token()
            if not token or not constant_eq(str(data.get("token") or ""), token):
                app.limiter.failure(ip)
                raise ApiError(403, "The setup code is wrong. It is shown on the console where NextPush was started and in setup-code.txt in its data folder")
            problem = check_admin_password(str(data.get("password") or ""))
            if problem:
                raise ApiError(400, problem)
            public_url = valid_public_url(str(data.get("public_url") or ""))
            app.settings.update(admin_hash=hash_password(str(data["password"])), server_name=str(data.get("server_name") or "").strip()[:60], public_url=public_url)
            app.consume_setup_token()
            app.limiter.success(ip)
            session = app.sessions.create(ip)
            log.info("setup completed from %s", ip)
            return 200, {"ok": True, "csrf": session.csrf}, self._set_cookie(session.token, 12 * 3600)

        def api_login(self):
            ip = self._ip()
            wait = app.limiter.locked_for(ip)
            if wait:
                raise ApiError(429, f"Too many attempts. Try again in {wait} seconds")
            if not self._origin_ok():
                raise ApiError(403, "Request refused (wrong origin)")
            data = self._body()
            stored = app.settings.get("admin_hash")
            if not stored:
                raise ApiError(409, "NextPush is not set up yet")
            if not verify_password(str(data.get("password") or ""), stored):
                app.limiter.failure(ip)
                log.warning("failed sign-in from %s", ip)
                raise ApiError(401, "Wrong password")
            app.limiter.success(ip)
            session = app.sessions.create(ip)
            return 200, {"ok": True, "csrf": session.csrf}, self._set_cookie(session.token, 12 * 3600)

        def api_logout(self):
            session = self._require_admin(True)
            app.sessions.delete(session.token)
            return 200, {"ok": True}, self._set_cookie("", 0)

        def api_change_password(self):
            session = self._require_admin(True)
            data = self._body()
            if not verify_password(str(data.get("current") or ""), app.settings.get("admin_hash")):
                raise ApiError(403, "The current password is wrong")
            problem = check_admin_password(str(data.get("new") or ""))
            if problem:
                raise ApiError(400, problem)
            app.settings.update(admin_hash=hash_password(str(data["new"])))
            app.sessions.clear()
            new = app.sessions.create(self._ip())
            return 200, {"ok": True, "csrf": new.csrf}, self._set_cookie(new.token, 12 * 3600)

        def _flag(self, user: dict) -> dict:
            return {**user, "no_block": user["username"] in app.settings.get("no_block_users")}

        def _set_no_block(self, name: str, flag: bool) -> None:
            current = [u for u in app.settings.get("no_block_users") if u != name]
            app.settings.update(no_block_users=sorted(current + [name]) if flag else current)

        def api_users(self):
            self._require_admin(False)
            return {"users": [self._flag(u) for u in app.users().list()]}

        def api_user_create(self):
            self._require_admin(True)
            spec = self._body()
            user, password = app.users().create(spec)
            if spec.get("no_block"):
                self._set_no_block(user["username"], True)
            log.info("user created: %s", user["username"])
            return 201, {"user": self._flag(user), "password": password}, {}

        def api_user_get(self, name):
            self._require_admin(False)
            return {"user": self._flag(app.users().get(name))}

        def api_user_update(self, name):
            self._require_admin(True)
            spec = self._body()
            user = app.users().update(name, spec)
            if "no_block" in spec:
                self._set_no_block(name, bool(spec["no_block"]))
            log.info("user updated: %s", name)
            return {"user": self._flag(user)}

        def api_user_delete(self, name):
            self._require_admin(True)
            app.users().delete(name)
            self._set_no_block(name, False)
            log.info("user deleted: %s", name)
            return {"ok": True}

        def api_user_password(self, name):
            self._require_admin(True)
            password = app.users().reset_password(name)
            log.info("password reset: %s", name)
            return {"password": password}

        def api_user_invite(self, name):
            self._require_admin(False)
            return {"code": app.users().invite(name)}

        def api_fs(self):
            self._require_admin(False)
            return fsbrowse.list_dirs((self._query.get("path") or [""])[0], show_hidden=bool(self._query.get("hidden")))

        def api_activity(self):
            self._require_admin(False)
            rows = []
            for item in app.api().connections():
                rows.append({"username": item.get("username", ""), "protocol": item.get("protocol", ""), "address": item.get("remote_address", ""), "since": item.get("connection_time", 0),
                             "command": item.get("command", "")})
            return {"connections": rows}

        def api_blocked(self):
            self._require_admin(False)
            return {"blocked": [{"ip": ip, "seconds": seconds} for ip, seconds in app.dav_limiter.snapshot()]}

        def api_unblock(self, ip):
            self._require_admin(True)
            import ipaddress
            try:
                ipaddress.ip_address(ip)
            except ValueError:
                raise ApiError(400, "That is not an address") from None
            app.dav_limiter.success(ip)
            log.info("address unblocked by the administrator: %s", ip)
            return {"ok": True}

        def api_log(self):
            self._require_admin(False)
            count = max(10, min(500, int((self._query.get("lines") or ["200"])[0] or 200)))
            return {"nextpush": list(app.log_lines)[-count:], "sftpgo": list(app.supervisor.lines)[-count:]}

        def api_settings_get(self):
            self._require_admin(False)
            s = app.settings.snapshot()
            return {"server_name": s["server_name"], "public_url": s["public_url"], "access_key": s["access_key"], "trusted_proxies": s["trusted_proxies"],
                    "admin_port": s["admin_port"], "public_port": s["public_port"], "admin_host": s["admin_host"], "public_host": s["public_host"], "dav_path": "/dav"}

        def api_settings_put(self):
            self._require_admin(True)
            data = self._body()
            changes: dict[str, Any] = {}
            if "server_name" in data:
                changes["server_name"] = str(data["server_name"]).strip()[:60]
            if "public_url" in data:
                changes["public_url"] = valid_public_url(str(data["public_url"]))
            key = app.settings.get("access_key")
            if "access_key_enabled" in data:
                key["enabled"] = bool(data["access_key_enabled"])
            if data.get("rotate_key"):
                import secrets
                key["value"] = secrets.token_urlsafe(24)
                key["header"] = ACCESS_KEY_HEADER
            changes["access_key"] = key
            if "trusted_proxies" in data:
                from .security import parse_networks
                if not isinstance(data["trusted_proxies"], list):
                    raise ApiError(400, "Trusted proxies must be a list")
                values = [str(v).strip() for v in data["trusted_proxies"]]
                if len(parse_networks(values)) != len(values):
                    raise ApiError(400, "Trusted proxies must be addresses or networks like 192.168.1.0/24")
                changes["trusted_proxies"] = values
            app.settings.update(**changes)
            return self.api_settings_get()

        def api_sftpgo_restart(self):
            self._require_admin(True)
            app.supervisor.restart()
            return {"ok": True}

    ROUTES: dict[tuple[str, str], Callable[..., Any]] = {
        ("GET", "/api/state"): Handler.api_state, ("POST", "/api/setup"): Handler.api_setup, ("POST", "/api/login"): Handler.api_login, ("POST", "/api/logout"): Handler.api_logout,
        ("POST", "/api/admin/password"): Handler.api_change_password, ("GET", "/api/users"): Handler.api_users, ("POST", "/api/users"): Handler.api_user_create,
        ("GET", "/api/fs"): Handler.api_fs, ("GET", "/api/blocked"): Handler.api_blocked, ("GET", "/api/activity"): Handler.api_activity, ("GET", "/api/log"): Handler.api_log, ("GET", "/api/settings"): Handler.api_settings_get,
        ("PUT", "/api/settings"): Handler.api_settings_put, ("POST", "/api/sftpgo/restart"): Handler.api_sftpgo_restart,
    }
    user = r"/api/users/([^/]{1,64})"
    PATTERNS = [("GET", re.compile(f"^{user}$"), Handler.api_user_get), ("PUT", re.compile(f"^{user}$"), Handler.api_user_update), ("DELETE", re.compile(f"^{user}$"), Handler.api_user_delete),
                ("POST", re.compile(f"^{user}/password$"), Handler.api_user_password), ("DELETE", re.compile(r"^/api/blocked/([0-9a-fA-F:.]{2,45})$"), Handler.api_unblock), ("GET", re.compile(f"^{user}/invite$"), Handler.api_user_invite)]
    return Handler
