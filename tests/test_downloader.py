from __future__ import annotations

import json
import queue
import subprocess
import time
from pathlib import Path

import pytest

from resolve_ytdlp import downloader, formats
from resolve_ytdlp.config import Settings
from resolve_ytdlp.deps import ResolvedDeps


def _settings(tmp_path: Path) -> Settings:
    return Settings(download_dir=str(tmp_path / "downloads"))


def _probe_with_entries(count: int) -> formats.ProbeResult:
    entries = tuple(
        formats.PlaylistEntry(id=f"id{i}", title=f"title{i}", url=f"https://example.com/{i}")
        for i in range(count)
    )
    return formats.ProbeResult(
        is_playlist=True, playlist_count=count, entries=entries, formats=()
    )


def _drain_until_terminal(events: queue.Queue, timeout: float = 5.0) -> list:
    items: list = []
    while True:
        item = events.get(timeout=timeout)
        items.append(item)
        if isinstance(item, downloader.TerminalEvent):
            return items


# ---- parse_progress_line ----


def test_parse_progress_line_download_event() -> None:
    payload = {
        "status": "downloading",
        "downloaded_bytes": 100,
        "total_bytes": 200,
        "tmpfilename": "/tmp/video.mp4.part",
        "filename": "/tmp/video.mp4",
        "eta": 5,
        "speed": 1234.5,
        "_percent": 50.0,
    }
    line = formats.DL_SENTINEL + json.dumps(payload)

    event = downloader.parse_progress_line(line)

    assert event is not None
    assert event.kind == "download"
    assert event.status == "downloading"
    assert event.percent == 50.0
    assert event.speed == 1234.5
    assert event.eta == 5
    assert event.downloaded_bytes == 100
    assert event.total_bytes == 200
    assert event.filename == "/tmp/video.mp4"
    assert event.tmpfilename == "/tmp/video.mp4.part"
    assert event.postprocessor is None
    assert event.playlist_index is None
    assert event.playlist_count is None
    assert event.raw == payload


def test_parse_progress_line_postprocess_event() -> None:
    payload = {"status": "started", "postprocessor": "Metadata"}
    line = formats.PP_SENTINEL + json.dumps(payload)

    event = downloader.parse_progress_line(line)

    assert event is not None
    assert event.kind == "postprocess"
    assert event.status == "started"
    assert event.postprocessor == "Metadata"


@pytest.mark.parametrize(
    "line",
    [
        "[youtube] Extracting URL: https://example.com",
        "",
        "some random chatter",
    ],
)
def test_parse_progress_line_ignores_non_matching_lines(line: str) -> None:
    assert downloader.parse_progress_line(line) is None


# ---- run_probe ----


def test_run_probe_success(tmp_path: Path, make_fake_ytdlp) -> None:
    probe_json = json.dumps({"_type": "video", "formats": []})
    ytdlp = make_fake_ytdlp(tmp_path / "bin", stdout_lines=[probe_json])
    deps = ResolvedDeps(ytdlp=ytdlp, ffmpeg=None)

    result = downloader.run_probe(deps, "https://example.com/video")

    assert result.is_playlist is False
    assert result.formats == ()


def test_run_probe_raises_on_non_zero_exit(tmp_path: Path, make_fake_ytdlp) -> None:
    ytdlp = make_fake_ytdlp(tmp_path / "bin", stderr_text="ERROR: unsupported URL", exit_code=1)
    deps = ResolvedDeps(ytdlp=ytdlp, ffmpeg=None)

    with pytest.raises(downloader.DownloaderError) as exc_info:
        downloader.run_probe(deps, "https://example.com/video")

    assert "unsupported URL" in str(exc_info.value)


def test_run_probe_timeout_propagates(tmp_path: Path, make_fake_ytdlp) -> None:
    ytdlp = make_fake_ytdlp(tmp_path / "bin", delay_before_exit=2.0)
    deps = ResolvedDeps(ytdlp=ytdlp, ffmpeg=None)

    with pytest.raises(subprocess.TimeoutExpired):
        downloader.run_probe(deps, "https://example.com/video", timeout=0.2)


# ---- DownloadRunner ----


def test_download_runner_success_sequence(tmp_path: Path, make_fake_ytdlp) -> None:
    lines = [
        formats.DL_SENTINEL
        + json.dumps(
            {"status": "downloading", "downloaded_bytes": 50, "total_bytes": 100, "_percent": 50.0}
        ),
        formats.DL_SENTINEL + json.dumps({"status": "finished"}),
        formats.PP_SENTINEL + json.dumps({"status": "finished", "postprocessor": "Metadata"}),
    ]
    ytdlp = make_fake_ytdlp(tmp_path / "bin", stdout_lines=lines, exit_code=0)
    deps = ResolvedDeps(ytdlp=ytdlp, ffmpeg=None)
    runner = downloader.DownloadRunner(deps)
    events: queue.Queue = queue.Queue()

    runner.start(_settings(tmp_path), "https://example.com/video", events)
    items = _drain_until_terminal(events)

    assert len(items) == 4
    assert items[0].kind == "download"
    assert items[0].status == "downloading"
    assert items[2].kind == "postprocess"
    assert items[-1] == downloader.TerminalEvent("done", None, None, None)


def test_download_runner_error_sequence(tmp_path: Path, make_fake_ytdlp) -> None:
    ytdlp = make_fake_ytdlp(tmp_path / "bin", stderr_text="ERROR: boom", exit_code=1)
    deps = ResolvedDeps(ytdlp=ytdlp, ffmpeg=None)
    runner = downloader.DownloadRunner(deps)
    events: queue.Queue = queue.Queue()

    runner.start(_settings(tmp_path), "https://example.com/video", events)
    items = _drain_until_terminal(events)

    assert len(items) == 1
    terminal = items[0]
    assert terminal.status == "error"
    assert terminal.message is not None
    assert "boom" in terminal.message


def test_download_runner_cancel_is_noop_when_nothing_running(tmp_path: Path) -> None:
    deps = ResolvedDeps(ytdlp=Path("/nonexistent/yt-dlp"), ffmpeg=None)
    runner = downloader.DownloadRunner(deps)

    runner.cancel()  # must not raise


def test_download_runner_cancel_terminates_and_cleans_part_file(
    tmp_path: Path, make_fake_ytdlp, monkeypatch: pytest.MonkeyPatch
) -> None:
    part_file = tmp_path / "video.mp4.part"
    part_file.write_bytes(b"partial")
    progress_payload = {
        "status": "downloading",
        "downloaded_bytes": 10,
        "total_bytes": 100,
        "tmpfilename": str(part_file),
        "_percent": 10.0,
    }
    ytdlp = make_fake_ytdlp(
        tmp_path / "bin",
        stdout_lines=[formats.DL_SENTINEL + json.dumps(progress_payload)],
        delay_before_exit=10.0,
    )
    deps = ResolvedDeps(ytdlp=ytdlp, ffmpeg=None)
    monkeypatch.setattr(downloader.DownloadRunner, "GRACE_PERIOD_SECONDS", 1.0)
    runner = downloader.DownloadRunner(deps)
    events: queue.Queue = queue.Queue()

    runner.start(_settings(tmp_path), "https://example.com/video", events)
    first = events.get(timeout=5.0)
    assert isinstance(first, downloader.ProgressEvent)
    assert first.tmpfilename == str(part_file)

    runner.cancel()
    terminal = events.get(timeout=5.0)

    assert terminal == downloader.TerminalEvent("canceled", None, None, None)
    assert not part_file.exists()


# ---- PlaylistRunner ----


def test_playlist_runner_respects_limit_and_stamps_events(
    tmp_path: Path, make_fake_ytdlp
) -> None:
    ytdlp = make_fake_ytdlp(
        tmp_path / "bin",
        stdout_lines=[formats.DL_SENTINEL + json.dumps({"status": "finished"})],
        exit_code=0,
    )
    deps = ResolvedDeps(ytdlp=ytdlp, ffmpeg=None)
    settings = _settings(tmp_path)
    settings.playlist_limit = 2
    probe = _probe_with_entries(3)
    events: queue.Queue = queue.Queue()
    runner = downloader.PlaylistRunner(deps)

    runner.start(settings, probe, events)

    terminals = []
    while len(terminals) < 2:
        item = events.get(timeout=5.0)
        if isinstance(item, downloader.TerminalEvent):
            terminals.append(item)

    assert [t.status for t in terminals] == ["done", "done"]
    assert [t.playlist_index for t in terminals] == [1, 2]
    assert all(t.playlist_count == 2 for t in terminals)

    # Nothing further should arrive for a 3rd entry.
    with pytest.raises(queue.Empty):
        events.get(timeout=1.0)


def test_playlist_runner_cancel_stops_after_current_item(
    tmp_path: Path, make_fake_ytdlp, monkeypatch: pytest.MonkeyPatch
) -> None:
    ytdlp = make_fake_ytdlp(tmp_path / "bin", delay_before_exit=10.0)
    deps = ResolvedDeps(ytdlp=ytdlp, ffmpeg=None)
    monkeypatch.setattr(downloader.DownloadRunner, "GRACE_PERIOD_SECONDS", 1.0)
    settings = _settings(tmp_path)
    settings.playlist_limit = None
    probe = _probe_with_entries(3)
    events: queue.Queue = queue.Queue()
    runner = downloader.PlaylistRunner(deps)

    runner.start(settings, probe, events)
    time.sleep(0.5)  # let the first item's subprocess actually spawn
    runner.cancel()

    terminal = events.get(timeout=5.0)
    assert terminal == downloader.TerminalEvent("canceled", None, 1, 3)

    with pytest.raises(queue.Empty):
        events.get(timeout=1.0)
