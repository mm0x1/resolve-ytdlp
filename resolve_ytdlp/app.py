"""Wires `downloader` events to `resolve_bridge` imports and assembles startup state.

The one-time, mostly-deterministic startup sequence (`bootstrap()`) and the
`downloader` -> `resolve_bridge` glue (`ImportCoordinator`/`handle_event()`)
explicitly deferred from both of those modules' own plans. `gui` (PR-4b) is
the only other module that touches this one; `main()` is what the Utility
entry script calls.

Stdlib only — this module runs inside Resolve's embedded Python interpreter.
"""

from __future__ import annotations

import logging
import logging.handlers
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from resolve_ytdlp import config, deps, resolve_bridge
from resolve_ytdlp.config import Settings
from resolve_ytdlp.deps import ResolvedDeps
from resolve_ytdlp.downloader import ProgressEvent, TerminalEvent
from resolve_ytdlp.resolve_bridge import ImportResult, ResolveBridge

LOG_FILENAME = "resolve-ytdlp.log"

LOGGER_NAME = "resolve_ytdlp"


def configure_logging(directory: Path) -> logging.Logger:
    """(Re)configure the `"resolve_ytdlp"` logger with a rotating file handler.

    Creates `<directory>/logs/`. Clears any handlers already attached to the
    logger first, so repeated calls (as tests, and `bootstrap()` across
    restarts, will do) don't accumulate duplicate handlers or leak file
    handles across calls.
    """
    logs_dir = directory / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)

    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    handler = logging.handlers.RotatingFileHandler(
        logs_dir / LOG_FILENAME, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.propagate = False

    return logger


@dataclass(frozen=True)
class AppContext:
    settings: Settings
    deps: ResolvedDeps
    startup_problems: tuple[str, ...]
    bridge: ResolveBridge
    logger: logging.Logger


def bootstrap(
    *, connect: Callable[[], ResolveBridge | None] = resolve_bridge.connect
) -> AppContext | None:
    """Assemble settings, dependency discovery, the Resolve connection, and logging.

    Returns `None` (after logging `resolve_bridge.NOT_RUNNING_IN_RESOLVE_MESSAGE`
    at `ERROR`) when `connect()` fails — there is no `UIManager` to show
    anything with in that case, so there is nothing further `main()` can do.
    """
    settings = config.load_settings()
    logger = configure_logging(config.config_dir())

    bridge = connect()
    if bridge is None:
        logger.error(resolve_bridge.NOT_RUNNING_IN_RESOLVE_MESSAGE)
        return None

    resolved_deps = deps.discover(settings)
    startup_problems = tuple(deps.preflight(resolved_deps))
    for problem in startup_problems:
        logger.warning(problem)

    return AppContext(
        settings=settings,
        deps=resolved_deps,
        startup_problems=startup_problems,
        bridge=bridge,
        logger=logger,
    )


class ImportCoordinator:
    """Tracks the last-seen `filename` from a `downloader` events stream.

    Yields a `Path` exactly once per completed (`"done"`) download. Assumes a
    single in-flight download at a time (per `downloader.py`'s own
    single-active-download model) — events for two different downloads never
    interleave.
    """

    def __init__(self) -> None:
        self._last_filename: str | None = None

    def observe(self, event: ProgressEvent | TerminalEvent) -> Path | None:
        if isinstance(event, ProgressEvent):
            if event.filename:
                self._last_filename = event.filename
            return None

        if event.status != "done":
            self._last_filename = None
            return None

        filename = self._last_filename
        self._last_filename = None
        if filename is None:
            return None
        return Path(filename)


def handle_event(
    ctx: AppContext, coordinator: ImportCoordinator, event: ProgressEvent | TerminalEvent
) -> ImportResult | None:
    """Auto-import a just-finished download into the `yt-dlp` bin, if enabled.

    A no-op (no bridge calls) unless `coordinator.observe(event)` yields a
    path *and* `ctx.settings.auto_import` is `True`. Logs (does not raise)
    when no project is open or the import is partial/total failure.
    """
    path = coordinator.observe(event)
    if path is None or not ctx.settings.auto_import:
        return None

    bin_folder = ctx.bridge.get_or_create_bin(ctx.settings)
    if bin_folder is None:
        ctx.logger.warning(resolve_bridge.NO_PROJECT_OPEN_MESSAGE)
        return None

    result = ctx.bridge.import_media(bin_folder, [path])
    if result.ok:
        ctx.logger.info("Imported %s into the %r bin.", path, ctx.settings.bin_name)
    else:
        ctx.logger.warning(
            "Only imported %d/%d file(s) for %s.", result.imported, result.requested, path
        )
    return result


def main() -> None:
    """Entry point called by the Utility script: `bootstrap()` then show the window.

    Returns immediately (no window, nothing else to do) if `bootstrap()`
    couldn't connect to Resolve.
    """
    ctx = bootstrap()
    if ctx is None:
        return

    from resolve_ytdlp import gui

    gui.run(ctx)
