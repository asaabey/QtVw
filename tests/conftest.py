"""Keep GUI tests independent of a developer's saved application preferences."""

import pytest
from PySide6.QtCore import QSettings


@pytest.fixture(autouse=True)
def isolated_preferences(monkeypatch, tmp_path):
    from qtvw.cache import store
    from qtvw.ui import main_window

    cache_root = tmp_path.parent / f".cache-{tmp_path.name}"
    monkeypatch.setattr(store, "default_cache_dir", lambda: cache_root)
    monkeypatch.setattr(
        main_window,
        "QSettings",
        lambda *args: QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat),
    )


@pytest.fixture
def image_bytes():
    from PySide6.QtCore import QBuffer, QIODevice
    from PySide6.QtGui import QColor, QImage

    def create(color="red", width=80, height=40):
        image = QImage(width, height, QImage.Format.Format_RGB32)
        image.fill(QColor(color))
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        assert image.save(buffer, "PNG")
        return bytes(buffer.data())

    return create
