import json
import logging
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Callable, Literal, Optional, TypeVar
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .analysis_providers.base import ProviderAnalysisError
from .analysis_service_secrets import SecureStorageUnavailable
from .credential_store import ANALYSIS_PROVIDER_IDS
from .provider_models import CATALOG_VERSION, PROVIDER_IDS, ProviderId, model_is_allowed, provider_for


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


class AnalysisProviderConfigurationChangedError(Exception):
    """A result belongs to a provider configuration that is no longer current."""


class AnalysisProviderUnconfiguredError(Exception):
    """The requested provider snapshot is incomplete or no longer usable."""


class AnalysisProviderProbeError(Exception):
    """A provider probe failed outside the stable provider failure contract."""


class AnalysisProviderVerificationPersistenceError(Exception):
    """A provider verification result could not be persisted or reloaded."""


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


_Result = TypeVar("_Result")


@dataclass(frozen=True)
class _ConfiguredProviderSnapshot:
    setting: _StoredProviderConfiguration
    credential: Optional[str]
    selected_provider: Optional[ProviderId]


class AnalysisProviderConfigurations:
    """Owns provider settings, credentials, runtime binding, and verification CAS."""

    def __init__(
        self,
        *,
        settings: AnalysisSettings,
        credential_store: Callable[[], Any],
        provider_factory: Callable[..., Any],
        provider_closer: Callable[[Any], None],
        clock: Callable[[], datetime],
    ):
        self._settings = settings
        self._credential_store = credential_store
        self._provider_factory = provider_factory
        self._provider_closer = provider_closer
        self._clock = clock
        self._lock = RLock()

    def list(self) -> list[AnalysisProviderConfiguration]:
        with self._lock:
            settings = {item.provider: item for item in self._settings.providers()}
            selected_provider = self._settings.selected_provider()
            credentials = self._credential_store()
            return [
                self._public_configuration(
                    provider=provider,
                    setting=settings.get(provider),
                    credential_configured=credentials.get(provider) is not None,
                    selected_provider=selected_provider,
                )
                for provider in PROVIDER_IDS
            ]

    def save(
        self,
        *,
        provider: str,
        model: str,
        base_url: Optional[str],
        api_key: Optional[str],
    ) -> AnalysisProviderConfiguration:
        with self._lock:
            if not model_is_allowed(provider, model):
                raise ValueError("所选模型不在该分析供应商的可用清单中。")
            candidate, prepared = self._settings.prepare_save(
                provider=provider,
                model=model,
                base_url=base_url,
                selected_provider=provider,
            )
            credentials = self._credential_store()
            previous_credential = credentials.get(provider)
            if provider != "local_openai_compatible" and not (api_key or previous_credential):
                raise ValueError("分析供应商配置无效。")
            if api_key is not None and api_key != previous_credential:
                candidate, prepared = self._settings.prepare_save(
                    provider=provider,
                    model=model,
                    base_url=base_url,
                    selected_provider=provider,
                    credential_changed=True,
                )

            credential_write_succeeded = False
            credential_configured = previous_credential is not None
            try:
                if api_key is not None:
                    credentials.set(provider, api_key)
                    credential_write_succeeded = True
                    credential_configured = True
                committed = self._settings.commit(prepared)
            except (OSError, SecureStorageUnavailable, AnalysisSettingsConflictError):
                if credential_write_succeeded:
                    try:
                        if previous_credential is None:
                            credentials.delete(provider)
                        else:
                            credentials.set(provider, previous_credential)
                    except SecureStorageUnavailable as rollback_error:
                        logging.getLogger(__name__).warning(
                            "无法回退分析供应商凭据：%s", provider,
                        )
                        raise rollback_error
                raise

            setting = next(item for item in committed.providers if item.provider == candidate.provider)
            return self._public_configuration(
                provider=provider,
                setting=setting,
                credential_configured=credential_configured,
                selected_provider=committed.selectedProvider,
            )

    def require_configured(
        self,
        provider: str,
        model: Optional[str] = None,
        *,
        require_verified: bool = False,
    ) -> AnalysisProviderConfiguration:
        with self._lock:
            snapshot = self._configured_snapshot_locked(
                provider, model, require_verified=require_verified,
            )
            return self._public_configuration(
                provider=provider,
                setting=snapshot.setting,
                credential_configured=snapshot.credential is not None,
                selected_provider=snapshot.selected_provider,
            )

    def use_provider(
        self,
        *,
        provider: str,
        model: Optional[str],
        operation: Callable[[Any, str], _Result],
        require_verified: bool = False,
    ) -> _Result:
        with self._lock:
            snapshot = self._configured_snapshot_locked(
                provider, model, require_verified=require_verified,
            )
        configured_provider = self._provider_factory(
            provider=provider,
            credential=snapshot.credential,
            base_url=snapshot.setting.baseUrl,
            model=snapshot.setting.model,
        )
        try:
            return operation(configured_provider, snapshot.setting.model)
        finally:
            self._provider_closer(configured_provider)

    def verify(self, provider: str) -> AnalysisProviderConfiguration:
        with self._lock:
            snapshot = self._configured_snapshot_locked(provider, None)

        configured_provider = None
        probe_error: Optional[Exception] = None
        probe_cause: Optional[Exception] = None
        error_code: Optional[str] = None
        try:
            configured_provider = self._provider_factory(
                provider=provider,
                credential=snapshot.credential,
                base_url=snapshot.setting.baseUrl,
                model=snapshot.setting.model,
            )
            configured_provider.test_connection(snapshot.setting.model)
        except ProviderAnalysisError as error:
            probe_error = error
            error_code = error.failure.code
        except Exception as error:
            probe_error = AnalysisProviderProbeError()
            probe_cause = error
            error_code = "provider_error"
        finally:
            if configured_provider is not None:
                self._provider_closer(configured_provider)

        try:
            with self._lock:
                persisted = self._settings.record_verification(
                    provider=provider,
                    configuration_revision=snapshot.setting.configurationRevision,
                    catalog_version=snapshot.setting.catalogVersion,
                    state="failed" if probe_error is not None else "available",
                    error_code=error_code,
                    now=self._clock().isoformat(),
                )
                if not persisted:
                    raise AnalysisProviderConfigurationChangedError()
                if probe_error is None:
                    current = self._configured_snapshot_locked(provider, snapshot.setting.model)
                    if (
                        current.setting.configurationRevision != snapshot.setting.configurationRevision
                        or current.setting.catalogVersion != snapshot.setting.catalogVersion
                    ):
                        raise AnalysisProviderConfigurationChangedError()
                    result = self._public_configuration(
                        provider=provider,
                        setting=current.setting,
                        credential_configured=current.credential is not None,
                        selected_provider=current.selected_provider,
                    )
        except (OSError, ValueError) as error:
            raise AnalysisProviderVerificationPersistenceError() from error

        if probe_error is not None:
            if probe_cause is not None:
                raise probe_error from probe_cause
            raise probe_error
        return result

    def _configured_snapshot_locked(
        self,
        provider: str,
        model: Optional[str],
        *,
        require_verified: bool = False,
    ) -> _ConfiguredProviderSnapshot:
        setting = self._settings.get(provider)
        credential = self._credential_store().get(provider)
        if (
            setting is None
            or model is not None and setting.model != model
            or provider != "local_openai_compatible" and credential is None
            or not model_is_allowed(provider, setting.model)
            or require_verified and setting.verificationState != "available"
        ):
            raise AnalysisProviderUnconfiguredError()
        return _ConfiguredProviderSnapshot(
            setting=setting,
            credential=credential,
            selected_provider=self._settings.selected_provider(),
        )

    @staticmethod
    def _public_configuration(
        *,
        provider: str,
        setting: Optional[_StoredProviderConfiguration],
        credential_configured: bool,
        selected_provider: Optional[ProviderId],
    ) -> AnalysisProviderConfiguration:
        catalog = provider_for(provider)
        return AnalysisProviderConfiguration(
            provider=provider,
            label=catalog.label,
            models=tuple(model.model_dump() for model in catalog.models),
            model=setting.model if setting is not None else None,
            baseUrl=setting.baseUrl if setting is not None else None,
            credentialState="configured" if credential_configured else "unconfigured",
            selectedProvider=selected_provider,
            configurationRevision=setting.configurationRevision if setting is not None else None,
            catalogVersion=setting.catalogVersion if setting is not None else CATALOG_VERSION,
            verificationState=setting.verificationState if setting is not None else "unverified",
            verifiedAt=setting.verifiedAt if setting is not None else None,
            failedAt=setting.failedAt if setting is not None else None,
            errorCode=setting.errorCode if setting is not None else None,
        )
