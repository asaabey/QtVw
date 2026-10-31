"""Cancellable display-only bilateral filtering on the shared image scheduler."""

from threading import Event

import cv2
import numpy as np
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage

from qtvw.services.scheduler import OPEN_IMAGE, QueueFull
from qtvw.sources.archive import Cancelled, check_cancel

# The application scheduler owns concurrency, including OpenCV's CPU work.
cv2.setNumThreads(1)
TILE_SIZE = 512
RADIUS = 3


def denoise_memory_bytes(image):
    workspace = min(image.width(), TILE_SIZE + 2 * RADIUS) * min(
        image.height(), TILE_SIZE + 2 * RADIUS
    )
    return image.width() * image.height() * 4 + workspace * 32


def denoise_image(image: QImage, token: Event) -> QImage:
    """Filter overlapping tiles to bound workspace and cancellation latency."""
    check_cancel(token)
    if image.isNull():
        raise ValueError("Open an image before applying denoise")
    width, height = image.width(), image.height()
    output = QImage(width, height, QImage.Format.Format_RGBA8888)
    if output.isNull():
        raise MemoryError("Not enough memory to denoise this image")
    output.setColorSpace(image.colorSpace())
    output.setDevicePixelRatio(image.devicePixelRatio())
    target = np.frombuffer(output.bits(), dtype=np.uint8).reshape(height, width, 4)
    for y in range(0, height, TILE_SIZE):
        for x in range(0, width, TILE_SIZE):
            check_cancel(token)
            x0, y0 = max(0, x - RADIUS), max(0, y - RADIUS)
            x1 = min(width, x + TILE_SIZE + RADIUS)
            y1 = min(height, y + TILE_SIZE + RADIUS)
            tile = image.copy(x0, y0, x1 - x0, y1 - y0).convertToFormat(
                QImage.Format.Format_RGBA8888
            )
            if tile.isNull():
                raise MemoryError("Not enough memory to denoise this image")
            pixels = np.frombuffer(tile.constBits(), dtype=np.uint8).reshape(y1 - y0, x1 - x0, 4)
            filtered = cv2.bilateralFilter(
                np.ascontiguousarray(pixels[:, :, :3]), 2 * RADIUS + 1, 35, 3
            )
            rows, columns = min(TILE_SIZE, height - y), min(TILE_SIZE, width - x)
            crop = np.s_[y - y0 : y - y0 + rows, x - x0 : x - x0 + columns]
            target[y : y + rows, x : x + columns, :3] = filtered[crop]
            target[y : y + rows, x : x + columns, 3] = pixels[crop][:, :, 3]
    check_cancel(token)
    return output


class DenoiseService(QObject):
    finished = Signal(int, QImage, str)

    def __init__(self, scheduler, parent=None):
        super().__init__(parent)
        self.scheduler = scheduler
        self._token = Event()
        self._future = None

    def cancel(self):
        self._token.set()
        if self._future is not None:
            self._future.cancel()
            self._future = None
        self._token = Event()

    def start(self, image, request):
        self.cancel()
        token = self._token
        source = QImage(image)

        def operation():
            result, error = QImage(), ""
            try:
                with self.scheduler.budget.reserve(denoise_memory_bytes(source), token):
                    result = denoise_image(source, token)
            except Cancelled:
                return
            except Exception as exc:
                error = str(exc) or "Unable to denoise image"
            if not token.is_set():
                self.finished.emit(request, result, error)

        try:
            self._future = self.scheduler.submit(operation, priority=OPEN_IMAGE)
        except QueueFull:
            return False
        return True
