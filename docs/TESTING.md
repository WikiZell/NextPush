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
| `test_admin_api.py` | setup flow, sign-in, CSRF, origin check, lock-out, settings, static files and path traversal, bad bodies, no leaks, keep-alive with unread bodies |
| `test_integration.py` | real SFTPGo: users and virtual folders, unicode names, Range, delete by mode, isolation, `../` and symlink escapes, upload/rename/mkdir refused even with the gate bypassed, password reset, disable, expiry, speed limit, activity, crash restart, persistence across restarts, brute-force ban, throughput |
| `test_nextpull_compat.py` | the real NextPull client: invite -> login -> listing -> copy and move with real rclone, read-only users cannot delete, wrong key/password messages, secrets absent from its logs |

## Also checked by hand (not automated)

- The web GUI in a real browser: setup (wrong code, mismatching passwords), folder picker, create user (password and invite shown once), edit, sign out/in, settings, log. No console errors other than expected refusals.
- The packaged Windows program (`build\build.ps1`): it finds its own `bin\sftpgo.exe`, serves the GUI, creates a user and serves a download.
- `tools/fetch-sftpgo.ps1` accepts the right archive and refuses a modified one.

## Not covered by automated tests

`deploy/install-linux.sh`, `deploy/nextpush.service`, `deploy/install-windows.ps1` and the `Dockerfile` are provided as templates; they were reviewed but not exercised in the test environment.
