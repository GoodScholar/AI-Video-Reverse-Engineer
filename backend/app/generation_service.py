"""Project-scoped durable text GenerationRun facade and adapter registry.

The service owns only execution records. Callers keep their domain data and adopt
completed immutable output explicitly.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock, Timer
from typing import Any, Callable, Iterable, Optional
from uuid import uuid4

from .durable_runs import freeze_run_input


TEXT_CAPABILITIES = {
    "version": "analysis-structured-text-v1",
    "structuredSchema": True,
    "streaming": False,
    "stableReferences": False,
    "outputBudget": False,
    "timeout": False,
    "cancel": False,
}

_CAPABILITY_KEYS = frozenset(TEXT_CAPABILITIES)
_DONE_OUTCOMES = frozenset({"completed", "partial_success", "incomplete"})
_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "unknown", "partial_success"})
_TEXT_PARAM_KEYS = frozenset({
    "structuredSchema",
    "references",
    "outputBudget",
    "timeoutSeconds",
    "stream",
})
_DISCLOSURE_KEYS = frozenset({"provider", "model", "prompt", "digest", "briefRevision", "accepted"})


class GenerationError(RuntimeError):
    """An API-safe failure with an outcome and conservative billing state."""

    def __init__(self, code: str, *, outcome: str = "failed", billing_unknown: bool = False):
        self.code = code
        self.outcome = outcome
        self.billing_unknown = billing_unknown
        super().__init__(code)


@dataclass(frozen=True)
class GenerationAdapter:
    adapter_id: str
    version: str
    modality: str
    execute: Callable[[dict[str, Any]], Any]
    capability_resolver: Callable[[str, str], Optional[dict[str, Any]]]
    cancel: Optional[Callable[[dict[str, Any]], None]] = None
    result_validator: Optional[Callable[[str], None]] = None


class GenerationAdapterRegistry:
    """Ordered registry; ``None`` says an adapter does not own a provider."""

    def __init__(self) -> None:
        self._items: dict[str, GenerationAdapter] = {}

    def register(self, adapter: GenerationAdapter) -> None:
        if adapter.adapter_id in self._items:
            raise ValueError("generation_adapter_duplicate")
        self._items[adapter.adapter_id] = adapter

    def get(self, adapter_id: str) -> Optional[GenerationAdapter]:
        return self._items.get(adapter_id)

    def resolve_by_id(self, adapter_id: str) -> GenerationAdapter:
        adapter = self.get(adapter_id)
        if adapter is None:
            raise GenerationError("generation_adapter_missing")
        return adapter

    def resolve(
        self,
        modality: str,
        provider: str,
        model: str,
    ) -> tuple[GenerationAdapter, dict[str, Any]]:
        for adapter in self._items.values():
            if adapter.modality != modality:
                continue
            try:
                catalog = adapter.capability_resolver(provider, model)
            except GenerationError:
                raise
            except Exception as error:
                raise GenerationError(
                    "generation_adapter_resolution_failed",
                    billing_unknown=True,
                ) from error
            if catalog is None:
                continue
            if not isinstance(catalog, dict):
                raise GenerationError("generation_capability_unavailable")
            return adapter, catalog
        raise GenerationError("generation_adapter_missing")


class GenerationRunStore:
    """Small atomic project-local persistence layer for GenerationRun records."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self.lock = RLock()

    def _path(self, project_id: str) -> Path:
        return self.root / "project-files" / project_id / "generation-runs.json"

    def load(self, project_id: str) -> list[dict[str, Any]]:
        with self.lock:
            path = self._path(project_id)
            if not path.exists():
                return []
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as error:
                raise GenerationError("generation_runs_invalid") from error
            if not isinstance(data, dict) or data.get("schemaVersion") != 1:
                raise GenerationError("generation_runs_invalid")
            if not isinstance(data.get("runs"), list):
                raise GenerationError("generation_runs_invalid")
            return data["runs"]

    def save(self, project_id: str, runs: list[dict[str, Any]]) -> None:
        with self.lock:
            path = self._path(project_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(
                dir=path.parent,
                prefix=".generation-runs-",
                suffix=".part",
            )
            temporary_path = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    json.dump(
                        {"schemaVersion": 1, "runs": runs},
                        output,
                        ensure_ascii=False,
                        separators=(",", ":"),
                        allow_nan=False,
                    )
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary_path, path)
            finally:
                temporary_path.unlink(missing_ok=True)


class GenerationService:
    def __init__(
        self,
        root: Path,
        *,
        registry: Optional[GenerationAdapterRegistry] = None,
        text_adapter: Optional[Callable[[dict[str, Any]], Any]] = None,
        capability_catalog: Optional[Callable[[str, str], Optional[dict[str, Any]]]] = None,
        queue: Any = None,
    ) -> None:
        self.store = GenerationRunStore(root)
        self.queue = queue
        self.lock = RLock()
        self.recovered_projects: set[str] = set()
        self.registry = registry or GenerationAdapterRegistry()
        if text_adapter is not None:
            self.registry.register(
                GenerationAdapter(
                    "legacy-text",
                    "compat-1",
                    "text",
                    text_adapter,
                    capability_catalog or self._default_capability_catalog,
                    getattr(text_adapter, "cancel", None),
                )
            )

    @staticmethod
    def _default_capability_catalog(provider: str, model: str) -> dict[str, Any]:
        return {
            "provider": provider,
            "model": model,
            "configurationVersion": "test",
            "capabilities": dict(TEXT_CAPABILITIES),
        }

    _default = _default_capability_catalog

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _capability_value(capabilities: dict[str, Any], key: str) -> Any:
        if key == "stableReferences" and key not in capabilities:
            return capabilities.get("references", False)
        return capabilities.get(key, False)

    def capabilities(self, provider: str, model: str) -> dict[str, Any]:
        adapter, catalog = self.registry.resolve("text", provider, model)
        raw_capabilities = catalog.get("capabilities")
        configuration_version = catalog.get("configurationVersion")
        if not isinstance(raw_capabilities, dict):
            raise GenerationError("generation_capability_unavailable")
        if not isinstance(raw_capabilities.get("version"), str):
            raise GenerationError("generation_capability_unavailable")
        if (not isinstance(configuration_version, (str, int))
                or isinstance(configuration_version, bool)
                or not str(configuration_version)):
            raise GenerationError("generation_capability_unavailable")

        normalized: dict[str, Any] = {"version": raw_capabilities["version"]}
        for key in _CAPABILITY_KEYS - {"version"}:
            value = self._capability_value(raw_capabilities, key)
            if type(value) is not bool:
                raise GenerationError("generation_capability_unavailable")
            normalized[key] = value
        normalized["references"] = normalized["stableReferences"]
        return {
            "adapterId": adapter.adapter_id,
            "adapterVersion": adapter.version,
            "provider": provider,
            "model": catalog.get("model", model),
            "configurationVersion": configuration_version,
            "capabilities": normalized,
        }

    def _recover(self, project_id: str) -> None:
        with self.lock:
            if project_id in self.recovered_projects:
                return
            self.recovered_projects.add(project_id)
            runs = self.store.load(project_id)
            changed = False
            for run in runs:
                if run.get("status") not in {"queued", "running", "cancel_requested"}:
                    continue
                for output in run.get("outputItems", []):
                    if output.get("status") in {"pending", "running"}:
                        output["status"] = "failed"
                run.update(
                    status="failed",
                    outcome="unknown",
                    reason="generation_recovery_unknown",
                    billingUnknown=True,
                    updatedAt=self._now(),
                )
                changed = True
            if changed:
                self.store.save(project_id, runs)

    def list_runs(self, project_id: str) -> list[dict[str, Any]]:
        self._recover(project_id)
        return self.store.load(project_id)

    def get_run(self, project_id: str, run_id: str) -> dict[str, Any]:
        self._recover(project_id)
        run = next((item for item in self.store.load(project_id) if item.get("id") == run_id), None)
        if run is None:
            raise GenerationError("generation_run_missing")
        return run

    @staticmethod
    def _validate_disclosure(disclosure: dict[str, Any]) -> None:
        required = {"provider", "model", "prompt", "accepted"}
        if not isinstance(disclosure, dict) or not required <= set(disclosure):
            raise GenerationError("generation_disclosure_invalid")
        if set(disclosure) - _DISCLOSURE_KEYS:
            raise GenerationError("generation_disclosure_invalid")
        if disclosure.get("accepted") is not True:
            raise GenerationError("generation_disclosure_invalid")
        for key in ("provider", "model", "prompt"):
            if not isinstance(disclosure.get(key), str):
                raise GenerationError("generation_disclosure_invalid")
        if "digest" in disclosure and not isinstance(disclosure["digest"], str):
            raise GenerationError("generation_disclosure_invalid")
        if "briefRevision" in disclosure and type(disclosure["briefRevision"]) is not int:
            raise GenerationError("generation_disclosure_invalid")

    @staticmethod
    def _validate_reference(reference: Any) -> bool:
        if not isinstance(reference, dict):
            return False
        if set(reference) != {"kind", "id", "versionId"}:
            return False
        if reference.get("kind") not in {"text", "image"}:
            return False
        return all(
            isinstance(reference.get(key), str) and reference[key]
            for key in ("id", "versionId")
        )

    def _validate_text_params(self, params: dict[str, Any], capabilities: dict[str, Any]) -> None:
        if set(params) - _TEXT_PARAM_KEYS:
            raise GenerationError("generation_request_invalid")
        if "structuredSchema" in params:
            if (
                not isinstance(params["structuredSchema"], dict)
                or not capabilities["structuredSchema"]
            ):
                raise GenerationError("generation_capability_unavailable")
        if "references" in params:
            references = params["references"]
            if not capabilities["stableReferences"]:
                raise GenerationError("generation_capability_unavailable")
            if (
                not isinstance(references, list)
                or not all(self._validate_reference(item) for item in references)
            ):
                raise GenerationError("generation_request_invalid")
        if "outputBudget" in params:
            if not capabilities["outputBudget"]:
                raise GenerationError("generation_capability_unavailable")
            if type(params["outputBudget"]) is not int or params["outputBudget"] <= 0:
                raise GenerationError("generation_request_invalid")
        if "timeoutSeconds" in params:
            if not capabilities["timeout"]:
                raise GenerationError("generation_capability_unavailable")
            if type(params["timeoutSeconds"]) is not int or params["timeoutSeconds"] <= 0:
                raise GenerationError("generation_request_invalid")
        if "stream" in params:
            if params["stream"] is not True or not capabilities["streaming"]:
                raise GenerationError("generation_capability_unavailable")

    def submit(
        self,
        project_id: str,
        *,
        disclosure: dict[str, Any],
        text_params: Optional[dict[str, Any]] = None,
        idempotency_key: str,
        caller: str,
    ) -> dict[str, Any]:
        self._validate_disclosure(disclosure)
        if not isinstance(text_params, (dict, type(None))):
            raise GenerationError("generation_request_invalid")
        params = dict(text_params or {})
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise GenerationError("generation_request_invalid")
        if not isinstance(caller, str) or not 1 <= len(caller) <= 120:
            raise GenerationError("generation_request_invalid")

        catalog = self.capabilities(disclosure["provider"], disclosure["model"])
        self._validate_text_params(params, catalog["capabilities"])
        snapshot = {
            "modality": "text",
            "provider": disclosure["provider"],
            "model": disclosure["model"],
            "configurationVersion": catalog["configurationVersion"],
            "input": {"prompt": disclosure["prompt"], "textParams": params},
            "disclosure": dict(disclosure),
            "idempotencyKey": idempotency_key,
            "caller": caller,
            "routingPolicy": {"mode": "explicit", "fallback": False},
            "capabilitySnapshot": catalog["capabilities"],
            "adapterId": catalog["adapterId"],
            "adapterVersion": catalog["adapterVersion"],
        }
        frozen = freeze_run_input(snapshot)

        with self.lock:
            self.recovered_projects.add(project_id)
            runs = self.store.load(project_id)
            existing = next(
                (run for run in runs if run.get("idempotencyKey") == idempotency_key),
                None,
            )
            if existing is not None:
                if existing.get("inputFingerprint") != frozen.fingerprint:
                    raise GenerationError("generation_idempotency_conflict")
                return existing
            now = self._now()
            run_id = f"generation-run-{uuid4().hex}"
            run = {
                "id": run_id,
                "commonRunId": f"run-{uuid4().hex}",
                "domainKind": "generation",
                "attempt": 1,
                "status": "queued",
                "outcome": None,
                "reason": None,
                "createdAt": now,
                "updatedAt": now,
                **snapshot,
                "inputSnapshot": frozen.snapshot,
                "inputFingerprint": frozen.fingerprint,
                "billingUnknown": False,
                "logs": [],
                "outputItems": [{
                    "resultId": f"generation-result-{uuid4().hex}",
                    "status": "pending",
                    "chunks": [],
                    "text": "",
                }],
            }
            runs.append(run)
            self.store.save(project_id, runs)

        if self.queue is None:
            self._execute(project_id, run_id)
        elif not self.queue.submit(
            "generation-service",
            run_id,
            lambda _job_id: self._execute(project_id, run_id),
            project_id=project_id,
        ):
            self._final(project_id, run_id, "failed", "failed", "queue_unavailable", True)
        return self.get_run(project_id, run_id)

    def _mutate(
        self,
        project_id: str,
        run_id: str,
        mutation: Callable[[dict[str, Any]], None],
    ) -> Optional[dict[str, Any]]:
        with self.lock:
            runs = self.store.load(project_id)
            run = next((item for item in runs if item.get("id") == run_id), None)
            if run is None:
                return None
            mutation(run)
            run["updatedAt"] = self._now()
            self.store.save(project_id, runs)
            return run

    def _execute(self, project_id: str, run_id: str) -> None:
        def start(run: dict[str, Any]) -> None:
            if run.get("status") == "queued":
                run["status"] = "running"

        run = self._mutate(
            project_id,
            run_id,
            start,
        )
        if run is None or run.get("status") != "running":
            return
        try:
            adapter = self.registry.resolve_by_id(run["adapterId"])
        except GenerationError:
            self._final(project_id, run_id, "failed", "failed", "adapter_missing", True)
            return

        timer: Optional[Timer] = None
        timeout = run["input"]["textParams"].get("timeoutSeconds")
        if timeout is not None:
            timer = Timer(
                timeout,
                lambda: self._final(
                    project_id,
                    run_id,
                    "failed",
                    "timed_out",
                    "generation_timed_out",
                    True,
                ),
            )
            timer.daemon = True
            timer.start()

        try:
            self._consume_adapter_result(project_id, run_id, adapter, run)
        except GenerationError as error:
            self._record_generation_error(project_id, run_id, error)
        except Exception:
            self._final(project_id, run_id, "failed", "failed", "generation_provider_failed", True)
        finally:
            if timer is not None:
                timer.cancel()

    def _consume_adapter_result(
        self,
        project_id: str,
        run_id: str,
        adapter: GenerationAdapter,
        run: dict[str, Any],
    ) -> None:
        value = adapter.execute(run)
        if isinstance(value, str):
            self._append(project_id, run_id, 0, value)
            self._complete(project_id, run_id, adapter)
            return
        if not isinstance(value, Iterable):
            raise GenerationError(
                "generation_protocol_invalid",
                outcome="unknown",
                billing_unknown=True,
            )

        expected_sequence = 0
        done_outcome: Optional[str] = None
        for event in value:
            current = self.get_run(project_id, run_id)
            if current["status"] != "running":
                return
            if not isinstance(event, dict):
                raise GenerationError(
                    "generation_protocol_invalid",
                    outcome="unknown",
                    billing_unknown=True,
                )
            if event.get("type") == "done":
                outcome = event.get("outcome", "completed")
                if outcome not in _DONE_OUTCOMES:
                    raise GenerationError(
                        "generation_protocol_invalid",
                        outcome="unknown",
                        billing_unknown=True,
                    )
                done_outcome = outcome
                break
            if type(event.get("sequence")) is not int or not isinstance(event.get("text"), str):
                raise GenerationError(
                    "generation_protocol_invalid",
                    outcome="unknown",
                    billing_unknown=True,
                )
            if event["sequence"] != expected_sequence:
                raise GenerationError(
                    "generation_stream_incomplete",
                    outcome="incomplete",
                    billing_unknown=True,
                )
            self._append(project_id, run_id, expected_sequence, event["text"])
            expected_sequence += 1

        if done_outcome is None:
            raise GenerationError(
                "generation_stream_incomplete",
                outcome="incomplete",
                billing_unknown=True,
            )
        if done_outcome == "completed":
            self._complete(project_id, run_id, adapter)
            return
        self._final(
            project_id,
            run_id,
            "partial_success",
            done_outcome,
            "generation_stream_partial",
            True,
        )

    def _record_generation_error(
        self,
        project_id: str,
        run_id: str,
        error: GenerationError,
    ) -> None:
        status = "failed"
        if error.outcome in {"incomplete", "partial_success"}:
            status = "partial_success"
        self._final(project_id, run_id, status, error.outcome, error.code, True)

    def _append(self, project_id: str, run_id: str, sequence: int, text: str) -> None:
        def append(run: dict[str, Any]) -> None:
            if run["status"] != "running":
                return
            output = run["outputItems"][0]
            output["chunks"].append({"sequence": sequence, "text": text})
            output["text"] += text
            run["logs"].append({"event": "chunk", "sequence": sequence})

        self._mutate(project_id, run_id, append)

    def _complete(self, project_id: str, run_id: str, adapter: GenerationAdapter) -> None:
        text = self.get_run(project_id, run_id)["outputItems"][0]["text"]
        try:
            if adapter.result_validator is not None:
                adapter.result_validator(text)
        except Exception:
            self._final(project_id, run_id, "failed", "failed", "generation_result_invalid", True)
            return
        self._final(project_id, run_id, "completed", "completed", None, False)

    def _final(
        self,
        project_id: str,
        run_id: str,
        status: str,
        outcome: str,
        reason: Optional[str],
        billing_unknown: bool,
    ) -> None:
        def finish(run: dict[str, Any]) -> None:
            if run["status"] in _TERMINAL_STATUSES:
                return
            run.update(
                status=status,
                outcome=outcome,
                reason=reason,
                billingUnknown=billing_unknown,
            )
            output_status = "failed" if status == "failed" else outcome
            run["outputItems"][0]["status"] = output_status

        self._mutate(project_id, run_id, finish)

    def cancel(self, project_id: str, run_id: str) -> dict[str, Any]:
        run = self.get_run(project_id, run_id)
        if run["status"] == "queued":
            self._final(project_id, run_id, "cancelled", "cancelled", "generation_cancelled", True)
            return self.get_run(project_id, run_id)
        if run["status"] in _TERMINAL_STATUSES:
            raise GenerationError("generation_cancel_invalid")
        if run["status"] == "cancel_requested":
            return run
        if run["status"] != "running":
            raise GenerationError("generation_cancel_invalid")
        if not run["capabilitySnapshot"].get("cancel"):
            raise GenerationError("generation_cancel_unsupported")

        adapter = self.registry.resolve_by_id(run["adapterId"])
        if adapter.cancel is None:
            raise GenerationError("generation_cancel_unsupported")
        def claim(run: dict[str, Any]) -> None:
            if run["status"] == "running":
                run["status"] = "cancel_requested"

        claimed = self._mutate(
            project_id,
            run_id,
            claim,
        )
        if claimed is None:
            raise GenerationError("generation_run_missing")
        if claimed["status"] != "cancel_requested":
            if claimed["status"] in _TERMINAL_STATUSES:
                raise GenerationError("generation_cancel_invalid")
            return claimed
        try:
            adapter.cancel(claimed)
        except Exception:
            self._final(project_id, run_id, "failed", "unknown", "generation_cancel_unknown", True)
        else:
            self._final(project_id, run_id, "cancelled", "cancelled", "generation_cancelled", True)
        return self.get_run(project_id, run_id)
