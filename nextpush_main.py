"""Entry point for the packaged program (PyInstaller needs a plain script; ``python -m nextpush`` is the same thing)."""
import sys

from nextpush.__main__ import main

if __name__ == "__main__":
    sys.exit(main())
