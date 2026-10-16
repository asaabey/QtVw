"""Nonblocking application logging with time retention and a hard disk-size cap."""

import json
import logging
import os
import queue
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from logging.handlers import QueueHandler
from pathlib import Path

from PySide6.QtCore import QStandardPaths, QtMsgType, qInstallMessageHandler

RETENTION_SECONDS = 3600
MAX_LOG_BYTES = 8 * 1024 * 1024
SEGMENT_BYTES = 256 * 1024
MAX_MESSAGE_CHARS = 8192
_active = None


def default_log_dir():
    location = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    return Path(location) / "logs"


def log_location():
    return _active.directory if _active is not None else default_log_dir()


def logging_status():
    if _active is None:
        return "File logging starts when the application is launched normally."
    if _active.writer.last_error:
        return "File logging unavailable: " + _active.writer.last_error
    suffix = (
        f" {_active.handler.dropped} messages dropped under load."
        if _active.handler.dropped
        else ""
    )
    return "Logs retain about one hour, capped at 8 MiB in total." + suffix


class WindowLogWriter:
    def __init__(
        self,
        directory,
        retention=RETENTION_SECONDS,
        max_bytes=MAX_LOG_BYTES,
        segment_bytes=SEGMENT_BYTES,
        clock=time.time,
    ):
        self.directory = Path(directory)
        self.retention = retention
        self.max_bytes = max_bytes
        self.segment_bytes = min(segment_bytes, max_bytes)
        self.clock = clock
        self.session = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
        self._bucket = None
        self._part = 0
        self._file = None
        self.last_error = ""

    def write(self, record):
        try:
            if record.created < self.clock() - self.retention:
                return
            self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            bucket = datetime.fromtimestamp(record.created, timezone.utc).strftime("%Y%m%dT%H%MZ")
            if bucket != self._bucket:
                self._bucket, self._part = bucket, 0
                self._file = None
            data = {
                "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage()[:MAX_MESSAGE_CHARS],
            }
            line = (json.dumps(data, ensure_ascii=False) + "\n").encode("utf-8", errors="replace")
            if len(line) > self.max_bytes:
                return
            if self._file is None or (
                self._file.exists() and self._file.stat().st_size + len(line) > self.segment_bytes
            ):
                if self._file is not None:
                    self._part += 1
                self._file = self.directory / f"qtvw-{bucket}-{self.session}-{self._part:04}.log"
            descriptor = os.open(self._file, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(descriptor, "ab") as stream:
                stream.write(line)
            self.last_error = ""
            self.prune()
        except OSError as error:
            self.last_error = str(error)

    def prune(self):
        """Runs on startup, during writes, and periodically even when the UI is idle."""
        try:
            files = []
            cutoff = self.clock() - self.retention
            for path in self.directory.glob("qtvw-*.log"):
                try:
                    bucket = path.name.split("-")[1]
                    timestamp = (
                        datetime.strptime(bucket, "%Y%m%dT%H%MZ")
                        .replace(tzinfo=timezone.utc)
                        .timestamp()
                    )
                    size = path.stat().st_size
                    if timestamp + 60 <= cutoff:
                        path.unlink(missing_ok=True)
                    else:
                        files.append((timestamp, path.stat().st_mtime_ns, path, size))
                except (ValueError, IndexError, FileNotFoundError):
                    continue
            total = sum(entry[3] for entry in files)
            for _, _, path, size in sorted(files):
                if total <= self.max_bytes:
                    break
                path.unlink(missing_ok=True)
                total -= size
        except OSError as error:
            self.last_error = str(error)


class BoundedQueueHandler(QueueHandler):
    def __init__(self, records):
        super().__init__(records)
        self.dropped = 0

    def enqueue(self, record):
        try:
            self.queue.put_nowait(record)
        except queue.Full:
            self.dropped += 1


class Diagnostics:
    def __init__(self, directory, clock=time.time, maintenance_interval=30, qt_messages=True):
        self.directory = Path(directory)
        self.writer = WindowLogWriter(directory, clock=clock)
        self.records = queue.Queue(maxsize=2048)
        self.handler = BoundedQueueHandler(self.records)
        self.logger = logging.getLogger("qtvw")
        self._previous_level = self.logger.level
        self._previous_propagate = self.logger.propagate
        self.logger.setLevel(logging.INFO)
        self.logger.propagate = False
        self.logger.addHandler(self.handler)
        self._stop = threading.Event()
        self._fatal_written = threading.Event()
        self._interval = maintenance_interval
        self._closed = False
        self.thread = threading.Thread(target=self._run, name="qtvw-log", daemon=True)
        self.thread.start()
        self._previous_hook = sys.excepthook
        sys.excepthook = self._exception_hook
        self._qt_messages = qt_messages
        self._previous_qt = qInstallMessageHandler(self._qt_message) if qt_messages else None

    def _run(self):
        self.writer.prune()
        while not self._stop.is_set() or not self.records.empty():
            try:
                record = self.records.get(timeout=self._interval)
            except queue.Empty:
                self.writer.prune()
                continue
            try:
                if record is not None:
                    self.writer.write(record)
                    if record.levelno >= logging.CRITICAL:
                        self._fatal_written.set()
            finally:
                self.records.task_done()

    def _qt_message(self, kind, context, message):
        levels = {
            QtMsgType.QtDebugMsg: logging.DEBUG,
            QtMsgType.QtInfoMsg: logging.INFO,
            QtMsgType.QtWarningMsg: logging.WARNING,
            QtMsgType.QtCriticalMsg: logging.ERROR,
            QtMsgType.QtFatalMsg: logging.CRITICAL,
        }
        logging.getLogger("qtvw.qt").log(
            levels.get(kind, logging.WARNING), "%s: %s", context.category or "qt", message
        )
        if kind == QtMsgType.QtFatalMsg:
            self._fatal_written.wait(0.25)
        if self._previous_qt is not None:
            self._previous_qt(kind, context, message)

    def _exception_hook(self, kind, error, traceback):
        self.logger.critical("Uncaught exception", exc_info=(kind, error, traceback))
        self._previous_hook(kind, error, traceback)

    def close(self):
        global _active
        if self._closed:
            return
        self._closed = True
        if self._qt_messages:
            qInstallMessageHandler(self._previous_qt)
        sys.excepthook = self._previous_hook
        self.logger.removeHandler(self.handler)
        self.logger.setLevel(self._previous_level)
        self.logger.propagate = self._previous_propagate
        self._stop.set()
        try:
            self.records.put_nowait(None)
        except queue.Full:
            pass
        self.thread.join(timeout=2)
        self.handler.close()
        if _active is self:
            _active = None


def configure_logging():
    global _active
    if _active is not None:
        _active.close()
    _active = Diagnostics(default_log_dir())
    return _active
