"""Shared pytest fixtures: tmp config/home dir, fake-binary factory, platform helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from resolve_ytdlp import deps as deps_module


@pytest.fixture
def tmp_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point $HOME (and Path.home()) at an isolated temp directory."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    return home


@pytest.fixture
def set_platform(monkeypatch: pytest.MonkeyPatch):
    """Factory fixture: set_platform("darwin") / set_platform("linux") for the test."""

    def _set(platform_name: str) -> None:
        monkeypatch.setattr(sys, "platform", platform_name)

    return _set


@pytest.fixture
def make_fake_binary(tmp_path: Path):
    """Factory fixture that writes an executable (or non-executable) stub file.

    Usage: ``make_fake_binary(some_dir, "yt-dlp")`` -> Path, executable by default.
    Pass ``executable=False`` to create a non-executable candidate for negative tests.
    """

    def _make(directory: Path, name: str, *, executable: bool = True) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        path.chmod(0o755 if executable else 0o644)
        return path

    return _make


@pytest.fixture
def empty_path_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clear $PATH so shutil.which finds nothing, isolating common-location search."""
    monkeypatch.setenv("PATH", "")


@pytest.fixture
def isolate_common_locations(tmp_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Restrict deps.common_locations() to a single dir under the isolated tmp_home.

    Without this, tests run on a dev machine that actually has yt-dlp/ffmpeg
    installed under a real common location (e.g. /usr/bin) would spuriously
    "find" those binaries instead of exercising the isolated fixture setup.
    """
    monkeypatch.setattr(
        deps_module, "common_locations", lambda: (tmp_home / ".local" / "bin",)
    )
