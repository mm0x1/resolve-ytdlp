"""DaVinci Resolve Utility entry point: "Download from URL (yt-dlp)" (decisions.md Q11).

Thin per decisions.md Q9: adds this script's own directory to `sys.path` so a
sibling `resolve_ytdlp/` package is importable, then delegates to `app.main()`.
`install.py` (PR-5) places this file directly alongside a `resolve_ytdlp/`
copy or symlink in the same directory — deliberately *not* resolving symlinks
below, so this works identically whether this file itself arrived via `cp` or
`symlink_to()`. Kept minimal, stdlib-only.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPT_DIR = Path(__file__).parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

from resolve_ytdlp import app  # noqa: E402

if __name__ == "__main__":
    app.main()
