"""Exercise selection, confirmation, real ZIP rewrites, and cancellation from Qt."""

import zipfile
from threading import Event

from PySide6.QtCore import QItemSelectionModel, Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QMessageBox

from qtvw.domain.locations import ArchiveLocation
from qtvw.services import archive_edit as edit_service
from qtvw.sources.archive import Cancelled
from qtvw.ui.main_window import MainWindow


def open_zip(qtbot, tmp_path, image_bytes, nested=False):
    path = tmp_path / "photos.zip"
    prefix = "one/two/" if nested else ""
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as stream:
        for index, color in enumerate(("red", "green", "blue")):
            stream.writestr(f"{prefix}photo{index}.png", image_bytes(color))
        stream.writestr("folder/notes.txt", b"keep")
        stream.writestr("nested.zip", b"keep nested")
    location = ArchiveLocation(path, prefix.rstrip("/"))
    window = MainWindow(location)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    return window, path


def image_rows(window):
    return [row for row, entry in enumerate(window.model.entries) if entry.kind == "image"]


def select_rows(window, rows):
    window.browser.clearSelection()
    for row in rows:
        window.browser.selectionModel().select(
            window.model.index(row), QItemSelectionModel.SelectionFlag.Select
        )


def confirm_delete(window):
    dialog = window.delete_selected_images()
    assert dialog is not None
    assert dialog.defaultButton() == dialog.button(QMessageBox.StandardButton.Cancel)
    button = next(
        button
        for button in dialog.buttons()
        if dialog.buttonRole(button) == QMessageBox.ButtonRole.DestructiveRole
    )
    button.click()


def test_multiselect_shortcuts_confirmation_and_real_delete(qtbot, tmp_path, image_bytes):
    window, path = open_zip(qtbot, tmp_path, image_bytes)
    rows = image_rows(window)

    # Exercise actual mouse selection rather than just calling selection helpers.
    def rectangle(row):
        return window.browser.visualRect(window.model.index(row)).center()

    qtbot.mouseClick(window.browser.viewport(), Qt.MouseButton.LeftButton, pos=rectangle(rows[0]))
    qtbot.mouseClick(
        window.browser.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier,
        pos=rectangle(rows[2]),
    )
    assert len(window.browser.selectedIndexes()) == 2
    qtbot.mouseClick(window.browser.viewport(), Qt.MouseButton.LeftButton, pos=rectangle(rows[0]))
    qtbot.mouseClick(
        window.browser.viewport(),
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ShiftModifier,
        pos=rectangle(rows[2]),
    )
    assert len(window.browser.selectedIndexes()) == 3
    window.activateWindow()
    window.browser.setFocus()
    qtbot.waitUntil(lambda: window.isActiveWindow())
    qtbot.keyClick(window.browser, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
    assert {index.row() for index in window.browser.selectedIndexes()} == set(rows)
    assert window.delete_action.isEnabled()
    before = path.read_bytes()
    qtbot.keyClick(window.browser, Qt.Key.Key_Delete)
    qtbot.waitUntil(lambda: window._delete_confirmation is not None)
    dialog = window._delete_confirmation
    assert "3 image" in dialog.text()
    dialog.button(QMessageBox.StandardButton.Cancel).click()
    assert path.read_bytes() == before
    assert not window._archive_editing
    # Keep one image and verify positions/cache keys shift correctly after deletion.
    select_rows(window, rows[:2])
    confirm_delete(window)
    qtbot.waitUntil(lambda: not window._archive_editing and not window._listing_active)
    assert [entry.name for entry in window.model.entries if entry.kind == "image"] == ["photo2.png"]
    with zipfile.ZipFile(path) as stream:
        assert stream.namelist() == ["photo2.png", "folder/notes.txt", "nested.zip"]
    entry = next(entry for entry in window.model.entries if entry.kind == "image")
    window.open_image(entry)
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    assert window.viewer.item.pixmap().toImage().pixelColor(0, 0) == QColor("blue")
    assert not window.delete_action.isEnabled()
    window.show_grid()
    select_rows(window, image_rows(window))
    assert window.delete_action.isEnabled()


def test_delete_last_images_returns_to_surviving_parent(qtbot, tmp_path, image_bytes):
    window, path = open_zip(qtbot, tmp_path, image_bytes, nested=True)
    window.browser.selectAll()
    confirm_delete(window)
    qtbot.waitUntil(lambda: not window._archive_editing and not window._listing_active)
    assert window.navigation.current == ArchiveLocation(path)
    assert [entry.name for entry in window.model.entries] == ["folder", "nested.zip"]
    assert not window.delete_action.isEnabled()


def test_local_images_cannot_be_deleted(qtbot, tmp_path, image_bytes):
    image = tmp_path / "photo.png"
    image.write_bytes(image_bytes())
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    window.browser.selectAll()
    assert not window.delete_action.isEnabled()
    assert window.delete_selected_images() is None
    assert image.exists()


def test_background_cancel_keeps_ui_responsive_and_original(
    qtbot, tmp_path, image_bytes, monkeypatch
):
    started = Event()

    def wait_for_cancel(path, entries, token, progress, limits):
        started.set()
        progress(10, "Rewriting ZIP…")
        assert token.wait(5)
        raise Cancelled()

    monkeypatch.setattr(edit_service, "delete_images", wait_for_cancel)
    window, path = open_zip(qtbot, tmp_path, image_bytes)
    original = path.read_bytes()
    window.browser.selectAll()
    confirm_delete(window)
    qtbot.waitUntil(started.is_set)
    heartbeat = []
    QTimer.singleShot(0, lambda: heartbeat.append(True))
    qtbot.waitUntil(lambda: bool(heartbeat))
    window._delete_progress.cancel_button.click()
    qtbot.waitUntil(lambda: not window._archive_editing and not window._listing_active)
    assert path.read_bytes() == original
    assert window._delete_progress is None
    assert len(image_rows(window)) == 3


def test_close_cancels_edit_before_shutting_down(qtbot, tmp_path, image_bytes, monkeypatch):
    started = Event()

    def wait_for_cancel(path, entries, token, progress, limits):
        started.set()
        assert token.wait(5)
        raise Cancelled()

    monkeypatch.setattr(edit_service, "delete_images", wait_for_cancel)
    window, path = open_zip(qtbot, tmp_path, image_bytes)
    original = path.read_bytes()
    window.browser.selectAll()
    confirm_delete(window)
    qtbot.waitUntil(started.is_set)
    window.close()
    qtbot.waitUntil(lambda: not window.isVisible())
    assert not window._archive_editing
    assert window._closing
    assert path.read_bytes() == original


def test_stale_confirmed_selection_reports_error_and_refreshes(qtbot, tmp_path, image_bytes):
    window, path = open_zip(qtbot, tmp_path, image_bytes)
    window.browser.selectAll()
    # Mimic an external edit after selection but before the confirmation is accepted.
    dialog = window.delete_selected_images()
    window.watcher.removePaths(window.watcher.files())
    with zipfile.ZipFile(path, "a") as stream:
        stream.writestr("external.png", image_bytes())
    external = path.read_bytes()
    button = next(
        button
        for button in dialog.buttons()
        if dialog.buttonRole(button) == QMessageBox.ButtonRole.DestructiveRole
    )
    button.click()
    qtbot.waitUntil(lambda: not window._archive_editing and not window._listing_active)
    assert path.read_bytes() == external
    assert "external.png" in [entry.name for entry in window.model.entries]
    assert window._delete_error.isVisible()
    assert "stale" in window._delete_error.text()
    window._delete_error.accept()


def test_success_is_reported_after_commit_even_if_cache_cleanup_fails(
    qtbot, tmp_path, image_bytes, monkeypatch
):
    window, path = open_zip(qtbot, tmp_path, image_bytes)

    def fail(path):
        raise OSError("cache unavailable")

    monkeypatch.setattr(window.cache, "invalidate_source", fail)
    window.browser.selectAll()
    with qtbot.waitSignal(window.archive_editor.finished) as signal:
        confirm_delete(window)
    result, error, cancelled = signal.args
    assert result.removed == 3
    assert not error and not cancelled
    qtbot.waitUntil(lambda: not window._listing_active)
    assert image_rows(window) == []
    with zipfile.ZipFile(path) as stream:
        assert all(not name.endswith(".png") for name in stream.namelist())
