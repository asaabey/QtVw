"""Transform-based image viewer; pixmaps are created only on the GUI thread."""

import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QOffscreenSurface, QOpenGLContext, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QGraphicsScene, QGraphicsView, QWidget


def probe_opengl():
    """Check context creation on the GUI thread without installing a GL viewport."""
    platform = QGuiApplication.platformName().lower()
    if platform in {"offscreen", "minimal", "minimalegl", "vnc", "linuxfb"}:
        return None, f"OpenGL viewer is unavailable on the {platform} display platform"
    context = QOpenGLContext()
    surface = QOffscreenSurface()
    try:
        if not context.create() or not context.isValid():
            return None, "An OpenGL context could not be created"
        surface.setFormat(context.format())
        surface.create()
        if not surface.isValid() or not context.makeCurrent(surface):
            return None, "The OpenGL context could not render to a surface"
        return context.format(), ""
    except (RuntimeError, OSError) as error:
        return None, f"OpenGL is unavailable: {error}"
    finally:
        context.doneCurrent()
        surface.destroy()


def create_opengl_viewport(surface_format, parent):
    from qtvw.ui.opengl import OpenGLViewport

    return OpenGLViewport(surface_format, parent)


class ImageViewer(QGraphicsView):
    backend_changed = Signal(str)
    navigate_requested = Signal(int)
    browse_requested = Signal()

    def __init__(self, parent=None, rendering_mode="auto", smooth_scaling=True):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.item = None
        self._click_origin = None
        self._click_dragged = False
        self.fit_mode = True
        self.smooth_scaling = smooth_scaling
        self.rendering_mode = None
        self.rendering_backend = "software"
        self.fallback_reason = ""
        self._gl_viewport = None
        self._gl_ready = False
        self._switching_renderer = False
        self._pending_center = None
        self._restore_timer = QTimer(self)
        self._restore_timer.setSingleShot(True)
        self._restore_timer.timeout.connect(self._restore_center)
        self._fallback_timer = QTimer(self)
        self._fallback_timer.setSingleShot(True)
        self._fallback_timer.timeout.connect(self._fallback_to_software)
        self.set_smooth_scaling(smooth_scaling)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setBackgroundBrush(Qt.GlobalColor.black)
        self.set_rendering_mode(rendering_mode)

    @property
    def rendering_description(self):
        if self.rendering_backend == "opengl":
            return "OpenGL" if self._gl_ready else "OpenGL (initializes when the viewer opens)"
        if self.fallback_reason:
            return f"Software — {self.fallback_reason}"
        return "Software rendering"

    def set_rendering_mode(self, mode):
        if mode not in {"auto", "software"}:
            raise ValueError("Rendering mode must be auto or software")
        if mode == self.rendering_mode:
            return
        self._fallback_timer.stop()
        self.rendering_mode = mode
        self.fallback_reason = ""
        self._gl_ready = False
        if mode == "auto":
            surface_format, reason = probe_opengl()
            if surface_format is not None:
                try:
                    viewport = create_opengl_viewport(surface_format, self)
                    viewport.ready.connect(self._confirm_opengl)
                    viewport.failed.connect(self._schedule_fallback)
                    self._gl_viewport = viewport
                    self._replace_viewport(viewport, "opengl")
                    return
                except (ImportError, RuntimeError, OSError) as error:
                    reason = f"OpenGL viewport is unavailable: {error}"
            self.fallback_reason = reason
        self._gl_viewport = None
        self._replace_viewport(QWidget(), "software")

    def _replace_viewport(self, viewport, backend):
        transform = self.transform()
        center = self._pending_center
        if center is None:
            center = self.mapToScene(self.viewport().rect().center())
        self._switching_renderer = True
        try:
            self.rendering_backend = backend
            self.setViewport(viewport)
            self.setViewportUpdateMode(
                QGraphicsView.ViewportUpdateMode.FullViewportUpdate
                if backend == "opengl"
                else QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate
            )
            if self.fit_mode:
                self.fit()
            else:
                self.setTransform(transform)
                self.centerOn(center)
                self._pending_center = center
                self._restore_timer.start(0)
        finally:
            self._switching_renderer = False
        self.viewport().update()
        self.backend_changed.emit(self.rendering_description)
        logging.getLogger(__name__).info("Viewer renderer: %s", self.rendering_description)

    def _restore_center(self):
        # Scrollbar geometry can settle one event-loop turn after setViewport().
        if self._pending_center is not None and not self.fit_mode:
            self.centerOn(self._pending_center)
        self._pending_center = None

    def _confirm_opengl(self):
        if self.rendering_backend == "opengl":
            self._gl_ready = True
            self.backend_changed.emit(self.rendering_description)

    def _schedule_fallback(self, reason):
        if self.rendering_backend == "opengl":
            logging.getLogger(__name__).warning("OpenGL fallback: %s", reason)
            self.fallback_reason = reason
            # Replacing a viewport inside its show/paint handler would delete it mid-call.
            self._fallback_timer.start(0)

    def _fallback_to_software(self):
        if self.rendering_backend == "opengl":
            self._gl_viewport = None
            self._replace_viewport(QWidget(), "software")

    def clear(self):
        self.scene().clear()
        self.item = None

    def set_image(self, image):
        self.clear()
        self.item = self.scene().addPixmap(QPixmap.fromImage(image))
        self.item.setTransformationMode(
            Qt.TransformationMode.SmoothTransformation
            if self.smooth_scaling
            else Qt.TransformationMode.FastTransformation
        )
        self.scene().setSceneRect(self.item.boundingRect())
        self.fit()

    def set_smooth_scaling(self, enabled):
        self.smooth_scaling = bool(enabled)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, self.smooth_scaling)
        if self.item is not None:
            self.item.setTransformationMode(
                Qt.TransformationMode.SmoothTransformation
                if self.smooth_scaling
                else Qt.TransformationMode.FastTransformation
            )

    def fit(self):
        self._pending_center = None
        self._restore_timer.stop()
        self.fit_mode = True
        if self.item:
            self.fitInView(self.item, Qt.AspectRatioMode.KeepAspectRatio)

    def actual_size(self):
        self._pending_center = None
        self._restore_timer.stop()
        self.fit_mode = False
        self.resetTransform()

    def zoom(self, factor):
        scale = self.transform().m11() * factor
        if 0.01 <= scale <= 64:
            self._pending_center = None
            self._restore_timer.stop()
            self.fit_mode = False
            self.scale(factor, factor)

    def wheelEvent(self, event):
        self.zoom(1.25 if event.angleDelta().y() > 0 else 0.8)
        event.accept()

    def mousePressEvent(self, event):
        self._click_origin = None
        self._click_dragged = False
        if event.button() == Qt.MouseButton.LeftButton:
            self._click_origin = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._click_origin is not None:
            distance = (event.position().toPoint() - self._click_origin).manhattanLength()
            if distance >= QApplication.startDragDistance():
                self._click_dragged = True
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        origin = self._click_origin
        position = event.position().toPoint()
        clicked = (
            event.button() == Qt.MouseButton.LeftButton
            and origin is not None
            and not self._click_dragged
            and (position - origin).manhattanLength() < QApplication.startDragDistance()
            and self.viewport().rect().contains(position)
        )
        self._click_origin = None
        super().mouseReleaseEvent(event)
        if clicked and origin.y() >= self.viewport().height() / 2:
            self.navigate_requested.emit(-1 if origin.x() < self.viewport().width() / 2 else 1)

    def mouseDoubleClickEvent(self, event):
        self._click_origin = None
        if (
            event.button() == Qt.MouseButton.LeftButton
            and event.position().y() < self.viewport().height() / 2
        ):
            event.accept()
            self.browse_requested.emit()
            return
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fit_mode and not self._switching_renderer:
            self.fit()
        elif self._pending_center is not None:
            self.centerOn(self._pending_center)
