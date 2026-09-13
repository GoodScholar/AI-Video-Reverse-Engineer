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

---

### Task 1: 锁定模型目录和供应商契约固件

**Files:**
- Create: `backend/app/provider_models.py`
- Modify: `backend/app/analysis_providers/test_image.py`
- Modify: `backend/tests/analysis_providers/test_contract.py`
- Test: `backend/tests/test_provider_models.py`
- Modify: `frontend/src/models.ts`

**Interfaces:**
- Produces: `models_for(provider: ProviderId) -> tuple[ProviderModel, ...]`。
- Produces: `connection_test_request(provider, model) -> ProviderRequest`，只含内置图片和固定提示。
- Consumers: 配置 API、五个适配器和前端下拉框。

- [ ] **Step 1: 写模型白名单和内置测试图失败测试**

```python
def test_cloud_model_catalog_is_closed():
    assert model_is_allowed('openai', 'gpt-5.6-luna')
    assert not model_is_allowed('openai', 'user-entered-model')

def test_connection_test_has_no_project_data():
    request = connection_test_request('openai', 'gpt-5.6-luna')
    assert request.mediaType == 'image'
    assert request.context == {'width': 16, 'height': 16, 'aspectRatio': 1.0}
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

百炼和本地目录仍由 04c 定义。复用计划 04c 以代码生成的固定 16×16 RGB PNG，不新增远程资源。

- [ ] **Step 4: 将配置 API 的模型校验改为目录查询**

未知云端模型返回 `unsupported_analysis_model`；本地兼容服务继续允许用户输入模型名。

- [ ] **Step 5: 运行目录与核心回归测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_provider_models.py backend/tests/test_analysis_settings.py backend/tests/analysis_providers/test_contract.py`

Expected: PASS。

- [ ] **Step 6: 验证检查点**

建议提交信息：`feat: lock analysis provider model catalog`

### Task 2: 接入 OpenAI 与 Grok Responses API

**Files:**
- Create: `backend/app/analysis_providers/responses_api.py`
- Create: `backend/app/analysis_providers/openai.py`
- Create: `backend/app/analysis_providers/grok.py`
- Test: `backend/tests/analysis_providers/test_openai.py`
- Test: `backend/tests/analysis_providers/test_grok.py`

**Interfaces:**
- Consumes: `ProviderRequest`、模型目录和凭据。
- Produces: `OpenAIAnalysisProvider`、`GrokAnalysisProvider`。

- [ ] **Step 1: 写端点、鉴权、图片块和输出提取失败测试**

```python
def test_openai_uses_responses_image_input(http_mock, image_request):
    provider = OpenAIAnalysisProvider(http_mock.client)
    provider.analyze(image_request)
    assert http_mock.last_url == 'https://api.openai.com/v1/responses'
    assert find_content_type(http_mock.last_json, 'input_image')
```

Grok 对应地址固定为 `https://api.x.ai/v1/responses`，两者都使用 Bearer 鉴权。

- [ ] **Step 2: 运行两家测试并确认适配器缺失**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/analysis_providers/test_openai.py backend/tests/analysis_providers/test_grok.py`

Expected: FAIL。

- [ ] **Step 3: 实现协议帮助函数与独立适配器**

共享函数只编码 `input_text`/`input_image` 和提取 `output_text`；端点、Header、模型、超时与错误映射留在各自适配器。图片使用 Base64 data URL，不创建供应商文件对象。

- [ ] **Step 4: 覆盖 401、429、5xx、超时和拒绝响应**

分别映射 `provider_auth_failed`、`provider_rate_limited`、`provider_unavailable`、`provider_timeout`、`provider_content_rejected`。

- [ ] **Step 5: 运行适配器和共享契约测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/analysis_providers/test_openai.py backend/tests/analysis_providers/test_grok.py backend/tests/analysis_providers/test_contract.py`

Expected: PASS。

- [ ] **Step 6: 验证检查点**

建议提交信息：`feat: add OpenAI and Grok analysis providers`

### Task 3: 接入 Claude Messages API

**Files:**
- Create: `backend/app/analysis_providers/claude.py`
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

设置 `max_tokens` 为结构化结果上限，不启用 Files API，避免把测试或用户代理持久化为供应商文件。

- [ ] **Step 4: 运行 Claude 与契约测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/analysis_providers/test_claude.py backend/tests/analysis_providers/test_contract.py`

Expected: PASS。

- [ ] **Step 5: 验证检查点**

建议提交信息：`feat: add Claude analysis provider`

### Task 4: 接入 Gemini generateContent API

**Files:**
- Create: `backend/app/analysis_providers/gemini.py`
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

请求使用 `responseMimeType: application/json`。拒绝空 candidates、安全过滤阻止和缺失文本，并映射为稳定错误，不回显 safetyRatings 全文。

- [ ] **Step 4: 运行 Gemini 与契约测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/analysis_providers/test_gemini.py backend/tests/analysis_providers/test_contract.py`

Expected: PASS。

- [ ] **Step 5: 验证检查点**

建议提交信息：`feat: add Gemini analysis provider`

### Task 5: 接入火山方舟豆包图片理解

**Files:**
- Create: `backend/app/analysis_providers/doubao.py`
- Test: `backend/tests/analysis_providers/test_doubao.py`

**Interfaces:**
- Produces: `DoubaoAnalysisProvider`。
- Uses: 火山方舟官方 Responses/Chat 兼容端点，端点常量由适配器封装，不能由用户覆盖。

- [ ] **Step 1: 根据官方图片理解请求写固定契约测试**

测试固定 Bearer 鉴权、模型 `doubao-seed-2-0-lite-260428`、Base64 图片块、JSON 输出要求和响应文本路径；将一份最小成功/鉴权失败/限流响应保存为测试内联字典，不保存真实响应头。

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
- Modify tests: `backend/tests/test_semantic_analysis_api.py`, `frontend/src/AnalysisProviderSettings.test.tsx`
- Modify: `README.md`

**Interfaces:**
- Produces: 七种方式完整注册表和 `verificationState: unverified | available | failed`。
- Produces: `python backend/scripts/smoke_analysis_provider.py --provider <id>`。

- [ ] **Step 1: 写未验证不得显示可用的失败测试**

```ts
expect(screen.getByText('OpenAI')).toBeInTheDocument();
expect(screen.getByText('未验证')).toBeInTheDocument();
expect(screen.queryByText('可用')).not.toBeInTheDocument();
```

- [ ] **Step 2: 实现注册表与验证状态持久化**

成功连接测试记录 provider、model、catalogVersion 和 `verifiedAt`；更换密钥或模型立即重置为 `unverified`。失败只记录标准错误码和时间。

- [ ] **Step 3: 实现真实冒烟脚本**

脚本从系统安全存储读取所选供应商密钥，调用与应用相同的内置测试图和提示词，校验完整结构后退出 0；不打印请求体、密钥或原始响应。

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
