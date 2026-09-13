import json
import os
import tempfile
from pathlib import Path
from typing import Literal, Optional
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from .credential_store import ANALYSIS_PROVIDER_IDS
from .provider_models import CATALOG_VERSION, ProviderId
from uuid import uuid4


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
    configurationRevision: str = Field(default_factory=lambda: str(uuid4()), min_length=1)
    catalogVersion: str = Field(default=CATALOG_VERSION, min_length=1)
    verificationState: Literal["unverified", "available", "failed"] = "unverified"
    verifiedAt: Optional[str] = None
    failedAt: Optional[str] = None
    errorCode: Optional[str] = None

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

    def get(self, provider: str) -> Optional[_StoredProviderConfiguration]:
        self._validate_provider(provider)
        return next((item for item in self._read().providers if item.provider == provider), None)

    def selected_provider(self) -> Optional[str]:
        return self._read().selectedProvider

    def providers(self) -> list[_StoredProviderConfiguration]:
        return list(self._read().providers)

    def save(
        self,
        *,
        provider: str,
        model: str,
        base_url: Optional[str],
        selected_provider: Optional[str],
    ) -> _StoredProviderConfiguration:
        candidate, prepared_settings = self.prepare_save(
            provider=provider,
            model=model,
            base_url=base_url,
            selected_provider=selected_provider,
        )
        self.commit(prepared_settings)
        return candidate

    def prepare_save(
        self,
        *,
        provider: str,
        model: str,
        base_url: Optional[str],
        selected_provider: Optional[str],
        credential_changed: bool = False,
    ) -> tuple[_StoredProviderConfiguration, _StoredAnalysisSettings]:
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
        return candidate, _StoredAnalysisSettings(
            providers=[by_provider[item] for item in sorted(by_provider)],
            selectedProvider=selected_provider,
        )

    def commit(self, settings: _StoredAnalysisSettings) -> None:
        self._write(settings)

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
        by_provider[provider] = setting.model_copy(update={
            "verificationState": state,
            "verifiedAt": now if state == "available" else None,
            "failedAt": now if state == "failed" else None,
            "errorCode": None if state == "available" else error_code,
        })
        self._write(_StoredAnalysisSettings(
            providers=[by_provider[item] for item in sorted(by_provider)],
            selectedProvider=current.selectedProvider,
        ))
        return True

    @staticmethod
    def _validate_provider(provider: Optional[str]) -> None:
        if provider not in ANALYSIS_PROVIDER_IDS:
            raise ValueError("不支持的分析供应商。")

    def _read(self) -> _StoredAnalysisSettings:
        if not self._path.exists():
            return _StoredAnalysisSettings()
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            settings = _StoredAnalysisSettings.model_validate(raw)
            migrated = [
                item.model_copy(update={
                    "configurationRevision": str(uuid4()),
                    "catalogVersion": CATALOG_VERSION,
                    "verificationState": "unverified",
                    "verifiedAt": None,
                    "failedAt": None,
                    "errorCode": None,
                }) if item.catalogVersion != CATALOG_VERSION else item
                for item in settings.providers
            ]
            return settings if migrated == settings.providers else settings.model_copy(update={"providers": migrated})
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, ValueError) as error:
            raise ValueError("分析供应商设置无效。") from error

    def _write(self, settings: _StoredAnalysisSettings) -> None:
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
                    settings.model_dump(exclude_none=True),
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
