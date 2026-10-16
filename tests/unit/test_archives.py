"""ZIP fixtures exercise indexing, identity, safety limits, and direct reads."""

import struct
import warnings
import zipfile
from threading import Event

import pytest

from qtvw.domain.locations import ArchiveLocation, restore_location, save_location
from qtvw.services import Navigation
from qtvw.sources.archive import ArchiveLimits, ArchiveReader, Cancelled


def write_zip(path, members):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for name, content in members:
                archive.writestr(name, content)
    return path


def test_index_implicit_folders_unicode_hidden_and_duplicates(tmp_path, image_bytes):
    path = write_zip(
        tmp_path / "photos.zip",
        [
            ("旅行/日1/photo2.png", image_bytes()),
            ("旅行/日1/photo2.png", image_bytes("blue")),
            ("empty/", b""),
            (".hidden.png", image_bytes()),
            ("nested.zip", b"ZIP"),
            ("notes.txt", b"notes"),
            ("../escape.png", image_bytes()),
            ("/absolute.png", image_bytes()),
            ("C:/drive.png", image_bytes()),
            ("back\\slash.png", image_bytes()),
        ],
    )
    reader = ArchiveReader(path, ArchiveLimits())
    try:
        assert {e.name for e in reader.listing("")} == {"旅行", "empty", "nested.zip"}
        assert ".hidden.png" in {e.name for e in reader.listing("", True)}
        assert [e.name for e in reader.listing("旅行")] == ["日1"]
        images = reader.listing("旅行/日1")
        assert len(images) == 2
        assert images[0].identity != images[1].identity
        assert reader.read(images[0]) == image_bytes()
        assert reader.read(images[1]) == image_bytes("blue")
        assert reader.listing("empty") == []
        with pytest.raises(ValueError, match="no longer exists"):
            reader.listing("missing")
    finally:
        reader.close()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["photos.zip"]


def test_location_history_and_settings_roundtrip(tmp_path):
    root = ArchiveLocation(tmp_path / "Photos.zip")
    child = ArchiveLocation(root.path, "旅行/日1")
    assert restore_location(save_location(child)) == child
    assert restore_location(save_location(tmp_path)) == tmp_path
    assert str(child).endswith("Photos.zip › 旅行/日1")
    navigation = Navigation()
    navigation.navigate(tmp_path)
    navigation.navigate(root)
    navigation.navigate(child)
    assert navigation.up() == ArchiveLocation(root.path, "旅行")
    assert navigation.up() == root
    assert navigation.up() == tmp_path
    assert navigation.back() == root
    assert navigation.forward() == tmp_path
    with pytest.raises(ValueError, match="Unsafe"):
        ArchiveLocation(root.path, "../outside")


def test_empty_corrupt_and_cancelled_archives(tmp_path):
    path = write_zip(tmp_path / "empty.zip", [])
    reader = ArchiveReader(path, ArchiveLimits())
    assert reader.listing("") == []
    assert reader.candidates() == []
    reader.close()
    token = Event()
    token.set()
    with pytest.raises(Cancelled):
        ArchiveReader(path, ArchiveLimits(), token)
    path.write_bytes(b"not a zip")
    with pytest.raises(ValueError, match="ZIP"):
        ArchiveReader(path, ArchiveLimits())


def test_zip64_index(tmp_path, monkeypatch, image_bytes):
    # Force genuine ZIP64 central-directory/end records with a small fixture.
    with monkeypatch.context() as patch:
        patch.setattr(zipfile, "ZIP64_LIMIT", 1)
        path = write_zip(tmp_path / "zip64.zip", [("photo.png", image_bytes())])
    assert b"PK\x06\x06" in path.read_bytes()
    reader = ArchiveReader(path, ArchiveLimits())
    try:
        assert reader.read(reader.images[0]) == image_bytes()
    finally:
        reader.close()


def test_preflight_bounds_before_zipfile_allocation(tmp_path, monkeypatch):
    path = write_zip(tmp_path / "many.zip", [("one.png", b"x"), ("two.png", b"x")])

    def unexpected(*args, **kwargs):
        pytest.fail("ZipFile must not allocate the central directory before preflight")

    monkeypatch.setattr(zipfile, "ZipFile", unexpected)
    with pytest.raises(ValueError, match="limit"):
        ArchiveReader(path, ArchiveLimits(max_entries=1))
    with pytest.raises(ValueError, match="limit"):
        ArchiveReader(path, ArchiveLimits(max_directory_bytes=1))


def test_member_limits_changed_archive_and_deep_paths(tmp_path, image_bytes):
    path = write_zip(tmp_path / "photos.zip", [("a/b/photo.png", image_bytes())])
    with pytest.raises(ValueError, match="depth"):
        ArchiveReader(path, ArchiveLimits(max_path_depth=2))
    with pytest.raises(ValueError, match="folder-count"):
        ArchiveReader(path, ArchiveLimits(max_folders=1))
    reader = ArchiveReader(path, ArchiveLimits(max_entry_bytes=10))
    try:
        with pytest.raises(ValueError, match="byte limit"):
            reader.read(reader.images[0])
        with path.open("ab") as stream:
            stream.write(b"changed")
        with pytest.raises(ValueError, match="changed"):
            reader.listing("")
    finally:
        reader.close()


def test_encrypted_archive_explained(tmp_path):
    path = write_zip(tmp_path / "encrypted.zip", [("photo.png", b"x")])
    data = bytearray(path.read_bytes())
    for signature, flag_offset in [(b"PK\x03\x04", 6), (b"PK\x01\x02", 8)]:
        offset = data.index(signature) + flag_offset
        flags = struct.unpack_from("<H", data, offset)[0]
        struct.pack_into("<H", data, offset, flags | 1)
    path.write_bytes(data)
    with pytest.raises(ValueError, match="Password-protected"):
        ArchiveReader(path, ArchiveLimits())
