"""ZIP URL drop targets for the image grid and the filesystem sidebar."""

from pathlib import Path

from PySide6.QtCore import QPersistentModelIndex, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPen
from PySide6.QtWidgets import QTreeView

from qtvw.ui.archive_delete import ArchiveDeleteProgress


def zip_paths(mime):
    if not mime.hasUrls():
        return []
    paths = []
    for url in mime.urls():
        if not url.isLocalFile():
            return []
        path = Path(url.toLocalFile())
        if path.suffix.lower() != ".zip":
            return []
        paths.append(path)
    return list(dict.fromkeys(paths))


class ZipDropTarget:
    """The receiver owns the filesystem move, including external URI drops."""

    def setup_zip_drops(self):
        self.zip_drops_enabled = True
        self._drop_active = False
        self._drop_index = QPersistentModelIndex()
        self.setAcceptDrops(True)
        self.viewport().setAcceptDrops(True)

    def _accept_zip_drop(self, event):
        if not self.zip_drops_enabled or not zip_paths(event.mimeData()):
            return False
        source = event.source()
        internal = source is not None and source.property("qtvw_zip_drag") is True
        # External drag sources must not remove originals on an asynchronous drop
        # acknowledgement. QtVw performs both the copy/rename and source removal.
        action = Qt.DropAction.MoveAction if internal else Qt.DropAction.CopyAction
        if not event.possibleActions() & action:
            return False
        event.setDropAction(action)
        event.accept()
        return True

    def dragEnterEvent(self, event):
        if not self._accept_zip_drop(event):
            event.ignore()

    def dragMoveEvent(self, event):
        folder, index = self.folder_at(event.position().toPoint())
        if folder is None or not self._accept_zip_drop(event):
            self._clear_drop()
            event.ignore()
            return
        self._drop_active = True
        self._drop_index = QPersistentModelIndex(index)
        self.viewport().update()

    def dragLeaveEvent(self, event):
        self._clear_drop()
        event.accept()

    def dropEvent(self, event):
        folder, _ = self.folder_at(event.position().toPoint())
        self._clear_drop()
        if folder is None or not self._accept_zip_drop(event):
            event.ignore()
            return
        self.zip_files_dropped.emit(zip_paths(event.mimeData()), folder)

    def _clear_drop(self):
        self._drop_active = False
        self._drop_index = QPersistentModelIndex()
        self.viewport().update()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self._drop_active:
            rectangle = (
                self.visualRect(self._drop_index)
                if self._drop_index.isValid()
                else self.viewport().rect()
            )
            painter = QPainter(self.viewport())
            painter.setPen(QPen(self.palette().highlight().color(), 2))
            painter.drawRect(rectangle.adjusted(2, 2, -2, -2))
            painter.end()


class FolderTree(ZipDropTarget, QTreeView):
    zip_files_dropped = Signal(list, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_zip_drops()
        self._hover = QPersistentModelIndex()
        self._expand_timer = QTimer(self)
        self._expand_timer.setSingleShot(True)
        self._expand_timer.setInterval(700)
        self._expand_timer.timeout.connect(self._expand_hover)

    def folder_at(self, position):
        index = self.indexAt(position)
        if index.isValid() and self.model().isDir(index):
            return Path(self.model().filePath(index)), index
        return None, index

    def dragMoveEvent(self, event):
        super().dragMoveEvent(event)
        if self._drop_active and self._drop_index != self._hover:
            self._hover = QPersistentModelIndex(self._drop_index)
            self._expand_timer.start()
        elif not self._drop_active:
            self._expand_timer.stop()

    def _expand_hover(self):
        if self._drop_active and self._hover.isValid():
            self.expand(self._hover)

    def _clear_drop(self):
        self._expand_timer.stop()
        self._hover = QPersistentModelIndex()
        super()._clear_drop()


class FileMoveProgress(ArchiveDeleteProgress):
    def __init__(self, service, parent=None):
        super().__init__(service, parent)
        self.setWindowTitle("Moving ZIP files")
        self.progress.setRange(0, 0)
        self.label.setText("Moving ZIP files…")
        self.cancel_button.setText("Stop after current file")

    def update_file(self, position, total, name):
        if self.cancel_button.isEnabled():
            self.label.setText(f"Moving {position}/{total}: {name}\nLarge transfers may take time.")

    def reject(self):
        self.editor.cancel()
        self.cancel_button.setEnabled(False)
        self.label.setText("Stopping after the current file finishes…")
