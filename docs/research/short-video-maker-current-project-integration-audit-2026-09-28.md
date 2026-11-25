# short-video-maker 固定提交源码深审与当前项目融合判断

审计日期：2026-09-28。基准为 [`gyoridavid/short-video-maker@9bb9a212ced86caa7e09099c382da1a44d638760`](https://github.com/gyoridavid/short-video-maker/commit/9bb9a212ced86caa7e09099c382da1a44d638760)，本地检出 `/tmp/short-video-maker-audit.tEW4rw/short-video-maker` 经 `git rev-parse HEAD` 核实同一 SHA；上游 `package.json` 版本为 1.3.4。以下上游文件链接均钉在该提交。本文只作源码和当前工作树的融合审计，未接入代码、安装模型、启动容器或验证视频质量。累计决策入口见[参考项目累计融合决策登记表](./reference-project-integration-register.md)，术语及边界见[领域词汇](../../CONTEXT.md)、[ADR 0007](../adr/0007-preproduction-with-shot-steps.md)、[ADR 0009](../adr/0009-content-project-and-workflow-boundaries.md)。

**总判定：只参考设计；当前无可直接、迁移后或部分复制的上游代码。** 上游围绕输入文字与 Pexels 搜索词自动配音、找库存视频并合成单条成片；当前权威对象是 `ContentProject`、`ProjectAssets`、`Scene`、`Shot`、`ShotResultVersion`，另有 Timeline、Python FFmpeg、`durable_runs` 和审核/交付版本。引入上游整套 TypeScript/Remotion/进程内队列会形成第二份项目、素材、运行、渲染与审核事实。其库存视频搜索思路也已在 [MoneyPrinterTurbo 深审](./moneyprinterturbo-current-project-integration-audit-2026-09-28.md) 中有更完整的来源记录与缓存候选；本报告不叠加同一能力。[上游流水线](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/ShortCreator.ts#L89-L218)、[本地素材边界](../../backend/app/project_assets.py)、[本地时间线渲染](../../backend/app/timeline_render.py)。

## 1. 参考项目核心能力

用户填写每段 `text` 与 `searchTerms`，选择音色、音乐情绪、字幕位置/底色和横竖屏；服务端用 Kokoro 生成旁白、Whisper.cpp 得到字幕、Pexels 选择并下载背景视频，Remotion 输出 MP4。它确实产生视频，但 README 明确说不从图片或提示词生成新画面。[请求模型](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/types/shorts.ts#L33-L40)、[核心实现](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/ShortCreator.ts#L106-L212)、[README](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/README.md#L7-L17)。

## 2. 当前项目核心能力

内容项目统一持有项目素材、场景分组和制作镜头，镜头结果候选由稳定素材 ID 关联；批量混剪以独立短视频变体保存脚本、配音、字幕、时间线、预览、审核及交付版本。Timeline 的 Python FFmpeg 渲染会检查来源、取消信号和输出媒体，验证后原子发布；`durable_runs` 管理冻结输入及迟到结果。最终 AI 视频生成提交受 ADR 0007 限制在外部工具，应用内接受外部结果候选。[领域词汇](../../CONTEXT.md)、[镜头领域](../../backend/app/shot_production.py)、[批量版本](../../backend/app/batch_production.py)、[渲染](../../backend/app/timeline_render.py)、[ADR 0007](../adr/0007-preproduction-with-shot-steps.md)。

## 3. 产品定位差异

上游面向“给文字和关键词、快速得到无真人出镜短片”的创作者或外部 Agent；其 Pexels 素材被自动选择，用户不能在同一模型中维护项目素材/镜头候选及审核。当前项目面向参考复刻准备与内部团队的批量混剪，重点是可追溯来源、编辑、人工审核和交付。上游 `scene` 是一次渲染的文字/搜索词项，不等于本地 `Scene` 场景分组或 `Shot` 制作镜头。[上游类型](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/types/shorts.ts#L24-L41)、[本地领域词汇](../../CONTEXT.md)。

## 4. 功能模块对比

| 模块 | 上游固定提交实际实现 | 当前项目与处理标签 |
| --- | --- | --- |
| 创建 | React/MUI 表单，可加删文字场景、手填逗号分隔搜索词，提交 `POST /api/short-video`；没有脚本生成器 [源码](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/ui/pages/VideoCreator.tsx#L38-L135) | 当前已有内容项目、批量脚本/变体；简表单仅**只参考设计** |
| 库存素材 | Pexels 搜索、时长与目标分辨率过滤、随机选一条、按视频 ID 排除重复；搜索失败会试 `nature/globe/space/ocean` [源码](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/libraries/Pexels.ts#L6-L183) | 本地项目素材/匹配为权威；对外图库若立项，应先沿 MPT Provider 方案，当前**只参考设计** |
| 旁白/字幕 | Kokoro CPU TTS，FFmpeg 转 16k 单声道，Whisper token 时间戳再渲染高亮 [流水线](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/ShortCreator.ts#L109-L140)、[字幕](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/libraries/Whisper.ts#L47-L91) | 已有 `local_voice`、`batch_subtitles`、识别/配音 API；**不引入**第二管线 |
| 合成 | Remotion 25fps 横/竖屏、场景视频、旁白、音乐和逐词高亮 [组件](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/components/videos/PortraitVideo.tsx#L18-L160) | 已有可编辑 Timeline + FFmpeg；高亮视觉样式**只参考设计**，渲染器**不引入** |
| 入口与结果 | REST 创建/状态/列表/读取/删除、MCP SSE `create-short-video` 和 `get-video-status` [REST](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/server/routers/rest.ts#L30-L224)、[MCP](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/server/routers/mcp.ts#L32-L97) | 本地 FastAPI 路由与版本化结果仍是权威；上游 API/MCP **不引入** |

## 5. 业务逻辑对比

上游直接 `addToQueue`，逐场景顺序处理，选定一个背景视频后合成；没有项目级素材归属、镜头采用、预览审核或交付授权。`paddingBack` 已在末场景 `audioLength` 加入，该长度随即计入 `totalDuration`，随后 `totalDuration` 又加一次，构成总时长双计。竖屏组件把前 `i+1` 个场景累计时长作为第 `i` 个 `Sequence.durationInFrames`，而非本场景长度；`i>0` 时会使镜头重叠/超长，末场景再追加一次 padding；横屏同构。这些是源码可定位的时长错误，不应把它作为本地时间线算法来源。[场景处理](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/ShortCreator.ts#L118-L121)、[总时长](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/ShortCreator.ts#L186-L211)、[竖屏 Sequence](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/components/videos/PortraitVideo.tsx#L72-L91)、[横屏组件](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/components/videos/LandscapeVideo.tsx)。本地审核仅允许当前已批准预览导出，[批量生产规则](../../backend/app/batch_production.py)。

## 6. UI / UX 对比

上游只有创建、视频列表、详情三页：创建页按场景填文字/关键词，详情页每 5 秒轮询，成片可播放/下载；列表首次加载获取 MP4，删除入口直接删文件。它没有镜头级预览修改、候选对照、时间线编辑、审批或失败原因展示。UI 文案为英语，非中文产品界面。当前已有 `PreproductionWorkspace`、`ShotResultVersions`、`TimelineEditor`、批量编辑与审核；如取“一页快速提交”交互，需服从当前设计系统及现有版本边界。[创建页](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/ui/pages/VideoCreator.tsx#L108-L224)、[列表页](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/ui/pages/VideoList.tsx#L22-L65)、[详情页](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/ui/pages/VideoDetails.tsx#L26-L77)、[本地镜头界面](../../frontend/src/ShotResultVersions.tsx)。

## 7. 技术架构与运行边界

Node 22、Express、React/MUI、Zod、Kokoro.js、Whisper.cpp、Pexels API、Remotion/Chromium 和 FFmpeg；服务启动先初始化并可能下载模型/浏览器，非 Docker 首次还会联网试 Pexels 与测试渲染。`CONCURRENCY` 是 Remotion 单次渲染浏览器 tab 数，不是业务作业并发；业务队列在一个 `ShortCreator` 实例内串行。Docker 有 tiny/base/CUDA 三版，构建期克隆 Whisper.cpp v1.7.1 并下载 `.en` 模型；Compose 没有数据卷声明，容器层数据持久性需由部署者另处理。[依赖](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/package.json#L46-L99)、[启动](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/index.ts#L16-L89)、[配置](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/config.ts#L29-L109)、[Docker](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/main.Dockerfile#L1-L87)、[Compose](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/docker-compose.yml#L1-L14)。

## 8. 数据模型、队列与持久化

`SceneInput{text,searchTerms}`、`RenderConfig` 和输出用 `Scene{captions,video,audio}` 都是渲染参数，不含项目 ID、资产来源页/授权、镜头 ID、修订、审核 ID；Zod 的 `text`、`searchTerms`、`scenes` 均未设 `min/max`，空数组和无界大输入也可通过结构校验。`queue` 是没有容量上限的进程内数组；`status` 仅由“仍在数组内/MP4 是否存在”推导 `processing/ready/failed`，列表扫描 MP4 和当前数组，故失败任务从列表消失，重启后排队与运行输入丢失，也没有可靠恢复。异常只记录日志，`finally` 移走条目；无取消端点，删除已存在文件也不停止运行。缓存设置只对应 Remotion `OffthreadVideo` 帧缓存，不是可恢复的下载/任务缓存。临时 WAV/MP3/MP4 只在成功渲染后删除；下载流非 200 时未统一清理，失败还可能残留文件。[类型/校验](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/types/shorts.ts#L24-L40)、[请求结构](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/types/shorts.ts#L145-L149)、[状态/队列/文件](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/ShortCreator.ts#L27-L86)、[临时文件](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/ShortCreator.ts#L123-L218)、[列表](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/ShortCreator.ts#L257-L291)、[本地持久运行](../../backend/app/durable_runs.py)。

## 9. 当前项目已有优势

本地 `ProjectAssets` 用项目 ID 和稳定素材 ID 管理文件、可用性与引用；`ShotResultVersion`、Timeline 与批量变体分别保存版本/失效规则；本地渲染支持取消、媒体校验及原子发布，预览批准后才可导出。上游只有“输出 MP4 即 ready”的事实，无法替换这些边界。[项目素材](../../backend/app/project_assets.py)、[镜头候选](../../backend/app/shot_production.py)、[渲染](../../backend/app/timeline_render.py)、[批量审批](../../backend/app/batch_production.py)。

## 10. 参考项目优势与真实性边界

优势是低配置的文本→库存短片闭环、同一服务同时给 REST 与 MCP、内置情绪音乐和逐词高亮。但“批量队列”实际仅可提交多个请求、单实例串行执行；没有批次对象、队列容量/并发保护、持久运行记录或恢复。“任务历史”是文件扫描；没有自动发布、图像生成、本地视频上传或从本地素材语义选片。中文也未实证支持：README 明确 Kokoro-js 只支持英语，默认 `medium.en`、Docker `tiny.en/base.en/medium.en`，虽然类型允许非 `.en` Whisper 模型，也不能据此称整条中文流水线可用。[README FAQ](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/README.md#L449-L480)、[队列](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/ShortCreator.ts#L27-L86)、[配置](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/config.ts#L10-L11)、[Docker 模型](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/main-tiny.Dockerfile#L13-L17)。

## 11. 功能映射与累计去重

| 需求/能力 | 已有或其他项目的更适合来源 | short-video-maker 增量判断 |
| --- | --- | --- |
| 外部库存视频搜索/来源 | [MPT](./moneyprinterturbo-current-project-integration-audit-2026-09-28.md) 已核对 Pexels/Pixabay/Coverr、来源记录及缓存；本地 [`ProjectAssets`](../../backend/app/project_assets.py) 是所有权入口 | 上游 Pexels 只返回 ID/URL/尺寸、严格等宽高、随机回退；**只参考设计**，不再列第二份 Provider 代码 |
| 配音、字幕、合成 | 本地 [`local_voice.py`](../../backend/app/local_voice.py)、[`batch_subtitles.py`](../../backend/app/batch_subtitles.py)、[`timeline_render.py`](../../backend/app/timeline_render.py)；MPT 已评估多供应商/配音指纹 | Kokoro/Whisper/Remotion 链与现有实现重复，且英语优先；**不引入** |
| 长视频候选与构图 | [OpenShorts](./openshorts-current-project-integration-audit-2026-09-28.md)、[opensource-clipping](./opensource-clipping-current-project-integration-audit-2026-09-28.md) 已审计片段/构图；本地已有镜头处理 | 上游没有此能力；**不引入** |
| 镜头/工作流外部生成 | [ComfyStudio](./comfystudio-current-project-integration-audit-2026-09-28.md) 偏桌面镜头/ComfyUI；[OpenMontage](./openmontage-current-project-integration-audit-2026-09-28.md) 偏 Agent 阶段；[YumCut](./yumcut-current-project-integration-audit-2026-09-28.md) 偏内容生成/发布 | 上游没有镜头结果版本或外部 ComfyUI 交接；**不引入**其单次成片对象。OpenMontage AGPL、YumCut PolyForm Noncommercial 不能因上游 MIT 而间接复制 |
| 轻量创建与结果反馈 | 本地批量混剪工作区已有任务反馈，上游三页表单有简洁性 | **只参考设计**：在现有版本化 UI 中呈现阶段与产物，不能直接粘贴页面 |

## 12. 直接复用

**无。** 固定提交上游文件 → 当前目标文件、依赖、修改、影响、测试及许可的直接复制条目：**无**。组件绑定 Remotion；服务绑定 Node、Pexels、Kokoro、Whisper、临时 URL；本地目标是 Python/FastAPI 与项目版本对象。即使根 MIT 允许在保留声明条件下复制，也不意味着有合适的直接复用点。[上游依赖](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/package.json#L46-L79)、[根许可](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/LICENSE#L1-L20)。

## 13. 迁移后适配

**无当前建议复制条目。** `Pexels.ts` → 当前可选素材 Provider 的想法已由 MPT 的多来源、来源记录方案覆盖；若未来真正决定引入 Pexels，目标可为 [`project_assets.py`](../../backend/app/project_assets.py) 边界旁的新 Python Provider，而不是把 TypeScript 文件迁入。届时需重新实现来源 ID、来源页/作者、授权快照、下载检验、路径/配额/失败收敛，接入前测试搜索失败、重复、无符合时长、权限与来源记录；Pexels API/素材许可独查。[上游搜索](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/libraries/Pexels.ts#L14-L183)、[MPT 对应结论](./moneyprinterturbo-current-project-integration-audit-2026-09-28.md)。

## 14. 部分复用

**无当前建议复制片段。** `createCaptionPages` 的按长度和间隙分页可作为字幕效果的设计提醒，真正实现须用当前 cue/修订与 Python FFmpeg，并验证中文断行、词/字时间戳及预览/正式输出一致性；本次不批准复制 TypeScript 算法。[上游分页](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/components/utils.ts#L37-L130)、[本地字幕](../../backend/app/batch_subtitles.py)。

## 15. 只参考设计

参考其“场景文字+关键词”轻量提交、背景音乐情绪选择、生成中详情和逐词高亮的视觉呈现；保留本地内容项目、素材来源、版本与审核。上游 Pexels 兜底到通用关键词时可能生成与文案不相关的画面，当前若借鉴应明确告知“找不到匹配素材”并允许人工选择，不能静默替换。[上游兜底](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/libraries/Pexels.ts#L6-L7)、[搜索循环](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/libraries/Pexels.ts#L128-L183)、[本地推荐](../../backend/app/batch_recommendation.py)。

## 16. 不引入

不引入 `ShortCreator` 内存队列/文件列表、REST/MCP 路由、React/MUI 独立页面、Remotion 渲染器、Kokoro/Whisper/FFmpeg 第二管线、随仓库音乐及 Docker 镜像。它们分别冲突于本地 `durable_runs`、FastAPI/项目路由、现有 UI、Timeline+FFmpeg、Qwen 本地配音及资产许可链。上游也没有自动发布或图像生成模块可引入。[队列](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/ShortCreator.ts#L27-L86)、[依赖](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/package.json#L46-L79)、[本地 ADR 0009](../adr/0009-content-project-and-workflow-boundaries.md)。

## 17. 当前缺失或待确认

外部库存素材 Provider 是否真属产品需求，仍待明确；若需要，先按 MPT 审计建来源/授权模型，再做小型 Python 适配器。逐词动态高亮可作为独立 UX 需求评估，但当前字幕已有保存、识别和渲染，不能把“视觉样式缺失”夸为“没有字幕”。上游没有带来中文 TTS 或可靠恢复的解决方案。[MPT 深审](./moneyprinterturbo-current-project-integration-audit-2026-09-28.md)、[本地字幕](../../backend/app/batch_subtitles.py)、[上游语言 FAQ](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/README.md#L449-L453)。

## 18. 可以删除或替换的当前实现

**无。** 本次审计没有证明本地配音、字幕、素材、Timeline、任务或审批模块被上游实现更好地取代，也没有触碰产品代码。待需求独立确认的库存素材/字幕样式属于增加能力，不是删除当前权威实现。[本地运行](../../backend/app/durable_runs.py)、[本地渲染](../../backend/app/timeline_render.py)。

## 19. 融合后的模块架构

若未来确需外部库存素材，仅可新增一个边缘 `StockVideoProvider`：搜索结果经来源/许可校验及媒体下载后注册为 `ProjectAssets`，由现有镜头/批量混剪选择；任何预览仍走 Timeline + `durable_runs`，审核后才形成交付版本。Provider 不拥有 Scene/Shot、作业或成片状态；不接入上游 Remotion。该架构是条件性建议，当前没有实施授权或代码改动。[本地素材](../../backend/app/project_assets.py)、[本地渲染](../../backend/app/timeline_render.py)、[批量审批](../../backend/app/batch_production.py)。

## 20. 融合后的用户流程

现阶段维持：内容项目准备素材 → 制作镜头或批量混剪规划 → 编辑 Timeline/字幕/配音 → 预览 → 审核当前版本 → 导出。未来若加库存素材，在准备素材环节明确搜索词、来源及授权提示、下载并注册项目素材，再由用户选择；搜索不到时保持缺口提示。外部 AI 最终生成仍在工具外完成，再将结果导入镜头候选。[领域流程](../../CONTEXT.md)、[ADR 0007](../adr/0007-preproduction-with-shot-steps.md)。

## 21. 对当前代码目录的影响

本次影响仅本报告。未来条件性 Provider 的位置应在 `backend/app/` 中与 [`project_assets.py`](../../backend/app/project_assets.py) 相邻，前端在现有素材入口而非引入 `src/ui/pages/VideoCreator.tsx`；不新增 Node 服务、Docker 镜像或 Remotion 目录。确切新文件名须在外部库存素材需求立项后决定。[上游 UI](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/ui/pages/VideoCreator.tsx)、[本地素材 API](../../backend/app/preproduction_api.py)。

## 22. 数据模型调整

本次**无调整**。未来库存素材须作为 `ProjectAssets` 记录额外保留 provider、provider asset ID、公开来源页、作者、取得时间、授权快照、文件指纹、许可核验状态，再以稳定 asset ID 被 Shot/Timeline/短视频变体引用。上游 `Video{id,url,width,height}` 不足以承担这些事实，`SceneInput` 更不能映射为本地 `Scene`。[上游类型](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/types/shorts.ts#L24-L40)、[视频类型](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/types/shorts.ts#L124-L129)、[本地领域](../../CONTEXT.md)。

## 23. Store 与状态调整

本次**无调整**。上游 `status()` 的“队列内=processing、MP4 在=ready、否则=failed”不可表示排队、失败原因、取消、重启恢复、输入冻结或审核；本地继续由 `durable_runs`、项目/批量 Store 和审批记录分别持有。若未来库存素材下载需要异步运行，也须走现有运行契约并让失败可定位，不复制上游内存数组。[上游状态](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/ShortCreator.ts#L27-L86)、[本地运行](../../backend/app/durable_runs.py)、[本地批量 Store](../../backend/app/batch_editing_api.py)。

## 24. API 与服务调整

本次**无调整**。上游 `POST /api/short-video` 只收短片场景与配置，`GET status`、`GET list`、`DELETE` 只面向 MP4 文件；MCP SSE 也只调用相同 `ShortCreator`。不能把它们直接挂到内容项目 API。REST 的 `/tmp/:tmpFile`、`/music/:fileName` 直接 `path.join` 用户参数而没有显式目录边界校验，存在潜在路径穿越风险，具体可达性取决于 Express 路径解码；`getVideo` 又把整段 MP4 读入内存，路由未提供 Range 处理。Pexels 下载经 `https.get`，未设重定向、超时、体积上限或目标主机白名单；虽然 URL 来自供应商 API，也不适合作为安全下载模板。未来库存素材若立项，可在现有项目素材 API 下加搜索/导入端点，输入带项目 ID 和修订，输出为项目素材 ID 与来源信息，并经过用户确认；不添加上游公共临时文件出口。[上游 REST](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/server/routers/rest.ts#L129-L221)、[整文件读取](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/ShortCreator.ts#L231-L237)、[下载](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/ShortCreator.ts#L147-L173)、[MCP](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/server/routers/mcp.ts#L32-L97)、[本地素材 API](../../backend/app/preproduction_api.py)。

## 25. P0 / P1 / P2、许可与验收

| 优先级 | 判断与验收 |
| --- | --- |
| P0 | 登记“只参考设计/无代码复制”，保持当前 `ProjectAssets`、Timeline、`durable_runs`、审核/导出版本；验收为不出现第二套任务/渲染/素材权威。本报告即完成该研究判断。 |
| P1（需产品需求） | 外部库存素材 Provider 先与 MPT 去重并核许可；测试搜索无结果/视频过短/下载失败/重复/项目隔离/来源记录，确认文件进入 `ProjectAssets` 且删除引用安全。 |
| P2（可选视觉需求） | 逐词高亮在现有字幕与 FFmpeg 路径重做；测试中文、中英混排、时间戳、预览/正式导出一致性、旧审核失效。 |

**许可清单。** 根 [`LICENSE`](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/LICENSE#L1-L20) 为 MIT，只覆盖仓库作者可授权的代码，复制实质部分须保留版权及许可文本；这不自动授权第三方依赖、模型、媒体和容器二进制。上游 README 依赖表也将 Remotion 专有许可、FFmpeg LGPL/GPL、Pexels 条款分列，不能以根 MIT 覆盖。[上游依赖表](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/README.md#L481-L492)。Remotion 的[官方许可 FAQ](https://www.remotion.dev/docs/license/faq)区分免费许可资格与 Company License，组织人数/自动化用途须按实际部署核定。Kokoro 取自 [`onnx-community/Kokoro-82M-v1.0-ONNX`](https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX)，模型卡标为 Apache-2.0，下载具体权重/音色文件和 `kokoro-js` 包仍须留存对应版本条款；上游运行时未固定 Hugging Face 模型修订。[模型 ID](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/config.ts#L112)、[加载](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/libraries/Kokoro.ts#L64-L70)。Whisper.cpp 源码为[其仓库 MIT](https://github.com/ggml-org/whisper.cpp/blob/master/LICENSE)，OpenAI Whisper [代码与模型权重亦声明 MIT](https://github.com/openai/whisper#license)；但上游 Docker 构建按 tag 克隆、脚本下载不同 `.en` 模型，最终镜像中二进制、权重与构建环境仍需单独列物料和版本。[Docker 下载](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/main.Dockerfile#L1-L17)、[tiny](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/main-tiny.Dockerfile#L1-L17)、[CUDA](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/main-cuda.Dockerfile#L6-L19)。Pexels 的[素材许可](https://www.pexels.com/license/)允许多种使用但限制未改动转售、误导背书等，API 的[文档与条件](https://www.pexels.com/api/documentation/)另行适用；上游只保存视频 ID/文件 URL，未记录逐条素材许可与作者。[Pexels 实现](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/libraries/Pexels.ts#L54-L105)。音乐目录 README 自称来自 YouTube Audio Library、可商业用且无需署名，但未附逐曲许可证与取得证明，不能据此整体搬入；Barlow Condensed 字体经 Remotion Google Fonts 加载，字体许可/再分发方式、npm 依赖、FFmpeg 安装包、Chromium/系统包和基础容器镜像同样须逐项核对，根 MIT 不覆盖。[音乐声明](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/static/music/README.md#L1-L4)、[字体调用](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/components/videos/PortraitVideo.tsx#L1-L18)、[Docker 依赖](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/main.Dockerfile#L19-L47)。本次没有分发上游代码、模型或素材，以上为接入前门槛，不是已完成的商用许可验收。

**验证记录。** 已按固定 SHA 阅读路由、MCP、校验/类型、`ShortCreator`、Pexels/Kokoro/Whisper/FFmpeg/Remotion、Remotion 组件、UI、配置、两处测试、Docker/安装脚本、音乐与根许可，并对照当前代码与七份既有深审。仓库只有两个测试文件，主要是 mock 的 Pexels 请求和队列→成片状态；没有覆盖 REST/MCP、安全边界、崩溃恢复、取消、时长计算、真实模型中文、媒体授权或 Docker 运行。[队列测试](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/ShortCreator.test.ts#L145-L220)、[Pexels 测试](https://github.com/gyoridavid/short-video-maker/blob/9bb9a212ced86caa7e09099c382da1a44d638760/src/short-creator/libraries/Pexels.test.ts#L10-L58)。未运行上游或当前产品测试；本报告完成后只执行文档结构、链接/路径和 `git diff --check` 检查。
