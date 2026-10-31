"""Filesystem folder renames preserve contents and refuse conflicting paths."""

from pathlib import Path

import pytest

from qtvw.services.folder_delete import folder_identity
from qtvw.services.folder_rename import rename_folder


@pytest.mark.parametrize(
    "name", ["", " ", ".", "..", "../escape", "/absolute", "one/two", "one\\two", "bad\x00name"]
)
def test_rename_rejects_paths_and_blank_names(tmp_path, name):
    source = tmp_path / "original"
    source.mkdir()
    with pytest.raises(ValueError):
        rename_folder(source, name, folder_identity(source))
    assert list(tmp_path.iterdir()) == [source]


@pytest.mark.parametrize("kind", ["empty_folder", "folder", "file", "broken_link"])
def test_rename_never_replaces_existing_entries(tmp_path, kind):
    source = tmp_path / "original"
    source.mkdir()
    (source / "photo.png").write_bytes(b"keep source")
    destination = tmp_path / "existing"
    if kind in {"empty_folder", "folder"}:
        destination.mkdir()
        if kind == "folder":
            (destination / "keep.txt").write_text("keep destination")
    elif kind == "file":
        destination.write_text("keep destination")
    else:
        destination.symlink_to(tmp_path / "missing")
    with pytest.raises(OSError):
        rename_folder(source, destination.name, folder_identity(source))
    assert (source / "photo.png").read_bytes() == b"keep source"
    if kind == "folder":
        assert (destination / "keep.txt").read_text() == "keep destination"
    elif kind == "file":
        assert destination.read_text() == "keep destination"
    elif kind == "broken_link":
        assert destination.is_symlink()
    else:
        assert list(destination.iterdir()) == []


def test_rename_rejects_replaced_folder_links_and_root(tmp_path):
    source = tmp_path / "original"
    source.mkdir()
    identity = folder_identity(source)
    source.rename(tmp_path / "moved")
    source.mkdir()
    with pytest.raises(ValueError, match="changed"):
        rename_folder(source, "new", identity)
    source.rmdir()
    source.symlink_to(tmp_path / "moved", target_is_directory=True)
    with pytest.raises(ValueError):
        rename_folder(source, "new", identity)
    with pytest.raises(ValueError):
        rename_folder(Path(tmp_path.anchor), "new", identity)
    assert not (tmp_path / "new").exists()
