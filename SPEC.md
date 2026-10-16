# QtVw — Application Specification

QtVw is a desktop image browser for Ubuntu that lets users browse folders and view photos stored in ZIP archives without manually extracting them. It prioritises fast thumbnail browsing, responsive navigation, and persistent caching.

## 1. Scope

The initial release will support:

- Browsing local folders and subfolders.
- Displaying ZIP archives alongside ordinary image files.
- Showing a representative thumbnail for each ZIP archive.
- Browsing images and folders inside ZIP archives.
- Viewing individual images with zoom, pan, and fullscreen controls.
- Caching thumbnails and archive indexes to improve repeat browsing.

Image editing, archive creation, archive modification, cloud storage, and nested archives are outside the initial scope.

## 2. Target platform and technology

| Component | Choice |
|---|---|
| Primary platform | Ubuntu Desktop |
| Development platform | macOS, with Ubuntu testing |
| Language | Python |
| Desktop framework | Qt 6 through PySide6 |
| Interface toolkit | Qt Widgets |
| ZIP access | Python `zipfile` |
| Cache metadata and indexes | SQLite |
| Thumbnail storage | Files in the user’s cache directory |

The application should keep platform-specific code separate so the same core application can run on macOS.

Ubuntu ARM64 and x86-64 releases will require separate packaging and validation.

## 3. Application layout

The main window will contain:

| Area | Purpose |
|---|---|
| Navigation toolbar | Back, forward, up, current location, and refresh |
| Left sidebar | Expandable filesystem folder tree |
| Main content area | Thumbnail grid or image viewer |
| Status bar | Item count, selected item information, and background activity |

Sidebar width, window size, thumbnail size, and the last visited location will persist between sessions.

## 4. Folder navigation

Users must be able to:

- Choose a starting folder.
- Expand and collapse folders in the sidebar.
- Browse subfolders without opening a separate window.
- See folders, supported images, and ZIP archives in the main panel.
- Open a folder or archive by double-clicking it.
- Navigate back, forward, and up.
- Read and copy the current location.

Folder contents must load progressively. The application must not recursively scan an entire directory tree at startup.

Hidden files will be excluded by default, with an option to show them. Symbolic links must not cause recursive traversal loops.

## 5. ZIP archive browsing

ZIP archives will behave as browsable containers.

When a user opens an archive, QtVw will:

1. Read its entry listing.
2. Identify supported image files and internal folders.
3. Display its immediate contents.
4. Generate visible thumbnails in the background.
5. Allow navigation into internal subfolders.

Images will be read from archive entries without extracting the entire archive to disk. Individual entries will still need to be decompressed for decoding.

An archive location should be presented clearly, for example:

```text
/home/user/Photos/Holiday.zip › Day 1
```

Initial archive support:

- Standard, unencrypted ZIP files.
- Internal folder hierarchies.
- Unicode filenames.
- ZIP64 archives within configured resource limits.

Password-protected, damaged, and unsupported archives will display an explanatory message without preventing navigation elsewhere.

ZIP files contained inside another ZIP will not be browsable in the initial release.

## 6. Archive cover thumbnails

Each ZIP archive will display a cover thumbnail in the main grid.

The cover selection rule will be:

1. Prefer a supported image named `cover`, ignoring case and file extension.
2. Otherwise, use the first decodable image in natural path order, including subfolders.
3. Show a generic archive icon if no suitable image is available.

Cover generation must run in the background and use the thumbnail cache. The cover will have an archive badge so users can distinguish it from a standalone image.

Manual cover selection may be added in a later release.

## 7. Thumbnail grid

The grid will support:

- Standalone images, folders, and ZIP archives.
- Thumbnails for images inside archives.
- Adjustable thumbnail size.
- Filenames beneath thumbnails.
- Clear selection and keyboard focus.
- Natural filename sorting, so `photo2` precedes `photo10`.
- Sorting by name, modification date, and size where available.
- Ascending and descending sort order.

Visible items will receive the highest thumbnail-generation priority, followed by nearby items. Large folders must use a virtualised view rather than creating a separate widget for every entry.

Failed thumbnails will show a placeholder. One unreadable image must not interrupt the remaining work.

## 8. Image viewer

Opening an image will show it in the main content area.

The viewer will provide:

- Fit to window.
- Actual size at 100%.
- Zoom in and out.
- Mouse-wheel zoom.
- Panning when the image exceeds the viewport.
- Next and previous image.
- Fullscreen mode.
- Filename, dimensions, and position within the current collection.
- Correct display orientation when supported image metadata provides it.

Next and previous navigation will follow the browser’s current sort order and operate on images in the current folder or archive subfolder.

Returning to the grid will restore the previous selection and scroll position.

Image rendering will offer Auto and Software modes. Auto uses an OpenGL viewport
when available and falls back to software when initialization or context checks
fail. The active backend will be visible, and switching renderers will preserve
the current image and view. This affects viewer drawing; image decoding and ZIP
decompression remain separate background operations.

The initial release will target static JPEG, PNG, WebP, BMP, and TIFF images. Actual decoder availability must be verified in the packaged application. Animated formats, multipage TIFF navigation, HEIC, and camera RAW support are deferred.

## 9. Caching

QtVw will use three complementary caches.

| Cache | Contents | Purpose |
|---|---|---|
| Memory cache | Recent thumbnails and decoded images | Fast navigation during a session |
| Thumbnail disk cache | Image thumbnails and archive covers | Fast browsing across application restarts |
| Archive index | Entry paths and relevant ZIP metadata | Avoid repeated archive enumeration |

Thumbnail files will be stored outside SQLite. SQLite will hold metadata and references to those files.

Cache identity will include:

- Source location.
- Source file size and modification time.
- Archive entry path and its CRC/size, where applicable.
- Thumbnail size class.
- Thumbnail-generation version.

Changing an archive will invalidate its index and associated thumbnails. Changing a standalone image will invalidate that image’s thumbnails.

Cache management will provide:

- A configurable disk size limit.
- Least-recently-used eviction.
- A clear-cache action.
- Automatic regeneration of missing or damaged cache entries.

Full-resolution images will have a bounded memory cache; they will not be persistently cached by default.

## 10. Background processing and responsiveness

Filesystem enumeration, archive indexing, image decoding, and thumbnail generation must run outside the UI thread.

The work scheduler must:

- Prioritise the current view.
- Cancel or deprioritise work when navigation changes.
- Limit concurrent jobs and memory consumption.
- Avoid duplicate jobs for the same thumbnail.
- Preload adjacent images when resources permit.
- Discard stale results from a previous location.

The interface must remain usable while background work is running.

## 11. Error handling and resource limits

QtVw must handle:

- Missing or disconnected folders.
- Permission errors.
- Archives modified while being viewed.
- Corrupt ZIP entries.
- Unsupported image formats.
- Insufficient cache storage.
- Extremely large images or archive entries.

Archive entry paths must never be used as unrestricted extraction destinations. Configurable limits on decoded image size, entry size, and background work must prevent a single file from exhausting system resources.

Errors affecting individual items should appear on those items or in the status area. Dialogs should be reserved for failures that block the requested action.

## 12. Keyboard controls

| Shortcut | Action |
|---|---|
| `Ctrl+O` | Choose a folder |
| `Alt+Left` / `Alt+Right` | Navigation back / forward |
| `Alt+Up` | Parent location |
| `Enter` | Open selected item |
| `Left` / `Right` | Previous / next image in viewer |
| `+` / `-` | Zoom in / out |
| `0` | Fit to window |
| `1` | Actual size |
| `F11` | Toggle fullscreen |
| `Esc` | Exit fullscreen, then return to grid |
| `F5` | Refresh current location |

## 13. Preferences

The initial settings interface will include:

- Default starting folder or restore-last-location behaviour.
- Thumbnail size.
- Default sort order.
- Show hidden files.
- Thumbnail disk cache limit.
- Clear cache.
- Light, dark, or system appearance.

## 14. Acceptance criteria

The first release is complete when:

1. Users can browse a folder hierarchy from the sidebar.
2. ZIP archives display cached representative thumbnails.
3. Opening a ZIP displays its images and internal folders without full extraction.
4. Image thumbnails load progressively while navigation remains responsive.
5. Users can open images, zoom, pan, enter fullscreen, and navigate between images.
6. Revisiting a previously browsed archive reuses valid cached thumbnails.
7. Modifying an archive causes its stale cached results to be replaced.
8. Rapid navigation does not display thumbnails from the wrong location.
9. Corrupt files and unsupported archives do not crash the application.
10. Cache usage stays within its configured limit after cleanup.
11. The packaged application passes testing on each supported Ubuntu architecture.

Performance measurements should cover both cold-cache and warm-cache browsing, using a documented Ubuntu test machine and a representative collection containing thousands of images. Numerical targets should be set after an early prototype establishes decoding and storage costs.

## 15. Implementation stages

| Stage | Deliverable |
|---|---|
| 1 | Main window, folder tree, ordinary image grid, basic viewer |
| 2 | ZIP navigation, internal folders, archive cover thumbnails |
| 3 | Background scheduling, persistent thumbnails, archive indexing |
| 4 | Cache limits, error handling, preferences, keyboard controls |
| 5 | Ubuntu packaging, architecture testing, performance validation |
