# Ticket 04 多供应商语义分析 Implementation Plan

Status: superseded

Superseded by: [`04-image-reference-and-multi-provider-analysis-implementation-index.md`](./04-image-reference-and-multi-provider-analysis-implementation-index.md)

> 不再执行本计划；它不包含参考图片、Grok、本地 OpenAI 兼容服务和新的静态/时序事实边界。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让用户安全配置并明确选择首批五家分析供应商，按需发送固定分析代理，并获得可恢复、可重试、可持久化的统一结构化语义分析。

**Architecture:** FastAPI 使用 `keyring` 管理应用级供应商凭据，以统一 `AnalysisProvider` 协议隔离百炼、OpenAI、Gemini、豆包和 Claude 的请求差异。单工作线程把结构化分析状态持久化到 `Project`，供应商响应经统一 Pydantic 契约校验和本地适用性合并后提交；React 只消费稳定项目模型，负责配置、披露、明确启动、轮询和错误恢复。

**Tech Stack:** Python 3.9+、FastAPI 0.115.12、Pydantic 2、HTTPX 0.28.1、keyring 25.7.0、pytest 8.3.5、React 18.3.1、TypeScript 5.7.3、Vitest 3.0.8、Testing Library、Vite 6.1.0。

**Spec:** [`.scratch/ai-video-reverse-engineer/04-run-recoverable-multi-provider-semantic-analysis-design.md`](./04-run-recoverable-multi-provider-semantic-analysis-design.md)

**Requirements:** [Ticket 04](./issues/04-run-recoverable-semantic-analysis.md)；[产品总规格](./spec.md)；[ADR 0005](../../docs/adr/0005-explicit-multi-provider-analysis.md)

## Global Constraints

- 第一批只实现 `bailian`、`openai`、`gemini`、`doubao`、`claude`；Grok、智谱、Kimi、Azure OpenAI 和 Bedrock 不进入本计划。
- 用户必须明确选择供应商和已验证模型；不得自动选择、自动回退、静默切换或接受任意 Base URL。
- 只发送当前预处理版本的 `contact-sheet.jpg` 和 `analysis-proxy.json`；不得发送完整视频、独立关键帧、绝对路径、项目名或原文件名。
- API Key 只进入内存中的鉴权请求和系统安全存储；不得进入项目、浏览器存储、日志、错误响应、检查点或导出。
- 每个供应商维护集中、版本化的已验证模型注册表；新增模型必须通过视觉输入、结构化输出和真实冒烟测试。
- 豆包允许保存用户创建的推理接入点 ID，但必须先验证它绑定到注册表中的已验证视觉模型；它不是任意模型 ID 或任意 URL。
- 同一项目同一时刻只允许一个语义分析；每次用户启动最多发出一个供应商请求，后台自动重试次数为 0。
- 外部调用前先持久化任务；完成时必须核对任务 ID、参考视频 ID、本地预处理 ID、供应商和模型。
- 服务重启把遗留 `queued/running` 转为可重试的 `analysis_interrupted`，不得假装任务仍在运行。
- 本地多镜头或高运动失败事实不可被模型覆盖；语义分析只补全主体数量、复杂交互和描述性条目。
- 前端不得依赖供应商原始响应；五家供应商必须转换为同一 `StructuredAnalysis`。
- 默认测试不访问真实网络或真实 keyring；真实冒烟测试必须显式启用并可能产生费用。
- 当前目录没有 `.git`；不得初始化 Git、提交或推送，以每项任务的测试检查点代替提交步骤。

## File Structure

### Create

- `backend/app/analysis_models.py`：统一领域模型、适用性合并和结构化响应 Schema。
- `backend/app/analysis_provider.py`：供应商协议、注册表、错误类型、统一请求对象。
- `backend/app/analysis_provider_catalog.py`：版本化供应商与已验证模型清单。
- `backend/app/analysis_service_secrets.py`：安全 keyring 后端校验和按供应商凭据读写。
- `backend/app/analysis_providers/{bailian,openai,gemini,doubao,claude}.py`：五个明确适配器。
- `backend/app/semantic_analysis_jobs.py`：单工作线程队列与同项目去重。
- `backend/app/semantic_analysis_storage.py`：版本目录、检查点和失效清理。
- `backend/tests/test_analysis_models.py`：领域模型与本地结论合并测试。
- `backend/tests/test_analysis_provider_contract.py`：五适配器共享契约测试。
- `backend/tests/test_analysis_service_secrets.py`：系统安全存储测试。
- `backend/tests/test_semantic_analysis_api.py`：配置、启动、恢复、竞态和持久化测试。
- `frontend/src/analysisServiceApi.ts`、`frontend/src/analysisServiceApi.test.ts`：配置和分析 API。
- `frontend/src/AnalysisServiceSettings.tsx`、`frontend/src/AnalysisServiceSettings.test.tsx`：供应商配置界面。
- `frontend/src/SemanticAnalysisPanel.tsx`、`frontend/src/SemanticAnalysisPanel.test.tsx`：项目分析流程和报告。

### Modify

- `backend/app/main.py`：扩展 `Project`、注入供应商注册表与任务队列、增加配置和语义分析接口。
- `backend/pyproject.toml`、`backend/requirements.lock`：锁定 HTTPX 与 keyring。
- `backend/tests/test_projects_api.py`、`backend/tests/test_local_preprocessing_api.py`：旧项目兼容和预处理回归。
- `frontend/src/models.ts`：供应商配置、分析任务和结构化结果类型。
- `frontend/src/App.tsx`：设置入口、项目级供应商选择和分析面板。
- `frontend/src/styles.css`：配置卡、披露、阶段、错误和报告样式。
- `README.md`：配置、隐私、调用、恢复和验证说明。

---

### Task 1: 统一结构化分析领域契约

**Files:**
- Create: `backend/app/analysis_models.py`
- Create: `backend/tests/test_analysis_models.py`

**Interfaces:**
- Consumes: `LocalPreprocessing.reproducibilityAssessment` 和代理中的视频时间范围。
- Produces: `StructuredAnalysis`、`SemanticAnalysisTask`、`SemanticAnalysisError`、`merge_reproducibility()`、`new_semantic_analysis()`。

- [ ] **Step 1: 写失败测试锁定稳定领域结构**

```python
def test_structured_analysis_requires_all_sections_and_time_ranges():
    analysis = StructuredAnalysis.model_validate(valid_analysis_payload())
    assert analysis.version == 1
    assert [entry.kind for entry in analysis.subject] == ["observation"]
    assert analysis.action[0].timeRange.startSeconds < analysis.action[0].timeRange.endSeconds


def test_local_failure_cannot_be_overridden_by_provider():
    merged = merge_reproducibility(local_out_of_scope(), provider_claims_in_scope())
    assert merged.status == "out_of_scope"
    assert merged.checks[0].criterion == "single_shot"
    assert merged.checks[0].status == "failed"
```

增加边界测试：空章节、越界时间、开始时间不小于结束时间、非法置信值、总体相似度字段、缺少主体数量和复杂交互判断均拒绝。

- [ ] **Step 2: 运行测试确认模块不存在**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_analysis_models.py`

Expected: FAIL，错误包含 `No module named 'app.analysis_models'`。

- [ ] **Step 3: 实现领域模型**

```python
class TimeRange(BaseModel):
    startSeconds: float = Field(ge=0)
    endSeconds: float = Field(gt=0)

class AnalysisEntry(BaseModel):
    id: str
    kind: Literal["observation", "recommendation", "unsupported"]
    summary: str = Field(min_length=1, max_length=2000)
    timeRange: TimeRange
    confidence: Literal["high", "medium", "low"]
    confidenceReason: str = Field(min_length=1, max_length=1000)

class StructuredAnalysis(BaseModel):
    version: int = Field(ge=1)
    status: Literal["in_scope", "out_of_scope"]
    basicFacts: list[AnalysisEntry]
    suitability: list[ReproducibilityCheck]
    subject: list[AnalysisEntry]
    scene: list[AnalysisEntry]
    action: list[AnalysisEntry]
    camera: list[AnalysisEntry]
    lighting: list[AnalysisEntry]
```

`SemanticAnalysisTask` 固定记录 `providerId`、`modelId`、`sourceReferenceVideoId`、`sourcePreprocessingId`、`status`、时间戳、`result` 和 `error`。状态为 `queued | running | completed | failed`，错误码使用设计文档列出的十种闭集。

- [ ] **Step 4: 实现本地结论合并和时间校验**

模型结果中的时间必须处于 `[0, referenceVideo.durationSeconds]`。`merge_reproducibility()` 保留本地 `single_shot`、`motion_range`，只接受模型提供的 `primary_subject_count` 和 `complex_interaction`；任一失败即为 `out_of_scope`。

- [ ] **Step 5: 运行领域与后端回归**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_analysis_models.py backend/tests`

Expected: PASS。

---

### Task 2: 系统安全存储与应用级配置

**Files:**
- Create: `backend/app/analysis_service_secrets.py`
- Create: `backend/tests/test_analysis_service_secrets.py`
- Modify: `backend/pyproject.toml`
- Modify: `backend/requirements.lock`

**Interfaces:**
- Produces: `ProviderSecretStore.get(provider_id)`、`set(provider_id, candidate)`、`delete(provider_id)`、`configured(provider_id)`。

- [ ] **Step 1: 写失败测试**

```python
def test_secret_names_are_scoped_by_provider(fake_keyring):
    store = ProviderSecretStore(fake_keyring)
    store.set("openai", "secret-value")
    assert fake_keyring.saved == ("ai-video-reverse-engineer", "analysis-provider:openai", "secret-value")

def test_fail_null_and_plaintext_backends_are_rejected():
    for backend in unsafe_backends():
        with pytest.raises(SecureStorageUnavailable):
            ProviderSecretStore(backend)
```

覆盖：读取不存在、替换、删除、供应商 ID 白名单、异常脱敏，以及对象序列化中不出现 secret。

- [ ] **Step 2: 运行测试确认失败**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_analysis_service_secrets.py`

Expected: FAIL，模块不存在。

- [ ] **Step 3: 锁定依赖并实现安全后端校验**

在 `pyproject.toml` 增加 `httpx==0.28.1`、`keyring==25.7.0`，同步锁文件。只接受系统 keyring 后端模块：`keyring.backends.macOS`、`keyring.backends.Windows`、`keyring.backends.SecretService`、`keyring.backends.libsecret`；拒绝 fail、null、`keyrings.alt` 和未知文件后端。

- [ ] **Step 4: 运行测试与本机只读后端识别**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_analysis_service_secrets.py`

Run: `.venv/bin/python -c 'import keyring; print(type(keyring.get_keyring()).__module__)'`

Expected: 测试 PASS；本机输出系统 keyring 模块，不读取或写入真实密钥。

---

### Task 3: 供应商协议与已验证模型注册表

**Files:**
- Create: `backend/app/analysis_provider.py`
- Create: `backend/app/analysis_provider_catalog.py`
- Create: `backend/tests/test_analysis_provider.py`

**Interfaces:**
- Produces: `AnalysisProvider.analyze(request, credential)`、`ProviderCatalog`、`ProviderRequest`、`ProviderFailure`。

- [ ] **Step 1: 写协议和注册表失败测试**

```python
def test_catalog_contains_exactly_first_wave_providers():
    assert set(CATALOG.providers) == {"bailian", "openai", "gemini", "doubao", "claude"}

def test_every_provider_has_one_recommended_verified_vision_model():
    for provider in CATALOG.providers.values():
        recommended = [model for model in provider.models if model.recommended]
        assert len(recommended) == 1
        assert recommended[0].vision is True
        assert recommended[0].structuredOutput is True
```

覆盖非法 provider/model、任意 Base URL、豆包 endpoint 未验证绑定，以及错误类别闭集。

- [ ] **Step 2: 实现最小协议**

```python
class AnalysisProvider(Protocol):
    provider_id: str
    def validate_configuration(self, config: ProviderConfig, credential: str) -> None: ...
    def analyze(self, request: ProviderRequest, config: ProviderConfig, credential: str) -> dict: ...

class ProviderRequest(BaseModel):
    contactSheetBytes: bytes
    proxy: dict
    responseSchema: dict
```

`ProviderFailure` 保存统一 code、用户文案、retryable 和不含敏感内容的供应商 request ID。

- [ ] **Step 3: 实现集中模型注册表**

注册表字段固定为 `providerId`、`modelId`、`label`、`recommended`、`vision`、`structuredOutput`、`regions`。百炼使用 `qwen3.7-flash`；其他供应商的首个模型 ID 必须在实现当日从官方模型目录锁定，并在同一变更中把官方文档 URL、验证日期和真实冒烟报告路径写入注册表注释与 Ticket Comments。没有真实验证证据时，该模型不得暴露给前端。

- [ ] **Step 4: 运行协议测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_analysis_provider.py`

Expected: PASS；注册表不包含第二批或企业部署层供应商。

---

### Task 4: 首批五个供应商适配器与共享契约测试

**Files:**
- Create: `backend/app/analysis_providers/bailian.py`
- Create: `backend/app/analysis_providers/openai.py`
- Create: `backend/app/analysis_providers/gemini.py`
- Create: `backend/app/analysis_providers/doubao.py`
- Create: `backend/app/analysis_providers/claude.py`
- Create: `backend/tests/test_analysis_provider_contract.py`

**Interfaces:**
- Consumes: Task 3 协议和 Task 1 的 JSON Schema。
- Produces: 五个 `AnalysisProvider` 实现。

- [ ] **Step 1: 建立参数化共享契约测试**

```python
@pytest.mark.parametrize("provider_id", ["bailian", "openai", "gemini", "doubao", "claude"])
def test_provider_sends_only_proxy_and_contact_sheet(provider_id, provider_fixture):
    captured = provider_fixture(provider_id).analyze_valid_request()
    serialized = json.dumps(captured.body)
    assert captured.contact_sheet_was_sent
    assert "motion" in serialized
    for forbidden in ["reference-video.mp4", "/Users/", "project name", "secret-value"]:
        assert forbidden not in serialized

@pytest.mark.parametrize("provider_id", FIRST_WAVE)
def test_provider_returns_same_domain_payload(provider_id, provider_fixture):
    assert provider_fixture(provider_id).result() == valid_analysis_payload()
```

参数化覆盖 401/403、429、连接失败、总超时、能力不支持、内容拒绝、5xx、非 JSON、Schema 不匹配和 request ID 脱敏。

- [ ] **Step 2: 运行测试确认五个模块缺失**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_analysis_provider_contract.py`

Expected: FAIL，首个适配器模块不存在。

- [ ] **Step 3: 实现百炼和 OpenAI 适配器**

两者使用官方 HTTPS 固定端点和 JSON Schema 严格输出。图片使用 base64 data URL；代理 JSON 作为文本块。不得设置会截断结构化 JSON 的低 `max_tokens`，不得自动重试。

- [ ] **Step 4: 实现 Gemini 和 Claude 适配器**

使用各自官方结构化输出参数与原生图片块；不得通过 OpenAI 兼容中转站。响应先提取 JSON，再交给同一个 `StructuredAnalysis` 验证器。

- [ ] **Step 5: 实现豆包适配器**

固定火山方舟官方域名；配置只接受已验证 endpoint ID。保存配置前执行最小能力验证，确认 endpoint 绑定到注册表模型并支持视觉与结构化输出。

- [ ] **Step 6: 运行共享契约与后端回归**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_analysis_provider_contract.py backend/tests`

Expected: PASS；测试使用 HTTPX MockTransport，不访问真实网络。

---

### Task 5: 检查点、队列与恢复

**Files:**
- Create: `backend/app/semantic_analysis_storage.py`
- Create: `backend/app/semantic_analysis_jobs.py`
- Create: `backend/tests/test_semantic_analysis_storage.py`
- Create: `backend/tests/test_semantic_analysis_jobs.py`

**Interfaces:**
- Produces: `analysis_directory()`、`write_validated_checkpoint()`、`read_validated_checkpoint()`、`SemanticAnalysisJobQueue.submit()`。

- [ ] **Step 1: 写原子检查点与路径安全测试**

检查点只包含统一结构化结果、供应商、模型、源引用 ID 和版本；拒绝路径穿越、损坏 JSON、版本不匹配和任何 credential 字段。原子写使用同目录临时文件、`fsync` 和 `os.replace`。

- [ ] **Step 2: 写单线程队列测试**

断言同项目重复提交被拒绝、不同项目按顺序执行、异常后 active 状态清理、关闭等待已提交任务完成。

- [ ] **Step 3: 实现存储与队列并运行测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_semantic_analysis_storage.py backend/tests/test_semantic_analysis_jobs.py`

Expected: PASS。

---

### Task 6: 配置、启动、轮询和重试 API

**Files:**
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_semantic_analysis_api.py`
- Modify: `backend/tests/test_projects_api.py`
- Modify: `backend/tests/test_local_preprocessing_api.py`

**Interfaces:**
- Produces: `GET /api/analysis-providers`、`PUT /api/analysis-providers/{id}/configuration`、`DELETE /api/analysis-providers/{id}/credential`、`POST /api/projects/{id}/semantic-analysis`。

- [ ] **Step 1: 写配置 API 失败测试**

验证列表只返回非敏感配置和已验证模型；保存候选密钥必须先验证再替换；验证失败保留旧密钥；读取、错误和项目 JSON 不含明文、长度或前后缀。

- [ ] **Step 2: 写分析生命周期失败测试**

覆盖未预处理、供应商未配置、模型不在注册表、未确认披露、202 排队、重复 409、成功提交、十类错误、失败重试、检查点复用、服务重启中断、替换视频失效、旧任务返回不覆盖和上次成功结果保留。

- [ ] **Step 3: 扩展 Project 与依赖注入**

```python
class AnalysisSelection(BaseModel):
    providerId: Literal["bailian", "openai", "gemini", "doubao", "claude"]
    modelId: str

class Project(BaseModel):
    # existing fields
    analysisSelection: AnalysisSelection | None = None
    semanticAnalysis: SemanticAnalysisTask | None = None
```

`create_app()` 注入 `provider_registry`、`secret_store`、`semantic_queue_factory` 和时钟，测试不得访问真实服务。

- [ ] **Step 4: 实现配置 API**

PUT 请求携带供应商特定非敏感字段、modelId 和候选 API Key；后端先按注册表验证模型，再调用适配器最小连接验证，成功后写 keyring 和非敏感配置。DELETE 二次确认由前端负责，后端删除指定供应商凭据且不影响其他供应商。

- [ ] **Step 5: 实现语义分析事务**

请求体固定包含 `providerId`、`modelId`、`disclosureAccepted: true`。提交前读取当前预处理产物并保存 queued；后台开始时写 running，成功先写检查点，再在项目写锁中核对五项身份后提交 completed。失败保存稳定错误，不删除预处理或上一版成功结果。

- [ ] **Step 6: 运行 API 与后端全量测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_semantic_analysis_api.py backend/tests`

Expected: PASS。

---

### Task 7: 前端类型、API 和供应商设置

**Files:**
- Modify: `frontend/src/models.ts`
- Create: `frontend/src/analysisServiceApi.ts`
- Create: `frontend/src/analysisServiceApi.test.ts`
- Create: `frontend/src/AnalysisServiceSettings.tsx`
- Create: `frontend/src/AnalysisServiceSettings.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: Task 6 配置 API。
- Produces: 五供应商卡片、配置状态、模型选择、密钥写入/替换/删除和连接验证。

- [ ] **Step 1: 写 API 客户端测试**

锁定 URL 编码、HTTP 方法、候选密钥只出现在 PUT body、响应错误转换，以及 GET 响应无 credential 字段。

- [ ] **Step 2: 写设置组件测试**

覆盖五张卡、推荐模型默认值、只显示已验证模型、供应商特定字段、保存中防重复、失败保留旧状态、成功后清空输入、不回显密钥、删除二次确认和键盘焦点恢复。

- [ ] **Step 3: 实现类型、客户端和组件**

供应商卡片使用后端目录作为唯一显示来源，不在组件复制模型清单。密钥输入使用 `type="password"`、关闭自动完成并在请求结束后清空；“已配置”不显示掩码、长度或更新时间。

- [ ] **Step 4: 挂载设置入口并运行前端测试**

Run: `cd frontend && npm test -- --run src/analysisServiceApi.test.ts src/AnalysisServiceSettings.test.tsx && npm run build`

Expected: PASS。

---

### Task 8: 项目语义分析面板与报告

**Files:**
- Create: `frontend/src/SemanticAnalysisPanel.tsx`
- Create: `frontend/src/SemanticAnalysisPanel.test.tsx`
- Modify: `frontend/src/App.tsx`
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `Project.localPreprocessing`、供应商目录、Task 6 启动接口和项目轮询。
- Produces: 供应商/模型选择、数据披露、启动、状态、重试和基础结构化报告。

- [ ] **Step 1: 写关键用户流程失败测试**

断言挂载和预处理完成不会自动调用；只有勾选披露并点击开始才 POST；披露文字准确列出联系表、运动数据、供应商、模型和用途；切换供应商会同步更新接收方。

- [ ] **Step 2: 写恢复、错误和竞态测试**

覆盖排队/运行轮询、十类错误、只重跑语义分析、上次成功结果保留、项目切换忽略旧 Promise、服务重启中断、窄屏只读和完成后报告恢复。

- [ ] **Step 3: 实现组件并复用现有轮询模式**

不得新建全局状态库或 WebSocket。沿用 `LocalPreprocessingPanel` 的 generation token、mounted ref 和单次定时轮询模式；完成或失败即停止轮询。

- [ ] **Step 4: 展示统一结构化报告**

报告按基础事实、适用性、主体、场景、动作、运镜和光线分组；每项显示时间范围、观察/建议/未支持标签和局部置信原因，不显示总体百分比。

- [ ] **Step 5: 运行组件、全量测试和构建**

Run: `cd frontend && npm test -- --run src/SemanticAnalysisPanel.test.tsx && npm test && npm run build`

Expected: PASS。

---

### Task 9: 五供应商真实冒烟、文档和最终验收

**Files:**
- Modify: `README.md`
- Modify: `.scratch/ai-video-reverse-engineer/issues/04-run-recoverable-semantic-analysis.md`
- Create: `.scratch/ai-video-reverse-engineer/04-provider-smoke-report.md`

**Interfaces:**
- Consumes: Tasks 1～8。
- Produces: 已验证模型证据、运行文档和 Ticket 04 验收记录。

- [ ] **Step 1: 运行无网络全量测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests`

Run: `cd frontend && npm test && npm run build`

Expected: 全部 PASS，测试日志无真实外部请求。

- [ ] **Step 2: 对五家逐一执行显式真实冒烟**

使用同一受控联系表与代理 JSON，每家只执行一次最小视觉结构化请求。记录供应商、模型 ID、区域、HTTP 状态、结构验证结果、耗时和官方模型文档 URL；不记录密钥、Authorization、完整请求体或供应商原始响应。缺少某家凭据时该家保持“未验证”，不得加入前端目录，也不得勾选对应验收项。

- [ ] **Step 3: 更新 README**

说明五供应商配置字段、系统安全存储、已验证模型目录、数据外发边界、可能计费、明确启动、失败不切换、恢复机制和默认测试不联网。不得提供真实密钥示例或任意 Base URL 配置方法。

- [ ] **Step 4: 执行桌面与窄屏人工验收**

桌面至少 1024px：完成配置、切换、披露、启动、状态、失败重试和报告查看。窄屏：只能查看配置状态和已有报告，不可写密钥或启动分析。检查键盘顺序、可见焦点、`aria-live`、错误 `role="alert"` 和非颜色单一传意。

- [ ] **Step 5: 更新 Ticket 状态与证据**

只勾选有自动化或真实冒烟证据的验收项。在 Comments 记录测试数量、构建结果、人工视口和冒烟报告路径；五家均完成真实验证后才把 `Status` 改为 `done`。当前目录无 Git，不创建 commit。
