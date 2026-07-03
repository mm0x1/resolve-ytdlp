# resolve-ytdlp

A DaVinci Resolve Utility script that downloads a video via [yt-dlp](https://github.com/yt-dlp/yt-dlp)
and imports it directly into a media-pool bin, without leaving Resolve.

## Requirements

- DaVinci Resolve (macOS or Linux), with its embedded Python interpreter and scripting API enabled.
- [`yt-dlp`](https://github.com/yt-dlp/yt-dlp) installed and discoverable (on `PATH`, in a common
  install location, or configured with an explicit path).
- [`ffmpeg`](https://ffmpeg.org/) (+ `ffprobe`) installed and discoverable, for format merging and
  audio extraction.

## Install

_TODO: documented in a later PR alongside `install.py`._

## Usage

_TODO: documented in a later PR alongside the GUI entry point._

## Development

This repository ships two kinds of code:

- `resolve_ytdlp/` — runtime code that runs inside Resolve's embedded Python interpreter. It must
  import **only the Python standard library**, since `pip install` is not available in that
  environment.
- `tests/` — developer-only tests, run with `pytest` on a regular system Python. Dev dependencies
  (`pytest`, `ruff`) are declared in `pyproject.toml` under `[project.optional-dependencies].dev`
  and are never imported by runtime code.

```sh
pip install -e ".[dev]"
pytest -q
ruff check .
```

## License

MIT — see [LICENSE](LICENSE).
