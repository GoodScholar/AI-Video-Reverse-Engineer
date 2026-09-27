# 01: 统一内容项目边界与兼容入口

Status: resolved
Type: task
Blocked by: none

## What to build

按 [规格](../spec.md) 统一领域词汇、项目与工作流边界、历史生成任务状态和旧版复刻工作台标识；保留现有数据与 HTTP 契约。

## Acceptance criteria

- [x] `CONTEXT.md` 和新 ADR 明确顶层内容项目及七类模块所有权。
- [x] 历史总规格及冲突的 ComfyUI/云生成任务不再显示为待实现。
- [x] README、设计规范和默认界面的兼容入口使用“旧版复刻工作台（兼容）”。
- [x] 最终生成提交仍返回 `410 final_generation_disabled`。
- [x] 前端相关测试、后端边界测试与生产构建通过。

## Comments

2026-09-27 已领取。只做边界与公开契约收口，不进行存储迁移或大文件拆分。

2026-09-27 已完成。前端 376 项通过；后端 1271 项通过、10 项跳过；最终生成边界专项 6 项通过；生产构建通过。
