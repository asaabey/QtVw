# QtVw

QtVw is a Python/PySide6 image browser for Ubuntu, with macOS development support.
See [SPEC.md](SPEC.md) for requirements and [IMPLEMENTATION.md](IMPLEMENTATION.md)
for the delivery plan.

Milestones 1–4 provide local folder navigation, a filesystem sidebar, a progressive
thumbnail grid, and an image viewer with fit, actual size, zoom, pan, fullscreen,
and previous/next navigation. ZIP archives have representative cover thumbnails
and can be browsed directly, including internal folders and Unicode filenames.
Thumbnails and archive indexes persist between sessions, and background jobs
prioritise the current view. Saved preferences include sorting, appearance,
startup behavior, cache controls, and resource limits. Ubuntu packaging and
release performance validation remain milestone 5.

## Development setup

Use Python **3.10 or newer**; the Python 3.9 supplied with some macOS installations
is too old. Run these commands from the repository root, replacing `python3.10`
with your supported interpreter if necessary:

```sh
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m qtvw
qtvw /path/to/photos
qtvw /path/to/photos/Holiday.zip
python -m qtvw --help
```

Qt needs a desktop session for interactive use. On Ubuntu, install the runtime
libraries required by Qt's platform plugin if launch reports missing libraries.
JPEG, PNG, WebP, BMP, and TIFF decoding depends on the Qt plugins available in the
installed PySide6 distribution. Ubuntu ARM64 and x86-64 builds require separate
validation; there are no distributable packages yet.

## Browsing ZIP archives

Double-click an archive to open it, then navigate its folders and images normally.
The location field displays paths such as `/photos/Holiday.zip › Day 1`; Up from
the archive root returns to its containing folder. Back/Forward preserve archive
locations, and the last location is restored on restart.

Covers prefer a supported `cover.*` image anywhere in the archive, then the first
decodable image in natural path order. Covers carry a ZIP badge. Archives without
a usable cover show a generic archive icon; hover over it for the failure reason.
Images are decompressed individually in memory without writing extracted files.
Encrypted, multi-disk, and nested ZIP archives are unsupported. Corrupt entries
and unsupported compression produce explanatory errors.

Archive limits are defined by `ArchiveLimits` in `sources/archive.py`: 50,000
entries and folders, a 16 MiB central directory, 64 MiB per decompressed entry,
64 path components, and 4,096 characters per path. Cover searches try at most 256
candidates with a total 128 MiB decompressed input budget. Unsafe paths and
symlink entries are excluded. The existing 64-million-pixel decode limit applies
to archived images too. Limits currently require code configuration; the settings
interface comes later.

## Caching and background work

The application stores PNG thumbnails and a SQLite metadata/index database under
Qt's per-user `CacheLocation`, in a `v1/` subdirectory. The cache is disposable;
source folders and archives are never modified. Thumbnail keys include source
path, size, nanosecond modification time, archive member position/CRC/size where
applicable, thumbnail size class, and generation policy versions.

Warm archive listings use the persisted index without opening the ZIP. Warm
thumbnails, including covers, avoid decompressing or decoding source images.
Uncached archive reads still use a worker-owned ZIP handle. Source fingerprint
changes invalidate related disk entries; file watching refreshes the displayed
folder or archive, preserving the opened image when it remains available.
Missing/corrupt cache entries regenerate, and storage failures fall back to
source reads and memory caches.

Two shared workers prioritise the opened image, listing, visible thumbnails,
nearby thumbnails, then adjacent-image preloading. The queue has 128 slots with
capacity reserved for foreground work. Navigation cancels obsolete work;
scrolling cancels queued thumbnails that are no longer nearby. Running decoder
calls finish cooperatively, and obsolete results are discarded.

Shared image caches have byte limits of 32 MiB for thumbnails and 128 MiB for
full images. Full images stay in memory only. A separate 256 MiB budget bounds
estimated concurrent decode output; archive input buffers and displayed pixmaps
have their own bounds. The browser keeps at most 256 display pixmaps.

The disk cache uses least-recently-used eviction with a 512 MiB default limit.
Cleanup accounts for thumbnail files, SQLite, and its journal/shared-memory files;
it checkpoints and compacts metadata and removes abandoned cache files older than
five minutes. Recent files have a grace period for writes from another process.
If overhead or recent files prevent meeting the limit, cleanup reports the problem.
Cache access timestamps are coalesced over 30-second intervals to reduce writes.

## Preferences and controls

Open **Preferences** in the toolbar or press `Ctrl+,`. Choose whether startup
restores the last location or opens a default folder, and set thumbnail size,
hidden-file visibility, sort field/direction, and system/light/dark appearance.
Name sorting is natural; folders stay first in either direction. The toolbar
also provides immediate sort controls and the hidden-files toggle.

The **Cache and limits** tab sets the disk cache budget (16 MiB–64 GiB) and offers
**Clear cache**. Cache work runs in the background, and failures appear in the
status area while image browsing remains available. Clear invalidates older jobs
so they cannot repopulate removed entries. Already displayed images remain visible;
new browsing regenerates cached data as needed.

Maximum decoded pixels, image input size, decompressed ZIP entry size, and worker
count are configurable within bounded ranges. These resource settings take effect
on the next launch; browsing, appearance, and cache settings apply immediately.
Saving an invalid default folder displays an inline error.

Use `Ctrl+O` to choose a folder; `Alt+Left`/`Alt+Right` for history; `Alt+Up` for the
parent; Enter to open; and `F5` to refresh. In the viewer, Left/Right change images,
`0` fits, `1` shows actual size, `+`/`-` zoom, and the mouse wheel zooms. Drag to pan.
`F11` toggles fullscreen; Esc exits fullscreen, then returns to the grid.
Backspace also returns to the grid from the viewer. Press **F1** or click **Help**
for the full shortcut list, mouse controls, and the log folder location.

## Diagnostic logging

Logging starts automatically when launching `qtvw` or `python -m qtvw`. It records
startup details, navigation, renderer changes, Qt messages, and errors in JSON-line
files under Qt's per-user application data directory, in `logs/`. **Help** displays
the actual path and provides **Copy log folder**.

Files retain approximately the last hour, with an additional **8 MiB total cap**.
Minute-sized time buckets are cleaned on startup, after writes, and every 30 seconds
while idle. Logging runs on a background thread with a bounded queue; overload
drops messages, and file failures do not interrupt browsing. Help reports logging
failures or dropped messages. Logs include local file paths and error details.

## Image rendering

**Preferences → Browsing → Smooth image scaling** enables interpolation when
fitting or zooming images and defaults to on. Disable it for crisp pixel art.
This affects display only; blur, denoise, and sharpening filters are not available.

**Preferences → Browsing → Image rendering** offers **Auto** (the default) and
**Software**. Auto probes for a usable OpenGL context, then uses an OpenGL viewport
for image zooming and panning. Unavailable contexts, unsupported headless displays,
missing OpenGL widget support, or detected initialization/context failures fall
back to software. The current image, zoom, and pan survive a renderer change.
The status bar shows **Viewer: OpenGL** or **Viewer: Software**; hover for details.
Preferences also shows the active backend and any fallback reason.

Rendering changes apply immediately. To force software for one launch without
overwriting the saved preference:

```sh
qtvw --software-rendering /path/to/photos
```

OpenGL uses the installed graphics driver, which may itself use software rendering.
This option accelerates viewer drawing; image decoding, ZIP decompression,
thumbnail generation, and the thumbnail grid retain their existing pipelines.
Performance depends on the machine and driver; select Software if it works better.

Compare steady-state pan/zoom rendering on a native display:

```sh
python tools/benchmark_viewer.py --frames 120 --width 4000 --height 3000
```

The tool generates an image in memory, warms up each renderer, and prints timing
and renderer details. OpenGL samples synchronize GPU work and may include vsync;
these are synthetic timings rather than decoding or end-to-end browsing results.
The local Apple M5 Max run verified GPU rendering but did not demonstrate a speedup.

## Checks

```sh
python -m pytest
QT_QPA_PLATFORM=offscreen python -m pytest
python -m ruff check .
python -m ruff format --check .
```

To additionally check actual OpenGL rendering on a native display:

```sh
QTVW_TEST_OPENGL=1 python -m pytest -q tests/gui/test_rendering.py::test_native_opengl_framebuffer_and_software_switch
```

The normal headless suite exercises software/fallback behavior and skips this
native-only check. It uses context/paint-engine checks and captures its own window
to verify the presented image; native window capture must be supported.

The offscreen command supports headless GUI checks. Interactive testing remains
necessary for native desktop behavior, panning, fullscreen, and responsiveness.
No coverage threshold is configured.

A user reported successful testing on **Ubuntu 25.10 with Python 3.13**.
Architecture, graphics driver, and release performance measurements remain unverified.

## Layout

- `src/qtvw/domain/`: entries, archive locations, and natural sorting.
- `src/qtvw/sources/`: filesystem enumeration and bounded ZIP access.
- `src/qtvw/services/`: navigation state, background enumeration, and image decoding.
- `src/qtvw/cache/`: byte-bounded image caches, SQLite metadata, and thumbnail files.
- `src/qtvw/ui/`: browser model, main window, and image viewer.
- `tests/`: unit and Qt GUI regression checks.

Keep image decoding and directory enumeration outside the GUI thread. Workers
produce `QImage` data; the GUI owns display pixmaps and rejects stale results.
