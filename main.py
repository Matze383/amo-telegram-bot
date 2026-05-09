from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from amo_bot.main import run


if __name__ == "__main__":
    # Default to combined bot+webui startup for betatest convenience.
    argv = sys.argv[1:] or ["--serve"]
    run(argv)
