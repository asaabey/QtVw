"""Browse archives and their folders through the same grid and image viewer."""

import zipfile

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor

from qtvw.domain.locations import ArchiveLocation
from qtvw.ui.main_window import MainWindow


def wait_listing(qtbot, window, action):
    with qtbot.waitSignal(window.workers.listing_finished) as result:
        action()
    assert not result.args[1]


def test_zip_cover_nested_folders_viewer_and_history(qtbot, tmp_path, image_bytes):
    archive = tmp_path / "Holiday.zip"
    with zipfile.ZipFile(archive, "w") as stream:
        stream.writestr("Day 1/photo10.png", image_bytes("green"))
        stream.writestr("Day 1/photo2.png", image_bytes("red"))
        stream.writestr("cover.png", image_bytes("blue"))
        stream.writestr("nested.zip", b"unsupported")
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: window.model.rowCount() == 1)
    qtbot.waitUntil(lambda: str(archive) in window.model.pixmaps)
    # Covers have both the representative image and a visible ZIP badge.
    cover = window.model.pixmaps[str(archive)].toImage()
    assert window.browser.visualRect(window.model.index(0)).width() >= cover.width()
    assert cover.pixelColor(0, 0) == QColor("blue")
    assert cover.pixelColor(cover.width() - 1, cover.height() - 1) != QColor("blue")
    wait_listing(qtbot, window, lambda: window._open_index(window.model.index(0)))
    assert window.navigation.current == ArchiveLocation(archive)
    assert [e.name for e in window.model.entries] == ["Day 1", "cover.png", "nested.zip"]
    window._open_index(window.model.index(2))
    assert "Nested ZIP" in window.statusBar().currentMessage()
    assert window.navigation.current == ArchiveLocation(archive)
    wait_listing(qtbot, window, lambda: window._open_index(window.model.index(0)))
    assert window.location.text().endswith("Holiday.zip › Day 1")
    assert [e.name for e in window.model.entries] == ["photo2.png", "photo10.png"]
    window.browser.setCurrentIndex(window.model.index(0))
    window._open_index(window.model.index(0))
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    assert window.viewer.item.pixmap().toImage().pixelColor(0, 0) == QColor("red")
    window.step_image(1)
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    assert window.viewer.item.pixmap().toImage().pixelColor(0, 0) == QColor("green")
    window.show_grid()
    assert window.browser.currentIndex().row() == 0
    wait_listing(qtbot, window, window.up)
    assert window.navigation.current == ArchiveLocation(archive)
    wait_listing(qtbot, window, window.up)
    assert window.navigation.current == tmp_path
    wait_listing(qtbot, window, window.back)
    assert window.navigation.current == ArchiveLocation(archive)
    wait_listing(qtbot, window, window.forward)
    assert window.navigation.current == tmp_path
    window.close()
    # Browsing and decoding must leave the filesystem unchanged.
    assert list(tmp_path.glob("*.png")) == []


def test_corrupt_archive_recovery_address_and_saved_location(qtbot, tmp_path, image_bytes):
    path = tmp_path / "bad.zip"
    path.write_bytes(b"not a zip")
    good = tmp_path / "good.zip"
    with zipfile.ZipFile(good, "w") as archive:
        archive.writestr("旅行/photo.png", image_bytes())
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    with qtbot.waitSignal(window.workers.listing_finished) as result:
        window.navigate(ArchiveLocation(path))
    assert "ZIP" in result.args[1]
    assert window.model.rowCount() == 0
    window.location.setText(f"{good} › 旅行")
    wait_listing(qtbot, window, window.open_location)
    assert window.model.entries[0].name == "photo.png"
    with qtbot.waitSignal(window.workers.listing_finished):
        window.slider.setValue(100)
    assert window.navigation.current == ArchiveLocation(good, "旅行")
    assert window.model.rowCount() == 1
    window.close()
    restored = MainWindow()
    qtbot.addWidget(restored)
    restored.show()
    qtbot.waitUntil(lambda: restored.model.rowCount() == 1)
    assert restored.navigation.current == ArchiveLocation(good, "旅行")
    # Invalid internal locations produce a status message without a traceback.
    restored.location.setText(f"{good} › ../escape")
    qtbot.keyClick(restored.location, Qt.Key.Key_Return)
    assert "Unsafe" in restored.statusBar().currentMessage()
    restored.close()


def test_archive_without_decodable_cover_uses_placeholder(qtbot, tmp_path):
    from PySide6.QtGui import QPixmap

    path = tmp_path / "empty.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("cover.png", b"corrupt image")
        archive.writestr("notes.txt", b"not an image")
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: str(path) in window.model.failed)
    assert str(path) not in window.model.pixmaps
    placeholder = window.model.data(window.model.index(0), Qt.ItemDataRole.DecorationRole)
    assert isinstance(placeholder, QPixmap)
    assert not placeholder.isNull()
    assert window.model.errors[str(path)]
    wait_listing(qtbot, window, lambda: window._open_index(window.model.index(0)))
    assert window.navigation.current == ArchiveLocation(path)
    assert window.model.entries[0].name == "cover.png"
    window._open_index(window.model.index(0))
    qtbot.waitUntil(lambda: "Loading" not in window.statusBar().currentMessage())
    assert window.viewer.item is None
    window.show_grid()
    wait_listing(qtbot, window, window.up)
    assert window.navigation.current == tmp_path
    window.close()
