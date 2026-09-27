# AIGC 全新画面与自动配音接入调研

核验日期：2026-09-25（中国标准时间）。范围：第二阶段电商商品短视频；仅依据供应商官方文档、服务协议和本仓库代码。**本文件是接入候选调查，不表示账号已开通、协议已确认、实际调用已通过或客户素材已获授权。** 价格为核验时公开刊例价或标注的资源包价；最终以业务账号合同、控制台和账单为准。

## 结论与验证顺序

1. **先核验阿里云百炼账号与商用条款。** 百炼在华北 2（北京）提供万相图像与图生视频、千问语音合成 API。先用有书面授权、无真人肖像的商品图小样，验证 `wan2.7-image` 图生图与 `wan2.7-i2v` 的商品一致性；再验证 `qwen3-tts-flash` 的中文配音和音画合成。相应模型的公开价分别为 **0.20 元/张、720P 0.60 元/秒、0.80 元/万字符**；请求失败与多张/多秒计费细则见各模型及总价目表。以上是技术选型建议，并非任何生成内容自动取得可商用权利。[万相图像 API](https://help.aliyun.com/zh/model-studio/wan-image-generation-and-editing-api-reference)、[万相图生视频模型](https://help.aliyun.com/zh/model-studio/wan2-7-i2v)、[百炼价目表](https://help.aliyun.com/zh/model-studio/model-pricing)、[百炼服务协议](https://terms.alicdn.com/legal-agreement/terms/common_platform_service/20230728213935489/20230728213935489.html)。
2. **火山方舟 Seedream 可作图像质量对照，先审协议再接入。** 官方提供北京区域图片生成接口和文/图生图模型；但本次未核实客户账号所适用的 Seedream 商用授权、客户数据处理选项及方舟该模型的实际计费档位，因此不纳入首个生产接入。[方舟图片生成 API](https://docs.volcengine.com/docs/ark/image-generation-api?lang=zh)、[模型列表](https://docs.volcengine.com/docs/ark/model-list?lang=zh)。
3. **火山 Seedance 视频暂不用于客户营销成片。** 方舟官方《豆包生视频模型使用规则及数据授权使用协议》在 2026-08-06 更新的公开文本中明确写有“仅可”用于研究、学术等非商业目的，并要求用户在使用前接受覆盖输入与输出数据的授权；若考虑此路线，必须先取得供应商对当前客户账号、具体模型和商业用途的书面澄清。[专用规则](https://docs.volcengine.com/docs/ark/doubao-video-generation-model-rules-and-data-authorization-agreement?lang=zh)。
4. **本地 ComfyUI 仅作可选对照。** 仓库已有只接收本机/私网地址的 `ComfyUIClient` 和实验性工作流，但界面写明未在当前机器验证真实 Queue、不会安装模型或节点。ComfyUI 引擎与具体权重/节点的许可不同，不能凭引擎可运行推断生成素材可用于客户广告。[ComfyUI 官方仓库](https://github.com/Comfy-Org/ComfyUI)、[官方许可](https://github.com/Comfy-Org/ComfyUI/blob/master/LICENSE)、本仓库 `backend/app/comfyui_client.py` 与 `frontend/src/ReproductionPanel.tsx`。

## 画面候选

| 候选 | 官方 API 能力、输入和输出 | 中国大陆接入及价格 | 商用与数据边界 |
| --- | --- | --- | --- |
| 阿里百炼 `wan2.7-image` / `wan2.7-image-pro` | 支持文生图、图生图、编辑和多参考图；JSON/HTTP API；输出 PNG。标准版最高 2K，专业版的纯文生图可达 4K。商品原图、提示词及其他参考图会提交百炼。图生图更符合需要维持真实商品外观的场景，但**一致性仍须人工验收**。[API](https://help.aliyun.com/zh/model-studio/wan-image-generation-and-editing-api-reference)、[选型表](https://help.aliyun.com/zh/model-studio/image-model) | 华北 2（北京）API Key/域名与其他地域分开；标准版公开原价 0.20 元/张，专业版 0.50 元/张，按成功生成张数计费。[标准版](https://help.aliyun.com/zh/model-studio/wan2-7-image)、[专业版](https://help.aliyun.com/zh/model-studio/wan2-7-image-pro)、[API 地域说明](https://help.aliyun.com/zh/model-studio/wan-image-generation-and-editing-api-reference) | 百炼协议要求输入素材具备权利或授权；阿里自有模型的合成内容权属受协议 7.5 的前提与法律限制约束，生成物能否获知识产权、能否对外商业化由用户判断并承担责任；平台可为提供服务存储和使用上传内容，并可审核输入与输出。[百炼协议 4.3、7.4–7.7](https://terms.alicdn.com/legal-agreement/terms/common_platform_service/20230728213935489/20230728213935489.html) |
| 阿里百炼 `wan2.7-i2v` | 以图像加提示词生成 2–15 秒、720P/1080P、MP4 视频；输出宽高比尽量保持首帧比例，竖屏商品图需实测 9:16 成片；异步任务提交后须查询结果。输入商品图/提示词被发送给百炼，输出应下载后留存并记录来源。[视频模型表](https://help.aliyun.com/zh/model-studio/video-generate-edit-model)、[万相 2.7 图生视频 API](https://help.aliyun.com/zh/model-studio/image-to-video-general-api-reference) | 北京原价 720P 0.60 元/秒、1080P 1 元/秒；输出视频秒数计费。北京可用不等于本账号已获准调用。[模型信息](https://help.aliyun.com/zh/model-studio/wan2-7-i2v)、[总价目表](https://help.aliyun.com/zh/model-studio/model-pricing) | 同百炼协议。视频模型不会证明商品功效、尺寸、材质或包装细节真实；必须与商家已确认事实和原图逐镜核对。[百炼协议](https://terms.alicdn.com/legal-agreement/terms/common_platform_service/20230728213935489/20230728213935489.html) |
| 火山方舟 Seedream 5.0/4.5/4.0 | `POST /api/v3/images/generations`，支持文本、单张或多张参考图加提示词的图像生成与编辑；官方模型列表列出各版本能力。参考图与文本会进入方舟服务。[图片 API](https://docs.volcengine.com/docs/ark/image-generation-api?lang=zh)、[模型列表](https://docs.volcengine.com/docs/ark/model-list?lang=zh) | 官方接口位于 `ark.cn-beijing.volces.com`，需方舟 API Key、开通对应模型；不同版本与购买方式价格不同。本次找到的 **LAS 算子** 价目表列 Seedream 5.0 为 0.22 元/张、4.5 为 0.25 元/张、4.0 为 0.20 元/张，但这**不是方舟直连 API 的报价**，不可直接据此预算直连成本。[LAS 专属计费](https://docs.volcengine.com/docs/LakeAIService/Largemodelbilling?lang=zh)、[方舟模型列表](https://docs.volcengine.com/docs/ark/model-list?lang=zh) | 当前未取得适用于目标账号的 Seedream 模型订购协议及客户数据处理承诺；商用、存储/训练及权属须先核对账号所签版本。不能以 LAS 或方舟其他模型的协议代替。[方舟接口](https://docs.volcengine.com/docs/ark/image-generation-api?lang=zh) |

阿里百炼文生视频 `wan2.7-t2v` 也是全新场景补镜候选：输入提示词，输出 720P/1080P、2–15 秒 MP4，支持 9:16；但仅凭文字生成的画面不能冒充真实商品实拍，优先用于无商品事实细节的场景镜头。[文生视频 API](https://help.aliyun.com/zh/model-studio/text-to-video-api-reference)、[模型表](https://help.aliyun.com/zh/model-studio/video-generate-edit-model)。

## 自动配音候选

| 候选 | 官方 API 能力、输入和输出 | 中国大陆接入及价格 | 权利与审核 |
| --- | --- | --- | --- |
| 阿里百炼 `qwen3-tts-flash` | 非实时 HTTP 接收已审核文案与所选系统音色，返回音频文件 URL；官方说明 URL 有效期 24 小时，应及时下载入项目并保存模型/音色/文案版本。无需客户声音样本；若另做声音复刻，必须单独核对声音授权。[非实时语音合成](https://help.aliyun.com/zh/model-studio/non-realtime-tts-user-guide)、[语音模型表](https://help.aliyun.com/zh/model-studio/tts-model) | 北京公开价 0.80 元/万输入字符，输出不计费；北京地域有 1 万字符免费额度（须核对开通/有效期）。Python 可走官方 DashScope SDK 或 HTTP，和现有 Python 后端兼容。[百炼价目表](https://help.aliyun.com/zh/model-studio/model-pricing)、[非实时指南](https://help.aliyun.com/zh/model-studio/non-realtime-tts-user-guide) | 文案、音色选择及由此生成的语音进入百炼；不得假定系统音色以外的真人声音可复刻。合成语音同样需要商家核对商品事实与发布用途。[百炼协议](https://terms.alicdn.com/legal-agreement/terms/common_platform_service/20230728213935489/20230728213935489.html) |
| 火山引擎豆包语音合成大模型 2.0（`seed-tts-2.0`） | 官方语音 API 使用 APP ID、Access Token 与 Resource ID，`seed-tts-2.0` 为指定语音合成资源；可调用大模型语音合成接口，文本与音色信息发送至火山语音服务，返回语音数据。Python 调用示例由官方提供。[鉴权](https://docs.volcengine.com/docs/6561/2534847?lang=zh)、[语音合成接口指南](https://www.volcengine.com/docs/6561/2228192?lang=zh)、[能力介绍](https://www.volcengine.com/docs/6561/1257543?lang=zh) | 中国大陆火山语音控制台需开通服务/应用并取得凭证；公开产品页展示“语音合成大模型”10 万字包原价 45 元、新客 22.50 元，但应在控制台核对它是否适用于选定 2.0 音色和计费方式。未核到当前直连接口统一后付费单价，不以其他产品的 3 元/万字符报价代用。[产品页](https://www.volcengine.com/product/TTS)、[开通指南](https://www.volcengine.com/docs/6561/1167802?lang=zh) | 本次未核实目标账号所签语音服务协议对商用输出与文本留存的具体约定。首轮只比较系统音色，不上传真人声样；声音复刻需另取声源本人/权利人授权并核对服务商条件。[声音复刻官方介绍](https://www.volcengine.com/docs/6561/133350?lang=zh)、[开通指南](https://www.volcengine.com/docs/6561/1167802?lang=zh) |

## AI 音乐生成候选（补充，2026-09-25）

目标是给中国大陆客户的营销短视频制作**可单独试听、剪裁和混音的背景音乐文件**。以下两条均有官方生成 API 文档，但公开 API 与计费页面**不能单独证明输出可用于客户广告或跨平台发布**；商用范围须以业务账号实际订购协议及供应商书面答复核准。

| 候选 | API、时长和格式 | 接入与计费 | 商用、版权及数据边界 |
| --- | --- | --- | --- |
| 阿里百炼 Fun-Music `fun-music-v1` | 北京业务空间的 HTTPS `POST /api/v1/services/audio/music/generation`；以 `prompt` 指定风格，`is_instrumental=true` 可生成无人声音乐，`format` 可选 MP3/WAV；非流式结果给出 24 小时有效的音频 URL 与实际计费秒数。API 参考中**未列可指定精确时长的参数或保证时长**，因此短视频配乐需在后期剪裁/循环并试听接缝。[API 参考](https://help.aliyun.com/zh/model-studio/fun-music-api)、[用户指南](https://help.aliyun.com/zh/model-studio/fun-music) | 当前**邀测**，需在模型广场申请，且仅华北 2（北京）可用。公开原价 `fun-music-v1` **0.002 元/成功输出秒**、`fun-music-preview` **0.005 元/秒**；价目表仅为 `preview` 列出北京 1000 秒免费额度，不能把该额度算给 `v1`。[API 参考](https://help.aliyun.com/zh/model-studio/fun-music-api)、[价目表“音乐生成”](https://help.aliyun.com/zh/model-studio/model-pricing) | 音乐描述及可选自写歌词发送百炼，生成文件暂存于其音频 URL；公开文档未给出此模型输入/输出是否用于训练、完整留存期及邀测附加条款。百炼通用协议要求对输入拥有权利，且仅在协议 7.5 的适用前提下谈及合成内容知识产权归属；7.6 要求用户自行评估商业化侵权风险，7.7 提醒输出可能与他人相似。**不可由付费或技术可用推断取得客户广告、歌曲版权或独占授权。**[百炼协议 7.4–7.7](https://terms.alicdn.com/legal-agreement/terms/common_platform_service/20230728213935489/20230728213935489.html) |
| 火山引擎 AI 音乐生成大模型“生成纯音乐” | 官方提供纯音乐 BGM 生成及任务查询 API；纯音乐**不超过 60 秒**，人声歌曲 standard 为 30–240 秒。`QuerySong` 使用北京区域 `open.volcengineapi.com`，返回音频 URL、时长及任务状态；默认 WAV，视频云链路可能变为 MP4，须下载、校验并转为所需格式，URL 标示有效期 1 年。提交接口的具体参数仍须按开通后的官方对应文档逐项核对。[计费说明](https://docs.volcengine.com/docs/Audiovideounderstandingandprocessing/Productbilling?lang=zh)、[查询任务 API](https://docs.volcengine.com/docs/Audiovideounderstandingandprocessing/Querytask-1?lang=zh) | 官方快速接入说明当前只支持**企业认证用户购买**，使用 AK/SK；中国大陆服务范围见官方 SLA。公开后付费**0.002 元/成功生成秒**；预付费纯音乐试用版 200 首、标价 200 元，企业首次可 0 元购买一次，但试用资格或试用内容商用范围尚未核实。[快速接入](https://www.volcengine.com/docs/84992/1404668)、[计费说明](https://docs.volcengine.com/docs/Audiovideounderstandingandprocessing/Productbilling?lang=zh)、[SLA](https://www.volcengine.com/docs/84992/1404653) | 提示词及若选歌曲时的歌词进入火山服务；结果 URL 指向火山视频云域名。官方查询示例列有 `InputLyricsPlagiarized` 失败原因，说明需处理输入抄袭审核。公开的[该产品“生成式模型服务专用条款”页面](https://docs.volcengine.com/docs/Audiovideounderstandingandprocessing/Generativemodelservicespecificterms?lang=zh)未在本次核到可引用正文；其他产品的同名条款不能替代目标账号合同。**客户营销使用许可、输出权属、数据训练/留存与平台发布权均待书面核实。**[查询任务 API](https://docs.volcengine.com/docs/Audiovideounderstandingandprocessing/Querytask-1?lang=zh) |

**推荐验证顺序：**先向阿里确认 Fun-Music 邀测资格、适用协议和客户广告配乐的授权，再用无第三方音乐/歌词引用的风格提示词验证纯音乐质量、实际长度与计费；若邀测或授权不满足，再核火山企业开通和音乐产品专用条款。两条路径都应将生成音乐与配音分轨保存，记录模型、提示词、商家授权和人工试听结论。上传视频号、淘宝、快手等平台前，须分别核对其**当时有效的官方上传、版权及 AI 内容标识规则**；本次未核实这些平台的具体规则，不承诺跨平台通用授权。上述验证只形成选型门槛，**尚未调用付费 API 或取得商用许可**。[Fun-Music API](https://help.aliyun.com/zh/model-studio/fun-music-api)、[百炼协议](https://terms.alicdn.com/legal-agreement/terms/common_platform_service/20230728213935489/20230728213935489.html)、[火山音乐快速接入](https://www.volcengine.com/docs/84992/1404668)。

## 供应商审查和数据发送边界

- **每次生成前展示可发送内容。** 图生图/图生视频会发送商品原图或参考图与提示词；TTS 会发送已确认口播文案与音色选择。客户未授权或包含未清除的个人信息、商标/肖像争议、保密定价/未发布活动的素材不用于云端测试。百炼协议明确要求输入素材权利与内容真实性、允许技术/人工审核；火山视频规则还涉及广泛数据授权。[百炼协议](https://terms.alicdn.com/legal-agreement/terms/common_platform_service/20230728213935489/20230728213935489.html)、[火山生视频专用规则](https://docs.volcengine.com/docs/ark/doubao-video-generation-model-rules-and-data-authorization-agreement?lang=zh)。
- **记录调用账本。** 记录服务、模型、地域、输入素材及权利证明、商家确认版本、请求 ID、失败原因、输出文件与实际费用；对模型审核拒绝、失败任务、过期 URL、内容失真设置人工处理。百炼图像按成功张数、视频按成功秒数、TTS 按字符收费；供应商实际账单仍需复核。[百炼价目表](https://help.aliyun.com/zh/model-studio/model-pricing)、[图像 API](https://help.aliyun.com/zh/model-studio/wan-image-generation-and-editing-api-reference)、[TTS 指南](https://help.aliyun.com/zh/model-studio/non-realtime-tts-user-guide)。
- **区分供应商审核与交付审核。** 供应商对输入/输出的审核、账号开通、应用/算法备案材料或专用服务规则可能另设条件；即便 API 返回成功，团队仍须核对商品真实外观、事实、配音读法和发布平台要求。[百炼协议](https://terms.alicdn.com/legal-agreement/terms/common_platform_service/20230728213935489/20230728213935489.html)、[火山方舟资质材料说明](https://www.volcengine.com/docs/82379/1326340?lang=zh)。

## 本地 ComfyUI 与现有项目

本仓库 `backend/app/comfyui_client.py` 已实现仅本机/私网 HTTP(S) 地址校验、输入上传、工作流提交与结果查询；`frontend/src/ReproductionPanel.tsx` 告知用户它不自动安装 ComfyUI、节点或模型，真实队列未在当前机器验证。这是**已有实验接口**，不是 AIGC 商品画面生产能力的验收证据。上游 ComfyUI 的引擎许可为 GPL-3.0；所选模型权重、LoRA 和节点应分别核许可、算力与质量。上游支持 macOS Apple Silicon 安装，但机器实际可跑哪种模型、耗时及商用品质仍须本地测量。[ComfyUI 官方仓库](https://github.com/Comfy-Org/ComfyUI)、[官方许可](https://github.com/Comfy-Org/ComfyUI/blob/master/LICENSE)。

## 尚未核实，实施前必须解决

1. 百炼与火山目标业务账号的实名、地域、模型开通状态、当前合同、实际扣费和并发额度；本次未登录控制台或发起任何付费请求。
2. 阿里图像/视频/语音输入的实际留存、模型训练使用选项与客户数据处理附约；公开服务协议不足以替代业务账号所签合同。[百炼协议](https://terms.alicdn.com/legal-agreement/terms/common_platform_service/20230728213935489/20230728213935489.html)。
3. 火山 Seedream 的当前商用与数据条款、直连方舟计费；火山 Seedance 商业用途限制能否通过特定版本的书面合同变更。未解决前不得用于客户广告。[火山生视频专用规则](https://docs.volcengine.com/docs/ark/doubao-video-generation-model-rules-and-data-authorization-agreement?lang=zh)。
4. 客户商品素材和声音样本的授权、商品真实外观保持、生成内容标识方式、平台发布审核；需以具体试点素材和发布渠道验收。[百炼协议](https://terms.alicdn.com/legal-agreement/terms/common_platform_service/20230728213935489/20230728213935489.html)。
5. 本地 ComfyUI 是否安装、可用模型/权重许可、GPU 性能与工作流稳定性，当前都未做真实环境验证。见本仓库 `frontend/src/ReproductionPanel.tsx`。
