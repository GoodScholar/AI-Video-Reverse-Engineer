"""OpenAI-compatible, streaming text generation for creative-canvas text nodes."""
from __future__ import annotations

import base64
import codecs
import json
import os
import random
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, RLock, Thread
from typing import Any, Callable, Iterator, Optional
from uuid import uuid4

import httpx
from PIL import Image, UnidentifiedImageError

from .analysis_service_secrets import ProviderSecretStore, SecureStorageUnavailable
from .analysis_providers.http_transport import new_provider_http_client
from .image_generation import validate_image_generation_base_url
from .project_assets import AssetFileUnavailableError, ProjectAssets


_ACCOUNT = "default"
_SERVICE = "ai-video-reverse-engineer.text-generation"
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_MAX_OUTPUT_CHARS = 12_000
_MAX_IMAGE_REFERENCES = 2
_MAX_IMAGE_BYTES = 16 * 1024 * 1024
_DEADLINE_SECONDS = 120


class TextGenerationError(RuntimeError):
    def __init__(self, code: str, *, outcome_unknown: bool = False, billing_unknown: bool = False) -> None:
        self.code = code
        self.outcome_unknown = outcome_unknown
        self.billing_unknown = billing_unknown
        super().__init__(code)


class TextGenerationSecretStore(ProviderSecretStore):
    def __init__(self, backend=None) -> None:
        super().__init__(backend, service_name=_SERVICE, allowed_provider_ids=frozenset({_ACCOUNT}), account_prefix="")


def _models(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ValueError("文本生成模型列表不能为空。")
    result = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("文本生成模型配置无效。")
        model_id = item.get("modelId")
        label = item.get("label")
        if not isinstance(model_id, str) or not model_id.strip() or len(model_id.strip()) > 256:
            raise ValueError("文本生成模型配置无效。")
        if label is not None and (not isinstance(label, str) or not label.strip() or len(label.strip()) > 256):
            raise ValueError("文本生成模型配置无效。")
        model = {"modelId": model_id.strip()}
        if label is not None:
            model["label"] = label.strip()
        if any(existing["modelId"] == model["modelId"] for existing in result):
            raise ValueError("文本生成模型标识重复。")
        result.append(model)
    return result


def _verification(value: Any, models: list[dict[str, str]]) -> dict[str, dict[str, Any]]:
    raw = value if isinstance(value, dict) else {}
    result = {}
    for model in models:
        item = raw.get(model["modelId"], {})
        if not isinstance(item, dict):
            raise ValueError("文本生成验证记录无效。")
        text_verified = bool(item.get("textVerified", False))
        image_verified = bool(item.get("imageVerified", False))
        max_refs = item.get("maxImageReferences", _MAX_IMAGE_REFERENCES if image_verified else 0)
        if not isinstance(max_refs, int) or isinstance(max_refs, bool) or not 0 <= max_refs <= _MAX_IMAGE_REFERENCES:
            raise ValueError("文本生成验证记录无效。")
        result[model["modelId"]] = {
            "textVerified": text_verified,
            "imageVerified": image_verified,
            "maxImageReferences": max_refs if image_verified else 0,
            **({"textVerifiedAt": item["textVerifiedAt"]} if isinstance(item.get("textVerifiedAt"), str) else {}),
            **({"imageVerifiedAt": item["imageVerifiedAt"]} if isinstance(item.get("imageVerifiedAt"), str) else {}),
        }
    return result


class TextGenerationSettings:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._lock = RLock()

    def get(self) -> Optional[dict[str, Any]]:
        with self._lock:
            if not self._path.is_file():
                return None
            try:
                value = json.loads(self._path.read_text(encoding="utf-8"))
                base_url = validate_image_generation_base_url(value["baseUrl"])
                models = _models(value["models"])
                revision = value["revision"]
                verifications = _verification(value.get("verifications"), models)
            except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
                raise ValueError("文本生成配置无效。") from None
            if not isinstance(revision, str) or not revision:
                raise ValueError("文本生成配置无效。")
            return {"baseUrl": base_url, "models": models, "revision": revision, "verifications": verifications}

    def save(self, *, base_url: str, models: list[dict[str, str]], revision: Optional[str] = None,
             verifications: Optional[dict[str, dict[str, Any]]] = None) -> dict[str, Any]:
        normalized = _models(models)
        saved = {
            "baseUrl": validate_image_generation_base_url(base_url),
            "models": normalized,
            "revision": revision or str(uuid4()),
            "verifications": _verification(verifications, normalized),
        }
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_name = tempfile.mkstemp(prefix=".text-generation-", dir=self._path.parent)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                    json.dump(saved, output, ensure_ascii=False, separators=(",", ":"))
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary_name, self._path)
            except Exception:
                Path(temporary_name).unlink(missing_ok=True)
                raise
        return saved


class TextGenerationConfigurations:
    def __init__(self, settings: TextGenerationSettings, *, secret_store: Callable[[], Any] = TextGenerationSecretStore) -> None:
        self._settings = settings
        self._secret_store = secret_store
        self._lock = RLock()

    def get(self) -> dict[str, Any]:
        with self._lock:
            saved = self._settings.get()
            return self._public(saved, self._has_credential())

    def catalog(self) -> list[dict[str, Any]]:
        return self.get()["modelCatalog"]

    def save(self, *, base_url: str, models: list[dict[str, str]], api_key: Optional[str] = None,
             expected_revision: Optional[str] = None) -> dict[str, Any]:
        if api_key is not None and (not isinstance(api_key, str) or not api_key.strip()):
            raise ValueError("文本生成密钥不能为空白。")
        with self._lock:
            previous = self._settings.get()
            if (previous is None and expected_revision is not None) or (previous is not None and expected_revision != previous["revision"]):
                raise TextGenerationError("configuration_changed")
            normalized_base_url = validate_image_generation_base_url(base_url)
            normalized_models = _models(models)
            changed = previous is None or previous["baseUrl"] != normalized_base_url or previous["models"] != normalized_models or api_key is not None
            if previous is not None and previous["baseUrl"] != normalized_base_url and api_key is None:
                raise ValueError("更换文本生成服务地址时必须提供新的密钥。")
            store = self._secret_store()
            old_key = None
            changed_key = False
            try:
                if api_key is not None:
                    old_key = store.get(_ACCOUNT)
                    store.set(_ACCOUNT, api_key.strip())
                    changed_key = True
                saved = self._settings.save(
                    base_url=normalized_base_url,
                    models=normalized_models,
                    revision=None if changed else previous["revision"],
                    verifications={} if changed else previous["verifications"],
                )
            except Exception:
                if changed_key:
                    try:
                        if old_key is None:
                            store.delete(_ACCOUNT)
                        else:
                            store.set(_ACCOUNT, old_key)
                    except (AttributeError, SecureStorageUnavailable):
                        pass
                raise
            return self._public(saved, self._has_credential(store))

    def record_verification(self, revision: str, model_id: str, *, mode: str,
                            max_image_references: int = _MAX_IMAGE_REFERENCES) -> dict[str, Any]:
        if mode not in {"text", "image"}:
            raise ValueError("文本生成验证模式无效。")
        with self._lock:
            saved = self._settings.get()
            if saved is None or saved["revision"] != revision or model_id not in saved["verifications"]:
                raise TextGenerationError("configuration_changed")
            if not self._has_credential():
                raise TextGenerationError("provider_unconfigured")
            now = datetime.now(timezone.utc).isoformat()
            verifications = {key: dict(value) for key, value in saved["verifications"].items()}
            target = verifications[model_id]
            if mode == "text":
                target.update(textVerified=True, textVerifiedAt=now)
            else:
                target.update(imageVerified=True, imageVerifiedAt=now, maxImageReferences=max_image_references)
            updated = self._settings.save(base_url=saved["baseUrl"], models=saved["models"], revision=saved["revision"], verifications=verifications)
            return self._public(updated, True)

    def configured_snapshot(self, snapshot: dict[str, Any], *, require_images: int = 0,
                            require_text_verification: bool = True) -> tuple[dict[str, Any], str]:
        if not isinstance(snapshot, dict):
            raise TextGenerationError("configuration_changed")
        with self._lock:
            saved = self._settings.get()
            if saved is None or snapshot.get("configurationVersion") != saved["revision"]:
                raise TextGenerationError("configuration_changed")
            if snapshot.get("providerId") != "default":
                raise TextGenerationError("configuration_changed")
            model_id = snapshot.get("modelId")
            verification = saved["verifications"].get(model_id)
            if verification is None:
                raise TextGenerationError("configuration_changed")
            if require_text_verification and not verification["textVerified"]:
                raise TextGenerationError("model_unverified")
            if require_images and (not verification["imageVerified"] or require_images > verification["maxImageReferences"]):
                raise TextGenerationError("image_references_unverified")
            try:
                credential = self._secret_store().get(_ACCOUNT)
            except SecureStorageUnavailable:
                credential = None
            if not isinstance(credential, str) or not credential.strip():
                raise TextGenerationError("provider_unconfigured")
            return {"baseUrl": saved["baseUrl"], "modelId": model_id, "revision": saved["revision"], "verification": verification}, credential

    def _has_credential(self, store=None) -> bool:
        try:
            value = (store or self._secret_store()).get(_ACCOUNT)
        except SecureStorageUnavailable:
            return False
        return isinstance(value, str) and bool(value.strip())

    @staticmethod
    def _public(saved: Optional[dict[str, Any]], credential: bool) -> dict[str, Any]:
        if saved is None:
            return {"providerId": "default", "baseUrl": None, "models": [], "revision": None,
                    "credentialState": "unconfigured", "modelCatalog": []}
        catalog = []
        for model in saved["models"]:
            verification = saved["verifications"][model["modelId"]]
            catalog.append({
                "providerId": "default", "providerLabel": "默认文本服务", "modelId": model["modelId"],
                "label": model.get("label", model["modelId"]),
                "configured": credential and verification["textVerified"],
                "configurationVersion": saved["revision"], "textVerified": verification["textVerified"],
                "imageVerified": verification["imageVerified"], "maxImageReferences": verification["maxImageReferences"],
            })
        return {"providerId": "default", "baseUrl": saved["baseUrl"], "models": saved["models"],
                "revision": saved["revision"],
                "credentialState": "configured" if credential else "unconfigured",
                "modelCatalog": catalog}


class _RequestLifecycle:
    """Interrupt an OpenAI-compatible request during headers and body streaming."""

    def __init__(self, client: httpx.Client, cancel_event: Optional[Event]) -> None:
        self._client = client
        self.cancel_event = cancel_event or Event()
        self.started = time.monotonic()
        self._closed = Event()
        self._cancelled = Event()
        self._deadline_expired = Event()
        self._response: Optional[httpx.Response] = None
        self._lock = RLock()
        self._watcher = Thread(target=self._watch, daemon=True)
        self._watcher.start()

    def attach_response(self, response: httpx.Response) -> None:
        with self._lock:
            self._response = response
        if self.interrupted():
            self._abort()

    def interrupted(self) -> bool:
        return self.cancel_event.is_set() or self._cancelled.is_set() or self._deadline_expired.is_set()

    def error(self, fallback: str = "network_failed") -> TextGenerationError:
        if self.cancel_event.is_set() or self._cancelled.is_set():
            return TextGenerationError("cancelled", outcome_unknown=True)
        if self._deadline_expired.is_set():
            return TextGenerationError("deadline_exceeded", outcome_unknown=True)
        return TextGenerationError(fallback, outcome_unknown=True)

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._abort()

    def _watch(self) -> None:
        while not self._closed.wait(0.05):
            if self.cancel_event.is_set():
                self._cancelled.set()
                self._abort()
                return
            if time.monotonic() - self.started >= _DEADLINE_SECONDS:
                self._deadline_expired.set()
                self._abort()
                return

    def _abort(self) -> None:
        with self._lock:
            response = self._response
        registry = getattr(self._client, "_aivre_active_socket_registry", None)
        cancel_active = getattr(registry, "cancel_active", None)
        if callable(cancel_active):
            cancel_active()
        if response is not None:
            try:
                response.close()
            except (httpx.HTTPError, OSError, RuntimeError):
                pass
        try:
            self._client.close()
        except (httpx.HTTPError, OSError, RuntimeError):
            pass


class _TextSSEStream(Iterator[str]):
    def __init__(self, response: httpx.Response, lifecycle: _RequestLifecycle) -> None:
        self._response = response
        self._lifecycle = lifecycle
        self._chunks = response.iter_bytes()
        self._decoder = codecs.getincrementaldecoder("utf-8")()
        self._line_buffer = ""
        self._input_exhausted = False
        self._bytes = 0
        self._characters = 0
        self._done = False
        self._finish_reason: Optional[str] = None
        self._overflowed = False

    def __iter__(self) -> "_TextSSEStream":
        return self

    def __next__(self) -> str:
        if self._lifecycle.interrupted():
            self.close()
            raise self._lifecycle.error()
        if self._overflowed:
            self.close()
            raise TextGenerationError("text_output_too_large", billing_unknown=True)
        if self._done:
            self.close()
            raise StopIteration
        while True:
            if self._lifecycle.interrupted():
                self.close()
                raise self._lifecycle.error()
            try:
                line = self._next_line()
            except StopIteration:
                self.close()
                raise TextGenerationError("stream_incomplete", outcome_unknown=True) from None
            except TextGenerationError:
                raise
            except (httpx.HTTPError, OSError, RuntimeError) as error:
                self.close()
                if self._lifecycle.interrupted():
                    raise self._lifecycle.error() from error
                raise TextGenerationError("stream_read_failed", outcome_unknown=True) from error
            if not isinstance(line, str) or not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                if self._finish_reason != "stop":
                    self.close()
                    raise TextGenerationError("stream_not_completed", outcome_unknown=self._finish_reason is None)
                self._done = True
                self.close()
                raise StopIteration
            try:
                event = json.loads(data)
                choice = event["choices"][0]
                finish_reason = choice.get("finish_reason")
                delta = choice.get("delta", {}).get("content")
            except (IndexError, KeyError, TypeError, json.JSONDecodeError):
                self.close()
                raise TextGenerationError("invalid_stream", outcome_unknown=True) from None
            if finish_reason is not None:
                if not isinstance(finish_reason, str):
                    self.close()
                    raise TextGenerationError("invalid_stream", outcome_unknown=True)
                self._finish_reason = finish_reason
            if not isinstance(delta, str) or not delta:
                continue
            remaining = _MAX_OUTPUT_CHARS - self._characters
            if remaining <= 0:
                self.close()
                raise TextGenerationError("text_output_too_large", billing_unknown=True)
            self._characters += len(delta)
            if len(delta) > remaining:
                self._characters = _MAX_OUTPUT_CHARS
                self._overflowed = True
                return delta[:remaining]
            return delta

    def close(self) -> None:
        self._lifecycle.close()

    def _next_line(self) -> str:
        while True:
            newline = self._line_buffer.find("\n")
            if newline >= 0:
                line = self._line_buffer[:newline]
                self._line_buffer = self._line_buffer[newline + 1:]
                return line[:-1] if line.endswith("\r") else line
            if self._input_exhausted:
                if not self._line_buffer:
                    raise StopIteration
                line = self._line_buffer
                self._line_buffer = ""
                return line[:-1] if line.endswith("\r") else line
            try:
                chunk = next(self._chunks)
            except StopIteration:
                self._input_exhausted = True
                try:
                    self._line_buffer += self._decoder.decode(b"", final=True)
                except UnicodeDecodeError:
                    self.close()
                    raise TextGenerationError("invalid_stream", outcome_unknown=True) from None
                continue
            if not isinstance(chunk, bytes):
                self.close()
                raise TextGenerationError("invalid_stream", outcome_unknown=True)
            self._bytes += len(chunk)
            if self._bytes > _MAX_RESPONSE_BYTES:
                self.close()
                raise TextGenerationError("response_too_large", outcome_unknown=True)
            try:
                self._line_buffer += self._decoder.decode(chunk)
            except UnicodeDecodeError:
                self.close()
                raise TextGenerationError("invalid_stream", outcome_unknown=True) from None


class TextGenerationService:
    def __init__(self, configurations: TextGenerationConfigurations, data_dir: Path,
                 *, client_factory: Optional[Callable[[], httpx.Client]] = None) -> None:
        self._configurations = configurations
        self._assets = ProjectAssets(Path(data_dir))
        self._client_factory = client_factory or self._new_client

    @staticmethod
    def _new_client() -> httpx.Client:
        return new_provider_http_client(timeout=30)

    def catalog(self) -> list[dict[str, Any]]:
        return self._configurations.catalog()

    def validate(self, snapshot: dict[str, Any]) -> None:
        references = self._references(snapshot)
        images = sum(item.get("dataType") == "image" for item in references)
        self._configurations.configured_snapshot(snapshot, require_images=images)

    def generate(self, snapshot: dict[str, Any], cancel_event: Optional[Event] = None) -> Iterator[str]:
        references = self._references(snapshot)
        images = sum(item.get("dataType") == "image" for item in references)
        configured, credential = self._configurations.configured_snapshot(snapshot, require_images=images)
        prompt = snapshot.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise TextGenerationError("invalid_request")
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt.strip()}]
        for index, item in enumerate(references, start=1):
            if item["dataType"] == "text":
                text = item.get("text")
                if not isinstance(text, str):
                    raise TextGenerationError("invalid_reference")
                content.append({"type": "text", "text": "{{{{ref {}}}}}\n{}".format(index, text)})
            else:
                content.append({"type": "text", "text": "{{{{ref {}}}}}".format(index)})
                content.append({"type": "image_url", "image_url": {"url": self._image_data_url(snapshot, item)}})
        return self._request_stream(configured, credential, content, cancel_event)

    def test_connection(self, revision: str, model_id: str, *, mode: str) -> dict[str, Any]:
        if mode not in {"text", "image"}:
            raise TextGenerationError("invalid_request")
        snapshot = {"providerId": "default", "modelId": model_id, "configurationVersion": revision,
                    "projectId": "connection-test", "canvasId": "connection-test", "runId": "connection-test", "versionId": "connection-test",
                    "prompt": "Reply with OK.", "references": []}
        probe_colors: tuple[str, str] = ()
        if mode == "image":
            probe_colors = self._probe_colors()
            snapshot["prompt"] = "Inspect the two reference images. Reply only with their colors in order, separated by a comma."
            snapshot["connectionImages"] = [self._probe_image(color) for color in probe_colors]
        configured, credential = self._configurations.configured_snapshot(snapshot, require_text_verification=False)
        content: list[dict[str, Any]] = [{"type": "text", "text": snapshot["prompt"]}]
        for image in snapshot.get("connectionImages", []):
            content.append({"type": "image_url", "image_url": {"url": image}})
        stream = self._request_stream(configured, credential, content, None)
        text = "".join(stream)
        if not text.strip() or mode == "image" and not self._probe_matches(text, probe_colors):
            raise TextGenerationError("image_probe_mismatch")
        return self._configurations.record_verification(revision, model_id, mode=mode,
                                                        max_image_references=_MAX_IMAGE_REFERENCES)

    def _request_stream(self, configured: dict[str, Any], credential: str, content: list[dict[str, Any]], cancel_event: Optional[Event]) -> Iterator[str]:
        payload = {"model": configured["modelId"], "stream": True, "messages": [{"role": "user", "content": content}]}
        client = self._client_factory()
        lifecycle = _RequestLifecycle(client, cancel_event)
        if lifecycle.interrupted():
            lifecycle.close()
            raise lifecycle.error()
        try:
            response = client.send(client.build_request("POST", configured["baseUrl"] + "/chat/completions", headers={"Authorization": "Bearer " + credential, "Content-Type": "application/json"}, content=json.dumps(payload)), stream=True)
        except (httpx.HTTPError, OSError, RuntimeError) as error:
            lifecycle.close()
            if lifecycle.interrupted():
                raise lifecycle.error() from error
            raise TextGenerationError("network_failed", outcome_unknown=True) from error
        lifecycle.attach_response(response)
        if lifecycle.interrupted():
            lifecycle.close()
            raise lifecycle.error()
        if response.status_code >= 400:
            lifecycle.close()
            raise TextGenerationError("request_failed", outcome_unknown=response.status_code >= 500)
        if "text/event-stream" not in response.headers.get("content-type", ""):
            lifecycle.close()
            raise TextGenerationError("invalid_stream", outcome_unknown=True)
        return _TextSSEStream(response, lifecycle)

    def _references(self, snapshot: dict[str, Any]) -> list[dict[str, Any]]:
        if not isinstance(snapshot, dict) or not all(isinstance(snapshot.get(key), str) and snapshot[key] for key in ("projectId", "canvasId", "runId", "versionId")):
            raise TextGenerationError("invalid_request")
        references = snapshot.get("references", [])
        if not isinstance(references, list) or any(not isinstance(item, dict) for item in references):
            raise TextGenerationError("invalid_reference")
        for item in references:
            if item.get("dataType") not in {"text", "image"}:
                raise TextGenerationError("unsupported_reference")
        return references

    def _image_data_url(self, snapshot: dict[str, Any], reference: dict[str, Any]) -> str:
        asset_id = reference.get("assetId")
        if not isinstance(asset_id, str) or not asset_id:
            raise TextGenerationError("invalid_reference")
        try:
            asset = self._assets.index(snapshot["projectId"]).get(asset_id)
            if not isinstance(asset, dict) or asset.get("kind") != "image" or not self._assets.available(snapshot["projectId"], asset):
                raise TextGenerationError("invalid_reference")
            path = self._assets.file(snapshot["projectId"], asset)
            data = path.read_bytes()
        except (AssetFileUnavailableError, OSError, ValueError):
            raise TextGenerationError("invalid_reference") from None
        if not data or len(data) > _MAX_IMAGE_BYTES:
            raise TextGenerationError("invalid_reference")
        try:
            with Image.open(Path(path)) as image:
                image.verify()
            with Image.open(Path(path)) as image:
                mime = Image.MIME.get(image.format)
        except (OSError, UnidentifiedImageError):
            raise TextGenerationError("invalid_reference") from None
        if not isinstance(mime, str) or not mime.startswith("image/"):
            raise TextGenerationError("invalid_reference")
        return "data:{};base64,{}".format(mime, base64.b64encode(data).decode("ascii"))

    @staticmethod
    def _probe_image(color: str) -> str:
        output = tempfile.SpooledTemporaryFile(max_size=1024 * 1024)
        try:
            Image.new("RGB", (2, 2), color).save(output, format="PNG")
            output.seek(0)
            return "data:image/png;base64," + base64.b64encode(output.read()).decode("ascii")
        finally:
            output.close()

    @staticmethod
    def _probe_colors() -> tuple[str, str]:
        return tuple(random.SystemRandom().sample(("red", "blue", "green", "yellow"), 2))

    @staticmethod
    def _probe_matches(text: str, colors: tuple[str, str]) -> bool:
        return tuple(part.strip().lower() for part in text.strip().split(",")) == colors
