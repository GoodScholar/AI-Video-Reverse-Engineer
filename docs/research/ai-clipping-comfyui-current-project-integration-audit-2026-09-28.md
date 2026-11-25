# ai-clipping-comfyui 与当前项目源码级融合审计（2026-09-28）

> **结论：不引入。** 审计固定提交 [`d0a7b1943dfd1e144c9bfe656e57941623b6408d`](https://github.com/Anil-matcha/ai-clipping-comfyui/tree/d0a7b1943dfd1e144c9bfe656e57941623b6408d)，并与[累计登记表](./reference-project-integration-register.md)及此前八份项目深审对照。上游的高光识别、转录、排序、去重和裁切在 MuAPI/Vadoo 的外部服务完成；本仓库是 ComfyUI 节点包装，不含可移植的本地高光算法。本文为静态源码审计，未调用付费 API、运行 ComfyUI 或实测视频质量。上游文件链接均锁定此提交；下文的接入方案是条件性设计，不表示已实施。

## 1. 参考项目核心能力

该提交提供四个节点：API Key、AI Clipping、Pick Clip、Save Video，由 [`__init__.py`](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/__init__.py#L1-L12) 合并注册。真实调用链是本地文件上传或使用 URL → `POST /ai-clipping` → 同步轮询结果 → 宽松解析列表 → 选取、下载和返回 ComfyUI `IMAGE`。主节点源码没有 Whisper、语义打分、重叠去重、跟脸裁切或 FFmpeg 处理；这些是 [`README.md`](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/README.md#L36-L49) 对服务端能力的描述，不能归为本仓库本地算法。[提交入口与轮询](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L72-L121)、[主节点](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L222-L292)。

## 2. 当前项目核心能力

当前项目以唯一内容项目、[`ProjectAssets`](../../backend/app/project_assets.py)、规范制作镜头与结果候选、[`TimelineStore`](../../backend/app/timeline.py)、[`durable_runs`](../../backend/app/durable_runs.py) 和当前版本的[审核/交付](../../backend/app/batch_production.py)形成权威链。参考镜头段与制作镜头不同，候选时间区间不能自动成为已采用镜头、已批准预览或交付版本；领域词义见 [`CONTEXT.md`](../../CONTEXT.md)。[ADR 0007](../adr/0007-preproduction-with-shot-steps.md)规定最终 AI 视频生成在外部完成，[ADR 0009](../adr/0009-content-project-and-workflow-boundaries.md)规定单一内容项目和状态边界，[ADR 0010](../adr/0010-shot-workflow-complementary-views.md)规定列表与关系图引用同一 Shot。

## 3. 产品定位差异

上游面向已安装 ComfyUI 且持有 MuAPI Key 的用户，把一次付费远端剪辑调用包装为画布节点；本项目面向本地内容工作室与复刻前置准备，不能让 ComfyUI 工作流、云端结果或节点执行状态成为业务真相源。README 的“free/open-source/self-hostable Opus Clip alternative”只可理解为**节点包装代码可自托管**，不能推论核心分析服务免费、开源或可自托管：同一 README 明言服务器承担全链路且按 MuAPI 使用付费。[README 宣称](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/README.md#L1-L30)、[服务器描述](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/README.md#L36-L49)、[官方 AI Clipping API](https://muapi.ai/playground/ai-clipping/api)。

## 4. 功能模块对比

| 上游模块 | 源码事实 | 当前落点与标签 |
| --- | --- | --- |
| `AiClippingApiKey` | 普通 `STRING` 节点，也读 `~/.muapi/config.json`；[源码](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L37-L53) | 当前凭据安全存储；**不引入**工作流普通字符串 Key |
| `AiClipping` | 上传、提交、轮询、输出 JSON/首帧；[源码](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L243-L292) | 若需求成立，当前 FastAPI 外围 Provider；现阶段**不引入** |
| `AiClippingPickClip` | 1 基序号、越界夹取、可下载首帧；[源码](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L295-L348) | 多候选展示方式**只参考设计**，不复制选择语义 |
| `AiClippingVideoSaver` | URL 下载至 ComfyUI 输出目录并把帧堆入内存；[源码](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_video_saver.py#L17-L91) | 当前素材导入与媒体检验；**不引入**下载器 |

## 5. 业务逻辑对比

上游 `video_url` 与 `video_path` 同时提供时静默优先 URL；本地路径只做 `isfile` 后原样上传，MIME 固定写 `video/mp4`，未检验文件真实格式、项目归属或授权范围。`return_coordinates_only` 仅告诉远端省去裁切，**仍会上传本地视频或向远端提交源 URL**。当前若产生高光候选，必须先锁定 `ProjectAssets` 源身份、修订和用户许可，再把区间作为可审查建议；不得因坐标输出就免除外发披露。[上传](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L72-L87)、[优先级及 payload](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L264-L283)。

## 6. UI / UX 对比

两个工作流示例分别展示首条保存和三路 `Pick Clip → Save Video`，属于 ComfyUI 画布接线，而非本项目的镜头列表、候选对照、素材授权、费用确认、审核与交付界面。[单路示例](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/AiClipping_Example.json)、[三路示例](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/AiClipping_FanOut_Example.json)。API Key 作为普通 widget/连线 `STRING`，保存工作流时**可能明文落入 JSON**；示例 widget 值为空并不证明实际密钥会被保护。当前界面应在分析服务设置中管理凭据和显式外发确认，而不是复制此输入方式。[Key 节点](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L201-L219)。

## 7. 技术架构与运行边界

上游只有三个 Python 节点/注册文件、[`requirements.txt`](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/requirements.txt)、两个工作流 JSON、README 与 LICENSE；固定提交 [`d0a7b19`](https://github.com/Anil-matcha/ai-clipping-comfyui/commit/d0a7b1943dfd1e144c9bfe656e57941623b6408d) 将原课程仓内容一次性替换为节点包，旧提交内容不能当作本版剪辑模块。节点只使用 `requests` 调外部 HTTPS API；没有独立 FastAPI、数据库、后台 worker、WebSocket、项目系统或本地推理。[依赖](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/requirements.txt)、[URL 常量](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L28-L32)。

## 8. 数据模型、队列与持久化

输出是 `first_clip_url`、`IMAGE`、未定 schema 的 `highlights_json` 字符串、`request_id` 和数量；Pick Clip 以数组位置而非稳定候选 ID 选片。`_extract_clips` 兼容 `shorts/highlights/clips/outputs/output`，却不校验 `start_time < end_time`、非负时长、源媒体时长、分数范围、重叠、URL 类型或结果来源；Pick Clip 只在读取时尝试 `float/int`。`request_id` 仅输出，仓库没有按它恢复查询的节点；`_poll` 在 ComfyUI 执行线程内每 10 秒轮询，最长约 1800 秒，无持久队列、取消、重试计划或恢复。[提取](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L124-L155)、[轮询](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L104-L121)、[输出](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L259-L292)、[Pick Clip](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L323-L348)。

## 9. 当前项目已有优势

当前 `ProjectAssets` 有项目范围和稳定素材 ID；`ShotProduction` 有候选与人工检查；`TimelineStore` 有剪辑区间；`durable_runs` 保存执行快照且禁止运行结果写入采用/审核/交付业务决定；批量制作审核只对当前可播放预览生效。上游返回的 URL/数组序号/`completed` 均不承载这些事实，不能替换现有边界。[素材](../../backend/app/project_assets.py)、[镜头](../../backend/app/shot_production.py)、[时间线](../../backend/app/timeline.py)、[运行](../../backend/app/durable_runs.py)、[审核](../../backend/app/batch_production.py)。

## 10. 参考项目优势与真实性边界

其真正可借鉴的是“一个请求返回排序候选、坐标模式、按序号分流”的极简交互形状；但排序品质、分数定义、字幕/跟脸精度、API 时延和产出可用性无法从客户端源码证明。README 的 1–20 `num_clips` 与三个比例是节点 UI 限制；MuAPI 当前[模型页 schema](https://muapi.ai/comparison/ai-clipping)显示 `num_highlights` 上限 60、六种比例，说明节点与服务契约已可能漂移，不能把节点 UI 当服务端上限或稳定承诺。官方[模型页](https://muapi.ai/zh/playground/ai-clipping)标识“Powered by Vadoo AI”并展示按视频分钟与 highlight 收费；价格是查询时快照，接入前须重核。上游客户端没有可验证的服务端算法实现。[节点参数](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L243-L258)。

## 11. 功能映射与累计去重

| 需求 | 当前与前序首选 | 本项目新增价值和处理标签 |
| --- | --- | --- |
| 本地长视频高光候选 | [OpenShorts](./openshorts-current-project-integration-audit-2026-09-28.md) 的本地候选/转录/去重规则；[opensource-clipping](./opensource-clipping-current-project-integration-audit-2026-09-28.md)仅供播客构图验收 | 本仓无本地算法；**不引入**，坐标输出形状**只参考设计** |
| 素材搜索与脚本分配 | [MoneyPrinterTurbo](./moneyprinterturbo-current-project-integration-audit-2026-09-28.md) | 无素材检索或规划；**不引入** |
| ComfyUI workflow 交换 | [ComfyStudio](./comfystudio-current-project-integration-audit-2026-09-28.md) 的受控格式识别/映射 | 仅自定义节点接线；**不引入**依赖它的工作流 |
| 关系图交互与 Agent 分镜 | [Toonflow](./toonflow-current-project-integration-audit-2026-09-28.md)；[OpenMontage](./openmontage-current-project-integration-audit-2026-09-28.md)仅供阶段表达 | 无同等能力；**不引入** |
| 批量制作、发布、轻量文字成片 | [YumCut](./yumcut-current-project-integration-audit-2026-09-28.md)、[short-video-maker](./short-video-maker-current-project-integration-audit-2026-09-28.md)已分别限于设计参考；本地 Timeline/审核是权威 | 上游只在 ComfyUI 复制三条分支，不是生产批量任务；**不引入** |

## 12. 可以直接复用的代码

**无。** 根 [`LICENSE`](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/LICENSE) 是 MIT，复制实质代码须保留版权和许可文本；但许可通过不等于架构适合。节点的核心价值依赖 MuAPI 付费服务，上传/下载/密钥/阻塞轮询与当前约束冲突，故没有上游文件 → 当前目标文件的直接复制项。

## 13. 可以迁移后适配的代码

**无当前建议迁移条目。** 若将来获得产品需求和服务条款许可，目标应是现有 FastAPI 边缘的 `HighlightProvider` 协议、授权素材代理和 `durable_runs` 运行适配；需独立实现外发确认、成本估算、取消/恢复、响应校验、媒体下载安全、候选来源/修订记录，不能移植 [`AiClipping.run`](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L264-L292) 作为后端任务。

## 14. 可以部分复用的代码

**无当前建议复制片段。** `shorts/highlights/outputs` 兼容分支只能说明服务端响应可能漂移，不能作为规范解析器；`PickClip` 的夹取会把请求的第 3 条悄然改成末条，破坏候选身份。若未来需要多候选，应以 `sourceAssetId + runId + stableCandidateId + start/end` 建模，校验并拒绝越界索引；算法与解析器独立实现。[响应兼容](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L124-L155)、[夹取](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L323-L348)。

## 15. 只参考设计的功能

**只参考设计**：单次分析返回有序候选列表；允许只要时间坐标、跳过服务端裁切；可把不同候选分派给不同后续步骤。这三点仅是输出与交互形状，现有 OpenShorts 导向的 `HighlightSuggestionService` 规划更完整，包含源身份、区间、转录/场景证据和运行版本，不能以无来源的 JSON 数组取代。[节点输出](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L259-L292)、[累计登记表](./reference-project-integration-register.md)。

## 16. 不引入的功能

**不引入** MuAPI 节点包、示例 workflow、普通 `STRING` 密钥、任意服务端路径上传、同步 30 分钟轮询、宽松响应解析、URL 任意下载、ComfyUI Saver 和其磁盘/内存策略，也不把远端 `completed` 当本地“已采用/已审核”。尤其 Saver 的 `save_subfolder`/`filename_prefix` 没有验证输出根边界，可用绝对路径或 `../` 越界；目标文件直接打开写入，异常会留半文件，先 `exists` 后写也有竞态；`frame_load_cap=0` 可把全部帧堆进内存。`_first_frame` 下载到临时文件后仅在成功路径删除，失败可遗留文件；两个下载入口都没有主机白名单、内容类型和大小上限，按接入环境可能触发服务端请求伪造（SSRF）或资源耗尽。这里是源码风险判断，未做可达性攻击测试。[Saver](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_video_saver.py#L35-L91)、[首帧](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L158-L178)。

## 17. 当前项目缺失或待确认

当前项目是否需要**付费外部高光服务**，以及客户视频是否允许向 MuAPI/Vadoo 和其执行供应商传输，都须由产品和授权范围决定；不能从“ComfyUI 集成”推导出需求。真正已登记的缺口是大量本地长视频的稳定来源与高光候选链，由 OpenShorts 优先提供算法证据。本仓未补该缺口；其远端模型具体质量和中文能力没有本次实测。[累计登记表](./reference-project-integration-register.md)、[OpenShorts 深审](./openshorts-current-project-integration-audit-2026-09-28.md)。

## 18. 可以删除或替换的当前实现

**无。** 上游没有可替换当前 [`ProjectAssets`](../../backend/app/project_assets.py)、[`ShotProduction`](../../backend/app/shot_production.py)、[`TimelineStore`](../../backend/app/timeline.py)、[`durable_runs`](../../backend/app/durable_runs.py) 或审核/交付模型的实现。本次只新增本报告，未改产品代码或累计登记表。

## 19. 融合后的模块架构

当前结论是**不接入**。若将来对第三方高光服务单独立项，唯一合理的条件性落点是：`ProjectAssets` 授权素材 → 当前 FastAPI 的外发确认和 Provider 边界 → `durable_runs` 冻结来源/配置/费用/响应 → 验证过的候选区间 → 当前 Shot/Timeline 建议 → 人工采用与审核；外部服务只持有执行结果，不拥有项目、镜头、剪辑或交付状态。该路径也适用于其他 Provider，不为本仓库另造 ComfyUI 状态系统。[ADR 0009](../adr/0009-content-project-and-workflow-boundaries.md)、[运行约束](../../backend/app/durable_runs.py)、[审核规则](../../backend/app/batch_production.py)。

## 20. 融合后的用户流程

现阶段继续本地素材导入、OpenShorts 导向的高光候选评估、用户确认制作镜头/时间线、预览及审核；无需在前置工作台安装或运行本节点。若未来获准使用 MuAPI，必须先展示发送哪段素材/URL、费用和供应商，用户确认后提交；返回时间段先是建议，源媒体时长/边界核验并人工选择后才能进入规范镜头或 Timeline。最终 AI 视频生成仍由外部工具执行，结果另行导入当前素材与候选记录。[ADR 0007](../adr/0007-preproduction-with-shot-steps.md)、[当前领域](../../CONTEXT.md)。

## 21. 对当前代码目录的影响

本次**仅新增本报告**。没有理由将上游 `ai_clipping_nodes.py`、`ai_clipping_video_saver.py`、示例 JSON 或 `requirements.txt` 复制进 `backend/app/` 或 `frontend/src/`。未来条件性 Provider 若获准，应位于现有服务/运行边界旁，并以 [`preproduction_api.py`](../../backend/app/preproduction_api.py) 的项目 ID 和修订约束接入；不能加第二个 ComfyUI 插件目录作为本项目业务入口。

## 22. 数据模型调整

本次**无调整**。未来如需外部高光，候选至少记录 `sourceAssetId`、媒体指纹/时长、`runId`、Provider/模型与响应版本、稳定候选 ID、秒单位 `start/end`、证据、状态和来源；远端 URL 仅是受控下载来源，不是素材 ID。上游 `highlights_json` 的 `title/score/hook_sentence/start_time/end_time/clip_url` 没有稳定来源或版本关系，甚至不保证字段存在，不能直接升级为 Shot 或审核事实。[README 示例 schema](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/README.md#L195-L211)、[宽松提取](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L124-L155)。

## 23. Store 与状态调整

本次**无调整**。上游仅有 ComfyUI 节点运行时状态，`request_id` 不会持久化为可恢复业务运行；`status=completed` 只说明远端调用结束。当前继续由 [`durable_runs`](../../backend/app/durable_runs.py) 记录运行事实、现有工作台 Store 保存同一 Shot 的视图状态、Timeline 持有播放顺序、审核记录持有批准决定；不得加平行 MuAPI 队列或把 ComfyUI 图节点视为制作镜头。[轮询状态](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L104-L121)、[ADR 0010](../adr/0010-shot-workflow-complementary-views.md)。

## 24. API 与服务调整

本次**无调整**。上游 API 调用是 `x-api-key` 头下的上传、提交、查询，且 `_check` 对其他非 2xx 错误先抛再由宽泛 `except Exception` 接住，通常退化成文本错误，结构化错误信息丢失；对 401/402/429 只给文本提示。若未来获准接入，要从当前 FastAPI 项目 API 发起，按当前凭据管理、外发确认、URL/文件 allowlist、大小/时长/内容类型校验、超时/取消/恢复、费用快照和错误分型独立重写。服务的 `request_id` 要与本地运行 ID 关联且不可当作可重试凭据或业务主键。[上游错误](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L56-L69)、[远端调用](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py#L72-L121)、[本地 API](../../backend/app/preproduction_api.py)。

## 25. P0 / P1 / P2、许可与验收

| 优先级 | 决策与验收 |
| --- | --- |
| P0 | **不引入**节点与远端服务；继续 OpenShorts 首选的本地候选规划；确认无第二项目、素材、队列、Timeline 或审核系统。本报告完成该研究决策。 |
| P1（仅需求明确后） | **只参考设计**坐标模式和多候选展示；在当前 `HighlightSuggestionService` 规划中验证稳定来源 ID、区间、时间单位、候选去重和人工采纳。无上游代码复制。 |
| P2（仅另行批准外部 Provider 后） | **重新实现**安全 Provider：条款与客户外发授权确认、费用估算、Key 安全存储、受控上传/下载、可恢复运行、响应校验、失败/取消和审核边界；需在实际服务契约及真实媒体上验收。 |

**License 与服务门禁。** 本仓 [`LICENSE`](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/LICENSE) 为 MIT，仅授权仓库作者可授权的节点源码；本次代码复用为**无**，没有复制或分发上游代码。Python 包、ComfyUI 本体、远端 MuAPI/Vadoo 服务、客户视频和输出媒体不因根 MIT 自动获授权。MuAPI 官方 [Terms（2025-07-02）](https://dev.muapi.ai/api/app/pdf/terms.pdf)明确禁止用 API 建 competing services，第三方工具条款由客户负责；本产品若以其构建对外高光服务，须先取得适用范围的明确许可，不能仅凭 README 的“open-source alternative”上线。官方[隐私政策](https://www.muapi.ai/api/app/pdf/privacy_policy.pdf)称输入/输出可能临时存储，日志与元数据按分析、计费、安全需要保留，数据可用于改进模型/服务，可为用户显式使用的服务与第三方模型提供商共享，也可能跨境处理。官方[AI Clipping 页](https://muapi.ai/zh/playground/ai-clipping)本次显示约 `$0.05/视频分钟 + $0.05/highlight`，价格需在真实调用前重新确认；现有节点没有费用估算或确认环节。

**验证记录。** 已核对固定提交完整文件树、三个 Python 文件、依赖、两份工作流、README、MIT 许可及该提交的替换范围，并与当前源码及前序累计审计对照；官方服务条款/隐私/产品 schema 为 2026-09-28 查询快照。静态语法与示例 JSON 检查通过（`python3 -m py_compile`、两个 JSON 的 `jq empty`）；没有运行上游功能测试、ComfyUI、付费 API 或本项目测试；固定树未见自动化测试文件。本报告完成后另检查 25 节结构、本地链接、固定 SHA 上游链接及 `git diff --check`。
