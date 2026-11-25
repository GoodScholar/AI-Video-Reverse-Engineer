# opensource-clipping × 当前项目：源码级增量融合审计

审计日期：2026-09-28。上游固定提交：[`4a478dff3dacd293eeb5e28335aa7c81d78b8f50`](https://github.com/NaufalRizqullah/opensource-clipping/tree/4a478dff3dacd293eeb5e28335aa7c81d78b8f50)。本报告为静态源码审计，不代表运行效果或测试通过。判断优先服从[累计融合登记表](./reference-project-integration-register.md)、[ADR 0007](../adr/0007-preproduction-with-shot-steps.md)、[ADR 0009](../adr/0009-content-project-and-workflow-boundaries.md)、[ADR 0010](../adr/0010-shot-workflow-complementary-views.md)和当前源码。处理方式限于：**直接复用／迁移后适配／部分复用／只参考设计／重新实现／不引入**。

**总判断：只参考设计。** 上游确有可验证的播客双人/多人分屏构图启发式，但没有证明其比已审计的 OpenShorts 方案更成熟或能独立迁移；其高光选择是整段转录交给 Gemini/NVIDIA 的提示词与 JSON 响应，并非独立的高光索引或可复用排序算法。因此维持登记表中 OpenShorts 为长视频候选首选来源，opensource-clipping 只提供构图失败场景和验证用例。此结论不修改登记表。

## 1. 参考项目核心能力

上游 [`clipping/runner.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/runner.py) 串行执行下载/本地输入、Faster-Whisper 或来源字幕转录、AI 选段、元数据整理、可选 pyannote 说话人分离、逐段渲染与 manifest 保存。另有 [`clipping/story_runner.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/story_runner.py) 的手写多来源故事配方、[`youtube_uploader/uploader.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/youtube_uploader/uploader.py) 和 [`facebook_uploader/uploader.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/facebook_uploader/uploader.py) 的平台上传。没有发现独立 `ClipIndex`、可脱离流水线的语义评分服务或项目级审核模型。

## 2. 当前项目核心能力

当前以 [`content_workflow.py`](../../backend/app/content_workflow.py) 的内容项目、[`project_assets.py`](../../backend/app/project_assets.py) 的稳定素材身份、[`preproduction_api.py`](../../backend/app/preproduction_api.py) 的 Scene/Shot/结果候选、[`video_toolkit.py`](../../backend/app/video_toolkit.py) 的场景和字幕工具、[`timeline.py`](../../backend/app/timeline.py) 与 [`timeline_render.py`](../../backend/app/timeline_render.py) 的 FFmpeg 时间线，以及 [`batch_production.py`](../../backend/app/batch_production.py) 的变体、预览、审核失效与导出形成闭环；[`durable_runs.py`](../../backend/app/durable_runs.py) 持有运行快照与恢复。长视频语义高光候选仍是待建能力，不能把现有场景/字幕工具误说成完整选段服务。

## 3. 定位差异

上游的业务单位是“一条长视频 → 若干自动短片 → 可选上传”，配置与输出以 CLI/job 文件夹为中心；当前业务单位是“内容项目 → 素材 → 镜头/变体 → 人工预览审核 → 交付”。上游的 rank、manifest 行与 job ID 是执行/输出标记，不可代替当前稳定 `assetId`、Shot ID、变体修订或批准状态。两者可交汇于“已有来源视频提出候选区间”，而其实现归属已由[登记表](./reference-project-integration-register.md)指定给 OpenShorts 为主的单一服务。

## 4. 功能模块

| 上游模块与源码证据 | 实际能力 | 当前落点／处理方式 |
| --- | --- | --- |
| [`engine.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/engine.py) | yt-dlp/gdown 下载、来源字幕/Whisper、整段 transcript 发 AI 选段 | 已规划 `SourceMediaResolver`/高光建议；**不引入**第二下载与选段链 |
| [`metadata.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/metadata.py) | 字段别名、标题/标签整理、警告、按模型 `viral_score` 排序 | 仅作输出校验**设计参考**；得分不是客观质量，也未校验片段相互重叠 |
| [`render_split_screen.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/studio/render_split_screen.py)、[`render_hybrid.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/studio/render_hybrid.py) | 人脸采样、分屏/竖屏裁切、deadzone/snap/平滑、逐帧输出 | 与 OpenShorts 重构重叠；**只参考设计**失败场景 |
| [`story/loader.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/story/loader.py)、[`story/assembler.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/story/assembler.py) | JSON 来源表、手工场景配方、FFmpeg 拼接 | 多来源配方仅**只参考设计**；当前 Timeline 拥有合成 |
| [`web/api/store.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/web/api/store.py)、[`web/api/worker.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/web/api/worker.py) | 进程内 job dict、尽力 JSON 持久化、线程池和 SSE | 对当前耐久运行模型**不引入** |
| [`docs/studio/`](https://github.com/NaufalRizqullah/opensource-clipping/tree/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/docs/studio)、[`web/dashboard/`](https://github.com/NaufalRizqullah/opensource-clipping/tree/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/web/dashboard) | 两套建 job/监看作业的 UI | 对现有项目工作台**不引入** |

## 5. 业务逻辑

[`runner.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/runner.py) 先调用 [`engine.get_analysis_prompt/analyze_with_ai`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/engine.py#L596)，再把模型建议送入渲染；[`metadata.normalize_and_validate`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/metadata.py#L104) 主要规范社媒字段，`start_time/end_time` 默认缺失为 0，没有来源版本、词边界精剪、重叠去重或人工采用门禁。NVIDIA 失败会自动回退 Gemini，见 [`analyze_with_ai`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/engine.py#L1135)；当前供应商选择与成本/数据外发边界不可照搬这种静默切换。模型输出直接触发渲染的节奏亦不符合当前“建议 → 人工确认 → 预览 → 审核”生命周期。

## 6. UI/UX

上游 [`docs/studio/new-job.html`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/docs/studio/new-job.html)、[`docs/studio/job.html`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/docs/studio/job.html)、[`web/dashboard/src/pages/JobDetail.jsx`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/web/dashboard/src/pages/JobDetail.jsx) 以参数表单、进度、成片列表为中心，没有当前 [`ShotWorkspaceLayout.tsx`](../../frontend/src/ShotWorkspaceLayout.tsx)、[`TimelineEditor.tsx`](../../frontend/src/TimelineEditor.tsx) 的镜头/时间线共用对象，也没有“候选来源证据 → 人工采用 → 审核失效”视图。若后续展示播客构图，仅在现有工作台增加候选预览/手工覆盖，遵循 [`DESIGN.md`](../../DESIGN.md)，不复制上游整页。

## 7. 技术架构

上游是 Python 3.10+ CLI + FastAPI 包装 + 静态 Studio 与另一个 Vite/React dashboard；[`pyproject.toml`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/pyproject.toml) 与 [`requirements.txt`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/requirements.txt) 依赖声明不一致，前者未列 pyannote/FastAPI/edge-tts 等。Face 模型在 [`face_detection.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/studio/face_detection.py#L48) 运行时下载并使用模块级单例；`render_split_screen.py` 在同一函数中采样、身份猜测、平滑、逐帧渲染。当前 React/Zustand + FastAPI/FFmpeg 已有对应宿主，不存在引入另一套前端或进程内执行器的必要。

## 8. 数据模型

上游 [`web/api/models.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/web/api/models.py) 的 JobCreate/JobResponse 是一次作业参数和输出，[`web/api/store.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/web/api/store.py#L95) 存状态于内存并尽力写 `outputs/jobs.json`，不是项目资产/审核事实库。[`story/loader.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/story/loader.py#L29) 的 `source_id/local_path` 是配方内引用，不能越过当前 `ProjectAssets` 安全路径和稳定 `assetId`。上游 manifest、Gemini 原始响应、转录段以输出目录文件为主；当前数据需带 `projectId`、来源版本与运行修订，不能以绝对路径或 rank 作外键。

## 9. 当前优势

当前 [`batch_production.py`](../../backend/app/batch_production.py) 明确把预览、审核、导出与修订关联；[`project_assets.py`](../../backend/app/project_assets.py) 提供统一素材身份；[`durable_runs.py`](../../backend/app/durable_runs.py) 管运行冻结与恢复；[`preproductionWorkspaceStore.ts`](../../frontend/src/preproductionWorkspaceStore.ts) 与 [`workflowCanvasProjection.ts`](../../frontend/src/workflowCanvasProjection.ts) 使列表/关系图共用规范镜头。这些均是上游 job/manifest 模型缺乏的业务边界，也是融合必须保留的单一归属。

## 10. 参考优势

上游对“播客镜头语言”有更具体的启发式：[`render_split_screen.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/studio/render_split_screen.py#L185) 按说话时长选上下屏、两遍读取脸中心并用单人帧中位数估计说话人位置，再按 `deadzone`、大位移 `snap`、平滑和切换缓存控制机位；[`diarization.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/diarization.py) 支持 pyannote。它是**可核实的实现差异**，但映射依赖 X 位置启发式，单人视觉模式甚至把同一脸写入左右两条轨迹；没有找到相应自动测试或与 OpenShorts 的质量基准，不能称为“独有且成熟的可复用模块”。手工多来源配方也是功能差异，但 [`story/assembler.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/story/assembler.py#L218) 的 crossfade `offset=0`（两段和三段以上迭代均如此）、concat list 对路径单引号未转义，不能视为成熟合成优势。

## 11. 功能映射表

| 能力 | 已登记首选/当前唯一归属 | 上游增量证据 | 处理方式 |
| --- | --- | --- | --- |
| 长视频语义高光/去重 | [OpenShorts 审计](./openshorts-current-project-integration-audit-2026-09-28.md) → 当前 `HighlightSuggestionService` | Gemini 整段提示词、模型 `viral_score`，缺可独立复用算法 | **不引入** |
| 词级字幕与切点 | OpenShorts 词边界；当前 toolkit/字幕版本 | Faster-Whisper 词段和 ASS | **不引入**第二字幕链 |
| 双人/多人构图 | OpenShorts 重构候选 → 当前可选构图计划 | 中位脸位置、snap/deadzone、切换缓存；未量化验证 | **只参考设计** |
| 多来源故事编排 | [MoneyPrinterTurbo 审计](./moneyprinterturbo-current-project-integration-audit-2026-09-28.md) 的 MontagePlanner → 当前 Timeline | 手写 JSON source/scene 配方，无自动素材编排 | **只参考设计** |
| Storyboard/Agent | [Toonflow 审计](./toonflow-current-project-integration-audit-2026-09-28.md) 的候选 Storyboard → 规范 Shot | 上游只有剪辑配方，不是 Agent 分镜系统 | **不引入** |
| Workflow 准备 | [ComfyStudio 审计](./comfystudio-current-project-integration-audit-2026-09-28.md) → 外部生成候选包 | 上游没有 ComfyUI Workflow 转换器 | **不引入** |
| 下载/外部素材 | MPT `MaterialProvider` → `ProjectAssets` | yt-dlp/gdown/Pexels 下载 | **不引入**并行下载源 |
| 字幕/配音/时间线/渲染 | 当前字幕/配音/Timeline/FFmpeg | ASS、edge-tts、逐帧 FFmpeg | **不引入**并行制作链 |
| 队列/运行/审核 | 当前 `durable_runs` + `batch_production` | 内存 job + 尽力 JSON；无人工审核 | **不引入** |
| 发布 | 当前批准后导出；发布尚未立项 | YouTube/Facebook uploader | **不引入**；单独立项后重审权限 |

## 12. 直接复用

**无。** 本次没有可在当前契约内原样复制的代码；根 MIT 不是忽略依赖、素材和接口边界的通行证。OpenShorts 已有纯候选整理逻辑，其审计给出更明确的局部复用范围。

## 13. 迁移后适配

**无当前推荐的代码迁移。** 上游高光、字幕、下载和渲染均与当前或已登记首选方案重叠；没有独立成熟实现值得迁移整套流程。若未来出现代表性素材基准证明某条构图规则显著优于 OpenShorts，应重新立项、逐文件确认许可与依赖，再给出具体上游固定路径 → 当前目标路径、修改点和测试；本报告不预授权代码复制。

## 14. 部分复用

**无当前推荐的代码片段复用。** [`render_split_screen.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/studio/render_split_screen.py#L367) 的映射/平滑闭包嵌在检测、pyannote、逐帧裁切与全局配置中，且无自动测试；即使只截取几行，仍要重新定义身份置信度、时间坐标、镜头切换与当前输出契约。当前更合适的是从行为要求**重新实现**纯构图计划，并以失败用例验证，非代码移植。

## 15. 只参考设计

参考 [`render_split_screen.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/studio/render_split_screen.py#L575) 的“采样脸框 → 匹配音频说话人 → 对每人稳定轨迹 → 切换布局/镜头”分层思路，补 OpenShorts 构图试验中的单人紧镜头、多人交叉说话、脸丢失和场景切换样例。当前若实施，由拟议 `ReframePlanner` 只输出候选轨迹和“不确定”标志，当前 [`video_toolkit.py`](../../backend/app/video_toolkit.py) / [`timeline_render.py`](../../backend/app/timeline_render.py) 执行，人工覆盖保留最高优先级。此为**重新实现**的设计约束，不复制上游 detector、渲染器或位置映射代码。

参考 [`story/loader.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/story/loader.py#L149) 的“多来源、按段引用”信息组织，作为当前 `MontagePlanner`/Timeline 输入可读性的反例与测试素材；业务引用仍是当前 `assetId` + 来源区间，不能引入 `local_path` 来源表。参考 [`metadata.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/metadata.py#L104) 的“模型元数据先规范化再展示警告”交互思想，但最终元数据须由当前版本/人工审核拥有。

## 16. 不建议引入

**不引入** [`engine.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/engine.py) 整段 AI 选段、[`studio/subtitles.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/studio/subtitles.py) 第二字幕、[`studio/core.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/studio/core.py) 第二渲染、[`web/api/store.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/web/api/store.py) 第二队列、[`story/assembler.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/story/assembler.py) 第二时间线、[`youtube_uploader/`](https://github.com/NaufalRizqullah/opensource-clipping/tree/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/youtube_uploader) 和 [`facebook_uploader/`](https://github.com/NaufalRizqullah/opensource-clipping/tree/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/facebook_uploader) 自动发布。上游没有规范 Shot、人脸身份审查或批准成片门禁；不能伪造这些模块。

**许可证硬门槛（截至审计日）**：根 [`LICENSE`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/LICENSE) 是 MIT；仓库检索仅见根 LICENSE，未见子目录独立许可证，但 `assets/bgm/` 内 MP3 与字体/转场视频/人脸权重不因根 MIT 自动获授权。上游 [`assets/bgm/README.md`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/assets/bgm/README.md) 只是建议找“royalty-free”音乐，逐首授权材料未随代码提供，**禁止复制媒体资产**。[`requirements.txt`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/requirements.txt) 含 Ultralytics、pyannote、Faster-Whisper、MediaPipe 等，且未锁版本；[Ultralytics 官方](https://docs.ultralytics.com/help/contributing/)说明 YOLO 代码/模型为 AGPL-3.0 或 Enterprise，商业闭源场景未确认许可前禁止纳入。[pyannote speaker-diarization-3.1 模型卡](https://huggingface.co/pyannote/speaker-diarization-3.1/tree/main)标 MIT，但为 gated、需接受访问条件；模型与 `pyannote.audio`、权重版本分别核查。[MediaPipe 官方源码](https://github.com/google-ai-edge/mediapipe)为 Apache-2.0，运行时下载的 BlazeFace 权重及具体使用条件仍须核定。[Faster-Whisper 官方许可证](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE)为 MIT，Whisper 模型权重、转录数据权利与分发方式另核。[Gemini API 附加条款](https://ai.google.dev/gemini-api/terms)独立于 MIT，处理外发和费用须获授权；上游 NVIDIA NIM/edge-tts/Pexels/Google Fonts 和下载内容也需逐服务、逐素材核查。[yt-dlp 官方许可说明](https://github.com/yt-dlp/yt-dlp/blob/master/README.md#licensing)须按安装发行物与依赖核对，工具许可不等于 YouTube/TikTok/Instagram/Drive 视频下载或再利用权。[YouTube API 条款](https://developers.google.com/youtube/terms/api-services-terms-of-service)对上传有用户告知与版权/隐私约束；Facebook/Meta 发布权限、账户授权和平台条款未完成核查。**未核明的代码依赖、模型、下载源、上传权限均不得复制或启用。**

## 17. 当前缺失

当前相对“长视频再利用”的实质缺口已由[OpenShorts 审计](./openshorts-current-project-integration-audit-2026-09-28.md)识别：项目素材安全来源解析、带时码/证据的高光建议、人工应用、可选竖屏构图。当前已有 [`video_toolkit.py`](../../backend/app/video_toolkit.py) 场景/字幕产物及 [`batch_subtitles.py`](../../backend/app/batch_subtitles.py) 字幕能力，不能把字幕/转录笼统列为空白。opensource-clipping 没有额外证明一个 P0 独有缺口；若播客作为专项，尚缺可审计的人脸与音频标签关联置信度及构图失效回退，这应放在既有 ReframePlanner 规划内。

## 18. 删除/替换

**无。** 不删除或替换当前 `ProjectAssets`、预处理、字幕、配音、Timeline、FFmpeg、durable_runs、审核与导出实现。上游 [`story/assembler.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/story/assembler.py#L190) 的硬切路径引号问题及 `#L218` 的 crossfade `offset=0` 使其更不适合作为替换品；这只是静态代码风险，未声称实际成片故障已复现。

## 19. 融合架构

维持[登记表](./reference-project-integration-register.md)的一条能力链：`SourceMediaResolver`（参考视频/项目素材安全来源）→ 单一 `HighlightSuggestionService`（OpenShorts 首选候选规则）→ 可选 `ReframePlanner`（依据脸框/说话人信号生成可审阅轨迹）→ 现有 Timeline/FFmpeg → 预览、审核、导出。opensource-clipping 仅给 `ReframePlanner` 的行为用例与交互设计线索；所有源文件、候选、构图计划与运行都留在当前项目模型和 API 内。MPT 的 `MontagePlanner` 是脚本驱动多素材分配，不并入高光评分；Toonflow 的规范 Shot/关系图、ComfyStudio 的外部 Workflow 候选也不因本项目扩大职责。

## 20. 融合流程

用户从当前内容项目选已授权视频 → 安全解析并冻结来源版本 → 现有 toolkit/未来单一高光服务给候选时段、依据和不确定项 → 用户采用候选进入变体草稿或规范 Shot → 播客场景可请求构图建议并手工覆盖 → 现有时间线预览 → 当前审核通过后导出。对外发布仍需独立立项、平台授权与用户明确批准；上游“模型建议完成即自动渲染/上传”的链路不穿透审核。

## 21. 目录影响

本次实际目录影响：**仅本报告**。若未来基于[OpenShorts 审计](./openshorts-current-project-integration-audit-2026-09-28.md)落实长视频能力，拟增加 `backend/app/source_media_resolver.py`、`backend/app/highlight_candidates.py`、`backend/app/highlight_selection.py`，在 [`video_toolkit_api.py`](../../backend/app/video_toolkit_api.py) 与已有前端工作台接入；这些是**拟议路径，不是当前文件链接**。若播客构图的专项基准有收益，再拟增 `backend/app/reframe_planner.py`，只产生轨迹计划并交现有处理链。不上游 `web/`、`youtube_tracker/`、`story/`、上传器目录。

## 22. 数据模型调整

本次**无数据模型改动**。未来高光候选依 OpenShorts 规划保存 `projectId + sourceRef(assetId/referenceMediaId) + sourceRevision + [startSeconds,endSeconds) + evidence + runId`；构图建议只需按来源时段记录脸框/不确定说话人标签/目标裁切轨迹和人工覆盖，任何说话人名称不得由左右脸位置自动确认为事实。应用候选后才生成当前变体片段或规范 Shot，复用现有版本、审核失效与导出记录；不得引入上游 `source_id`、`rank`、`jobs.json` 为业务主键。

## 23. Store 调整

本次**无 Store 调整**。将来候选仅作为 [`preproductionWorkspaceStore.ts`](../../frontend/src/preproductionWorkspaceStore.ts) 或时间线现有视图的只读加载状态；采纳动作使用当前规范 Shot/变体更新，[`workflowCanvasProjection.ts`](../../frontend/src/workflowCanvasProjection.ts) 继续从同一 Shot ID 投影。构图预览的暂存 UI 状态不得成为第二份脸身份、镜头顺序、时间线或审核真相。

## 24. API 调整

本次**无 API 调整**。将来长视频建议入口沿用 [`video_toolkit_api.py`](../../backend/app/video_toolkit_api.py) 的项目 ID、输入冻结与运行状态；读取候选时返回来源证据/版本，采纳写请求经 [`preproduction_api.py`](../../backend/app/preproduction_api.py) 或当前变体 API 的 revision 校验。构图建议若立项可作为同一项目下的可选处理请求，输出项目素材或计划，不复制上游 [`web/api/routes/jobs.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/web/api/routes/jobs.py) 的 `/api/jobs`、内存队列或任意路径上传契约。供应商显式选择/外发披露沿用 [`analysis_provider.py`](../../backend/app/analysis_provider.py)。

## 25. P0/P1/P2

| 优先级 | 增量决定与可验收条件 | 处理方式 |
| --- | --- | --- |
| P0 | **不为 opensource-clipping 开新实施线。** 先按登记表和 OpenShorts 完成长视频安全来源、候选证据、人工应用与修订失效；验证重叠/边界/过期运行。 | **不引入**上游高光/下载/队列 |
| P1 | 如播客重构纳入范围，用代表性素材比较 OpenShorts 与“单人帧中位位置、deadzone/snap、切换缓存”规则；测多人、交叉说话、脸丢失、切镜、字幕/音画同步和人工覆盖。仅质量收益可证且依赖许可核定时，在当前 `ReframePlanner` 内**重新实现**适用规则。 | **只参考设计**；条件满足后**重新实现** |
| P2 | 多来源手写故事配方、平台发布、社媒元数据自动填充各自单独立项；先核准来源素材、MP3/字体/模型和上传平台权限，并验证当前批准后导出边界。 | 当前**不引入** |

**静态审计边界：** 本次只阅读固定提交与当前源码、核对文件/许可/链接，不运行上游流水线、媒体质量基准或当前产品测试。上游缺少对应构图/故事拼接的自动测试证据；上述算法质量和平台上传行为仅为待验证判断，不写成测试通过结论。
