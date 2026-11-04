# Changelog

## 0.1.1 - login protection fixed

- **Problem found in real use:** SFTPGo's own login protection ("defender") raised a ban by 50 % on every further attempt, so a client that kept retrying (a saved password that was wrong for a while) went from a
  30-minute ban to one lasting **6 days**; the person was then "rejected" (HTTP 403) from outside while everything worked from the home network.
- **Now NextPush does the protection itself** on the public port: wrong passwords (HTTP 401) are counted per real client address; after 8, that address gets a clear `429 Too many wrong passwords, try again in N seconds`
  (`Retry-After`), SFTPGo is not contacted while it is locked, the lock grows 2 s, 4 s, 8 s... and **never exceeds 10 minutes**; a good login resets the count. SFTPGo's defender is switched off.
- **Activity -> Blocked addresses**: see who is locked out and unblock them with one click.
- **Users can be exempt from lock-out** ("Never lock this user's address out", per user): their wrong passwords are not counted and never lock anyone out.
- The log now says why a login was refused or an address locked (one line per address per minute, never a password).
- Tests: real-SFTPGo brute-force tests, unit tests for the limiter and the proxy, exemptions, caps.

## 0.1.0 - first version

- Verified on a Raspberry Pi (arm64): the full suite, and the whole public chain through a reverse proxy with HTTPS driven by the real NextPull client (see docs/TESTING.md). Documentation of the client side and of a tested native install added.

- **Web-managed file server** for NextPull: first-run setup with a one-time code, users with per-user shared folders (`pull` = download and delete after copy, `read` = download only), optional speed limit,
  connection limit and expiry, invite codes in NextPull's format (never containing the password), activity and log views, settings (public address, access key, trusted proxies, admin password).
- **Public listener** (the only port for a reverse proxy): access-key gate and method filter (strangers get no answer at all), discovery document `/.well-known/nextpush`, streaming WebDAV proxy with Range support.
- **SFTPGo supervisor**: the unmodified SFTPGo 2.7.6 as a separate process (WebDAV and REST on loopback only), generated configuration, health checks, automatic restart with back-off.
- **Packaging**: PyInstaller build for Windows (`build/build.ps1`), checksum-verified SFTPGo fetch scripts, systemd unit and Docker/Windows-task templates.
- **Tests**: unit, API, proxy and **real-SFTPGo integration** tests, plus a compatibility test with the real NextPull client and rclone.
- Bugs found by those tests and fixed before release: a trailing newline slipped past the user-name check; unread request bodies poisoned keep-alive connections; a download cut short by SFTPGo left the client waiting
  for a timeout; a client reset printed tracebacks.
