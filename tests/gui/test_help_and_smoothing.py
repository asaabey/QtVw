"""Help uses live key bindings, while smoothing changes actual displayed pixels."""

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QKeySequence, QPainter
from PySide6.QtWidgets import QApplication

from qtvw.ui.main_window import MainWindow
from qtvw.ui.viewer import ImageViewer


def test_help_shortcuts_log_folder_and_viewer_backspace(qtbot, tmp_path, image_bytes):
    (tmp_path / "photo.png").write_bytes(image_bytes())
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.model.rowCount() == 1)
    # Verify help is derived from the QAction, rather than a second shortcut list.
    window.back_action.setShortcut(QKeySequence("F2"))
    qtbot.keyClick(window.browser, Qt.Key.Key_F1)
    qtbot.waitUntil(lambda: window._help_dialog is not None and window._help_dialog.isVisible())
    help_dialog = window._help_dialog
    text = help_dialog.content.toPlainText()
    for expected in (
        "F2",
        "F1",
        "Enter / Return",
        "Previous image",
        "Next image",
        "Image viewer",
        "Smooth image scaling",
    ):
        assert expected in text
    help_dialog.copy_logs.click()
    assert QApplication.clipboard().text() in help_dialog.logs.text()
    help_dialog.reject()
    window.open_image(window.model.entries[0])
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    window.activateWindow()
    window.viewer.setFocus()
    qtbot.waitUntil(lambda: window.isActiveWindow())
    qtbot.keyClick(window.viewer, Qt.Key.Key_Backspace)
    assert window.stack.currentWidget() == window.browser
    window.close()


def test_smooth_scaling_changes_pixels_without_modifying_source(qtbot):
    viewer = ImageViewer(rendering_mode="software")
    qtbot.addWidget(viewer)
    source = QImage(2, 1, QImage.Format.Format_RGB32)
    source.setPixelColor(0, 0, QColor("black"))
    source.setPixelColor(1, 0, QColor("white"))
    viewer.set_image(source)
    viewer.actual_size()
    viewer.zoom(2)

    def pixels():
        result = QImage(20, 10, QImage.Format.Format_RGB32)
        result.fill(QColor("red"))
        painter = QPainter(result)
        viewer.scene().render(
            painter, QRectF(0, 0, 20, 10), QRectF(0, 0, 2, 1), Qt.AspectRatioMode.IgnoreAspectRatio
        )
        painter.end()
        return {result.pixelColor(x, 5).red() for x in range(20)}

    assert viewer.item.transformationMode() == Qt.TransformationMode.SmoothTransformation
    assert any(0 < value < 255 for value in pixels())
    viewer.set_smooth_scaling(False)
    assert pixels() == {0, 255}
    assert viewer.transform().m11() == 2
    assert viewer.item.pixmap().toImage() == source
    viewer.set_image(source)
    assert viewer.item.transformationMode() == Qt.TransformationMode.FastTransformation


def test_smoothing_preference_applies_and_survives_restart(qtbot, tmp_path, image_bytes):
    (tmp_path / "photo.png").write_bytes(image_bytes())
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    dialog = window.show_preferences()
    assert dialog.smooth_scaling.isChecked()
    dialog.smooth_scaling.setChecked(False)
    dialog.accept()
    qtbot.waitUntil(lambda: not window._cache_busy)
    assert not window.viewer.smooth_scaling
    window.close()
    restored = MainWindow(tmp_path)
    qtbot.addWidget(restored)
    assert not restored.preferences.smooth_scaling
    assert not restored.viewer.smooth_scaling
    restored.close()
