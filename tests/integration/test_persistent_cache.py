"""Persistent cache reuse and invalidation across worker and process lifetimes."""

import json
import sqlite3
import subprocess
import sys
import zipfile
from dataclasses import asdict

import pytest
from PySide6.QtGui import QColor, QImage

from qtvw.cache import CacheStore
from qtvw.domain import Entry
from qtvw.domain.locations import ArchiveLocation
from qtvw.services import WorkerService
from qtvw.sources.archive import ArchiveLimits, fingerprint


def decode(qtbot, worker, entry, generation=1, size=32):
    with qtbot.waitSignal(worker.decoded, timeout=10000) as result:
        assert worker.decode(entry, generation, size)
    assert not result.args[3], result.args[3]
    return result.args[2]


def zip_image(path, data, name="folder/photo.png"):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, data)


def test_archive_restart_reuses_index_and_thumbnail_without_enumeration(
    qtbot,
    tmp_path,
    image_bytes,
    monkeypatch,
):
    path = tmp_path / "photos.zip"
    zip_image(path, image_bytes("blue"))
    worker = WorkerService()
    root = worker.cache.root
    entries = []
    worker.listed.connect(lambda _, batch: entries.extend(batch))
    with qtbot.waitSignal(worker.listing_finished):
        worker.list_directory(ArchiveLocation(path, "folder"), 1)
    entry = entries[0]
    assert decode(qtbot, worker, entry).pixelColor(0, 0) == QColor("blue")
    decode(qtbot, worker, Entry(path, "archive"), 2)
    worker.shutdown()
    assert list((root / "thumbnails").glob("*.png"))

    def fail(*args, **kwargs):
        pytest.fail("A warm archive must use its persistent index and thumbnails")

    from qtvw.sources import archive

    monkeypatch.setattr(archive.ArchiveReader, "__init__", fail)
    warm = WorkerService()
    warm._decode_source = fail
    warm_entries = []
    warm.listed.connect(lambda _, batch: warm_entries.extend(batch))
    try:
        with qtbot.waitSignal(warm.listing_finished) as result:
            warm.list_directory(ArchiveLocation(path, "folder"), 3)
        assert not result.args[1]
        assert warm_entries == [entry]
        assert decode(qtbot, warm, entry, 4).pixelColor(0, 0) == QColor("blue")
        assert decode(qtbot, warm, Entry(path, "archive"), 5).pixelColor(0, 0) == QColor("blue")
    finally:
        warm.shutdown()


def test_warm_cache_survives_a_new_process(qtbot, tmp_path, image_bytes):
    path = tmp_path / "photos.zip"
    zip_image(path, image_bytes("green"))
    worker = WorkerService()
    root = worker.cache.root
    decode(qtbot, worker, Entry(path, "archive"))
    worker.shutdown()
    script = """
import sys
from pathlib import Path
from PySide6.QtCore import QCoreApplication, QTimer
from qtvw.cache import CacheStore
from qtvw.domain import Entry
from qtvw.domain.locations import ArchiveLocation
from qtvw.services import WorkerService
from qtvw.sources.archive import ArchiveReader
app = QCoreApplication([])
path, root = map(Path, sys.argv[1:])
def fail(*args, **kwargs):
    raise AssertionError("Source must not be enumerated or decoded")
ArchiveReader.__init__ = fail
worker = WorkerService(cache=CacheStore(root))
worker._decode_source = fail
results = []
def listed(generation, error):
    assert not error, error
    worker.decode(Entry(path, "archive"), 2, 32)
def decoded(generation, identity, image, error):
    results.append((image.isNull(), error))
    app.quit()
worker.listing_finished.connect(listed)
worker.decoded.connect(decoded)
worker.list_directory(ArchiveLocation(path), 1)
QTimer.singleShot(10000, app.quit)
app.exec()
worker.shutdown()
worker.cache.close()
assert results == [(False, "")], results
print("Persistent index and thumbnail reused in new process")
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), str(root)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr
    assert "reused in new process" in result.stdout


@pytest.mark.parametrize("damage", ["missing", "corrupt", "wrong_size"])
def test_damaged_thumbnail_regenerates(qtbot, tmp_path, image_bytes, damage):
    path = tmp_path / "photo.png"
    path.write_bytes(image_bytes("red"))
    worker = WorkerService()
    root = worker.cache.root
    decode(qtbot, worker, path)
    worker.shutdown()
    file = next((root / "thumbnails").glob("*.png"))
    if damage == "missing":
        file.unlink()
    elif damage == "wrong_size":
        file.write_bytes(b"bad")
    else:
        data = bytearray(file.read_bytes())
        data[:8] = b"corrupt!"
        file.write_bytes(data)  # Same byte length: force the image-validation path.
    warm = WorkerService()
    try:
        assert decode(qtbot, warm, path, 2).pixelColor(0, 0) == QColor("red")
        assert not QImage(str(file)).isNull()
    finally:
        warm.shutdown()


def test_changed_archive_invalidates_index_covers_and_members(qtbot, tmp_path, image_bytes):
    path = tmp_path / "photos.zip"
    zip_image(path, image_bytes("red"))
    worker = WorkerService()
    root = worker.cache.root
    decode(qtbot, worker, Entry(path, "archive"))
    old = fingerprint(path)
    old_files = set((root / "thumbnails").glob("*.png"))
    zip_image(path, image_bytes("green"), "new/photo10.png")
    assert fingerprint(path) != old
    try:
        assert decode(qtbot, worker, Entry(path, "archive"), 2).pixelColor(0, 0) == QColor("green")
        entries = []
        worker.listed.connect(lambda _, batch: entries.extend(batch))
        with qtbot.waitSignal(worker.listing_finished):
            worker.list_directory(ArchiveLocation(path), 3)
        assert [entry.name for entry in entries] == ["new"]
        assert not old_files & set((root / "thumbnails").glob("*.png"))
        with sqlite3.connect(root / "index.sqlite3") as db:
            assert db.execute("SELECT size, mtime FROM sources").fetchall() == [fingerprint(path)]
    finally:
        worker.shutdown()


def test_source_change_during_decode_never_writes_or_displays_old_image(
    qtbot, tmp_path, image_bytes
):
    path = tmp_path / "photo.png"
    path.write_bytes(image_bytes("red"))
    worker = WorkerService()
    original = worker._decode_source
    changes = []
    worker.source_changed.connect(lambda *args: changes.append(args))

    def changing(*args):
        image = original(*args)
        path.write_bytes(image_bytes("green"))
        return image

    worker._decode_source = changing
    try:
        with qtbot.waitSignal(worker.decoded) as result:
            worker.decode(path, 1, 32)
        assert result.args[2].isNull()
        assert "changed" in result.args[3]
        assert changes
        assert not list((worker.cache.root / "thumbnails").glob("*.png"))
    finally:
        worker.shutdown()


def test_malformed_index_rebuilds_and_tighter_limits_do_not_reuse_it(
    qtbot,
    tmp_path,
    image_bytes,
):
    path = tmp_path / "photos.zip"
    zip_image(path, image_bytes())
    worker = WorkerService()
    with qtbot.waitSignal(worker.listing_finished):
        worker.list_directory(ArchiveLocation(path), 1)
    root = worker.cache.root
    worker.shutdown()
    with sqlite3.connect(root / "index.sqlite3") as db:
        db.execute(
            "UPDATE archives SET data=?", (json.dumps([["../bad", "folder", 0, 0, None, 0]]),)
        )
    warm = WorkerService()
    try:
        with qtbot.waitSignal(warm.listing_finished) as result:
            warm.list_directory(ArchiveLocation(path), 2)
        assert not result.args[1]
        records = warm.cache.index_get(path, fingerprint(path), asdict(ArchiveLimits()))
        assert records[0][0] == "folder"
    finally:
        warm.shutdown()
    tight = WorkerService(archive_limits=ArchiveLimits(max_directory_bytes=1))
    try:
        with qtbot.waitSignal(tight.listing_finished) as result:
            tight.list_directory(ArchiveLocation(path), 3)
        assert "limit" in result.args[1]
    finally:
        tight.shutdown()


def test_storage_unavailable_falls_back_to_browsing(qtbot, tmp_path, image_bytes):
    path = tmp_path / "photo.png"
    path.write_bytes(image_bytes("red"))
    invalid_root = tmp_path / "file-instead-of-directory"
    invalid_root.write_text("occupied")
    cache = CacheStore(invalid_root)
    worker = WorkerService(cache=cache)
    try:
        assert decode(qtbot, worker, path).pixelColor(0, 0) == QColor("red")
    finally:
        worker.shutdown()
        cache.close()


def test_full_images_use_memory_only_and_tighter_decode_limits_invalidate(
    qtbot,
    tmp_path,
    image_bytes,
):
    path = tmp_path / "photo.png"
    path.write_bytes(image_bytes("red"))
    worker = WorkerService()
    try:
        decode(qtbot, worker, path, size=0)
        assert worker.cache.images.bytes > 0
        assert not list((worker.cache.root / "thumbnails").glob("*.png"))
        calls = []
        original = worker._decode_source

        def source(*args):
            calls.append(True)
            return original(*args)

        worker._decode_source = source
        decode(qtbot, worker, path, 2, size=0)
        assert not calls
        decode(qtbot, worker, path, 3)
        assert calls == [True]
        worker.max_pixels = 1
        with qtbot.waitSignal(worker.decoded) as result:
            worker.decode(path, 4, 32)
        assert result.args[2].isNull()
        assert "pixel limit" in result.args[3]
    finally:
        worker.shutdown()
    warm = WorkerService()
    try:
        calls = []
        original = warm._decode_source

        def source_after_restart(*args):
            calls.append(True)
            return original(*args)

        warm._decode_source = source_after_restart
        decode(qtbot, warm, path, 5, size=0)
        assert calls == [True]
    finally:
        warm.shutdown()


def test_full_cache_storage_keeps_source_browsing_and_cleans_temporary_files(
    qtbot,
    tmp_path,
    image_bytes,
    monkeypatch,
):
    import errno

    from qtvw.cache import store

    path = tmp_path / "photo.png"
    path.write_bytes(image_bytes("blue"))
    worker = WorkerService()

    def full_disk(*args):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(store.os, "replace", full_disk)
    try:
        assert decode(qtbot, worker, path).pixelColor(0, 0) == QColor("blue")
        assert not list((worker.cache.root / "thumbnails").iterdir())
    finally:
        worker.shutdown()
