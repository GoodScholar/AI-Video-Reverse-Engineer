from threading import Event, Lock, Thread
import time

from app.local_preprocessing_jobs import LocalPreprocessingJobQueue


def wait_until(predicate, *, description: str) -> None:
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"等待超时：{description}")


def test_queue_runs_projects_one_at_a_time_in_submission_order():
    release = Event()
    started: list[str] = []
    lock = Lock()

    def handle(project_id: str) -> None:
        with lock:
            started.append(project_id)
        if project_id == "project-001":
            release.wait(timeout=2)

    queue = LocalPreprocessingJobQueue(handle)
    try:
        assert queue.submit("project-001") is True
        assert queue.submit("project-002") is True
        wait_until(lambda: started == ["project-001"], description=f"当前已启动项目：{started}")
        release.set()
        wait_until(
            lambda: started == ["project-001", "project-002"],
            description=f"当前已启动项目：{started}",
        )
    finally:
        release.set()
        queue.shutdown()


def test_queue_rejects_same_project_until_handler_finishes():
    release = Event()
    queue = LocalPreprocessingJobQueue(lambda _: release.wait(timeout=2))
    try:
        assert queue.submit("project-001") is True
        assert queue.submit("project-001") is False
        release.set()
        wait_until(
            lambda: not queue.is_active("project-001"),
            description="project-001 仍处于活跃状态",
        )
    finally:
        release.set()
        queue.shutdown()


def test_queue_releases_project_after_handler_raises():
    started = Event()

    def handle(_: str) -> None:
        started.set()
        raise RuntimeError("处理失败")

    queue = LocalPreprocessingJobQueue(handle)
    try:
        assert queue.submit("project-001") is True
        assert started.wait(timeout=2)
        wait_until(
            lambda: not queue.is_active("project-001"),
            description="异常处理后 project-001 仍处于活跃状态",
        )
        assert queue.submit("project-001") is True
    finally:
        queue.shutdown()


def test_queue_rejects_submissions_after_shutdown():
    queue = LocalPreprocessingJobQueue(lambda _: None)

    queue.shutdown()

    assert queue.submit("project-001") is False


def test_shutdown_releases_cancelled_queued_project():
    release = Event()
    started: list[str] = []
    lock = Lock()
    shutdown_complete = Event()
    probe_count = 0

    def handle(project_id: str) -> None:
        with lock:
            started.append(project_id)
        release.wait(timeout=2)

    queue = LocalPreprocessingJobQueue(handle)
    try:
        assert queue.submit("project-001") is True
        wait_until(lambda: started == ["project-001"], description=f"当前已启动项目：{started}")
        assert queue.submit("project-002") is True

        shutdown_thread = Thread(
            target=lambda: (queue.shutdown(), shutdown_complete.set()),
        )
        shutdown_thread.start()

        def queue_is_closed() -> bool:
            nonlocal probe_count
            probe_count += 1
            return queue.submit(f"shutdown-probe-{probe_count}") is False

        wait_until(
            queue_is_closed,
            description="队列尚未进入关闭状态",
        )
        release.set()
        assert shutdown_complete.wait(timeout=2)
        shutdown_thread.join(timeout=2)

        assert started == ["project-001"]
        assert queue.is_active("project-001") is False
        assert queue.is_active("project-002") is False
    finally:
        release.set()
        queue.shutdown()
