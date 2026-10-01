"""Project-scoped durable GenerationRun facade and adapter registry.

The service owns only execution records. Callers keep their domain data and adopt
completed immutable output explicitly.
"""

from __future__ import annotations

import json
import io
import os
import re
import stat
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock, RLock, Timer
from typing import Any, BinaryIO, Callable, Iterable, Optional, Protocol
from uuid import uuid4

from PIL import Image, UnidentifiedImageError

from .durable_runs import EXTERNAL_RUN_POLICY, freeze_run_input
from .generation_routing import GenerationRoutingConflict, GenerationRoutingPolicyError, GenerationRoutingPolicyStore
from .project_assets import ProjectAssets, ResultInstallationConflict
from .video_media import probe_video


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
_IMAGE_CAPABILITY_KEYS = frozenset({
    "version", "referenceTypes", "minReferences", "maxReferences",
    "imageEditing", "superResolution", "aspectRatios", "resolutions",
    "minResults", "maxResults", "cancel", "timeout",
})
_IMAGE_PARAM_KEYS = frozenset({
    "operation", "references", "aspectRatio", "resolution", "resultCount",
    "timeoutSeconds",
})
_IMAGE_OPERATIONS = frozenset({"generate", "edit", "super-resolution"})
_IMAGE_REFERENCE_TYPES = frozenset({"reference", "edit-source", "upscale-source"})
_IMAGE_SUCCESS_STATUSES = frozenset({"completed", "reused"})
_MAX_IMAGE_PIXELS = 64_000_000
_VIDEO_CAPABILITY_KEYS = frozenset({"version", "profiles", "cancel"})
_VIDEO_PROFILE_KEYS = frozenset({
    "id", "mode", "referencePatterns", "combinations", "minResults", "maxResults",
})
_VIDEO_COMBINATION_KEYS = frozenset({
    "durationSeconds", "aspectRatio", "resolution", "audio",
})
_VIDEO_PARAM_KEYS = frozenset({
    "profileId", "mode", "references", "durationSeconds", "aspectRatio", "resolution", "audio", "resultCount",
})
_VIDEO_MODES = frozenset({"text-to-video", "image-to-video"})
_VIDEO_REFERENCE_TYPES = frozenset({"first-frame", "last-frame", "reference"})
_VIDEO_SUCCESS_STATUSES = frozenset({"completed", "reused"})
_DEFAULT_MAX_VIDEO_RESULT_BYTES = 200_000_000
VIDEO_MAX_REFERENCES = 32
VIDEO_MAX_RESULTS = 64
VIDEO_MAX_PROFILE_ID_LENGTH = 120
VIDEO_MAX_OPTION_LENGTH = 32
_VIDEO_MAX_REMOTE_ID_LENGTH = 512
_VIDEO_MAX_CURSOR_LENGTH = 4096
_VIDEO_MAX_RESULT_NAME_LENGTH = 255
_VIDEO_ERROR_CODE = re.compile(r"[a-z][a-z0-9_.-]{0,119}")


class _VideoResultTooLarge(OSError):
    pass


class VideoResultWriter(Protocol):
    def write(self, value: bytes) -> int: ...
    def flush(self) -> None: ...


class _BoundedVideoWriter:
    __slots__ = ("__output", "limit", "written")

    def __init__(self, output: BinaryIO, limit: int) -> None:
        self.__output = output
        self.limit = limit
        self.written = 0

    def write(self, value: bytes) -> int:
        if not isinstance(value, bytes):
            raise TypeError("video download chunks must be bytes")
        if self.written + len(value) > self.limit:
            raise _VideoResultTooLarge("generation_result_too_large")
        count = self.__output.write(value)
        if count != len(value):
            raise OSError("generation_result_download_failed")
        self.written += count
        return count

    def flush(self) -> None:
        self.__output.flush()


class GenerationError(RuntimeError):
    """An API-safe failure with an outcome and conservative billing state."""

    def __init__(self, code: str, *, outcome: str = "failed", billing_unknown: bool = False, safe_to_fallback: bool = False):
        self.code = code
        self.outcome = outcome
        self.billing_unknown = billing_unknown
        self.safe_to_fallback = safe_to_fallback
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


@dataclass(frozen=True)
class VideoGenerationAdapter:
    """Protocol boundary for externally managed asynchronous video jobs."""

    adapter_id: str
    version: str
    modality: str
    capability_resolver: Callable[[str, str], Optional[dict[str, Any]]]
    catalog: Callable[[], list[dict[str, Any]]]
    submit: Callable[[dict[str, Any]], dict[str, Any]]
    poll: Callable[[dict[str, Any]], dict[str, Any]]
    download: Callable[[dict[str, Any], dict[str, Any], VideoResultWriter], None]
    cancel: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None


class GenerationAdapterRegistry:
    """Ordered registry; ``None`` says an adapter does not own a provider."""

    def __init__(self) -> None:
        self._items: dict[str, GenerationAdapter | VideoGenerationAdapter] = {}

    def register(self, adapter: GenerationAdapter | VideoGenerationAdapter) -> None:
        if adapter.adapter_id in self._items:
            raise ValueError("generation_adapter_duplicate")
        self._items[adapter.adapter_id] = adapter

    def get(self, adapter_id: str) -> Optional[GenerationAdapter | VideoGenerationAdapter]:
        return self._items.get(adapter_id)

    def resolve_by_id(self, adapter_id: str) -> GenerationAdapter | VideoGenerationAdapter:
        adapter = self.get(adapter_id)
        if adapter is None:
            raise GenerationError("generation_adapter_missing")
        return adapter

    def resolve(
        self,
        modality: str,
        provider: str,
        model: str,
    ) -> tuple[GenerationAdapter | VideoGenerationAdapter, dict[str, Any]]:
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
                    billing_unknown=False,
                ) from error
            if catalog is None:
                continue
            if not isinstance(catalog, dict):
                raise GenerationError("generation_capability_unavailable")
            return adapter, catalog
        raise GenerationError("generation_adapter_missing")

    def adapters(self, modality: str) -> list[GenerationAdapter | VideoGenerationAdapter]:
        return [adapter for adapter in self._items.values() if adapter.modality == modality]


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
        ffmpeg_path: str = "ffmpeg",
        ffprobe_path: str = "ffprobe",
        max_video_result_bytes: int = _DEFAULT_MAX_VIDEO_RESULT_BYTES,
        routing_policies: Optional[GenerationRoutingPolicyStore] = None,
    ) -> None:
        self.store = GenerationRunStore(root)
        self.assets = ProjectAssets(root)
        self.queue = queue
        self.lock = RLock()
        self.video_operation_locks: dict[tuple[str, str], Lock] = {}
        self.recovered_projects: set[str] = set()
        self.registry = registry or GenerationAdapterRegistry()
        self.routing_policies = routing_policies or GenerationRoutingPolicyStore(Path(root) / "generation-routing.json")
        self.ffmpeg_path = ffmpeg_path
        self.ffprobe_path = ffprobe_path
        if type(max_video_result_bytes) is not int or max_video_result_bytes <= 0:
            raise ValueError("max_video_result_bytes must be positive")
        self.max_video_result_bytes = max_video_result_bytes
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

    def capabilities(self, provider: str, model: str, *, modality: str = "text") -> dict[str, Any]:
        adapter, catalog = self.registry.resolve(modality, provider, model)
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

        if modality == "text":
            normalized = self._normalize_text_capabilities(raw_capabilities)
        elif modality == "image":
            normalized = self._normalize_image_capabilities(raw_capabilities)
        elif modality == "video" and isinstance(adapter, VideoGenerationAdapter):
            normalized = self._normalize_video_capabilities(raw_capabilities)
        else:
            raise GenerationError("generation_capability_unavailable")
        return {
            "adapterId": adapter.adapter_id,
            "adapterVersion": adapter.version,
            "provider": provider,
            "model": catalog.get("model", model),
            "configurationVersion": configuration_version,
            "capabilities": normalized,
        }

    def routing_policy(self, policy_id: str, revision: int) -> dict[str, Any]:
        """Resolve a saved policy without exposing any provider configuration."""
        try:
            return self.routing_policies.get(policy_id, revision=revision)
        except GenerationRoutingConflict as error:
            raise GenerationError("generation_routing_policy_revision_conflict") from error
        except GenerationRoutingPolicyError as error:
            raise GenerationError(str(error)) from error

    def _routing_plan(
        self,
        modality: str,
        disclosure: dict[str, Any],
        routing_policy_ref: Optional[dict[str, Any]],
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        explicit = {"provider": disclosure["provider"], "model": disclosure["model"]}
        if routing_policy_ref is None:
            return ({"mode": "explicit", "fallback": False}, [explicit])
        if (
            not isinstance(routing_policy_ref, dict) or set(routing_policy_ref) != {"id", "revision"}
            or not isinstance(routing_policy_ref.get("id"), str)
            or type(routing_policy_ref.get("revision")) is not int
        ):
            raise GenerationError("generation_routing_policy_invalid")
        policy = self.routing_policy(routing_policy_ref["id"], routing_policy_ref["revision"])
        if policy["modality"] != modality or policy["routes"][0] != explicit:
            raise GenerationError("generation_routing_policy_invalid")
        candidates = [dict(route) for route in policy["routes"]]
        return (
            {"mode": "saved", "fallback": True, "id": policy["id"], "revision": policy["revision"]},
            candidates,
        )

    def _routing_snapshot(self, modality: str, policy: dict[str, Any], candidates: list[dict[str, Any]]) -> dict[str, Any]:
        if policy["mode"] == "explicit":
            return {"mode": "explicit", "fallback": False, "candidates": candidates}
        return {"id": policy["id"], "revision": policy["revision"], "modality": modality, "candidates": candidates}

    @staticmethod
    def _route_attempt(catalog: dict[str, Any], route: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider": route["provider"], "model": route["model"],
            "adapterId": catalog["adapterId"], "adapterVersion": catalog["adapterVersion"],
            "configurationVersion": catalog["configurationVersion"],
            "capabilityVersion": catalog["capabilities"]["version"],
            "status": "selected", "failureCode": None, "fallbackDecision": "selected",
        }

    def _freeze_route_candidates(self, modality: str, candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Resolve every saved candidate once at submission; never re-read catalog for an old run."""
        frozen = []
        for route in candidates:
            catalog = self.capabilities(route["provider"], route["model"], modality=modality)
            frozen.append({
                "provider": route["provider"], "model": route["model"],
                "adapterId": catalog["adapterId"], "adapterVersion": catalog["adapterVersion"],
                "configurationVersion": catalog["configurationVersion"], "capabilitySnapshot": catalog["capabilities"],
            })
        return frozen

    @staticmethod
    def _catalog_from_frozen(candidate: dict[str, Any]) -> dict[str, Any]:
        return {
            "provider": candidate["provider"], "model": candidate["model"],
            "adapterId": candidate["adapterId"], "adapterVersion": candidate["adapterVersion"],
            "configurationVersion": candidate["configurationVersion"], "capabilities": candidate["capabilitySnapshot"],
        }

    def _normalize_text_capabilities(self, raw: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {"version": raw["version"]}
        for key in _CAPABILITY_KEYS - {"version"}:
            value = self._capability_value(raw, key)
            if type(value) is not bool:
                raise GenerationError("generation_capability_unavailable")
            normalized[key] = value
        normalized["references"] = normalized["stableReferences"]
        return normalized

    @staticmethod
    def _normalize_image_capabilities(raw: dict[str, Any]) -> dict[str, Any]:
        if set(raw) != _IMAGE_CAPABILITY_KEYS:
            raise GenerationError("generation_capability_unavailable")
        reference_types = raw.get("referenceTypes")
        aspect_ratios = raw.get("aspectRatios")
        resolutions = raw.get("resolutions")
        if (
            not isinstance(reference_types, list)
            or len(reference_types) != len(set(reference_types))
            or any(value not in _IMAGE_REFERENCE_TYPES for value in reference_types)
            or not isinstance(aspect_ratios, list)
            or not aspect_ratios
            or len(aspect_ratios) != len(set(aspect_ratios))
            or any(not isinstance(value, str) or not value for value in aspect_ratios)
            or not isinstance(resolutions, list)
            or not resolutions
            or len(resolutions) != len(set(resolutions))
            or any(not isinstance(value, str) or not value for value in resolutions)
        ):
            raise GenerationError("generation_capability_unavailable")
        for key in ("imageEditing", "superResolution", "cancel", "timeout"):
            if type(raw.get(key)) is not bool:
                raise GenerationError("generation_capability_unavailable")
        for minimum, maximum in (("minReferences", "maxReferences"), ("minResults", "maxResults")):
            if (
                type(raw.get(minimum)) is not int
                or type(raw.get(maximum)) is not int
                or raw[minimum] < (0 if minimum == "minReferences" else 1)
                or raw[maximum] < raw[minimum]
            ):
                raise GenerationError("generation_capability_unavailable")
        if (
            (raw["maxReferences"] == 0) != (len(reference_types) == 0)
            or raw["imageEditing"] and "edit-source" not in reference_types
            or raw["superResolution"] and "upscale-source" not in reference_types
        ):
            raise GenerationError("generation_capability_unavailable")
        return {key: raw[key] for key in _IMAGE_CAPABILITY_KEYS}

    @staticmethod
    def _normalize_video_capabilities(raw: dict[str, Any]) -> dict[str, Any]:
        if set(raw) != _VIDEO_CAPABILITY_KEYS or type(raw.get("cancel")) is not bool:
            raise GenerationError("generation_capability_unavailable")
        if not isinstance(raw.get("version"), str) or not 1 <= len(raw["version"]) <= 120:
            raise GenerationError("generation_capability_unavailable")
        profiles = raw.get("profiles")
        if not isinstance(profiles, list) or not profiles:
            raise GenerationError("generation_capability_unavailable")
        normalized_profiles = []
        profile_ids: set[str] = set()
        for profile in profiles:
            if not isinstance(profile, dict) or set(profile) != _VIDEO_PROFILE_KEYS:
                raise GenerationError("generation_capability_unavailable")
            profile_id, mode = profile.get("id"), profile.get("mode")
            patterns, combinations = profile.get("referencePatterns"), profile.get("combinations")
            if (
                not isinstance(profile_id, str) or not 1 <= len(profile_id) <= VIDEO_MAX_PROFILE_ID_LENGTH
                or profile_id in profile_ids
                or mode not in _VIDEO_MODES
                or not isinstance(patterns, list) or not patterns
                or not isinstance(combinations, list) or not combinations
            ):
                raise GenerationError("generation_capability_unavailable")
            profile_ids.add(profile_id)
            normalized_patterns: list[list[str]] = []
            seen_patterns: set[tuple[str, ...]] = set()
            for pattern in patterns:
                if (
                    not isinstance(pattern, list)
                    or len(pattern) > VIDEO_MAX_REFERENCES
                    or any(reference_type not in _VIDEO_REFERENCE_TYPES for reference_type in pattern)
                    or pattern.count("first-frame") > 1
                    or pattern.count("last-frame") > 1
                    or (mode == "text-to-video" and pattern != [])
                    or (mode == "image-to-video" and not pattern)
                    or tuple(pattern) in seen_patterns
                ):
                    raise GenerationError("generation_capability_unavailable")
                seen_patterns.add(tuple(pattern))
                normalized_patterns.append(list(pattern))
            normalized_combinations = []
            seen_combinations: set[tuple[Any, ...]] = set()
            for combination in combinations:
                if not isinstance(combination, dict) or set(combination) != _VIDEO_COMBINATION_KEYS:
                    raise GenerationError("generation_capability_unavailable")
                identity = (
                    combination.get("durationSeconds"), combination.get("aspectRatio"),
                    combination.get("resolution"), combination.get("audio"),
                )
                if (
                    type(identity[0]) is not int or not 1 <= identity[0] <= 300
                    or not isinstance(identity[1], str) or not 1 <= len(identity[1]) <= VIDEO_MAX_OPTION_LENGTH
                    or not isinstance(identity[2], str) or not 1 <= len(identity[2]) <= VIDEO_MAX_OPTION_LENGTH
                    or type(identity[3]) is not bool
                    or identity in seen_combinations
                ):
                    raise GenerationError("generation_capability_unavailable")
                seen_combinations.add(identity)
                normalized_combinations.append(dict(combination))
            minimum, maximum = profile.get("minResults"), profile.get("maxResults")
            if (
                type(minimum) is not int or type(maximum) is not int or minimum < 1
                or maximum < minimum or maximum > VIDEO_MAX_RESULTS
            ):
                raise GenerationError("generation_capability_unavailable")
            normalized_profiles.append({
                "id": profile_id,
                "mode": mode,
                "referencePatterns": normalized_patterns,
                "combinations": normalized_combinations,
                "minResults": minimum,
                "maxResults": maximum,
            })
        return {"version": raw["version"], "profiles": normalized_profiles, "cancel": raw["cancel"]}

    @staticmethod
    def _valid_video_protocol_text(value: Any, *, maximum: int, allow_path: bool = True) -> bool:
        if not isinstance(value, str) or not 1 <= len(value) <= maximum:
            return False
        if value != value.strip():
            return False
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            return False
        lowered = value.lower()
        if lowered.startswith(("http://", "https://", "file://")):
            return False
        if not allow_path:
            normalized_path = value.replace("\\", "/")
            segments = normalized_path.split("/")
            if (
                normalized_path.startswith("/")
                or re.match(r"^[a-zA-Z]:/", normalized_path)
                or any(segment in {".", ".."} for segment in segments)
            ):
                return False
        return True

    def list_video_models(self) -> list[dict[str, Any]]:
        models = []
        seen: set[tuple[str, str]] = set()
        for adapter in self.registry.adapters("video"):
            if not isinstance(adapter, VideoGenerationAdapter):
                continue
            try:
                entries = adapter.catalog()
            except Exception:
                continue
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict) or set(entry) != {"provider", "model", "label"}:
                    continue
                provider, model, label = entry.get("provider"), entry.get("model"), entry.get("label")
                if (
                    not all(isinstance(value, str) and value for value in (provider, model, label))
                    or len(provider) > 120 or len(model) > 256 or len(label) > 256
                ):
                    continue
                if (provider, model) in seen:
                    continue
                try:
                    catalog = self.capabilities(provider, model, modality="video")
                except GenerationError:
                    continue
                if catalog["adapterId"] != adapter.adapter_id:
                    continue
                seen.add((provider, model))
                models.append({
                    "provider": provider,
                    "model": catalog["model"],
                    "label": label,
                    "adapterId": adapter.adapter_id,
                    "adapterVersion": adapter.version,
                    "configurationVersion": catalog["configurationVersion"],
                    "capabilities": catalog["capabilities"],
                })
        return models

    def _recover(self, project_id: str) -> None:
        with self.lock:
            if project_id in self.recovered_projects:
                return
            self.recovered_projects.add(project_id)
            runs = self.store.load(project_id)
            changed = False
            for run in runs:
                if run.get("status") not in {"submitting", "queued", "running", "cancel_requested"}:
                    continue
                if run.get("modality") == "video" and isinstance(run.get("remoteTaskId"), str):
                    run["status"] = "running"
                    run["reason"] = None
                    run["updatedAt"] = self._now()
                    changed = True
                    continue
                if run.get("modality") == "video":
                    recovered = EXTERNAL_RUN_POLICY.recover_after_restart(run["status"])
                    status = recovered.status
                    if status != "unknown":
                        status = EXTERNAL_RUN_POLICY.mark_outcome_unknown(status)
                    for output in run.get("outputItems", []):
                        if output.get("status") in {"pending", "running"}:
                            output.update(status="unknown", reason="generation_submit_outcome_unknown", billingUnknown=True)
                    run.update(
                        status=status,
                        outcome="unknown",
                        reason="generation_submit_outcome_unknown",
                        billingUnknown=True,
                        updatedAt=self._now(),
                    )
                    attempts = run.get("routeAttempts")
                    if isinstance(attempts, list) and attempts and attempts[-1].get("status") == "selected":
                        attempts[-1].update(status="failed", failureCode="generation_recovery_unknown", fallbackDecision="fallback_blocked_unknown_outcome")
                    changed = True
                    continue
                for output in run.get("outputItems", []):
                    if output.get("status") in {"pending", "running"}:
                        output["status"] = "failed"
                if run.get("modality") == "image" and any(
                    item.get("status") in _IMAGE_SUCCESS_STATUSES
                    for item in run.get("outputItems", [])
                ):
                    run.update(status="partial_success", outcome="unknown")
                else:
                    run.update(status="failed", outcome="unknown")
                run.update(reason="generation_recovery_unknown", billingUnknown=True, updatedAt=self._now())
                attempts = run.get("routeAttempts")
                if isinstance(attempts, list) and attempts and attempts[-1].get("status") == "selected":
                    attempts[-1].update(status="partial" if run["status"] == "partial_success" else "failed",
                        failureCode="generation_recovery_unknown", fallbackDecision="fallback_blocked_unknown_outcome")
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
    def public_run(run: dict[str, Any]) -> dict[str, Any]:
        """Safe lifecycle projection shared by text, image and video HTTP routes."""
        keys = {
            "id", "commonRunId", "domainKind", "attempt", "modality", "status", "outcome", "reason",
            "createdAt", "updatedAt", "provider", "model", "configurationVersion", "capabilitySnapshot",
            "adapterId", "adapterVersion", "billingUnknown", "retryOf", "actualRoute", "routeAttempts",
            "routingPolicySnapshot", "localDisposition", "remoteCancelResult", "localCancelResult", "lastConfirmedRemoteStatus",
        }
        result = {key: run[key] for key in keys if key in run}
        outputs = []
        output_keys = {
            "index", "resultId", "status", "reason", "billingUnknown", "assetId", "assetVersionId", "url",
            "name", "width", "height", "duration", "text", "chunks",
        }
        for item in run.get("outputItems", []):
            if isinstance(item, dict):
                outputs.append({key: item[key] for key in output_keys if key in item})
        result["outputItems"] = outputs
        cancel_result = run.get("localCancelResult") or run.get("remoteCancelResult")
        if cancel_result in {"cancelled", "unknown", "unsupported"}:
            cancel = cancel_result
        elif run.get("status") == "cancel_requested":
            cancel = "requested"
        elif run.get("status") == "queued" and run.get("modality") in {"text", "image"}:
            cancel = "supported"
        elif run.get("capabilitySnapshot", {}).get("cancel"):
            cancel = "supported"
        else:
            cancel = "unsupported"
        cancel_support = "supported" if run.get("localCancelResult") == "cancelled" or (
            run.get("status") == "queued" and run.get("modality") in {"text", "image"}
        ) or run.get("capabilitySnapshot", {}).get("cancel") else "unsupported"
        result["executionSemantics"] = {
            "cancel": cancel,
            "cancelSupport": cancel_support,
            "cancelResult": cancel if cancel in {"cancelled", "unknown", "unsupported", "requested"} else None,
            "stateConfidence": "unknown" if run.get("status") == "unknown" or run.get("outcome") == "unknown"
            or run.get("lastConfirmedRemoteStatus") == "unknown" or str(run.get("reason") or "").endswith("_unknown") else "known",
            "late": run.get("outcome") == "late_success" or bool(run.get("lateResult")),
            "partial": run.get("status") == "partial_success" or run.get("outcome") in {"partial_success", "incomplete"},
            "billing": "unknown" if run.get("billingUnknown") else "known",
            "actualRoute": result.get("actualRoute", {"provider": run.get("provider"), "model": run.get("model")}),
        }
        return result

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

    def _validate_image_params(
        self,
        project_id: str,
        params: dict[str, Any],
        capabilities: dict[str, Any],
    ) -> None:
        if set(params) - _IMAGE_PARAM_KEYS or set(params) < {
            "operation", "references", "aspectRatio", "resolution", "resultCount",
        }:
            raise GenerationError("generation_request_invalid")
        operation = params.get("operation")
        references = params.get("references")
        if operation not in _IMAGE_OPERATIONS or not isinstance(references, list):
            raise GenerationError("generation_request_invalid")
        if not capabilities["minReferences"] <= len(references) <= capabilities["maxReferences"]:
            raise GenerationError("generation_reference_limit")
        seen: set[tuple[str, str, str]] = set()
        for reference in references:
            if not isinstance(reference, dict) or set(reference) != {"assetId", "assetVersionId", "referenceType"}:
                raise GenerationError("generation_reference_invalid")
            identity = (reference.get("assetId"), reference.get("assetVersionId"), reference.get("referenceType"))
            if not all(isinstance(value, str) and value for value in identity) or identity in seen:
                raise GenerationError("generation_reference_invalid")
            seen.add(identity)
            if reference["referenceType"] not in capabilities["referenceTypes"]:
                raise GenerationError("generation_reference_type_unsupported")
            try:
                asset = self.assets.get(project_id, reference["assetId"])
                if (
                    asset.get("kind") != "image"
                    or self.assets.version_id(asset) != reference["assetVersionId"]
                    or not self.assets.available(project_id, asset)
                ):
                    raise KeyError()
            except (KeyError, OSError, ValueError):
                raise GenerationError("generation_reference_invalid") from None
        if operation == "edit":
            if not capabilities["imageEditing"]:
                raise GenerationError("generation_image_edit_unsupported")
            if not references or not any(item["referenceType"] == "edit-source" for item in references):
                raise GenerationError("generation_reference_invalid")
        elif operation == "super-resolution":
            if not capabilities["superResolution"]:
                raise GenerationError("generation_super_resolution_unsupported")
            if len(references) != 1 or references[0]["referenceType"] != "upscale-source":
                raise GenerationError("generation_reference_invalid")
        if params.get("aspectRatio") not in capabilities["aspectRatios"]:
            raise GenerationError("generation_aspect_ratio_unsupported")
        if params.get("resolution") not in capabilities["resolutions"]:
            raise GenerationError("generation_resolution_unsupported")
        result_count = params.get("resultCount")
        if (
            type(result_count) is not int
            or not capabilities["minResults"] <= result_count <= capabilities["maxResults"]
        ):
            raise GenerationError("generation_result_count_unsupported")
        if "timeoutSeconds" in params:
            if not capabilities["timeout"]:
                raise GenerationError("generation_capability_unavailable")
            if type(params["timeoutSeconds"]) is not int or params["timeoutSeconds"] <= 0:
                raise GenerationError("generation_request_invalid")

    def submit(
        self,
        project_id: str,
        *,
        disclosure: dict[str, Any],
        text_params: Optional[dict[str, Any]] = None,
        idempotency_key: str,
        caller: str,
        routing_policy_ref: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        self._validate_disclosure(disclosure)
        if not isinstance(text_params, (dict, type(None))):
            raise GenerationError("generation_request_invalid")
        params = dict(text_params or {})
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise GenerationError("generation_request_invalid")
        if not isinstance(caller, str) or not 1 <= len(caller) <= 120:
            raise GenerationError("generation_request_invalid")

        policy, candidates = self._routing_plan("text", disclosure, routing_policy_ref)
        frozen_candidates = self._freeze_route_candidates("text", candidates)
        catalog = self._catalog_from_frozen(frozen_candidates[0])
        self._validate_text_params(params, catalog["capabilities"])
        for candidate in frozen_candidates[1:]:
            self._validate_text_params(params, candidate["capabilitySnapshot"])
        snapshot = {
            "modality": "text",
            "provider": candidates[0]["provider"],
            "model": candidates[0]["model"],
            "configurationVersion": catalog["configurationVersion"],
            "input": {"prompt": disclosure["prompt"], "textParams": params},
            "disclosure": dict(disclosure),
            "idempotencyKey": idempotency_key,
            "caller": caller,
            "routingPolicy": policy,
            "routingPolicySnapshot": self._routing_snapshot("text", policy, candidates),
            "routingCandidateSnapshots": frozen_candidates,
            "actualRoute": dict(candidates[0]),
            "routeAttempts": [self._route_attempt(catalog, candidates[0])],
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

    def submit_image(
        self,
        project_id: str,
        *,
        disclosure: dict[str, Any],
        image_params: dict[str, Any],
        idempotency_key: str,
        caller: str,
        routing_policy_ref: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        self._validate_disclosure(disclosure)
        if not isinstance(image_params, dict):
            raise GenerationError("generation_request_invalid")
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise GenerationError("generation_request_invalid")
        if not isinstance(caller, str) or not 1 <= len(caller) <= 120:
            raise GenerationError("generation_request_invalid")
        policy, candidates = self._routing_plan("image", disclosure, routing_policy_ref)
        frozen_candidates = self._freeze_route_candidates("image", candidates)
        catalog = self._catalog_from_frozen(frozen_candidates[0])
        params = json.loads(json.dumps(image_params, ensure_ascii=False, allow_nan=False))
        self._validate_image_params(project_id, params, catalog["capabilities"])
        for candidate in frozen_candidates[1:]:
            self._validate_image_params(project_id, params, candidate["capabilitySnapshot"])
        snapshot = {
            "modality": "image",
            "provider": candidates[0]["provider"],
            "model": candidates[0]["model"],
            "configurationVersion": catalog["configurationVersion"],
            "input": {"prompt": disclosure["prompt"], "imageParams": params},
            "disclosure": dict(disclosure),
            "idempotencyKey": idempotency_key,
            "caller": caller,
            "routingPolicy": policy,
            "routingPolicySnapshot": self._routing_snapshot("image", policy, candidates),
            "routingCandidateSnapshots": frozen_candidates,
            "actualRoute": dict(candidates[0]),
            "routeAttempts": [self._route_attempt(catalog, candidates[0])],
            "capabilitySnapshot": catalog["capabilities"],
            "adapterId": catalog["adapterId"],
            "adapterVersion": catalog["adapterVersion"],
        }
        return self._create_image_run(project_id, snapshot)

    def _create_image_run(
        self,
        project_id: str,
        snapshot: dict[str, Any],
        *,
        retry_of: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        frozen = freeze_run_input(snapshot)
        with self.lock:
            self.recovered_projects.add(project_id)
            runs = self.store.load(project_id)
            existing = next(
                (run for run in runs if run.get("idempotencyKey") == snapshot["idempotencyKey"]),
                None,
            )
            if existing is not None:
                if existing.get("inputFingerprint") != frozen.fingerprint:
                    raise GenerationError("generation_idempotency_conflict")
                return existing
            now = self._now()
            run_id = f"generation-run-{uuid4().hex}"
            previous_items = retry_of.get("outputItems", []) if retry_of is not None else []
            result_count = snapshot["input"]["imageParams"]["resultCount"]
            output_items = []
            for index in range(result_count):
                previous = next((item for item in previous_items if item.get("index") == index), None)
                if previous is not None and previous.get("status") in _IMAGE_SUCCESS_STATUSES:
                    output_items.append({
                        key: previous[key]
                        for key in ("index", "resultId", "assetId", "assetVersionId", "url", "name", "width", "height")
                        if key in previous
                    } | {"status": "reused"})
                    continue
                output_items.append({
                    "index": index,
                    "resultId": f"generation-result-{uuid4().hex}",
                    "status": "pending",
                    "billingUnknown": False,
                })
            run = {
                "id": run_id,
                "commonRunId": f"run-{uuid4().hex}",
                "domainKind": "generation",
                "attempt": 1 if retry_of is None else retry_of.get("attempt", 1) + 1,
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
                "outputItems": output_items,
            }
            if retry_of is not None:
                run["retryOf"] = retry_of["id"]
            runs.append(run)
            self.store.save(project_id, runs)
        if not any(item["status"] == "pending" for item in output_items):
            self._finish_image_run(project_id, run_id)
        elif self.queue is None:
            self._execute(project_id, run_id)
        elif not self.queue.submit(
            "generation-service",
            run_id,
            lambda _job_id: self._execute(project_id, run_id),
            project_id=project_id,
        ):
            self._finish_image_terminal(
                project_id, run_id, outcome="failed", reason="queue_unavailable", billing_unknown=True,
            )
        return self.get_run(project_id, run_id)

    def retry_image(self, project_id: str, run_id: str, *, idempotency_key: str) -> dict[str, Any]:
        previous = self.get_run(project_id, run_id)
        if (
            previous.get("modality") != "image"
            or previous.get("status") not in _TERMINAL_STATUSES
            or not any(
                item.get("status") not in _IMAGE_SUCCESS_STATUSES
                for item in previous.get("outputItems", [])
            )
        ):
            raise GenerationError("generation_retry_invalid")
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise GenerationError("generation_request_invalid")
        params = previous.get("input", {}).get("imageParams")
        if not isinstance(params, dict):
            raise GenerationError("generation_retry_invalid")
        catalog = self.capabilities(previous["provider"], previous["model"], modality="image")
        if (
            catalog["adapterId"] != previous.get("adapterId")
            or catalog["adapterVersion"] != previous.get("adapterVersion")
            or catalog["configurationVersion"] != previous.get("configurationVersion")
            or catalog["capabilities"] != previous.get("capabilitySnapshot")
        ):
            raise GenerationError("generation_configuration_changed")
        self._validate_image_params(project_id, params, catalog["capabilities"])
        snapshot = {
            key: value for key, value in previous.items()
            if key in {
                "modality", "provider", "model", "configurationVersion", "input", "disclosure",
                "caller", "routingPolicy", "routingPolicySnapshot", "routingCandidateSnapshots", "actualRoute", "routeAttempts",
                "capabilitySnapshot", "adapterId", "adapterVersion",
            }
        }
        snapshot["routeAttempts"] = self._fresh_retry_route_attempt(previous)
        snapshot["idempotencyKey"] = idempotency_key
        return self._create_image_run(project_id, snapshot, retry_of=previous)

    def _fresh_retry_route_attempt(self, previous: dict[str, Any]) -> list[dict[str, Any]]:
        """Keep the frozen route contract, but never copy a prior run's audit trail."""
        candidates = previous.get("routingCandidateSnapshots")
        actual = previous.get("actualRoute")
        if isinstance(candidates, list) and isinstance(actual, dict):
            candidate = next((item for item in candidates if isinstance(item, dict) and {
                "provider": item.get("provider"), "model": item.get("model"),
            } == actual), None)
            if isinstance(candidate, dict):
                return [self._route_attempt(self._catalog_from_frozen(candidate), candidate)]
        return [self._route_attempt({
            "adapterId": previous["adapterId"], "adapterVersion": previous["adapterVersion"],
            "configurationVersion": previous["configurationVersion"], "capabilities": previous["capabilitySnapshot"],
        }, {"provider": previous["provider"], "model": previous["model"]})]

    def _validate_video_params(
        self,
        project_id: str,
        params: dict[str, Any],
        capabilities: dict[str, Any],
    ) -> None:
        if set(params) != _VIDEO_PARAM_KEYS:
            raise GenerationError("generation_request_invalid")
        profile = next(
            (item for item in capabilities["profiles"] if item["id"] == params.get("profileId")),
            None,
        )
        if profile is None:
            raise GenerationError("generation_profile_unsupported")
        if params.get("mode") != profile["mode"]:
            raise GenerationError("generation_reference_pattern_unsupported")
        references = params.get("references")
        if not isinstance(references, list):
            raise GenerationError("generation_request_invalid")
        pattern = []
        seen: set[tuple[str, str, str]] = set()
        for reference in references:
            if not isinstance(reference, dict) or set(reference) != {"assetId", "assetVersionId", "referenceType"}:
                raise GenerationError("generation_reference_invalid")
            identity = (reference.get("assetId"), reference.get("assetVersionId"), reference.get("referenceType"))
            if not all(isinstance(value, str) and value for value in identity) or identity in seen:
                raise GenerationError("generation_reference_invalid")
            seen.add(identity)
            pattern.append(reference["referenceType"])
            try:
                asset = self.assets.get(project_id, reference["assetId"])
                if (
                    asset.get("kind") != "image"
                    or self.assets.version_id(asset) != reference["assetVersionId"]
                    or not self.assets.available(project_id, asset)
                ):
                    raise KeyError()
            except (KeyError, OSError, ValueError):
                raise GenerationError("generation_reference_invalid") from None
        if pattern not in profile["referencePatterns"]:
            raise GenerationError("generation_reference_pattern_unsupported")
        combination = {
            "durationSeconds": params.get("durationSeconds"),
            "aspectRatio": params.get("aspectRatio"),
            "resolution": params.get("resolution"),
            "audio": params.get("audio"),
        }
        if combination not in profile["combinations"]:
            raise GenerationError("generation_combination_unsupported")
        result_count = params.get("resultCount")
        if type(result_count) is not int or not profile["minResults"] <= result_count <= profile["maxResults"]:
            raise GenerationError("generation_result_count_unsupported")

    def submit_video(
        self,
        project_id: str,
        *,
        disclosure: dict[str, Any],
        video_params: dict[str, Any],
        idempotency_key: str,
        caller: str,
        routing_policy_ref: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        self._validate_disclosure(disclosure)
        if not isinstance(video_params, dict):
            raise GenerationError("generation_request_invalid")
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise GenerationError("generation_request_invalid")
        if not isinstance(caller, str) or not 1 <= len(caller) <= 120:
            raise GenerationError("generation_request_invalid")
        policy, candidates = self._routing_plan("video", disclosure, routing_policy_ref)
        frozen_candidates = self._freeze_route_candidates("video", candidates)
        catalog = self._catalog_from_frozen(frozen_candidates[0])
        params = json.loads(json.dumps(video_params, ensure_ascii=False, allow_nan=False))
        self._validate_video_params(project_id, params, catalog["capabilities"])
        for candidate in frozen_candidates[1:]:
            self._validate_video_params(project_id, params, candidate["capabilitySnapshot"])
        snapshot = {
            "modality": "video",
            "provider": candidates[0]["provider"],
            "model": candidates[0]["model"],
            "configurationVersion": catalog["configurationVersion"],
            "input": {"prompt": disclosure["prompt"], "videoParams": params},
            "disclosure": dict(disclosure),
            "idempotencyKey": idempotency_key,
            "caller": caller,
            "routingPolicy": policy,
            "routingPolicySnapshot": self._routing_snapshot("video", policy, candidates),
            "routingCandidateSnapshots": frozen_candidates,
            "actualRoute": dict(candidates[0]),
            "routeAttempts": [self._route_attempt(catalog, candidates[0])],
            "capabilitySnapshot": catalog["capabilities"],
            "adapterId": catalog["adapterId"],
            "adapterVersion": catalog["adapterVersion"],
        }
        frozen = freeze_run_input(snapshot)
        with self.lock:
            self.recovered_projects.add(project_id)
            runs = self.store.load(project_id)
            existing = next((run for run in runs if run.get("idempotencyKey") == idempotency_key), None)
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
                "status": "submitting",
                "outcome": None,
                "reason": None,
                "createdAt": now,
                "updatedAt": now,
                **snapshot,
                "inputSnapshot": frozen.snapshot,
                "inputFingerprint": frozen.fingerprint,
                "billingUnknown": False,
                "remoteTaskId": None,
                "lastConfirmedRemoteStatus": None,
                "pollCursor": None,
                "callbackCursor": None,
                "remoteCancelResult": None,
                "localDisposition": "active",
                "logs": [{"event": "video_submit_started"}],
                "outputItems": [{
                    "index": index,
                    "resultId": f"generation-result-{uuid4().hex}",
                    "status": "pending",
                    "billingUnknown": False,
                } for index in range(params["resultCount"])],
            }
            runs.append(run)
            self.store.save(project_id, runs)
        self._submit_video_remote(project_id, run_id)
        return self.get_run(project_id, run_id)

    def _submit_video_remote(self, project_id: str, run_id: str) -> None:
        run = self.get_run(project_id, run_id)
        try:
            adapter = self.registry.resolve_by_id(run["adapterId"])
            if getattr(adapter, "version", None) != run.get("adapterVersion"):
                raise GenerationError("generation_adapter_version_unavailable", safe_to_fallback=True)
        except GenerationError as error:
            error.safe_to_fallback = True
            if self._start_safe_fallback(project_id, run_id, error):
                return
            self._finish_video_terminal(project_id, run_id, "unknown" if error.outcome == "unknown" else "failed", error.outcome, error.code, error.billing_unknown)
            return
        if not isinstance(adapter, VideoGenerationAdapter):
            error = GenerationError("generation_adapter_missing", safe_to_fallback=True)
            if self._start_safe_fallback(project_id, run_id, error):
                return
            self._finish_video_terminal(project_id, run_id, "failed", "failed", error.code, False)
            return
        try:
            remote = adapter.submit({**run, "_projectId": project_id})
            self._validate_video_submit_result(remote)
        except GenerationError as error:
            if self._start_safe_fallback(project_id, run_id, error):
                return
            self._finish_video_terminal(project_id, run_id, "unknown" if error.outcome == "unknown" else "failed", error.outcome, error.code, error.billing_unknown)
            return
        except Exception:
            self._mark_route_failure(project_id, run_id, "generation_submit_unknown", "fallback_blocked_unknown_outcome")
            self._finish_video_terminal(project_id, run_id, "unknown", "unknown", "generation_submit_unknown", True)
            return

        def accepted(current: dict[str, Any]) -> None:
            if current.get("status") != "submitting":
                return
            current.update(
                status="running",
                remoteTaskId=remote["remoteTaskId"],
                lastConfirmedRemoteStatus=remote["status"],
                pollCursor=remote.get("pollCursor"),
                callbackCursor=remote.get("callbackCursor"),
            )
            current["logs"].append({"event": "video_remote_submitted", "remoteStatus": remote["status"]})

        self._mutate(project_id, run_id, accepted)

    @staticmethod
    def _validate_video_submit_result(value: Any) -> None:
        if not isinstance(value, dict) or set(value) != {"remoteTaskId", "status", "pollCursor", "callbackCursor"}:
            raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
        if not GenerationService._valid_video_protocol_text(
            value.get("remoteTaskId"), maximum=_VIDEO_MAX_REMOTE_ID_LENGTH, allow_path=False,
        ):
            raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
        if value.get("status") not in {"queued", "running"}:
            raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
        for key in ("pollCursor", "callbackCursor"):
            if value.get(key) is not None and not GenerationService._valid_video_protocol_text(
                value[key], maximum=_VIDEO_MAX_CURSOR_LENGTH, allow_path=False,
            ):
                raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)

    def _video_operation_lock(self, project_id: str, run_id: str) -> Lock:
        with self.lock:
            return self.video_operation_locks.setdefault((project_id, run_id), Lock())

    def refresh_video(self, project_id: str, run_id: str) -> dict[str, Any]:
        with self._video_operation_lock(project_id, run_id):
            return self._refresh_video(project_id, run_id)

    def _refresh_video(self, project_id: str, run_id: str) -> dict[str, Any]:
        run = self.get_run(project_id, run_id)
        if run.get("modality") != "video" or not isinstance(run.get("remoteTaskId"), str):
            raise GenerationError("generation_refresh_invalid")
        if run.get("status") in _TERMINAL_STATUSES:
            return run
        adapter = self._video_adapter_for_run(run)
        if (
            run.get("lastConfirmedRemoteStatus") == "succeeded"
            and all(
                item.get("status") != "pending" or isinstance(item.get("remoteResultId"), str)
                for item in run.get("outputItems", [])
            )
        ):
            self._download_video_outputs(project_id, run_id, adapter)
            self._finish_video_run(project_id, run_id)
            return self.get_run(project_id, run_id)
        unknown_reason = None
        try:
            event = adapter.poll({**run, "_projectId": project_id})
            self._validate_video_poll_event(event)
        except GenerationError as error:
            unknown_reason = error.code
            event = {
                "status": "unknown", "pollCursor": run.get("pollCursor"),
                "callbackCursor": run.get("callbackCursor"), "billingUnknown": True,
            }
        except Exception:
            unknown_reason = "generation_remote_unknown"
            event = {
                "status": "unknown", "pollCursor": run.get("pollCursor"),
                "callbackCursor": run.get("callbackCursor"), "billingUnknown": True,
            }
        status = event["status"]
        if status in {"queued", "running", "unknown"}:
            def progress(current: dict[str, Any]) -> None:
                if current.get("status") in _TERMINAL_STATUSES or current.get("status") == "cancel_requested":
                    return
                current.update(
                    status="running",
                    outcome=None,
                    reason=unknown_reason if status == "unknown" else None,
                    lastConfirmedRemoteStatus=status,
                    pollCursor=event.get("pollCursor"),
                    callbackCursor=event.get("callbackCursor"),
                    billingUnknown=event["billingUnknown"],
                )
                current["logs"].append({"event": "video_remote_status", "status": status})
            self._mutate(project_id, run_id, progress)
            return self.get_run(project_id, run_id)
        if status == "failed":
            self._finish_video_terminal(
                project_id, run_id, "failed", "failed", event["reason"], event["billingUnknown"],
                remote_status="failed", poll_cursor=event.get("pollCursor"), callback_cursor=event.get("callbackCursor"),
            )
            return self.get_run(project_id, run_id)
        if status == "cancelled":
            self._finish_video_terminal(
                project_id, run_id, "cancelled", "cancelled", "generation_remote_cancelled", event["billingUnknown"],
                remote_status="cancelled", poll_cursor=event.get("pollCursor"), callback_cursor=event.get("callbackCursor"),
            )
            return self.get_run(project_id, run_id)
        try:
            self._record_video_success(project_id, run_id, event)
        except GenerationError as error:
            def protocol_unknown(current: dict[str, Any]) -> None:
                current.update(status="running", outcome=None, reason=error.code, billingUnknown=True)
                current["logs"].append({"event": "video_remote_status", "status": "unknown"})
            self._mutate(project_id, run_id, protocol_unknown)
            return self.get_run(project_id, run_id)
        self._download_video_outputs(project_id, run_id, adapter)
        self._finish_video_run(project_id, run_id)
        return self.get_run(project_id, run_id)

    def _video_adapter_for_run(self, run: dict[str, Any]) -> VideoGenerationAdapter:
        adapter = self.registry.resolve_by_id(run["adapterId"])
        if not isinstance(adapter, VideoGenerationAdapter):
            raise GenerationError("generation_adapter_missing")
        if adapter.version != run.get("adapterVersion"):
            raise GenerationError("generation_adapter_version_changed")
        return adapter

    @staticmethod
    def _validate_video_poll_event(event: Any) -> None:
        if not isinstance(event, dict) or event.get("status") not in {"queued", "running", "unknown", "failed", "cancelled", "succeeded"}:
            raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
        status = event["status"]
        common = {"status", "pollCursor", "callbackCursor", "billingUnknown"}
        expected = common | ({"reason"} if status == "failed" else {"results", "failedItems"} if status == "succeeded" else set())
        if set(event) != expected or type(event.get("billingUnknown")) is not bool:
            raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
        for key in ("pollCursor", "callbackCursor"):
            if event.get(key) is not None and not GenerationService._valid_video_protocol_text(
                event[key], maximum=_VIDEO_MAX_CURSOR_LENGTH, allow_path=False,
            ):
                raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
        if status == "failed" and (
            not isinstance(event.get("reason"), str) or _VIDEO_ERROR_CODE.fullmatch(event["reason"]) is None
        ):
            raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
        if status == "succeeded" and (not isinstance(event.get("results"), list) or not isinstance(event.get("failedItems"), list)):
            raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)

    def _record_video_success(self, project_id: str, run_id: str, event: dict[str, Any]) -> None:
        def record(run: dict[str, Any]) -> None:
            if run.get("status") in _TERMINAL_STATUSES:
                return
            items = {item["index"]: item for item in run["outputItems"]}
            observed: set[int] = set()
            for result in event["results"]:
                if (
                    not isinstance(result, dict) or set(result) != {"index", "remoteResultId", "name"}
                    or type(result.get("index")) is not int or result["index"] not in items or result["index"] in observed
                    or not self._valid_video_protocol_text(
                        result.get("remoteResultId"), maximum=_VIDEO_MAX_REMOTE_ID_LENGTH, allow_path=False,
                    )
                    or not self._valid_video_protocol_text(
                        result.get("name"), maximum=_VIDEO_MAX_RESULT_NAME_LENGTH, allow_path=False,
                    )
                    or Path(result["name"]).name != result["name"]
                    or "/" in result["name"] or "\\" in result["name"]
                ):
                    raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
                observed.add(result["index"])
                items[result["index"]].update(
                    remoteResultId=result["remoteResultId"], name=result["name"].strip(), status="pending",
                )
            for failure in event["failedItems"]:
                if (
                    not isinstance(failure, dict) or set(failure) != {"index", "code", "billingUnknown"}
                    or type(failure.get("index")) is not int or failure["index"] not in items or failure["index"] in observed
                    or not isinstance(failure.get("code"), str) or _VIDEO_ERROR_CODE.fullmatch(failure["code"]) is None
                    or type(failure.get("billingUnknown")) is not bool
                ):
                    raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
                observed.add(failure["index"])
                items[failure["index"]].update(
                    status="failed", reason=failure["code"], billingUnknown=failure["billingUnknown"],
                )
            if observed != set(items):
                raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
            run.update(
                status="running", lastConfirmedRemoteStatus="succeeded", pollCursor=event.get("pollCursor"),
                callbackCursor=event.get("callbackCursor"), billingUnknown=event["billingUnknown"], reason=None,
            )
            run["logs"].append({"event": "video_remote_status", "status": "succeeded"})
        self._mutate(project_id, run_id, record)

    def _download_video_outputs(
        self,
        project_id: str,
        run_id: str,
        adapter: VideoGenerationAdapter,
    ) -> None:
        for item in self.get_run(project_id, run_id)["outputItems"]:
            if item.get("status") != "pending" or not item.get("remoteResultId"):
                continue
            suffix = Path(item.get("name", "video.mp4")).suffix.lower()
            if suffix not in {".mp4", ".mov", ".webm"}:
                suffix = ".mp4"
            descriptor, temporary_name = tempfile.mkstemp(
                dir=self.store.root, prefix=".generation-video-", suffix=suffix,
            )
            temporary = Path(temporary_name)
            try:
                current = self.get_run(project_id, run_id)
                try:
                    with os.fdopen(descriptor, "wb") as output:
                        bounded = _BoundedVideoWriter(output, self.max_video_result_bytes)
                        adapter.download(
                            {**current, "_projectId": project_id},
                            {"remoteResultId": item["remoteResultId"], "name": item["name"], "index": item["index"]},
                            bounded,
                        )
                        bounded.flush()
                        os.fsync(output.fileno())
                except _VideoResultTooLarge:
                    self._finish_video_item(project_id, run_id, item["index"], "generation_result_too_large", False)
                    continue
                except Exception:
                    self._finish_video_item(project_id, run_id, item["index"], "generation_result_download_failed", False)
                    continue
                try:
                    info = os.lstat(temporary)
                    if not stat.S_ISREG(info.st_mode) or info.st_size <= 0 or temporary.is_symlink():
                        raise OSError()
                    if info.st_size > self.max_video_result_bytes:
                        self._finish_video_item(project_id, run_id, item["index"], "generation_result_too_large", False)
                        continue
                except OSError:
                    self._finish_video_item(project_id, run_id, item["index"], "generation_result_download_failed", False)
                    continue
                try:
                    media = probe_video(temporary, ffprobe_path=self.ffprobe_path, ffmpeg_path=self.ffmpeg_path)
                except (OSError, ValueError):
                    self._finish_video_item(project_id, run_id, item["index"], "generation_result_invalid", False)
                    continue
                self._install_video_result(project_id, run_id, adapter, item["index"], temporary, media)
            finally:
                temporary.unlink(missing_ok=True)

    def _install_video_result(
        self,
        project_id: str,
        run_id: str,
        adapter: VideoGenerationAdapter,
        index: int,
        source: Path,
        media: dict[str, Any],
    ) -> None:
        with self.lock:
            run = self.get_run(project_id, run_id)
            item = next((value for value in run["outputItems"] if value.get("index") == index), None)
            if item is None or item.get("status") != "pending":
                return
            try:
                asset = self.assets.install_run_result(
                    project_id,
                    source=source,
                    run_id=run_id,
                    common_run_id=run["commonRunId"],
                    result_id=item["resultId"],
                    domain_kind="generation",
                    domain_run_id=run_id,
                    kind="video",
                    name=item["name"],
                    input_snapshot=run["inputSnapshot"],
                    provider=run["provider"],
                    processing_method=f"{adapter.adapter_id}@{adapter.version}",
                    media=media,
                )
            except (OSError, ValueError, ResultInstallationConflict):
                self._finish_video_item(project_id, run_id, index, "generation_result_install_failed", False)
                return
            item.update(
                status="completed",
                assetId=asset["id"],
                assetVersionId=asset["assetVersionId"],
                url=f"/api/projects/{project_id}/preproduction/assets/{asset['id']}/file",
                name=asset["name"],
                duration=asset.get("duration"),
                width=asset.get("width"),
                height=asset.get("height"),
                billingUnknown=False,
            )
            run["logs"].append({"event": "video_result", "index": index, "resultId": item["resultId"]})
            run["updatedAt"] = self._now()
            runs = self.store.load(project_id)
            stored = next(value for value in runs if value.get("id") == run_id)
            stored.clear()
            stored.update(run)
            self.store.save(project_id, runs)

    def _finish_video_item(self, project_id: str, run_id: str, index: int, reason: str, billing_unknown: bool) -> None:
        def finish(run: dict[str, Any]) -> None:
            item = next((value for value in run["outputItems"] if value.get("index") == index), None)
            if item is not None and item.get("status") == "pending":
                item.update(status="failed", reason=reason, billingUnknown=billing_unknown)
                run["logs"].append({"event": "video_result_error", "index": index, "reason": reason})
        self._mutate(project_id, run_id, finish)

    def _finish_video_run(self, project_id: str, run_id: str) -> None:
        def finish(run: dict[str, Any]) -> None:
            successes = [item for item in run["outputItems"] if item.get("status") in _VIDEO_SUCCESS_STATUSES]
            failures = [item for item in run["outputItems"] if item.get("status") not in _VIDEO_SUCCESS_STATUSES]
            if successes and not failures:
                if run.get("routeAttempts") and run["routeAttempts"][-1].get("status") == "selected":
                    run["routeAttempts"][-1].update(status="completed", failureCode=None, fallbackDecision="completed")
                run.update(
                    status="completed", outcome="late_success" if run.get("localDisposition") == "stopped" else "completed",
                    reason=None, billingUnknown=bool(run.get("billingUnknown")),
                )
            elif successes:
                if run.get("routeAttempts") and run["routeAttempts"][-1].get("status") == "selected":
                    run["routeAttempts"][-1].update(status="partial", failureCode="generation_results_partial", fallbackDecision="fallback_blocked_remote_identity")
                run.update(
                    status="partial_success", outcome="partial_success", reason="generation_results_partial",
                    billingUnknown=bool(run.get("billingUnknown")) or any(item.get("billingUnknown") for item in failures),
                )
            else:
                if run.get("routeAttempts") and run["routeAttempts"][-1].get("status") == "selected":
                    run["routeAttempts"][-1].update(status="failed", failureCode="generation_results_failed", fallbackDecision="fallback_blocked_remote_identity")
                run.update(
                    status="failed", outcome="failed", reason="generation_results_failed",
                    billingUnknown=bool(run.get("billingUnknown")) or any(item.get("billingUnknown") for item in failures),
                )
        self._mutate(project_id, run_id, finish)

    def _finish_video_terminal(
        self,
        project_id: str,
        run_id: str,
        status: str,
        outcome: str,
        reason: str,
        billing_unknown: bool,
        *,
        remote_status: Optional[str] = None,
        poll_cursor: Optional[str] = None,
        callback_cursor: Optional[str] = None,
    ) -> None:
        def finish(run: dict[str, Any]) -> None:
            if run.get("status") in _TERMINAL_STATUSES:
                return
            item_status = "cancelled" if status == "cancelled" else "unknown" if status == "unknown" else "failed"
            for item in run["outputItems"]:
                if item.get("status") == "pending":
                    item.update(status=item_status, reason=reason, billingUnknown=billing_unknown)
            if run.get("routeAttempts") and run["routeAttempts"][-1].get("status") == "selected":
                run["routeAttempts"][-1].update(
                    status="cancelled" if status == "cancelled" else "failed", failureCode=reason,
                    fallbackDecision="fallback_not_requested_cancelled" if status == "cancelled"
                    else "fallback_blocked_remote_identity" if run.get("remoteTaskId") else "fallback_not_allowed",
                )
            run.update(status=status, outcome=outcome, reason=reason, billingUnknown=billing_unknown)
            if remote_status is not None:
                run["lastConfirmedRemoteStatus"] = remote_status
                run["pollCursor"] = poll_cursor
                run["callbackCursor"] = callback_cursor
        self._mutate(project_id, run_id, finish)

    def stop_video_local(self, project_id: str, run_id: str) -> dict[str, Any]:
        run = self.get_run(project_id, run_id)
        if run.get("modality") != "video" or run.get("status") in _TERMINAL_STATUSES:
            raise GenerationError("generation_stop_invalid")
        def stop(current: dict[str, Any]) -> None:
            current["localDisposition"] = "stopped"
            current["logs"].append({"event": "video_local_stopped"})
        self._mutate(project_id, run_id, stop)
        return self.get_run(project_id, run_id)

    def cancel_video_remote(self, project_id: str, run_id: str) -> dict[str, Any]:
        with self._video_operation_lock(project_id, run_id):
            return self._cancel_video_remote(project_id, run_id)

    def _cancel_video_remote(self, project_id: str, run_id: str) -> dict[str, Any]:
        run = self.get_run(project_id, run_id)
        if run.get("modality") != "video" or run.get("status") in _TERMINAL_STATUSES:
            raise GenerationError("generation_cancel_invalid")
        if not run["capabilitySnapshot"].get("cancel"):
            def unsupported(current: dict[str, Any]) -> None:
                current["remoteCancelResult"] = "unsupported"
                current["logs"].append({"event": "video_remote_cancel", "result": "unsupported"})
            self._mutate(project_id, run_id, unsupported)
            return self.get_run(project_id, run_id)
        adapter = self._video_adapter_for_run(run)
        if adapter.cancel is None:
            def unsupported(current: dict[str, Any]) -> None:
                current["remoteCancelResult"] = "unsupported"
                current["logs"].append({"event": "video_remote_cancel", "result": "unsupported"})
            self._mutate(project_id, run_id, unsupported)
            return self.get_run(project_id, run_id)
        try:
            result = adapter.cancel({**run, "_projectId": project_id})
            self._validate_video_cancel_result(result)
        except Exception:
            result = {"status": "unknown", "billingUnknown": True}
        def cancelled(current: dict[str, Any]) -> None:
            current["remoteCancelResult"] = result["status"]
            current["billingUnknown"] = result["billingUnknown"]
            if result.get("pollCursor") is not None:
                current["pollCursor"] = result["pollCursor"]
            if result["status"] == "cancelled":
                attempts = current.get("routeAttempts")
                if isinstance(attempts, list) and attempts and attempts[-1].get("status") == "selected":
                    attempts[-1].update(status="cancelled", failureCode="generation_remote_cancelled", fallbackDecision="fallback_not_requested_cancelled")
                current.update(
                    status="cancelled", outcome="cancelled", reason="generation_remote_cancelled",
                    lastConfirmedRemoteStatus="cancelled",
                )
                for item in current["outputItems"]:
                    if item.get("status") == "pending":
                        item.update(status="cancelled", reason="generation_remote_cancelled", billingUnknown=result["billingUnknown"])
            elif result["status"] == "unknown":
                current.update(status="running", reason="generation_cancel_unknown", lastConfirmedRemoteStatus="unknown")
            current["logs"].append({"event": "video_remote_cancel", "result": result["status"]})
        self._mutate(project_id, run_id, cancelled)
        return self.get_run(project_id, run_id)

    @staticmethod
    def _validate_video_cancel_result(result: Any) -> None:
        if not isinstance(result, dict) or set(result) not in (
            {"status", "billingUnknown"}, {"status", "billingUnknown", "pollCursor"},
        ):
            raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
        if result.get("status") not in {"cancelled", "unsupported", "unknown"} or type(result.get("billingUnknown")) is not bool:
            raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
        if result.get("pollCursor") is not None and not GenerationService._valid_video_protocol_text(
            result["pollCursor"], maximum=_VIDEO_MAX_CURSOR_LENGTH, allow_path=False,
        ):
            raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)

    def retry_video(self, project_id: str, run_id: str, *, idempotency_key: str) -> dict[str, Any]:
        previous = self.get_run(project_id, run_id)
        failed = [item for item in previous.get("outputItems", []) if item.get("status") not in _VIDEO_SUCCESS_STATUSES]
        if (
            previous.get("modality") != "video"
            or previous.get("status") not in {"failed", "partial_success"}
            or previous.get("lastConfirmedRemoteStatus") != "succeeded"
            or not failed
            or any(not isinstance(item.get("remoteResultId"), str) for item in failed)
        ):
            raise GenerationError("generation_retry_invalid")
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 200:
            raise GenerationError("generation_request_invalid")
        with self.lock:
            runs = self.store.load(project_id)
            existing = next((item for item in runs if item.get("idempotencyKey") == idempotency_key), None)
            if existing is not None:
                if existing.get("retryOf") != previous["id"]:
                    raise GenerationError("generation_idempotency_conflict")
                return existing
            adapter = self._video_adapter_for_run(previous)
            snapshot = {
                key: previous[key] for key in (
                    "modality", "provider", "model", "configurationVersion", "input", "disclosure", "caller",
                    "routingPolicy", "routingPolicySnapshot", "routingCandidateSnapshots", "actualRoute",
                    "capabilitySnapshot", "adapterId", "adapterVersion",
                )
            }
            snapshot["routeAttempts"] = self._fresh_retry_route_attempt(previous)
            snapshot["idempotencyKey"] = idempotency_key
            frozen = freeze_run_input(snapshot)
            now = self._now()
            retry_id = f"generation-run-{uuid4().hex}"
            items = []
            for old in previous["outputItems"]:
                if old.get("status") in _VIDEO_SUCCESS_STATUSES:
                    items.append({
                        key: old[key] for key in (
                            "index", "resultId", "remoteResultId", "assetId", "assetVersionId", "url", "name",
                            "duration", "width", "height",
                        ) if key in old
                    } | {"status": "reused", "billingUnknown": False})
                else:
                    items.append({
                        "index": old["index"], "resultId": f"generation-result-{uuid4().hex}",
                        "remoteResultId": old["remoteResultId"], "name": old["name"],
                        "status": "pending", "billingUnknown": False,
                    })
            retry = {
                "id": retry_id, "commonRunId": f"run-{uuid4().hex}", "domainKind": "generation",
                "attempt": previous.get("attempt", 1) + 1, "retryOf": previous["id"], "status": "running",
                "outcome": None, "reason": None, "createdAt": now, "updatedAt": now, **snapshot,
                "inputSnapshot": frozen.snapshot, "inputFingerprint": frozen.fingerprint,
                "billingUnknown": bool(previous.get("billingUnknown")),
                "remoteTaskId": previous["remoteTaskId"], "lastConfirmedRemoteStatus": "succeeded",
                "pollCursor": previous.get("pollCursor"), "callbackCursor": previous.get("callbackCursor"),
                "remoteCancelResult": previous.get("remoteCancelResult"), "localDisposition": "active",
                "logs": [{"event": "video_local_result_retry", "retryOf": previous["id"]}], "outputItems": items,
            }
            runs.append(retry)
            self.store.save(project_id, runs)
        self._download_video_outputs(project_id, retry_id, adapter)
        self._finish_video_run(project_id, retry_id)
        return self.get_run(project_id, retry_id)

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
            error = GenerationError("generation_adapter_version_unavailable", safe_to_fallback=True)
            if self._start_safe_fallback(project_id, run_id, error):
                return
            self._final(project_id, run_id, "failed", "failed", error.code, True)
            return
        if adapter.version != run.get("adapterVersion"):
            # A persisted snapshot is an executable contract: never silently
            # switch an old queued run to the adapter now registered under the
            # same id.
            error = GenerationError("generation_adapter_version_unavailable", safe_to_fallback=True)
            if self._start_safe_fallback(project_id, run_id, error):
                return
            self._final(project_id, run_id, "failed", "failed", error.code, True)
            return

        timer: Optional[Timer] = None
        params_key = "imageParams" if run.get("modality") == "image" else "textParams"
        timeout = run["input"][params_key].get("timeoutSeconds")
        if timeout is not None:
            timer = Timer(
                timeout,
                lambda: self._timeout(project_id, run_id),
            )
            timer.daemon = True
            timer.start()

        try:
            if run.get("modality") == "image":
                self._consume_image_adapter_result(project_id, run_id, adapter, run)
            else:
                self._consume_adapter_result(project_id, run_id, adapter, run)
        except GenerationError as error:
            if run.get("modality") == "image":
                if self._start_safe_fallback(project_id, run_id, error):
                    return
                self._finish_image_terminal(
                    project_id, run_id, outcome=error.outcome, reason=error.code,
                    billing_unknown=error.billing_unknown,
                )
            else:
                self._record_generation_error(project_id, run_id, error)
        except Exception:
            self._mark_route_failure(project_id, run_id, "generation_provider_failed", "fallback_blocked_unknown_outcome")
            if run.get("modality") == "image":
                self._finish_image_terminal(
                    project_id, run_id, outcome="failed", reason="generation_provider_failed",
                    billing_unknown=False,
                )
            else:
                self._final(project_id, run_id, "failed", "failed", "generation_provider_failed", True)
        finally:
            if timer is not None:
                timer.cancel()

    def _timeout(self, project_id: str, run_id: str) -> None:
        run = self.get_run(project_id, run_id)
        self._mark_route_failure(project_id, run_id, "generation_timed_out", "fallback_blocked_unknown_outcome")
        if run.get("modality") == "image":
            self._finish_image_terminal(
                project_id, run_id, outcome="timed_out", reason="generation_timed_out",
                billing_unknown=True,
            )
        else:
            self._final(project_id, run_id, "failed", "timed_out", "generation_timed_out", True)

    def _consume_image_adapter_result(
        self,
        project_id: str,
        run_id: str,
        adapter: GenerationAdapter,
        run: dict[str, Any],
    ) -> None:
        value = adapter.execute({**run, "_projectId": project_id})
        if not isinstance(value, Iterable) or isinstance(value, (str, bytes, bytearray, dict)):
            raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
        done = False
        for event in value:
            current = self.get_run(project_id, run_id)
            if current.get("status") != "running":
                if current.get("status") in _TERMINAL_STATUSES | {"cancel_requested"}:
                    self._consume_late_image_events(project_id, run_id, adapter, [event, *list(value)])
                return
            if not isinstance(event, dict) or not isinstance(event.get("type"), str):
                raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
            if event["type"] == "done":
                if set(event) != {"type"}:
                    raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
                done = True
                break
            if type(event.get("index")) is not int:
                raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
            item = next((item for item in current["outputItems"] if item.get("index") == event["index"]), None)
            if item is None or item.get("status") != "pending":
                raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
            if event["type"] == "result":
                if set(event) != {"type", "index", "content", "name"}:
                    raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
                self._install_image_result(project_id, run_id, adapter, event)
            elif event["type"] == "error":
                if set(event) != {"type", "index", "code", "billingUnknown"}:
                    raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
                if not isinstance(event["code"], str) or not event["code"] or type(event["billingUnknown"]) is not bool:
                    raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
                self._finish_image_item(
                    project_id, run_id, event["index"], status="failed", reason=event["code"],
                    billing_unknown=event["billingUnknown"],
                )
            else:
                raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
        if not done:
            raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
        self._finish_image_run(project_id, run_id)

    def _consume_late_image_events(self, project_id: str, run_id: str, adapter: GenerationAdapter, events: list[Any]) -> None:
        done = False
        for event in events:
            if not isinstance(event, dict) or not isinstance(event.get("type"), str): return
            if event["type"] == "done":
                done = set(event) == {"type"}; break
            if type(event.get("index")) is not int: return
            if event["type"] == "result" and set(event) == {"type", "index", "content", "name"}:
                self._install_image_result(project_id, run_id, adapter, event)
            elif event["type"] == "error" and set(event) == {"type", "index", "code", "billingUnknown"} and isinstance(event["code"], str) and type(event["billingUnknown"]) is bool:
                def failed(run: dict[str, Any]) -> None:
                    item = next((item for item in run["outputItems"] if item.get("index") == event["index"]), None)
                    if item is not None: item.update(status="failed", reason=event["code"], billingUnknown=event["billingUnknown"])
                self._mutate(project_id, run_id, failed)
            else: return
        if not done: return
        def finish(run: dict[str, Any]) -> None:
            successes = [item for item in run["outputItems"] if item.get("status") in _IMAGE_SUCCESS_STATUSES]
            failures = [item for item in run["outputItems"] if item.get("status") not in _IMAGE_SUCCESS_STATUSES]
            run["lateResult"] = True
            if successes and not failures:
                if run.get("routeAttempts") and run["routeAttempts"][-1].get("status") == "selected": run["routeAttempts"][-1].update(status="completed", failureCode=None, fallbackDecision="completed")
                run.update(status="completed", outcome="late_success", reason=None, billingUnknown=bool(run.get("billingUnknown")))
            elif successes:
                if run.get("routeAttempts") and run["routeAttempts"][-1].get("status") == "selected": run["routeAttempts"][-1].update(status="partial", failureCode="generation_results_partial", fallbackDecision="fallback_blocked_partial_result")
                run.update(status="partial_success", outcome="partial_success", reason="generation_results_partial", billingUnknown=bool(run.get("billingUnknown")) or any(item.get("billingUnknown") for item in failures))
        self._mutate(project_id, run_id, finish)

    @staticmethod
    def _normalize_image_content(content: Any) -> tuple[bytes, int, int]:
        if not isinstance(content, bytes) or not content:
            raise GenerationError("generation_result_invalid", outcome="unknown", billing_unknown=True)
        try:
            with Image.open(io.BytesIO(content)) as image:
                image.verify()
            with Image.open(io.BytesIO(content)) as image:
                if image.width * image.height > _MAX_IMAGE_PIXELS:
                    raise ValueError()
                image.load()
                output = io.BytesIO()
                image.convert("RGBA" if "A" in image.getbands() else "RGB").save(output, format="PNG")
                return output.getvalue(), image.width, image.height
        except (OSError, ValueError, UnidentifiedImageError):
            raise GenerationError("generation_result_invalid", outcome="unknown", billing_unknown=True) from None

    def _install_image_result(
        self,
        project_id: str,
        run_id: str,
        adapter: GenerationAdapter,
        event: dict[str, Any],
    ) -> None:
        if not isinstance(event["name"], str) or not event["name"].strip():
            raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
        content, width, height = self._normalize_image_content(event["content"])
        descriptor, temporary_name = tempfile.mkstemp(
            dir=self.store.root, prefix=".generation-image-", suffix=".png",
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
            with self.lock:
                run = self.get_run(project_id, run_id)
                late = run.get("status") in _TERMINAL_STATUSES or run.get("status") == "cancel_requested"
                if run.get("status") != "running" and not late:
                    return
                item = next((item for item in run["outputItems"] if item.get("index") == event["index"]), None)
                if item is None or (item.get("status") != "pending" and not late):
                    raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
                try:
                    asset = self.assets.install_run_result(
                        project_id,
                        source=temporary,
                        run_id=run_id,
                        common_run_id=run["commonRunId"],
                        result_id=item["resultId"],
                        domain_kind="generation",
                        domain_run_id=run_id,
                        kind="image",
                        name=event["name"].strip(),
                        input_snapshot=run["inputSnapshot"],
                        provider=run["provider"],
                        processing_method=f"{adapter.adapter_id}@{adapter.version}",
                        media={"width": width, "height": height},
                    )
                except (OSError, ValueError, ResultInstallationConflict):
                    raise GenerationError("generation_result_install_failed", outcome="unknown", billing_unknown=True) from None
                item.update(
                    status="completed",
                    assetId=asset["id"],
                    assetVersionId=asset["assetVersionId"],
                    url=f"/api/projects/{project_id}/preproduction/assets/{asset['id']}/file",
                    name=asset["name"],
                    width=asset.get("width"),
                    height=asset.get("height"),
                    billingUnknown=False,
                )
                if late:
                    run["lateResult"] = True
                run["logs"].append({"event": "image_result", "index": event["index"], "resultId": item["resultId"]})
                run["updatedAt"] = self._now()
                runs = self.store.load(project_id)
                stored = next(value for value in runs if value.get("id") == run_id)
                stored.clear()
                stored.update(run)
                self.store.save(project_id, runs)
        finally:
            temporary.unlink(missing_ok=True)

    def _finish_image_item(
        self,
        project_id: str,
        run_id: str,
        index: int,
        *,
        status: str,
        reason: str,
        billing_unknown: bool,
    ) -> None:
        def finish(run: dict[str, Any]) -> None:
            if run.get("status") != "running":
                return
            item = next((item for item in run["outputItems"] if item.get("index") == index), None)
            if item is None or item.get("status") != "pending":
                raise GenerationError("generation_protocol_invalid", outcome="unknown", billing_unknown=True)
            item.update(status=status, reason=reason, billingUnknown=billing_unknown)
            run["logs"].append({"event": "image_error", "index": index, "reason": reason})

        self._mutate(project_id, run_id, finish)

    def _finish_image_run(self, project_id: str, run_id: str) -> None:
        def finish(run: dict[str, Any]) -> None:
            if run.get("status") not in {"running", "queued"}:
                return
            pending = [item for item in run["outputItems"] if item.get("status") == "pending"]
            for item in pending:
                item.update(status="failed", reason="generation_protocol_incomplete", billingUnknown=True)
            successes = [item for item in run["outputItems"] if item.get("status") in _IMAGE_SUCCESS_STATUSES]
            failures = [item for item in run["outputItems"] if item.get("status") not in _IMAGE_SUCCESS_STATUSES]
            if successes and not failures:
                if run.get("routeAttempts"):
                    run["routeAttempts"][-1].update(status="completed", failureCode=None, fallbackDecision="completed")
                run.update(status="completed", outcome="completed", reason=None, billingUnknown=False)
            elif successes:
                if run.get("routeAttempts"):
                    run["routeAttempts"][-1].update(status="partial", failureCode="generation_results_partial", fallbackDecision="fallback_blocked_partial_result")
                run.update(
                    status="partial_success", outcome="partial_success", reason="generation_results_partial",
                    billingUnknown=any(item.get("billingUnknown") for item in failures),
                )
            else:
                if run.get("routeAttempts"):
                    run["routeAttempts"][-1].update(status="failed", failureCode="generation_results_failed", fallbackDecision="fallback_blocked_unknown_outcome")
                run.update(
                    status="failed", outcome="failed", reason="generation_results_failed",
                    billingUnknown=any(item.get("billingUnknown") for item in failures),
                )

        self._mutate(project_id, run_id, finish)

    def _finish_image_terminal(
        self,
        project_id: str,
        run_id: str,
        *,
        outcome: str,
        reason: str,
        billing_unknown: bool,
    ) -> None:
        def finish(run: dict[str, Any]) -> None:
            if run.get("status") in _TERMINAL_STATUSES:
                return
            item_status = "cancelled" if outcome == "cancelled" else "timed_out" if outcome == "timed_out" else "failed"
            for item in run["outputItems"]:
                if item.get("status") == "pending":
                    item.update(status=item_status, reason=reason, billingUnknown=billing_unknown)
            successes = [item for item in run["outputItems"] if item.get("status") in _IMAGE_SUCCESS_STATUSES]
            if run.get("routeAttempts") and run["routeAttempts"][-1].get("status") == "selected":
                run["routeAttempts"][-1].update(
                    status="cancelled" if outcome == "cancelled" else "partial" if successes else "failed", failureCode=reason,
                    fallbackDecision="fallback_not_requested_cancelled" if outcome == "cancelled" else "fallback_blocked_partial_result" if successes else "fallback_blocked_unknown_outcome",
                )
            run.update(
                status="partial_success" if successes else ("cancelled" if outcome == "cancelled" else "failed"),
                outcome=outcome,
                reason=reason,
                billingUnknown=billing_unknown or any(item.get("billingUnknown") for item in run["outputItems"]),
            )

        self._mutate(project_id, run_id, finish)

    def _consume_adapter_result(
        self,
        project_id: str,
        run_id: str,
        adapter: GenerationAdapter,
        run: dict[str, Any],
    ) -> None:
        value = adapter.execute(run)
        if isinstance(value, str):
            if self.get_run(project_id, run_id).get("status") in _TERMINAL_STATUSES | {"cancel_requested"}:
                self._record_late_text_result(project_id, run_id, [{"sequence": 0, "text": value}], adapter)
                return
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
                if current.get("status") in _TERMINAL_STATUSES | {"cancel_requested"}:
                    chunks = list(current["outputItems"][0].get("chunks", []))
                    sequence = len(chunks)
                    complete = False
                    for late in [event, *list(value)]:
                        if not isinstance(late, dict):
                            return
                        if late.get("type") == "done":
                            complete = late.get("outcome", "completed") == "completed"
                            break
                        if type(late.get("sequence")) is not int or not isinstance(late.get("text"), str) or late["sequence"] != sequence:
                            return
                        chunks.append({"sequence": sequence, "text": late["text"]})
                        sequence += 1
                    if complete:
                        self._record_late_text_result(project_id, run_id, chunks, adapter)
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
        if self._start_safe_fallback(project_id, run_id, error):
            return
        status = "failed"
        if error.outcome in {"incomplete", "partial_success"}:
            status = "partial_success"
        self._final(project_id, run_id, status, error.outcome, error.code, error.billing_unknown)

    def _start_safe_fallback(self, project_id: str, run_id: str, error: GenerationError) -> bool:
        """Move only a definitely non-billable, pre-remote failure to a frozen route."""
        if not error.safe_to_fallback or error.billing_unknown or error.outcome != "failed":
            decision = "fallback_blocked_unknown_outcome"
        else:
            decision = "fallback"
        run = self.get_run(project_id, run_id)
        snapshot = run.get("routingPolicySnapshot")
        attempts = run.get("routeAttempts")
        if not isinstance(snapshot, dict):
            return False
        if snapshot.get("mode") == "explicit":
            self._mark_route_failure(project_id, run_id, error.code, "fallback_not_allowed")
            return False
        if not isinstance(attempts, list) or not attempts:
            return False
        current = attempts[-1]
        if not isinstance(current, dict):
            return False
        candidates = run.get("routingCandidateSnapshots", [])
        if not isinstance(candidates, list):
            return False
        if run.get("modality") == "video" and run.get("remoteTaskId"):
            decision = "fallback_blocked_remote_identity"
        if run.get("modality") == "text" and run.get("outputItems", [{}])[0].get("chunks"):
            decision = "fallback_blocked_partial_result"
        if run.get("modality") == "image" and any(item.get("status") != "pending" for item in run.get("outputItems", [])):
            decision = "fallback_blocked_partial_result"
        actual = run.get("actualRoute")
        current_index = next((index for index, candidate in enumerate(candidates)
            if isinstance(candidate, dict) and isinstance(actual, dict)
            and candidate.get("provider") == actual.get("provider") and candidate.get("model") == actual.get("model")), -1)
        if current_index < 0:
            self._mark_route_failure(project_id, run_id, error.code, "fallback_blocked_capability_unavailable")
            return False
        next_index = current_index + 1
        if decision != "fallback" or next_index >= len(candidates):
            self._mark_route_failure(project_id, run_id, error.code, decision if decision != "fallback" else "fallback_exhausted")
            return False
        next_route = candidates[next_index]
        try:
            catalog = self._catalog_from_frozen(next_route)
            adapter = self.registry.resolve_by_id(catalog["adapterId"])
            if adapter.version != catalog["adapterVersion"] or adapter.modality != run.get("modality"):
                raise GenerationError("generation_adapter_version_unavailable")
        except GenerationError as resolution_error:
            def unavailable(value: dict[str, Any]) -> None:
                if value.get("routeAttempts"):
                    value["routeAttempts"][-1].update(
                        status="failed", failureCode=error.code,
                        fallbackDecision="fallback_blocked_capability_unavailable",
                    )
                value["reason"] = resolution_error.code
            self._mutate(project_id, run_id, unavailable)
            error.code = resolution_error.code
            return False

        def switch(value: dict[str, Any]) -> None:
            if value.get("status") not in {"running", "submitting"} or value.get("actualRoute") != actual:
                return
            value["routeAttempts"][-1].update(status="failed", failureCode=error.code, fallbackDecision="fallback")
            value.update(
                provider=next_route["provider"], model=next_route["model"],
                configurationVersion=catalog["configurationVersion"], capabilitySnapshot=catalog["capabilities"],
                adapterId=catalog["adapterId"], adapterVersion=catalog["adapterVersion"],
                actualRoute={"provider": next_route["provider"], "model": next_route["model"]},
            )
            value["routeAttempts"].append(self._route_attempt(catalog, next_route))
            value["logs"].append({"event": "generation_route_fallback", "reason": error.code})
        changed = self._mutate(project_id, run_id, switch)
        if changed is None or changed.get("actualRoute") != {"provider": next_route["provider"], "model": next_route["model"]}:
            return False
        if changed.get("modality") == "video":
            self._submit_video_remote(project_id, run_id)
        else:
            self._execute(project_id, run_id)
        return True

    def _mark_route_failure(self, project_id: str, run_id: str, code: str, decision: str) -> None:
        def mark(value: dict[str, Any]) -> None:
            attempts = value.get("routeAttempts")
            if isinstance(attempts, list) and attempts:
                attempts[-1].update(status="failed", failureCode=code, fallbackDecision=decision)
        self._mutate(project_id, run_id, mark)

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
        self._mark_route_completed(project_id, run_id)
        self._final(project_id, run_id, "completed", "completed", None, False)

    def _mark_route_completed(self, project_id: str, run_id: str) -> None:
        def mark(value: dict[str, Any]) -> None:
            attempts = value.get("routeAttempts")
            if isinstance(attempts, list) and attempts:
                attempts[-1].update(status="completed", failureCode=None, fallbackDecision="completed")
        self._mutate(project_id, run_id, mark)

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
            attempts = run.get("routeAttempts")
            if status != "completed" and isinstance(attempts, list) and attempts and attempts[-1].get("status") == "selected":
                attempts[-1].update(
                    status="cancelled" if status == "cancelled" else "partial" if status == "partial_success" else "failed",
                    failureCode=reason or "generation_result_incomplete",
                    fallbackDecision="fallback_not_requested_cancelled" if status == "cancelled"
                    else "fallback_blocked_partial_result" if status == "partial_success" else "fallback_blocked_unknown_outcome",
                )
            run.update(
                status=status,
                outcome=outcome,
                reason=reason,
                billingUnknown=billing_unknown,
            )
            output_status = "failed" if status == "failed" else outcome
            run["outputItems"][0]["status"] = output_status

        self._mutate(project_id, run_id, finish)

    def _record_late_text_result(self, project_id: str, run_id: str, chunks: list[dict[str, Any]], adapter: GenerationAdapter) -> None:
        text = "".join(item["text"] for item in chunks)
        try:
            if adapter.result_validator is not None:
                adapter.result_validator(text)
        except Exception:
            return
        def finish(run: dict[str, Any]) -> None:
            if run.get("status") not in _TERMINAL_STATUSES and run.get("status") != "cancel_requested":
                return
            item = run["outputItems"][0]
            item.update(status="completed", text=text, chunks=chunks)
            if run.get("routeAttempts") and run["routeAttempts"][-1].get("status") == "selected":
                run["routeAttempts"][-1].update(status="completed", failureCode=None, fallbackDecision="completed")
            run.update(status="completed", outcome="late_success", reason=None, billingUnknown=bool(run.get("billingUnknown")))
        self._mutate(project_id, run_id, finish)

    def cancel(self, project_id: str, run_id: str) -> dict[str, Any]:
        run = self.get_run(project_id, run_id)
        if run["status"] == "queued":
            if run.get("modality") == "image":
                self._finish_image_terminal(
                    project_id, run_id, outcome="cancelled", reason="generation_cancelled",
                    billing_unknown=False,
                )
            else:
                self._final(project_id, run_id, "cancelled", "cancelled", "generation_cancelled", False)
            self._mutate(project_id, run_id, lambda value: value.update(localCancelResult="cancelled"))
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
            self._mutate(project_id, run_id, lambda value: value.update(localCancelResult="unknown"))
            if run.get("modality") == "image":
                self._finish_image_terminal(
                    project_id, run_id, outcome="unknown", reason="generation_cancel_unknown",
                    billing_unknown=True,
                )
            else:
                self._final(project_id, run_id, "failed", "unknown", "generation_cancel_unknown", True)
        else:
            self._mutate(project_id, run_id, lambda value: value.update(localCancelResult="cancelled"))
            if run.get("modality") == "image":
                self._finish_image_terminal(
                    project_id, run_id, outcome="cancelled", reason="generation_cancelled",
                    billing_unknown=True,
                )
            else:
                self._final(project_id, run_id, "cancelled", "cancelled", "generation_cancelled", True)
        return self.get_run(project_id, run_id)
