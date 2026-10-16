"""Bounded ZIP indexing and direct member reads. Nothing is extracted to disk."""

import stat
import struct
import threading
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath

from qtvw.domain import IMAGE_EXTENSIONS, Entry, natural_key
from qtvw.domain.locations import member_path


@dataclass(frozen=True)
class ArchiveLimits:
    max_entries: int = 50_000
    max_directory_bytes: int = 16 * 1024 * 1024
    max_entry_bytes: int = 64 * 1024 * 1024
    max_cover_bytes: int = 128 * 1024 * 1024
    max_cover_candidates: int = 256
    max_path_depth: int = 64
    max_path_chars: int = 4096
    max_folders: int = 50_000


class SourceChanged(ValueError):
    pass


class Cancelled(Exception):
    pass


def check_cancel(token):
    if token is not None and token.is_set():
        raise Cancelled()


def fingerprint(path: Path) -> tuple[int, int]:
    info = path.stat()
    return info.st_size, info.st_mtime_ns


def preflight(path: Path, limits: ArchiveLimits):
    """Bound central-directory allocation before ZipFile builds its index."""
    with path.open("rb") as stream:
        stream.seek(0, 2)
        total = stream.tell()
        tail_size = min(total, 65535 + 22)
        stream.seek(total - tail_size)
        tail = stream.read(tail_size)
        offset = tail.rfind(b"PK\x05\x06")
        if offset < 0 or len(tail) - offset < 22:
            raise ValueError("Damaged or unsupported ZIP: end record is missing")
        _, disk, directory_disk, disk_count, count, size, _, comment = struct.unpack_from(
            "<4s4H2LH", tail, offset
        )
        if offset + 22 + comment != len(tail):
            raise ValueError("Damaged or unsupported ZIP end record")
        end_offset = total - tail_size + offset
        if end_offset >= 20:
            stream.seek(end_offset - 20)
            locator = stream.read(20)
            if locator[:4] == b"PK\x06\x07":
                _, zip64_disk, zip64_offset, disks = struct.unpack("<4sLQL", locator)
                if zip64_disk or disks != 1:
                    raise ValueError("Multi-disk ZIP archives are unsupported")
                stream.seek(zip64_offset)
                record = stream.read(56)
                if len(record) != 56 or record[:4] != b"PK\x06\x06":
                    raise ValueError("Damaged or unsupported ZIP64 end record")
                fields = struct.unpack("<4sQ2H2L4Q", record)
                disk, directory_disk, disk_count, count, size = fields[4:9]
        if disk or directory_disk or disk_count != count:
            raise ValueError("Multi-disk ZIP archives are unsupported")
        if count > limits.max_entries or size > limits.max_directory_bytes:
            raise ValueError("Archive index exceeds the entry-count or directory-size limit")


class ArchiveIndex:
    """Serializable browser index, usable without opening/enumerating the ZIP."""

    @classmethod
    def restore(cls, path, source_fingerprint, records, limits):
        if not isinstance(records, list) or len(records) > limits.max_entries + limits.max_folders:
            raise ValueError("Damaged archive cache")
        index = cls()
        index.path = path
        index.fingerprint = source_fingerprint
        index.children = {"": []}
        index.images = []
        folders = set()
        members = set()
        for record in records:
            if not isinstance(record, list) or len(record) != 6:
                raise ValueError("Damaged archive cache")
            member, kind, size, modified, position, crc = record
            if (
                not isinstance(member, str)
                or member_path(member) != member
                or not member
                or len(member) > limits.max_path_chars
                or len(member.split("/")) > limits.max_path_depth
                or kind not in {"folder", "image", "archive"}
                or any(type(value) is not int for value in (size, modified, crc))
                or size < 0
                or not 0 <= crc <= 0xFFFFFFFF
            ):
                raise ValueError("Damaged archive cache")
            if kind == "folder":
                if position is not None or member in folders:
                    raise ValueError("Damaged archive cache")
                folders.add(member)
                index.children.setdefault(member, [])
            else:
                if (
                    type(position) is not int
                    or not 0 <= position < limits.max_entries
                    or position in members
                ):
                    raise ValueError("Damaged archive cache")
                members.add(position)
                suffix = PurePosixPath(member).suffix.lower()
                if (kind == "image" and suffix not in IMAGE_EXTENSIONS) or (
                    kind == "archive" and suffix != ".zip"
                ):
                    raise ValueError("Damaged archive cache")
            entry = Entry(path, kind, size, modified, member, position, crc, source_fingerprint)
            index.children.setdefault(member.rpartition("/")[0], []).append(entry)
            if kind == "image":
                index.images.append(entry)
        if len(folders) > limits.max_folders or any(
            parent and parent not in folders for parent in index.children
        ):
            raise ValueError("Damaged archive cache")
        index.images.sort(key=lambda entry: (natural_key(entry.member), entry.member_index))
        index.verify()
        return index

    def records(self):
        return [
            [entry.member, entry.kind, entry.size, entry.modified_ns, entry.member_index, entry.crc]
            for children in self.children.values()
            for entry in children
        ]

    def verify(self):
        if fingerprint(self.path) != self.fingerprint:
            raise SourceChanged("Archive changed while being viewed; refresh to reload")

    def listing(self, internal: str, show_hidden=False):
        self.verify()
        if internal not in self.children:
            raise ValueError("Archive folder no longer exists; refresh or move up")
        return [
            entry
            for entry in self.children[internal]
            if show_hidden or not entry.name.startswith(".")
        ]

    def candidates(self):
        return sorted(
            self.images,
            key=lambda entry: (
                PurePosixPath(entry.member).stem.casefold() != "cover",
                natural_key(entry.member),
                entry.member_index,
            ),
        )


class ArchiveReader(ArchiveIndex):
    """One reader belongs to one worker thread and holds one bounded index."""

    def __init__(self, path: Path, limits: ArchiveLimits, token=None):
        self.path = path
        self.limits = limits
        self.fingerprint = fingerprint(path)
        preflight(path, limits)
        check_cancel(token)
        self.zip = zipfile.ZipFile(path)
        try:
            infos = self.zip.infolist()
            if len(infos) > limits.max_entries:
                raise ValueError("Archive index exceeds the entry-count limit")
            if any(info.flag_bits & 1 for info in infos):
                raise ValueError("Password-protected ZIP archives are unsupported")
            self.images = []
            self.children = {"": []}
            folders = set()
            for index, info in enumerate(infos):
                check_cancel(token)
                try:
                    name = member_path(info.filename)
                except ValueError:
                    continue
                if not name or stat.S_ISLNK(info.external_attr >> 16):
                    continue
                parts = name.split("/")
                if len(parts) > limits.max_path_depth or len(name) > limits.max_path_chars:
                    raise ValueError("Archive member path exceeds the depth or length limit")
                for end in range(1, len(parts) + (1 if info.is_dir() else 0)):
                    folder = "/".join(parts[:end])
                    if folder not in folders:
                        folders.add(folder)
                        if len(folders) > limits.max_folders:
                            raise ValueError("Archive index exceeds the folder-count limit")
                        parent = folder.rpartition("/")[0]
                        self.children.setdefault(parent, []).append(
                            Entry(
                                path, "folder", member=folder, archive_fingerprint=self.fingerprint
                            )
                        )
                        self.children.setdefault(folder, [])
                suffix = PurePosixPath(name).suffix.lower()
                if info.is_dir() or suffix not in IMAGE_EXTENSIONS | {".zip"}:
                    continue
                try:
                    modified = int(datetime(*info.date_time).timestamp() * 1_000_000_000)
                except (ValueError, OverflowError, OSError):
                    modified = 0
                entry = Entry(
                    path,
                    "archive" if suffix == ".zip" else "image",
                    info.file_size,
                    modified,
                    name,
                    index,
                    info.CRC,
                    self.fingerprint,
                )
                self.children.setdefault(name.rpartition("/")[0], []).append(entry)
                if entry.kind == "image":
                    self.images.append(entry)
            self.images.sort(key=lambda entry: (natural_key(entry.member), entry.member_index))
            self.verify()
        except BaseException:
            self.close()
            raise

    def read(self, entry: Entry, token=None):
        self.verify()
        if entry.archive_fingerprint != self.fingerprint:
            raise SourceChanged("Archive changed while being viewed; refresh to reload")
        info = self.zip.infolist()[entry.member_index]
        if (info.filename.rstrip("/"), info.CRC, info.file_size) != (
            entry.member,
            entry.crc,
            entry.size,
        ):
            raise ValueError("Archive member changed; refresh to reload")
        if info.file_size > self.limits.max_entry_bytes:
            raise ValueError("Archive entry exceeds the decompressed byte limit")
        content = bytearray()
        with self.zip.open(info) as member:
            while True:
                check_cancel(token)
                block = member.read(min(65536, self.limits.max_entry_bytes + 1 - len(content)))
                if not block:
                    break
                content.extend(block)
                if len(content) > self.limits.max_entry_bytes:
                    raise ValueError("Archive entry exceeds the decompressed byte limit")
        self.verify()
        return content

    def close(self):
        self.zip.close()


class ArchiveStore:
    """At most one open archive per worker thread; never share ZIP handles."""

    def __init__(self, limits: ArchiveLimits):
        self.limits = limits
        self._readers = {}
        self._lock = threading.Lock()

    def get(self, path: Path, token=None):
        thread = threading.get_ident()
        with self._lock:
            reader = self._readers.get(thread)
        if reader and reader.path == path and reader.fingerprint == fingerprint(path):
            return reader
        if reader:
            reader.close()
            with self._lock:
                self._readers.pop(thread, None)
        reader = ArchiveReader(path, self.limits, token)
        with self._lock:
            self._readers[thread] = reader
        return reader

    def close(self):
        # Called only after all worker threads have stopped.
        for reader in self._readers.values():
            reader.close()
        self._readers.clear()
