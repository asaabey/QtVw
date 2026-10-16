"""Background ZIP file moves with Qt's no-overwrite rename/copy fallback."""

import fcntl
import logging
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

from PySide6.QtCore import QFile, QObject, Signal


def move_zip(source, directory):
    source = Path(source).absolute()
    directory = Path(directory).resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("Drop onto an existing filesystem folder")
    info = source.lstat()
    if source.suffix.lower() != ".zip" or not stat.S_ISREG(info.st_mode):
        raise ValueError("Only regular ZIP files can be moved; symbolic links are unsupported")
    destination = directory / source.name
    if source.parent.resolve() == directory:
        raise ValueError("The ZIP is already in that folder")
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"A file named {source.name} already exists in {directory}")
    with source.open("rb") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError("This ZIP is being edited or moved; wait and try again") from error
        current = source.lstat()
        opened = os.fstat(stream.fileno())

        def signature(item):
            return item.st_dev, item.st_ino, item.st_size, item.st_mtime_ns, item.st_ctime_ns

        if signature(info) != signature(current) or signature(info) != signature(opened):
            raise ValueError("The source changed before the move; refresh and try again")
        # QFile refuses to overwrite an existing destination. Across filesystems,
        # Qt copies before removing the source and rolls back an unsuccessful copy.
        file = QFile(str(source))
        if not file.rename(str(destination)):
            raise OSError(file.errorString())
    logging.getLogger(__name__).info("Moved ZIP: %s -> %s", source, destination)
    return source, destination


class FileMoveService(QObject):
    progress = Signal(int, int, str)
    finished = Signal(list, list, bool)

    def __init__(self, cache, parent=None):
        super().__init__(parent)
        self.cache = cache
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qtvw-file-move")
        self._stop = Event()

    def start(self, paths, directory):
        self._stop = Event()
        token = self._stop
        paths = tuple(dict.fromkeys(Path(path) for path in paths))

        def operation():
            moved, errors = [], []
            for position, source in enumerate(paths, 1):
                if token.is_set():
                    break
                self.progress.emit(position, len(paths), source.name)
                try:
                    pair = move_zip(source, directory)
                except Exception as error:
                    logging.getLogger(__name__).warning("Cannot move ZIP %s: %s", source, error)
                    errors.append(f"{source}: {error}")
                    continue
                moved.append(pair)
                for path in pair:
                    try:
                        self.cache.invalidate_source(path)
                    except Exception:
                        logging.getLogger(__name__).exception("Move cache cleanup failed: %s", path)
            self.finished.emit(moved, errors, token.is_set())

        self._pool.submit(operation)

    def cancel(self):
        self._stop.set()

    def shutdown(self):
        self.cancel()
        self._pool.shutdown(wait=True, cancel_futures=True)
