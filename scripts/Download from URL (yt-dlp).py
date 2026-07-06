"""DaVinci Resolve Utility entry point: "Download from URL (yt-dlp)" (decisions.md Q11).

Resolve's Scripts menu derives the menu label directly from this file's own
name (extension stripped), and recursively lists every subdirectory as a
submenu with every ``.py`` file inside as its own runnable entry — so the
``resolve_ytdlp`` package must *not* live alongside this file under
Scripts/Utility (it would otherwise show each internal module as if it were
a separate script). ``install.py`` instead places the package under
Resolve's ``Scripts/Modules`` directory, the convention Resolve itself
provides for shared code that Scripts menus don't scan.

This file adds ``../Modules`` (relative to its own directory, deliberately
*not* resolving symlinks, so this works identically whether this file itself
arrived via `cp` or `symlink_to()`) to `sys.path`, then delegates to
`app.main()`. Kept minimal, stdlib-only.
"""

from __future__ import annotations

import sys
from pathlib import Path

_MODULES_DIR = Path(__file__).parent.parent / "Modules"
if str(_MODULES_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULES_DIR))

from resolve_ytdlp import app  # noqa: E402

if __name__ == "__main__":
    app.main()
