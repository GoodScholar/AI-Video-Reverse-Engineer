# 02: 扩展共享运行契约并兼容迁移旧记录

**What to build:** 在现有持久运行基础设施上增加公共运行标识、输入快照、幂等、取消、恢复和日志契约，同时保留 `RemixRun`、`CanvasExecution`、`GenerationRun` 的领域语义。以一个现有媒体处理流程贯穿持久化、API、界面状态和重启恢复，证明扩展后旧数据和旧调用仍可用。

**Blocked by:** 01: 冻结五张验收矩阵与三条金标接缝

**Status:** resolved

## Scope

- 采用 expand–migrate–contract 的兼容策略：先增加公共字段和兼容读取，再迁移调用者，最终清理只在确认无调用后进行。
- 公共层只负责排队、取消请求、恢复、幂等和日志；领域运行继续拥有自己的状态和结果。
- 为旧运行记录提供幂等升级和失败回滚，迁移前保留可恢复备份。
- 在界面上明确排队、运行、取消中、已取消、失败、状态未知、部分成功和完成等可观察状态。

## Source reuse prerequisite

- 先阅读现有运行队列、任务恢复、媒体处理和审核链源码，列出可直接复用的状态、端点和持久化模式。
- 不引入参考项目的任务系统；若需要新增公共字段，记录其与现有状态的映射和兼容理由。

## Acceptance criteria

- [x] 一个现有媒体运行能通过公共运行标识创建、查询、取消、重试和恢复，并保留领域专属信息。
- [x] 同一幂等键不会重复启动同一计费或媒体工作；重试使用新的领域运行身份并关联原运行。
- [x] 服务重启和页面刷新后，界面显示持久化真实状态，不自动重提未知任务。
- [x] 迟到结果可保存但不自动采用，旧输入不能覆盖新修订。
- [x] 旧记录可读取或幂等迁移；迁移失败不覆盖原数据且可再次尝试。
- [x] 公共层没有引入新的项目、素材、时间线、审核或交付系统。

## Verification evidence

- 领域/API 合同测试覆盖创建、查询、幂等、取消、未知、迟到、重试和重启恢复。
- 使用旧格式夹具验证兼容读取、一次迁移、重复迁移和失败回滚。
- 界面行为记录证明公共状态和领域详情同时可见。

## Out of scope

- 在本票实现三类引擎完整业务。
- 删除旧字段或大规模一次性迁移所有调用者。

## Comments

### 2026-10-01 实施与证据

- 选定现有 Timeline 渲染作为纵向样板：它已具备 `runs[]` 历史、冻结 `snapshot`、私有源文件副本、创建/查询/取消端点、轮询 UI 和重启接缝。没有改造 source-segment 的覆盖式 `state.json` 重试路径。
- `backend/app/durable_runs.py` 扩展公共可观察词表为 `queued / running / cancel_requested / cancelled / failed / unknown / partial_success / completed`；保留既有 `cancel()` 的立即取消兼容语义，新增仅供 Timeline 使用的 `request_cancel()/acknowledge_cancel()`。Timeline 本地运行不伪造 `unknown` 或 `partial_success`。
- `TimelineStore` 以 expand–migrate–contract 方式将旧记录升级到 `runContractVersion: 1`：原字段 `id / snapshot / sources / status` 均保留；补充 `commonRunId`（统一规则 `timeline:{id}`）、`domainKind`、冻结输入/指纹、幂等键、`retryOf`、attempt、恢复信息和持久日志。首次迁移原子保存 `state.json.run-contract-v1.bak`；替换失败时原状态不变、备份留存，下一次加载可重试，已升级记录不会重复写入。
- Timeline API 保持旧 `POST /runs` 与取消调用可用，同时增加可选幂等键、`GET /runs/{id}` 和专用 `POST /runs/{id}/retry`。同键同冻结输入返回同一运行且不重复入队；测试以不同 `format` 实际改变输入验证冲突。重试复制原运行私有冻结源，生成新领域/公共身份并记录 `retryOf`，其重试请求自身幂等。
- 运行中取消先持久化为 `cancel_requested`，worker 确认后收敛到 `cancelled`；队列中运行直接取消。迟到或旧 attempt 的产物保留在运行私有目录并记录 `retained_not_adopted`，不发布为输出。重启将 queued/running 同一运行以新 attempt 续跑，`cancel_requested` 重启后收敛为已取消。当前修订之外的完成预览仅作为“历史预览”展示，不作为当前预览。
- 前端 `timelineApi`、`TimelineEditor` 增加八态中文、公共运行 ID、领域、attempt、关联重试与“迟到结果已保留，未采用”提示；重试按钮调用专用 endpoint，不再用无关联创建请求。
- 源码复用/适配：仅复用项目内 `backend/app/durable_runs.py`、`timeline.py`、`timeline_api.py`、`render_inputs.py`、`frontend/src/timelineApi.ts`、`TimelineEditor.tsx` 的既有队列、持久化、冻结输入、路由与轮询结构；未引入外部任务系统，因此无新增第三方源码或许可证项。适配范围限于公共运行字段、兼容迁移、两阶段取消/恢复与 UI 可观察性。
- TDD 红灯已实际确认：旧运行迁移缺少契约版本；带幂等键创建返回 422；运行中取消停在错误状态；专用 retry 返回 404；重启将运行标为 failed；前端缺少公共运行详情。收口时又以失败用例确认前端重试键随点击变化会破坏刷新后的幂等、取消后渲染器抛错会把迟到文件留在通用 staging 名称；随后均以稳定重试键与 attempt 私有文件转绿。
- 绿灯证据：相关后端合同、真实 FFmpeg 与备份迁移测试 → `81 passed`；前端 Timeline 合同测试 → `35 passed`；`npm run build` 通过，保留既有大 bundle 提示。完整回归最终结果为后端 `1585 passed, 8 skipped`、前端 `64 files / 639 passed`。完整后端首次运行曾由本票的 `os.replace` 过早绑定暴露 1 个既有原子保存兼容回归；修复为默认运行时解析、保留显式失败注入后，既有原子保存测试和后续全量回归均通过。
- 双轴首次审查发现并以回归测试闭合：公共 `commonRunId` 现在可直接查询、取消和重试，同时保留旧领域 ID；每个 attempt 从渲染开始使用独立 staging，旧 attempt 的失败或迟到文件不能覆盖/失败化新 attempt；重试端点恢复同项目活动任务限制；迁移前若原文变化会原子刷新可恢复备份，项目备份在冻结 inventory 前完成迁移；浏览器为每次明确操作生成幂等键，未收到持久化响应时跨刷新复用，确认后才清除并允许下一次明确运行。
- 该票据仅完成 A07–A11、A14 的 Timeline 合同切片自动化证据；五张矩阵、真实 Provider/媒体金标和整个产品仍保持阻塞，未在验收资产中改写为通过。
