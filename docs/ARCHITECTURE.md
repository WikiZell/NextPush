# Architecture

One Python process (standard library only) plus one child process (SFTPGo).

```
nextpush (Python)                                   sftpgo (child process, unmodified)
  App ── Settings (nextpush.json, atomic, 0600)       WebDAV  127.0.0.1:<dav_port>  prefix /dav
   │                                                  REST    127.0.0.1:<api_port>  (private admin account made by NextPush)
   ├─ Supervisor ── starts / watches / restarts ───►  sqlite database in <data>/sftpgo (users, virtual folders)
   ├─ Admin listener   :8095   GUI + JSON API (session cookie, CSRF, lock-out)
   ├─ Public listener  :8096   gate -> discovery | /dav proxy ──► SFTPGo WebDAV
   └─ UserService ── sftpgo.Api (REST) ── users, folders, connections
```

| Module | Role |
| --- | --- |
| `nextpush/__main__.py` | CLI: data dir, ports, prints the admin address and the one-time setup code |
| `app.py` | wires everything; logging with redaction of secrets; setup-code file |
| `config.py` | data dir, `Settings` (defaults, atomic private writes, free ports, access key) |
| `security.py` | PBKDF2 hashes, session store, login lock-out, trusted-proxy client address, name rules |
| `sftpgo.py` | `Supervisor` (config generation, process, health, restart with back-off) and `Api` (REST client with token renewal) |
| `users.py` | `UserService`: NextPush users and shared folders -> SFTPGo users, virtual folders, permissions; modes `pull` / `read` |
| `fsbrowse.py` | folder picker data and the rules about what may be shared |
| `admin.py` | the admin listener (static GUI + API) |
| `public.py` | the public listener: access-key gate, method filter, discovery, streaming WebDAV proxy |
| `invite.py` | invite codes in NextPull's format |
| `web/` | the GUI: plain HTML/CSS/JS, no build step, everything escaped before it is inserted |

## Data folder (`--data-dir` / `NEXTPUSH_DATA_DIR`)

`nextpush.json` (settings, the admin password **hash**, the access key), `setup-code.txt` (only until setup is done), `nextpush.log`, `empty-root/` (the read-only home of every user),
`sftpgo/` (`sftpgo.json` generated at each start, `sftpgo.db`, `admin.json` = the private SFTPGo admin login, `sftpgo.log`). Back up this folder to back up the server.

## A user's view

Each NextPush user is an SFTPGo user whose **only protocol is WebDAV**, whose home is the empty read-only folder, and whose files are **virtual folders** mapped to real folders
(`/Films` -> `/data/media/films`). Real paths never reach clients. Modes: `pull` = list, download, delete files; `read` = list, download. Nobody can upload, rename, overwrite or make folders
(the public listener drops those methods, and SFTPGo would refuse them anyway: both are tested).

## Request path (public)

1. The reverse proxy forwards to `:8096` (plain http on your network).
2. `PublicServer.parse_request` runs the **gate** before anything else: access key header (constant-time compare), then allowed methods (`GET HEAD OPTIONS PROPFIND DELETE`). A failure closes the connection without a byte.
3. `/.well-known/nextpush` answers the discovery document; `/.well-known/nextpush/me` tells a signed-in user the names and modes of their folders (login checked by SFTPGo, counted by the same lock-out, never real paths); `/dav/...` is proxied: hop-by-hop headers dropped, the key and forwarding headers removed, `X-Forwarded-For` set to the real client address
   (believed from the proxy only when it is a trusted proxy; the right-most untrusted entry wins), the body read, the answer streamed in 256 KB chunks. If SFTPGo ends early, the client connection is cut so the download fails fast.
4. SFTPGo authenticates the user, applies the virtual-folder permissions and its defender (it sees the real client address).
