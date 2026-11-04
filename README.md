# NextPush

**A zero-config file server for [NextPull](https://github.com/WikiZell/NextPull), managed from a web page.** Create users, choose which server folders each of them may use, and send them an invite code.
It runs on **Linux and Windows**, also with no screen attached (headless).

- **No config files.** First start prints a one-time setup code; you open a web page, choose a password, and you are done.
- **Users and folders from the browser.** For each user: which folders, and whether they may only download or also delete files after copying (what NextPull does in *Move* mode). Optional speed limit, connection limit, expiry date.
- **One port for your reverse proxy.** Point Nginx Proxy Manager / Caddy / Traefik at it and turn on HTTPS. The access key and the method filter are built in: strangers get **no answer at all**.
- **Invite code.** One string with the address, the user name and the access key. Paste it in NextPull (*Settings -> Other servers -> Add a server*) and type the password you were given. The password is never in the code.
- **Built on SFTPGo.** The proven [SFTPGo](https://github.com/drakkan/sftpgo) program (AGPL-3.0) does the file serving, as a separate, unmodified process that NextPush starts, watches and restarts. NextPush itself is MIT-licensed and uses only the Python standard library.

```
  NextPull (client) --HTTPS--> reverse proxy --http--> NextPush public port --loopback--> SFTPGo (WebDAV) --> your folders
                                                       NextPush admin port (private) <-- you, in a browser
```

## Quick start

**Windows** (packaged): unzip, then run `nextpush.exe`. **Linux / from source** (Python 3.10+):

```bash
tools/fetch-sftpgo.sh        # downloads SFTPGo, verifies its checksum, puts it in ./bin   (Windows: tools\fetch-sftpgo.ps1)
python3 -m nextpush          # prints the admin address and the setup code
```

Open the admin address (default `http://<this-computer>:8095/`), enter the setup code, choose a password and a server name, add the **public address** (what clients will use, for example `https://dl.example.com`).
Then **Users -> Add user**: pick the folders, create, and send the invite code and the password (separately). See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for services, Docker and the reverse proxy.

## Ports

| Port | What | Publish it? |
| --- | --- | --- |
| 8095 | the admin web interface | **No**, never through a reverse proxy |
| 8096 | public: discovery + WebDAV (gated by the access key) | yes, **only through your reverse proxy with HTTPS** |
| random, loopback | SFTPGo's WebDAV and API | no, they listen on 127.0.0.1 only |

## Documentation

[Architecture](docs/ARCHITECTURE.md) - [Security](docs/SECURITY.md) - [Deployment](docs/DEPLOYMENT.md) - [Testing](docs/TESTING.md) - [Changelog](CHANGELOG.md) - [AGENTS.md](AGENTS.md) (rules for contributors and AI agents)

## Credits and license

Made by **René Girardi**, MIT ([LICENSE](LICENSE)). Runs **SFTPGo** (AGPL-3.0, https://github.com/drakkan/sftpgo) unmodified as a separate program: see [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).
NextPush is an independent project, not affiliated with the SFTPGo project or Nextcloud GmbH.
