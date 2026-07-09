"""Build yt-dlp argv lists for downloads and format/playlist probing.

Pure logic only: translates the curated presets / custom `-f` field / probe JSON
into `argv` lists and parsed dataclasses. No subprocess execution happens here —
that's `downloader` (PR-2b), which runs what this module builds.

Stdlib only — this module runs inside Resolve's embedded Python interpreter.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from resolve_ytdlp.config import Settings
from resolve_ytdlp.deps import ResolvedDeps

# Must match downloader.parse_progress_line's sentinel constants (PR-2b) — these
# prefix our structured JSON progress lines so they can be told apart from
# yt-dlp's ordinary human-readable stdout chatter.
DL_SENTINEL = "RYTDLP_DL_JSON:"
PP_SENTINEL = "RYTDLP_PP_JSON:"

# Prefixes yt-dlp's `--print after_move:...` output: the definitive final file
# path, emitted once postprocessing (merging separate video/audio streams,
# audio extraction, thumbnail/metadata embedding, etc.) has finished and the
# file has been moved to its final location. Needed because the *download*
# phase's `filename`/`tmpfilename` (DL_SENTINEL above) name whichever stream
# was downloaded most recently (e.g. a `bv*+ba` selector's separate video and
# audio streams) — not the merged output — so tracking those alone yields the
# wrong (and often already-deleted) file for auto-import. Confirmed via a real
# Resolve session: auto-import failed trying to decode a `.f140.m4a` audio
# stream file that yt-dlp had already deleted after merging it into the final
# `.mp4`. Must match downloader.parse_final_filename_line's constant.
DONE_SENTINEL = "RYTDLP_DONE_JSON:"


@dataclass(frozen=True)
class FormatPreset:
    selector: str
    extra_args: tuple[str, ...] = ()


FORMAT_PRESETS: dict[str, FormatPreset] = {
    "best_mp4": FormatPreset(
        selector="bv*[ext=mp4]+ba[ext=m4a]/b[ext=mp4]/b",
    ),
    "1080p": FormatPreset(
        selector="bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b",
    ),
    "720p": FormatPreset(
        selector="bv*[height<=720][ext=mp4]+ba[ext=m4a]/b[height<=720][ext=mp4]/b",
    ),
    "audio_mp3": FormatPreset(
        selector="ba/b",
        extra_args=("--extract-audio", "--audio-format", "mp3"),
    ),
    "audio_best": FormatPreset(
        selector="ba/b",
        extra_args=("--extract-audio", "--audio-format", "best"),
    ),
}


def resolve_format_selector(settings: Settings) -> FormatPreset:
    """Resolve the effective format selector for a download.

    ``settings.custom_format``, if set, wins over the preset table with no
    extra args. Otherwise ``settings.format_preset`` is looked up in
    :data:`FORMAT_PRESETS`. An unknown preset key raises ``ValueError`` — a
    corrupt/hand-edited settings file is a caller bug to surface, not a case
    to silently paper over (matches ``deps.find_binary``'s explicit-failure
    philosophy).
    """
    if settings.custom_format:
        return FormatPreset(selector=settings.custom_format)

    try:
        return FORMAT_PRESETS[settings.format_preset]
    except KeyError:
        raise ValueError(f"Unknown format preset: {settings.format_preset!r}") from None


def build_download_argv(deps: ResolvedDeps, settings: Settings, url: str) -> list[str]:
    """Build the full yt-dlp argv for downloading a single item at ``url``."""
    preset = resolve_format_selector(settings)

    argv: list[str] = [str(deps.ytdlp), "-f", preset.selector, *preset.extra_args]

    if deps.ffmpeg is not None:
        argv += ["--ffmpeg-location", str(deps.ffmpeg)]

    output_template = str(Path(settings.download_dir) / "%(title).200B [%(id)s].%(ext)s")
    argv += ["-o", output_template]

    argv += ["--newline"]
    argv += ["--progress-template", f"download:{DL_SENTINEL}%(progress)j"]
    argv += ["--progress-template", f"postprocess:{PP_SENTINEL}%(progress)j"]
    # `--print` implies `--quiet` (which would silence the progress-template
    # output above) unless explicitly countered — confirmed against yt-dlp's
    # own option-handling (opts.quiet only auto-set `if opts.quiet is None`).
    argv += ["--print", f"after_move:{DONE_SENTINEL}%(filepath)j"]
    argv += ["--no-quiet"]

    if settings.embed_metadata:
        argv.append("--embed-metadata")
    if settings.embed_thumbnail:
        argv.append("--embed-thumbnail")

    if settings.write_subs:
        argv += [
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs",
            settings.sub_langs,
            "--embed-subs",
        ]

    argv.append("--no-playlist")
    argv.append(url)

    return argv


def build_probe_argv(deps: ResolvedDeps, url: str) -> list[str]:
    """Build the argv for a structured (JSON) format/playlist probe of ``url``."""
    return [str(deps.ytdlp), "-J", "--flat-playlist", url]


# Audio codec re-encoded into downloads for DaVinci Resolve on Linux. Resolve on
# Linux can decode neither AAC nor Opus (proprietary codec licensing), so a
# normal download imports with silent audio. FLAC was confirmed decodable in a
# real Resolve session and is lossless — no further quality loss on top of the
# already-lossy source — and mux-compatible with the .mp4/.mkv/.mov containers
# these presets produce. See `downloader.transcode_audio_for_resolve`.
RESOLVE_AUDIO_CODEC = "flac"

# Force 16-bit FLAC. Confirmed empirically against a real Resolve session on
# Linux: 24-bit FLAC (ffmpeg's default when the decoded source is high-precision
# — e.g. AAC decodes to float) imports *silent*, while the bit-identical 16-bit
# FLAC plays. (24-bit *PCM* decodes fine there, so this is a FLAC-decoder depth
# limit, not a general 24-bit problem.) 16-bit is CD quality and the source is
# already lossy, so there is no meaningful loss.
RESOLVE_AUDIO_SAMPLE_FMT = "s16"


def build_audio_transcode_argv(deps: ResolvedDeps, source: Path, dest: Path) -> list[str]:
    """Build the ffmpeg argv that copies video and re-encodes audio to 16-bit FLAC.

    ``-map 0 -c copy`` passes every stream through untouched (so the H.264
    video is never re-encoded — fast and lossless); ``-c:a``/``-sample_fmt``
    then override just the audio streams to 16-bit :data:`RESOLVE_AUDIO_CODEC`.
    ``-y`` overwrites ``dest`` if a stale temp file is present.
    """
    return [
        str(deps.ffmpeg),
        "-y",
        "-i",
        str(source),
        "-map",
        "0",
        "-c",
        "copy",
        "-c:a",
        RESOLVE_AUDIO_CODEC,
        "-sample_fmt",
        RESOLVE_AUDIO_SAMPLE_FMT,
        str(dest),
    ]


@dataclass(frozen=True)
class FormatInfo:
    format_id: str
    ext: str
    resolution: str | None
    vcodec: str | None
    acodec: str | None
    filesize: int | None
    format_note: str | None


def custom_format_selector_for(fmt: FormatInfo) -> str:
    """Build a `-f` selector for one probed format picked from the formats list.

    yt-dlp reports a missing stream as the literal string `"none"` (not
    absent/`None`) in its probe JSON — confirmed via a real download where
    picking a video-only format's bare `format_id` (e.g. `"400"`, a
    video-only AV1 stream) produced a video with no audio track at all, since
    nothing paired it with an audio stream. Video-only formats (`acodec` is
    falsy/`"none"`) are paired with `+ba[ext=m4a]/ba` — audio constrained to
    m4a (AAC) if available, falling back to plain best-audio otherwise —
    mirroring the `FORMAT_PRESETS` table's own `bv*[...]+ba[ext=m4a]/...`
    selectors. Confirmed via a real download that a bare `+ba` isn't enough:
    it picked a higher-bitrate WebM/Opus audio stream over an available m4a
    one, and DaVinci Resolve can't decode Opus — it silently imports the
    video with no audio track, even though the merged file (verified via
    `ffprobe`) genuinely has both streams. Audio-only or already-muxed (both
    streams present) formats are used as-is.
    """
    has_video = fmt.vcodec not in (None, "none")
    has_audio = fmt.acodec not in (None, "none")
    if has_video and not has_audio:
        return f"{fmt.format_id}+ba[ext=m4a]/ba"
    return fmt.format_id


@dataclass(frozen=True)
class PlaylistEntry:
    id: str
    title: str | None
    url: str


@dataclass(frozen=True)
class ProbeResult:
    is_playlist: bool
    playlist_count: int | None
    entries: tuple[PlaylistEntry, ...]
    formats: tuple[FormatInfo, ...]


def _parse_formats(raw_formats: list[dict[str, Any]]) -> tuple[FormatInfo, ...]:
    return tuple(
        FormatInfo(
            format_id=f["format_id"],
            ext=f["ext"],
            resolution=f.get("resolution"),
            vcodec=f.get("vcodec"),
            acodec=f.get("acodec"),
            filesize=f.get("filesize"),
            format_note=f.get("format_note"),
        )
        for f in raw_formats
    )


def _parse_entries(raw_entries: list[dict[str, Any]]) -> tuple[PlaylistEntry, ...]:
    return tuple(
        PlaylistEntry(id=e["id"], title=e.get("title"), url=e["url"]) for e in raw_entries
    )


def parse_probe_json(raw: str) -> ProbeResult:
    """Parse ``yt-dlp -J --flat-playlist`` output into a :class:`ProbeResult`.

    Branches on the info-dict's ``_type``: ``"video"`` populates ``formats``;
    ``"playlist"`` populates ``playlist_count``/``entries``. Malformed JSON,
    a non-object top level, an unrecognized ``_type``, or a missing expected
    field all raise ``ValueError`` — a probe that can't be parsed is surfaced
    to the caller, not silently papered over.
    """
    try:
        data: Any = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed probe JSON: {exc}") from exc

    if not isinstance(data, dict):
        raise ValueError("Probe JSON must be a JSON object")

    kind = data.get("_type")

    try:
        if kind == "video":
            return ProbeResult(
                is_playlist=False,
                playlist_count=None,
                entries=(),
                formats=_parse_formats(data.get("formats", [])),
            )

        if kind == "playlist":
            return ProbeResult(
                is_playlist=True,
                playlist_count=data.get("playlist_count"),
                entries=_parse_entries(data.get("entries", [])),
                formats=(),
            )
    except (KeyError, TypeError) as exc:
        raise ValueError(f"Malformed probe JSON: missing expected field {exc}") from exc

    raise ValueError(f"Unrecognized probe JSON _type: {kind!r}")
