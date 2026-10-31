"""Noise reduction, edge/alpha preservation, cancellation, and memory limits."""

from threading import Event

import numpy as np
import pytest
from PySide6.QtGui import QImage

from qtvw.services import denoise
from qtvw.services.scheduler import PriorityExecutor
from qtvw.sources.archive import Cancelled


def noisy_image(width=80, height=32):
    rng = np.random.default_rng(47)
    values = np.empty((height, width, 4), dtype=np.uint8)
    base = np.where(np.arange(width) < width // 2, 40, 210)
    values[:, :, :3] = base[None, :, None] + rng.integers(-18, 19, (height, width, 3))
    values[:, :, 3] = np.arange(width, dtype=np.uint8)[None, :]
    return QImage(values.data, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()


def pixels(image):
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    return (
        np.frombuffer(rgba.constBits(), dtype=np.uint8)
        .reshape(image.height(), image.width(), 4)
        .copy()
    )


def test_denoise_reduces_grain_preserves_edges_alpha_and_original():
    source = noisy_image()
    original = source.copy()
    result = denoise.denoise_image(source, Event())
    before, after = pixels(source), pixels(result)
    assert result.size() == source.size()
    assert np.array_equal(after[:, :, 3], before[:, :, 3])
    assert after[:, 4:36, :3].var() < before[:, 4:36, :3].var() / 2
    assert after[:, 40, :3].mean() - after[:, 39, :3].mean() > 140
    assert source == original


def test_tiling_does_not_introduce_seams(monkeypatch):
    source = noisy_image(83, 29)
    whole = denoise.denoise_image(source, Event())
    monkeypatch.setattr(denoise, "TILE_SIZE", 16)
    tiled = denoise.denoise_image(source, Event())
    assert tiled == whole


@pytest.mark.parametrize("width,height", [(1, 1), (1, 5), (5, 1)])
def test_denoise_handles_tiny_images(width, height):
    source = QImage(width, height, QImage.Format.Format_Grayscale8)
    source.fill(123)
    result = denoise.denoise_image(source, Event())
    assert result.size() == source.size()
    assert result.pixelColor(0, 0) == source.pixelColor(0, 0)


def test_cancellation_stops_between_tiles(monkeypatch):
    token = Event()
    source = noisy_image(80, 32)
    original = source.copy()
    filter_image = denoise.cv2.bilateralFilter

    def cancelling(*args, **kwargs):
        result = filter_image(*args, **kwargs)
        token.set()
        return result

    monkeypatch.setattr(denoise, "TILE_SIZE", 16)
    monkeypatch.setattr(denoise.cv2, "bilateralFilter", cancelling)
    with pytest.raises(Cancelled):
        denoise.denoise_image(source, token)
    assert source == original


def test_filter_respects_shared_memory_budget(qtbot):
    scheduler = PriorityExecutor(max_workers=1)
    scheduler.budget.limit = 16
    service = denoise.DenoiseService(scheduler)
    try:
        with qtbot.waitSignal(service.finished) as result:
            assert service.start(noisy_image(), 17)
        request, image, error = result.args
        assert request == 17
        assert image.isNull()
        assert "memory budget" in error
        assert scheduler.budget.used == 0
    finally:
        service.cancel()
        scheduler.shutdown()
