"""Install the "Download from URL (yt-dlp)" Utility script into DaVinci Resolve.

Runs on the user's regular system Python at install time (not Resolve's
embedded interpreter, and not at runtime) — kept stdlib-only anyway for
simplicity and consistency with the rest of this repo.

Two install modes, mirroring decisions.md Q10:
- ``symlink`` (dev): symlinks the entry script and the ``resolve_ytdlp``
  package into Resolve's per-OS Scripts/Utility directory, so edits to this
  checkout are picked up live.
- ``copy`` (release): copies both into that directory as a standalone tree,
  independent of this checkout.

Both land side-by-side directly in the target directory (not in a
``scripts/`` subdirectory) because Resolve's Scripts > Utility menu only
picks up script files placed directly in that directory — this is also why
``scripts/download_from_url.py`` adds *its own* directory (not its parent) to
``sys.path`` to find its ``resolve_ytdlp`` sibling.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Literal

MODES = ("symlink", "copy")


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _linux_resolve_data_dir() -> Path:
    """The directory DaVinci Resolve itself creates for a per-user Linux install.

    A function (not a module constant) so it re-reads ``Path.home()`` at call
    time, mirroring ``deps.common_locations()``'s monkeypatch-friendly style.
    """
    return Path.home() / ".local" / "share" / "DaVinciResolve"


def _linux_system_root() -> Path:
    """Fallback root for a system-wide Linux install (decisions.md Q10)."""
    return Path("/opt/resolve")


def resolve_target_dir() -> Path:
    """Per-OS Scripts/Utility directory Resolve scans for Utility scripts.

    macOS: ``~/Library/Application Support/Blackmagic Design/DaVinci Resolve/
    Fusion/Scripts/Utility``. Linux: ``~/.local/share/DaVinciResolve/Fusion/
    Scripts/Utility``, falling back to ``/opt/resolve/Fusion/Scripts/Utility``
    if the per-user Resolve data directory doesn't exist (system-wide install).
    """
    if _is_macos():
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "Blackmagic Design"
            / "DaVinci Resolve"
            / "Fusion"
            / "Scripts"
            / "Utility"
        )

    primary_root = _linux_resolve_data_dir()
    linux_root = primary_root if primary_root.exists() else _linux_system_root()
    return linux_root / "Fusion" / "Scripts" / "Utility"


def _repo_root() -> Path:
    """This checkout's root directory (install.py lives at the repo root)."""
    return Path(__file__).resolve().parent


def _is_git_checkout() -> bool:
    return (_repo_root() / ".git").exists()


def default_mode() -> Literal["symlink", "copy"]:
    """Symlink if run from inside a git checkout (dev), copy otherwise (release)."""
    return "symlink" if _is_git_checkout() else "copy"


def _remove_existing(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def install(mode: Literal["symlink", "copy"], *, target_dir: Path | None = None) -> Path:
    """Install the entry script + ``resolve_ytdlp`` package into ``target_dir``.

    ``target_dir`` defaults to :func:`resolve_target_dir`; overridable for
    tests and custom Resolve install layouts. Idempotent: re-running replaces
    whatever was previously installed at the destination.
    """
    if mode not in MODES:
        raise ValueError(f"Unknown install mode: {mode!r}")

    target = target_dir if target_dir is not None else resolve_target_dir()
    target.mkdir(parents=True, exist_ok=True)

    root = _repo_root()
    entry_script = root / "scripts" / "download_from_url.py"
    package_dir = root / "resolve_ytdlp"

    dest_script = target / entry_script.name
    dest_package = target / package_dir.name

    _remove_existing(dest_script)
    _remove_existing(dest_package)

    if mode == "symlink":
        dest_script.symlink_to(entry_script)
        dest_package.symlink_to(package_dir, target_is_directory=True)
    else:
        shutil.copy2(entry_script, dest_script)
        shutil.copytree(package_dir, dest_package)

    return target


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode",
        choices=MODES,
        default=None,
        help="Install mode. Defaults to 'symlink' inside a git checkout, 'copy' otherwise.",
    )
    parser.add_argument(
        "--target-dir",
        type=Path,
        default=None,
        help="Override the destination directory (defaults to Resolve's Scripts/Utility dir).",
    )
    args = parser.parse_args(argv)

    mode = args.mode or default_mode()
    target = install(mode, target_dir=args.target_dir)
    print(f"Installed ({mode}) into {target}")


if __name__ == "__main__":
    main()
