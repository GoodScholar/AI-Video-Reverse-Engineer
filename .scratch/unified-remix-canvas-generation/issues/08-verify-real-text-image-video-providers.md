# 08: 验证文本图片视频真实 Provider 闭环

**What to build:** 为文本、图片和视频各选择至少一个真实 Provider，完成从用户披露、提交、状态读取或流式响应、取消语义、失败恢复、结果保存到下游使用的真实验收，并让界面支持声明严格跟随证据。

**Blocked by:** 07: 收口 Provider 路由、取消、恢复与支持声明

**Status:** ready-for-human

## Scope

- 使用合法凭据和可外发测试输入，对每种模态执行至少一个真实端到端运行。
- 对 UI 中所有计划显示为“已支持”的 Provider 分别做真实冒烟；无法提供凭据或证据的保持未配置、未验证或阻塞。
- 保存脱敏请求摘要、运行身份、能力快照、取消/失败恢复、真实结果和下游引用证据。
- 把结果回填 G09–G20 及相关 A/X 项，不以模拟 Provider 代替。

## Source reuse prerequisite

- 逐个核对 Provider 官方文档、服务条款、当前模型能力、数据政策和费用边界。
- 复用已建立的适配器和安全存储；不为冒烟编写绕过统一门面的临时脚本作为唯一证据。

## Acceptance criteria

- [ ] 文本真实 Provider 完成提交/流式、取消或其真实不支持语义、失败恢复、结果保存和明确采用。
- [ ] 图片真实 Provider 完成适用的文生图、参考/编辑能力、取消、结果入库和下游引用。
- [ ] 视频真实 Provider 完成适用的文生/图生、异步查询、取消语义、重启恢复、结果入库和下游引用。
- [ ] 每次真实运行保存 Provider、模型、能力、输入、披露、配置和调用来源快照，且不包含凭据。
- [ ] UI 只把逐个通过真实冒烟的 Provider 标为已支持；缺凭据或失败项保持阻塞并留在分母。
- [ ] 真实媒体可从项目素材预览，并至少被一个画布或混剪测试场景稳定引用。

## Verification evidence

- E3 真实 Provider 运行记录和真实文本/图片/视频结果。
- E4 人工确认输出可读/可播放、来源正确、取消与费用提示真实。
- 回归测试证明关闭真实凭据后不会泄漏、伪造支持或静默回退。

## Out of scope

- 要求所有市场 Provider 在本票完成接入。
- 将缺少凭据的阻塞项标记为明确不适用。

## Comments

### 2026-10-01 条件预检、代码核查与阻塞记录

- 本次未发送任何真实 Provider 请求，也没有写入 Provider、项目素材、运行、审核或交付真相源；因此没有 E3/E4，也没有把任何正式矩阵项改为“通过”。所有 Provider 身份、模型标识、远端地址、提示词与凭据值均未输出或记录。
- 已核对依赖提交：文本 `69402fe`、图片 `1d53958`、视频 `c15f3c0`、路由与安全收口 `0355e4b`。当前正式装配在 `backend/app/main.py` 只注册 `analysis-structured-text` 和 `configured-image` 到统一 `GenerationService`；没有任何生产 `VideoGenerationAdapter` 注册。图片适配器 `backend/app/image_generation_adapter.py` 只接受 `credentialState=configured` 且 `capabilitySource=verified` 的目录项；用户自行填写能力或未知能力均不会显示或提交为已支持。
- 脱敏预检：默认启动数据目录 `./data` 缺失。仓内另一运行数据目录存在分析 Provider 配置与凭据、且已有连接验证，但可被统一文本门面选择的已验证精确文本目录项为 0；另有文本节点配置和凭据，但其模型验证项为 0，且该节点服务并未注册为本票要求的统一文本门面。图片配置缺失；视频正式适配器与目录均缺失。没有发现运行中的本项目服务进程。以上只说明当前工作区可见运行条件，不能替代目标部署环境的配置核验。
- 官方资料的核查必须针对最终由人工批准、实际配置的 Provider/模型完成；现有实现仅给出候选协议而不构成选择。文档级候选核查：百炼 `qwen3.7-flash` 的官方页确认 OpenAI 兼容 Chat Completions、文本流式返回和当前上下文/价格边界（[模型页](https://help.aliyun.com/zh/model-studio/qwen3-7-flash)、[Chat Completions 文档](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)、[价格表](https://help.aliyun.com/zh/model-studio/model-pricing)）。普通 Chat 文档未提供服务端任务查询/取消；只有独立的 [Batch API](https://help.aliyun.com/zh/model-studio/batch-inference) 有查询/取消语义，且不适合作为单条冒烟的替代。百炼 [隐私说明](https://help.aliyun.com/zh/model-studio/privacy-notice) 与 [服务协议](https://terms.alicdn.com/legal-agreement/terms/common_platform_service/20230728213935489/20230728213935489.html) 说明普通 API 数据不用于训练、会因合规与服务需要保留，固定保留天数未公开；特定 Token/Coding Plan 有不同的数据使用例外，不能与通用 API Key 混同。火山方舟图片接口文档说明图片生成请求、Base64 响应选项、参考图约束与用量字段（[官方 API 文档](https://docs.volcengine.com/docs/ark/image-generation-api?lang=en)）。尚无已配置的视频 Provider，故不能把任一视频服务的营销或文档能力映射为项目支持能力。最终真实运行前仍须逐项确认所选服务的当期条款、数据处理政策、可外发输入权利、地域与计费边界。
- 可执行候选的边界已进一步冻结，仍不是通过证据：百炼的项目文本适配器当前声明 `streaming=false`、`cancel=false`，即使 Provider 文档支持流式也不得在 UI 中反推为项目已支持；其真实冒烟可在人工配置该精确模型、最小 token 预算与合法输入后按“同步/远端取消不支持”验收。火山方舟图片候选可考虑 Seedream 5.0 flash（官方 [价格页](https://docs.volcengine.com/docs/ark/model-pricing?lang=zh) 当前列单图费用），但必须先完成精确模型、尺寸/比例/参考数和版本的官方能力快照，并取得适用 [服务条款](https://docs.volcengine.com/docs/Legalagreements/VolcanoEngineTermsofService?lang=zh) 与账户数据条款确认；当前公开资料没有被本票采用为“默认不训练”的保证。视频方面，Seedance 2.0 的官方 [创建任务 API](https://docs.volcengine.com/docs/ark/create-video-generation-task-api?lang=zh&redirect=1) 与 [查询/取消语义](https://docs.volcengine.com/docs/ark/list-video-generation-tasks-api?lang=zh) 技术上匹配异步恢复，且仅 `queued` 状态允许取消；但当前 [生视频模型使用规则与数据授权协议](https://docs.volcengine.com/docs/ark/doubao-video-generation-model-rules-and-data-authorization-agreement?lang=zh) 写有非商业用途及输入/输出数据授权限制。项目目标为商用，且没有生产视频适配器，因此它只记录为“技术候选/条款阻塞”，不允许作为本票真实商用候选。
- 回归证据（仅 E2）：`PYTHONPATH=. .venv/bin/pytest -q tests/test_generation_service_review.py tests/test_generation_service_images.py tests/test_generation_service_videos.py tests/test_generation_provider_routing.py tests/test_aigc_content_api.py tests/test_image_generation.py tests/test_text_generation.py` → `158 passed, 1 warning`；`npm test -- --run src/AigcCreator.test.tsx src/ImageGenerationPanel.test.tsx src/VideoGenerationPanel.test.tsx src/CreativeCanvasGenerationSettings.test.tsx src/TextGenerationSettings.test.tsx src/ImageGenerationSettings.test.tsx src/GenerationRoutingPolicyControl.test.tsx` → `7 files / 55 tests passed`。它们覆盖未配置、未验证能力、无静默回退、取消/恢复合同与密钥不回显；不作为真实运行或人工验收证据。
- 验收合同仍有效：`PYTHONPATH=. .venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/validate_acceptance.py validate` 返回 `valid: true`、`138` 项均为“阻塞”；`PYTHONPATH=. .venv/bin/pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_acceptance_contract.py` → `15 passed`。G09–G20、A12、X08、X11–X13 仍在分母中，未缩小或误标不适用。
- 阻塞解除所需人工输入/操作：在实际运行数据目录的系统安全存储中配置并批准一套可合法调用的文本、图片和视频 Provider/模型；为每个模态确认最小成本上限、可外发的无敏感测试输入与参考媒体权利；为视频选择并接入生产异步适配器（含官方能力快照、查询、取消与恢复语义）；完成真实结果的项目素材安装、下游引用，并由人工记录可读/可播放、来源、取消与费用提示的 E4 审核。完成后才能用统一门面执行本票的真实闭环。
