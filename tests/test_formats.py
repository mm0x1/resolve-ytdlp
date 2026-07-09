from __future__ import annotations

import json
from pathlib import Path

import pytest

from resolve_ytdlp import formats
from resolve_ytdlp.config import Settings
from resolve_ytdlp.deps import ResolvedDeps

# --- Real captured JSON shapes (trimmed) from yt-dlp -J --flat-playlist ---

VIDEO_JSON = json.dumps(
    {
        "_type": "video",
        "id": "jNQXAC9IVRw",
        "title": "Me at the zoo",
        "formats": [
            {
                "format_id": "139",
                "ext": "m4a",
                "resolution": "audio only",
                "vcodec": "none",
                "acodec": "mp4a.40.5",
                "filesize": 117526,
                "format_note": "low",
            }
        ],
    }
)

PLAYLIST_JSON = json.dumps(
    {
        "_type": "playlist",
        "id": "PLxxxx",
        "title": "Some Playlist",
        "playlist_count": 37,
        "entries": [
            {"_type": "url", "id": "abc123", "title": "Entry Title", "url": "abc123"}
        ],
    }
)


# --- resolve_format_selector ---


def test_resolve_format_selector_custom_format_wins_over_preset() -> None:
    settings = Settings(format_preset="best_mp4", custom_format="bv*+ba/b")

    preset = formats.resolve_format_selector(settings)

    assert preset == formats.FormatPreset(selector="bv*+ba/b")


@pytest.mark.parametrize("preset_key", list(formats.FORMAT_PRESETS.keys()))
def test_resolve_format_selector_each_preset_resolves(preset_key: str) -> None:
    settings = Settings(format_preset=preset_key)

    preset = formats.resolve_format_selector(settings)

    assert preset == formats.FORMAT_PRESETS[preset_key]


def test_resolve_format_selector_unknown_preset_raises() -> None:
    settings = Settings(format_preset="nonexistent")

    with pytest.raises(ValueError, match="nonexistent"):
        formats.resolve_format_selector(settings)


# --- custom_format_selector_for ---


def _fmt(**overrides) -> formats.FormatInfo:
    fields = dict(
        format_id="400",
        ext="mp4",
        resolution="1920x1080",
        vcodec="av01.0.08M.08",
        acodec="none",
        filesize=None,
        format_note=None,
    )
    fields.update(overrides)
    return formats.FormatInfo(**fields)


def test_custom_format_selector_pairs_video_only_format_with_m4a_audio() -> None:
    fmt = _fmt(vcodec="av01.0.08M.08", acodec="none")

    assert formats.custom_format_selector_for(fmt) == "400+ba[ext=m4a]/ba"


def test_custom_format_selector_leaves_muxed_format_alone() -> None:
    fmt = _fmt(format_id="18", vcodec="avc1.42001E", acodec="mp4a.40.2")

    assert formats.custom_format_selector_for(fmt) == "18"


def test_custom_format_selector_leaves_audio_only_format_alone() -> None:
    fmt = _fmt(format_id="140", vcodec="none", acodec="mp4a.40.2")

    assert formats.custom_format_selector_for(fmt) == "140"


def test_custom_format_selector_treats_none_codec_like_literal_none_string() -> None:
    fmt = _fmt(vcodec="av01.0.08M.08", acodec=None)

    assert formats.custom_format_selector_for(fmt) == "400+ba[ext=m4a]/ba"


# --- build_download_argv ---


def _deps(ffmpeg: Path | None) -> ResolvedDeps:
    return ResolvedDeps(ytdlp=Path("/usr/bin/yt-dlp"), ffmpeg=ffmpeg)


def test_build_download_argv_binary_path_first() -> None:
    argv = formats.build_download_argv(_deps(None), Settings(), "https://example.com/v")

    assert argv[0] == "/usr/bin/yt-dlp"


def test_build_download_argv_includes_selector() -> None:
    settings = Settings(format_preset="720p")
    argv = formats.build_download_argv(_deps(None), settings, "https://example.com/v")

    assert "-f" in argv
    selector = argv[argv.index("-f") + 1]
    assert selector == formats.FORMAT_PRESETS["720p"].selector


def test_build_download_argv_includes_extra_args_for_audio_preset() -> None:
    settings = Settings(format_preset="audio_mp3")
    argv = formats.build_download_argv(_deps(None), settings, "https://example.com/v")

    assert "--extract-audio" in argv
    assert "--audio-format" in argv
    assert argv[argv.index("--audio-format") + 1] == "mp3"


def test_build_audio_transcode_argv_copies_video_and_reencodes_audio() -> None:
    ffmpeg = Path("/usr/bin/ffmpeg")
    source = Path("/downloads/Clip [id].mp4")
    dest = Path("/downloads/Clip [id].rytdlp-compat.mp4")

    argv = formats.build_audio_transcode_argv(_deps(ffmpeg), source, dest)

    assert argv[0] == str(ffmpeg)
    assert argv[argv.index("-i") + 1] == str(source)
    assert argv[-1] == str(dest)
    # Video (and everything) copied; only audio re-encoded to 16-bit FLAC.
    assert argv[argv.index("-c") + 1] == "copy"
    assert argv[argv.index("-c:a") + 1] == formats.RESOLVE_AUDIO_CODEC == "flac"
    # 16-bit specifically: 24-bit FLAC imports silent in Resolve on Linux.
    assert argv[argv.index("-sample_fmt") + 1] == formats.RESOLVE_AUDIO_SAMPLE_FMT == "s16"


def test_build_download_argv_ffmpeg_location_included_when_set() -> None:
    ffmpeg = Path("/usr/bin/ffmpeg")
    argv = formats.build_download_argv(_deps(ffmpeg), Settings(), "https://example.com/v")

    assert "--ffmpeg-location" in argv
    assert argv[argv.index("--ffmpeg-location") + 1] == str(ffmpeg)


def test_build_download_argv_ffmpeg_location_omitted_when_none() -> None:
    argv = formats.build_download_argv(_deps(None), Settings(), "https://example.com/v")

    assert "--ffmpeg-location" not in argv


def test_build_download_argv_output_template_uses_download_dir() -> None:
    settings = Settings(download_dir="/tmp/downloads")
    argv = formats.build_download_argv(_deps(None), settings, "https://example.com/v")

    assert "-o" in argv
    template = argv[argv.index("-o") + 1]
    assert template == "/tmp/downloads/%(title).200B [%(id)s].%(ext)s"


def test_build_download_argv_metadata_flag_present_when_enabled() -> None:
    settings = Settings(embed_metadata=True)
    argv = formats.build_download_argv(_deps(None), settings, "https://example.com/v")

    assert "--embed-metadata" in argv


def test_build_download_argv_metadata_flag_absent_when_disabled() -> None:
    settings = Settings(embed_metadata=False)
    argv = formats.build_download_argv(_deps(None), settings, "https://example.com/v")

    assert "--embed-metadata" not in argv


def test_build_download_argv_thumbnail_flag_present_when_enabled() -> None:
    settings = Settings(embed_thumbnail=True)
    argv = formats.build_download_argv(_deps(None), settings, "https://example.com/v")

    assert "--embed-thumbnail" in argv


def test_build_download_argv_thumbnail_flag_absent_when_disabled() -> None:
    settings = Settings(embed_thumbnail=False)
    argv = formats.build_download_argv(_deps(None), settings, "https://example.com/v")

    assert "--embed-thumbnail" not in argv


def test_build_download_argv_subtitle_flags_present_when_enabled() -> None:
    settings = Settings(write_subs=True, sub_langs="en,fr")
    argv = formats.build_download_argv(_deps(None), settings, "https://example.com/v")

    assert "--write-subs" in argv
    assert "--write-auto-subs" in argv
    assert "--embed-subs" in argv
    assert "--sub-langs" in argv
    assert argv[argv.index("--sub-langs") + 1] == "en,fr"


def test_build_download_argv_subtitle_flags_absent_when_disabled() -> None:
    settings = Settings(write_subs=False)
    argv = formats.build_download_argv(_deps(None), settings, "https://example.com/v")

    assert "--write-subs" not in argv
    assert "--write-auto-subs" not in argv
    assert "--embed-subs" not in argv
    assert "--sub-langs" not in argv


def test_build_download_argv_no_playlist_always_present() -> None:
    argv = formats.build_download_argv(_deps(None), Settings(), "https://example.com/v")

    assert "--no-playlist" in argv


def test_build_download_argv_url_is_last() -> None:
    url = "https://example.com/v"
    argv = formats.build_download_argv(_deps(None), Settings(), url)

    assert argv[-1] == url


def test_build_download_argv_progress_sentinels_present_exactly_once() -> None:
    argv = formats.build_download_argv(_deps(None), Settings(), "https://example.com/v")

    dl_matches = [a for a in argv if formats.DL_SENTINEL in a]
    pp_matches = [a for a in argv if formats.PP_SENTINEL in a]

    assert len(dl_matches) == 1
    assert len(pp_matches) == 1
    assert dl_matches[0].startswith("download:")
    assert pp_matches[0].startswith("postprocess:")


# --- build_probe_argv ---


def test_build_probe_argv() -> None:
    deps = _deps(None)
    url = "https://example.com/v"

    argv = formats.build_probe_argv(deps, url)

    assert argv == [str(deps.ytdlp), "-J", "--flat-playlist", url]


# --- parse_probe_json ---


def test_parse_probe_json_single_video() -> None:
    result = formats.parse_probe_json(VIDEO_JSON)

    assert result.is_playlist is False
    assert result.playlist_count is None
    assert result.entries == ()
    assert len(result.formats) == 1

    fmt = result.formats[0]
    assert fmt.format_id == "139"
    assert fmt.ext == "m4a"
    assert fmt.resolution == "audio only"
    assert fmt.vcodec == "none"
    assert fmt.acodec == "mp4a.40.5"
    assert fmt.filesize == 117526
    assert fmt.format_note == "low"


def test_parse_probe_json_playlist() -> None:
    result = formats.parse_probe_json(PLAYLIST_JSON)

    assert result.is_playlist is True
    assert result.playlist_count == 37
    assert result.formats == ()
    assert len(result.entries) == 1

    entry = result.entries[0]
    assert entry.id == "abc123"
    assert entry.title == "Entry Title"
    assert entry.url == "abc123"


def test_parse_probe_json_malformed_raises() -> None:
    with pytest.raises(ValueError):
        formats.parse_probe_json("{not valid json")


def test_parse_probe_json_non_object_raises() -> None:
    with pytest.raises(ValueError):
        formats.parse_probe_json("[1, 2, 3]")


def test_parse_probe_json_unrecognized_type_raises() -> None:
    with pytest.raises(ValueError):
        formats.parse_probe_json(json.dumps({"_type": "something_else"}))


def test_parse_probe_json_playlist_with_zero_entries() -> None:
    result = formats.parse_probe_json(
        json.dumps({"_type": "playlist", "playlist_count": 0, "entries": []})
    )

    assert result.is_playlist is True
    assert result.playlist_count == 0
    assert result.entries == ()
