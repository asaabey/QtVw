"""One independent, cancellable ZIP editing job; no Qt widgets in its worker."""

import logging
from concurrent.futures import ThreadPoolExecutor
from threading import Event

from PySide6.QtCore import QObject, Signal

from qtvw.sources.archive import Cancelled
from qtvw.sources.archive_edit import delete_images


class ArchiveEditService(QObject):
    progress = Signal(int, str)
    finished = Signal(object, str, bool)

    def __init__(self, cache, limits, parent=None):
        super().__init__(parent)
        self.cache = cache
        self.limits = limits
        self._pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qtvw-zip-edit")
        self._token = Event()

    def start(self, path, entries):
        self._token = Event()
        token = self._token

        def operation():
            try:
                result = delete_images(path, entries, token, self.progress.emit, self.limits)
            except Cancelled:
                logging.getLogger(__name__).info("ZIP deletion cancelled: %s", path)
                self.finished.emit(None, "", True)
            except Exception as error:
                logging.getLogger(__name__).exception("ZIP deletion failed: %s", path)
                self.finished.emit(None, str(error), False)
            else:
                try:
                    self.cache.invalidate_source(path)
                except Exception:
                    # The atomic replacement already committed. Never report this
                    # as a failed deletion; new fingerprints prevent stale reuse.
                    logging.getLogger(__name__).exception("ZIP cache invalidation failed: %s", path)
                self.finished.emit(result, "", False)

        self._pool.submit(operation)

    def cancel(self):
        self._token.set()

    def shutdown(self):
        self.cancel()
        self._pool.shutdown(wait=True, cancel_futures=True)
