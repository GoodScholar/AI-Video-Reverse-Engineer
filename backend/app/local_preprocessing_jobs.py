from concurrent.futures import ThreadPoolExecutor
from threading import Lock
from typing import Callable


class LocalPreprocessingJobQueue:
    def __init__(self, handler: Callable[[str], None]) -> None:
        self._handler = handler
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="local-preprocessing",
        )
        self._active: set[str] = set()
        self._lock = Lock()
        self._closed = False

    def submit(self, project_id: str) -> bool:
        with self._lock:
            if self._closed or project_id in self._active:
                return False
            self._active.add(project_id)
        try:
            future = self._executor.submit(self._run, project_id)
        except RuntimeError:
            with self._lock:
                self._active.discard(project_id)
            return False
        future.add_done_callback(lambda _: self._release(project_id))
        return True

    def _run(self, project_id: str) -> None:
        self._handler(project_id)

    def _release(self, project_id: str) -> None:
        with self._lock:
            self._active.discard(project_id)

    def is_active(self, project_id: str) -> bool:
        with self._lock:
            return project_id in self._active

    def shutdown(self) -> None:
        with self._lock:
            self._closed = True
        self._executor.shutdown(wait=True, cancel_futures=True)
