from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Callable


class LocalComputeJobQueue:
    """One local executor shared by all resource-intensive project jobs."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="local-compute")
        self._active: set[tuple[str, str]] = set()
        self._lock = Lock()
        self._closed = False

    def submit(self, job_kind: str, project_id: str, handler: Callable[[str], None]) -> bool:
        key = (job_kind, project_id)
        with self._lock:
            if self._closed or key in self._active:
                return False
            self._active.add(key)
        try:
            future = self._executor.submit(handler, project_id)
        except RuntimeError:
            with self._lock:
                self._active.discard(key)
            return False
        future.add_done_callback(lambda _: self._release(key))
        return True

    def _release(self, key: tuple[str, str]) -> None:
        with self._lock:
            self._active.discard(key)

    def is_active(self, job_kind: str, project_id: str) -> bool:
        with self._lock:
            return (job_kind, project_id) in self._active

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)


class LocalPreprocessingQueueAdapter:
    """Keeps the historical preprocessing queue interface on the shared queue."""

    def __init__(self, queue: LocalComputeJobQueue, handler: Callable[[str], None]) -> None:
        self._queue = queue
        self._handler = handler

    def submit(self, project_id: str) -> bool:
        return self._queue.submit("preprocessing", project_id, self._handler)

    def is_active(self, project_id: str) -> bool:
        return self._queue.is_active("preprocessing", project_id)

    def shutdown(self) -> None:
        # App lifetime owns the shared executor.
        return None
