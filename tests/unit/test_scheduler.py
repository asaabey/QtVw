"""Deterministic queue ordering, cancellation, deduplication, and memory bounds."""

from threading import Event

import pytest

from qtvw.services import WorkerService
from qtvw.services.scheduler import (
    OPEN_IMAGE,
    PRELOAD,
    VISIBLE,
    DecodeBudget,
    PriorityExecutor,
    QueueFull,
)
from qtvw.sources.archive import Cancelled


def test_priority_promotion_fifo_and_queue_bound():
    scheduler = PriorityExecutor(max_workers=1, max_pending=5)
    entered, release = Event(), Event()
    order = []

    def block():
        entered.set()
        assert release.wait(5)

    try:
        blocker = scheduler.submit(block)
        assert entered.wait(2)
        low = scheduler.submit(lambda: order.append("preload"), priority=PRELOAD)
        visible = scheduler.submit(lambda: order.append("visible"), priority=VISIBLE)
        opened = scheduler.submit(lambda: order.append("opened"), priority=OPEN_IMAGE)
        promoted = scheduler.submit(lambda: order.append("promoted"), priority=PRELOAD)
        scheduler.promote(promoted, OPEN_IMAGE)
        with pytest.raises(QueueFull):
            scheduler.submit(lambda: None)
        assert low.cancel()
        replacement = scheduler.submit(lambda: order.append("replacement"), priority=VISIBLE)
        release.set()
        for future in (blocker, visible, opened, promoted, replacement):
            future.result(timeout=5)
        assert order == ["opened", "promoted", "visible", "replacement"]
    finally:
        release.set()
        scheduler.shutdown()


def test_decode_budget_and_cancelled_wait():
    budget = DecodeBudget(limit=10)
    cancelled = Event()
    with pytest.raises(ValueError, match="budget"):
        with budget.reserve(11, cancelled):
            pass
    with budget.reserve(10, cancelled):
        cancelled.set()
        with pytest.raises(Cancelled):
            with budget.reserve(1, cancelled):
                pass
        assert budget.used == 10
    assert budget.used == 0


def test_dedup_promotes_preload_to_visible_result(qtbot, tmp_path, image_bytes):
    path = tmp_path / "photo.png"
    path.write_bytes(image_bytes())
    worker = WorkerService(max_workers=1)
    entered, release = Event(), Event()
    decoded = []
    worker.decoded.connect(lambda *args: decoded.append(args))
    count = []
    original = worker._decode_source

    def source(*args):
        count.append(True)
        return original(*args)

    worker._decode_source = source

    def block(token):
        entered.set()
        assert release.wait(5)

    try:
        worker._submit(("block",), block, OPEN_IMAGE)
        assert entered.wait(2)
        assert worker.decode(path, 1, priority=PRELOAD, emit=False)
        assert worker.decode(path, 1, priority=OPEN_IMAGE)
        release.set()
        qtbot.waitUntil(lambda: bool(decoded))
        assert len(count) == 1
        assert len(decoded) == 1
        assert not decoded[0][3]
    finally:
        release.set()
        worker.shutdown()


def test_opened_image_and_listing_have_reserved_queue_capacity():
    from qtvw.services.scheduler import LISTING

    scheduler = PriorityExecutor(max_workers=1, max_pending=8)
    entered, release = Event(), Event()
    try:
        scheduler.submit(lambda: (entered.set(), release.wait(5)))
        assert entered.wait(2)
        for _ in range(6):
            scheduler.submit(lambda: None, priority=PRELOAD)
        with pytest.raises(QueueFull):
            scheduler.submit(lambda: None, priority=PRELOAD)
        opened = scheduler.submit(lambda: "opened", priority=OPEN_IMAGE)
        listing = scheduler.submit(lambda: "listing", priority=LISTING)
        release.set()
        assert opened.result(timeout=5) == "opened"
        assert listing.result(timeout=5) == "listing"
    finally:
        release.set()
        scheduler.shutdown()


def test_reprioritize_cancels_queued_offscreen_images(qtbot, tmp_path, image_bytes):
    first, second = tmp_path / "one.png", tmp_path / "two.png"
    first.write_bytes(image_bytes())
    second.write_bytes(image_bytes("blue"))
    worker = WorkerService(max_workers=1)
    entered, release = Event(), Event()
    results = []
    worker.decoded.connect(lambda *args: results.append(args))

    def blocker(token):
        entered.set()
        assert release.wait(5)

    try:
        worker._submit(("block",), blocker)
        assert entered.wait(2)
        worker.decode(first, 1, 32)
        worker.decode(second, 1, 32)
        assert worker.reprioritize(1, {str(second): VISIBLE}) == {str(first)}
        release.set()
        qtbot.waitUntil(lambda: bool(results))
        assert [result[1] for result in results] == [str(second)]
        worker.cancel()
        assert not worker._interests
    finally:
        release.set()
        worker.shutdown()
