"""Time/disk/queue bounds and useful diagnostic records without UI-thread I/O."""

import json
import logging
import queue
import sys
import time

from PySide6.QtCore import qWarning

from qtvw.diagnostics import BoundedQueueHandler, Diagnostics, WindowLogWriter


def record(message, created):
    result = logging.LogRecord("qtvw.test", logging.INFO, __file__, 1, message, (), None)
    result.created = created
    return result


def read_logs(directory):
    return [
        json.loads(line)
        for path in directory.glob("qtvw-*.log")
        for line in path.read_text().splitlines()
    ]


def test_retention_and_size_cap(tmp_path):
    now = [time.time()]
    writer = WindowLogWriter(tmp_path, max_bytes=2048, segment_bytes=512, clock=lambda: now[0])
    for index in range(100):
        writer.write(record(f"record {index} " + "x" * 100, now[0]))
    assert sum(path.stat().st_size for path in tmp_path.glob("*.log")) <= 2048
    assert read_logs(tmp_path)
    now[0] += 3661
    writer.prune()
    assert not list(tmp_path.glob("*.log"))
    writer.write(record("new session", now[0]))
    assert read_logs(tmp_path)[0]["message"] == "new session"


def test_idle_session_expires_logs_without_new_messages(qtbot, tmp_path):
    now = [time.time()]
    diagnostics = Diagnostics(
        tmp_path, clock=lambda: now[0], maintenance_interval=0.01, qt_messages=False
    )
    try:
        diagnostics.logger.info("idle logging")
        diagnostics.records.join()
        assert read_logs(tmp_path)
        now[0] += 3661
        qtbot.waitUntil(lambda: not list(tmp_path.glob("*.log")), timeout=1000)
    finally:
        diagnostics.close()
    assert not diagnostics.thread.is_alive()


def test_full_logging_queue_drops_without_blocking():
    records = queue.Queue(maxsize=1)
    handler = BoundedQueueHandler(records)
    handler.handle(record("one", time.time()))
    handler.handle(record("two", time.time()))
    assert records.qsize() == 1
    assert handler.dropped == 1
    handler.close()


def test_unavailable_log_storage_is_nonfatal(tmp_path):
    path = tmp_path / "occupied"
    path.write_text("file")
    diagnostics = Diagnostics(path, qt_messages=False)
    diagnostics.logger.warning("diagnostic message")
    diagnostics.close()
    assert diagnostics.writer.last_error
    assert path.read_text() == "file"
    assert not diagnostics.thread.is_alive()


def test_qt_messages_exceptions_and_hook_restoration(tmp_path, monkeypatch):
    delegated = []

    def original(*args):
        delegated.append(args)

    monkeypatch.setattr(sys, "excepthook", original)
    diagnostics = Diagnostics(tmp_path)
    try:
        qWarning("diagnostic Qt warning")
        try:
            raise ValueError("diagnostic failure")
        except ValueError as error:
            diagnostics._exception_hook(type(error), error, error.__traceback__)
    finally:
        diagnostics.close()
    messages = read_logs(tmp_path)
    assert any("diagnostic Qt warning" in row["message"] for row in messages)
    assert any("ValueError: diagnostic failure" in row["message"] for row in messages)
    assert any(row["level"] == "CRITICAL" for row in messages)
    assert delegated
    assert sys.excepthook is original
