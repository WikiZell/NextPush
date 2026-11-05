# Testing

```bash
python3 -W error::ResourceWarning -m unittest discover -s tests          # everything that can run here
node --check nextpush/web/app.js                                         # the GUI script
```

The unit and API tests need nothing else. The **integration** tests run the **real SFTPGo**: they are skipped with a clear reason when it is not found. Install it with `tools/fetch-sftpgo.*`
(it goes to `bin/`) or point `NEXTPUSH_SFTPGO` at the program. `tests/test_nextpull_compat.py` additionally needs a NextPull checkout (`NEXTPULL_DIR`, or a sibling folder named `NextPull`) with its rclone.

| File | What it proves |
| --- | --- |
| `test_security.py` | password hashing, sessions and expiry, lock-out, real-client-address rules, user-name rules (including a trailing-newline bug it caught) |
| `test_config_invite_fs.py` | settings persistence and privacy, invite format **identical to NextPull's**, what folders may be shared (roots, system folders, the data folder, symlinks) |
| `test_public_proxy.py` | the gate (no answer without the key), method filter, discovery, streaming, Range, HEAD, chunked answers, 32 MB file, client address, early end of upstream (fails fast), dead upstream, body limits, keep-alive |
| `test_checks.py` | the "check my public address" feature against scripted servers (ok, wrong NextPush, 404, 502, nothing listening, bad address), its API (session, CSRF, key never returned), every file the page loads is served, the strict CSP, no inline styles/scripts |
| `test_admin_api.py` | setup flow, sign-in, CSRF, origin check, lock-out, settings, static files and path traversal, bad bodies, no leaks, keep-alive with unread bodies |
| `test_integration.py` | real SFTPGo: users and virtual folders, unicode names, Range, delete by mode, isolation, `../` and symlink escapes, upload/rename/mkdir refused even with the gate bypassed, password reset, disable, expiry, speed limit, activity, crash restart, persistence across restarts, brute-force ban, throughput |
| `test_nextpull_compat.py` | the real NextPull client: invite -> login -> listing -> copy and move with real rclone, read-only users cannot delete, wrong key/password messages, secrets absent from its logs |

## Also checked by hand (not automated)

- The web GUI in a real browser: setup (wrong code, mismatching passwords), folder picker, create user (password and invite shown once), edit, sign out/in, settings, log. No console errors other than expected refusals.
- The packaged Windows program (`build\build.ps1`): it finds its own `bin\sftpgo.exe`, serves the GUI, creates a user and serves a download.
- `tools/fetch-sftpgo.ps1` accepts the right archive and refuses a modified one.

## Also verified on real infrastructure (manual, before the first release)

- **Linux, arm64 (Raspberry Pi, Python 3.11):** the whole suite (117 tests) passes, including the symbolic-link escape tests and the Linux system-folder rules that are skipped on Windows. About 270 MB/s through the proxy.
- **The complete public chain** (HTTPS with a Let's Encrypt certificate -> Nginx Proxy Manager -> NextPush on the Pi -> SFTPGo -> a staging folder), driven from another computer with the real NextPull and rclone:
  discovery and listing, a byte-exact download checked with SHA-256, Range, a 300 MB file (about 60 MB/s over the home network), delete after copy, uploads refused and creating nothing, a wrong password (401), strangers getting only the proxy's
  `502`, the admin API not reachable from outside, NextPull's TLS evidence (TLS 1.3, verified certificate), a real rclone Copy and Move (the shared folder survives), and no secret in NextPull's logs. The access key was rotated afterwards.

## Not covered by automated tests

`deploy/install-linux.sh`, `deploy/nextpush.service`, `deploy/install-windows.ps1` and the `Dockerfile` are provided as templates; they were reviewed but not exercised in the test environment.
