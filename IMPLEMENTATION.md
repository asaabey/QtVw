# QtVw Implementation Plan

## Purpose and Status

This document translates [SPEC.md](SPEC.md) into an implementation plan. Milestones 1–4 now include filesystem/ZIP browsing, persistent caches, prioritised workers, a shared viewer, saved preferences, and cache/error recovery. Validation results are recorded below; Ubuntu packaging and release performance validation remain milestone 5. `SPEC.md` remains the source of product requirements.

The initial release targets Ubuntu Desktop, with macOS used for development. Image editing, nested archives, archive modification, cloud storage, animation, HEIC, and RAW decoding remain outside scope.

## Repository Structure and Planned Extensions

```text
pyproject.toml              # Dependencies, entry point, test and lint configuration
src/qtvw/
  __main__.py              # Application startup
  app.py                   # Service construction and shutdown
  domain/                  # Locations, entries, sorting, resource limits
  ui/                      # Main window, browser model/delegate, viewer, preferences
  sources/                 # Filesystem and ZIP enumeration and image access
  services/                # Navigation, scheduling, decoding, thumbnail generation
  cache/                   # SQLite metadata, thumbnail files, memory caches, eviction
  platform/                # Platform-specific integration
  assets/                  # Icons and Qt resources
tests/
  unit/
  integration/
  gui/
packaging/                 # Packaging configuration and platform instructions
```

Use Python with PySide6 and Qt Widgets, standard-library `zipfile` and `sqlite3`, and Qt image decoders. Introduce `pytest`, `pytest-qt`, and Ruff as development tools. The package requires Python 3.10 or newer and PySide6 6.7–6.x; `pyproject.toml` declares compatible development dependency ranges. Platform, assets, and packaging modules in the layout are planned extensions.

## Architecture and Data Flow

### Domain and source access

- Represent a location as a filesystem directory or an archive path plus an internal POSIX directory. Keep display text separate from location identity.
- Represent entries with a stable identity, kind, name, source location, size, modification metadata, and optional archive member identity.
- Provide source operations for listing immediate children and opening bounded image data. Keep source code independent of widgets.
- Enumerate filesystem directories progressively without recursive startup scans. Follow directory links only through explicit navigation; avoid recursive traversal loops.
- Index ZIP entries in a worker, synthesising missing directory entries. Preserve Unicode names and distinguish duplicate member names using entry metadata or position.
- Define natural sorting centrally, with stable tie-breaking and consistent treatment of unavailable size/date metadata. The viewer uses the same ordered image collection as the grid.

### User interface

Use a main window with a navigation toolbar, filesystem sidebar, stacked browser/viewer area, and status bar. Use a lazy filesystem tree and a `QListView` backed by a custom item model and delegate for the thumbnail grid. Avoid one widget per entry.

A navigation controller owns history, current location, selection, scroll position, and a monotonically increasing request generation. Returning from the viewer restores browser state. Opening an archive root and moving up returns to its containing filesystem directory.

Use a graphics view or equivalent transform-based image widget for fit, actual size, zoom, pan, and fullscreen. Implement shortcuts from SPEC §12 through shared actions. Persist preferences and window state using `QSettings`; locate caches using `QStandardPaths`.

### Background work

Introduce basic worker scheduling in the first milestone so filesystem enumeration and decoding never block the UI. Extend it with priority and persistent caching in milestone 3.

1. Navigation increments the request generation and cancels queued obsolete work.
2. Workers emit listing batches and image results through queued signals.
3. The controller accepts a result only when its generation and source identity still match.
4. The scheduler prioritises the opened image, visible thumbnails, nearby thumbnails, then optional preloading.

Deduplicate jobs by source fingerprint and requested output. Bound worker count, queued work, compressed input buffers, and estimated decoded memory. Check cancellation between operations; in-progress decoder calls may finish, but obsolete results must be discarded.

Decode into `QImage` in workers; create display `QPixmap` objects and update models on the GUI thread. Do not share active ZIP handles or SQLite connections across worker threads. Use worker-owned archive handles and a dedicated cache database owner.

## Image and Archive Processing

Use `QImageReader` with orientation transforms and scaled decoding where supported. Probe image dimensions before decoding, apply allocation limits, and verify decoder availability in packaged builds. Read only the first frame/page for supported static formats.

Read archive members directly without extracting the archive. Enforce both declared and actual byte limits while reading; reject unsafe absolute or parent-traversal member paths from the navigable index. Limit archive entry counts and total index memory, and configure decoded-pixel and background-work budgets. Treat encrypted or unsupported archives as explanatory errors.

For covers, try supported images whose filename stem equals `cover`, case-insensitively, in deterministic natural path order. If none decode, try remaining images in natural path order, including subfolders. Use a generic archive icon if all candidates fail, and overlay an archive badge on successful covers.

Check source fingerprints before and after expensive work. If a file changes during reading, discard the result and refresh or retry with a bounded policy. Item failures produce placeholders or status messages; blocking navigation failures may use dialogs.

## Cache Design

Use bounded memory caches for thumbnails and full images, thumbnail files on disk, and SQLite for metadata and archive indexes. Full images are not persisted by default.

Proposed records:

| Record | Essential fields |
|---|---|
| Source | Normalised path, size, modification time in nanoseconds, source type |
| Archive index | Source fingerprint, index version, member identity/path, CRC, sizes, timestamp |
| Thumbnail | Key, source fingerprint, member identity/CRC/size, size class, generator version, relative file path, byte size, last access |
| Database metadata | Schema version and migration state |

Hash a canonical representation of thumbnail identity to derive cache filenames. Include standalone/archive source identity, archive member metadata where applicable, output size class, and generator version. Cover keys must also identify the archive fingerprint and cover-selection version.

Write thumbnail files through temporary files and atomic replacement, then commit metadata. Recover from incomplete writes, missing files, corrupt thumbnails, and unavailable cache storage by regenerating or operating without disk caching. Batch access-time updates.

Invalidate associated indexes and thumbnails when source fingerprints change. Evict least-recently-used disk entries until the configured budget is met, accounting for index/database storage and cleaning orphan files. Clear-cache must coordinate with active jobs using a cache generation so old jobs cannot repopulate cleared data. Version schemas and rebuild disposable cache data when migration is unnecessary.

## Delivery Milestones

### 1. Project foundation and local browsing

**Status:** implemented for local files; automated and interactive validation must be confirmed before declaring the milestone complete.

- Add packaging metadata, entry point, development tools, and installation instructions.
- Implement domain types, natural sorting, filesystem listing, basic workers, and navigation history.
- Build the main window, folder sidebar, virtualised grid, and basic image viewer.
- Support fit, actual size, zoom, pan, fullscreen, next/previous, and browser-state restoration.

**Exit:** folders and supported standalone images work progressively; enumeration and decoding run outside the GUI thread. Verify SPEC acceptance criteria 1, 4, and 5 for ordinary files.

### 2. ZIP browsing and covers

**Status:** implemented and tested offscreen on macOS ARM64. Native Ubuntu validation remains pending; persistent disk caches belong to milestone 3.

- Add bounded ZIP indexing, implicit directories, archive navigation, and member decoding.
- Implement deterministic cover selection, badges, and unsupported/corrupt archive feedback.
- Reuse the browser and viewer for archive entries without full extraction.

**Exit:** criteria 2 and 3 work functionally; disk reuse follows in milestone 3. Cover Unicode paths, empty archives, missing directory records, duplicate names, and damaged entries in tests.

### 3. Scheduling and persistent caching

**Status:** implemented and tested offscreen on macOS ARM64, including fresh-process reuse and automatic source-change refresh. Native Ubuntu validation remains pending.

- Add priority queues, job deduplication, cancellation, and adjacent-image preloading.
- Implement SQLite indexes, thumbnail files, cache fingerprints, and bounded memory caches.
- Reject stale navigation results and invalidate changed sources.

**Exit:** criteria 2, 6, 7, and 8 pass, including reuse across restarts and archive modification during active work.

### 4. Preferences, limits, and recovery

**Status:** implemented and tested offscreen on macOS ARM64. Native Ubuntu validation remains pending.

- Add settings, appearance selection, hidden-file handling, complete shortcuts, and refresh.
- Implement disk-budget cleanup, clear-cache coordination, and storage-failure recovery.
- Complete resource limits and error handling for missing folders, permission failures, oversized images, and unsupported archives.

**Exit:** criteria 9 and 10 pass; preferences survive restart and all specified keyboard actions work.

### 5. Ubuntu packaging and performance

- Select and document a packaging approach after a PySide6 bundling prototype.
- Build and test separately on Ubuntu ARM64 and x86-64; verify Qt platform plugins, image codecs, icons, and launch integration.
- Measure cold/warm browsing, archive indexing, thumbnail throughput, UI responsiveness, peak memory, and disk usage on a documented collection containing thousands of images.
- Set numerical performance targets from prototype measurements and resolve regressions before release.

**Exit:** all eleven SPEC acceptance criteria pass on each supported Ubuntu architecture.

## Verification and Development Workflow

### Milestone 1 validation

Validated on macOS ARM64 with Python 3.12.15 and PySide6 6.12.0:

- Editable installation and both `python -m qtvw --help` and `qtvw --help` work.
- `QT_QPA_PLATFORM=offscreen python -m pytest -q`: **13 passed**.
- `python -m ruff check .` and `python -m ruff format --check .`: passed.
- An offscreen application smoke check generated and displayed JPEG, PNG, WebP,
  BMP, and TIFF thumbnails, captured the main window, and shut down cleanly.
- Regression checks cover natural sorting, batched enumeration, cancellation,
  invalid/corrupt inputs, decode limits, viewer transitions, far scrolling,
  thumbnail resizing during enumeration, stale results, and shortcut contexts.

Milestone 1 code is implemented. Native Ubuntu validation for SPEC criteria 1,
4, and 5 remains pending, including interactive pan/fullscreen behavior and
large-collection responsiveness.

### Milestone 2 validation

Validated on macOS ARM64 with Python 3.12.15 and PySide6 6.12.0:

- The full offscreen suite passes **30 tests**; Ruff lint and formatting checks pass.
- ZIP navigation supports explicit/implicit folders, Unicode paths, duplicate
  member names, Back/Forward/Up, address-field navigation, refresh, and restoring
  the last archive location. Viewer navigation follows the grid's natural order.
- Cover tests verify `cover.*` preference, natural ordering across subfolders,
  corrupt-image fallback, generic placeholders, visible badges, and byte budgets.
- Generated fixtures cover empty/damaged archives, genuine ZIP64 end records,
  encrypted flags, unsupported compression, CRC failures, changed sources,
  unsafe paths, index limits, entry-size limits, and excessive folder depth.
- An offscreen smoke check rendered an archive cover, internal folder thumbnails,
  and an image in the viewer, then shut down without extracting any files.

`sources/archive.py` owns ZIP preflight, bounded indexing, and incremental member
reads. Typed `ArchiveLocation` values keep archive folders separate from filesystem
paths. `Entry` identities include member position so duplicate names read the
correct ZIP entry. Each worker retains at most one archive handle/index, checks
source size/mtime, and closes it on replacement or shutdown. Handles are never
shared across worker threads.

`ArchiveLimits` documents configurable code-level defaults; see README for values.
Cover searches stop at configured candidate/byte budgets and use a generic icon
when no suitable image can be read within them. Image decoding retains existing
pixel/allocation limits. This milestone initially provided in-memory reuse;
milestone 3 adds persistent thumbnail/index caches and shared scheduling below.
Native Ubuntu testing and large-collection performance measurements remain pending.

### Milestone 3 validation

Validated on macOS ARM64 with Python 3.12.15 and PySide6 6.12.0:

- The full offscreen suite passes **52 tests**; Ruff lint and formatting checks pass.
- A fresh Python process reuses both a persisted ZIP index and cover while ZIP
  enumeration and source-image decoding are disabled in the test.
- Tests cover missing/damaged thumbnails, malformed index records, corrupt SQLite
  startup recovery, unavailable storage, changed sources, and stricter resource
  policies. Changes during decoding produce no stale image or thumbnail write.
- Queue tests cover priority/FIFO ordering, promotion, reserved foreground slots,
  deduplication, offscreen cancellation, queue bounds, and the decode memory budget.
- GUI tests confirm automatic cover/viewer refresh after atomic ZIP replacement
  and adjacent-image preloading into the full-image memory cache.
- A temporary-cache smoke check against `play/macst.zip` and `play/retro-games.zip`
  produced zero ZIP index builds and zero source decodes on the warm pass. These
  small fixtures establish reuse, not representative performance targets.

`cache/store.py` serialises SQLite access on one owner thread. PNG thumbnail files
are atomically replaced before metadata commits; source fingerprints invalidate
associated indexes and thumbnails. JSON index records are versioned, validated,
and rebuilt on corruption. Full images are never written to the disk cache.

`services/scheduler.py` supplies two shared workers and a 128-slot priority queue.
Foreground image/listing work has reserved admission capacity; visible thumbnails
precede nearby ones and adjacent-image preloading. Repeated requests promote an
existing job. Navigation cancels obsolete jobs, viewport changes drop queued
offscreen thumbnails, and generation checks reject stale results. Cancellation
cannot interrupt an active Qt decoder call, but its result is discarded.

Default shared memory limits are 32 MiB of thumbnail images, 128 MiB of full images,
and 256 MiB of estimated concurrent decode output. ZIP input buffers and GUI
pixmaps remain separately bounded. Disk payload LRU eviction initially defaulted to 512 MiB;
milestone 4 below adds physical accounting, compaction, preferences, and the
clear-cache UI. The clear operation uses a cache epoch
to reject writes from jobs created before the clear.

Native Ubuntu checks for SPEC criteria 2, 6, 7, and 8 and large-collection
performance measurements remain pending.

### Milestone 4 validation

Validated on macOS ARM64 with Python 3.12.15 and PySide6 6.12.0:

- The full offscreen suite passes **61 tests**; Ruff lint and formatting checks pass.
- Preferences persist startup location, thumbnail size, hidden files, sort field
  and direction, appearance, disk budget, pixel/input/entry limits, and worker count.
  Browsing/appearance/cache changes apply immediately; resource limits apply on restart.
- Tests verify saved startup/resource behavior, invalid-folder validation,
  cancellation without changing settings, date/size sorting and viewer order,
  Enter activation, fullscreen/Esc transitions, and the preferences shortcut.
- Asynchronous clear-cache tests retain the current view, remove disk thumbnails,
  reject older writes, and regenerate on later browsing.
- Physical cleanup tests count SQLite/WAL/shared-memory files and thumbnail files,
  enforce a smaller disk limit, remove old orphan/temporary files, and compact
  metadata. Recent orphan files retain a five-minute grace period for other writers;
  an unmet limit produces an explicit status result.
- Simulated full-disk and unavailable-storage tests preserve image browsing and
  clean up incomplete writes. Existing tests cover corrupt files/archives, missing
  folders, unsupported compression, and oversized/deep archive content.
- A UI smoke check applied dark appearance and a 16 MiB budget while rendering
  the local sample archive covers and shutting down all services cleanly.

`domain/preferences.py` validates settings; `ui/preferences.py` provides the form
and palettes. Sort controls share the browser model's order with viewer navigation.
Cache maintenance runs on the database owner thread and reports completion through
GUI polling, avoiding disk scans and compaction on the UI thread. Entry activation
handles both Enter keys explicitly so it does not depend on platform defaults.

Native Ubuntu checks for SPEC criteria 9 and 10, native theme behavior, keyboard
integration, and interactive responsiveness remain pending for milestone 5.

### Optional OpenGL viewer

Implemented Auto/Software rendering as a saved preference, with immediate changes
and a session-only `--software-rendering` CLI override. The existing Qt Widgets
grid and background processing remain unchanged.

Auto probes an OpenGL context and surface on the GUI thread before constructing
`ui/opengl.py`'s `QOpenGLWidget`. The graphics view uses full viewport updates for
OpenGL and regular updates for software, following the
[Qt graphics-view guidance](https://doc.qt.io/qt-6/qgraphicsview.html).
The viewport checks initialization and context validity while visible. Detected
failures schedule a software replacement outside the viewport event handler,
preserving the scene image, zoom, and pan. Unsupported headless platforms skip GL
creation. The status bar and preferences show the actual backend/fallback reason.

Validation on macOS ARM64, Python 3.12.15, PySide6 6.12.0:

- Offscreen suite: **68 passed, 1 skipped**; the skip requires a native display.
- Native Cocoa OpenGL test: **1 passed**, including context validity, presented
  image pixels, zoom/pan, and switching back to software.
- Native main-window smoke: Apple M5 Max renderer, image navigation, fullscreen,
  live software/Auto switches, and grid return all passed.
- Tests cover forced software, headless probing, unavailable widgets/contexts,
  simulated late context failure, pending-fallback cancellation, saved preferences,
  CLI override, and preserving the view across renderer changes.
- Ruff lint and formatting pass. Ubuntu driver/backend validation remains pending.

`tools/benchmark_viewer.py` provides a reproducible synthetic pan/zoom comparison.
An Apple M5 Max run with a 4000×3000 generated image, a 1200×800 window, 20 warmup
frames, and 120 measured frames produced:

| Mode | Median frame time | 95th percentile | Process CPU time |
|---|---|---|---|
| Software | 0.838 ms | 1.186 ms | 0.128 s |
| OpenGL | 16.831 ms | 20.934 ms | 0.401 s |

The OpenGL loop synchronizes GPU work and includes Qt event handling/potential
display pacing; software submission does not use a GPU completion barrier.
These timings do not establish a user-visible speedup or an end-to-end browsing
comparison. Keep both rendering modes available and measure on target Ubuntu
hardware before making performance claims.

### Follow-up: diagnostics, help, and smooth scaling

Application startup now enables asynchronous JSON-line logging for navigation,
renderer changes, Qt messages, and failures. A bounded queue feeds a single writer;
minute segments retain about one hour and at most 8 MiB. Cleanup also runs every
30 seconds while idle. Help shows the actual log folder, logging status, and a
copy-path button. File failures leave browsing available.

F1 and the toolbar Help button show shortcuts derived from live QAction bindings,
their contexts, and mouse controls. Backspace now works in the image viewer as
well as the browser. Smooth image scaling is enabled by default and saved in
Preferences. Explicit pixmap transformation mode fixes the previous render hint
being overridden by Qt's default fast pixmap mode. Disabling smoothing preserves
source pixels and zoom state; image-editing filters remain outside this change.

Validation: **76 passed, 1 skipped** with the offscreen platform; **1 passed** for
the native Cocoa OpenGL check. New tests cover retention during idle time, noisy
logging size limits, queue overflow, unwritable storage, Qt messages and exception
hooks, actual shortcut bindings, viewer Backspace, interpolated output pixels,
and saved smoothing preferences. Ruff lint and formatting pass.

The user reported successful testing on Ubuntu 25.10 with Python 3.13. Architecture,
driver details, and release performance acceptance remain unverified.

### Follow-up: folder descent and a per-user Linux installer

Opening a folder from the grid, sidebar, or chooser follows single-folder chains
using completed asynchronous listings. Local and ZIP locations stop at images,
multiple visible items, errors, empty directories, repeated targets, or 64 visited
locations. Intermediate locations replace the current history entry so Back
returns to the origin. Up, refresh, startup, and history loads retain their exact
locations. Six GUI regression cases cover descent and stopping/history behavior.

`install.sh` now invokes `packaging/install.py`: a private venv, a non-editable
source install, a shell launcher, and a freedesktop app-menu entry. Rerunning
updates the installation; `--uninstall` removes installer-managed files and keeps
preferences, caches, logs, and source images. Unmanaged destinations are refused.
Installation stages only package sources and metadata, excluding sample files.
This is a source installer, not completion of standalone release packaging.

Validation on macOS: **87 passed, 1 skipped**, Ruff checks, and shell syntax pass.
Installer tests mock dependency installation and verify layout, updates, launcher
argument handling with spaces, removal, and unmanaged-file protection. A real
dependency-free wheel install in a fresh venv verifies both CLI help entry points
after removing the staging source. Native Ubuntu dependency installation, desktop
launch integration, and separate architecture validation remain pending.

### Follow-up: multi-select image deletion inside ZIPs

The grid uses extended selection: Ctrl+click toggles individual items, Shift+click
selects ranges, and Ctrl+A selects only image rows in the current folder. Delete
and the toolbar Delete images action operate only on selected ZIP image entries,
after a confirmation containing the archive, count, and filename preview. Local
images, folders, and nested ZIP entries are excluded.

`sources/archive_edit.py` streams retained members into a temporary sibling ZIP,
then verifies their CRCs, sizes, order, and compression methods before atomic
replacement. Comments, member timestamps/attributes, and file permission bits are
preserved. Selection checks use directory position, filename, CRC, size, and the
archive fingerprint; duplicate filenames remain distinct. The source inode,
size, modification time, and change time are rechecked before replacement.
A nonblocking advisory file lock prevents simultaneous cooperative ZIP editors.
Retained entries obey the configured decompressed-entry limit. No entries are
extracted to filesystem paths.

`services/archive_edit.py` owns a dedicated background executor and cancellation
token. A modal progress dialog keeps the event loop responsive; Cancel, Escape,
and closing the app request cooperative cancellation. Closing waits asynchronously
for completion before shutting down services. Successful replacement invalidates
the source's persistent cache entries and advances the memory-cache epoch so old
jobs cannot repopulate it. A missing implicit folder returns to its nearest
surviving parent. Cache cleanup errors after replacement cannot turn a committed
deletion into a reported failure.

Validation on macOS ARM64: **115 passed, 1 skipped** offscreen, **2 passed** on native
Cocoa for actual deletion and responsive cancellation, plus Ruff lint/formatting.
New cases cover real Ctrl/Shift selection and keyboard actions, confirmation
cancellation, duplicate names, Unicode, ZIP64, metadata/content preservation,
empty archives, corrupted retained data, stale selections, permissions, size limits,
symbolic links, write/verification/replacement failures, external modification,
cache invalidation, close cancellation, last-image parent recovery, and rejection
of a concurrent editor's file lock. Native Ubuntu ZIP editing remains unverified.
Deletion has no Trash or undo; standalone
release packaging and milestone 5 acceptance remain pending.

### Follow-up: select portrait or landscape images

Ctrl+Shift+P and Ctrl+Shift+L (also available as toolbar actions) replace the grid
selection with portrait or landscape images in the current local/ZIP folder.
Squares, folders, and nested ZIP entries are excluded. Help lists both bindings.

A separate WorkerService shares the bounded scheduler and reads source dimensions
plus Qt transformation metadata without decoding pixels. Rotations with a 90-degree
component swap axes, matching the viewer for all eight EXIF orientations. ZIP
members are decompressed within the existing entry-size limit. Invalid dimensions
and oversized images are skipped and counted. A changed archive aborts selection.

Progress appears in the status bar. Esc, navigation, opening an image, manual
selection, Ctrl+A, or a new orientation request cancels obsolete work. Request
generations reject late results, and Delete stays disabled during scanning. The
matching identities are applied together when scanning completes.

Validation: **133 passed, 1 skipped** offscreen, and **3 passed** on native Cocoa
for both keyboard bindings/scopes and selecting then deleting only portrait images.
The 18 added cases cover local/ZIP folders, exact replacement, squares, invalid
headers, size limits, empty folders, all EXIF transforms, cancellation/superseding,
late results, source changes, and the orientation-to-confirmed-delete workflow.
Ruff lint and formatting pass. Native Ubuntu verification remains pending.

### Commands

Milestone 1 configures the following commands. Create the virtual environment with Python 3.10 or newer (substitute your installed interpreter):

```sh
python3.10 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python -m qtvw
python -m pytest
python -m ruff check .
python -m ruff format --check .
```

Use unit tests for sorting, location transitions, cache identities, invalidation, and cover ordering. Use integration tests with temporary directories and generated ZIP fixtures for reads, changed sources, corruption, limits, eviction, and restart reuse. Avoid timing-dependent assertions; control worker completion to test cancellation and stale results.

Use `pytest-qt` for navigation, model updates, viewer state, and shortcuts. Headless runs may use `QT_QPA_PLATFORM=offscreen`; retain interactive Ubuntu checks for windowing, fullscreen, appearance, and responsiveness. Do not impose an arbitrary coverage percentage; require regression tests for correctness fixes and critical source/cache/scheduler behavior.

Record milestone completion and actual commands as implementation lands. Update `AGENTS.md` when directories or tooling become established. Keep release evidence mapped to SPEC §14, including platform, architecture, collection size, cache state, measurements, and outstanding limitations.
