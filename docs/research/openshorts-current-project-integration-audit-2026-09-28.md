# OpenShorts 与当前项目源码级融合审计（2026-09-28）

> **静态审计限制。** 本文基于 OpenShorts 固定提交 [`89f030934388d5167b3b0ede164ef893dad09ae9`](https://github.com/mutonby/openshorts/tree/89f030934388d5167b3b0ede164ef893dad09ae9) 的源码，以及当前仓库 `HEAD=e9bc5431b999f951627c904edeaf489028c854ab` 加审计时工作树源码。检查了接口、调用链、数据形状和已有测试；没有部署 OpenShorts、调用模型或实测视频质量，也没有运行两项目测试。工作树含其他人的未提交改动，本文只读使用，不把静态判断写成运行验证结论。文中“迁移”是建议，不表示已经实现。上游链接一律固定到该提交；当前链接相对本报告。

**许可边界。** 上游根目录 [`LICENSE`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/LICENSE) 是 MIT，复制实质代码须保留版权和许可文本；[`NOTICE`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/NOTICE) 明示 [`cloud/LICENSE`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/cloud/LICENSE) 覆盖 `cloud/` 全部代码，限制对第三方托管、付费再分发等用途。本文不把 `cloud/` 的鉴权、计费、数据库、发布、Autopilot 代码列为复制候选。根目录代码即便调用 `cloud/`，也只考虑剥离商业依赖后的 MIT 部分。尤其不能把根 MIT 误认为依赖自动变成 MIT：上游 [`requirements.txt`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/requirements.txt#L3) 锁定 `ultralytics==8.4.46`，[`main.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/main.py#L13) 引入 YOLO，[`Dockerfile`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/Dockerfile#L107) 拉取 `yolov8n.pt`。Ultralytics [官方许可说明](https://www.ultralytics.com/license) 称其代码与模型默认 AGPL-3.0，闭源/商业集成需核对 Enterprise 授权；因此 P2 人物检测迁移应先完成许可评估，或改用许可适配的检测器。TransNetV2 权重、字体和其他依赖也需逐项核对。

## 1. 参考项目核心能力

OpenShorts 的主链路是输入 URL/本地视频 → 下载与探测 → Whisper/Parakeet 转录，语音稀疏时走视觉路径 → 两轮模型打分和细化高光 → 词边界对齐与重叠去重 → 按场景重构竖屏 → 字幕、Hook、特效和渲染 → 单条再剪、下载及可选发布。入口在 [`app.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/app.py#L2755)，处理在 [`main.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/main.py#L1853)，候选辅助函数在 [`clip_selection.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/clip_selection.py#L1)。另有 SaaS 广告、缩略图、社媒等独立产品面，见 [`saasshorts.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/saasshorts.py) 和 [`app.py` 的 API](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/app.py#L5198)。

## 2. 当前项目核心能力

本项目以“内容项目”为本地顶层单元，分别承载参考复刻、项目素材、镜头准备、镜头关系图、时间线、批量混剪、版本化预览、审核和交付。顶层持久化模型仍名为 [`Project`](../../backend/app/main.py)，其 `referenceMedia` 与项目素材分开；[`ProjectAssets`](../../backend/app/project_assets.py) 把项目素材的 ID、文件、引用和删除边界集中管理。前置工作台 [`PreproductionStore`](../../backend/app/preproduction.py) 保存 `brief/assets/scenes/shots`，前端 [`PreproductionWorkspace`](../../frontend/src/preproductionApi.ts) 与 [`preproductionWorkspaceStore`](../../frontend/src/preproductionWorkspaceStore.ts) 投影同一批对象；[`WorkflowCanvas`](../../frontend/src/WorkflowCanvas.tsx) 使用 React Flow，布局单存。独立 [`timeline`](../../backend/app/timeline.py)、[`batch_production`](../../backend/app/batch_production.py)、[`batch_editing_api`](../../backend/app/batch_editing_api.py) 和 [`durable_runs`](../../backend/app/durable_runs.py) 分别拥有剪辑、短视频变体业务、HTTP/执行和运行状态。术语与权属遵循 [`CONTEXT.md`](../../CONTEXT.md)、[`ADR 0009`](../adr/0009-content-project-and-workflow-boundaries.md)、[`ADR 0010`](../adr/0010-shot-workflow-complementary-views.md)。

## 3. 产品定位差异

OpenShorts 聚焦“长视频自动切成多条可传播短片”，最强的是内容筛选及自动竖屏；当前项目聚焦参考素材复刻准备与内部交付团队的本地制作，AI 最终生成仍在外部工具。OpenShorts 的 `job_id + clip_index` 更像一次处理运行及其派生文件；本项目已有内容项目、参考镜头段、制作镜头、短视频变体、变体版本与审核版本，不能把 `job_id` 直接改名为内容项目，也不能把高光候选当成制作镜头或审核通过的短视频变体。依据上游 [`App.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/App.jsx#L267)、当前 [`CONTEXT.md`](../../CONTEXT.md) 与 [`ADR 0007`](../adr/0007-preproduction-with-shot-steps.md)。

## 4. 功能模块

| 上游模块 | 源码边界和实际能力 | 当前对应 |
| --- | --- | --- |
| 输入与处理 | [`MediaInput.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/components/MediaInput.jsx)、[`/api/uploads` 与 `/api/process`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/app.py#L2622)：URL/文件进入一次作业 | [`Project`](../../backend/app/main.py)、[`ProjectAssets`](../../backend/app/project_assets.py) |
| 转录与候选 | [`transcribe_backends.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/transcribe_backends.py)、[`clip_selection.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/clip_selection.py)、[`gemini_worker.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/gemini_worker.py) | [`video_toolkit`](../../backend/app/video_toolkit.py) 的 scenes/subtitles 产物、[`analysis_provider`](../../backend/app/analysis_provider.py)、[`batch_recommendation`](../../backend/app/batch_recommendation.py)；项目素材输入须补解析适配 |
| 场景与构图 | [`scene_detection.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/scene_detection.py)、[`main.py` 跟踪器](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/main.py#L109)、[`reframe_v2.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/reframe_v2.py) | [`local_preprocessing_runner`](../../backend/app/local_preprocessing_runner.py)、[`video_toolkit`](../../backend/app/video_toolkit.py)、[`timeline_render`](../../backend/app/timeline_render.py) |
| 精剪与合成 | [`ClipEditor.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/components/ClipEditor.jsx)、[`recut.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/recut.py)、[`remotion/src`](https://github.com/mutonby/openshorts/tree/89f030934388d5167b3b0ede164ef893dad09ae9/remotion/src) | [`Timeline`](../../frontend/src/TimelineEditor.tsx)、[`timeline_api`](../../backend/app/timeline_api.py)、[`batch_editing_api`](../../backend/app/batch_editing_api.py) |
| 发行与 SaaS | [`app.py` 社媒接口](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/app.py#L5198)、`cloud/`（商业许可） | 当前导出与交付版本，不含社媒代发 |

| 专项 | OpenShorts 源码结论 | 当前项目及融合判断 |
| --- | --- | --- |
| AI/Agent | [`gemini_worker.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/gemini_worker.py) 提供模型提示词/结构化响应，另有 [`mcp_server.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/mcp_server.py) 工具入口；没有可移植的自治“高光 Agent”领域层 | [`analysis_provider.py`](../../backend/app/analysis_provider.py) 是模型适配边界，保留显式选择 |
| 节点画布、镜头管理、Storyboard | 上游 [`App.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/App.jsx) 和 [`ResultCard.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/components/ResultCard.jsx) 围绕一次作业的片段卡；未见节点画布、结构化 Storyboard 或具有场景/制作镜头稳定身份的模型 | 当前 [`PreproductionWorkspace`](../../frontend/src/preproductionApi.ts) 与 [`WorkflowCanvas`](../../frontend/src/WorkflowCanvas.tsx) 已负责镜头/场景/流程节点，不能用上游卡片取代 |
| Timeline | 上游 [`ClipEditor.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/components/ClipEditor.jsx)、[`recut.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/recut.py) 是单条 clip 的 EDL 重剪，加 [`ReframeEditor.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/components/ReframeEditor.jsx) 逐场景裁切；不是多轨制作时间线 | 当前 [`timeline.py`](../../backend/app/timeline.py) 已拥有多轨、素材引用和版本校验 |
| 素材、队列、版本 | 上游 [`app.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/app.py#L545) 以作业/片段位置、输出目录、manifest、内存队列组织；自托管没有当前项目的项目素材引用与变体审核版本契约 | 当前 [`ProjectAssets`](../../backend/app/project_assets.py)、[`durable_runs`](../../backend/app/durable_runs.py)、[`batch_production`](../../backend/app/batch_production.py) 继续拥有身份、执行、审核 |
| 协作 | 上游 `cloud/` 有账户/发布运营代码，但在所审核心源码中未见多人同时编辑同一项目的协作/冲突合并领域模型；`cloud/` 不能复制 | 当前主要是内部团队使用场景，现有 [`PreproductionStore`](../../backend/app/preproduction.py) 的 revision/CAS 是本地并发边界，不应推断已有实时多人协作 |
| 性能/扩展性 | [`main.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/main.py) 与大型 [`app.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/app.py) 耦合解码、模型与处理；[`scene_detection.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/scene_detection.py) 的重模型推断、队列槽位和 [`render-service`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/render-service/src/server.ts) 跨进程渲染是主要容量/恢复边界 | 当前只借算法置于可取消的运行与文件快照内，不额外引入一个服务和内存状态源 |

## 5. 业务逻辑

高光核心**不是一个可独立拷贝的 ClipIndex 服务**。仓库没有 `ClipIndex` 类型或独立高光算法：[`clip_selection.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/clip_selection.py#L1) 是标准库辅助函数，负责按转录段建窗口、分批、排序、词边界对齐和去重；真正“哪些时段精彩”的语义判断在 [`main.get_viral_clips`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/main.py#L1853) 调用 [`gemini_worker`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/gemini_worker.py#L22) 的提示词与响应模型完成。自托管文本模型可走 [`llm_backend.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/llm_backend.py)，但视觉稀疏语音路径仍使用 Gemini 视频理解；数据库不是高光排序算法的必要依赖，队列是 HTTP 作业执行和结果生命周期的依赖。上游排序后的 `clip_index` 是列表位置而非稳定领域 ID；前端甚至须保留原始 index 避免编辑错片，见 [`App.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/App.jsx#L303)。

## 6. UI/UX

上游 [`main.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/main.jsx#L75) 用 hash 在落地页、价格、账户和应用间切换；应用内 [`App.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/App.jsx#L1124) 是 tab 状态，包含 Dashboard、Autopilot、AI Shorts、AI Agent、UGC Gallery、YouTube Studio、History、Settings。核心编辑体验由 [`ResultCard.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/components/ResultCard.jsx)、[`ClipEditor.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/components/ClipEditor.jsx)、[`ReframeEditor.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/components/ReframeEditor.jsx) 构成：候选卡片、来源转录与 EDL、逐场景裁切手动覆盖。当前项目已有 [`ContentStudioApp`](../../frontend/src/ContentStudioApp.tsx)、[`ShotWorkspaceLayout`](../../frontend/src/ShotWorkspaceLayout.tsx)、[`WorkflowCanvas`](../../frontend/src/WorkflowCanvas.tsx) 和时间线；适合借鉴“来源时段对照 + 自动建议可手调”，不适合移植第二个导航壳。若未来调整界面须按根目录 [`DESIGN.md`](../../DESIGN.md) 实施。

## 7. 技术架构

上游是 Python/FastAPI [`app.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/app.py)、媒体处理 [`main.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/main.py)、React/Vite [`dashboard`](https://github.com/mutonby/openshorts/tree/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src)，另有 Express/Remotion [`render-service`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/render-service/src/server.ts)；[`docker-compose.yml`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/docker-compose.yml) 启动三服务。`app.py` 在自托管模式用内存优先队列、作业目录/manifest 恢复；[`docker-compose.cloud.yml`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/docker-compose.cloud.yml) 才加 Postgres 与商业代码。当前项目是本地 FastAPI + React，已有 `RunPolicy` 的冻结输入/失效规则和本地运行适配 [`durable_runs.py`](../../backend/app/durable_runs.py)；若移入检测器应在现有执行边界调用，避免再设上游队列与 Remotion 进程。上游 `tests/` 共 82 个文件，其中 81 个 `test_*.py` 与 1 个 `conftest.py`；静态检索到 230 个顶层 `def test_`。这仅说明有测试资产，本文未执行 pytest，不能推断全部通过或视频质量。

| 层面 | 上游源码证据与边界 | 对接判断 |
| --- | --- | --- |
| 页面/路由 | [`main.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/main.jsx#L75) 用 hash 页面路由；[`App.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/App.jsx#L1124) 内部 tab；[`app.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/app.py#L2622) 分上传/处理/状态、clip 编辑/场景、字幕/Hook/渲染、社媒/缩略图/SaaS | 只接当前项目范围 API，不复制上游路由树 |
| 组件/状态 Hooks | [`App.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/App.jsx#L267) 主要以 `useState/useEffect/useMemo/useRef` 组织 job、结果与 tab；[`AuthContext.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/contexts/AuthContext.jsx) 服务云账户，`ClipEditor` 自带 reducer；未见独立领域 Store | 当前 [`preproductionWorkspaceStore.ts`](../../frontend/src/preproductionWorkspaceStore.ts) 是权威工作台投影，不复制会话状态 |
| 类型/契约 | [`gemini_worker.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/gemini_worker.py#L22) Pydantic 模型定义候选响应；[`remotion/src/lib/types.ts`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/remotion/src/lib/types.ts#L1) 声明字幕、Hook、效果及 Zod 运行校验 | 候选与渲染配置都需转换为当前稳定 ID/版本/时间单位 |
| 文件/数据库 | [`app.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/app.py#L38) 用 `uploads/`、`output/`、manifest 和清理配额；商业模式的 [`docker-compose.cloud.yml`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/docker-compose.cloud.yml) 启用 Postgres，归档可连对象存储 | 保留当前 [`ProjectAssets`](../../backend/app/project_assets.py) 文件权属；不依赖 cloud 表或 R2 |
| 异步任务 | [`app.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/app.py#L1465) 优先队列/并发槽、manifest 恢复和重试；[`render-service/server.ts`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/render-service/src/server.ts#L24) 的 Map 仅进程内 | 用当前 [`durable_runs.py`](../../backend/app/durable_runs.py) 的状态与冻结输入，不建立第二运行真相 |
| 发布/部署/测试 | [`app.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/app.py#L5198) 有社媒发布/日程接口，[`docker-compose.yml`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/docker-compose.yml) 提供自托管三服务，[`tests/test_clip_selection.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/tests/test_clip_selection.py) 等覆盖部分算法/接口 | 发布与云部署范围外；迁入算法时重新跑本项目的相关验证 |

## 8. 数据模型

OpenShorts 的核心数据以一次 `job_id`、返回 `clips[]`、`clip_index`、`start/end`、`predicted_score`、`source_window_id` 为中心；[`/api/clip/.../edl`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/app.py#L3848) 处理剪辑段，裁切覆盖按场景序号键控 [`reframe_v2.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/reframe_v2.py#L171)。本项目 `Project`、资产 ID、场景 ID、制作镜头 ID、流程节点 ID、变体 ID、运行 ID、预览 ID 和审核 ID 各有权属；[`PreproductionWorkspace`](../../frontend/src/preproductionApi.ts)、[`timeline.py`](../../backend/app/timeline.py)、[`batch_production.py`](../../backend/app/batch_production.py) 不应承接上游位置型索引作为外键。

## 9. 当前项目优势

项目素材可查引用、拒绝删除仍被使用的文件；制作镜头在列表、关系图和分镜板共用身份，画布布局独立；时间线轨道与渲染快照独立；批量变体的脚本/时间线/字幕修订组合决定预览是否当前，审核绑定具体预览，导出仅接受当前已批准版本。这些均有明确代码约束，见 [`ProjectAssets`](../../backend/app/project_assets.py)、[`PreproductionStore`](../../backend/app/preproduction.py)、[`workflowCanvasProjection`](../../frontend/src/workflowCanvasProjection.ts)、[`batch_production`](../../backend/app/batch_production.py#L57)、[`durable_runs`](../../backend/app/durable_runs.py)。因此当前项目更适合作融合宿主。

## 10. 参考项目优势

上游把长视频候选选择做成二轮提示词链路，含长视频覆盖、按分数截断、重叠去重和词边界修正；场景检测支持 TransNetV2 → PySceneDetect 回退；自动构图覆盖人脸/人物跟踪、双人分屏、活跃说话人和屏幕录制布局；精剪 UI 显示源片段并允许手工逐场景修正。这些是当前项目可研究的能力，证据分别为 [`main.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/main.py#L1853)、[`scene_detection.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/scene_detection.py)、[`reframe_v2.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/reframe_v2.py#L343)、[`ClipEditor.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/components/ClipEditor.jsx)。

## 11. 功能映射表

“处理方式”严格使用六种：直接复用、迁移后适配、部分复用、只参考设计、重新实现、不引入。它描述对上游实现的处置，不表示对应能力今天是否缺失。

| 上游能力 | 当前锚点 | 处理方式 | 判定 |
| --- | --- | --- | --- |
| 评分窗口分批/分数截断/候选重叠去重纯函数 | 新候选模块（输入已归一化） | 直接复用 | `score_batches/trim_to_best/dedupe_overlapping` 可原样使用 |
| 转录窗口构造/词边界对齐 | [`video_toolkit`](../../backend/app/video_toolkit.py) 的字幕产物与新候选接缝 | 迁移后适配 | 需转换词级时间结构与边界规则 |
| EDL 段校验/重基准 | [`timeline`](../../backend/app/timeline.py) | 不引入 | 当前已有不同且更严格的片段校验 |
| 两轮高光模型提示与响应 | [`analysis_provider`](../../backend/app/analysis_provider.py) | 迁移后适配 | 使用现有供应商协议与显式披露 |
| TransNetV2 场景检测 | [`video_toolkit`](../../backend/app/video_toolkit.py) 的 scenes 运行 | 迁移后适配 | 重型模型和坐标契约需隔离 |
| 活跃说话人判定 | [`person_controls`](../../backend/app/person_controls.py) | 部分复用 | 两脸静态框假设限制明显 |
| 人脸跟踪和竖屏构图 | [`video_toolkit`](../../backend/app/video_toolkit.py) | 部分复用 | 大量 `main.py`/布局模块耦合 |
| 字幕断句与 ASS 样式 | [`batch_subtitles`](../../backend/app/batch_subtitles.py) | 部分复用 | 保留当前字幕版本与预览失效 |
| Remotion 合成层 | [`timeline_render`](../../backend/app/timeline_render.py) | 只参考设计 | 当前已有 FFmpeg 预览/导出闭环，不引入第二渲染真相 |
| 来源转录/EDL 精剪交互 | [`TimelineEditor`](../../frontend/src/TimelineEditor.tsx) | 只参考设计 | 上游组件捆绑其 `job_id/clip_index` |
| 场景裁切覆盖交互 | [`ShotWorkspaceLayout`](../../frontend/src/ShotWorkspaceLayout.tsx) | 只参考设计 | 当前没有同形构图状态 |
| 高光候选的稳定 ID、来源与人工确认 | [`ProjectAssets`](../../backend/app/project_assets.py) / [`batch_production`](../../backend/app/batch_production.py) | 重新实现 | 上游位置型索引不满足领域约束 |
| 社媒代发、计费、账户、Autopilot、`cloud/` | 当前无相应工作流 | 不引入 | 范围外且商业许可边界 |

## 12. 直接复用

“直接”只指三个可原样复制的小纯函数。候选 D1：上游 [`clip_selection.py` 的 `score_batches/trim_to_best/dedupe_overlapping`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/clip_selection.py#L84) → 当前拟新增 `backend/app/highlight_candidates.py`，由新候选服务调用。复用：近均衡评分分批、按模型分数保留最佳候选、按重叠率去重；必须修改：函数体不改，但调用方先构造上游期望的内部窗口/候选结构，输出再包装为稳定 ID 与来源时码，`predicted_score` 不得当审核分数；依赖：标准库和模型提议分数；现有影响：只产建议候选，不修改制作镜头、时间线或审核；License：根 MIT，复制时附许可；测试：移植上游 [`test_clip_selection.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/tests/test_clip_selection.py) 的分批、并列分数与重叠用例，另测包装层身份与时间单位。

## 13. 迁移后适配

候选 A1：上游 [`main.get_viral_clips`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/main.py#L1853) + [`gemini_worker.py` 响应 schema/提示词](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/gemini_worker.py#L22) → 当前拟新增 `backend/app/highlight_selection.py`，供应商调用复用 [`analysis_provider.py`](../../backend/app/analysis_provider.py) 与 [`analysis_providers/`](../../backend/app/analysis_providers)，API 进入 [`video_toolkit_api.py`](../../backend/app/video_toolkit_api.py) 的项目范围。复用：窗口评分、全局 shortlist、细化、分数截断、补候选的流程规则；必须修改：模型输出契约、供应商显式选择与披露、代理上传范围、候选证据与置信度、取消/迟到结果处理，读取当前 toolkit 的 scenes/subtitles 产物；依赖：当前分析服务配置、冻结运行输入与新增安全的来源解析适配，不能要求 `cloud/` 数据库或复制上游作业队列；现有影响：候选作为来源视频上的建议，人工确认后才进入短视频变体或时间线；License：仅根 MIT 提示/逻辑可移，`cloud/` 排除；测试：模拟两阶段响应、低语音密度、重复候选、模型失败、供应商无声切换禁止和过期结果隔离。上游的视觉候选 [`get_visual_clips`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/main.py#L2041) 应单独评估授权与成本，不假定文本供应商都支持视频理解。

候选 A2：上游 [`scene_detection.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/scene_detection.py#L40) → 当前 [`video_toolkit.py`](../../backend/app/video_toolkit.py) 的 `scenes` 执行适配与 [`video_toolkit_api.py`](../../backend/app/video_toolkit_api.py) 的既有运行记录。复用：TransNetV2 推断、短场景合并和 PySceneDetect 回退；必须修改：以当前 toolkit `scenes.json` 的秒级切点契约输出，记录检测引擎、阈值和模型身份，避免上游 `FrameTimecode` 泄漏到领域对象；依赖：`transnetv2_pytorch` 权重、PyTorch、CV2、FFmpeg、PySceneDetect，安装体积及权重许可需核实；现有影响：作为可选高精度 scenes 策略，不自动把检测结果变成制作镜头，也不替换仅面向 `referenceMedia` 的现有 [`local_preprocessing_runner.py`](../../backend/app/local_preprocessing_runner.py)；License：根 MIT 源码，模型权重单独核验；测试：镜头边界、短镜头合并、帧率换算、模型失败回退、资源占用的代表性素材测试。

候选 A3：上游 [`clip_selection.py` 的 `build_transcript_windows/snap_clip_to_words`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/clip_selection.py#L203) → 当前拟新增 `backend/app/highlight_candidates.py`，由 [`video_toolkit.py`](../../backend/app/video_toolkit.py) 的字幕产物适配器供数。复用：按转录段覆盖长视频、把模型提议的切点贴合词边界；必须修改：当前 `subtitles.json` 与上游 `segments.words` 数据契约不一致时做显式转换，缺词级时间戳时退化为原提议并标注原因，统一时码与截取范围；依赖：当前 subtitles 运行产物及安全来源解析；现有影响：只生成来源范围建议，不写时间线；License：根 MIT；测试：无语音、缺词、边界词、长段与字幕时间漂移。

## 14. 部分复用

候选 P1：上游 [`active_speaker.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/active_speaker.py#L53) 与 [`split_layout.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/split_layout.py#L117) → 当前 [`person_controls.py`](../../backend/app/person_controls.py) 附近的拟建 `backend/app/speaker_focus.py`。复用：音频能量门控、每人嘴部活动归一化、判定保持时间；必须修改：上游假设固定双人面框且不是真正声纹分离，须显式标注“不确定”、使用当前人物/场景检测结果并隔离坐标系；依赖：NumPy、OpenCV、FFmpeg、双人面框；现有影响：仅提供构图建议或轨迹，不作为谁在讲话的可审计事实；License：根 MIT；测试：静音、背身、多人、嘴动与声不同步、短插话及未知判定。

候选 P2：上游 [`main.py` 的 SmoothedCameraman/SpeakerTracker](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/main.py#L109)、[`reframe_v2.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/reframe_v2.py#L343)、[`split_layout.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/split_layout.py) → 当前 [`video_toolkit.py`](../../backend/app/video_toolkit.py) 的可选“竖屏重构”处理器及 [`timeline_render.py`](../../backend/app/timeline_render.py) 的输出接缝。复用：场景级 TRACK/GENERAL/SPLIT 决策、轨迹平滑、手工裁切覆盖的几何规则；必须修改：拆开对上游 `main`、全局环境变量和文件命名的调用，换成输入素材 ID、冻结构图配置、输出项目素材及当前运行记录；依赖：人脸/YOLO 模型、CV2、FFmpeg 及可选说话人信号；现有影响：生成新项目素材或派生渲染，绝不覆写原素材/当前时间线；License：根 MIT 只覆盖上游自写源代码，Ultralytics 代码/YOLO 权重另有 AGPL-3.0 或商业许可风险，未核定前不复制其检测依赖；测试：横竖源、镜头切换、脸丢失、双人、手工覆盖优先级、输出尺寸与音画同步。

候选 P3：上游 [`subtitles.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/subtitles.py#L130) → 当前 [`batch_subtitles.py`](../../backend/app/batch_subtitles.py) / [`batch_editing_api.py`](../../backend/app/batch_editing_api.py)。复用：按词断句、ASS 高亮/排版的局部逻辑；必须修改：当前字幕 cue/修订是权威，样式需进入冻结预览输入，字幕变化必须使旧预览与审核失效；依赖：词级时间戳、字体、FFmpeg libass；现有影响：不另建上游字幕状态；License：根 MIT，随附字体许可独查；测试：中英混排、断句、时间重叠、字体缺失、预览/正式导出一致性和失效链。

## 15. 只参考设计

候选 U1：上游 [`ClipEditor.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/components/ClipEditor.jsx#L98) 与 [`ReframeEditor.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/components/ReframeEditor.jsx#L26) → 当前 [`TimelineEditor.tsx`](../../frontend/src/TimelineEditor.tsx)、[`ShotWorkspaceLayout.tsx`](../../frontend/src/ShotWorkspaceLayout.tsx) 的交互设计。复用：来源片段和转录同步、局部撤销、自动构图与逐场景人工覆盖的流程；必须修改：界面布局遵循当前 [`DESIGN.md`](../../DESIGN.md)，用当前资产/变体/时间线 ID、保存冲突和脏状态，不能引入上游 session/localStorage 为权威；依赖：现有 UI 与裁切建议 API；现有影响：提升现有视图而非新增第二编辑器；License：仅借鉴设计不复制代码，若复制片段仍按根 MIT；测试：键盘、拖拽、撤销、窄屏及来源时码一致性。

候选 U2：上游 [`ResultCard.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/components/ResultCard.jsx) 候选卡和 [`ReframeEditor.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/components/ReframeEditor.jsx) 场景预览 → 当前 [`ShotResultVersions.tsx`](../../frontend/src/ShotResultVersions.tsx) 或批量变体界面。复用：候选对比时展示来源、得分理由、裁切方案及“人工覆盖”状态的设计；必须修改：得分不能替代当前“采用/审核”，区分参考镜头段和制作镜头；依赖：候选只读 API；现有影响：保留现有审核入口；License：设计借鉴，无代码复制；测试：候选重排后身份稳定、旧审核不复活。

候选 U3：上游 [`ShortVideo.tsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/remotion/src/compositions/ShortVideo.tsx)、[`VideoEffects.tsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/remotion/src/compositions/VideoEffects.tsx) 和 [`HookOverlay.tsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/remotion/src/compositions/HookOverlay.tsx) 的字幕、Hook 与视觉效果组合，只作当前 [`TimelineEditor.tsx`](../../frontend/src/TimelineEditor.tsx) / [`timeline_render.py`](../../backend/app/timeline_render.py) 的效果设计参考。当前已有 FFmpeg 预览、渲染、取消和恢复契约；引入 Remotion 或上游 [`render-service`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/render-service/src/server.ts#L24) 会制造第二套渲染和进程内作业状态，因此不复用代码。如果后续自行实现同类效果，必须验证预览/正式导出一致性、字体许可和旧审核失效。

## 16. 不建议引入

不引入 [`cloud/`](https://github.com/mutonby/openshorts/tree/89f030934388d5167b3b0ede164ef893dad09ae9/cloud) 的账户、计费、社媒、Autopilot 和 Postgres 表：许可受限且超出本地工作台范围。也不引入 [`app.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/app.py#L1465) 的独立内存优先队列、`job_id/clip_index` 作为领域键、[`dashboard/src/App.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/App.jsx#L267) 的整体导航、[`render-service`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/render-service/src/server.ts) 的第二运行状态。上游前端把若干 Key 存入 `localStorage` 并用客户端 XOR/Base64 包装，见 [`App.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/App.jsx#L34)；当前凭据应继续走 [`credential_store.py`](../../backend/app/credential_store.py)。

## 17. 当前缺失

按本次源码检查，相对 OpenShorts 的可评估缺口是：面向长参考视频的高光候选提议及人工确认；词级来源对齐的精剪引导；可选高精度 TransNetV2 场景边界；对横屏现有视频的自动主体竖屏重构、两人构图建议与逐场景覆盖。当前已有场景变化检测 [`local_preprocessing.py`](../../backend/app/local_preprocessing.py#L226)，且 [`video_toolkit.py`](../../backend/app/video_toolkit.py) 能产生 scenes/subtitles 产物，另有字幕识别与编辑 [`batch_editing_api.py`](../../backend/app/batch_editing_api.py#L691)，因此不能将这些大类笼统写成“没有”。但当前 [`asset_paths()`](../../backend/app/video_toolkit.py#L60) 仅枚举参考视频、处理流水线和生成结果，**不直接枚举 `ProjectAssets` 上传视频**；跨项目素材的高光入口必须加 `ProjectAssets.file()` 安全解析适配。是否优先补高光取决于用户是否把“长视频再利用”纳入当前产品范围。

## 18. 可删除/替换

当前项目本次**没有**建议删除的既有项目、素材、任务、时间线、审核代码。若将来采纳上游检测器，优先在 [`video_toolkit.py`](../../backend/app/video_toolkit.py) 的 scenes/subtitles 与现有 [`video_toolkit_api.py`](../../backend/app/video_toolkit_api.py) 的耐久运行接缝补充策略；[`local_preprocessing_runner.py`](../../backend/app/local_preprocessing_runner.py) 是 `referenceMedia` 专用链路，若为该链路替换检测算法，须保留其阶段记录和落盘契约。竖屏实现也只应作为 toolkit 可选处理器。上游 `recut.py` 的段校验与当前 [`timeline.py`](../../backend/app/timeline.py) 模型不同，不引入重复校验；只有多段 EDL 被正式立项才重评。这个“替换”是规划，不是本次改动。

## 19. 融合架构

建议先建小型 `SourceMediaResolver` seam：接受 `projectId + sourceRef`，只返回经校验的文件句柄/媒体属性/稳定来源 ID/来源版本；`referenceMedia` 走当前安全解析，项目素材走 [`ProjectAssets.file()`](../../backend/app/project_assets.py)，不接收用户任意路径。当前 [`video_toolkit.asset_paths()`](../../backend/app/video_toolkit.py#L60) 尚不枚举项目素材，因此此 seam 是新工作，不是现成能力。其上建小而深的 `HighlightSuggestionService`：输入 `SourceMediaResolver` 结果、toolkit scenes/subtitles、`SelectionPolicy/ProviderChoice`，输出不带业务决定的 `HighlightProposal{id, sourceRef, startSeconds, endSeconds, evidence, score?, modelRunId, sourceRevision}`。实现内部组合窗口器、模型评分适配器、候选归一化器；API、Store、队列仍由当前项目拥有。另一个 `ReframePlanner` 只把场景边界、人脸/说话人证据、目标比例转为可审阅构图计划，再由现有媒体处理器执行。`durable_runs` 管冻结输入和状态，`ProjectAssets` 管文件，`batch_production` 管变体/审核/交付；依据 [`durable_runs.py`](../../backend/app/durable_runs.py)、[`ADR 0009`](../adr/0009-content-project-and-workflow-boundaries.md)。

## 20. 融合流程

用户显式选择内容项目的参考视频或项目素材 → `SourceMediaResolver` 安全定位素材 → 当前 toolkit 运行产 scenes/subtitles（项目素材须先补安全输入适配）→ 可选高光服务提出带来源时段的建议 → 人工筛选后把所选范围映射为现有时间线片段或短视频变体草稿 → 可选竖屏构图建议和人工覆盖 → 当前渲染运行生成预览 → 当前审核版本批准 → 当前交付版本导出。任何候选重排、模型重试或构图变化都不能改写已保存资产 ID 和旧审核；输入或产物变化按 [`batch_production.py`](../../backend/app/batch_production.py#L93) 的现行失效规则重走预览/审核。参考复刻流程中来源于参考视频的高光候选只对应“参考镜头段”，需用户明确动作才建立“制作镜头”。

## 21. 目录影响

若实施，预计最小新增 `backend/app/source_media_resolver.py`（参考视频和项目素材的安全解析）、`backend/app/highlight_candidates.py`（纯函数）、`backend/app/highlight_selection.py`（服务）及相应 `backend/tests/test_highlight_*.py`；在 [`video_toolkit.py`](../../backend/app/video_toolkit.py) / [`video_toolkit_api.py`](../../backend/app/video_toolkit_api.py) 接入项目素材来源与候选运行。前端只扩充现有工作台/时间线局部面板，不复制上游 `dashboard/` 或 `render-service/`。这是拟议目录，不是现有文件清单；应先确认 P0 产品范围，再产生代码。

## 22. 数据模型

新增建议应以**来源稳定 ID + 秒级闭开区间 + 来源版本**标识事实，以独立建议 ID 标识一次候选；`sourceRef` 使用带类型的 `projectAsset(assetId)` 或 `referenceMedia(referenceMediaId)`，项目素材始终以既有稳定 `assetId` 为业务主键，参考素材进入时间线前按现有导入流程建立项目素材引用。保存模型建议、评分依据、裁切计划与执行快照，但“确认、采用、审核、交付”继续由现有对象负责。上游 `source_window_id` 可作审计证据，不能当外键；`clip_index` 仅是显示顺序。将建议转换为短视频变体时复用 [`batch_production.py`](../../backend/app/batch_production.py) 的 `contentRevision/variant.revision/subtitle.revision` 组合和预览 `runId`；转换为制作镜头时走 [`preproduction.py`](../../backend/app/preproduction.py) 的 `sceneId/rank` 及镜头身份。不得另外建 OpenShorts Project、Clip、Review 表。

## 23. Store

当前 [`preproductionWorkspaceStore.ts`](../../frontend/src/preproductionWorkspaceStore.ts) 管镜头、选择、布局、脏状态和保存冲突；高光建议只需一次加载/刷新/确认的局部状态或同 Store 的只读投影，不能成为第二份镜头权威数据。映射后的对象才进入现有 `actions`；画布继续由 [`workflowCanvasProjection.ts`](../../frontend/src/workflowCanvasProjection.ts) 从一份镜头数据生成。上游 [`App.jsx`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/src/App.jsx#L357) 的大量 `useState`、sessionStorage/localStorage 恢复和 `clip_index` UI 状态不移入。

## 24. API

建议复用当前 `/api/projects/{project_id}/toolkit` 的来源与运行边界，增加例如 `POST /highlight-proposals`（`sourceRef`、冻结来源/供应商/参数，返回运行 ID）与 `GET /highlight-proposals/{run_id}`（只读结果）；`POST /highlight-proposals/{proposal_id}/apply` 应由目标业务端点持有，要求目标工作流及其当前 revision，明确创建变体片段或制作镜头。具体路径实施时须与 [`video_toolkit_api.py`](../../backend/app/video_toolkit_api.py#L68)、[`preproduction_api.py`](../../backend/app/preproduction_api.py#L162)、[`timeline_api.py`](../../backend/app/timeline_api.py#L41)、[`batch_editing_api.py`](../../backend/app/batch_editing_api.py#L293) 的所有权协调；`sourceRef` 在服务端经 `SourceMediaResolver` 严格解析，不能由客户端提交路径。供应商选择与代理披露沿用 [`analysis_provider.py`](../../backend/app/analysis_provider.py)；结果只能由当前 [`durable_runs.py`](../../backend/app/durable_runs.py) 的冻结输入校验后落地。不要照搬上游 `/api/process`、`/api/status/{job_id}`、`/api/clip/...` 路由。

## 25. P0/P1/P2

| 优先级 | 建议与验收门槛 | 不做的事 |
| --- | --- | --- |
| P0 | 在“长视频挑高光”产品范围内，先交付参考视频或项目视频的来源区间建议与人工确认。以 `SourceMediaResolver` + 纯函数窗口/去重 + 当前供应商适配器 + 当前运行契约形成垂直切片；验证来源时码、模型失败和过期结果、候选应用后的版本失效。 | 不自动把建议视为制作镜头或批准成片 |
| P1 | 在代表性长视频上比较当前 toolkit scenes 检测与 TransNetV2；若质量收益抵得过模型体积，再加可选策略。评估主体跟踪与人工裁切覆盖，验证横竖屏、多人和边界切换。 | 不替换已有预处理/时间线权威模型 |
| P2 | 词级精剪体验、ASS/Hook 视觉层、说话人辅助构图；先做预览/正式输出一致性、许可及资源成本验证，再扩大 UI。 | 不引入独立 Remotion 服务、社媒代发或 `cloud/` 商业实现 |

**审计结论：** 优先复用上游的纯候选整理规则，并把高光模型与竖屏构图放在当前项目的少量接口和适配器后。OpenShorts 没有可直接搬来的独立 ClipIndex；其语义选段依赖模型提示/响应和作业流程，但不必复制商业数据库。当前内容项目、项目素材、制作镜头、时间线、运行、审核与交付模型已经覆盖融合所需的权属，新增第二套只会制造身份和版本漂移。
