# 内容制作阶段投影 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 由现有简报、脚本、批量变体、配音、预览、审核与导出记录派生唯一内容制作阶段，并让四步内容工作室在刷新后恢复真实进度。

**Architecture:** 新增无持久化副作用的 `content_workflow` 纯投影模块，以及只负责一致读取与错误映射的 GET 路由。前端把服务端投影作为步骤完成状态和默认位置的事实来源，只保留用户当前查看步骤的临时状态；现有 AIGC 与批量制作 mutation 成功后通知父工作区刷新投影。

**Tech Stack:** Python 3、FastAPI、pytest、React 18、TypeScript、Vitest、Testing Library。

**Spec:** `docs/superpowers/specs/2026-09-27-content-workflow-projection-design.md`

## Global Constraints

- 不新增可手工修改或持久化的阶段字段，不迁移现有状态文件。
- 不改变现有 AIGC、配音、预览、审核、导出写接口及版本冲突契约。
- 当前活动 AIGC 批次必须匹配现有 `aigcHandoffKey`，包括五条候选 ID 与版本。
- 一条变体失败、失效或退回不得改变兄弟变体的阶段与审核结果。
- 无配音但已有当前有效预览的旧项目必须继续可审核。
- 上游重新编辑只改变当前默认创作阶段，不删除或改写历史交接快照。
- 不新增依赖，不顺带执行任务 03 的批量制作模块拆分。

## Review Focus

- 交接后重新保存并确认同组候选时，旧 `aigcHandoffKey` 不得仍被当作当前活动批次；Task 1 的精确 key 回归测试覆盖。
- 字幕、时间线或画布变化后，旧预览、审核和导出不得继续算作当前结果；Task 2 的版本回退测试覆盖。
- 同批次混合通过、退回、待审核和失败时，项目保持可继续处理，兄弟状态不互相覆盖；Task 2 的混合批次测试覆盖。
- AIGC 或批量状态文件损坏时，接口必须返回 503 而不是伪装为 `draft`；Task 3 的损坏存储测试覆盖。
- 旧项目没有 AIGC 记录或配音记录时，最后一个传统批次和有效预览仍可恢复；Task 1、Task 2 的兼容测试覆盖。

---

### Task 1: 上游阶段与活动批次选择

**Files:**
- Create: `backend/app/content_workflow.py`
- Create: `backend/tests/test_content_workflow.py`
- Modify: `.scratch/module-business-boundaries/issues/02-content-workflow-projection.md`

**Interfaces:**
- Consumes: AIGC 状态 `{brief, candidates}`、批量状态 `{tasks}`、当前图片/视频素材 ID 集合。
- Produces: `project_content_workflow(aigc_state: dict[str, Any], batch_state: dict[str, Any], visual_asset_ids: set[str]) -> dict[str, Any]`；首个切片支持 `draft`、`brief_ready`、`scripts_confirmed`、精确活动批次选择和四步映射。

- [ ] **Step 1: 领取任务票**

将 issue 02 的 `Status` 从 `ready-for-agent` 改为 `claimed`，在 `## Comments` 记录已确认规格和实施计划路径。

- [ ] **Step 2: 写上游阶段与精确交接的失败测试**

在 `backend/tests/test_content_workflow.py` 添加：

```python
def test_projection_maps_draft_brief_and_confirmed_scripts_to_steps(): ...
def test_projection_requires_exact_handoff_key_after_candidate_revision_changes(): ...
def test_projection_uses_latest_legacy_batch_only_when_aigc_has_no_activity(): ...
```

关键断言：

```python
assert draft["stage"] == "draft" and draft["currentStep"] == 0
assert brief_ready["stage"] == "brief_ready" and brief_ready["completedSteps"] == [True, False, False, False]
assert confirmed["stage"] == "scripts_confirmed" and confirmed["currentStep"] == 2
assert edited_after_handoff["activeBatchId"] is None
assert edited_after_handoff["stage"] == "scripts_confirmed"
assert legacy["activeBatchId"] == "legacy-parent"
```

- [ ] **Step 3: 运行测试并确认失败**

Run: `PYTHONPATH=backend python3 -m pytest -q backend/tests/test_content_workflow.py`

Expected: FAIL，原因是 `app.content_workflow` 或 `project_content_workflow` 尚不存在。

- [ ] **Step 4: 实现最小上游投影**

在 `backend/app/content_workflow.py` 定义：

```python
ContentWorkflowStage = Literal["draft", "brief_ready", "scripts_confirmed", "voice_ready", "preview_ready", "review_pending", "approved", "rejected", "delivered"]

def project_content_workflow(
    aigc_state: dict[str, Any],
    batch_state: dict[str, Any],
    visual_asset_ids: set[str],
) -> dict[str, Any]: ...
```

使用与 `create_from_aigc` 相同的紧凑 JSON 序列化计算当前 `aigcHandoffKey`；当前候选版本不匹配时只保留历史批次，不选为活动批次。无 AIGC 活动时才使用最后一个拥有子变体的传统父任务作为兼容回退。

- [ ] **Step 5: 运行定向测试**

Run: `PYTHONPATH=backend python3 -m pytest -q backend/tests/test_content_workflow.py`

Expected: PASS。

- [ ] **Step 6: 提交上游投影切片**

```bash
git add backend/app/content_workflow.py backend/tests/test_content_workflow.py .scratch/module-business-boundaries/issues/02-content-workflow-projection.md
git commit -m "feat: derive content workflow entry stage"
```

### Task 2: 变体新鲜度与批次聚合

**Files:**
- Modify: `backend/app/content_workflow.py`
- Modify: `backend/app/batch_editing_api.py:78-97`
- Modify: `backend/tests/test_content_workflow.py`
- Test: `backend/tests/test_batch_review_api.py`
- Test: `backend/tests/test_batch_export_api.py`

**Interfaces:**
- Consumes: Task 1 的 `project_content_workflow(...)`、批量任务的 `contentRevision`、`variant.revision`、字幕版本、配音、预览、审核和导出记录。
- Produces: `review_version(task: dict[str, Any]) -> tuple[int, int, int]`、`review_status(task: dict[str, Any]) -> str` 公共纯函数；完整项目与逐变体投影响应。

- [ ] **Step 1: 写变体阶段和混合批次失败测试**

补充：

```python
def test_current_versions_roll_back_stale_preview_review_and_delivery(): ...
def test_mixed_variants_keep_independent_review_and_delivery_stages(): ...
def test_current_preview_without_voiceover_remains_reviewable(): ...
def test_failed_variant_reports_issue_without_overwriting_siblings(): ...
```

关键断言：

```python
assert changed["variants"][0]["stage"] == "scripts_confirmed"
assert {issue["code"] for issue in changed["variants"][0]["issues"]} >= {"preview_stale", "review_stale"}
assert mixed["stage"] == "review_pending"
assert [item["stage"] for item in mixed["variants"]] == ["approved", "rejected", "review_pending"]
assert no_voice["variants"][0]["stage"] == "review_pending"
assert failed["counts"]["failed"] == 1
assert failed["variants"][1]["stage"] == "approved"
```

- [ ] **Step 2: 运行新测试并确认失败**

Run: `PYTHONPATH=backend python3 -m pytest -q backend/tests/test_content_workflow.py`

Expected: FAIL，现有投影尚未返回逐变体阶段、计数和失效原因。

- [ ] **Step 3: 公共化既有审核纯函数**

将 `_review_version`、`_review_status` 分别重命名为 `review_version`、`review_status`，更新 `batch_editing_api.py` 内部所有引用。不要改变函数算法或现有 HTTP 响应。

- [ ] **Step 4: 实现当前预览、交付和聚合规则**

扩展 `project_content_workflow(...)`，返回规格中的 `stage`、`currentStep`、`completedSteps`、活动 ID、`counts`、`variants`、`issues`。逐变体按“当前交付 → 当前审核 → 当前预览 → 当前配音 → 已交接脚本”选择最深合法事实；项目级阶段表达下一处瓶颈。

稳定问题码至少包括：`generation_failed`、`voiceover_active`、`voiceover_failed`、`voiceover_stale`、`preview_active`、`preview_failed`、`preview_stale`、`review_stale`、`review_rejected`、`export_active`、`export_failed`、`export_stale`。

- [ ] **Step 5: 运行投影及既有审核导出测试**

Run: `PYTHONPATH=backend python3 -m pytest -q backend/tests/test_content_workflow.py backend/tests/test_batch_review_api.py backend/tests/test_batch_export_api.py`

Expected: PASS，且既有审核、导出契约无变化。

- [ ] **Step 6: 提交变体投影切片**

```bash
git add backend/app/content_workflow.py backend/app/batch_editing_api.py backend/tests/test_content_workflow.py
git commit -m "feat: project content variant lifecycle"
```

### Task 3: 暴露只读内容工作流接口

**Files:**
- Create: `backend/app/content_workflow_api.py`
- Create: `backend/tests/test_content_workflow_api.py`
- Modify: `backend/app/aigc_content_api.py:84-94`
- Modify: `backend/app/main.py:2688-2720`

**Interfaces:**
- Consumes: Task 2 的 `project_content_workflow(...)`、`BatchStore.load(project_id)`、`PreproductionStore.load(project_id)`。
- Produces: `load_aigc_content_state(root: Path, project_id: str) -> dict[str, Any]`；`create_content_workflow_router(data_dir, get_project, source_lock=None) -> APIRouter`；`GET /api/projects/{project_id}/content-workflow`。

- [ ] **Step 1: 写 GET、损坏状态和无副作用失败测试**

在 `backend/tests/test_content_workflow_api.py` 添加：

```python
def test_content_workflow_get_returns_saved_projection_after_reopen(tmp_path): ...
def test_content_workflow_returns_503_for_corrupt_aigc_or_batch_state(tmp_path): ...
def test_content_workflow_get_does_not_create_state_files(tmp_path): ...
```

关键断言：

```python
assert response.status_code == 200
assert response.json()["stage"] == "scripts_confirmed"
assert reopened.get(url).json() == response.json()
assert corrupt.status_code == 503
assert corrupt.json()["detail"]["code"] == "content_workflow_storage_invalid"
assert not unexpected_state_path.exists()
```

- [ ] **Step 2: 运行接口测试并确认失败**

Run: `PYTHONPATH=backend python3 -m pytest -q backend/tests/test_content_workflow_api.py`

Expected: FAIL，路由返回 404 或模块不存在。

- [ ] **Step 3: 暴露窄读取接口并实现 GET 路由**

把 AIGC `_load` 重命名为 `load_aigc_content_state` 并更新本模块引用。新增：

```python
def create_content_workflow_router(data_dir, get_project, source_lock=None) -> APIRouter: ...
```

GET 在共享项目锁中读取三份状态并投影；任何结构或 I/O 错误映射为 `503 content_workflow_storage_invalid`。不得捕获 `HTTPException` 后改写 404，也不得写文件。

- [ ] **Step 4: 在应用工厂注册路由**

在 AIGC 与批量路由附近调用 `app.include_router(create_content_workflow_router(...))`，复用同一个 `project_write_lock`。

- [ ] **Step 5: 运行接口和相关后端测试**

Run: `PYTHONPATH=backend python3 -m pytest -q backend/tests/test_content_workflow_api.py backend/tests/test_aigc_content_api.py backend/tests/test_batch_review_api.py backend/tests/test_batch_export_api.py`

Expected: PASS。

- [ ] **Step 6: 提交接口切片**

```bash
git add backend/app/content_workflow_api.py backend/app/aigc_content_api.py backend/app/main.py backend/tests/test_content_workflow_api.py
git commit -m "feat: expose content workflow projection"
```

### Task 4: 从服务端投影恢复四步工作区

**Files:**
- Create: `frontend/src/contentWorkflowApi.ts`
- Create: `frontend/src/ContentProductionWorkspace.test.tsx`
- Modify: `frontend/src/ContentProductionWorkspace.tsx:15-53`
- Modify: `frontend/src/ContentStudioApp.test.tsx`

**Interfaces:**
- Consumes: Task 3 的 GET 响应。
- Produces: `getContentWorkflow(projectId: string) -> Promise<ContentWorkflowProjection>`；工作区 `viewStep` 临时导航与服务端 `projection` 事实分离。

- [ ] **Step 1: 写刷新恢复、历史查看和错误失败测试**

在专用测试中 mock 三个大型子工作区，只测试编排行为：

```tsx
it("按服务端投影恢复选声步骤和活动批次", async () => { ... });
it("点击已完成步骤只改变当前视图而不改完成事实", async () => { ... });
it("投影读取失败时显示真实错误而不伪装为第一步", async () => { ... });
```

并更新 `ContentStudioApp.test.tsx` 默认 fetch mock，使 `/content-workflow` 返回 `draft` 投影；添加重新挂载后仍恢复 `currentStep: 3` 的集成断言。

- [ ] **Step 2: 运行前端测试并确认失败**

Run: `cd frontend && npm test -- src/ContentProductionWorkspace.test.tsx src/ContentStudioApp.test.tsx`

Expected: FAIL，原因是客户端模块或投影驱动步骤尚不存在。

- [ ] **Step 3: 实现投影客户端类型与 GET**

在 `contentWorkflowApi.ts` 定义规格中的 `ContentWorkflowStage`、`ContentWorkflowProjection`、逐变体与问题类型，并实现：

```ts
export function getContentWorkflow(projectId: string): Promise<ContentWorkflowProjection>;
```

沿用现有 `readApiError` 和连接失败文案。

- [ ] **Step 4: 将步骤事实切换到投影**

在 `ContentProductionWorkspace`：

- 用 `projection`、`projectionError`、`viewStep` 替换 `stage`、`aigcProgress`、`batchProgress` 作为步骤事实的用途。
- 项目首次加载时把 `viewStep` 初始化为 `projection.currentStep`。
- 后续投影回退时把不可访问的 `viewStep` 收紧到 `projection.currentStep`；用户主动返回旧步骤时不自动跳回。
- `StudioSteps.completed` 使用 `projection.completedSteps`。
- `BatchEditor.focusTaskId` 优先使用 `projection.activeBatchId`。
- “进入审核导出”只在投影至少为 `preview_ready` 时启用。
- 投影失败显示 `role="alert"`，不构造本地默认进度。

- [ ] **Step 5: 运行工作区测试**

Run: `cd frontend && npm test -- src/ContentProductionWorkspace.test.tsx src/ContentStudioApp.test.tsx`

Expected: PASS。

- [ ] **Step 6: 提交工作区恢复切片**

```bash
git add frontend/src/contentWorkflowApi.ts frontend/src/ContentProductionWorkspace.tsx frontend/src/ContentProductionWorkspace.test.tsx frontend/src/ContentStudioApp.test.tsx
git commit -m "feat: restore studio stage from workflow projection"
```

### Task 5: Mutation 后刷新投影并完成验收

**Files:**
- Modify: `frontend/src/AigcCreator.tsx`
- Modify: `frontend/src/AigcCreator.test.tsx`
- Modify: `frontend/src/BatchEditor.tsx`
- Modify: `frontend/src/BatchEditor.test.tsx`
- Modify: `frontend/src/ContentProductionWorkspace.tsx`
- Modify: `frontend/src/ContentProductionWorkspace.test.tsx`
- Modify: `.scratch/module-business-boundaries/issues/02-content-workflow-projection.md`

**Interfaces:**
- Consumes: Task 4 的投影读取函数和父工作区刷新回调。
- Produces: 两个子工作区的 `onPersistedChange?: () => void`；父工作区的稳定 `refreshWorkflow() -> Promise<void>`。

- [ ] **Step 1: 写 mutation 通知与回退失败测试**

添加：

```tsx
it("保存并确认脚本后通知刷新服务端阶段", async () => { ... });
it("保存变体或审核结果后通知刷新服务端阶段", async () => { ... });
it("投影回退时离开已不可访问的后续步骤", async () => { ... });
```

关键断言：AIGC 与 Batch 回调各调用一次；父工作区第二次投影从 `review_pending` 回到 `scripts_confirmed` 后，`aria-current="step"` 位于“选声制作”，审核步骤完成标记消失。

- [ ] **Step 2: 运行新测试并确认失败**

Run: `cd frontend && npm test -- src/AigcCreator.test.tsx src/BatchEditor.test.tsx src/ContentProductionWorkspace.test.tsx`

Expected: FAIL，组件尚未暴露或触发 `onPersistedChange`。

- [ ] **Step 3: 实现稳定投影刷新**

父工作区用 `useCallback` 和递增请求编号实现 `refreshWorkflow()`，忽略旧项目或较早请求结果。首次加载负责初始化视图；mutation 刷新只更新投影并在必要时回退不可访问步骤。

- [ ] **Step 4: 在 AIGC 保存边界通知父工作区**

仅在服务器成功并合并响应后调用 `onPersistedChange`：保存简报、准备请求时自动保存简报、生成候选、保存候选、确认候选、保存并确认、完成交接。不要在草稿输入、披露预览或失败请求时调用。

- [ ] **Step 5: 在批量制作保存边界通知父工作区**

仅在会影响投影的服务器事实成功合并后调用：创建任务/批次、配音生成回调、保存脚本/变体/字幕、预览提交或轮询刷新、审核、导出提交或轮询刷新、取消预览/导出。素材推荐与纯本地草稿不调用。

- [ ] **Step 6: 运行定向前端测试**

Run: `cd frontend && npm test -- src/AigcCreator.test.tsx src/BatchEditor.test.tsx src/ContentProductionWorkspace.test.tsx src/ContentStudioApp.test.tsx`

Expected: PASS。

- [ ] **Step 7: 运行完整验证**

Run: `cd frontend && npm test`

Expected: 全部通过。

Run: `PYTHONPATH=backend python3 -m pytest -q backend/tests`

Expected: 全部通过；已有明确 skip 可保留。

Run: `cd frontend && npm run build`

Expected: TypeScript 与 Vite 生产构建成功。

Run: `git diff --check`

Expected: 无输出。

- [ ] **Step 8: 关闭任务票并提交最终切片**

把 issue 02 改为 `Status: resolved`，在 `## Answer` 记录接口、阶段规则、刷新行为和实际验证结果。

```bash
git add frontend/src/AigcCreator.tsx frontend/src/AigcCreator.test.tsx frontend/src/BatchEditor.tsx frontend/src/BatchEditor.test.tsx frontend/src/ContentProductionWorkspace.tsx frontend/src/ContentProductionWorkspace.test.tsx .scratch/module-business-boundaries/issues/02-content-workflow-projection.md
git commit -m "feat: refresh content workflow after saved changes"
```

## Self-review

- **规格覆盖：** Task 1 覆盖上游与活动批次，Task 2 覆盖逐变体、混合聚合和失效，Task 3 覆盖只读接口与错误，Task 4 覆盖刷新恢复，Task 5 覆盖所有影响阶段的 mutation 和最终验收。
- **步骤粒度：** 每个测试、实现、验证和提交步骤都有单一可检查结果；未把任务 03 的模块拆分混入本计划。
- **类型一致性：** 后端与前端统一使用 `ContentWorkflowProjection` 字段名；`activeBatchId` 直接供 `focusTaskId` 使用；阶段枚举与已确认规格一致。
- **Review Focus：** 精确交接 key、版本失效、混合状态、损坏存储和旧项目兼容均有明确测试归属。
- **比例适当：** 计划只决定文件、公共签名、关键规则和断言；投影算法正文仍由实现者依据规格完成。
