# Ticket 04 Implementation Plan

Status: superseded

Superseded by: [`04-run-recoverable-multi-provider-semantic-analysis-design.md`](./04-run-recoverable-multi-provider-semantic-analysis-design.md)

> 本计划基于已废止的百炼单供应商设计，不得执行。新的多供应商实施计划需在新设计审阅通过后重新编写。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户安全配置自己的百炼密钥，在明确披露后手动发送固定分析代理，并获得可恢复、可重试、持久化的结构化语义分析与最终适用性报告。

**Architecture:** FastAPI 使用系统 keyring 管理全局密钥，通过固定北京地域客户端完成一次图像加 JSON 请求，并在本地严格校验后以检查点和项目状态两阶段提交。独立单工作线程持久化排队/运行/失败状态；React 在当前项目页提供配置、披露、手动启动、轮询和基础只读报告，继续以 `Project` 作为前后端共享真值。

**Tech Stack:** Python 3.9.6 / `>=3.9`、FastAPI 0.115.12、Pydantic 2.13.5、HTTPX 0.28.1、keyring 25.7.0、pytest 8.3.5、React 18.3.1、TypeScript 5.7.3、Vitest 3.0.8、Testing Library、Vite 6.1.0。

**Spec:** [`.scratch/ai-video-reverse-engineer/04-run-recoverable-semantic-analysis-design.md`](./04-run-recoverable-semantic-analysis-design.md)

**Requirements:** [Ticket 04](./issues/04-run-recoverable-semantic-analysis.md)；[产品总规格](./spec.md)；[ADR 0004](../../docs/adr/0004-bailian-analysis-with-user-owned-key.md)

## Global Constraints

- 接收方固定为阿里云百炼北京地域，Endpoint 固定为 `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`，模型固定为 `qwen3.7-flash`；不接受用户输入或环境变量覆盖。
- 模型请求固定 `response_format.type=json_object`、`enable_thinking=false`、`stream=false`、`max_tokens=8192`；每个手动 attempt 最多一个请求，后台自动重试次数为 0。
- 120 秒是整个 HTTP 操作的总 deadline；必须在 HTTPX inactivity timeout 外再使用外层总时限。
- 分析仅发送当前预处理目录内的 `contact-sheet.jpg` 和 `analysis-proxy.json`，不发送完整视频、独立关键帧、项目名、原文件名、本地路径或供应商之外的数据。
- 联系表上限 `4,000,000` 字节且不超过 `1536×1536`；代理 JSON 上限 `131,072` 字节；模型响应上限 `262,144` 字节；达到上限立即中止。
- 密钥只允许写入可识别的操作系统 keyring backend；拒绝 fail、null、`keyrings.alt` 和未知/明文 backend，不写项目、文件、浏览器存储、日志或响应。
- 密钥验证是一个固定、不含项目数据、可能计费的真实模型请求；验证成功后才覆盖安全存储，失败保留旧密钥。自动化测试只能使用 mock transport 和 fake secret store。
- 敏感 mutation 必须为 JSON，携带 `X-AIVRE-Intent: semantic-analysis`，Origin 必须在设计规定的四个本地地址内；在解析密钥正文和外发之前完成校验，不开放 `*` CORS。
- 供应商响应先由 `extra="forbid"` 的 Pydantic 模型校验，再校验数量、Unicode 长度、时间范围和关键帧 index/time 引用；原始请求、base64 图片和原始响应都不落盘。
- `observation` 在界面称为“模型观察”；每条观察必须有有效关键帧证据。证据引用不等于人工确认，不生成总体置信度或综合相似度。
- 最终适用性只有 `in_scope | out_of_scope | needs_review`。Ticket 03 的多镜头或高运动失败必须原样保留，模型不能覆盖；无明确语义结论时使用 `needs_review`。
- 已验证结果先写 `validated-result.json` 再 finalize 到项目。finalize 失败后的手动重试优先本地恢复检查点，不增加 attempt、不调用模型。
- 遗留 `queued/running` 在服务启动时转为 `failed/semantic_analysis_interrupted`；重启绝不自动读取密钥或发起网络请求。
- 当前页只提供基础只读报告。Ticket 05 负责视频时间联动，Ticket 06 负责人工编辑与覆盖，Ticket 07 负责按需策略和 Prompt。
- 移动端只读；桌面操作边界为宽度至少 1024px。状态必须有文字和图标、可见焦点、键盘可达、`aria-live`/`role=alert`，且不只依赖颜色。
- 保持 `requires-python = ">=3.9"`，不得升级本机 Python；新增代码不能使用 Python 3.10 才支持的 `X | None` 类型语法。
- Ticket 01～03 的上传、预处理、存储、隐私、响应式、对比度和构建测试必须持续通过。
- 当前目录没有 `.git`；不得初始化 Git、提交或推送。各任务以定向测试和完整回归检查点代替 commit。

## File Structure

### Create

- `backend/app/semantic_analysis.py`：供应商语义载荷、稳定报告模型、严格校验、条目标准化和本地/语义适用性合并。
- `backend/app/analysis_service_secrets.py`：keyring backend 安全判定、读取与成功后替换密钥。
- `backend/app/bailian_client.py`：固定请求、最小密钥验证、总 deadline、响应大小限制和错误映射。
- `backend/app/semantic_analysis_storage.py`：安全读取代理、输入绑定、attempt 清单、检查点和完成清单原子提交。
- `backend/app/semantic_analysis_jobs.py`：语义任务的独立单工作线程队列。
- `backend/tests/test_semantic_analysis.py`：稳定模型、边界、证据和适用性合并测试。
- `backend/tests/test_analysis_service_secrets.py`：系统 backend、无回显和保留旧密钥测试。
- `backend/tests/test_bailian_client.py`：固定协议、单请求、总 deadline、大小限制和错误映射测试。
- `backend/tests/test_semantic_analysis_storage.py`：路径、限制、绑定、原子检查点和恢复测试。
- `backend/tests/test_semantic_analysis_jobs.py`：单线程、去重和关闭测试。
- `backend/tests/test_semantic_analysis_api.py`：配置、请求防护、任务、重启、恢复、替换与持久化 API 测试。
- `frontend/src/semanticAnalysisApi.ts`：配置查询、验证保存、开始/重试和项目读取请求。
- `frontend/src/semanticAnalysisApi.test.ts`：固定方法、头、正文和错误处理测试。
- `frontend/src/SemanticAnalysisPanel.tsx`：服务配置、外发披露、手动分析、轮询、阶段和只读报告。
- `frontend/src/SemanticAnalysisPanel.test.tsx`：配置、费用披露、状态机、报告、焦点和窄屏测试。

### Modify

- `backend/app/main.py`：扩展 `Project`、注入密钥/客户端/队列、新增接口、任务恢复、能力状态和替换规则。
- `backend/pyproject.toml`、`backend/requirements.lock`：加入 `httpx==0.28.1` 与 `keyring==25.7.0`，保留 Python 3.9 下限。
- `backend/tests/test_projects_api.py`：旧项目 `semanticAnalysis: null` 兼容与敏感字段缺失断言。
- `backend/tests/test_reference_video_upload_api.py`：运行锁、替换失败保留及成功清空语义结果。
- `frontend/src/models.ts`：增加服务配置、语义任务、报告和最终适用性类型。
- `frontend/src/App.tsx`：挂载语义面板、同步项目与能力状态、传递语义替换锁。
- `frontend/src/App.test.tsx`：旧项目恢复、完成报告重开、轮询同步和项目切换回归。
- `frontend/src/ReferenceVideoPanel.tsx`、`frontend/src/ReferenceVideoPanel.test.tsx`：语义活跃期锁定与失效说明。
- `frontend/src/styles.css`：配置、披露、进度、报告、错误、焦点和窄屏只读样式。
- `README.md`：服务地域、密钥存储、外发边界、手动请求和验证说明。
- `.scratch/ai-video-reverse-engineer/issues/04-run-recoverable-semantic-analysis.md`：在真实证据形成后更新验收项和 Comments。

---

### Task 1: 稳定语义领域模型与适用性合并

**Files:**
- Create: `backend/app/semantic_analysis.py`
- Create: `backend/tests/test_semantic_analysis.py`

**Interfaces:**
- Consumes: Ticket 03 `ReferenceVideo`、`AnalysisProxy` JSON、`ReproducibilityAssessment` 及严格校验后的模型 JSON。
- Produces: `ProviderSemanticResult`、`StructuredAnalysis`、`SemanticAnalysis`、`SemanticAnalysisError`、`build_structured_analysis(provider, reference, proxy, local_assessment) -> StructuredAnalysis`、`new_semantic_analysis(...) -> SemanticAnalysis`。

- [ ] **Step 1: 写严格契约和本地失败不可覆盖的失败测试**

在 `backend/tests/test_semantic_analysis.py` 创建工厂，覆盖：未知字段被拒绝、五类总数超过 64 被拒绝、文本和不确定原因长度、时间越界、观察缺证据、index/time 不匹配、`needs_review` 缺原因、`supported` 带原因。核心断言如下：

```python
import pytest
from pydantic import ValidationError

from app.semantic_analysis import ProviderSemanticResult, build_structured_analysis


def test_provider_result_forbids_unknown_fields(valid_provider_payload):
    valid_provider_payload["unexpected"] = "reject-me"
    with pytest.raises(ValidationError):
        ProviderSemanticResult.model_validate(valid_provider_payload)


def test_observation_must_reference_exact_proxy_keyframe(valid_context):
    payload = valid_context.provider.model_copy(deep=True)
    payload.subjects[0].evidence[0].timeSeconds += 0.01
    with pytest.raises(ValueError, match="关键帧证据"):
        build_structured_analysis(
            payload, valid_context.reference, valid_context.proxy,
            valid_context.local_assessment,
        )


def test_local_failed_checks_cannot_be_overridden(valid_context):
    local = valid_context.local_assessment.model_copy(deep=True)
    local.status = "out_of_scope"
    local.checks[0].status = "failed"
    result = build_structured_analysis(
        valid_context.provider, valid_context.reference, valid_context.proxy, local,
    )
    assert result.applicability.status == "out_of_scope"
    assert result.applicability.checks[0].status == "failed"
    assert result.applicability.checks[0].evidence == local.checks[0].evidence
```

再增加具名参数化测试：`test_subject_count_maps_zero_and_many_to_failed`、`test_unknown_subject_or_interaction_maps_to_needs_review`、`test_all_four_pass_maps_to_in_scope`、`test_base_facts_ignore_provider_values`、`test_generated_item_ids_are_stable_in_source_order`。

- [ ] **Step 2: 运行测试并确认预期失败**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_semantic_analysis.py
```

Expected: 因 `app.semantic_analysis` 尚不存在而收集失败。

- [ ] **Step 3: 实现严格供应商模型和稳定产品模型**

在 `backend/app/semantic_analysis.py` 定义固定契约；全部模型使用 `ConfigDict(extra="forbid")`：

```python
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

PROMPT_VERSION = 1
RESULT_SCHEMA_VERSION = 1
MODEL = "qwen3.7-flash"
MAX_TOTAL_ITEMS = 64


class EvidenceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    keyframeIndex: int = Field(ge=0, le=11)
    timeSeconds: float = Field(ge=0)


class ProviderAnalysisItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["observation", "reproduction_suggestion", "unsupported_capability"]
    text: str = Field(min_length=1, max_length=500)
    timeRange: Optional[TimeRange]
    evidence: list[EvidenceReference] = Field(max_length=12)
    reviewStatus: Literal["supported", "needs_review"]
    uncertaintyReason: Optional[str] = Field(default=None, max_length=300)


class ProviderSemanticResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    primarySubjectCount: Optional[int] = Field(ge=0, le=8)
    primarySubjectReviewReason: Optional[str] = Field(default=None, max_length=300)
    complexInteraction: Literal["absent", "present", "needs_review"]
    complexInteractionReviewReason: Optional[str] = Field(default=None, max_length=300)
    subjects: list[ProviderAnalysisItem] = Field(max_length=8)
    scenes: list[ProviderAnalysisItem] = Field(max_length=12)
    actions: list[ProviderAnalysisItem] = Field(max_length=24)
    camera: list[ProviderAnalysisItem] = Field(max_length=16)
    lighting: list[ProviderAnalysisItem] = Field(max_length=16)
```

用 `model_validator` 锁定 uncertainty 成对规则和总条目数。`build_structured_analysis()` 必须：

```python
def build_structured_analysis(provider, reference, proxy, local_assessment):
    validate_proxy_evidence(provider, proxy, reference.durationSeconds)
    checks = merge_applicability(local_assessment, provider)
    status = (
        "out_of_scope" if any(item.status == "failed" for item in checks)
        else "needs_review" if any(item.status == "needs_review" for item in checks)
        else "in_scope"
    )
    return StructuredAnalysis(
        schemaVersion=RESULT_SCHEMA_VERSION,
        baseFacts=base_facts_from_local(reference, proxy),
        applicability=FinalApplicability(status=status, checks=checks),
        **normalize_sections(provider),
    )
```

条目 ID 使用 `f"{category}-{index + 1:02d}"`，保持源顺序稳定。Unicode 长度由 Pydantic `max_length` 验证；时间范围与代理关键帧证据在转换函数中验证。

- [ ] **Step 4: 运行定向测试**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_semantic_analysis.py
```

Expected: 全部通过，并且没有任何 `in_scope` 路径能够覆盖本地 `failed`。

---

### Task 2: 操作系统安全存储与依赖锁定

**Files:**
- Create: `backend/app/analysis_service_secrets.py`
- Create: `backend/tests/test_analysis_service_secrets.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/requirements.lock`

**Interfaces:**
- Consumes: `keyring.get_keyring()`、`get_password()`、`set_password()`。
- Produces: `SecretStore` protocol、`KeyringSecretStore.get() -> Optional[str]`、`KeyringSecretStore.replace(candidate: str) -> None`、`KeyringSecretStore.available() -> bool`、`SecureStorageUnavailable`。

- [ ] **Step 1: 写 backend 拒绝和失败保留测试**

```python
import pytest

from app.analysis_service_secrets import KeyringSecretStore, SecureStorageUnavailable


class FakeSystemBackend:
    priority = 5
    __module__ = "keyring.backends.macOS"

    def __init__(self):
        self.value = "old-key"

    def get_password(self, service, username):
        return self.value

    def set_password(self, service, username, value):
        self.value = value


@pytest.mark.parametrize("module", [
    "keyring.backends.fail", "keyring.backends.null", "keyrings.alt.file",
    "third_party.plaintext",
])
def test_rejects_non_system_backends(module):
    backend = type("Backend", (), {"priority": 1, "__module__": module})()
    with pytest.raises(SecureStorageUnavailable):
        KeyringSecretStore(backend)


def test_replace_uses_fixed_entry_and_never_returns_secret():
    backend = FakeSystemBackend()
    store = KeyringSecretStore(backend)
    assert store.get() == "old-key"
    assert store.replace("new-key") is None
    assert backend.value == "new-key"
```

另测读取/写入异常只抛 `SecureStorageUnavailable`，`repr(error)`、异常 message 和日志捕获中均不出现候选密钥。

- [ ] **Step 2: 运行测试确认失败**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_analysis_service_secrets.py
```

Expected: 模块不存在导致失败。

- [ ] **Step 3: 锁定依赖并实现 allowlist**

在 `backend/pyproject.toml` 和 `backend/requirements.lock` 增加：

```text
httpx==0.28.1
keyring==25.7.0
```

保留 `requires-python = ">=3.9"`。在当前虚拟环境安装锁定文件后，实现：

```python
SERVICE = "ai-video-reverse-engineer.analysis-service"
USERNAME = "bailian-api-key"
SYSTEM_BACKEND_MODULES = (
    "keyring.backends.macOS",
    "keyring.backends.Windows",
    "keyring.backends.SecretService",
    "keyring.backends.libsecret",
)


class KeyringSecretStore:
    def __init__(self, backend=None):
        selected = backend if backend is not None else keyring.get_keyring()
        module = selected.__class__.__module__
        if not module.startswith(SYSTEM_BACKEND_MODULES) or selected.priority <= 0:
            raise SecureStorageUnavailable("系统安全存储不可用")
        self._backend = selected

    def get(self):
        try:
            return self._backend.get_password(SERVICE, USERNAME)
        except Exception:
            raise SecureStorageUnavailable("系统安全存储不可用") from None

    def replace(self, candidate):
        try:
            self._backend.set_password(SERVICE, USERNAME, candidate)
        except Exception:
            raise SecureStorageUnavailable("系统安全存储不可用") from None
```

`replace` 只由 API 在候选密钥验证成功后调用。任何异常日志只写稳定 code。

- [ ] **Step 4: 验证 Python 3.9 和当前系统 backend**

Run:

```sh
.venv/bin/pip install -r backend/requirements.lock
.venv/bin/python -c 'import keyring; print(type(keyring.get_keyring()).__module__)'
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_analysis_service_secrets.py
```

Expected: 安装不改变 Python 版本；测试通过；macOS 实机输出系统 keyring 模块。命令只识别 backend，不读写真实密钥。

---

### Task 3: 固定百炼客户端与稳定错误

**Files:**
- Create: `backend/app/bailian_client.py`
- Create: `backend/tests/test_bailian_client.py`

**Interfaces:**
- Consumes: 候选/已保存 API key、联系表 bytes、代理 JSON bytes、可注入 `httpx.AsyncBaseTransport`。
- Produces: `BailianClient.validate_key(api_key) -> None`、`BailianClient.analyze(api_key, contact_sheet, analysis_proxy) -> ProviderSemanticResult`、`AnalysisServiceFailure(code, message, retryable)`。

- [ ] **Step 1: 写固定请求和单次调用测试**

使用 `httpx.MockTransport` 捕获请求：

```python
def test_analysis_uses_fixed_endpoint_and_options(valid_provider_json, proxy_bytes, jpeg_bytes):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={
            "choices": [{"message": {"content": json.dumps(valid_provider_json)}}]
        })

    client = BailianClient(transport=httpx.MockTransport(handler))
    result = client.analyze("secret", jpeg_bytes, proxy_bytes)

    assert len(seen) == 1
    assert str(seen[0].url) == BAILIAN_CHAT_COMPLETIONS_URL
    body = json.loads(seen[0].content)
    assert body["model"] == "qwen3.7-flash"
    assert body["response_format"] == {"type": "json_object"}
    assert body["enable_thinking"] is False
    assert body["stream"] is False
    assert body["max_tokens"] == 8192
```

另写：

- `test_validate_key_uses_minimal_non_project_prompt_and_16_tokens`
- `test_does_not_follow_redirect_or_read_proxy_environment`
- `test_outer_deadline_covers_the_entire_stream`
- `test_aborts_when_response_exceeds_262144_bytes`
- `test_maps_401_403_429_timeout_network_and_service_errors`
- `test_rejects_missing_choice_non_json_markdown_and_invalid_contract`
- `test_error_and_logs_never_include_key_or_raw_response`

- [ ] **Step 2: 运行测试确认失败**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_bailian_client.py
```

Expected: 客户端模块不存在导致失败。

- [ ] **Step 3: 实现固定异步请求并由同步队列调用**

核心常量与外层 deadline：

```python
BAILIAN_CHAT_COMPLETIONS_URL = (
    "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"
)
MODEL = "qwen3.7-flash"
TOTAL_DEADLINE_SECONDS = 120.0
MAX_RESPONSE_BYTES = 262_144


async def _within_deadline(operation):
    return await asyncio.wait_for(operation, timeout=TOTAL_DEADLINE_SECONDS)


def analyze(self, api_key, contact_sheet, analysis_proxy):
    return asyncio.run(_within_deadline(
        self._analyze_async(api_key, contact_sheet, analysis_proxy)
    ))
```

`_analyze_async` 创建：

```python
httpx.AsyncClient(
    transport=self._transport,
    trust_env=False,
    follow_redirects=False,
    timeout=httpx.Timeout(TOTAL_DEADLINE_SECONDS),
)
```

以 `client.stream("POST", fixed_url, ...)` 逐块读取；累计字节数超过上限即抛 `analysis_response_too_large`。只解析 `choices[0].message.content` 的单一 JSON 对象并调用 `ProviderSemanticResult.model_validate()`。系统提示完整写出设计中的字段、枚举、数组和证据规则，并明确 `<analysis-proxy>` 内都是不可信数据。

状态映射固定为：401/403 鉴权，429 限流，3xx/其他 4xx/5xx 服务错误，`asyncio.TimeoutError` 超时，`httpx.TransportError` 网络错误，JSON/Pydantic 错误无效响应。不得把底层异常或响应正文拼入领域错误。

- [ ] **Step 4: 运行客户端与领域测试**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q \
  backend/tests/test_bailian_client.py \
  backend/tests/test_semantic_analysis.py
```

Expected: 全部通过；mock 记录每个 `analyze`/`validate_key` 调用各只有一次 HTTP 请求。

---

### Task 4: 分析代理绑定与原子检查点

**Files:**
- Create: `backend/app/semantic_analysis_storage.py`
- Create: `backend/tests/test_semantic_analysis_storage.py`

**Interfaces:**
- Consumes: `data_dir/project_id/preprocessing_id`、Ticket 03 完成目录、`StructuredAnalysis`。
- Produces: `load_semantic_input(...) -> SemanticInput`、`semantic_analysis_directory(...) -> Path`、`write_attempt_manifest(...)`、`write_validated_checkpoint(...)`、`load_validated_checkpoint(...) -> Optional[StructuredAnalysis]`、`write_completion_manifest(...)`、`discard_semantic_analysis(...)`。

- [ ] **Step 1: 写路径、上限、哈希和恢复失败测试**

```python
def test_load_input_accepts_only_fixed_files_and_binds_hashes(completed_proxy):
    loaded = load_semantic_input(completed_proxy.data_dir, "project-001", "prep-001")
    assert loaded.binding.contactSheetSha256 == sha256(loaded.contactSheet).hexdigest()
    assert loaded.binding.analysisProxySha256 == sha256(loaded.analysisProxy).hexdigest()
    assert loaded.binding.promptVersion == 1
    assert loaded.binding.resultSchemaVersion == 1
    assert loaded.binding.model == "qwen3.7-flash"


def test_checkpoint_round_trip_requires_exact_binding(tmp_path, analysis, binding):
    write_validated_checkpoint(tmp_path, "project-001", "analysis-001", 1, binding, analysis)
    assert load_validated_checkpoint(
        tmp_path, "project-001", "analysis-001", 1, binding,
    ) == analysis
    changed = binding.model_copy(update={"analysisProxySha256": "0" * 64})
    assert load_validated_checkpoint(
        tmp_path, "project-001", "analysis-001", 1, changed,
    ) is None
```

参数化覆盖：代理或联系表为软链接/目录、JPEG 魔数错误、尺寸超限、JSON 非 UTF-8/非法/Schema 错、字节恰好边界和边界加一、项目/预处理 ID 路径穿越、部分临时文件、检查点未知字段、原子替换失败。递归扫描产物内容，断言不存在测试密钥、`data:image` 和原始模型响应标记。

- [ ] **Step 2: 运行测试确认失败**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_semantic_analysis_storage.py
```

Expected: 存储模块不存在导致失败。

- [ ] **Step 3: 实现安全读取和绑定**

只通过固定目录推导文件：

```python
def load_semantic_input(data_dir, project_id, preprocessing_id):
    validate_storage_id(project_id)
    validate_storage_id(preprocessing_id)
    directory = preprocessing_directory(data_dir, project_id, preprocessing_id)
    contact_path = directory / "contact-sheet.jpg"
    proxy_path = directory / "analysis-proxy.json"
    contact = read_regular_file(contact_path, MAX_CONTACT_SHEET_BYTES)
    proxy_bytes = read_regular_file(proxy_path, MAX_ANALYSIS_PROXY_BYTES)
    parsed = AnalysisProxy.model_validate_json(proxy_bytes)
    validate_jpeg(contact)
    return SemanticInput(
        contactSheet=contact,
        analysisProxy=proxy_bytes,
        parsedProxy=parsed,
        binding=SemanticInputBinding(
            contactSheetSha256=sha256(contact).hexdigest(),
            analysisProxySha256=sha256(proxy_bytes).hexdigest(),
            contactSheetBytes=len(contact),
            analysisProxyBytes=len(proxy_bytes),
            promptVersion=PROMPT_VERSION,
            resultSchemaVersion=RESULT_SCHEMA_VERSION,
            model=MODEL,
        ),
    )
```

`read_regular_file` 使用 `lstat` 拒绝链接/非普通文件，读取 `limit + 1` 字节并在越界时失败。JPEG 尺寸使用安全、轻量的现有或新增解析函数读取头部，不解码整张图片且不引入 Pillow。

- [ ] **Step 4: 实现 attempt 检查点和恢复校验**

复用 Ticket 03 的原子 JSON 写法，目录固定 `attempt-{attempt:04d}`。`validated-result.json` 包装：

```json
{
  "analysisId": "analysis-001",
  "attemptNumber": 1,
  "inputBinding": {},
  "result": {}
}
```

加载时重新用 `SemanticInputBinding` 和 `StructuredAnalysis` 严格验证，并比较全部绑定字段。无效检查点返回 `None` 并删除该文件；不把解析内容写日志。`request-manifest.json` 和最终 `manifest.json` 只保存固定元数据、字节数、code 和时间。

- [ ] **Step 5: 运行存储测试**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q \
  backend/tests/test_semantic_analysis_storage.py \
  backend/tests/test_local_preprocessing_storage.py
```

Expected: 全部通过；Ticket 03 产物读取行为无回归。

---

### Task 5: 配置 API、语义队列、恢复与项目事务

**Files:**
- Create: `backend/app/semantic_analysis_jobs.py`
- Create: `backend/tests/test_semantic_analysis_jobs.py`
- Create: `backend/tests/test_semantic_analysis_api.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/test_projects_api.py`
- Modify: `backend/tests/test_reference_video_upload_api.py`

**Interfaces:**
- Consumes: Tasks 1～4 的 `SecretStore`、`BailianClient`、输入/检查点函数和领域模型。
- Produces: `GET /api/analysis-service`、`PUT /api/analysis-service/key`、`POST /api/projects/{id}/semantic-analysis`，以及扩展后的 `Project.semanticAnalysis` 和 `/api/capabilities`。

- [ ] **Step 1: 写 API 安全边界与配置失败测试**

在 `test_semantic_analysis_api.py` 的 `create_app` 注入 fake secret store、fake client 和 inline/blocking queue。测试所有敏感请求都带：

```python
SENSITIVE_HEADERS = {
    "Content-Type": "application/json",
    "X-AIVRE-Intent": "semantic-analysis",
    "Origin": "http://127.0.0.1:5173",
}
```

具名测试至少包括：

```python
def test_key_mutation_rejects_origin_before_reading_secret_body(client, secret_store):
    response = client.put(
        "/api/analysis-service/key",
        headers={"Content-Type": "application/json", "Origin": "https://evil.example"},
        content=b'{"apiKey":"must-not-appear"}',
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "request_origin_rejected"
    assert secret_store.calls == []
    assert "must-not-appear" not in response.text


def test_failed_candidate_validation_preserves_existing_key(client, secret_store, bailian):
    secret_store.value = "old-key"
    bailian.validation_error = AnalysisServiceFailure(
        "analysis_authentication_failed", "API 密钥无效。", False,
    )
    response = client.put(
        "/api/analysis-service/key", headers=SENSITIVE_HEADERS,
        json={"apiKey": "new-invalid-key"},
    )
    assert response.status_code == 401
    assert secret_store.value == "old-key"
```

覆盖缺失/错误 Content-Type、缺失自定义头、四个允许 Origin、没有 Origin、非法 JSON、key 非字符串/空白/超过 512、密钥不出现在 400/422/日志，以及 secure store 不可用时 GET 状态仍无密钥。

- [ ] **Step 2: 写任务、恢复和替换事务失败测试**

覆盖以下行为：

- 旧项目 JSON 缺少 `semanticAnalysis` 时读取为 `null`。
- 缺预处理、代理无效、未配置、进行中分别返回稳定 409/503 且 client 调用为 0。
- 新启动 `202`，完成绑定幂等 `200`，失败手动重试 attempt 加一。
- 单 attempt 的 fake client `analyze` 调用严格为 1；失败后不会自动再调。
- validated checkpoint 后模拟 `_write_projects` finalize 失败，再手动 POST 直接完成且 client 调用仍为 1、attempt 不增加。
- 应用重建把 queued/running 转为 interrupted，构造 app 本身不会访问 secret store/client。
- 语义 queued/running 时上传中间件在读取正文前返回 409。
- 替换失败保留旧语义报告；替换成功清空 `localPreprocessing` 与 `semanticAnalysis` 并清理旧目录。
- 不同项目按提交顺序运行，同一项目重复提交被拒绝。

- [ ] **Step 3: 运行失败测试**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q \
  backend/tests/test_semantic_analysis_jobs.py \
  backend/tests/test_semantic_analysis_api.py \
  backend/tests/test_projects_api.py \
  backend/tests/test_reference_video_upload_api.py
```

Expected: 新模块、字段和接口缺失导致失败；既有 Ticket 01～03 测试仍可收集。

- [ ] **Step 4: 实现独立单工作线程与 create_app 注入**

`SemanticAnalysisJobQueue` 沿用现有队列的锁、`ThreadPoolExecutor(max_workers=1)`、活跃项目集合、顺序提交和 shutdown 行为，线程名前缀固定 `semantic-analysis`。不改名或重构现有 `LocalPreprocessingJobQueue`。

扩展 `create_app`：

```python
def create_app(
    data_dir: Path,
    *,
    # existing arguments,
    secret_store_factory: Callable = KeyringSecretStore,
    bailian_client_factory: Callable = BailianClient,
    semantic_queue_factory: Callable = SemanticAnalysisJobQueue,
) -> FastAPI:
```

应用启动只构造安全存储状态对象并协调项目，不调用 `get()`。`Project` 增加：

```python
semanticAnalysis: Optional[SemanticAnalysis] = None
```

实现 `ensure_sensitive_local_request(request)`，先检查 `Content-Type`、`X-AIVRE-Intent`、Origin，再由密钥接口使用 `await request.body()` 和 `json.loads()` 专门解析，所有输入错误返回 `400 invalid_api_key_input`。

- [ ] **Step 5: 实现配置和分析任务状态机**

配置成功顺序固定：`candidate = parse -> bailian.validate_key(candidate) -> secret_store.replace(candidate)`。运行任务顺序固定：

```python
input_data = load_semantic_input(...)
checkpoint = load_validated_checkpoint(...)
if checkpoint is None:
    api_key = secret_store.get()
    provider = bailian.analyze(api_key, input_data.contactSheet, input_data.analysisProxy)
    result = build_structured_analysis(provider, reference, input_data.parsedProxy, local_assessment)
    write_validated_checkpoint(..., result)
finalize_project(result)
write_completion_manifest(...)
```

`POST` 排队前计算并持久化完整 input binding。普通失败重试 `attemptNumber += 1`；`analysis_finalize_failed` 有有效检查点时保持原 attempt。worker 所有异常只转换为设计中的稳定 code。任务 `queued/running` 时密钥 PUT 和参考视频 PUT 都返回语义进行中 409。

新增第二个 app shutdown handler 关闭语义队列。`GET /api/capabilities` 读取 `available()/get()` 只用于用户显式页面查询；不可用、未配置、已配置映射为稳定状态，响应不含密钥信息。

- [ ] **Step 6: 运行后端定向与完整测试**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q \
  backend/tests/test_semantic_analysis_jobs.py \
  backend/tests/test_semantic_analysis_api.py \
  backend/tests/test_projects_api.py \
  backend/tests/test_reference_video_upload_api.py
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
```

Expected: 全部通过；测试过程没有真实网络访问或真实 keyring 写入。

---

### Task 6: 前端配置、披露与敏感请求封装

**Files:**
- Create: `frontend/src/semanticAnalysisApi.ts`
- Create: `frontend/src/semanticAnalysisApi.test.ts`
- Create: `frontend/src/SemanticAnalysisPanel.tsx`
- Create: `frontend/src/SemanticAnalysisPanel.test.tsx`
- Modify: `frontend/src/models.ts`

**Interfaces:**
- Consumes: Task 5 API 和扩展后的 `Project`。
- Produces: `getAnalysisServiceConfig()`、`saveAnalysisServiceKey(apiKey)`、`startSemanticAnalysis(projectId)`、`SemanticAnalysisPanel` 的配置/披露/启动状态。

- [ ] **Step 1: 增加共享 TypeScript 类型**

在 `models.ts` 增加与后端 camelCase 完全一致的类型，保留旧项目可空字段：

```ts
export type AnalysisItem = {
  id: string;
  category: "subject" | "scene" | "action" | "camera" | "lighting";
  kind: "observation" | "reproduction_suggestion" | "unsupported_capability";
  text: string;
  timeRange: { startSeconds: number; endSeconds: number } | null;
  evidence: { keyframeIndex: number; timeSeconds: number }[];
  reviewStatus: "supported" | "needs_review";
  uncertaintyReason: string | null;
};

export type SemanticAnalysis = {
  id: string;
  sourcePreprocessingId: string;
  inputBinding: SemanticInputBinding;
  status: "queued" | "running" | "completed" | "failed";
  attemptNumber: number;
  queuedAt: string;
  startedAt: string | null;
  updatedAt: string;
  completedAt: string | null;
  result: StructuredAnalysis | null;
  error: SemanticAnalysisError | null;
};

export type Project = {
  // existing fields
  semanticAnalysis: SemanticAnalysis | null;
};
```

定义 `AnalysisServiceConfig`、`SemanticInputBinding`、`StructuredAnalysis`、`FinalApplicability` 和错误类型，不使用 `any`。

- [ ] **Step 2: 写 API 固定头和无回显测试**

```ts
it("验证密钥只发送固定敏感请求且不返回密钥", async () => {
  vi.mocked(fetch).mockResolvedValue(response({
    provider: "阿里云百炼", region: "北京", model: "qwen3.7-flash",
    configured: true, secureStorageAvailable: true,
  }));
  const result = await saveAnalysisServiceKey("secret-value");
  expect(fetch).toHaveBeenCalledWith("/api/analysis-service/key", {
    method: "PUT",
    headers: {
      "Content-Type": "application/json",
      "X-AIVRE-Intent": "semantic-analysis",
    },
    body: JSON.stringify({ apiKey: "secret-value" }),
  });
  expect(JSON.stringify(result)).not.toContain("secret-value");
});
```

测试分析 POST 发送 `{}` 和相同固定头，配置 GET 无敏感头；网络、稳定 detail 对象和非 JSON 错误分别转换为中文消息。

- [ ] **Step 3: 写配置与外发披露组件失败测试**

测试：固定显示供应商/地域/模型；未配置显示 password input；费用说明包含“一次不含项目数据的验证请求”和“可能计费”；保存成功清空 input 且 DOM 不再含完整 key；失败清空 input 并保留“未配置/此前配置”；预处理未完成时按钮禁用并解释；完整披露恰好列出两个文件和用途，明确完整视频不上传；点击一次只产生一次 POST；queued/running 禁用重复点击。

移动端 `matchMedia("(min-width: 1024px)")` 为 false 时，断言没有密钥输入、验证、开始或重试按钮。

- [ ] **Step 4: 实现 API 和面板配置区**

`semanticAnalysisApi.ts` 复用 `readApiError`，敏感请求都由一个函数生成固定头：

```ts
const semanticMutation = (method: "PUT" | "POST", body: unknown): RequestInit => ({
  method,
  headers: {
    "Content-Type": "application/json",
    "X-AIVRE-Intent": "semantic-analysis",
  },
  body: JSON.stringify(body),
});
```

`SemanticAnalysisPanel` props：

```ts
type Props = {
  project: Project;
  serviceConfig: AnalysisServiceConfig;
  isDesktop: boolean;
  onProjectUpdated(project: Project): void;
  onServiceConfigUpdated(config: AnalysisServiceConfig): void;
};
```

密钥保存在局部 `useState`，请求 settle 后在 `finally` 中 `setApiKey("")`。配置区和披露区使用真实文本节点，不能只放 tooltip。分析按钮必须由用户 click 触发，不放在 effect 中。

- [ ] **Step 5: 运行前端定向测试**

Run:

```sh
npm --prefix frontend test -- semanticAnalysisApi.test.ts SemanticAnalysisPanel.test.tsx
```

Expected: 全部通过；测试确认页面和 API 返回对象不含输入密钥。

---

### Task 7: 分析轮询、阶段和基础只读报告接入项目页

**Files:**
- Modify: `frontend/src/SemanticAnalysisPanel.tsx`
- Modify: `frontend/src/SemanticAnalysisPanel.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/App.test.tsx`
- Modify: `frontend/src/ReferenceVideoPanel.tsx`
- Modify: `frontend/src/ReferenceVideoPanel.test.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: Task 6 面板/API、现有 `getProject()`、`LocalPreprocessingPanel` 的项目同步方式。
- Produces: 完整语义任务状态机、每秒轮询、七阶段展示、报告分组、语义替换锁和窄屏只读体验。

- [ ] **Step 1: 写轮询、恢复和报告失败测试**

在面板测试覆盖：queued/running 每秒轮询；completed/failed/卸载/项目切换停止；旧项目响应不能覆盖新项目；刷新失败保留后端状态并提供“重新读取”；失败后焦点移动到错误标题；成功后焦点移动到“语义分析已完成”。

报告断言：

```tsx
expect(screen.getByText("模型观察")).toBeVisible();
expect(screen.getByText("复刻建议")).toBeVisible();
expect(screen.getByText("当前不支持")).toBeVisible();
expect(screen.getByText("需人工确认：画面遮挡，主体数量不明确")).toBeVisible();
expect(screen.getByText("00:01.250–00:02.500")).toBeVisible();
expect(screen.getByText("证据：关键帧 2（00:01.250）")).toBeVisible();
```

验证最终 `out_of_scope` 同时显示 Ticket 03 本地失败原因；`needs_review` 不渲染成“范围内”。“策略准备”必须显示“等待后续步骤”，且 fetch 中没有 Prompt/策略 endpoint。

在 App/ReferenceVideoPanel 测试语义运行时替换禁用；替换确认文案说明会清除本地预处理和语义分析；替换失败报告仍在，成功后移除。

- [ ] **Step 2: 运行测试确认失败**

Run:

```sh
npm --prefix frontend test -- \
  SemanticAnalysisPanel.test.tsx \
  App.test.tsx \
  ReferenceVideoPanel.test.tsx
```

Expected: 报告、轮询和替换锁行为尚未实现而失败。

- [ ] **Step 3: 实现轮询与七阶段状态**

沿用 `LocalPreprocessingPanel` 的项目 ID/ref 防过期模式。只在：

```ts
const canPoll = task?.status === "queued" || task?.status === "running";
```

时每 `1000ms` GET 项目。后端状态是唯一真值；单次 GET 失败只更新 `refreshError`。

阶段列表按固定顺序渲染 Ticket 03 五阶段，再渲染：

```ts
{ name: "semanticAnalysis", label: "语义分析", status: semanticStatus }
{ name: "strategyPreparation", label: "策略准备", status: "pending", detail: "等待后续步骤" }
```

策略准备没有 mutation。完成、失败后用 `tabIndex={-1}` 标题和 effect 恢复焦点。

- [ ] **Step 4: 实现只读报告和项目页同步**

`SemanticAnalysisReport` 在同一文件内先保持局部组件，按 `subjects/scenes/actions/camera/lighting` 固定顺序渲染。格式函数：

```ts
function formatTimecode(seconds: number): string {
  const milliseconds = Math.round(seconds * 1000);
  const minutes = Math.floor(milliseconds / 60_000);
  const secs = Math.floor((milliseconds % 60_000) / 1000);
  const millis = milliseconds % 1000;
  return `${String(minutes).padStart(2, "0")}:${String(secs).padStart(2, "0")}.${String(millis).padStart(3, "0")}`;
}
```

条目使用文字标签表达 kind 和 reviewStatus。时间范围/证据当前为普通文本，不绑定 click。空分组显示“模型未返回此类条目”，不伪造示例。

App 初始并行读取项目、capabilities 和 analysis service config；面板更新同时合并 selectedProject 与首页 projects。把：

```ts
semanticLocked={
  selectedProject.semanticAnalysis?.status === "queued" ||
  selectedProject.semanticAnalysis?.status === "running"
}
```

传给 `ReferenceVideoPanel`，与现有预处理锁共同决定替换行为。

- [ ] **Step 5: 完成响应式与可访问样式**

在 `styles.css` 增加稳定 class，沿用现有胶片检测台色板。必须提供：focus-visible 轮廓；状态图标加文字；错误 `role="alert"`；轮询 `aria-live="polite"`；报告使用有标题的 section/list；`prefers-reduced-motion` 下不增加动画。

在现有 `@media (max-width: 1023px)` 中隐藏 `.semantic-analysis-actions` 和 `.analysis-key-form`，保留配置状态、任务状态和报告。1024px 必须保留全部操作。

- [ ] **Step 6: 运行前端定向和完整测试**

Run:

```sh
npm --prefix frontend test -- \
  semanticAnalysisApi.test.ts \
  SemanticAnalysisPanel.test.tsx \
  App.test.tsx \
  ReferenceVideoPanel.test.tsx
npm --prefix frontend test
node frontend/scripts/verify-color-contrast.mjs
npm --prefix frontend run build
```

Expected: 全部测试、颜色对比与生产构建通过。

---

### Task 8: 端到端安全回归、文档与受控真实验收准备

**Files:**
- Modify: `README.md`
- Modify: `.scratch/ai-video-reverse-engineer/issues/04-run-recoverable-semantic-analysis.md`
- Create during execution: `.superpowers/sdd/04-run-recoverable-semantic-analysis-implementation-plan/progress.md`
- Create during execution: `.superpowers/sdd/04-run-recoverable-semantic-analysis-implementation-plan/task-8-report.md`
- Create during approved real validation only: `.superpowers/sdd/04-run-recoverable-semantic-analysis-implementation-plan/task-8-evidence/`

**Interfaces:**
- Consumes: 完成的后端/前端功能、fake service、临时数据目录，以及用户另行批准后提供的真实百炼密钥。
- Produces: 可复查的自动化与浏览器证据、密钥/数据边界扫描结果、真实请求计划和 Ticket 04 验收记录。

- [ ] **Step 1: 补 README 的运行和隐私说明**

记录以下确定事实：固定百炼北京地域和模型；系统 keyring 前置检查；密钥验证会发出一次不含项目数据且可能计费的请求；分析只发联系表和代理 JSON；每次点击最多一个请求；失败保留预处理；移动端只读。不要在 README 放密钥示例、环境变量密钥方式或可自定义 base URL。

- [ ] **Step 2: 增加无真实网络的集成脚本测试**

使用临时 `data_dir`、fake secret store 和本地 mock transport 完成：创建项目 → 上传受控视频/植入真实 Ticket 03 完成产物 → 配置 fake key → 手动 POST → queued/running/completed → 重开项目读取报告。再注入鉴权、超时、网络和 invalid response，确认失败后预处理哈希未变且新手动 attempt 只增加一次 client 调用。

检查运行中替换 409、finalize 检查点恢复不新增调用、服务重启不外发。所有 fake key 使用唯一哨兵 `ticket04-secret-must-not-persist`。

- [ ] **Step 3: 扫描持久化与构建产物的数据边界**

Run:

```sh
rg -n --hidden --glob '!frontend/node_modules/**' --glob '!frontend/dist/**' \
  'ticket04-secret-must-not-persist|Authorization: Bearer|data:image/jpeg;base64' \
  <task-8-temporary-data-dir> .superpowers/sdd/04-run-recoverable-semantic-analysis-implementation-plan
find <task-8-temporary-data-dir>/project-files -type f -print | sort
```

Expected: 第一个命令无匹配；第二个清单只包含参考视频、Ticket 03 产物、语义 request manifest、validated result 和 completion manifest，不包含原始响应或请求正文。执行时把实际临时目录绝对路径写入报告，不能保留尖括号形式运行。

- [ ] **Step 4: 用浏览器完成桌面、边界宽度和移动只读验收**

启动 FastAPI 与 Vite，使用隔离浏览器 session 完成：

- 1280px：配置披露、密钥输入清空、外发披露、开始、七阶段、完成报告、刷新/重开。
- 1024px：全部配置、开始和重试仍可操作。
- 1023px 与 390px：配置状态、任务和报告可读，没有密钥、开始或重试控件。
- fake 鉴权/超时/网络/无效响应分别显示不同错误，失败后预处理摘要仍存在。
- 语义运行时参考视频替换被禁用；完成后替换确认和成功清空行为正确。

浏览器验收使用项目规定的浏览器自动化技能，不通过 DOM 注入修改应用状态。保存截图和可访问性文本摘要到 evidence 目录。

- [ ] **Step 5: 运行最终自动化验证**

Run:

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
npm --prefix frontend test
node frontend/scripts/verify-color-contrast.mjs
npm --prefix frontend run build
.venv/bin/python -m compileall -q backend/app backend/tests
```

Expected: 所有命令退出码为 0；后端语义测试通过 mock，无真实百炼调用。

- [ ] **Step 6: 形成真实验收门槛，不在本轮擅自调用**

在 `task-8-report.md` 明确列出真实验收只需要：一次最小 key 验证 + 一次受支持样本分析，总请求数 2，可能由用户账户计费。获得用户对次数和费用的单独批准后才运行，并记录：系统 backend 模块、每次请求耗时/状态、请求输入哈希与字节数、完成报告重开、递归密钥扫描和外发边界。

如果用户尚未批准，报告必须写“真实百炼验收待批准”，不能把 mock 通过表述为真实服务已验证，也不能勾选依赖真实服务的验收证据。

- [ ] **Step 7: 更新 Ticket 04 证据**

自动化和浏览器证据形成后，在 Ticket Comments 记录报告路径和实际结果；只勾选已被测试证明的条目。真实密钥/服务可用性条目在完成两次受控真实请求前保持未勾选。项目任务系统只支持现有状态值时保持 `Status: ready-for-agent`，在 Comments 说明实现/验收进度，不虚构 `completed` 状态。

---

## Plan Self-Review

- Spec coverage：Tasks 1～5 覆盖稳定结构、密钥、固定服务、恢复和错误；Tasks 6～7 覆盖披露、手动调用、报告、阶段和移动只读；Task 8 覆盖持久化边界、完整回归及单独批准的真实验收。
- Scope boundary：计划没有引入 Ticket 05 时间联动、Ticket 06 编辑/人工覆盖或 Ticket 07 策略/Prompt；“策略准备”只有只读等待状态。
- Type consistency：后端 `SemanticAnalysis`、`StructuredAnalysis`、`AnalysisItem`、`SemanticInputBinding` 与前端同名 camelCase 字段一致；最终状态统一为 `in_scope | out_of_scope | needs_review`。
- Recovery consistency：普通手动重试增加 attempt；`analysis_finalize_failed` 的有效检查点恢复不增加 attempt、不发网络请求；服务重启只标记 interrupted。
- Security consistency：真实 key 只进入候选请求内存、Authorization header 和系统 keyring；API 错误、日志、项目、检查点、浏览器存储和报告都没有密钥或原始响应。
- Placeholder scan：所有任务都给出具体文件、接口、失败测试、实现契约和验证命令；执行时生成的临时目录明确要求替换为实际绝对路径。
