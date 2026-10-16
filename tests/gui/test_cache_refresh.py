"""Changed archives refresh visible covers and the currently opened image."""

import zipfile

from PySide6.QtGui import QColor

from qtvw.domain.locations import ArchiveLocation
from qtvw.ui.main_window import MainWindow


def write_archive(path, data):
    temporary = path.with_suffix(".tmp")
    with zipfile.ZipFile(temporary, "w") as archive:
        archive.writestr("photo.png", data)
    temporary.replace(path)


def test_changed_archive_cover_and_viewer_refresh_automatically(qtbot, tmp_path, image_bytes):
    path = tmp_path / "photos.zip"
    write_archive(path, image_bytes("red"))
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()

    def cover_is(color):
        pixmap = window.model.pixmaps.get(str(path))
        return pixmap is not None and pixmap.toImage().pixelColor(0, 0) == QColor(color)

    qtbot.waitUntil(lambda: cover_is("red"))
    original_generation = window.generation
    write_archive(path, image_bytes("green"))
    qtbot.waitUntil(lambda: cover_is("green"), timeout=5000)
    assert window.generation > original_generation
    window.navigate(ArchiveLocation(path))
    qtbot.waitUntil(lambda: window.model.rowCount() == 1)
    window.open_image(window.model.entries[0])
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    assert window.viewer.item.pixmap().toImage().pixelColor(0, 0) == QColor("green")
    write_archive(path, image_bytes("blue"))
    qtbot.waitUntil(
        lambda: (
            window.viewer.item is not None
            and window.viewer.item.pixmap().toImage().pixelColor(0, 0) == QColor("blue")
        ),
        timeout=5000,
    )
    assert window.stack.currentWidget() == window.viewer
    window.close()


def test_adjacent_preload_reuses_full_image_memory(qtbot, tmp_path, image_bytes):
    for name, color in [("photo1.png", "red"), ("photo2.png", "green")]:
        (tmp_path / name).write_bytes(image_bytes(color))
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.model.rowCount() == 2)
    decoded = []
    original = window.image_workers._decode_source

    def source(entry, size, *args):
        decoded.append((entry.name, size))
        return original(entry, size, *args)

    window.image_workers._decode_source = source
    window.open_image(window.model.entries[0])
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    qtbot.waitUntil(lambda: len(window.cache.images.items) == 2)
    window.step_image(1)
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    assert window.viewer.item.pixmap().toImage().pixelColor(0, 0) == QColor("green")
    assert decoded.count(("photo2.png", 0)) == 1
    window.close()
