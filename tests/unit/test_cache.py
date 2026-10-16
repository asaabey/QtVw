"""Byte bounds, cache identity, disposable corruption, and clear generations."""

import sqlite3
from dataclasses import replace

from PySide6.QtGui import QColor, QImage

from qtvw.cache import CacheStore, thumbnail_key
from qtvw.cache.store import ImageLRU
from qtvw.domain import Entry
from qtvw.sources.archive import fingerprint


def test_image_lru_uses_byte_budget_and_detached_return_values():
    memory = ImageLRU(800)
    red = QImage(10, 10, QImage.Format.Format_RGB32)
    red.fill(QColor("red"))
    memory.put("one", red)
    memory.put("two", red)
    assert memory.bytes == 800
    returned = memory.get("one")
    returned.fill(QColor("blue"))
    assert memory.get("one").pixelColor(0, 0) == QColor("red")
    memory.put("three", red)
    assert memory.get("two") is None
    assert memory.get("one") is not None
    large = QImage(100, 100, QImage.Format.Format_RGB32)
    memory.put("large", large)
    assert memory.get("large") is None
    assert memory.bytes <= memory.limit


def test_thumbnail_identity_includes_source_member_size_and_versions(tmp_path):
    entry = Entry(tmp_path / "a.zip", "image", 99, member="folder/a.png", member_index=1, crc=123)
    original = thumbnail_key(entry, (100, 200), 128, {"version": 1})
    alternatives = [
        thumbnail_key(entry, (100, 201), 128, {"version": 1}),
        thumbnail_key(entry, (101, 200), 128, {"version": 1}),
        thumbnail_key(entry, (100, 200), 256, {"version": 1}),
        thumbnail_key(entry, (100, 200), 128, {"version": 2}),
        thumbnail_key(replace(entry, crc=124), (100, 200), 128, {"version": 1}),
        thumbnail_key(replace(entry, member_index=2), (100, 200), 128, {"version": 1}),
        thumbnail_key(replace(entry, member="other.png"), (100, 200), 128, {"version": 1}),
        thumbnail_key(replace(entry, size=100), (100, 200), 128, {"version": 1}),
    ]
    assert len(set([original, *alternatives])) == 9


def test_clear_rejects_late_writes_and_memory_results(tmp_path):
    source = tmp_path / "image.png"
    source.write_bytes(b"source")
    cache = CacheStore(tmp_path / "cache")
    key = thumbnail_key(Entry(source, "image"), fingerprint(source), 128, {})
    epoch = cache.epoch
    image = QImage(1, 1, QImage.Format.Format_RGB32)
    try:
        cache.memory_put(key, image, epoch)
        cache.thumbnail_put(key, source, fingerprint(source), b"before", epoch)
        assert cache.memory_get(key) is not None
        cache.clear()
        assert cache.epoch != epoch
        cache.memory_put(key, image, epoch)
        cache.thumbnail_put(key, source, fingerprint(source), b"late", epoch)
        cache.index_put(source, fingerprint(source), {}, [], epoch)
        assert cache.memory_get(key) is None
        assert cache.thumbnail_get(key, source, fingerprint(source)) is None
        assert cache.index_get(source, fingerprint(source), {}) is None
        assert not list((cache.root / "thumbnails").glob("*.png"))
    finally:
        cache.close()


def test_corrupt_database_is_rebuilt(tmp_path):
    root = tmp_path / "cache"
    root.mkdir()
    (root / "index.sqlite3").write_bytes(b"corrupt database")
    path = tmp_path / "image.png"
    path.write_bytes(b"source")
    cache = CacheStore(root)
    try:
        cache.index_put(path, fingerprint(path), {}, [], cache.epoch)
        assert cache.index_get(path, fingerprint(path), {}) == []
        with sqlite3.connect(root / "index.sqlite3") as db:
            assert db.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        cache.close()


def test_disk_payload_eviction_retains_recent_entry(tmp_path):
    path = tmp_path / "image.png"
    path.write_bytes(b"source")
    cache = CacheStore(tmp_path / "cache", disk_payload_limit=6)
    first, second = "a" * 64, "b" * 64
    try:
        cache.thumbnail_put(first, path, fingerprint(path), b"first", cache.epoch)
        cache.thumbnail_put(second, path, fingerprint(path), b"second", cache.epoch)
        assert cache.thumbnail_get(first, path, fingerprint(path)) is None
        assert cache.thumbnail_get(second, path, fingerprint(path)) == b"second"
    finally:
        cache.close()


def test_physical_cleanup_accounts_for_sqlite_and_removes_old_orphans(tmp_path):
    import os
    import time

    source = tmp_path / "source.png"
    source.write_bytes(b"source")
    cache = CacheStore(tmp_path / "cache")
    try:
        for index in range(12):
            key = f"{index:064x}"
            cache.thumbnail_put(key, source, fingerprint(source), b"x" * 20000, cache.epoch)
        orphan = cache.root / "thumbnails" / ("f" * 64 + ".png")
        orphan.write_bytes(b"orphan")
        temporary = orphan.with_name(".tmp-abandoned")
        temporary.write_bytes(b"unfinished")
        old = time.time() - 3600
        os.utime(orphan, (old, old))
        os.utime(temporary, (old, old))
        result = cache.maintain_async(120000).result(timeout=5)
        assert result["ok"], result
        assert result["bytes"] <= 120000
        actual = sum(path.stat().st_size for path in cache.root.rglob("*") if path.is_file())
        assert actual == result["bytes"]
        assert not orphan.exists()
        assert not temporary.exists()
        cleared = cache.clear_async().result(timeout=5)
        assert cleared["ok"]
        assert not list((cache.root / "thumbnails").iterdir())
    finally:
        cache.close()


def test_unavailable_storage_reports_maintenance_failure(tmp_path):
    invalid = tmp_path / "file"
    invalid.write_bytes(b"occupied")
    cache = CacheStore(invalid)
    try:
        result = cache.clear_async().result(timeout=5)
        assert not result["ok"]
        assert result["message"]
    finally:
        cache.close()
