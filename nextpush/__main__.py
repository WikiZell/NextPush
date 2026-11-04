"""``python -m nextpush`` (or the packaged ``nextpush`` program): start the server."""
from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

from . import __version__
from .app import App
from .config import default_data_dir
from .sftpgo import find_binary


def lan_address() -> str:
    """The address other computers on the network would use to reach this one (best effort, no traffic is sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            return sock.getsockname()[0]
    except OSError:
        return "localhost"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nextpush", description="NextPush: a zero-config file server for NextPull, managed from a web page")
    parser.add_argument("--data-dir", type=Path, help="where users, keys and logs are kept (default: a per-platform folder)")
    parser.add_argument("--sftpgo", help="path to the SFTPGo program (default: ./bin next to NextPush, NEXTPUSH_SFTPGO, or the PATH)")
    parser.add_argument("--admin-port", type=int, help="web interface port (default 8095)")
    parser.add_argument("--public-port", type=int, help="port for the reverse proxy and clients (default 8096)")
    parser.add_argument("--admin-host", help="address the web interface listens on (default 0.0.0.0; use 127.0.0.1 to allow this computer only)")
    parser.add_argument("--public-host", help="address the public port listens on (default 0.0.0.0)")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)   # the setup code must show up at once when the output goes to a file or a service log
    data_dir = args.data_dir or default_data_dir()
    app_dir = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    app = App(data_dir, binary=find_binary(args.sftpgo, app_dir), admin_port=args.admin_port, public_port=args.public_port, admin_host=args.admin_host, public_host=args.public_host)
    app.start()
    host = lan_address()
    print()
    print(f"NextPush {__version__}")
    print(f"  Admin interface : http://{host}:{app.admin_address[1]}/   (this is private: never publish it through a reverse proxy)")
    print(f"  Public port     : {app.public_address[1]}   (point your reverse proxy here)")
    print(f"  Data folder     : {data_dir}")
    if not app.settings.configured:
        print(f"  FIRST-TIME SETUP: open the admin interface and enter this setup code: {app.setup_token()}")
    if app.sftpgo_error:
        print(f"  PROBLEM         : {app.sftpgo_error}")
    print()
    try:
        app.wait()
    finally:
        app.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
