# 短视频变体版本有效性 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 由 `batch_production` 统一派生配音、预览、审核和交付的新鲜度，使命令、内容工作流投影与批量 HTTP 适配器使用同一业务事实。

**Architecture:** 在现有深模块中新增不可变生命周期视图和目的明确的复用判定；不新增持久化状态或独立服务。`content_workflow` 保留阶段聚合与用户问题文案，`batch_editing_api` 保留文件、队列和 HTTP 适配，但二者不再自行解释版本关系。

**Tech Stack:** Python 3、FastAPI、pytest。

**Spec:** `.scratch/module-business-boundaries/issues/05-unify-variant-validity.md`

## Global Constraints

- 保持现有 HTTP 响应、错误码、状态文件结构和历史记录兼容。
- 不新增依赖、数据库、事件总线、工作流引擎或迁移步骤。
- 配音有效性只依赖脚本内容与实际生成的音色；画面、输出设置或字幕修改不使配音本身失效。
- 预览必须匹配当前三类修订、冻结画面输入和字幕；审核必须绑定当前具体预览。
- 导出复用必须匹配当前变体版本与具体预览 ID，但同一预览重新批准不强制重复渲染。
- 最新预览处于运行、失败或取消状态时，不绕过它恢复更早的审核与交付。
- 历史运行、审核和导出只追加保留，不因失效删除或改写。

## Review Focus

- 没有配音记录的历史变体仍可预览、审核和交付；Task 1、Task 3 的无配音测试覆盖。
- 旧状态缺失 `contentRevision` 或 `subtitleRevision` 时继续按 `0` 兼容；Task 1 的兼容记录测试覆盖。
- 画面或字幕编辑后配音仍可用，但预览、审核与交付必须回退；Task 1、Task 3 的因果失效测试覆盖。
- 同一编辑版本产生新预览后，旧审核与旧导出不得授权新产物；Task 1、Task 2、Task 3 的预览身份测试覆盖。
- 同一预览重复批准不得产生无意义的重复导出；Task 1、Task 2、Task 3 的重新批准测试覆盖。

---

### Task 1: 建立权威变体生命周期视图

**Files:**
- Modify: `backend/app/batch_production.py`
- Modify: `backend/tests/test_batch_production.py`

**Interfaces:**
- Consumes: 现有 `current_version(task)`、冻结编辑快照、运行/审核/导出历史。
- Produces: `LifecycleFact`、`VariantLifecycle`、`inspect_variant(task: dict[str, Any]) -> VariantLifecycle`；`review_status(task)` 作为兼容包装。

- [x] **Step 1: 写生命周期视图失败测试**

新增以下测试并使用字面量期望值：

```python
def test_inspection_keeps_completed_voice_current_across_non_voice_edits(): ...
def test_inspection_stales_review_when_a_new_preview_replaces_the_reviewed_artifact(): ...
def test_inspection_keeps_delivery_current_after_the_same_preview_is_reapproved(): ...
def test_inspection_reads_missing_legacy_revision_fields_as_zero(): ...
```

- [x] **Step 2: 运行测试并确认失败**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_batch_production.py`

Expected: FAIL，原因是 `inspect_variant` 尚不存在或旧审核/交付身份语义不满足断言。

- [x] **Step 3: 实现最小生命周期视图**

在 `backend/app/batch_production.py` 定义不可变事实对象，分别返回 `voice`、`preview`、`review`、`delivery` 的状态、原因和当前记录。配音按脚本版本判断；预览按最新运行、三类修订及冻结输入判断；审核按最新审核与当前预览 ID 判断；交付按当前版本和预览 ID 判断，不要求审核记录 ID 完全一致。

- [x] **Step 4: 运行定向测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_batch_production.py`

Expected: PASS。

- [x] **Step 5: 提交生命周期视图**

```bash
git add backend/app/batch_production.py backend/tests/test_batch_production.py
git commit -m "refactor: centralize variant lifecycle facts"
```

### Task 2: 让审核、导出和批量预览消费权威规则

**Files:**
- Modify: `backend/app/batch_production.py`
- Modify: `backend/app/batch_editing_api.py`
- Modify: `backend/tests/test_batch_production.py`
- Modify: `backend/tests/test_batch_bulk_api.py`

**Interfaces:**
- Consumes: Task 1 的 `inspect_variant(...)`。
- Produces: `reusable_preview(task: dict[str, Any]) -> dict[str, Any] | None`；审核只接受生命周期视图中的当前预览；导出仅复用绑定同一预览的运行。

- [x] **Step 1: 写命令与复用失败测试**

新增：

```python
def test_export_does_not_reuse_a_delivery_from_an_older_preview(): ...
def test_export_reuses_delivery_after_same_preview_is_reapproved(): ...
def test_reusable_preview_preserves_legacy_revision_defaults(): ...
```

在批量 API 测试中保留已有完成预览复用断言，确保迁移后不重复提交队列。

- [x] **Step 2: 运行测试并确认失败**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_batch_production.py backend/tests/test_batch_bulk_api.py`

Expected: FAIL，旧导出只比较三类修订，且 `reusable_preview` 尚不存在。

- [x] **Step 3: 收拢命令门禁和适配器判断**

`review_current_version` 使用当前预览身份；`_approved_export_context` 使用当前审核与预览，并仅复用 `previewRunId` 相同的活动或完成导出。批量预览路由调用 `reusable_preview`，不再直接比较 revision 字段。

- [x] **Step 4: 运行审核、导出和批量定向测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_batch_production.py backend/tests/test_batch_bulk_api.py backend/tests/test_batch_review_api.py backend/tests/test_batch_export_api.py`

Expected: PASS。

- [x] **Step 5: 提交命令迁移**

```bash
git add backend/app/batch_production.py backend/app/batch_editing_api.py backend/tests/test_batch_production.py backend/tests/test_batch_bulk_api.py
git commit -m "fix: bind variant approvals and exports to previews"
```

### Task 3: 让内容工作流只聚合生命周期事实

**Files:**
- Modify: `backend/app/content_workflow.py`
- Modify: `backend/tests/test_content_workflow.py`

**Interfaces:**
- Consumes: Task 1 的 `inspect_variant(...)` 及稳定原因码。
- Produces: 现有 `project_content_workflow(...)` 响应结构；阶段只反映当前有效链路。

- [x] **Step 1: 写阶段回退失败测试**

新增：

```python
def test_projection_keeps_voice_ready_after_timeline_or_subtitle_edits(): ...
def test_projection_requires_review_for_a_new_preview_of_the_same_version(): ...
def test_projection_keeps_delivery_after_the_same_preview_is_reapproved(): ...
```

- [x] **Step 2: 运行测试并确认失败**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_content_workflow.py`

Expected: FAIL，旧投影按三类 revision 判断配音，并要求导出绑定最新审核 ID。

- [x] **Step 3: 删除投影内重复的新鲜度算法**

移除 `_voice_current`、`_current_preview`、`_current_export`，由 `_variant_projection` 消费 `inspect_variant(task)`；阶段、问题文案和批次聚合仍留在 `content_workflow`。

- [x] **Step 4: 运行内容工作流与接口测试**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_content_workflow.py backend/tests/test_content_workflow_api.py`

Expected: PASS，响应结构不变。

- [x] **Step 5: 提交投影迁移**

```bash
git add backend/app/content_workflow.py backend/tests/test_content_workflow.py
git commit -m "refactor: derive workflow from variant lifecycle"
```

### Task 4: 完成验证、审查和票据记录

**Files:**
- Modify: `.scratch/module-business-boundaries/issues/05-unify-variant-validity.md`
- Modify: `CONTEXT.md`

**Interfaces:**
- Consumes: Tasks 1–3 的行为和测试证据。
- Produces: 已关闭 Issue 05、领域词汇与可复核验证记录。

- [x] **Step 1: 运行完整验证**

Run: `PYTHONPATH=backend .venv/bin/pytest -q backend/tests`

Expected: PASS，0 failures。

Run: `cd frontend && npm test`

Expected: PASS，0 failures。

Run: `cd frontend && npm run build`

Expected: PASS，exit 0。

Run: `.venv/bin/python -m py_compile backend/app/batch_production.py backend/app/content_workflow.py backend/app/batch_editing_api.py && git diff --check`

Expected: PASS，exit 0。

- [x] **Step 2: 按规格与代码标准审查完整分支**

使用 `/code-review` 检查固定基线 `585b86a` 到当前 HEAD；Critical/Important 发现必须在一次 TDD 修复轮中解决。

- [x] **Step 3: 更新任务结果并提交**

将 Issue 05 标为 `resolved`，勾选验收标准，在 `## Answer` 记录实现、验证和审查结果；只提交本任务直接相关文档 hunk。

```bash
git add .scratch/module-business-boundaries/issues/05-unify-variant-validity.md docs/superpowers/plans/2026-09-27-variant-validity.md
git commit -m "docs: close variant validity task"
```

## Self-review

- 规格覆盖：配音因果失效、预览身份、审核身份、导出复用、阶段回退、兼容数据和适配器边界均有任务与测试。
- 步骤粒度：每个实现任务均包含独立 RED、GREEN、定向验证和提交。
- 类型一致性：所有调用方只消费 `inspect_variant` 与 `reusable_preview`，现有 HTTP 类型不变。
- Review Focus：五类高风险输入均绑定到具体测试。
- 比例：计划只锁定接口、行为和验证，不复制实现函数体。
