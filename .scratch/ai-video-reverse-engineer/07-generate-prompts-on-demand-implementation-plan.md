# Ticket 07 按需生成提示词 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在复刻工作台中提供用户主动触发的提示词生成能力，生成并持久化可编辑、可复制的中英文正负图生视频提示词。

**Architecture:** FastAPI 在复刻项目中保存版本化 `PromptGeneration`，复用当前 `SemanticAnalysis` 记录的供应商、模型、凭据和统一供应商边界，仅发送已校正的 `StructuredVisualAnalysis` 异步生成稳定结构。React 在 Ticket 05 的复刻工作台右栏提供按需按钮和四个提示词字段；Ticket 06 的结构化分析版本变化只把现有结果标记为过期，不自动调用模型或覆盖用户编辑。

**Tech Stack:** Python 3.9+、FastAPI 0.115.12、Pydantic 2、pytest 8.3.5、React 18.3.1、TypeScript 5.7.3、Vitest 3.0.8、Testing Library、Vite 6.1.0。

**Spec:** [`.scratch/ai-video-reverse-engineer/07-generate-prompts-on-demand-design.md`](./07-generate-prompts-on-demand-design.md)

**Requirements:** [Ticket 07](./issues/07-generate-strategy-prompts-and-smart-match.md)；[产品总规格](./spec.md)

## Global Constraints

- Ticket 04、05、06 必须先完成；本计划不得在当前仅有本地预处理的界面中伪造语义分析或复刻工作台。
- 提示词只在桌面端用户点击“生成提示词”后生成，分析完成或项目恢复时不得自动调用。
- 固定复用当前语义分析的供应商与模型；不显示第二套供应商选择，不自动回退，也不再次发送图片/视频代理或原始素材。
- 首版只提供通用图生视频中文正向、中文负向、英文正向、英文负向四个字段，不提供可灵、Runway、Veo 等平台格式。
- 结果必须记录所依据的结构化分析版本；相关分析变化后标记为 `stale`，不得自动覆盖用户编辑。
- 超出可复刻范围的结果标记为实验性建议，不得称为可执行工作流。
- 未配置密钥、鉴权、网络、超时、无效响应必须可区分；失败不得重跑本地预处理或语义分析，不得静默切换供应商。
- 供应商原始响应不进入前端契约；API 密钥不进入项目数据、日志或导出内容。
- 同一项目只允许一个提示词生成请求；重复请求返回 `409 prompt_generation_in_progress`。
- 生成期间若结构化分析版本变化，完成的旧结果不得覆盖当前项目。
- 移动端只允许查看和复制已有结果，不允许生成或编辑。
- 所有状态使用文字与图标联合传意，复制反馈可被辅助技术读取，键盘焦点可见。
- 当前目录没有 `.git`；不得初始化 Git、提交或推送，以每个任务末尾的完整验证代替提交步骤。

## 前置接口契约

执行前先确认 Ticket 04～06 已提供等价接口；若实际命名不同，只在本计划开工前统一映射一次，不并行维护两套类型：

```python
class StructuredVisualAnalysis(BaseModel):
    version: int
    observedFacts: ObservedFacts
    generationSuggestions: GenerationSuggestions

class PromptGenerationService(Protocol):
    def generate_json(self, *, purpose: str, payload: dict) -> dict: ...
```

```ts
export type StructuredVisualAnalysis = {
  version: number;
  observedFacts: ObservedFacts;
  generationSuggestions: GenerationSuggestions;
};
```

图片请求必须断言 `observedFacts.temporal === null`，动作、运镜、节奏和音频只从 `generationSuggestions` 读取；视频请求可以同时使用已观察时间事实和生成建议。两类请求都必须剔除供应商、模型以外的运行元数据以及任何素材路径。

## File Structure

### Create

- `backend/app/prompt_generation.py`：提示词领域模型、供应商响应校验、请求载荷构造与错误映射。
- `backend/app/prompt_generation_jobs.py`：按项目去重的后台生成队列和过期结果保护。
- `backend/tests/test_prompt_generation.py`：纯领域契约、载荷和响应校验测试。
- `backend/tests/test_prompt_generation_api.py`：启动、轮询、失败、重试、并发和持久化测试。
- `frontend/src/promptGenerationApi.ts`：启动、保存编辑的 API 客户端。
- `frontend/src/promptGenerationApi.test.ts`：请求与错误转换测试。
- `frontend/src/PromptPanel.tsx`：按需生成、四字段编辑、复制、过期与实验性状态。
- `frontend/src/PromptPanel.test.tsx`：桌面、移动、无障碍和竞态测试。

### Modify

- `backend/app/main.py`：扩展 `Project`，注入提示词生成服务与队列，增加生成和编辑接口，并在结构化分析变化时失效提示词。
- `backend/tests/test_projects_api.py`：锁定新项目 `promptGeneration: null` 与旧项目兼容。
- `frontend/src/models.ts`：增加 `PromptGeneration`、`PromptTextSet`、`PromptGenerationError` 和项目字段。
- `frontend/src/App.tsx` 或 Ticket 05 建立的工作台组件：挂载 `PromptPanel` 并合并项目更新。
- `frontend/src/styles.css`：提示词卡片、状态、编辑器、复制反馈和响应式样式。
- `README.md`：按需调用、保存位置、数据边界和验证命令。

---

### Task 1: 提示词领域模型与稳定生成契约

**Files:**
- Create: `backend/app/prompt_generation.py`
- Create: `backend/tests/test_prompt_generation.py`

**Interfaces:**
- Consumes: `SemanticAnalysis.result.model_dump()` 和 `result.version`。
- Produces: `PromptGeneration`、`PromptTextSet`、`PromptGenerationError`、`build_prompt_request()`、`parse_prompt_response()`、`new_prompt_generation()`。

- [ ] **Step 1: 写失败测试，锁定四字段、版本和实验性标记**

```python
from app.prompt_generation import build_prompt_request, new_prompt_generation, parse_prompt_response


def test_new_generation_records_analysis_version_and_scope():
    task = new_prompt_generation("prompt-1", analysis_version=7, experimental=True, now=NOW)
    assert task.status == "queued"
    assert task.sourceAnalysisVersion == 7
    assert task.experimental is True
    assert task.prompts is None


def test_parse_response_requires_all_four_non_blank_fields():
    parsed = parse_prompt_response({
        "positiveZh": "雨夜街头，人物缓慢前行",
        "negativeZh": "画面抖动，肢体畸形",
        "positiveEn": "A person walking slowly on a rainy street",
        "negativeEn": "camera shake, malformed limbs",
    })
    assert parsed.positiveEn.startswith("A person")
```

再增加 `test_parse_response_rejects_missing_or_blank_field`、`test_request_contains_only_confirmed_analysis_fields`、`test_request_never_labels_output_as_original_prompt` 和 `test_error_codes_are_closed_literal_set`。

- [ ] **Step 2: 运行定向测试，确认因模块不存在而失败**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_prompt_generation.py`

Expected: FAIL，错误包含 `No module named 'app.prompt_generation'`。

- [ ] **Step 3: 实现最小领域模型**

```python
class PromptTextSet(BaseModel):
    positiveZh: str = Field(min_length=1, max_length=12000)
    negativeZh: str = Field(min_length=1, max_length=12000)
    positiveEn: str = Field(min_length=1, max_length=12000)
    negativeEn: str = Field(min_length=1, max_length=12000)


class PromptGenerationError(BaseModel):
    code: Literal["key_unconfigured", "authentication_failed", "network_error", "timeout", "invalid_response", "generation_failed"]
    message: str
    retryable: bool


class PromptGeneration(BaseModel):
    id: str
    sourceAnalysisVersion: int = Field(ge=1)
    status: Literal["queued", "running", "available", "failed", "stale"]
    experimental: bool
    prompts: PromptTextSet | None = None
    editedFields: list[Literal["positiveZh", "negativeZh", "positiveEn", "negativeEn"]] = []
    createdAt: str
    updatedAt: str
    completedAt: str | None = None
    error: PromptGenerationError | None = None
```

`build_prompt_request()` 只接收结构化分析产品字段，固定 `purpose="image_to_video_prompt"`，并明确要求返回四个 JSON 字段；`parse_prompt_response()` 使用 `PromptTextSet.model_validate()`，任何缺失、空白、超长或额外顶层结构都映射为 `ValueError("提示词响应结构无效")`。

- [ ] **Step 4: 运行领域测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_prompt_generation.py`

Expected: PASS。

- [ ] **Step 5: 运行后端回归**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests`

Expected: PASS；不得初始化或提交 Git。

---

### Task 2: 可恢复且防旧结果覆盖的生成任务

**Files:**
- Create: `backend/app/prompt_generation_jobs.py`
- Create: `backend/tests/test_prompt_generation_jobs.py`

**Interfaces:**
- Consumes: `PromptGenerationService.generate_json(purpose="image_to_video_prompt", payload=request)`、Task 1 的构造与解析函数。
- Produces: `PromptGenerationJobQueue.submit(project_id: str, generation_id: str) -> bool`、`is_active(project_id: str) -> bool`、`close() -> None`。

- [ ] **Step 1: 写失败测试锁定顺序、去重和关闭行为**

```python
def test_queue_rejects_second_active_job_for_same_project():
    queue = PromptGenerationJobQueue(run_job=blocking_runner, max_workers=1)
    assert queue.submit("project-1", "prompt-1") is True
    assert queue.submit("project-1", "prompt-2") is False
    release_runner()
    queue.close()


def test_queue_allows_different_projects_and_clears_active_state():
    queue = PromptGenerationJobQueue(run_job=recording_runner, max_workers=1)
    assert queue.submit("project-1", "prompt-1") is True
    assert queue.submit("project-2", "prompt-2") is True
    wait_until_complete()
    assert queue.is_active("project-1") is False
    queue.close()
```

- [ ] **Step 2: 运行测试并确认模块缺失失败**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_prompt_generation_jobs.py`

Expected: FAIL，错误包含 `No module named 'app.prompt_generation_jobs'`。

- [ ] **Step 3: 用单工作线程实现最小队列**

使用 `ThreadPoolExecutor(max_workers=1)` 和锁保护的 `active_project_ids`。`submit()` 在提交前原子去重，在 `finally` 中清理 active 状态；`close()` 使用 `shutdown(wait=True, cancel_futures=False)`，不得增加外部任务系统或 WebSocket。

- [ ] **Step 4: 运行队列测试和后端回归**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_prompt_generation_jobs.py backend/tests`

Expected: PASS；不得初始化或提交 Git。

---

### Task 3: 生成、状态读取、重试与编辑 API

**Files:**
- Modify: `backend/app/main.py`
- Create: `backend/tests/test_prompt_generation_api.py`
- Modify: `backend/tests/test_projects_api.py`

**Interfaces:**
- Consumes: 前置 `Project.semanticAnalysis.result`、`PromptGenerationService`，Task 1/2 的模型和队列。
- Produces: `POST /api/projects/{project_id}/prompt-generation`、`PATCH /api/projects/{project_id}/prompts`，并在现有 `GET /api/projects/{project_id}` 返回 `promptGeneration`。

- [ ] **Step 1: 写 API 失败测试**

```python
def test_start_requires_completed_structured_analysis(client, project_without_analysis):
    response = client.post(f"/api/projects/{project_without_analysis}/prompt-generation")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "structured_analysis_required"


def test_start_is_on_demand_and_persists_queued_task(client, analyzed_project, fake_service):
    restored = client.get(f"/api/projects/{analyzed_project}").json()
    assert restored["promptGeneration"] is None
    response = client.post(f"/api/projects/{analyzed_project}/prompt-generation")
    assert response.status_code == 202
    assert response.json()["promptGeneration"]["sourceAnalysisVersion"] == 3


def test_old_completion_cannot_overwrite_new_analysis_version(app, analyzed_project, blocking_service):
    start_generation(app, analyzed_project)
    replace_analysis_version(app, analyzed_project, 4)
    blocking_service.release_success(VALID_PROMPTS)
    project = read_project(app, analyzed_project)
    assert project.promptGeneration.status == "stale"
    assert project.promptGeneration.sourceAnalysisVersion == 3
```

同时覆盖：`prompt_generation_in_progress`、四类供应商错误映射、失败后重试、不重跑预处理/分析、旧成功结果在失败时保留、超范围 `experimental=true`、编辑字段持久化、空白/超长编辑拒绝、旧 JSON 缺少字段兼容、密钥不落盘。

- [ ] **Step 2: 运行 API 测试确认接口不存在**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_prompt_generation_api.py`

Expected: FAIL，首次 POST 返回 404 或 `Project` 缺少 `promptGeneration`。

- [ ] **Step 3: 扩展项目与应用注入点**

在 `Project` 增加：

```python
promptGeneration: Optional[PromptGeneration] = None
```

为 `create_app()` 增加显式注入参数：

```python
    prompt_generation_service: PromptGenerationService | None = None,
prompt_queue_factory: Callable = PromptGenerationJobQueue,
```

读取旧项目时依赖 Pydantic 默认 `None` 保持兼容。应用关闭时关闭提示词队列。

- [ ] **Step 4: 实现启动与后台完成事务**

POST 规则：无已完成的 `semanticAnalysis.result` 返回 `409 structured_analysis_required`；同项目运行中返回 `409 prompt_generation_in_progress`；否则固定当前分析记录中的供应商与模型，保存 `queued` 并返回 202，再提交队列。后台完成时在项目写锁内同时比较 `generation.id` 和 `semanticAnalysis.result.version`，只有二者仍匹配才写入 `available`；版本变化时保留任务并写成 `stale`，不得写入旧 prompts。

- [ ] **Step 5: 实现显式编辑接口**

请求体固定为：

```python
class UpdatePromptsInput(BaseModel):
    positiveZh: str = Field(min_length=1, max_length=12000)
    negativeZh: str = Field(min_length=1, max_length=12000)
    positiveEn: str = Field(min_length=1, max_length=12000)
    negativeEn: str = Field(min_length=1, max_length=12000)
```

PATCH 仅允许 `available` 或 `stale` 且已有 prompts 的结果；比较原值后更新 `editedFields` 与 `updatedAt`。编辑不得改变 `sourceAnalysisVersion` 或把 `stale` 恢复为 `available`。

- [ ] **Step 6: 接入 Ticket 06 的失效规则**

在结构化分析成功保存或人工修改事务中：若 `promptGeneration.sourceAnalysisVersion != semanticAnalysis.result.version`，将已有任务状态改为 `stale`；不发请求、不清空 prompts、不重置 `editedFields`。

- [ ] **Step 7: 运行 API 与全量后端测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_prompt_generation_api.py backend/tests/test_projects_api.py backend/tests`

Expected: PASS；不得初始化或提交 Git。

---

### Task 4: 前端 API、类型与按需状态模型

**Files:**
- Modify: `frontend/src/models.ts`
- Create: `frontend/src/promptGenerationApi.ts`
- Create: `frontend/src/promptGenerationApi.test.ts`

**Interfaces:**
- Consumes: Task 3 的两个 HTTP 接口。
- Produces: `startPromptGeneration(projectId): Promise<Project>`、`updatePrompts(projectId, prompts): Promise<Project>` 和共享 TypeScript 类型。

- [ ] **Step 1: 写失败测试锁定 URL、方法和错误信息**

```ts
it("only starts generation when called", async () => {
  fetchMock.mockResolvedValue(response(projectWithQueuedPrompt, 202));
  await startPromptGeneration("project / 1");
  expect(fetchMock).toHaveBeenCalledWith(
    "/api/projects/project%20%2F%201/prompt-generation",
    { method: "POST" },
  );
});

it("sends all four edited fields", async () => {
  fetchMock.mockResolvedValue(response(projectWithPrompts));
  await updatePrompts("project-1", prompts);
  expect(fetchMock).toHaveBeenCalledWith("/api/projects/project-1/prompts", expect.objectContaining({
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(prompts),
  }));
});
```

覆盖网络失败、结构化 API detail 和非 JSON 错误响应。

- [ ] **Step 2: 运行测试确认模块不存在**

Run: `cd frontend && npm test -- --run src/promptGenerationApi.test.ts`

Expected: FAIL，错误包含 `Failed to resolve import "./promptGenerationApi"`。

- [ ] **Step 3: 增加与后端完全一致的类型**

```ts
export type PromptTextSet = {
  positiveZh: string;
  negativeZh: string;
  positiveEn: string;
  negativeEn: string;
};

export type PromptGeneration = {
  id: string;
  sourceAnalysisVersion: number;
  status: "queued" | "running" | "available" | "failed" | "stale";
  experimental: boolean;
  prompts: PromptTextSet | null;
  editedFields: (keyof PromptTextSet)[];
  createdAt: string;
  updatedAt: string;
  completedAt: string | null;
  error: { code: string; message: string; retryable: boolean } | null;
};
```

在 `Project` 增加 `promptGeneration: PromptGeneration | null`。

- [ ] **Step 4: 实现 API 客户端并运行测试**

复用 `readApiError()` 和现有网络错误文案，不复制第三套错误解析逻辑。

Run: `cd frontend && npm test -- --run src/promptGenerationApi.test.ts && npm run build`

Expected: PASS；不得初始化或提交 Git。

---

### Task 5: 复刻工作台提示词面板

**Files:**
- Create: `frontend/src/PromptPanel.tsx`
- Create: `frontend/src/PromptPanel.test.tsx`
- Modify: Ticket 05 建立的工作台组件
- Modify: `frontend/src/styles.css`

**Interfaces:**
- Consumes: `project.semanticAnalysis.result`、`project.promptGeneration`、Task 4 的 API 函数。
- Produces: `PromptPanel({ project, onProjectUpdated, start?, save? })`。

- [ ] **Step 1: 写未生成、生成中、成功和失败测试**

```tsx
it("does not generate on mount and starts only after click", async () => {
  const start = vi.fn().mockResolvedValue(projectWithQueuedPrompt);
  render(<PromptPanel project={analyzedProject} onProjectUpdated={vi.fn()} start={start} />);
  expect(start).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "生成提示词" }));
  expect(start).toHaveBeenCalledWith(analyzedProject.id);
});

it("shows four editable prompt fields and experimental warning", () => {
  render(<PromptPanel project={outOfScopeProjectWithPrompts} onProjectUpdated={vi.fn()} />);
  for (const name of ["中文正向提示词", "中文负向提示词", "英文正向提示词", "英文负向提示词"]) {
    expect(screen.getByRole("textbox", { name })).toBeVisible();
  }
  expect(screen.getByText("实验性建议")).toBeVisible();
});
```

继续覆盖：缺少分析时禁用原因、生成中防重复点击、失败重试保留旧结果、`stale` 提示、保存编辑、保存失败不丢输入、四个独立复制按钮、复制成功 `aria-live`、窄屏只读、项目切换时忽略旧请求。

- [ ] **Step 2: 运行组件测试确认失败**

Run: `cd frontend && npm test -- --run src/PromptPanel.test.tsx`

Expected: FAIL，错误包含 `Failed to resolve import "./PromptPanel"`。

- [ ] **Step 3: 实现组件状态和轮询复用**

按钮仅在桌面、结构化分析可用且非 `queued/running` 时启用。生成后沿用 Ticket 04/05 的项目轮询，不新增第二个全局轮询器；项目 ID 或请求 generation 变化时忽略旧 Promise。`failed` 且已有 prompts 时继续显示旧内容和错误，重试按钮只调用 `startPromptGeneration()`。

- [ ] **Step 4: 实现四字段编辑、保存与复制**

使用四个带明确 `<label>` 的 `textarea`。只有桌面端显示编辑和保存；移动端使用只读内容。复制调用 `navigator.clipboard.writeText()`，成功显示“已复制”，失败显示“复制失败，请手动选择文本”，两者放在 `aria-live="polite"` 区域。

- [ ] **Step 5: 挂载到工作台右栏并增加样式**

挂载点必须是 Ticket 05 的复刻工作台右栏“复刻方案”区域，不得挂到 `LocalPreprocessingPanel`。沿用现有橙红主操作、深炭灰信息结构、蓝灰辅助状态；`stale` 和实验性状态同时使用文字、图标和边框，不只依赖颜色。

- [ ] **Step 6: 运行组件、全量前端和构建验证**

Run: `cd frontend && npm test -- --run src/PromptPanel.test.tsx && npm test && npm run build`

Expected: PASS；不得初始化或提交 Git。

---

### Task 6: 文档、隐私回归与整体验收

**Files:**
- Modify: `README.md`
- Modify: `.scratch/ai-video-reverse-engineer/issues/07-generate-strategy-prompts-and-smart-match.md`

**Interfaces:**
- Consumes: Tasks 1～5 的完整用户流程。
- Produces: 可执行说明、验证证据和 Ticket 状态更新。

- [ ] **Step 1: 更新 README**

记录：提示词仅点击后生成、发送的是当前结构化分析而非原始参考素材或分析代理、复用当前语义分析供应商且不静默切换、生成四个字段、项目本地保存位置、移动端只读、失败重试不会重跑前置分析，以及 API 密钥不进入项目和导出结果。

- [ ] **Step 2: 运行密钥与敏感字段扫描**

Run: `rg -n "api[_-]?key|authorization|bearer|sk-" backend frontend/src .scratch/ai-video-reverse-engineer -g '!*.test.*'`

Expected: 只出现安全存储、请求头构造或文档性说明；`projects.json` 序列化模型、提示词结果和前端类型中不包含密钥字段。

- [ ] **Step 3: 运行全量自动化验证**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests`

Expected: PASS。

Run: `cd frontend && npm test && npm run build`

Expected: PASS。

- [ ] **Step 4: 执行桌面和移动人工验收**

桌面宽度至少 1024px：确认分析完成后无自动请求；点击按钮后显示生成状态；成功后四字段可编辑、保存、复制；分析修改后显示“需要重新生成”；超范围显示“实验性建议”。移动宽度：确认已有四字段可查看和复制，但没有生成、编辑或保存控件。

- [ ] **Step 5: 更新 Ticket 07 状态与证据**

只有上述自动化与人工验收全部通过后，才把 Ticket 07 的 `Status` 改为 `done`，逐项勾选验收条件，并在 `## Comments` 追加执行日期、后端测试数量、前端测试数量、构建结果和人工验收视口。当前目录无 Git，不创建 commit。
