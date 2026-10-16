"""Orientation selection uses source metadata, handles rotation, and rejects stale scans."""

import struct
import zipfile
from threading import Event

import pytest
from PySide6.QtCore import QBuffer, QIODevice, QItemSelectionModel, Qt
from PySide6.QtGui import QImage, QImageReader

from qtvw.domain.locations import ArchiveLocation
from qtvw.ui.main_window import MainWindow


def rotated_jpeg(orientation):
    image = QImage(80, 40, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.red)
    output = QBuffer()
    output.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(output, "JPEG")
    data = bytes(output.data())
    exif = (
        b"Exif\0\0II"
        + struct.pack("<HIH", 42, 8, 1)
        + struct.pack("<HHI", 0x0112, 3, 1)
        + struct.pack("<H", orientation)
        + b"\0\0"
        + struct.pack("<I", 0)
    )
    return data[:2] + b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif + data[2:]


def open_collection(qtbot, tmp_path, image_bytes, archived):
    members = {
        "portrait.png": image_bytes(width=40, height=80),
        "landscape.png": image_bytes(width=80, height=40),
        "square.png": image_bytes(width=40, height=40),
        "rotated.jpg": rotated_jpeg(6),
        "bad.png": b"not an image",
        "folder/nested.png": image_bytes(width=40, height=80),
    }
    if archived:
        path = tmp_path / "photos.zip"
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in members.items():
                archive.writestr(name, data)
        location = ArchiveLocation(path)
    else:
        for name, data in members.items():
            path = tmp_path / name
            path.parent.mkdir(exist_ok=True)
            path.write_bytes(data)
        location = tmp_path
    window = MainWindow(location)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    return window


def selected_names(window):
    return {window.model.entries[index.row()].name for index in window.browser.selectedIndexes()}


@pytest.mark.parametrize("archived", [False, True])
def test_orientation_key_bindings_replace_selection_and_skip_square(
    qtbot, tmp_path, image_bytes, archived
):
    window = open_collection(qtbot, tmp_path, image_bytes, archived)
    window.activateWindow()
    window.browser.setFocus()
    qtbot.waitUntil(lambda: window.isActiveWindow())
    modifiers = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    qtbot.keyClick(window.browser, Qt.Key.Key_P, modifiers)
    qtbot.waitUntil(lambda: window._orientation_pending is None)
    assert selected_names(window) == {"portrait.png", "rotated.jpg"}
    assert "skipped 1" in window.statusBar().currentMessage()
    qtbot.keyClick(window.browser, Qt.Key.Key_L, modifiers)
    qtbot.waitUntil(lambda: window._orientation_pending is None)
    assert selected_names(window) == {"landscape.png"}
    assert window.delete_action.isEnabled() == archived
    assert all(entry.name != "nested.png" for entry in window.model.entries)
    # The location editor retains normal text editing and cannot start a scan.
    window.location.setFocus()
    qtbot.keyClick(window.location, Qt.Key.Key_P, modifiers)
    assert window._orientation_pending is None
    assert selected_names(window) == {"landscape.png"}


@pytest.mark.parametrize("orientation", range(1, 9))
def test_all_exif_transformations_match_viewer_orientation(qtbot, tmp_path, orientation):
    path = tmp_path / "rotated.jpg"
    path.write_bytes(rotated_jpeg(orientation))
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    image = reader.read()
    assert not image.isNull()
    expected = "portrait" if image.height() > image.width() else "landscape"
    window.select_orientation(expected)
    qtbot.waitUntil(lambda: window._orientation_pending is None)
    assert selected_names(window) == {"rotated.jpg"}
    window.select_orientation("landscape" if expected == "portrait" else "portrait")
    qtbot.waitUntil(lambda: window._orientation_pending is None)
    assert selected_names(window) == set()


@pytest.mark.parametrize("cancel", ["navigate", "escape", "manual", "all", "switch"])
def test_cancelled_scan_cannot_replace_new_selection(
    qtbot, tmp_path, image_bytes, monkeypatch, cancel
):
    window = open_collection(qtbot, tmp_path, image_bytes, archived=False)
    started, stopped = Event(), Event()
    original = window.selection_workers._orientation
    first = True

    def slow(entry, token):
        nonlocal first
        if first:
            first = False
            started.set()
            assert token.wait(5)
            stopped.set()
        return original(entry, token)

    monkeypatch.setattr(window.selection_workers, "_orientation", slow)
    window.select_orientation("portrait")
    request = window._orientation_pending[0]
    qtbot.waitUntil(started.is_set)
    identity = next(
        entry.identity for entry in window.model.entries if entry.name == "portrait.png"
    )
    expected = set()
    if cancel == "navigate":
        window.navigate(tmp_path / "folder")
    elif cancel == "escape":
        window.escape()
    elif cancel == "manual":
        row = next(
            row for row, entry in enumerate(window.model.entries) if entry.name == "square.png"
        )
        window.browser.selectionModel().select(
            window.model.index(row), QItemSelectionModel.SelectionFlag.ClearAndSelect
        )
        expected = {"square.png"}
    elif cancel == "switch":
        window.select_orientation("landscape")
        expected = {"landscape.png"}
    else:
        window._select_all_images()
        expected = {"bad.png", "portrait.png", "landscape.png", "square.png", "rotated.jpg"}
    qtbot.waitUntil(stopped.is_set)
    qtbot.waitUntil(lambda: window._orientation_pending is None and not window._listing_active)
    window._orientation_selected(request, [identity], 0, "")
    assert selected_names(window) == expected


def test_resource_limits_and_empty_collection(qtbot, tmp_path, image_bytes):
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    window.select_orientation("portrait")
    qtbot.waitUntil(lambda: window._orientation_pending is None)
    assert selected_names(window) == set()
    (tmp_path / "portrait.png").write_bytes(image_bytes(width=40, height=80))
    window.refresh()
    qtbot.waitUntil(lambda: not window._listing_active)
    window.selection_workers.max_pixels = 1
    window.select_orientation("portrait")
    qtbot.waitUntil(lambda: window._orientation_pending is None)
    assert selected_names(window) == set()
    assert "skipped 1" in window.statusBar().currentMessage()


def test_portrait_selection_can_delete_only_matching_zip_images(qtbot, tmp_path, image_bytes):
    from PySide6.QtWidgets import QMessageBox

    window = open_collection(qtbot, tmp_path, image_bytes, archived=True)
    archive = window.navigation.current.path
    before = archive.read_bytes()
    window.select_orientation("portrait")
    qtbot.waitUntil(lambda: window._orientation_pending is None)
    assert selected_names(window) == {"portrait.png", "rotated.jpg"}
    dialog = window.delete_selected_images()
    assert archive.read_bytes() == before
    assert "2 images" in dialog.text()
    delete = next(
        button
        for button in dialog.buttons()
        if dialog.buttonRole(button) == QMessageBox.ButtonRole.DestructiveRole
    )
    delete.click()
    qtbot.waitUntil(lambda: not window._archive_editing and not window._listing_active)
    with zipfile.ZipFile(archive) as stream:
        assert set(stream.namelist()) == {
            "landscape.png",
            "square.png",
            "bad.png",
            "folder/nested.png",
        }


def test_archive_change_aborts_selection_without_applying_partial_matches(
    qtbot, tmp_path, image_bytes, monkeypatch
):
    window = open_collection(qtbot, tmp_path, image_bytes, archived=True)
    window.watcher.removePaths(window.watcher.files())
    path = window.navigation.current.path
    original = window.selection_workers._orientation
    changed = False

    def change_after_first_match(entry, token):
        nonlocal changed
        shape = original(entry, token)
        if not changed:
            changed = True
            with zipfile.ZipFile(path, "a") as stream:
                stream.writestr("new.png", image_bytes())
        return shape

    monkeypatch.setattr(window.selection_workers, "_orientation", change_after_first_match)
    window.select_orientation("landscape")
    qtbot.waitUntil(lambda: window._orientation_pending is None)
    assert selected_names(window) == set()
    assert "Selection failed" in window.statusBar().currentMessage()
    assert not window.delete_action.isEnabled()
