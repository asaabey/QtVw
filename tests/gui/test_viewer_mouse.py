"""Mouse navigation shares collection order and preserves drag panning."""

import zipfile

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QImage

from qtvw.domain.locations import ArchiveLocation
from qtvw.ui.main_window import MainWindow
from qtvw.ui.viewer import ImageViewer


@pytest.mark.parametrize("archive", [False, True])
def test_mouse_collection_navigation(qtbot, tmp_path, image_bytes, archive):
    location = tmp_path
    if archive:
        path = tmp_path / "photos.zip"
        with zipfile.ZipFile(path, "w") as stream:
            for name in ("photo2.png", "photo10.png"):
                stream.writestr(name, image_bytes())
        location = ArchiveLocation(path)
    else:
        for name in ("photo2.png", "photo10.png"):
            (tmp_path / name).write_bytes(image_bytes())
    window = MainWindow(location, software_rendering=True)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.model.rowCount() == 2)
    window.browser.setCurrentIndex(window.model.index(0))
    scroll = window.browser.verticalScrollBar().value()
    window._open_index(window.model.index(0))
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    viewport = window.viewer.viewport()

    def click(right):
        qtbot.mouseClick(
            viewport,
            Qt.MouseButton.LeftButton,
            pos=QPoint(viewport.width() - 5 if right else 5, viewport.height() - 5),
        )

    click(False)  # No wrap at the first image.
    assert window.viewed.name == "photo2.png"
    click(True)
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    assert window.viewed.name == "photo10.png"
    click(True)  # No wrap at the last image.
    assert window.viewed.name == "photo10.png"
    click(False)
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    assert window.viewed.name == "photo2.png"
    qtbot.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    assert window.stack.currentWidget() == window.viewer
    qtbot.mouseDClick(viewport, Qt.MouseButton.LeftButton, pos=QPoint(5, 5))
    assert window.stack.currentWidget() == window.browser
    assert window.browser.currentIndex().row() == 0
    assert window.browser.verticalScrollBar().value() == scroll
    window.close()


def test_drag_pan_and_resized_zones(qtbot):
    viewer = ImageViewer(rendering_mode="software")
    qtbot.addWidget(viewer)
    viewer.resize(500, 400)
    viewer.show()
    image = QImage(2000, 2000, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.red)
    viewer.set_image(image)
    viewer.actual_size()
    steps = []
    viewer.navigate_requested.connect(steps.append)
    viewport = viewer.viewport()
    start = QPoint(viewport.width() * 3 // 4, viewport.height() * 3 // 4)
    before = viewer.horizontalScrollBar().value()
    qtbot.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
    qtbot.mouseMove(viewport, start - QPoint(80, 0))
    qtbot.mouseMove(viewport, start)  # Returning to the origin is still a drag.
    qtbot.mouseMove(viewport, start - QPoint(80, 0))
    qtbot.mouseRelease(viewport, Qt.MouseButton.LeftButton, pos=start - QPoint(80, 0))
    assert steps == []
    assert viewer.horizontalScrollBar().value() != before
    viewer.fit()
    viewer.resize(700, 500)
    qtbot.waitUntil(lambda: viewport.width() > 600)
    mid = QPoint(viewport.width() // 2, viewport.height() // 2)
    qtbot.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=mid + QPoint(1, 1))
    qtbot.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=mid + QPoint(-1, 1))
    qtbot.mouseClick(viewport, Qt.MouseButton.LeftButton, pos=mid - QPoint(0, 1))
    qtbot.mouseClick(viewport, Qt.MouseButton.RightButton, pos=mid + QPoint(1, 1))
    assert steps == [1, -1]
