import json

import pytest

from app.generation_service import GenerationError, GenerationService
from app.project_backup import check_idle


def _disclosure(prompt="为晴雨杯生成脚本"):
    return {
        "provider": "fake", "model": "structured-model", "prompt": prompt,
        "digest": "test-digest", "briefRevision": 1, "accepted": True,
    }


def test_fake_adapter_orders_chunks_and_marks_unterminated_stream_incomplete(tmp_path):
    service = GenerationService(
        tmp_path,
        text_adapter=lambda _run: {"chunks": [
            {"sequence": 1, "text": "后半"}, {"sequence": 0, "text": "前半"},
        ], "complete": False},
    )

    run = service.submit("project-1", disclosure=_disclosure(), text_params={"structuredSchema": {"type": "object"}}, idempotency_key="stream-1", caller="test")

    assert run["status"] == "failed"
    assert run["outcome"] == "unknown"
    assert run["billingUnknown"] is True
    assert run["outputItems"][0]["chunks"] == []
    assert run["outputItems"][0]["status"] == "failed"
    check_idle(run)


def test_idempotency_conflict_and_restart_recovery_do_not_retry_or_change_provider(tmp_path):
    calls = []
    service = GenerationService(tmp_path, text_adapter=lambda run: calls.append(run) or "{}")

    first = service.submit("project-1", disclosure=_disclosure(), text_params={"structuredSchema": {}}, idempotency_key="repeat", caller="test")
    same = service.submit("project-1", disclosure=_disclosure(), text_params={"structuredSchema": {}}, idempotency_key="repeat", caller="test")
    with pytest.raises(GenerationError, match="generation_idempotency_conflict"):
        service.submit("project-1", disclosure=_disclosure("changed"), text_params={"structuredSchema": {}}, idempotency_key="repeat", caller="test")

    assert same["id"] == first["id"]
    assert len(calls) == 1
    saved = tmp_path / "project-files/project-2/generation-runs.json"
    saved.parent.mkdir(parents=True)
    saved.write_text(json.dumps({"schemaVersion": 1, "runs": [{
        "id": "generation-run-interrupted", "status": "running", "provider": "fake", "outputItems": [
            {"resultId": "pending", "status": "pending", "chunks": [], "text": ""},
            {"resultId": "running", "status": "running", "chunks": [], "text": ""},
            {"resultId": "completed", "status": "completed", "chunks": [], "text": "done"},
        ],
    }]}), encoding="utf-8")

    restarted = GenerationService(tmp_path, text_adapter=lambda _run: pytest.fail("不得在恢复时自动重提"))

    recovered = restarted.get_run("project-2", "generation-run-interrupted")
    assert recovered["status"] == "failed"
    assert recovered["outcome"] == "unknown"
    assert recovered["billingUnknown"] is True
    assert [item["status"] for item in recovered["outputItems"]] == [
        "failed", "failed", "completed",
    ]
    check_idle(recovered)


def test_declared_cancel_and_timeout_are_terminal_and_late_provider_result_is_ignored(tmp_path):
    class FakeAdapter:
        def __init__(self): self.cancelled = []; self.calls = 0
        def __call__(self, _run): self.calls += 1; return "late result"
        def cancel(self, run): self.cancelled.append(run["id"])

    class HeldQueue:
        def submit(self, *_args, **_kwargs): return True

    adapter = FakeAdapter()
    service = GenerationService(tmp_path, text_adapter=adapter, queue=HeldQueue(), capability_catalog=lambda provider, model: {
        "provider": provider, "model": model, "configurationVersion": "fake-v1",
        "capabilities": {"version": "fake-v1", "structuredSchema": True, "streaming": True,
                         "stableReferences": False, "outputBudget": False, "timeout": True, "cancel": True,
                         "undeclaredInternalOption": True},
    })
    run = service.submit("project-1", disclosure=_disclosure(), text_params={"structuredSchema": {}}, idempotency_key="cancel", caller="test")
    assert "undeclaredInternalOption" not in run["capabilitySnapshot"]

    cancelled = service.cancel("project-1", run["id"])
    service._execute("project-1", run["id"])
    assert cancelled["status"] == "cancelled"
    assert service.get_run("project-1", run["id"])["status"] == "cancelled"
    assert service.get_run("project-1", run["id"])["routeAttempts"][-1]["fallbackDecision"] == "fallback_not_requested_cancelled"
    assert adapter.cancelled == []
    assert adapter.calls == 0

    later = service.submit("project-1", disclosure=_disclosure("timeout"), text_params={"structuredSchema": {}}, idempotency_key="timeout", caller="test")
    assert service.get_run("project-1", later["id"])["status"] == "queued"


def test_fake_adapter_maps_errors_and_rejects_conflicting_or_undeclared_protocol_options(tmp_path):
    failed = GenerationService(tmp_path, text_adapter=lambda _run: (_ for _ in ()).throw(GenerationError("provider_rate_limited")))
    assert failed.submit("failed", disclosure=_disclosure(), text_params={"structuredSchema": {}}, idempotency_key="failed", caller="test")["status"] == "failed"

    conflicting = GenerationService(tmp_path, text_adapter=lambda _run: {"complete": True, "chunks": [
        {"sequence": 0, "text": "A"}, {"sequence": 0, "text": "B"},
    ]})
    conflict_run = conflicting.submit("conflict", disclosure=_disclosure(), text_params={"structuredSchema": {}}, idempotency_key="conflict", caller="test")
    assert conflict_run["status"] == "failed"
    assert conflict_run["outcome"] == "unknown"
    assert conflict_run["billingUnknown"] is True

    unsupported = GenerationService(tmp_path, text_adapter=lambda _run: "never", capability_catalog=lambda provider, model: {
        "provider": provider, "model": model, "configurationVersion": "bad",
        "capabilities": {"version": "bad", "streaming": True},
    })
    with pytest.raises(GenerationError, match="generation_capability_unavailable"):
        unsupported.submit("unsupported", disclosure=_disclosure(), text_params={"structuredSchema": {}}, idempotency_key="unsupported", caller="test")
