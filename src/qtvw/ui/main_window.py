"""Filesystem and ZIP browsing with a shared image viewer."""

import logging
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
    QMenu,
    QMessageBox,
    QProgressDialog,
    QSlider,
    QSplitter,
    QStackedWidget,
    QToolBar,
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
from qtvw.services.archive_edit import ArchiveEditService
from qtvw.services.file_moves import FileMoveService
from qtvw.services.folder_delete import FolderDeleteService, folder_identity
from qtvw.services.scheduler import NEARBY, OPEN_IMAGE, PRELOAD, VISIBLE, PriorityExecutor
from qtvw.sources.archive import ArchiveLimits
from qtvw.ui.archive_delete import ArchiveDeleteProgress
from qtvw.ui.browser import Browser, BrowserModel
from qtvw.ui.file_moves import FileMoveProgress, FolderTree
from qtvw.ui.help import HelpDialog
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
        self._help_dialog = None
        self._shortcut_actions = []
        self._cache_busy = False
        self._maintenance = None
        self._listing_active = False
        self._descent_seen = None
        self._archive_editing = False
        self._delete_confirmation = None
        self._folder_deleting = False
        self._close_after_folder_delete = False
        self.folder_deleter = FolderDeleteService(self)
        self.folder_deleter.finished.connect(self._folder_delete_finished)
        self._delete_progress = None
        self._close_after_edit = False
        self._orientation_generation = 0
        self._orientation_pending = None
        self._file_moving = False
        self._move_progress = None
        self._close_after_move = False
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
        self.archive_editor = ArchiveEditService(self.cache, limits, self)
        self.archive_editor.progress.connect(self._archive_delete_progress)
        self.archive_editor.finished.connect(self._archive_delete_finished)
        self.file_mover = FileMoveService(self.cache, self)
        self.file_mover.progress.connect(self._file_move_progress)
        self.file_mover.finished.connect(self._file_move_finished)
        self.workers = WorkerService(
            self, archive_limits=limits, cache=self.cache, scheduler=self.scheduler
        )
        self.image_workers = WorkerService(
            self, archive_limits=limits, cache=self.cache, scheduler=self.scheduler
        )
        self.selection_workers = WorkerService(
            self, archive_limits=limits, cache=self.cache, scheduler=self.scheduler
        )
        self.selection_workers.orientation_selected.connect(self._orientation_selected)
        self.selection_workers.orientation_progress.connect(self._orientation_progress)
        for service in (self.workers, self.image_workers, self.selection_workers):
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
        self.browser.zip_files_dropped.connect(self.move_zip_files)
        mode = "software" if software_rendering else self.preferences.rendering
        self.viewer = ImageViewer(
            rendering_mode=mode, smooth_scaling=self.preferences.smooth_scaling
        )
        self.renderer_label = QLabel()
        self.statusBar().addPermanentWidget(self.renderer_label)
        self.viewer.backend_changed.connect(self._renderer_changed)
        self.viewer.navigate_requested.connect(self.step_image)
        self.viewer.browse_requested.connect(self.show_grid)
        self._renderer_changed(self.viewer.rendering_description)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.browser)
        self.stack.addWidget(self.viewer)
        self.tree = FolderTree()
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._folder_context_menu)
        self.tree.zip_files_dropped.connect(self.move_zip_files)
        self.folders = QFileSystemModel(self)
        self.folders.setFilter(QDir.Filter.AllDirs | QDir.Filter.NoDotAndDotDot)
        self.folders.setRootPath(QDir.rootPath())
        self.tree.setModel(self.folders)
        for column in range(1, self.folders.columnCount()):
            self.tree.hideColumn(column)
        self.tree.setHeaderHidden(True)
        self.tree.clicked.connect(
            lambda index: self.navigate(Path(self.folders.filePath(index)), auto_descend=True)
        )
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
        self.viewer.addAction(grid_action)
        grid_action.setProperty("help_context", "Image viewer")
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
        self._action(toolbar, "Help", "F1", self.show_help)
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
            action.setProperty("help_context", "Image viewer")
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            self.viewer.addAction(action)
        self._action(controls, "Fullscreen", "F11", self.toggle_fullscreen)
        select_action = self._action(
            controls, "Select all images", "Ctrl+A", self._select_all_images
        )
        portrait_action = self._action(
            controls, "Select portrait", "Ctrl+Shift+P", lambda: self.select_orientation("portrait")
        )
        landscape_action = self._action(
            controls,
            "Select landscape",
            "Ctrl+Shift+L",
            lambda: self.select_orientation("landscape"),
        )
        self.delete_action = self._action(
            controls, "Delete images", "Delete", self.delete_selected_images
        )
        for action in (select_action, portrait_action, landscape_action, self.delete_action):
            action.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            action.setProperty("help_context", "Thumbnail grid")
            self.browser.addAction(action)
        self.delete_action.setProperty("help_context", "Thumbnail grid inside ZIPs")
        self.delete_action.setEnabled(False)
        self.browser.selectionModel().selectionChanged.connect(self._browser_selection_changed)
        escape_action = self._action(controls, "Return", "Esc", self.escape)
        escape_action.setProperty("help_context", "Image viewer / fullscreen")
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
            self._shortcut_actions.append(action)
        action.triggered.connect(callback)
        toolbar.addAction(action)
        return action

    def choose_folder(self):
        current = self.navigation.current
        directory = current.path.parent if isinstance(current, ArchiveLocation) else current
        path = QFileDialog.getExistingDirectory(self, "Choose image folder", str(directory))
        if path:
            self.navigate(Path(path), auto_descend=True)

    def navigate(self, path, auto_descend=False):
        logging.getLogger(__name__).info("Navigate: %s", path)
        self._restore_state = None
        if not isinstance(path, ArchiveLocation):
            path = Path(path).expanduser().absolute()
        self.navigation.navigate(path)
        seen = {self._descent_key(path)} if auto_descend else None
        self._load(path, descent_seen=seen)

    @staticmethod
    def _descent_key(path):
        return path if isinstance(path, ArchiveLocation) else Path(path).resolve()

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

    def _load(self, path, descent_seen=None):
        self._cancel_orientation_selection()
        self._descent_seen = descent_seen
        self.browser.directory = None if isinstance(path, ArchiveLocation) else path
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
        self._update_delete_action()
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
        seen = self._descent_seen
        self._descent_seen = None
        if not error and seen is not None and len(seen) < 64 and self.model.rowCount() == 1:
            entry = self.model.entries[0]
            if entry.kind == "folder":
                target = entry.location
                key = self._descent_key(target)
                if key not in seen:
                    seen.add(key)
                    logging.getLogger(__name__).info("Automatically descend: %s", target)
                    self.navigation.replace_current(target)
                    self._load(target, descent_seen=seen)
                    return
        self.model.sort()
        self._update_delete_action()
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
        if self._file_moving or self._archive_editing or self._folder_deleting:
            return
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
            self.navigate(entry.location, auto_descend=True)
        elif entry.kind == "image":
            self.open_image(entry)
        else:
            self.statusBar().showMessage("Nested ZIP archives are not supported")

    def open_image(self, entry):
        self._cancel_orientation_selection()
        logging.getLogger(__name__).info("Open image: %s", entry.display_path)
        self.delete_action.setEnabled(False)
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
        self._update_delete_action()
        self._schedule_thumbnails()

    def _selected_archive_images(self):
        current = self.navigation.current
        if not isinstance(current, ArchiveLocation):
            return []
        return [
            self.model.entries[index.row()]
            for index in self.browser.selectionModel().selectedIndexes()
            if self.model.entries[index.row()].kind == "image"
            and self.model.entries[index.row()].member is not None
            and self.model.entries[index.row()].path == current.path
        ]

    def _cancel_orientation_selection(self):
        self._orientation_generation += 1
        self._orientation_pending = None
        self.selection_workers.cancel()

    def _browser_selection_changed(self, *_args):
        if self._orientation_pending is not None:
            self._cancel_orientation_selection()
        self._update_delete_action()

    def _select_all_images(self):
        self._cancel_orientation_selection()
        self.browser.selectAll()

    def select_orientation(self, orientation):
        if (
            self._archive_editing
            or self._file_moving
            or self._folder_deleting
            or self._listing_active
            or self.stack.currentWidget() != self.browser
        ):
            return
        self._cancel_orientation_selection()
        self.browser.clearSelection()
        request = self._orientation_generation
        self._orientation_pending = (request, orientation)
        self.statusBar().showMessage(f"Finding {orientation} images… Esc cancels")
        self._update_delete_action()
        self.selection_workers.select_orientation(self.model.entries, request, orientation)

    def _orientation_progress(self, request, completed, total):
        if self._orientation_pending is not None and self._orientation_pending[0] == request:
            orientation = self._orientation_pending[1]
            self.statusBar().showMessage(
                f"Finding {orientation} images… {completed}/{total} — Esc cancels"
            )

    def _orientation_selected(self, request, identities, skipped, error):
        if self._orientation_pending is None or self._orientation_pending[0] != request:
            return
        orientation = self._orientation_pending[1]
        self._orientation_pending = None
        if self._closing or self.stack.currentWidget() != self.browser:
            return
        if error:
            self.statusBar().showMessage("Selection failed: " + error)
        else:
            self.browser.select_images(identities)
            message = f"Selected {len(identities)} {orientation} images"
            if skipped:
                message += f"; skipped {skipped} unreadable or oversized images"
            self.statusBar().showMessage(message)
        self._update_delete_action()

    def _update_delete_action(self, *_args):
        if not hasattr(self, "delete_action"):
            return
        enabled = (
            not self._archive_editing
            and not self._file_moving
            and not self._folder_deleting
            and self._orientation_pending is None
            and not self._listing_active
            and self.stack.currentWidget() == self.browser
            and bool(self._selected_archive_images())
        )
        self.delete_action.setEnabled(enabled)

    def delete_selected_images(self):
        if (
            self._archive_editing
            or self._file_moving
            or self._folder_deleting
            or self._listing_active
            or self._orientation_pending is not None
            or self.stack.currentWidget() != self.browser
        ):
            return
        if self._delete_confirmation is not None and self._delete_confirmation.isVisible():
            self._delete_confirmation.raise_()
            return
        entries = self._selected_archive_images()
        if not entries:
            return
        path = entries[0].path
        count = len(entries)
        image_count = f"{count} image" + ("s" if count != 1 else "")
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Delete images from ZIP?")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(f"Permanently delete {image_count} from {path.name}?")
        names = "\n".join(entry.member for entry in entries[:10])
        if count > 10:
            names += f"\n… and {count - 10} more"
        dialog.setInformativeText(
            f"{path}\n\n{names}\n\nOnly selected image entries will be removed. "
            "The ZIP will be rebuilt; this needs temporary disk space. "
            "Deleted images do not go to the Trash."
        )
        delete_button = dialog.addButton(
            f"Delete {image_count}", QMessageBox.ButtonRole.DestructiveRole
        )
        cancel_button = dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(cancel_button)
        dialog.setEscapeButton(cancel_button)
        self._delete_confirmation = dialog

        def decided(button):
            if button == delete_button and not self._closing:
                self._start_archive_delete(path, entries)

        dialog.buttonClicked.connect(decided)
        dialog.open()
        return dialog

    def _start_archive_delete(self, path, entries):
        if self._archive_editing:
            return
        self._cancel_orientation_selection()
        self._archive_editing = True
        self.browser.zip_drops_enabled = False
        self.tree.zip_drops_enabled = False
        self._refresh_timer.stop()
        self.workers.cancel()
        self.image_workers.cancel()
        self.generation += 1
        self.viewer_generation += 1
        self.requested.clear()
        self._thumbnail_timer.stop()
        self._update_delete_action()
        self._delete_progress = ArchiveDeleteProgress(self.archive_editor, self)
        apply_appearance(self._delete_progress, self.preferences.appearance)
        self._delete_progress.open()
        self.archive_editor.start(path, entries)

    def _archive_delete_progress(self, percent, label):
        if self._delete_progress is not None:
            self._delete_progress.update_progress(percent, label)

    def _archive_delete_finished(self, result, error, cancelled):
        self._archive_editing = False
        self.browser.zip_drops_enabled = True
        self.tree.zip_drops_enabled = True
        if self._delete_progress is not None:
            self._delete_progress.accept()
            self._delete_progress.deleteLater()
            self._delete_progress = None
        if self._close_after_edit:
            self.close()
            return
        if result is not None:
            current = self.navigation.current
            if isinstance(current, ArchiveLocation) and current.path == result.path:
                while current.internal not in result.folders:
                    current = current.parent
                self.navigation.replace_current(current)
            self._restore_state = None
            self.refresh()
            noun = "image" if result.removed == 1 else "images"
            message = f"Deleted {result.removed} {noun} from {result.path.name}"
        else:
            message = "Deletion cancelled; the original ZIP was kept" if cancelled else error
            self._reload_changed_source()
        logging.getLogger(__name__).info("ZIP edit: %s", message)
        self.statusBar().showMessage(message)
        if error:
            dialog = QMessageBox(self)
            dialog.setWindowTitle("Could not delete images")
            dialog.setIcon(QMessageBox.Icon.Warning)
            dialog.setTextFormat(Qt.TextFormat.PlainText)
            dialog.setText(error)
            dialog.setInformativeText("No deletion was applied. Refresh the archive and try again.")
            self._delete_error = dialog
            dialog.open()

    def _folder_context_menu(self, position):
        index = self.tree.indexAt(position)
        if not index.isValid():
            return
        path = Path(self.folders.filePath(index))
        menu = QMenu(self.tree)
        action = menu.addAction("Delete folder…")
        try:
            folder_identity(path)
            allowed = True
        except (OSError, ValueError):
            allowed = False
        action.setEnabled(
            allowed and not (self._archive_editing or self._file_moving or self._folder_deleting)
        )
        action.triggered.connect(lambda: self.delete_sidebar_folder(path))
        self._folder_menu = menu
        menu.popup(self.tree.viewport().mapToGlobal(position))

    def delete_sidebar_folder(self, path):
        if self._closing or self._archive_editing or self._file_moving or self._folder_deleting:
            return
        if self._delete_confirmation is not None and self._delete_confirmation.isVisible():
            self._delete_confirmation.raise_()
            return
        try:
            identity = folder_identity(path)
        except (OSError, ValueError) as error:
            self.statusBar().showMessage(str(error))
            return
        dialog = QMessageBox(self)
        dialog.setWindowTitle("Delete folder?")
        dialog.setIcon(QMessageBox.Icon.Warning)
        dialog.setTextFormat(Qt.TextFormat.PlainText)
        dialog.setText(f"Permanently delete folder {path.name} and all its contents?")
        dialog.setInformativeText(
            f"{path}\n\nAll files and subfolders will be deleted. "
            "This cannot be undone. Nothing is moved to the Trash."
        )
        delete = dialog.addButton("Delete folder", QMessageBox.ButtonRole.DestructiveRole)
        cancel = dialog.addButton(QMessageBox.StandardButton.Cancel)
        dialog.setDefaultButton(cancel)
        dialog.setEscapeButton(cancel)
        self._delete_confirmation = dialog

        def decided(button):
            if button != delete or self._closing:
                return
            self._folder_deleting = True
            self._cancel_orientation_selection()
            self.workers.cancel()
            self.image_workers.cancel()
            self.generation += 1
            self.viewer_generation += 1
            self._refresh_timer.stop()
            self._thumbnail_timer.stop()
            self._folder_progress = QProgressDialog("Deleting folder and contents…", "", 0, 0, self)
            self._folder_progress.setCancelButton(None)
            self._folder_progress.setWindowModality(Qt.WindowModality.ApplicationModal)
            self._folder_progress.open()
            self.folder_deleter.start(path, identity)

        dialog.buttonClicked.connect(decided)
        dialog.open()
        return dialog

    def _folder_delete_finished(self, path, error):
        self._folder_deleting = False
        self._folder_progress.close()
        self._folder_progress.deleteLater()
        current = self.navigation.current
        local = current.path if isinstance(current, ArchiveLocation) else current
        if local is not None and (local == path or path in local.parents):
            destination = path.parent
            while not destination.is_dir() and destination != destination.parent:
                destination = destination.parent
            self.navigation.replace_current(destination)
        self._restore_state = None
        self.refresh()
        if error:
            dialog = QMessageBox(self)
            dialog.setWindowTitle("Could not delete folder")
            dialog.setIcon(QMessageBox.Icon.Warning)
            dialog.setTextFormat(Qt.TextFormat.PlainText)
            dialog.setText(f"{path}\n\n{error}")
            dialog.setInformativeText("Some contents may already have been deleted.")
            self._delete_error = dialog
            dialog.open()
        else:
            self.statusBar().showMessage(f"Deleted folder {path}")
        if self._close_after_folder_delete:
            self.close()

    def move_zip_files(self, paths, directory):
        if (
            self._closing
            or self._file_moving
            or self._archive_editing
            or self._folder_deleting
            or not paths
        ):
            return
        self._file_moving = True
        self.browser.zip_drops_enabled = False
        self.tree.zip_drops_enabled = False
        self._cancel_orientation_selection()
        self._refresh_timer.stop()
        self._thumbnail_timer.stop()
        self.workers.cancel()
        self.image_workers.cancel()
        self.generation += 1
        self.viewer_generation += 1
        self.requested.clear()
        self._update_delete_action()
        self._move_progress = FileMoveProgress(self.file_mover, self)
        apply_appearance(self._move_progress, self.preferences.appearance)
        self._move_progress.open()
        self.file_mover.start(paths, directory)

    def _file_move_progress(self, position, total, name):
        if self._move_progress is not None:
            self._move_progress.update_file(position, total, name)

    def _file_move_finished(self, moved, errors, stopped):
        self._file_moving = False
        self.browser.zip_drops_enabled = True
        self.tree.zip_drops_enabled = True
        self._move_progress.accept()
        self._move_progress.deleteLater()
        self._move_progress = None
        for source, destination in moved:
            self.navigation.relocate_archive(source, destination)
        if self._close_after_move:
            self.close()
            return
        self._restore_state = None
        self.refresh()
        message = f"Moved {len(moved)} ZIP file(s)"
        if errors:
            message += f"; {len(errors)} could not be moved"
        if stopped:
            message += "; stopped after the current file"
        self.statusBar().showMessage(message)
        if errors:
            dialog = QMessageBox(self)
            dialog.setWindowTitle("Some ZIP files could not be moved")
            dialog.setIcon(QMessageBox.Icon.Warning)
            dialog.setTextFormat(Qt.TextFormat.PlainText)
            dialog.setText("\n".join(errors[:10]))
            dialog.setInformativeText("Existing destination files were not overwritten.")
            self._move_error = dialog
            dialog.open()

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
        if not (
            self._closing or self._archive_editing or self._file_moving or self._folder_deleting
        ):
            self._refresh_timer.start()

    def _browser_source_changed(self, generation, _path):
        if generation == self.generation:
            self._source_dirty()

    def _viewer_source_changed(self, generation, _path):
        if generation == self.viewer_generation:
            self._source_dirty()

    def _reload_changed_source(self):
        if self._archive_editing or self._file_moving or self._folder_deleting:
            return
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

    def show_help(self):
        if self._help_dialog is not None:
            self._help_dialog.close()
            self._help_dialog.deleteLater()
        self._help_dialog = HelpDialog(self._shortcut_actions, self)
        apply_appearance(self._help_dialog, self.preferences.appearance)
        self._help_dialog.show()
        return self._help_dialog

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
        self.viewer.set_smooth_scaling(self.preferences.smooth_scaling)
        logging.getLogger(__name__).info(
            "Preferences saved: rendering=%s smoothing=%s",
            self.preferences.rendering,
            self.preferences.smooth_scaling,
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
            logging.getLogger(__name__).warning("%s", message)
        else:
            logging.getLogger(__name__).info("%s", message)
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
        if self._orientation_pending is not None:
            self._cancel_orientation_selection()
            self._update_delete_action()
            self.statusBar().showMessage("Orientation selection cancelled")
            return
        if self.isFullScreen():
            self.showNormal()
        else:
            self.show_grid()

    def closeEvent(self, event):
        if self._folder_deleting:
            self._close_after_folder_delete = True
            event.ignore()
            return
        if self._file_moving:
            self._close_after_move = True
            self._move_progress.reject()
            event.ignore()
            return
        if self._archive_editing:
            self._close_after_edit = True
            self._delete_progress.reject()
            event.ignore()
            return
        self._closing = True
        self._cancel_orientation_selection()
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
        self.archive_editor.shutdown()
        self.file_mover.shutdown()
        self.folder_deleter.shutdown()
        self.workers.shutdown()
        self.image_workers.shutdown()
        self.selection_workers.shutdown()
        self.scheduler.shutdown()
        self.cache.close()
        super().closeEvent(event)
