"""Bounded worker pool. Only QImages cross threads; widgets stay on the GUI thread."""

from dataclasses import asdict
from pathlib import Path
from threading import Event, Lock

from PySide6.QtCore import QBuffer, QIODevice, QObject, QSize, Qt, Signal
from PySide6.QtGui import QImage, QImageReader

from qtvw.cache import CacheStore, thumbnail_key
from qtvw.domain import Entry
from qtvw.domain.locations import ArchiveLocation
from qtvw.services.scheduler import LISTING, VISIBLE, PriorityExecutor, QueueFull
from qtvw.sources.archive import (
    ArchiveIndex,
    ArchiveLimits,
    ArchiveStore,
    Cancelled,
    SourceChanged,
    check_cancel,
    fingerprint,
)
from qtvw.sources.filesystem import iter_directory


class WorkerService(QObject):
    listed = Signal(int, list)
    listing_finished = Signal(int, str)
    decoded = Signal(int, str, QImage, str)
    source_changed = Signal(int, str)

    def __init__(
        self,
        parent: QObject | None = None,
        max_workers: int = 2,
        archive_limits: ArchiveLimits | None = None,
        cache: CacheStore | None = None,
        scheduler: PriorityExecutor | None = None,
    ) -> None:
        super().__init__(parent)
        self._owns_scheduler = scheduler is None
        self._pool = scheduler or PriorityExecutor(max_workers=max_workers)
        self._owns_cache = cache is None
        self.cache = cache or CacheStore()
        self._cancelled = Event()
        self._lock = Lock()
        self._jobs: dict[tuple, object] = {}
        self._running = set()
        self._interests = {}
        self._closed = False
        self.max_pixels = 64_000_000
        self.max_file_bytes = 256 * 1024 * 1024
        self.max_pending = 128
        self.archive_limits = archive_limits or ArchiveLimits()
        self._archives = ArchiveStore(self.archive_limits)
        QImageReader.setAllocationLimit(256)

    def _submit(self, key: tuple, operation, priority=VISIBLE, emit_interest=None) -> bool:
        with self._lock:
            if self._closed:
                return False
            if key in self._jobs:
                if emit_interest is not None:
                    self._interests[key] = emit_interest or self._interests.get(key, False)
                self._pool.promote(self._jobs[key], priority)
                return True
            if len(self._jobs) >= self.max_pending:
                return False
            token = self._cancelled
            try:
                future = self._pool.submit(operation, token, priority=priority)
            except QueueFull:
                return False
            if emit_interest is not None:
                self._interests[key] = emit_interest
            self._jobs[key] = future
            self._running.add(future)

        def finished(_future) -> None:
            with self._lock:
                self._running.discard(future)
                if self._jobs.get(key) is future:
                    self._jobs.pop(key, None)
                    self._interests.pop(key, None)

        future.add_done_callback(finished)
        return True

    def _index(self, path, token, epoch):
        expected = fingerprint(path)
        policy = asdict(self.archive_limits)
        records = self.cache.index_get(path, expected, policy)
        check_cancel(token)
        if records is not None:
            try:
                return ArchiveIndex.restore(path, expected, records, self.archive_limits)
            except SourceChanged:
                raise
            except (ValueError, TypeError, KeyError, IndexError):
                pass  # Rebuild malformed or obsolete disposable cache data.
        reader = self._archives.get(path, token)
        check_cancel(token)
        self.cache.index_put(path, reader.fingerprint, policy, reader.records(), epoch)
        return reader

    def list_directory(
        self, path: Path | ArchiveLocation, generation: int, show_hidden: bool = False
    ) -> None:
        epoch = self.cache.epoch

        def operation(token: Event) -> None:
            try:
                batch = []
                if isinstance(path, ArchiveLocation):
                    source = self._index(path.path, token, epoch)
                    entries = source.listing(path.internal, show_hidden)
                else:
                    entries = iter_directory(Path(path), show_hidden)
                for entry in entries:
                    if token.is_set():
                        return
                    batch.append(entry)
                    if len(batch) >= 128:
                        self.listed.emit(generation, batch)
                        batch = []
                if not token.is_set():
                    if batch:
                        self.listed.emit(generation, batch)
                    self.listing_finished.emit(generation, "")
            except Cancelled:
                return
            except SourceChanged as error:
                if not token.is_set():
                    self.source_changed.emit(generation, str(path))
                    self.listing_finished.emit(generation, str(error))
            except Exception as error:
                if not token.is_set():
                    self.listing_finished.emit(generation, str(error))

        if not self._submit(("list", generation, str(path)), operation, priority=LISTING):
            self.listing_finished.emit(generation, "Background work queue is full")

    def _read_image(self, reader, size, token):
        reader.setAutoTransform(True)
        dimensions = reader.size()
        if not dimensions.isValid():
            raise ValueError(reader.errorString())
        if dimensions.width() * dimensions.height() > self.max_pixels:
            raise ValueError("Image exceeds the decoded pixel limit")
        if size > 0:
            reader.setScaledSize(dimensions.scaled(QSize(size, size), Qt.KeepAspectRatio))
        check_cancel(token)
        with self._pool.budget.reserve(dimensions.width() * dimensions.height() * 4, token):
            image = reader.read()
            if image.isNull():
                raise ValueError(reader.errorString())
        check_cancel(token)
        return image

    def _member_image(self, entry, size, token):
        archive = self._archives.get(entry.path, token)
        content = archive.read(entry, token)
        buffer = QBuffer()
        buffer.setData(content)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        try:
            image = self._read_image(QImageReader(buffer), size, token)
            archive.verify()
            return image
        finally:
            buffer.close()

    def _cover(self, entry, size, token, epoch):
        archive = self._index(entry.path, token, epoch)
        budget = self.archive_limits.max_cover_bytes
        last_error = "Archive contains no supported, decodable images"
        for candidate in archive.candidates()[: self.archive_limits.max_cover_candidates]:
            check_cancel(token)
            if candidate.size > self.archive_limits.max_entry_bytes or candidate.size > budget:
                last_error = "Archive cover exceeds the decompressed byte budget"
                continue
            budget -= candidate.size
            try:
                return self._member_image(candidate, size, token)
            except Cancelled:
                raise
            except Exception as error:
                last_error = str(error)
                archive.verify()
        raise ValueError(last_error)

    def _thumbnail_read(self, data, size, token):
        buffer = QBuffer()
        buffer.setData(data)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        try:
            reader = QImageReader(buffer)
            dimensions = reader.size()
            if not dimensions.isValid() or max(dimensions.width(), dimensions.height()) > size:
                return None
            return self._read_image(reader, 0, token)
        except ValueError:
            return None
        finally:
            buffer.close()

    def _decode_source(self, entry, size, token, epoch):
        if entry.member is not None:
            if entry.kind != "image":
                raise ValueError("Nested ZIP archives are not supported")
            return self._member_image(entry, size, token)
        if entry.kind == "archive":
            return self._cover(entry, size, token, epoch)
        if entry.path.stat().st_size > self.max_file_bytes:
            raise ValueError("Image file exceeds the input byte limit")
        return self._read_image(QImageReader(str(entry.path)), size, token)

    def decode(
        self, path: Path | Entry, generation: int, size: int = 0, priority=VISIBLE, emit=True
    ) -> bool:
        entry = path if isinstance(path, Entry) else Entry(Path(path), "image")
        epoch = self.cache.epoch
        size_class = 1 << max(5, (size - 1).bit_length()) if size else 0
        job_key = ("decode", generation, entry.identity, size)

        def operation(token: Event) -> None:
            if token.is_set():
                return
            image = QImage()
            error = ""
            try:
                expected = fingerprint(entry.path)
                if entry.member is not None and entry.archive_fingerprint != expected:
                    raise SourceChanged("Archive changed while being viewed; refresh to reload")
                if not self.cache.validate_source(entry.path, expected):
                    raise SourceChanged("Source changed while being read; refresh to reload")
                policy = [asdict(self.archive_limits), self.max_pixels, self.max_file_bytes]
                key = thumbnail_key(entry, expected, size_class, policy)
                image = self.cache.memory_get(key, full=not size)
                if image is None and size:
                    data = self.cache.thumbnail_get(key, entry.path, expected)
                    if data is not None:
                        image = self._thumbnail_read(data, size_class, token)
                        if image is None:
                            self.cache.thumbnail_drop(key)
                if image is None:
                    image = self._decode_source(entry, size_class, token, epoch)
                    check_cancel(token)
                    if fingerprint(entry.path) != expected:
                        raise SourceChanged("Source changed while being read; refresh to reload")
                    if size:
                        buffer = QBuffer()
                        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
                        if image.save(buffer, "PNG"):
                            self.cache.thumbnail_put(
                                key, entry.path, expected, bytes(buffer.data()), epoch
                            )
                check_cancel(token)
                if fingerprint(entry.path) != expected:
                    raise SourceChanged("Source changed while being read; refresh to reload")
                self.cache.memory_put(key, image, epoch, full=not size)
                if size:
                    image = image.scaled(
                        QSize(size, size), Qt.KeepAspectRatio, Qt.SmoothTransformation
                    )
            except Cancelled:
                return
            except SourceChanged as failure:
                image = QImage()
                error = str(failure)
                if not token.is_set():
                    self.source_changed.emit(generation, str(entry.path))
            except Exception as failure:
                image = QImage()
                error = str(failure)
            with self._lock:
                interested = self._interests.get(job_key, False)
            if interested and not token.is_set():
                self.decoded.emit(generation, entry.identity, image, error)

        return self._submit(job_key, operation, priority, emit_interest=emit)

    def reprioritize(self, generation, priorities):
        """Drop obsolete queued thumbnails and promote newly visible ones."""
        with self._lock:
            jobs = [
                (key, future)
                for key, future in self._jobs.items()
                if key[0] == "decode" and key[1] == generation
            ]
        for key, future in jobs:
            if key[2] in priorities:
                self._pool.promote(future, priorities[key[2]])
            elif future.cancel():
                with self._lock:
                    if self._jobs.get(key) is future:
                        self._jobs.pop(key, None)
        return {key[2] for key, future in jobs if future.cancelled()}

    def cancel(self) -> None:
        with self._lock:
            self._cancelled.set()
            self._cancelled = Event()
            futures = list(self._jobs.values())
            self._jobs.clear()
            self._interests.clear()
        for future in futures:
            future.cancel()

    def shutdown(self) -> None:
        self._closed = True
        self.cancel()
        if self._owns_scheduler:
            self._pool.shutdown(wait=True, cancel_futures=True)
        else:
            # The shared scheduler stays alive for other service groups.
            with self._lock:
                futures = list(self._running)
            for future in futures:
                try:
                    future.result()
                except Exception:
                    pass
        self._archives.close()
        if self._owns_cache:
            self.cache.close()
