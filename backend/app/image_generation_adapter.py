"""Verified image-provider adapter for the shared GenerationService facade."""

from pathlib import Path
from typing import Any, Iterable

from .generation_service import GenerationAdapter, GenerationError
from .project_assets import AssetFileUnavailableError, ProjectAssets


class ConfiguredImageGenerationAdapter:
    """Map verified public model capabilities onto the existing image caller."""

    adapter_id = "configured-image"
    version = "image-protocol-v1"

    def __init__(self, root: Path, configurations: Any, image_service: Any) -> None:
        self._assets = ProjectAssets(root)
        self._configurations = configurations
        self._image_service = image_service

    def as_generation_adapter(self) -> GenerationAdapter:
        return GenerationAdapter(
            self.adapter_id,
            self.version,
            "image",
            self.execute,
            self.capability_catalog,
        )

    def capability_catalog(self, provider: str, model: str) -> dict[str, Any] | None:
        try:
            configuration = self._configurations.snapshot()
        except Exception as error:
            raise GenerationError("generation_adapter_resolution_failed") from error
        if not isinstance(configuration, dict):
            raise GenerationError("generation_capability_unavailable")
        entries = configuration.get("providers")
        catalog = configuration.get("modelCatalog")
        if not isinstance(entries, list) or not isinstance(catalog, list):
            return None
        provider_entry = next((
            item for item in entries
            if isinstance(item, dict) and item.get("providerId") == provider
        ), None)
        model_entry = next((
            item for item in catalog
            if isinstance(item, dict)
            and item.get("providerId") == provider
            and item.get("modelId") == model
            and item.get("mediaType") == "image"
        ), None)
        if (
            provider_entry is None
            or provider_entry.get("credentialState") != "configured"
            or model_entry is None
            or model_entry.get("capabilitySource") != "verified"
            or not isinstance(model_entry.get("capabilityVersion"), str)
            or not model_entry["capabilityVersion"]
            or not isinstance(model_entry.get("capabilities"), dict)
            or not model_entry["capabilities"]
        ):
            return None
        revision = configuration.get("revision")
        if not isinstance(revision, str) or not revision:
            raise GenerationError("generation_capability_unavailable")
        capabilities = {
            **model_entry["capabilities"],
            # The existing synchronous provider caller has no remote-cancel
            # endpoint, returns one validated image per request, and has no
            # distinct model-super-resolution operation.
            "minResults": 1,
            "maxResults": 1,
            "cancel": False,
            "superResolution": False,
        }
        return {
            "provider": provider,
            "model": model,
            "configurationVersion": f"{revision}:{model_entry['capabilityVersion']}",
            "capabilities": capabilities,
        }

    def execute(self, run: dict[str, Any]) -> Iterable[dict[str, Any]]:
        catalog = self.capability_catalog(run.get("provider"), run.get("model"))
        if catalog is None or catalog["configurationVersion"] != run.get("configurationVersion"):
            raise GenerationError("generation_configuration_changed")
        configuration = self._configurations.snapshot()
        provider_entry = next(
            item for item in configuration["providers"]
            if item.get("providerId") == run["provider"]
        )
        pending = [item for item in run.get("outputItems", []) if item.get("status") == "pending"]
        if len(pending) != 1:
            raise GenerationError("generation_capability_unavailable")
        params = run.get("input", {}).get("imageParams", {})
        references = params.get("references", [])
        input_paths = []
        project_id = run.get("_projectId")
        if not isinstance(project_id, str) or not project_id:
            raise GenerationError("generation_reference_invalid")
        for reference in references:
            try:
                asset = self._assets.get(project_id, reference["assetId"])
            except (KeyError, TypeError, ValueError):
                raise GenerationError("generation_reference_invalid") from None
            if self._assets.version_id(asset) != reference.get("assetVersionId"):
                raise GenerationError("generation_reference_invalid")
            try:
                input_paths.append(self._assets.file(project_id, asset))
            except (AssetFileUnavailableError, KeyError, TypeError, ValueError):
                raise GenerationError("generation_reference_invalid") from None
        snapshot = {
            "baseUrl": provider_entry.get("baseUrl"),
            "provider": provider_entry.get("protocol"),
            "providerId": run["provider"],
            "model": run["model"],
            "revision": configuration.get("revision"),
            "prompt": run["input"]["prompt"],
            "size": params.get("resolution"),
        }
        try:
            content = self._image_service.generate(snapshot, input_images=input_paths)
        except GenerationError:
            raise
        except Exception as error:
            code = getattr(error, "code", "generation_provider_failed")
            unknown = bool(getattr(error, "outcome_unknown", True))
            raise GenerationError(code, outcome="unknown" if unknown else "failed", billing_unknown=unknown) from None
        yield {
            "type": "result",
            "index": pending[0]["index"],
            "content": content,
            "name": f"模型图片结果 {pending[0]['index'] + 1}.png",
        }
        yield {"type": "done"}
