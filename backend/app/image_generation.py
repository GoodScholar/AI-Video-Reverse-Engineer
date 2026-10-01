"""Cloud image-generation configuration for OpenAI-compatible and Volcengine Ark APIs.

Only ``b64_json`` image responses are accepted, so provider URL responses are
never fetched into this process.  The Ark protocol is adapted from the fixed
Apache-2.0 source recorded under ``vendor/VolcengineArk``; Python 3.9 uses the
small HTTP adaptation here instead of importing that newer SDK.
"""

import base64
import binascii
import io
import json
import os
import re
import tempfile
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Optional
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import httpx
from PIL import Image, UnidentifiedImageError

from .analysis_service_secrets import ProviderSecretStore, SecureStorageUnavailable


_ACCOUNT = "image-generation"
_SERVICE = "ai-video-reverse-engineer.image-generation"
_MAX_RESPONSE_BYTES = 32 * 1024 * 1024
_MAX_INPUT_BYTES = 32 * 1024 * 1024
_MAX_IMAGE_PIXELS = 64_000_000
_MAX_CANVAS_IMAGE_DIMENSION = 8192
# Application resource budgets, not assertions about any provider or model capability.
_MAX_INPUT_IMAGES = 32
_MAX_TOTAL_INPUT_BYTES = 64 * 1024 * 1024
_OPENAI_COMPATIBLE = "openai-compatible"
_VOLCENGINE_ARK = "volcengine-ark"
_PROVIDERS = frozenset({_OPENAI_COMPATIBLE, _VOLCENGINE_ARK})
_PROVIDER_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_PROTOCOL_CAPABILITIES = {
    _OPENAI_COMPATIBLE: {
        "referenceImages": "single",
        "sizeMode": "provider-defined",
    },
    _VOLCENGINE_ARK: {
        "referenceImages": "multiple",
        "sizeMode": "custom-pixels",
        "sizePattern": "<width>x<height>",
    },
}


class ImageGenerationError(RuntimeError):
    """A stable, safe result for a possibly billable generation attempt."""

    def __init__(self, code: str, *, outcome_unknown: bool = False) -> None:
        self.code = code
        self.outcome_unknown = outcome_unknown
        super().__init__(code)


class ImageGenerationSecretStore(ProviderSecretStore):
    """Uses an account and service separate from analysis-provider credentials."""

    def __init__(self, backend=None) -> None:
        super().__init__(backend, service_name=_SERVICE, allowed_provider_ids=frozenset({_ACCOUNT}), account_prefix="")

    def _account_name(self, provider_id: str) -> str:
        # The legacy account remains readable. New stable provider IDs use an
        # isolated namespace in the same secure service.
        if provider_id == _ACCOUNT:
            return _ACCOUNT
        if not isinstance(provider_id, str) or not _PROVIDER_ID_PATTERN.fullmatch(provider_id):
            raise ValueError("图片生成供应商标识无效。")
        return "provider:" + provider_id


def validate_image_generation_base_url(value: str) -> str:
    """Accept HTTPS endpoints, plus HTTP loopback endpoints for development."""

    if not isinstance(value, str) or not value or any(ord(char) < 0x20 or ord(char) == 0x7F for char in value):
        raise ValueError("图片生成服务地址无效。")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise ValueError("图片生成服务地址无效。") from None
    hostname = parsed.hostname.lower() if parsed.hostname else None
    if (
        parsed.scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port is None and parsed.netloc.endswith(":")
        or parsed.scheme == "http" and hostname not in {"localhost", "127.0.0.1", "::1"}
    ):
        raise ValueError("图片生成服务地址必须使用 HTTPS；本地开发仅允许 HTTP 回环地址。")
    authority = hostname if ":" not in hostname else f"[{hostname}]"
    if port is not None:
        authority = f"{authority}:{port}"
    return urlunsplit((parsed.scheme, authority, parsed.path.rstrip("/"), "", ""))


class ImageGenerationSettings:
    """Atomically persist public provider entries; credentials stay in keyring."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = RLock()

    def get(self) -> Optional[dict[str, Any]]:
        with self._lock:
            if not self._path.is_file():
                return None
            try:
                payload = json.loads(self._path.read_text(encoding="utf-8"))
                revision = payload["revision"]
                providers = _providers_from_payload(payload)
                active_id = payload.get("activeProviderId", "default")
                active = next(item for item in providers if item["providerId"] == active_id)
            except (OSError, ValueError, KeyError, StopIteration, TypeError, json.JSONDecodeError):
                raise ValueError("图片生成配置无效。") from None
            if not isinstance(revision, str) or not revision:
                raise ValueError("图片生成配置无效。")
            return _settings_view(providers, active_id, revision)

    def save(self, *, providers: Optional[list[dict[str, Any]]] = None, active_provider_id: str = "default", revision: Optional[str] = None,
             base_url: Optional[str] = None, model: Optional[str] = None, provider: str = _OPENAI_COMPATIBLE, models: Optional[list[str]] = None) -> dict[str, Any]:
        if providers is None:
            if not isinstance(base_url, str) or not isinstance(model, str):
                raise ValueError("图片生成服务地址和模型不能为空。")
            providers = [{"providerId": "default", "protocol": provider, "baseUrl": base_url, "defaultModelId": model,
                          "models": [{"modelId": item} for item in (models if models is not None else [model])]}]
        normalized = _valid_providers(providers)
        if active_provider_id not in {item["providerId"] for item in normalized}:
            raise ValueError("活动图片生成供应商不存在。")
        active = next(item for item in normalized if item["providerId"] == active_provider_id)
        saved = {
            # Retain these fields so old settings files and tooling remain readable.
            "baseUrl": active["baseUrl"], "provider": active["protocol"], "model": active["defaultModelId"],
            "models": [item["modelId"] for item in active["models"]],
            "providers": normalized, "activeProviderId": active_provider_id, "revision": revision or str(uuid4()),
        }
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(prefix=".image-generation-", dir=self._path.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    json.dump(saved, output, ensure_ascii=False, separators=(",", ":"))
                    output.flush(); os.fsync(output.fileno())
                os.replace(temporary_name, self._path)
            except Exception:
                try: os.unlink(temporary_name)
                except FileNotFoundError: pass
                raise
        return _settings_view(normalized, active_provider_id, saved["revision"])


class ImageGenerationConfigurations:
    """Coordinates public provider entries with isolated credential accounts."""

    def __init__(self, settings: ImageGenerationSettings, *, secret_store: Callable[[], Any] = ImageGenerationSecretStore):
        self._settings = settings
        self._secret_store = secret_store
        self._lock = RLock()

    def get(self) -> dict[str, Any]:
        with self._lock:
            saved = self._settings.get()
            return self._public(saved, self._credential_states(saved))

    def snapshot(self) -> dict[str, Any]:
        return self.get()

    def save(self, *, base_url: Optional[str] = None, model: Optional[str] = None, provider: Optional[str] = None,
             models: Optional[list[str]] = None, api_key: Optional[str] = None, expected_revision: Optional[str] = None,
             providers: Optional[list[dict[str, Any]]] = None, active_provider_id: Optional[str] = None) -> dict[str, Any]:
        if api_key is not None and (not isinstance(api_key, str) or not api_key.strip()):
            raise ValueError("图片生成密钥不能为空白。")
        with self._lock:
            previous = self._settings.get()
            if (previous is None and expected_revision is not None) or (previous is not None and expected_revision != previous["revision"]):
                raise ImageGenerationError("configuration_changed")
            if providers is None:
                if not isinstance(base_url, str) or not isinstance(model, str):
                    raise ValueError("图片生成服务地址和模型不能为空。")
                selected_protocol = _valid_provider(provider if provider is not None else previous["provider"] if previous else _OPENAI_COMPATIBLE)
                selected_models = _valid_models(models if models is not None else previous["models"] if previous else [model], model)
                entries = [{"providerId": "default", "protocol": selected_protocol, "baseUrl": base_url,
                            "defaultModelId": model, "models": [{"modelId": item} for item in selected_models]}]
                active_id = "default"
                credentials = {"default": api_key} if api_key is not None else {}
            else:
                entries = providers
                active_id = active_provider_id or (previous or {}).get("activeProviderId", "default")
                credentials = {item["providerId"]: item.get("apiKey") for item in providers if isinstance(item, dict) and item.get("apiKey") is not None}
            normalized = _valid_providers(entries)
            if active_id not in {item["providerId"] for item in normalized}:
                raise ValueError("活动图片生成供应商不存在。")
            old_entries = {item["providerId"]: item for item in (previous or {}).get("providers", [])}
            store = self._secret_store()
            changed_accounts: list[tuple[str, Optional[str]]] = []
            try:
                for entry in normalized:
                    provider_id = entry["providerId"]
                    old = old_entries.get(provider_id)
                    endpoint_changed = old is None or old["baseUrl"] != entry["baseUrl"] or old["protocol"] != entry["protocol"]
                    supplied = credentials.get(provider_id)
                    account = _credential_account(provider_id)
                    if endpoint_changed and supplied is None:
                        raise ValueError("更换图片生成服务地址或协议时必须提供新的密钥。")
                    if supplied is not None:
                        old_key = store.get(account)
                        store.set(account, supplied.strip())
                        changed_accounts.append((account, old_key))
                changed = previous is None or previous.get("providers") != normalized or previous.get("activeProviderId") != active_id or bool(changed_accounts)
                saved = self._settings.save(providers=normalized, active_provider_id=active_id, revision=None if changed else previous["revision"])
            except Exception:
                for account, old_key in reversed(changed_accounts):
                    try:
                        if old_key is None: store.delete(account)
                        else: store.set(account, old_key)
                    except (AttributeError, SecureStorageUnavailable): pass
                raise
            return self._public(saved, self._credential_states(saved, store))

    def configured_snapshot(self, snapshot: dict) -> tuple[dict[str, Any], str]:
        if not isinstance(snapshot, dict):
            raise ImageGenerationError("configuration_changed")
        with self._lock:
            saved = self._settings.get()
            if saved is None or snapshot.get("revision") != saved["revision"]:
                raise ImageGenerationError("configuration_changed")
            provider_id = snapshot.get("providerId") or saved["activeProviderId"]
            entry = next((item for item in saved["providers"] if item["providerId"] == provider_id), None)
            if entry is None or snapshot.get("provider") != entry["protocol"] or snapshot.get("baseUrl") != entry["baseUrl"]:
                raise ImageGenerationError("configuration_changed")
            if snapshot.get("model") not in [item["modelId"] for item in entry["models"]]:
                raise ImageGenerationError("configuration_changed")
            try: credential = self._secret_store().get(_credential_account(provider_id))
            except SecureStorageUnavailable: credential = None
            if not isinstance(credential, str) or not credential.strip():
                raise ImageGenerationError("provider_unconfigured")
            return {"baseUrl": entry["baseUrl"], "provider": entry["protocol"], "model": snapshot["model"], "models": [item["modelId"] for item in entry["models"]], "revision": saved["revision"]}, credential

    def _credential_states(self, saved: Optional[dict[str, Any]], store=None) -> dict[str, bool]:
        if saved is None: return {}
        return {item["providerId"]: self._credential_configured(store, item["providerId"]) for item in saved["providers"]}

    def _credential_configured(self, store=None, provider_id: str = "default") -> bool:
        try: value = (store or self._secret_store()).get(_credential_account(provider_id))
        except SecureStorageUnavailable: return False
        return isinstance(value, str) and bool(value.strip())

    @staticmethod
    def _public(saved: Optional[dict[str, Any]], credential_states: dict[str, bool]) -> dict[str, Any]:
        if saved is None:
            return {"baseUrl": None, "provider": None, "model": None, "models": [], "providers": [], "modelCatalog": [], "protocolCapabilities": None, "credentialState": "unconfigured", "revision": None, "configVersion": None}
        active = next(item for item in saved["providers"] if item["providerId"] == saved["activeProviderId"])
        catalog = []
        public_providers = []
        for entry in saved["providers"]:
            configured = credential_states.get(entry["providerId"], False)
            public_providers.append({"providerId": entry["providerId"], "protocol": entry["protocol"], "baseUrl": entry["baseUrl"], "defaultModelId": entry["defaultModelId"], "models": [{**model, "configured": configured} for model in entry["models"]], "credentialState": "configured" if configured else "unconfigured"})
            for model in entry["models"]:
                capabilities = model.get("capabilities", {})
                catalog.append({"providerId": entry["providerId"], "modelId": model["modelId"], "mediaType": "image", "label": model.get("label", model["modelId"]), "configured": configured, "capabilitySource": "user-configured" if capabilities else "unknown", "capabilityVersion": saved["revision"], "capabilities": capabilities})
        return {"baseUrl": active["baseUrl"], "provider": active["protocol"], "model": active["defaultModelId"], "models": [item["modelId"] for item in active["models"]], "providers": public_providers, "activeProviderId": saved["activeProviderId"], "modelCatalog": catalog, "protocolCapabilities": _PROTOCOL_CAPABILITIES[active["protocol"]], "credentialState": "configured" if credential_states.get(active["providerId"], False) else "unconfigured", "revision": saved["revision"], "configVersion": saved["revision"]}


def _credential_account(provider_id: str) -> str:
    return _ACCOUNT if provider_id == "default" else provider_id


def _providers_from_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if "providers" in payload:
        return _valid_providers(payload["providers"])
    return _valid_providers([{"providerId": "default", "protocol": payload.get("provider", _OPENAI_COMPATIBLE), "baseUrl": payload["baseUrl"], "defaultModelId": payload["model"], "models": [{"modelId": item} for item in payload.get("models", [payload["model"]])]}])


def _settings_view(providers: list[dict[str, Any]], active_id: str, revision: str) -> dict[str, Any]:
    active = next(item for item in providers if item["providerId"] == active_id)
    return {"baseUrl": active["baseUrl"], "provider": active["protocol"], "model": active["defaultModelId"], "models": [item["modelId"] for item in active["models"]], "providers": providers, "activeProviderId": active_id, "revision": revision}


def _valid_providers(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError("图片生成供应商列表不能为空。")
    entries = []
    for raw in value:
        if not isinstance(raw, dict): raise ValueError("图片生成供应商配置无效。")
        provider_id = raw.get("providerId")
        if not isinstance(provider_id, str) or not _PROVIDER_ID_PATTERN.fullmatch(provider_id) or provider_id == _ACCOUNT: raise ValueError("图片生成供应商标识无效。")
        protocol = _valid_provider(raw.get("protocol"))
        base_url = validate_image_generation_base_url(raw.get("baseUrl"))
        default_model = raw.get("defaultModelId")
        raw_models = raw.get("models")
        if not isinstance(raw_models, list) or not raw_models: raise ValueError("图片生成模型列表不能为空。")
        models = []
        for raw_model in raw_models:
            if not isinstance(raw_model, dict): raise ValueError("图片生成模型配置无效。")
            model_id = raw_model.get("modelId")
            if not isinstance(model_id, str) or not model_id.strip() or len(model_id.strip()) > 256 or any(item["modelId"] == model_id.strip() for item in models): raise ValueError("图片生成模型配置无效。")
            label = raw_model.get("label")
            if label is not None and (not isinstance(label, str) or not label.strip() or len(label) > 256): raise ValueError("图片生成模型配置无效。")
            capabilities = raw_model.get("capabilities", {})
            if not isinstance(capabilities, dict) or set(capabilities) - {"imageSizes", "imageRatios", "maxReferences"}: raise ValueError("图片模型能力配置无效。")
            if "imageSizes" in capabilities and (not isinstance(capabilities["imageSizes"], list) or any(not isinstance(item, str) or not _valid_canvas_size(item) for item in capabilities["imageSizes"])): raise ValueError("图片模型能力配置无效。")
            if "imageRatios" in capabilities and (not isinstance(capabilities["imageRatios"], list) or any(not isinstance(item, str) or not re.fullmatch(r"[1-9][0-9]{0,3}:[1-9][0-9]{0,3}", item) for item in capabilities["imageRatios"])): raise ValueError("图片模型能力配置无效。")
            if "maxReferences" in capabilities and (not isinstance(capabilities["maxReferences"], int) or isinstance(capabilities["maxReferences"], bool) or not 0 <= capabilities["maxReferences"] <= _MAX_INPUT_IMAGES): raise ValueError("图片模型能力配置无效。")
            models.append({"modelId": model_id.strip(), **({"label": label.strip()} if isinstance(label, str) else {}), **({"capabilities": capabilities} if capabilities else {})})
        if default_model not in [item["modelId"] for item in models]: raise ValueError("默认图片生成模型必须位于模型列表中。")
        entries.append({"providerId": provider_id, "protocol": protocol, "baseUrl": base_url, "defaultModelId": default_model, "models": models})
    if len({item["providerId"] for item in entries}) != len(entries): raise ValueError("图片生成供应商标识重复。")
    return entries


class ImageGenerationService:
    """Executes one configured image request without retrying paid calls."""

    def __init__(
        self,
        configurations: ImageGenerationConfigurations,
        *,
        client_factory: Optional[Callable[[], httpx.Client]] = None,
    ) -> None:
        self._configurations = configurations
        self._client_factory = client_factory or self._new_client

    def generate(
        self,
        snapshot: dict,
        input_image: Optional[Path] = None,
        input_images: Optional[list[Path]] = None,
    ) -> bytes:
        saved, credential = self._configurations.configured_snapshot(snapshot)
        prompt = snapshot.get("prompt") if isinstance(snapshot, dict) else None
        size = snapshot.get("size") if isinstance(snapshot, dict) else None
        if not isinstance(prompt, str) or not prompt.strip() or not isinstance(size, str) or not _valid_size(size):
            raise ImageGenerationError("invalid_request")
        images = _input_images(input_image, input_images)
        provider = saved["provider"]
        if provider == _OPENAI_COMPATIBLE and len(images) > 1:
            raise ImageGenerationError("multiple_input_images_unsupported")
        endpoint = saved["baseUrl"] + (
            "/images/edits" if provider == _OPENAI_COMPATIBLE and images else "/images/generations"
        )
        headers = {"Authorization": "Bearer " + credential}
        try:
            with self._client_factory() as client:
                if provider == _VOLCENGINE_ARK:
                    payload = {
                        "model": snapshot["model"],
                        "prompt": prompt,
                        "size": size,
                        "response_format": "b64_json",
                    }
                    if images:
                        references = [_data_url_from_image(image) for image in images]
                        payload["image"] = references[0] if len(references) == 1 else references
                    body = self._post_json(client, endpoint, headers, payload)
                elif not images:
                    body = self._post_json(client, endpoint, headers, {
                        "model": snapshot["model"], "prompt": prompt, "n": 1, "size": size,
                    })
                else:
                    body = self._post_edit(client, endpoint, headers, images[0], {
                        "model": snapshot["model"], "prompt": prompt, "n": "1", "size": size,
                    })
        except ImageGenerationError:
            raise
        except httpx.TimeoutException:
            raise ImageGenerationError("timeout", outcome_unknown=True) from None
        except httpx.RequestError:
            raise ImageGenerationError("network_error", outcome_unknown=True) from None
        return _png_from_response(body)

    @staticmethod
    def _new_client() -> httpx.Client:
        return httpx.Client(
            timeout=httpx.Timeout(300.0), trust_env=False, follow_redirects=False,
        )

    def _post_json(self, client, endpoint: str, headers: dict[str, str], payload: dict[str, Any]) -> Any:
        with client.stream("POST", endpoint, headers=headers, json=payload, follow_redirects=False) as response:
            return _response_body(response)

    def _post_edit(self, client, endpoint: str, headers: dict[str, str], image: Path, payload: dict[str, str]) -> Any:
        try:
            filename, content, mime_type = _normalized_edit_image(image)
            with client.stream(
                "POST", endpoint, headers=headers, data=payload,
                files={"image": (filename, content, mime_type)}, follow_redirects=False,
            ) as response:
                return _response_body(response)
        except OSError:
            raise ImageGenerationError("invalid_input") from None


def _response_body(response: httpx.Response) -> Any:
    if response.is_redirect or response.status_code >= 500:
        raise ImageGenerationError("provider_error", outcome_unknown=True)
    if response.status_code >= 400:
        code = "authentication_failed" if response.status_code in {401, 403} else "rate_limited" if response.status_code == 429 else "request_failed"
        raise ImageGenerationError(code)
    content_length = response.headers.get("content-length")
    if content_length is not None and (not content_length.isdigit() or int(content_length) > _MAX_RESPONSE_BYTES):
        raise ImageGenerationError("invalid_response", outcome_unknown=True)
    content = bytearray()
    for chunk in response.iter_bytes():
        if len(content) + len(chunk) > _MAX_RESPONSE_BYTES:
            raise ImageGenerationError("invalid_response", outcome_unknown=True)
        content.extend(chunk)
    try:
        return json.loads(content)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise ImageGenerationError("invalid_response", outcome_unknown=True) from None


def _png_from_response(body: Any) -> bytes:
    try:
        encoded = body["data"][0]["b64_json"]
        if not isinstance(encoded, str):
            raise TypeError()
        image_bytes = base64.b64decode(encoded, validate=True)
        with Image.open(io.BytesIO(image_bytes)) as image:
            image.verify()
        with Image.open(io.BytesIO(image_bytes)) as image:
            if image.width * image.height > _MAX_IMAGE_PIXELS:
                raise ValueError()
            image.load()
            output = io.BytesIO()
            image.convert("RGBA" if "A" in image.getbands() else "RGB").save(output, format="PNG")
            return output.getvalue()
    except (KeyError, IndexError, TypeError, ValueError, binascii.Error, UnidentifiedImageError, OSError):
        raise ImageGenerationError("invalid_response", outcome_unknown=True) from None


def _normalized_edit_image(image: Path) -> tuple[str, bytes, str]:
    """Verify a project image before multipart upload and give it an honest MIME type."""

    source = Path(image)
    if not source.is_file() or source.is_symlink() or source.stat().st_size > _MAX_INPUT_BYTES:
        raise ImageGenerationError("invalid_input")
    content = source.read_bytes()
    try:
        with Image.open(io.BytesIO(content)) as opened:
            image_format = opened.format
            opened.verify()
        with Image.open(io.BytesIO(content)) as opened:
            if opened.width * opened.height > _MAX_IMAGE_PIXELS:
                raise ValueError()
            opened.load()
            known_formats = {
                "PNG": ("input.png", "image/png"),
                "JPEG": ("input.jpg", "image/jpeg"),
                "WEBP": ("input.webp", "image/webp"),
            }
            if image_format in known_formats:
                filename, mime_type = known_formats[image_format]
                return filename, content, mime_type
            normalized = io.BytesIO()
            opened.convert("RGBA" if "A" in opened.getbands() else "RGB").save(normalized, format="PNG")
            return "input.png", normalized.getvalue(), "image/png"
    except (ValueError, UnidentifiedImageError, OSError):
        raise ImageGenerationError("invalid_input") from None


def _data_url_from_image(image: Path) -> str:
    _, content, mime_type = _normalized_edit_image(image)
    return "data:{};base64,{}".format(mime_type, base64.b64encode(content).decode("ascii"))


def _input_images(input_image: Optional[Path], input_images: Optional[list[Path]]) -> list[Path]:
    if input_image is not None and input_images is not None:
        raise ImageGenerationError("invalid_input")
    if input_images is not None:
        if not isinstance(input_images, list):
            raise ImageGenerationError("invalid_input")
        images = [Path(image) for image in input_images]
    else:
        images = [Path(input_image)] if input_image is not None else []
    try:
        total_bytes = 0
        for image in images:
            if not image.is_file() or image.is_symlink():
                raise ImageGenerationError("invalid_input")
            size = image.stat().st_size
            if size > _MAX_INPUT_BYTES:
                raise ImageGenerationError("invalid_input")
            total_bytes += size
    except OSError:
        raise ImageGenerationError("invalid_input") from None
    if len(images) > _MAX_INPUT_IMAGES or total_bytes > _MAX_TOTAL_INPUT_BYTES:
        raise ImageGenerationError("input_budget_exceeded")
    return images


def _valid_provider(value: Any) -> str:
    if value not in _PROVIDERS:
        raise ValueError("图片生成服务协议无效。")
    return value


def _valid_models(value: Any, default_model: Any) -> list[str]:
    if not isinstance(default_model, str) or not default_model.strip():
        raise ValueError("图片生成模型不能为空白。")
    if not isinstance(value, list) or not value:
        raise ValueError("图片生成模型列表不能为空。")
    models = []
    for model in value:
        if not isinstance(model, str) or not model.strip() or model.strip() in models:
            raise ValueError("图片生成模型列表无效。")
        models.append(model.strip())
    if default_model.strip() not in models:
        raise ValueError("默认图片生成模型必须位于模型列表中。")
    return models


def _valid_canvas_size(value: str) -> bool:
    parts = value.split("x")
    if len(parts) != 2 or any(not part.isdigit() or len(part) > 4 for part in parts):
        return False
    width, height = (int(part) for part in parts)
    return 64 <= width <= _MAX_CANVAS_IMAGE_DIMENSION and 64 <= height <= _MAX_CANVAS_IMAGE_DIMENSION and width * height <= _MAX_IMAGE_PIXELS


def _valid_size(value: str) -> bool:
    parts = value.split("x")
    return len(parts) == 2 and all(part.isdigit() and len(part) <= 9 and int(part) >= 1 for part in parts)
