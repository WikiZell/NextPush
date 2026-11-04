"""Browsing the server's folders from the admin GUI (to pick what to share) and the rules about what may be shared."""
from __future__ import annotations

import os
import string
from pathlib import Path
from typing import Any

NEVER_SHARE_POSIX = ("/proc", "/sys", "/dev", "/etc", "/boot", "/run", "/root", "/var/lib/nextpush")
NEVER_SHARE_WINDOWS = ("windows", "program files", "program files (x86)", "programdata", "$recycle.bin", "system volume information")


class ShareError(ValueError):
    """A folder that cannot be shared, with a message for the administrator."""


def windows_drives() -> list[str]:
    if os.name != "nt":
        return []
    import ctypes
    mask = ctypes.windll.kernel32.GetLogicalDrives()   # type: ignore[attr-defined]
    return [f"{letter}:\\" for index, letter in enumerate(string.ascii_uppercase) if mask & (1 << index)]


def roots() -> list[str]:
    return windows_drives() if os.name == "nt" else ["/"]


def list_dirs(path: str, show_hidden: bool = False) -> dict[str, Any]:
    """Sub-folders of ``path`` (empty path = the roots). Only directories are listed; unreadable ones are skipped."""
    if not path:
        return {"path": "", "parent": None, "dirs": [{"name": root, "path": root} for root in roots()]}
    target = Path(path)
    if not target.is_absolute() or not target.is_dir():
        raise ShareError("That folder does not exist on this server")
    real = os.path.realpath(target)
    dirs: list[dict[str, str]] = []
    try:
        with os.scandir(real) as entries:
            for entry in entries:
                try:
                    if not entry.is_dir(follow_symlinks=True):
                        continue
                except OSError:
                    continue
                if entry.name.startswith(".") and not show_hidden:
                    continue
                dirs.append({"name": entry.name, "path": os.path.join(real, entry.name)})
    except PermissionError as error:
        raise ShareError("This server is not allowed to read that folder") from error
    dirs.sort(key=lambda item: item["name"].lower())
    parent = os.path.dirname(real.rstrip("\\/")) if os.path.dirname(real.rstrip("\\/")) != real else ""
    if os.name == "nt" and len(real) <= 3:
        parent = ""
    return {"path": real, "parent": parent, "dirs": dirs}


def _is_inside(child: str, parent: str) -> bool:
    child, parent = os.path.normcase(os.path.realpath(child)), os.path.normcase(os.path.realpath(parent))
    return child == parent or child.startswith(parent.rstrip("\\/") + os.sep)


def validate_share_path(path: str, data_dir: Path | None) -> str:
    """The real, absolute path of a folder that may be shared. Refuses what would be a foot-gun: a drive/filesystem root, NextPush's own data (it holds the
    user database and the keys), and system folders. Symbolic links are resolved first, so a link cannot be used to escape these rules."""
    raw = str(path or "").strip()
    if not raw or not os.path.isabs(raw):
        raise ShareError("Choose a folder by its full path")
    if "\x00" in raw:
        raise ShareError("Invalid folder")
    real = os.path.realpath(raw)
    if not os.path.isdir(real):
        raise ShareError("That folder does not exist on this server")
    if os.path.dirname(real.rstrip("\\/")) == real.rstrip("\\/") or real in roots() or (os.name == "nt" and len(real) <= 3):
        raise ShareError("Sharing a whole drive or the filesystem root is not allowed. Pick a folder inside it")
    if data_dir is not None and (_is_inside(real, str(data_dir)) or _is_inside(str(data_dir), real)):
        raise ShareError("This folder contains (or is inside) NextPush's own data folder")
    lowered = os.path.normcase(real)
    if os.name == "nt":
        parts = [part.lower() for part in Path(real).parts[1:]]
        if parts and parts[0] in NEVER_SHARE_WINDOWS:
            raise ShareError("System folders cannot be shared")
    else:
        for blocked in NEVER_SHARE_POSIX:
            if lowered == blocked or lowered.startswith(blocked + "/"):
                raise ShareError("System folders cannot be shared")
    if not os.access(real, os.R_OK | os.X_OK):
        raise ShareError("This server is not allowed to read that folder")
    return real


def virtual_name(path: str) -> str:
    """The default name a shared folder gets in the user's view: the folder's own name, made safe for a URL path."""
    base = os.path.basename(os.path.realpath(path).rstrip("\\/")) or "share"
    cleaned = "".join(ch if (ch.isalnum() or ch in " ._-()[]+&'") else "_" for ch in base).strip(" .") or "share"
    return cleaned[:60]
