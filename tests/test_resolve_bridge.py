from __future__ import annotations

import sys

import pytest

from resolve_ytdlp import resolve_bridge
from resolve_ytdlp.config import Settings


def test_get_or_create_bin_creates_new_bin_when_none_exists(make_fake_resolve_app) -> None:
    app = make_fake_resolve_app()
    bridge = resolve_bridge.ResolveBridge(app)
    settings = Settings(bin_name="yt-dlp")

    bin_folder = bridge.get_or_create_bin(settings)

    assert bin_folder is not None
    assert bin_folder.GetName() == "yt-dlp"
    assert app.media_pool.add_subfolder_calls == 1
    assert bin_folder in app.media_pool.root.GetSubFolderList()


def test_get_or_create_bin_reuses_existing_bin_without_creating(make_fake_resolve_app) -> None:
    app = make_fake_resolve_app(existing_bins=["yt-dlp"])
    bridge = resolve_bridge.ResolveBridge(app)
    settings = Settings(bin_name="yt-dlp")
    existing = app.media_pool.root.GetSubFolderList()[0]

    bin_folder = bridge.get_or_create_bin(settings)

    assert bin_folder is existing
    assert app.media_pool.add_subfolder_calls == 0


def test_get_or_create_bin_returns_none_when_no_project_open(make_fake_resolve_app) -> None:
    app = make_fake_resolve_app(no_project=True)
    bridge = resolve_bridge.ResolveBridge(app)

    assert bridge.get_or_create_bin(Settings()) is None


def test_get_or_create_bin_returns_none_when_add_subfolder_fails(make_fake_resolve_app) -> None:
    app = make_fake_resolve_app(add_subfolder_fails=True)
    bridge = resolve_bridge.ResolveBridge(app)

    assert bridge.get_or_create_bin(Settings()) is None


def test_import_media_calls_set_current_folder_before_import(
    make_fake_resolve_app, tmp_path
) -> None:
    app = make_fake_resolve_app()
    media_pool = app.media_pool
    bridge = resolve_bridge.ResolveBridge(app)
    bin_folder = bridge.get_or_create_bin(Settings(bin_name="yt-dlp"))
    path = tmp_path / "video.mp4"

    bridge.import_media(bin_folder, [path])

    assert media_pool.call_log == ["SetCurrentFolder", "ImportMedia"]
    assert media_pool.current_folder is bin_folder


def test_import_media_full_success(make_fake_resolve_app, tmp_path) -> None:
    app = make_fake_resolve_app()
    bridge = resolve_bridge.ResolveBridge(app)
    bin_folder = bridge.get_or_create_bin(Settings(bin_name="yt-dlp"))
    paths = [tmp_path / "a.mp4", tmp_path / "b.mp4"]

    result = bridge.import_media(bin_folder, paths)

    assert result.requested == 2
    assert result.imported == 2
    assert result.ok is True


def test_import_media_partial_failure(make_fake_resolve_app, tmp_path) -> None:
    app = make_fake_resolve_app(import_media_missing=1)
    bridge = resolve_bridge.ResolveBridge(app)
    bin_folder = bridge.get_or_create_bin(Settings(bin_name="yt-dlp"))
    paths = [tmp_path / "a.mp4", tmp_path / "b.mp4"]

    result = bridge.import_media(bin_folder, paths)

    assert result.requested == 2
    assert result.imported == 1
    assert result.ok is False


def test_import_media_total_failure(make_fake_resolve_app, tmp_path) -> None:
    app = make_fake_resolve_app(import_media_missing=2)
    bridge = resolve_bridge.ResolveBridge(app)
    bin_folder = bridge.get_or_create_bin(Settings(bin_name="yt-dlp"))
    paths = [tmp_path / "a.mp4", tmp_path / "b.mp4"]

    result = bridge.import_media(bin_folder, paths)

    assert result.requested == 2
    assert result.imported == 0
    assert result.ok is False


def test_import_media_empty_paths(make_fake_resolve_app) -> None:
    app = make_fake_resolve_app()
    bridge = resolve_bridge.ResolveBridge(app)
    bin_folder = bridge.get_or_create_bin(Settings(bin_name="yt-dlp"))

    result = bridge.import_media(bin_folder, [])

    assert result.requested == 0
    assert result.imported == 0
    assert result.ok is True


def test_app_property_exposes_raw_connected_object(make_fake_resolve_app) -> None:
    app = make_fake_resolve_app()
    bridge = resolve_bridge.ResolveBridge(app)

    assert bridge.app is app


def test_connect_returns_none_when_module_not_importable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "DaVinciResolveScript", None)

    assert resolve_bridge.connect() is None


def test_bmd_property_exposes_raw_module(make_fake_resolve_app) -> None:
    app = make_fake_resolve_app()
    sentinel = object()
    bridge = resolve_bridge.ResolveBridge(app, bmd_module=sentinel)

    assert bridge.bmd is sentinel


def test_bmd_property_defaults_to_none(make_fake_resolve_app) -> None:
    app = make_fake_resolve_app()
    bridge = resolve_bridge.ResolveBridge(app)

    assert bridge.bmd is None
