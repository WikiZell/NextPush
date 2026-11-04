# Deployment

NextPush needs Python 3.10+ (or the packaged Windows program, which includes it) and the SFTPGo program (`tools/fetch-sftpgo.*` installs the tested version into `bin/` and checks its SHA-256).
Everything is configured from the web page; the command line only chooses where things live.

```
python3 -m nextpush [--data-dir DIR] [--sftpgo PATH] [--admin-port 8095] [--public-port 8096] [--admin-host 0.0.0.0] [--public-host 0.0.0.0]
```

SFTPGo is looked for in: `--sftpgo`, `NEXTPUSH_SFTPGO`, `bin/` next to the program, then the PATH. If it is missing the web page still opens and explains it.

## Windows

1. Run `build\build.ps1` (or take a packaged folder). It makes `dist\nextpush\` with `nextpush.exe`, `bin\sftpgo.exe` and the licenses.
2. Quick try: run `nextpush.exe`; the console shows the admin address and the setup code.
3. To run at every start with nobody logged in, from an **elevated** PowerShell: `deploy\install-windows.ps1 -Program C:\NextPush\nextpush.exe` (a Scheduled Task as SYSTEM that restarts the program if it stops).
   Data goes to `C:\ProgramData\NextPush`; the code is in `setup-code.txt` there. Allow the ports in Windows Firewall. Give the SYSTEM account access to the folders you share.

## Linux

- **systemd**: `sudo deploy/install-linux.sh` creates the `nextpush` user, installs to `/opt/nextpush`, fetches SFTPGo, enables the service. `journalctl -u nextpush` shows the setup code. Folders must be readable by `nextpush`
  (and writable if users may delete after copying).
- **Docker**: `docker build -t nextpush .` then `docker run -d --name nextpush -p 8095:8095 -p 8096:8096 -v nextpush-data:/data -v /srv/media:/media nextpush`; the setup code is in `docker logs nextpush`.
  Share the folders as they appear **inside** the container (`/media/...`). On a Raspberry Pi (arm64) the same Dockerfile works.

## Reverse proxy

Point it to **port 8096** (plain http) on the NextPush computer and turn on HTTPS (Force SSL, HSTS). Nothing else is needed. Set the **public address** in NextPush (*Settings*), then the invites contain the right address.

- Nginx Proxy Manager: Scheme `http`, Forward host/port = this computer and 8096, SSL certificate + Force SSL. If the proxy runs on another computer, add its address to *Trusted proxies* (private networks are trusted by default)
  so that logins are attributed to the real client address.
- Use a hard-to-guess hostname with a wildcard certificate, so certificate transparency logs do not publish the exact name, and a DNS-only record if no CDN should see the traffic in clear.
- Do **not** publish port 8095.

## Backups and moving

Stop NextPush and copy the data folder (`nextpush.json`, `sftpgo/`). The access key, users and the admin password come with it. Do not copy `sftpgo/` between two *running* servers.

## Troubleshooting

| Symptom | Likely cause |
| --- | --- |
| The page says "The file service (SFTPGo) is not running" | `bin/sftpgo` missing or not executable; run `tools/fetch-sftpgo.*`; the page shows the reason and the Log tab has SFTPGo's last lines |
| Clients get "Cannot reach the server" | the proxy does not point to 8096, or the access key in their invite is old (rotated) |
| A user can log in but sees no files | the `nextpush` / SYSTEM account cannot read the folder |
| Deleting fails (403) for a `pull` user | the account running NextPush cannot write to the folder |
| Lost the admin password | stop NextPush, set `"admin_hash": ""` in `nextpush.json`, start it: a new setup code is printed (users are kept) |
