"""Filesystem and ZIP browsing with a shared image viewer."""

from pathlib import Path

from PySide6.QtCore import QDir, QFileSystemWatcher, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFileSystemModel,
    QLabel,
    QLineEdit,
    QMainWindow,
    QSlider,
    QSplitter,
    QStackedWidget,
    QToolBar,
    QTreeView,
)

from qtvw.cache import CacheStore
from qtvw.domain.locations import (
    ArchiveLocation,
    parse_location,
    restore_location,
    save_location,
)
from qtvw.domain.preferences import Preferences
from qtvw.services import Navigation, WorkerService
from qtvw.services.scheduler import NEARBY, OPEN_IMAGE, PRELOAD, VISIBLE, PriorityExecutor
from qtvw.sources.archive import ArchiveLimits
from qtvw.ui.browser import Browser, BrowserModel
from qtvw.ui.preferences import PreferencesDialog, apply_appearance
from qtvw.ui.viewer import ImageViewer


class MainWindow(QMainWindow):
    def __init__(self, start_path: Path | ArchiveLocation | None = None, software_rendering=False):
        super().__init__()
        self.setWindowTitle("QtVw")
        self.settings = QSettings("QtVw", "QtVw")
        self.preferences = Preferences.load(self.settings)
        self._force_software_rendering = software_rendering
        self._preferences_dialog = None
        self._cache_busy = False
        self._maintenance = None
        self._listing_active = False
        self._after_clear_reload = False
        self._resume_thumbnails = False
        self.navigation = Navigation()
        self.generation = 0
        self.viewer_generation = 0
        self.requested = set()
        self.viewed = None
        cache_bytes = self.preferences.cache_mb * 1024 * 1024
        self.cache = CacheStore(disk_payload_limit=cache_bytes, disk_limit=cache_bytes)
        self.scheduler = PriorityExecutor(max_workers=self.preferences.workers)
        limits = ArchiveLimits(max_entry_bytes=self.preferences.entry_mb * 1024 * 1024)
        self.workers = WorkerService(
            self, archive_limits=limits, cache=self.cache, scheduler=self.scheduler
        )
        self.image_workers = WorkerService(
            self, archive_limits=limits, cache=self.cache, scheduler=self.scheduler
        )
        for service in (self.workers, self.image_workers):
            service.max_pixels = self.preferences.decoded_mp * 1_000_000
            service.max_file_bytes = self.preferences.input_mb * 1024 * 1024
        self.workers.source_changed.connect(self._browser_source_changed)
        self.image_workers.source_changed.connect(self._viewer_source_changed)
        self.workers.listed.connect(self._listed)
        self.workers.listing_finished.connect(self._listing_finished)
        self.workers.decoded.connect(self._thumbnail)
        self.image_workers.decoded.connect(self._image)
        self._closing = False
        self._restore_state = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(150)
        self._refresh_timer.timeout.connect(self._reload_changed_source)
        self.watcher = QFileSystemWatcher(self)
        self.watcher.directoryChanged.connect(self._source_dirty)
        self.watcher.fileChanged.connect(self._source_dirty)
        self._thumbnail_timer = QTimer(self)
        self._thumbnail_timer.setSingleShot(True)
        self._thumbnail_timer.timeout.connect(self._request_visible)
        self.model = BrowserModel(self)
        self.browser = Browser()
        self.browser.setModel(self.model)
        self.browser.activated.connect(self._open_index)
        self.browser.visible_changed.connect(self._schedule_thumbnails)
        mode = "software" if software_rendering else self.preferences.rendering
        self.viewer = ImageViewer(rendering_mode=mode)
        self.renderer_label = QLabel()
        self.statusBar().addPermanentWidget(self.renderer_label)
        self.viewer.backend_changed.connect(self._renderer_changed)
        self._renderer_changed(self.viewer.rendering_description)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.browser)
        self.stack.addWidget(self.viewer)
        self.tree = QTreeView()
        self.folders = QFileSystemModel(self)
        self.folders.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot)
        self.folders.setRootPath(QDir.rootPath())
        self.tree.setModel(self.folders)
        for column in range(1, self.folders.columnCount()):
            self.tree.hideColumn(column)
        self.tree.setHeaderHidden(True)
        self.tree.clicked.connect(lambda index: self.navigate(Path(self.folders.filePath(index))))
        self.splitter = QSplitter()
        self.splitter.addWidget(self.tree)
        self.splitter.addWidget(self.stack)
        self.splitter.setStretchFactor(1, 1)
        self.setCentralWidget(self.splitter)
        toolbar = QToolBar("Navigation", self)
        toolbar.setObjectName("navigation")
        self.addToolBar(toolbar)
        self.back_action = self._action(toolbar, "Back", "Alt+Left", self.back)
        self.forward_action = self._action(toolbar, "Forward", "Alt+Right", self.forward)
        self._action(toolbar, "Up", "Alt+Up", self.up)
        self._action(toolbar, "Choose folder", "Ctrl+O", self.choose_folder)
        self.location = QLineEdit()
        self.location.returnPressed.connect(self.open_location)
        toolbar.addWidget(self.location)
        self._action(toolbar, "Refresh", "F5", self.refresh)
        grid_action = self._action(toolbar, "Grid", "Backspace", self.show_grid)
        grid_action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        self.browser.addAction(grid_action)
        self.hidden_action = self._action(toolbar, "Hidden", None, self.refresh)
        self.hidden_action.setCheckable(True)
        self.hidden_action.setChecked(self.preferences.show_hidden)
        self.sort_combo = QComboBox()
        for label, value in [("Name", "name"), ("Date", "modified"), ("Size", "size")]:
            self.sort_combo.addItem(label, value)
        self.sort_combo.setCurrentIndex(self.sort_combo.findData(self.preferences.sort_field))
        toolbar.addWidget(self.sort_combo)
        self.descending_action = self._action(toolbar, "Descending", None, self._sort_changed)
        self.descending_action.setCheckable(True)
        self.descending_action.setChecked(self.preferences.descending)
        self.sort_combo.currentIndexChanged.connect(self._sort_changed)
        self._action(toolbar, "Preferences", "Ctrl+,", self.show_preferences)
        self._action(toolbar, "Clear cache", None, self.clear_cache)
        self._sort_changed()
        controls = QToolBar("Image controls", self)
        self.addToolBar(Qt.ToolBarArea.BottomToolBarArea, controls)
        self.previous_action = self._action(
            controls, "Previous", "Left", lambda: self.step_image(-1)
        )
        self.next_action = self._action(controls, "Next", "Right", lambda: self.step_image(1))
        self._action(controls, "Fit", "0", self.viewer.fit)
        self._action(controls, "100%", "1", self.viewer.actual_size)
        self._action(controls, "Zoom in", "+", lambda: self.viewer.zoom(1.25))
        self._action(controls, "Zoom out", "-", lambda: self.viewer.zoom(0.8))
        # Single-character image shortcuts must not intercept location-field editing.
        for action in controls.actions():
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.viewer.addAction(action)
        self._action(controls, "Fullscreen", "F11", self.toggle_fullscreen)
        self._action(controls, "Return", "Esc", self.escape)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(64, 256)
        self.slider.setMaximumWidth(180)
        self.slider.setValue(self.preferences.thumbnail_size)
        self.slider.valueChanged.connect(self._thumbnail_size)
        controls.addWidget(self.slider)
        self.browser.set_thumbnail_size(self.slider.value())
        self.resize(1100, 760)
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        state = self.settings.value("splitter")
        if state:
            self.splitter.restoreState(state)
        else:
            self.splitter.setSizes([230, 870])
        previous = self.settings.value("last_path", str(Path.home()))
        initial = start_path
        if initial is None:
            initial = (
                restore_location(
                    self.settings.value("last_location", save_location(Path(previous)))
                )
                if self.preferences.restore_last
                else self.preferences.starting_path
            )
        apply_appearance(self, self.preferences.appearance)
        self.navigate(initial)

    def _action(self, toolbar, title, shortcut, callback):
        action = QAction(title, self)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        action.triggered.connect(callback)
        toolbar.addAction(action)
        return action

    def choose_folder(self):
        current = self.navigation.current
        directory = current.path.parent if isinstance(current, ArchiveLocation) else current
        path = QFileDialog.getExistingDirectory(self, "Choose image folder", str(directory))
        if path:
            self.navigate(Path(path))

    def navigate(self, path):
        self._restore_state = None
        if not isinstance(path, ArchiveLocation):
            path = Path(path).expanduser().absolute()
        self.navigation.navigate(path)
        self._load(path)

    def open_location(self):
        try:
            text = self.location.text()
            if text == str(self.navigation.current):
                return self.refresh()
            path = parse_location(text)
            if isinstance(path, Path) and path.suffix.lower() == ".zip" and not path.is_dir():
                path = ArchiveLocation(path)
            self.navigate(path)
        except ValueError as error:
            self.statusBar().showMessage(str(error))

    def _load(self, path):
        self._refresh_timer.stop()
        self.show_grid()
        self.workers.cancel()
        self.generation += 1
        self.requested.clear()
        self.model.clear()
        self.location.setText(str(path))
        self.back_action.setEnabled(self.navigation.can_back)
        self.forward_action.setEnabled(self.navigation.can_forward)
        self.statusBar().showMessage(
            "Loading archive…" if isinstance(path, ArchiveLocation) else "Loading folder…"
        )
        filters = QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot
        if self.hidden_action.isChecked():
            filters |= QDir.Filter.Hidden
        self.folders.setFilter(filters)
        self._listing_active = True
        self.workers.list_directory(path, self.generation, self.hidden_action.isChecked())
        folder = path.path.parent if isinstance(path, ArchiveLocation) else path
        self.tree.setCurrentIndex(self.folders.index(str(folder)))
        self._watch_sources()

    def back(self):
        self._restore_state = None
        path = self.navigation.back()
        if path is not None:
            self._load(path)

    def forward(self):
        self._restore_state = None
        path = self.navigation.forward()
        if path is not None:
            self._load(path)

    def up(self):
        self._restore_state = None
        path = self.navigation.up()
        if path is not None:
            self._load(path)

    def refresh(self):
        if self.navigation.current is not None:
            self._load(self.navigation.current)

    def _listed(self, generation, entries):
        if generation == self.generation:
            self.model.append(entries)
            self._schedule_thumbnails()

    def _listing_finished(self, generation, error):
        if generation != self.generation:
            return
        self._listing_active = False
        self.model.sort()
        if self.stack.currentWidget() == self.browser or error:
            self.statusBar().showMessage(error or f"{self.model.rowCount()} items")
        if self._restore_state is not None:
            viewed, selected, scroll = self._restore_state
            self._restore_state = None
            for row, entry in enumerate(self.model.entries):
                if entry.identity == selected:
                    self.browser.setCurrentIndex(self.model.index(row))
            if viewed is not None:
                images = [entry for entry in self.model.entries if entry.kind == "image"]
                replacement = next(
                    (entry for entry in images if entry.identity == viewed.identity), None
                )
                if replacement is None:
                    replacement = next(
                        (
                            entry
                            for entry in images
                            if (entry.path, entry.member) == (viewed.path, viewed.member)
                        ),
                        None,
                    )
                if replacement is not None:
                    self.open_image(replacement)
            self.browser.verticalScrollBar().setValue(scroll)
        self._schedule_thumbnails()

    def _schedule_thumbnails(self):
        if not self._closing:
            self._thumbnail_timer.start(0)

    def _request_visible(self):
        if self._cache_busy:
            self._resume_thumbnails = True
            return
        if self._closing or self.stack.currentWidget() != self.browser:
            return
        visible = set(self.browser.visible_rows(nearby=False))
        nearby = set(self.browser.visible_rows()) - visible
        priorities = {
            self.model.entries[row].identity: VISIBLE if row in visible else NEARBY
            for row in visible | nearby
        }
        self.requested.difference_update(self.workers.reprioritize(self.generation, priorities))
        self._watch_sources()
        for row in sorted(visible) + sorted(nearby):
            entry = self.model.entries[row]
            if (
                entry.kind not in {"image", "archive"}
                or (entry.kind == "archive" and entry.member is not None)
                or entry.identity in self.requested
                or entry.identity in self.model.pixmaps
                or entry.identity in self.model.failed
            ):
                continue
            if len(self.requested) >= 64:
                break
            if self.workers.decode(
                entry, self.generation, self.slider.value(), priority=priorities[entry.identity]
            ):
                self.requested.add(entry.identity)

    def _thumbnail(self, generation, identity, image, error):
        if generation == self.generation:
            self.requested.discard(identity)
            self.model.decoded(identity, image, error)
            self._schedule_thumbnails()

    def _thumbnail_size(self, size):
        self.preferences.thumbnail_size = size
        self.browser.set_thumbnail_size(size)
        self.refresh()

    def _open_index(self, index):
        if not index.isValid():
            return
        entry = self.model.entries[index.row()]
        if entry.kind == "folder" or (entry.kind == "archive" and entry.member is None):
            self.navigate(entry.location)
        elif entry.kind == "image":
            self.open_image(entry)
        else:
            self.statusBar().showMessage("Nested ZIP archives are not supported")

    def open_image(self, entry):
        self.workers.reprioritize(self.generation, {})
        self.requested.clear()
        self.image_workers.cancel()
        self.viewer_generation += 1
        self.viewed = entry
        self.stack.setCurrentWidget(self.viewer)
        self.viewer.setFocus()
        self.previous_action.setEnabled(True)
        self.next_action.setEnabled(True)
        self.viewer.clear()
        self.statusBar().showMessage(f"Loading {entry.name}…")
        if not self.image_workers.decode(entry, self.viewer_generation, priority=OPEN_IMAGE):
            self.statusBar().showMessage(
                "Background work queue is full; try opening the image again"
            )
        self._watch_sources()

    def _image(self, generation, identity, image, error):
        if (
            generation != self.viewer_generation
            or self.viewed is None
            or identity != self.viewed.identity
        ):
            return
        if error or image.isNull():
            self.statusBar().showMessage(error or "Unable to decode image")
            return
        self.viewer.set_image(image)
        images = [entry for entry in self.model.entries if entry.kind == "image"]
        position = next((i + 1 for i, e in enumerate(images) if e.identity == identity), 0)
        self.statusBar().showMessage(
            f"{self.viewed.name} — {image.width()} × {image.height()} — {position}/{len(images)}"
        )
        for neighbour in (position - 2, position):
            if 0 <= neighbour < len(images):
                self.image_workers.decode(
                    images[neighbour], self.viewer_generation, priority=PRELOAD, emit=False
                )

    def step_image(self, step):
        if self.stack.currentWidget() != self.viewer or self.viewed is None:
            return
        images = [entry for entry in self.model.entries if entry.kind == "image"]
        for i, entry in enumerate(images):
            if entry.identity == self.viewed.identity and 0 <= i + step < len(images):
                self.open_image(images[i + step])
                break

    def show_grid(self):
        self.image_workers.cancel()
        self.viewer_generation += 1
        self.viewer.clear()
        self.stack.setCurrentWidget(self.browser)
        self.browser.setFocus()
        self.previous_action.setEnabled(False)
        self.next_action.setEnabled(False)
        self.viewed = None
        self._schedule_thumbnails()

    def _watch_sources(self):
        current = self.navigation.current
        paths = {str(current.path if isinstance(current, ArchiveLocation) else current)}
        if self.viewed is not None and self.stack.currentWidget() == self.viewer:
            paths.add(str(self.viewed.path))
        else:
            paths.update(
                str(self.model.entries[row].path) for row in self.browser.visible_rows()[:128]
            )
        existing = set(self.watcher.files()) | set(self.watcher.directories())
        if existing - paths:
            self.watcher.removePaths(sorted(existing - paths))
        if paths - existing:
            self.watcher.addPaths(sorted(paths - existing))

    def _source_dirty(self, *_args):
        if not self._closing:
            self._refresh_timer.start()

    def _browser_source_changed(self, generation, _path):
        if generation == self.generation:
            self._source_dirty()

    def _viewer_source_changed(self, generation, _path):
        if generation == self.viewer_generation:
            self._source_dirty()

    def _reload_changed_source(self):
        selected = self.browser.currentIndex()
        identity = self.model.entries[selected.row()].identity if selected.isValid() else None
        viewed = self.viewed if self.stack.currentWidget() == self.viewer else None
        self._restore_state = (viewed, identity, self.browser.verticalScrollBar().value())
        self.refresh()

    def _sort_changed(self, *_args):
        self.preferences.sort_field = self.sort_combo.currentData()
        self.preferences.descending = self.descending_action.isChecked()
        self.model.sort_field = self.preferences.sort_field
        self.model.sort_order = (
            Qt.SortOrder.DescendingOrder
            if self.preferences.descending
            else Qt.SortOrder.AscendingOrder
        )
        self.model.sort()
        self._schedule_thumbnails()

    def show_preferences(self):
        if self._preferences_dialog is not None and self._preferences_dialog.isVisible():
            self._preferences_dialog.raise_()
            return self._preferences_dialog
        self.preferences.show_hidden = self.hidden_action.isChecked()
        dialog = PreferencesDialog(self.preferences, self)
        apply_appearance(dialog, self.preferences.appearance)
        self._preferences_dialog = dialog
        dialog.rendering_status.setText("Current viewer: " + self.viewer.rendering_description)
        if self._cache_busy:
            dialog.clear_button.setEnabled(False)
            dialog.cache_status.setText("Updating cache…")
        dialog.clear_requested.connect(self.clear_cache)
        dialog.accepted.connect(self._apply_preferences)
        dialog.open()
        return dialog

    def _apply_preferences(self):
        self.preferences = self._preferences_dialog.values()
        self.preferences.save(self.settings)
        self.hidden_action.setChecked(self.preferences.show_hidden)
        self.sort_combo.blockSignals(True)
        self.sort_combo.setCurrentIndex(self.sort_combo.findData(self.preferences.sort_field))
        self.sort_combo.blockSignals(False)
        self.descending_action.setChecked(self.preferences.descending)
        self._sort_changed()
        self.slider.blockSignals(True)
        self.slider.setValue(self.preferences.thumbnail_size)
        self.slider.blockSignals(False)
        self.browser.set_thumbnail_size(self.preferences.thumbnail_size)
        apply_appearance(self, self.preferences.appearance)
        self.viewer.set_rendering_mode(
            "software" if self._force_software_rendering else self.preferences.rendering
        )
        self._reload_changed_source()
        self._begin_maintenance(
            self.cache.maintain_async(self.preferences.cache_mb * 1024 * 1024),
            "Cache cleanup complete",
        )

    def _renderer_changed(self, description):
        name = "OpenGL" if self.viewer.rendering_backend == "opengl" else "Software"
        self.renderer_label.setText("Viewer: " + name)
        self.renderer_label.setToolTip(description)
        if self._preferences_dialog is not None:
            self._preferences_dialog.rendering_status.setText("Current viewer: " + description)

    def clear_cache(self):
        if self._cache_busy:
            return
        self._after_clear_reload = self._listing_active or (
            self.stack.currentWidget() == self.viewer and self.viewer.item is None
        )
        self.workers.cancel()
        self.image_workers.cancel()
        self.generation += 1
        self.viewer_generation += 1
        self.requested.clear()
        self._thumbnail_timer.stop()
        self._begin_maintenance(self.cache.clear_async(), "Cache cleared")

    def _begin_maintenance(self, future, message):
        self._cache_busy = True
        self._resume_thumbnails = False
        self._maintenance = (future, message)
        self.statusBar().showMessage("Updating cache…")
        if self._preferences_dialog is not None:
            self._preferences_dialog.clear_button.setEnabled(False)
            self._preferences_dialog.cache_status.setText("Updating cache…")
        QTimer.singleShot(25, self._poll_maintenance)

    def _poll_maintenance(self):
        if self._closing or self._maintenance is None:
            return
        future, message = self._maintenance
        if not future.done():
            QTimer.singleShot(25, self._poll_maintenance)
            return
        result = future.result()
        self._cache_busy = False
        self._maintenance = None
        if not result["ok"]:
            message = "Cache update failed: " + result.get("message", "storage unavailable")
        self.statusBar().showMessage(message)
        if self._preferences_dialog is not None:
            self._preferences_dialog.clear_button.setEnabled(True)
            self._preferences_dialog.cache_status.setText(message)
        if self._after_clear_reload:
            self._after_clear_reload = False
            self._reload_changed_source()
        elif self._resume_thumbnails:
            self._schedule_thumbnails()

    def toggle_fullscreen(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def escape(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.show_grid()

    def closeEvent(self, event):
        self._closing = True
        self._refresh_timer.stop()
        self._thumbnail_timer.stop()
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.setValue("splitter", self.splitter.saveState())
        self.preferences.thumbnail_size = self.slider.value()
        self.preferences.show_hidden = self.hidden_action.isChecked()
        self.preferences.save(self.settings)
        current = self.navigation.current
        self.settings.setValue("last_location", save_location(current))
        folder = current.path.parent if isinstance(current, ArchiveLocation) else current
        self.settings.setValue("last_path", str(folder))
        self.settings.setValue("hidden", self.hidden_action.isChecked())
        self.settings.sync()
        self.workers.shutdown()
        self.image_workers.shutdown()
        self.scheduler.shutdown()
        self.cache.close()
        super().closeEvent(event)
