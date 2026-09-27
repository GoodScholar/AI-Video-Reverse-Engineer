from threading import Event

import pytest

from app.durable_runs import (
    EXTERNAL_RUN_POLICY,
    LOCAL_RUN_POLICY,
    LocalComputeJobQueue,
    RunConflict,
    RunQueueAdapter,
    freeze_run_input,
)
from app.semantic_analysis_jobs import SemanticAnalysisQueueAdapter


def test_local_and_external_runs_share_states_without_inventing_local_unknown():
    assert LOCAL_RUN_POLICY.initial_status == "queued"
    assert EXTERNAL_RUN_POLICY.initial_status == "submitting"
    assert LOCAL_RUN_POLICY.active("queued")
    assert EXTERNAL_RUN_POLICY.active("unknown")

    with pytest.raises(RunConflict, match="本地运行不能进入未知状态"):
        LOCAL_RUN_POLICY.validate("unknown")

    assert EXTERNAL_RUN_POLICY.validate("unknown") == "unknown"


def test_restart_recovery_respects_local_and_external_capabilities():
    local = LOCAL_RUN_POLICY.recover_after_restart("running")
    external = EXTERNAL_RUN_POLICY.recover_after_restart("submitting")

    assert (local.status, local.reason) == ("failed", "interrupted")
    assert (external.status, external.reason) == ("unknown", "outcome_unknown")
    assert EXTERNAL_RUN_POLICY.recover_after_restart("queued").status == "queued"


def test_unknown_is_only_available_after_an_external_submission_may_have_started():
    assert EXTERNAL_RUN_POLICY.mark_outcome_unknown("submitting") == "unknown"
    assert EXTERNAL_RUN_POLICY.mark_outcome_unknown("queued") == "unknown"

    with pytest.raises(RunConflict, match="只有可能已提交"):
        EXTERNAL_RUN_POLICY.mark_outcome_unknown("completed")
    with pytest.raises(RunConflict, match="只有可能已提交"):
        LOCAL_RUN_POLICY.mark_outcome_unknown("queued")


def test_frozen_input_is_immutable_and_rejects_a_late_result_for_newer_input():
    source = {"revision": 3, "tracks": [{"id": "video"}]}
    frozen = freeze_run_input(source)
    source["tracks"][0]["id"] = "changed"

    assert frozen.snapshot == {"revision": 3, "tracks": [{"id": "video"}]}
    assert frozen.matches({"revision": 3, "tracks": [{"id": "video"}]})
    assert not frozen.matches({"revision": 4, "tracks": [{"id": "video"}]})

    with pytest.raises(RunConflict, match="运行输入已更新"):
        LOCAL_RUN_POLICY.complete(
            "running",
            frozen_input=frozen,
            current_input={"revision": 4, "tracks": [{"id": "video"}]},
            result={"output": "preview.mp4"},
        )


def test_cancelled_run_rejects_late_completion_and_completion_has_no_business_decision():
    frozen = freeze_run_input({"revision": 1})

    assert LOCAL_RUN_POLICY.cancel("running") == "cancelled"
    with pytest.raises(RunConflict, match="运行状态不允许完成"):
        LOCAL_RUN_POLICY.complete(
            "cancelled",
            frozen_input=frozen,
            current_input={"revision": 1},
            result={"output": "preview.mp4"},
        )

    completion = LOCAL_RUN_POLICY.complete(
        "running",
        frozen_input=frozen,
        current_input={"revision": 1},
        result={"output": "preview.mp4", "media": {"width": 720, "height": 1280}},
    )
    assert completion.status == "completed"
    assert completion.result == {
        "output": "preview.mp4",
        "media": {"width": 720, "height": 1280},
    }
    assert not hasattr(completion, "approved")
    assert not hasattr(completion, "adopted")
    assert not hasattr(completion, "delivered")


@pytest.mark.parametrize("decision", ["approved", "adopted", "delivered", "reviewDecision"])
def test_run_result_rejects_business_decisions(decision):
    frozen = freeze_run_input({"revision": 1})

    with pytest.raises(RunConflict, match="业务决定"):
        LOCAL_RUN_POLICY.complete(
            "running",
            frozen_input=frozen,
            current_input={"revision": 1},
            result={decision: True},
        )


def test_queue_adapter_gives_business_modules_a_narrow_submit_and_read_interface():
    seen = []
    started = Event()
    release = Event()

    def handle(owner_id):
        seen.append(owner_id)
        started.set()
        release.wait(timeout=2)

    queue = LocalComputeJobQueue()
    preview = RunQueueAdapter(queue, "preview", handle)
    try:
        assert preview.submit("project-1") is True
        assert started.wait(timeout=2)
        assert preview.is_active("project-1") is True
        release.set()
        preview.wait("project-1")
        assert seen == ["project-1"]
        assert preview.is_active("project-1") is False
    finally:
        release.set()
        queue.shutdown()


def test_legacy_local_compute_import_points_to_the_shared_queue():
    from app.depth_capture_jobs import LocalComputeJobQueue as LegacyLocalComputeJobQueue

    assert LegacyLocalComputeJobQueue is LocalComputeJobQueue


def test_semantic_analysis_uses_the_shared_queue_through_a_narrow_adapter():
    calls = []

    class FakeQueue:
        def submit(self, run_kind, owner_id, handler):
            calls.append((run_kind, owner_id))
            handler(owner_id)
            return True

        def is_active(self, run_kind, owner_id):
            return (run_kind, owner_id) in calls

    handled = []
    queue = SemanticAnalysisQueueAdapter(FakeQueue(), handled.append)

    assert queue.submit("project-1") is True
    assert queue.is_active("project-1") is True
    assert calls == [("semantic-analysis", "project-1")]
    assert handled == ["project-1"]
