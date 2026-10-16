"""Preferences controls with inline validation and asynchronous cache actions."""

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


class PreferencesDialog(QDialog):
    clear_requested = Signal()

    def __init__(self, preferences, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Preferences")
        self.setModal(True)
        self.original = preferences
        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)
        browsing = QWidget()
        form = QFormLayout(browsing)
        self.restore_last = QCheckBox("Restore the last location on startup")
        self.restore_last.setChecked(preferences.restore_last)
        form.addRow(self.restore_last)
        self.start_folder = QLineEdit(preferences.start_folder)
        self.start_folder.setPlaceholderText(str(Path.home()))
        folder = QHBoxLayout()
        folder.addWidget(self.start_folder)
        choose = QPushButton("Choose…")
        choose.clicked.connect(self.choose_folder)
        folder.addWidget(choose)
        form.addRow("Starting folder", folder)
        self.thumbnail_size = self.spin(preferences.thumbnail_size, 64, 256, " px")
        form.addRow("Thumbnail size", self.thumbnail_size)
        self.show_hidden = QCheckBox("Show hidden files and folders")
        self.show_hidden.setChecked(preferences.show_hidden)
        form.addRow(self.show_hidden)
        self.sort_field = QComboBox()
        for label, value in [("Name", "name"), ("Modification date", "modified"), ("Size", "size")]:
            self.sort_field.addItem(label, value)
        self.sort_field.setCurrentIndex(self.sort_field.findData(preferences.sort_field))
        form.addRow("Sort by", self.sort_field)
        self.descending = QCheckBox("Descending order")
        self.descending.setChecked(preferences.descending)
        form.addRow(self.descending)
        self.appearance = QComboBox()
        for label, value in [("System", "system"), ("Light", "light"), ("Dark", "dark")]:
            self.appearance.addItem(label, value)
        self.appearance.setCurrentIndex(self.appearance.findData(preferences.appearance))
        form.addRow("Appearance", self.appearance)
        self.rendering = QComboBox()
        self.rendering.addItem("Auto (OpenGL when available)", "auto")
        self.rendering.addItem("Software", "software")
        self.rendering.setCurrentIndex(self.rendering.findData(preferences.rendering))
        self.rendering.setToolTip(
            "Auto uses OpenGL for the image viewer and falls back to software."
        )
        form.addRow("Image rendering", self.rendering)
        self.rendering_status = QLabel()
        self.rendering_status.setWordWrap(True)
        form.addRow(self.rendering_status)
        tabs.addTab(browsing, "Browsing")
        resources = QWidget()
        form = QFormLayout(resources)
        self.cache_mb = self.spin(preferences.cache_mb, 16, 65536, " MiB")
        form.addRow("Disk cache limit", self.cache_mb)
        self.clear_button = QPushButton("Clear cache")
        self.clear_button.clicked.connect(self.clear_requested)
        form.addRow(self.clear_button)
        self.cache_status = QLabel("Cache changes run in the background.")
        self.cache_status.setWordWrap(True)
        form.addRow(self.cache_status)
        notice = QLabel("The resource limits below take effect on the next launch.")
        notice.setWordWrap(True)
        form.addRow(notice)
        self.decoded_mp = self.spin(preferences.decoded_mp, 1, 64, " MP")
        self.input_mb = self.spin(preferences.input_mb, 1, 256, " MiB")
        self.entry_mb = self.spin(preferences.entry_mb, 1, 64, " MiB")
        self.workers = self.spin(preferences.workers, 1, 4)
        form.addRow("Maximum image pixels", self.decoded_mp)
        form.addRow("Maximum image file size", self.input_mb)
        form.addRow("Maximum ZIP entry size", self.entry_mb)
        form.addRow("Background workers", self.workers)
        tabs.addTab(resources, "Cache and limits")
        self.error = QLabel()
        self.error.setWordWrap(True)
        layout.addWidget(self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(480, 420)

    @staticmethod
    def spin(value, low, high, suffix=""):
        widget = QSpinBox()
        widget.setRange(low, high)
        widget.setSuffix(suffix)
        widget.setValue(value)
        return widget

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self, "Choose starting folder", self.start_folder.text()
        )
        if folder:
            self.start_folder.setText(folder)

    def values(self):
        return replace(
            self.original,
            restore_last=self.restore_last.isChecked(),
            start_folder=self.start_folder.text().strip(),
            thumbnail_size=self.thumbnail_size.value(),
            show_hidden=self.show_hidden.isChecked(),
            sort_field=self.sort_field.currentData(),
            descending=self.descending.isChecked(),
            appearance=self.appearance.currentData(),
            rendering=self.rendering.currentData(),
            cache_mb=self.cache_mb.value(),
            decoded_mp=self.decoded_mp.value(),
            input_mb=self.input_mb.value(),
            entry_mb=self.entry_mb.value(),
            workers=self.workers.value(),
        )

    def accept(self):
        values = self.values()
        if not values.restore_last and not values.starting_path.is_dir():
            self.error.setText("Choose an existing starting folder, or restore the last location.")
            return
        super().accept()


def apply_appearance(window, mode):
    if mode == "system":
        window.setPalette(QPalette())
        return
    palette = QPalette(QApplication.palette())
    if mode != "system":
        colors = {
            "dark": ("#242830", "#191d24", "#303641", "#edf0f4", "#3c78a8"),
            "light": ("#f2f3f5", "#ffffff", "#e6e8ec", "#20242a", "#286eb5"),
        }
        background, base, button, text, highlight = colors[mode]
        for role, color in [
            (QPalette.ColorRole.Window, background),
            (QPalette.ColorRole.Base, base),
            (QPalette.ColorRole.AlternateBase, background),
            (QPalette.ColorRole.Button, button),
            (QPalette.ColorRole.WindowText, text),
            (QPalette.ColorRole.Text, text),
            (QPalette.ColorRole.ButtonText, text),
            (QPalette.ColorRole.ToolTipBase, base),
            (QPalette.ColorRole.ToolTipText, text),
            (QPalette.ColorRole.Highlight, highlight),
            (QPalette.ColorRole.HighlightedText, "#ffffff"),
        ]:
            palette.setColor(role, QColor(color))
        for role in (
            QPalette.ColorRole.WindowText,
            QPalette.ColorRole.Text,
            QPalette.ColorRole.ButtonText,
        ):
            palette.setColor(QPalette.ColorGroup.Disabled, role, QColor("#80858e"))
    window.setPalette(palette)
