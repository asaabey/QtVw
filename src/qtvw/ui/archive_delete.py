"""ZIP rewrite progress that stays modal until cancellation has completed."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QPushButton, QVBoxLayout


class ArchiveDeleteProgress(QDialog):
    def __init__(self, editor, parent=None):
        super().__init__(parent)
        self.editor = editor
        self.setWindowTitle("Deleting images from ZIP")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        layout = QVBoxLayout(self)
        self.label = QLabel("Preparing ZIP rewrite…")
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        layout.addWidget(self.progress)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        layout.addWidget(self.cancel_button)
        self.resize(440, 150)

    def update_progress(self, percent, label):
        self.progress.setValue(percent)
        if self.cancel_button.isEnabled():
            self.label.setText(label)

    def reject(self):
        self.editor.cancel()
        self.cancel_button.setEnabled(False)
        self.label.setText("Cancelling… Waiting for the archive operation to finish.")
