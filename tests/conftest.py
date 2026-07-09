"""Shared pytest fixtures: tmp config/home dir, fake-binary factory, platform helpers."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

from resolve_ytdlp import deps as deps_module


@pytest.fixture(autouse=True)
def _isolate_resolve_ytdlp_logger():
    """Snapshot and restore the process-global ``resolve_ytdlp`` logger per test.

    ``app.configure_logging`` mutates this singleton — it attaches a rotating
    file handler and sets ``propagate = False``. Left un-restored, that state
    leaks into later tests: in particular, disabled propagation stops pytest's
    ``caplog`` (whose handler sits on the root logger) from capturing records
    emitted by the ``resolve_ytdlp.test`` child logger, so log-assertion tests
    fail depending on execution order.
    """
    logger = logging.getLogger("resolve_ytdlp")
    saved_handlers = logger.handlers[:]
    saved_level = logger.level
    saved_propagate = logger.propagate
    try:
        yield
    finally:
        for handler in logger.handlers[:]:
            if handler not in saved_handlers:
                logger.removeHandler(handler)
                handler.close()
        logger.handlers[:] = saved_handlers
        logger.setLevel(saved_level)
        logger.propagate = saved_propagate

_FAKE_YTDLP_TEMPLATE = """\
#!/usr/bin/env python3
import signal
import sys
import time

if {ignore_sigterm!r}:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

for line in {stdout_lines!r}:
    print(line, flush=True)
    if {line_delay!r}:
        time.sleep({line_delay!r})

if {delay_before_exit!r}:
    time.sleep({delay_before_exit!r})

stderr_text = {stderr_text!r}
if stderr_text:
    print(stderr_text, file=sys.stderr, end="")

sys.exit({exit_code!r})
"""


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
def make_fake_ytdlp(tmp_path: Path):
    """Factory fixture: writes an executable stub "yt-dlp" script with scripted behavior.

    Real `subprocess.Popen`/threading/signal handling is exercised against this
    script in `test_downloader.py` — the far end (real yt-dlp/ffmpeg/network) is
    what's faked, not `Popen` itself. Mirrors `make_fake_binary`'s
    executable-stub-file style, extended with scriptable stdout/stderr/exit
    behavior needed for progress-parsing and cancel tests.

    Usage: ``make_fake_ytdlp(tmp_path, stdout_lines=[...], exit_code=1, ...)``.
    """

    def _make(
        directory: Path,
        *,
        stdout_lines: list[str] | None = None,
        stderr_text: str = "",
        exit_code: int = 0,
        line_delay: float = 0.0,
        delay_before_exit: float = 0.0,
        ignore_sigterm: bool = False,
        name: str = "yt-dlp",
    ) -> Path:
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        script = _FAKE_YTDLP_TEMPLATE.format(
            stdout_lines=stdout_lines or [],
            stderr_text=stderr_text,
            exit_code=exit_code,
            line_delay=line_delay,
            delay_before_exit=delay_before_exit,
            ignore_sigterm=ignore_sigterm,
        )
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)
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


class FakeFolder:
    """Stand-in for a Resolve `Folder` object: a name and a list of sub-folders."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.subfolders: list[FakeFolder] = []

    def GetName(self) -> str:
        return self.name

    def GetSubFolderList(self) -> list[FakeFolder]:
        return self.subfolders


class FakeMediaPool:
    """Stand-in for a Resolve `MediaPool`, with scriptable failure modes.

    `add_subfolder_fails` simulates `AddSubFolder` returning `None` (creation
    failure). `import_media_missing` simulates `ImportMedia` silently omitting
    that many trailing paths from its returned list (partial/total failure).
    `call_log` records call order so tests can assert `SetCurrentFolder` runs
    before `ImportMedia`.
    """

    def __init__(
        self,
        *,
        add_subfolder_fails: bool = False,
        import_media_missing: int = 0,
    ) -> None:
        self.root = FakeFolder("Master")
        self.current_folder: FakeFolder | None = None
        self.add_subfolder_calls = 0
        self.call_log: list[str] = []
        self._add_subfolder_fails = add_subfolder_fails
        self._import_media_missing = import_media_missing

    def GetRootFolder(self) -> FakeFolder:
        return self.root

    def AddSubFolder(self, parent: FakeFolder, name: str) -> FakeFolder | None:
        self.add_subfolder_calls += 1
        if self._add_subfolder_fails:
            return None
        folder = FakeFolder(name)
        parent.subfolders.append(folder)
        return folder

    def SetCurrentFolder(self, folder: FakeFolder) -> bool:
        self.call_log.append("SetCurrentFolder")
        self.current_folder = folder
        return True

    def ImportMedia(self, paths: list[str]) -> list[str]:
        self.call_log.append("ImportMedia")
        missing = min(self._import_media_missing, len(paths))
        return paths[: len(paths) - missing]


class FakeProject:
    """Stand-in for a Resolve `Project`: exposes `GetMediaPool()`."""

    def __init__(self, media_pool: FakeMediaPool) -> None:
        self._media_pool = media_pool

    def GetMediaPool(self) -> FakeMediaPool:
        return self._media_pool


class FakeProjectManager:
    """Stand-in for a Resolve `ProjectManager`: exposes `GetCurrentProject()`."""

    def __init__(self, project: FakeProject | None) -> None:
        self._project = project

    def GetCurrentProject(self) -> FakeProject | None:
        return self._project


class FakeResolveApp:
    """Stand-in for the top-level `resolve` app object. `project=None` models
    "no project open" (`GetCurrentProject()` returning falsy), matching the
    real API's `GetProjectManager().GetCurrentProject().GetMediaPool()` chain.

    `media_pool` is exposed directly (not just through the real-shaped chain)
    so tests can assert against it without repeating the chain everywhere.
    """

    def __init__(self, project: FakeProject | None) -> None:
        self._project_manager = FakeProjectManager(project)
        self.media_pool = project.GetMediaPool() if project else None

    def GetProjectManager(self) -> FakeProjectManager:
        return self._project_manager


@pytest.fixture
def make_fake_resolve_app():
    """Factory fixture building a `FakeResolveApp` with a configurable media pool.

    Usage: ``make_fake_resolve_app(existing_bins=["yt-dlp"])`` for a bin that
    already exists, ``make_fake_resolve_app(no_project=True)`` for no project
    open, ``make_fake_resolve_app(import_media_missing=1)`` for a partial
    `ImportMedia` failure.
    """

    def _make(
        *,
        no_project: bool = False,
        existing_bins: list[str] | None = None,
        add_subfolder_fails: bool = False,
        import_media_missing: int = 0,
    ) -> FakeResolveApp:
        if no_project:
            return FakeResolveApp(project=None)

        media_pool = FakeMediaPool(
            add_subfolder_fails=add_subfolder_fails,
            import_media_missing=import_media_missing,
        )
        for name in existing_bins or []:
            media_pool.root.subfolders.append(FakeFolder(name))
        return FakeResolveApp(FakeProject(media_pool))

    return _make
