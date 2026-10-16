"""Saved controls, sort order, shortcuts, and cache actions in the real window."""

import os

from PySide6.QtCore import QDir, Qt
from PySide6.QtGui import QColor, QPalette

from qtvw.domain.preferences import Preferences
from qtvw.ui.main_window import MainWindow


def test_preferences_persist_startup_display_and_resource_limits(qtbot, tmp_path, image_bytes):
    start = tmp_path / "start"
    start.mkdir()
    (start / "photo.png").write_bytes(image_bytes())
    (start / ".hidden.png").write_bytes(image_bytes())
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    dialog = window.show_preferences()
    dialog.restore_last.setChecked(False)
    dialog.start_folder.setText(str(start))
    dialog.thumbnail_size.setValue(96)
    dialog.show_hidden.setChecked(True)
    dialog.sort_field.setCurrentIndex(dialog.sort_field.findData("size"))
    dialog.descending.setChecked(True)
    dialog.appearance.setCurrentIndex(dialog.appearance.findData("dark"))
    dialog.cache_mb.setValue(16)
    dialog.decoded_mp.setValue(8)
    dialog.input_mb.setValue(32)
    dialog.entry_mb.setValue(4)
    dialog.workers.setValue(1)
    dialog.accept()
    qtbot.waitUntil(lambda: not window._cache_busy)
    assert window.slider.value() == 96
    assert window.model.sort_field == "size"
    assert window.model.sort_order == Qt.SortOrder.DescendingOrder
    assert window.palette().color(QPalette.ColorRole.Window) == QColor("#242830")
    appearance_dialog = window.show_preferences()
    assert appearance_dialog.palette().color(QPalette.ColorRole.Window) == QColor("#242830")
    appearance_dialog.reject()
    # Resource limits remain stable until restart.
    assert window.workers.max_pixels == 64_000_000
    window.close()
    restarted = MainWindow()
    qtbot.addWidget(restarted)
    restarted.show()
    qtbot.waitUntil(lambda: restarted.model.rowCount() == 2)
    assert restarted.navigation.current == start
    assert restarted.workers.max_pixels == 8_000_000
    assert restarted.workers.max_file_bytes == 32 * 1024 * 1024
    assert restarted.workers.archive_limits.max_entry_bytes == 4 * 1024 * 1024
    assert len(restarted.scheduler._threads) == 1
    assert restarted.cache.disk_limit == 16 * 1024 * 1024
    assert restarted.folders.filter() & QDir.Filter.Hidden
    restarted.close()


def test_cancel_and_invalid_preferences_leave_existing_settings(qtbot, tmp_path):
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    dialog = window.show_preferences()
    dialog.restore_last.setChecked(False)
    dialog.start_folder.setText(str(tmp_path / "missing"))
    dialog.accept()
    assert dialog.isVisible()
    assert "existing" in dialog.error.text()
    dialog.thumbnail_size.setValue(64)
    dialog.reject()
    assert window.preferences.thumbnail_size == 144
    assert Preferences.load(window.settings).thumbnail_size == 144
    window.close()


def test_sorting_controls_drive_viewer_order_and_keep_folder_first(qtbot, tmp_path, image_bytes):
    (tmp_path / "folder").mkdir()
    for name, color, dimensions, mtime in [
        ("photo2.png", "red", (80, 40), 1000),
        ("photo10.png", "blue", (200, 100), 2000),
    ]:
        path = tmp_path / name
        path.write_bytes(image_bytes(color, *dimensions))
        os.utime(path, (mtime, mtime))
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    window.sort_combo.setCurrentIndex(window.sort_combo.findData("modified"))
    window.descending_action.trigger()
    assert [e.name for e in window.model.entries] == ["folder", "photo10.png", "photo2.png"]
    window.open_image(window.model.entries[1])
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    window.step_image(1)
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    assert window.viewed.name == "photo2.png"
    window.show_grid()
    window.sort_combo.setCurrentIndex(window.sort_combo.findData("size"))
    assert window.model.entries[1].name == "photo10.png"
    window.close()


def test_cache_clear_is_async_and_keeps_current_view_available(qtbot, tmp_path, image_bytes):
    (tmp_path / "photo.png").write_bytes(image_bytes())
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: bool(window.model.pixmaps))
    qtbot.waitUntil(lambda: not window.requested)
    directory = window.cache.root / "thumbnails"
    assert list(directory.glob("*.png"))
    old_epoch = window.cache.epoch
    dialog = window.show_preferences()
    dialog.clear_button.click()
    assert window._cache_busy
    assert not dialog.clear_button.isEnabled()
    qtbot.waitUntil(lambda: not window._cache_busy)
    assert window.cache.epoch > old_epoch
    assert not list(directory.glob("*.png"))
    assert window.model.pixmaps
    assert "cleared" in dialog.cache_status.text()
    assert dialog.clear_button.isEnabled()
    dialog.reject()
    window.refresh()
    qtbot.waitUntil(lambda: bool(list(directory.glob("*.png"))))
    window.close()


def test_fullscreen_escape_and_preferences_keyboard_action(qtbot, tmp_path, image_bytes):
    (tmp_path / "photo.png").write_bytes(image_bytes())
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.model.rowCount() == 1)
    window.browser.setCurrentIndex(window.model.index(0))
    qtbot.keyClick(window.browser, Qt.Key.Key_Return)
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    qtbot.keyClick(window.viewer, Qt.Key.Key_F11)
    assert window.isFullScreen()
    qtbot.keyClick(window.viewer, Qt.Key.Key_Escape)
    assert not window.isFullScreen()
    assert window.stack.currentWidget() == window.viewer
    qtbot.keyClick(window.viewer, Qt.Key.Key_Escape)
    assert window.stack.currentWidget() == window.browser
    qtbot.keyClick(window.browser, Qt.Key.Key_Comma, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(
        lambda: window._preferences_dialog is not None and window._preferences_dialog.isVisible()
    )
    window._preferences_dialog.reject()
    window.close()
