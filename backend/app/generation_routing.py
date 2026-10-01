"""Non-sensitive, application-scoped routing policies for GenerationService."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any


class GenerationRoutingConflict(RuntimeError):
    """The policy collection changed after the editor read its revision."""


class GenerationRoutingPolicyError(ValueError):
    """The public policy shape is invalid or cannot be resolved."""


_MODALITIES = frozenset({"text", "image", "video"})
_ROUTE_KEYS = frozenset({"provider", "model"})
_POLICY_KEYS = frozenset({"id", "name", "modality", "routes"})


class GenerationRoutingPolicyStore:
    """Atomic CAS store.  Policies intentionally contain no configuration or secret."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._lock = RLock()

    def list(self, *, modality: str | None = None) -> dict[str, Any]:
        with self._lock:
            document = self._read()
            policies = document["policies"]
            if modality is not None:
                policies = [policy for policy in policies if policy["modality"] == modality]
            return {
                "revision": document["revision"],
                "policies": [{**policy, "revision": document["revision"]} for policy in policies],
            }

    def get(self, policy_id: str, *, revision: int | None = None) -> dict[str, Any]:
        with self._lock:
            document = self._read()
            if revision is not None and revision != document["revision"]:
                raise GenerationRoutingConflict("generation_routing_policy_revision_conflict")
            policy = next((item for item in document["policies"] if item["id"] == policy_id), None)
            if policy is None:
                raise GenerationRoutingPolicyError("generation_routing_policy_missing")
            return {**policy, "revision": document["revision"]}

    def save(self, policy: dict[str, Any], *, expected_revision: int) -> dict[str, Any]:
        """Create or replace one policy after a collection-level compare-and-swap."""
        clean = self._validate_policy(policy)
        if type(expected_revision) is not int or expected_revision < 0:
            raise GenerationRoutingPolicyError("generation_routing_policy_invalid")
        with self._lock:
            document = self._read()
            if document["revision"] != expected_revision:
                raise GenerationRoutingConflict("generation_routing_policy_revision_conflict")
            policies = [item for item in document["policies"] if item["id"] != clean["id"]]
            policies.append(clean)
            document = {"schemaVersion": 1, "revision": document["revision"] + 1, "policies": policies}
            self._write(document)
            return {**clean, "revision": document["revision"]}

    @staticmethod
    def _validate_policy(policy: Any) -> dict[str, Any]:
        if not isinstance(policy, dict) or set(policy) != _POLICY_KEYS:
            raise GenerationRoutingPolicyError("generation_routing_policy_invalid")
        policy_id, name, modality, routes = policy.get("id"), policy.get("name"), policy.get("modality"), policy.get("routes")
        if (
            not isinstance(policy_id, str) or not 1 <= len(policy_id) <= 120
            or not isinstance(name, str) or not 1 <= len(name.strip()) <= 160
            or modality not in _MODALITIES
            or not isinstance(routes, list) or not 1 <= len(routes) <= 16
        ):
            raise GenerationRoutingPolicyError("generation_routing_policy_invalid")
        clean_routes: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for route in routes:
            if not isinstance(route, dict) or set(route) != _ROUTE_KEYS:
                raise GenerationRoutingPolicyError("generation_routing_policy_invalid")
            provider, model = route.get("provider"), route.get("model")
            if (
                not isinstance(provider, str) or not 1 <= len(provider) <= 120
                or not isinstance(model, str) or not 1 <= len(model) <= 256
                or any(marker in provider.lower() or marker in model.lower() for marker in ("://", "api_key", "token", "credential"))
                or (provider, model) in seen
            ):
                raise GenerationRoutingPolicyError("generation_routing_policy_invalid")
            seen.add((provider, model))
            clean_routes.append({"provider": provider, "model": model})
        return {"id": policy_id, "name": name.strip(), "modality": modality, "routes": clean_routes}

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schemaVersion": 1, "revision": 0, "policies": []}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise GenerationRoutingPolicyError("generation_routing_policy_invalid") from error
        if (
            not isinstance(value, dict) or set(value) != {"schemaVersion", "revision", "policies"}
            or value.get("schemaVersion") != 1 or type(value.get("revision")) is not int or value["revision"] < 0
            or not isinstance(value.get("policies"), list)
        ):
            raise GenerationRoutingPolicyError("generation_routing_policy_invalid")
        validated = [self._validate_policy(item) for item in value["policies"]]
        if len({item["id"] for item in validated}) != len(validated):
            raise GenerationRoutingPolicyError("generation_routing_policy_invalid")
        return {"schemaVersion": 1, "revision": value["revision"], "policies": validated}

    def _write(self, value: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(dir=self.path.parent, prefix=".generation-routing-", suffix=".part")
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(value, output, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)
