"""ZIP moves preserve payloads and reject conflicts or unsafe sources."""

import fcntl
import os
import stat
import tempfile
import zipfile
from pathlib import Path

import pytest

from qtvw.domain.locations import ArchiveLocation
from qtvw.services import file_moves
from qtvw.services.navigation import Navigation


def archive(tmp_path, name="旅行 album.zip"):
    source = tmp_path / name
    with zipfile.ZipFile(source, "w") as stream:
        stream.writestr("one/photo.png", b"image bytes")
        stream.comment = b"unchanged archive"
    source.chmod(0o640)
    return source


def test_move_preserves_exact_bytes_and_permissions(tmp_path):
    source = archive(tmp_path)
    before = source.read_bytes()
    destination = tmp_path / "target"
    destination.mkdir()
    old, new = file_moves.move_zip(source, destination)
    assert old == source
    assert new == destination / source.name
    assert not source.exists()
    assert new.read_bytes() == before
    assert stat.S_IMODE(new.stat().st_mode) == 0o640


@pytest.mark.parametrize("conflict", ["file", "folder", "symlink"])
def test_existing_destination_is_never_overwritten(tmp_path, conflict):
    source = archive(tmp_path)
    before = source.read_bytes()
    target = tmp_path / "target"
    target.mkdir()
    collision = target / source.name
    if conflict == "file":
        collision.write_bytes(b"keep destination")
    elif conflict == "folder":
        collision.mkdir()
    else:
        collision.symlink_to(target / "missing")
    with pytest.raises(FileExistsError):
        file_moves.move_zip(source, target)
    assert source.read_bytes() == before
    assert collision.is_symlink() if conflict == "symlink" else collision.exists()
    if conflict == "file":
        assert collision.read_bytes() == b"keep destination"


@pytest.mark.parametrize(
    "invalid", ["same_folder", "symlink", "directory", "image", "missing_target"]
)
def test_invalid_source_and_target_leave_files_untouched(tmp_path, invalid):
    source = archive(tmp_path)
    before = source.read_bytes()
    target = tmp_path / "target"
    target.mkdir()
    actual = source
    if invalid == "same_folder":
        target = tmp_path
    elif invalid == "symlink":
        actual = tmp_path / "alias.zip"
        actual.symlink_to(source)
    elif invalid == "directory":
        actual = tmp_path / "folder.zip"
        actual.mkdir()
    elif invalid == "image":
        actual = tmp_path / "image.png"
        actual.write_bytes(b"image")
    else:
        target = tmp_path / "missing"
    with pytest.raises((ValueError, OSError)):
        file_moves.move_zip(actual, target)
    assert source.read_bytes() == before


def test_archive_edit_lock_blocks_move(tmp_path):
    source = archive(tmp_path)
    target = tmp_path / "target"
    target.mkdir()
    with source.open("rb") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="being edited or moved"):
            file_moves.move_zip(source, target)
    assert source.exists()
    assert not (target / source.name).exists()


@pytest.mark.parametrize("late_conflict", [False, True])
def test_qfile_failure_and_late_destination_conflict_keep_original(
    tmp_path, monkeypatch, late_conflict
):
    source = archive(tmp_path)
    before = source.read_bytes()
    target = tmp_path / "target"
    target.mkdir()
    real_qfile = file_moves.QFile

    class FailingFile:
        def __init__(self, path):
            self.file = real_qfile(path)

        def rename(self, destination):
            if late_conflict:
                Path(destination).write_bytes(b"created after preflight")
                return self.file.rename(destination)
            return False

        def errorString(self):
            return "Permission denied"

    monkeypatch.setattr(file_moves, "QFile", FailingFile)
    with pytest.raises(OSError):
        file_moves.move_zip(source, target)
    assert source.read_bytes() == before
    if late_conflict:
        assert (target / source.name).read_bytes() == b"created after preflight"
    else:
        assert not (target / source.name).exists()


def test_move_archive_history_updates_every_internal_location(tmp_path):
    source = tmp_path / "source.zip"
    destination = tmp_path / "elsewhere/source.zip"
    navigation = Navigation()
    navigation.navigate(tmp_path)
    navigation.navigate(ArchiveLocation(source))
    navigation.navigate(ArchiveLocation(source, "one/two"))
    navigation.relocate_archive(source, destination)
    assert navigation.current == ArchiveLocation(destination, "one/two")
    assert navigation.back() == ArchiveLocation(destination)
    assert navigation.back() == tmp_path
    assert navigation.forward() == ArchiveLocation(destination)


def test_cross_filesystem_move_when_available(tmp_path):
    mount = Path("/dev/shm")
    if (
        not mount.is_dir()
        or not os.access(mount, os.W_OK)
        or mount.stat().st_dev == tmp_path.stat().st_dev
    ):
        pytest.skip("A second writable filesystem is required")
    source = archive(tmp_path)
    before = source.read_bytes()
    with tempfile.TemporaryDirectory(prefix="qtvw-move-", dir=mount) as directory:
        _, destination = file_moves.move_zip(source, directory)
        assert not source.exists()
        assert destination.read_bytes() == before
