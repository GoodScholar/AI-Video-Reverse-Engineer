# 素材库候选项目初筛：30 项完整清单与深审队列

核验日期：2026-09-28。本报告的 30 项清单来自[用户提供的 ChatGPT 分享副本](https://chatgpt.com/s/t_6aba709ad88c8191b1437c852b5538a5)，其原始排序和许可证说法**仅是候选线索**；项目定位、实现和许可以各项目第一方仓库、文档及 GitHub 仓库 API 重新核对。分享页另点名 AtroDAM、OpenPype、Clapper 的排除理由，见文末。这里是入围初筛，**不是固定提交源码深审，也不批准复制代码**。活跃度只记录 2026-09-28 查询到的 `pushed_at` 快照；推送、星数和 README 自述都不能证明质量、成熟度或未来维护。

## 判断基线与结论

本项目已有按内容项目归属的 [`ProjectAssets`](../../backend/app/project_assets.py)，提供稳定素材 ID、安全文件安装、名称/备注、媒体种类/用途、引用检查和受保护删除；[`preproduction_api.py`](../../backend/app/preproduction_api.py) 已接收图片、视频、音频并探测基础尺寸/时长。现有[累计融合登记表](./reference-project-integration-register.md)把它定为唯一素材身份，把本地长视频候选发现交给待建的 `SourceMediaResolver` / `HighlightSuggestionService`，把脚本分配交给 `MontagePlanner`，把外部库存素材交给可选 `MaterialProvider`。因此新增能力应是**共享检索和组织层**：跨项目发现但不改变资产归属、元数据/标签/预览、可追踪的片段级索引与权利来源；不能再造第二套项目、任务、时间线或审核系统。这个判断与 [ADR 0007](../adr/0007-preproduction-with-shot-steps.md)、[ADR 0009](../adr/0009-content-project-and-workflow-boundaries.md)、[ADR 0010](../adr/0010-shot-workflow-complementary-views.md)一致。

现有 [MPT 深审](./moneyprinterturbo-current-project-integration-audit-2026-09-28.md)已经明确：Pexels/Pixabay/Coverr 的搜索和来源记录可启发 `MaterialProvider`，但其本地素材路径不做语义选片，且来源记录不等于授权证明。[OpenShorts 深审](./openshorts-current-project-integration-audit-2026-09-28.md)已经覆盖窗口、片段裁剪和重叠去重。下表优先寻找两者尚未覆盖的**库级元数据、视频片段检索和导入权利链**，不重复审整条生成/渲染流水线。

**推荐逐项固定提交深审顺序：** ① SmartGallery DAM（ComfyUI 产物和库级元数据）→ ② Media Search Agent（本地视频镜头索引、自然语言定位）→ ③ Boomerang（精确时刻/连续区间检索）→ ④ Openverse（开放图片/音频的来源与许可适配）→ ⑤ Immich（外部目录、重复项、搜索与元数据失效边界）→ ⑥ UnoPim DAM（作者、版权、来源、自定义字段及标签操作）。前四项直接补缺口；后两项验证导入和元数据契约。MediaCMS、Fireshare、QMedia 可按预览或多模态问答的实际需求再作专题，不在本轮代码深审队列。**本次无“直接复用”结论**：只有在固定提交、目标函数、根及子目录 License、模型/素材条款和本项目契约均过审后，才能升级为该标签。

## 完整候选清单与证据

表中“处理标签”是**初筛处理方向**，不是已通过许可或函数级复用审计。`最近推送` 是 UTC 日期，30 个仓库 API 在本次查询中均未标为 archived；仓库和 README 链接均指第一方，`元` 指 GitHub 仓库 API，根许可特殊项另见下一节。能力栏按“素材类型；检索/导入/索引/标签/预览/去重/元数据”写实；未见明确实现的能力不推断为已具备。

### A. 素材库、DAM 与托管（原清单 1–12）

| 原序 | 项目与第一方证据 | 已证实的定位、能力与技术栈 | 最近推送；根许可 | 对本项目的处理 |
| --- | --- | --- | --- | --- |
| 1 | [SmartGallery DAM](https://github.com/biagiomaf/smart-comfyui-gallery#readme) · [元](https://api.github.com/repos/biagiomaf/smart-comfyui-gallery) | 图片/视频/音频；扫描和 Auto-Watch 现有目录，上传时提取 ComfyUI 工作流；按 prompt、模型、LoRA、日期、评论、状态检索，SQLite/自然语言生成只读 SQL，视频缩略图/故事板/转码；工作流/提示词哈希分组**不是像素去重**。Python Flask + SQLite + FFmpeg，主体集中于 [`smartgallery.py`](https://github.com/biagiomaf/smart-comfyui-gallery/blob/main/smartgallery.py)。 | 09-17；MIT | **P0，迁移后适配**：深审嵌入元数据提取、扫描和预览边界；保留本项目 `assetId` 与项目归属，不迁其 ComfyUI 队列、客户端审核或整个 Flask 应用。 |
| 2 | [MediaCMS](https://github.com/mediacms-io/mediacms#readme) · [元](https://api.github.com/repos/mediacms-io/mediacms) | 视频优先，也管音频/图片/PDF；上传、分类/标签、搜索、裁切、分辨率转码、HLS、REST API；Django + React。是媒体门户/服务，不提供本项目片段语义选片。 | 09-24；AGPL-3.0 | **P2，只参考设计**：仅对照缩略图/预览/转码生命周期；现有 FFmpeg 和 FastAPI 不再加第二服务。 |
| 3 | [Immich](https://github.com/immich-app/immich#readme) · [搜索文档](https://docs.immich.app/features/searching/) · [外部库](https://docs.immich.app/features/libraries/) · [元](https://api.github.com/repos/immich-app/immich) | 本地照片/视频库；外部目录扫描、Postgres + VectorChord 的 CLIP 语义/元数据/人脸/标签过滤、图片相似重复项、预览与转码；TypeScript 服务/界面。外部目录**不保证全局去重**；移动路径后元数据可能丢失，见[官方 FAQ](https://docs.immich.app/FAQ/)。 | 09-28；AGPL-3.0 | **P1，只参考设计**：重点审“路径变化≠资产变化”、扫描与重复项的失败边界；不复制代码或用其资产 ID 替代本项目 ID。 |
| 4 | [PhotoPrism](https://github.com/photoprism/photoprism#readme) · [过滤文档](https://docs.photoprism.app/user-guide/search/filters/) · [元](https://api.github.com/repos/photoprism/photoprism) | 图片/视频浏览；标签、颜色、画质、位置、分辨率等组合过滤，Exif/XMP 合并与媒体管理；Go。偏相册检索，未证明片段级视频语义索引。 | 09-28；根文件 AGPL-3.0 | **P2，只参考设计**：可对照筛选维度；与 Immich 的库级检索高度重叠。 |
| 5 | [ResourceSpace](https://github.com/resourcespace/resourcespace) · [元](https://api.github.com/repos/resourcespace/resourcespace) | 企业 DAM，图片/视频/音频与文档的分类、元数据、权限、共享和交付；PHP。重点在团队资产治理，非镜头级发现。 | 09-26；自定义宽松条款，含商标限制 | **P2，只参考设计**：权限/元数据字段可看，项目当前本地单机边界无需整套企业 DAM。 |
| 6 | [UnoPim DAM](https://github.com/unopim/unopim-digital-asset-management#readme) · [元](https://api.github.com/repos/unopim/unopim-digital-asset-management) | 图片/视频/文档/音频；目录和上传、搜索/过滤、标签批量操作、作者/版权/来源自定义属性、关联资源、视频首帧缩略图、CSV/XLSX 分配；Laravel/PHP，AI 图片打标签为可选远端队列。 | 09-16；MIT | **P1，重新实现**：深审元数据字段与批量标签语义；以当前 Python/React 模型独立实现，不迁 PIM 目录树。 |
| 7 | [Pimcore](https://github.com/pimcore/pimcore#readme) · [元](https://api.github.com/repos/pimcore/pimcore) | PIM/MDM/DAM 平台；任意文件、200+ 格式预览、元数据/版本/渠道输出和 API；PHP Symfony、React Studio、MySQL、OpenSearch。 | 09-28；**POCL** | **不引入**：数据平台远超素材缺口，POCL 不是 MIT/Apache 开源许可。 |
| 8 | [LibrePhotos](https://github.com/LibrePhotos/librephotos#readme) · [元](https://api.github.com/repos/LibrePhotos/librephotos) | 照片为主、支持视频；人脸/物体识别、图像语义搜索和元数据检索；Django 5 API、FFmpeg，README 列 CLIP/FAISS 与第三方模型。 | 09-27；[根 MIT](https://github.com/LibrePhotos/librephotos/blob/main/LICENSE) | **P2，只参考设计**：与 Immich 重叠，视频时间段检索未被 README 证实；模型权重另核。 |
| 9 | [Cloudreve](https://github.com/cloudreve/cloudreve#readme) · [元](https://api.github.com/repos/cloudreve/cloudreve) | 多存储文件管理；本地/S3/WebDAV/云盘，上传下载、标签/元数据搜索，图片/音视频预览；Go。 | 09-21；GPL-3.0 | **不引入**：多云文件系统会变成第二存储与权限模型，非当前核心缺口。 |
| 10 | [Chevereto](https://github.com/chevereto/chevereto#readme) · [元](https://api.github.com/repos/chevereto/chevereto) | 图片/视频托管、相册/标签、上传和分享 API；PHP。 | 09-22；AGPL-3.0 或另购商业许可 | **不引入**：分享站而非制作素材索引；与现有项目文件/审片边界重复。 |
| 11 | [Fireshare](https://github.com/fireshare-app/fireshare#readme) · [元](https://api.github.com/repos/fireshare-app/fireshare) | 视频分享；挂载并扫描原目录，海报/元数据、可选转码、标签、唯一链接和密码；JavaScript/Node + FFmpeg。原片直供，低清版本是附加产物。 | 09-26；GPL-3.0 | **P2，只参考设计**：可研究“索引已有目录而不重复上传”和派生预览缓存；不迁分享服务器。 |
| 12 | [PeerTube](https://github.com/Chocobozzz/PeerTube#readme) · [元](https://api.github.com/repos/Chocobozzz/PeerTube) | 联邦视频发布/直播平台；上传、标签、转码、字幕、REST/OpenAPI；TypeScript/Node。 | 09-28；AGPL-3.0 | **不引入**：发布、联邦和 P2P 范围与本地制作素材库无关。 |

### B. 镜头、时间线与生产（原清单 13–21）

| 原序 | 项目与第一方证据 | 已证实的定位、能力与技术栈 | 最近推送；根许可 | 对本项目的处理 |
| --- | --- | --- | --- | --- |
| 13 | [Kitsu](https://github.com/cgwire/kitsu#readme) · [元](https://api.github.com/repos/cgwire/kitsu) | 动画/VFX 的项目、场景、镜头、任务与审核协作；Vue 前端，配套 Zou 后端。不是素材检索引擎。 | 09-28；AGPL-3.0 | **只参考设计**：ADR 0010 和现有 Shot/Review 已定身份，避免第二生产管理系统。 |
| 14 | [AYON Core](https://github.com/ynput/ayon-core#readme) · [元](https://api.github.com/repos/ynput/ayon-core) | 专业 DCC 发布/加载和应用集成管线；Python 插件/API；偏跨创作软件版本交接。 | 09-28；Apache-2.0 | **P2，只参考设计**：仅在以后需要外部 DCC 发布时参考版本适配，不补当前素材浏览/搜索。 |
| 15 | [OpenTimelineIO](https://github.com/AcademySoftwareFoundation/OpenTimelineIO#readme) · [元](https://api.github.com/repos/AcademySoftwareFoundation/OpenTimelineIO) | C++/Python 的剪辑时间线交换 API/格式，保存 Clip/Track/时间范围和媒体引用，**不是媒体库或实际视频文件**。 | 09-26；Apache-2.0 | **P2，只参考设计**：现有 Timeline 已有主模型；仅明确外部交换需求时评估 Adapter。分享页称其适合作“节点画布与镜头列表共用协议”缺少本项目模型依据。 |
| 16 | [OpenAssetIO](https://github.com/OpenAssetIO/OpenAssetIO#readme) · [元](https://api.github.com/repos/OpenAssetIO/OpenAssetIO) | C++/Python 的资产管理互操作接口，以稳定实体身份解析外部管理系统；不是 DAM、索引器或播放器。 | 09-28；Apache-2.0 | **P2，只参考设计**：本地 `assetId` 已存在；多 DCC/多 DAM 接入立项后才评估。 |
| 17 | [Remotion](https://github.com/remotion-dev/remotion#readme) · [元](https://api.github.com/repos/remotion-dev/remotion) | React/TypeScript 程序化视频和批量渲染框架，不负责素材发现；现有 Python FFmpeg 渲染已属本项目。 | 09-28；[特殊双层许可](https://github.com/remotion-dev/remotion/blob/main/LICENSE.md) | **不引入**：第二渲染链及公司规模许可判断均超出素材库目标。 |
| 18 | [Quickcut](https://github.com/jamesqquick/quickcut#readme) · [元](https://api.github.com/repos/jamesqquick/quickcut) | Cloudflare 上的版本化视频审片、带时间点评论、断点上传和 Stream/HLS；Astro/React、Workers/Durable Objects。 | 06-09；**未找到根 LICENSE** | **不引入**：审核/版本已有唯一归属，Cloudflare 托管链不补素材库；没有许可时不能复用源码。 |
| 19 | [Clapper](https://github.com/jbilcke-hf/clapper#readme) · [元](https://api.github.com/repos/jbilcke-hf/clapper) | AI 电影高层故事/角色/场景交互；TypeScript。README 明言早期、按钮/流程可能失效且没有测试。 | 2025-08-01；GPL-3.0 | **不引入**：不是素材检索；维护风险是上游自述与推送快照，不能单凭日期断言失效。 |
| 20 | [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo#readme) · [元](https://api.github.com/repos/harry0703/MoneyPrinterTurbo) | Python/FastAPI/Streamlit 的脚本→库存/生成素材→配音字幕→合成；Pexels/Pixabay/Coverr 搜索与来源记录。 | 09-28；MIT | **迁移后适配，但不重复审**：固定提交[现有深审](./moneyprinterturbo-current-project-integration-audit-2026-09-28.md)已确定窄 `MaterialProvider`/编排规则；外部授权另核。 |
| 21 | [Automated Video Generator](https://github.com/itsPremkumar/Automated-Video-Generator#readme) · [元](https://api.github.com/repos/itsPremkumar/Automated-Video-Generator) | TypeScript/Remotion 的脚本→库存画面→配音→字幕→MP4；README 提到 Pexels、Pixabay、Openverse。未见独立本地 DAM 或片段索引能力。 | 09-11；MIT | **不引入**：与 MPT、现有配音/时间线/渲染重复；供应商素材条款不随 MIT 自动获得。 |

### C. 语义理解与开放素材（原清单 22–30）

| 原序 | 项目与第一方证据 | 已证实的定位、能力与技术栈 | 最近推送；根许可 | 对本项目的处理 |
| --- | --- | --- | --- | --- |
| 22 | [QMedia](https://github.com/QmiAI/Qmedia#readme) · [元](https://api.github.com/repos/QmiAI/Qmedia) | 面向创作者的图片/文本/短视频提取和多模态 RAG 问答；短视频转录、摘要和内容卡片；TypeScript Web + Python/LlamaIndex 模型服务，可本地部署。 | 04-09；MIT | **P2，部分复用候选**：若需要问答式素材探索再核提取/来源卡片；与本地搜索服务重叠，不能先搬 RAG 三服务。 |
| 23 | [RAPTOR](https://github.com/DHT-AI-Studio/RAPTOR#readme) · [元](https://api.github.com/repos/DHT-AI-Studio/RAPTOR) | Python 企业多模态 Agent/RAG 框架，视频检索只是其 27 个 Compose 模块的一部分，另含 MCP/A2A、GraphRAG、个人数据库。 | 09-21；Apache-2.0 | **不引入**：能力边界和运行体量远超本地素材索引，不能让 Agent 运行时成为第二任务系统。 |
| 24 | [Media Search Agent](https://github.com/openara-ai/media-search-agent#readme) · [源码树](https://github.com/openara-ai/media-search-agent/tree/main/src) · [元](https://api.github.com/repos/openara-ai/media-search-agent) | 本地图片/视频；扫描目录、镜头检测和关键帧索引，文本搜到视频时刻，人脸/物体、EXIF/GPS；SQLite 元数据 + 嵌入式 Qdrant，Python/FastAPI + React。 | 08-11；MIT，3 stars 快照 | **P0，部分复用候选**：深审 `assetId + 源指纹 + 片段区间`、索引更新/删除、模型和测试；小项目，README 功能需源码及真实媒体验证。 |
| 25 | [Semantic Search for ComfyUI](https://github.com/EricRollei/Semantic-Search#readme) · [元](https://api.github.com/repos/EricRollei/Semantic-Search) | Python ComfyUI 节点；Qwen3-VL 两阶段图像/视频/文档语义检索，持久索引、FAISS Flat/IVF/HNSW。绑定节点和模型。 | 02-06；**CC BY-NC 4.0／商业另授权** | **不引入**：非商业源码不可默认用于本项目产品；仅可独立理解两阶段检索概念，模型许可另核。 |
| 26 | [clip-retrieval](https://github.com/rom1504/clip-retrieval#readme) · [元](https://api.github.com/repos/rom1504/clip-retrieval) | Python CLIP 图文嵌入、批量索引、FAISS/检索服务，偏大规模图像/文本数据集；不提供视频源区间或项目素材身份。 | 03-28；MIT | **P2，部分复用候选**：只在选定本地嵌入模型与规模后审推理/索引函数；不作为完整素材库底座。 |
| 27 | [Boomerang](https://github.com/iBz-04/boomerang#readme) · [源码树](https://github.com/iBz-04/boomerang/tree/main/crates) · [元](https://api.github.com/repos/iBz-04/boomerang) | Rust 视频窗口嵌入与检索；重叠分段、文本/图片查找、`exact` 时刻细化、`span` 连续区间、重叠去重和异常高光；Qdrant，Gemini/Qwen Cloud/本地后端可选。 | 06-05；MIT，6 stars 快照 | **P0，重新实现**：深审时间区间细化和模型版本隔离算法，与 OpenShorts 候选窗口互补；Rust/Qdrant 不直接成为本项目持久化真相。 |
| 28 | [Video Search System](https://github.com/ColdZero00/video-search-system#readme) · [元](https://api.github.com/repos/ColdZero00/video-search-system) | Python/Streamlit 研究原型；镜头切分、关键帧、CLIP/SigLIP2、FAISS、OCR、SQLite、预览片段与 DRES 提交。 | 07-03；**未找到根 LICENSE**，0 stars 快照 | **只参考设计**：可做片段/图像/OCR 检索行为基准；无源码许可时不复制实现。 |
| 29 | [Semantixel](https://github.com/KarunyaChavan/Semantixel-Semantic_Image_Retrieval#readme) · [元](https://api.github.com/repos/KarunyaChavan/Semantixel-Semantic_Image_Retrieval) | Python 图片语义/相似/OCR 检索，视频抽帧、ChromaDB/BM25、多来源标识和 REST/gRPC；本质是图像索引扩到帧。 | 07-09；MIT，6 stars 快照 | **P2，部分复用候选**：若需图搜图/OCR，再审来源标识和帧索引；视频时段精确度优先由 Boomerang 验证。 |
| 30 | [Openverse](https://github.com/WordPress/openverse#readme) · [API 字段源码](https://github.com/WordPress/openverse/blob/main/api/api/serializers/media_serializers.py) · [元](https://api.github.com/repos/WordPress/openverse) | 聚合第三方**图片和音频**开放许可目录；采集/导入、Django REST API、搜索前端；结果有 `creator`、`foreign_landing_url`、`license`、`license_version`、`license_url`、`attribution` 等字段。**没有库存视频目录**。 | 09-28；根 MIT | **P1，迁移后适配**：评估官方 API 的图片/音频 Provider，导入前保存来源页、作者、许可证版本及取得时快照；每件素材仍需核实上游权利。 |

## 许可与第三方内容的准入门槛

1. **软件许可先于源码复用。** 以上 MIT/Apache 项也须在固定提交核对根、子目录、依赖、模型权重和复制范围；保留要求的版权/许可文本。AGPL/GPL 项（MediaCMS、Immich、PhotoPrism、Cloudreve、Fireshare、PeerTube、Kitsu、Chevereto、Clapper）当前只研究行为和契约，不移植源码。`ResourceSpace` 根 [`license.txt`](https://github.com/resourcespace/resourcespace/blob/master/license.txt) 指向 [`documentation/licenses/resourcespace.txt`](https://github.com/resourcespace/resourcespace/blob/master/documentation/licenses/resourcespace.txt)，后者是含商标限制的自定义宽松条款，不能只按 API 的 `NOASSERTION` 当成无许可。Pimcore 当前 [`LICENSE.md`](https://github.com/pimcore/pimcore/blob/2026.x/LICENSE.md) 为 POCL；Remotion [`LICENSE.md`](https://github.com/remotion-dev/remotion/blob/main/LICENSE.md) 对企业规模/用途分层；ComfyUI Semantic Search [`LICENSE.md`](https://github.com/EricRollei/Semantic-Search/blob/main/LICENSE.md) 要求商业另授权。Quickcut 和 Video Search System 的 GitHub `/license` API 在本次核验返回 404，按**未建立代码复用许可**处理。LibrePhotos 的 MIT 已由[根 LICENSE](https://github.com/LibrePhotos/librephotos/blob/main/LICENSE)核实，不能只照搬分享页断言。
2. **模型与媒体权利独立。** CLIP/SigLIP2/Qwen、自动标签、人脸模型、ComfyUI checkpoint/LoRA、字体和 FFmpeg 构建各有各自条款；README 的“本地”或“MIT”不能替代模型权重许可。MPT/Pexels/Pixabay/Coverr 与 Openverse 的搜索结果都不是商用许可证明。Openverse 官方[条款](https://docs.openverse.org/terms_of_service.html)说明它聚合第三方网站元数据，不验证作品授权；[官网说明](https://openverse.org/uk/about)亦要求用户核对单件作品。应保留实际来源页、作者、许可 URL/版本、查询与取得时间、核实状态，并把导入后的本地 `assetId` 作为唯一生产引用。
3. **链接不是资产。** 外部 API 结果和签名下载 URL 不进入 Timeline、Shot 或交付状态；成功下载并经过格式/权利检查后由 `ProjectAssets` 注册。目录扫描项目须解决“同文件改路径、文件变更、索引删除、已有引用”四种状态，不能复制 Immich 外部库的路径即身份限制。片段索引应绑定稳定资产 ID、源指纹、模型/算法版本及源时间区间；供应商 ID/路径/向量 ID 仅是外部字段。

## 下一轮深审的固定提交问题

| 顺序 | 项目 | 必须读到的源码/测试与决策门槛 |
| --- | --- | --- |
| 1 | SmartGallery | 固定提交审 `smartgallery.py` 的文件发现、ComfyUI PNG/视频 sidecar 元数据解析、SQLite schema、扫描增量、视频缩略图/故事板及误删防护；区分真正去重与按工作流/提示词分组。只有可独立抽出的 MIT 逻辑才谈迁移。 |
| 2 | Media Search Agent | 固定提交审 `src` 的索引、FastAPI 检索、向量/SQLite 映射、删除/重建、视频时间戳和测试；取 2–3 个真实长视频验证中文检索、索引增量、错误定位和硬件成本。若仅 README 承诺而源码不闭环，则降为参考。 |
| 3 | Boomerang | 固定提交审 `crates/video-chunking`、`footage-search`、`boomerang-cli` 的 exact/span、去重和测试；比较 OpenShorts 的片段窗口，证明精确时刻/区间有增量质量；确认本地模型与 Qdrant/云服务依赖后再决定独立重写范围。 |
| 4 | Openverse | 固定提交审 API 响应、许可/来源字段与 API 使用限制；只选图片/音频样本核对来源页许可证及归属。若作品权利无法确认，不导入。 |
| 5 | Immich | 固定提交审外部库扫描、文件哈希/视觉重复项和素材元数据保持策略；只吸收失效/UX 规则，不引入 AGPL 源码。 |
| 6 | UnoPim DAM | 固定提交审自定义版权/来源/作者、标签批量操作和视频缩略图任务，抽取符合本项目字段边界的契约；不用 Laravel/PIM 作为第二后端。 |

**不安排本轮固定提交审计：** MediaCMS/Fireshare 与当前上传、FFmpeg、预览高度重叠且 GPL/AGPL；QMedia/RAPTOR 对本地素材搜索而言引入整套 RAG/Agent 服务；PhotoPrism/LibrePhotos 与 Immich 的库级搜索重叠而少视频片段证据；clip-retrieval、Semantixel、Video Search System 可在模型/图搜图/OCR 子问题确定后再看；Kitsu/AYON/OTIO/OpenAssetIO、Remotion/Quickcut/Clapper/Automated Video Generator 不解决此轮素材库缺口。MPT 已有固定提交深审；[OpenShorts](./openshorts-current-project-integration-audit-2026-09-28.md)也已审，后续仅用其既有结论做对照。分享页额外提到的 [AtroDAM](https://github.com/atrocore/atrodam) 和已归档的 [OpenPype](https://github.com/ynput/OpenPype) 不在 30 项清单中；不为两个已被原候选来源排除的旧平台另开审计，Clapper 已列第 19 项并说明原因。

## 本次未验证

未安装、运行或测量这些上游项目；未下载模型/第三方素材，未进行法律意见、API 调用额度或每件作品的授权核实，未做函数级许可证兼容审查，也未改产品代码或[累计登记表](./reference-project-integration-register.md)。下一轮只有在固定提交与实际样本验证后，才能把本报告的“候选”改为代码复用结论。
