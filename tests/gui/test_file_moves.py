"""Real Qt URL drops route ZIP moves without mutating archive members."""

import zipfile
from threading import Event

import pytest
from PySide6.QtCore import QItemSelectionModel, QMimeData, QPoint, QPointF, Qt, QTimer, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from qtvw.domain.locations import ArchiveLocation
from qtvw.services import file_moves
from qtvw.ui import browser as browser_module
from qtvw.ui.main_window import MainWindow


def collection(qtbot, tmp_path, image_bytes):
    folder = tmp_path / "destination"
    folder.mkdir()
    for name in ("first.zip", "second.zip"):
        with zipfile.ZipFile(tmp_path / name, "w") as stream:
            stream.writestr("one/photo.png", image_bytes())
    (tmp_path / "photo.png").write_bytes(image_bytes())
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    return window, folder


def url_mime(paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    return mime


def drag_move(view, mime, position):
    actions = Qt.DropAction.CopyAction | Qt.DropAction.MoveAction
    events = []
    for event_type in (QDragEnterEvent, QDragMoveEvent):
        arguments = (actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        try:
            event = event_type(QPointF(position), *arguments)
        except TypeError:  # Qt versions before the floating-point overload.
            event = event_type(position, *arguments)
        QApplication.sendEvent(view.viewport(), event)
        events.append(event)
    return events


def drop(view, paths, position):
    mime = url_mime(paths)
    enter, move = drag_move(view, mime, position)
    event = QDropEvent(
        QPointF(position),
        Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(view.viewport(), event)
    return enter, move, event


def settle_move(qtbot, window):
    qtbot.waitUntil(lambda: not window._file_moving and not window._listing_active)


def folder_center(window):
    row = next(row for row, entry in enumerate(window.model.entries) if entry.kind == "folder")
    return window.browser.visualRect(window.model.index(row)).center()


def sidebar_center(qtbot, window, folder):
    window.tree.setRootIndex(window.folders.index(str(folder.parent)))
    index = window.folders.index(str(folder))
    window.tree.scrollTo(index)
    qtbot.waitUntil(lambda: window.tree.visualRect(index).height() > 0)
    return window.tree.visualRect(index).center(), index


def test_grid_folder_drop_moves_multiple_zips_and_refreshes(qtbot, tmp_path, image_bytes):
    window, folder = collection(qtbot, tmp_path, image_bytes)
    sources = [tmp_path / "first.zip", tmp_path / "second.zip"]
    original = [source.read_bytes() for source in sources]
    enter, move, event = drop(window.browser, sources, folder_center(window))
    assert enter.isAccepted() and move.isAccepted() and event.isAccepted()
    # Acknowledge external drags as Copy: QtVw owns removal, not the drag source.
    assert event.dropAction() == Qt.DropAction.CopyAction
    settle_move(qtbot, window)
    for source, data in zip(sources, original):
        assert not source.exists()
        assert (folder / source.name).read_bytes() == data
    assert [entry.name for entry in window.model.entries] == ["destination", "photo.png"]
    assert window.navigation.current == tmp_path


def test_sidebar_hover_expands_then_drop_moves_zip(qtbot, tmp_path, image_bytes):
    window, folder = collection(qtbot, tmp_path, image_bytes)
    (folder / "subfolder").mkdir()
    position, index = sidebar_center(qtbot, window, folder)
    mime = url_mime([tmp_path / "first.zip"])
    enter, move = drag_move(window.tree, mime, position)
    assert enter.isAccepted() and move.isAccepted()
    qtbot.waitUntil(lambda: window.tree.isExpanded(index))
    assert window.tree._drop_active
    event = QDropEvent(
        QPointF(position),
        Qt.DropAction.CopyAction | Qt.DropAction.MoveAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(window.tree.viewport(), event)
    assert event.isAccepted()
    settle_move(qtbot, window)
    assert (folder / "first.zip").is_file()
    assert not (tmp_path / "first.zip").exists()
    assert not window.tree._drop_active


def test_external_drop_on_grid_background_moves_into_current_folder(qtbot, tmp_path, image_bytes):
    window, folder = collection(qtbot, tmp_path, image_bytes)
    source = tmp_path / "first.zip"
    window.navigate(folder)
    qtbot.waitUntil(lambda: not window._listing_active)
    position = QPoint(
        window.browser.viewport().width() - 10, window.browser.viewport().height() - 10
    )
    _, _, event = drop(window.browser, [source], position)
    assert event.isAccepted()
    settle_move(qtbot, window)
    assert not source.exists()
    assert [entry.name for entry in window.model.entries] == ["first.zip"]


@pytest.mark.parametrize("invalid", ["image", "mixed", "archive_folder", "archive_item", "busy"])
def test_invalid_drop_cannot_move_files(qtbot, tmp_path, image_bytes, invalid):
    window, folder = collection(qtbot, tmp_path, image_bytes)
    source = tmp_path / "first.zip"
    position = folder_center(window)
    paths = [source]
    if invalid == "image":
        paths = [tmp_path / "photo.png"]
    elif invalid == "mixed":
        paths.append(tmp_path / "photo.png")
    elif invalid == "archive_folder":
        window.navigate(ArchiveLocation(source))
        qtbot.waitUntil(lambda: not window._listing_active)
        position = window.browser.visualRect(window.model.index(0)).center()
    elif invalid == "archive_item":
        row = next(
            row for row, entry in enumerate(window.model.entries) if entry.name == "second.zip"
        )
        position = window.browser.visualRect(window.model.index(row)).center()
    else:
        window.browser.zip_drops_enabled = False
    _, _, event = drop(window.browser, paths, position)
    assert not event.isAccepted()
    assert not window._file_moving
    assert source.exists()
    assert list(folder.iterdir()) == []


def test_destination_conflict_keeps_both_original_files(qtbot, tmp_path, image_bytes):
    window, folder = collection(qtbot, tmp_path, image_bytes)
    source = tmp_path / "first.zip"
    before = source.read_bytes()
    (folder / source.name).write_bytes(b"keep destination")
    drop(window.browser, [source], folder_center(window))
    settle_move(qtbot, window)
    assert source.read_bytes() == before
    assert (folder / source.name).read_bytes() == b"keep destination"
    assert window._move_error.isVisible()
    window._move_error.accept()


def test_drag_source_exports_only_selected_local_zip_urls(
    qtbot, tmp_path, image_bytes, monkeypatch
):
    window, _ = collection(qtbot, tmp_path, image_bytes)
    captured = []

    class Drag:
        def __init__(self, parent):
            pass

        def setMimeData(self, mime):
            captured.append([url.toLocalFile() for url in mime.urls()])

        def exec(self, *args):
            return Qt.DropAction.MoveAction

        def deleteLater(self):
            pass

    monkeypatch.setattr(browser_module, "QDrag", Drag)
    selection = window.browser.selectionModel()
    for row, entry in enumerate(window.model.entries):
        if entry.kind == "archive":
            assert window.model.flags(window.model.index(row)) & Qt.ItemFlag.ItemIsDragEnabled
            selection.select(window.model.index(row), QItemSelectionModel.SelectionFlag.Select)
    window.browser.startDrag(Qt.DropAction.MoveAction)
    assert set(captured[0]) == {str(tmp_path / "first.zip"), str(tmp_path / "second.zip")}
    assert (tmp_path / "first.zip").exists()  # No source deletion on drag acknowledgement.
    window.browser.selectAll()  # Local image only: not a ZIP drag.
    window.browser.startDrag(Qt.DropAction.MoveAction)
    assert len(captured) == 1


def test_moving_open_archive_relocates_history_and_reloads_contents(qtbot, tmp_path, image_bytes):
    window, folder = collection(qtbot, tmp_path, image_bytes)
    source = tmp_path / "first.zip"
    window.navigate(ArchiveLocation(source, "one"))
    qtbot.waitUntil(lambda: not window._listing_active)
    position, _ = sidebar_center(qtbot, window, folder)
    drop(window.tree, [source], position)
    settle_move(qtbot, window)
    assert window.navigation.current == ArchiveLocation(folder / source.name, "one")
    assert [entry.name for entry in window.model.entries] == ["photo.png"]
    assert window.model.entries[0].path == folder / source.name
    window.back()
    qtbot.waitUntil(lambda: not window._listing_active)
    assert window.navigation.current == tmp_path
    window.forward()
    qtbot.waitUntil(lambda: not window._listing_active)
    assert window.navigation.current == ArchiveLocation(folder / source.name, "one")


@pytest.mark.parametrize("close", [False, True])
def test_stop_or_close_waits_for_current_move_and_skips_remaining(
    qtbot, tmp_path, image_bytes, monkeypatch, close
):
    window, folder = collection(qtbot, tmp_path, image_bytes)
    started, release = Event(), Event()
    original = file_moves.move_zip

    def slow(source, destination):
        started.set()
        assert release.wait(5)
        return original(source, destination)

    monkeypatch.setattr(file_moves, "move_zip", slow)
    window.move_zip_files([tmp_path / "first.zip", tmp_path / "second.zip"], folder)
    qtbot.waitUntil(started.is_set)
    heartbeat = []
    QTimer.singleShot(0, lambda: heartbeat.append(True))
    qtbot.waitUntil(lambda: bool(heartbeat))
    if close:
        window.close()
    else:
        window._move_progress.cancel_button.click()
    assert window._file_moving
    release.set()
    qtbot.waitUntil(lambda: not window._file_moving)
    if close:
        qtbot.waitUntil(lambda: not window.isVisible())
    else:
        settle_move(qtbot, window)
    assert (folder / "first.zip").exists()
    assert not (tmp_path / "first.zip").exists()
    assert (tmp_path / "second.zip").exists()
    assert not (folder / "second.zip").exists()


def test_batch_conflict_does_not_prevent_other_zip_moves(qtbot, tmp_path, image_bytes):
    window, folder = collection(qtbot, tmp_path, image_bytes)
    (folder / "first.zip").write_bytes(b"keep destination")
    window.move_zip_files([tmp_path / "first.zip", tmp_path / "second.zip"], folder)
    settle_move(qtbot, window)
    assert (tmp_path / "first.zip").exists()
    assert (folder / "first.zip").read_bytes() == b"keep destination"
    assert not (tmp_path / "second.zip").exists()
    assert (folder / "second.zip").exists()
    window._move_error.accept()
