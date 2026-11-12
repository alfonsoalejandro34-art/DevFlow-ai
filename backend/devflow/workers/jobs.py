"""In-process job execution on a bounded thread pool.

DevFlow is a single-user local tool, so an in-process pool is simpler and more
reliable than an external queue. Job state lives in the database; on restart, jobs
that were queued or running are marked as interrupted (see RunService.recover).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor

from devflow.observability.logging import correlation_id
from devflow.observability.metrics import ACTIVE_JOBS

log = logging.getLogger(__name__)


class JobManager:
    def __init__(self, max_workers: int) -> None:
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="devflow-job")
        self._cancel: dict[str, threading.Event] = {}
        self._futures: dict[str, Future[None]] = {}
        self._lock = threading.Lock()

    def submit(self, key: str, fn: Callable[[threading.Event], None]) -> threading.Event:
        event = threading.Event()
        cid = correlation_id.get()

        def wrapped() -> None:
            token = correlation_id.set(cid)
            try:
                fn(event)
            except Exception:
                log.exception("job crashed", extra={"job": key})
            finally:
                correlation_id.reset(token)
                with self._lock:
                    self._cancel.pop(key, None)
                    self._futures.pop(key, None)
                ACTIVE_JOBS.dec()

        with self._lock:
            self._cancel[key] = event
            ACTIVE_JOBS.inc()
            self._futures[key] = self._pool.submit(wrapped)
        return event

    def cancel(self, key: str) -> bool:
        with self._lock:
            event = self._cancel.get(key)
        if event is None:
            return False
        event.set()
        return True

    def wait(self, key: str, timeout: float | None = None) -> None:
        with self._lock:
            future = self._futures.get(key)
        if future is not None:
            future.result(timeout=timeout)

    def active_keys(self) -> list[str]:
        with self._lock:
            return list(self._cancel)

    def shutdown(self, cancel_running: bool = True) -> None:
        if cancel_running:
            with self._lock:
                events = list(self._cancel.values())
            for event in events:
                event.set()
        self._pool.shutdown(wait=True, cancel_futures=True)
