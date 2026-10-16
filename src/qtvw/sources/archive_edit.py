"""Transactional, bounded-memory image removal from an existing ZIP archive."""

import copy
import fcntl
import logging
import os
import stat
import tempfile
import time
import warnings
import zipfile
from dataclasses import dataclass
from pathlib import Path

from qtvw.domain import IMAGE_EXTENSIONS
from qtvw.domain.locations import member_path
from qtvw.sources.archive import ArchiveLimits, SourceChanged, check_cancel, preflight

BLOCK_BYTES = 64 * 1024


@dataclass(frozen=True)
class DeleteResult:
    path: Path
    removed: int
    folders: frozenset[str]


def _signature(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _read_checked(stream, size, token, advance):
    total = 0
    while True:
        check_cancel(token)
        block = stream.read(BLOCK_BYTES)
        if not block:
            break
        total += len(block)
        if total > size:
            raise ValueError("ZIP member exceeds its declared size")
        advance(len(block))
        yield block
    if total != size:
        raise ValueError("ZIP member size does not match its directory entry")


def delete_images(path, entries, token=None, progress=None, limits=None):
    """Stream retained members into a verified ZIP, then atomically replace the source."""
    path = Path(path)
    limits = limits or ArchiveLimits()
    progress = progress or (lambda percent, label: None)
    check_cancel(token)
    original = path.lstat()
    if not stat.S_ISREG(original.st_mode) or path.is_symlink():
        raise ValueError(
            "Only regular ZIP files can be edited; open the original instead of a link"
        )
    if not os.access(path, os.W_OK) or not os.access(path.parent, os.W_OK):
        raise PermissionError("The ZIP archive and its containing folder must be writable")
    expected = original.st_size, original.st_mtime_ns
    if not entries:
        raise ValueError("Select one or more images inside this ZIP")
    selected = {}
    for entry in entries:
        if (
            entry.path != path
            or entry.kind != "image"
            or entry.member is None
            or entry.member_index is None
            or entry.archive_fingerprint != expected
        ):
            raise SourceChanged(
                "The selection is stale; refresh the ZIP and select the images again"
            )
        selected[entry.member_index] = entry
    preflight(path, limits)
    temporary = None
    last_percent = -1
    completed = 0

    def report(amount=0, label="Rewriting ZIP…"):
        nonlocal completed, last_percent
        completed += amount
        percent = min(99, int(completed * 100 / max(1, total_work)))
        if percent != last_percent or amount == 0:
            last_percent = percent
            progress(percent, label)

    try:
        with path.open("rb") as original_stream:
            try:
                fcntl.flock(original_stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise ValueError("This ZIP is already being edited; wait and try again") from error
            if _signature(os.fstat(original_stream.fileno())) != _signature(original):
                raise SourceChanged("Archive changed before deletion; refresh and try again")
            with zipfile.ZipFile(original_stream) as source:
                infos = source.infolist()
                if any(info.flag_bits & 1 for info in infos):
                    raise ValueError("Deleting from encrypted ZIP archives is unsupported")
                for position, entry in selected.items():
                    if not 0 <= position < len(infos):
                        raise SourceChanged("Archive member changed; refresh and try again")
                    info = infos[position]
                    if (
                        (info.filename, info.CRC, info.file_size)
                        != (entry.member, entry.crc, entry.size)
                        or info.is_dir()
                        or Path(info.filename).suffix.lower() not in IMAGE_EXTENSIONS
                        or stat.S_ISLNK(info.external_attr >> 16)
                        or member_path(info.filename) != entry.member
                    ):
                        raise SourceChanged("Archive member changed; refresh and try again")
                retained = [info for index, info in enumerate(infos) if index not in selected]
                if any(info.file_size > limits.max_entry_bytes for info in retained):
                    raise ValueError("A retained ZIP entry exceeds the decompressed byte limit")
                total_work = 2 * sum(info.file_size for info in retained)
                report()
                descriptor, filename = tempfile.mkstemp(
                    prefix=f".{path.name}.qtvw-", suffix=".tmp", dir=path.parent
                )
                temporary = Path(filename)
                with os.fdopen(descriptor, "w+b") as output:
                    with zipfile.ZipFile(output, "w", allowZip64=True) as destination:
                        destination.comment = source.comment
                        for info in retained:
                            check_cancel(token)
                            with warnings.catch_warnings():
                                warnings.filterwarnings("ignore", message="Duplicate name:")
                                with (
                                    source.open(info) as reader,
                                    destination.open(copy.copy(info), "w") as writer,
                                ):
                                    for block in _read_checked(
                                        reader, info.file_size, token, report
                                    ):
                                        writer.write(block)
                    output.flush()
                    os.fsync(output.fileno())
            check_cancel(token)
            progress(last_percent if last_percent >= 0 else 0, "Verifying rebuilt ZIP…")
            with zipfile.ZipFile(temporary) as rebuilt:
                actual = rebuilt.infolist()
                if len(actual) != len(retained) or rebuilt.comment != source.comment:
                    raise ValueError("Rebuilt ZIP verification failed")
                folders = {""}
                for old, new in zip(retained, actual):
                    if (old.filename, old.CRC, old.file_size, old.compress_type) != (
                        new.filename,
                        new.CRC,
                        new.file_size,
                        new.compress_type,
                    ):
                        raise ValueError("Rebuilt ZIP member verification failed")
                    with rebuilt.open(new) as reader:
                        for _ in _read_checked(
                            reader,
                            new.file_size,
                            token,
                            lambda amount: report(amount, "Verifying rebuilt ZIP…"),
                        ):
                            pass
                    try:
                        name = member_path(new.filename)
                        parts = name.split("/")
                        folders.update(
                            "/".join(parts[:end])
                            for end in range(1, len(parts) + (1 if new.is_dir() else 0))
                        )
                    except ValueError:
                        pass
            os.chmod(temporary, stat.S_IMODE(original.st_mode))
            modified = max(time.time_ns(), original.st_mtime_ns + 1)
            os.utime(temporary, ns=(modified, modified))
            check_cancel(token)
            if _signature(path.lstat()) != _signature(original):
                raise SourceChanged(
                    "Archive changed during deletion; your changes were not applied"
                )
            if _signature(os.fstat(original_stream.fileno())) != _signature(original):
                raise SourceChanged(
                    "Archive changed during deletion; your changes were not applied"
                )
            os.replace(temporary, path)
            temporary = None
        logging.getLogger(__name__).info("Deleted %s images from ZIP: %s", len(selected), path)
        return DeleteResult(path, len(selected), frozenset(folders))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
