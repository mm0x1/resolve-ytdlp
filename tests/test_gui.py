from __future__ import annotations

from resolve_ytdlp import gui
from resolve_ytdlp.config import Settings
from resolve_ytdlp.downloader import ProgressEvent, TerminalEvent
from resolve_ytdlp.formats import PlaylistEntry, ProbeResult


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


# -- format_progress_text -----------------------------------------------------


def test_format_progress_text_with_full_data() -> None:
    event = _progress_event(percent=42.5, speed=1_048_576.0, eta=65)

    text = gui.format_progress_text(event)

    assert "42.5%" in text
    assert "1.0MB/s" in text
    assert "ETA 01:05" in text


def test_format_progress_text_handles_none_percent() -> None:
    event = _progress_event(percent=None)

    text = gui.format_progress_text(event)

    assert "None" not in text


def test_format_progress_text_handles_none_speed_and_eta() -> None:
    event = _progress_event(percent=10.0, speed=None, eta=None)

    text = gui.format_progress_text(event)

    assert "None" not in text
    assert "10.0%" in text


def test_format_progress_text_includes_postprocessor() -> None:
    event = _progress_event(percent=None, postprocessor="Merger")

    text = gui.format_progress_text(event)

    assert "[Merger]" in text
    assert "None" not in text


def test_format_progress_text_includes_playlist_position() -> None:
    event = _progress_event(playlist_index=2, playlist_count=5)

    text = gui.format_progress_text(event)

    assert text.startswith("(2/5)")


# -- format_terminal_text ------------------------------------------------------


def test_format_terminal_text_done() -> None:
    text = gui.format_terminal_text(_terminal_event("done"))
    assert "complete" in text.lower()


def test_format_terminal_text_error_includes_message() -> None:
    text = gui.format_terminal_text(_terminal_event("error", message="network down"))
    assert "network down" in text


def test_format_terminal_text_error_without_message() -> None:
    text = gui.format_terminal_text(_terminal_event("error", message=None))
    assert "None" not in text


def test_format_terminal_text_canceled() -> None:
    text = gui.format_terminal_text(_terminal_event("canceled"))
    assert "cancel" in text.lower()


def test_format_terminal_text_includes_playlist_position_for_all_statuses() -> None:
    for status, kwargs in (
        ("done", {}),
        ("error", {"message": "x"}),
        ("canceled", {}),
    ):
        text = gui.format_terminal_text(
            _terminal_event(status, playlist_index=3, playlist_count=4, **kwargs)
        )
        assert text.startswith("(3/4)")


# -- fields_from_settings / settings_from_fields -------------------------------


def test_fields_from_settings_maps_none_optional_fields_to_empty_string() -> None:
    settings = Settings(custom_format=None, playlist_limit=None)

    fields = gui.fields_from_settings(settings)

    assert fields["custom_format"] == ""
    assert fields["playlist_limit"] == ""


def test_fields_from_settings_maps_playlist_limit_to_string() -> None:
    settings = Settings(playlist_limit=5)

    fields = gui.fields_from_settings(settings)

    assert fields["playlist_limit"] == "5"


def test_settings_from_fields_empty_dict_returns_current_unchanged() -> None:
    current = Settings(download_dir="/tmp/x", auto_import=False)

    result = gui.settings_from_fields(current, {})

    assert result == current


def test_settings_from_fields_normalizes_empty_custom_format_to_none() -> None:
    current = Settings(custom_format="bv+ba")

    result = gui.settings_from_fields(current, {"custom_format": ""})

    assert result.custom_format is None


def test_settings_from_fields_normalizes_empty_playlist_limit_to_none() -> None:
    current = Settings(playlist_limit=3)

    result = gui.settings_from_fields(current, {"playlist_limit": ""})

    assert result.playlist_limit is None


def test_settings_from_fields_parses_playlist_limit_string() -> None:
    current = Settings(playlist_limit=None)

    result = gui.settings_from_fields(current, {"playlist_limit": "7"})

    assert result.playlist_limit == 7


def test_settings_from_fields_leaves_absent_keys_unchanged() -> None:
    current = Settings(auto_import=True, bin_name="yt-dlp")

    result = gui.settings_from_fields(current, {"download_dir": "/new/dir"})

    assert result.download_dir == "/new/dir"
    assert result.auto_import is True
    assert result.bin_name == "yt-dlp"


# -- playlist_confirm_message ---------------------------------------------------


def _probe(count: int) -> ProbeResult:
    entries = tuple(PlaylistEntry(id=str(i), title=None, url=f"u{i}") for i in range(count))
    return ProbeResult(is_playlist=True, playlist_count=count, entries=entries, formats=())


def test_playlist_confirm_message_unlimited() -> None:
    text = gui.playlist_confirm_message(_probe(10), None)
    assert "10" in text
    assert "entries" in text


def test_playlist_confirm_message_singular_at_one_entry_unlimited() -> None:
    text = gui.playlist_confirm_message(_probe(1), None)
    assert "1 entry." in text
    assert "1 entries" not in text


def test_playlist_confirm_message_capped_below_count() -> None:
    text = gui.playlist_confirm_message(_probe(10), 3)
    assert "first 3" in text
    assert "10" in text


def test_playlist_confirm_message_singular_when_capped_to_one() -> None:
    text = gui.playlist_confirm_message(_probe(10), 1)
    assert "first 1 entry" in text


def test_playlist_confirm_message_limit_zero_does_not_crash() -> None:
    text = gui.playlist_confirm_message(_probe(10), 0)
    assert "first 0" in text


def test_playlist_confirm_message_limit_above_count_treated_as_unlimited() -> None:
    text = gui.playlist_confirm_message(_probe(5), 100)
    assert "all 5" in text
