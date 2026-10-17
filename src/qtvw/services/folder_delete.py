"""Recursive filesystem deletion outside the GUI thread."""

import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PySide6.QtCore import QObject, Signal


def folder_identity(path):
    path = Path(path)
    if path.is_symlink() or not path.is_dir() or path.resolve() == Path(path.anchor):
        raise ValueError("Choose a regular folder other than the filesystem root")
    info = path.stat()
    return info.st_dev, info.st_ino


def delete_folder(path, identity):
    if folder_identity(path) != identity:
        raise ValueError("The folder changed since confirmation; try again")
    shutil.rmtree(path)


class FolderDeleteService(QObject):
    finished = Signal(object, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qtvw-folder-delete")

    def start(self, path, identity):
        def operation():
            error = ""
            try:
                delete_folder(path, identity)
            except Exception as exc:
                error = str(exc)
            self.finished.emit(path, error)

        self._pool.submit(operation)

    def shutdown(self):
        self._pool.shutdown(wait=True)
