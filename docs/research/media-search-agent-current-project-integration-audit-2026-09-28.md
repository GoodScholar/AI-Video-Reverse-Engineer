# Media Search Agent 固定提交源码深审与当前项目融合判断

审计日期：2026-09-28。上游是 [`openara-ai/media-search-agent@1ea46df10b00e350c00a1cf344267a979a183db6`](https://github.com/openara-ai/media-search-agent/tree/1ea46df10b00e350c00a1cf344267a979a183db6)（提交时间 2026-08-11；镜像提交说明 `release: mirror from kumraj/media-search-agent@235d01f`），不是浮动 `main`。第一方 [README](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/README.md)自称 pre-1.0 实验软件、不用于生产；GitHub API 在审计日显示非归档、3 stars、最近推送 2026-08-11，这只是维护快照。本文逐段读 `src/` 中索引、存储、查询、API 与 React 页面，以及配置、测试、许可；**没有安装约 1.5 GB 模型、运行服务或真实长视频质量/硬件实验**。以下“可用”限于源码可证能力，不是模型效果保证。

## 结论先行

**总体标签：部分复用。** 上游确实有视频 `shots(t_start,t_end)`、关键帧时间点、CLIP 向量、SQLite 正本/Qdrant 派生索引、增量指纹、两次完整扫描后墓碑删除和页面跳到命中时刻，最适合补当前项目“本地视频视觉语义定位”的缺口；它不负责 OpenShorts 的内容价值/高光打分，也不负责 MPT 外部图库来源，SmartGallery 则补 ComfyUI 产物元数据。[镜头与关键帧 schema](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/db/schema.sql#L127-L163)、[查询主链](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_query/query_engine/engine.py#L619-L736)。

**不能宣称已提供本项目所需的 `assetId + sourceFingerprint + [start,end]` 搜索结果契约。** 上游 `media_id` 是文件 SHA-256，快路径按 `source_name + rel_path + size + mtime_ns` 复用，Qdrant 点 ID 又由哈希前缀与 8 位镜头/关键帧序号构造；它从不知晓本项目 `assetId`。[主键/指纹表](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/db/schema.sql#L2-L29)、[快路径](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/pipeline.py#L934-L1039)、[点 ID](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/db/qdrant_export.py#L859-L863)。更重要的是检索器把各关键帧的 `id` 归一为同一 `media_id`，合并器再按 `id` 去重，所以同一视频的多个命中会在 5 秒时域去重**之前**折叠；`SearchItem` 未声明 `shot_start/shot_end`，虽然 SQLite 和 Qdrant payload 均有区间，`/search` 响应会舍弃它们。`/videos/{media_id}/shots` 是另一个可取全量区间的端点。[检索归一](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_query/storage/qdrant_client.py#L246-L290)、[合并/时域去重](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_query/query_engine/engine.py#L433-L533)、[响应 schema](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_apps/search_api/schemas.py#L17-L39)、[shots API](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_apps/search_api/app.py#L915-L1004)。这使“多片段候选”必须重做结果身份和 API，而非直接嵌入现有时间线。

# 综合融合决策（25 项）

## 1. 参考项目核心能力

本地只读扫描图片/视频；PySceneDetect 分镜、每镜头默认一个关键帧（可设 1–3）、CLIP 图文向量、RT-DETR 物体标签、FaceNet 人脸检测/相似脸及用户标签、EXIF 与 GoPro GPMF GPS、React 网格搜索/浏览/人物/索引/设置页。索引数据在 SQLite，Qdrant 嵌入式用于查询。[架构](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/docs/ARCHITECTURE.md)、[页面路由](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_apps/ui/src/App.tsx#L16-L35)。视频文档明确**音频、ASR 和字幕尚未索引**，因此“说了什么”不是它当前的检索能力。[视频指南](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/docs/features/video.md)。

## 2. 当前项目核心能力

[`ProjectAssets`](../../backend/app/project_assets.py)以稳定 `assetId` 管理内容项目图片/视频/音频，受控安装、读取、引用检查和带回滚删除；参考镜头段、制作镜头、Timeline、短视频变体与审核/交付有独立身份。[`CONTEXT.md`](../../CONTEXT.md)、[ADR 0009](../adr/0009-content-project-and-workflow-boundaries.md)。[`timeline_render.py`](../../backend/app/timeline_render.py)是 FFmpeg 成片合成路径，[`durable_runs.py`](../../backend/app/durable_runs.py)负责冻结输入、运行与恢复。当前缺本地**大量视频可检索的视觉片段索引**，但已有基础媒体探测、参考视频分镜/分析和 OpenShorts 候选发现方向，不能平行创建第二资产、第二渲染或第二审核链。[累计登记表](./reference-project-integration-register.md)。

## 3. 两个项目定位差异

Media Search Agent 是外部目录的个人图片/视频检索桌面应用，素材文件仍在原地；当前项目是内容项目内的制作与交付系统。上游 `media_id` 是内容哈希且重复文件合并为一个索引媒体，路径复制/移动用 `file_fingerprint` 维护；本项目同字节素材也可能是不同项目的独立 `assetId` 和权利/引用上下文，不能按内容哈希合并业务身份。[上游双层身份](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/db/schema.sql#L190-L207)、[本地资产 ID](../../backend/app/project_assets.py)。

## 4. 功能模块对比

| 能力 | 上游实际边界 | 当前项目落点 | 标签 |
| --- | --- | --- | --- |
| 目录扫描/去重 | `os.scandir`、后缀白名单、SHA-256 媒体 ID、路径指纹快路径；同内容路径合并[源码](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/io/scanner.py#L8-L121) | `ProjectAssets` 仍唯一持有资产；索引只读消费受控文件 | 迁移后适配 |
| 镜头/关键帧 | PySceneDetect 边界，OpenCV 按时间戳抽帧；失败时全视频合成镜头[源码](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/pipeline.py#L1383-L1409) | 片段索引挂 `assetId`、源指纹和算法版本；OpenShorts 候选可消费 | 部分复用 |
| 视觉语义检索 | CLIP → Qdrant 图片/关键帧，物体/人物/时间/地点过滤，启发式排序 | 片段候选服务，不写 Timeline 决策 | 迁移后适配 |
| 人脸/物体/GPS | FaceNet/RT-DETR、EXIF/GPMF，可标人物并检索 | 可选辅助过滤；须授权、隐私和模型许可 | 只参考设计 |
| 缩略图/预览 | 派生 JPEG、原视频 Range/浏览器 seek | 现有素材 URL、未来派生预览；Timeline 不变 | 部分复用 |
| 桌面/API/运行 | React/Vite、FastAPI、Tauri sidecar、索引子进程、嵌入 Qdrant 锁交接 | 当前 FastAPI/React + `durable_runs` | 不引入第二应用/运行器 |

## 5. 业务逻辑对比

上游每次扫描按 `(source_name, rel_path)` 找指纹，`size_bytes + mtime_ns` 相等即免哈希；不同则全文件 SHA-256，按内容查旧记录来判断复制/移动/原位替换。[快路径](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/pipeline.py#L934-L1055)。这是**速度优化不是强内容校验**：同尺寸且 mtime_ns 被保留的内容变化需 `verify_content` 才能发现。完整且无错误的非过滤扫描中，第一次缺失只记 grace，第二次移除最后一个指纹才 `tombstone_media`；停止/目录错误/过滤扫描不做删除清扫。[扫描保护](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/pipeline.py#L1754-L1835)。这只是**索引失效**，不等于本项目可删除仍被镜头/Timeline 引用的源资产。[本地删除边界](../../backend/app/project_assets.py#L212)。

## 6. UI / UX 对比

上游 `SearchPage` 有查询、分数阈值、筛选和结果网格；`BrowsePage` 浏览库，`IndexerPage` 设置来源和运行/日志，`PeoplePage` 标注身份；详情抽屉对视频只按 `timestamp` seek，不呈现可选择的 `[shot_start,shot_end]` 裁剪区间。[SearchPage](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_apps/ui/src/pages/SearchPage.tsx#L21-L130)、[详情 seek](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_apps/ui/src/components/media/MediaDetailDrawer.tsx#L79-L115)。本项目只宜借鉴“搜索时刻→预览→显示证据→人工选区→绑定既有素材/制作镜头”的交互，不迁入独立索引页和人物库。新增 UI 需按 [`design.md`](../../design.md)实现。

## 7. 技术架构对比

上游 Python 3.11+、FastAPI/Uvicorn、React/Vite、Tauri；索引独立 CLI 子进程，SQLite WAL 持正本，Qdrant 嵌入式索引只能单进程持锁，通过 API/indexer sentinel 交接，导出期间搜索短暂不可用。[依赖](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/requirements.txt)、[共享客户端](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_query/storage/qdrant_client.py#L9-L39)、[索引管理器](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_apps/search_api/indexer_manager.py#L185-L230)。本项目已运行 FastAPI/React 且由 `durable_runs` 管运行：可借 SQLite 正本/派生索引概念，但不复制 Tauri sidecar、PID 文件、全局 Qdrant 锁协议或另立运行状态。

## 8. 数据模型对比

上游 `media(media_id=SHA256(file), path/source_name/rel_path, deleted, model_version...)`、`file_fingerprint`、`shots(video_id,shot_index,t_start,t_end)`、`video_keyframes(video_id,shot_index,kf_index,timestamp,shot_start,shot_end,tags/GPS)`、三种 embedding BLOB、face/person/tag、`index_state`。[schema](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/db/schema.sql#L2-L207)。本项目应以 `(projectId, assetId, sourceFingerprint, modelVersion, segmentationVersion, shotIndex/keyframeIndex)` 定位派生记录，`media_id`/Qdrant point ID 仅内部查找。上游 `shots` 存区间，但 API 搜索结果没有区间字段，是当前适配必须修补的契约缺口。[搜索 schema](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_apps/search_api/schemas.py#L17-L39)。

## 9. 当前项目已有优势

当前 `assetId` 稳定且按项目区分，同字节不同授权/用途不会合并；安装与删除有原文件边界、引用阻止和回滚，版本化成片与审核不被索引重建改变。[`ProjectAssets`](../../backend/app/project_assets.py#L103-L239)、[`durable_runs`](../../backend/app/durable_runs.py)。OpenShorts 已明确“参考片段窗口→高光候选→用户选择”，此仓只能做视觉证据与定位，不替代价值判断。[OpenShorts 深审](./openshorts-current-project-integration-audit-2026-09-28.md)。

## 10. 参考项目优势

真正互补点是独立关键帧的本地图文向量检索，以及路径指纹/无错扫描 grace、SQLite 向量正本与可重建 Qdrant 的恢复设计。[SQLite 正本 ADR](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/docs/decisions/ADR-010-sqlite-canonical-embedding-store.md)、[增量导出/删点](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/db/qdrant_export.py#L604-L710)。SmartGallery 的 Workflow/LoRA 结构信息和 MPT 的远端图库来源无法提供这一视觉检索；OpenShorts 的高光模型也不承担全库 ANN 召回。[SmartGallery 深审](./smartgallery-current-project-integration-audit-2026-09-28.md)、[MPT 深审](./moneyprinterturbo-current-project-integration-audit-2026-09-28.md)。

## 11. 功能映射表

| 上游固定 SHA 证据 | 当前唯一落点 | 与既定链路关系 | 处理标签 |
| --- | --- | --- | --- |
| [`scanner.py` + 指纹快路径](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/io/scanner.py#L38-L146) | 受控素材文件的派生索引更新；`ProjectAssets` 仍所有者 | 不替换 MPT Provider 导入 | 迁移后适配 |
| [`shot_detection.py` + `extract_keyframes_from_shot`](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/io/shot_detection.py#L32-L70) | `SourceMediaResolver` 后的视觉证据 | OpenShorts 高光窗口的召回辅助 | 部分复用 |
| [`schema.sql` 的 shots/keyframes/embeddings](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/db/schema.sql#L93-L163) | `assetId` 关联的可重建片段索引 | 不成为 Shot/Timeline 主键 | 迁移后适配 |
| [`qdrant_export.py`](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/db/qdrant_export.py#L882-L1024) + [`engine.py`](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_query/query_engine/engine.py#L619-L736) | 片段检索派生索引和候选 API | 候选交给 OpenShorts 排序/人工选择 | 迁移后适配 |
| [`EXIF/GPMF`、人脸与物体](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/io/video.py#L55-L210) | 可选筛选字段，不默认存人脸身份 | SmartGallery 管 Workflow，不做人脸库 | 只参考设计 |
| [`IndexManager`/Tauri/独立页面](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_apps/search_api/indexer_manager.py#L148-L230) | 不进入产品 | 现有 `durable_runs`/FastAPI/React 接管 | 不引入 |

## 12. 可以直接复用的代码

**无。** 即使根仓为 MIT，关键能力依赖模型权重、CPU/GPU 资源、目录路径/Qdrant 身份和上游 API 契约；复制一个函数并不能提供 `assetId + 指纹 + 区间`。尤其不能原样复用 `_compound_point_id`：只取 SHA-256 字符串前六字节，再把 `shot_index`/`kf_index` 截成各 8 位，超过 255 或前缀相同存在点 ID 碰撞风险。[ID 生成](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/db/qdrant_export.py#L859-L863)。

## 13. 可以迁移后适配的代码

下列**拟建目标文件**本次均未写入。任何实质源码迁移先保留根 [MIT 版权和许可](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/LICENSE)，再核查依赖/模型权重及测试素材条款；不能把仓库根 MIT 推及所有权重、样本或 ExifTool。

| 上游固定 SHA 文件/范围 | 当前拟建目标、必要修改 | 依赖/影响/许可门槛与测试 |
| --- | --- | --- |
| [`src/msa_indexer/io/scanner.py#L38-L146`](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/io/scanner.py#L38-L146)、[`pipeline.py#L934-L1055`](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/pipeline.py#L934-L1055) | 拟建 `backend/app/asset_segment_index.py`：从 `ProjectAssets.file(projectId, assetId)` 枚举，源内容指纹+mtime/size 作校验与跳过，不扫描任意用户目录；不合并不同 `assetId`；写索引失败不改业务资产。 | 标准库/Python 文件扫描逻辑可考虑迁移，根 MIT；测试同字节不同资产、改名/重导入、同 size+mtime 变更、符号链接、离线/中断与受引用删除。 |
| [`src/msa_indexer/db/schema.sql#L93-L207`](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/db/schema.sql#L93-L207)、[`sqlite_store.py#L1551-L1655`](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/db/sqlite_store.py#L1551-L1655) | 拟建 `backend/app/asset_segment_store.py`：主键改为项目/`assetId`/源指纹/算法版本，保留 `[start,end]` 与证据；SQLite 为派生数据正本，模型版本升级只重建派生表，删除由 `ProjectAssets` 事件触发失效。 | SQLite 标准库、根 MIT；影响索引存储/迁移，不影响 `PreproductionStore` 业务真相。测试旧版本失效、跨项目隔离、空/错镜头、事务失败/重建、引用与版本回放。 |
| [`src/msa_query/query_engine/engine.py#L433-L736`](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_query/query_engine/engine.py#L433-L736)、[`storage/qdrant_client.py#L226-L290`](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_query/storage/qdrant_client.py#L226-L290)、[`search_api/schemas.py#L17-L39`](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_apps/search_api/schemas.py#L17-L39) | 拟建 `backend/app/asset_segment_search.py`：候选键用 `(assetId, sourceFingerprint, shotIndex, keyframeIndex)`，先保留多个片段，后按源区间重叠/时间窗去重；响应明确返回 `start/end/seek/modelVersion/evidence`，不可按媒体 ID 提前折叠。 | CLIP/OpenCLIP、Qdrant/PyTorch 与权重许可先审；大量模型/内存开销。测试一视频多命中、同镜头多帧、跨项目同内容、ID 碰撞、payload/响应区间一致、中文查询质量、旧索引不可读恢复。 |

## 14. 可以部分复用的代码

[`shot_detection.py`](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/io/shot_detection.py#L32-L70) 是 PySceneDetect `ContentDetector` 的薄包装；[`video.py#L503-L576`](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/io/video.py#L503-L576) 用 OpenCV seek 到 `t*fps`，失败还可能回退**整片中间帧**，因此只能吸收“分镜后每镜头有限抽帧”规则。拟建 `backend/app/asset_segment_extractor.py` 须以现有媒体探测/FFmpeg 时间基准验真 VFR、HDR、首尾帧、短镜头和解码失败；不保证采样帧等于真实边界。若复制上游实际片段需 MIT 声明、PySceneDetect/OpenCV/FFmpeg 构建逐项许可；测试应比较抽帧时间点和图像与源区间，真实长视频性能另测。缩略图 [`io/thumbnails.py`](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/io/thumbnails.py#L5-L58)只参考内容哈希命名与 EXIF 方向修正；当前派生缓存仍必须 `assetId + 源指纹 + 参数版本`，避免同字节资产共享权利上下文。

## 15. 只建议参考设计的功能

人物过滤、物体标签、EXIF/GPS（含 GoPro 每关键帧坐标）、分数详情、结果跳到命中时刻可作为产品需求/测试用例；默认不建生物识别人物库，也不把位置作为公开默认字段。[人脸链](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/pipeline.py#L1464-L1497)、[GPS 提取/插值](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/io/video.py#L90-L210)。语义分数是相似度而非概率，[上游搜索指南](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/docs/features/search.md)也提醒 20–35% 常见，不能以固定 80% 阈值承诺正确性。`SearchPage` 的视频卡片虽以 `(id,shot_id,timestamp)` 作 React key，但后端先按 `id` 合并，不能据此推断页面真的展示多片段。[页面](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_apps/ui/src/pages/SearchPage.tsx#L112-L123)。

## 16. 不建议引入的功能

不引入 Tauri 第二桌面壳、全局个人媒体来源管理、独立 `/indexer/start` 子进程/PID/锁交接、未授权局域网 API、`media_id`/Qdrant point 作为业务 ID、人脸标签直接进入审核或 Timeline。上游 FAQ 明示 `--bind-host 0.0.0.0` 供 LAN 用时**API 无鉴权**；`/videos/{media_id}` 对 SQLite 中未删除路径开放服务且当前来源根限制已撤销，不能复制进本项目访问边界。[FAQ](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/docs/FAQ.md)、[视频端点](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_apps/search_api/app.py#L771-L813)。上游可选 InsightFace 权重为**非商业使用**，与本项目潜在客户商业交付不兼容，除非独立取得授权。[NOTICE](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/NOTICE)。

## 17. 当前项目缺失能力

缺少对大量已保存项目视频的视觉语义召回、可检索关键帧与来源时间区间、派生索引版本/删除失效/重建。OpenShorts 已覆盖“候选窗口价值判断与剪辑建议”，但不承担全资产视觉向量库；SmartGallery 补 ComfyUI 嵌入元数据，MPT Provider 补外部素材来源。此仓的优势恰好在四者之间，但当前代码还不能直接产生可被 `Shot`/`Timeline` 安全引用的结果。[能力分工登记](./reference-project-integration-register.md#L69-L71)。

## 18. 可以删除或替换的当前实现

**无。** 不替换 `ProjectAssets`、已有参考视频分镜、Timeline FFmpeg、`durable_runs` 或 OpenShorts 候选计划。新增索引仅增加召回信号；若模型/索引不可用，既有项目资产、手选片段和审核/交付继续工作。[本地源资产](../../backend/app/project_assets.py)、[本地渲染](../../backend/app/timeline_render.py)。

## 19. 融合后的模块架构

```text
ProjectAssets（唯一 assetId/源文件/引用/删除所有者）
  └─ SourceMediaResolver（只读受控文件 + 内容源指纹）
       └─ AssetSegmentIndexer（可重建；分镜、关键帧、模型版本）
            ├─ SQLite 派生记录/向量正本：assetId + 指纹 + [start,end]
            └─ 可选 ANN 派生索引：内部 point ID；丢失可重建
                 └─ AssetSegmentSearch → 可解释候选窗口
                      └─ 既定 HighlightSuggestionService / 人工选择 → 既有 Shot/Timeline
```

SmartGallery 的 ComfyUI 字段可附着在同一 `assetId` 但不是片段向量身份；MPT Provider 的远端结果必须先合法导入 `ProjectAssets` 才索引。索引运行走当前长时运行规则；模型结果不得直接改业务审核或渲染。[当前职责登记](./reference-project-integration-register.md#L55-L71)。

## 20. 融合后的用户流程

用户将来源明确、权利可用的视频安装为项目素材 → 取得稳定 `assetId` → 可选后台分镜/抽帧/向量索引并记录模型/源指纹 → 文本/物体过滤返回 `assetId + [start,end] + seek + 证据/分数` → 用户预览并裁定 → OpenShorts 式价值候选与当前制作镜头/Timeline 接管。源文件被替换、索引模型变更或资产删除时，仅旧候选失效/不可选择，历史版本化预览和审核留存，不静默把新向量命中套进已批准内容。

## 21. 对当前代码目录的影响

若批准实施，拟新增 `backend/app/asset_segment_index.py`、`asset_segment_store.py`、`asset_segment_extractor.py`、`asset_segment_search.py` 及对应 tests；由 [`preproduction_api.py`](../../backend/app/preproduction_api.py)或当前项目路由按 `projectId/assetId` 暴露受控查询。前端在既有 [`AssetManager.tsx`](../../frontend/src/AssetManager.tsx)与素材/镜头选择流扩展检索、时域候选和证据；不复制上游 `src/msa_apps/ui` 全套 SPA、`src-tauri`、CLI 或整库 API。实现时先核 [`design.md`](../../design.md)。

## 22. 数据模型调整

建议派生 `AssetSegmentIndex(assetId, projectId, sourceFingerprint, segmentationVersion, embeddingModelVersion, indexedAt, status)`；`AssetSegment(shotIndex, startSeconds, endSeconds, synthetic, keyframes[{timeSeconds, tags, vectorRef}], evidence)`。`start<end`、区间不越过探测时长、seek 落区间、源指纹与当前资产一致；业务记录只引用 `assetId` 与确认过的源区间，向量 ID 可重建且永不外露为持久 ID。同文件跨项目可共享昂贵计算缓存，但权利/可见性与结果身份必须按项目分离。上游 `sha256` 可作源指纹候选，不能作为项目资产 ID。[当前 ID 生成](../../backend/app/project_assets.py#L40-L45)。

## 23. Store 调整

React/Zustand 只保存查询、过滤、选中候选、索引状态；不要把 Qdrant 命中或本机绝对路径缓存成持久业务对象。后端 SQLite 派生状态与 `durable_runs` 的运行输入快照分开，索引重建可重试/恢复；Qdrant 只作可丢弃 ANN 缓存。上游有 `index_version_seq`、`updated_seq/deleted_seq` 和导出水位确保失败后重试，但这是实现参考，不等于可直接替换当前运行恢复。[dirty/watermark](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/db/schema.sql#L81-L177)、[导出成功才记版本](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/pipeline.py#L2168-L2189)。

## 24. API 调整

若实施，现有 FastAPI 应提供项目范围内 `index/status`、受控重建和带分页/上限的 `segments/search`；请求只接受已保存 `assetId`，响应显式 `projectId, assetId, sourceFingerprint, startSeconds, endSeconds, seekSeconds, score, evidence, algorithmVersion`。结果必须二次核对资产存在/源指纹/权限，不能复用上游以 `media_id` 直接读任意已索引绝对路径的端点。[上游路径服务](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_apps/search_api/app.py#L771-L813)。人脸、GPS、模型下载与外网/局域网访问各需单独授权，不作为默认索引参数。

## 25. P0 / P1 / P2 实施计划与验收

| 优先级 | 最小切片 | 必须通过的证据 |
| --- | --- | --- |
| P0 | 先把“多关键帧命中→多个区间候选”的契约和 `assetId` 适配做成无模型假数据测试 | 一视频不同镜头不被 `media_id` 合并；`shot_start/end` 穿透 API；源指纹变化、跨项目同字节与受引用删除不串档。当前上游**未满足**前两项。 |
| P1 | 选许可合适的 CLIP/向量后端做 2–3 个真实长视频离线实验，测中文/英文召回、误定位、VFR、CPU/MPS/CUDA 成本与重建 | 召回/区间定位标注集、首尾误差、索引吞吐/内存、索引中断/恢复、Qdrant 删除/重建、无模型时回退；未实测前不承诺效果。 |
| P2 | 确有需求后才加 RT-DETR 物体、EXIF/GPS/可选人脸 | 每个依赖/权重/媒体单独 License 与隐私评估，显示敏感信息的权限与删除验证；InsightFace 非商业权重不得默认进入客户交付。 |

## 源码核验补充与许可边界

- **视频能力的真实级别：** `detect_shots` 调 PySceneDetect；`extract_keyframes_from_shot` 默认取中点，按 OpenCV `fps*t` seek，若失败可回退全视频代表帧。上游视频文档说 H.264/H.265/AV1 通过 ffmpeg 解码，但这条实际抽帧路径是 OpenCV `VideoCapture`，格式和时间精度取决于本机 OpenCV 构建，须以媒体实验而非文档承诺验收。[检测](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/io/shot_detection.py#L32-L70)、[抽帧](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/io/video.py#L503-L576)。
- **多命中缺陷的证据级别：** 这是固定提交源码静态推导，不是已运行复现：`QdrantStore.search` 给每个视频关键帧 `id=media_id`，`_merge_hits_with_source_scores` 以该 `id` 为 map 键，`temporal_deduplicate_videos` 排在其后。现有[时域去重测试](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/tests/test_query_engine.py#L633-L646)直接喂不同 `id` 的模拟结果，未覆盖实际检索归一后的同视频多镜头路径。需在实施前补端到端回归。
- **删除/重建：** 上游 soft-delete、Qdrant 点删除和版本水位有专门[测试](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/tests/test_delta_export.py)；Qdrant collection 丢失可由 SQLite 向量重建。[导出函数](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_indexer/db/qdrant_export.py#L882-L1024)。不过上游墓碑仅是检索层，不会保护本项目的 Shot/Timeline 引用。
- **许可：** 根 [`LICENSE`](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/LICENSE) 是 MIT，版权 `2024–2026 Kumar Rajeev`；提交树未见子目录 LICENSE。[`NOTICE`](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/NOTICE)把 OpenCLIP、PyTorch、Qdrant、ExifTool、MediaInfo、FaceNet 和可选 InsightFace 分开说明；默认 RT-DETR [`PekingU/rtdetr_r18vd` 模型卡](https://huggingface.co/PekingU/rtdetr_r18vd)标 Apache-2.0。OpenAI CLIP 权重、facenet VGGFace2 权重的实际下载产物及再分发条件必须再独立核验，不能只依赖上游 NOTICE；InsightFace 预训练权重上游 NOTICE 已明示非商业。仓库 `tests/real_media/` 带 CC BY、CC0、公有领域混合样本和人像肖像权提示，不能因根 MIT 复制到本项目。[样本授权清单](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/tests/real_media/THIRD_PARTY_MEDIA.md)。
- **测试与性能证据：** 仓库有 Python 单元/集成、真实媒体脚本和 React Vitest/Playwright 文件；这证明有测试资产，不证明本次环境已通过。[tests 目录](https://github.com/openara-ai/media-search-agent/tree/1ea46df10b00e350c00a1cf344267a979a183db6/tests)、[前端测试配置](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/src/msa_apps/ui/vitest.config.ts)。README 的“快”与文档给的性能数不是当前硬件基准；默认 `ViT-L-14/openai`、RT-DETR GPU 自动门控、FaceNet 与首次约 1.5 GB 权重下载会改变安装与隐私体验。[配置](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/config.yaml#L25-L76)、[首次下载 FAQ](https://github.com/openara-ai/media-search-agent/blob/1ea46df10b00e350c00a1cf344267a979a183db6/docs/FAQ.md)。

## 本次验证范围

已核对固定 SHA、上游路径与行号、仓库树许可/测试文件；只读检查当前 `ProjectAssets`、`timeline_render`、`durable_runs` 与既有 OpenShorts/SmartGallery/MPT 决策。未安装依赖、下载模型、运行上游 Python/前端测试、启动 API/Tauri、运行真实长视频或验证检索质量/硬件性能；报告中的数据模型、目标路径和 P0–P2 均为**建议而非已实现**，本次未改任何产品代码或累计登记表。
