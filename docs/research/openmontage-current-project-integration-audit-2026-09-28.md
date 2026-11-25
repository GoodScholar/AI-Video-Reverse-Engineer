# OpenMontage 固定提交源码深审与当前项目融合判断

审计日期：2026-09-28。上游固定提交：[`08e2151fa02de28a5d6a312b3d575692bf147ad7`](https://github.com/calesthio/OpenMontage/tree/08e2151fa02de28a5d6a312b3d575692bf147ad7)（`git ls-remote` 与检出 HEAD 一致）。本报告是**静态源码审计**，没有运行上游或当前项目测试，也不把上游 `stability: production` 当作生产质量的独立证明。处理方式只使用“直接复用 / 迁移后适配 / 部分复用 / 只参考设计 / 重新实现 / 不引入”。

总判定：维持[累计登记表](./reference-project-integration-register.md)的 **只参考设计**。OpenMontage 有真实 Python 工具、checkpoint 校验、Backlot 只读看板及局部 QA；其跨阶段创作编排主要由 Coding Agent 阅读 YAML 与 Markdown skills 执行，**不是可嵌入当前 FastAPI 的独立生产运行时**。根 AGPL-3.0 是代码、Schema、技能文本复制的硬门槛；闭源产品默认不复制，除非另有明确授权或经过许可与隔离审查。[上游 Agent 合约](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/AGENT_GUIDE.md)、[根许可证](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/LICENSE)、[当前 ADR 0007](../adr/0007-preproduction-with-shot-steps.md)、[ADR 0009](../adr/0009-content-project-and-workflow-boundaries.md)、[ADR 0010](../adr/0010-shot-workflow-complementary-views.md)。

## 1. 参考项目核心能力

OpenMontage 把视频制作组织为 Pipeline manifest、阶段导演技能、工具注册表、阶段产物与 checkpoint。13 个 YAML 文件中含一个 `framework-smoke` 测试用 manifest；README 的“12 条生产管线”应按此口径理解。可做库存视频纪录混剪、讲述类、动画、屏录、配音本地化等，但各管线成熟度不同，例如 `documentary-montage` 明示 `beta`。Agent 自行选择管线、读取技能、调用工具、复审并请求人工批准。代码负责工具执行与持久化，创作调度依赖外部 Coding Agent。[Agent Guide](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/AGENT_GUIDE.md)、[纪录混剪 manifest](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/pipeline_defs/documentary-montage.yaml)、[测试 manifest](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/pipeline_defs/framework-smoke.yaml)、[工具契约](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tools/base_tool.py)。

## 2. 当前项目核心能力

当前产品以内容项目保存项目素材、参考复刻与商品视频制作；制作镜头、场景、流程节点、结果候选共享权威身份，列表与关系图投影同一状态。短视频变体有剪辑、配音、字幕、冻结渲染、预览、审核与批准导出。最终 AI 视频生成按 ADR 0007 在外部完成。[领域术语](../../CONTEXT.md)、[当前工作室](../../frontend/src/ContentStudioApp.tsx)、[制作镜头 API](../../backend/app/preproduction_api.py)、[项目素材](../../backend/app/project_assets.py)、[变体生命周期](../../backend/app/batch_production.py)、[本地运行](../../backend/app/durable_runs.py)。

## 3. 两个项目定位差异

| 维度 | OpenMontage | 当前项目 | 融合边界 |
| --- | --- | --- | --- |
| 用户与入口 | 掌握 Coding Agent、环境变量及工具链的创作者；Agent 指令驱动 | 普通创作者、ComfyUI 创作者、内部交付团队；内容工作室 UI | 不把 Coding Agent 会话当应用服务 |
| 权威对象 | `projects/<id>/project.json`、阶段 checkpoint、`assets/`、`renders/` | `ContentProject`、稳定 `assetId`、规范制作镜头、短视频变体版本 | 不建第二项目/素材/产物库 |
| 时间线与渲染 | Agent 提交 `edit_decisions`，工具可用 FFmpeg/Remotion 等 | 当前真实 Timeline 与 Python FFmpeg | 不引入第二时间线或渲染器 |
| 审核 | 阶段门禁、Agent 复审、Backlot 展示 | 绑定当前预览产物的审核版本与交付版本 | 上游“阶段批准”不可替代当前审片 |

依据：[上游 checkpoint](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/checkpoint.py)、[上游编辑产物 Schema](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/schemas/artifacts/edit_decisions.schema.json)、[当前 Timeline](../../frontend/src/timelineApi.ts)、[当前审核导出](../../backend/app/batch_production.py)。

## 4. 功能模块对比

上游包含预飞工具发现、来源媒体检查、脚本/场景计划、库存素材检索与生成工具、音视频合成、QA、自审、导出、Backlot 观察页。当前已有项目素材、参考分析与预处理、制作镜头、批量混剪、配音字幕、时间线、审核交付。上游 `source_media_review` 产生检查对象，但固定提交中 `review_source_media()` 的 Python 调用只见测试；其在正式制作中的执行是技能约定。上游 `required_artifacts_in` 和 `success_criteria` 在 YAML/Schema 中可查，但通用 checkpoint 写入器主要校验阶段、规范产物 Schema 和前置 checkpoint，不应推断它会自动验证全部自然语言成功条件。[来源检查](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/source_media_review.py)、[manifest loader](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/pipeline_loader.py)、[checkpoint](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/checkpoint.py)、[当前预处理](../../backend/app/local_preprocessing_runner.py)。

## 5. 业务逻辑对比

上游 `delivery_promise` 分类运动主导、来源主导等交付承诺，检查镜头中真实运动比例，避免静图方案静默降级；`slideshow_risk` 与 `variation_checker` 给场景多样性启发式评分；`verify_scene_pacing` 针对其 `TerminalScene` 帧级步进对齐旁白。前两者只覆盖计划结构，分数不是视频真实质量或审核决定。当前商品链更重视已确认脚本、有效配音、冻结预览、审核版本及批准导出；复刻链的可复刻范围和外部生成边界比上游通用承诺更明确。适合重写少量“承诺未兑现”和“视觉重复”提示，但不能把上游阈值当当前默认门槛。[delivery_promise](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/delivery_promise.py)、[幻灯片风险](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/slideshow_risk.py)、[变化检查](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/variation_checker.py)、[节奏检查](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/verify_scene_pacing.py)、[当前生命周期](../../backend/app/batch_production.py)。

## 6. UI / UX 对比

Backlot 用本地 FastAPI + 原生 JS 读 `project.json`、checkpoint、事件与文件，显示阶段轨、故事板、成本及回放。回放由时间戳筛选阶段状态和可见素材，是**运行过程展示**，不重放模型调用、项目状态变化或用户审核。服务端只有 GET/SSE/静态媒体路由，未发现“点击批准”写入 API 或用户认证；它适合作本机只读看板，不能替代当前写请求/权限边界。批准须回到 Agent 会话并写 checkpoint。当前 UI 已有按内容项目组织的镜头列表/关系图、变体编辑与审片，不需要再放一个 Backlot 看板。[Backlot 服务](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/backlot/server.py)、[状态投影](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/backlot/state.py)、[前端回放](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/backlot/ui/board.js#L891-L1050)、[当前关系图](../../frontend/src/WorkflowCanvas.tsx)、[当前工作室](../../frontend/src/ContentStudioApp.tsx)。

## 7. 技术架构对比

上游 Python `BaseTool` 统一元数据、输入 Schema、依赖与执行结果，工具调用加非阻断 `events.jsonl` 观测；事件文件只有进程内线程锁，跨进程写入不保证无损。YAML loader 验证 manifest，`write_checkpoint()` 写临时文件并替换、存档旧版、检查阶段前置和审批位，但持久化是按文件/时间戳读取，并不提供当前 `durable_runs` 的冻结输入、取消与迟到结果隔离契约。后端没有一个读取全部 manifest 后自行调度所有阶段的服务，Backlot 明确只读。当前 FastAPI 使用项目 ID、现有队列适配与冻结运行输入，React/Zustand 是 UI 状态，FFmpeg 是本地成片。把上游 Agent 指令和文件状态直接装入应用会形成第二运行协议。[上游工具基类](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tools/base_tool.py)、[事件](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/events.py)、[当前队列与冻结](../../backend/app/durable_runs.py)、[当前渲染](../../backend/app/timeline_render.py)。

## 8. 数据模型对比

上游 `pipeline_manifest` 定义阶段/技能/工具/审批默认，`checkpoint` 保存 `project_id`、stage/status、artifact、审批位和费用快照；另有 `cost_log.json`、`decision_log.json`、`events.jsonl`。`final_review` 包含技术探针、抽帧、音频、承诺与字幕检查。当前 `PreproductionWorkspace` 的 Shot/关系图、`ProjectAssets` 的稳定素材 ID、`TimelineClip` 的素材来源区间，以及短视频变体/审核/交付版本拥有不同且已确立的语义，不能由上游 `scene_plan`、`asset_manifest` 或 `checkpoint` 覆盖。[上游 checkpoint Schema](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/schemas/checkpoints/checkpoint.schema.json)、[最终检查 Schema](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/schemas/artifacts/final_review.schema.json)、[当前镜头类型](../../frontend/src/preproductionApi.ts)、[当前时间线类型](../../frontend/src/timelineApi.ts)。

## 9. 当前项目已有优势

当前已将“制作镜头结果采用”“编辑预览”“审核批准”“交付导出”拆成独立版本和失效关系；重渲染的新产物要重审，晚到结果不能覆盖新输入。列表和受约束关系图共用 Shot 身份，布局与内容分开保存。上游阶段 checkpoint 无法直接表达这些具体领域关系。[当前审核版本](../../backend/app/batch_production.py)、[冻结运行](../../backend/app/durable_runs.py)、[当前 Store](../../frontend/src/preproductionWorkspaceStore.ts)、[ADR 0010](../adr/0010-shot-workflow-complementary-views.md)。

## 10. 参考项目优势与成熟度边界

| 能力 | 固定提交实际实现与测试证据 | 边界 |
| --- | --- | --- |
| 阶段门禁 | [`write_checkpoint`](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/checkpoint.py#L422-L565) 阻止 gated 阶段无 `human_approved` 完成，检查前置阶段；[门禁场景测试](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tests/backlot/test_gate_scenarios.py) 与[前置测试](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tests/lib/test_checkpoint_prerequisites.py) 覆盖缺批准、错管线、历史标记和前置阶段 | 只强制写入路径；手写旧 checkpoint 可在看板中被标记，不能视为安全审批 API |
| 成本审批 | [`CostTracker`](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tools/cost_tracker.py) 有估算/预留/核销及阈值；[单元测试](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tests/tools/test_cost_tracker_governance.py) | 全库调用检索仅见测试与 QA 示例，`BaseTool.execute()` 未统一调用；不能宣称付费工具被自动拦截 |
| 回放 | [历史存档](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/checkpoint.py#L349-L386)、[Backlot 投影](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/backlot/state.py#L145-L245)、[JS 时间滑杆](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/backlot/ui/board.js#L891-L1050)、[历史测试](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tests/contracts/test_backlot_contract.py) | 展示状态/事件时间切片，不恢复任何外部工具副作用或旧媒体快照 |
| 质量检查 | [`video_compose._run_final_review`](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tools/video/video_compose.py#L2285-L2696) 真做探针、抽帧等并对失败返回错误；[相关测试](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tests/tools/test_hyperframes_compose.py)；[`VisualQA`](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tools/analysis/visual_qa.py) 标 `experimental` | 结构与机器检查不能替代人工审片；不是全部渲染器的统一审核契约 |
| 计划风险 | [`delivery_promise`](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/delivery_promise.py)、[`slideshow_risk`](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/slideshow_risk.py)、[`variation_checker`](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/variation_checker.py)、[合成入口](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tools/video/video_compose.py#L1435-L1495)、[合成样例基准](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tests/eval/bench_runner.py) | 规则针对上游场景字典和渲染文法，部分校验在缺输入时跳过；无当前领域接口稳定性承诺 |
| YAML 与技能 | [loader](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/pipeline_loader.py)、[目录契约测试](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tests/contracts/test_pipeline_catalog.py)；[checkpoint 技能](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/skills/meta/checkpoint-protocol.md) | YAML 主要供 Agent 阅读；`review_focus`/自然语言 `success_criteria` 不是 Python 全自动验收 |

## 11. 功能映射与累计去重

| 上游能力 | 当前/其他参考已登记能力 | 处理方式 | 唯一落点 |
| --- | --- | --- | --- |
| 源素材检查、库存素材搜索 | [OpenShorts](./openshorts-current-project-integration-audit-2026-09-28.md) 负责长视频候选；[MoneyPrinterTurbo](./moneyprinterturbo-current-project-integration-audit-2026-09-28.md) 负责外部库存素材 | 只参考设计 | 当前 `ProjectAssets` 来源/授权记录；不复制语义检索或素材库 |
| Agent 分镜/阶段角色 | [Toonflow](./toonflow-current-project-integration-audit-2026-09-28.md) 的候选分镜；当前规范 Shot | 只参考设计 | 候选方案经人工采用，才写入同一 Shot |
| YAML Pipeline/阶段 checkpoint | 当前 `durable_runs`、变体与审核版本 | 不引入 | 当前运行快照和领域状态继续唯一 |
| 幻灯片风险/交付承诺 | 当前视频预检与审核；MPT 的分配规则 | 重新实现 | 在现有方案/预览处增加非授权的质量提示 |
| Backlot 故事板/回放 | 当前镜头双视图、批量变体与审核历史；[ComfyStudio](./comfystudio-current-project-integration-audit-2026-09-28.md) 的候选流程交换只在范围批准后 | 只参考设计 | 当前工作室现有页面与运行记录 |
| 播客构图/裁切 | [opensource-clipping](./opensource-clipping-current-project-integration-audit-2026-09-28.md) 只作失败场景；OpenShorts 为候选首选 | 不引入 | 不重复引入上游裁切/渲染链 |

## 12. 可以直接复用的代码

**无。** 根 AGPL-3.0，闭源当前项目未获另行许可；且上游模型/存储与当前权威对象不同。固定提交上游文件 → 当前目标文件、复用部分、必改项、依赖、影响、License、测试：**无代码复制条目**。[根许可证](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/LICENSE)。

## 13. 可以迁移后适配的代码

**无。** 即使只迁 `delivery_promise`、checkpoint、Backlot 或 JSON Schema 的局部片段，也属于 AGPL 代码/表达复制；不以改名、翻译、拆模块规避。若将来取得独立授权，必须针对**具体上游文件 → 当前目标文件**重新做依赖、影响、许可和测试审查，本报告不预先批准。[上游承诺模块](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/delivery_promise.py)、[上游 Schema](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/schemas/artifacts/final_review.schema.json)。

## 14. 可以部分复用的代码

**无。** 字段、规则、阈值、测试用例都不从 AGPL 源码直接移植；独立实现仅吸收“先说明承诺、阶段可观察、费用前置、渲染后检查”的抽象需求。代码复用建议所需的上游文件 → 当前目标文件、复用部分、必改项、依赖、影响、License、测试：**无**。

## 15. 只参考设计的功能

参考“付费操作前向人说明预算”“阶段完成后展示证据并暂停”“已保存状态按时间回看”“视频可播且与原承诺相符”的产品语言。源头分别为[预算配置](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/config_model.py)、[checkpoint 门禁](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/checkpoint.py)、[Backlot 回放](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/backlot/ui/board.js)、[final_review Schema](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/schemas/artifacts/final_review.schema.json)。这些应进入现有[批量编辑 API](../../backend/app/batch_editing_api.py)、[审核生命周期](../../backend/app/batch_production.py)和[工作室](../../frontend/src/ContentStudioApp.tsx)的产品设计，不复制实现或技能文案。

## 16. 不建议引入的功能

不引入上游整套 `pipeline_defs`、Markdown 导演技能、`projects/<id>/checkpoint_*.json`、`cost_log.json`、`events.jsonl`、Backlot 服务、Remotion 时间线/合成、上游供应商工具库和自动发布。它们与当前项目/Shot/运行/时间线/审片权威形成双写，并使 AGPL、Remotion 商业许可、外部服务条款一起进入产品。[上游目录](https://github.com/calesthio/OpenMontage/tree/08e2151fa02de28a5d6a312b3d575692bf147ad7)、[Remotion 依赖](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/remotion-composer/package.json)、[Remotion 官方许可入口](https://www.remotion.dev/docs/license)、[当前 ADR 0009](../adr/0009-content-project-and-workflow-boundaries.md)。

**许可证硬门槛（非法律意见）：** 根 [`LICENSE`](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/LICENSE) 为 AGPL-3.0，修改版经网络提供交互时有向网络用户提供对应源码的义务，分发复制/修改版本时亦涉及版权与对应源码条款；闭源产品直接复制 Python、TS、Schema 或 Markdown skills 默认排除。清晰隔离的独立 AGPL 服务或另行商业授权需单独评估，不能用“只复制一点”自动豁免。仓库另有 [Ink Theater 第三方说明](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/ink-theater/THIRD_PARTY_NOTICES.md)：Patrick Hand 字体随 [OFL](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/ink-theater/assets/OFL.txt)，CMU 动捕派生片段有独立来源说明；`.agents/skills` 与 `.claude/skills` 内还含 Manim 技能目录的独立 `LICENSE.txt`，须逐包处理。根目录 `assets/signal-from-tomorrow-demo.mp4`、Logo/赞助图等生成或展示媒体未见逐件可转授权清单，不复制。Python [requirements](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/requirements.txt) 与 Remotion npm 依赖各自有许可；[npm 锁文件](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/remotion-composer/package-lock.json) 出现 `Remotion License` 和 `UNLICENSED` 项，自动化商业产品须按 [Remotion 官方授权说明](https://www.remotion.dev/docs/license) 分别核查，不能按根 AGPL 推定。Pexels/Pixabay/Archive/模型服务及下载媒体还受来源条款、人物/商标/音乐权利和 API 约定约束，[环境变量清单](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/.env.example) 可见多家供应商，不因项目 AGPL 自动授权输出或模型。对本报告建议的纯设计借鉴，License 影响为“不复制上游受保护表达”；若要代码复用，须重新开许可证审查。

## 17. 当前项目缺失能力

当前项目在现有业务闭环中缺少的是“生成/导入候选前的费用与外发范围确认”“短视频承诺与实际运动素材差异提醒”“审核前可解释的质量检查摘要”，而非缺一个 Agent Pipeline。第一项要与当前供应商显式选择/披露和运行冻结绑定；第二项是领域提示，不自动否决；第三项补充当前审片而非改写决定。[现有分析供应商边界](../../backend/app/analysis_provider.py)、[当前审核](../../backend/app/batch_production.py)、[当前运行](../../backend/app/durable_runs.py)、[上游承诺](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/lib/delivery_promise.py)。

## 18. 可以删除或替换的当前实现

**无。** 现有 `durable_runs.py`、`batch_production.py`、`timeline_render.py`、`preproductionWorkspaceStore.ts` 各自承担明确职责；没有上游证据证明可用同等范围替换。未来新增提示只嵌入现有路径，不删除项目/镜头/审核历史。[当前运行](../../backend/app/durable_runs.py)、[当前生命周期](../../backend/app/batch_production.py)、[当前渲染](../../backend/app/timeline_render.py)、[当前 Store](../../frontend/src/preproductionWorkspaceStore.ts)。

## 19. 融合后的模块架构

概念层：`ContentProject`/`ProjectAssets` → 当前制作镜头或短视频变体 → 当前冻结运行与 Timeline → 当前预览/审核/交付。新增的质量提示只读取同一版本快照，写入同一对象的“建议/检查结果”，不成为审核状态。成本提示挂在**实际调用前**的现有服务边界，取得显式选择与预算确认后才由既有队列执行；不能仅依赖 Agent 文案。上游可追溯性启发以当前运行 ID、输入指纹、预览 ID 和审核版本表达。[当前运行契约](../../backend/app/durable_runs.py)、[当前业务生命周期](../../backend/app/batch_production.py)、[当前项目权威 ADR](../adr/0009-content-project-and-workflow-boundaries.md)。

## 20. 融合后的用户流程

用户进入内容项目，确认简报与素材；如采用外部付费/外发分析或生成，先看到模型、预计费用和将发送的素材范围；获得确认后在当前运行路径执行。生成的脚本、镜头/混剪建议先作为候选，经人确认才成为规范制作镜头或变体编辑。当前时间线渲染形成预览；质量检查给出可读问题清单，审核者仍针对该**具体预览**作决定，批准后才能导出。回看使用现有运行/审核历史，不用 Backlot 状态代替版本链。[当前镜头 API](../../backend/app/preproduction_api.py)、[变体生命周期](../../backend/app/batch_production.py)、[时间线 API](../../backend/app/timeline_api.py)。

## 21. 对当前代码目录的影响

本次**只新增本报告**。未来若落实“重新实现”建议，优先在现有 `backend/app/batch_editing_api.py` 的请求/预检、`backend/app/batch_production.py` 的预览检查摘要、`backend/app/durable_runs.py` 的冻结输入、`frontend/src/ContentStudioApp.tsx` 的确认与提示中接入。只有确认多处调用后才新增窄范围检查模块；不创建 `pipeline_defs/`、`skills/pipelines/`、`backlot/` 或第二 checkpoint 目录。[当前编辑 API](../../backend/app/batch_editing_api.py)、[当前工作室](../../frontend/src/ContentStudioApp.tsx)。

## 22. 数据模型调整

P0 无持久化迁移。若后续落实质量提示，在现有预览/运行记录上保存输入版本、检查算法版本、时间与问题列表；检查结论独立于 `review_status`，旧预览失效时旧检查也只供历史查看。若落实费用确认，冻结 provider/model、外发素材清单、预计/实际费用和授权时间到现有运行记录，不复制上游 `cost_log` 或 `checkpoint` Schema。任何 Scene/Shot 引用使用已有 ID，继续符合 ADR 0009/0010。[现有版本关系](../../backend/app/batch_production.py)、[现有镜头类型](../../frontend/src/preproductionApi.ts)、[上游成本结构](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tools/cost_tracker.py)。

## 23. Store 调整

P0 无 Store 改造。未来 UI 只在现有 Zustand `preproductionWorkspaceStore`/工作室状态中展示候选、检查摘要和确认中状态，服务器的项目/镜头/变体版本仍为权威；禁止创建 Agent 专属 Shot、Storyboard 或另一套阶段状态 Store。Backlot JS 的时间切片不能直接移到当前 Store 当业务状态。[当前 Store](../../frontend/src/preproductionWorkspaceStore.ts)、[上游回放](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/backlot/ui/board.js#L891-L1050)。

## 24. API 调整

P0 不新增 API。若质量提示落地，沿现有项目/变体版本接口扩展只读预检结果与明确“提示非审核”的字段；计算走现有本地运行快照。若费用确认立项，在现有供应商请求前新增冻结的估算/确认步骤及错误分支，批准凭证绑定项目、输入指纹与供应商，拒绝迟到结果复用。不得暴露上游 Backlot 文件路径 API、Agent stage API 或 `/approve` 平行审核端点。[当前编辑 API](../../backend/app/batch_editing_api.py)、[当前运行](../../backend/app/durable_runs.py)、[上游看板 API](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/backlot/server.py#L165-L305)。

## 25. P0 / P1 / P2 实施计划

| 优先级 | 决策与验收 | 累计关系 |
| --- | --- | --- |
| P0 | 维持“不复制 AGPL 源码/Schema/技能文本”门槛；现有项目、Shot、运行、Timeline、审核唯一权威不变。若做成本提示，先盘点**现有**收费调用是否有自动阻断，再定义调用前确认；验证拒绝后不调用、费用变化后需重确认。 | 不抢 [OpenShorts](./openshorts-current-project-integration-audit-2026-09-28.md) 的候选片段和 [MPT](./moneyprinterturbo-current-project-integration-audit-2026-09-28.md) 的编排 P0 |
| P1 | 以“重新实现”在当前预览路径加入技术探针/承诺差异/重复画面提示，记录算法版本和输入快照；验证同一预览、重渲染、坏音频、静图降级与人工审核互不混淆。 | 不引入 Remotion、上游 QA Schema 或第二审核门禁；[opensource-clipping](./opensource-clipping-current-project-integration-audit-2026-09-28.md) 只补构图失败样例 |
| P2 | 在 Agent 分镜产品范围明确后，借鉴阶段交付物的可解释性；候选经人工确认转入同一 Shot。若关系图编辑开放，遵循 [Toonflow](./toonflow-current-project-integration-audit-2026-09-28.md) 与 ADR 0010；如需 ComfyUI 交换，遵循 [ComfyStudio](./comfystudio-current-project-integration-audit-2026-09-28.md) 的范围界线。 | 不建立第二 Pipeline、Storyboard、队列或终局 AI 生成提交 |

**验证口径：** 上游审计通过固定提交检出和静态文件/调用点检索；上游确有 `tests/` 自动化测试（例如[前置门禁](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tests/lib/test_checkpoint_prerequisites.py)、[成本](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tests/tools/test_cost_tracker_governance.py)、[变化检查](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tests/lib/test_variation_checker_runs.py)），但本次未运行上游或本项目测试，未用 README 声称的数量或质量替代源码证据。以上 P0/P1/P2 是后续实施建议，非本次已实现功能。
