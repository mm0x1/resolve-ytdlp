"""The Resolve-facing window: widget tree, event wiring, and the non-blocking pump loop.

This is the one module that touches `UIManager`/`UIDispatcher` widgets — the
same category of untestable seam as `resolve_bridge.connect()`. Only the pure
helper functions below (`format_progress_text`, `format_terminal_text`,
`fields_from_settings`/`settings_from_fields`, `playlist_confirm_message`) are
exercised by `pytest`; `build_window`/`run` are verified manually inside a
real Resolve session (decisions.md's confirmed bootstrap sequence:
`ui = resolve.Fusion().UIManager`, `disp = bmd.UIDispatcher(ui)`,
`disp.AddWindow({...}, ui.VGroup([...]))`, events via `win.On[id].Clicked`,
pump via `disp.StepLoop(False)` + `ExitLoop`). Widget kinds beyond the
session-confirmed `Label`/`Button`/`HGroup`/`VGroup`/`CheckBox`/`ComboBox`
are standard in Resolve's Fusion UI toolkit but were not independently
re-verified this session — flag if they don't match the real API. In
particular the UI-polish pass moved every single-line text input to
`LineEdit` (`UrlField`, `CustomFormatField`, `SubLangsField`,
`DownloadDirField`, `PlaylistLimitField`) — read/written via `.Text` (not
`TextEdit`'s `.PlainText`), so `TextEdit` is no longer used at all. That pass
also newly relies on the two-arg group form (`ui.VGroup({attrs}, [children])`)
and the widget attributes `Weight`, `StyleSheet`, `PlaceholderText`, and
`MinimumSize` — all standard Qt/Fusion but unverified in a live session here;
flag if a real Resolve session shows a stretched/mis-laid-out window or a
`LineEdit` whose text doesn't round-trip. `FormatsCombo.Clear()` (used to
reset the list on a re-fetch) is a similarly unverified assumption about
`ComboBox`'s API — flag if it's not the right method name.

`build_window` deliberately omits `Geometry`'s `x, y` and only sets
`MinimumSize` — community reports (steakunderwater's UIManager thread) say
an absolute `Geometry` `[x, y, w, h]` places the window in screen-global
coordinates (confirmed the hard way: on a multi-monitor Linux box where the
secondary monitor happens to sit at the coordinate-space origin, `[100, 100,
...]` always opened the window on the wrong monitor, regardless of which one
Resolve itself was on), whereas omitting position lets Fusion/Qt center the
new window over the main Resolve window instead — monitor-layout-agnostic by
construction. Unverified beyond that community report; flag if a real
session shows the window appearing somewhere unexpected (e.g. screen
top-left) instead of centered over Resolve.

Stdlib only — this module runs inside Resolve's embedded Python interpreter.
"""

from __future__ import annotations

import queue
import time
from dataclasses import dataclass, replace
from typing import Any

from resolve_ytdlp import app as app_module
from resolve_ytdlp import config, downloader
from resolve_ytdlp.app import AppContext, ImportCoordinator
from resolve_ytdlp.config import Settings
from resolve_ytdlp.downloader import DownloaderError, ProgressEvent, TerminalEvent
from resolve_ytdlp.formats import ProbeResult, custom_format_selector_for

WINDOW_ID = "ResolveYtdlpWindow"
PUMP_SLEEP_SECONDS = 0.05

FORMAT_PRESET_LABELS: dict[str, str] = {
    "best_mp4": "Best MP4",
    "1080p": "1080p",
    "720p": "720p",
    "audio_mp3": "Audio (MP3)",
    "audio_best": "Audio (best)",
}


# -- Pure helpers (unit-tested) ----------------------------------------------


def _format_bytes(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(num) < 1024.0:
            return f"{num:.1f}{unit}"
        num /= 1024.0
    return f"{num:.1f}TB"


def _format_eta(seconds: int) -> str:
    minutes, secs = divmod(max(seconds, 0), 60)
    return f"{minutes:02d}:{secs:02d}"


def format_progress_text(event: ProgressEvent) -> str:
    """Render a `ProgressEvent` into a short display string.

    Handles `percent`/`speed`/`eta` all being `None` (e.g. an indeterminate
    phase) without crashing or emitting literal `"None"` text.
    """
    parts: list[str] = []
    parts.append(f"{event.percent:.1f}%" if event.percent is not None else "...")
    if event.speed is not None:
        parts.append(f"{_format_bytes(event.speed)}/s")
    if event.eta is not None:
        parts.append(f"ETA {_format_eta(event.eta)}")
    if event.postprocessor:
        parts.append(f"[{event.postprocessor}]")

    text = " ".join(parts)
    if event.playlist_index is not None and event.playlist_count is not None:
        text = f"({event.playlist_index}/{event.playlist_count}) {text}"
    return text


def format_terminal_text(event: TerminalEvent) -> str:
    """Render a `TerminalEvent` into a short display string."""
    if event.status == "done":
        text = "Download complete."
    elif event.status == "error":
        text = f"Download failed: {event.message or 'unknown error'}."
    else:
        text = "Canceled."

    if event.playlist_index is not None and event.playlist_count is not None:
        text = f"({event.playlist_index}/{event.playlist_count}) {text}"
    return text


def fields_from_settings(settings: Settings) -> dict[str, Any]:
    """Map `Settings` onto widget-friendly initial field values.

    Counterpart to `settings_from_fields`: `None`-valued optional text fields
    become `""` (widgets have no `None`), `playlist_limit` becomes its string
    form (or `""` for unlimited).
    """
    return {
        "download_dir": settings.download_dir,
        "auto_import": settings.auto_import,
        "bin_name": settings.bin_name,
        "format_preset": settings.format_preset,
        "custom_format": settings.custom_format or "",
        "embed_metadata": settings.embed_metadata,
        "embed_thumbnail": settings.embed_thumbnail,
        "write_subs": settings.write_subs,
        "sub_langs": settings.sub_langs,
        "playlist_limit": "" if settings.playlist_limit is None else str(settings.playlist_limit),
        "resolve_audio_compat": settings.resolve_audio_compat,
    }


def settings_from_fields(current: Settings, fields: dict[str, Any]) -> Settings:
    """Merge widget-read values in `fields` onto `current` via `dataclasses.replace`.

    Keys absent from `fields` keep `current`'s value. An empty `custom_format`
    string is normalized to `None` (matches
    `formats.resolve_format_selector`'s falsy-check contract). An empty
    `playlist_limit` string is normalized to `None` (unlimited); otherwise
    parsed as `int`.
    """
    updates: dict[str, Any] = {}

    for key in (
        "download_dir",
        "auto_import",
        "bin_name",
        "format_preset",
        "embed_metadata",
        "embed_thumbnail",
        "write_subs",
        "sub_langs",
        "resolve_audio_compat",
    ):
        if key in fields:
            updates[key] = fields[key]

    if "custom_format" in fields:
        updates["custom_format"] = fields["custom_format"] or None

    if "playlist_limit" in fields:
        raw_limit = fields["playlist_limit"]
        updates["playlist_limit"] = None if raw_limit in (None, "") else int(raw_limit)

    return replace(current, **updates)


def playlist_confirm_message(probe: ProbeResult, limit: int | None) -> str:
    """Build the playlist-confirm prompt text (decisions.md Q7).

    Names the full entry count when `limit is None`; names "first N of M"
    when `limit` caps below the probed count. Singular/plural wording is
    exact at 1 entry. `limit=0` ("download nothing") still yields a sane
    string, not a crash.
    """
    total = probe.playlist_count if probe.playlist_count is not None else len(probe.entries)

    if limit is None or limit >= total:
        noun = "entry" if total == 1 else "entries"
        return f"This is a playlist with {total} {noun}. Download all {total}?"

    capped = max(limit, 0)
    noun = "entry" if capped == 1 else "entries"
    return f"This is a playlist with {total} entries. Download the first {capped} {noun}?"


# -- Window / pump loop (manual-verification-only, not exercised by pytest) --


@dataclass
class _WindowHandle:
    win: Any
    disp: Any
    ui: Any


def build_window(ctx: AppContext) -> Any:
    """Build the widget tree and register it with a fresh `UIDispatcher`.

    Reaches Resolve exclusively through `ctx.bridge.app`/`ctx.bridge.bmd` —
    no second `DaVinciResolveScript` bootstrap (decisions.md Q9).
    """
    ui = ctx.bridge.app.Fusion().UIManager
    disp = ctx.bridge.bmd.UIDispatcher(ui)
    initial = fields_from_settings(ctx.settings)

    preset_items = list(FORMAT_PRESET_LABELS.keys())

    def header(text: str) -> Any:
        """A bold section divider (`Weight: 0` so it never steals vertical slack)."""
        return ui.Label({"Text": text, "Weight": 0, "StyleSheet": "font-weight: bold;"})

    def label(text: str) -> Any:
        return ui.Label({"Text": text, "Weight": 0})

    # Row groups are all `Weight: 0` so they stay their natural height; the lone
    # trailing spacer (`Weight: 1`) absorbs any extra vertical space, keeping the
    # form top-aligned instead of stretching every row.
    body: list[Any] = []

    if ctx.startup_problems:
        body.append(
            ui.Label(
                {
                    "ID": "StartupProblems",
                    "Text": "\n".join(ctx.startup_problems),
                    "Weight": 0,
                    "StyleSheet": "color: #e0a13c;",
                }
            )
        )

    body += [
        label("URL"),
        ui.LineEdit(
            {
                "ID": "UrlField",
                "Text": "",
                "Weight": 0,
                "PlaceholderText": "Paste a video or playlist URL",
            }
        ),
        header("Format"),
        ui.HGroup(
            {"Weight": 0},
            [
                ui.VGroup(
                    {"Weight": 1},
                    [label("Preset"), ui.ComboBox({"ID": "PresetCombo", "Weight": 0})],
                ),
                ui.VGroup(
                    {"Weight": 2},
                    [
                        label("Custom format (yt-dlp -f)"),
                        ui.LineEdit(
                            {
                                "ID": "CustomFormatField",
                                "Text": initial["custom_format"],
                                "Weight": 0,
                                "PlaceholderText": "override, e.g. bv*+ba / best",
                            }
                        ),
                    ],
                ),
            ],
        ),
        label("Available formats"),
        ui.HGroup(
            {"Weight": 0},
            [
                ui.Button({"ID": "FetchFormatsButton", "Text": "Fetch", "Weight": 0}),
                ui.ComboBox({"ID": "FormatsCombo", "Weight": 1}),
                ui.Button({"ID": "UseFormatButton", "Text": "Use", "Weight": 0}),
            ],
        ),
        header("Options"),
        ui.HGroup(
            {"Weight": 0},
            [
                ui.CheckBox(
                    {
                        "ID": "EmbedMetadataCheckBox",
                        "Text": "Embed metadata",
                        "Checked": initial["embed_metadata"],
                        "Weight": 1,
                    }
                ),
                ui.CheckBox(
                    {
                        "ID": "EmbedThumbnailCheckBox",
                        "Text": "Embed thumbnail",
                        "Checked": initial["embed_thumbnail"],
                        "Weight": 1,
                    }
                ),
            ],
        ),
        ui.HGroup(
            {"Weight": 0},
            [
                ui.CheckBox(
                    {
                        "ID": "WriteSubsCheckBox",
                        "Text": "Subtitles",
                        "Checked": initial["write_subs"],
                        "Weight": 0,
                    }
                ),
                label("Languages"),
                ui.LineEdit(
                    {
                        "ID": "SubLangsField",
                        "Text": initial["sub_langs"],
                        "Weight": 1,
                        "PlaceholderText": "en",
                    }
                ),
            ],
        ),
        ui.HGroup(
            {"Weight": 0},
            [
                ui.CheckBox(
                    {
                        "ID": "AutoImportCheckBox",
                        "Text": "Auto-import into the yt-dlp bin",
                        "Checked": initial["auto_import"],
                        "Weight": 1,
                    }
                ),
                ui.CheckBox(
                    {
                        "ID": "ResolveAudioCompatCheckBox",
                        "Text": "Resolve-compatible audio (FLAC, Linux)",
                        "Checked": initial["resolve_audio_compat"],
                        "Weight": 1,
                    }
                ),
            ],
        ),
        header("Destination"),
        label("Download folder"),
        ui.LineEdit(
            {"ID": "DownloadDirField", "Text": initial["download_dir"], "Weight": 0}
        ),
        ui.HGroup(
            {"Weight": 0},
            [
                label("Playlist item limit"),
                ui.LineEdit(
                    {
                        "ID": "PlaylistLimitField",
                        "Text": initial["playlist_limit"],
                        "Weight": 0,
                        "MinimumSize": [80, 0],
                        "PlaceholderText": "all",
                    }
                ),
                ui.Label({"Text": "", "Weight": 1}),
            ],
        ),
        ui.HGroup(
            {"Weight": 0},
            [
                ui.Button(
                    {
                        "ID": "DownloadButton",
                        "Text": "Download",
                        "Weight": 1,
                        "StyleSheet": "font-weight: bold;",
                    }
                ),
                ui.Button({"ID": "CancelButton", "Text": "Cancel", "Weight": 1}),
            ],
        ),
        ui.Label({"ID": "ProgressLabel", "Text": "", "Weight": 0}),
        ui.Label({"ID": "StatusLabel", "Text": "", "Weight": 0}),
        ui.Label({"Text": "", "Weight": 1}),
    ]

    win = disp.AddWindow(
        {
            "ID": WINDOW_ID,
            "WindowTitle": "Download from URL (yt-dlp)",
            "MinimumSize": [520, 540],
        },
        ui.VGroup(body),
    )

    items = win.GetItems()
    items["PresetCombo"].AddItems(preset_items)
    try:
        items["PresetCombo"].CurrentIndex = preset_items.index(initial["format_preset"])
    except ValueError:
        pass

    return _WindowHandle(win=win, disp=disp, ui=ui)


def _read_fields(items: dict[str, Any]) -> dict[str, Any]:
    preset_index = items["PresetCombo"].CurrentIndex
    preset_keys = list(FORMAT_PRESET_LABELS.keys())
    format_preset = preset_keys[preset_index] if 0 <= preset_index < len(preset_keys) else None

    fields: dict[str, Any] = {
        "download_dir": items["DownloadDirField"].Text,
        "custom_format": items["CustomFormatField"].Text,
        "write_subs": items["WriteSubsCheckBox"].Checked,
        "sub_langs": items["SubLangsField"].Text,
        "embed_metadata": items["EmbedMetadataCheckBox"].Checked,
        "embed_thumbnail": items["EmbedThumbnailCheckBox"].Checked,
        "playlist_limit": items["PlaylistLimitField"].Text,
        "auto_import": items["AutoImportCheckBox"].Checked,
        "resolve_audio_compat": items["ResolveAudioCompatCheckBox"].Checked,
    }
    if format_preset is not None:
        fields["format_preset"] = format_preset
    return fields


def run(ctx: AppContext) -> None:
    """Show the window and run its pump loop until closed.

    Non-blocking per decisions.md Q2: `disp.StepLoop(False)` pumps UI events,
    the shared `events` queue is drained each iteration, and `handle_event`
    (from `app`, PR-4a) fires auto-import on completed downloads. Playlist
    confirmation is an in-window state swap rather than a second popup window
    (see `pr-4b-plan.md`'s Risks — no confirmed modal-dialog API).
    """
    handle = build_window(ctx)
    win, disp = handle.win, handle.disp
    items = win.GetItems()

    coordinator = ImportCoordinator()
    events: queue.Queue[ProgressEvent | TerminalEvent] = queue.Queue()
    state: dict[str, Any] = {
        "closed": False,
        "runner": None,
        "pending_probe": None,
        "fetched_formats": [],
    }

    def _set_status(text: str) -> None:
        items["StatusLabel"].Text = text

    def _current_settings() -> Settings:
        return settings_from_fields(ctx.settings, _read_fields(items))

    def _start_download(settings: Settings, url: str) -> None:
        runner = downloader.DownloadRunner(ctx.deps)
        state["runner"] = runner
        runner.start(settings, url, events)

    def _start_playlist(settings: Settings, probe: ProbeResult) -> None:
        runner = downloader.PlaylistRunner(ctx.deps)
        state["runner"] = runner
        runner.start(settings, probe, events)

    def _clear_pending_confirm() -> None:
        state["pending_probe"] = None
        items["DownloadButton"].Text = "Download"

    def on_download_clicked(_event: Any) -> None:
        pending = state.get("pending_probe")
        if pending is not None:
            settings, probe = pending
            _clear_pending_confirm()
            _start_playlist(settings, probe)
            return

        settings = _current_settings()
        config.save_settings(settings)
        url = items["UrlField"].Text

        try:
            probe = downloader.run_probe(ctx.deps, url)
        except DownloaderError as exc:
            ctx.logger.warning("Probe failed for %s: %s", url, exc)
            _set_status(f"Could not read that URL: {exc}")
            return

        if probe.is_playlist:
            state["pending_probe"] = (settings, probe)
            items["DownloadButton"].Text = "Confirm download"
            _set_status(playlist_confirm_message(probe, settings.playlist_limit))
        else:
            _start_download(settings, url)

    def on_fetch_formats_clicked(_event: Any) -> None:
        url = items["UrlField"].Text
        try:
            probe = downloader.run_probe(ctx.deps, url)
        except DownloaderError as exc:
            ctx.logger.warning("Format fetch failed for %s: %s", url, exc)
            _set_status(f"Could not fetch formats: {exc}")
            return

        state["fetched_formats"] = probe.formats
        labels = [
            f"{fmt.format_id}  {fmt.ext}  {fmt.resolution or '-'}  {fmt.format_note or ''}"
            for fmt in probe.formats
        ]
        items["FormatsCombo"].Clear()
        items["FormatsCombo"].AddItems(labels)

    def on_use_format_clicked(_event: Any) -> None:
        fetched_formats = state["fetched_formats"]
        index = items["FormatsCombo"].CurrentIndex
        if not (0 <= index < len(fetched_formats)):
            return
        items["CustomFormatField"].Text = custom_format_selector_for(fetched_formats[index])

    def on_cancel_clicked(_event: Any) -> None:
        if state.get("pending_probe") is not None:
            _clear_pending_confirm()
            _set_status("Canceled.")
            return
        runner = state.get("runner")
        if runner is not None:
            runner.cancel()

    def on_close(_event: Any) -> None:
        config.save_settings(_current_settings())
        state["closed"] = True

    win.On[WINDOW_ID].Close = on_close
    win.On["DownloadButton"].Clicked = on_download_clicked
    win.On["CancelButton"].Clicked = on_cancel_clicked
    win.On["FetchFormatsButton"].Clicked = on_fetch_formats_clicked
    win.On["UseFormatButton"].Clicked = on_use_format_clicked

    win.Show()

    while not state["closed"]:
        disp.StepLoop(False)

        while True:
            try:
                event = events.get_nowait()
            except queue.Empty:
                break

            if isinstance(event, ProgressEvent):
                items["ProgressLabel"].Text = format_progress_text(event)
            else:
                items["ProgressLabel"].Text = format_terminal_text(event)

            app_module.handle_event(ctx, coordinator, event)

        time.sleep(PUMP_SLEEP_SECONDS)
