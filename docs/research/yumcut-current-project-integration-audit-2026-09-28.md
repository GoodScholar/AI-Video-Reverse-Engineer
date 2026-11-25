# YumCut 固定提交源码深审与当前项目融合判断

审计日期：2026-09-28。上游仓库 [`IgorShadurin/app.yumcut.com`](https://github.com/IgorShadurin/app.yumcut.com/tree/7e07eb356393c20014ab6badcf6e27c61a740781)，本地检出 HEAD 为 `7e07eb356393c20014ab6badcf6e27c61a740781`。本报告审的是固定提交的静态源码、测试用例和文档，不代表部署验证。处理标签仅使用“直接复用、迁移后适配、部分复用、只参考设计、重新实现、不引入”。

**总判定：不引入 YumCut 源码、Schema、组件、提示词或流程实现；只参考产品设计，未来确有需求时重新实现。** 根 [`LICENSE`](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/LICENSE) 为 **PolyForm Noncommercial 1.0.0**。README 自述“个人使用免费；为客户托管、出售访问权、商业服务须取得商业许可”，与根许可方向一致，但“open-source/free/self-hosted”不能解释为商用复制许可；商业产品在没有权利人另行书面授权及具体用途审查前，默认不可复制。[README 许可段](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/README.md#L94-L111)、[当前 ADR 0007](../adr/0007-preproduction-with-shot-steps.md)、[累计登记表](./reference-project-integration-register.md)。

## 1. 参考项目核心能力

真实主链是 Next.js 账户创建 Project，按语言计价并扣积分，daemon 领取 Job，依次生成脚本、音频、转录、元数据、字幕、图像/视频部分和成片；用户可检查脚本和音频、下载多语言视频。另有角色视频、Image Prank、移动 API、用户 API、付费与 YouTube 排程。它面向“从提示词到竖屏成片”的云服务，非任意素材时间线编辑器。[状态管线](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/shared/pipeline/project-pipeline.ts)、[项目创建路由](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/app/api/projects/route.ts)、[daemon 执行器](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/scripts/daemon/helpers/executor.ts)。

## 2. 当前项目核心能力

当前项目以 `ContentProject`、`ProjectAssets` 稳定素材 ID 承载制作，规范 Scene/Shot/ShotResultVersion 及同一 Shot 的列表和关系图投影；剪辑由当前 Timeline 与 Python FFmpeg 完成，异步任务由 `durable_runs` 冻结输入并恢复，变体预览、审核和导出有独立版本边界。ADR 0007 已关闭应用内最终 AI 生成提交。[领域边界](../adr/0009-content-project-and-workflow-boundaries.md)、[镜头服务](../../backend/app/shot_production.py)、[素材库](../../backend/app/project_assets.py)、[Timeline API](../../backend/app/timeline_api.py)、[审核生命周期](../../backend/app/batch_production.py)、[运行契约](../../backend/app/durable_runs.py)。

## 3. 产品定位差异

| 维度 | YumCut | 当前项目 | 判断 |
| --- | --- | --- | --- |
| 成片起点 | Prompt/模板/角色，按 Project 线性自动生成 | 内容项目和显式素材、Scene/Shot、变体编辑 | 不把自动生成 Project 当规范 Shot |
| 主交付 | 多语言竖屏成片、账户积分及可选排程 | 本地制作、预览绑定审核、批准导出 | 不让“任务完成”代替审核 |
| 运行架构 | Next.js + MySQL/Prisma + 外置 daemon/workspaces | React/Zustand + FastAPI + 本地项目存储/FFmpeg | 不建第二服务/数据库/队列 |
| 商业前提 | PolyForm Noncommercial，商业另授权 | 当前产品要求商用许可安全 | 源码复制门槛先于技术适配 |

依据：[上游技术依赖](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/package.json)、[上游 Prisma](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/prisma/schema.prisma)、[当前 ADR 0009](../adr/0009-content-project-and-workflow-boundaries.md)。

## 4. 功能模块对比

YumCut 固定提交中可找到项目创建/状态/停止、脚本和音频审批、语言版本、模板、角色、图片替换、积分交易、订阅、发布排程、管理后台、移动端、Bearer 用户 API。当前项目已有内容项目、镜头准备、Timeline、变体、审核、导出和本地任务；没有与之同构的 SaaS 账户计费、订阅、移动账户和公开用户 API。上游这些模块可作为需求清单，不能整体迁入。[路由总入口](https://github.com/IgorShadurin/app.yumcut.com/tree/7e07eb356393c20014ab6badcf6e27c61a740781/src/app/api)、[当前内容工作流](../../backend/app/content_workflow_api.py)、[当前编辑/审核](../../backend/app/batch_production.py)。

## 5. 业务逻辑对比

YumCut 创建项目时归一目标语言，按时长和语言数计算积分，在数据库事务内创建 Project、扣余额并建立各语言进度；余额更新用 `gte amount` 条件阻止透支。脚本/音频可人工批准，也可随 `autoApproveScript/autoApproveAudio` 跳过门禁；ProjectStatus 为单线状态，不能表达当前 Shot 结果采用、预览失效、审核版本或导出版本。[创建事务](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/app/api/projects/route.ts#L286-L398)、[积分账本](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/server/tokens.ts)、[管线状态](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/shared/pipeline/project-pipeline.ts)、[当前 Shot 结果](../../backend/app/shot_production.py)。

## 6. UI / UX 对比

上游主页面输入提示词、语言/语音/模板，确认页展示设置与价格，项目页按状态显示脚本、音频与视频卡，语言 tab 展示独立进度并提供逐语言视频预览、下载全部。它有实用的“一项目多语言”呈现，但最终视频卡是观看和下载，不是当前预览绑定的批准审核。项目列表按账户项目列出，没有当前场景/镜头关系图。[输入组件](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/components/main/PromptInput.tsx)、[确认页](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/components/create/ProjectConfirmation.tsx)、[语言 tab](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/components/project/LanguageTabsList.tsx)、[视频卡](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/components/project/ProjectFinalVideoCard.tsx)、[当前工作室](../../frontend/src/ContentStudioApp.tsx)。

## 7. 技术架构与运行边界

上游 Next.js App Router 兼服务端 API，Prisma/MySQL 持久化用户、项目、Job、积分、发布；daemon 通过受保护的 API 抢 Job，并在外置脚本 workspace 运行阶段命令，随后更新状态/产物。`daemon.env.example` 指向四个独立工作区：`video-slides/ai-agent`、`yumcut-shorts-tools`、`yumcut-characters`、`yumcut-captions-video-maker`；配置 schema 强制要求对应路径，启动时解析为绝对路径。脚本、字幕和角色处理跨仓，本仓的 dummy 测试工作区只替代测试执行，不足以证明完整生产闭环或“仅克隆本仓即可自托管”。部署仍需逐仓验证代码、许可、版本、密钥和服务。最终视频的正常 daemon 路径调用外置 `video:merge-layers` 等命令；仓库内 Remotion 仅见欢迎 Image Prank 视频脚本，不能说主成片用 Remotion。[daemon 环境样例](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/daemon.env.example)、[daemon 配置](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/scripts/daemon/helpers/config.ts)、[视频合成](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/scripts/daemon/helpers/video.ts)、[Remotion 辅助脚本](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/scripts/welcome-prank-video/render.ts)、[当前本地渲染](../../backend/app/timeline_render.py)。

## 8. 数据模型对比

上游 `Project` 持有 `userId/status/languages/finalVideo*` 等，语言子表有 `Script`、`AudioCandidate`、`VideoAsset`、`ProjectLanguageProgress`；`Job` 与 `Daemon` 记录生产领取，`ProjectStatusHistory` 记录状态，`TokenTransaction` 记资金，`PublishTask` 另记排程。`ProjectArtifact` 有 `kind/variant`，但这些不是镜头版本审核。当前的资产、Shot、TimelineClip、运行与审核各有独立身份和失效关系，任何字段映射都不能把上游 `Project` 当 `ContentProject` 一对一替换。[上游 Schema](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/prisma/schema.prisma)、[当前前置 API](../../backend/app/preproduction_api.py)、[当前审核](../../backend/app/batch_production.py)。

## 9. 当前项目已有优势

当前对源资产稳定引用、Scene/Shot 准备、结果候选采用、Timeline 输入冻结、异步迟到结果与审核/导出版本有明确责任分层；列表与关系图只是同一 Shot 的投影。YumCut 的线性项目状态无法承接这组独立事实，也不提供当前可复核的审片闭环。[当前项目素材](../../backend/app/project_assets.py)、[当前镜头](../../backend/app/shot_production.py)、[当前运行](../../backend/app/durable_runs.py)、[当前双视图 ADR](../adr/0010-shot-workflow-complementary-views.md)。

## 10. YumCut 优势与真实性边界

| 能力 | 固定提交证据 | 实际边界 |
| --- | --- | --- |
| 一项目多目标语言 | [Schema](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/prisma/schema.prisma)、[语言进度集成测试](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/tests/daemon-and-api/multilanguage.spec.ts)、[语言标签](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/components/project/LanguageTabsList.tsx) | 目标视频语言有 en/ru/es/fr/de/pt/it；应用 UI 文案仅 en/ru。每语言失败可 `disabled`，不代表七语 UI 完整本地化 |
| 用户 API | [Bearer 鉴权](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/server/user-api/api-auth.ts)、[read/write scope 路由](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/app/api/user/v1/%5B...path%5D/route.ts)、[持久化幂等](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/server/user-api/api-idempotency.ts) | 对外自动化入口确有实现；其 Project/积分/账户契约不适配当前本地服务 |
| 积分/订阅 | [账本](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/server/tokens.ts)、[Stripe 回调](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/app/api/stripe/webhook/route.ts) | SaaS 模型真实，但价格与第三方成本需要部署配置；不应导入当前本地任务 |
| 分组/多品牌 | [ProjectGroup](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/prisma/schema.prisma#L1169-L1210)、[groups 创建路由](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/app/api/groups/route.ts)、[隐藏入口注释](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/components/main/PromptInput.tsx#L844-L869) | 只是用户内设置聚合；创建组不自动 fan-out 项目，未见 Organization/Workspace/Role 协作模型；不能据 README 宣称成熟 agency 多品牌批量生产 |
| 发布 | [任务表](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/prisma/schema.prisma#L716-L746)、[provider 注册表](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/scripts/publish-daemon/providers/index.ts)、[YouTube 上传](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/scripts/publish-daemon/providers/youtube.ts)、[runner 测试](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/tests/publish-daemon/runner.test.ts) | 实际 provider 仅 YouTube，特性开关/白名单控制；无 TikTok/Instagram 自动发布证据；并发 claim 风险见下段 |

发布任务的 `claimPublishTasks/claimCleanupTasks` 先 `findMany` 再 `updateMany`，随后不检查实际更新的 ID/数量而返回原 candidates；两个 publish-daemon 并发读取同一待处理记录时存在重复上传/清理风险。现有发布测试覆盖筛选与状态，但未见并发抢领证明。主生产 Job 的 claim 则使用带 `queued` 条件和项目锁的 `updateMany`，以 `count` 判定成功，且有锁测试；两套队列的成熟度不能混同。[发布 claim](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/server/publishing/tasks.ts#L52-L91)、[发布测试](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/tests/server/publishing-schedule.test.ts)、[主 Job claim](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/app/api/daemon/jobs/%5BjobId%5D/claim/route.ts)、[主 Job 锁测试](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/tests/server/daemon-locking.test.ts)。

## 11. 功能映射与累计去重

| YumCut 能力 | 当前及前六项审计的归属 | 处理标签 | 唯一落点 |
| --- | --- | --- | --- |
| Prompt→脚本/配音/视觉/成片 | 当前准备、变体、Timeline/FFmpeg；[MoneyPrinterTurbo](./moneyprinterturbo-current-project-integration-audit-2026-09-28.md) 的素材有序分配已是更贴切的混剪参考 | 不引入 | 当前 ContentProject 和既有生产链 |
| 长视频选片 | [OpenShorts](./openshorts-current-project-integration-audit-2026-09-28.md) 为首选；[opensource-clipping](./opensource-clipping-current-project-integration-audit-2026-09-28.md) 仅补构图失败用例 | 不引入 | 不把 YumCut 脚本生成误作高光检索 |
| 多语言版本 UI 与失败隔离 | 当前变体/配音/字幕已有身份，YumCut 的语言 tab 与逐语言失败提示可补体验 | 只参考设计 | 当前变体与审核版本，不增加第二 ProjectLanguageProgress |
| 工作流/画布/Agent | [Toonflow](./toonflow-current-project-integration-audit-2026-09-28.md) 的受约束图交互、[ComfyStudio](./comfystudio-current-project-integration-audit-2026-09-28.md) 的候选 Workflow 导入；[OpenMontage](./openmontage-current-project-integration-audit-2026-09-28.md) 的阶段证据表达 | 不引入 | 同一规范 Shot 投影和当前运行 |
| Bearer API 的范围与幂等 | 当前 FastAPI 若未来公开 API，可借鉴权限、请求指纹、结果重放语义 | 重新实现 | 当前项目 ID、版本冲突与写请求边界 |
| 账户积分、分组、YouTube 排程 | 与当前产品闭环不同；MPT/opensource-clipping 的发布也未列为当前核心 | 只参考设计 | 只有单独立项后在批准导出后建 Publisher Adapter；积分/租户另立项 |

此表与[累计登记表](./reference-project-integration-register.md)保持一致：YumCut 不抢 OpenShorts 的本地候选片段、MPT 的蒙太奇分配、Toonflow 的画布、ComfyStudio 的 Workflow 交换或 OpenMontage 的质量提示；更不替换现有审核与渲染。

## 12. 可以直接复用的代码

**无。** 没有获准商业复制的具体文件，也没有与当前 Python 领域对象同构到可原样放入的文件。上游路径 → 当前路径、依赖、测试与许可证映射：**无代码复用条目**。[根许可](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/LICENSE)。

## 13. 可以迁移后适配的代码

**无。** 把 TypeScript 账本、API 幂等、翻译/发布模块改写成 Python 并保留上游表达仍可能构成改编；未经商业许可不将其列为迁移候选。若未来取得覆盖具体用途的授权，再针对具体文件、依赖、权属与目标接口单独审查；本报告不预设授权。[上游幂等模块](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/server/user-api/api-idempotency.ts)。

## 14. 可以部分复用的代码

**无。** 小函数、Schema 片段、文案、Remotion 组件也受许可证与第三方条款约束。可采用“逐语言结果需要独立显示”等抽象需求，具体实现按第 15、19—25 节独立设计，不复制表达。[上游语言状态](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/prisma/schema.prisma#L280-L305)、[根许可](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/LICENSE)。

## 15. 只参考设计的功能

值得记录的产品表达：创建前展示语言数、预计费用/资源与可用余额；语言 tab 各自标记 ready/processing/error，部分语言失败不隐藏已完成输出；自动化 API 区分只读与写入且对重复提交返回原结果；发布任务独立于制作任务并限制可编辑时间窗。若当前产品确需公开 API 或发布，采用“重新实现”，以现有项目、审核、导出为权威。[上游确认页](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/components/create/ProjectConfirmation.tsx)、[幂等](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/server/user-api/api-idempotency.ts)、[发布窗口](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/server/publishing/tasks.ts)。

## 16. 不建议引入的功能

不引入上游账户/Prisma/Stripe/移动认证/Job daemon、`ProjectGroup`、上游线性状态、四个跨仓生产工作区、Remotion 辅助渲染、YouTube 排程或自动审批，作为当前核心链的默认组成。这样会新建平行的项目、素材、队列、时间线或审核语义，并带入未完成逐仓许可核验的脚本依赖；尤其不得为了 YumCut 重新开放 ADR 0007 关闭的应用内最终 AI 视频生成提交。[上游数据模型](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/prisma/schema.prisma)、[daemon 工作区](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/daemon.env.example)、[当前 ADR 0007](../adr/0007-preproduction-with-shot-steps.md)。

## 17. 当前项目缺失或待补足

当前缺少 YumCut 式七目标语言的完整产品化选择/逐语言失败总览、外部 Bearer 用户 API、账户级积分订阅和批准后发布渠道。前两项仅在明确需求下评估；积分订阅、租户多品牌和自动发布并非当前本地视频制作闭环的 P0 缺陷。MPT、OpenShorts 已覆盖当前更迫切的素材发现与编排缺口。[当前变体生命周期](../../backend/app/batch_production.py)、[当前累计优先级](./reference-project-integration-register.md)。

## 18. 可以删除或替换的当前实现

**无。** YumCut 不提供与当前 Shot、Timeline、`durable_runs` 或审核完全等价且可合法复制的替代实现。没有本轮要删除的文件；任何“替换当前服务”都需要另立产品与迁移方案。[当前边界](../adr/0009-content-project-and-workflow-boundaries.md)。

## 19. 融合后的模块架构

维持 `ContentProject → ProjectAssets → Scene/Shot/ShotResultVersion → Timeline/FFmpeg → 变体预览/审核/导出`；列表和关系图继续读取同一 Shot。若未来批准多语言，语言只作为现有变体/音轨/字幕和交付版本的维度，运行仍由 `durable_runs` 承载。若批准公开 API，增加当前 FastAPI 的授权与幂等外层；若批准发布，仅从**已批准导出**生成独立发布任务，失败不得回写镜头采用或批准状态。这些均为重新实现的条件性架构，不是本次代码改动。[当前内容 ADR](../adr/0009-content-project-and-workflow-boundaries.md)、[当前队列](../../backend/app/durable_runs.py)、[当前审核](../../backend/app/batch_production.py)。

## 20. 融合后的用户流程

现有流程保持“创建内容项目 → 导入有稳定 ID 的素材 → 场景/镜头准备 → 选候选结果 → 编排变体 → 冻结预览 → 人工审核 → 批准导出”。若新增语言维度，在变体层逐语言查看进度与失败并分别预览；批量产物不默认批准。若将来单独立项发布，须由用户在已批准导出上选账号、时间和平台，展示授权与结果；发布失败仅影响发布任务。[当前 Shot 结果](../../backend/app/shot_production.py)、[当前 Timeline](../../backend/app/timeline_api.py)、[当前审核](../../backend/app/batch_production.py)。

## 21. 对当前代码目录的影响

**本轮为研究报告，产品目录零改动。** 条件性 P1 语言体验只应改现有 `frontend/src` 的变体页面/Store 与 `backend/app/batch_production.py` 附近契约；P2 若公开 API，在 `backend/app` 增加当前项目的认证/幂等适配；发布需独立的批准导出后服务。不建立 `yumcut/`、Prisma、Node daemon 或第二前端。[当前前端](../../frontend/src/ContentStudioApp.tsx)、[当前后端](../../backend/app/batch_production.py)。

## 22. 数据模型调整

P0 **无调整**。P1 若多语言有真实需求，先盘点现有变体/音轨/字幕与审核版本，再对每一语言保留输入快照、候选、预览、审核、导出及失败原因；不得直接移植 `ProjectLanguageProgress` 或以语言覆盖 `ShotResultVersion`。P2 发布可有 `approvedExportId/channelId/scheduledAt/status/providerResult` 的独立记录，项目身份仍用 `ContentProject`。公开 API 的请求指纹/响应重放记录若实施，必须绑定当前项目权限与版本号。[上游语言模型](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/prisma/schema.prisma#L280-L305)、[当前项目数据](../../backend/app/content_workflow.py)。

## 23. Store 与状态调整

P0 **无调整**。上游 `ProjectsProvider` 是 React Context + `any` 项目列表及浏览器事件同步，不是当前 `preproductionWorkspaceStore` 的替代；当前同一 Shot ID/修订号仍唯一。P1 如有多语言，只为现有变体状态加语言筛选和逐语言错误投影，服务端版本仍权威；不得把上游 ProjectStatus、Job 状态或发布任务状态映射成审核状态。[上游 Provider](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/components/providers/ProjectsProvider.tsx)、[当前 Store](../../frontend/src/preproductionWorkspaceStore.ts)。

## 24. API 与服务调整

P0 **无调整**。P1 如新增语言能力，沿用当前项目 ID、变体版本、`durable_runs`、审核端点，只增与真实需求对应的读写字段。P2 若公开 API，独立实现 Bearer key 哈希存储、read/write scope、主体与项目归属检查、写请求幂等键、同键异体冲突、已完成结果重放和过期策略；若发布，单独对已批准导出提供排程/取消/状态 API，不复用上游 `/api/user/v1` 或 `/api/scheduler` 路由。[上游 API 分发](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/app/api/user/v1/%5B...path%5D/route.ts)、[上游任务 API](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/app/api/scheduler/tasks/route.ts)、[当前前置服务](../../backend/app/preproduction_api.py)。

## 25. P0 / P1 / P2 实施计划、许可与验收

| 优先级 | 决策与可验证目标 | 与累计路线的关系 |
| --- | --- | --- |
| P0 | 维持 YumCut 代码零复制和现有唯一对象；需求评审中明确“ProjectGroup ≠ 多品牌批量”“仅 YouTube 发布”“UI en/ru ≠ 七语 UI”。验收：本报告固定提交证据可回溯、无产品代码差异。 | 优先继续 [OpenShorts](./openshorts-current-project-integration-audit-2026-09-28.md) 候选选片与 [MPT](./moneyprinterturbo-current-project-integration-audit-2026-09-28.md) 编排，不插入第二生成链 |
| P1 | 只有用户确需多语言时，**重新实现**当前变体上的逐语言进度/失败呈现；验收：单语言失败不遮蔽其他版本、重渲染使对应预览/审核失效、未审版本不得导出。 | 保留当前 Timeline/FFmpeg/审核，不复制 YumCut Schema/UI |
| P2 | 公开 API、多品牌、账户计费、批准后自动发布分别立项。API 验收覆盖 scope、归属、重放/冲突；发布验收覆盖授权、取消、并发 daemon 恰一领取、重复提交、失败与既有批准隔离。 | 与 Toonflow/ComfyStudio/OpenMontage 的图、Workflow、质量参考互不争夺权威对象 |

**许可证与供应链硬门槛。** 根 PolyForm Noncommercial 许可的个人/非营利允许范围不能外推至商业产品；README 明示为客户托管或商业服务需要商业许可。四个外部生产工作区不在本次固定提交审计范围内，不能假定共享根许可证或已许可商用，需逐仓固定提交、权属、依赖和许可核验。仓库 `package.json` 包含 Remotion 4.0.484，但只在欢迎 Image Prank 脚本实际调用；Remotion 官方[许可和价格说明](https://www.remotion.dev/docs/license/pricing)及[FAQ](https://www.remotion.dev/docs/license/faq)按组织规模与自动化用途另设许可，不能由 YumCut 根许可覆盖。MiniMax/Inworld/ElevenLabs/RunPod/Runware、Google/YouTube、Stripe、存储及外置生成脚本各有独立 API、商用和数据条款；角色、音乐、字体、图片/视频素材、声音克隆和平台账号也须分别核定来源授权。本轮未取得任何可转授的权利或验证媒体权属。[daemon 环境样例](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/daemon.env.example)、[上游配音路由](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/scripts/daemon/helpers/prompt-to-wav.ts)、[图像服务](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/server/image-generation/runware.ts)、[YouTube 实现](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/scripts/publish-daemon/providers/youtube.ts)。

**测试证据与限制。** 固定提交包含多语言 daemon/API 集成用例（包括单语言失败、重启恢复、翻译和审批）、积分失败退款用例、发布 runner/provider 用例、用户 API 幂等相关测试；这些是源码中的测试覆盖证据，并非本轮运行通过的证明。dummy workspace 的模拟输出也不验证四个生产工作区的真实模型/媒体处理。[多语言测试](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/tests/daemon-and-api/multilanguage.spec.ts)、[退款测试](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/tests/server/project-failure-refund.test.ts)、[发布测试](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/tests/publish-daemon/runner.test.ts)、[dummy 工作区](https://github.com/IgorShadurin/app.yumcut.com/tree/7e07eb356393c20014ab6badcf6e27c61a740781/tests/daemon/dummy-scripts)。本次未安装依赖、启动 MySQL/外部模型或运行上游/当前项目测试；真实生成质量、发布 API 权限、费用和多租户规模没有实测结论。README 的“约 10 倍便宜”、agency 批量和 TikTok/Instagram 成片就绪是宣传或用途描述，不作为固定提交的性能或发布能力证明。[README](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/README.md)。
