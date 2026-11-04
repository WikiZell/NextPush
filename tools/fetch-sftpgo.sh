#!/usr/bin/env bash
# Downloads the official SFTPGo release that NextPush was tested with, verifies its SHA-256 and unpacks it into ./bin (next to the project / the installed program).
# SFTPGo is a separate program (AGPL-3.0, https://github.com/drakkan/sftpgo); NextPush never modifies it.
#
#   tools/fetch-sftpgo.sh                       # downloads for this machine (x86_64, arm64 or armv7)
#   tools/fetch-sftpgo.sh --from-file x.tar.xz  # verify and unpack an archive you downloaded yourself
set -euo pipefail

VERSION="${SFTPGO_VERSION:-2.7.6}"
DEST="${DEST:-$(cd "$(dirname "$0")/.." && pwd)/bin}"
FROM_FILE=""
[ "${1:-}" = "--from-file" ] && FROM_FILE="${2:?usage: --from-file <archive>}"

case "$(uname -m)" in
  x86_64|amd64)  ARCH="x86_64"; SUM_2_7_6="71f5c9ae3284fc20bb606b2cf396db49645f9bdf4dba770cd76d6fd4720d5cb4" ;;
  aarch64|arm64) ARCH="arm64";  SUM_2_7_6="0d5c54a266515ae86a6a534e76c94807453353a1913ba3a26e81b9c20e548937" ;;
  armv7l|armv7)  ARCH="armv7";  SUM_2_7_6="4ca2f028507b2640a68c91f73265b3257f6382bd298a90d424eb5df17e12ad78" ;;
  *) echo "Unsupported CPU: $(uname -m)" >&2; exit 1 ;;
esac
[ "$VERSION" = "2.7.6" ] || { echo "No pinned checksum for SFTPGo $VERSION. Add it to this script after checking it on the GitHub release page." >&2; exit 1; }

ASSET="sftpgo_v${VERSION}_linux_${ARCH}.tar.xz"
URL="https://github.com/drakkan/sftpgo/releases/download/v${VERSION}/${ASSET}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

if [ -n "$FROM_FILE" ]; then cp "$FROM_FILE" "$WORK/$ASSET"; else echo "Downloading $URL"; curl -fL --proto '=https' --tlsv1.2 -o "$WORK/$ASSET" "$URL"; fi
ACTUAL="$(sha256sum "$WORK/$ASSET" | cut -d' ' -f1)"
[ "$ACTUAL" = "$SUM_2_7_6" ] || { echo "SHA-256 mismatch for $ASSET (got $ACTUAL). Nothing was installed." >&2; exit 1; }
echo "SHA-256 verified."

mkdir -p "$WORK/x" "$DEST"
tar -xJf "$WORK/$ASSET" -C "$WORK/x"
cp "$WORK/x/sftpgo" "$DEST/sftpgo"
chmod 755 "$DEST/sftpgo"
cp -r "$WORK/x/templates" "$DEST/templates"
for f in LICENSE NOTICE; do [ -f "$WORK/x/$f" ] && cp "$WORK/x/$f" "$DEST/SFTPGO-$f.txt" || true; done   # AGPL: the license travels with the program
echo "SFTPGo $VERSION installed in $DEST"
