# Semantic Analysis Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立媒体无关的结构化语义分析任务、安全配置、隐私确认，以及百炼和本地 OpenAI 兼容基准适配器。

**Architecture:** 共享服务负责构造脱敏输入、提示词、结构校验、一次同供应商修复、任务持久化和重试；供应商适配器只负责 HTTP 请求与错误映射。分析任务沿用单工作线程和项目轮询模式，密钥通过可注入的系统安全存储接口管理。

**Tech Stack:** Python 3.9、FastAPI、Pydantic、httpx 0.28.1、keyring 25.7.0、React 18、Vitest。

**Spec:** `.scratch/ai-video-reverse-engineer/04-image-reference-and-multi-provider-analysis-design.md`

## Global Constraints

- 图片只发送 `analysis-proxy.jpg` 与宽高/比例；视频只发送 `contact-sheet.jpg` 与 `analysis-proxy.json`。
- 请求不得包含原素材、标准化图片、项目名、原文件名、绝对路径、ComfyUI 信息或其他供应商密钥。
- 结果必须分为 `observedFacts` 和 `generationSuggestions`；图片的 `observedFacts.temporal` 必须为 `null`。
- 格式修复只允许同一供应商一次；不得静默切换供应商。
- 密钥不得进入项目文件、前端存储、日志、产物或错误响应。
- 本地兼容服务 Base URL 仅允许回环主机。
- 实现与测试使用 `gpt-5.6-terra/high`；阶段审查使用 `gpt-6 Astra/medium`。

---

### Task 1: 定义分析输入、结果和任务领域模型

**Files:**
- Create: `backend/app/semantic_analysis.py`
- Create: `backend/app/analysis_input.py`
- Test: `backend/tests/test_semantic_analysis.py`
- Test: `backend/tests/test_analysis_input.py`
- Modify: `backend/app/main.py`
- Modify: `frontend/src/models.ts`

**Interfaces:**
- Produces: `ImageAnalysisInput`, `VideoAnalysisInput`, `StructuredVisualAnalysis`, `SemanticAnalysis`。
- Produces: `validate_analysis_for_media(payload, media_type) -> StructuredVisualAnalysis`。
- Produces: `build_analysis_input(data_dir, project) -> AnalysisInput`。
- Consumers: 所有供应商适配器和异步 runner。

- [ ] **Step 1: 写结构边界和泄漏防护失败测试**

```python
def test_image_analysis_rejects_temporal_observations():
    payload = valid_image_result()
    payload['observedFacts']['temporal'] = {'subjectMotion': '奔跑'}
    with pytest.raises(ValidationError):
        validate_analysis_for_media(payload, 'image')

def test_image_input_contains_only_proxy_and_dimensions(tmp_path, completed_image_project):
    value = build_analysis_input(tmp_path, completed_image_project)
    serialized = value.model_dump_json()
    assert 'originalName' not in serialized
    assert str(tmp_path) not in serialized
```

- [ ] **Step 2: 运行测试并确认模型缺失**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_semantic_analysis.py backend/tests/test_analysis_input.py`

Expected: FAIL。

- [ ] **Step 3: 实现明确的静态事实、时间事实和建议模型**

```python
class ObservedFacts(BaseModel):
    staticVisual: StaticVisualFacts
    temporal: Optional[TemporalFacts]

class StructuredVisualAnalysis(BaseModel):
    model_config = ConfigDict(extra='forbid')
    version: int = Field(default=1, ge=1)
    observedFacts: ObservedFacts
    generationSuggestions: GenerationSuggestions
```

`validate_analysis_for_media` 先做严格 Pydantic 校验，再依据 `mediaType` 拒绝图片的时间事实；所有结构模型使用 `extra='forbid'`，不要通过宽松可选字段掩盖供应商漏填。供应商分析首次落库固定 `version=1`；后续 Ticket 06 每次保存人工校正时递增该值。

- [ ] **Step 4: 实现只从已验证产物构造 AnalysisInput**

构造器先调用本地预处理产物校验；返回相对文件句柄/字节对象和允许的数值，不返回项目对象。测试恶意项目名、原文件名和数据目录均不出现在序列化请求元数据中。

- [ ] **Step 5: 运行领域测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_semantic_analysis.py backend/tests/test_analysis_input.py`

Expected: PASS。

- [ ] **Step 6: 验证检查点**

建议提交信息：`feat: define semantic analysis contract`

### Task 2: 实现安全凭据和供应商配置

**Files:**
- Create: `backend/app/credential_store.py`
- Create: `backend/app/analysis_settings.py`
- Test: `backend/tests/test_credential_store.py`
- Test: `backend/tests/test_analysis_settings.py`
- Modify: `backend/pyproject.toml`, `backend/requirements.lock`
- Modify: `backend/app/main.py`

**Interfaces:**
- Produces: `CredentialStore.get(provider)`, `set(provider, secret)`, `delete(provider)`。
- Produces: `AnalysisProviderConfiguration`，只含供应商、模型、配置状态和本地地址，不含密钥。
- Produces: `validate_loopback_base_url(value: str) -> str`。

- [ ] **Step 1: 锁定运行依赖并写密钥不回显测试**

在运行依赖加入 `httpx==0.28.1` 与 `keyring==25.7.0`。

```python
def test_configuration_response_never_returns_secret(client, fake_credentials):
    response = client.put('/api/analysis-providers/bailian/configuration', json={
        'apiKey': 'secret-value', 'model': 'qwen3.7-flash',
    })
    assert response.status_code == 200
    assert 'secret-value' not in response.text
    assert response.json()['credentialState'] == 'configured'
```

- [ ] **Step 2: 运行测试并确认配置接口缺失**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_credential_store.py backend/tests/test_analysis_settings.py`

Expected: FAIL。

- [ ] **Step 3: 实现可注入凭据存储**

生产实现调用系统 keyring，service name 固定为 `ai-video-reverse-engineer.analysis-provider`，username 使用供应商标识。测试只注入内存实现。捕获 keyring 后端不可用并返回 `secure_storage_unavailable`，不得回退到明文文件。

- [ ] **Step 4: 实现非敏感设置原子存储和回环地址校验**

```python
def validate_loopback_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {'http', 'https'} or parsed.hostname not in {'localhost', '127.0.0.1', '::1'}:
        raise ValueError('本地分析服务地址必须使用回环主机。')
    return value.rstrip('/')
```

拒绝 URL 用户信息、查询串和片段；设置文件只保存 provider、model、baseUrl 和 selectedProvider。

- [ ] **Step 5: 运行配置安全测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_credential_store.py backend/tests/test_analysis_settings.py`

Expected: PASS。

- [ ] **Step 6: 验证检查点**

建议提交信息：`feat: store analysis credentials securely`

### Task 3: 建立供应商协议、结构修复与两个基准适配器

**Files:**
- Create: `backend/app/analysis_providers/base.py`
- Create: `backend/app/analysis_providers/openai_compatible_chat.py`
- Create: `backend/app/analysis_providers/bailian.py`
- Create: `backend/app/analysis_providers/local_openai_compatible.py`
- Create: `backend/app/analysis_providers/test_image.py`
- Create: `backend/app/analysis_prompt.py`
- Create: `backend/app/analysis_response.py`
- Test: `backend/tests/analysis_providers/test_contract.py`
- Test: `backend/tests/analysis_providers/test_bailian.py`
- Test: `backend/tests/analysis_providers/test_local_openai_compatible.py`

**Interfaces:**
- Produces: `AnalysisProvider.analyze(request) -> ProviderResult`。
- Produces: `validate_or_repair(provider, raw, request) -> StructuredVisualAnalysis`。
- Consumes: Task 1 的输入/结果模型和 Task 2 的凭据配置。

- [ ] **Step 1: 写共享契约测试**

```python
@pytest.mark.parametrize('provider_factory', PROVIDER_FACTORIES)
def test_provider_sends_only_allowed_image_payload(provider_factory, image_request, http_mock):
    provider_factory(http_mock).analyze(image_request)
    body = http_mock.last_request_json
    assert 'project-name' not in json.dumps(body)
    assert 'original.png' not in json.dumps(body)
    assert 'data:image/jpeg;base64,' in json.dumps(body)
```

- [ ] **Step 2: 运行契约测试并确认适配器缺失**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/analysis_providers`

Expected: FAIL。

- [ ] **Step 3: 实现统一请求对象和版本化提示词**

提示词必须明确媒体类型、允许观察的事实、禁止推断为事实的字段，并要求只返回 JSON。`PROMPT_VERSION = 1` 与结果一起保存。

- [ ] **Step 4: 实现百炼与本地兼容适配器**

两者复用 Chat Completions 请求编码，但 Base URL、模型清单和凭据来源独立。百炼地址固定，不允许用户覆盖；本地地址必须通过 Task 2 校验。HTTP 超时、401/403、429、5xx 和无法解析响应映射为稳定领域错误。连接测试使用代码生成的固定 16×16 RGB PNG 和固定问题，验证图片输入能力但不发送用户素材。

- [ ] **Step 5: 实现一次同供应商结构修复**

第一次校验失败时构造修复提示，只包含供应商返回的候选 JSON 和 Pydantic 错误摘要；不重复附加代理图。第二次失败返回 `invalid_analysis_response`。

- [ ] **Step 6: 运行所有供应商核心测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/analysis_providers backend/tests/test_semantic_analysis.py`

Expected: PASS。

- [ ] **Step 7: 验证检查点**

建议提交信息：`feat: add baseline analysis providers`

### Task 4: 增加异步语义分析任务与 API

**Files:**
- Create: `backend/app/semantic_analysis_jobs.py`
- Create: `backend/app/semantic_analysis_runner.py`
- Create: `backend/app/semantic_analysis_storage.py`
- Modify: `backend/app/main.py`
- Test: `backend/tests/test_semantic_analysis_api.py`
- Test: `backend/tests/test_semantic_analysis_runner.py`

**Interfaces:**
- Produces: `POST /api/projects/{project_id}/semantic-analysis`。
- Produces: `GET/PUT /api/analysis-providers` 配置系列接口。
- Persists: `Project.semanticAnalysis`。

- [ ] **Step 1: 写确认字段、幂等、失败和不切换测试**

```python
def test_analysis_requires_explicit_disclosure_confirmation(client, ready_project):
    response = client.post(f"/api/projects/{ready_project}/semantic-analysis", json={
        'provider': 'bailian', 'model': 'qwen3.7-flash', 'disclosureAccepted': False,
    })
    assert response.status_code == 400
    assert response.json()['detail']['code'] == 'analysis_disclosure_required'
```

- [ ] **Step 2: 运行 API 测试并确认路由缺失**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_semantic_analysis_api.py backend/tests/test_semantic_analysis_runner.py`

Expected: FAIL。

- [ ] **Step 3: 实现单工作线程队列和原子项目状态更新**

同项目去重；启动返回 202；有效同源、同供应商、同模型、同提示词/结构版本结果返回 200。服务重启时把 queued/running 标为可重试失败，不自动重发外部请求。

- [ ] **Step 4: 实现配置、连接测试和分析路由**

连接测试发送 Task 3 的内置测试图，不包含用户代理素材。分析路由验证预处理完成、来源 ID 一致、供应商已配置、模型在清单中且本次披露已确认。参考素材替换成功时清空 `semanticAnalysis`；新分析任务开始时使旧结果失效。

- [ ] **Step 5: 验证日志和错误脱敏**

捕获测试日志并断言 API Key、Authorization、Base64 前缀后的内容、绝对路径和供应商完整响应均不存在。

- [ ] **Step 6: 运行后端分析全套测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_semantic_analysis*.py backend/tests/analysis_providers`

Expected: PASS。

- [ ] **Step 7: 验证检查点**

建议提交信息：`feat: run recoverable semantic analysis jobs`

### Task 5: 增加分析服务设置、披露确认和结果界面

**Files:**
- Create: `frontend/src/analysisProviderApi.ts`
- Create: `frontend/src/SemanticAnalysisPanel.tsx`
- Create: `frontend/src/AnalysisProviderSettings.tsx`
- Test: `frontend/src/analysisProviderApi.test.ts`
- Test: `frontend/src/SemanticAnalysisPanel.test.tsx`
- Test: `frontend/src/AnalysisProviderSettings.test.tsx`
- Modify: `frontend/src/models.ts`, `frontend/src/App.tsx`, `frontend/src/App.test.tsx`, `frontend/src/styles.css`

**Interfaces:**
- Consumes: provider configuration/status API and `Project.semanticAnalysis`。
- Produces: provider selection, secure key write form, connection test, one-shot disclosure confirmation, polling and fact/suggestion sections。

- [ ] **Step 1: 写密钥不回显、披露和语义分区失败测试**

```ts
expect(screen.getByRole('heading', { name: '可观察事实' })).toBeInTheDocument();
expect(screen.getByRole('heading', { name: '生成建议' })).toBeInTheDocument();
await user.click(screen.getByRole('button', { name: '开始语义分析' }));
expect(screen.getByText(/不会发送原始素材/)).toBeInTheDocument();
expect(startAnalysis).not.toHaveBeenCalled();
```

- [ ] **Step 2: 运行前端测试并确认组件缺失**

Run: `npm --prefix frontend test -- analysisProviderApi.test.ts SemanticAnalysisPanel.test.tsx AnalysisProviderSettings.test.tsx`

Expected: FAIL。

- [ ] **Step 3: 实现设置与披露界面**

密码输入提交后立即清空，不把值复制到 React 全局状态或 localStorage。配置响应只显示状态。确认区使用页面内结构和明确按钮，不用原生 `confirm`。

- [ ] **Step 4: 实现分析任务轮询和媒体专属结果**

复用本地预处理的请求代次保护和卸载清理模式。图片隐藏时间事实；视频展示时间事实。刷新失败不伪造任务失败。首页环境状态只展示当前选中的分析供应商，设置区展示其他供应商状态。

- [ ] **Step 5: 完成增量全量验收**

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
npm --prefix frontend test
node frontend/scripts/verify-color-contrast.mjs
npm --prefix frontend run build
```

Expected: 全部通过；扫描项目数据、前端构建和测试输出均不含测试密钥。

- [ ] **Step 6: 验证检查点**

建议提交信息：`feat: add semantic analysis workflow`
