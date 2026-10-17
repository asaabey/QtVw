"""Stream RAR members into a verified ZIP without extracting filesystem paths."""

import os
import stat
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from threading import Event

import rarfile
from PySide6.QtCore import QObject, Signal


class ConversionCancelled(Exception):
    pass


def convert_rar(source, cancel, progress, max_entry_bytes=512 * 1024 * 1024):
    source = Path(source).absolute()
    destination = source.with_suffix(".zip")
    initial = source.lstat()
    if source.suffix.lower() != ".rar" or not stat.S_ISREG(initial.st_mode):
        raise ValueError("Choose a regular RAR file; symbolic links are unsupported")
    if os.path.lexists(destination):
        raise FileExistsError(f"{destination.name} already exists")

    def check_cancel():
        if cancel.is_set():
            raise ConversionCancelled()

    def signature(info):
        return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns

    temporary = None
    try:
        check_cancel()
        with rarfile.RarFile(source, errors="strict") as archive:
            if archive.needs_password():
                raise ValueError("Password-protected RAR archives are not supported")
            if len(archive.volumelist()) != 1:
                raise ValueError(
                    "Multi-volume RAR archives are not supported; choose a single archive"
                )
            members = archive.infolist()
            names = set()
            for member in members:
                name = member.filename
                parts = PurePosixPath(name).parts
                if (
                    not parts
                    or name.startswith("/")
                    or "\\" in name
                    or "\x00" in name
                    or ".." in parts
                    or ":" in parts[0]
                    or name in names
                ):
                    raise ValueError(f"Unsafe or duplicate archive path: {name}")
                if (
                    member.is_symlink()
                    or getattr(member, "file_redir", None)
                    or not (member.isdir() or member.is_file())
                ):
                    raise ValueError(f"Links and special archive entries are unsupported: {name}")
                if member.file_size > max_entry_bytes:
                    raise ValueError(f"Entry exceeds the configured archive size limit: {name}")
                names.add(name)
            if any(m.compress_type != rarfile.RAR_M0 for m in members):
                rarfile.tool_setup(
                    unar=False, bsdtar=False, sevenzip=False, sevenzip2=False, force=True
                )
            total = max(1, sum(m.file_size for m in members if not m.isdir()))
            processed = 0
            with tempfile.NamedTemporaryFile(
                prefix=".qtvw-convert-", suffix=".zip", dir=source.parent, delete=False
            ) as stream:
                temporary = Path(stream.name)
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as output:
                for member in members:
                    check_cancel()
                    info = zipfile.ZipInfo(member.filename)
                    if member.date_time and 1980 <= member.date_time[0] <= 2107:
                        info.date_time = member.date_time
                    info.compress_type = zipfile.ZIP_DEFLATED
                    if member.isdir():
                        info.filename = info.filename.rstrip("/") + "/"
                        output.writestr(info, b"")
                        continue
                    copied = 0
                    with (
                        archive.open(member) as reader,
                        output.open(info, "w", force_zip64=True) as writer,
                    ):
                        while True:
                            check_cancel()
                            chunk = reader.read(256 * 1024)
                            if not chunk:
                                break
                            copied += len(chunk)
                            if copied > min(member.file_size, max_entry_bytes):
                                raise ValueError(
                                    f"Entry exceeds its declared size: {member.filename}"
                                )
                            writer.write(chunk)
                            processed += len(chunk)
                            progress(int(processed * 80 / total), f"Converting {member.filename}")
                    if copied != member.file_size:
                        raise ValueError(f"Incomplete RAR entry: {member.filename}")
            progress(80, "Verifying ZIP contents…")
            with zipfile.ZipFile(temporary) as result:
                if len(result.infolist()) != len(members):
                    raise ValueError("ZIP verification failed: member count differs")
                for original, converted in zip(members, result.infolist()):
                    check_cancel()
                    if converted.file_size != original.file_size:
                        raise ValueError("ZIP verification failed: size differs")
                    if (
                        not original.isdir()
                        and original.CRC is not None
                        and converted.CRC != original.CRC
                    ):
                        raise ValueError("ZIP verification failed: CRC differs")
                    with result.open(converted) as reader:
                        while reader.read(256 * 1024):
                            check_cancel()
            check_cancel()
            if signature(source.lstat()) != signature(initial):
                raise ValueError("The RAR changed during conversion; try again")
            # Publishing via a hard link is atomic and refuses late destination conflicts.
            os.link(temporary, destination)
            return destination
    except rarfile.RarCannotExec as error:
        raise ValueError(
            "Install a RAR extractor (Ubuntu: sudo apt install unrar) and try again"
        ) from error
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class RarConversionService(QObject):
    progress = Signal(int, str)
    finished = Signal(object, str, bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qtvw-rar-convert")
        self._cancel = Event()

    def start(self, source, max_entry_bytes):
        self._cancel = Event()
        token = self._cancel

        def operation():
            try:
                destination = convert_rar(source, token, self.progress.emit, max_entry_bytes)
            except ConversionCancelled:
                self.finished.emit(None, "", True)
            except Exception as error:
                self.finished.emit(None, str(error), False)
            else:
                self.finished.emit(destination, "", False)

        self._pool.submit(operation)

    def cancel(self):
        self._cancel.set()

    def shutdown(self):
        self.cancel()
        self._pool.shutdown(wait=True)
