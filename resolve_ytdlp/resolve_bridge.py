"""Thin wrapper around DaVinci Resolve's scripting API: bin reuse/create, media import, bootstrap.

The single bootstrap/import site for `DaVinciResolveScript` in this codebase
(decisions.md Q9: "all Resolve API behind one interface") — no other module
should import it directly. Imports cleanly under plain `pytest` because the
import is lazy, inside `connect()`, not at module level.
"""

from __future__ import annotations

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

    def get_or_create_bin(self, settings: Settings) -> Any | None:
        """Reuse-or-create the single yt-dlp media-pool bin (decisions.md Q6/Q11).

        Returns `None` if no project is open (`GetMediaPool()` returns falsy).
        """
        media_pool = self._resolve_app.GetMediaPool()
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
        media_pool = self._resolve_app.GetMediaPool()
        media_pool.SetCurrentFolder(bin_folder)
        imported = media_pool.ImportMedia([str(path) for path in paths])
        return ImportResult(requested=len(paths), imported=len(imported or []))


def connect() -> ResolveBridge | None:
    """Bootstrap the connection to Resolve's embedded scripting API.

    Deliberately thin: this is the one code path that can't be exercised
    against the real API in CI. Returns `None` (never raises) when
    `DaVinciResolveScript` can't be imported or `scriptapp("Resolve")` fails.
    """
    try:
        import DaVinciResolveScript as bmd  # type: ignore[import-not-found]
    except ImportError:
        return None

    resolve_app = bmd.scriptapp("Resolve")
    if not resolve_app:
        return None

    return ResolveBridge(resolve_app, bmd_module=bmd)
