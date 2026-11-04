# Security

## What is protected, and how

| Threat | Protection | Tested in |
| --- | --- | --- |
| A stranger finds the server | The public port answers **nothing** (connection closed) without the access key; wrong methods also get nothing; unknown paths are a bare 404 | `test_public_proxy.Gate`, `Discovery` |
| Someone guesses a user password | SFTPGo's defender bans the address after a few failures; passwords are 32 random characters by default | `test_integration.BruteForce` |
| Someone guesses the admin password | PBKDF2-HMAC-SHA256 (600 000 rounds), 10+ characters, per-address lock-out growing to 15 minutes, session cookie HttpOnly + SameSite=Strict (+ Secure behind https) | `test_admin_api` |
| A web page makes the admin's browser act | CSRF header on every change, `Origin` check, strict Content-Security-Policy, no inline scripts, `X-Frame-Options: DENY` | `test_admin_api.SessionsAndCsrf`, `StaticFiles` |
| First-run takeover | The first administrator needs the **setup code** (console / private file); wrong codes lock the address out; the code is single-use | `test_admin_api.SetupFlow` |
| Reading files outside the shares (`..`, encoded, symlinks) | SFTPGo's virtual-folder confinement; NextPush refuses to share roots, system folders and its own data folder; all tested with a secret file outside the share | `test_integration.UsersAndAccess`, `test_config_invite_fs` |
| One user reading another's folders | per-user virtual folders; tested both ways | `test_integration` |
| Upload / overwrite / rename / mkdir | not allowed: dropped by the public listener, refused by SFTPGo permissions (tested **with the gate bypassed**) | `test_integration` |
| Forged client address | `X-Forwarded-For` is honoured only from trusted proxies; the right-most untrusted entry wins | `test_security.ClientAddress`, `test_public_proxy` |
| Secrets in logs | the access key and the private SFTPGo password are redacted by a log filter; SFTPGo runs at log level `info`; the key header is not forwarded to SFTPGo | `test_admin_api.Hygiene` |
| SFTPGo's own interfaces | only the REST API (loopback) and WebDAV (loopback) are on; its web admin and web client are off | `test_integration.Setup` |
| Internal errors leaking details | the API returns a generic message, the log has the traceback | `test_admin_api.Hygiene` |

## What is NOT protected (know this)

- **The admin port is yours to protect.** It is password-protected and rate-limited, but do not publish it through the reverse proxy. By default it listens on all addresses so that you can reach it from another computer on your
  network; use `--admin-host 127.0.0.1` to allow only the local computer.
- **The hop from the reverse proxy to NextPush is plain http.** Keep it inside your network. Everything that leaves your network should be HTTPS at the proxy.
- **A CDN proxy in front** (for example Cloudflare's orange cloud) decrypts the traffic. Use a DNS-only record if that matters.
- Files are not encrypted at rest. The admin of the server sees everything.
- The access key is a shared secret for everyone invited. Make a new one in *Settings* (old invites then stop working) if it leaks.

## Reporting a problem

Please use a private security advisory on the GitHub repository instead of a public issue.
