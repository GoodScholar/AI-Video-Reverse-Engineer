import json
from threading import Event, Thread

import pytest

from app.generation_service import GenerationAdapter, GenerationAdapterRegistry, GenerationService


def _caps(**changes):
    return {"version": "fake-v1", "structuredSchema": True, "streaming": True, "references": False,
            "outputBudget": False, "timeout": False, "cancel": False, **changes}


def _service(tmp_path, execute):
    registry = GenerationAdapterRegistry()
    registry.register(GenerationAdapter("fake-text", "1", "text", execute, lambda _provider, _model: {
        "configurationVersion": "cfg-1", "capabilities": _caps()}))
    return GenerationService(tmp_path, registry=registry)


def test_registry_stream_persists_each_chunk_before_next_yield(tmp_path):
    service = None
    observed = []
    def execute(run):
        yield {"sequence": 0, "text": "first"}
        stored = json.loads((tmp_path / "project-files/p/generation-runs.json").read_text())
        observed.append(stored["runs"][0]["outputItems"][0]["chunks"])
        yield {"type": "done"}
    service = _service(tmp_path, execute)

    run = service.submit("p", disclosure={"provider": "fake", "model": "m", "prompt": "hello", "digest": "d", "briefRevision": 1, "accepted": True},
                         text_params={"structuredSchema": {"type": "object"}}, idempotency_key="one", caller="test")

    assert observed == [[{"sequence": 0, "text": "first"}]]
    assert run["status"] == "completed" and run["outcome"] == "completed"
    assert run["commonRunId"] and run["domainKind"] == "generation"


def test_strict_disclosure_and_unsupported_text_params_are_rejected(tmp_path):
    service = _service(tmp_path, lambda _run: "plain")
    for disclosure, text_params in [
        ({"provider": "fake", "model": "m", "prompt": "x", "digest": "d", "briefRevision": 1, "accepted": True, "apiKey": "canary"}, {}),
        ({"provider": "fake", "model": "m", "prompt": "x", "digest": "d", "briefRevision": 1, "accepted": True}, {"outputBudget": 10}),
    ]:
        try:
            service.submit("p", disclosure=disclosure, text_params=text_params, idempotency_key=str(len(disclosure)), caller="test")
        except Exception as error:
            assert getattr(error, "code", None) in {"generation_disclosure_invalid", "generation_capability_unavailable"}
        else:
            raise AssertionError("非法字段不可提交")


def test_plain_text_adapter_without_schema_and_second_registry_adapter_route(tmp_path):
    registry = GenerationAdapterRegistry()
    registry.register(GenerationAdapter("first", "1", "text", lambda _run: "wrong", lambda _p, _m: None))
    registry.register(GenerationAdapter("plain", "1", "text", lambda _run: "plain", lambda _p, _m: {
        "configurationVersion": "plain-v1", "capabilities": {"version": "plain", "structuredSchema": False,
        "streaming": False, "stableReferences": False, "outputBudget": False, "timeout": False, "cancel": False}}))
    service = GenerationService(tmp_path, registry=registry)
    run = service.submit("p", disclosure={"provider": "plain", "model": "m", "prompt": "x", "accepted": True},
                         text_params={}, idempotency_key="plain", caller="test")
    assert run["status"] == "completed"


def test_stream_gap_after_first_chunk_preserves_partial_text(tmp_path):
    service = _service(tmp_path, lambda _run: iter([{"sequence": 0, "text": "kept"}, {"sequence": 2, "text": "gap"}]))
    run = service.submit("p", disclosure={"provider": "fake", "model": "m", "prompt": "x", "accepted": True}, text_params={}, idempotency_key="gap", caller="test")
    assert run["outcome"] == "incomplete"
    assert run["outputItems"][0]["text"] == "kept"


def test_done_event_can_mark_partial_success_and_incomplete(tmp_path):
    service = _service(tmp_path, lambda _run: iter([{"sequence": 0, "text": "kept"}, {"type": "done", "outcome": "partial_success"}]))
    run = service.submit("p", disclosure={"provider": "fake", "model": "m", "prompt": "x", "accepted": True}, text_params={}, idempotency_key="partial", caller="test")
    assert run["outcome"] == "partial_success"

    incomplete = _service(
        tmp_path,
        lambda _run: iter([
            {"sequence": 0, "text": "still useful"},
            {"type": "done", "outcome": "incomplete"},
        ]),
    ).submit(
        "incomplete",
        disclosure={"provider": "fake", "model": "m", "prompt": "x", "accepted": True},
        text_params={},
        idempotency_key="incomplete",
        caller="test",
    )
    assert incomplete["status"] == "partial_success"
    assert incomplete["outcome"] == "incomplete"


def test_registry_does_not_fallback_after_selected_provider_error(tmp_path):
    registry = GenerationAdapterRegistry()
    registry.register(GenerationAdapter("broken", "1", "text", lambda _run: "x", lambda _p, _m: (_ for _ in ()).throw(RuntimeError("broken"))))
    registry.register(GenerationAdapter("fallback", "1", "text", lambda _run: "x", lambda _p, _m: {"configurationVersion":"v", "capabilities":_caps()}))
    try: GenerationService(tmp_path, registry=registry).capabilities("p", "m")
    except Exception as error: assert getattr(error, "code", None) == "generation_adapter_resolution_failed"
    else: raise AssertionError("resolver 异常不得静默换 adapter")


def test_timeout_preserves_late_text_as_run_output_without_auto_adoption(tmp_path):
    started = Event()
    release = Event()

    def execute(_run):
        started.set()
        release.wait(1.2)
        return "late provider text"

    registry = GenerationAdapterRegistry()
    registry.register(GenerationAdapter(
        "blocking", "1", "text", execute,
        lambda _provider, _model: {
            "configurationVersion": "timeout-config",
            "capabilities": _caps(timeout=True),
        },
    ))
    service = GenerationService(tmp_path, registry=registry)
    queued = service.submit(
        "timeout",
        disclosure={"provider": "fake", "model": "m", "prompt": "x", "accepted": True},
        text_params={"timeoutSeconds": 1},
        idempotency_key="timeout",
        caller="test",
    )

    assert queued["status"] == "completed"
    assert queued["billingUnknown"] is True
    assert queued["outcome"] == "late_success"
    assert queued["outputItems"][0]["text"] == "late provider text"
    assert service.public_run(queued)["executionSemantics"]["late"] is True
    assert not (tmp_path / "project-files/timeout/aigc-content/state.json").exists()
    assert started.is_set()
    release.set()


def test_running_cancel_preserves_late_text_without_auto_adoption(tmp_path):
    started = Event()
    release = Event()
    cancelled: list[str] = []

    def execute(_run):
        started.set()
        release.wait(3)
        return "late text"

    def cancel(run):
        cancelled.append(run["id"])

    registry = GenerationAdapterRegistry()
    registry.register(GenerationAdapter(
        "cancellable", "1", "text", execute,
        lambda _provider, _model: {
            "configurationVersion": "cancel-config",
            "capabilities": _caps(cancel=True),
        },
        cancel=cancel,
    ))

    class HeldQueue:
        def submit(self, *_args, **_kwargs):
            return True

    service = GenerationService(tmp_path, registry=registry, queue=HeldQueue())
    queued = service.submit(
        "cancel",
        disclosure={"provider": "fake", "model": "m", "prompt": "x", "accepted": True},
        text_params={},
        idempotency_key="cancel",
        caller="test",
    )
    worker = Thread(target=service._execute, args=("cancel", queued["id"]), daemon=True)
    worker.start()
    assert started.wait(1)

    cancelled_run = service.cancel("cancel", queued["id"])
    assert cancelled_run["status"] == "cancelled"
    assert cancelled == [queued["id"]]
    with pytest.raises(Exception) as error:
        service.cancel("cancel", queued["id"])
    assert getattr(error.value, "code", None) == "generation_cancel_invalid"
    assert cancelled == [queued["id"]]

    release.set()
    worker.join(1)
    late = service.get_run("cancel", queued["id"])
    assert late["status"] == "completed" and late["outcome"] == "late_success"
    assert late["outputItems"][0]["text"] == "late text"
    assert service.public_run(late)["executionSemantics"]["late"] is True
    assert not (tmp_path / "project-files/cancel/aigc-content/state.json").exists()


def test_remote_cancel_failure_preserves_late_text_without_auto_adoption(tmp_path):
    started = Event()
    release = Event()

    def execute(_run):
        started.set()
        release.wait(3)
        return "late text"

    def cancel(_run):
        raise RuntimeError("remote cancellation failed")

    registry = GenerationAdapterRegistry()
    registry.register(GenerationAdapter(
        "cancel-failure", "1", "text", execute,
        lambda _provider, _model: {
            "configurationVersion": "cancel-failure-config",
            "capabilities": _caps(cancel=True),
        },
        cancel=cancel,
    ))

    class HeldQueue:
        def submit(self, *_args, **_kwargs):
            return True

    service = GenerationService(tmp_path, registry=registry, queue=HeldQueue())
    queued = service.submit(
        "cancel-failure",
        disclosure={"provider": "fake", "model": "m", "prompt": "x", "accepted": True},
        text_params={},
        idempotency_key="cancel-failure",
        caller="test",
    )
    worker = Thread(target=service._execute, args=("cancel-failure", queued["id"]), daemon=True)
    worker.start()
    assert started.wait(1)

    failed = service.cancel("cancel-failure", queued["id"])
    assert failed["status"] == "failed"
    assert failed["outcome"] == "unknown"
    assert failed["billingUnknown"] is True

    release.set()
    worker.join(1)
    late = service.get_run("cancel-failure", queued["id"])
    assert late["status"] == "completed" and late["outcome"] == "late_success"
    assert late["outputItems"][0]["text"] == "late text"
    assert service.public_run(late)["executionSemantics"]["late"] is True
    assert not (tmp_path / "project-files/cancel-failure/aigc-content/state.json").exists()


def test_provider_and_result_validation_failures_leave_billing_unknown(tmp_path):
    provider_failed = _service(
        tmp_path,
        lambda _run: (_ for _ in ()).throw(RuntimeError("network down")),
    ).submit(
        "provider-failed",
        disclosure={"provider": "fake", "model": "m", "prompt": "x", "accepted": True},
        text_params={},
        idempotency_key="provider-failed",
        caller="test",
    )
    assert provider_failed["status"] == "failed"
    assert provider_failed["billingUnknown"] is True

    registry = GenerationAdapterRegistry()
    registry.register(GenerationAdapter(
        "invalid-result", "1", "text", lambda _run: "not schema",
        lambda _provider, _model: {"configurationVersion": "validator", "capabilities": _caps()},
        result_validator=lambda _text: (_ for _ in ()).throw(ValueError("invalid result")),
    ))
    invalid_result = GenerationService(tmp_path, registry=registry).submit(
        "validator-failed",
        disclosure={"provider": "fake", "model": "m", "prompt": "x", "accepted": True},
        text_params={},
        idempotency_key="validator-failed",
        caller="test",
    )
    assert invalid_result["reason"] == "generation_result_invalid"
    assert invalid_result["billingUnknown"] is True


def test_text_params_strictly_allow_only_declared_text_or_image_references(tmp_path):
    catalog = lambda _provider, _model: {
        "configurationVersion": "all-options",
        "capabilities": _caps(
            references=True,
            outputBudget=True,
            timeout=True,
            streaming=True,
        ),
    }
    service = GenerationService(tmp_path, text_adapter=lambda _run: "ok", capability_catalog=catalog)
    valid = service.submit(
        "strict",
        disclosure={"provider": "fake", "model": "m", "prompt": "x", "accepted": True},
        text_params={
            "references": [
                {"kind": "text", "id": "text-1", "versionId": "v1"},
                {"kind": "image", "id": "image-1", "versionId": "v2"},
            ],
            "outputBudget": 100,
            "timeoutSeconds": 1,
            "stream": True,
        },
        idempotency_key="valid",
        caller="test",
    )
    assert valid["status"] == "completed"

    invalid_params = [
        {"references": [{"kind": "video", "id": "video-1", "versionId": "v1"}]},
        {"references": [{"kind": "text", "id": "text-1"}]},
        {"outputBudget": 0},
        {"timeoutSeconds": False},
        {"stream": False},
    ]
    for index, params in enumerate(invalid_params):
        with pytest.raises(Exception) as error:
            service.submit(
                "strict",
                disclosure={"provider": "fake", "model": "m", "prompt": "x", "accepted": True},
                text_params=params,
                idempotency_key=f"invalid-{index}",
                caller="test",
            )
        assert getattr(error.value, "code", None) in {
            "generation_request_invalid",
            "generation_capability_unavailable",
        }
