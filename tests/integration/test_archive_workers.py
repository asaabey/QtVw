"""Worker-driven archive thumbnails, covers, failures, and changed sources."""

import struct
import zipfile

import pytest
from PySide6.QtGui import QColor

from qtvw.domain import Entry
from qtvw.domain.locations import ArchiveLocation
from qtvw.services import WorkerService
from qtvw.sources.archive import ArchiveLimits, ArchiveReader


def archive_file(tmp_path, members):
    path = tmp_path / "photos.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in members:
            archive.writestr(name, data)
    return path


def decode(qtbot, worker, entry, generation=1, size=32):
    with qtbot.waitSignal(worker.decoded, timeout=10000) as result:
        worker.decode(entry, generation, size)
    assert result.args[:2] == [generation, entry.identity]
    return result.args[2:]


@pytest.mark.parametrize(
    "members,expected",
    [
        (["photo2.png", "cover.PNG", "photo10.png"], "blue"),
        (["photo10.png", "folder/photo2.png", "folder/photo10.png"], "red"),
        (["cover.png", "photo2.png", "photo10.png"], "red"),
    ],
)
def test_cover_preference_natural_paths_and_corrupt_fallback(
    qtbot,
    tmp_path,
    image_bytes,
    members,
    expected,
):
    colors = {
        "photo2.png": "red",
        "photo10.png": "green",
        "cover.PNG": "blue",
        "folder/photo2.png": "red",
        "folder/photo10.png": "green",
    }
    path = archive_file(
        tmp_path,
        [(name, image_bytes(colors[name]) if name in colors else b"bad") for name in members],
    )
    worker = WorkerService()
    try:
        image, error = decode(qtbot, worker, Entry(path, "archive"))
        assert not error
        assert image.width() == 32
        assert image.pixelColor(0, 0) == QColor(expected)
    finally:
        worker.shutdown()


def test_member_listing_decode_identity_and_changed_source(qtbot, tmp_path, image_bytes):
    path = archive_file(tmp_path, [("旅行/photo.png", image_bytes("blue"))])
    worker = WorkerService()
    batches = []
    worker.listed.connect(lambda generation, entries: batches.extend(entries))
    try:
        with qtbot.waitSignal(worker.listing_finished) as finished:
            worker.list_directory(ArchiveLocation(path, "旅行"), 10)
        assert finished.args == [10, ""]
        assert len(batches) == 1
        image, error = decode(qtbot, worker, batches[0], 11)
        assert not error
        assert image.pixelColor(0, 0) == QColor("blue")
        archive_file(tmp_path, [("new.png", image_bytes("green"))])
        image, error = decode(qtbot, worker, batches[0], 12)
        assert image.isNull()
        assert "changed" in error
    finally:
        worker.shutdown()


def test_cover_limits_fallback_and_corrupt_listing(qtbot, tmp_path, image_bytes):
    path = archive_file(tmp_path, [("cover.png", image_bytes())])
    worker = WorkerService(archive_limits=ArchiveLimits(max_cover_bytes=1))
    try:
        image, error = decode(qtbot, worker, Entry(path, "archive"))
        assert image.isNull()
        assert "budget" in error
        path.write_bytes(b"bad archive")
        with qtbot.waitSignal(worker.listing_finished) as result:
            worker.list_directory(ArchiveLocation(path), 2)
        assert result.args[0] == 2
        assert "ZIP" in result.args[1]
    finally:
        worker.shutdown()


def test_crc_failure_and_unsupported_compression_are_item_errors(qtbot, tmp_path, image_bytes):
    path = archive_file(tmp_path, [("photo.png", image_bytes())])
    original = path.read_bytes()
    changed = bytearray(original)
    changed[30 + len("photo.png") + 10] ^= 0xFF
    path.write_bytes(changed)
    reader = ArchiveReader(path, ArchiveLimits())
    entry = reader.images[0]
    reader.close()
    worker = WorkerService()
    try:
        image, error = decode(qtbot, worker, entry)
        assert image.isNull()
        assert "CRC" in error
        changed = bytearray(original)
        struct.pack_into("<H", changed, changed.index(b"PK\x03\x04") + 8, 99)
        struct.pack_into("<H", changed, changed.index(b"PK\x01\x02") + 10, 99)
        path.write_bytes(changed)
        reader = ArchiveReader(path, ArchiveLimits())
        entry = reader.images[0]
        reader.close()
        image, error = decode(qtbot, worker, entry, 2)
        assert image.isNull()
        assert "compression" in error.lower()
    finally:
        worker.shutdown()
