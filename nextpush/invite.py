"""Invite codes for NextPull ("Settings -> Other servers -> Add a server"). The format is NextPull's own (``nextpull-invite:`` + base64url JSON), so a code made here
is accepted there. It holds the WebDAV address, the user name, a display name and the access key. **Never the password.**"""
from __future__ import annotations

import base64
import json

PREFIX = "nextpull-invite:"


def make_invite(dav_url: str, user: str = "", header_name: str = "", header_value: str = "", label: str = "") -> str:
    data: dict[str, object] = {"v": 1, "t": "sftpgo", "u": dav_url.rstrip("/")}
    if user:
        data["user"] = user
    if label:
        data["l"] = label
    if header_name and header_value:
        data["k"] = [header_name, header_value]
    return PREFIX + base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode("utf-8")).decode("ascii").rstrip("=")


def parse_invite(code: str) -> dict[str, object]:
    text = code.strip()
    if not text.startswith(PREFIX):
        raise ValueError("not an invite code")
    payload = text[len(PREFIX):]
    return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8"))
