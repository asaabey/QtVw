"""ZIP edits are atomic and distinguish duplicate members without extracting them."""

import copy
import fcntl
import os
import stat
import struct
import warnings
import zipfile
import zlib
from dataclasses import replace
from threading import Event

import pytest

from qtvw.sources import archive_edit
from qtvw.sources.archive import ArchiveLimits, ArchiveReader, Cancelled, SourceChanged


def fixture_zip(tmp_path):
    path = tmp_path / "photos.zip"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w") as stream:
            stream.comment = b"album comment"
            for name, content in [
                ("旅行/photo.png", b"first"),
                ("旅行/photo.png", b"second"),
                ("旅行/other.png", b"third"),
                ("notes.txt", b"keep notes"),
                ("empty/", b""),
                ("../untouched.txt", b"unusual member"),
                ("nested.zip", b"opaque ZIP data"),
            ]:
                info = zipfile.ZipInfo(name, (2020, 2, 3, 4, 5, 6))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (stat.S_IFREG | 0o640) << 16
                info.comment = b"member comment"
                info.extra = b"\xfe\xca\x02\x00ok"
                stream.writestr(info, content)
    os.chmod(path, 0o640)
    with ArchiveReaderContext(path) as reader:
        entries = list(reader.images)
    return path, entries


class ArchiveReaderContext:
    def __init__(self, path):
        self.reader = ArchiveReader(path, ArchiveLimits())

    def __enter__(self):
        return self.reader

    def __exit__(self, *args):
        self.reader.close()


def snapshots(path):
    with zipfile.ZipFile(path) as stream:
        return [(copy.copy(info), stream.read(info)) for info in stream.infolist()]


def assert_no_temporary(path):
    assert list(path.parent.glob(f".{path.name}.qtvw-*.tmp")) == []


def test_delete_exact_duplicate_preserve_payload_metadata_and_mode(tmp_path):
    path, entries = fixture_zip(tmp_path)
    before = snapshots(path)
    previous_fingerprint = path.stat().st_size, path.stat().st_mtime_ns
    progress = []
    result = archive_edit.delete_images(
        path,
        [next(entry for entry in entries if entry.member_index == 0)],
        progress=lambda *args: progress.append(args),
    )
    assert result.removed == 1
    assert {"", "旅行", "empty"} <= result.folders
    after = snapshots(path)
    assert len(after) == len(before) - 1
    for (old, data), (new, actual) in zip(before[1:], after):
        assert actual == data
        for attribute in (
            "filename",
            "date_time",
            "external_attr",
            "comment",
            "extra",
            "compress_type",
        ):
            assert getattr(old, attribute) == getattr(new, attribute)
    with zipfile.ZipFile(path) as stream:
        assert stream.comment == b"album comment"
        assert stream.testzip() is None
    assert stat.S_IMODE(path.stat().st_mode) == 0o640
    assert (path.stat().st_size, path.stat().st_mtime_ns) != previous_fingerprint
    assert any("Verifying" in label for _, label in progress)
    assert [percent for percent, _ in progress] == sorted(percent for percent, _ in progress)
    assert_no_temporary(path)


def test_delete_every_image_and_empty_zip(tmp_path):
    path = tmp_path / "all.zip"
    with zipfile.ZipFile(path, "w") as stream:
        stream.writestr("one/two/photo.png", b"pixels")
    with ArchiveReaderContext(path) as reader:
        entries = reader.images
    result = archive_edit.delete_images(path, entries)
    assert result.folders == frozenset({""})
    with zipfile.ZipFile(path) as stream:
        assert stream.namelist() == []
        assert stream.testzip() is None


@pytest.mark.parametrize("phase", ["before", "rewrite", "verify"])
def test_cancellation_preserves_original_and_removes_temporary(tmp_path, phase):
    path, entries = fixture_zip(tmp_path)
    before = path.read_bytes()
    token = Event()
    if phase == "before":
        token.set()

    def progress(percent, label):
        if (phase == "rewrite" and percent > 0) or (phase == "verify" and "Verifying" in label):
            token.set()

    with pytest.raises(Cancelled):
        archive_edit.delete_images(path, entries[:1], token, progress)
    assert path.read_bytes() == before
    assert_no_temporary(path)


@pytest.mark.parametrize("failure", ["write", "verify", "replace"])
def test_io_or_validation_failure_preserves_original(tmp_path, monkeypatch, failure):
    path, entries = fixture_zip(tmp_path)
    before = path.read_bytes()
    if failure == "write":

        def fail(*args):
            raise OSError("No space left on device")

        monkeypatch.setattr(archive_edit.os, "fsync", fail)
    elif failure == "replace":

        def fail(*args):
            raise PermissionError("Cannot replace archive")

        monkeypatch.setattr(archive_edit.os, "replace", fail)
    else:

        def corrupt(percent, label):
            if "Verifying" in label:
                temporary = next(path.parent.glob(f".{path.name}.qtvw-*.tmp"))
                temporary.write_bytes(b"broken")

        monkeypatch.setattr(archive_edit, "_test_progress", corrupt, raising=False)
    with pytest.raises((OSError, ValueError, zipfile.BadZipFile)):
        archive_edit.delete_images(
            path, entries[:1], progress=getattr(archive_edit, "_test_progress", None)
        )
    assert path.read_bytes() == before
    assert_no_temporary(path)


def test_external_change_during_rewrite_is_not_overwritten(tmp_path):
    path, entries = fixture_zip(tmp_path)
    external = b"externally replaced content"
    changed = False

    def progress(percent, label):
        nonlocal changed
        if percent > 0 and not changed:
            changed = True
            path.write_bytes(external)

    with pytest.raises((SourceChanged, zipfile.BadZipFile)):
        archive_edit.delete_images(path, entries[:1], progress=progress)
    assert path.read_bytes() == external
    assert_no_temporary(path)


@pytest.mark.parametrize(
    "invalid", ["stale", "wrong_crc", "folder", "empty", "permission", "limit", "link"]
)
def test_reject_invalid_or_unwritable_selection(tmp_path, monkeypatch, invalid):
    path, entries = fixture_zip(tmp_path)
    before = path.read_bytes()
    selection = entries[:1]
    limits = None
    if invalid == "stale":
        selection = [replace(entries[0], archive_fingerprint=(0, 0))]
    elif invalid == "wrong_crc":
        selection = [replace(entries[0], crc=entries[0].crc ^ 1)]
    elif invalid == "folder":
        selection = [replace(entries[0], kind="folder")]
    elif invalid == "empty":
        selection = []
    elif invalid == "permission":
        monkeypatch.setattr(archive_edit.os, "access", lambda *args: False)
    elif invalid == "limit":
        limits = ArchiveLimits(max_entry_bytes=1)
    else:
        alias = tmp_path / "alias.zip"
        alias.symlink_to(path)
        path = alias
    with pytest.raises((ValueError, PermissionError)):
        archive_edit.delete_images(path, selection, limits=limits)
    assert path.read_bytes() == before
    assert_no_temporary(path)


def test_encrypted_member_rejected_without_modification(tmp_path):
    path, entries = fixture_zip(tmp_path)
    data = bytearray(path.read_bytes())
    position = data.find(b"PK\x01\x02")
    flags = struct.unpack_from("<H", data, position + 8)[0]
    struct.pack_into("<H", data, position + 8, flags | 1)
    path.write_bytes(data)
    selection = [replace(entries[0], archive_fingerprint=(len(data), path.stat().st_mtime_ns))]
    with pytest.raises(ValueError, match="encrypted"):
        archive_edit.delete_images(path, selection)
    assert path.read_bytes() == data
    assert_no_temporary(path)


def test_rewrite_retained_corrupt_payload_aborts(tmp_path):
    path, entries = fixture_zip(tmp_path)
    with zipfile.ZipFile(path) as stream:
        retained = stream.infolist()[3]
        offset = retained.header_offset + 30 + len(retained.filename.encode()) + len(retained.extra)
    data = bytearray(path.read_bytes())
    data[offset] ^= 0xFF
    path.write_bytes(data)
    selected = [replace(entries[0], archive_fingerprint=(len(data), path.stat().st_mtime_ns))]
    with pytest.raises((zipfile.BadZipFile, zlib.error)):
        archive_edit.delete_images(path, selected)
    assert path.read_bytes() == data
    assert_no_temporary(path)


def test_zip64_rewrite(tmp_path, monkeypatch):
    path = tmp_path / "zip64.zip"
    with monkeypatch.context() as patch:
        patch.setattr(zipfile, "ZIP64_LIMIT", 1)
        with zipfile.ZipFile(path, "w") as stream:
            stream.writestr("remove.png", b"remove")
            stream.writestr("keep.png", b"keep")
    with ArchiveReaderContext(path) as reader:
        selection = [entry for entry in reader.images if entry.name == "remove.png"]
    archive_edit.delete_images(path, selection)
    with zipfile.ZipFile(path) as stream:
        assert stream.namelist() == ["keep.png"]
        assert stream.read("keep.png") == b"keep"
        assert stream.testzip() is None


def test_concurrent_editor_lock_preserves_original(tmp_path):
    path, entries = fixture_zip(tmp_path)
    before = path.read_bytes()
    with path.open("rb") as other_editor:
        fcntl.flock(other_editor.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="already being edited"):
            archive_edit.delete_images(path, entries[:1])
    assert path.read_bytes() == before
    assert_no_temporary(path)


def test_source_cache_invalidation_rejects_old_jobs_and_keeps_other_disk_entries(
    tmp_path, image_bytes
):
    from PySide6.QtGui import QImage

    from qtvw.cache import CacheStore
    from qtvw.sources.archive import fingerprint

    path, _ = fixture_zip(tmp_path)
    other = tmp_path / "other.png"
    other.write_bytes(image_bytes())
    cache = CacheStore(root=tmp_path / "cache")
    try:
        epoch = cache.epoch
        cache.index_put(path, fingerprint(path), {}, [], epoch)
        cache.thumbnail_put("a" * 64, path, fingerprint(path), b"thumbnail", epoch)
        cache.thumbnail_put("b" * 64, other, fingerprint(other), b"other thumbnail", epoch)
        cache.memory_put("image", QImage.fromData(image_bytes()), epoch, full=True)
        assert cache.index_get(path, fingerprint(path), {}) == []
        cache.invalidate_source(path)
        assert cache.epoch > epoch
        assert cache.index_get(path, fingerprint(path), {}) is None
        assert cache.thumbnail_get("a" * 64, path, fingerprint(path)) is None
        assert cache.thumbnail_get("b" * 64, other, fingerprint(other)) == b"other thumbnail"
        assert cache.memory_get("image", full=True) is None
        cache.thumbnail_put("a" * 64, path, fingerprint(path), b"stale", epoch)
        cache.memory_put("image", QImage.fromData(image_bytes()), epoch, full=True)
        assert cache.thumbnail_get("a" * 64, path, fingerprint(path)) is None
        assert cache.memory_get("image", full=True) is None
    finally:
        cache.close()
