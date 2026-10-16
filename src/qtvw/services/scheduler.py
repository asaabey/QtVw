"""Bounded priority scheduling with cooperative cancellation and decode budgets."""

import heapq
import itertools
from concurrent.futures import Future
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import Condition, Thread

from qtvw.sources.archive import check_cancel

OPEN_IMAGE = 0
LISTING = 1
VISIBLE = 2
NEARBY = 3
PRELOAD = 4


class QueueFull(Exception):
    pass


@dataclass(order=True)
class Job:
    priority: int
    sequence: int
    future: Future = field(compare=False)
    operation: object = field(compare=False)


class DecodeBudget:
    def __init__(self, limit=256 * 1024 * 1024):
        self.limit = limit
        self.used = 0
        self.condition = Condition()

    @contextmanager
    def reserve(self, amount, token):
        if amount > self.limit:
            raise ValueError("Image exceeds the concurrent decode memory budget")
        with self.condition:
            while self.used + amount > self.limit:
                check_cancel(token)
                self.condition.wait(0.05)
            check_cancel(token)
            self.used += amount
        try:
            yield
        finally:
            with self.condition:
                self.used -= amount
                self.condition.notify_all()


class PriorityExecutor:
    def __init__(self, max_workers=2, max_pending=128):
        if max_workers < 1 or max_pending < 1:
            raise ValueError("Worker and queue limits must be positive")
        self.max_pending = max_pending
        self.budget = DecodeBudget()
        self._queue = []
        self._sequence = itertools.count()
        self._condition = Condition()
        self._closed = False
        self._threads = [Thread(target=self._run, name=f"qtvw-{i}") for i in range(max_workers)]
        for thread in self._threads:
            thread.start()

    def submit(self, operation, *args, priority=VISIBLE):
        with self._condition:
            if self._closed:
                raise RuntimeError("Background scheduler is closed")
            self._queue = [job for job in self._queue if not job.future.cancelled()]
            heapq.heapify(self._queue)
            reserved = min(2, self.max_pending // 4)
            capacity = self.max_pending if priority <= LISTING else self.max_pending - reserved
            if len(self._queue) >= capacity:
                raise QueueFull("Background work queue is full")
            future = Future()
            heapq.heappush(
                self._queue, Job(priority, next(self._sequence), future, lambda: operation(*args))
            )
            self._condition.notify()
            return future

    def promote(self, future, priority):
        with self._condition:
            for job in self._queue:
                if job.future is future and priority < job.priority:
                    job.priority = priority
                    heapq.heapify(self._queue)
                    break

    def _run(self):
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._queue or self._closed)
                if not self._queue:
                    return
                job = heapq.heappop(self._queue)
            if not job.future.set_running_or_notify_cancel():
                continue
            try:
                job.future.set_result(job.operation())
            except BaseException as error:
                job.future.set_exception(error)

    def shutdown(self, wait=True, cancel_futures=True):
        with self._condition:
            self._closed = True
            cancelled = list(self._queue) if cancel_futures else []
            if cancelled:
                self._queue.clear()
            self._condition.notify_all()
        for job in cancelled:
            job.future.cancel()
        if wait:
            for thread in self._threads:
                thread.join()
