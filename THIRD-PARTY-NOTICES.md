# Third-party notices

NextPush is an independent project, **not affiliated with or endorsed by** the SFTPGo project or Nextcloud GmbH. "SFTPGo" is the name of its owner; NextPush only runs that program.

## SFTPGo (the file service)

NextPush runs the official **[SFTPGo](https://github.com/drakkan/sftpgo)** program as a **separate process**, **unmodified**, and talks to it only through its documented REST API and its WebDAV service.
It does the real work: users, virtual folders, permissions, WebDAV, login protection.

- Version tested with: **2.7.6** (`sftpgo_v2.7.6_*`, SHA-256 checked by `tools/fetch-sftpgo.ps1` / `.sh`)
- License: **GNU Affero General Public License v3.0** (AGPL-3.0). The license text and SFTPGo's own NOTICE file are installed next to the program (`bin/SFTPGO-LICENSE.txt`, `bin/SFTPGO-NOTICE.txt`).
- Source code of exactly this version: https://github.com/drakkan/sftpgo/tree/v2.7.6 (also `sftpgo_v2.7.6_src_with_deps.tar.xz` on the release page: https://github.com/drakkan/sftpgo/releases/tag/v2.7.6)
- NextPush's own code is MIT-licensed (see `LICENSE`). Because SFTPGo runs as a separate program and is not modified or linked into NextPush, the AGPL applies to SFTPGo only. If you modify SFTPGo
  and let others use it over a network, the AGPL requires you to offer them your modified source.
- SFTPGo also exists as a commercial edition without the AGPL conditions (https://sftpgo.com); NextPush does not need it.

## Python

NextPush uses only the Python standard library (no third-party packages at run time). The packaged program contains the Python runtime (PSF License) and is built with PyInstaller
(GPL-2.0-or-later with the exception that allows bundling any program).

NextPull (https://github.com/WikiZell/NextPull, MIT) is the client these invites are made for; NextPush works with it but does not contain it.
