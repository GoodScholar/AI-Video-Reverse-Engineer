# GitHub 简化商品短视频流程调研

调研日期：2026-09-26。只读检索 GitHub 仓库、源码、许可证与官方文档；未克隆、安装、下载项目或修改产品代码。GitHub API 的默认分支树和 `pushed_at` 用于核验文件存在及维护状态；时间仅说明有提交，不能证明功能可靠。未运行上游程序，因此下文“可迁入”指源码和依赖层面的判断，不表示已经完成运行验证。

## 1. 结论与范围

目标流程：**商品资料/素材 → AI 生成 5 个脚本候选并由人工确认 → 9 个有描述的真实音色试听选择 → 正式配音与镜头字幕 → 预览审核导出**。

根代理已核对本地实际为 React 18.3.1 / TypeScript 5.7 / Vite 6.1 / FastAPI / FFmpeg；本地 MLX Qwen3-TTS 用于配音，脚本由工作台已配置并验证的分析服务生成（目前 ChatAnywhere `gpt-4o-mini`）。以此评估兼容性。数字人暂停。首期要减少切页、重复保存、手动时间线；A 制作 / B 精修 / C 批审原型已有批准，但不应把 DEV 原型等同正式入口。

**建议仍选择 OpenShorts 的轻量步骤组件作为可迁入源码；制作流程参考 MoneyPrinterTurbo；素材审核参考 Clip Architect；保留现有制作、音频、剪辑、审核后端。** 调研未发现同时满足上述完整商品流程、现有依赖和无新增安装条件的可直接嵌入整站。五脚本和九音色需要由本项目既有接口组织，不能用上游“一键生成”宣传替代功能证据。

## 2. 候选排名：共核验 11 个仓库

排名按本次任务的可用性，而非 stars。源码可读不等于可自由迁入；许可证以实际文件为准。

| 排名 | 仓库与一级来源 | 栈与可复用判断 | 许可证/维护风险 |
| --- | --- | --- | --- |
| 1 | [Warabellum/openshorts](https://github.com/Warabellum/openshorts) | React 18 + Vite；`StepIndicator.jsx` 仅依赖 React、Lucide、样式类，适合现有栈。**可迁入轻量步骤组件**，整站不迁入。 | [MIT，明确排除 cloud/](https://github.com/Warabellum/openshorts/blob/main/LICENSE)；fork，API 最近 push 2026-08-20。主题样式需映射。 |
| 2 | [harry0703/MoneyPrinterTurbo](https://github.com/harry0703/MoneyPrinterTurbo) | FastAPI + Python + Streamlit；分阶段音频、字幕、视频与试听复用规则最贴近。**后端缺口候选/流程参考**，优先复用本地已有实现。 | [MIT](https://github.com/harry0703/MoneyPrinterTurbo/blob/main/LICENSE)；最近 push 2026-09-24；模块混合多供应商和自动发布，整套迁入会扩大范围。 |
| 3 | [OpenCut-app/opencut-classic](https://github.com/OpenCut-app/opencut-classic) | Next 16 + React 19；纯 TS 字幕解析可小范围迁入；预览和导出与 EditorCore 耦合，**不直接搬编辑器**。 | [MIT](https://github.com/OpenCut-app/opencut-classic/blob/main/LICENSE)；已归档，最近 push 2026-05-17，无持续维护保障。 |
| 4 | [diflowrin/Clip-Architect](https://github.com/diflowrin/Clip-Architect) | React 19 + Tauri 2 + Python FastAPI；先试听配音，再审素材，最后渲染。**最贴近交互参考**，不能作为当前许可下的默认代码捐赠库。 | [GPL-3.0](https://github.com/diflowrin/Clip-Architect/blob/main/LICENSE)；另有[商业授权说明](https://github.com/diflowrin/Clip-Architect/blob/main/COMMERCIAL-LICENSE.md)，该说明本身不授予许可；最近 push 2026-09-06。 |
| 5 | [linyqh/NarratoAI](https://github.com/linyqh/NarratoAI) | Python/Streamlit；有真实 TTS 试听和可编辑镜头脚本，适合参考音色播放和脚本校验。**不迁入其长参数表单**。 | [MIT](https://github.com/linyqh/NarratoAI/blob/main/LICENSE)；最近 push 2026-09-17；短剧/影视解说假设较多，多 TTS 引擎配置会增加用户负担。 |
| 6 | [FujiwaraChoki/MoneyPrinter](https://github.com/FujiwaraChoki/MoneyPrinter) | Flask + 静态 HTML/JS + MoviePy，当前[官方运行文档](https://github.com/FujiwaraChoki/MoneyPrinter/blob/main/docs/docker.md)是 Ollama 优先、工作队列；**任务状态交互参考**。 | [MIT](https://github.com/FujiwaraChoki/MoneyPrinter/blob/main/LICENSE)；最近 push 2026-03-26。换 Flask/Ollama/数据库没有本次价值。 |
| 7 | [RayVentura/ShortGPT](https://github.com/RayVentura/ShortGPT) | Python/Gradio/MoviePy；[voice_module.py](https://github.com/RayVentura/ShortGPT/blob/stable/shortGPT/audio/voice_module.py)提供配音模块边界；**架构参考**。 | [MIT](https://github.com/RayVentura/ShortGPT/blob/stable/LICENSE)；默认分支 stable，最近 push 2025-02-10；旧依赖和实验框架的迁入成本较大。 |
| 8 | [reactvideoeditor/free-react-video-editor](https://github.com/reactvideoeditor/free-react-video-editor) | React 18 + Next + Remotion；版本贴近，但完整时间线并不减少本次手动操作。**界面参考**。 | [LICENSE.md 为 MIT](https://github.com/reactvideoeditor/free-react-video-editor/blob/main/LICENSE.md)，版权主体仍是占位符；[README](https://github.com/reactvideoeditor/free-react-video-editor)却声明 RVE 商业授权，存在相互不一致，不能据仓库摘要认定已解决许可；最近 push 2025-02-15。 |
| 9 | [CapSoftware/Cap](https://github.com/CapSoftware/Cap) | 屏幕录制/分享产品，React Web、Solid/Tauri/Rust 桌面等多栈；**成片预览/导出交互参考**，商品脚本与配音流程缺失。 | [LICENSE](https://github.com/CapSoftware/Cap/blob/main/LICENSE)：仅 `cap-camera*`/`scap-*` crates 属 MIT，其余默认 AGPLv3；活跃但不能把 UI 当作 MIT 迁入。 |
| 10 | [openvideodev/react-video-editor](https://github.com/openvideodev/react-video-editor) | Next/React/Remotion，媒体面板和时间线完善；**只作交互参考**，整站加重本次流程。 | 当前[OpenVideo License](https://github.com/openvideodev/react-video-editor/blob/main/LICENSE)是自定义许可证：个人/≤3员工营利机构等可免费，禁止销售/出租/许可其编辑器衍生品；不能沿用过去 AGPL 或 MIT 判断。最近 push 2026-06-30。 |
| 11 | [OpenCut-app/OpenCut](https://github.com/OpenCut-app/OpenCut) | 主仓库重写为 Rust core、多平台、Editor API；**观察名单**。 | [MIT](https://github.com/OpenCut-app/OpenCut/blob/main/LICENSE)；[官方状态](https://github.com/OpenCut-app/OpenCut#status)明确重写中，推荐当前使用 classic；不宜把规划中的 headless/MCP 当成已交付接口。 |

另检索了 Fillo，但搜索结果指向表单填写扩展，未核实到对应视频项目；不把同名无关仓库计入 11 个视频候选。若后续提供准确 owner/repo 再核对。

## 3. 深入源码：可迁入和交互参考的边界

### 3.1 OpenShorts：本次 UI 源码第一选择

固定核验提交：`dc22ac715710260d59b02b61a2ce877dda8d9d95`。

- [dashboard/src/components/ui/StepIndicator.jsx](https://github.com/Warabellum/openshorts/blob/dc22ac715710260d59b02b61a2ce877dda8d9d95/dashboard/src/components/ui/StepIndicator.jsx)：接口为 `steps: string[]`、`current`、`onStepClick(index)`，只允许已完成步骤回看，包含 `aria-current="step"`。没有业务请求、存储和 Remotion 依赖。
- [dashboard/package.json](https://github.com/Warabellum/openshorts/blob/dc22ac715710260d59b02b61a2ce877dda8d9d95/dashboard/package.json)：React 18.2、Vite 4、Lucide。轻组件可适配本地 React 18.3；整站还含 Remotion，不能按“无新增安装”整体复用。
- [LICENSE](https://github.com/Warabellum/openshorts/blob/dc22ac715710260d59b02b61a2ce877dda8d9d95/LICENSE)：保留 MIT 通知；不得把 `cloud/` 一起复制。

**选用理由（本次对比判断）：** 比 Clip Architect 的大制作页、OpenCut 的全编辑器、NarratoAI 的参数表更独立。按父设计适配为同一制作页的四阶段定位提示；切换阶段保留同一项目状态。原组件并不实现自动保存、请求顺序、失效处理、五候选或九音色，这些必须由本地领域状态负责。样式中的 brass/rule/ink 等类需要映射现有 CSS，不能裸复制后宣称已经完成交互。

### 3.2 MoneyPrinterTurbo：分阶段制作与试听一致性

固定核验提交：`ad5496f1b729d1d7e361dd972015d26c08b0e052`。

| 真实文件 | 源码证据与本次用途 | 迁入判断 |
| --- | --- | --- |
| [app/controllers/v1/video.py](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/app/controllers/v1/video.py) | `/audio`、`/subtitle`、`/videos` 分别启动制作任务；另有任务查询、stream、download。说明先做音频再做成片无需整站切换。上述为路由文件内部路径，实际前缀需核对其路由装配。 | 与 FastAPI 同栈；本地已有对应接口时调用本地接口，避免并列引入第二套任务管理。 |
| [app/services/task.py](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/app/services/task.py) | `generate_audio`、`generate_subtitle`、`generate_final_videos`；`start(..., stop_at=...)` 支持停在脚本/词/音频/字幕/素材阶段。 | 可研究缺口函数；整文件包含自动发布和供应商依赖，不适合整文件迁入。 |
| [同文件 _resolve_reusable_voice_preview](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/app/services/task.py#L422) | 核对完整脚本、音色、速度、音量、任务内文件、有效时长及字幕对象；不一致则重新 TTS。 | 推荐复用其一致性规则，确保试听与正式配音使用同一真实声音。它是 WebUI 进程内缓存，**不是公开 API 参数**；不可照抄成前端可传任意本地路径。 |
| [批量候选模板](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/docs/loomloom/moneyprinterturbo-script-candidates.template.json) | 每个候选传 `subject`、`requirements`、`candidateIndex`，返回 JSON `script` 与 `videoTerms`。 | 可借鉴提示词与结果校验，接既有分析服务脚本生成和 handoff 契约；模板本身不保证恰好五个、商品事实正确或人工确认，也不必引入 LoomLoom。 |
| [app/services/voice.py](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/app/services/voice.py) | 包含音色目录、TTS、字幕生成；不是九音色卡片组件。 | 多供应商大模块，作为接口参考；不能为复用它安装全部 TTS 依赖或新模型。 |

许可是 MIT，但项目素材、字体、音乐及外部供应商条款各自独立。此处仅推荐代码边界，不建议迁入随仓库附带的媒体素材。

### 3.3 Clip Architect：最贴近的审核交互，代码受 GPL 约束

固定核验提交：`156a37ff8bc63fa8c8f3f9aa8605d46c2e00d677`。

- [src/components/MaterialReview.tsx](https://github.com/diflowrin/Clip-Architect/blob/156a37ff8bc63fa8c8f3f9aa8605d46c2e00d677/src/components/MaterialReview.tsx)：用镜头列表预览、排序、移除/恢复、替换文件、重新搜索；`onRender(materialPaths, keepOrder)` 后才提交渲染。接口还依赖 `BackendApi`、`TaskStatus` 等项目类型。源码特意区分上传素材和脚本搜索匹配，避免把不存在的镜头文案关系当事实。
- [src/views/GenerateView.tsx](https://github.com/diflowrin/Clip-Architect/blob/156a37ff8bc63fa8c8f3f9aa8605d46c2e00d677/src/views/GenerateView.tsx#L827)：`handleGenerateVoice` 调音频任务、轮询、验证返回音频文件并展示时长；脚本/音色参数变化时清空旧试听。该大页面还引入 Tauri opener，因此不是可直接放进 Vite 浏览器项目的一页组件。
- [src/components/SubtitlePreview.tsx](https://github.com/diflowrin/Clip-Architect/blob/156a37ff8bc63fa8c8f3f9aa8605d46c2e00d677/src/components/SubtitlePreview.tsx)：按输出比例显示字幕样式，并加载后端字体；样式演示不能代替 FFmpeg 成片验收。

**本次选择：** 参考“配音试听 → 镜头列表审核 → 渲染”的顺序和每项就近报错；默认画面由现有自动编排产生，只在 B 精修中换镜头/改字幕，不要求创作者逐个摆时间线。GPL 允许制作商业视频，但复制前端进入再分发产品涉及 GPL 源码义务；作者商业授权摘要并未授予闭源嵌入权。因此首期不直接迁入其 React 代码。其 vendored MoneyPrinterTurbo 后端应优先回到 MIT 上游取材，而不是从 GPL 前端包绕过限制。

### 3.4 OpenCut classic：只取低耦合工具函数

固定核验提交：`cf5e79e919144200294fb9fed22a222592a0aeea`。

- [apps/web/src/subtitles/srt.ts](https://github.com/OpenCut-app/opencut-classic/blob/cf5e79e919144200294fb9fed22a222592a0aeea/apps/web/src/subtitles/srt.ts)：`parseSrt({input})` 返回 `captions`、`skippedCueCount`、`warnings`；支持 CRLF 和逗号/点毫秒，跳过无效或非正时长字幕。没有运行时包导入。
- [subtitles/types.ts](https://github.com/OpenCut-app/opencut-classic/blob/cf5e79e919144200294fb9fed22a222592a0aeea/apps/web/src/subtitles/types.ts) 仍引用其 text/transcription 类型；迁入解析时需适配到本地字幕 cue 类型，不能拖入整套类型树。若本地已经解析 SRT 则继续复用本地函数。
- [components/editor/export-button.tsx](https://github.com/OpenCut-app/opencut-classic/blob/cf5e79e919144200294fb9fed22a222592a0aeea/apps/web/src/components/editor/export-button.tsx) 含导出进度与下载界面，但依赖 `useEditor`、导出选项、UI 组件和浏览器渲染核心。只参考导出结果反馈。
- [apps/web/package.json](https://github.com/OpenCut-app/opencut-classic/blob/cf5e79e919144200294fb9fed22a222592a0aeea/apps/web/package.json) 为 React 19 / Next 16，还有 mediabunny、WASM 等；不符合整体无新增安装约束。

**本次选择：** MIT 纯工具函数可作为真实缺口的补充；全编辑器不迁入，字幕时长必须由现有真实音频/字幕生产链返回。

### 3.5 NarratoAI：试听行为可借鉴，供应商设置收起

核验提交：`9fa69e022d4add41205ee385207561df8796b3f1`。[webui/components/audio_settings.py](https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/webui/components/audio_settings.py#L1947) 的 `render_voice_preview_new` 先按所选引擎取真实音色参数，执行 `voice.tts`，只在音频文件存在时播放；不同引擎输出 WAV/MP3。它会播放后删临时文件，不适合作为跨页面长期试听缓存原样迁入。代码支持多引擎不等于本地已有可用模型，更不等于已有九种声音。

## 4. 从调研落到本项目的最小实现边界

以下是方案推断，非上游现成商品功能。

| 用户阶段 | 优先复用 | 少量必要适配 | 成功判据 |
| --- | --- | --- | --- |
| 商品资料/素材 | 本地创作简报、素材上传和项目保存 | 同一制作页收集事实与素材；高级技术设置默认折叠 | 不要求重复创建项目、复制资料或切去资源页才能继续 |
| 5 个脚本 | 工作台已配置且验证的分析服务、脚本候选与人工确认状态；MPT 候选模板作参考 | 保持既有 handoff 契约，以固定 5 个不同角度呈现可改卡片，确认后沿用候选 ID | 五个真实候选；未人工确认不启动正式配音，不扩大为任意 1–5 数量 |
| 9 音色 | 本地已可用的真实 TTS 音色目录/试听接口；NarratoAI 的真实文件检查；MPT 缓存一致性规则 | 9 张卡片展示声音描述与同一句短样试听，保存 provider/voice ID | 九张卡绑定九个可验证真实音色；不能用同一默认声音或浏览器占位合成冒充 |
| 配音与镜头字幕 | 本地真实配音、音频时长、镜头编排、字幕、FFmpeg | 一次操作使用已确认脚本与所选音色；用镜头摘要代替默认时间线 | 声音、字幕、镜头时长对应实际生成音频；无需手动排列全部镜头 |
| 预览审核导出 | 本地播放器、批审状态和导出；Clip Architect 列表审核交互作参考 | 可预览、退回修改、确认；进入 B 精修/C 批审时保留同一变体 | 预览真实 MP4；审核状态可追溯；导出保留所选版本 |

步骤组件只负责显示位置。保存、生成、确认必须由一个项目/短视频变体状态贯穿，不能每一步再复制一份草稿。修改脚本后应使旧配音和下游成片失效；修改音色后使旧试听/配音失效；回看未修改内容则保留已有有效结果。默认阶段切换无需独立“保存并下一步”两次点击，具体持久化策略沿用本地已存在接口。

**九音色的必要输入仍需本地核对：** 可用 TTS 服务、九个真实 ID、试听能否成功、正式生成是否使用相同服务和参数。开源项目的目录或 UI 无法提供缺失的本地音色模型；在“不安装/下载”约束下不得承诺未配置服务已可用。

## 5. 迁入清单与排除项

1. **首选迁入：** OpenShorts `StepIndicator.jsx`，改为本地 TSX/样式，保留 MIT 与来源记录；不引入 `cloud/`、Remotion 或整站依赖。
2. **存在真实缺口才迁入：** OpenCut classic `subtitles/srt.ts` 等纯函数，适配既有类型；本地相同功能直接复用。
3. **先参考再决定：** MPT 候选提示词、试听参数核验、分阶段任务规则；保留本地 FastAPI 路由和 FFmpeg 管线。复制具体 MIT 片段应记录固定提交和通知，不能另起同功能后端。
4. **只参考交互：** Clip Architect 配音/素材审核，Cap 预览导出，OpenVideo 媒体面板。当前许可、栈和范围不支持默认直接迁入。

本次证据足以选择轻量步骤组件与制作顺序；尚未完成上游运行兼容、真实九音色与本地适配验收。下一次实施应仅验证受影响的真实制作路径，不因调研引入新框架、桌面壳、自动发布、数字人或通用专业时间线。

## 6. 视觉结构：让内容成为主角，减少长表单

补充依据：父代理实查现有两段视频、两句配音的 BatchEditor 页面高约 4925px，33 个按钮、52 个字段，其中 33 个数值字段，AI 表单还需另展开。这个观察来自本地审计；下面的上游观察来自**实际源码结构**，本次没有打开上游运行界面或观看仓库截图，因此不将其称为已验证的视觉效果或“高端”设计。

| 视觉区域 | 查过的源码证据 | 本次推荐结构（设计推断） |
| --- | --- | --- |
| 真实素材缩略图 | [Clip Architect MaterialReview.tsx](https://github.com/diflowrin/Clip-Architect/blob/156a37ff8bc63fa8c8f3f9aa8605d46c2e00d677/src/components/MaterialReview.tsx#L525) 的 video 同时接 `src` 与后端 `poster`，可放大播放，邻近显示片段名称/文案 | 上传后立即显示真实首帧与素材数量，使用本地既有缩略图/媒体接口。缺少缩略图时显示素材类型和加载状态，不能以无关商品示意图冒充真实素材。 |
| 脚本与预览联动 | [SubtitlePreview.tsx](https://github.com/diflowrin/Clip-Architect/blob/156a37ff8bc63fa8c8f3f9aa8605d46c2e00d677/src/components/SubtitlePreview.tsx#L132) 随 params/sample 改比例、字幕位置/字体；MaterialReview 可相邻显示镜头文案 | 制作页左侧为当前阶段，右侧保留竖屏预览与当前脚本摘要。选候选时立即显示对应镜头摘要；真实 MP4 尚未生成时标注“脚本预览”，不能把字幕示意帧当成品。 |
| 音色卡片 | [NarratoAI audio_settings.py](https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/webui/components/audio_settings.py#L1947) 核验真实 TTS 产物；[MPT voice.py](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/app/services/voice.py) 提供目录和合成，均未提供符合需求的九卡 React 组件 | 九张卡采用 3×3 布局：音色名、一行听感描述、适合场景、单一试听按钮、选中态。共用同一句试听文本和一个 audio 播放器，新的试听停止前一段。这里是本项目所需的薄 UI 适配，不能宣称从上游搬到了现成九音色卡。 |
| 成片墙 | 新核验 [OpenShorts GalleryCard.jsx](https://github.com/Warabellum/openshorts/blob/dc22ac715710260d59b02b61a2ce877dda8d9d95/dashboard/src/components/GalleryCard.jsx)：仅 React/Lucide 运行时导入，9:16 video、可视区域懒加载、标题和下载；[Gallery.jsx](https://github.com/Warabellum/openshorts/blob/dc22ac715710260d59b02b61a2ce877dda8d9d95/dashboard/src/components/Gallery.jsx) 是分页请求网格 | 采用真实成片卡网格，展示封面、脚本角度、时长、审核状态。点击一张切换右侧预览；批量审核/导出放在统一操作栏。布局应随比例变化，不能强制横屏视频全部裁成竖屏。 |

### 最推荐的小组件组合

**OpenShorts StepIndicator + GalleryCard 的展示与懒加载部分 + 本地已有媒体预览/音频播放器/脚本编辑器。** 两个上游组件同在 `cloud/` 之外，适用 MIT；均无需引入 Remotion。迁入时保留来源通知，映射本地 CSS、clip/variant 类型；GalleryCard 的下载逻辑、社交平台文案与复制按钮应改接现有导出和审核流程，避免带回额外按钮。其 [Gallery.jsx](https://github.com/Warabellum/openshorts/blob/dc22ac715710260d59b02b61a2ce877dda8d9d95/dashboard/src/components/Gallery.jsx) 绑定上游 `/api/gallery/clips`，不可照搬为第二套列表接口；[ResultCard.jsx](https://github.com/Warabellum/openshorts/blob/dc22ac715710260d59b02b61a2ce877dda8d9d95/dashboard/src/components/ResultCard.jsx) 依赖认证、字幕弹窗、Remotion、社交发布，不选择它。

以下视觉取舍是建议，不是源码验收结果：

- 用真实商品画面、竖屏预览和成片卡提供辨识度；集中一处强调色、明确字号层级、充足留白，避免每个控制项都做抢眼按钮。
- 制作页每阶段一个主操作，字段随步骤逐步展开；速度、字幕像素、混音数值等放进高级设置或 B 精修，默认制作不显示 33 个数值字段。
- 明确突出“5 个角度可确认”“9 种真实声音可试听”“同一商品批量出片”三项能力，让用户首先看到可制作的内容和结果。
- 制作、精修、批审继续围绕同一项目和短视频变体；预览与进度固定在可见区域，减少切页和反复向下滚动。是否达到高端感仍需在实际页面用真实素材进行视觉验收。
