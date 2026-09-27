# 电商短视频工作流：公开产品与 UX 文档对照研究

日期：2026-09-26  
状态：只读研究与设计建议；未修改产品代码，未做登录后产品验收。

## 1. 研究目标与边界

目标是减少内部交付团队制作一批商品短视频时的理解、切换和重复操作负担。范围按本轮需求限定为：商品资料与授权素材 → AI 提出 5 条可编辑脚本 → 脚本确认 → 9 个带描述的音色试听与选择 → 本地真实配音、混剪、字幕 → 批量播放审核与导出。数字人保持暂停。

本报告读取了 11 个不同产品或设计机构的公开一级来源：CapCut、Canva、Clipchamp、Descript、Pictory、VEED、Adobe Express、HeyGen、LTX Studio、Nielsen Norman Group、IBM Carbon。补充查找 InVideo，但正文访问返回 401，只有搜索索引可读，因此不计入这 11 个可访问来源，也不将其作为关键设计结论依据。

证据分为三类：

- **公开文档观察**：实际读到的官方帮助、教程或设计规范正文；证明文档描述了某个模式，不证明当下账号可用。
- **本项目设计推论**：依据文档模式和本项目目标提出的方案，不声称竞品采用完全相同实现。
- **未验证**：所有登录后的交互、成片质量、中文发音质量、速度、价格、额度、账号权限和真实批量能力。本轮未登录、安装、下载模型或付费。教程中的插图链接可定位，但没有把网页文本提取当作截图视觉验收。

项目上下文采用 `CONTEXT.md` 中的“创作简报”“脚本候选”“内部交付团队”“短视频变体”“批量混剪”等术语。既有 ADR 0008 保留本地 FFmpeg 渲染、独立输入快照和版本标识；本报告建议的简化是改变默认操作界面，不取消这些已有一致性规则。配音和字幕属于后续需求扩展，不能据此宣称 ADR 0008 首轮已经实现。

## 2. 核心结论

推荐把默认工作流收敛为 **“准备资料 → 确认脚本与声音 → 制作并审核”三个业务阶段**。配音、镜头编排、字幕和渲染属于第三阶段内的自动处理状态；用户在有问题时才展开修改入口。依据是渐进披露：常用决策先呈现，专业参数按需出现。三阶段划分是本项目推论，来源没有要求具体必须为三步。[NN/G：Progressive Disclosure](https://www.nngroup.com/articles/progressive-disclosure/)

应保留两个内容决定：脚本确认和成片审核。可以消除“交接到混剪”“创建时间线”“加入配音轨”“生成字幕”“去另一个页面预览”等机械操作，但不应把模型提议直接当作已确认商品文案。CapCut 教程把多份脚本审核、修改和 Apply 放在视频制作之前；HeyGen Chat Mode 同样先让用户修订计划，确认后才制作。[CapCut：AI Script Writing 教程](https://www.capcut.com/resource/script-writing-ai)、[HeyGen：Video Agent 入门](https://help.heygen.com/en/articles/12402907-how-to-get-started-with-video-agent)

“自动化”应表现为已经完成的声画字幕草稿，而非把一长列技术参数交给用户填写。Pictory 公开教程描述了从脚本自动拆场景、匹配画面、安排字幕与时间，再开放逐场景修改的流程。适配本项目时，画面来源应限定为当前项目选定的授权素材，无法匹配时明确指出缺口。[Pictory：Script to Video 官方教程](https://pictory.ai/academy/how-to-turn-script-into-video-pictory-ai)

## 3. 11 个一级来源的具体对照

| 产品 / 机构 | 公开文档中实际观察到的流程 | 降低负担的可取模式 | 本项目适配 / 不适合照搬的地方 | 证据等级与来源 |
| --- | --- | --- | --- | --- |
| CapCut | AI Writer 输入名称、重点和长度；生成 3 份脚本，比较、Improve/Expand 后 Apply；随后视频预览与导出 | 把“生成草稿”和“采用文案”区分；以候选比较承接 AI 输出 | 我们仍需 5 份，但用可见卖点摘要和确认状态减少逐份打开；不照搬 All tools → AI tools → 类别的多层入口 | 官方步骤教程，含分步插图链接；未操作 App。[教程](https://www.capcut.com/resource/script-writing-ai) |
| Clipchamp | 选语言、选声音；Hear this voice 听音色示例；Preview 听输入文本；pitch/pace 放 Advanced settings；保存后音频进入时间线 | 音色样音和实际文案试听是两种不同需求；基础选择与高级调节分层 | 固定 9 音色用可比较卡片；默认不展示音高、语速数值；本地试听真实不可用时显示原因，不能放假试听按钮 | Microsoft 官方帮助正文。[TTS 操作](https://support.microsoft.com/en-us/clipchamp/how-to-use-the-text-to-speech-feature) |
| Descript | 声音目录支持搜索，Recommended 与 Additional results 分组；语言标签影响匹配；应用 Speaker 后自动开始 TTS | 推荐声音先呈现，说明声音适用语言；脚本与声音在同一内容上下文关联 | 9 个声音无需复制大型目录搜索；不能照搬选中即正式生成，因为本项目需要明确脚本版本与制作范围 | 当前帮助已重定向到新地址，旧索引中关于标签的描述不直接当作当前 UI 已验收。[Stock AI voices](https://help.descript.com/ai-speech/stock-voices) |
| Pictory | 可粘贴、写入或导入脚本；AI 写脚本可选；自动拆场景与选画面；Story/Visuals/Audio 等分区修改；随时 Preview Video | 脚本是编排的主线，先给完整草稿，再对具体场景纠错 | 我们可显示“旁白句子 ↔ 所用素材片段”；不照搬股票素材、AI 视觉生成和大量编辑标签，避免偏离授权素材混剪 | 官方 Academy 分步教程，含脚本和 storyboard 插图链接。[教程](https://pictory.ai/academy/how-to-turn-script-into-video-pictory-ai) |
| Canva | Bulk Create 用每一行数据形成一页或一个设计；先连接字段，逐项 Preview 后 Create；可按数据列命名 | 一批产物中每一条有独立身份；制作前可以检查映射，命名自动继承业务数据 | 用商品名、卖点和变体编号自动命名；不要求用户学习字段拖放；它的批量预览视频只显示冻结首帧，不适合作为我们声画审核验收 | 官方帮助明确列出预览限制；未验证账号批量能力。[Bulk Create](https://www.canva.com/help/bulk-create/) |
| VEED | AI Voice 读入脚本、选语言/声音、生成音频，结果作为可调整的正常音频片段；dashboard 项目以缩略图卡片出现 | 内容结果可直接复用，项目缩略图帮助找回作品；生成后继续在上下文中播放与修正 | 默认流程无需让团队手动放音频轨；批量卡片还须显示脚本、音色和审核状态，缩略图不构成已听声验收 | 官方 TTS 和 dashboard 帮助正文。[AI Voice](https://support.veed.io/en/articles/11662445-how-to-use-text-to-speech)、[Dashboard](https://support.veed.io/en/articles/11641509-how-veed-s-dashboard-works) |
| Adobe Express | 字幕在编辑器中按语言/媒体来源生成，Transcript 面板直接改文字；Format 调整样式；quick action 也可上传后改字幕并下载 | 字幕文字修订和画面预览处于同一任务上下文；文字校对优先于排版微调 | 本项目在实际成片旁展示字幕文本；默认品牌样式无需每条设置；不强制导出再重新上传去另一个工具加字幕 | 官方帮助正文，含 Captions 面板截图说明。[Caption videos](https://helpx.adobe.com/express/web/video-creation-and-editing/edit-videos/caption-video.html) |
| HeyGen | Chat Mode 先修订 Video Plan，确认 Proceed 后制作；小范围修改只调整相关部分；最后可 Edit a Copy 到 AI Studio 精修 | 先审计划后执行；把“一处改动”与“全部重做”分开 | 借鉴逐条/逐场景修正与影响范围提示；不启用 avatar、Seedance、云素材生成；自由聊天不应取代稳定的确认按钮 | 官方入门帮助；涉及成本的数值不作为本地性能依据。[Video Agent](https://help.heygen.com/en/articles/12402907-how-to-get-started-with-video-agent) |
| LTX Studio | 项目内 Storyboard 排序、替换和定时；源 generation 不改，编排引用它们；教程还描述模型选择与运动提示 | 修改顺序和镜头时复用已有素材，不要求重新创建整个项目 | 用既有素材 ID 和源时间范围保留非破坏性编排；不把模型、运动提示、分镜生成环境放进电商默认入口 | 官方教程正文，未验证其账户内实时协作或生成质量。[教程](https://ltx.io/blog/ltx-studio-tutorial) |
| NN/G | 渐进披露把少用或高级功能后置；可用性准则要求状态可见、语言符合用户世界、识别优于回忆 | 首屏只展示决定当前任务的选项；用户无需记住内部步骤和技术名词 | 主流程用业务动作与可见摘要；技术日志和专业时间线展开查看；重要前置条件不可藏入高级设置 | 官方 UX 规范，属于设计原则而非竞品实测。[渐进披露](https://www.nngroup.com/articles/progressive-disclosure/)、[十条准则](https://www.nngroup.com/articles/ten-usability-heuristics/) |
| IBM Carbon | Data table 可展开行、筛选、单条及批量操作；选行后出现批量操作条；批量模式关闭行内操作避免歧义 | 先确定选择范围，再执行批量动作；明示所选条数 | 批量审核页显示“已选 3 条 / 导出已选通过项”；保留可播放缩略图与明细抽屉，不能把视频审核只做成密集数据表 | 官方组件使用文档与图例。[Data table](https://carbondesignsystem.com/components/data-table/usage/) |

补充访问记录：InVideo 官方 Autopilot 索引可读到脚本、配音、字幕、媒体等编辑分类，但直接打开正文返回 401。本报告仅记录分类存在，不据此推断登录后界面布局、脚本确认门槛或真实可用能力。[InVideo 官方索引](https://help.invideo.io/en/collections/9486768-autopilot)

## 4. 具体可执行的页面模式（均为本项目设计推论）

### 4.1 准备资料：一页说明本次制作目标

首屏应回答三个问题：做什么商品、用什么素材、产出什么视频。

| 可见内容 | 默认操作 | 避免的负担 |
| --- | --- | --- |
| 商品资料 | 一次粘贴资料；系统提取名称、已知卖点、受众与行动引导，用户原位核对 | 不要求先写 AI 提示词或多次复制同一资料 |
| 授权素材 | 上传或从当前项目选择；显示缩略图、时长和可用数量 | 不要求逐条先建镜头、分析参考视频、创建技术节点 |
| 制作规格 | 业务预设，如“竖屏商品短视频”；展示默认数量和时长，允许修改 | 比例、字幕样式、配乐、编排策略不同时全部摊开 |
| 可用性提示 | 在本页指出缺少商品事实、素材无法读取、声音服务不可用等当前阻碍 | 不在后续第 7 个按钮才发现前置条件失败 |
| 唯一主动作 | “生成 5 条脚本” | 不同时摆放“生成计划 / 创建批次 / 新建时间线”等相近按钮 |

此页结构依据渐进披露与状态可见性提出；不是宣称竞品可以自动核实商品事实。现有外部服务发送披露应就地、一次清楚列出接收服务与发送内容，使用现有授权规则；不能以“减少步骤”为由取消必要披露。[NN/G：渐进披露](https://www.nngroup.com/articles/progressive-disclosure/)、[NN/G：状态可见性与错误预防](https://www.nngroup.com/articles/ten-usability-heuristics/)

### 4.2 脚本确认：5 条候选可比较、可编辑、可选取

先显示 5 个候选的**卖点、开场句、估计时长、确认状态**。点击其中一条后，在同页展开完整文案与对应画面建议，直接编辑，不跳到新的“详情/镜头/事实关联”页面。商品事实来源保持可见引用，未核对内容明确标记。来源模式是 CapCut 的候选比较后 Apply；5 条、摘要字段及事实来源呈现属于本项目推论。[CapCut：Review, refine and apply](https://www.capcut.com/resource/script-writing-ai)

每条都有明确“采用这条”选择，用户可以选 1–5 条。“确认 3 条脚本”应标明实际数量；未选项保持候选，不生成配音。脚本编辑后原确认状态失效，并用简短提示说明“文字已修改，需要重新确认”。选择范围与内容确认应区别清楚，不能仅用同一个无说明的勾选框同时承担两种语义。批量选择范围表达参考 Carbon；版本状态是本项目已有约束的延续。[Carbon：Batch actions](https://carbondesignsystem.com/components/data-table/usage/)

镜头建议默认是面向审核的图文对照，不强迫用户逐个创建镜头。Pictory 将脚本拆成可编辑场景，LTX 将编排引用与源素材分离；本项目可用“旁白句子 / 画面片段 / 选用理由”来完成相同的核对任务。[Pictory：Storyboard 与编辑](https://pictory.ai/academy/how-to-turn-script-into-video-pictory-ai)、[LTX：Storyboard](https://ltx.io/blog/ltx-studio-tutorial)

### 4.3 声音选择：9 张卡片，两个真实试听入口

每张音色卡片呈现：**可读名称 + 一句话听感描述 + 适合的用途 + 听样音 + 选用**。描述例如“语速平稳、适合讲解”；最终文字必须根据本地实际样音核验，不能只根据英文模型 ID 或臆测声线填写。当前选中项清楚标注，播放一个声音时停止前一个，避免串音。

两个入口分别服务不同决策：

1. **听样音**：9 个声音读取同一段固定短文本，快速比较音色；优先复用与当前模型/音色版本一致的真实样音。
2. **试听当前脚本**：用当前选定音色读本条实际文案的一小段，检验商品名、数字、停顿和语气；明确标注这是片段，不是整条配音已完成。

样音与实际文案预览分开的直接依据是 Clipchamp 的 Hear this voice 和 Preview；推荐项优先呈现可以参考 Descript。9 张卡片、用途描述和单路播放为本项目设计推论，需在本地确认 9 音色均可播放后才能验收。[Clipchamp：音色示例与文本 Preview](https://support.microsoft.com/en-us/clipchamp/how-to-use-the-text-to-speech-feature)、[Descript：Recommended voices](https://help.descript.com/ai-speech/stock-voices)

默认按整批选择一个音色；单条覆盖放进对应脚本详情。基础界面不展示模型目录、设备选择、seed、音频采样率，也不要求 9 个声音各自生成整条配音。语速及停顿修正按需展开；高级设置后置来自 Clipchamp，但“批次默认 + 单条覆盖”属于本项目效率取舍。[Clipchamp：Advanced settings](https://support.microsoft.com/en-us/clipchamp/how-to-use-the-text-to-speech-feature)

### 4.4 自动制作：一个主动作，后台状态说明正在做什么

确认脚本与音色后，主动作应为**“制作 3 条视频”**，按钮旁展示实际选取脚本数与音色。系统按顺序生成真实配音，按实际声音时长匹配素材、合成字幕并渲染预览；完成一条就显示一条，不要求用户分别点击“生成配音”“编排”“应用时间线”“生成字幕”“渲染预览”。完整草稿先行的模式参考 Pictory，执行细节由本项目本地引擎完成。[Pictory：自动 storyboard 制作](https://pictory.ai/academy/how-to-turn-script-into-video-pictory-ai)

状态文本应是可理解的业务反馈，例如“正在为第 2 条制作配音”“已完成 2/3 条”，并显示当前条目的失败原因和“重试这条”。只有确有可测的总量才显示百分比；未知时长不要给虚假的剩余秒数。系统状态应持续可见，错误应指出恢复办法。[NN/G：系统状态可见、识别与恢复错误](https://www.nngroup.com/articles/ten-usability-heuristics/)

局部修正的影响应具体表达：换字幕文字、换画面、换音色分别会影响哪些产物，重新制作受影响的条目，保留其他有效结果。HeyGen 公开文档区分局部修改和结构重做；本项目是否能精确复用缓存需要按现有版本/快照实现验证，不把文档模式直接当作现有能力。[HeyGen：Editing your video](https://help.heygen.com/en/articles/12402907-how-to-get-started-with-video-agent)

缺素材、读音错误、渲染失败应在当前条目内提供处理入口。不能用静音文件假装配音成功，也不能用首帧图片代替视频审核，不能因为预计文案 30 秒就宣称实际音频已对齐。

### 4.5 批量结果页：播放、纠错、审核、导出同一条作品

每个短视频变体是一张独立结果卡片，包含可播放视频、卖点/脚本摘要、音色、实际时长、当前版本、制作状态、审核状态。“已生成”和“已通过”是不同状态。项目卡片视觉检索参考 VEED；逐条独立身份参考 Canva；审核门槛是本项目需求，不声称这些竞品都有同样审核制度。[VEED：项目卡片](https://support.veed.io/en/articles/11641509-how-veed-s-dashboard-works)、[Canva：逐项预览与命名](https://www.canva.com/help/bulk-create/)

卡片保留三个常用动作：**播放、修改、审核通过**。修改后在同页展开“改文案/读音”“换这个画面”“改字幕”，播放器旁展示相应内容；时间线精修作为按需入口。字幕在 Transcript 中直接编辑参考 Adobe Express，单个场景纠错参考 HeyGen。[Adobe Express：Transcript 编辑](https://helpx.adobe.com/express/web/video-creation-and-editing/edit-videos/caption-video.html)、[HeyGen：Edit a specific scene](https://help.heygen.com/en/articles/12402907-how-to-get-started-with-video-agent)

批量栏至少显示“待审核 3 条 / 已通过 2 条 / 失败 1 条”。用户选取后显示“已选 2 条”，动作写清“导出所选已通过视频”；选取范围与是否通过同时可见。少量作品优先卡片，大批次可切紧凑列表；不要把默认视图变成素材 ID、revision、job_id 等技术字段表。[Carbon：Data table 的选择与批量操作](https://carbondesignsystem.com/components/data-table/usage/)

审核必须针对**可播放的实际声画字幕成片及其版本**。Canva 明确说明批量预览的视频只展示冻结首帧，因此它的预览模式可借鉴“批前核对映射”，不可用来证明口播、字幕、节奏或混音已审核。[Canva：Preview limitations](https://www.canva.com/help/bulk-create/)

## 5. 不适合本项目的模式及原因

| 模式 | 为什么不适合默认流程 | 可以保留的范围 | 来源 |
| --- | --- | --- | --- |
| 通用产品的多层工具发现入口 | 用户已经知道要做商品短视频，再选择 AI 分类、工具、子工具增加寻路 | 其他创作工具放独立入口；本入口直接开始创作简报 | [CapCut 的 All tools / AI Writer 步骤](https://www.capcut.com/resource/script-writing-ai) |
| 字段连接、拖放映射作为日常前置步骤 | 适合通用模板平台；电商固定字段可由系统转换，团队不应学习映射机制 | 内部模板管理员可在高级区域配置 | [Canva Bulk Create](https://www.canva.com/help/bulk-create/) |
| 选声音立即正式生成 | 比较声音是探索动作；未经确认的脚本可能反复消耗本地生成时间 | 明确区分试听和制作，保留选取即听样音 | [Descript 自动 TTS 描述](https://help.descript.com/ai-speech/stock-voices) |
| 依赖用户写专业长提示词 | 将场景结构、风格和技术编排的负担重新交给用户 | 补充说明可选；常用目标用业务字段 | [HeyGen 提示指南](https://help.heygen.com/en/articles/13566094-video-agent-prompting-guide) |
| 全自动研究、自动采用商品文案 | 无法替代交付团队核对商品事实；过早把草稿当成可发布内容 | 先产候选和计划，确认后执行 | [HeyGen Chat / Autopilot 的分支](https://help.heygen.com/en/articles/12402907-how-to-get-started-with-video-agent) |
| 股票素材或 AI 生成视觉默认混入 | 当前需求是授权素材混剪，默认扩展来源改变交付边界 | 有明确新需求再增加来源选择 | [Pictory 的素材来源分支](https://pictory.ai/academy/how-to-turn-script-into-video-pictory-ai) |
| 数字人或 avatar 强制起步 | 本轮已暂停，出现必选项直接阻断真实配音混剪主线 | 独立能力恢复时再设计入口 | [HeyGen avatar 起步](https://help.heygen.com/en/articles/12402907-how-to-get-started-with-video-agent) |
| 时间线和生成模型先于内容进入首屏 | 适合专业创作者逐镜头控制，但团队此任务主要核对商品内容与成片 | 单条精修展开时间线；环境设置集中到应用设置 | [LTX 官方教程的模型与 Storyboard 环节](https://ltx.io/blog/ltx-studio-tutorial) |

## 6. 建议的验收标准

以下为后续原型/实现的可验证目标，不代表本轮已经完成测试：

1. **首次进入可理解**：让一位未参与实现的内部成员指出商品资料入口、素材入口、下一步动作；无需解释“批次交接”“时间线修订”等内部概念。
2. **候选比较不跳页**：5 条脚本摘要全部可找到；选 3 条、修改其中 1 条并确认；制作按钮的数量必须为 3。
3. **声音真正可比较**：9 个音色均有名称、实际听感描述与可播放样音；样音使用相同文本；当前文案试听能够听到实际商品名；仅试听不把整批标记为已配音。
4. **一次启动完整制作**：确认后一次动作产生真实带声、带字幕的预览；用户不需要手动导入刚生成的配音或创建字幕轨。
5. **结果在同一上下文纠错**：播放发现字幕错误后可在同页改字并重新制作受影响视频；该条原审核失效，其他条的有效版本仍可查。
6. **失败不会污染完成状态**：人为制造一条素材不可读/配音失败，界面显示哪条失败及恢复动作；其他成功条继续可播放。
7. **导出范围明确**：待审核、通过、失败的数量可见；导出只包含所选且审核针对当前版本的真实产物，导出文件名能对应商品和变体。
8. **专业能力仍可找到**：普通制作路径无需打开时间线，高级用户可从单条结果进入精修；技术日志不承担主流程操作。

验证方式应是使用真实本地配音与渲染产物跑一次小批次，并记录完成任务所需的页面切换、重复填写和人工交接次数。公开文档只能支持模式选择；本项目是否变得更容易理解，最终需要任务观察和实际完整链路验证。

## 7. 推荐优先级

- **优先 1：统一业务入口和主动作。** 消除模块之间的人工交接，把系统已经拥有的数据带入下一环节。依据：[NN/G 渐进披露](https://www.nngroup.com/articles/progressive-disclosure/)。
- **优先 2：脚本候选原位比较与确认。** 5 条可见摘要，完整文案在同页编辑；候选、已选、已确认状态清楚。依据：[CapCut Review / Apply](https://www.capcut.com/resource/script-writing-ai)。
- **优先 3：9 音色实际试听。** 听样音快速比较，试听当前脚本核对读音；正式制作独立动作。依据：[Clipchamp Hear this voice / Preview](https://support.microsoft.com/en-us/clipchamp/how-to-use-the-text-to-speech-feature)。
- **优先 4：一键产出完整草稿。** 配音、素材编排、字幕、渲染自动串联，状态可见、单条可恢复。依据：[Pictory 自动 storyboard](https://pictory.ai/academy/how-to-turn-script-into-video-pictory-ai)、[NN/G 状态与恢复](https://www.nngroup.com/articles/ten-usability-heuristics/)。
- **优先 5：带声音的批量审核。** 独立视频卡片、当前版本、具体纠错入口、数量明确的通过项导出。依据：[Adobe Express 字幕修订](https://helpx.adobe.com/express/web/video-creation-and-editing/edit-videos/caption-video.html)、[Carbon 批量操作](https://carbondesignsystem.com/components/data-table/usage/)。

以上为流程与交互研究，未比较源代码许可或可直接移植性；GitHub 复用选型应单独以固定提交、模块、依赖和许可核验，不能把商业产品帮助文档或截图当作可复制代码授权。
