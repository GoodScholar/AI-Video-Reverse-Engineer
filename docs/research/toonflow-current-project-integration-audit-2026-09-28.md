# Toonflow 与当前内容工作室的源码级融合审计

> 本报告是 Toonflow 单项目深审，不取代此前项目结论。九个参考项目的累计能力归属、冲突消解和优先级统一维护在 [参考项目累计融合决策登记表](./reference-project-integration-register.md)。

审计日期：2026-09-28。参考项目固定为 [`HBAI-Ltd/Toonflow-app@d86cce6b689916ab73f75bc4c1f42a2a7424516c`](https://github.com/HBAI-Ltd/Toonflow-app/tree/d86cce6b689916ab73f75bc4c1f42a2a7424516c)（`master`）；当前项目固定为 `e9bc5431b999f951627c904edeaf489028c854ab`。以下结论来自两份源码的静态审查；未运行 Toonflow，也没有做真实用户、性能或生成质量实测。下文“未见”仅指这个固定提交内未发现，不等于项目永远没有此能力。

## 1. 参考项目核心能力

Toonflow 面向短剧、漫剧和短视频作者，把工作目录中的剧本、媒体资产、图像/视频生成节点与 Agent 对话放在画布和文档两个面板中。主线是选择本地/服务器工作区 → 创建或切换画布 → 导入文件、添加/连接节点 → 配置模型并生成媒体 → 从素材库查看结果；Agent 可以读写工作区并控制画布。[路由](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/router/index.ts)、[工作区页面](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/pages/workspace/index.vue)、[画布](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/pages/workspace/panels/canvas/index.vue)、[媒体生成](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/server/src/utils/media/generation.ts)。

核心模块是首页/首次配置、画布、文档、素材树、节点包、媒体模型配置、Agent 对话及团队、插件/MCP、FFmpeg 与桌面端。最有价值的是可操作的画布交互、节点端口类型校验、按模型能力组织媒体生成输入、工作区文件安全边界。它不是一个已经具备完整剪辑时间线、审片和交付生命周期的产品；源码中的 `storyboardTeam` 产出文字分镜，不等于持久化镜头制作系统。[节点目录](https://github.com/HBAI-Ltd/Toonflow-app/tree/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodes)、[分镜团队](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/teams/storyboardTeam/team.json)、[分镜技能](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/teams/storyboardTeam/skills/storyboard/SKILL.md)。

## 2. 当前项目核心能力

当前产品是本地内容工作室：内容项目下分开承载商品视频制作与参考复刻。正式入口有项目中心、商品制作、项目素材、审片、精修、参考复刻、本地工具、设置；商品链从简报与候选脚本，经人工确认、本地配音、批量变体/剪辑、预览、审核到 MP4 导出。参考复刻还具备本地预处理、语义分析、镜头前置、深度/人物控制和工作流包输出，但最终 AI 视频生成留给外部工具。[正式页面](../../frontend/src/ContentStudioApp.tsx)、[内容工作流](../../backend/app/content_workflow.py)、[批量生命周期](../../backend/app/batch_production.py)、[产品边界 ADR](../adr/0009-content-project-and-workflow-boundaries.md)。

当前镜头域有 `PreproductionShot`、`ShotResultVersion`、双视图列表/关系图；API 返回 `WorkflowNode/Edge` 和 `CanvasLayout`，但当前由后端投影/初始化生成，尚非持久化编辑数据。编辑域有音视频轨、片段、时间线预检、FFmpeg 渲染；生成域有可恢复运行状态与版本快照。[领域类型](../../frontend/src/preproductionApi.ts)、[关系图](../../frontend/src/WorkflowCanvas.tsx)、[当前投影生成](../../backend/app/preproduction_api.py)、[时间线](../../frontend/src/timelineApi.ts)、[运行契约](../../backend/app/durable_runs.py)。

## 3. 两个项目定位差异

| 维度 | Toonflow | 当前项目 | 融合判断 |
| --- | --- | --- | --- |
| 主要用户 | 以画布组织短剧/漫剧创意与 AI 媒体的作者 | 商品短视频制作人员及参考复刻操作者 | 共享素材/镜头操作，主任务不同 |
| 核心对象 | 文件工作区、画布 JSON、动态节点与输出 | 内容项目、稳定素材 ID、镜头、编辑序列、审核/运行；关系图目前为投影 | 不能让画布文件成为第二权威对象 |
| 主要产出 | 画布中的文档与生成媒体 | 经版本确认的预览、成片、交付包 | 保留当前交付生命周期 |
| 最终 AI 生成 | 直接调用配置的图片/视频模型 | 参考复刻最终生成刻意放在外部 | 引入生成入口需另立产品决策 |

依据：[Toonflow 工作区 Store](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/stores/workspace.ts)、[媒体生成服务](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/server/src/utils/media/generation.ts)、[当前产品 ADR](../adr/0009-content-project-and-workflow-boundaries.md)、[画布 ADR](../adr/0010-shot-workflow-complementary-views.md)。

## 4. 功能模块对比

Toonflow 的 `/hello`、`/home`、`/workspace` 是少量路由，工作区内切换画布/文档，设置与 Agent 作为叠层；当前项目的正式页面围绕项目内的制作步骤和审片/精修组织。前者的自由编排适合探索，后者的明确步骤适合高频商品成片。素材均能上传/预览，但上游主要是文件树和相对路径，当前是项目资产、角色和稳定引用。Toonflow 有可编辑生成节点；当前关系图是镜头模型的受约束投影，节点执行由 Python 后端拥有。上游没有发现剪辑 Timeline、生成作业队列的持久化实体、多人同时编辑/用户 RBAC；其 Agent 可委派子 Agent 并暴露 MCP/A2A，但这不是多人协作文档。当前有运行/审核历史和镜头结果版本，也没有真正的用户登录/RBAC；上游的本地工作区请求限制与 MCP 凭证、当前的本机写请求意图校验均是调用边界，不是用户权限角色。[上游页面](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/pages/workspace/index.vue)、[上游素材库](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/pages/workspace/panels/canvas/components/assetLibrary.vue)、[上游 Agent](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/server/src/agent/runtime/index.ts)、[上游工作区边界](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/server/src/utils/workspace/index.ts)、[当前工作室](../../frontend/src/ContentStudioApp.tsx)、[当前镜头执行](../../backend/app/preproduction_api.py)、[当前审核历史](../../backend/app/batch_production.py)。

## 5. 业务逻辑对比

Toonflow 将节点输出保存在工作区文件中，以节点连接传递 `STRING/IMAGE/VIDEO/AUDIO` 等类型；生成调用按提供商和模型路由，结果写到 `assets/generated`。其画布撤销仅覆盖结构、名称，明确不覆盖节点内部内容或外部文件。当前项目则把业务内容版本、镜头结果采用、预览审核、交付有效性放在独立对象中；晚到的执行结果不能覆盖新快照。因此可吸收端口兼容思想，但运行完成不能自动视为“采用”或“获批”。[上游值类型](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodeScaffold/src/values.ts)、[上游撤销](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/pages/workspace/panels/canvas/useCanvasHistory.ts)、[当前运行契约](../../backend/app/durable_runs.py)、[当前审核](../../backend/app/batch_production.py)。

## 6. UI / UX 对比

上游的节点菜单、搜索、框选/多选、分组、快捷键、自动排布、拖入素材和并排 Agent 适合复杂探索；当前列表/受约束关系图、精修、真实时间线以及审片入口更贴合既定内容制作。上游视觉使用 Vue/Element Plus/TDesign 与自身变量；当前使用 React、Lucide、普通 CSS 和已确认设计体系。可复用交互规则与结构层级，界面应遵守当前 [`DESIGN.md`](../../DESIGN.md)（仓库实际文件名为大写；AGENTS.md 中写 `design.md`）。[上游画布控件](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/pages/workspace/panels/canvas/components/canvasControls.vue)、[上游快捷键](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/lib/canvasShortcuts.ts)、[当前镜头布局](../../frontend/src/ShotWorkspaceLayout.tsx)。

## 7. 技术架构对比

Toonflow 是 Bun workspace 单仓：Vue 3 + Vue Flow + Pinia 前端，Express 5 + Bun 后端，动态节点/工具包、提供商包、MCP、Electrobun 桌面包装；数据主要落工作区文件与 `conf` 设置，源码未见 SQL schema/migration。当前是 Vite + React 18 + XYFlow + Zustand 与 Python FastAPI 本地服务，项目 JSON/项目文件、FFmpeg 和独立 worker。`@vue-flow/core` 与 `@xyflow/react` 概念近但组件/状态接口不可直接互换；Bun/Express 的运行时服务也不能复制进 Python API。[上游根包](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/package.json)、[上游前端依赖](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/package.json)、[上游服务依赖](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/server/package.json)、[当前前端](../../frontend/package.json)、[当前后端](../../backend/pyproject.toml)。

API 与性能也有实质差异：上游 Express 路由按文件、插件、Agent、MCP 分成大量 `/api/*` 端点，通用画布把 `only-render-visible-elements` 显式设为 `false`，素材树递归列目录；当前 FastAPI 路由围绕项目对象，镜头列表使用虚拟滚动，XYFlow 关系图启用 `onlyRenderVisibleElements`，渲染运行留快照和恢复状态。这些只说明性能设计取向；没有大项目基准，不能宣称任一方实际更快。上游媒体生成路由是等待提供商返回后的单次请求与请求级取消；未见与当前 `durable_runs.py` 同等的生成任务恢复/审片契约。[上游路由表](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/server/src/router.ts)、[上游画布渲染](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/pages/workspace/panels/canvas/index.vue)、[上游素材枚举](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/server/src/routes/assets/list.ts)、[上游生成路由](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/server/src/routes/ai/media/generate.ts)、[当前虚拟列表](../../frontend/src/SceneShotList.tsx)、[当前关系图](../../frontend/src/WorkflowCanvas.tsx)、[当前运行契约](../../backend/app/durable_runs.py)。

## 8. 数据模型对比

上游 `Project={directory,name,lastOpenedAt}`、Canvas=`{id,name,flow:{nodes,edges,viewport}}`、节点输出是媒体 URL/MIME；Agent 会话存 `.agent/sessions/*.jsonl`。当前项目 `Project.id` 关联素材、镜头、运行与审核，`PreproductionWorkspace` 为 schema v2，`ShotResultVersion` 持有结果关联快照，`TimelineClip` 持有源区间与轨道时序。路径与项目 ID、画布节点与规范 Shot、画布坐标与时间线秒数都不是同一个数据键，不能互作数据库迁移。[上游项目](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/stores/workspace.ts)、[上游画布文件](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/pages/workspace/panels/canvas/components/canvasMenu.vue)、[上游会话](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/server/src/agent/runtime/index.ts)、[当前镜头类型](../../frontend/src/preproductionApi.ts)、[当前剪辑类型](../../frontend/src/timelineApi.ts)。

## 9. 当前项目已有优势

当前项目已有 Toonflow 固定提交中未见的正式多轨剪辑/FFmpeg 可验证渲染、商品脚本确认、批量变体、配音/字幕、逐条审核、导出、过期判定、项目备份以及复刻专用前处理。镜头列表与关系图共享一份领域状态，符合既定 ADR。覆盖这些领域的现有 Python/React 测试明显多于上游源码中的自动测试；上游 `apps`/`packages` 源码未检出常见 `*.test.*` 或 `*.spec.*` 文件，不能据此断言上游未经其他方式测试。[当前渲染](../../backend/app/timeline_render.py)、[批量流程](../../backend/app/batch_production.py)、[项目备份](../../backend/app/project_backup.py)、[画布 ADR](../adr/0010-shot-workflow-complementary-views.md)、[上游包脚本](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/package.json)。

## 10. 参考项目优势

上游画布是可编辑操作面，具备动态节点、连线校验、成组移动、撤销/重做和 Dagre 排布；当前关系图主要展示依赖并定位到既有流程，尚未把画布作为编辑入口。上游媒体生成支持提供商模型元数据、多参考图/视频/音频、首尾帧等输入，当前最终 AI 视频生成被产品边界限制。上游工作区文件操作包含路径归一、符号链接防护与并发锁，适合作为当前安全审查对照。[上游连线](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodeScaffold/src/connection.ts)、[上游分组](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/pages/workspace/panels/canvas/selectionNodes.ts)、[上游排布](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/pages/workspace/panels/canvas/arrangeCanvas.ts)、[上游文件边界](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/server/src/utils/workspace/files.ts)、[当前关系图](../../frontend/src/WorkflowCanvas.tsx)。

## 11. 功能映射表

“技术兼容度”衡量能否接入当前 React/FastAPI 与单一领域模型，不评价想法好坏；处理方式严格使用指定六类。

| 参考功能 | 当前对应 | 业务一致度 | 技术兼容度 | 处理方式 | 理由/阶段 |
| --- | --- | --- | --- | --- | --- |
| 工作区首页/项目列表 | 项目中心 | 中 | 低 | 只参考设计 | 当前 ID 与项目生命周期更完整 |
| 画布/文档面板切换 | 镜头列表/关系图 | 中 | 中 | 只参考设计 | 保留双视图，单一镜头状态 |
| Vue Flow 节点内核 | XYFlow 关系图 | 中 | 低 | 不引入 | 已有等价 React 库 |
| 端口类型兼容判断 | `WorkflowEdge`/节点目录 | 高 | 中 | 迁移后适配 | 可抽取纯函数，需映射端口类型 |
| 连线回溯匹配 | 当前未开放多节点连接 | 中 | 中 | 部分复用 | 若启用画布编辑，再移植算法 |
| 分组/父子节点根集合 | 场景/镜头层次与多选 | 中 | 中 | 部分复用 | 算法可改写，不能搬 Vue 节点对象 |
| 画布结构撤销/重做 | 当前选择/布局状态 | 低 | 低 | 只参考设计 | 上游不覆盖媒体与业务版本 |
| Dagre 自动排布 | 关系图布局（当前未持久化） | 中 | 中 | 部分复用 | 保留图排序策略，换 React 渲染适配 |
| 快捷键解析/冲突判断 | 关系图固定快捷键 | 中 | 高 | 迁移后适配 | 纯 TS 可用，删去未开放动作并接当前键盘边界 |
| 素材拖入并建节点 | 项目素材 → 镜头引用 | 中 | 低 | 只参考设计 | 上游文件路径；当前稳定 asset ID |
| 素材树搜索/预览 | 项目素材页 | 高 | 低 | 只参考设计 | Vue UI 不移植；可借交互 |
| 图片/视频/音频基础节点 | 前置处理节点 | 中 | 低 | 不引入 | 当前已有 Python 节点与角色约束 |
| 生成节点状态 Hook | 持久化运行 | 中 | 低 | 只参考设计 | 上游是组件会话状态，非 durable run |
| 多模态生成参数 Schema | 分析服务/外部工作流 | 中 | 中 | 部分复用 | 可参考输入能力矩阵，需 Python 契约 |
| 媒体下载与 MIME 检查 | 本地素材/渲染校验 | 高 | 低 | 只参考设计 | 当前已有后端验证，Bun 模块不可搬 |
| Agent 分镜团队 | 镜头计划/脚本候选 | 中 | 低 | 只参考设计 | 文本方案有价值，输出不等于当前 Shot |
| Agent 对话/子 Agent | 暂无通用工作区 Agent | 低 | 低 | 不引入 | 引入完整运行时超出当前范围 |
| MCP 控制/插件市场 | 暂无对应扩展平台 | 低 | 低 | 不引入 | 授权与沙盒模型不一致 |
| FFmpeg 浏览器远程协议 | Python 本地 FFmpeg | 高 | 低 | 不引入 | 已有可验证渲染与任务恢复 |
| 桌面升级器/安装包 | 本地 Web 应用 | 低 | 低 | 不引入 | 发布形态不同 |
| 软件/插件版本显示 | 成片与方案版本 | 低 | 低 | 不引入 | 上游是插件/软件版本，非镜头结果版本 |
| 时间线 | 本地真实时间线 | 不适用 | 不适用 | 不引入 | 上游固定提交未见可迁移的剪辑时间线 |
| 审片/交付 | 审核、导出与历史 | 不适用 | 不适用 | 不引入 | 保留当前已实现流程 |

证据入口：[上游画布](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/pages/workspace/panels/canvas/index.vue)、[上游生成 schema](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/tools/mediaGeneration/src/runtime.ts)、[当前领域类型](../../frontend/src/preproductionApi.ts)、[当前运行契约](../../backend/app/durable_runs.py)。

## 12. 可以直接复用的代码

在**当前**业务边界和技术栈下，没有确认可把整份 Toonflow 文件原样放入当前项目的模块。MIT 许可允许复制，但“许可允许”不等于数据/框架适配完成。特别是上游 Vue 组件、Pinia Store、Bun/Express 路由、Agent 会话与当前 React/FastAPI 模型不兼容。唯一接近原样复用的是 `isTypeCompatible` 的纯字符串集合判断；当前端口尚无同构的 `dataType` 契约，因此必须先补受控端口目录，届时按第 13 节迁移，不能本轮写成无条件“直接复用”。[上游函数](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodeScaffold/src/connection.ts)、[当前端口表示](../../frontend/src/preproductionApi.ts)。

## 13. 可以迁移后适配的代码

以下是**建议实施时的文件级目标**，均为计划，尚未修改产品代码。上游所有列出的原创源码受根目录 MIT 许可覆盖；迁移实质代码时须保留版权与许可文本，并检查带入依赖自身的许可。目标文件名是建议位置，不表示已存在。

| 上游固定文件 → 当前目标文件 | 复用内容及原因 | 必须修改/依赖 | 对现有逻辑与测试 |
| --- | --- | --- | --- |
| [`packages/nodeScaffold/src/connection.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodeScaffold/src/connection.ts) → `frontend/src/workflowConnectionTypes.ts` | 迁移 `getDataTypes/isTypeCompatible`；纯 TS，媒体端口兼容语义相近 | 去掉 Vue Flow `ValidConnectionFunc`，以当前受控端口目录映射类型；前后端契约要一致 | 不替换既有边数据；新增同型、异型、通配符、非法端口测试；MIT 可用 |
| [`apps/web/src/pages/workspace/panels/canvas/selectionNodes.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/pages/workspace/panels/canvas/selectionNodes.ts) → `frontend/src/workflowSelectionTree.ts` | 保留“仅取被选节点根”“遍历树”的核心逻辑 | `parentNode` → 当前 `parentId`/场景镜头关系；排除自由组拖拽，遵守规范 Shot 身份 | 与现有多选合并，不增第二 Store；测嵌套、循环保护、范围选择；MIT 可用 |
| [`apps/web/src/pages/workspace/panels/canvas/arrangeCanvas.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/pages/workspace/panels/canvas/arrangeCanvas.ts) → `frontend/src/workflowAutoLayout.ts` | 保留 Dagre 连通分组、尺寸校验、横排策略 | 改 `@vue-flow/core`/`nextTick` 为 `@xyflow/react` 与纯图输入；若引 `@dagrejs/dagre` 核其许可 | 只更新 `CanvasLayout`，不能改变镜头 `rank`；测组顺序与稳定结果；MIT 原创代码可用 |
| [`apps/web/src/lib/canvasShortcuts.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/lib/canvasShortcuts.ts) → `frontend/src/workflowShortcuts.ts` | 迁移纯 TS 的绑定解析、事件匹配、按住键判断与标签格式化 | 默认动作中成组、自由节点复制等当前未开放；与 `WorkflowCanvas.tsx` 现有快捷键及输入框忽略规则合并，不增一套监听器 | 仅在 P1 需要可配置/复杂快捷键时实施；测 Mac/Windows 修饰键、冲突、输入框豁免；MIT 可用，无新增 Vue 依赖 |

这些是“**保留核心逻辑，重新实现当前项目适配层**”，而非整模块搬入。[当前投影](../../frontend/src/workflowCanvasProjection.ts)、[当前状态](../../frontend/src/preproductionWorkspaceStore.ts)。

## 14. 可以部分复用的代码

| 上游固定文件 → 建议目标 | 可复用部分 | 不复用部分与前提 |
| --- | --- | --- |
| [`apps/web/src/pages/workspace/panels/canvas/selectionConnections.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/pages/workspace/panels/canvas/selectionConnections.ts) → `frontend/src/workflowConnectionPlanner.ts` | 少量端口的回溯匹配算法 | Vue Flow `GraphNode/GraphEdge` 与 `validateConnection` 适配需重写；先确定多节点接线需求；测多候选冲突、环/业务约束；MIT 可用 |
| [`apps/web/src/pages/workspace/panels/canvas/canvasDrop.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/pages/workspace/panels/canvas/canvasDrop.ts) → `frontend/src/workflowAssetDrop.ts` | MIME→媒体类别和拖拽位置信息思路 | 不读全局 `/api/assets/read`、不以路径当身份；使用当前项目 `assetId` 与权限/校验；测误报 MIME、跨项目资产；MIT 可用 |
| [`packages/tools/mediaGeneration/src/runtime.ts`](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/tools/mediaGeneration/src/runtime.ts) → `frontend/src/generationInputCapabilities.ts`；对应服务契约在 `backend/app/provider_models.py` | 多参考、首尾帧、时长/画幅等字段目录思想 | Zod schema、服务端 Bun 媒体生成不可直接复用；仅当决定开放最终生成时实施；测供应商能力与请求拒绝；MIT 原创代码可用 |

任何迁移都沿用当前命名、错误展示、`X-AIVRE-Intent`、项目 ID、权限/本机调用边界、CSS token 与测试习惯；不并存两套 Store/API。依赖条件详见上表与第 24 节。[当前 API 调用风格](../../frontend/src/preproductionApi.ts)、[当前服务边界](../../backend/app/preproduction_api.py)。

## 15. 只建议参考设计的功能

素材库的搜索/预览/拖拽反馈、画布节点菜单和快捷键说明、文档与画布切换时先保存、Agent 给出的文字分镜应标明“原设定/创作补充/待确认”，都值得吸收为交互规范。对应 UI 组件是 Vue/Element Plus，接口是工作区文件，不宜直接复制。可在当前镜头列表/素材页做同等体验，并遵守现有设计变量。[上游素材库](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/pages/workspace/panels/canvas/components/assetLibrary.vue)、[工作区保存](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/pages/workspace/index.vue)、[分镜技能](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/teams/storyboardTeam/skills/storyboard/SKILL.md)。

## 16. 不建议引入的功能

不建议引入 Vue 画布内核、Pinia 项目列表、独立文件工作区、插件市场、通用 Agent 会话与子 Agent、MCP 控制服务、Electrobun 桌面升级器、Node/Bun FFmpeg 远程协议。它们会与当前已存在的 React 画布、项目存储、FastAPI、运行快照和本地 FFmpeg 形成两套运行/授权/状态机制。`storyboardTeam` 可作分镜提示词参考，不迁入团队运行框架。没有证据证明上游有更成熟的成片时间线或审核历史可替换当前实现。[上游目录与路由](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/server/src/router.ts)、[当前时间线](../../frontend/src/TimelineEditor.tsx)、[当前运行契约](../../backend/app/durable_runs.py)。

## 17. 当前项目缺失能力

与 Toonflow 相比，当前关系图缺少直接建连线/节点、端口类型反馈、自动布局、画布结构撤销、素材拖入后建立镜头引用，以及面向创作的 Agent 对话。前四项可考虑为**受约束的镜头图编辑**；素材拖入应成为资产引用命令。Agent 与最终生成是产品范围决定，不应因开源项目已实现就视作 P0 缺陷。[当前只读关系图](../../frontend/src/WorkflowCanvas.tsx)、[上游交互](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/pages/workspace/panels/canvas/index.vue)、[当前边界 ADR](../adr/0009-content-project-and-workflow-boundaries.md)。

## 18. 可以删除或替换的当前实现

**本轮无立即删除项。** 若将来开放画布编辑，应扩展 `WorkflowCanvas` 与 `preproductionWorkspaceStore` 的同一数据/命令路径，不新增第二画布文件或第二个镜头 Store；可替换 `workflowCanvasProjection.ts` 中仅针对可视节点的固定排布计算，且须先让后端持久化布局与受约束图变更，再保留投影与稳定 ID。当前页面中 `?workspace=legacy` 是明确兼容入口，移除须另行验证迁移及历史访问，不属于本研究授权范围。[当前关系图](../../frontend/src/WorkflowCanvas.tsx)、[投影](../../frontend/src/workflowCanvasProjection.ts)、[后端临时布局](../../backend/app/preproduction_api.py)、[兼容入口](../../frontend/src/ContentStudioApp.tsx)。

## 19. 融合后的模块架构

```text
ContentProject（唯一项目身份）
├─ ProjectAsset / 素材文件（唯一素材身份）
├─ PreproductionWorkspace v2（唯一镜头/场景/流程图）
│  ├─ SceneShotList ←→ WorkflowCanvas（同一 Zustand 投影/命令）
│  ├─ PortCatalog + ConnectionValidator（可选 P1）
│  └─ CanvasLayout / AutoLayout（当前为投影；P1 才持久化视图坐标）
├─ TimelineWorkspace（唯一播放时间与轨道）
├─ BatchTask / Variant / Run / Review / Export（现有生命周期）
└─ Local FastAPI + FFmpeg / provider adapter（现有执行边界）
```

这个设计保持既有“计划、编辑序列、运行、审核”分界；Toonflow 贡献端口/布局/交互算法，不拥有新增业务真相。[当前 ADR](../adr/0009-content-project-and-workflow-boundaries.md)、[当前双视图 ADR](../adr/0010-shot-workflow-complementary-views.md)、[上游端口](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodeScaffold/src/connection.ts)。

## 20. 融合后的用户流程

保留“项目 → 资料/素材 → 脚本确认 → 镜头计划（列表为排序与批处理，关系图为依赖）→ 选声/混剪 → 预览 → 审核 → 导出”。P1 若开放关系图编辑，用户从项目素材拖入引用，选择受控节点端口建立依赖，服务端验证并保存到同一 `PreproductionWorkspace`，列表立即同步；自动布局只改坐标。任何 AI 文本分镜先转成候选方案并人工确认，再生成规范 Shot，不能绕过现有审核。依据：[现有内容工作流](../../backend/app/content_workflow.py)、[镜头状态](../../frontend/src/preproductionWorkspaceStore.ts)、[上游分镜约束](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/teams/storyboardTeam/skills/storyboard/SKILL.md)。

## 21. 对当前代码目录的影响

P0 只需研究后续决定，不需改产品目录。P1 若批准图编辑，可新增 `frontend/src/workflowConnectionTypes.ts`、`workflowSelectionTree.ts`、`workflowAutoLayout.ts`，修改 `WorkflowCanvas.tsx`、`preproductionWorkspaceStore.ts`、`preproductionApi.ts` 与 `backend/app/preproduction_api.py`；各自测试留在当前 `*.test.ts(x)` / `backend/tests` 体系。不得另建 `toonflow/` 子树、Vue 前端或 Bun 服务。P2 若开放媒体生成，再评估 `generationInputCapabilities.ts` 与 Python provider adapter。[现有前端目录](../../frontend/src/WorkflowCanvas.tsx)、[现有后端 API](../../backend/app/preproduction_api.py)、[上游 workspace 包](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/package.json)。

## 22. 数据模型调整

P0 不改模型。当前 `workflow` 与 `canvasLayout` 是 API 从镜头状态生成的投影，`PreproductionSave` 只提交 `revision/brief/shots`；P1 要先设计 v2→下一版数据迁移及持久化，再允许画布编辑。可在受控 `nodeCatalog` 补端口签名，例如 `kind/inputPorts/outputPorts`，`WorkflowEdge` 沿用现有 `source/target.nodeId/portId` 形状；连接有效性和无环规则由服务端验证，前端即时反馈。布局持久化后只写 `CanvasLayout.layoutRevision/nodes/viewport`，不触碰 Shot.rank、resultVersions、TimelineClip.start。若需要撤销，仅撤销本次明确的图编辑命令与布局，并约束异步产物；不能照搬上游“结构撤销但节点内容不撤销”语义。[当前类型和保存载荷](../../frontend/src/preproductionApi.ts)、[当前投影生成](../../backend/app/preproduction_api.py)、[当前 v2 校验](../../backend/app/preproduction.py)、[上游端口类型](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodeScaffold/src/connection.ts)、[上游撤销范围](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/pages/workspace/panels/canvas/useCanvasHistory.ts)。

## 23. Store 调整

只扩充当前 `preproductionWorkspaceStore.ts` 的领域动作，如 `connectPorts`、`disconnectEdge`、`placeAssetReference`、`applyAutoLayout`；复用已有 selection source 与规范 Shot ID。服务端返回新 revision 后以同一状态更新列表/图；失败还原本次乐观修改或避免乐观提交。Pinia 的 `useWorkspaceStore` 是路径和最近目录列表，不可作为第二项目 Store；上游画布历史仅可借鉴批量动作和大小上限，具体撤销须服从当前版本契约。[当前 Store](../../frontend/src/preproductionWorkspaceStore.ts)、[上游 Store](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/web/src/stores/workspace.ts)。

## 24. API 调整

当前 `GET/PUT /api/projects/{id}/preproduction` 主要服务整个工作区，但 `PUT` 只保存 `revision/brief/shots`，图和布局是返回时生成的；节点运行与素材上传已有独立 API。P1 可增加围绕当前 revision 的图命令端点（连接、断开、布局保存），也可在现有 `PUT` 中纳入受控 workflow/layout 字段；选后者须避免允许前端覆盖服务端派生状态。服务端必须检验项目 ID、端口、环、资产归属与版本冲突；返回现有统一工作区，不引入 Toonflow 的 `/api/workspaces/files/*` 或直接按绝对目录操作。P2 媒体生成若批准，再用现有 provider/`durable_runs` 输入快照与任务状态，不能直接调用 Toonflow 同步生成路由。当前 API 层、上游 API 的调用边界与执行差异见：[当前前置 API](../../backend/app/preproduction_api.py)、[当前前端契约](../../frontend/src/preproductionApi.ts)、[上游工作区限制](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/server/src/utils/workspace/index.ts)、[上游生成路由](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/server/src/routes/ai/media/generate.ts)。

## 25. P0 / P1 / P2 实施计划

| 阶段 | 交付与依赖 | 可验证验收 | 保留/替换 |
| --- | --- | --- | --- |
| P0：确定边界 | 固定规范 Shot 与 Timeline 为权威对象；明确画布编辑的允许节点/命令和“不开放最终生成”是否继续有效；对 `nodeCatalog` 设计类型矩阵 | 产品规格与 ADR 一致；每个图命令能说明对版本/审核的影响 | 保留现有页面、时间线、审核和渲染 |
| P1：受约束图交互 | 先实现图/布局持久化与迁移，再做端口校验、项目素材引用、可选自动布局；同一 Store 与 FastAPI 保存路径 | 列表/图一致、刷新布局仍在、非法连线和跨项目素材被拒、布局不改 Shot.rank、版本冲突不覆盖、键盘与拖拽可用 | 替换固定图布局的局部实现；不并存第二画布 |
| P2：可选创作拓展 | 仅在产品决策后研究 Agent 候选分镜、供应商媒体生成；以当前持久化任务与审核接入 | 生成失败/取消/重启/迟到结果可恢复，候选经人工采用，费用与数据发送范围明确 | 继续保留本地时间线与审批链 |

**许可与复用结论。** 上游根 [`LICENSE`](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/LICENSE) 明确为 MIT，版权 `Copyright (c) 2026 HBAI-Ltd`，允许使用、修改、合并和再分发，但分发源代码/实质部分时要附原版权及许可文本。根 [`package.json`](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/package.json) 同样声明 MIT。这个结论只涵盖 Toonflow 自有代码；Vue Flow、Dagre、媒体 SDK、桌面资源或模型权重须按其各自许可另核。本轮未复制任何上游代码、未改产品文件；迁移提议均待独立实现与测试。

**审计限制。** 此为静态源码审查。未运行上游 UI、API、生成模型、桌面应用或性能基准；性能与可扩展性只根据代码形态推断。上游动态插件可能带来仓库之外的能力，不把插件市场宣传等同于固定提交内可用的业务模块。没有把“软件版本/插件版本”误判为镜头或成片版本，也没有把 Agent 文字分镜误判为正式 Storyboard 数据库。[动态插件入口](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/server/src/router.ts)、[分镜团队](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/teams/storyboardTeam/team.json)。
