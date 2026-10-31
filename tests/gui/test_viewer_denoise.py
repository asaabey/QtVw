"""Display-only denoising defaults, controls, original restoration, and races."""

import zipfile
from threading import Event

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from qtvw.domain.locations import ArchiveLocation
from qtvw.services import denoise
from qtvw.ui.main_window import MainWindow


def source_image(tmp_path):
    values = np.random.default_rng(13).integers(105, 150, (96, 128, 4), dtype=np.uint8)
    values[:, :, 3] = 255
    image = QImage(values.data, 128, 96, 128 * 4, QImage.Format.Format_RGBA8888).copy()
    path = tmp_path / "photo1.png"
    assert image.save(str(path))
    return path


def displayed_image(window):
    return window.viewer.item.pixmap().toImage().convertToFormat(QImage.Format.Format_RGBA8888)


@pytest.mark.parametrize("archive", [False, True])
def test_denoise_toggle_restores_original_and_keeps_zoom_pan(qtbot, tmp_path, image_bytes, archive):
    source = source_image(tmp_path)
    if archive:
        path = tmp_path / "photos.zip"
        with zipfile.ZipFile(path, "w") as stream:
            stream.writestr("photo1.png", source.read_bytes())
            stream.writestr("photo2.png", image_bytes("blue"))
        start = ArchiveLocation(path)
    else:
        path = source
        (tmp_path / "photo2.png").write_bytes(image_bytes("blue"))
        start = tmp_path
    original_bytes = path.read_bytes()
    window = MainWindow(start)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    assert not window.denoise_action.isEnabled()
    assert not window.denoise_action.isChecked()
    first = next(entry for entry in window.model.entries if entry.name == "photo1.png")
    window.open_image(first)
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    original = displayed_image(window)
    assert window.denoise_action.isEnabled()
    window.viewer.actual_size()
    window.viewer.zoom(8)
    window.viewer.centerOn(80, 60)
    transform = window.viewer.transform()
    scroll = window.viewer.horizontalScrollBar().value(), window.viewer.verticalScrollBar().value()
    window.activateWindow()
    window.viewer.setFocus()
    qtbot.waitUntil(lambda: window.isActiveWindow())
    qtbot.keyClick(window.viewer, Qt.Key.Key_D)
    qtbot.waitUntil(lambda: "Denoise on" in window.statusBar().currentMessage())
    assert window.denoise_action.isChecked()
    assert displayed_image(window) != original
    assert window.viewer.transform() == transform
    assert (
        window.viewer.horizontalScrollBar().value(),
        window.viewer.verticalScrollBar().value(),
    ) == scroll
    qtbot.keyClick(window.viewer, Qt.Key.Key_D)
    assert not window.denoise_action.isChecked()
    assert displayed_image(window) == original
    assert window.viewer.transform() == transform
    window.denoise_action.trigger()
    qtbot.waitUntil(lambda: "Denoise on" in window.statusBar().currentMessage())
    window.step_image(1)
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    assert not window.denoise_action.isChecked()
    window.open_image(first)
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    assert displayed_image(window) == original
    window.show_grid()
    assert not window.denoise_action.isEnabled()
    assert not window.denoise_action.isChecked()
    assert path.read_bytes() == original_bytes
    window.close()
    restored = MainWindow(start)
    qtbot.addWidget(restored)
    assert not restored.denoise_action.isChecked()
    assert not restored.denoise_action.isEnabled()
    restored.close()


@pytest.mark.parametrize("next_action", ["off", "next_image", "grid"])
def test_late_filter_results_are_discarded(qtbot, tmp_path, image_bytes, monkeypatch, next_action):
    source_image(tmp_path)
    (tmp_path / "photo2.png").write_bytes(image_bytes("blue"))
    entered, release = Event(), Event()

    def controlled(image, token):
        entered.set()
        release.wait(5)
        assert token.is_set()
        denoise.check_cancel(token)

    monkeypatch.setattr(denoise, "denoise_image", controlled)
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    window.open_image(window.model.entries[0])
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    original = displayed_image(window)
    window.denoise_action.trigger()
    request = window._denoise_generation
    try:
        qtbot.waitUntil(entered.is_set)
        if next_action == "off":
            window.denoise_action.trigger()
        elif next_action == "next_image":
            window.step_image(1)
            qtbot.waitUntil(lambda: window.viewer.item is not None)
            original = displayed_image(window)
        else:
            window.show_grid()
        obsolete = QImage(128, 96, QImage.Format.Format_RGB32)
        obsolete.fill(Qt.GlobalColor.red)
        window.denoiser.finished.emit(request, obsolete, "")
        assert not window.denoise_action.isChecked()
        if next_action == "grid":
            assert window.viewer.item is None
        else:
            assert displayed_image(window) == original
    finally:
        release.set()
        window.close()


def test_filter_failure_and_shortcut_scope(qtbot, tmp_path, monkeypatch):
    source_image(tmp_path)

    def failed(image, token):
        raise ValueError("Unable to process image")

    monkeypatch.setattr(denoise, "denoise_image", failed)
    window = MainWindow(tmp_path)
    qtbot.addWidget(window)
    window.show()
    qtbot.waitUntil(lambda: not window._listing_active)
    window.open_image(window.model.entries[0])
    qtbot.waitUntil(lambda: window.viewer.item is not None)
    original = displayed_image(window)
    window.location.setFocus()
    window.location.selectAll()
    qtbot.keyClick(window.location, Qt.Key.Key_D)
    assert window.location.text() == "d"
    assert not window.denoise_action.isChecked()
    window.denoise_action.trigger()
    qtbot.waitUntil(lambda: "Denoise failed" in window.statusBar().currentMessage())
    assert not window.denoise_action.isChecked()
    assert displayed_image(window) == original
    assert window.denoise_action.isEnabled()
    window.close()
