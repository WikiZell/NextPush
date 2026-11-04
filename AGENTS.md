# AGENTS.md

Guidance for AI coding agents and contributors. Humans start with [README.md](README.md).

## What this is

A zero-config, web-managed file server for **NextPull** (https://github.com/WikiZell/NextPull). Python standard library only; it runs the **unmodified SFTPGo** program (AGPL-3.0) as a separate process and drives it
through its REST API. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Commands

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests   # needs bin/sftpgo (tools/fetch-sftpgo.*) for the integration tests; they skip otherwise
node --check nextpush/web/app.js
python3 -m nextpush --data-dir /tmp/np --admin-host 127.0.0.1     # run it
```

## Hard rules (safety invariants; keep them, add a test when you touch the code that enforces them)

1. **The public listener answers strangers with nothing**: no access key (when enabled) or a method outside `GET HEAD OPTIONS PROPFIND DELETE` closes the connection before any byte is sent (`public.parse_request`).
2. **Clients never upload, rename, overwrite or create folders**: SFTPGo users get only `list`, `download`, (`delete_files` in `pull` mode) and only the `DAV` protocol. Both layers are tested, the second one with the gate bypassed.
3. **Real paths never reach clients**: shares are virtual folders; `fsbrowse.validate_share_path` refuses roots, system folders, NextPush's own data folder and anything reaching them through symlinks.
4. **Secrets**: the admin password is stored only as a PBKDF2 hash; the access key and the private SFTPGo admin login only in 0600 files; passwords of users are shown **once** and never stored or listed; invite codes never contain a
   password; the log filter redacts registered secrets; the key header is not forwarded to SFTPGo.
5. **Every admin change needs the session cookie, the CSRF header and a matching `Origin`**; the first administrator needs the one-time setup code; wrong passwords and codes lock the address out.
6. **Request bodies are always read** (admin and public), or the leftover bytes poison a keep-alive connection (bug found by testing).
7. **Never `$` in validation regexes**: use `fullmatch` (a trailing newline slipped past `$`).
8. **Login protection is NextPush's own** (`public.py` + `security.LoginLimiter`): SFTPGo's defender stays off (its ban time escalates compoundingly, to days, when a client keeps retrying). Locks are capped at 10 minutes, only HTTP 401 is
   counted, locked requests are not forwarded, exempt users are never counted.
9. **SFTPGo stays separate and unmodified.** Do not import or link its code; do not patch the binary. Pin the tested version (`SFTPGO_TESTED_VERSION`, `tools/fetch-sftpgo.*` checksums).
10. **No personal data in the repository**: use `dl.example.com`, `bob`, `192.168.1.10` in docs and tests. No real hosts, user names, paths or keys.

## Conventions

- Python 3.10+ (`from __future__ import annotations`, `X | None`), type hints on public functions, no third-party runtime dependencies (update README and `requirements.txt` if you ever add one).
- The GUI is plain HTML/CSS/JS in `nextpush/web/`: no build step, no inline scripts, escape everything with `esc()` before it goes into HTML.
- API calls return JSON; errors are `{"error": "message for the administrator"}` and never contain internals.
- Tests use real components where it matters (real SFTPGo, real rclone through NextPull) and fakes only for the edge cases that cannot be produced reliably.

## Definition of done

- The whole test suite passes; new behaviour has a test; `node --check` passes when the GUI changed.
- README / docs updated if behaviour, commands or the security model changed; add a line to `CHANGELOG.md`.
- `git grep` shows no real hostnames, IPs, user names or keys.
