"""Run yt-dlp as a subprocess, parse its progress output, and drive playlist downloads.

Executes what `formats` builds: no argv-construction or JSON-parsing logic lives
here. Progress/completion is reported on a plain, thread-safe `queue.Queue` —
this module has no notion of a GUI or Resolve; that's `resolve_bridge`/`gui` (PR-3/4).

Stdlib only — this module runs inside Resolve's embedded Python interpreter.
"""

from __future__ import annotations

import json
import os
import queue
import signal
import subprocess
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from resolve_ytdlp import formats
from resolve_ytdlp.config import Settings
from resolve_ytdlp.deps import ResolvedDeps


@dataclass(frozen=True)
class ProgressEvent:
    kind: Literal["download", "postprocess"]
    status: str
    percent: float | None
    speed: float | None
    eta: int | None
    downloaded_bytes: int | None
    total_bytes: int | None
    filename: str | None
    tmpfilename: str | None
    postprocessor: str | None
    playlist_index: int | None
    playlist_count: int | None
    raw: dict[str, Any]


@dataclass(frozen=True)
class TerminalEvent:
    status: Literal["done", "error", "canceled"]
    message: str | None
    playlist_index: int | None
    playlist_count: int | None
    #: The final downloaded file's path (post-merge/post-postprocessing), from
    #: yt-dlp's `--print after_move:...` hook. Only ever set when `status ==
    #: "done"`.
    filename: str | None = None


class DownloaderError(Exception):
    """Raised when a blocking yt-dlp invocation (currently just `run_probe`) fails."""


def parse_progress_line(line: str) -> ProgressEvent | None:
    """Parse one line of yt-dlp stdout into a `ProgressEvent`.

    Matches `formats.DL_SENTINEL`/`formats.PP_SENTINEL` (must stay in sync with
    the sentinels `formats.build_download_argv` embeds in its `--progress-template`
    args). Returns `None` for any non-matching line — ordinary yt-dlp chatter
    (e.g. `[youtube] Extracting URL: ...`) is expected and silently ignored, not
    an error.
    """
    line = line.rstrip("\n")

    if line.startswith(formats.DL_SENTINEL):
        kind: Literal["download", "postprocess"] = "download"
        raw_json = line[len(formats.DL_SENTINEL) :]
    elif line.startswith(formats.PP_SENTINEL):
        kind = "postprocess"
        raw_json = line[len(formats.PP_SENTINEL) :]
    else:
        return None

    data: dict[str, Any] = json.loads(raw_json)

    return ProgressEvent(
        kind=kind,
        status=data.get("status", ""),
        percent=data.get("_percent"),
        speed=data.get("speed"),
        eta=data.get("eta"),
        downloaded_bytes=data.get("downloaded_bytes"),
        total_bytes=data.get("total_bytes"),
        filename=data.get("filename"),
        tmpfilename=data.get("tmpfilename"),
        postprocessor=data.get("postprocessor"),
        playlist_index=None,
        playlist_count=None,
        raw=data,
    )


def parse_final_filename_line(line: str) -> str | None:
    """Parse one line of yt-dlp stdout for the `--print after_move:...` hook.

    Matches `formats.DONE_SENTINEL`. Returns `None` for any non-matching
    line — same "ignore ordinary chatter" contract as `parse_progress_line`.
    Unlike the DL/PP sentinels, the payload here is a bare JSON string (the
    final file path), not a progress dict, so this doesn't return a
    `ProgressEvent`.
    """
    line = line.rstrip("\n")
    if not line.startswith(formats.DONE_SENTINEL):
        return None
    return json.loads(line[len(formats.DONE_SENTINEL) :])


#: Container suffixes that can hold a FLAC audio stream (so the Resolve audio
#: compat transcode is valid). Notably excludes `.webm` (Opus/Vorbis only) and
#: audio-only outputs like `.mp3`/`.m4a` (which need no video-side fix).
RESOLVE_AUDIO_COMPAT_SUFFIXES = frozenset({".mp4", ".mkv", ".mov"})


def transcode_audio_for_resolve(deps: ResolvedDeps, path: str) -> str:
    """Re-encode a finished download's audio to FLAC in place, for Resolve on Linux.

    Resolve on Linux can't decode AAC or Opus (proprietary codec licensing) and
    imports such files with silent audio; FLAC is lossless and confirmed
    decodable there. Copies the video stream untouched and re-encodes only the
    audio, writing a sibling temp file then atomically replacing the original —
    so the returned path (and thus auto-import) is unchanged.

    Best-effort by design: returns ``path`` unchanged when ffmpeg is
    unavailable, the container can't hold FLAC (e.g. `.webm`, or an audio-only
    output), the source is missing, or the transcode fails — the compat step
    can never turn a successful download into a failed one.
    """
    if deps.ffmpeg is None:
        return path

    source = Path(path)
    if source.suffix.lower() not in RESOLVE_AUDIO_COMPAT_SUFFIXES or not source.exists():
        return path

    dest = source.with_name(f"{source.stem}.rytdlp-compat{source.suffix}")
    argv = formats.build_audio_transcode_argv(deps, source, dest)
    try:
        result = subprocess.run(
            argv, capture_output=True, encoding="utf-8", errors="replace", check=False
        )
    except OSError:
        dest.unlink(missing_ok=True)
        return path

    if result.returncode != 0 or not dest.exists():
        dest.unlink(missing_ok=True)
        return path

    os.replace(dest, source)
    return str(source)


def run_probe(deps: ResolvedDeps, url: str, *, timeout: float = 20.0) -> formats.ProbeResult:
    """Blocking probe of `url` via `yt-dlp -J --flat-playlist`.

    Short-lived and blocking is intentional (see pr-2b-plan.md Step 2) — this
    runs before the threaded download, not during it. Raises `DownloaderError`
    (with captured stderr) on non-zero exit. `subprocess.TimeoutExpired`
    propagates unwrapped on timeout — distinct from a `DownloaderError`, so
    callers can tell "yt-dlp rejected/failed the request" apart from "yt-dlp
    (or the network) hung".
    """
    argv = formats.build_probe_argv(deps, url)
    # UTF-8 explicitly (not the interpreter's locale) for the same reason as
    # DownloadRunner.start's Popen — Resolve's embedded Python is ASCII-locale,
    # and a probed title can contain non-ASCII characters.
    result = subprocess.run(
        argv,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )

    if result.returncode != 0:
        raise DownloaderError(result.stderr.strip())

    return formats.parse_probe_json(result.stdout)


class DownloadRunner:
    """Runs a single yt-dlp download in a background thread with a cancel path.

    Not thread-safe for concurrent `start()` calls — one in-flight download per
    instance, matching decisions.md Q2's single-active-download model. `start()`
    and `cancel()` (from a different thread, e.g. a GUI thread) are safe together.
    """

    #: SIGTERM -> SIGKILL escalation window for cancel(). Class attribute (not a
    #: constructor arg, per the plan's pinned `__init__(deps)` signature) so
    #: tests can shorten it via monkeypatch, mirroring deps.common_locations()'s
    #: monkeypatch-friendly-by-indirection style.
    GRACE_PERIOD_SECONDS: float = 3.0

    def __init__(self, deps: ResolvedDeps) -> None:
        self._deps = deps
        self._lock = threading.Lock()
        self._process: subprocess.Popen[str] | None = None
        self._last_tmpfilename: str | None = None
        self._final_filename: str | None = None
        self._events: queue.Queue[ProgressEvent | TerminalEvent] | None = None
        self._playlist_index: int | None = None
        self._playlist_count: int | None = None
        self._audio_compat = False
        self._canceling = threading.Event()
        self._finished = threading.Event()

    def start(
        self,
        settings: Settings,
        url: str,
        events: queue.Queue[ProgressEvent | TerminalEvent],
        *,
        playlist_index: int | None = None,
        playlist_count: int | None = None,
    ) -> None:
        """Spawn yt-dlp and start pushing events onto `events`. Returns immediately."""
        self._events = events
        self._playlist_index = playlist_index
        self._playlist_count = playlist_count
        self._audio_compat = settings.resolve_audio_compat
        self._last_tmpfilename = None
        self._final_filename = None
        self._canceling.clear()
        self._finished.clear()

        argv = formats.build_download_argv(self._deps, settings, url)
        # Decode yt-dlp's output as UTF-8 explicitly, not via the interpreter's
        # locale (`text=True` alone). Resolve's embedded Python reports an ASCII
        # (`ANSI_X3.4-1968`) preferred encoding, while yt-dlp itself runs UTF-8
        # (it writes emoji-bearing filenames to disk) — so a title/path with any
        # non-ASCII character (e.g. an emoji) made the reader thread's line
        # decode raise `UnicodeDecodeError`, silently killing the thread. That
        # left yt-dlp unreaped (a zombie), emitted no `TerminalEvent` at all
        # (no completion, no error, nothing logged), and stranded a `.part`
        # file. `errors="replace"` keeps a stray undecodable byte from ever
        # reintroducing that failure. Verified end-to-end against an emoji-titled
        # video under an ASCII-locale parent.
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            start_new_session=True,
        )
        with self._lock:
            self._process = process

        thread = threading.Thread(
            target=self._read_output,
            args=(process, events, playlist_index, playlist_count),
            daemon=True,
        )
        thread.start()

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the current run's terminal event has been pushed.

        Internal to this module's own orchestration (`PlaylistRunner`) — not
        part of the plan's public surface, but needed to sequence per-item
        subprocesses without racing whatever external consumer is draining
        `events`.
        """
        return self._finished.wait(timeout)

    def _read_output(
        self,
        process: subprocess.Popen[str],
        events: queue.Queue[ProgressEvent | TerminalEvent],
        playlist_index: int | None,
        playlist_count: int | None,
    ) -> None:
        assert process.stdout is not None
        reader_error: Exception | None = None
        try:
            for line in process.stdout:
                final_filename = parse_final_filename_line(line)
                if final_filename is not None:
                    self._final_filename = final_filename
                    continue
                event = parse_progress_line(line)
                if event is None:
                    continue
                if event.tmpfilename:
                    with self._lock:
                        self._last_tmpfilename = event.tmpfilename
                if playlist_index is not None or playlist_count is not None:
                    event = replace(
                        event, playlist_index=playlist_index, playlist_count=playlist_count
                    )
                events.put(event)
        except Exception as exc:
            # Defense-in-depth: an unhandled crash in this daemon thread (the
            # historical cause was a `UnicodeDecodeError`, now fixed at the
            # Popen level) used to strand yt-dlp as a zombie and emit no event
            # at all — an invisible, unrecoverable failure. Record it and kill
            # the child so `wait()` below can't deadlock on an unread, full
            # stdout pipe; the terminal-event block then surfaces it as an error.
            reader_error = exc
            self._kill_process_group(process)
        finally:
            process.stdout.close()

        returncode = process.wait()
        stderr_text = ""
        if process.stderr is not None:
            stderr_text = process.stderr.read()
            process.stderr.close()

        with self._lock:
            self._process = None

        # If cancel() is in progress, it owns pushing the terminal event (and
        # cleaning up the .part file) — skip here to avoid a duplicate.
        if not self._canceling.is_set():
            if reader_error is not None:
                events.put(
                    TerminalEvent(
                        "error",
                        f"Failed reading yt-dlp output: {reader_error}",
                        playlist_index,
                        playlist_count,
                    )
                )
            elif returncode == 0:
                final_filename = self._final_filename
                if final_filename is not None and self._audio_compat:
                    final_filename = transcode_audio_for_resolve(self._deps, final_filename)
                events.put(
                    TerminalEvent("done", None, playlist_index, playlist_count, final_filename)
                )
            else:
                events.put(
                    TerminalEvent("error", stderr_text.strip(), playlist_index, playlist_count)
                )
            self._finished.set()

    @staticmethod
    def _kill_process_group(process: subprocess.Popen[str]) -> None:
        """Best-effort SIGKILL of a spawned yt-dlp's whole process group.

        Used by `_read_output`'s crash guard to guarantee the child can't keep
        the (now-unread) stdout pipe full and deadlock the following `wait()`.
        Swallows `ProcessLookupError` (already gone) — this is a cleanup path.
        """
        try:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def cancel(self) -> None:
        """Terminate the running download, clean up its `.part` file, idempotently.

        No-op if nothing is running (including if the process already finished
        on its own). Otherwise: `SIGTERM` the process group, wait up to
        `GRACE_PERIOD_SECONDS`, escalate to `SIGKILL` if still alive, delete the
        last-known `tmpfilename`, then push `TerminalEvent("canceled")`.
        """
        with self._lock:
            process = self._process
            tmpfilename = self._last_tmpfilename
            events = self._events

        if process is None or process.poll() is not None:
            return

        self._canceling.set()

        try:
            pgid = os.getpgid(process.pid)
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pgid = None

        try:
            process.wait(timeout=self.GRACE_PERIOD_SECONDS)
        except subprocess.TimeoutExpired:
            if pgid is not None:
                try:
                    os.killpg(pgid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()

        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()

        if tmpfilename:
            Path(tmpfilename).unlink(missing_ok=True)

        with self._lock:
            self._process = None

        assert events is not None
        events.put(TerminalEvent("canceled", None, self._playlist_index, self._playlist_count))
        self._finished.set()


def _select_entries(
    entries: tuple[formats.PlaylistEntry, ...], limit: int | None
) -> tuple[formats.PlaylistEntry, ...]:
    """Apply `Settings.playlist_limit` to probed entries.

    `None` means unlimited (all entries). `0` means "download nothing" — the
    literal reading, kept distinct from `None`'s "unlimited" so the two aren't
    redundant.
    """
    if limit is None:
        return entries
    return entries[: max(limit, 0)]


class PlaylistRunner:
    """Wraps `DownloadRunner` to drive a playlist: one subprocess per entry, sequentially.

    Per-item (not single multi-item `--playlist-items`) invocation is deliberate
    — it gives each entry its own clean terminal event, which PR-3's future
    per-item auto-import needs. `cancel()` stops the whole queue rather than
    skipping to the next entry.
    """

    def __init__(self, deps: ResolvedDeps) -> None:
        self._runner = DownloadRunner(deps)
        self._stop_requested = threading.Event()
        self._thread: threading.Thread | None = None

    def start(
        self,
        settings: Settings,
        probe: formats.ProbeResult,
        events: queue.Queue[ProgressEvent | TerminalEvent],
    ) -> None:
        """Start the playlist run in a background thread. Returns immediately.

        Entry URLs are passed to `formats.build_download_argv` exactly as
        probed (`entry.url`), with no reconstruction: verified directly against
        the installed yt-dlp (2026.06.09) that flat-playlist `entries[].url` is
        already a full URL (`https://www.youtube.com/watch?v=<id>`), and
        separately that yt-dlp also accepts a bare video ID as its URL
        argument. Either shape `entry.url` takes works unmodified.
        """
        self._stop_requested.clear()
        entries = _select_entries(probe.entries, settings.playlist_limit)

        thread = threading.Thread(
            target=self._run,
            args=(settings, entries, events),
            daemon=True,
        )
        self._thread = thread
        thread.start()

    def _run(
        self,
        settings: Settings,
        entries: tuple[formats.PlaylistEntry, ...],
        events: queue.Queue[ProgressEvent | TerminalEvent],
    ) -> None:
        count = len(entries)
        for index, entry in enumerate(entries, start=1):
            if self._stop_requested.is_set():
                return
            self._runner.start(
                settings, entry.url, events, playlist_index=index, playlist_count=count
            )
            self._runner.wait()

    def cancel(self) -> None:
        """Cancel the in-flight item and stop iterating (whole-queue cancel)."""
        self._stop_requested.set()
        self._runner.cancel()
