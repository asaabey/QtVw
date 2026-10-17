"""Real generated RAR4 fixtures exercise streaming conversion and failure cleanup."""

import zipfile
from threading import Event

import pytest
import rarfile

from qtvw.services.rar_conversion import ConversionCancelled, convert_rar


def test_convert_real_rar_preserves_content_and_original(tmp_path, make_rar):
    source = make_rar(
        tmp_path / "album.rar", [("nested/photo.png", b"picture"), ("notes.txt", b"text")]
    )
    original = source.read_bytes()
    updates = []
    destination = convert_rar(source, Event(), lambda *args: updates.append(args))
    with zipfile.ZipFile(destination) as archive:
        assert archive.namelist() == ["nested/photo.png", "notes.txt"]
        assert archive.read("nested/photo.png") == b"picture"
        assert archive.read("notes.txt") == b"text"
        assert archive.testzip() is None
    assert source.read_bytes() == original
    assert updates
    assert not list(tmp_path.glob(".qtvw-convert-*"))


@pytest.mark.parametrize(
    "failure", ["cancel", "limit", "unsafe", "corrupt", "conflict", "late_conflict", "changed"]
)
def test_conversion_failures_leave_original_and_no_partial_zip(tmp_path, make_rar, failure):
    name = "../escape" if failure == "unsafe" else "photo.png"
    source = make_rar(tmp_path / "album.rar", [(name, b"payload")])
    destination = source.with_suffix(".zip")
    token = Event()
    if failure == "cancel":
        token.set()
    if failure == "corrupt":
        data = source.read_bytes().replace(b"payload", b"corrupt")
        source.write_bytes(data)
    if failure == "conflict":
        destination.write_bytes(b"keep")
    original = source.read_bytes()

    def progress(percent, label):
        if failure == "late_conflict":
            destination.write_bytes(b"keep")
        if failure == "changed":
            source.touch()

    with pytest.raises((ValueError, OSError, ConversionCancelled, rarfile.Error)):
        convert_rar(source, token, progress, max_entry_bytes=1 if failure == "limit" else 1024)
    assert source.read_bytes() == original
    if failure in {"conflict", "late_conflict"}:
        assert destination.read_bytes() == b"keep"
    else:
        assert not destination.exists()
    assert not list(tmp_path.glob(".qtvw-convert-*"))


def test_cancel_during_copy(tmp_path, make_rar):
    source = make_rar(tmp_path / "album.rar", [("photo.png", b"x" * 600000)])
    token = Event()
    with pytest.raises(ConversionCancelled):
        convert_rar(source, token, lambda *_: token.set())
    assert not source.with_suffix(".zip").exists()
    assert not list(tmp_path.glob(".qtvw-convert-*"))


@pytest.mark.parametrize("unsupported", ["password", "volumes", "missing_tool"])
def test_unsupported_archives_report_errors(tmp_path, make_rar, monkeypatch, unsupported):
    source = make_rar(tmp_path / "album.rar", [("photo.png", b"x")])
    if unsupported == "password":
        monkeypatch.setattr(rarfile.RarFile, "needs_password", lambda self: True)
        match = "Password"
    elif unsupported == "volumes":
        monkeypatch.setattr(rarfile.RarFile, "volumelist", lambda self: ["one", "two"])
        match = "Multi-volume"
    else:

        def missing(*args):
            raise rarfile.RarCannotExec("missing")

        monkeypatch.setattr(rarfile.RarFile, "open", missing)
        match = "Install a RAR extractor"
    with pytest.raises(ValueError, match=match):
        convert_rar(source, Event(), lambda *_: None)
    assert not source.with_suffix(".zip").exists()
    assert not list(tmp_path.glob(".qtvw-convert-*"))


def test_empty_rar_produces_valid_empty_zip(tmp_path, make_rar):
    source = make_rar(tmp_path / "empty.rar", [])
    result = convert_rar(source, Event(), lambda *_: None)
    with zipfile.ZipFile(result) as archive:
        assert archive.namelist() == []


def test_duplicate_names_and_symlink_source_are_rejected(tmp_path, make_rar):
    source = make_rar(tmp_path / "album.rar", [("photo.png", b"a"), ("photo.png", b"b")])
    with pytest.raises(ValueError, match="duplicate"):
        convert_rar(source, Event(), lambda *_: None)
    link = tmp_path / "link.rar"
    link.symlink_to(source)
    with pytest.raises(ValueError, match="symbolic"):
        convert_rar(link, Event(), lambda *_: None)
    assert not source.with_suffix(".zip").exists()


def test_compressed_rar_requires_unrar(tmp_path, make_rar, monkeypatch):
    source = make_rar(tmp_path / "album.rar", [("photo.png", b"x")])
    original = rarfile.RarFile.infolist

    def compressed(self):
        members = original(self)
        members[0].compress_type = rarfile.RAR_M3
        return members

    def missing(**options):
        assert options["bsdtar"] is False
        raise rarfile.RarCannotExec("No unrar")

    monkeypatch.setattr(rarfile.RarFile, "infolist", compressed)
    monkeypatch.setattr(rarfile, "tool_setup", missing)
    with pytest.raises(ValueError, match="Install a RAR extractor"):
        convert_rar(source, Event(), lambda *_: None)
    assert not source.with_suffix(".zip").exists()
