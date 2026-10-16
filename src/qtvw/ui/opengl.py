"""OpenGL viewport lifecycle checks; imported only after a successful probe."""

from PySide6.QtCore import QTimer, Signal
from PySide6.QtOpenGLWidgets import QOpenGLWidget


class OpenGLViewport(QOpenGLWidget):
    ready = Signal()
    failed = Signal(str)

    def __init__(self, surface_format, parent=None):
        super().__init__(parent)
        self.setFormat(surface_format)
        self._confirmed = False
        self._failure_sent = False
        self._check = QTimer(self)
        self._check.setSingleShot(True)
        self._check.timeout.connect(self._validate_context)
        self._monitor = QTimer(self)
        self._monitor.setInterval(1000)
        self._monitor.timeout.connect(self._validate_context)

    def showEvent(self, event):
        super().showEvent(event)
        self._check.start(0)
        self._monitor.start()

    def hideEvent(self, event):
        self._check.stop()
        self._monitor.stop()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._check.start(0)

    def _validate_context(self):
        if not self.isVisible() or self.width() <= 0 or self.height() <= 0:
            return
        context = self.context()
        if self.isValid() and context is not None and context.isValid():
            if not self._confirmed:
                self._confirmed = True
                self.ready.emit()
        elif not self._failure_sent:
            self._failure_sent = True
            self.failed.emit("OpenGL viewer initialization failed or its context was lost")
