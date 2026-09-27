# 视频复刻与商品视频制作：模块业务逻辑和边界对照研究

日期：2026-09-27

性质：一手资料研究与本项目边界建议；不代表完成竞品实机验收或授权重构。

## 1. 结论与证据范围

本项目适合沿业务对象划分模块：**项目与素材 → 参考分析或创作简报 → 可编辑方案 → 生成运行与结果素材 → 时间线变体 → 渲染产物 → 审核与交付**。外部服务适配与执行队列横向支撑这些业务模块，但不拥有商品事实、剪辑决策或审核结论。

这不是从某个竞品复制的内部架构，而是根据公开契约推导的本项目方案：OTIO 将编辑数据与媒体引用分离；MLT 将素材读取、编排、效果与输出分离；ComfyUI 将图提交、执行进度和结果查询分离；Remotion 将合成描述与渲染调用分离。它们共同说明，“编辑对象”和“一次执行”应当有不同生命周期。[OTIO 架构](https://opentimelineio.readthedocs.io/en/latest/tutorials/architecture.html)、[MLT 框架](https://www.mltframework.org/docs/framework/)、[ComfyUI 服务接口](https://docs.comfy.org/development/comfyui-server/comms_routes)、[Remotion renderMedia](https://www.remotion.dev/docs/renderer/render-media)

研究读取官方项目文档、官方源码入口及产品帮助，未采用第三方教程、论坛推测或聚合站。开源文档说明其公开架构；商业产品帮助只证明用户流程和公开 API，不能据此声称掌握其内部数据库或微服务划分。未安装竞品、登录付费账号、执行模型或核验输出质量。报告不讨论价格、账号可用性与地域限制。

本项目语义依据根目录 `CONTEXT.md`：参考素材只是一份当前分析输入；项目素材可多份复用；候选模板不等于可执行工作流；生成运行不同于用户导入的镜头结果关联；本地剪辑服务复刻收尾与商品批量交付。

## 2. 一手资料中的边界模式

### 2.1 ComfyUI：外部图执行器，不是业务项目或交付系统

官方接口中，提交 `/prompt` 会先校验图，成功返回 `prompt_id` 和队列位置；`/queue`、`/history/{prompt_id}` 与 WebSocket 负责执行状态及历史。`/object_info`、`/models`、`/system_stats` 是能力和环境查询，上传输入与查看输出又有独立接口。这是一套执行协议，并没有在这些接口中承诺商品事实确认、视频审片或客户交付语义。[官方 Routes](https://docs.comfy.org/development/comfyui-server/comms_routes)、[官方 server.py 源码入口](https://github.com/Comfy-Org/ComfyUI/blob/master/server.py)

**本项目推论：** 复刻方案属于产品；ComfyUI 图是目标工具的编译产物；`prompt_id` 只标识外部执行。内部生成运行应保留方案版本、输入引用、环境能力、已提交图和外部 ID。连接成功、图校验成功、排队成功、媒体生成完成应是不同状态。无法确认提交结果时，应先对账，不能因为 HTTP 超时直接重复提交。

### 2.2 Kdenlive / MLT：素材库存、时间线与媒体输出职责分离

Kdenlive Project Bin 管理项目关联素材，也保存 sequence；素材从 bin 被放入时间线。代理素材供编辑使用，正式渲染通常恢复原始素材。时间线预渲染则是为了加速效果合成后的播放，与原素材代理不同；导出界面另外提供预览分辨率和代理渲染选项。[Project Bin](https://docs.kdenlive.org/en/project_and_asset_management/project_bin.html)、[Proxy Settings](https://docs.kdenlive.org/en/project_and_asset_management/project_settings/proxy_settings.html)、[Timeline Preview Rendering](https://docs.kdenlive.org/en/tips_and_tricks/tips_and_tricks/timeline_preview_rendering.html)、[Rendering](https://docs.kdenlive.org/en/exporting/render.html)

MLT 的 producer 供给帧，playlist 编排来源区间，tractor 组织多轨，filter/transition 处理效果，consumer 消费帧用于播放或输出；这里的 producer/consumer 指帧流方向，不是业务里的“视频生成”。MLT XML 可以将多个来源通过引用和区间组织为多轨结构。[MLT 框架](https://www.mltframework.org/docs/framework/)、[MLT XML](https://mltframework.org/docs/mltxml/)

**本项目推论：** 素材目录拥有原文件与代理关系，时间线拥有素材引用和剪辑区间，渲染模块消费冻结时间线。分析代理、播放代理、时间线预览和正式成片不能共用一个无说明的“视频文件”字段；业务运行记录应注明用途。保留当前 FFmpeg 实现即可借鉴这些边界，无需立即换成 MLT。

### 2.3 OpenTimelineIO：编辑数据与媒资寻址分离

OTIO 表达 Timeline、Stack、Track、Clip、Gap、Transition；Clip 的 `ExternalReference` 或 `MissingReference` 表达媒体引用。`available_range` 是素材可用区间，`source_range` 是实际剪入区间；`RationalTime` 记录时间值和速率。适配器负责格式转换，media linker 在读取之后把引用连接到具体媒体。[官方架构与时间模型](https://opentimelineio.readthedocs.io/en/latest/tutorials/architecture.html)

**本项目推论：** 时间线片段必须引用 `assetId`，而不是复制素材元数据或把路径当永久身份；区分素材区间、片段在时间线的位置和输出时间。素材定位、缺失检测、格式导入导出应在边缘处理。OTIO 可作为未来交换层，不应当被当作模型执行器、完整特效引擎或审核系统。OTIO 本身不嵌入音视频。[官方概览](https://opentimelineio.readthedocs.io/en/latest/)

### 2.4 Remotion：合成配置和渲染调用共享输入契约

Remotion Composition 注册可渲染视频，具有标识、宽高、FPS、帧数和输入属性。`renderMedia` 接收解析后的 composition、bundle 地址、codec、输出位置及 `inputProps`；文档明确要求选择 composition 时传入与渲染相同的 `inputProps`，避免配置推导与实际执行不一致。[Composition](https://www.remotion.dev/docs/composition)、[renderMedia](https://www.remotion.dev/docs/renderer/render-media)

**本项目推论：** 输出比例应先解析为实际画布尺寸，再进入同一个渲染规格。预览、审核和正式导出不能各自独立读取“项目当前设置”。Remotion 更适合作为以后有明确品牌动效/模板需求时的一个渲染适配器；目前简单声画字幕混剪无需因此引入第二套时间线核心。

### 2.5 Descript：项目内多作品，长任务有独立 Job

Descript 官方 API 将 project 定义为内容容器，允许一个项目有多个 composition，并可针对其中一个作品执行操作。导入、AI 编辑和发布属于长任务，拥有可查询的 Job ID 和状态。媒体导入伴随转写；发布与转写导出是单独能力。[Descript API](https://help.descript.com/api-and-mcp/api)

发布为 web link 之后，可以用同一 URL 更新页面并重新渲染；官方说明这种更新会删除 share page 的评论，建议编辑协作使用项目内评论。[发布与更新 share page](https://help.descript.com/hc/en-us/articles/10255817744653-Publish-content-with-Descript-web-links)

**本项目推论：** 商品批次是作品集合，每条短视频变体应独立拥有内容、渲染和审核版本。任务 Job 是执行过程，不是作品本身。Descript 的同链接更新是一个需要慎用的反例：本项目交付审核应保留旧意见的归属和旧产物，不能用覆盖文件的方式延续一个已通过状态。

### 2.6 Canva / CapCut：脚本、素材编排与新媒体生成有区别

Canva 官方帮助明确区分：Magic Design 选择和安排布局，Magic Media / Canva AI Video 生成媒体，生成的媒体可以进入设计。其布局能力并不因此等同于视频生成。[Canva Magic Design 帮助](https://www.canva.com/help/use-magic-design/)

CapCut 官方流程先编写脚本或选择 AI 文案，再选择素材库素材或本地素材制作视频草稿，最后预览导出。该文档属于公开产品教程，不证明背后所有画面均由生成式模型新建，也不证明质量保证。[CapCut Script to Video](https://www.capcut.com/resource/script-to-video-ai)

**本项目推论：** 商品事实与素材授权进入创作简报；文案模型输出是脚本候选；已确认脚本驱动素材推荐与声画编排。不得把“智能混剪”宣传成已生成新的商品镜头，也不得让生成的营销句子自动成为已核实商品事实。自动制作可以编排这些模块，但不必把每个内部处理步骤都变成用户要点击的页面。

### 2.7 Adobe / Frame.io：转写、字幕、审片和版本有不同归属

Premiere 文本编辑通过带时间码的源转写选择、重排片段，时间线随之变化；官方限制说明，字幕需要根据最终编辑后的 sequence 生成，文本编辑转写本身不等于字幕工作流。[Text-Based Editing 概览](https://helpx.adobe.com/sg/premiere/desktop/edit-projects/edit-video-using-text-based-editing/overview-of-text-based-editing.html)

Premiere Share for review 可以把活动 sequence 的更新推送至同一 review link，也能另建链接。Frame.io version stack 允许查看历史文件版本和并排对比。[Premiere Share for review](https://helpx.adobe.com/uk/premiere/desktop/collaborate-with-others/share-for-review-using-frame-io/share-for-review-with-frame-io.html)、[Frame.io Versioning](https://help.frame.io/en/articles/9101068-versioning-in-frame-io)

**本项目推论：** 原素材转写属于素材分析；最终字幕属于具体时间线版本。审片意见必须指向实际媒体版本/运行 ID，可附时间码；审核通过是交付业务决定，不能由渲染成功自动产生。相同分享地址可聚合多个版本，但不意味着旧审核自动批准新版本。这里的批准失效规则是本项目建议，不声称上述竞品采用相同字段或校验算法。

## 3. 本项目可直接采用的模块归属

以下是建议的逻辑边界，不要求拆成微服务，也不是本轮已实施重构。可在当前单体内用业务服务和窄接口实现。

| 模块 | 拥有的业务对象与规则 | 对外输出 | 不应拥有 |
| --- | --- | --- | --- |
| 项目与素材 | 项目身份、素材身份、角色、原文件、代理/派生关系、授权范围、可读性 | 稳定素材引用与元数据 | 当前片段裁剪、模型凭据、审核结论 |
| 参考分析与控制准备 | 当前参考绑定、分析代理、可观察事实/建议区分、镜头分析、深度处理及质量确认 | 有来源与版本的分析/控制素材 | 商品文案确认、时间线采用结果 |
| 创作简报与脚本候选 | 商品事实、素材选择、目标与禁用表达、候选修改/确认 | 已确认内容版本与镜头建议 | TTS执行、真实媒体完成状态 |
| 复刻方案与生成计划 | 策略、提示词、所需输入、输出意图、能力限制及参数解析 | 可解释计划及目标工具图/包 | 外部队列当前状态、最终审核 |
| 生成运行与结果关联 | 冻结计划/输入/能力快照、外部ID、对账、结果接收；外部导入结果另记关联 | 已生成/已导入的项目素材与血缘说明 | 默认为已采用、自动批准成片 |
| 编排与短视频变体 | 内容版本、素材片段、轨道、配音绑定、字幕、画布、单条覆盖与采用选择 | 可渲染时间线快照 | 供应商HTTP协议、编码进程细节 |
| 渲染 | 校验冻结输入、预览/成片用途、媒体合成、进度、取消、文件探测 | 真实产物及实际宽高/FPS/时长 | 修改脚本或时间线、商品审核决定 |
| 审核与交付 | 指定版本审片、意见、批准/驳回、过期判断、交付选择与历史包 | 已批准产物/冻结输入的交付记录 | 静默刷新批准、替用户改内容 |
| 外部服务与任务基础设施 | 分析/TTS/生成协议适配、能力探测、凭据引用、队列运行和重试机制 | 统一执行状态与结果 | 项目业务决策、服务间静默回退 |

现有代码入口可据此审阅：`preproduction.py` / `preproduction_api.py` 对应素材与准备；`semantic_analysis*` / `depth_capture*` 对应分析与控制处理；`aigc_content_api.py` 对应创作简报与候选；`reproduction*` / `workflow_templates.py` 对应方案和目标工具编译；`timeline.py` / `timeline_render.py` 对应编辑模型和渲染；`batch_editing_api.py` 横跨编排、配音、字幕、预览、审核、交付多个责任。这个入口映射只根据仓库命名及领域上下文定位，不能代替完整代码依赖审计。

建议先对最后一个跨职责入口梳理状态转换与跨模块命令；路由可以保留兼容，再根据实际变更负担决定是否提取服务。不要仅按页面名称拆模块，也不要只为了文件行数机械拆分。

## 4. 两条业务链与跨模块命令

### 4.1 参考复刻

参考素材绑定 → 分析与控制准备 → 保存复刻方案 → 环境/能力核验 → 冻结生成运行 → 外部生成或导出交付包 → 接收结果素材 → 采用到收尾时间线 → 渲染 → 审核与交付。

“导出候选工作流”只完成方案交接；“外部模型生成成功”只获得结果素材；“导入镜头候选”只记录候选及关联。这些状态均不能代表成片已审核。依据来自 ComfyUI 执行契约与 OTIO 编辑/媒体分离；具体串联是本项目推论。[ComfyUI Routes](https://docs.comfy.org/development/comfyui-server/comms_routes)、[OTIO 概览](https://opentimelineio.readthedocs.io/en/latest/)

### 4.2 商品制作

创作简报 → 提出并确认脚本候选 → 建立批次及单条内容 → 制作配音/素材编排/字幕 → 渲染预览 → 每条审核 → 按已批准版本导出。

“制作所选 N 条”可以是一个编排命令，依次调用内部模块；应逐条保存真实状态、允许失败项独立重试，不能用一个批次布尔值覆盖各作品状态。依据是 Descript 的 project/composition/job 分离和 CapCut 的脚本到视频草稿流程；逐条交付约束为本项目建议。[Descript API](https://help.descript.com/api-and-mcp/api)、[CapCut 官方教程](https://www.capcut.com/resource/script-to-video-ai)

### 4.3 需要明确的五个命令

- `确认脚本`：确认该内容版本；不生成配音，不审核成片。
- `保存变体`：保存轨道/字幕/画布版本；不修改项目素材原件。
- `提交生成`：冻结计划和授权输入后执行；生成结果进入素材库，不自动采用。
- `生成预览`：冻结当前声画字幕与实际画布，产生可审媒体；不自动批准。
- `导出已通过版本`：绑定审核所指向的输入/产物；若重新编码，应验证交付输出对应同一内容快照。

这些命令是本项目建议，目的在于让每个用户动作有可解释后果，而非新增一层抽象框架。

## 5. 版本与失效规则建议

| 改动 | 可以复用 | 必须更新或失效 |
| --- | --- | --- |
| 换当前参考素材 | 原素材、旧分析与旧运行的历史记录 | 当前分析/控制质量结论、依赖旧来源的复刻方案可执行资格 |
| 修改脚本文字 | 不依赖文案的视觉素材 | 内容确认、实际配音、对应字幕时间、合成预览及审核 |
| 改音色 | 已确认事实与脚本文字、可继续使用的视觉素材 | 配音、依赖时长的编排/字幕、预览及审核 |
| 改画布比例 | 脚本、音色不变的有效配音、原素材 | 素材适配、字幕布局、预览、审核和新交付 |
| 只改变导出编码质量 | 审核时的内容快照 | 新导出运行与输出媒体校验；不能自动改内容 |
| 供应商配置/模板变化 | 旧运行及旧产物 | 当前能力验证，新的执行快照；旧记录不可被配置覆盖 |

上表属于本项目业务决策，不是某一产品的原样规则。Kdenlive 明确区分代理与正式渲染；Remotion 要求配置选择和渲染输入一致；Frame.io 支持历史媒体版本，这些是一手资料提供的基础模式。[Kdenlive Proxy](https://docs.kdenlive.org/en/project_and_asset_management/project_settings/proxy_settings.html)、[Remotion renderMedia](https://www.remotion.dev/docs/renderer/render-media)、[Frame.io Versioning](https://help.frame.io/en/articles/9101068-versioning-in-frame-io)

实现时优先使用现有 revision、来源标识和运行快照；只有确认现有字段无法表达依赖时再加字段。不要默认创建通用工作流引擎、事件总线或任意缓存图。

## 6. 反例与应避免的边界混淆

1. **一个 Project.status 代表所有阶段。** 分析完成、方案保存、模型排队、成片审核的对象不同；用单一 completed 会无法解释失败与过期。可用首页摘要聚合，真实状态仍归属具体对象。依据：ComfyUI 执行接口与 Descript 独立 Job。[ComfyUI](https://docs.comfy.org/development/comfyui-server/comms_routes)、[Descript](https://help.descript.com/api-and-mcp/api)
2. **生成图 JSON 充当完整项目文件。** 目标图没有自动涵盖素材授权、编辑采用及审核意见。保留产品方案，再编译工具图；不把所有业务塞进外部节点 metadata。依据：ComfyUI 提交协议与 OTIO 编辑结构。[ComfyUI](https://docs.comfy.org/development/comfyui-server/comms_routes)、[OTIO](https://opentimelineio.readthedocs.io/en/latest/tutorials/architecture.html)
3. **AI 文案建议立即成为商品事实，素材推荐立即成为最终采用。** 候选与确认必须分开；智能素材编排也不等于生成新媒体。依据：Canva 对布局/媒体生成的明确区分及 CapCut 脚本审核流程。[Canva](https://www.canva.com/help/use-magic-design/)、[CapCut](https://www.capcut.com/resource/script-to-video-ai)
4. **把源转写直接烧录成最终字幕。** 剪辑重排后时间位置不同，应根据最终时间线重建字幕。依据：Premiere 明确说明最终 sequence 字幕属于独立工作流。[Premiere 文本编辑](https://helpx.adobe.com/sg/premiere/desktop/edit-projects/edit-video-using-text-based-editing/overview-of-text-based-editing.html)
5. **修改项目默认设置就重写历史作品，或导出时重新读取当前设置。** 会让审核对象与实际交付不一致。作品和运行必须保留原始输入版本；这项失效规则是本项目推论，参考 Remotion 共享输入契约和 Frame.io 历史版本。[Remotion](https://www.remotion.dev/docs/renderer/render-media)、[Frame.io](https://help.frame.io/en/articles/9101068-versioning-in-frame-io)
6. **同一媒体 URL 始终等于同一审核版本。** Descript 同链接重新发布会移除 share page 评论，说明分享地址与讨论/产物生命周期不能自然画等号。本项目应保存旧媒体与旧意见，并明确当前审核版本。[Descript 更新分享页](https://help.descript.com/hc/en-us/articles/10255817744653-Publish-content-with-Descript-web-links)
7. **立即引入所有对照项目。** OTIO 是交换层，MLT 是媒体处理框架，Remotion 是程序化合成工具，ComfyUI 是生成执行环境，角色不同。先选择真实集成问题，再决定是否接入；不要为“成熟架构”同时替换现有 FFmpeg、时间线和生成链。依据为以上公开职责，集成取舍是本项目建议。

## 7. 可评审的后续顺序

1. 核对业务对象、唯一身份与来源依赖：参考素材是否替换项目素材、方案是否被运行修改、审核究竟指向哪个媒体。
2. 画出两条业务链的状态转换，列出每个保存/制作/采用/审核/导出命令的前置条件与失败恢复方式。
3. 围绕已有代码，只在跨职责路由上识别可提取的业务规则；保持 API 兼容，不先做全仓重构。
4. 用关键链路验证边界：旧方案运行期间继续编辑；比例变更后旧审核不能导出；生成请求超时可对账；外部导入候选不会冒充已验证生成血缘；字幕跟随最终时间线。
5. 真有跨编辑器交换需求时再引入 OTIO；真有固定品牌动效需求时再评估 Remotion；现有 FFmpeg 不满足实际合成需求时再评估 MLT。

本报告建议以这些可验证业务规则作为后续设计依据；不把工具选型本身当作模块边界问题已解决。
