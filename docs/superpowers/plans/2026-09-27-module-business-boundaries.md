# 内容项目与模块业务边界收口 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 统一顶层内容项目、两条制作工作流、七类模块所有权和旧版兼容入口，使后续实现不再沿冲突规格继续扩展。

**Architecture:** 保留现有单体、HTTP 接口和状态文件，只调整领域词汇、不可逆边界决策、历史任务分流和用户可见兼容标识。更大的阶段投影、批量制作模块与运行基础设施提取拆成后续独立任务。

**Tech Stack:** Markdown、React 18、TypeScript、Vitest、FastAPI、pytest。

**Spec:** `.scratch/module-business-boundaries/spec.md`

## Global Constraints

- 最终 AI 视频生成在外部工具完成，新提交继续返回 `410 final_generation_disabled`。
- 不删除历史运行记录、历史状态文件或离线工作流导出。
- 顶层工作单元统一称为“内容项目”；参考复刻与商品视频制作是项目内工作流。
- 不新增依赖、存储迁移、通用工作流引擎或微服务。
- 兼容工作台只维护历史复刻流程，不承接新产品功能。

## Review Focus

- 历史总规格仍把应用内 ComfyUI Queue 标成待实现，误导后续开发。
- “复刻项目”仍被当作全站项目类型，而非内容项目中的参考复刻工作流。
- 参考素材检测片段与人工制作镜头继续共用含义不明的“镜头”。
- 兼容入口看起来像与默认内容工作室同等的新建入口。
- 文档变更意外改变现有 `410` 边界或删除历史读取能力。

---

### Task 1: 领域词汇与架构决策

**Files:**
- Modify: `CONTEXT.md`
- Create: `docs/adr/0009-content-project-and-workflow-boundaries.md`
- Create: `docs/research/module-business-logic-benchmark-2026-09-27.md`

**Interfaces:**
- Consumes: ADR 0007 的外部最终生成边界、ADR 0008 的本地时间线边界。
- Produces: 内容项目、参考镜头段、制作镜头、渲染运行、审核版本的规范定义；七类模块所有权决策。

- [ ] 更新领域词汇，只定义业务概念，不写实现细节。
- [ ] 新增 ADR 0009，记录内容项目聚合、工作流边界和运行基础设施不拥有业务决定。
- [ ] 核对研究报告的推论与 ADR、领域词汇没有冲突。
- [ ] 使用 `rg` 检查新增规范术语在权威文档中的唯一含义。

### Task 2: 历史规格与任务分流

**Files:**
- Modify: `.scratch/ai-video-reverse-engineer/spec.md`
- Modify: `.scratch/ai-video-reverse-engineer/issues/08-generate-wan-i2v-reproduction-package.md`
- Modify: `.scratch/ai-video-reverse-engineer/issues/09-override-advanced-settings-and-track-workflow-freshness.md`
- Modify: `.scratch/ai-video-reverse-engineer/issues/10-check-environment-and-send-to-local-comfyui.md`
- Modify: `.scratch/ai-video-reverse-engineer/issues/11-support-wan-fun-camera-end-to-end.md`
- Modify: `.scratch/ai-video-reverse-engineer/issues/13-verify-compatibility-and-mvp-targets.md`
- Modify: `.scratch/ai-video-reverse-engineer/issues/14-generate-local-depth-control-and-wan-fun-control.md`
- Modify: `.scratch/ai-video-reverse-engineer/issues/15-validate-minimax-and-seedance-generation-providers.md`
- Modify: `README.md`
- Modify: `design.md`

**Interfaces:**
- Consumes: Task 1 的领域词汇和 ADR 0009。
- Produces: 不再可领取的冲突任务、统一的产品与兼容入口说明。

- [ ] 将历史总规格标为不再直接实施，并列出仍具权威性的增量规格。
- [ ] 将包含应用内最终生成提交的旧任务标为 `wontfix`，记录已实现部分及替代规格。
- [ ] 更新 README 与设计规范，使默认内容工作室和旧版兼容入口的定位一致。
- [ ] 运行 `rg`，确认没有处于 `ready-for-agent` 的直接生成提交任务。

### Task 3: 兼容入口的用户可见标识

**Files:**
- Modify: `frontend/src/ContentStudioApp.test.tsx`
- Modify: `frontend/src/ContentStudioApp.tsx`
- Modify: `.scratch/module-business-boundaries/issues/01-align-domain-and-compatibility.md`

**Interfaces:**
- Consumes: Task 2 的统一兼容入口措辞。
- Produces: 指向 `?workspace=legacy` 的“旧版复刻工作台（兼容）”链接。

- [ ] 在 `ContentStudioApp.test.tsx` 增加可见链接及目标地址测试，运行并确认因旧文案失败。
- [ ] 最小修改 `ContentStudioApp.tsx` 的链接文案，不改变导航或数据行为。
- [ ] 运行前端定向测试并确认通过。
- [ ] 运行 `backend/tests/test_preproduction_boundary.py`，确认最终生成仍返回 410。
- [ ] 运行完整前端测试、完整后端测试和前端生产构建。
- [ ] 将任务票状态更新为 `resolved` 并记录验证结果。

## Self-review

- 规格覆盖：术语、模块所有权、历史生成冲突和兼容入口均有对应任务；大规模模块拆分明确不在本轮。
- 类型一致性：本轮唯一运行时接口是现有 `?workspace=legacy` URL，没有新增数据类型。
- 比例适当：计划不复制研究报告或历史规格，只记录需要修改的文件和验证方式。
