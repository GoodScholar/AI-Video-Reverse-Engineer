# SmartGallery DAM 固定提交源码深审与本项目融合判断

审计日期：2026-09-28。上游固定提交 [`3cbcf83883b05f4af46723f3322d044c2ea972d3`](https://github.com/biagiomaf/smart-comfyui-gallery/tree/3cbcf83883b05f4af46723f3322d044c2ea972d3)（源码标记 v2.24.1）。范围：上游 README、Python 主文件、Jinja 模板、依赖与根许可，对照本项目实际代码和既有固定提交审计；未运行上游服务、ComfyUI、媒体样本或模型。下文上游链接都固定到该提交，推断与已验证事实分开。

## 结论先行

SmartGallery 真正独有的是**为 ComfyUI 产物建库**：从图像/视频嵌入数据提取工作流，索引模型/LoRA/提示词，按工作流结构或提示词分组，再以画廊、详情、缩略图、故事板和虚拟集合浏览。[OmniQuery 官方说明](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/docs/OmniQuery.md)的“AI 查询”是让用户将生成的提示词复制给外部 LLM，再把所得 SQL 贴回 OmniQuery 执行；独立的嵌入式 AI 语义搜索在[源码配置说明](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L299-L325)中明确标为**尚未发布、默认关闭**。不能据 AI 搜索界面和预建表推断已具备本地视频语义选片。

**总体处理：迁移后适配少量 ComfyUI 元数据解析与分组语义；预览/扫描只部分复用行为；单文件 Flask/SQLite 应用、路径型素材身份、ComfyUI 队列、独立审核状态、AI 索引队列均不引入。** 本次没有“直接复用”项。MIT 许可让源码迁移有法律起点，技术契约仍须函数级审计、测试与版权声明。页面、路由、数据库、模板与 ComfyUI 远端操作集中在一份约 11,450 行的 [`smartgallery.py`](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py)和大型 Jinja 模板，整体搬入会产生本项目禁止的第二后端、素材身份、任务和审核系统。

# 综合融合决策（25 项）

## 1. 参考项目核心能力

索引图片、动画、视频、音频和文本；支持物理目录、挂载目录、虚拟集合、状态旗标、评分/评论、提示词/模型/LoRA 检索、缩略图与视频故事板、ComfyUI Workflow 查看/Remix/队列，以及供客户查看的 Exhibition 模式。[文件扫描](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L2557-L2740)、[页面入口](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/templates/index.html#L542-L822)、[数据库表](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L2031-L2219)。这些“Approved/Review”仅是上游库级标签，不能等同本项目的版本化审核决定。

## 2. 当前项目核心能力

[`ProjectAssets`](../../backend/app/project_assets.py) 按内容项目安装图片/视频/音频，稳定 `assetId`、基础媒体元数据、原文件读取、引用检查和带回滚的删除；[`AssetManager`](../../frontend/src/AssetManager.tsx)维护名称/备注与引用位置。镜头、时间线、运行、预览、审核和交付已有独立身份；[`timeline_render.py`](../../backend/app/timeline_render.py)负责 FFmpeg 合成， [`durable_runs.py`](../../backend/app/durable_runs.py)负责冻结输入和运行恢复。当前缺少跨项目的库级浏览、标签/筛选、ComfyUI 产物元数据索引、稳定派生预览及片段语义搜索；后者已在[累计登记表](./reference-project-integration-register.md)分配给本地片段发现链。

## 3. 两个项目定位差异

SmartGallery 是面向 ComfyUI 输出目录的**独立媒体文件管理器**，用户可原地移动、重命名、删文件并控制 ComfyUI；本项目是按内容项目保存素材、制作镜头、变体和审核交付的本地制作系统。前者的路径/文件是中心，后者的资产/版本/引用是中心。[上游配置](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L149-L248)、[本地 ADR 0009](../adr/0009-content-project-and-workflow-boundaries.md)。

## 4. 功能模块对比

| 能力 | SmartGallery 实际实现 | 当前本项目 | 差额与标签 |
| --- | --- | --- | --- |
| 素材身份/导入 | 目录扫描或上传；`MD5(path)` 文件 ID，[处理函数](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L1961-L2016) | `projectId + assetId`，安装/引用/删除 | 路径 ID **不引入**；未来目录导入须落地为现有资产。 |
| 媒体元数据 | 类型、尺寸、时长、mtime、大小、Workflow 文件/提示词/哈希，[schema](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L2038-L2058) | 基础类型/尺寸/时长、名称/备注 | ComfyUI 部分**迁移后适配**。 |
| 检索 | 文件名/日期/扩展名/提示词/模型/评论/集合，OmniQuery SQL；语义 AI 未发布 | 资产列表与元数据关键词混剪建议 | 库级过滤**重新实现**；AI 视频片段检索用既定 OpenShorts 后续链。 |
| 预览 | 图片/视频缩略图、11 帧故事板、即时 FFmpeg 转码、音频波形开关 | 原文件 URL，FFmpeg 渲染与媒体探测 | 派生预览**部分复用行为**，不复制第二合成器。 |
| 标签/集合 | 多对多集合、状态旗标、评分/评论 | 镜头采用、版本审核；素材名称/备注 | 普通筛选标签**重新实现**；上游审核旗标**不引入**。 |
| ComfyUI 控制 | Workflow Remix、代理、Queue Deck、GPU 信息 | 最终生成在外部，当前只准备 Workflow 候选 | **不引入**；遵守 [ADR 0007](../adr/0007-preproduction-with-shot-steps.md)。 |

## 5. 业务逻辑对比

上游扫描磁盘、以 `path` 和 mtime 为一致性依据，文件不存在即删索引；重命名计算新 ID，评分/集合依赖该 ID 更新或级联。[全量同步](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L2557-L2738)、[重命名](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L5957-L6066)。本项目任何素材被镜头/时间线/运行引用时须阻止删除并保持历史可追溯；不能将目录同步的“消失”传播为资产删除。[本地引用索引](../../backend/app/asset_references.py)、[项目素材删除](../../backend/app/project_assets.py)。

## 6. UI / UX 对比

上游 [`index.html`](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/templates/index.html#L542-L822) 是服务端 Jinja + 内联 JS，侧栏分物理文件夹/虚拟集合/状态，主栏网格、筛选、Focus/对照、详情抽屉；[`list_view.html`](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/templates/list_view.html)补高密度排序列表。当前 React/Zustand 工作台可借鉴“网格与表格共用结果、来源与 Workflow 徽标、批量标签、预览抽屉”，但视觉/交互需按本项目 [`design.md`](../../design.md)独立设计；模板代码不能嵌进现有 React。

## 7. 技术架构对比

上游单个 Python 文件负责 Flask/Waitress 路由、SQLite WAL、扫描、Process/ThreadPool、FFmpeg/OpenCV/Pillow、用户会话和模板渲染；浏览器 Auto-Watch 用 `EventSource` 发 GET 触发服务端目录同步。[连接与表](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L2018-L2029)、[前端定时器](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/templates/index.html#L3329-L3505)、[GET 同步端点](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L3541-L3554)。当前 FastAPI/React、`durable_runs` 与本地文件所有权已有清楚边界；只抽取纯元数据规则，不新建 Flask 服务或进程内 `rescan_jobs`。

## 8. 数据模型对比

上游 `files(id=MD5(path), path UNIQUE, mtime, type, duration TEXT, dimensions TEXT, workflow_files/prompt/hash, ai_*)`，`collections`/`collection_files`、评分、评论、用户、OmniQuery 会话和 AI 队列一并存在同一 SQLite。[schema](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L2038-L2219)。当前 `assetId`、项目 JSON、Shot、BatchVariant、Preview/Review/Delivery 版本已独立；只新增附属于 `assetId` 的索引/派生信息。上游 `duration`/`dimensions` 字符串、绝对路径主键及 Approved 集合均不可作为当前规范模型。

## 9. 当前项目已有优势

素材 ID 不随名称/路径变化，文件安装和元数据保存具回滚，服务端核查引用后删除，生产运行以输入快照和版本失效规则回溯。[`ProjectAssets`](../../backend/app/project_assets.py)、[`durable_runs`](../../backend/app/durable_runs.py)、[累计登记表](./reference-project-integration-register.md)。上游库级收藏和审核标签不能覆盖这些领域保证。

## 10. 参考项目优势

ComfyUI PNG/视频内嵌 Workflow、API/UI 格式、模型和 LoRA 提取确有源码；Workflow 结构哈希、提示词哈希可帮助创作者**分组相似生产配置**，但不是文件或视觉去重。目录外部变化检测、浏览预览、故事板和高密度筛选提供素材规模增长时的 UX 参考。[提取](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L1295-L1377)、[哈希](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L10787-L10918)。

## 11. 功能映射表

| 上游能力/证据 | 本项目唯一落点 | 与 MPT / OpenShorts 关系 | 处理标签 |
| --- | --- | --- | --- |
| [`extract_workflow`、`ComfyMetadataParser`](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L933-L1377) | `ProjectAssets` 的派生 ComfyUI 元数据 | MPT 只记录素材来源；OpenShorts 面向长视频片段 | 迁移后适配 |
| [`workflow_files/prompt/hash`](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L1746-L1960) | `assetId` 下的可检索元数据索引 | 补二者缺失的生成产物追溯 | 迁移后适配 |
| [`full_sync_database` 与浏览器 Auto-Watch](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L2557-L2837) | 受控目录导入/刷新，注册后仍由 `ProjectAssets` 拥有 | 与 MPT 本地显式文件预处理不等价 | 部分复用 |
| [`create_thumbnail`/`get_storyboard`/`stream_video`](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L1655-L1744) | 资产派生预览缓存，不进入 Timeline | OpenShorts 的片段提取可共享预览；不重复渲染 | 部分复用 |
| [`collections`、筛选与列表 UI](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L2140-L2162) | 单一素材库查询/标签视图 | 与 MPT 外部检索结果是不同层 | 重新实现 |
| [`OmniQuery`](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L9020-L9082) | 暂不进入产品 | 非语义媒体搜索，也不补 OpenShorts 片段理解 | 只参考设计 |
| [`AI Search` 配置/队列](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L299-L325) | 既定 `HighlightSuggestionService` 后续范围 | 上游服务未发布，不能替代 OpenShorts | 不引入 |
| [`ComfyUI proxy`/Queue Deck/Remix](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L4808-L4883) | 不属于当前执行边界 | 与 ComfyStudio 候选 Workflow 准备不同 | 不引入 |

## 12. 可以直接复用的代码

**无。** 单文件全局配置、路径型文件身份、Flask 会话/SQLite 表及媒体副作用贯穿实际函数；即使纯逻辑片段也缺乏上游测试。MIT 只是许可可审的前提，不等于可直接集成。[主文件与依赖](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/requirements.txt)。

## 13. 可以迁移后适配的代码

下列目标文件均为**实施时拟新增**，本次没有写入。复制实质源码须保留 [MIT 版权声明](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/LICENSE)，且先核查被引入的每个依赖/媒体/模型。

| 固定提交上游路径/函数 | 当前目标位置与改法 | 依赖、影响、License、验收 |
| --- | --- | --- |
| [`smartgallery.py#L1202-L1377`](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L1202-L1377) `extract_workflow`、[`#L933-L1151`](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L933-L1151) `ComfyMetadataParser` | 拟建 `backend/app/comfy_asset_metadata.py`；只接收 `ProjectAssets.file()` 返回的受控图片/视频，区分 API/UI 格式、拒绝过大嵌入 JSON/无界递归，保留原始与规范字段及解析版本；同名 PNG 视频 companion 仅作为可确认的候选关联，不沿用 Remix 端点传任意路径。 | Pillow、ffprobe 可复用现有依赖；不引 Flask/OpenCV/ComfyUI。影响素材派生元数据和搜索；根 MIT，输入素材/workflow/模型权利另核。测试 PNG `prompt/workflow`、MP4 tags、缺失/畸形/超限、循环连接、多采样器、companion 非同源、文件修改。 |
| [`smartgallery.py#L1746-L1960`](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L1746-L1960) `extract_workflow_files_string`/`extract_workflow_prompt_string`，[`#L10787-L10918`](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L10787-L10918) `compute_workflow_hashes` | 同一拟建模块，改为结构化的模型/LoRA/提示词字段、版本化 canonical hash；区别配置分组与文件/视觉去重，不把绝对路径放业务主键。上游 `compute_workflow_hashes` 异常分支返回一元组，不能原样搬。 | 标准库即可；影响索引和库级筛选，MIT。测试 UI/API 等价、顺序扰动、种子变化、模型变化、提示词误判、畸形数据、哈希版本升级。 |

## 14. 可以部分复用的代码

[`create_thumbnail`](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L1655-L1744)、[`get_storyboard`](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L6327-L6791)和[`stream_video`](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L7294-L7360)可取“静态缩略图→均匀采样帧→必要时低清预览”的行为，但拟建 `backend/app/asset_previews.py` 要用 `assetId + 源指纹 + 参数版本` 作缓存键、设并发/时长/空间界限、验证媒体和产物、按引用生命周期清理。当前 `timeline_render.py` 仍唯一负责成片合成。上游 11 帧和 720p 只是默认值；Pillow、FFmpeg/OpenCV 的依赖与构建许可证需单独核对。验收应覆盖损坏/超大视频、首帧黑帧、缺流、重建、并发取消、文件改变/删除和权限；若复制上游源码，仍受 MIT 版权保留要求约束。

扫描的“成功后更新、离线盘别误删”规则也可部分吸收，但新索引应挂靠 `assetId`，目录不可达应记 `unavailable` 而非删除资产。[上游离线保护](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L2675-L2738)仅针对挂载根消失；[秒级取整 mtime 判断](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L2592-L2596)会漏同一秒修改，且重命名会改变 ID，不适合原样迁移。

## 15. 只建议参考设计的功能

画廊网格/表格双视图、按模型/LoRA/提示词组合过滤、详情中显示解析来源和原始 Workflow、批量标签、客户端展示时去元数据等可独立做 UX/安全设计。[`list_view.html`](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/templates/list_view.html)、[元数据剥离](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L3266-L3355)。不能照搬去元数据失败后[回退原件](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L6068-L6115)的行为。OmniQuery 的只读 SQLite authorizer值得记录，但执行/预览直接取全部行，没有上限；在本项目应优先预定义筛选与分页，而非让 LLM 产任意 SQL。[执行](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L9020-L9082)、[预览](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L9543-L9569)。

## 16. 不建议引入的功能

Flask/Waitress 服务、Jinja 模板、`MD5(path)` 主键、可逆加密的用户密码、Exhibition 评分/Approved 状态、进程内 `rescan_jobs`、尚未发布的 AI Search、ComfyUI 代理/Remix/Queue Deck、GPU/LoRA 模型浏览均不引入。[账号密码](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L3171-L3229)、[应用启动/全网接口绑定](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L11432-L11449)。`management_api_only` 在未启用登录且无会话时允许管理操作；[Comfy 代理](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L4808-L4883)只校验 URL scheme/host 是否存在，没有调用同文件的 `_is_local_or_same_host`，不能迁入当前本机写请求边界。GET `/sync_status` 会改数据库，也不能作为本项目写操作接口。[装饰器](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L661-L685)、[同步端点](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L3541-L3554)。

## 17. 当前项目缺失能力

缺 ComfyUI 产物的可检索元数据与来源归档、库级跨项目浏览/过滤、派生预览/故事板与可靠失效、可解释的本地长视频片段级搜索。这四项不应混成一个“AI 素材库”：SmartGallery主要补第一项并给第二/三项设计参考；第四项由 [OpenShorts 深审](./openshorts-current-project-integration-audit-2026-09-28.md)和后续片段检索候选负责，MPT 负责外部图库来源与脚本分配。[MPT 深审](./moneyprinterturbo-current-project-integration-audit-2026-09-28.md)。

## 18. 可以删除或替换的当前实现

本轮**无**应删除的现有实现。未来新增库级检索应消费 `ProjectAssets` 记录，不替换上传、引用检查、批量推荐、Timeline 或 `durable_runs`。原有名称/备注检索可作为候选缺失时的回退，但不并存第二个素材身份或第二套审核状态。[本地推荐](../../backend/app/batch_recommendation.py)。

## 19. 融合后的模块架构

```text
ProjectAssets（唯一文件/assetId/引用所有权）
  ├─ ComfyAssetMetadata（候选：受控解析，assetId + sourceFingerprint + parserVersion）
  ├─ AssetPreviewCache（候选：缩略图/故事板，派生可重建）
  └─ AssetLibraryIndex（候选：跨项目只读过滤/标签，不拥有文件）
             ↓
当前 Shot / Timeline / BatchVariant / 审核交付
             ↘ 既定 HighlightSuggestionService + MontagePlanner
```

外部 ComfyUI 队列与视频最终生成仍在应用外；素材库索引失败不能改变资产、镜头或已批准交付的有效性。[ADR 0007](../adr/0007-preproduction-with-shot-steps.md)。

## 20. 融合后的用户流程

用户导入/选择项目素材 → 取得稳定 `assetId` → 可选解析 ComfyUI 嵌入元数据和生成预览 → 在同一素材库筛选/查看来源 → 将资产绑定制作镜头或送入现有片段分析 → 用户改选并保存 → 现有预览/审核/交付链继续。解析失败只标元数据状态，不删除原素材；Workflow/提示词不得被误当“实际模型执行事实”。

## 21. 对当前代码目录的影响

若批准实施，先新增 `backend/app/comfy_asset_metadata.py`（纯解析）与对应 tests；再按需求新增 `backend/app/asset_library_index.py`、`backend/app/asset_previews.py`，通过现有 [`preproduction_api.py`](../../backend/app/preproduction_api.py)接入受控读取与保存；前端在既有 [`AssetManager.tsx`](../../frontend/src/AssetManager.tsx)或素材库视图扩展元数据与预览。不得添加 `smartgallery.py`、Flask/Waitress 或上游模板、数据库副本。涉及 UI 时先遵循本地 [`design.md`](../../design.md)。

## 22. 数据模型调整

派生字段建议分表/附属记录而不是改 `assetId`：`assetId, sourceFingerprint, parserVersion, parseStatus, workflowFormat, workflowRawRef, promptSummary, modelRefs[], loraRefs[], workflowStructureHash, promptHash, previewVersion`。`sourceFingerprint` 以受控文件内容或强校验属性构成，避免上游 path+mtime 的身份/缓存错配；哈希只用于生产配置分组，不宣称二进制或视觉去重。片段索引继续单独保存源区间/算法版本，不混入 ComfyUI 解析记录。

## 23. Store 调整

React/Zustand 仅增加素材库筛选状态、选中项和解析/预览加载状态；项目/镜头/时间线仍通过现有稳定 ID 读取同一后端资产。上游 JS 依赖 `localStorage` 保存 Auto-Watch 开关并在变化时整页重载，不应复制进本项目工作台；长时分析应使用现有运行状态边界。[上游定时器](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/templates/index.html#L3461-L3515)。

## 24. API 调整

若有实施需求，在当前 FastAPI 项目路径下提供只读素材搜索/预览/解析结果，以及受控的重新解析操作；返回项目 ID、稳定 assetId、元数据来源与解析版本，查询强制分页/上限。重新解析的写请求带当前版本约束；派生缓存失效不触发素材删除。不得开放任意目录挂载、绝对路径、任意 SQL、ComfyUI 代理或 GET 改写索引的端点。[当前文件 API](../../backend/app/preproduction_api.py)、[上游上传直接保存路径](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L4906-L4929)。

## 25. P0 / P1 / P2 实施计划与验收

| 优先级 | 最小切片 | 通过条件 |
| --- | --- | --- |
| P0 | `assetId` 下解析 ComfyUI PNG 的 API/UI Workflow 和模型/LoRA/提示词；只读展示来源 | 真样本与畸形/超限样本、修改后失效、前后兼容、MIT/依赖权利复核；不改变现有参考素材与审核。 |
| P1 | 库级筛选、批量标签、视频故事板/低清派生预览 | 大量素材的分页/缓存/取消、不同项目身份、引用删除保护、FFmpeg 错误恢复；视频 sidecar 配对需用户确认来源。 |
| P2 | 目录增量导入或高级自然语言检索 | 确认用户确需外部目录/SQL 场景后独立立项；目录移动/离线/同秒更新、无界查询和权限均有明确合同。AI 语义片段搜索不以本仓未发布服务为底座。 |

## 源码核验补充：易误判的边界

- **PNG/视频/sidecar：** `extract_workflow` 优先图像 `workflow`/`prompt` 块、Exif/UserComment，视频用 `ffprobe -show_format` tags；找不到时会**读入整个文件字节**扫描花括号，不应对大视频照搬。[源码](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L1202-L1377)。同名 PNG companion 的发现仅在[Remix 路由](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L10607-L10626)，常规 `process_single_file` 不会自动把视频与 PNG 合并成一个素材记录。
- **扫描/失效：** `full_sync_database` 和 `sync_folder_on_demand` 均把 mtime `int()` 后比较，漏同秒更改；索引 ID 为路径 MD5，路径改变等于新资产，文件删除触发外键级联，不能迁入本项目。[全量](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L2557-L2738)、[单目录](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L2742-L2837)。浏览器 Auto-Watch 是定时发同步请求；后台 AI watcher 另走 `ai_indexing_queue`，但依赖未发布 AI 服务。[UI](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/templates/index.html#L3329-L3505)、[AI watcher](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L2449-L2555)。
- **预览与转码：** 缩略图图片用 Pillow、视频优先 OpenCV 首帧再 FFmpeg；故事板均匀取 11 帧，缺解码时临时转码；浏览器 `/stream` 用 FFmpeg 即时转 H.264/AAC 720p，断开请求会终止进程。[缩略图](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L1655-L1744)、[故事板](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L6327-L6791)、[串流](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L7294-L7360)。这些是派生预览，不是成片生产链。
- **删除/隐私：** 默认 `safe_delete_file` 是永久 `os.remove`，只有设置 `DELETE_TO` 才转存垃圾目录；单/批量删除不查询本项目式镜头和时间线引用。[删除](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L1156-L1186)、[批量端点](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L5839-L5905)。客户模式去元数据失败回退原件，不能作为保密边界。[剥离失败回退](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L6068-L6115)。
- **测试与依赖：** 固定提交树没有 `tests/`、`test_*.py`、JS 测试或 `pytest` 配置；`requirements.txt` 未锁版本，列 Flask/Pillow/OpenCV/NumPy/tqdm/cryptography/Waitress 等；FFmpeg/ffprobe 是外部可执行依赖。[依赖](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/requirements.txt)、[FFmpeg 配置](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/smartgallery.py#L174-L181)。本次只对固定主文件做 Python AST 语法解析成功，不等于运行/媒体行为测试。
- **许可：** 根 [`LICENSE`](https://github.com/biagiomaf/smart-comfyui-gallery/blob/3cbcf83883b05f4af46723f3322d044c2ea972d3/LICENSE) 是 MIT，版权 `2025–2026 Biagio Maffettone`，复制实质代码须保留声明；固定提交树未发现子目录 LICENSE 或随仓模型权重。上游管理/展示的用户媒体、ComfyUI Workflow、checkpoint/LoRA、Civitai sidecar、字体/视觉素材、FFmpeg 构建与可选未来 AI 服务不因根 MIT 自动授权。未核第三方逐项条款，禁止据此直接引入模型或用户素材。

## 本次验证范围

已用 GitHub 提交 API 固定 SHA，逐段核对上游实际文件与行号；以 `git ls-tree` 检查测试和许可证路径；主文件经 `ast.parse` 成功。未安装上游依赖、启动服务、运行上游测试、连接 ComfyUI、运行媒体/模型质量实验，也未修改本项目产品代码或[累计登记表](./reference-project-integration-register.md)。本报告的实施位置和成功标准是工程建议，尚未通过真实素材验收。
