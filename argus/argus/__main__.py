"""`python -m argus` -- the front door without an install step."""
from __future__ import annotations

from argus.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
