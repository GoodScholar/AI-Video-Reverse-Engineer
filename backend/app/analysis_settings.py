import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from threading import RLock
from typing import Literal, Optional
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .credential_store import ANALYSIS_PROVIDER_IDS
from .provider_models import CATALOG_VERSION, ProviderId, model_is_allowed


# These are the stable, user-safe codes emitted by ProviderFailure.  Keep the
# persisted setting vocabulary deliberately narrower than arbitrary provider text.
_VERIFICATION_ERROR_CODES = frozenset({
    "provider_unconfigured",
    "authentication_failed",
    "rate_limited",
    "network_error",
    "timeout",
    "unsupported_model_capability",
    "provider_error",
    "invalid_analysis_response",
})
_PREVIOUS_CATALOG_VERSION = "2026-09-14.1"


class AnalysisSettingsConflictError(Exception):
    """A prepared provider configuration was superseded before it could commit."""


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def validate_loopback_base_url(value: str) -> str:
    if not isinstance(value, str):
        raise ValueError("本地分析服务地址必须使用回环主机。")
    if any(ord(character) < 0x20 or ord(character) == 0x7F for character in value) or "?" in value or "#" in value:
        raise ValueError("本地分析服务地址必须使用回环主机。")
    try:
        parsed = urlsplit(value)
        port = parsed.port
        normalized = value.rstrip("/")
        httpx.URL(normalized)
    except (ValueError, httpx.InvalidURL):
        raise ValueError("本地分析服务地址必须使用回环主机。") from None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in {"localhost", "127.0.0.1", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or port is None and parsed.netloc.endswith(":")
    ):
        raise ValueError("本地分析服务地址必须使用回环主机。")
    return normalized


class _StoredProviderConfiguration(_StrictModel):
    provider: ProviderId
    model: str = Field(min_length=1)
    baseUrl: Optional[str] = None
    configurationRevision: str = Field(min_length=1)
    catalogVersion: str = Field(min_length=1)
    verificationState: Literal["unverified", "available", "failed"] = "unverified"
    verifiedAt: Optional[str] = None
    failedAt: Optional[str] = None
    errorCode: Optional[str] = None

    @model_validator(mode="after")
    def verification_fields_are_consistent(self):
        if self.verificationState == "unverified":
            if any((self.verifiedAt, self.failedAt, self.errorCode)):
                raise ValueError("未验证配置不能包含验证结果。")
        elif self.verificationState == "available":
            if not self.verifiedAt or self.failedAt is not None or self.errorCode is not None:
                raise ValueError("可用配置的验证结果无效。")
        elif not self.failedAt or self.verifiedAt is not None or self.errorCode not in _VERIFICATION_ERROR_CODES:
            raise ValueError("失败配置的验证结果无效。")
        return self

    @field_validator("model")
    @classmethod
    def model_must_not_be_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("分析模型不能为空白。")
        return value

    @field_validator("baseUrl")
    @classmethod
    def local_base_url_must_be_safe(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return validate_loopback_base_url(value)

    @field_validator("baseUrl")
    @classmethod
    def only_local_provider_may_define_base_url(cls, value: Optional[str], info) -> Optional[str]:
        if value is not None and info.data.get("provider") != "local_openai_compatible":
            raise ValueError("只有本地分析服务可以配置地址。")
        return value


class _StoredAnalysisSettings(_StrictModel):
    providers: list[_StoredProviderConfiguration] = Field(default_factory=list)
    selectedProvider: Optional[ProviderId] = None

    @field_validator("providers")
    @classmethod
    def providers_must_not_repeat(cls, value: list[_StoredProviderConfiguration]):
        provider_ids = [item.provider for item in value]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("分析供应商设置不能重复。")
        return value

    @field_validator("selectedProvider")
    @classmethod
    def selected_provider_must_be_saved(cls, value, info):
        if value is not None and value not in {item.provider for item in info.data.get("providers", [])}:
            raise ValueError("当前分析供应商必须已有配置。")
        return value


@dataclass(frozen=True)
class _PreparedAnalysisSettings:
    settings: _StoredAnalysisSettings
    target_provider: ProviderId
    target_before_prepare: Optional[_StoredProviderConfiguration]
    selected_provider_before_prepare: Optional[ProviderId]


class AnalysisProviderConfiguration(_StrictModel):
    provider: ProviderId
    label: str
    models: tuple[dict[str, str], ...]
    model: Optional[str] = None
    baseUrl: Optional[str] = None
    credentialState: Literal["configured", "unconfigured"]
    selectedProvider: Optional[ProviderId] = None
    configurationRevision: Optional[str] = None
    catalogVersion: str = CATALOG_VERSION
    verificationState: Literal["unverified", "available", "failed"] = "unverified"
    verifiedAt: Optional[str] = None
    failedAt: Optional[str] = None
    errorCode: Optional[str] = None


class AnalysisSettings:
    """Atomically persists only non-sensitive analysis-provider selections."""

    def __init__(self, path: Path):
        self._path = path
        self._lock = RLock()

    def get(self, provider: str) -> Optional[_StoredProviderConfiguration]:
        with self._lock:
            self._validate_provider(provider)
            return next((item for item in self._read().providers if item.provider == provider), None)

    def selected_provider(self) -> Optional[str]:
        with self._lock:
            return self._read().selectedProvider

    def providers(self) -> list[_StoredProviderConfiguration]:
        with self._lock:
            return list(self._read().providers)

    def save(
        self,
        *,
        provider: str,
        model: str,
        base_url: Optional[str],
        selected_provider: Optional[str],
    ) -> _StoredProviderConfiguration:
        with self._lock:
            candidate, prepared_settings = self.prepare_save(
                provider=provider,
                model=model,
                base_url=base_url,
                selected_provider=selected_provider,
            )
            committed = self.commit(prepared_settings)
            return next(item for item in committed.providers if item.provider == candidate.provider)

    def prepare_save(
        self,
        *,
        provider: str,
        model: str,
        base_url: Optional[str],
        selected_provider: Optional[str],
        credential_changed: bool = False,
    ) -> tuple[_StoredProviderConfiguration, _PreparedAnalysisSettings]:
        with self._lock:
            self._validate_provider(provider)
            self._validate_provider(selected_provider)
            if provider == "local_openai_compatible" and base_url is None:
                raise ValueError("本地分析服务地址必须使用回环主机。")
            current = self._read()
            by_provider = {item.provider: item for item in current.providers}
            previous = by_provider.get(provider)
            unchanged = (
                previous is not None
                and previous.model == model
                and previous.baseUrl == base_url
                and previous.catalogVersion == CATALOG_VERSION
                and not credential_changed
            )
            candidate = _StoredProviderConfiguration(
                provider=provider,
                model=model,
                baseUrl=base_url,
                configurationRevision=previous.configurationRevision if unchanged else str(uuid4()),
                catalogVersion=CATALOG_VERSION,
                verificationState=previous.verificationState if unchanged else "unverified",
                verifiedAt=previous.verifiedAt if unchanged else None,
                failedAt=previous.failedAt if unchanged else None,
                errorCode=previous.errorCode if unchanged else None,
            )
            by_provider[provider] = candidate
            return candidate, _PreparedAnalysisSettings(
                settings=_StoredAnalysisSettings(
                    providers=[by_provider[item] for item in sorted(by_provider)],
                    selectedProvider=selected_provider,
                ),
                target_provider=provider,
                target_before_prepare=previous,
                selected_provider_before_prepare=current.selectedProvider,
            )

    def commit(self, prepared: _PreparedAnalysisSettings) -> _StoredAnalysisSettings:
        with self._lock:
            current = self._read()
            current_by_provider = {item.provider: item for item in current.providers}
            candidate_by_provider = {item.provider: item for item in prepared.settings.providers}
            candidate = candidate_by_provider[prepared.target_provider]
            current_target = current_by_provider.get(prepared.target_provider)
            if self._same_configuration_identity(current_target, prepared.target_before_prepare):
                if self._same_configuration_identity(current_target, candidate):
                    committed_target = _StoredProviderConfiguration.model_validate({
                        **candidate.model_dump(),
                        "verificationState": current_target.verificationState,
                        "verifiedAt": current_target.verifiedAt,
                        "failedAt": current_target.failedAt,
                        "errorCode": current_target.errorCode,
                    })
                else:
                    committed_target = candidate
            else:
                raise AnalysisSettingsConflictError()
            merged_by_provider = {
                **current_by_provider,
                prepared.target_provider: committed_target,
            }
            committed = _StoredAnalysisSettings.model_validate({
                "providers": [merged_by_provider[item] for item in sorted(merged_by_provider)],
                "selectedProvider": (
                    prepared.settings.selectedProvider
                    if current.selectedProvider == prepared.selected_provider_before_prepare
                    else current.selectedProvider
                ),
            })
            self._write(committed)
            return committed

    @staticmethod
    def _same_configuration_identity(
        left: Optional[_StoredProviderConfiguration],
        right: Optional[_StoredProviderConfiguration],
    ) -> bool:
        if left is None or right is None:
            return left is right
        return (
            left.model == right.model
            and left.baseUrl == right.baseUrl
            and left.configurationRevision == right.configurationRevision
            and left.catalogVersion == right.catalogVersion
        )

    def record_verification(
        self,
        *,
        provider: str,
        configuration_revision: str,
        catalog_version: str,
        state: Literal["available", "failed"],
        now: str,
        error_code: Optional[str] = None,
    ) -> bool:
        with self._lock:
            self._validate_provider(provider)
            current = self._read()
            by_provider = {item.provider: item for item in current.providers}
            setting = by_provider.get(provider)
            if (
                setting is None
                or setting.configurationRevision != configuration_revision
                or setting.catalogVersion != catalog_version
            ):
                return False
            by_provider[provider] = _StoredProviderConfiguration.model_validate({
                **setting.model_dump(),
                "verificationState": state,
                "verifiedAt": now if state == "available" else None,
                "failedAt": now if state == "failed" else None,
                "errorCode": None if state == "available" else error_code,
            })
            updated_settings = _StoredAnalysisSettings.model_validate({
                "providers": [by_provider[item] for item in sorted(by_provider)],
                "selectedProvider": current.selectedProvider,
            })
            self._write(updated_settings)
            return True

    @staticmethod
    def _validate_provider(provider: Optional[str]) -> None:
        if provider not in ANALYSIS_PROVIDER_IDS:
            raise ValueError("不支持的分析供应商。")

    def _read(self) -> _StoredAnalysisSettings:
        with self._lock:
            if not self._path.exists():
                return _StoredAnalysisSettings()
            try:
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("分析供应商设置无效。")
                raw_providers = raw.get("providers")
                current_fields = frozenset({
                    "configurationRevision", "catalogVersion", "verificationState",
                    "verifiedAt", "failedAt", "errorCode",
                })
                retired_models = {
                    item.get("provider")
                    for item in raw_providers
                    if isinstance(item, dict)
                    and isinstance(item.get("provider"), str)
                    and isinstance(item.get("model"), str)
                    and not model_is_allowed(item["provider"], item["model"])
                } if isinstance(raw_providers, list) else set()
                needs_migration = not isinstance(raw_providers, list) or any(
                    not isinstance(item, dict)
                    or not current_fields <= item.keys()
                    or item.get("catalogVersion") != CATALOG_VERSION
                    for item in raw_providers
                ) or bool(retired_models)
                # Older files never had verification metadata.  Normalize them
                # before strict validation, then persist that single migration.
                # A model retired from the directory cannot remain selectable or
                # retain a successful verification under a new catalog version.
                if needs_migration and isinstance(raw_providers, list):
                    providers = []
                    for item in raw_providers:
                        if not isinstance(item, dict):
                            providers.append(item)
                        elif item.get("provider") in retired_models:
                            continue
                        elif current_fields <= item.keys() and item.get("catalogVersion") == _PREVIOUS_CATALOG_VERSION:
                            providers.append({**item, "catalogVersion": CATALOG_VERSION})
                        else:
                            providers.append({
                                **item,
                                "configurationRevision": str(uuid4()),
                                "catalogVersion": CATALOG_VERSION,
                                "verificationState": "unverified",
                                "verifiedAt": None,
                                "failedAt": None,
                                "errorCode": None,
                            })
                    raw = {
                        **raw,
                        "providers": providers,
                        "selectedProvider": (
                            raw.get("selectedProvider")
                            if raw.get("selectedProvider") not in retired_models
                            else None
                        ),
                    }
                settings = _StoredAnalysisSettings.model_validate(raw)
                if needs_migration:
                    self._write(settings)
                return settings
            except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, ValueError) as error:
                raise ValueError("分析供应商设置无效。") from error

    def _write(self, settings: _StoredAnalysisSettings) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            descriptor, temporary_path = tempfile.mkstemp(
                dir=self._path.parent,
                prefix=".analysis-providers-",
                suffix=".tmp",
                text=True,
            )
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as file:
                    json.dump(
                        settings.model_dump(),
                        file,
                        ensure_ascii=False,
                        indent=2,
                    )
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(temporary_path, self._path)
            except OSError:
                Path(temporary_path).unlink(missing_ok=True)
                raise
