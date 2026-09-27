# 02: 建立内容制作阶段投影

Status: claimed
Type: task
Blocked by: 01

## What to build

由后端根据已保存的创作简报、脚本确认、短视频变体、配音、预览和审核记录派生唯一的内容制作阶段投影，供默认内容工作室渲染步骤状态。不得新增可与真实对象漂移的手工阶段字段。

## Acceptance criteria

- [ ] 阶段使用 `draft → brief_ready → scripts_confirmed → voice_ready → preview_ready → review_pending → approved|rejected → delivered` 的用户可见语义。
- [ ] 刷新页面和跨页面导航从服务端已保存事实恢复阶段，不依赖 React 局部步骤状态作为事实来源。
- [ ] 修改脚本、素材、配音、字幕、时间线或画布后，投影准确回退并说明失效对象。
- [ ] 一条短视频变体的失败或退回不改变其他变体的审核状态。
- [ ] 现有草稿、版本校验、预览和导出契约保持兼容。

## Comments

- 2026-09-27：已确认 `docs/superpowers/specs/2026-09-27-content-workflow-projection-design.md`，按 `docs/superpowers/plans/2026-09-27-content-workflow-projection.md` 实施。
