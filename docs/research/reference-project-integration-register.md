# 参考项目累计融合决策登记表

更新时间：2026-09-29。

这份文件是参考项目分析的**累计入口**，不是替代各项目的源码深审。新增参考项目时，必须先与本表已有能力归属和排除项比较，再更新本表；不能只做“新项目 vs 当前项目”的孤立判断。详细证据仍以固定提交审计报告为准：

当前项目的总定位是 **AI 视频创作与生产工作台**，不是单一参考复刻工具。`ContentProject` 并列承载参考复刻、AI 内容创作与批量混剪、制作镜头与关系画布、项目素材、Timeline、审核和交付。最终 AI 视频生成当前不作为应用内置执行链，是执行边界，不是整个产品定位；任何参考项目审计都必须按这些并列工作流逐项比较。

- [八个视频项目源码核验](./eight-video-projects-mpt-reuse-2026-09-28.md)
- [MoneyPrinterTurbo 深度融合审计](./moneyprinterturbo-current-project-integration-audit-2026-09-28.md)
- [OpenShorts 深度融合审计](./openshorts-current-project-integration-audit-2026-09-28.md)
- [opensource-clipping 深度融合审计](./opensource-clipping-current-project-integration-audit-2026-09-28.md)
- [ComfyStudio 深度融合审计](./comfystudio-current-project-integration-audit-2026-09-28.md)
- [Toonflow 深度融合审计](./toonflow-current-project-integration-audit-2026-09-28.md)
- [OpenMontage 深度融合审计](./openmontage-current-project-integration-audit-2026-09-28.md)
- [YumCut 深度融合审计](./yumcut-current-project-integration-audit-2026-09-28.md)
- [short-video-maker 深度融合审计](./short-video-maker-current-project-integration-audit-2026-09-28.md)
- [ai-clipping-comfyui 深度融合审计](./ai-clipping-comfyui-current-project-integration-audit-2026-09-28.md)
- [SmartGallery 深度融合审计](./smartgallery-current-project-integration-audit-2026-09-28.md)
- [Media Search Agent 深度融合审计](./media-search-agent-current-project-integration-audit-2026-09-28.md)
- [OpenStory 微信文章与固定提交深度融合审计](./wechat-article-projects-intake-2026-09-28.md)
- [Huobao Drama 固定提交适配分析](./huobao-drama.md)

历史专题候选入口（已分析或初筛，但不等于逐仓深审）：

- [开源无限画布项目对视频前置工作台的借鉴](./open-source-infinite-canvas.md)
- [GitHub 简化商品短视频流程调研](./github-simple-video-workflow-2026-09-26.md)
- [内容工作台 GitHub 源码选型](./github-content-workbench-reuse-2026-09-26.md)
- [多音色试听与脚本配音源码复用核验](./github-voice-source-reuse-2026-09-26.md)

新增候选池（尚未获得代码复用结论）：

- [30 项素材库候选初筛](./material-library-projects-intake-2026-09-28.md)

## 1. 当前项目唯一能力归属

融合任何参考项目时，以下对象保持唯一，不另建平行实现：

| 能力 | 当前唯一归属 | 累计约束 |
| --- | --- | --- |
| 项目与素材身份 | `ContentProject`、`ProjectAssets`、稳定 `assetId` | 不引入路径型第二项目系统或供应商私有素材 ID 作为业务主键 |
| 场景与镜头 | `PreproductionWorkspace`、Scene、Shot、ShotResultVersion | Agent/工作流输出只能生成候选，采用后转为规范 Shot |
| 镜头列表与关系图 | 同一 Zustand Store、同一 Shot ID | 不引入 Pinia/Qt/ComfyUI 状态作为第二真相源 |
| 时间线与合成 | 当前 Timeline、Python FFmpeg 渲染 | 不引入 MoviePy、Remotion、Qt Timeline 作为平行成片链 |
| 任务与恢复 | `durable_runs.py` 及现有运行快照 | 不引入 MPT Redis 状态、Toonflow 请求级状态或其他队列作为第二任务模型 |
| 版本、审核与导出 | 当前变体、预览、审核、失效和导出模型 | “生成成功”不能自动等于采用、批准或发布 |
| API 与权限边界 | 当前 FastAPI、项目 ID、本机写请求边界 | 不并存 Bun/Express、Node API 或绝对目录工作区接口 |

## 2. 十三个参考项目累计决策

| 项目 / 固定提交 | 独有价值 | 与其他项目的关系 | 处理方式 | 落地边界 | 许可边界 |
| --- | --- | --- | --- | --- | --- |
| MoneyPrinterTurbo `d7b7383` | 外部图库检索、来源记录、脚本关键词有序分配、跨成片去重、配音预览指纹 | 补素材检索与编排，不补本地长视频语义选片；队列、合成、审核不替换当前实现 | 迁移后适配 | 小型 `MaterialProvider`/`MontagePlanner` 规则；不引入整套任务、MoviePy、Streamlit、自动发布 | MIT；供应商、音色、素材和发布权另核 |
| [OpenShorts](./openshorts-current-project-integration-audit-2026-09-28.md) `89f0309` | 长视频转录、场景窗口、高光候选、活跃说话人和竖屏重构 | 与 MPT 互补：负责“发现候选片段”，MPT 负责“按脚本分配素材” | 部分复用 | 仅原样复用 `score_batches/trim_to_best/dedupe_overlapping`；高光流程、词边界、TransNetV2 和竖屏构图接入当前素材/运行/审核契约 | 根 MIT；`cloud/` 独立商业许可；Ultralytics/YOLO 为 AGPL-3.0/Enterprise 风险 |
| [ComfyStudio](./comfystudio-current-project-integration-audit-2026-09-28.md) `4641684` | ComfyUI Workflow 格式识别、基础连线/widget 映射、参数与端口建模思路 | 补候选 Workflow 导入与规范化；不补最终生成，Qt UI、时间线、队列、版本和项目状态与当前架构冲突 | 部分复用 | 官方 API-format 优先；UI-format 转换器仅迁移后适配并严格拒绝悬空引用；不引入 Qt/队列/项目 JSON | 根 MIT；PyQt 为 GPLv3/商业双许可，Qt WebEngine、节点、模型另核 |
| [opensource-clipping](./opensource-clipping-current-project-integration-audit-2026-09-28.md) `4a478df` | 播客构图的中位脸位置、deadzone/snap、切换缓存与多来源手写配方 | 与 OpenShorts 高度重叠；无独立成熟高光算法，构图实现与逐帧渲染/模型深耦合且无对应测试 | 只参考设计 | 仅作为 OpenShorts/ReframePlanner 的失败场景、行为用例与专项质量基准；不复制代码 | 根 MIT；Ultralytics AGPL/Enterprise，pyannote gated 模型、Gemini、下载源和发布权限另核 |
| [OpenMontage](./openmontage-current-project-integration-audit-2026-09-28.md) `08e2151` | 交付承诺、阶段门禁、成本前置、质量摘要和运行回放的产品表达 | 与 Toonflow 的 Agent 分镜重叠；其 checkpoint/Backlot/工具注册表不比当前领域运行与审核契约更适配 | 只参考设计 | 仅独立重写费用确认、承诺差异和质量提示；不复制 Pipeline、Schema、skills、Backlot 或工具包 | 根 AGPL-3.0；Remotion、字体/动捕、供应商和媒体另有许可边界 |
| [YumCut](./yumcut-current-project-integration-audit-2026-09-28.md) `7e07eb3` | 一项目多语言失败隔离、Bearer API scope/幂等、积分和批准后发布的产品表达 | 与当前变体/MPT 批量链重叠；`ProjectGroup` 入口隐藏且不 fan-out，非成熟多品牌协作 | 只参考设计 | 公开 API、多语言和发布仅按需独立重写；不引入 Next/Prisma/daemon/线性状态，发布仅有 YouTube 且存在并发 claim 风险 | PolyForm Noncommercial 1.0.0；四个外部生产仓、Remotion、供应商和媒体另核 |
| [short-video-maker](./short-video-maker-current-project-integration-audit-2026-09-28.md) `9bb9a21` | 轻量文字/搜索词→英语配音→逐词字幕→库存素材→成片，以及 REST/MCP 的最小接口表达 | 与 MPT 素材链和当前字幕、配音、渲染高度重叠；内存队列、文件历史与时长计算均不适合作为生产基线 | 只参考设计 | 只参考轻量提交、情绪音乐和逐词高亮呈现；不复制队列、API/MCP、Remotion、TTS/字幕、UI、音乐或 Docker | 根 MIT；Remotion 自有许可、Pexels 条款、模型/音色、FFmpeg/Chromium 与逐曲音乐权另核 |
| [ai-clipping-comfyui](./ai-clipping-comfyui-current-project-integration-audit-2026-09-28.md) `d0a7b19` | ComfyUI 中提交远端剪辑、返回有序候选/坐标并手工 fan-out 的最小交互形状 | 与 OpenShorts/ComfyStudio 重叠；转录、评分、去重、跟脸裁切全部依赖 MuAPI/Vadoo，仓库不含本地高光算法 | 不引入 | 坐标模式与多候选展示仅作设计参考；不复制密钥、任意路径上传、阻塞轮询、宽松解析、下载/保存节点或示例 Workflow | 根 MIT 仅覆盖节点包装；MuAPI 条款禁止用 API 构建竞争服务，视频外发、隐私、费用、第三方模型与客户授权另核 |
| Toonflow `d86cce6` | 可编辑媒体节点画布、端口类型、选区/分组、自动布局、快捷键和 Agent 分镜交互 | 补当前关系图交互；其 Agent 与 OpenMontage 重叠，其媒体生成与 ComfyStudio 重叠 | 部分复用 | 端口、快捷键、布局、选择算法分别适配；不引入 Vue/Pinia/Bun/通用 Agent | MIT；Vue Flow、Dagre、媒体 SDK、模型权重另核 |
| [SmartGallery](./smartgallery-current-project-integration-audit-2026-09-28.md) `3cbcf83` | ComfyUI PNG/视频嵌入 Workflow、模型/LoRA/提示词解析与生产配置分组；素材画廊、故事板和目录扫描行为 | 补 MPT/OpenShorts 未覆盖的生成产物追溯；不提供成熟视频语义选片，AI Search 源码标注未发布 | 迁移后适配 | 仅把受控元数据解析和版本化分组哈希迁入 `assetId` 派生记录；扫描/预览只吸收行为；不引入 Flask/SQLite、路径 MD5、ComfyUI 队列、OmniQuery 或审核标签 | 根 MIT；无上游测试且依赖未锁；媒体、Workflow、模型/LoRA、FFmpeg 构建和未来 AI 服务另核 |
| [Media Search Agent](./media-search-agent-current-project-integration-audit-2026-09-28.md) `1ea46df` | 本地视频分镜、关键帧、CLIP 向量、SQLite 正本/Qdrant 派生索引、增量墓碑和时刻预览 | 补全库视觉片段召回；OpenShorts 仍负责候选价值/高光判断，SmartGallery 负责生成产物元数据 | 部分复用 | 迁移索引 schema、扫描失效和查询思路时改为 `projectId + assetId + sourceFingerprint + [start,end]`；修复上游按 `media_id` 提前折叠同视频多镜头且搜索响应丢失区间的问题；不引入 Tauri、独立 API/运行器或人脸库 | 根 MIT + NOTICE；模型权重、PyTorch/Qdrant/ExifTool/FFmpeg 和测试媒体另核；InsightFace 预训练权重非商业 |
| [OpenStory](./wechat-article-projects-intake-2026-09-28.md) `3ff7e58` | 同源生成依赖计划、运行输入快照、产物输入哈希、claim/迟到结果分歧、费用前置和来源记录 | 与 Toonflow/OpenMontage 的 Storyboard/Agent 链重叠；补的是更成熟的失效与并发写回设计，不补共享素材库或视频语义检索 | 只参考设计 | 若立项，在现有 `durable_runs`、制作镜头版本和审核边界重新实现只读依赖 verdict 与 CAS/claim；不引入 Sequence/Scene/Shot/Frame 第二模型、Cloudflare/D1/R2/积分、模型生成、MCP 或 MediaBunny 导出 | 根 MIT；依赖、供应商模型/输出、用户媒体、字体/测试素材和云服务条款另核 |
| [Huobao Drama](./huobao-drama.md) `27f07fc` | 剧本/角色/场景/道具到分镜的结构化前置；提示词中的命名素材引用、全局风格与对象固定描述、分阶段缺项定位 | 与 Toonflow/OpenStory 的 Storyboard 重叠；独有补充是“提示词引用哪一份角色/场景/道具素材”的可核验关系 | 只参考设计 | 在当前 `assetId`、制作镜头和 Prompt 上独立实现素材引用插入、缺失/重名/数量上限校验及跨镜头一致性卡片；不引入剧集模型、Mastra、模型任务系统或整集拼接链 | CC BY-NC-SA 4.0，限制商业使用；不直接复制源码或界面资源，模型、素材和生成服务另核 |

## 3. 跨项目能力归并

| 产品能力 | 首选来源 | 次选/补充来源 | 融合后的唯一落点 | 不再重复引入 |
| --- | --- | --- | --- | --- |
| 本地长视频自动拆镜头与候选片段 | OpenShorts | opensource-clipping 仅补播客构图失败场景与验收用例；ai-clipping-comfyui 仅作远端坐标输出反例 | 当前项目新增 `SourceMediaResolver` + `HighlightSuggestionService`，保存稳定源身份、区间、转录/场景证据与运行版本 | 不伪造上游不存在的独立 `ClipIndex`；不把 MuAPI 包装当本地算法；不建立第二高光、字幕、下载或渲染链 |
| 多素材按脚本混剪 | MoneyPrinterTurbo 的分组、去重和来源规则 | OpenShorts 的片段候选排序 | 当前 `MontagePlanner` → 现有 Timeline 建议，用户可改选 | 不迁 MPT 全流程任务和 MoviePy 合成 |
| 外部图库素材 | MoneyPrinterTurbo | short-video-maker 仅补轻量输入/失败呈现的反例 | 当前 `MaterialProvider` → 导入 `ProjectAssets` 后再参与制作 | 不复用 short-video-maker 的 Pexels 随机兜底、无来源快照下载器；不让远端 URL 绕过项目素材身份与授权快照 |
| 节点画布编辑 | Toonflow | ComfyStudio 的镜头/Workflow 对应思想 | 当前 XYFlow、Zustand、FastAPI 的受约束图命令 | 不引入 Vue Flow、Pinia、Qt 画布或第二节点注册中心 |
| ComfyUI Workflow 准备与交换 | ComfyStudio 的格式识别、参数/端口建模 | 当前固定候选模板、Toonflow 的多模态能力矩阵 | 范围批准后增加受控 Workflow 导入/规范化/校验/候选导出；优先官方 API-format，最终生成仍在外部工具完成 | 不恢复应用内最终生成、第二队列或 Qt/ComfyUI 状态；不把 Workflow 执行状态当项目/审核状态 |
| Storyboard/Agent 创作 | Toonflow 的文字分镜和连续性检查 | OpenMontage 补阶段证据与人工暂停表达；Huobao 补角色/场景/道具的命名素材引用 | 候选 Storyboard → 人工确认 → 规范 Scene/Shot；引用始终落到当前 `assetId` | 不引入通用 Agent 会话、Pipeline/checkpoint 运行时、剧集模型或另一套 Storyboard 数据库 |
| 跨镜头一致性与提示词素材引用 | 当前项目素材、制作镜头与 Prompt | Huobao 的全局风格、对象固定描述和 `@名称` 引用行为 | 当前 `ProjectAssets(assetId)` + Shot 引用；UI 显示实际引用图并校验缺失、重名和数量上限 | 不以名称字符串替代素材身份，不复制其非商业代码，不建立角色/场景/道具第二素材库 |
| 配音、字幕与试听 | 当前实现 | MPT 的全文+音色+参数指纹和实际时长校验 | 当前本地配音、字幕版本与预览失效规则 | 不引入第二 TTS 路由或上游进程内缓存 |
| Timeline、渲染、取消与恢复 | 当前实现 | 仅按明确缺失单独取材 | 当前 Timeline + FFmpeg + `durable_runs` | 不引入 MoviePy、Remotion 或 Qt 时间线替换现有闭环；不复制 short-video-maker 的进程内串行数组与文件存在性状态 |
| 审核、交付与版本 | 当前实现 | OpenMontage 的交付承诺、质量摘要和门禁表达 | 当前预览、审核、过期和导出历史；提示结果不成为审核状态 | 不把生成任务、Agent checkpoint 或启发式分数当审核状态 |
| 费用与外发确认 | 当前供应商选择和运行快照 | OpenMontage 的估算→确认→核销产品语言 | 在实际付费/外发调用前冻结 provider、model、素材范围、估算与确认凭证 | 不复制未统一接入 BaseTool 的 `CostTracker`，不另建 `cost_log.json` |
| 多语言交付 | 当前变体、音轨、字幕和审核版本 | YumCut 的逐语言进度、失败隔离与语言 tab | 语言作为现有变体/预览/审核/交付版本的维度 | 不引入 `ProjectLanguageProgress` 或线性 ProjectStatus |
| 对外自动化 API | 当前 FastAPI 项目边界 | YumCut 的 read/write scope、请求指纹和成功响应重放语义 | 需求立项后在当前 API 独立实现 Bearer key 与幂等外层 | 不复制 PolyForm 代码，不以 API 绕过费用、版本冲突或审核 |
| 自动发布 | 暂不纳入核心范围 | MPT、opensource-clipping 仅作未来调研 | 若立项，必须位于“批准后导出”的独立 Publisher Adapter | 不让发布失败回写或破坏已批准成片 |
| 跨项目素材发现与组织 | 当前 `ProjectAssets` 继续持有项目内资产 | SmartGallery 补 ComfyUI 元数据与预览行为；Immich、UnoPim 为待深审候选 | 待建共享 `AssetLibrary` 只负责目录、标签、衍生预览、来源与权利元数据；选中后显式导入 `ProjectAssets` | 不让共享库路径、供应商 ID 或向量 ID 成为 Shot/Timeline 主键；不替换 `ProjectAssets` |
| 视频片段语义检索 | Media Search Agent 的分镜/关键帧/向量索引为部分复用来源；OpenShorts 负责候选价值与重叠去重 | Boomerang 为待深审候选 | 片段索引绑定稳定 `assetId`、源指纹、模型/算法版本和源时间区间，修复多镜头折叠后再输出候选 | 不把 Qdrant/FAISS ID 当业务身份，不建立第二审核或渲染链；不照搬上游缺失区间的搜索响应 |
| 开放素材及权利快照 | MPT 的图库 Provider 规则 | Openverse 为待深审的图片/音频补充来源 | Provider 结果经下载、格式与权利检查后导入 `ProjectAssets`，保存作者、来源页、许可 URL/版本、查询与取得时间 | 不把搜索结果或远端 URL 直接用于生产；Openverse 不作为库存视频来源 |
| 依赖过期与迟到结果保护 | 当前版本、运行快照、审核失效规则 | OpenStory 的 generation plan、input hash 与 claim/divergent 机制只作设计参考 | 需求立项后，在现有业务对象上派生缺失/过期/运行中/未知及原因；旧运行结果仅保留为候选，不静默覆盖新选择 | 不引入第二 Sequence、D1 表、Cloudflare Workflow 或上游工序词汇；哈希不替代权限、来源或审核 |

## 4. 累计优先级

本节服从[完整产品规划与范围冻结基线](../../.scratch/product-scope-freeze/spec.md)。参考项目只能为已经批准的能力提供实现来源，不能自行生成产品范围。

### P0：不新增参考项目模块

1. 使用真实商品完成“商品事实与素材核验 → 正式五条 AI 脚本 → 选声配音 → 至少两条差异化作品 → 字幕预览 → 人工审核 → 批准版本导出”的试点交付。
2. 修复项目素材删除引用检查未覆盖 AIGC 简报/候选和批量变体的问题。
3. 关闭仍能提交最终生成的历史后端行为，使代码与 ADR 0007、ADR 0009 及正式界面边界一致。
4. 在上述 P0 完成前，不实施本登记表中的新算法、Provider、素材库、画布编辑、Workflow 导入或 Agent 能力。

### P1：v1 后的核心生产增强

1. 以 OpenShorts 为主要算法参考，用当前项目的 `SourceMediaResolver` + `HighlightSuggestionService` 建模大量本地视频的稳定来源、候选区间、转录/场景证据和高光排序。
2. 以 MoneyPrinterTurbo 为参考实现 `MontagePlanner` 的有序分配、去重、来源记录和跨变体差异化；输出进入现有变体编辑序列，不另建合成链。
3. 建立跨项目共享素材库、视频片段语义索引和 `MaterialProvider`；选中素材必须显式导入 `ProjectAssets`，并保存来源与许可快照。
4. 管理 ComfyUI 生成产物时，按 SmartGallery 固定提交迁移后适配受控元数据解析与版本化 Workflow 分组哈希，不先建第二素材库服务。
5. 按 Toonflow 取材受控依赖连线、端口校验、快捷键、选择树和自动布局；Scene、Shot、节点及节点输入仍是权威数据，画布不另存业务边。
6. 复核 MPT 的配音预览指纹是否补充当前缓存，已被现有版本规则覆盖的部分不重复开发。

### P2、延后与预留：单独立项

1. 受控 ComfyUI Workflow 导入/规范化：仅在产品范围明确后接受官方 API-format；UI-format 转换按 ComfyStudio 迁移后适配并补完整图校验，只生成候选包，不恢复应用内最终生成提交。
2. Agent Storyboard：吸收 Toonflow 连续性检查、OpenMontage 的阶段证据/人工暂停表达，以及 Huobao 的命名素材引用和一致性分层，只生成候选方案。
3. AI 全新商品图片、AI 视频镜头、AI 音乐、多语言交付、公开 Bearer API 和有限 MCP 接口属于 P2；各自使用现有版本、审核与交付边界。
4. 自动发布、多客户/多品牌 SaaS、客户自助、实时协作、计费和数字人直播延后；发布只能消费已批准导出，并须先解决并发恰一领取。
5. 分布式 Worker、通用插件市场、通用 MCP、Publisher Adapter 和外部生成 Adapter 只预留接口，不构成当前功能承诺。
6. 任何付费外部高光 Provider 必须先通过服务条款、客户视频外发授权、费用确认、数据保留和安全上传/下载审查；ai-clipping-comfyui 不作为实现底座。

## 5. 后续参考项目的增量规则

每新增一个参考项目，先回答以下问题并更新本表：

1. 它补的是哪一个已登记能力，还是确有新的业务能力？
2. 如果与已有候选重叠，哪一个成为首选，另一个是补充还是淘汰？
3. 融合后由当前项目哪个唯一模型、Store、API 和任务状态持有？
4. 是否会产生第二项目系统、第二素材身份、第二画布、第二队列、第二时间线或第二审核模型？若会，默认不引入。
5. 固定提交、根许可证、子目录许可证、第三方依赖和外部服务条款是否都已核查？
6. 结论必须落到“直接复用、迁移后适配、部分复用、只参考设计、重新实现、不引入”之一，并同步调整本表的首选来源和优先级。

该登记表只记录已完成审计后的决策。没有固定提交和 License 证据的项目，不进入代码复用候选。

## 6. 素材库候选池与逐项深审顺序

[30 项素材库候选初筛](./material-library-projects-intake-2026-09-28.md)已核对候选项目的一方仓库、定位、活跃度快照和根许可证。2026-09-29 再次读取“全网趋势研究”原对话后，已确认其中编号 1–30 与初筛报告逐项一致；额外提到的 AtroDAM、OpenPype 和 Clapper 也分别以排除项或第 19 项记录，没有发现对话内另列但未登记的素材库项目。原分享会话的 Top 10 不直接沿用；结合当前项目已经存在的 Shot、Timeline、FFmpeg、审核和 `ProjectAssets` 边界，固定提交深审顺序调整如下，其中 SmartGallery 与 Media Search Agent 已完成固定提交源码审计，其余仍未批准复制代码：

1. SmartGallery（已完成）：结论为“迁移后适配”受控 ComfyUI 元数据解析和生产配置分组；扫描/预览只吸收行为。
2. Media Search Agent（已完成）：结论为“部分复用”；可借分镜/关键帧/向量与增量失效设计，但必须修复同视频多镜头折叠和搜索响应丢失区间。
3. Boomerang：精确时刻、连续区间、窗口细化和重叠去重。
4. Openverse：开放图片/音频的来源、作者和许可字段适配。
5. Immich：外部目录扫描、重复项、路径变化和元数据失效边界，仅参考设计。
6. UnoPim DAM：作者、版权、来源、自定义字段及批量标签语义，按当前技术栈重新实现。

这一队列与前述视频项目深审是累加关系：MPT 仍是外部图库与脚本分配首选参考，OpenShorts 仍是本地长视频候选发现首选参考；新候选只补共享目录、库级元数据、权利链与片段搜索，不重新审或替换已经确定的唯一能力归属。

## 7. 历史专题参考项目覆盖状态

此前研究中的项目不全部具备同等审计深度。下表用于防止“分析过但主表没有记录”，也防止把专题初筛误称为固定提交深审：

| 专题与项目 | 当前覆盖状态 | 与十三项主线的关系 | 后续处理 |
| --- | --- | --- | --- |
| Huobao Drama | 已核固定提交、源码路径、技术栈和 CC BY-NC-SA 4.0；已进入十三项正式决策 | 补 Toonflow/OpenStory 未明确表达的命名素材引用和跨镜头一致性 | 只参考设计；需要时按当前 `assetId`/Shot/Prompt 重新实现 |
| BeatDesign、React Flow/xyflow、ComfyUI、basketikun/infinite-canvas、tldraw、AFFiNE/BlockSuite、Excalidraw | 已完成画布专题的产品、许可和边界比较；不是逐仓函数级复用审计 | 结论已被 ADR 0010、当前 `@xyflow/react` 实现和 Toonflow 深审吸收 | BeatDesign 保留产物血缘参考；XYFlow 作为渲染依赖；其余不建立第二画布或自由白板数据模型 |
| Warabellum/openshorts、OpenCut/OpenCut Classic、Clip Architect、NarratoAI、MoneyPrinter、ShortGPT、free-react-video-editor、Cap、openvideodev/react-video-editor | 已完成 11 仓商品短视频流程初筛，部分固定到文件/提交；不是全部逐仓深审 | MPT 与 OpenShorts 已升级为主线深审；其余主要补 UI、试听、素材审核或时间线反例 | 保留专题结论；只有出现对应明确缺口时再做固定提交深审，不整体迁入编辑器或第二任务链 |
| react-resizable-panels、jnMetaCode/openshorts、PySceneDetect | 已在内容工作台/配音专题核过版本、目标文件和许可 | 分别是分栏实现候选、试听组织参考、镜头检测备选；都不是独立业务系统 | 依赖或函数接入前再核当前版本；已有同类能力优先保留，不因曾入选就默认安装或复制 |
| 素材库 30 项候选 | 全部完成一方仓库、定位、活跃度快照和根许可初筛；仅 SmartGallery、Media Search Agent 已完成固定提交深审 | 其余项目通过第 6 节队列补共享素材目录、元数据、权利链和片段检索，不替换 MPT/OpenShorts | Boomerang、Openverse、Immich、UnoPim 依序深审；未深审项目不得标为可直接复用 |
| Qwen3-TTS、mlx-audio、Whisper/faster-whisper、MediaPipe、FFmpeg、Remotion 等模型/库/运行依赖 | 分散记录在配音、分析、渲染或候选项目报告中；它们是依赖来源，不是产品级参考项目 | 由实际使用模块和锁文件管理，不进入业务项目数量 | 引入或升级时单独核版本、许可证、模型权重、数据和商用条款 |
| 百炼、火山方舟、Fal、Pexels、Pixabay、Coverr 等供应商与内容源 | 已在供应商、素材或对应项目报告记录能力和条款边界；不属于可复制的开源项目 | 通过显式 Provider/Adapter、外发确认、费用和来源快照接入 | 不因参考项目支持某供应商就自动获得账号、额度、内容权或发布权 |

因此，“已纳入累计考虑”与“已完成深度源码审计”必须分开表述：十三项主线有明确融合决策，30 项素材库有完整初筛和深审队列，历史 UI/画布/配音候选有专题结论；除已标明固定提交深审者外，均不能直接进入代码复用清单。
