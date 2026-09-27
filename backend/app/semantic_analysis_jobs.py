from typing import Callable

from .durable_runs import LocalComputeJobQueue, RunQueueAdapter


class SemanticAnalysisQueueAdapter(RunQueueAdapter):
    """Binds semantic analysis to the app-owned local executor."""

    def __init__(self, queue: LocalComputeJobQueue, handler: Callable[[str], None]) -> None:
        super().__init__(queue, "semantic-analysis", handler)

    def shutdown(self) -> None:
        # App lifetime owns the shared executor.
        return None


class SemanticAnalysisJobQueue(SemanticAnalysisQueueAdapter):
    """Backward-compatible standalone queue for injected tests and callers."""

    def __init__(self, handler: Callable[[str], None]) -> None:
        self._owned_queue = LocalComputeJobQueue()
        super().__init__(self._owned_queue, handler)

    def shutdown(self) -> None:
        self._owned_queue.shutdown()
