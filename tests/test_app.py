from __future__ import annotations

import logging
from pathlib import Path

import pytest

from resolve_ytdlp import app, resolve_bridge
from resolve_ytdlp.config import Settings
from resolve_ytdlp.downloader import ProgressEvent, TerminalEvent


def _progress_event(**overrides) -> ProgressEvent:
    fields = dict(
        kind="download",
        status="downloading",
        percent=50.0,
        speed=None,
        eta=None,
        downloaded_bytes=None,
        total_bytes=None,
        filename=None,
        tmpfilename=None,
        postprocessor=None,
        playlist_index=None,
        playlist_count=None,
        raw={},
    )
    fields.update(overrides)
    return ProgressEvent(**fields)


def _terminal_event(status: str = "done", **overrides) -> TerminalEvent:
    fields = dict(status=status, message=None, playlist_index=None, playlist_count=None)
    fields.update(overrides)
    return TerminalEvent(**fields)


# -- configure_logging --------------------------------------------------------


def test_configure_logging_creates_log_file(tmp_path: Path) -> None:
    logger = app.configure_logging(tmp_path)

    assert (tmp_path / "logs" / app.LOG_FILENAME).exists()
    assert logger.name == app.LOGGER_NAME


def test_configure_logging_writes_readable_record(tmp_path: Path) -> None:
    logger = app.configure_logging(tmp_path)

    logger.info("hello world")
    for handler in logger.handlers:
        handler.flush()

    log_path = tmp_path / "logs" / app.LOG_FILENAME
    assert "hello world" in log_path.read_text(encoding="utf-8")


def test_configure_logging_twice_does_not_duplicate_handlers(tmp_path: Path) -> None:
    app.configure_logging(tmp_path)
    logger = app.configure_logging(tmp_path)

    assert len(logger.handlers) == 1


def test_configure_logging_repoints_to_new_directory(tmp_path: Path) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second"

    app.configure_logging(first_dir)
    logger = app.configure_logging(second_dir)

    assert len(logger.handlers) == 1
    logger.info("in second dir")
    for handler in logger.handlers:
        handler.flush()

    assert (second_dir / "logs" / app.LOG_FILENAME).exists()
    second_log_text = (second_dir / "logs" / app.LOG_FILENAME).read_text(encoding="utf-8")
    assert "in second dir" in second_log_text


# -- bootstrap ------------------------------------------------------------


def test_bootstrap_returns_none_when_connect_fails(
    tmp_home: Path, set_platform, caplog: pytest.LogCaptureFixture
) -> None:
    set_platform("linux")

    ctx = app.bootstrap(connect=lambda: None)

    assert ctx is None


def test_bootstrap_logs_not_running_in_resolve_message(tmp_home: Path, set_platform) -> None:
    set_platform("linux")

    app.bootstrap(connect=lambda: None)

    log_path = tmp_home / ".config" / "resolve-ytdlp" / "logs" / app.LOG_FILENAME
    assert resolve_bridge.NOT_RUNNING_IN_RESOLVE_MESSAGE in log_path.read_text(encoding="utf-8")


def test_bootstrap_success_builds_context(
    tmp_home: Path,
    set_platform,
    empty_path_env: None,
    isolate_common_locations: None,
    make_fake_resolve_app,
) -> None:
    set_platform("linux")
    fake_app = make_fake_resolve_app()
    bridge = resolve_bridge.ResolveBridge(fake_app)

    ctx = app.bootstrap(connect=lambda: bridge)

    assert ctx is not None
    assert ctx.bridge is bridge
    assert ctx.deps.ytdlp is None
    assert ctx.deps.ffmpeg is None
    assert len(ctx.startup_problems) == 2


def test_bootstrap_success_with_resolved_deps_has_no_problems(
    tmp_home: Path,
    set_platform,
    make_fake_binary,
    empty_path_env: None,
    make_fake_resolve_app,
) -> None:
    set_platform("linux")
    ytdlp = make_fake_binary(tmp_home / "bin", "yt-dlp")
    ffmpeg = make_fake_binary(tmp_home / "bin", "ffmpeg")
    settings = Settings(ytdlp_path=str(ytdlp), ffmpeg_path=str(ffmpeg))
    from resolve_ytdlp import config

    config.save_settings(settings)
    fake_app = make_fake_resolve_app()
    bridge = resolve_bridge.ResolveBridge(fake_app)

    ctx = app.bootstrap(connect=lambda: bridge)

    assert ctx is not None
    assert ctx.startup_problems == ()


def test_bootstrap_auto_import_false_still_builds_context(
    tmp_home: Path,
    set_platform,
    empty_path_env: None,
    isolate_common_locations: None,
    make_fake_resolve_app,
) -> None:
    set_platform("linux")
    from resolve_ytdlp import config

    config.save_settings(Settings(auto_import=False))
    fake_app = make_fake_resolve_app()
    bridge = resolve_bridge.ResolveBridge(fake_app)

    ctx = app.bootstrap(connect=lambda: bridge)

    assert ctx is not None
    assert ctx.settings.auto_import is False


# -- ImportCoordinator ------------------------------------------------------


def test_coordinator_returns_none_for_progress_event() -> None:
    coordinator = app.ImportCoordinator()

    assert coordinator.observe(_progress_event(filename="a.mp4")) is None


def test_coordinator_returns_path_on_done_with_filename() -> None:
    coordinator = app.ImportCoordinator()

    result = coordinator.observe(_terminal_event("done", filename="a.mp4"))

    assert result == Path("a.mp4")


def test_coordinator_returns_none_on_done_without_filename() -> None:
    coordinator = app.ImportCoordinator()

    result = coordinator.observe(_terminal_event("done"))

    assert result is None


def test_coordinator_returns_none_on_error_even_with_filename() -> None:
    coordinator = app.ImportCoordinator()

    result = coordinator.observe(_terminal_event("error", message="boom", filename="a.mp4"))

    assert result is None


def test_coordinator_returns_none_on_canceled_even_with_filename() -> None:
    coordinator = app.ImportCoordinator()

    result = coordinator.observe(_terminal_event("canceled", filename="a.mp4"))

    assert result is None


# -- handle_event ------------------------------------------------------------


def _make_ctx(fake_app, *, auto_import: bool = True) -> app.AppContext:
    from resolve_ytdlp import deps as deps_module

    settings = Settings(auto_import=auto_import, bin_name="yt-dlp")
    bridge = resolve_bridge.ResolveBridge(fake_app)
    logger = logging.getLogger("resolve_ytdlp.test")
    return app.AppContext(
        settings=settings,
        deps=deps_module.ResolvedDeps(ytdlp=None, ffmpeg=None),
        startup_problems=(),
        bridge=bridge,
        logger=logger,
    )


def test_handle_event_imports_on_completed_download(make_fake_resolve_app) -> None:
    fake_app = make_fake_resolve_app()
    ctx = _make_ctx(fake_app)
    coordinator = app.ImportCoordinator()

    result = app.handle_event(ctx, coordinator, _terminal_event("done", filename="a.mp4"))

    assert result is not None
    assert result.ok is True
    assert fake_app.media_pool.call_log == ["SetCurrentFolder", "ImportMedia"]


def test_handle_event_skips_when_auto_import_false(make_fake_resolve_app) -> None:
    fake_app = make_fake_resolve_app()
    ctx = _make_ctx(fake_app, auto_import=False)
    coordinator = app.ImportCoordinator()

    result = app.handle_event(ctx, coordinator, _terminal_event("done", filename="a.mp4"))

    assert result is None
    assert fake_app.media_pool.call_log == []


def test_handle_event_noop_on_progress_event(make_fake_resolve_app) -> None:
    fake_app = make_fake_resolve_app()
    ctx = _make_ctx(fake_app)
    coordinator = app.ImportCoordinator()

    result = app.handle_event(ctx, coordinator, _progress_event(filename="a.mp4"))

    assert result is None
    assert fake_app.media_pool.call_log == []


def test_handle_event_noop_on_error_terminal_event(
    make_fake_resolve_app, caplog: pytest.LogCaptureFixture
) -> None:
    fake_app = make_fake_resolve_app()
    ctx = _make_ctx(fake_app)
    coordinator = app.ImportCoordinator()

    with caplog.at_level(logging.ERROR, logger="resolve_ytdlp.test"):
        result = app.handle_event(
            ctx, coordinator, _terminal_event("error", message="boom", filename="a.mp4")
        )

    assert result is None
    assert fake_app.media_pool.call_log == []
    assert "boom" in caplog.text


def test_handle_event_logs_error_with_fallback_when_message_missing(
    make_fake_resolve_app, caplog: pytest.LogCaptureFixture
) -> None:
    fake_app = make_fake_resolve_app()
    ctx = _make_ctx(fake_app)
    coordinator = app.ImportCoordinator()

    with caplog.at_level(logging.ERROR, logger="resolve_ytdlp.test"):
        app.handle_event(ctx, coordinator, _terminal_event("error", message=None))

    assert "unknown error" in caplog.text


def test_handle_event_noop_on_canceled_terminal_event(
    make_fake_resolve_app, caplog: pytest.LogCaptureFixture
) -> None:
    fake_app = make_fake_resolve_app()
    ctx = _make_ctx(fake_app)
    coordinator = app.ImportCoordinator()

    with caplog.at_level(logging.INFO, logger="resolve_ytdlp.test"):
        result = app.handle_event(ctx, coordinator, _terminal_event("canceled", filename="a.mp4"))

    assert result is None
    assert fake_app.media_pool.call_log == []
    assert "canceled" in caplog.text.lower()


def test_handle_event_logs_warning_when_no_project_open(make_fake_resolve_app) -> None:
    fake_app = make_fake_resolve_app(no_project=True)
    ctx = _make_ctx(fake_app)
    coordinator = app.ImportCoordinator()

    result = app.handle_event(ctx, coordinator, _terminal_event("done", filename="a.mp4"))

    assert result is None


def test_handle_event_logs_warning_on_partial_import_failure(make_fake_resolve_app) -> None:
    fake_app = make_fake_resolve_app(import_media_missing=1)
    ctx = _make_ctx(fake_app)
    coordinator = app.ImportCoordinator()

    result = app.handle_event(ctx, coordinator, _terminal_event("done", filename="a.mp4"))

    assert result is not None
    assert result.ok is False
