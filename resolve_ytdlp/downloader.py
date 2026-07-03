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
    result = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)

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
        self._events: queue.Queue[ProgressEvent | TerminalEvent] | None = None
        self._playlist_index: int | None = None
        self._playlist_count: int | None = None
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
        self._last_tmpfilename = None
        self._canceling.clear()
        self._finished.clear()

        argv = formats.build_download_argv(self._deps, settings, url)
        process = subprocess.Popen(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
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
        for line in process.stdout:
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
            if returncode == 0:
                events.put(TerminalEvent("done", None, playlist_index, playlist_count))
            else:
                events.put(
                    TerminalEvent("error", stderr_text.strip(), playlist_index, playlist_count)
                )
            self._finished.set()

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
