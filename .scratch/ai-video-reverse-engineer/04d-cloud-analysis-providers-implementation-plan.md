# Cloud Analysis Providers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在共享语义分析内核上接入 OpenAI、豆包、Gemini、Grok 和 Claude，并以统一契约和非用户测试图验证真实图片输入。

**Architecture:** 每家供应商拥有独立适配器和模型目录项，HTTP 编码可以复用协议级帮助函数，但不得通过一个充满供应商条件分支的通用类实现。共享契约测试验证数据边界和标准错误，供应商测试验证各自请求/响应格式，真实连接测试只发送内置测试图。

**Tech Stack:** Python 3.9、httpx 0.28.1、Pydantic、FastAPI、Vitest；不引入五家官方 SDK。

**Spec:** `.scratch/ai-video-reverse-engineer/04-image-reference-and-multi-provider-analysis-design.md`

## Global Constraints

- 不增加自动回退、并行分析或跨供应商修复。
- 云端 Base URL 固定在代码中，用户只能配置 API Key 和内置模型。
- 初始模型目录固定为：OpenAI `gpt-5.6-luna`、豆包 `doubao-seed-2-0-lite-260428`、Gemini `gemini-2.5-flash`、Grok `grok-4.6`、Claude `claude-sonnet-5`。
- 上述模型只有在对应真实连接测试成功后才显示“可用”；代码存在但未验证时显示“未验证”。
- 连接测试发送应用内置测试图，不发送用户参考素材。
- 供应商原始错误和响应正文不得直接返回前端或写入普通日志。
- 实现与测试使用 `gpt-5.6-terra/high`；阶段审查使用 `gpt-6 Astra/medium`。

## 2026-09-13 执行裁决（覆盖下文冲突步骤）

- 保留 04c 唯一核心请求：`ProviderRequest(analysisInput, prompt, model, isRepair)`；固定探针继续使用 `connection_test_request(model)`，验证 `request.analysisInput`，不增加平行的 `mediaType/context` 顶级字段。
- 七种供应商 ID 的唯一来源为 `provider_models.py`。云端模型严格查目录；本地 OpenAI 兼容服务继续允许非空自填模型。配置 API 对未知模型沿用 `invalid_analysis_model`。
- 沿用 04c 稳定错误码：`authentication_failed`、`rate_limited`、`network_error`、`timeout`、`provider_error`、`unsupported_model_capability`、`invalid_analysis_response`。如确需区分内容拒绝或配额不足，先在公共错误表增加 `content_rejected` / `quota_exceeded`，不得由适配器创建同义码。
- `openai.py`、`doubao.py`、`gemini.py`、`claude.py` 已有旧三参数入口，均按 Modify 处理。新增 04c 单参数入口并保留显式 `analyze_legacy`；豆包核心凭据只能使用关键字参数，避免与旧 endpoint binding 位置参数碰撞。
- 所有新核心适配器必须走 04c 的同一强化 HTTP 边界：禁环境代理、禁重定向、响应流累计不超过 256,000 bytes、120 秒总 deadline，并可在 TCP、TLS、部分响应头、首字节及慢滴流阶段取消。不得调用 `analysis_providers/__init__.py` 的旧全量缓冲 `post_json`。
- 每家核心适配器必须同时实现 `analyze(request)` 和 `test_connection(model=None)`。连接测试使用固定 16×16 PNG、完整分析提示和严格 `StructuredVisualAnalysis` 校验；HTTP 200 或任意文本不等于可用。
- Responses API 输出必须遍历 `output` 中的 `message/output_text`；Claude 只拼接 `text`、排除 `thinking`；Gemini 排除 thought 块，并拒绝安全阻断、截断和空 candidates。探针 MIME 按真实 PNG 发送。
- OpenAI 与 Grok 固定 Responses 端点并设置 `store: false`；结构化参数使用 Responses 的 `text.format`。Claude 使用 Messages `output_config.format`；Gemini 使用 `generationConfig.responseMimeType` 与 JSON Schema。
- 豆包保留官方发布型号 `doubao-seed-2-0-lite-260428`，固定 `https://ark.cn-beijing.volces.com/api/v3/responses`，按方舟官方 Responses/OpenAI 兼容格式实现，并设置 `store: false`。由于该快照的方舟原生图片/结构化契约未获得完整公开证据，在真实密钥冒烟成功前必须保持 `unverified`；不得用 `260215` 静默替换，也不得伪造通过。
- 验证状态必须绑定非敏感 `configurationRevision` 与 `catalogVersion` 并 CAS 写入。更换密钥、模型、本地地址或目录版本立即失效；迟到的旧连接结果不得覆盖新配置。目录版本不加入历史语义分析检查点身份，也不改写历史模型。
- 前端七家统一使用目录标签；云端选择内置模型下拉且 API Key 必填，本地保留地址、自填模型和可选 Key。`App` 与 `SemanticAnalysisPanel` 不得继续使用百炼/本地二分标签。
- 模拟契约通过与真实冒烟通过分开报告。没有实际密钥时完成代码与模拟测试，但状态保持 `unverified`；真实冒烟只有通过与 UI 相同的连接测试/状态写入路径才可标记 `available`。

### 官方协议证据

- OpenAI：`gpt-5.6-luna`、Responses 图片输入与结构化输出：<https://developers.openai.com/api/docs/models/gpt-5.6-luna>、<https://developers.openai.com/api/docs/guides/images-vision>、<https://developers.openai.com/api/docs/guides/structured-outputs>。
- Grok：`grok-4.6`、Responses 图片输入与结构化输出：<https://docs.x.ai/developers/models/grok-4.6>、<https://docs.x.ai/developers/model-capabilities/images/understanding>、<https://docs.x.ai/developers/model-capabilities/text/structured-outputs>。
- Claude：`claude-sonnet-5`、Messages、Vision 与 Structured Outputs：<https://platform.claude.com/docs/en/models/overview>、<https://platform.claude.com/docs/en/api/messages/create>、<https://platform.claude.com/docs/en/build-with-claude/vision>、<https://platform.claude.com/docs/en/build-with-claude/structured-outputs>。
- Gemini：`gemini-2.5-flash`、Generate Content 图片与结构化输出：<https://ai.google.dev/gemini-api/docs/models/gemini-2.5-flash>、<https://ai.google.dev/gemini-api/docs/generate-content/image-understanding>、<https://ai.google.dev/gemini-api/docs/generate-content/structured-output>。
- 豆包：官方发布记录确认 `doubao-seed-2-0-lite-260428` 存在；方舟官方 Responses 基址和 message/output_text 结构见：<https://www.volcengine.com/docs/6492/2165228?lang=en>、<https://www.volcengine.com/docs/82379/1795150>、<https://www.volcengine.com/docs/82379/1958524?lang=zh>。对 260428 图片/结构化支持的组合属于基于同系列兼容协议的实现推断，必须由真实冒烟最终确认。

---

### Task 1: 锁定模型目录和供应商契约固件

**Files:**
- Create: `backend/app/provider_models.py`
- Modify: `backend/app/analysis_providers/test_image.py`
- Modify: `backend/app/credential_store.py`
- Modify: `backend/app/analysis_settings.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/analysis_providers/test_contract.py`
- Test: `backend/tests/test_provider_models.py`
- Modify: `frontend/src/models.ts`
- Modify: `frontend/src/analysisProviderApi.ts`

**Interfaces:**
- Produces: `models_for(provider: ProviderId) -> tuple[ProviderModel, ...]`。
- Preserves: `connection_test_request(model) -> ProviderRequest`，只含内置图片和固定提示。
- Consumers: 配置 API、五个适配器和前端下拉框。

- [ ] **Step 1: 写模型白名单和内置测试图失败测试**

```python
def test_cloud_model_catalog_is_closed():
    assert model_is_allowed('openai', 'gpt-5.6-luna')
    assert not model_is_allowed('openai', 'user-entered-model')

def test_connection_test_has_no_project_data():
    request = connection_test_request('gpt-5.6-luna')
    assert request.analysisInput.mediaType == 'image'
    assert (request.analysisInput.width, request.analysisInput.height) == (16, 16)
    assert request.analysisInput.aspectRatio == 1.0
```

- [ ] **Step 2: 运行测试并确认目录缺失**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_provider_models.py backend/tests/analysis_providers/test_contract.py`

Expected: FAIL。

- [ ] **Step 3: 实现不可变模型目录**

```python
MODEL_CATALOG = {
    'openai': (ProviderModel(id='gpt-5.6-luna', label='GPT-5.6 Luna'),),
    'doubao': (ProviderModel(id='doubao-seed-2-0-lite-260428', label='Doubao Seed 2.0 Lite'),),
    'gemini': (ProviderModel(id='gemini-2.5-flash', label='Gemini 2.5 Flash'),),
    'grok': (ProviderModel(id='grok-4.6', label='Grok 4.6'),),
    'claude': (ProviderModel(id='claude-sonnet-5', label='Claude Sonnet 5'),),
}
```

同一 `MODEL_CATALOG` 也收录百炼 `qwen3.7-flash` 与本地兼容服务的空目录，作为七种供应商 ID、内置模型和展示标签的唯一来源；百炼适配器现有公开 `models` 属性改为从目录派生并保持兼容。本地兼容服务继续允许用户输入非空模型名。复用计划 04c 以代码生成的固定 16×16 RGB PNG，不新增远程资源。

- [ ] **Step 4: 将配置 API 的模型校验改为目录查询**

未知云端模型返回既有 `invalid_analysis_model`；本地兼容服务继续允许用户输入非空模型名。同步扩展 CredentialStore、设置存储与读取迁移、配置响应类型及主应用列表；旧两家设置读取默认补齐新字段且不丢失选择。五家核心适配器尚未实现，本步骤不注册供应商工厂，实际注册统一留到 Task 6。

- [ ] **Step 5: 运行目录与核心回归测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_provider_models.py backend/tests/test_analysis_settings.py backend/tests/analysis_providers/test_contract.py`

Expected: PASS。

- [ ] **Step 6: 验证检查点**

建议提交信息：`feat: lock analysis provider model catalog`

### Task 1.5: 抽取并复用强化 HTTP 边界

**Files:**
- Create: `backend/app/analysis_providers/http_transport.py`
- Modify: `backend/app/analysis_providers/openai_compatible_chat.py`
- Modify: `backend/app/main.py`
- Modify tests: `backend/tests/analysis_providers/test_bailian.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True)
  class ProviderHTTPResult:
      status_code: int
      json_body: Optional[Any]
      body_text: str
      request_id: Optional[str]

  def new_provider_http_client() -> httpx.Client: ...

  def post_provider_json(
      client: httpx.Client,
      url: str,
      *,
      headers: Mapping[str, str],
      payload: Mapping[str, Any],
  ) -> ProviderHTTPResult: ...
  ```
  `post_provider_json()` 对成功和失败 HTTP 状态都返回同一受限结果，使适配器可依据状态码及受限响应正文完成稳定错误映射；仅网络、TLS、超时、响应超限等传输失败抛出共享的类型化传输异常。`body_text` 与 `json_body` 均来自同一份不超过 `256_000 bytes` 的响应字节，`request_id` 仅从允许的响应头提取。
- Consumers: 百炼、本地兼容及 Task 2～5 的全部核心适配器。

- [ ] **Step 1: 迁移 04c 网络边界回归并先保持通过**

覆盖禁代理/禁重定向、流式超限早停、首字节延迟、慢滴流、部分响应头和 TLS ClientHello 阻塞；抽取前后行为必须一致。

- [ ] **Step 2: 建立协议无关的请求结果**

帮助函数只负责安全发送、状态码、有限 JSON 字节与 request ID；供应商 payload、Header、结构提取和拒绝映射仍留在独立适配器。

- [ ] **Step 3: 运行百炼、本地和 API 回归**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/analysis_providers/test_bailian.py backend/tests/analysis_providers/test_local_openai_compatible.py backend/tests/test_semantic_analysis_api.py`

Expected: PASS。

- [ ] **Step 4: 验证检查点**

建议提交信息：`refactor: share hardened provider transport`

### Task 2: 接入 OpenAI 与 Grok Responses API

**Files:**
- Create: `backend/app/analysis_providers/responses_api.py`
- Modify: `backend/app/analysis_providers/openai.py`
- Create: `backend/app/analysis_providers/grok.py`
- Test: `backend/tests/analysis_providers/test_openai.py`
- Test: `backend/tests/analysis_providers/test_grok.py`

**Interfaces:**
- Consumes: `ProviderRequest`、模型目录和凭据。
- Produces: `OpenAIAnalysisProvider`、`GrokAnalysisProvider`。

- [ ] **Step 1: 写端点、鉴权、图片块和输出提取失败测试**

```python
def test_openai_uses_responses_image_input(http_mock, image_request):
    provider = OpenAIAnalysisProvider(http_mock.client, credential="test-key")
    provider.analyze(image_request)
    assert http_mock.last_url == 'https://api.openai.com/v1/responses'
    assert find_content_type(http_mock.last_json, 'input_image')
```

Grok 对应地址固定为 `https://api.x.ai/v1/responses`，两者都使用 Bearer 鉴权。

- [ ] **Step 2: 运行两家测试并确认适配器缺失**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/analysis_providers/test_openai.py backend/tests/analysis_providers/test_grok.py`

Expected: FAIL。

- [ ] **Step 3: 实现协议帮助函数与独立适配器**

共享函数只编码 `input_text`/`input_image`，设置 `store: false`、`text.format`，并遍历 message/output_text；端点、Header、模型与错误映射留在各自适配器。图片使用 Base64 data URL，不创建供应商文件对象。两家都复用 Task 1.5 的网络边界。

- [ ] **Step 4: 覆盖 401、429、5xx、超时和拒绝响应**

分别映射到既有稳定错误码；只有公共错误表已显式增加时才使用 `content_rejected` / `quota_exceeded`。

- [ ] **Step 5: 运行适配器和共享契约测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/analysis_providers/test_openai.py backend/tests/analysis_providers/test_grok.py backend/tests/analysis_providers/test_contract.py`

Expected: PASS。

- [ ] **Step 6: 验证检查点**

建议提交信息：`feat: add OpenAI and Grok analysis providers`

### Task 3: 接入 Claude Messages API

**Files:**
- Modify: `backend/app/analysis_providers/claude.py`
- Test: `backend/tests/analysis_providers/test_claude.py`

**Interfaces:**
- Produces: `ClaudeAnalysisProvider`。
- Uses: `POST https://api.anthropic.com/v1/messages`。

- [ ] **Step 1: 写 Claude Header、图片 source 和文本提取失败测试**

```python
assert request.headers['x-api-key'] == 'test-key'
assert request.headers['anthropic-version'] == '2023-06-01'
assert request.json()['messages'][0]['content'][0]['type'] == 'image'
assert request.json()['messages'][0]['content'][0]['source']['type'] == 'base64'
```

- [ ] **Step 2: 运行测试并确认适配器缺失**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/analysis_providers/test_claude.py`

Expected: FAIL。

- [ ] **Step 3: 实现 Messages 请求、内容块提取和错误映射**

设置 `max_tokens` 为结构化结果上限并使用 `output_config.format` JSON Schema；不启用 Files API。提取所有 `text` 块并排除 thinking 块，避免把测试或用户代理持久化为供应商文件。

- [ ] **Step 4: 运行 Claude 与契约测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/analysis_providers/test_claude.py backend/tests/analysis_providers/test_contract.py`

Expected: PASS。

- [ ] **Step 5: 验证检查点**

建议提交信息：`feat: add Claude analysis provider`

### Task 4: 接入 Gemini generateContent API

**Files:**
- Modify: `backend/app/analysis_providers/gemini.py`
- Test: `backend/tests/analysis_providers/test_gemini.py`

**Interfaces:**
- Produces: `GeminiAnalysisProvider`。
- Uses: `POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent`。

- [ ] **Step 1: 写 Gemini inlineData、API Key Header 和候选文本失败测试**

```python
parts = request.json()['contents'][0]['parts']
assert parts[0]['inlineData']['mimeType'] == 'image/jpeg'
assert request.headers['x-goog-api-key'] == 'test-key'
assert extract_text(response_json) == response_json['candidates'][0]['content']['parts'][0]['text']
```

- [ ] **Step 2: 运行测试并确认适配器缺失**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/analysis_providers/test_gemini.py`

Expected: FAIL。

- [ ] **Step 3: 实现 Gemini 请求和安全设置**

请求使用 `responseMimeType: application/json` 与响应 Schema，MIME 按代理真实格式。拒绝空 candidates、安全过滤阻止、截断、thought-only 和缺失文本，并映射为稳定错误，不回显 safetyRatings 全文。

- [ ] **Step 4: 运行 Gemini 与契约测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/analysis_providers/test_gemini.py backend/tests/analysis_providers/test_contract.py`

Expected: PASS。

- [ ] **Step 5: 验证检查点**

建议提交信息：`feat: add Gemini analysis provider`

### Task 5: 接入火山方舟豆包图片理解

**Files:**
- Modify: `backend/app/analysis_providers/doubao.py`
- Test: `backend/tests/analysis_providers/test_doubao.py`

**Interfaces:**
- Produces: `DoubaoAnalysisProvider`。
- Uses: `POST https://ark.cn-beijing.volces.com/api/v3/responses`，端点常量由适配器封装，不能由用户覆盖。

- [ ] **Step 1: 根据官方图片理解请求写固定契约测试**

测试固定 Bearer 鉴权、模型 `doubao-seed-2-0-lite-260428`、`store: false`、Base64 `input_image`、完整 JSON Schema 要求和 message/output_text 响应遍历；将最小成功/鉴权失败/限流响应保存为测试内联字典，不保存真实响应头。此为基于方舟 Responses 兼容协议的实现裁决，真实冒烟前状态保持 `unverified`。

- [ ] **Step 2: 运行测试并确认适配器缺失**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/analysis_providers/test_doubao.py`

Expected: FAIL。

- [ ] **Step 3: 实现豆包适配器与方舟错误映射**

只实现图片和当前视频联系表所需的单图请求；不借机加入视频文件直传。解析请求 ID 时只保存非敏感字符串。

- [ ] **Step 4: 运行豆包与共享契约测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/analysis_providers/test_doubao.py backend/tests/analysis_providers/test_contract.py`

Expected: PASS。

- [ ] **Step 5: 验证检查点**

建议提交信息：`feat: add Doubao analysis provider`

### Task 6: 注册供应商、显示验证状态并完成真实冒烟

**Files:**
- Modify: `backend/app/analysis_providers/__init__.py`
- Modify: `backend/app/main.py`, `backend/app/analysis_settings.py`
- Create: `backend/scripts/smoke_analysis_provider.py`
- Modify: `frontend/src/AnalysisProviderSettings.tsx`
- Modify: `frontend/src/SemanticAnalysisPanel.tsx`, `frontend/src/App.tsx`
- Modify: `frontend/src/analysisProviderApi.ts`, `frontend/src/models.ts`
- Modify tests: `backend/tests/test_semantic_analysis_api.py`, `frontend/src/AnalysisProviderSettings.test.tsx`
- Modify: `README.md`, `CONTEXT.md` 和七供应商 ADR/实施索引

**Interfaces:**
- Produces: 七种方式完整注册表和 `verificationState: unverified | available | failed`，并绑定 `configurationRevision` 与 `catalogVersion`。
- Produces: `python backend/scripts/smoke_analysis_provider.py --provider <id>`。

- [ ] **Step 1: 写未验证不得显示可用的失败测试**

```ts
expect(screen.getByText('OpenAI')).toBeInTheDocument();
expect(screen.getByText('未验证')).toBeInTheDocument();
expect(screen.queryByText('可用')).not.toBeInTheDocument();
```

- [ ] **Step 2: 实现注册表与验证状态持久化**

成功连接测试记录 provider、model、非敏感 configurationRevision、catalogVersion 和 `verifiedAt`；更换密钥、模型、本地地址或目录版本立即重置为 `unverified`。测试开始捕获 revision，成功/失败写入时用 CAS 拒绝迟到结果；失败只记录标准错误码和时间。

- [ ] **Step 3: 实现真实冒烟脚本**

脚本通过与 UI 相同的本地 API 连接测试路径，调用相同内置测试图、严格解析/一次修复与 CAS 状态写入；不直接绕过服务读取密钥，不打印请求体、密钥或原始响应。

- [ ] **Step 4: 运行模拟全套测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/analysis_providers backend/tests/test_semantic_analysis_api.py`

Run: `npm --prefix frontend test -- AnalysisProviderSettings.test.tsx`

Expected: PASS。

- [ ] **Step 5: 对已由用户配置密钥的每家执行真实冒烟**

Run: `PYTHONPATH=backend .venv/bin/python backend/scripts/smoke_analysis_provider.py --provider openai`，并依次替换为 `doubao`、`gemini`、`grok`、`claude`、`bailian`；本地方式使用 `local_openai_compatible`。

Expected: 已配置供应商输出一行脱敏成功摘要并退出 0；未配置供应商退出非零且保持“未验证”，不得伪造通过。

- [ ] **Step 6: 完成增量全量验收**

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
npm --prefix frontend test
node frontend/scripts/verify-color-contrast.mjs
npm --prefix frontend run build
```

Expected: 全部通过。

- [ ] **Step 7: 验证检查点**

建议提交信息：`feat: complete cloud analysis provider support`
