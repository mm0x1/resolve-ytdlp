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


# ---- parse_final_filename_line ----


def test_parse_final_filename_line_matches_sentinel() -> None:
    line = formats.DONE_SENTINEL + json.dumps("/videos/Some Title [abc123].mp4")

    assert downloader.parse_final_filename_line(line) == "/videos/Some Title [abc123].mp4"


@pytest.mark.parametrize(
    "line",
    [
        "[youtube] Extracting URL: https://example.com",
        "",
        "some random chatter",
    ],
)
def test_parse_final_filename_line_ignores_non_matching_lines(line: str) -> None:
    assert downloader.parse_final_filename_line(line) is None


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


def test_download_runner_terminal_event_carries_final_filename(
    tmp_path: Path, make_fake_ytdlp
) -> None:
    """The final path (`--print after_move:...`) may differ from the last
    downloaded stream's own `filename` (e.g. a separate video+audio download
    merged by ffmpeg) — this is what auto-import relies on to avoid importing
    a deleted intermediate stream file (see `app.ImportCoordinator`)."""
    lines = [
        formats.DL_SENTINEL + json.dumps({"status": "finished", "filename": "video.f298.mp4"}),
        formats.DL_SENTINEL + json.dumps({"status": "finished", "filename": "audio.f140.m4a"}),
        formats.PP_SENTINEL + json.dumps({"status": "finished", "postprocessor": "Merger"}),
        formats.DONE_SENTINEL + json.dumps("/videos/Merged Title [abc123].mp4"),
    ]
    ytdlp = make_fake_ytdlp(tmp_path / "bin", stdout_lines=lines, exit_code=0)
    deps = ResolvedDeps(ytdlp=ytdlp, ffmpeg=None)
    runner = downloader.DownloadRunner(deps)
    events: queue.Queue = queue.Queue()

    runner.start(_settings(tmp_path), "https://example.com/video", events)
    items = _drain_until_terminal(events)

    terminal = items[-1]
    assert terminal.status == "done"
    assert terminal.filename == "/videos/Merged Title [abc123].mp4"


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


def test_download_runner_decodes_non_ascii_output(tmp_path: Path, make_fake_ytdlp) -> None:
    """yt-dlp output is decoded as UTF-8 regardless of the interpreter's locale.

    Regression guard for the "no audio" bug: Resolve's embedded Python reports
    an ASCII (`ANSI_X3.4-1968`) preferred encoding, so a title with a non-ASCII
    character (e.g. an emoji) used to raise `UnicodeDecodeError` in the reader
    thread and silently strand the download. `ensure_ascii=False` forces the
    fake to emit the raw multibyte bytes (not `\\uXXXX` escapes) so the decode
    path is actually exercised.
    """
    final_path = "/videos/The Jury Decides 🫣 Yellowjackets [nLYCW50gewk].mp4"
    lines = [
        formats.DL_SENTINEL
        + json.dumps({"status": "finished", "filename": "clip 🫣.f137.mp4"}, ensure_ascii=False),
        formats.DONE_SENTINEL + json.dumps(final_path, ensure_ascii=False),
    ]
    ytdlp = make_fake_ytdlp(tmp_path / "bin", stdout_lines=lines, exit_code=0)
    deps = ResolvedDeps(ytdlp=ytdlp, ffmpeg=None)
    runner = downloader.DownloadRunner(deps)
    events: queue.Queue = queue.Queue()

    runner.start(_settings(tmp_path), "https://example.com/video", events)
    items = _drain_until_terminal(events)

    terminal = items[-1]
    assert terminal.status == "done"
    assert terminal.filename == final_path


def test_download_runner_reader_crash_emits_terminal_error(
    tmp_path: Path, make_fake_ytdlp
) -> None:
    """A crash in the output reader thread must surface as an error terminal
    event, never a silent hang with an unreaped (zombie) subprocess.

    A sentinel-prefixed line with malformed JSON makes `parse_progress_line`'s
    `json.loads` raise inside the reader thread — the same class of failure
    (originally a `UnicodeDecodeError`) that produced an invisible, unlogged
    hang. The guard should terminate the child and emit `"error"`.
    """
    lines = [formats.DL_SENTINEL + "{not-valid-json"]
    ytdlp = make_fake_ytdlp(tmp_path / "bin", stdout_lines=lines, exit_code=0)
    deps = ResolvedDeps(ytdlp=ytdlp, ffmpeg=None)
    runner = downloader.DownloadRunner(deps)
    events: queue.Queue = queue.Queue()

    runner.start(_settings(tmp_path), "https://example.com/video", events)
    items = _drain_until_terminal(events)

    terminal = items[-1]
    assert terminal.status == "error"
    assert terminal.message is not None
    assert "Failed reading yt-dlp output" in terminal.message


# ---- transcode_audio_for_resolve (Resolve-on-Linux audio compat) ----


def _make_fake_ffmpeg(directory: Path, *, exit_code: int = 0) -> Path:
    """A fake ffmpeg that writes marker bytes to its output (last argv) path.

    Mirrors the real transcode argv, whose final positional argument is the
    destination file. Lets the transcode's replace-in-place path be exercised
    without a real encode.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "ffmpeg"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        f"if {exit_code!r} == 0:\n"
        "    open(sys.argv[-1], 'wb').write(b'TRANSCODED')\n"
        f"sys.exit({exit_code!r})\n",
        encoding="utf-8",
    )
    path.chmod(0o755)
    return path


def test_transcode_audio_replaces_file_in_place(tmp_path: Path) -> None:
    source = tmp_path / "Clip [id].mp4"
    source.write_bytes(b"ORIGINAL")
    ffmpeg = _make_fake_ffmpeg(tmp_path / "bin")
    deps = ResolvedDeps(ytdlp=None, ffmpeg=ffmpeg)

    result = downloader.transcode_audio_for_resolve(deps, str(source))

    assert result == str(source)
    assert source.read_bytes() == b"TRANSCODED"
    # No stray temp file left behind.
    assert list(tmp_path.glob("*.rytdlp-compat*")) == []


def test_transcode_audio_noop_without_ffmpeg(tmp_path: Path) -> None:
    source = tmp_path / "Clip [id].mp4"
    source.write_bytes(b"ORIGINAL")
    deps = ResolvedDeps(ytdlp=None, ffmpeg=None)

    result = downloader.transcode_audio_for_resolve(deps, str(source))

    assert result == str(source)
    assert source.read_bytes() == b"ORIGINAL"


@pytest.mark.parametrize("name", ["Track [id].mp3", "Clip [id].webm"])
def test_transcode_audio_skips_incompatible_containers(tmp_path: Path, name: str) -> None:
    source = tmp_path / name
    source.write_bytes(b"ORIGINAL")
    ffmpeg = _make_fake_ffmpeg(tmp_path / "bin")
    deps = ResolvedDeps(ytdlp=None, ffmpeg=ffmpeg)

    result = downloader.transcode_audio_for_resolve(deps, str(source))

    assert result == str(source)
    assert source.read_bytes() == b"ORIGINAL"


def test_transcode_audio_keeps_original_when_ffmpeg_fails(tmp_path: Path) -> None:
    source = tmp_path / "Clip [id].mp4"
    source.write_bytes(b"ORIGINAL")
    ffmpeg = _make_fake_ffmpeg(tmp_path / "bin", exit_code=1)
    deps = ResolvedDeps(ytdlp=None, ffmpeg=ffmpeg)

    result = downloader.transcode_audio_for_resolve(deps, str(source))

    assert result == str(source)
    assert source.read_bytes() == b"ORIGINAL"
    assert list(tmp_path.glob("*.rytdlp-compat*")) == []


def test_download_runner_applies_audio_compat_on_done(
    tmp_path: Path, make_fake_ytdlp
) -> None:
    final = tmp_path / "downloads" / "Clip [id].mp4"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b"ORIGINAL")
    ytdlp = make_fake_ytdlp(
        tmp_path / "bin",
        stdout_lines=[formats.DONE_SENTINEL + json.dumps(str(final))],
        exit_code=0,
    )
    ffmpeg = _make_fake_ffmpeg(tmp_path / "ffbin")
    deps = ResolvedDeps(ytdlp=ytdlp, ffmpeg=ffmpeg)
    settings = Settings(download_dir=str(tmp_path / "downloads"), resolve_audio_compat=True)
    runner = downloader.DownloadRunner(deps)
    events: queue.Queue = queue.Queue()

    runner.start(settings, "https://example.com/video", events)
    terminal = _drain_until_terminal(events)[-1]

    assert terminal.status == "done"
    assert terminal.filename == str(final)
    assert final.read_bytes() == b"TRANSCODED"


def test_download_runner_skips_audio_compat_when_disabled(
    tmp_path: Path, make_fake_ytdlp
) -> None:
    final = tmp_path / "downloads" / "Clip [id].mp4"
    final.parent.mkdir(parents=True, exist_ok=True)
    final.write_bytes(b"ORIGINAL")
    ytdlp = make_fake_ytdlp(
        tmp_path / "bin",
        stdout_lines=[formats.DONE_SENTINEL + json.dumps(str(final))],
        exit_code=0,
    )
    ffmpeg = _make_fake_ffmpeg(tmp_path / "ffbin")
    deps = ResolvedDeps(ytdlp=ytdlp, ffmpeg=ffmpeg)
    settings = Settings(download_dir=str(tmp_path / "downloads"), resolve_audio_compat=False)
    runner = downloader.DownloadRunner(deps)
    events: queue.Queue = queue.Queue()

    runner.start(settings, "https://example.com/video", events)
    terminal = _drain_until_terminal(events)[-1]

    assert terminal.status == "done"
    assert final.read_bytes() == b"ORIGINAL"


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
