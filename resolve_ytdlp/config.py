"""Typed settings for resolve-ytdlp: per-OS paths, guarded JSON load/save.

Stdlib only — this module runs inside Resolve's embedded Python interpreter.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any

APP_NAME = "resolve-ytdlp"
SETTINGS_FILENAME = "settings.json"


def _is_macos() -> bool:
    return sys.platform == "darwin"


def config_dir() -> Path:
    """Per-OS directory for settings and logs.

    macOS: ``~/Library/Application Support/resolve-ytdlp``
    Linux: ``~/.config/resolve-ytdlp``
    """
    if _is_macos():
        return Path.home() / "Library" / "Application Support" / APP_NAME
    return Path.home() / ".config" / APP_NAME


def settings_path() -> Path:
    return config_dir() / SETTINGS_FILENAME


def default_download_dir() -> Path:
    """Per-OS default download destination.

    macOS: ``~/Movies/Resolve-ytdlp``
    Linux: ``~/Videos/Resolve-ytdlp``
    """
    if _is_macos():
        return Path.home() / "Movies" / "Resolve-ytdlp"
    return Path.home() / "Videos" / "Resolve-ytdlp"


@dataclass
class Settings:
    download_dir: str = field(default_factory=lambda: str(default_download_dir()))
    ytdlp_path: str | None = None
    ffmpeg_path: str | None = None
    auto_import: bool = True
    bin_name: str = "yt-dlp"
    format_preset: str = "best_mp4"
    custom_format: str | None = None
    embed_metadata: bool = True
    embed_thumbnail: bool = True
    write_subs: bool = False
    sub_langs: str = "en"
    playlist_limit: int | None = None


def load_settings() -> Settings:
    """Load settings from disk, guarded against a missing or corrupt file.

    Unknown keys in the JSON are ignored. Any failure to read or parse the
    file falls back to defaults rather than raising.
    """
    path = settings_path()
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return Settings()

    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError:
        return Settings()

    if not isinstance(data, dict):
        return Settings()

    known_fields = {f.name for f in fields(Settings)}
    kwargs = {key: value for key, value in data.items() if key in known_fields}
    try:
        return Settings(**kwargs)
    except TypeError:
        return Settings()


def save_settings(settings: Settings) -> None:
    """Persist settings as JSON, creating the config dir and writing atomically.

    Also lazily creates the configured download directory, mirroring the
    config dir's lazy creation — neither exists until the first save.
    """
    directory = config_dir()
    directory.mkdir(parents=True, exist_ok=True)
    Path(settings.download_dir).mkdir(parents=True, exist_ok=True)

    payload = json.dumps(asdict(settings), indent=2, sort_keys=True)

    fd, tmp_name = tempfile.mkstemp(dir=directory, prefix=f".{SETTINGS_FILENAME}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp_file:
            tmp_file.write(payload)
        os.replace(tmp_name, settings_path())
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
