from __future__ import annotations

from pathlib import Path

import pytest

from resolve_ytdlp import deps
from resolve_ytdlp.config import Settings


def test_find_binary_returns_none_when_nothing_matches(
    tmp_home: Path, empty_path_env: None, isolate_common_locations: None
) -> None:
    assert deps.find_binary("yt-dlp", extra_paths=()) is None


def test_find_binary_override_is_used_when_valid(
    tmp_path: Path, make_fake_binary, empty_path_env: None
) -> None:
    binary = make_fake_binary(tmp_path / "custom", "yt-dlp")

    result = deps.find_binary("yt-dlp", override=str(binary))

    assert result == binary


def test_find_binary_override_expands_user(
    tmp_home: Path, make_fake_binary, empty_path_env: None
) -> None:
    binary = make_fake_binary(tmp_home / "bin", "yt-dlp")

    result = deps.find_binary("yt-dlp", override="~/bin/yt-dlp")

    assert result == binary


def test_find_binary_override_rejected_when_missing(
    tmp_path: Path, empty_path_env: None
) -> None:
    missing = tmp_path / "does-not-exist" / "yt-dlp"

    result = deps.find_binary("yt-dlp", override=str(missing))

    assert result is None


def test_find_binary_override_rejected_when_not_executable(
    tmp_path: Path, make_fake_binary, empty_path_env: None
) -> None:
    binary = make_fake_binary(tmp_path / "custom", "yt-dlp", executable=False)

    result = deps.find_binary("yt-dlp", override=str(binary))

    assert result is None


def test_find_binary_override_takes_precedence_over_path(
    tmp_path: Path, make_fake_binary, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_path = make_fake_binary(tmp_path / "path-dir", "yt-dlp")
    override_binary = make_fake_binary(tmp_path / "override-dir", "yt-dlp")
    monkeypatch.setenv("PATH", str(tmp_path / "path-dir"))

    result = deps.find_binary("yt-dlp", override=str(override_binary))

    assert result == override_binary
    assert result != on_path


def test_find_binary_falls_back_to_path(
    tmp_path: Path, make_fake_binary, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = make_fake_binary(tmp_path / "path-dir", "yt-dlp")
    monkeypatch.setenv("PATH", str(tmp_path / "path-dir"))

    result = deps.find_binary("yt-dlp")

    assert result == binary


def test_find_binary_falls_back_to_common_locations(
    tmp_path: Path, make_fake_binary, empty_path_env: None
) -> None:
    common_dir = tmp_path / "opt" / "resolve" / "bin"
    binary = make_fake_binary(common_dir, "yt-dlp")

    result = deps.find_binary("yt-dlp", extra_paths=(common_dir,))

    assert result == binary


def test_find_binary_common_locations_reflect_current_home(
    tmp_home: Path, make_fake_binary, empty_path_env: None, isolate_common_locations: None
) -> None:
    binary = make_fake_binary(tmp_home / ".local" / "bin", "yt-dlp")

    result = deps.find_binary("yt-dlp")

    assert result == binary


def test_find_binary_skips_non_executable_common_location(
    tmp_home: Path,
    make_fake_binary,
    empty_path_env: None,
    isolate_common_locations: None,
) -> None:
    common_dir = tmp_home / "common"
    make_fake_binary(common_dir, "yt-dlp", executable=False)

    result = deps.find_binary("yt-dlp", extra_paths=(common_dir,))

    assert result is None


def test_find_binary_path_takes_precedence_over_common_locations(
    tmp_path: Path, make_fake_binary, monkeypatch: pytest.MonkeyPatch
) -> None:
    on_path = make_fake_binary(tmp_path / "path-dir", "yt-dlp")
    common_dir = tmp_path / "common"
    make_fake_binary(common_dir, "yt-dlp")
    monkeypatch.setenv("PATH", str(tmp_path / "path-dir"))

    result = deps.find_binary("yt-dlp", extra_paths=(common_dir,))

    assert result == on_path


def test_discover_resolves_both_binaries_from_settings_overrides(
    tmp_path: Path, make_fake_binary, empty_path_env: None
) -> None:
    ytdlp = make_fake_binary(tmp_path / "bin", "yt-dlp")
    ffmpeg = make_fake_binary(tmp_path / "bin", "ffmpeg")
    settings = Settings(ytdlp_path=str(ytdlp), ffmpeg_path=str(ffmpeg))

    resolved = deps.discover(settings)

    assert resolved.ytdlp == ytdlp
    assert resolved.ffmpeg == ffmpeg


def test_discover_returns_none_for_unresolved_binaries(
    tmp_home: Path, empty_path_env: None, isolate_common_locations: None
) -> None:
    resolved = deps.discover(Settings())

    assert resolved.ytdlp is None
    assert resolved.ffmpeg is None


def test_preflight_ok_when_both_resolved(tmp_path: Path) -> None:
    resolved = deps.ResolvedDeps(ytdlp=tmp_path / "yt-dlp", ffmpeg=tmp_path / "ffmpeg")

    assert deps.preflight(resolved) == []


def test_preflight_reports_missing_ytdlp(tmp_path: Path) -> None:
    resolved = deps.ResolvedDeps(ytdlp=None, ffmpeg=tmp_path / "ffmpeg")

    problems = deps.preflight(resolved)

    assert len(problems) == 1
    assert "yt-dlp" in problems[0]


def test_preflight_reports_missing_ffmpeg(tmp_path: Path) -> None:
    resolved = deps.ResolvedDeps(ytdlp=tmp_path / "yt-dlp", ffmpeg=None)

    problems = deps.preflight(resolved)

    assert len(problems) == 1
    assert "ffmpeg" in problems[0]


def test_preflight_reports_both_missing(tmp_path: Path) -> None:
    resolved = deps.ResolvedDeps(ytdlp=None, ffmpeg=None)

    problems = deps.preflight(resolved)

    assert len(problems) == 2
