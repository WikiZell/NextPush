"""Users and the folders they may use, translated to SFTPGo users and virtual folders.

A NextPush user is an SFTPGo user whose only protocol is WebDAV and whose home is an empty read-only folder; everything they see is a **virtual folder**
mapped to a real server folder, so the real paths never reach the client. A folder is shared in one of two modes:

* ``pull``: list, download and delete files (what NextPull needs: it removes a file only after a verified copy)
* ``read``: list and download only

Nobody can upload, rename, overwrite or create folders.
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import fsbrowse
from .invite import make_invite
from .security import generate_password, valid_username
from .sftpgo import Api, SftpgoApiError

PRESETS: dict[str, list[str]] = {"pull": ["list", "download", "delete_files"], "read": ["list", "download"]}
MODE_LABELS = {"pull": "Download and delete after copy", "read": "Download only"}
MAX_FOLDERS = 20
_VNAME = re.compile(r"^[^/\\:*?\"<>|\x00-\x1f]{1,60}$")


class UserError(ValueError):
    """Bad input or a conflict, with a message for the administrator."""


def folder_object_name(real_path: str) -> str:
    """Stable name of the SFTPGo folder object for a real path (one object can serve several users)."""
    return "np-" + hashlib.sha1(os.path.normcase(real_path).encode("utf-8")).hexdigest()[:12]


def _expiry_ms(value: Any) -> int:
    """'2027-01-31' (end of that day, UTC) or a millisecond timestamp -> ms; empty -> 0 (never)."""
    if value in (None, "", 0):
        return 0
    if isinstance(value, (int, float)):
        return int(value)
    try:
        day = datetime.strptime(str(value), "%Y-%m-%d").replace(hour=23, minute=59, second=59, tzinfo=timezone.utc)
    except ValueError as error:
        raise UserError("The expiry date must look like 2027-01-31") from error
    return int(day.timestamp() * 1000)


class UserService:
    def __init__(self, api: Api, data_dir: Path | None, settings) -> None:   # noqa: ANN001 - Settings, kept loose to avoid an import cycle in tests
        self.api, self.data_dir, self.settings = api, data_dir, settings
        self._lock = threading.RLock()   # one change at a time: folder objects are shared between users

    # ------------------------------------------------------------------------------------------------ views
    def _view(self, user: dict[str, Any]) -> dict[str, Any]:
        permissions = user.get("permissions") or {}
        folders = []
        for item in user.get("virtual_folders") or []:
            vpath = item.get("virtual_path", "")
            perms = permissions.get(vpath) or []
            mode = "pull" if "delete_files" in perms else ("read" if perms else "none")
            folders.append({"name": vpath.lstrip("/"), "path": item.get("mapped_path", ""), "mode": mode, "mode_label": MODE_LABELS.get(mode, mode)})
        folders.sort(key=lambda entry: entry["name"].lower())
        return {"username": user["username"], "enabled": user.get("status") == 1, "folders": folders, "max_sessions": user.get("max_sessions", 0),
                "download_kbps": user.get("download_bandwidth", 0), "expires": (datetime.fromtimestamp(user["expiration_date"] / 1000, timezone.utc).strftime("%Y-%m-%d") if user.get("expiration_date") else ""),
                "last_login": user.get("last_login", 0), "description": user.get("description", "")}

    def list(self) -> list[dict[str, Any]]:
        return sorted((self._view(user) for user in self.api.list_users()), key=lambda entry: entry["username"])

    def get(self, username: str) -> dict[str, Any]:
        try:
            return self._view(self.api.get_user(username))
        except SftpgoApiError as error:
            if error.status == 404:
                raise UserError("That user does not exist") from error
            raise

    # ------------------------------------------------------------------------------------------------ input
    def _folders(self, raw: Any) -> list[dict[str, str]]:
        if not isinstance(raw, list) or not raw:
            raise UserError("Share at least one folder")
        if len(raw) > MAX_FOLDERS:
            raise UserError(f"At most {MAX_FOLDERS} folders per user")
        clean, seen = [], set()
        for item in raw:
            if not isinstance(item, dict):
                raise UserError("Invalid folder")
            real = fsbrowse.validate_share_path(str(item.get("path") or ""), self.data_dir)
            mode = str(item.get("mode") or "pull")
            if mode not in PRESETS:
                raise UserError("The folder mode must be 'pull' or 'read'")
            name = str(item.get("name") or "").strip() or fsbrowse.virtual_name(real)
            if not _VNAME.fullmatch(name) or name in (".", ".."):
                raise UserError(f"'{name}' is not a valid folder name")
            if name.lower() in seen:
                raise UserError(f"Two folders are called '{name}'. Give one a different name")
            seen.add(name.lower())
            clean.append({"name": name, "path": real, "mode": mode})
        return clean

    def _ensure_folders(self, folders: list[dict[str, str]]) -> None:
        for folder in folders:
            try:
                self.api.get_folder(folder_object_name(folder["path"]))
            except SftpgoApiError as error:
                if error.status != 404:
                    raise
                self.api.create_folder(folder_object_name(folder["path"]), folder["path"], "shared by NextPush")

    def _body(self, folders: list[dict[str, str]], spec: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
        permissions = {"/": ["list"]}
        virtual = []
        for folder in folders:
            vpath = "/" + folder["name"]
            permissions[vpath] = list(PRESETS[folder["mode"]])
            virtual.append({"name": folder_object_name(folder["path"]), "virtual_path": vpath})
        body = dict(base)
        body.update({"permissions": permissions, "virtual_folders": virtual, "status": 1 if spec.get("enabled", True) else 0})
        if "max_sessions" in spec or "max_sessions" not in base:
            body["max_sessions"] = max(0, min(100, int(spec.get("max_sessions", 4) or 0)))
        if "download_kbps" in spec or "download_bandwidth" not in base:
            body["download_bandwidth"] = max(0, int(spec.get("download_kbps", 0) or 0))
        if "expires" in spec or "expiration_date" not in base:
            body["expiration_date"] = _expiry_ms(spec.get("expires"))
        if "description" in spec:
            body["description"] = str(spec["description"])[:200]
        return body

    # ------------------------------------------------------------------------------------------------ actions
    def create(self, spec: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """Returns the new user and the **only** time its password is visible (generated when none was given)."""
        username = str(spec.get("username") or "").strip().lower()
        if not valid_username(username):
            raise UserError("The user name needs 2-32 characters: lowercase letters, digits, dot, dash or underscore, starting with a letter or digit")
        password = str(spec.get("password") or "") or generate_password()
        if len(password) < 8:
            raise UserError("The password needs at least 8 characters (or leave it empty for a generated one)")
        with self._lock:
            folders = self._folders(spec.get("folders"))
            self._ensure_folders(folders)
            base = {"username": username, "password": password, "home_dir": str((self.data_dir or Path(".")) / "empty-root"), "filters": {"denied_protocols": ["SSH", "FTP", "HTTP"]}}
            try:
                self.api.create_user(self._body(folders, spec, base))
            except SftpgoApiError as error:
                if error.status == 409 or (error.status == 400 and "exist" in str(error).lower()):
                    raise UserError("A user with this name already exists") from error
                raise
            return self.get(username), password

    def update(self, username: str, spec: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            current = self.api.get_user(username)
            folders = self._folders(spec["folders"]) if "folders" in spec else self._folders([{"path": f["mapped_path"], "name": f["virtual_path"].lstrip("/"),
                                                                                                "mode": "pull" if "delete_files" in (current.get("permissions", {}).get(f["virtual_path"]) or []) else "read"}
                                                                                               for f in current.get("virtual_folders") or []])
            self._ensure_folders(folders)
            old = {item["name"] for item in current.get("virtual_folders") or []}
            current.pop("password", None)
            merged_spec = {"enabled": spec.get("enabled", current.get("status") == 1), **{k: v for k, v in spec.items() if k in ("max_sessions", "download_kbps", "expires", "description")}}
            self.api.update_user(username, self._body(folders, merged_spec, current), disconnect=True)
            self._drop_orphans(old - {folder_object_name(f["path"]) for f in folders})
            return self.get(username)

    def reset_password(self, username: str) -> str:
        password = generate_password()
        with self._lock:
            current = self.api.get_user(username)
            current["password"] = password
            self.api.update_user(username, current, disconnect=True)
        return password

    def delete(self, username: str) -> None:
        with self._lock:
            try:
                current = self.api.get_user(username)
            except SftpgoApiError as error:
                if error.status == 404:
                    raise UserError("That user does not exist") from error
                raise
            self.api.delete_user(username)
            self._drop_orphans({item["name"] for item in current.get("virtual_folders") or []})

    def _drop_orphans(self, names: set[str]) -> None:
        for name in names:
            try:
                if not self.api.get_folder(name).get("users"):
                    self.api.delete_folder(name)
            except SftpgoApiError:
                continue

    # ------------------------------------------------------------------------------------------------ invite
    def invite(self, username: str) -> str:
        """The code a person pastes into NextPull. It needs the public address to be set; it never contains the password."""
        public = str(self.settings.get("public_url") or "")
        if not public:
            raise UserError("Set the public address first (Settings), so the invite knows where the server is")
        self.get(username)
        key = self.settings.get("access_key")
        return make_invite(public.rstrip("/") + "/dav", username, key["header"] if key["enabled"] else "", key["value"] if key["enabled"] else "", str(self.settings.get("server_name") or ""))
