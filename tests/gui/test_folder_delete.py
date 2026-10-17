"""Confirmed recursive deletion from the filesystem sidebar."""

import pytest
from PySide6.QtWidgets import QMessageBox

from qtvw.services.folder_delete import delete_folder, folder_identity
from qtvw.ui.main_window import MainWindow


def test_sidebar_delete_cancel_then_delete_current_folder(qtbot, tmp_path, image_bytes):
    folder = tmp_path / "album"
    nested = folder / "nested"
    nested.mkdir(parents=True)
    (nested / "photo.png").write_bytes(image_bytes())
    outside = tmp_path / "keep.txt"
    outside.write_text("keep")
    (folder / "link").symlink_to(outside)
    window = MainWindow(nested)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    dialog = window.delete_sidebar_folder(folder)
    assert str(folder) in dialog.informativeText()
    assert "all its contents" in dialog.text()
    assert dialog.defaultButton() == dialog.button(QMessageBox.StandardButton.Cancel)
    dialog.button(QMessageBox.StandardButton.Cancel).click()
    assert nested.exists()
    dialog = window.delete_sidebar_folder(folder)
    next(b for b in dialog.buttons() if b.text() == "Delete folder").click()
    qtbot.waitUntil(lambda: not window._folder_deleting)
    qtbot.waitUntil(lambda: not window._listing_active)
    assert not folder.exists()
    assert outside.read_text() == "keep"
    assert window.navigation.current == tmp_path
    window.close()


def test_folder_delete_rejects_symlinks_root_and_replacement(tmp_path):
    folder = tmp_path / "album"
    folder.mkdir()
    link = tmp_path / "link"
    link.symlink_to(folder, target_is_directory=True)
    with pytest.raises(ValueError):
        folder_identity(link)
    with pytest.raises(ValueError):
        folder_identity(tmp_path / tmp_path.anchor)
    identity = folder_identity(folder)
    folder.rename(tmp_path / "original")
    folder.mkdir()
    with pytest.raises(ValueError, match="changed"):
        delete_folder(folder, identity)
    assert folder.exists()
