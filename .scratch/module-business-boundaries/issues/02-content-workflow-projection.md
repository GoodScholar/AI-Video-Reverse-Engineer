# 02: 建立内容制作阶段投影

Status: resolved
Type: task
Blocked by: 01

## What to build

由后端根据已保存的创作简报、脚本确认、短视频变体、配音、预览和审核记录派生唯一的内容制作阶段投影，供默认内容工作室渲染步骤状态。不得新增可与真实对象漂移的手工阶段字段。

## Acceptance criteria

- [x] 阶段使用 `draft → brief_ready → scripts_confirmed → voice_ready → preview_ready → review_pending → approved|rejected → delivered` 的用户可见语义。
- [x] 刷新页面和跨页面导航从服务端已保存事实恢复阶段，不依赖 React 局部步骤状态作为事实来源。
- [x] 修改脚本、素材、配音、字幕、时间线或画布后，投影准确回退并说明失效对象。
- [x] 一条短视频变体的失败或退回不改变其他变体的审核状态。
- [x] 现有草稿、版本校验、预览和导出契约保持兼容。

## Answer

- 新增后端只读内容工作流投影及 `/api/projects/{project_id}/content-workflow`，阶段、活动批次、变体新鲜度、问题与聚合计数均由已保存事实派生。
- 内容工作室首次打开、刷新和跨页面返回时恢复服务端阶段；AIGC、配音、字幕、时间线、画布、预览、审核和导出等持久化变化会重新读取投影，并在事实回退时收回过时步骤。
- 变体级审核与失败状态独立聚合，旧审核根据内容、配音、字幕、画布和预览版本关系自动失效。
- 验证：前端 39 个测试文件、383 个测试通过；后端 1283 个测试通过、10 个跳过；TypeScript 与 Vite 生产构建通过；`git diff --check` 通过。

## Comments

- 2026-09-27：已确认 `docs/superpowers/specs/2026-09-27-content-workflow-projection-design.md`，按 `docs/superpowers/plans/2026-09-27-content-workflow-projection.md` 实施。
- 2026-09-27：实现与全量回归完成，任务关闭。
