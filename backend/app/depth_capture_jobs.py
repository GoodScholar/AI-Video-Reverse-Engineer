from typing import Callable

from .durable_runs import LocalComputeJobQueue, RunQueueAdapter

class LocalPreprocessingQueueAdapter(RunQueueAdapter):
    """Keeps the historical preprocessing queue interface on the shared queue."""

    def __init__(self, queue: LocalComputeJobQueue, handler: Callable[[str], None]) -> None:
        super().__init__(queue, "preprocessing", handler)

    def shutdown(self) -> None:
        # App lifetime owns the shared executor.
        return None
