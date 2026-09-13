from threading import Event, Thread
import time

from app.depth_capture_jobs import LocalComputeJobQueue


def _wait_until(predicate):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("队列等待超时")


def test_shared_queue_serializes_different_job_kinds_and_deduplicates_same_key():
    release = Event()
    started: list[tuple[str, str]] = []

    def first(_: str) -> None:
        started.append(("preprocessing", "project-001"))
        release.wait(timeout=2)

    def second(_: str) -> None:
        started.append(("depth_capture", "project-001"))

    queue = LocalComputeJobQueue()
    try:
        assert queue.submit("preprocessing", "project-001", first) is True
        assert queue.submit("preprocessing", "project-001", first) is False
        assert queue.submit("depth_capture", "project-001", second) is True
        _wait_until(lambda: started == [("preprocessing", "project-001")])
        release.set()
        _wait_until(lambda: started == [("preprocessing", "project-001"), ("depth_capture", "project-001")])
    finally:
        release.set()
        queue.shutdown()


def test_shared_queue_releases_key_after_failure_and_rejects_after_shutdown():
    ran = Event()

    def broken(_: str) -> None:
        ran.set()
        raise RuntimeError("expected")

    queue = LocalComputeJobQueue()
    assert queue.submit("depth_capture", "project-001", broken) is True
    assert ran.wait(timeout=2)
    _wait_until(lambda: not queue.is_active("depth_capture", "project-001"))
    assert queue.submit("depth_capture", "project-001", lambda _: None) is True
    queue.shutdown()
    assert queue.submit("depth_capture", "project-002", lambda _: None) is False


def test_shutdown_releases_a_cancelled_queued_key():
    release = Event()
    started = Event()

    def running(_: str) -> None:
        started.set()
        release.wait(timeout=2)

    queue = LocalComputeJobQueue()
    try:
        assert queue.submit("preprocessing", "project-001", running) is True
        assert started.wait(timeout=2)
        assert queue.submit("depth_capture", "project-002", lambda _: None) is True
        stopped = Thread(target=queue.shutdown)
        stopped.start()
        release.set()
        stopped.join(timeout=2)
        assert queue.is_active("depth_capture", "project-002") is False
    finally:
        release.set()
        queue.shutdown()
