from __future__ import annotations

import json
from pathlib import Path

from resolve_ytdlp import config


def test_config_dir_macos(tmp_home: Path, set_platform) -> None:
    set_platform("darwin")
    assert config.config_dir() == tmp_home / "Library" / "Application Support" / "resolve-ytdlp"


def test_config_dir_linux(tmp_home: Path, set_platform) -> None:
    set_platform("linux")
    assert config.config_dir() == tmp_home / ".config" / "resolve-ytdlp"


def test_settings_path_is_under_config_dir(tmp_home: Path, set_platform) -> None:
    set_platform("linux")
    assert config.settings_path() == config.config_dir() / "settings.json"


def test_default_download_dir_macos(tmp_home: Path, set_platform) -> None:
    set_platform("darwin")
    assert config.default_download_dir() == tmp_home / "Movies" / "Resolve-ytdlp"


def test_default_download_dir_linux(tmp_home: Path, set_platform) -> None:
    set_platform("linux")
    assert config.default_download_dir() == tmp_home / "Videos" / "Resolve-ytdlp"


def test_settings_defaults(tmp_home: Path, set_platform) -> None:
    set_platform("linux")
    settings = config.Settings()
    assert settings.download_dir == str(config.default_download_dir())
    assert settings.ytdlp_path is None
    assert settings.ffmpeg_path is None
    assert settings.auto_import is True
    assert settings.bin_name == "yt-dlp"
    assert settings.embed_metadata is True
    assert settings.embed_thumbnail is True
    assert settings.write_subs is False
    assert settings.playlist_limit is None
    assert settings.resolve_audio_compat is True  # default on for Linux


def test_settings_resolve_audio_compat_defaults_off_on_macos(tmp_home: Path, set_platform) -> None:
    set_platform("darwin")
    assert config.Settings().resolve_audio_compat is False


def test_load_settings_missing_file_returns_defaults(tmp_home: Path, set_platform) -> None:
    set_platform("linux")
    assert not config.settings_path().exists()
    loaded = config.load_settings()
    assert loaded == config.Settings()


def test_load_settings_corrupt_json_returns_defaults(tmp_home: Path, set_platform) -> None:
    set_platform("linux")
    path = config.settings_path()
    path.parent.mkdir(parents=True)
    path.write_text("{not valid json", encoding="utf-8")

    loaded = config.load_settings()

    assert loaded == config.Settings()


def test_load_settings_non_dict_json_returns_defaults(tmp_home: Path, set_platform) -> None:
    set_platform("linux")
    path = config.settings_path()
    path.parent.mkdir(parents=True)
    path.write_text("[1, 2, 3]", encoding="utf-8")

    loaded = config.load_settings()

    assert loaded == config.Settings()


def test_load_settings_unknown_keys_are_ignored(tmp_home: Path, set_platform) -> None:
    set_platform("linux")
    path = config.settings_path()
    path.parent.mkdir(parents=True)
    payload = {"auto_import": False, "totally_unknown_key": "should be dropped"}
    path.write_text(json.dumps(payload), encoding="utf-8")

    loaded = config.load_settings()

    assert loaded.auto_import is False
    assert not hasattr(loaded, "totally_unknown_key")


def test_save_then_load_round_trip(tmp_home: Path, set_platform) -> None:
    set_platform("linux")
    original = config.Settings(
        download_dir=str(tmp_home / "custom-downloads"),
        ytdlp_path="/usr/bin/yt-dlp",
        auto_import=False,
        sub_langs="en,fr",
        playlist_limit=5,
    )

    config.save_settings(original)
    loaded = config.load_settings()

    assert loaded == original


def test_save_settings_creates_config_dir(tmp_home: Path, set_platform) -> None:
    set_platform("linux")
    assert not config.config_dir().exists()

    config.save_settings(config.Settings(download_dir=str(tmp_home / "downloads")))

    assert config.config_dir().is_dir()
    assert config.settings_path().is_file()


def test_download_dir_not_created_on_load(tmp_home: Path, set_platform) -> None:
    set_platform("linux")
    download_dir = tmp_home / "Videos" / "Resolve-ytdlp"

    config.load_settings()

    assert not download_dir.exists()


def test_save_settings_creates_download_dir_lazily(tmp_home: Path, set_platform) -> None:
    set_platform("linux")
    settings = config.Settings(download_dir=str(tmp_home / "Videos" / "Resolve-ytdlp"))
    assert not Path(settings.download_dir).exists()

    config.save_settings(settings)

    assert Path(settings.download_dir).is_dir()


def test_save_settings_writes_atomically_no_leftover_tmp_files(
    tmp_home: Path, set_platform
) -> None:
    set_platform("linux")
    config.save_settings(config.Settings(download_dir=str(tmp_home / "downloads")))

    leftovers = list(config.config_dir().glob("*.tmp"))
    assert leftovers == []


def test_save_settings_overwrites_existing_file(tmp_home: Path, set_platform) -> None:
    set_platform("linux")
    config.save_settings(config.Settings(auto_import=True, download_dir=str(tmp_home / "d")))
    config.save_settings(config.Settings(auto_import=False, download_dir=str(tmp_home / "d")))

    loaded = config.load_settings()

    assert loaded.auto_import is False
