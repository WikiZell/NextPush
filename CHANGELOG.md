# Changelog

## 0.1.0 - first version

- **Web-managed file server** for NextPull: first-run setup with a one-time code, users with per-user shared folders (`pull` = download and delete after copy, `read` = download only), optional speed limit,
  connection limit and expiry, invite codes in NextPull's format (never containing the password), activity and log views, settings (public address, access key, trusted proxies, admin password).
- **Public listener** (the only port for a reverse proxy): access-key gate and method filter (strangers get no answer at all), discovery document `/.well-known/nextpush`, streaming WebDAV proxy with Range support.
- **SFTPGo supervisor**: the unmodified SFTPGo 2.7.6 as a separate process (WebDAV and REST on loopback only), generated configuration, health checks, automatic restart with back-off.
- **Packaging**: PyInstaller build for Windows (`build/build.ps1`), checksum-verified SFTPGo fetch scripts, systemd unit and Docker/Windows-task templates.
- **Tests**: unit, API, proxy and **real-SFTPGo integration** tests, plus a compatibility test with the real NextPull client and rclone.
- Bugs found by those tests and fixed before release: a trailing newline slipped past the user-name check; unread request bodies poisoned keep-alive connections; a download cut short by SFTPGo left the client waiting
  for a timeout; a client reset printed tracebacks.
