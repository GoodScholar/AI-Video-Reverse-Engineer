# 微信文章 OpenStory 候选提取与固定提交融合深审

审计日期：2026-09-28；产品定位修订：2026-09-29。二手入口是[《OpenStory：开源 AI 视频制作平台》](https://mp.weixin.qq.com/s/Lqq8_kAevF85q6km_3xflQ)（AI小展厅，2026-08-26）；它只用于确认文章**说了什么**，不作为能力、许可或维护状态的证明。实际源码固定为 [`openstory-so/openstory@3ff7e58c1afba853e3db201d98f85798ef7e5891`](https://github.com/openstory-so/openstory/tree/3ff7e58c1afba853e3db201d98f85798ef7e5891)，提交时间 2026-09-28T20:33:19+10:00，说明 `fix(#1744): call theatre stitcher units clips, not scenes (#1861)`。以下结论是这一提交的源码静态审计，不是浮动 `main` 的持续保证。

## 文章完整候选与作者结论

文章的项目清单**只有 1 项**：OpenStory；给出[项目官网](https://openstory.so/)和[官方 GitHub](https://github.com/openstory-so/openstory)。Fal.ai、OpenRouter、ModelSchemas API、TanStack AI、Cloudflare D1/R2/Workers/Workflows/Durable Objects、Drizzle、Better Auth、Bun、Vite、React、shadcn 是文章提及的依赖/平台，**不是额外开源候选**；不能因此把文章算作多个项目。作者把它概括为“剧本分析→场景/分镜→图像、视频、音频→风格连续性→状态推送→最终视频”的端到端开源平台，特别推介 Cloudflare 持久工作流、D1/R2、API Key 异步提交与实时事件；这只是文章的主张。[文章原文](https://mp.weixin.qq.com/s/Lqq8_kAevF85q6km_3xflQ)。

| 文章说法 | 固定提交第一方核验 | 判定 |
| --- | --- | --- |
| 剧本结构化、角色/场景/风格连续、生成镜头 | [README](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/README.md)、[`AnalyzeScriptWorkflow`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/sequences/server/workflows/analyze-script-workflow.ts)、[`reconcileSceneTags`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/sequences/tag-reconcile.ts) | 源码有链路；效果和一致性质量未实测。 |
| `POST` 异步创建返回 `sequenceId + workflowRunId` | 实际 [`POST /api/v1/sequences`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/routes/api/v1/sequences.ts) 返回 `202`，`sequences[]` 各有 `id`、`workflowRunId`、`statusUrl`；可 `?wait=` 长轮询，[API 文档](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/docs/developer-guide/public-api.md)。 | 基本属实，但字段不是单一顶层 `sequenceId`。 |
| SSE 为 AG-UI 标准的 `STATE_SNAPSHOT/STATE_DELTA/RUN_*` 等 | 固定提交的 [`RealtimeChannel`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/realtime/realtime-channel.do.ts)明定自有 `{id,event,channel,data}` 与 `{type:connected/ping}`；[`/api/realtime`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/routes/api/realtime.ts)合并通道。全文检索上述 AG-UI 事件名及 `AG-UI` 未命中 `src/` 与 `docs/`。 | **不应沿用文章的 AG-UI 兼容说法**；只能称自定义 SSE 进度事件。 |
| `bun dev` 即可本地完整产出视频、Bun ≥1.3.0 | [README](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/README.md)现写 Bun ≥1.3.9，基础本地栈由 Miniflare 提供，但 AI 生成需要 Fal/OpenRouter Key；[`SequenceExportWorkflow`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/sequences/server/workflows/sequence-export-workflow.ts)说明非生产导出还需 `bun dev:all`/本地 export 服务，否则缺 Container binding 而失败。 | “无需 Docker/Cloudflare 账号启动基础本地栈”与“零配置完成 AI 生成和 MP4 导出”不是一回事。 |
| `src/lib/ai`、`src/lib/workflows` 等代码路径 | 固定提交按 `src/sequences/`、`src/shots/`、`src/models/`、`src/platform/` 等领域拆分；当前 [README 的 Project Structure](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/README.md)自身也仍列旧 `src/lib` 树。 | 文章和 README 的目录介绍均不可作为定位源码的依据。 |
| 多人共享素材工作区 | [README](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/README.md)“Team workspaces (coming soon)”与总述并存；[`styles` schema](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/db/schema/libraries.ts)已有 `teamId`，[`generated_assets`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/db/schema/generated-assets.ts)也按团队存。 | 团队范围数据已实现；完整协作工作区成熟度不可由文章或 README 一句确认。 |

## 结论先行

**总体标签：只参考设计；局部需求若立项则在现有架构重新实现。** OpenStory 是以脚本到模型生成链为中心的端到端 AI 视频生产平台；本项目则是同时承载参考复刻、AI 内容创作与批量混剪、项目素材、制作镜头与关系画布、Timeline、审核和交付的本地 AI 视频创作与生产工作台。当前应用不把最终 AI 视频生成作为内置执行链，但这只是执行边界，不是产品总定位。[本项目领域边界](../../CONTEXT.md)、[ADR 0009](../adr/0009-content-project-and-workflow-boundaries.md)。可借鉴的增量是**生成依赖计划的同源读模型、输入快照/内容哈希的过期说明、在途 claim 与迟到产物分歧的保护、费用前置与输出来源记录**；不把 Cloudflare 工作流、OpenStory `Sequence`、R2 URL、团队素材库、积分系统或 MediaBunny renderer 搬进当前项目。它**不补**当前共享 `AssetLibrary` 的跨项目检索、外部图库权利链或本地长视频语义片段索引，不能取代 [Media Search Agent 深审](./media-search-agent-current-project-integration-audit-2026-09-28.md) 的候选主线。

维护快照：审计时仓库非归档、`HEAD` 提交在 2026-09-28，仓库确有大量单元/端到端测试（提交树中 `.test`/`.spec` 文件约 498 个）；这只是活跃度和测试资产快照，不等于本次测试通过或生产稳定。[固定提交](https://github.com/openstory-so/openstory/commit/3ff7e58c1afba853e3db201d98f85798ef7e5891)、[测试脚本](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/package.json)。

# 综合融合决策（25 项）

## 1. 参考项目核心能力

从脚本建立 `Sequence → Scene → Shot → Frame`、角色/地点/元素 bible、提示词/静帧/动态/对白/音乐、多人团队范围素材、预览和导出；[`AnalyzeScriptWorkflow`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/sequences/server/workflows/analyze-script-workflow.ts)协调 Cloudflare 子工作流。模型生成是上游产品核心，不是本项目当前要内置的能力。

## 2. 当前项目核心能力

当前项目以 `ContentProject` 承载并列工作流：[`aigc_content_api`](../../backend/app/aigc_content_api.py)与[`batch_production`](../../backend/app/batch_production.py)支撑创作简报、脚本候选、短视频变体和批量混剪；[`WorkflowCanvas`](../../frontend/src/WorkflowCanvas.tsx)与[`PreproductionWorkspace`](../../frontend/src/PreproductionWorkspace.tsx)组织场景、制作镜头、流程节点和镜头关系；[`ProjectAssets`](../../backend/app/project_assets.py)提供稳定 `assetId`、受控文件安装/引用与删除边界；[`durable_runs`](../../backend/app/durable_runs.py)保留运行输入/恢复，[`timeline_render`](../../backend/app/timeline_render.py)执行现有 FFmpeg 成片。参考镜头段、制作镜头和 Timeline 片段保持不同身份。[`CONTEXT.md`](../../CONTEXT.md)、[ADR 0010](../adr/0010-shot-workflow-complementary-views.md)。

## 3. 两个项目定位差异

OpenStory 自己持有从脚本、模型生成到团队库、账务、云存储和导出的整条平台链；当前产品以内容项目为中心，并列承载参考复刻、AI 内容创作/批量混剪和镜头编排/交付，最终 AI 视频生成当前外置。两者在 Storyboard、镜头版本、素材与生成任务上有交集，但上游 `sequenceId/shotId/frameId` 不是当前 `projectId/assetId/制作镜头 ID` 的替代主键；上游 R2 输出也不能绕过项目素材导入、来源/权利快照和用户审核。[上游 schema](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/db/schema/frames.ts)、[本地登记表](./reference-project-integration-register.md)。

## 4. 功能模块对比

| 模块 | 上游源码事实 | 本项目唯一落点 | 标签 |
| --- | --- | --- | --- |
| 剧本→场景/镜头 | [`AnalyzeScriptWorkflow`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/sequences/server/workflows/analyze-script-workflow.ts)、[`scene-split-workflow`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/sequences/server/workflows/scene-split-workflow.ts) | 现有制作镜头候选/人工确认；与 Toonflow 的 Agent 分镜重叠 | 只参考设计 |
| 连续性 | [`tag-reconcile`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/sequences/tag-reconcile.ts)按名字/词元回填角色、地点、元素 | 受控候选标签和可解释证据；不自动覆盖用户镜头 | 重新实现 |
| 生成计划与过期 | [`generation-plan`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/sequences/generation-plan.ts)、[哈希架构说明](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/docs/architecture/workflow-snapshots-and-content-hash-staleness.md) | 在现有 `durable_runs`/版本对象上说明缺项和失效原因 | 重新实现 |
| API/实时 | [REST `202`/长轮询](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/docs/developer-guide/public-api.md)、[自有 DO SSE](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/realtime/realtime-channel.do.ts) | 现有 FastAPI/React；若开放 API，独立做 scope/事件契约 | 只参考设计 |
| 生成/账务/存储/导出 | [Fal/OpenRouter 等适配](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/models/server/create-adapter.ts)、[预留积分](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/billing/server/preflight.ts)、[容器导出](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/sequences/server/workflows/sequence-export-workflow.ts) | 当前供应商选择/外发确认、FFmpeg、审核；不加第二执行/计费/渲染链 | 不引入 |

## 5. 业务逻辑对比

上游以制作/生成工序为中心，`Sequence` 有自己的 Scene/Shot/Frame 与选择指针；本项目既有“参考镜头段/制作镜头/流程节点/Timeline”细分，不可把上游镜头转为项目参考源的事实。上游 [`Scene` schema](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/db/schema/scenes.ts)与[`Shot` schema](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/db/schema/shots.ts)支持软删/顺序；本项目继续由现有业务对象持真相。

## 6. UI / UX 对比

上游有脚本、场景、角色、地点、音乐、Theatre、模型目录和开发者设置页面（[路由树](https://github.com/openstory-so/openstory/tree/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/routes/_app/sequences)）；本项目仅可借“欠缺何种产物/何处过期/如何重新生成”的解释方式，不复制全站流程与视觉组件。新增界面必须沿用本地 [`design.md`](../../design.md) 与现有 [`AssetManager.tsx`](../../frontend/src/AssetManager.tsx)。

## 7. 技术架构对比

上游为 Bun + React 19/TanStack Start + Drizzle/D1 + Cloudflare Workflows/DO/R2 + Better Auth + Cloudflare Container/MediaBunny，[README](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/README.md)、[`package.json`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/package.json)。本项目为 Python FastAPI/React 与本地持久化/FFmpeg；大部分上游服务函数无法不改架构而移植。

## 8. 数据模型对比

[`scenes`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/db/schema/scenes.ts)、[`shots`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/db/schema/shots.ts)、[`frames`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/db/schema/frames.ts)及版本表是生成实体；[`generated_assets`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/db/schema/generated-assets.ts)又是独立团队级模型运行产物。因此上游也有多种生成产物身份，不能误称其提供本项目统一 `AssetLibrary → ProjectAssets(assetId)` 契约。后者仍需本项目自建。

## 9. 当前项目已有优势

项目素材安装、删除时的引用阻止/回滚、源与制作镜头分离、现有 FFmpeg 结果和审核版本边界已经在位；上游整套账户/团队/积分/存储替换这些能力不是增量。[`ProjectAssets`](../../backend/app/project_assets.py)、[累计登记表](./reference-project-integration-register.md)。

## 10. 参考项目优势

真正值得比较的是：[`planUnits`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/sequences/generation-plan.ts)将缺失、过期、运行中、阻塞和级联依赖统一为一个读模型；[`input-hash`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/shots/input-hash.ts)按产物的最小输入 DTO 规范化 SHA-256；[`sheet-claims`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/cast/server/db/sheet-claims.ts)把旧运行的迟到结果停在分歧版本。它们为“可解释失效与并发竞争”提供源码级案例，不证明当前系统也应照搬其实体和持久化方式。

## 11. 功能映射表

| 上游固定 SHA 文件 | 当前目标与去重关系 | 处理标签 |
| --- | --- | --- |
| [`src/sequences/generation-plan.ts`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/sequences/generation-plan.ts) + [`server/generation-plan.ts`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/sequences/server/generation-plan.ts) | 当前镜头准备状态/运行快照上建立同源“欠缺/过期”建议；不建第二 `Sequence` | 重新实现 |
| [`src/shots/input-hash.ts`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/shots/input-hash.ts) + [`regenerate-shots-snapshot.ts`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/shots/server/workflows/regenerate-shots-snapshot.ts) | 当前 `durable_runs`/制作镜头版本的输入指纹；与 MPT 配音指纹、现有过期规则逐字段去重 | 只参考设计 |
| [`src/cast/server/db/sheet-claims.ts`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/cast/server/db/sheet-claims.ts) + [`src/stills/server/db/frame-variants.ts`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/stills/server/db/frame-variants.ts) | 现有结果采纳/审核的 CAS 与迟到结果隔离；不引入 D1 表 | 重新实现 |
| [`src/platform/server/api-v1`](https://github.com/openstory-so/openstory/tree/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/api-v1) + [`src/platform/server/mcp/server.ts`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/mcp/server.ts) | 若立项公开自动化：与 YumCut API scope/幂等结论合并；当前无需求不加 MCP | 只参考设计 |
| [`src/platform/server/realtime/realtime-channel.do.ts`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/realtime/realtime-channel.do.ts) + [`src/sequences/server/workflows/sequence-export-workflow.ts`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/sequences/server/workflows/sequence-export-workflow.ts) | 当前 `durable_runs` 和 `timeline_render` 持续唯一 | 不引入 |

## 12. 可以直接复用的代码

**无。** TypeScript/Cloudflare/D1 代码的生命周期和数据契约不适配本项目 Python 业务边界；根 MIT 仅解决上游仓库源码的基本授权，不能自动放行外部模型、供应商媒体与依赖。初筛/本次审计没有批准任何代码复制。[根 LICENSE](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/LICENSE)。

## 13. 可以迁移后适配的代码

**本轮无可签收候选。** `planUnits` 虽偏纯函数，却内含上游 `sheet/voice/prompt/still/clip/music` 工序与 `stopAt` 语义；`tag-reconcile` 假设英文名字/大写词元，且自动回填连续性标签，不能无测试迁入中文复刻数据。若未来一定要逐函数迁移，先以固定 SHA 文件为源核对目标文件、保留 MIT 版权及许可、做语言/实体/错误语义适配、建立真实回归测试；目前均未达到这个门槛。[`generation-plan.ts`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/sequences/generation-plan.ts)、[`tag-reconcile.ts`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/sequences/tag-reconcile.ts)。

## 14. 可以部分复用的代码

**无独立可复制切片。** `sha256Hex` 的规范化是简单思路，但上游大量 `compute*InputHash` 同其具体版本指针、模型标识、素材 URL 绑定；离开 D1 Schema/运行快照即失去一致性。当前需要的是重新定义本项目每类产物的“真正影响输出的输入字段”，不是复制散落的哈希 helper。[`input-hash.ts`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/shots/input-hash.ts)、[架构说明](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/docs/architecture/workflow-snapshots-and-content-hash-staleness.md)。

## 15. 只建议参考设计的功能

“一个计划读模型同时服务预览、继续和更新全部”的防漂移原则；运行前冻结输入、写回前区分选中/分歧结果；缺失/过期要给原因；导出按服务端解析后的输入指纹复用而非信任客户端哈希；外发生成前费用预留与内容来源记录。[计划说明](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/sequences/generation-plan.ts)、[导出路由](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/routes/api/v1/sequences.$id.exports.ts)、[账务预检](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/billing/server/preflight.ts)、[provenance](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/compliance/provenance.ts)。

## 16. 不建议引入的功能

不引入上游 `Sequence/Scene/Shot/Frame` 持久化、团队库/`generated_assets` 作为第二素材身份、Cloudflare Workflows/DO/D1/R2 与积分账本、平台 Fal 代理/自动切换、MediaBunny Container 与新 Theatre 时间线、完整 OAuth/MCP 面。特别是不能把 OpenStory 的 AI 生成任务、`workflowRunId` 或 R2 URL 当本项目素材/审核/交付身份。[现有边界登记](./reference-project-integration-register.md)。

## 17. 当前项目缺失能力

当前主要缺口仍是共享 `AssetLibrary` 的跨项目发现、元数据、权利与可导入约束，以及视频片段视觉语义检索；OpenStory 的团队“生成素材库”不是它们的直接答案。另一个较窄缺口是“镜头/控制素材/运行结果的依赖过期解释及迟到结果冲突提示”，可作为独立产品切片评估。[素材候选初筛](./material-library-projects-intake-2026-09-28.md)、[Media Search Agent 深审](./media-search-agent-current-project-integration-audit-2026-09-28.md)。

## 18. 可以删除或替换的当前实现

**无。** 不替换 `ProjectAssets`、`durable_runs`、Timeline/FFmpeg、当前供应商显式选择或审核版本。上游完整生成链会造成已明确拒绝的第二项目/运行/渲染链。[ADR 0009](../adr/0009-content-project-and-workflow-boundaries.md)。

## 19. 融合后的模块架构

若产品确认需要“过期解释”，建议沿现有边界增加只读派生层：`ProjectAssets + 制作镜头/流程节点 + durable_runs 快照 → DependencyVerdict（缺失/过期/运行中/未知及原因）→ 现有准备状态 UI 与人工确认 → 既有 Timeline/审核`。OpenStory 只给出原则和测试问题，不提供新的运行时。片段检索仍按 Media Search Agent 的 `assetId + sourceFingerprint + [start,end]`；外部素材仍走 MPT `MaterialProvider → ProjectAssets`。[本地登记](./reference-project-integration-register.md)。

## 20. 融合后的用户流程

用户导入/确认项目素材 → 建立或调整制作镜头 → 查看由本地业务状态算出的待补/过期提示 → 选择是否重新准备、保留历史版本或采纳新结果 → 现有 Timeline 预览与审核。若模型生成结果从外部回来，先校验当次冻结输入和当前镜头选择指针；迟到结果保留为候选，不静默覆盖已采纳结果。这是建议流程，非已实现能力。[上游 claim 合并范例](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/cast/server/db/sheet-claims.ts)。

## 21. 对当前代码目录的影响

本次**不改代码**。若独立立项，优先在现有 [`backend/app/shot_preparation.py`](../../backend/app/shot_preparation.py)、[`backend/app/shot_production.py`](../../backend/app/shot_production.py)与 [`backend/app/durable_runs.py`](../../backend/app/durable_runs.py)附近定义 Python 只读 verdict/版本校验，前端既有 [`shotPreparationStatus.ts`](../../frontend/src/shotPreparationStatus.ts)消费；不建 Cloudflare/TanStack 平行模块。真正编辑界面前读 [`design.md`](../../design.md)。

## 22. 数据模型调整

建议只在未来需求确认后，为已有产物记录增加/复用 `inputFingerprint`、生成时的源 `assetId` 与版本、选中版本、在途 claim、过期原因的派生计算；字段必须按本地业务实际输入确定，哈希不能替代权限、来源许可或审核批准。上游 null 哈希代表未知、不等于最新；模型版本变更也不是自动宣告旧结果过期，[上游架构说明](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/docs/architecture/workflow-snapshots-and-content-hash-staleness.md)。

## 23. Store 调整

前端仅保存查询/选中/用户可见 verdict；业务事实仍以当前后端项目状态为准。上游 [`generation-plan`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/sequences/generation-plan.ts)的“同源读结果服务 footer/continue/update all”可作验收准则，但不迁 D1、DO 或另一套 Zustand/React Query 状态。

## 24. API 调整

本轮无 API 变更。若实施，仅在现有 FastAPI 增加项目范围内只读 `verdict` 和显式的重新准备/采纳命令，核对项目/素材/镜头当前版本和用户权限；不复制 [`/api/v1/sequences`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/routes/api/v1/sequences.ts)或 [`/mcp`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/routes/mcp.ts)。公开自动化另与 YumCut 的 scope/幂等设计合并评审。[YumCut 深审](./yumcut-current-project-integration-audit-2026-09-28.md)。

## 25. P0 / P1 / P2 实施计划与验收

| 优先级 | 决策/试验 | 验收与边界 |
| --- | --- | --- |
| P0 | **不列入共享素材库下一项深审队列**；继续既定 Immich/UnoPim/Boomerang/Openverse 路线，而 OpenStory 的本轮固定提交审计已完成 | 不以生成平台代替 `AssetLibrary`；[既定队列](./material-library-projects-intake-2026-09-28.md)。 |
| P1 | 若“准备状态失效解释”成为明确需求，先以本项目少数产物做纯读模型/并发迟到用例设计 | 改角色/地点/源素材/模型参数时只影响对应下游；无关编辑不误报；旧运行结束不盖新选择；未知不冒充 fresh。 |
| P2 | 若未来需要平台级生成、公共 API、账务或云导出，另行立项 | 需重审产品边界、真实媒体/供应商条款、费用/权限与审核；不得借本报告自动扩大范围。 |

## 专项源码核验与许可边界

- **工作流/快照/claim：** [`RegenerateShotsWorkflow`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/shots/server/workflows/regenerate-shots-workflow.ts)校验快照哈希；[工作流底座](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/workflow/base-workflow.ts)按团队范围运行和处理失败；[`sheet-claims`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/cast/server/db/sheet-claims.ts)在编辑清 claim、完成时有条件提升，未中 claim 停为 divergent。上游文档也明确**并非所有**工作流都已迁快照，不能泛化为全链已无中途读竞态。[快照架构](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/docs/architecture/workflow-snapshots-and-content-hash-staleness.md)。
- **API/OAuth/MCP：** 固定源码支持 API Key、团队范围、OAuth scope/PKCE/设备码和 HAL/OpenAPI；[`MCP server`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/mcp/server.ts)当前是 `whoami` 与生产数据只读工具，不等于文章所暗示的任意 Agent 可直接写入并生成；生成仍经 REST。公网 API 对外图片摄取另有 [SSRF/重定向/尺寸限制](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/api-v1/safe-fetch.ts)，其注释承认 Workers 无 DNS pin 的残余边界。[API 指南](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/docs/developer-guide/public-api.md)。
- **实时/存储/导出：** DO 通道保存有上限的事件历史，瞬时 LLM delta 不持久化，SSE 队列有背压/丢帧；它是 UI 进度，不是 `durable_runs` 的业务事实。[DO 源码](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/realtime/realtime-channel.do.ts)。[导出路由](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/routes/api/v1/sequences.$id.exports.ts)在服务端冻结已选片段和 music URL，算源 hash、复用 ready/合并 processing，35 分钟旧 processing 置失败；[workflow](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/sequences/server/workflows/sequence-export-workflow.ts)把 MP4 从 Container 流入 R2。它并非本项目现有 FFmpeg 路径的替代品。
- **模型/费用/来源：** [`via.ts`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/models/via.ts)区分实际调用通道与模型训练方，回查/计费需沿提交时 stamped via；[`preflight.ts`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/billing/server/preflight.ts)支持 BYOK 免平台预留、否则 reservation；[`workflow-deduction.ts`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/billing/server/workflow-deduction.ts)以运行 ID 派生幂等扣费键，扣款不足会记录未收费差额；[`provenance.ts`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/compliance/provenance.ts)保存产物、provider/model、提示词哈希和运行关联。上述均不能替代本项目的第三方素材**使用授权**记录。
- **许可：** 固定提交树只有根 [`LICENSE`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/LICENSE)，为 MIT、版权 2025–2026 OpenStory；根许可不自动覆盖依赖、供应商模型/输出、用户上传媒体、字体/测试素材、Fal/OpenRouter/Cloudflare/Stripe/MediaBunny 等条款。没有定位到仓内子目录独立 LICENSE/NOTICE，**不是**第三方权利已清空；复用任何代码均需保留 MIT 声明，并先核具体依赖/素材的许可证与使用场景。[依赖清单](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/package.json)、[容器依赖](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/containers/video-export/package.json)。
- **测试证据：** 固定树存在 [`generation-plan.test.ts`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/sequences/generation-plan.test.ts)、[`sheet-claims.test.ts`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/cast/server/db/sheet-claims.test.ts)、[`create.test.ts`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/api-v1/create.test.ts)、[`realtime-channel.do.test.ts`](https://github.com/openstory-so/openstory/blob/3ff7e58c1afba853e3db201d98f85798ef7e5891/src/platform/server/realtime/realtime-channel.do.test.ts)及 Vitest/Playwright 脚本；本次未安装依赖、未运行上游单测/E2E、未启动 Cloudflare 模拟栈、未购买/调用生成模型、未执行真实媒体质量/并发/计费实验，故结论仅为源码级。

## 验证记录

已检查固定 SHA、文中引用的上游文件在该提交存在、本地相对链接目标存在、Markdown 差异无空白错误；本文件是唯一新增目标，不修改产品代码或累计登记表。官网 `openstory.so` 的实时产品体验本次未验证；活跃度和站点状态会变化，不能用本文取代上线前复核。
