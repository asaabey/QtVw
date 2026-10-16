from threading import Event

from PySide6.QtGui import QColor, QImage

from qtvw.services import WorkerService


def test_empty_directory_completes(qtbot, tmp_path):
    worker = WorkerService()
    try:
        with qtbot.waitSignal(worker.listing_finished) as signal:
            worker.list_directory(tmp_path, 7)
        assert signal.args == [7, ""]
    finally:
        worker.shutdown()


def test_listing_batches_and_missing_directory(qtbot, tmp_path):
    for i in range(130):
        (tmp_path / f"{i}.png").touch()
    worker = WorkerService()
    batches = []
    worker.listed.connect(lambda generation, entries: batches.append((generation, entries)))
    try:
        with qtbot.waitSignal(worker.listing_finished):
            worker.list_directory(tmp_path, 2)
        assert [len(batch) for _, batch in batches] == [128, 2]
        assert all(generation == 2 for generation, _ in batches)
        with qtbot.waitSignal(worker.listing_finished) as signal:
            worker.list_directory(tmp_path / "missing", 3)
        assert signal.args[0] == 3
        assert signal.args[1]
    finally:
        worker.shutdown()


def test_decode_scaled_corrupt_and_limits(qtbot, tmp_path):
    path = tmp_path / "image.png"
    image = QImage(100, 50, QImage.Format_RGB32)
    image.fill(QColor("red"))
    assert image.save(str(path))
    worker = WorkerService()
    try:
        with qtbot.waitSignal(worker.decoded) as signal:
            worker.decode(path, 1, 20)
        assert signal.args[2].size().width() == 20
        assert not signal.args[3]
        worker.max_pixels = 10
        with qtbot.waitSignal(worker.decoded) as signal:
            worker.decode(path, 2)
        assert signal.args[2].isNull()
        assert "pixel limit" in signal.args[3]
        path.write_bytes(b"corrupt")
        with qtbot.waitSignal(worker.decoded) as signal:
            worker.decode(path, 3)
        assert signal.args[2].isNull()
        assert signal.args[3]
    finally:
        worker.shutdown()


def test_cancel_discards_queued_old_generation(qtbot, tmp_path):
    worker = WorkerService(max_workers=1)
    entered, release = Event(), Event()

    def blocker(token):
        entered.set()
        release.wait(5)

    results = []
    worker.listing_finished.connect(lambda *args: results.append(args))
    try:
        worker._submit(("block",), blocker)
        assert entered.wait(2)
        worker.list_directory(tmp_path, 1)
        worker.cancel()
        worker.list_directory(tmp_path, 2)
        release.set()
        qtbot.waitUntil(lambda: bool(results))
        assert results == [(2, "")]
    finally:
        release.set()
        worker.shutdown()
