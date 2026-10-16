"""Software fallback, live renderer changes, and opt-in native OpenGL coverage."""

import os

import pytest
from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QImage, QSurfaceFormat
from PySide6.QtWidgets import QGraphicsView, QWidget

from qtvw.ui import viewer as viewer_module
from qtvw.ui.main_window import MainWindow
from qtvw.ui.viewer import ImageViewer


class FakeGLViewport(QWidget):
    """Lifecycle stand-in: headless tests do not claim actual GPU rendering."""

    ready = Signal()
    failed = Signal(str)


@pytest.fixture
def fake_gl(monkeypatch):
    monkeypatch.setattr(viewer_module, "probe_opengl", lambda: (QSurfaceFormat(), ""))
    monkeypatch.setattr(
        viewer_module,
        "create_opengl_viewport",
        lambda surface_format, parent: FakeGLViewport(parent),
    )


def test_software_never_probes_or_installs_opengl(qtbot, monkeypatch):
    def unexpected():
        pytest.fail("Software mode must not probe OpenGL")

    monkeypatch.setattr(viewer_module, "probe_opengl", unexpected)
    viewer = ImageViewer(rendering_mode="software")
    qtbot.addWidget(viewer)
    assert viewer.rendering_backend == "software"
    assert viewer.viewportUpdateMode() == QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate


def test_headless_auto_skips_context_creation(qtbot, monkeypatch):
    class Headless:
        @staticmethod
        def platformName():
            return "offscreen"

    def unexpected():
        pytest.fail("The offscreen platform must not attempt OpenGL context creation")

    monkeypatch.setattr(viewer_module, "QGuiApplication", Headless)
    monkeypatch.setattr(viewer_module, "QOpenGLContext", unexpected)
    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    assert viewer.rendering_mode == "auto"
    assert viewer.rendering_backend == "software"
    assert "offscreen" in viewer.rendering_description


def test_failed_probe_keeps_software(qtbot, monkeypatch):
    monkeypatch.setattr(viewer_module, "probe_opengl", lambda: (None, "No working context"))
    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    assert viewer.rendering_backend == "software"
    assert "No working context" in viewer.rendering_description


def test_unavailable_widget_keeps_software(qtbot, monkeypatch, fake_gl):
    def unavailable(*args):
        raise ImportError("OpenGL widget support is missing")

    monkeypatch.setattr(viewer_module, "create_opengl_viewport", unavailable)
    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    assert viewer.rendering_backend == "software"
    assert "missing" in viewer.rendering_description


def test_late_opengl_failure_preserves_image_zoom_and_pan(qtbot, fake_gl):
    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(400, 300)
    viewer.show()
    image = QImage(1000, 800, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    viewer.set_image(image)
    viewport = viewer.viewport()
    assert isinstance(viewport, FakeGLViewport)
    assert viewer.viewportUpdateMode() == QGraphicsView.ViewportUpdateMode.FullViewportUpdate
    viewport.ready.emit()
    assert viewer.rendering_description == "OpenGL"
    viewer.actual_size()
    viewer.zoom(1.5)
    qtbot.waitUntil(lambda: viewer.viewport().width() < viewer.maximumViewportSize().width())
    viewer.centerOn(600, 500)
    item = viewer.item
    scale = viewer.transform().m11()
    center = viewer.mapToScene(viewer.viewport().rect().center())
    viewport.failed.emit("Simulated context loss")
    qtbot.waitUntil(lambda: viewer.rendering_backend == "software")
    qtbot.waitUntil(lambda: viewer._pending_center is None)
    assert viewer.rendering_mode == "auto"
    assert viewer.item is item
    assert viewer.item.pixmap().toImage().pixelColor(0, 0) == QColor("red")
    assert viewer.transform().m11() == scale
    restored = viewer.mapToScene(viewer.viewport().rect().center())
    assert abs(restored.x() - center.x()) <= 2
    assert abs(restored.y() - center.y()) <= 2
    assert "Simulated context loss" in viewer.rendering_description
    assert viewer.viewportUpdateMode() == QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate


def test_switching_modes_cancels_a_pending_fallback(qtbot, fake_gl):
    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.viewport().failed.emit("Old context failed")
    viewer.set_rendering_mode("software")
    viewer.set_rendering_mode("auto")
    current = viewer.viewport()
    qtbot.wait(1)  # Deliver the obsolete deferred callback, if one survived the switch.
    assert viewer.rendering_backend == "opengl"
    assert viewer.viewport() is current
    assert not viewer.fallback_reason


def test_rendering_preference_persists_and_cli_override_is_session_only(qtbot, tmp_path):
    window = MainWindow(tmp_path, software_rendering=True)
    qtbot.addWidget(window)
    window.show()
    assert window.viewer.rendering_mode == "software"
    assert window.preferences.rendering == "auto"
    assert "Software" in window.renderer_label.text()
    dialog = window.show_preferences()
    assert "Software" in dialog.rendering_status.text()
    dialog.rendering.setCurrentIndex(dialog.rendering.findData("software"))
    dialog.accept()
    qtbot.waitUntil(lambda: not window._cache_busy)
    window.close()
    restored = MainWindow(tmp_path)
    qtbot.addWidget(restored)
    assert restored.preferences.rendering == "software"
    assert restored.viewer.rendering_mode == "software"
    restored.close()


@pytest.mark.skipif(
    os.environ.get("QTVW_TEST_OPENGL") != "1",
    reason="Opt in on a native display with QTVW_TEST_OPENGL=1",
)
def test_native_opengl_framebuffer_and_software_switch(qtbot):
    from PySide6.QtGui import QPaintEngine
    from PySide6.QtOpenGLWidgets import QOpenGLWidget

    surface_format, reason = viewer_module.probe_opengl()
    assert surface_format is not None, reason
    viewer = ImageViewer()
    qtbot.addWidget(viewer)
    viewer.resize(640, 480)
    image = QImage(1600, 800, QImage.Format.Format_RGB32)
    image.fill(QColor("red"))
    viewer.set_image(image)
    viewer.show()
    qtbot.waitExposed(viewer)
    qtbot.waitUntil(lambda: viewer.rendering_description == "OpenGL", timeout=10000)
    assert isinstance(viewer.viewport(), QOpenGLWidget)
    assert viewer.viewport().isValid()
    viewer.viewport().makeCurrent()
    assert viewer.viewport().paintEngine().type() == QPaintEngine.Type.OpenGL2
    viewer.viewport().doneCurrent()

    # QOpenGLWidget.grabFramebuffer() repaints its empty paintGL(), overwriting
    # QGraphicsView's externally painted frame. Capture the presented window instead.
    def red_is_presented():
        image = viewer.screen().grabWindow(viewer.winId()).toImage()
        if image.isNull():
            pytest.skip("The native display does not support window capture")
        color = image.pixelColor(image.width() // 2, image.height() // 2)
        # Native display color management can change the exact RGB values.
        return color.red() > 180 and color.green() < 40 and color.blue() < 40

    qtbot.waitUntil(red_is_presented, timeout=10000)
    viewer.actual_size()
    viewer.zoom(1.25)
    viewer.centerOn(1000, 500)
    viewer.set_rendering_mode("software")
    assert viewer.transform().m11() == 1.25
    assert viewer.item.pixmap().size() == image.size()
    assert viewer.rendering_backend == "software"
    viewer.fit()
    assert viewer.fit_mode
    viewer.close()
