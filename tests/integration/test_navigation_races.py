"""Regression checks for work completing after navigation or UI changes."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from qtvw.domain import Entry
from qtvw.ui.main_window import MainWindow


def test_stale_results_and_thumbnail_resize(qtbot, tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    first.mkdir()
    second.mkdir()
    for i in range(300):
        (first / f"image{i}.png").touch()
    window = MainWindow(first)
    qtbot.addWidget(window)
    window.show()
    old_generation = window.generation
    window.slider.setValue(100)
    qtbot.waitUntil(lambda: window.model.rowCount() == 300)
    window.navigate(second)
    with qtbot.waitSignal(window.workers.listing_finished):
        window.refresh()
    assert window.model.rowCount() == 0
    window._listed(old_generation, [Entry(first / "stale.png", "image")])
    window._thumbnail(old_generation, str(first / "stale.png"), QImage(), "failed")
    window._listing_finished(old_generation, "obsolete error")
    assert window.model.rowCount() == 0
    assert not window.model.failed
    assert "obsolete" not in window.statusBar().currentMessage()
    window.close()


def test_location_editing_and_viewer_shortcuts(qtbot, tmp_path):
    image = QImage(80, 40, QImage.Format.Format_RGB32)
    image.fill(0xFF336699)
    assert image.save(str(tmp_path / "photo.png"))
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.model.rowCount() == 1)
    window.location.setFocus()
    window.location.selectAll()
    qtbot.keyClicks(window.location, "photo10-+")
    qtbot.keyClick(window.location, Qt.Key.Key_Backspace)
    assert window.location.text() == "photo10-"
    window.location.setText(str(tmp_path))
    window.open_image(window.model.entries[0])
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    qtbot.keyClick(window.viewer, Qt.Key.Key_1)
    assert window.viewer.transform().m11() == 1
    qtbot.keyClick(window.viewer, Qt.Key.Key_Escape)
    assert window.stack.currentWidget() == window.browser
    window.close()
