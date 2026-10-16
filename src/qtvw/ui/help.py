"""Keyboard help drawn from the actual QAction bindings and viewer diagnostics."""

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
)

from qtvw.diagnostics import log_location, logging_status


class HelpDialog(QDialog):
    def __init__(self, actions, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts and help")
        layout = QVBoxLayout(self)
        self.content = QTextBrowser()
        rows = [("Enter / Return", "Open the selected folder, archive, or image", "Thumbnail grid")]
        descriptions = {
            "Grid": "Return to the thumbnail grid",
            "Return": "Exit fullscreen first; otherwise return to the grid",
            "Previous": "Previous image in the current sort order",
            "Next": "Next image in the current sort order",
            "Fit": "Fit the image to the window",
            "100%": "Show the image at actual size",
            "Help": "Show keyboard shortcuts and help",
            "Delete images": "Confirm deletion of selected images from the current ZIP",
            "Select portrait": "Select portrait images in the current folder",
            "Select landscape": "Select landscape images in the current folder",
        }
        for action in actions:
            keys = " / ".join(
                key.toString(QKeySequence.SequenceFormat.NativeText) for key in action.shortcuts()
            )
            if keys:
                rows.append(
                    (
                        keys,
                        descriptions.get(action.text(), action.text()),
                        action.property("help_context") or "Browser and viewer",
                    )
                )
        table = "".join(
            "<tr>" + "".join(f"<td>{escape(value)}</td>" for value in row) + "</tr>" for row in rows
        )
        self.content.setHtml(
            "<h2>Keyboard shortcuts</h2><table cellpadding='6'>"
            "<tr><th>Key</th><th>Action</th><th>Where</th></tr>" + table + "</table>"
            "<h3>Mouse controls</h3><p>Double-click to open an item. In the image viewer, "
            "use the mouse wheel to zoom and drag to pan.</p>"
            "<h3>Select and delete ZIP images</h3><p>In the grid, Ctrl+click toggles "
            "individual selections; Shift+click selects a range. Ctrl+A selects all images "
            "in the current folder. Ctrl+Shift+P selects portrait images; Ctrl+Shift+L selects "
            "landscape images. These replace the selection, respect EXIF rotation, and exclude "
            "square images. Checking runs in the background; Esc cancels. "
            "Press Delete or click Delete images to confirm permanent "
            "removal from a writable ZIP. Folders and nested ZIPs are not deleted. "
            "Rewriting needs temporary disk space; progress includes a Cancel button. "
            "There is no Trash or undo.</p>"
            "<h3>Image smoothing</h3><p>Preferences → Browsing → Smooth image scaling "
            "controls interpolation when fitting or zooming. Turn it off for crisp pixel art. "
            "Blur, denoise, and sharpening filters are not implemented; "
            "source images are unchanged.</p>"
        )
        layout.addWidget(self.content)
        self.logs = QLabel("Log folder: " + str(log_location()))
        self.logs.setWordWrap(True)
        self.logs.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(self.logs)
        self.log_status = QLabel(logging_status())
        self.log_status.setWordWrap(True)
        layout.addWidget(self.log_status)
        self.copy_logs = QPushButton("Copy log folder")
        self.copy_logs.clicked.connect(
            lambda: QApplication.clipboard().setText(str(log_location()))
        )
        layout.addWidget(self.copy_logs)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(720, 640)
