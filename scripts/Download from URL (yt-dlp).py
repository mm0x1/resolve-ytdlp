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

Resolve's Fusion script loader executes Utility scripts without populating
the `__file__` global (confirmed empirically: a plain ``__file__`` reference
here raises ``NameError``), unlike a normal module import. The compiled code
object's ``co_filename`` still holds the real script path, so that's used
instead via `inspect`.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

_THIS_FILE = Path(inspect.currentframe().f_code.co_filename)
_MODULES_DIR = _THIS_FILE.parent.parent / "Modules"
if str(_MODULES_DIR) not in sys.path:
    sys.path.insert(0, str(_MODULES_DIR))

from resolve_ytdlp import app  # noqa: E402

if __name__ == "__main__":
    app.main()
