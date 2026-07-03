"""Cross-platform discovery of the yt-dlp and ffmpeg binaries, plus preflight checks.

Stdlib only — this module runs inside Resolve's embedded Python interpreter.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from resolve_ytdlp.config import Settings

def common_locations() -> tuple[Path, ...]:
    """Install locations not always on PATH when Resolve is launched from a GUI
    (notably macOS, which does not inherit the shell's PATH).

    Computed per-call (rather than as a module constant) so it reflects the
    current ``$HOME`` — this keeps it monkeypatch-friendly in tests.
    """
    return (
        Path("/usr/bin"),
        Path("/usr/local/bin"),
        Path("/opt/homebrew/bin"),
        Path.home() / ".local" / "bin",
        Path("/opt/resolve/bin"),
    )


def _is_executable_file(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def find_binary(
    name: str,
    override: str | None = None,
    extra_paths: Sequence[Path] | None = None,
) -> Path | None:
    """Resolve a binary by name, preferring an explicit override.

    Resolution order:
    1. ``override`` (``~`` expanded); used only if it exists and is executable.
    2. ``PATH`` via ``shutil.which``.
    3. ``extra_paths`` followed by :func:`common_locations`, each checked for
       ``<dir>/<name>``.

    Returns ``None`` if no candidate is found or executable.
    """
    if override:
        candidate = Path(override).expanduser()
        if _is_executable_file(candidate):
            return candidate
        return None

    found = shutil.which(name)
    if found:
        return Path(found)

    search_dirs = (*(extra_paths or ()), *common_locations())
    for directory in search_dirs:
        candidate = directory / name
        if _is_executable_file(candidate):
            return candidate

    return None


@dataclass
class ResolvedDeps:
    ytdlp: Path | None
    ffmpeg: Path | None


def discover(settings: Settings) -> ResolvedDeps:
    """Resolve yt-dlp and ffmpeg paths from settings overrides, PATH, and common locations."""
    return ResolvedDeps(
        ytdlp=find_binary("yt-dlp", settings.ytdlp_path),
        ffmpeg=find_binary("ffmpeg", settings.ffmpeg_path),
    )


def preflight(deps: ResolvedDeps) -> list[str]:
    """Return human-readable problems for missing dependencies; empty list means OK."""
    problems: list[str] = []
    if deps.ytdlp is None:
        problems.append(
            "yt-dlp was not found. Install it or set an explicit path in settings."
        )
    if deps.ffmpeg is None:
        problems.append(
            "ffmpeg was not found. Install it or set an explicit path in settings "
            "(required for audio extraction and video+audio merging)."
        )
    return problems
