"""Virtual thumbnail browser with a bounded GUI-thread pixmap cache."""

from collections import OrderedDict

from PySide6.QtCore import (
    QAbstractListModel,
    QItemSelection,
    QItemSelectionModel,
    QModelIndex,
    QRect,
    QSize,
    Qt,
    Signal,
)
from PySide6.QtGui import QColor, QFont, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QListView, QStyle

from qtvw.domain import natural_key


class BrowserModel(QAbstractListModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.entries = []
        self.pixmaps = OrderedDict()
        self.failed = set()
        self.errors = {}
        self.thumbnail_size = 144
        self.sort_field = "name"
        self.sort_order = Qt.SortOrder.AscendingOrder

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.entries)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        entry = self.entries[index.row()]
        if role == Qt.ItemDataRole.SizeHintRole:
            return QSize(self.thumbnail_size + 32, self.thumbnail_size + 48)
        if role == Qt.ItemDataRole.DisplayRole:
            return entry.name
        if role == Qt.ItemDataRole.ToolTipRole:
            reason = self.errors.get(entry.identity, "")
            if entry.kind == "archive" and entry.member is not None:
                reason = "Nested ZIP archives are not supported"
            return entry.display_path + (f"\n{reason}" if reason else "")
        if role == Qt.ItemDataRole.DecorationRole:
            if entry.identity in self.pixmaps:
                self.pixmaps.move_to_end(entry.identity)
                return self.pixmaps[entry.identity]
            if entry.kind == "archive":
                return archive_badge(
                    QApplication.style()
                    .standardIcon(QStyle.StandardPixmap.SP_DirIcon)
                    .pixmap(96, 96)
                )
            icon = (
                QStyle.StandardPixmap.SP_DirIcon
                if entry.kind == "folder"
                else QStyle.StandardPixmap.SP_FileIcon
            )
            return QApplication.style().standardIcon(icon)
        return None

    def clear(self):
        self.beginResetModel()
        self.entries.clear()
        self.pixmaps.clear()
        self.failed.clear()
        self.errors.clear()
        self.endResetModel()

    def append(self, entries):
        if not entries:
            return
        start = len(self.entries)
        self.beginInsertRows(QModelIndex(), start, start + len(entries) - 1)
        self.entries.extend(entries)
        self.endInsertRows()

    def sort(self, column=0, order=None):
        if order is not None:
            self.sort_order = order
        self.layoutAboutToBeChanged.emit()
        old = self.persistentIndexList()
        identities = [self.entries[index.row()].identity for index in old]

        def key(entry):
            if self.sort_field == "size":
                return entry.size, natural_key(entry.name)
            if self.sort_field == "modified":
                return entry.modified_ns, natural_key(entry.name)
            return natural_key(entry.name)

        reverse = self.sort_order == Qt.SortOrder.DescendingOrder
        folders = sorted((e for e in self.entries if e.kind == "folder"), key=key, reverse=reverse)
        files = sorted((e for e in self.entries if e.kind != "folder"), key=key, reverse=reverse)
        self.entries[:] = folders + files
        positions = {e.identity: i for i, e in enumerate(self.entries)}
        self.changePersistentIndexList(old, [self.index(positions[i]) for i in identities])
        self.layoutChanged.emit()

    def decoded(self, identity, image, error):
        if error or image.isNull():
            self.failed.add(identity)
            self.errors[identity] = error or "Unable to decode image"
        else:
            self.pixmaps[identity] = QPixmap.fromImage(image)
            while len(self.pixmaps) > 256:
                self.pixmaps.popitem(last=False)
        for row, entry in enumerate(self.entries):
            if entry.identity == identity:
                index = self.index(row)
                if entry.kind == "archive" and identity in self.pixmaps:
                    self.pixmaps[identity] = archive_badge(self.pixmaps[identity])
                self.dataChanged.emit(index, index)
                break


def archive_badge(pixmap):
    """Apply a ZIP badge to covers and archive placeholders on the GUI thread."""
    result = pixmap.copy()
    painter = QPainter(result)
    ratio = result.devicePixelRatio()
    rectangle = QRect(
        max(0, int(result.width() / ratio) - 34),
        max(0, int(result.height() / ratio) - 19),
        34,
        19,
    )
    painter.fillRect(rectangle, QColor("#263644"))
    painter.setPen(QColor("white"))
    painter.setFont(QFont("", 9, QFont.Weight.Bold))
    painter.drawText(rectangle, Qt.AlignmentFlag.AlignCenter, "ZIP")
    painter.end()
    return result


class Browser(QListView):
    visible_changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVerticalScrollMode(QListView.ScrollMode.ScrollPerPixel)
        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setUniformItemSizes(True)
        self.setWordWrap(False)
        self.setSelectionMode(QListView.SelectionMode.ExtendedSelection)
        self.verticalScrollBar().valueChanged.connect(self.visible_changed)
        self.set_thumbnail_size(144)

    def set_thumbnail_size(self, size):
        if self.model() is not None:
            self.model().thumbnail_size = size
        self.setIconSize(QSize(size, size))
        self.setGridSize(QSize(size + 32, size + 48))
        self.visible_changed.emit()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.visible_changed.emit()

    def selectAll(self):
        """Select image rows only; folders and nested archives are kept."""
        self.select_images()

    def select_images(self, identities=None):
        """Replace selection, grouping adjacent matches for large image grids."""
        if identities is not None:
            identities = set(identities)
        selection = QItemSelection()
        start = None
        for row, entry in enumerate(self.model().entries):
            if entry.kind == "image" and (identities is None or entry.identity in identities):
                if start is None:
                    start = row
            elif start is not None:
                selection.select(self.model().index(start), self.model().index(row - 1))
                start = None
        if start is not None:
            selection.select(
                self.model().index(start), self.model().index(self.model().rowCount() - 1)
            )
        self.selectionModel().select(selection, QItemSelectionModel.SelectionFlag.ClearAndSelect)

    def keyPressEvent(self, event):
        if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
            if self.currentIndex().isValid():
                self.activated.emit(self.currentIndex())
            event.accept()
            return
        super().keyPressEvent(event)

    def visible_rows(self, nearby=True):
        model = self.model()
        if model is None:
            return []
        # QListView owns geometry; request only rows intersecting the viewport.
        margin = self.gridSize().height() if nearby else 0
        rectangle = self.viewport().rect().adjusted(0, -margin, 0, margin)
        columns = max(1, self.viewport().width() // self.gridSize().width())
        first = max(0, self.verticalScrollBar().value() // self.gridSize().height() - 2) * columns
        count = (self.viewport().height() // self.gridSize().height() + 5) * columns
        return [
            row
            for row in range(first, min(first + count, model.rowCount()))
            if self.visualRect(model.index(row)).intersects(rectangle)
        ]
