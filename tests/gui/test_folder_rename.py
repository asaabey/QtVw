"""Folder rename shortcuts, prompts, navigation recovery, and background work."""

import zipfile
from threading import Event

import pytest
from PySide6.QtCore import QItemSelectionModel, Qt
from PySide6.QtWidgets import QLineEdit

from qtvw.domain.locations import ArchiveLocation
from qtvw.ui import main_window
from qtvw.ui.main_window import MainWindow


@pytest.mark.parametrize("target", ["sidebar", "grid"])
def test_f2_renames_selected_folder_and_keeps_contents(qtbot, tmp_path, target):
    source = tmp_path / "album"
    source.mkdir()
    (source / "keep.txt").write_text("keep")
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    if target == "sidebar":
        window.tree.setRootIndex(window.folders.index(str(tmp_path)))
        window.tree.setCurrentIndex(window.folders.index(str(source)))
        view = window.tree
    else:
        window.browser.setCurrentIndex(window.model.index(0))
        view = window.browser
    window.activateWindow()
    view.setFocus()
    qtbot.waitUntil(lambda: window.isActiveWindow() and view.hasFocus())
    qtbot.keyClick(view, Qt.Key.Key_F2)
    qtbot.waitUntil(lambda: window._folder_rename_dialog is not None)
    dialog = window._folder_rename_dialog
    assert str(source) in dialog.labelText()
    assert dialog.textValue() == source.name
    assert dialog.findChild(QLineEdit).selectedText() == source.name
    dialog.setTextValue("旅行 2026")
    dialog.accept()
    qtbot.waitUntil(lambda: not window._folder_renaming and not window._listing_active)
    destination = tmp_path / "旅行 2026"
    assert not source.exists()
    assert (destination / "keep.txt").read_text() == "keep"
    assert [entry.path for entry in window.model.entries] == [destination]
    assert window.navigation.current == tmp_path
    window.close()


def test_context_menu_renames_clicked_folder_and_cancel_keeps_original(qtbot, tmp_path):
    source = tmp_path / "album"
    source.mkdir()
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    window.tree.setCurrentIndex(window.folders.index(str(tmp_path)))
    window.tree.setRootIndex(window.folders.index(str(tmp_path)))
    index = window.folders.index(str(source))
    qtbot.waitUntil(lambda: not window.tree.visualRect(index).isEmpty())
    window._folder_context_menu(window.tree.visualRect(index).center())
    action = next(a for a in window._folder_menu.actions() if a.text() == "Rename folder…")
    assert action.isEnabled()
    window._folder_menu.close()
    action.trigger()
    dialog = window._folder_rename_dialog
    assert dialog.textValue() == source.name
    dialog.setTextValue("cancelled")
    dialog.reject()
    assert source.is_dir()
    assert not (tmp_path / "cancelled").exists()
    window._browser_context_menu(window.browser.visualRect(window.model.index(0)).center())
    action = window._browser_menu.actions()[0]
    assert action.text() == "Rename folder…"
    window._browser_menu.close()
    action.trigger()
    window._folder_rename_dialog.setTextValue("renamed")
    window._folder_rename_dialog.accept()
    qtbot.waitUntil(lambda: not window._folder_renaming and not window._listing_active)
    assert (tmp_path / "renamed").is_dir()
    window.close()


def test_rename_parent_restores_zip_viewer_and_relocates_history(qtbot, tmp_path, image_bytes):
    source = tmp_path / "album"
    source.mkdir()
    archive = source / "photos.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("nested/photo.png", image_bytes())
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    window.navigate(source)
    qtbot.waitUntil(lambda: not window._listing_active)
    window.navigate(ArchiveLocation(archive, "nested"))
    qtbot.waitUntil(lambda: not window._listing_active)
    window.browser.setCurrentIndex(window.model.index(0))
    window._open_index(window.model.index(0))
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    dialog = window.rename_local_folder(source)
    dialog.setTextValue("renamed")
    dialog.accept()
    qtbot.waitUntil(lambda: not window._folder_renaming and not window._listing_active)
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    renamed = tmp_path / "renamed"
    assert window.navigation.current == ArchiveLocation(renamed / "photos.zip", "nested")
    assert window.viewed.path == renamed / "photos.zip"
    assert str(renamed) in window.location.text()
    window.back()
    qtbot.waitUntil(lambda: not window._listing_active)
    assert window.navigation.current == renamed
    window.back()
    qtbot.waitUntil(lambda: not window._listing_active)
    assert window.navigation.current == tmp_path
    window.forward()
    qtbot.waitUntil(lambda: not window._listing_active)
    assert window.navigation.current == renamed
    window.forward()
    qtbot.waitUntil(lambda: not window._listing_active)
    assert window.navigation.current == ArchiveLocation(renamed / "photos.zip", "nested")
    window.close()


def test_invalid_selection_and_conflict_are_reported(qtbot, tmp_path):
    source = tmp_path / "album"
    source.mkdir()
    (tmp_path / "existing").mkdir()
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    window.browser.setCurrentIndex(window.model.index(0))
    window.browser.selectionModel().select(
        window.model.index(1), QItemSelectionModel.SelectionFlag.Select
    )
    window.browser.setFocus()
    window.rename_selected_folder()
    assert window._folder_rename_dialog is None
    assert "Select one" in window.statusBar().currentMessage()
    for name in ("../escape", "existing"):
        dialog = window.rename_local_folder(source)
        dialog.setTextValue(name)
        dialog.accept()
        qtbot.waitUntil(lambda: not window._folder_renaming and not window._listing_active)
        assert window._folder_rename_error.isVisible()
        window._folder_rename_error.accept()
        assert source.is_dir()
        assert not (tmp_path.parent / "escape").exists()
    assert window.rename_local_folder(tmp_path / "missing") is None
    window.close()


def test_close_waits_for_background_rename(qtbot, tmp_path, monkeypatch):
    source = tmp_path / "album"
    source.mkdir()
    entered, release = Event(), Event()
    rename = main_window.rename_folder

    def controlled(path, name, identity):
        entered.set()
        release.wait(5)
        return rename(path, name, identity)

    monkeypatch.setattr(main_window, "rename_folder", controlled)
    window = MainWindow(source)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    dialog = window.rename_local_folder(source)
    dialog.setTextValue("renamed")
    dialog.accept()
    try:
        qtbot.waitUntil(entered.is_set)
        window.close()
        assert window.isVisible()
        assert not window._closing
        assert window._close_after_folder_rename
    finally:
        release.set()
    qtbot.waitUntil(lambda: not window.isVisible())
    assert (tmp_path / "renamed").is_dir()
    assert window.navigation.current == tmp_path / "renamed"
