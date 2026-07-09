"""Thin wrapper around DaVinci Resolve's scripting API: bin reuse/create, media import, bootstrap.

The single bootstrap/import site for `DaVinciResolveScript` in this codebase
(decisions.md Q9: "all Resolve API behind one interface") — no other module
should import it directly. Imports cleanly under plain `pytest` because the
import is lazy, inside `connect()`, not at module level.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from resolve_ytdlp.config import Settings

NOT_RUNNING_IN_RESOLVE_MESSAGE = (
    "Could not connect to DaVinci Resolve. Run this script from within Resolve "
    "(Workspace > Scripts > Utility)."
)
NO_PROJECT_OPEN_MESSAGE = "No Resolve project is open. Open a project and try again."


@dataclass(frozen=True)
class ImportResult:
    requested: int
    imported: int

    @property
    def ok(self) -> bool:
        return self.imported == self.requested


class ResolveBridge:
    """Wraps a connected Resolve app object with the operations this app needs.

    Takes the connected `resolve` app as a constructor argument instead of
    connecting itself, so tests can inject a fake object graph
    (`tests/conftest.py`) in place of the real embedded interpreter.
    """

    def __init__(self, resolve_app: Any, bmd_module: Any = None) -> None:
        self._resolve_app = resolve_app
        self._bmd_module = bmd_module

    @property
    def app(self) -> Any:
        """The raw connected `resolve` app object.

        Exposed so `gui` (PR-4) can reach `Fusion()`/`UIManager` etc. without a
        second, independent `DaVinciResolveScript` bootstrap of its own.
        """
        return self._resolve_app

    @property
    def bmd(self) -> Any:
        """The raw `DaVinciResolveScript` module, or `None` if not supplied.

        Needed alongside `.app` because `bmd.UIDispatcher(ui)` (decisions.md's
        confirmed GUI bootstrap sequence) requires the module itself, not just
        the connected `resolve` app object.
        """
        return self._bmd_module

    def _current_media_pool(self) -> Any | None:
        """`GetMediaPool()` lives on `Project`, not the top-level `resolve` app —
        confirmed via a real-Resolve traceback (`'NoneType' object is not
        callable` calling `GetMediaPool()` directly on the app object). Reach it
        through `GetProjectManager().GetCurrentProject()`; `None` at either hop
        means no project is open.
        """
        project = self._resolve_app.GetProjectManager().GetCurrentProject()
        if not project:
            return None
        return project.GetMediaPool()

    def get_or_create_bin(self, settings: Settings) -> Any | None:
        """Reuse-or-create the single yt-dlp media-pool bin (decisions.md Q6/Q11).

        Returns `None` if no project is open (`_current_media_pool()` returns falsy).
        """
        media_pool = self._current_media_pool()
        if not media_pool:
            return None

        root = media_pool.GetRootFolder()
        for sub_folder in root.GetSubFolderList():
            if sub_folder.GetName() == settings.bin_name:
                return sub_folder

        return media_pool.AddSubFolder(root, settings.bin_name)

    def import_media(self, bin_folder: Any, paths: Sequence[Path]) -> ImportResult:
        """Import `paths` into `bin_folder`.

        Assumes `bin_folder` is not `None` — the caller already handled the
        `get_or_create_bin` guard; not re-checked here, per this repo's
        explicit-boundaries convention.
        """
        media_pool = self._current_media_pool()
        media_pool.SetCurrentFolder(bin_folder)
        imported = media_pool.ImportMedia([str(path) for path in paths])
        return ImportResult(requested=len(paths), imported=len(imported or []))


def connect() -> ResolveBridge | None:
    """Bootstrap the connection to Resolve's embedded scripting API.

    Scripts launched from Resolve's own Scripts menu run in a `__main__` that
    Resolve pre-populates with a already-connected `resolve` global (and
    `bmd`, `fusion`, `fu`) — confirmed empirically against a real Resolve
    install. `DaVinciResolveScript` itself isn't even importable in that
    context (not on `sys.path`); that module + `scriptapp("Resolve")` is only
    the documented bootstrap for scripts launched *outside* Resolve (a plain
    terminal `python`), where `RESOLVE_SCRIPT_API`/`PYTHONPATH` are set up.
    This checks the pre-populated global first and falls back to the
    external-script path for that case.

    Deliberately thin: this is the one code path that can't be exercised
    against the real API in CI. Returns `None` (never raises) when neither
    route yields a connected `resolve` app object.
    """
    main_globals = getattr(sys.modules.get("__main__"), "__dict__", {})
    resolve_app = main_globals.get("resolve")
    if resolve_app:
        return ResolveBridge(resolve_app, bmd_module=main_globals.get("bmd"))

    try:
        import DaVinciResolveScript as bmd  # type: ignore[import-not-found]
    except ImportError:
        return None

    resolve_app = bmd.scriptapp("Resolve")
    if not resolve_app:
        return None

    return ResolveBridge(resolve_app, bmd_module=bmd)
