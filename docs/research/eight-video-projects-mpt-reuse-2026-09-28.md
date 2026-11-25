# 八个视频项目源码核验：以 MoneyPrinterTurbo 为重点

> 累计融合结论以 [参考项目累计融合决策登记表](./reference-project-integration-register.md) 为入口；本报告保留这八个项目固定提交的详细证据。

核验日期：2026-09-28。范围是仓库默认分支的固定提交、GitHub 仓库元数据、仓库内 README、许可证和配置/源码；未安装或运行这些项目，未验证外部 API、模型质量或成片效果。星数和最近推送时间是本次查询 GitHub 仓库 API 的快照，不代表代码质量或持续维护承诺。源码判断均针对下述固定提交，推荐的接入顺序是对本项目的工程推断。

## 结论先行

1. 八个指定仓库均存在。最适合进一步*函数级*取材的是 [MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo)：FastAPI/Python 与本项目后端接近，具有分阶段任务、配音参数一致性检查和外部素材来源记录；但本项目已经有 `batch_production`、`batch_editing_api`、`local_voice`、`timeline_render`、`durable_runs`，不应整体搬入第二套任务/审核/渲染状态机。
2. [YumCut 的根许可](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/LICENSE) 是 **PolyForm Noncommercial 1.0.0**，不是 MIT 或可默认商用的开源许可；仓库 [README](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/README.md) 也写明面向客户托管/销售访问需商业许可。[OpenMontage](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/LICENSE) 是 AGPL-3.0。二者可以研究交互或架构，商业产品迁入代码前须另作许可证决策。
3. [Anil-matcha/ai-clipping-comfyui](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/README.md) 的节点源码虽是 MIT，关键 Whisper、排序、去重、裁切在 **muapi.ai 服务端**执行；它不是本地全链路开源剪辑器。[gyoridavid/short-video-maker](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/README.md) 明确是文本+库存素材合成器，不做从图像或图像提示词生成新画面。

## 1. 仓库身份、活跃度与许可快照

每行的仓库 API 链接可核对 `full_name`、`stargazers_count`、`pushed_at`、`archived`、`license`；固定提交链接使下文的文件证据可复现。`pushed_at` 只能证明仓库最近有推送，不能证明核心功能最近维护。八个仓库查询时均未归档。

| 指定仓库 / 固定提交 | Stars；最近推送（UTC） | 根许可证与边界 |
| --- | ---: | --- |
| [harry0703/MoneyPrinterTurbo](https://api.github.com/repos/harry0703/MoneyPrinterTurbo) / [`d7b7383`](https://github.com/harry0703/MoneyPrinterTurbo/commit/d7b738345e2298d3e21738140190b8a6a68cd39f) | 126,415；2026-09-28 | [MIT](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/LICENSE)；第三方 API 和素材另有条件。 |
| [mutonby/openshorts](https://api.github.com/repos/mutonby/openshorts) / [`89f0309`](https://github.com/mutonby/openshorts/commit/89f030934388d5167b3b0ede164ef893dad09ae9) | 5,742；2026-09-27 | 根 [MIT](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/LICENSE)，但 [`cloud/` 独立商业许可](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/cloud/LICENSE)。 |
| [XmYx/ComfyStudio](https://api.github.com/repos/XmYx/ComfyStudio) / [`4641684`](https://github.com/XmYx/ComfyStudio/commit/46416846d44b544396e66d9c6f29fccc1b30971d) | 23；2026-07-20 | [MIT](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/LICENSE)。 |
| [NaufalRizqullah/opensource-clipping](https://api.github.com/repos/NaufalRizqullah/opensource-clipping) / [`4a478df`](https://github.com/NaufalRizqullah/opensource-clipping/commit/4a478dff3dacd293eeb5e28335aa7c81d78b8f50) | 129；2026-09-26 | [MIT](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/LICENSE)；模型/数据源授权另核。 |
| [calesthio/OpenMontage](https://api.github.com/repos/calesthio/OpenMontage) / [`08e2151`](https://github.com/calesthio/OpenMontage/commit/08e2151fa02de28a5d6a312b3d575692bf147ad7) | 61,613；2026-09-06 | [AGPL-3.0](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/LICENSE)。 |
| [IgorShadurin/app.yumcut.com](https://api.github.com/repos/IgorShadurin/app.yumcut.com) / [`7e07eb3`](https://github.com/IgorShadurin/app.yumcut.com/commit/7e07eb356393c20014ab6badcf6e27c61a740781) | 885；2026-09-03 | [PolyForm Noncommercial 1.0.0](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/LICENSE)；GitHub API 的 SPDX 字段为 `NOASSERTION`。 |
| [gyoridavid/short-video-maker](https://api.github.com/repos/gyoridavid/short-video-maker) / [`9bb9a21`](https://github.com/gyoridavid/short-video-maker/commit/9bb9a212ced86caa7e09099c382da1a44d638760) | 1,380；2025-06-21 | [MIT](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/LICENSE)。 |
| [Anil-matcha/ai-clipping-comfyui](https://api.github.com/repos/Anil-matcha/ai-clipping-comfyui) / [`d0a7b19`](https://github.com/Anil-matcha/ai-clipping-comfyui/commit/d0a7b1943dfd1e144c9bfe656e57941623b6408d) | 26；2026-07-21 | [MIT](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/LICENSE)；服务端处理不在这个许可证覆盖的本仓库代码中。 |

本项目旧文档使用 `Warabellum/openshorts` 的固定提交。GitHub [fork 元数据](https://api.github.com/repos/Warabellum/openshorts) 显示它以 `mutonby/openshorts` 为父仓库；本次按用户指定的 **mutonby 当前提交**核验，不能把旧 fork 文件路径或代码现状直接当作当前上游事实。

## 2. MoneyPrinterTurbo：可迁入边界与本地重叠

上游 [README](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/README.md) 声明主题/关键词→脚本→素材→配音→字幕→成片，也支持 WebUI、API、CLI、Agent、批量生成、自动发布。实际 [依赖清单](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/pyproject.toml) 是 Python ≥3.11、FastAPI、Streamlit、MoviePy、edge-tts、faster-whisper、Azure Speech SDK、Redis、LiteLLM 等多栈集合；不能把 `pip install` 整套依赖当成单模块集成成本。

**最重要的能力边界：MPT 不是“大量本地素材的语义选片器”。** 当 `video_source == "local"` 时，[`get_video_materials`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L648-L667) 只是把用户指定素材交给 [`preprocess_video`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/video.py#L1616-L1698)；后者负责路径、可读性、分辨率和图片转视频校验，没有场景切分、ASR、人物/动作识别或片段语义排序。MPT 的强项是**外部图库/生成源检索、来源记录、下载缓存、按脚本关键词顺序分配和成片编排**。因此它能补“找素材与分配素材”，不能单独补齐本项目目标中的“从大量本地视频自动选片”。

| 上游源码/接口（固定提交） | 本项目现状与差异 | 复用判断 |
| --- | --- | --- |
| [`app/controllers/v1/video.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/controllers/v1/video.py#L188-L209)：`/videos`、`/subtitle`、`/audio`；[`task.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L1382-L1764) 的 `start(..., stop_at=...)` 可停在 script/terms/audio/subtitle/materials/video。 | 本地 [`batch_editing_api.py`](../../backend/app/batch_editing_api.py) 已有内容保存、字幕、配音、预览、审核、导出端点；[`batch_production.py`](../../backend/app/batch_production.py) 保存短视频变体版本、旧预览失效和批准后导出。 | **流程参考/Adapter 重写**。可采用分阶段触发语义，但不要迁入上游路由、任务 ID 和全流程 `start()`；需要把结果写回本地现有变体和冻结版本。 |
| [`task.py::_resolve_reusable_voice_preview`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L422-L481) 比较全文脚本、音色、语速、音量，限制音频在任务目录并检查文件/时长/字幕对象；[`webui/Main.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/webui/Main.py#L5635-L6259) 有短句试听、指纹和全文试听复用。 | 本地 [`local_voice.py`](../../backend/app/local_voice.py) 是离线 Qwen3-TTS 目录/样例/合成服务；[`batch_editing_api.py`](../../backend/app/batch_editing_api.py#L870) 已在任务内请求配音，并在 [`batch_production.py`](../../backend/app/batch_production.py#L378) 关联配音版本。现有音色样例是本地文件，不等于上游 Streamlit 动态试听。 | **可取材的最小逻辑**是“全文+音色+参数+版本指纹”和“只接受当前任务目录内已验证音频”；实现须按本地版本模型写一个小 Adapter。上游缓存字典是 WebUI 进程内载荷，明确不是公开 API 参数，不能由浏览器任意提交服务器文件路径。 |
| [`voice.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/voice.py#L1913-L2101)：`get_minimax_voice_catalog`、`minimax_tts`、验证音频落盘；另有 Azure、Edge、Gemini 等。 | 本地 Qwen 服务已可表示“未安装/不可用”，且离线 worker 与上游多供应商配置完全不同。 | **只有确定新增外部 TTS 供应商时**，才以对应供应商的目录解析、HTTP 错误处理、音频完整性检查作 Adapter 素材；保留本地供应商选择和凭据/外发确认。不能直接换掉 `local_voice` 或复制整个 3000+ 行 `voice.py`。 |
| [`task.py::generate_audio`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L482-L579) 优先探测写出的音频时长；[`voice.py::create_subtitle`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/voice.py#L3080-L3122) 从可用的语音边界或估算生成 SRT。 | 本地 [`timeline_render.py`](../../backend/app/timeline_render.py) 已有 FFmpeg/ffprobe 媒体探测、源区间限制、取消、临时输出与验证后原子发布；[`batch_editing_api.py`](../../backend/app/batch_editing_api.py#L691) 有字幕保存/识别。 | **校验原则可借鉴**，尤其实际音频时长优先；不能将字符比例估算误称逐词对齐，也不能替换本地字幕/渲染实现。 |
| [`material.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/material.py#L41-L156) 保留素材来源 URL/创作者信息，包含 Pexels、Pixabay、Coverr 检索；[`task.py::get_video_materials`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task.py#L648-L827) 选择素材。 | 本地 [`batch_recommendation.py`](../../backend/app/batch_recommendation.py) 已根据**用户项目素材**名称/备注给脚本段落做可解释匹配，并要求不足时显式报错。外部图库搜索会扩大素材来源及权利/授权范围。 | **P1 可选 Adapter**：只有产品明确需要外部素材时，再评估来源元数据结构、检索和授权记录。不要把库存素材“自动匹配”覆盖本地可解释推荐，或直接复用下载/发布整条链。 |
| [`state.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/state.py) 的 Memory/Redis 状态与 [`task_artifacts.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/task_artifacts.py) 的脚本文件。 | 本地 [`durable_runs.py`](../../backend/app/durable_runs.py) 冻结输入、限定运行状态与迟到结果，业务审批由 `batch_production` 持有。 | **禁止整体迁入**上游状态、Redis 管理器和任务产物目录，否则双写、重启恢复和审核版本可能冲突。仅取幂等/失败处理思路，最终结果由本地 `durable_runs` 与变体领域逻辑接收。 |
| [`video.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/video.py#L778-L1031) 的 MoviePy 合成、[`upload_post.py`](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/app/services/upload_post.py) 的社媒发布。 | 本地 FFmpeg 时间线和审批后交付已形成独立闭环，发布平台不在当前核心复刻/批量交付合同内。 | **不整体迁入**合成器、Streamlit 页面、自动跨平台发布。若出现经证实的缺失滤镜，可单独看算法，再用本地渲染契约实现。 |

**直接迁入、Adapter、排除的边界：** 当前阶段无值得“整文件直接复制”的 MPT 后端文件。MIT 下可函数级取材的候选是素材来源白名单、有序分配/去重规则、缓存键设计、试听指纹/参数核验和任务内音频路径核验；这些都需要本地类型、路径、安全边界和版本号适配。`voice.py`、`task.py`、`video.py`、`webui/Main.py`、Memory/Redis 状态、自动发布不能整体迁入。若实际复制了 MIT 源码的实质部分，应把上游 copyright 和 MIT 许可全文随分发保留，并记录固定提交和修改；自行重写行为规则与直接复制源码是两种不同的处理方式。[MPT 许可证](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/LICENSE)

**外部依赖/商业风险：** [README 的供应商清单](https://github.com/harry0703/MoneyPrinterTurbo/blob/d7b738345e2298d3e21738140190b8a6a68cd39f/README.md) 涵盖大模型、TTS、文生图/视频、图库和发布服务；代码 MIT 不赋予这些服务的额度、商用音色、模特/演员形象、图库素材或平台发布权限。`material.py` 的来源记录是审计线索，不自动证明每段素材可用于客户交付。本次没有调用 MiniMax/ElevenLabs，也没有重新执行本项目现有九音色试听验收；不能以 MPT 的供应商目录或 README 代替真实凭据、商用授权和本地验收。

### MPT 单项取材顺序

- **P0：只吸收编排规则，不引入供应商。** 将 MPT 的“脚本关键词有序分组、素材去重、跨变体减少重复、来源可追踪”重写为本地 `MontagePlanner` 规则，输入仍是本项目素材库与冻结版本，输出仍是可解释镜头建议。全文试听一致性、实际音频时长和版本失效先核对本地是否已覆盖，已覆盖则不复制。
- **P1：外部素材是独立 Adapter。** 只有产品明确需要库存素材时，才增加 `MaterialProvider` 接口并先接 Pexels/Pixabay；统一返回来源页、作者/资产 ID、搜索词、尺寸时长和授权快照，再进入本地素材库。缓存、临时签名 URL 和下载失败不能泄漏进变体/审核状态机。
- **P2：按已确认缺口增量接入。** 外部 TTS、AI 生成素材、特定转场或社交发布都要分别立项；每类供应商一个小 Adapter，保留凭据、费用、外发确认和远端任务恢复边界。不要一并安装全部上游依赖，也不引入整套 MoviePy 管线。

## 3. 其余七项对照

| 项目 | 真实技术栈与可取材处 | 依赖、许可与当前项目的取舍 |
| --- | --- | --- |
| **OpenShorts** | [README](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/README.md) 与 [Python 依赖](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/requirements.txt)：FastAPI、faster-whisper、SceneDetect、YOLO/MediaPipe、Gemini；[前端依赖](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/dashboard/package.json)：React 18/Vite、Remotion。可研究 [`clip_selection.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/clip_selection.py)、[`active_speaker.py`](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/active_speaker.py) 和轻量 UI，但主线是长视频转短片/UGC，与参考复刻及项目内批量变体不同。 | 自托管需要 Docker、模型下载与自备 AI Key；README 同时有“可选本地 LLM”及要求部分 AI 功能使用 Gemini 的说明，具体离线能力要按调用路径逐项核实。根 MIT 与 [`cloud/` 商业许可](https://github.com/mutonby/openshorts/blob/89f030934388d5167b3b0ede164ef893dad09ae9/cloud/LICENSE) 必须按目录分开；托管版收费不等于自托管代码收费。 |
| **ComfyStudio** | [README](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/README.md) 为 Python/PyQt6 桌面镜头管理、ComfyUI 工作流链、版本历史、多轨时间线/FFmpeg；[`setup.py`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/setup.py) 与 [`comfy_workflow_convert.py`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/comfy_workflow_convert.py) 可作为 ComfyUI 参数映射参考。 | MIT；需要运行中的 ComfyUI 与 ffmpeg。Qt 桌面 UI 不能直接嵌入本地 React Web；23 stars 和 7 月最后推送仅说明项目较小/近期无推送，不能单凭此推断代码不可用。 |
| **opensource-clipping** | [README](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/README.md)、[`pyproject.toml`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/pyproject.toml)：Python、Faster-Whisper、Gemini、MediaPipe、yt-dlp；[Web Studio](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/web/dashboard/package.json) 是 React 19/Vite。可研究 [`clipping/studio/face_detection.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/studio/face_detection.py) 和 [`subtitles.py`](https://github.com/NaufalRizqullah/opensource-clipping/blob/4a478dff3dacd293eeb5e28335aa7c81d78b8f50/clipping/studio/subtitles.py) 的长视频重构图。 | MIT，但 Gemini Key、可选 Pexels Key、Pyannote 模型协议、下载来源视频和社媒上传权限各自独立；README 推荐 GPU/Colab。侧重播客/长视频高光，非本地制作镜头或参考复刻核心。 |
| **OpenMontage** | [README](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/README.md) 与 [`pipeline_defs/clip-factory.yaml`](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/pipeline_defs/clip-factory.yaml)：Python 工具注册/阶段清单、Remotion/FFmpeg，可参考“生产阶段→审查门槛”的表达；[`tools/_comfyui/client.py`](https://github.com/calesthio/OpenMontage/blob/08e2151fa02de28a5d6a312b3d575692bf147ad7/tools/_comfyui/client.py) 也有 ComfyUI 接入线索。 | AGPL-3.0；供应商矩阵、开源模型、图库/音乐/API 各有独立条件。其 Agent 指令包和多流水线规模大，直接迁入会改写本项目架构与许可边界；可借鉴需求/门槛概念，不默认复制代码。 |
| **YumCut** | [README](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/README.md) 与 [`package.json`](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/package.json)：Next.js 16/React 19/Prisma、独立 daemon、Remotion/FFmpeg、账户积分与用户 API；可研究 [`src/shared/pipeline/project-pipeline.ts`](https://github.com/IgorShadurin/app.yumcut.com/blob/7e07eb356393c20014ab6badcf6e27c61a740781/src/shared/pipeline/project-pipeline.ts) 的阶段命名。 | PolyForm Noncommercial；README 要求客户托管/出售访问取得商业许可。与本地 React 18/FastAPI/项目审核模型差异大；商业代码复用候选先排除，除非取得独立授权。 |
| **short-video-maker** | [README](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/README.md) 与 [`package.json`](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/package.json)：Node/TypeScript、Kokoro TTS、Whisper.cpp、Pexels、Remotion，暴露 REST/MCP；[`ShortCreator.ts`](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/ShortCreator.ts) 是可参考的文本配音→字幕→库存素材→合成编排。 | MIT；Pexels Key/互联网、较高内存/磁盘、模型下载；README 写明当前仅英语配音且不根据图像/图像提示生成新画面。最后推送为 2025-06-21，迁入前需再看依赖兼容性。 |
| **ai-clipping-comfyui** | [README](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/README.md) 与 [`ai_clipping_nodes.py`](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/ai_clipping_nodes.py)：Python ComfyUI 自定义节点，提交视频 URL/本地文件到 muapi.ai，返回排名片段、时间范围/片段 URL；`return_coordinates_only` 可只取时间坐标。 | MIT 仅覆盖节点代码；[依赖](https://github.com/Anil-matcha/ai-clipping-comfyui/blob/d0a7b1943dfd1e144c9bfe656e57941623b6408d/requirements.txt) 含 torch/OpenCV，但核心识别/排名/裁切由外部 muapi.ai API 完成，需 Key、网络及视频外发。可参考 ComfyUI 节点 I/O 结构，不适合作为本地免费剪辑引擎。 |

## 4. 对本项目的最终组合建议

不是选择一个仓库作为新底座，而是保留本项目为唯一业务系统，只补两个深模块和两个窄适配层：

```text
项目素材 ProjectAssets
  ├─ 本地 ClipIndex（新增：场景、转录、人物/运动、可用区间）
  └─ 外部 MaterialProvider（可选：MPT 的图库检索/来源/缓存思路）
                 ↓
MontagePlanner（新增：MPT 的有序分配 + OpenShorts 的高光片段排序）
                 ↓
现有 BatchVariant / Timeline
                 ↓
现有配音、字幕、FFmpeg 预览、审核、导出
```

| 级别 | 项目/能力 | 决策与原因 |
| --- | --- | --- |
| **P0 加入** | **MPT 的素材来源结构、有序分配、去重/跨变体多样性思路** | 重写进新的 `MontagePlanner`，保留本地可解释推荐、版本冻结和审核；不复制 `task.py` 大流程。 |
| **P0 加入** | **OpenShorts 的片段窗口、候选裁剪和高光排序思路** | 用来建立 MPT 缺失的本地 `ClipIndex`；只取 MIT 根目录中经审计的纯逻辑，不迁入 `cloud/`、发布和整套应用。 |
| **P1 可选** | **MPT 的 Pexels/Pixabay Provider 与缓存/来源审计思路** | 只有确认需要外部库存素材后接入；下载结果先注册为项目素材，再进入规划，不允许 Provider 直接写时间线或交付状态。 |
| **P2 参考** | **ComfyStudio 的工作流参数映射与结果版本** | 当前 ADR 仍把最终 AI 生成放在外部；只有重开 ComfyUI 执行范围时做 `ComfyWorkflowAdapter`，不迁 Qt UI、时间线或桌面状态。 |
| **只参考** | **OpenMontage 的 QA 门槛、成本审批、回放记录** | AGPL 且 Agent-first 架构与本地确定性状态机不同；独立实现概念，不复制代码。 |
| **暂不加入** | opensource-clipping、short-video-maker、YumCut、ai-clipping-comfyui | 前两者与现有字幕/配音/渲染高度重叠；YumCut 非商业许可；ComfyUI 剪辑节点实际依赖视频外发的 muapi.ai。 |

**建议首个实施切片：** 先为现有 [`batch_recommendation.py`](../../backend/app/batch_recommendation.py) 增加独立 `ClipIndex` 输入和 `MontagePlanner` 输出合同，用 2～3 个真实长视频验证“场景切分→候选片段→脚本段落匹配→解释与手工改选”。这个切片直接改善本地自动选片，又不触碰已经稳定的配音、时间线、审核和导出链。外部图库 Provider 放在第二个切片，避免先集成 API 却仍无法理解用户自己的素材。

## 5. 尚未验证与易错说法

- 本次只做源码/元数据核验，没有运行八个候选；“可复用”指可进一步做函数级移植或行为参考，**不等于**已经通过本项目集成或真实模型验收。
- Star 快照不能推出维护承诺；仓库 `pushed_at` 也可能是文档/依赖更新。当前 `mutonby/openshorts` 和 `Warabellum/openshorts` 有 fork 关系，但旧文档所固定的 fork 提交不是本次上游提交。
- “OpenShorts 全仓 MIT”“YumCut MIT/可直接商用”“ai-clipping-comfyui 完整自托管 AI 剪辑”“MoneyPrinterTurbo 免费提供所有配音/素材/视频模型”均与所列许可证、源码调用或配置要求不符。
- 各仓库 README 的供应商价格、免费额度、模型支持、版权/社交平台条款会变，本次没有核验第三方实际服务条款或成本；接入时须以当时官方条款和本地真实调用为准。
