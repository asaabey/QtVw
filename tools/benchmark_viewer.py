"""Compare steady-state viewer pan/zoom rendering on the local native display."""

import argparse
import json
import math
import statistics
import time

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QTimer
from PySide6.QtGui import QColor, QImage, QLinearGradient, QPainter
from PySide6.QtWidgets import QApplication

from qtvw.ui.viewer import ImageViewer


def wait_for(predicate):
    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(10)
    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(loop.quit)
    poll.timeout.connect(lambda: loop.quit() if predicate() else None)
    poll.start()
    timeout.start(5000)
    loop.exec()
    poll.stop()
    timeout.stop()
    return predicate()


def measure(app, image, mode, frames):
    viewer = ImageViewer(rendering_mode=mode)
    viewer.resize(1200, 800)
    viewer.set_image(image)
    viewer.show()
    samples = []
    try:
        ready = wait_for(
            lambda: (
                viewer.windowHandle().isExposed()
                and (viewer.rendering_backend == "software" or viewer._gl_ready)
            )
        )
        if not ready:
            return {"mode": mode, "error": "Viewer did not become ready"}
        if mode == "auto" and viewer.rendering_backend != "opengl":
            return {"mode": mode, "unavailable": viewer.rendering_description}
        viewer.actual_size()
        renderer = None
        if viewer.rendering_backend == "opengl":
            viewport = viewer.viewport()
            viewport.makeCurrent()
            renderer = viewport.context().functions().glGetString(0x1F01)  # GL_RENDERER
            viewport.doneCurrent()
        cpu_start = None
        for frame in range(frames + 20):
            if frame == 20:
                cpu_start = time.process_time()
            started = time.perf_counter()
            viewer.zoom(1.003 if frame % 40 < 20 else 1 / 1.003)
            viewer.centerOn(
                image.width() / 2 + math.sin(frame / 10) * 500,
                image.height() / 2 + math.cos(frame / 10) * 300,
            )
            viewer.viewport().repaint()
            app.processEvents()
            if viewer.rendering_backend == "opengl":
                viewport.makeCurrent()
                viewport.context().functions().glFinish()
                viewport.doneCurrent()
            if frame >= 20:
                samples.append((time.perf_counter() - started) * 1000)
        return {
            "mode": mode,
            "backend": viewer.rendering_backend,
            "renderer": renderer,
            "frames": frames,
            "image_pixels": [image.width(), image.height()],
            "median_frame_ms": round(statistics.median(samples), 3),
            "p95_frame_ms": round(sorted(samples)[math.ceil(len(samples) * 0.95) - 1], 3),
            "cpu_seconds": round(time.process_time() - cpu_start, 3),
            "timing_note": "OpenGL timings include GPU synchronization and possible vsync",
        }
    finally:
        viewer.close()
        viewer.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--width", type=int, default=4000)
    parser.add_argument("--height", type=int, default=3000)
    args = parser.parse_args()
    if not 1 <= args.frames <= 10000 or min(args.width, args.height) < 1:
        parser.error("Use 1–10000 frames and positive image dimensions")
    if args.width * args.height > 64_000_000:
        parser.error("Image exceeds the 64-million-pixel limit")
    app = QApplication([])
    image = QImage(args.width, args.height, QImage.Format.Format_RGB32)
    gradient = QLinearGradient(0, 0, args.width, args.height)
    gradient.setColorAt(0, QColor("steelblue"))
    gradient.setColorAt(0.5, QColor("goldenrod"))
    gradient.setColorAt(1, QColor("darkred"))
    painter = QPainter(image)
    painter.fillRect(image.rect(), gradient)
    painter.end()
    for mode in ("software", "auto"):
        print(json.dumps(measure(app, image, mode, args.frames)))


if __name__ == "__main__":
    main()
