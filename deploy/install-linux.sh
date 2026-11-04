#!/usr/bin/env bash
# Installs NextPush as a systemd service on Linux: a "nextpush" system user, the program in /opt/nextpush, SFTPGo (checksum verified) in /opt/nextpush/bin, data in /var/lib/nextpush.
# Run as root from a checkout:   sudo deploy/install-linux.sh
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "Run this as root (sudo)." >&2; exit 1; }
SRC="$(cd "$(dirname "$0")/.." && pwd)"
command -v python3 >/dev/null || { echo "Python 3.10+ is required." >&2; exit 1; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || { echo "Python 3.10+ is required." >&2; exit 1; }

id nextpush >/dev/null 2>&1 || useradd --system --home-dir /var/lib/nextpush --shell /usr/sbin/nologin nextpush
install -d -o nextpush -g nextpush -m 750 /var/lib/nextpush
install -d -m 755 /opt/nextpush
rm -rf /opt/nextpush/nextpush && cp -r "$SRC/nextpush" /opt/nextpush/nextpush
cp "$SRC/LICENSE" "$SRC/THIRD-PARTY-NOTICES.md" /opt/nextpush/
DEST=/opt/nextpush/bin "$SRC/tools/fetch-sftpgo.sh"
find /opt/nextpush -name '__pycache__' -type d -prune -exec rm -rf {} +
install -m 644 "$SRC/deploy/nextpush.service" /etc/systemd/system/nextpush.service
systemctl daemon-reload
systemctl enable --now nextpush
sleep 3
echo
echo "NextPush is running. The admin address and the one-time setup code are in the log:"
echo "  journalctl -u nextpush -n 20 --no-pager"
echo "Remember: the shared folders must be readable (and writable for 'pull' users) by the 'nextpush' user."
