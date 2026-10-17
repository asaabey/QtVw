"""Keep GUI tests independent of a developer's saved application preferences."""

import pytest
from PySide6.QtCore import QSettings


@pytest.fixture(autouse=True)
def isolated_preferences(monkeypatch, tmp_path):
    from qtvw import diagnostics
    from qtvw.cache import store
    from qtvw.ui import main_window

    cache_root = tmp_path.parent / f".cache-{tmp_path.name}"
    monkeypatch.setattr(store, "default_cache_dir", lambda: cache_root)
    monkeypatch.setattr(diagnostics, "default_log_dir", lambda: cache_root / "logs")
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


@pytest.fixture
def make_rar():
    import struct
    import zlib

    def write_rar(path, members):
        """Create stored RAR4 members with valid header and payload checksums."""

        def header(kind, flags, body):
            data = struct.pack("<BHH", kind, flags, len(body) + 7) + body
            return struct.pack("<H", zlib.crc32(data) & 0xFFFF) + data

        data = b"Rar!\x1a\x07\x00" + header(0x73, 0, b"\x00" * 6)
        for name, payload in members:
            encoded = name.encode("ascii")
            body = (
                struct.pack(
                    "<IIBIIBBHI",
                    len(payload),
                    len(payload),
                    3,
                    zlib.crc32(payload),
                    0,
                    20,
                    0x30,
                    len(encoded),
                    0o100644,
                )
                + encoded
            )
            data += header(0x74, 0x8000, body) + payload
        path.write_bytes(data + header(0x7B, 0x4000, b""))
        return path

    return write_rar
