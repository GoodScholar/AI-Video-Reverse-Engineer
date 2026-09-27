from typing import Callable

from .durable_runs import LocalComputeJobQueue, RunQueueAdapter


class LocalPreprocessingJobQueue(RunQueueAdapter):
    """Backward-compatible standalone preprocessing queue."""

    def __init__(self, handler: Callable[[str], None]) -> None:
        self._owned_queue = LocalComputeJobQueue()
        super().__init__(self._owned_queue, "preprocessing", handler)

    def shutdown(self) -> None:
        self._owned_queue.shutdown()
