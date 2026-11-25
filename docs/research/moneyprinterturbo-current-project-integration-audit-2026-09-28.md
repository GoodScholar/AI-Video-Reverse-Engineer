# MoneyPrinterTurbo 固定提交源码深审与本项目接入判断

> 累计融合结论以 [参考项目累计融合决策登记表](./reference-project-integration-register.md) 为入口；本报告只负责 MoneyPrinterTurbo 的固定提交深审和文件级证据。

审计日期：2026-09-28。上游唯一基准是 [`harry0703/MoneyPrinterTurbo@d7b738345e2298d3e21738140190b8a6a68cd39f`](https://github.com/harry0703/MoneyPrinterTurbo/commit/d7b738345e2298d3e21738140190b8a6a68cd39f)（`pyproject.toml` 声明版本 `1.3.7`）。以下源码链接均固定到该提交；本次检出源码阅读，未安装、启动或调用任何供应商服务。本项目现状依据当前工作树的 `backend/app` 与 `frontend/src`，只作接入边界判断，不代表已实现集成。

## 结论与复用级别

MPT 是面向需要快速制作解说式短视频的个人创作者/运营者的“主题或自写脚本 → 搜索词 → 库存/生成/上传素材 → 配音 → 字幕 → 合成 → 可选发布”工具；CLI、FastAPI、Streamlit 共享大体相同的生成服务。它的强项是可配置的素材来源、完整自动生成与供应商适配，而本项目的核心是项目素材、可编辑时间线、版本冻结、预览审核和批准后交付。因此不宜整体搬入第二套任务、素材、渲染和发布系统。[上游自述](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/README-en.md#L1-L14)、[上游流水线](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L1382-L1764)、本地 [`batch_production.py`](../../backend/app/batch_production.py)、[`batch_editing_api.py`](../../backend/app/batch_editing_api.py)。

**最重要的否定结论：该提交没有“从大量本地视频自动理解内容并按脚本语义选片”的完整能力。** `video_source="local"` 时，流水线明确跳过搜索词生成，只接收调用方已选的 `video_materials`；预处理只做白名单路径、可读取、分辨率和图片转视频；合成器将每个输入视频按固定时长机械切段，再按随机/顺序及源文件去重优先级拼接，直到覆盖旁白时长。它没有为本地素材建立场景/转录/视觉索引，也没有对本地片段与脚本做语义打分。可选 TwelveLabs 在实际调用链中只把**非本地**的文本搜索词按主题重排，`analyze_clip` 是接受公共 URL 的独立辅助函数，不在本地选片主链路上。[本地分支](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L648-L667)、[跳过本地关键词](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L1532-L1543)、[本地预处理](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/video.py#L1616-L1698)、[机械切段与拼接](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/video.py#L778-L1031)、[TwelveLabs](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/twelvelabs.py#L97-L166)。

级别定义：**A 直接复用**＝接口和依赖几乎原样满足本项目契约；**B 部分复用**＝取局部算法/规则，重写适配本项目模型；**C 只参考**＝参考产品/流程或边界，重新实现；**D 不引入**＝当前无相应需求，或引入会造成状态/许可/依赖冲突。本次没有 A 项：上游的实质逻辑均绑定 `VideoParams`、任务目录、MoviePy 或供应商配置，本项目已有不同的领域所有权。

| 候选与级别 | 上游精确证据 | 本项目接法、依赖与风险 |
| --- | --- | --- |
| **B：外部库存素材 Provider 与来源记录** | [`search_videos_pexels`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material.py#L313-L412)、[`search_videos_pixabay`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material.py#L414-L554)、[`search_videos_coverr`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material.py#L555-L694)、[`_material_source_record`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material.py#L41-L156) | 产品确认需要外部库存素材时，单独实现 `MaterialProvider`，返回供应商、资产 ID、公开来源页、作者、尺寸、搜索词、取得时间与授权快照，先注册到本地 [`ProjectAssets`](../../backend/app/project_assets.py) 再供剪辑使用。需要各图库 Key、网络、下载/素材授权核查；MPT 来源记录不会自动证明商用权利，也未保存授权快照。 |
| **B：搜索结果缓存策略** | [`_cache_key`、24h TTL、原子写、清理](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material_cache.py#L19-L430)、[`_search_videos_with_cache`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material.py#L1673-L1765) | 可取“供应商+词+最短时长+画幅”键、非空成功才缓存、签名 URL 来源不落盘、同目录原子替换；按本地缓存生命周期实现。依赖磁盘/锁与图库响应结构；缓存 URL 可能失效，不能把缓存命中等同已拥有素材，更不能混入项目素材索引。 |
| **B：素材有序分配、去重和批量多样性** | [`_download_videos_by_script_order`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material.py#L2493-L2606) 对**外部搜索结果**按关键词分组轮询；[`_prioritize_unique_source_clips`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/video.py#L204-L270) 每源先取最长切片；[`generate_final_videos`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L894-L967) 记录跨成片复用及警告 | 可将“叙事顺序、同源不重复、候选不足时显式降级”转为本地规划规则；输入应是本地候选片段，输出可解释的镜头建议，交给现有 [`batch_recommendation.py`](../../backend/app/batch_recommendation.py) /时间线。MPT 逻辑基于供应商搜索词和整文件路径，不含本地片段语义分数；直接接入会覆盖已有的匹配解释与版本边界。 |
| **B：试听复用、真实音频时长校验** | [`_resolve_reusable_voice_preview`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L422-L481) 比较全文/音色/速率/音量并限任务目录；[`generate_audio`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L482-L579) 优先探测写出文件时长；[WebUI 全文试听](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/webui/Main.py#L5635-L6259) | 核对本地 [`local_voice.py`](../../backend/app/local_voice.py) 与变体 `contentRevision` 后，仅补缺失的“脚本+音色+参数+模型版本”指纹及文件真实性检查。上游试听载荷只在 WebUI 进程内传递，不是公开 API；不得让浏览器提交任意服务器路径。外部 TTS API 不在当前离线语音合同中。 |
| **B：上传预检与原子落盘规则** | [`save_material_upload`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material_upload.py#L286-L323) 用临时文件、完整媒体解码、UUID 文件名、`os.replace`；[上传路由](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/controllers/v1/video.py#L405-L472) | 只提炼预检清单核对本地 `ProjectAssets` 上传入口；其固定 200MB 视频/20MB 图片上限、FFmpeg 120 秒完整解码与本地交互需求未必一致。不可复制 `storage/local_videos` 全局共享目录来替换按项目拥有的素材。 |
| **C：分阶段流水线与错误状态** | [`_run_pipeline`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L1382-L1715) 有 `script→terms→audio→subtitle→materials→video` 和 `stop_at`；[`_mark_task_failed`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L243-L291) 保存阶段、错误、远端 ID | 参考阶段名/前置费用校验/失败可定位原则；现有 [`durable_runs.py`](../../backend/app/durable_runs.py) 已有冻结输入、迟到结果、状态约束，`batch_production` 持有审核/交付，不能迁入上游单条任务状态机。 |
| **C：WebUI 四栏与任务反馈** | [`_render_application`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/webui/Main.py#L8250-L8302)、[任务管理/筛选/重做](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/webui/Main.py#L1226-L1399)、[进度/结果 Fragment](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/webui/Main.py#L1965-L2119) | 可参考“明确设置分组、素材来源提示、后台进度/失败阶段、已生成结果下载”；本项目是 React 多工作区，已有 BatchEditor/审核页面，Streamlit 页面与 `session_state` 无法直接嵌入。 |
| **C：视频处理细节** | [`combine_videos`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/video.py#L778-L1031) 的时长余量、变速源区间、cover/contain、转场与 FFmpeg concat；[`generate_video`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/video.py#L1258-L1581) 叠配音、音乐、字幕 | 按证实的具体缺口参考；本地 [`timeline_render.py`](../../backend/app/timeline_render.py) 已承担 FFmpeg 时间线、源区间与产物校验。MoviePy 2.2.1 加一整套临时文件/编码回退会形成第二渲染真相。 |
| **D：上游 API/队列/Redis 状态整体迁入** | [路由](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/controllers/v1/video.py#L188-L519)、[队列](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/controllers/manager/base_manager.py#L41-L150)、[Redis 状态](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/state.py#L111-L235) | 本地已有 FastAPI、项目/批量任务与 `durable_runs`。引入将造成任务 ID、并发、恢复、文件 URL、审核版本双写。Redis 依赖及生产部署复杂度无对应收益。 |
| **D：自动跨平台发布与完整供应商 TTS 集** | [`upload_post.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/upload_post.py#L14-L196)、[`voice.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/voice.py#L598-L1052) | 发布不属于当前批准后导出合同；Upload-Post 需要账户/Key、平台授权与视频外发。多 TTS Provider 需要各自 Key/模型/网络及条款，不能替换本地 Qwen3-TTS。若产品另立外部发布或配音需求，重新评估单供应商小适配器。 |

## 产品、界面和交互的实际形态

上游 [README](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/README-en.md#L1-L14) 以输入主题/关键词自动制作高清短视频为主要价值；从 [VideoParams](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/models/schema.py#L91-L163) 可见也允许用户自行提供脚本/关键词、指定多种视频来源、配音、BGM、字幕、画幅、转场与批量数量。因此更接近“生成表单 + 作业监控”，不是非线性剪辑器或项目资产管理器；这后一判断是根据界面与模型结构所作推断。

[`webui/Main.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/webui/Main.py#L8250-L8302) 是一个 Streamlit 单页，主区域四列：文案、视频素材、音频/BGM、字幕；下面是生成按钮、进度、日志和成片。顶部有语言、设置与任务管理；设置弹窗为 LLM、素材 API、自动发布、界面、密钥备份、缓存管理六组 [设置分区](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/webui/Main.py#L3136-L3182)。素材来源按库存视频、AI 视频、AI 图片、本地文件分组；本地是多文件上传控件，[“按脚本顺序匹配”复选框](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/webui/Main.py#L5117-L5229) 会强制顺序拼接，但它在 `task.py` 的 local 路径上**没有生成或使用语义关键词**，不可据控件文案推断本地语义匹配。

用户提交前会检查主题或脚本、供应商 Key 与收费确认、本地素材/自定义音频是否存在；上传本地文件后保存可在当前会话再次使用；全长试听可复制入任务目录；最后 `webui_task.submit_generation` 快照参数并入队。[生成控件与预检](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/webui/Main.py#L7828-L8240)、[WebUI 工作线程](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/webui_task.py#L53-L188)。任务入口按全部/进行中/完成/失败筛选，有播放、打开目录、按保存配置重做、删除；结果区域定时刷新并提供视频下载。[任务面板](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/webui/Main.py#L1226-L1399)、[结果](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/webui/Main.py#L1977-L2119)。这些是可参考的反馈模式，不构成分镜编辑、局部替换或审批流程。

## 后端工作流、数据与运行边界

**入口/路由。** [FastAPI 组装](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/asgi.py#L243-L295) 将 `ping`、`video`、`llm` 加入路由；v1 前缀为 `/api/v1`。`POST /videos`、`/subtitle`、`/audio` 都调用 `create_task`，分别停在 video/subtitle/audio；`GET /tasks` 分页、`GET/DELETE /tasks/{id}` 查询/删除；另有本地 BGM 与视频素材列举/上传、视频 stream/download。[视频端点](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/controllers/v1/video.py#L188-L519)。[`/scripts`、`/terms`、`/social-metadata`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/controllers/v1/llm.py#L21-L68) 同前缀。创建任务先写状态再调度，队列满返回 429，调度失败回滚状态；输入校验异常统一 400。[`create_task`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/controllers/v1/video.py#L209-L254)、[异常处理](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/asgi.py#L58-L73)。

**Schema/状态。** `VideoParams` 是大而扁平的生成请求，混合主题、脚本、搜索词、素材路径、供应商、配音、BGM、字幕和视频样式；`MaterialInfo` 只有 provider/url/duration/source_info；`TaskStatusData` 是任务状态，`extra="allow"` 允许透传扩展字段，没有项目 ID、素材版本、镜头候选评分或审核决定。[请求/素材模型](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/models/schema.py#L81-L163)、[响应模型](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/models/schema.py#L278-L352)。生成状态整数为 processing=4、complete=1、failed=-1；发布状态另有 pending/processing/complete/failed。[常量](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/models/const.py#L26-L37)。任务目录 `script.json` 原子写入脚本/词/参数，再增补来源与批量选用记录；这是辅助清单，不是完整版本化领域数据库。[`task_artifacts.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task_artifacts.py#L16-L88)。

**执行。** [`_run_pipeline`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L1382-L1715) 依次预检 FFmpeg/付费供应商，生成或沿用脚本、生成非本地搜索词、生成配音、字幕、取素材、按 `video_count` 合成。素材来源的库存视频走搜索/下载，AI 视频/图片走逐段付费生成，本地走显式文件预处理，[`download_videos`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material.py#L1766-L1957)。`generate_final_videos` 针对多成片统计源文件复用，但不是带审阅的多版本时间线。成功后可异步交给 Upload-Post；发布结果不回滚已完成的视频。[合成](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L894-L1041)、[发布分流](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L1638-L1715)。

**队列/Redis 恢复限制。** API 默认线程+内存队列，配置 Redis 后把**等待中的请求**写入 Redis List，在进程启动时取回并重新派发；状态另用 Redis Hash。并发名额由各进程的 `current_tasks` 与锁维护，不是 Redis 分布式租约；`LPOP` 后运行中的工作线程没有持久化执行上下文，因此不能将其称为崩溃后可无损续跑。上游有队列上限、启动恢复与旧 schema 条目校验/失败收敛，值得参考，但本项目不需要复制另一套队列。[管理器](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/controllers/manager/base_manager.py#L41-L150)、[Redis 队列](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/controllers/manager/redis_manager.py#L20-L156)、[启动恢复](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/asgi.py#L22-L53)、[状态存储](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/state.py#L24-L235)。WebUI 自己另有并发 1 的内存任务管理器及会话日志，重启后不保留执行线程。[`webui_task.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/webui_task.py#L17-L30)。

**安全/错误与缓存。** API Key 未配置则免认证，配置后 v1 与 `/tasks` 静态产物检查同一 Header；CORS 是显式白名单，上传和下载路径有限目录约束。[认证](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/controllers/base.py#L37-L111)、[ASGI 防护](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/asgi.py#L158-L295)。远端供应商失败多在素材层降为无候选或记录远端 ID；`task.start` 捕捉最终未预期异常并写 `failed_stage="pipeline"`。[错误封口](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L1717-L1764)。图库搜索缓存 24 小时、格式版本 2；Coverr 因签名 URL 不入缓存；视频文件缓存有独立的空间/时龄清理。[搜索缓存](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material_cache.py#L19-L430)、[视频缓存清理](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/cache_manager.py#L57-L205)。版本检查通过 GitHub 最新正式 Release，异步线程与 12 小时缓存避免页面阻塞；网络失败降级为不提示。[`version_checker.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/version_checker.py#L20-L202)。

## 源码索引、依赖与许可

| 源码定位 | 审读重点 |
| --- | --- |
| [`webui/Main.py#L3136`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/webui/Main.py#L3136-L3182)、[`#L4922`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/webui/Main.py#L4922-L5116)、[`#L5117`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/webui/Main.py#L5117-L5433)、[`#L6809`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/webui/Main.py#L6809-L7512)、[`#L7513`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/webui/Main.py#L7513-L7827) | 设置弹窗、文案、素材、音频、字幕四区。 |
| [`schema.py#L81`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/models/schema.py#L81-L352)、[`video.py#L188`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/controllers/v1/video.py#L188-L519)、[`llm.py#L21`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/controllers/v1/llm.py#L21-L68) | 请求/响应模型与 API 入口。 |
| [`task.py#L292`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L292-L647)、[`#L648`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L648-L827)、[`#L894`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L894-L1041)、[`#L1382`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L1382-L1764) | 脚本/词、语音/字幕、本地分流、合成、主流水线。 |
| [`material.py#L41`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material.py#L41-L156)、[`#L313`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material.py#L313-L695)、[`#L1766`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material.py#L1766-L1957)、[`#L2493`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material.py#L2493-L2606) | 来源记录、三个图库、供应商分流、脚本顺序轮询。 |
| [`material_cache.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material_cache.py)、[`material_upload.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material_upload.py)、[`task_artifacts.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task_artifacts.py) | 缓存、上传、产物清单的文件生命周期。 |
| [`video.py#L204`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/video.py#L204-L270)、[`#L778`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/video.py#L778-L1031)、[`#L1258`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/video.py#L1258-L1581)、[`#L1616`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/video.py#L1616-L1698) | 源去重、机械切片、合成、局部素材预处理。 |
| [`voice.py#L598`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/voice.py#L598-L1052)、[`#L1913`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/voice.py#L1913-L2101)、[`#L3080`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/voice.py#L3080-L3171)、[`subtitle.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/subtitle.py) | 多 TTS 路由、MiniMax 示例、字幕边界与 Whisper。 |
| [`base_manager.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/controllers/manager/base_manager.py)、[`redis_manager.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/controllers/manager/redis_manager.py)、[`state.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/state.py)、[`webui_task.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/webui_task.py) | 线程/Redis 队列、状态、WebUI 包装层。 |
| [`upload_post.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/upload_post.py)、[`version_checker.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/version_checker.py)、[`twelvelabs.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/twelvelabs.py) | 外部发布、版本提醒、仅文本词重排的可选视觉服务。 |

上游 [`pyproject.toml`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/pyproject.toml#L8-L53) 要求 Python ≥3.11，固定 FastAPI 0.136.3、Streamlit 1.59.1、MoviePy 2.2.1、edge-tts 7.2.7、faster-whisper 1.1.0、Azure Speech、Redis、LiteLLM、Google GenAI 等；TwelveLabs SDK 是可选 extra。FFmpeg 是实际媒体预检与合成的外部可执行依赖。[FFmpeg 预检](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L1508-L1525)。这些依赖并非“引入一个素材函数”的必要集合：候选应以小适配器逐项引入。

根 [`LICENSE`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/LICENSE) 为 MIT，版权声明 `Copyright (c) 2024 Harry`；复制上游源码的实质部分须保留版权与许可证文本。MIT 只覆盖该仓库软件，不自动覆盖 Pexels/Pixabay/Coverr 素材、音色、外部模型、Upload-Post 账户与平台发布权。当前报告只提供复用决策，未复制上游代码。

## 对本项目的落地边界与验证缺口

本地 [`batch_recommendation.py`](../../backend/app/batch_recommendation.py) 已用用户项目素材的名称/备注给脚本段落做可解释匹配，并在没有匹配或素材不足时明确失败；[`ProjectAssets`](../../backend/app/project_assets.py) 持有素材文件与项目归属；[`batch_production.py`](../../backend/app/batch_production.py) 冻结内容/时间线/字幕版本并约束预览、审核、导出。这些所有权应保持唯一。MPT 能提供外部图库/缓存与分配规则参考，却不能直接解决用户自有的大量长视频的镜头发现；若以此为目标，仍需另做 `ClipIndex`（场景边界、候选源区间、可检索的转录/视觉描述）及面向脚本段落的可解释排名，再把建议交给已有时间线和人工改选流程。这是依据两个代码库能力差异提出的工程推断，尚未实施或实测。

本次只做固定源码静态审计；未运行上游 API/WebUI、未测试真实图库返回/签名 URL、TTS 质量、模型生成、Redis 故障恢复、上传平台或本地长视频选片效果。接入前应以实际服务条款、真实媒体样本和本项目既有检查进行分项验收，不能用 README 宣称或本报告替代运行验证。

---

# 综合融合决策（按要求的 25 项输出）

本节把上面的源码证据转成可执行的产品与架构决策。当前项目基准为 `0de6aec7c037c63db5628fa6e3b21da57d0cd4cb`；MoneyPrinterTurbo 基准为 `d7b738345e2298d3e21738140190b8a6a68cd39f`。其余项目只作为补缺对照，不改变“MoneyPrinterTurbo 是重点分析对象”的范围。

## 1. 参考项目核心能力

MoneyPrinterTurbo 的核心用户是个人创作者、短视频运营人员和需要快速批量生成解说式视频的小团队。它解决的不是专业非编，而是把大量供应商能力收拢成一个“填表后生成”的自动化入口：

```text
主题/自写脚本
  → LLM 脚本与搜索词
  → 库存素材检索 / AI 生成素材 / 用户指定本地文件
  → TTS
  → 字幕
  → MoviePy/FFmpeg 合成
  → 多条成片
  → 可选外部发布
```

主要模块是 Streamlit 生成页、FastAPI/CLI/Agent 入口、`VideoParams` 请求模型、脚本与搜索词服务、图库/生成素材 Provider、多 TTS、字幕、MoviePy 合成、任务状态与内存/Redis 队列、缓存和自动发布。最有价值的通用能力不是它的页面或完整流水线，而是：外部素材来源记录、搜索缓存边界、按叙事顺序分配素材、同源去重、跨成片多样性、生成前费用/Key 检查和明确的失败阶段。

只适用于 MPT 自身的能力包括：以 `VideoParams` 为中心的大表单、任务目录 `script.json`、Streamlit `session_state`、MoviePy 渲染真相、Upload-Post 自动发布、没有项目/镜头/审核身份的任务状态。它没有节点画布、镜头列表、Storyboard、可编辑 Timeline、项目资产引用图、版本化审核或多人权限。

其余七个项目的补充价值如下：

| 项目 / 固定提交 | 真正可取能力 | 决策 | License |
| --- | --- | --- | --- |
| [OpenShorts `89f0309`](https://github.com/mutonby/openshorts/tree/89f030934388d5167b3b0ede164ef893dad09ae9) | ASR 窗口、片段截取、重叠去重、词边界吸附；可选活跃说话人/竖屏重构 | P0 复用纯筛选函数；访谈重构放 P2 | 根 MIT；`cloud/` 独立商业许可 |
| [ComfyStudio `4641684`](https://github.com/XmYx/ComfyStudio/tree/46416846d44b544396e66d9c6f29fccc1b30971d) | ComfyUI UI 图到 API prompt 转换、端口身份、防环/单输入约束 | 仅在重新打开本地 ComfyUI 执行范围后部分复用 | MIT |
| [opensource-clipping `4a478df`](https://github.com/NaufalRizqullah/opensource-clipping/tree/4a478dff3dacd293eeb5e28335aa7c81d78b8f50) | 说话人分离、脸部位置与平滑切换 | 与 OpenShorts 的访谈路线二选一 | MIT；模型许可另核 |
| [OpenMontage `08e2151`](https://github.com/calesthio/OpenMontage/tree/08e2151fa02de28a5d6a312b3d575692bf147ad7) | 阶段交付物、QA/费用门禁、过程回放理念 | 只参考设计 | AGPL-3.0 |
| [YumCut `7e07eb3`](https://github.com/IgorShadurin/app.yumcut.com/tree/7e07eb356393c20014ab6badcf6e27c61a740781) | 多 Worker 抢占、补建任务理念 | 只参考；当前单机阶段不引入 | PolyForm Noncommercial 1.0.0 |
| [short-video-maker `9bb9a21`](https://github.com/gyoridavid/short-video-maker/tree/9bb9a212ced86caa7e09099c382da1a44d638760) | MCP 的“创建任务/查询任务”最小接口 | 只参考协议外观 | MIT |
| [ai-clipping-comfyui `d0a7b19`](https://github.com/Anil-matcha/ai-clipping-comfyui/tree/d0a7b1943dfd1e144c9bfe656e57941623b6408d) | ComfyUI 节点 I/O 外观 | 不引入；核心剪辑在 muapi.ai 服务端 | 节点 MIT，不覆盖远端服务 |

## 2. 当前项目核心能力

当前项目已经从早期“参考视频反向工程 + ComfyUI 包导出”演进成项目制内容生产工作台。主要用户是需要管理自己的素材、脚本、镜头结果、批量版本、审核和交付的本地制作团队或个人。正式导航包含项目中心、本地工具、应用设置；项目内包含商品视频制作、项目素材、成片与审片、单条精修、参考视频复刻。

核心流程是“准备资料 → 确认脚本 → 选声制作 → 审核导出”，参考复刻、镜头准备和项目时间线作为独立但共享资产身份的工作流。当前已有：

- `ProjectAssets`：按项目拥有素材、稳定 `assetId`、安全路径和引用检查；
- 单一制作镜头模型：`scene → shot → process node → result versions`；
- 同一镜头实体的虚拟化列表和只读 React Flow 关系图；
- 项目 Timeline：多轨、拆分、复制、撤销/重做、录音、导入镜头结果、FFmpeg 渲染；
- 批量变体：脚本/轨道/字幕修订、配音、预览、审核、批准后导出；
- `durable_runs`：冻结输入、指纹、状态约束和迟到结果隔离；
- 本地场景检测、关键帧、运动分析、Whisper、SAM2/RIFE/放大等工具链；
- 应用级分析 Provider 配置、系统密钥存储、明确外发披露。

`PRODUCT.md` 仍写“首次 ComfyUI Queue”和“MVP 不含视频编辑”，与较新的 ADR 0007–0010、`CONTEXT.md` 及当前代码冲突。后续融合应以 ADR、领域文档和代码为准，并单独修正文档漂移，不能用旧定位否定现有 Timeline 与内容项目边界。

## 3. 两个项目定位差异

| 维度 | MoneyPrinterTurbo | 当前项目 | 融合判断 |
| --- | --- | --- | --- |
| 核心价值 | 尽快从主题生成一条或多条成片 | 管理可编辑、可审核、可追溯的项目生产过程 | MPT 是能力供应者，不是新底座 |
| 主要用户 | 单人创作者/运营者 | 本地创作者与制作交付团队 | 保留当前项目制 IA |
| 工作单元 | 一次 `VideoParams` 任务 | 项目、素材、镜头、变体、运行、审核、交付 | 不引入第二套 task 领域模型 |
| 本地素材 | 调用方先选文件，预检后机械切片 | 已有项目素材、场景/ASR/运动工具和引用关系 | 新增 `ClipIndex`，不采用 MPT local 路径 |
| 自动化 | 高自动化、低局部可编辑性 | 建议 → 人工改选 → 预览 → 审核 → 导出 | 自动选片输出建议，不直接覆盖时间线 |
| 渲染 | MoviePy/FFmpeg 一次性成片 | FFmpeg 时间线 + 明确运行和版本 | 保留当前渲染器 |
| 权限/协作 | 可选 Token，无项目角色 | 暂无用户/角色，只有 revision/锁 | 两边都不能直接提供多人协作 |

## 4. 功能模块对比

| 模块 | MoneyPrinterTurbo | 当前项目 | 结论 |
| --- | --- | --- | --- |
| 项目管理 | 无真正项目聚合 | 内容项目是顶层聚合 | 保留当前实现 |
| 素材管理 | 全局上传目录 + 远端搜索/下载 | 项目资产、稳定 ID、引用检查 | Provider 结果先导入 ProjectAssets |
| 自动选片 | 外部搜索词分配；本地无语义选片 | 元数据关键词推荐较浅；已有分析工具 | 用 `ClipIndex + MontagePlanner` 补齐 |
| 镜头/Scene | 无正式 Shot/Scene 模型 | 单一镜头事实源，列表已虚拟化 | 不复制 MPT task/material 项 |
| Storyboard | 无 | 尚无正式视图 | P2 用同一 shot 投影实现 |
| 节点画布 | 无 | React Flow 只读关系图 | 与 MPT 无复用关系 |
| Timeline | 不可编辑的合成参数 | 多轨编辑和变体轨道 | 保留并共享纯编辑组件 |
| 配音/字幕 | 多 Provider，参数丰富 | 本地 Qwen3-TTS、字幕与版本关系 | 只补确有需求的 Provider Adapter |
| 生成任务/队列 | 线程队列，可选 Redis 等待队列 | 冻结输入的领域运行，单本地 worker | 保留当前语义，按压力扩展 Adapter |
| 版本/审核/交付 | 基本无领域版本与审批 | 当前版本、预览 run、审核和交付严格绑定 | 当前实现明显更成熟 |
| 发布 | Upload-Post | 当前仅审核后导出 | 不引入 |

## 5. 业务逻辑对比

MPT 的流水线是线性的：每个步骤产生文件，后一步读取前一步，失败记录阶段；它适合确定输入后“一路跑到底”。当前项目是可回到任意编辑点的版本化生产：素材、脚本、轨道、字幕、配音、预览和审核都有不同身份，旧结果必须保留但不能授权新版本。

因此融合规则是：MPT/其他项目只能产出候选、来源信息或计算结果，所有“采用、替换、审核、交付”决定仍由当前项目领域层执行。远端 Provider 不得直接写 Timeline；自动选片不得直接覆盖人工轨道；队列结果不得携带业务批准决定。

## 6. UI / UX 对比

MPT 是单页四栏生成表单，优势是设置集中、素材来源明确、生成前检查充分、任务筛选和失败阶段反馈清楚；缺点是 8,000 多行 Streamlit 主文件、状态高度依赖 `session_state`，信息密度大，也没有镜头级检查和局部改选。

当前项目是 React 多工作区，已有统一 `DESIGN.md`、项目导航、镜头列表/关系图、Timeline、批量编辑、审核和交付。融合时只吸收以下交互：

- 在批量推荐旁显示“分析中/候选不足/回退到元数据匹配/失败阶段”；
- 每个推荐片段显示来源、命中理由、分数、源区间和同源复用警告；
- 外部素材搜索明确显示 Provider、作者、来源页、授权待确认状态；
- 支持“重新生成推荐”，但不自动替换已编辑轨道。

不复制 Streamlit 四栏视觉、设置弹窗、任务面板或颜色样式。新 UI 必须使用当前设计变量、组件和错误/空态规范。

## 7. 技术架构对比

MPT 是 Python 单体：FastAPI + Streamlit + 多供应商 SDK + MoviePy/FFmpeg + 内存/Redis 状态。核心服务文件较大且跨层耦合，例如 `webui/Main.py`、`task.py`、`material.py`、`video.py`、`voice.py` 同时承担表单、领域编排、供应商、文件和渲染细节。

当前项目是 React 18/Vite/TypeScript + FastAPI/Python，本地 JSON 领域仓储与 FFmpeg 计算 worker。前端已有 Zustand、React Flow 和 TanStack Virtual；后端按 preproduction、timeline、batch、assets、analysis 等边界拆分。二者语言兼容，但架构所有权不同。

应建立两个“深模块”和一个窄适配层：

1. `ClipIndex`：把一个项目素材转成可检索片段；隐藏场景、ASR、运动和后续视觉描述的工具细节；
2. `MontagePlanner`：输入脚本段、候选片段和多样性约束，输出可解释方案；
3. `MaterialProvider`：统一 Pexels/Pixabay 等外部来源，结果只能先进入 ProjectAssets。

## 8. 数据模型对比

MPT 的 `VideoParams` 混合主题、脚本、素材文件、Provider、音色、BGM、字幕和渲染参数；`MaterialInfo` 只描述下载候选；`TaskStatusData` 允许扩展字段但没有项目/镜头/版本/审核身份。

当前项目已经区分 `assetId`、`sceneId`、`shotId`、`processNodeId`、Timeline `clipId`、批量 `variantId`、运行 `runId` 和交付记录。这个身份体系应保持。融合只新增：

- `ClipIndex`：某个 `assetId + sourceFingerprint + algorithmVersion` 的分析结果；
- `ClipSegment`：稳定 `clipSegmentId`、源区间、转录/场景/运动/标签；
- `ClipCandidate`：脚本段与片段之间的分数、理由、来源和约束命中；
- `ExternalMaterialProvenance`：Provider、远端资产 ID、来源页、作者、检索词、规格、取得时间、条款/授权快照；
- `MontageProposal`：引用现有 batch task/variant，而不是再建另一套成片任务。

## 9. 当前项目已有优势

1. 项目资产、镜头、Timeline、变体、运行、审核和交付的身份比 MPT 清楚。
2. 预览/审核绑定精确修订和运行，编辑后旧审核自动失效。
3. `durable_runs` 冻结输入并拒绝迟到结果污染新状态。
4. FFmpeg Timeline 已有源区间、取消、输出校验和原子发布，不需要 MoviePy 第二真相。
5. 镜头列表虚拟化；列表与关系图共享 Zustand 正规化实体。
6. 已有本地场景、ASR、运动、SAM2 等处理能力，建立 ClipIndex 不必从零引入整套识别栈。
7. Provider 选择、密钥和外发披露边界明确。

## 10. 参考项目优势

MoneyPrinterTurbo 的优势是入口多、供应商多、从主题到成片的自动化闭环完整；外部图库检索、来源记录、缓存、顺序分配和多成片去重已覆盖较多边界；任务面板和失败阶段对单人使用友好。OpenShorts 则补上 MPT 缺失的长视频片段窗口、排序、去重和词边界处理。ComfyStudio 对未来 ComfyUI 工作流导入有明确的图转换和端口约束实现。

## 11. 功能映射表

处理方式严格使用指定六种：

| 参考项目功能 | 当前项目对应功能 | 业务一致度 | 技术兼容度 | 处理方式 |
| --- | --- | ---: | ---: | --- |
| MPT Pexels/Pixabay 搜索 | 暂无外部库存素材 Provider | 高 | 高 | 迁移后适配 |
| MPT 来源记录 | ProjectAssets 元数据 | 高 | 高 | 部分复用 |
| MPT 搜索缓存 | 本地原子文件存储/缓存惯例 | 高 | 高 | 部分复用 |
| MPT 顺序分配与同源去重 | `batch_recommendation.py` | 高 | 高 | 部分复用 |
| MPT 本地素材预处理 | ProjectAssets + 媒体探测 | 中 | 高 | 不引入 |
| MPT Streamlit 四栏 | React 内容工作室 | 中 | 低 | 只参考设计 |
| MPT `VideoParams` | 多个领域 Schema | 低 | 中 | 不引入 |
| MPT `/videos` 与 task 状态 | batch/timeline/durable runs API | 低 | 中 | 不引入 |
| MPT MoviePy 合成 | FFmpeg Timeline | 中 | 低 | 不引入 |
| MPT Redis 等待队列 | `RunQueueAdapter`/单 worker | 中 | 中 | 重新实现 |
| MPT 多 TTS Provider | 本地 Qwen3-TTS | 中 | 中 | 迁移后适配 |
| MPT 自动发布 | 审核后文件交付 | 低 | 中 | 不引入 |
| OpenShorts 片段窗口/去重/词吸附 | 待建 ClipIndex/MontagePlanner | 高 | 高 | 直接复用 |
| OpenShorts 活跃说话人重构图 | 本地 toolkit worker | 中 | 中 | 迁移后适配 |
| ComfyStudio UI 图转 API prompt | workflow template/外部 ComfyUI 包 | 中 | 高 | 部分复用 |
| ComfyStudio Shot/Timeline UI | 当前镜头与 Timeline | 中 | 低 | 不引入 |
| OpenMontage QA/费用门禁 | 当前预检/审核/交付 | 中 | 中 | 只参考设计 |
| YumCut 多 worker 抢占 | 当前单 worker | 低（当前阶段） | 中 | 只参考设计 |
| short-video-maker MCP 外观 | 暂无公开 MCP | 低 | 中 | 只参考设计 |
| ai-clipping-comfyui 远端节点 | 本地工具链 | 低 | 低 | 不引入 |
| Storyboard 视图 | 当前仅镜头列表/图 | 高 | 高 | 重新实现 |
| 可编辑节点画布 | 当前只读关系图 | 中 | 高 | 重新实现 |
| 多人权限/RBAC | 当前无 actor/member/role | 低 | 低 | 不引入 |

## 12. 可以直接复用的代码

MoneyPrinterTurbo：**当前没有完整文件或完整模块满足直接迁移条件。** 它的候选都绑定上游模型、全局配置、日志、任务目录或 MoviePy，强行标为“直接复用”会把适配工作隐藏在复制之后。

OpenShorts 有一组 MIT 根目录纯函数可直接迁移，不需要重新发明其窗口数学：

| 参考项目文件 | 当前项目目标位置 | 复用范围 | 必须修改 | 依赖/影响 | License / 测试 |
| --- | --- | --- | --- | --- | --- |
| [`clip_selection.py#L203-L340`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/clip_selection.py#L203-L340) | `backend/app/clip_selection.py` | `build_transcript_windows`、`dedupe_overlapping`、`snap_clip_to_words` 及纯排序/截取帮助函数 | 改成当前项目类型，时长/重叠阈值由调用方注入；去掉环境变量、模型价目和 Gemini 假设 | 仅 Python 标准库；由新 `MontagePlanner` 调用，不改现有版本/审核逻辑 | 根 MIT，保留版权和许可；新增窗口边界、重叠、词边界、排序测试 |

对这组纯函数的结论是：**建议直接迁移，不重新实现数学/窗口逻辑。** 但“直接迁移”只指列出的纯函数，不包括 OpenShorts 的 Gemini、发布、Docker、Remotion 或 `cloud/` 代码。

## 13. 可以迁移后适配的代码

| 参考项目文件 | 当前项目目标位置 | 为什么可迁移 | 必须适配 | 依赖、影响与测试 |
| --- | --- | --- | --- | --- |
| MPT [`material.py#L313-L554`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material.py#L313-L554) | `backend/app/material_providers/pexels.py`、`pixabay.py` | 请求参数、画幅过滤、响应候选解析已有成熟实现 | 去掉全局 `config`/`MaterialInfo`/下载副作用；实现统一 Provider Protocol；返回 provenance；显式超时/错误 | `httpx` 或沿用后端 HTTP 客户端、Provider Key；不得直接写 Timeline。契约测试、录制响应 fixture、超时/限流/空结果测试。MIT |
| MPT [`material_cache.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material_cache.py) | `backend/app/material_providers/cache.py` | 缓存键、TTL、原子替换、损坏缓存降级和 URL 脱敏边界有价值 | 使用当前文件根与 logger；Provider 显式声明 URL 是否可缓存；不把缓存条目当项目资产 | 磁盘锁/JSON；测 key 稳定性、过期/未来时间、损坏文件、签名 URL 排除。MIT |
| MPT [`task.py#L422-L579`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L422-L579) | `backend/app/local_voice.py` 或独立 `voice_preview_cache.py` | 脚本+音色+参数一致性和真实时长验证与当前需求一致 | 加入当前模型/样例/内容修订；只接受服务端生成的受控路径；先确认本地是否已经覆盖 | 不引入上游 TTS SDK；缓存命中/参数变化/文件缺失/越界路径测试。MIT |
| OpenShorts [`active_speaker.py#L104-L221`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/active_speaker.py#L104-L221) | `backend/toolkit_worker/` 新的可选处理器 | 访谈场景的活动说话人选择可复用 | 输入改为项目 ClipIndex 与当前 worker 协议；输出只保存重构建议 | OpenCV/NumPy/人脸框；仅 P2。测静音、双人切换、重叠讲话、丢脸。MIT |

MPT Provider 和缓存的结论是：**保留核心逻辑，重新实现当前项目适配层。** 不复制 `material.py` 或 `material_cache.py` 整文件。

## 14. 可以部分复用的代码

- MPT [`_prioritize_unique_source_clips`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/video.py#L204-L270) 与 [`generate_final_videos`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L894-L1041)：复用“同源先去重、候选耗尽才复用、跨版本记录复用”的规则；放入 `backend/app/montage_planner.py`。不复用 MoviePy clip、全局随机、文件路径和 task state。测试应覆盖来源不足、可重复/不可重复、稳定排序和解释字段。
- MPT [`_material_source_record`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material.py#L41-L156)：复用公开来源页/作者/远端资产 ID 的白名单思想；重新定义 `ExternalMaterialProvenance`，补授权快照和取得时间。
- MPT [`material_upload.py#L286-L323`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material_upload.py#L286-L323)：仅对照临时文件、完整解码、UUID 名和 `os.replace` 检查当前 `ProjectAssets`，不引入全局 `storage/local_videos`。
- ComfyStudio [`comfy_workflow_convert.py#L35-L146`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/comfy_workflow_convert.py#L35-L146)：未来如需要导入用户 ComfyUI UI JSON，可保留 link/widget/禁用节点转换逻辑，在 `backend/app/comfy_workflow_import.py` 实现当前 Schema 与错误模型。MIT，需旧格式拒绝、悬空边、端口值映射测试。

## 15. 只建议参考设计的功能

- MPT 的四组设置、生成前条件检查、任务筛选、失败阶段和成片下载反馈；按当前 React IA 重画。
- OpenMontage 的阶段交付物、QA Gate、费用确认和运行回放；因 AGPL 只重建概念，不复制实现。
- YumCut 的数据库抢占和补建任务；只有未来多进程 worker 有真实性能需求时，作为 `RunQueueAdapter` 新实现的参考。
- short-video-maker 的两个 MCP 工具外观；如果将来开放 Agent 接口，只做现有领域服务的薄协议层。
- Storyboard：将当前 `shotId` 投影成卡片视图，拖动只改变 `shot management order`，不得建立第二套 storyboard shot。

## 16. 不建议引入的功能

- MPT 的 Streamlit 页面、`VideoParams`、`task.py` 总流水线、Memory/Redis 状态、`script.json` 任务产物、MoviePy 合成器和 Upload-Post。
- MPT 的本地素材“顺序匹配”作为自动选片方案；它没有本地语义索引。
- MPT 全量 TTS/LLM/AI 图片/视频 SDK；按具体供应商逐个立项。
- ComfyStudio 的 PyQt UI、Timeline 和独立 Shot 模型；会与 React、现有 Timeline 和单一镜头事实源重复。
- OpenShorts/opensource-clipping 的完整应用、发布和第二套任务系统。
- ai-clipping-comfyui：本地节点只是上传/轮询远端服务，不能补本地核心算法。
- 多人协作/RBAC：当前没有 actor/member/role 领域合同，不能只搬 UI。

## 17. 当前项目缺失能力

1. 大量项目视频素材的持久化片段索引：场景、源区间、转录、运动、人物/视觉描述和算法版本。
2. 面向脚本段落的片段级排序、去重、覆盖和跨变体多样性；当前 `asset-metadata-keywords` 只匹配文件名/备注。
3. 推荐方案的候选替换器和充分的解释：为什么选、还有哪些候选、为何回退。
4. 可选外部素材 Provider、来源/权利元数据和导入 ProjectAssets 的完整链路。
5. 正式 Storyboard 视图和场景创建/改名/跨场景移动命令。
6. 关系图布局和受控依赖边持久化；当前 React Flow 只读且布局重建。
7. 项目素材引用索引对 `batch-edits` 当前/历史运行的覆盖。
8. 真正多人协作所需的身份、成员、角色和审计人；此项不应与本次选片融合捆绑。

## 18. 可以删除或替换的当前实现

- **替换内部实现，不新增第二个推荐端点：** 保留 `backend/app/batch_recommendation.py` 对外合同，把内部“只按素材名称/备注匹配”替换为 `MontagePlanner`。原算法作为 `metadata-keyword-fallback` 保留在同一模块内，并在响应中明确 `method`，而不是继续维护两个并列推荐系统。
- **合并已有分析产物：** `local_preprocessing_runner.py`、`video_toolkit.py`、`shot_analysis.py` 的场景/ASR/运动结果进入统一 ClipIndex；不要再建第三套媒体分析持久化。
- **共享而非合并领域：** TimelineEditor 与 BatchEditor 可提取轨道/片段纯 UI，但项目 Timeline 与变体 Timeline 的状态、版本和审核不能合并。
- **扩展引用索引：** 将 `batch-edits` 纳入 `asset_references.py`；不能仅靠禁止删除或复制素材绕开。
- 保留并不替换：ProjectAssets、镜头模型、`durable_runs`、FFmpeg Timeline、配音、字幕、审核和交付。

## 19. 融合后的模块架构

```text
ProjectAssets（唯一素材入口）
  ├─ 本地用户素材
  └─ MaterialProvider Adapter（P1，可选）
       └─ Pexels / Pixabay → 来源与授权元数据 → 导入 ProjectAssets
              │
              ▼
ClipIndex（新增深模块）
  ├─ 复用现有 scene detection / Whisper / motion
  ├─ 片段与源指纹、算法版本
  └─ 可查询候选区间
              │
              ▼
MontagePlanner（新增深模块）
  ├─ OpenShorts 窗口/词边界/去重
  ├─ MPT 顺序分配/同源去重/跨变体多样性
  └─ 当前业务约束与解释
              │
              ▼
现有 BatchVariant / Timeline（唯一编辑真相）
  → 现有 Voice / Subtitle / FFmpeg Preview
  → 现有 Review / Delivery
```

这里没有第二套任务系统、第二套素材库、第二套 Timeline 或第二套审核模型。

## 20. 融合后的用户流程

1. 用户创建项目并导入本地素材；P1 后也可搜索外部素材并先确认来源、再导入项目素材。
2. 用户对素材发起分析；系统建立或复用 ClipIndex，显示分析版本、进度和失败阶段。
3. 用户确认脚本；系统按脚本段生成候选窗口并执行 MontagePlanner。
4. 界面展示推荐片段、来源、区间、命中理由、分数、复用警告和替代候选。
5. 用户采用全部、逐段替换或忽略建议；采用动作通过现有 batch/timeline 命令写入，不由 Planner 直接写状态。
6. 用户继续配音、字幕和轨道编辑，生成冻结版本预览。
7. 审核只批准具体当前版本与预览 run；通过后由现有交付流程导出。

## 21. 对当前代码目录的影响

| 目录/文件 | 动作 | 说明 |
| --- | --- | --- |
| `backend/app/clip_selection.py` | 新增 | 放直接迁移的 OpenShorts 纯窗口/去重函数及本地类型适配 |
| `backend/app/clip_index.py` | 新增 | ClipIndex Schema、仓储、查询和指纹；不放供应商代码 |
| `backend/app/clip_index_runner.py` | 新增 | 编排现有场景/ASR/运动工具，接入 durable run |
| `backend/app/montage_planner.py` | 新增 | MPT 分配规则 + OpenShorts 片段选择 + 当前业务约束 |
| `backend/app/material_providers/base.py` | P1 新增 | Provider Protocol 和 provenance |
| `backend/app/material_providers/pexels.py`、`pixabay.py`、`cache.py` | P1 新增 | 小适配器，不复制 MPT 大服务文件 |
| `backend/app/batch_recommendation.py` | 修改/收窄 | 变成 Planner 接入层；旧逻辑只作显式 fallback |
| `backend/app/asset_references.py` | 修改 | 补 batch-edits 和冻结运行引用 |
| `backend/app/main.py` | 修改 | 注册 ClipIndex/Provider 路由和依赖，不加入 MPT 路由 |
| `frontend/src/batchPlanningApi.ts` | 新增 | ClipIndex、推荐、外部来源的 API 类型 |
| `frontend/src/BatchClipPicker.tsx` | 新增 | 推荐与候选替换 UI，遵循 `DESIGN.md` |
| `frontend/src/BatchEditor.tsx` | 修改/拆薄 | 挂接 ClipPicker；不再承载全部候选状态 |
| `frontend/src/batchPlanningStore.ts` | 条件新增 | 仅当跨组件选择/过滤状态确有必要；服务端仍是权威 |
| `backend/tests/test_clip_selection.py` | 新增 | 直接迁移逻辑的回归测试 |
| `backend/tests/test_clip_index.py` | 新增 | 指纹、失效、原子存储、工具失败和恢复 |
| `backend/tests/test_montage_planner.py` | 新增 | 顺序、去重、多样性、候选不足、解释、稳定性 |
| `frontend/src/BatchClipPicker.test.tsx` | 新增 | 加载/失败/回退/替换/不覆盖人工编辑 |

## 22. 数据模型调整

建议 ClipIndex 独立保存到 `project-files/{projectId}/clip-index/{assetId}/state.json`，避免把大体积分析结果塞进 `preproduction/state.json`：

```text
ClipIndex
  schemaVersion
  projectId
  sourceAssetId
  sourceFingerprint
  algorithmVersion
  revision
  status / latestRunId
  segments[]

ClipSegment
  clipSegmentId
  startSeconds / endSeconds
  sceneId?                 # 分析场景，不等于制作 sceneId
  transcript / words[]?
  motionScore?
  visualTags[]?
  thumbnailAssetRef?
```

`BatchProposal` 增加 `method`（例如 `clip-index-v1` 或 `metadata-keyword-fallback`）、`clipSegmentId`、`sourceAssetId`、`sourceRange`、`score`、`reasons[]` 和 `plannerVersion`。外部素材必须带 `ExternalMaterialProvenance`；导入后仍获得本地 `assetId`。任何自动分析 `sceneId` 都不能复用制作场景 `sceneId`，建议命名为 `analysisSegmentGroupId` 或完全省略，防止身份混淆。

## 23. Store 调整

- 保留 `preproductionWorkspaceStore` 为镜头列表/关系图的唯一前端实体源；ClipIndex 不进入该 store。
- Batch 推荐和候选量变大后，可以新增轻量 `batchPlanningStore`，只保存当前脚本段、筛选条件、选中候选、请求状态和未提交草稿；持久化权威仍在后端。
- 不把 Provider Key、服务端文件路径、整个转录或所有缩略图二进制放进 Zustand。
- Storyboard 将来复用现有 `shotsById` 和 `shotOrderBySceneId`，不建 `storyboardShots`。
- 可编辑画布将来通过显式领域命令保存依赖和独立布局 revision，不让 React Flow 节点对象变成领域真相。

## 24. API 调整

P0 建议：

```text
POST /api/projects/{projectId}/assets/{assetId}/clip-index-runs
GET  /api/projects/{projectId}/assets/{assetId}/clip-index
GET  /api/projects/{projectId}/assets/{assetId}/clip-index-runs/{runId}
POST /api/projects/{projectId}/batch-edits/{taskId}/recommendations
```

最后一个端点保留现有入口，扩展请求为脚本修订、目标比例/时长、允许复用程度、候选资产范围；响应包含 method、plannerVersion、解释、候选和输入指纹。采用建议继续走现有变体/Timeline 保存端点。

P1 建议：

```text
GET  /api/projects/{projectId}/external-materials/search
POST /api/projects/{projectId}/external-materials/import
```

搜索结果只是临时候选；`import` 下载、校验、记录 provenance，并返回本地 `assetId`。Provider 不暴露 MPT `/videos`、任务路径或远端签名 URL 为持久业务身份。

## 25. P0 / P1 / P2 实施计划

### P0：本地大量素材自动选片

1. 定义 ClipIndex、源指纹、算法版本、失效与运行合同。验证：同文件重复分析命中；文件/算法变化失效；中断运行不发布半成品。
2. 用现有 scene detection、Whisper、motion 构建片段，不重复引入识别栈。验证：2–3 个真实长视频可得到稳定源区间与可追溯转录。
3. 直接迁移 OpenShorts 纯窗口/去重函数；实现 MontagePlanner，吸收 MPT 同源去重和跨变体多样性规则。验证：边界、重叠、词吸附、素材不足、确定性和解释测试。
4. 扩展现有 recommendations 端点与 BatchClipPicker。验证：人工替换不被重新推荐覆盖，fallback 清晰，现有审核/交付测试仍通过。
5. 扩展资产引用索引至 batch-edits。验证：正在使用的素材不能被误删，冻结产物策略明确。

### P1：外部库存素材

1. 建立 MaterialProvider Protocol，只接 Pexels/Pixabay。验证：契约 fixture、限流、超时、空结果和密钥缺失。
2. 迁移 MPT 缓存/来源边界到当前 Adapter。验证：TTL、损坏缓存、URL 脱敏、签名 URL 排除。
3. 外部候选先导入 ProjectAssets，再进 ClipIndex/Planner。验证：来源、作者、检索词、取得时间和授权快照可查；删除规则与本地素材一致。
4. 在当前设计系统中加入搜索与导入 UI。验证：清楚区分“搜索结果”“已导入项目素材”“授权待确认”。

### P2：经产品验证后再做

- 访谈活跃说话人/自动竖屏重构，OpenShorts 与 opensource-clipping 二选一；
- ComfyUI UI JSON 导入和受控 DAG 编辑，仅在 ADR 重新确认本地执行范围后；
- Storyboard 作为同一 shot 模型的第三种视图；
- 多 worker 队列 Adapter，仅在真实并发压力出现后，并保持 frozen input/late result 语义；
- 外部 TTS、生成视频 Provider、自动发布和多人协作分别立项，不与本次融合打包。

完成标准不是“拥有 MPT 同款页面”，而是：用户能对自己的大量项目素材得到可解释、可替换、可审核的片段推荐；外部素材可选但不污染资产和版本边界；系统仍只有一套项目、素材、镜头、Timeline、运行与审核真相。
