"""Checks the GUI offers to the administrator. Standard library only.

``check_public`` answers the question every new installation asks: "does my public address really reach NextPush through my reverse proxy?". It fetches the
discovery document at the public address **from this server** (with the access key and real certificate verification) and explains what it found.
"""
from __future__ import annotations

import http.client
import ipaddress
import json
import socket
import ssl
import urllib.parse
from typing import Any

TIMEOUT = 10.0


def _is_local(host: str) -> bool:
    host = host.strip("[]").lower()
    if host == "localhost" or host.endswith((".local", ".lan")) or "." not in host and ":" not in host:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback


def check_public(public_url: str, key_header: str, key_value: str, expected_name: str = "", timeout: float = TIMEOUT) -> dict[str, Any]:
    """Returns ``{"ok": bool, "level": "ok"|"warn"|"bad", "title": ..., "detail": ..., "tls": {...}|None, "status": int|None}``. Never raises."""
    if not public_url:
        return {"ok": False, "level": "warn", "title": "No public address yet", "detail": "Enter the address your reverse proxy serves (for example https://dl.example.com) in Settings, then check again.", "tls": None, "status": None}
    parts = urllib.parse.urlsplit(public_url)
    host, https = parts.hostname or "", parts.scheme == "https"
    if not host or parts.scheme not in ("http", "https"):
        return {"ok": False, "level": "bad", "title": "The public address is not valid", "detail": "It must look like https://dl.example.com", "tls": None, "status": None}
    port = parts.port or (443 if https else 80)
    note = " This check runs from the server itself: if everything else works for people outside, a failure here can simply mean your router does not allow the server to reach its own public address."
    try:
        if https:
            context = ssl.create_default_context()
            conn: http.client.HTTPConnection = http.client.HTTPSConnection(host, port, timeout=timeout, context=context)
        else:
            conn = http.client.HTTPConnection(host, port, timeout=timeout)
        conn.request("GET", "/.well-known/nextpush", headers={key_header: key_value} if key_header and key_value else {})
        response = conn.getresponse()
        body = response.read(100_000)
        status = response.status
        tls: dict[str, Any] | None = None
        if https:
            sock = conn.sock
            tls = {"protocol": sock.version() if hasattr(sock, "version") else "", "encrypted": True}  # type: ignore[union-attr]
        conn.close()
    except ssl.SSLCertVerificationError as error:
        reason = getattr(error, "verify_message", "") or str(error)
        return {"ok": False, "level": "bad", "title": "The certificate is not valid", "detail": f"The connection to {host} is encrypted but the certificate was refused ({reason}). Check the certificate in your reverse proxy: it must match this name and not be expired.", "tls": None, "status": None}
    except ssl.SSLError as error:
        return {"ok": False, "level": "bad", "title": "HTTPS could not be set up", "detail": f"The TLS handshake with {host}:{port} failed ({error.reason or error}). Is HTTPS enabled for this host in the proxy?", "tls": None, "status": None}
    except socket.gaierror:
        return {"ok": False, "level": "bad", "title": "The name does not resolve", "detail": f"{host} has no DNS record that this server can see. Create it (DNS-only is best for large downloads) and give it a minute." + note, "tls": None, "status": None}
    except (TimeoutError, socket.timeout):
        return {"ok": False, "level": "bad", "title": "No answer (timed out)", "detail": f"{host}:{port} did not answer within {int(timeout)} seconds. Check the DNS record, the router's port forwarding and the firewall." + note, "tls": None, "status": None}
    except (ConnectionRefusedError, ConnectionResetError, OSError) as error:
        return {"ok": False, "level": "bad", "title": "Cannot connect", "detail": f"Connecting to {host}:{port} failed ({error.__class__.__name__}). Check that the reverse proxy is running and the port is open." + note, "tls": None, "status": None}
    except http.client.HTTPException as error:
        return {"ok": False, "level": "bad", "title": "Unexpected answer", "detail": f"The server at {host} did not answer like a web server ({error.__class__.__name__}).", "tls": None, "status": None}

    try:
        document = json.loads(body.decode("utf-8")) if status == 200 else {}
    except (ValueError, UnicodeDecodeError):
        document = {}
    if status == 200 and isinstance(document, dict) and document.get("nextpush"):
        same = not expected_name or document.get("name") == expected_name
        detail = "Your reverse proxy forwards to this NextPush, the access key is accepted" + (" and the connection is encrypted (" + (tls or {}).get("protocol", "TLS") + ")." if https else (", plain http on your own network is fine, but use https:// for anything outside it." if _is_local(host) else ", but the connection is NOT encrypted: use https:// for anything outside your own network."))
        if not same:
            return {"ok": False, "level": "warn", "title": "A different NextPush answers", "detail": f"The address works but the server there is called '{document.get('name')}', not '{expected_name}'. Is the proxy pointing at another computer?", "tls": tls, "status": status}
        return {"ok": True, "level": "ok" if https or _is_local(host) else "warn", "title": "Reachable and working", "detail": detail, "tls": tls, "status": status}
    if status in (502, 503, 504):
        return {"ok": False, "level": "bad", "title": "The proxy answers but cannot reach NextPush", "detail": f"HTTP {status} from the proxy. Usually the access key is wrong in the request, the proxy forwards to the wrong computer or port (it must be the public port of this NextPush), or NextPush is not running there." + note, "tls": tls, "status": status}
    if status == 404:
        return {"ok": False, "level": "bad", "title": "Something else answers here", "detail": "The address works but it is not NextPush (HTTP 404). Check which host and port the proxy forwards to.", "tls": tls, "status": status}
    return {"ok": False, "level": "bad", "title": f"Unexpected answer (HTTP {status})", "detail": "The address answers, but not with NextPush's discovery document.", "tls": tls, "status": status}
