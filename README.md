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

Run the installer from a checkout of this repository, using your regular system Python (not
Resolve's embedded interpreter):

```sh
python install.py
```

This resolves DaVinci Resolve's per-user Scripts directory for your OS and installs:

- the entry script into **Scripts/Utility** (so it shows up in Resolve's Scripts menu):
  - macOS: `~/Library/Application Support/Blackmagic Design/DaVinci Resolve/Fusion/Scripts/Utility`
  - Linux: `~/.local/share/DaVinciResolve/Fusion/Scripts/Utility` (falling back to
    `/opt/resolve/Fusion/Scripts/Utility` if the per-user directory doesn't exist, e.g. a
    system-wide install)
- the `resolve_ytdlp/` package into the sibling **Scripts/Modules** directory — Resolve's own
  convention for shared script code. This matters: Resolve's Scripts menu recursively lists every
  subdirectory as a submenu and every `.py` file inside it as its own runnable entry, so the
  package can't live alongside the entry script in Scripts/Utility without every internal module
  (`config`, `deps`, `downloader`, ...) showing up as a spurious menu item. Scripts/Modules isn't
  scanned for menu entries, only added to the Python path.

By default, `install.py` **symlinks** the entry script and package when run from inside a git
checkout (so edits to your working copy take effect immediately, no reinstall needed), and
**copies** them otherwise (e.g. from a downloaded release archive, as a standalone tree
independent of the original files). Force one or the other explicitly:

```sh
python install.py --mode symlink
python install.py --mode copy
```

Pass `--target-dir <path>` / `--modules-dir <path>` to install somewhere other than the
auto-detected Resolve directories (useful for a non-standard Resolve install location).

### Manual install (fallback)

If the installer fails, or Resolve changes its script directory layout, install by hand instead:

1. Find your DaVinci Resolve Scripts directory (see paths above; check Resolve's own
   preferences/install location if neither matches). It should contain (or you should create)
   `Utility` and `Modules` subdirectories.
2. Copy (or symlink) this repo's `scripts/Download from URL (yt-dlp).py` directly into
   `Scripts/Utility`.
3. Copy (or symlink) this repo's `resolve_ytdlp/` directory directly into `Scripts/Modules` (as a
   sibling of `Utility`, not inside it) — putting it in `Utility` instead will make Resolve's
   Scripts menu list every internal module as its own (broken) menu entry.

## Usage

1. In DaVinci Resolve, open **Workspace > Scripts > Utility > Download from URL (yt-dlp)**.
2. Paste a video (or playlist) URL into the URL field.
3. Pick a format preset (Best MP4, 1080p, 720p, Audio MP3, Audio best), or enter a custom yt-dlp
   `-f` selector. Optionally check "Show available formats" to fetch and display the exact format
   list for that URL first.
4. Adjust subtitle, metadata/thumbnail embedding, download directory, playlist item limit, and
   auto-import options as needed, then click **Download**.
5. If the URL is a playlist, you'll be asked to confirm the number of entries before it starts.
6. Progress is shown live in the window. Click **Cancel** at any point to stop the download and
   clean up its partial file.
7. When a download finishes, it's automatically imported into a `yt-dlp` bin in the current
   project's media pool (toggle this off with the auto-import checkbox). Importing requires a
   Resolve project to be open; if none is open, the file still downloads to disk, but you'll need
   to import it manually.

Settings persist between sessions. Full logs (including yt-dlp's own output and any errors) are
written to a rotating log file under your per-OS config directory: `~/Library/Application
Support/resolve-ytdlp/logs/` on macOS, `~/.config/resolve-ytdlp/logs/` on Linux — check there
first if something goes wrong that isn't clear from the window.

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
