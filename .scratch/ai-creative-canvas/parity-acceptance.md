# AI 创作画布参考源码对齐与验收差异（2026-09-30）

**状态：未达标。** 本文是实施用的差异清单，不是完成声明。当前无可用图片生成凭据，也没有真实服务出图、结果入库及结果续创的端到端证据。用户要求商用质量及所选参考功能完整对齐；不能把现有文本/图片/生成三个节点能保存、连线和局部预览，或者协议测试通过，称作节点功能已实现或画布已 100% 对齐。此处优先落地三类节点和明确的节点操作；下文“后续未达标范围”仍属于完整目标，不能从百分比计算中移除。

## 本轮视觉适配核对（2026-09-30，方案 1）

| 项目 | 已有证据 | 尚未闭合的差异 |
|---|---|---|
| 页面及控制 | 固定 Toonflow 已在回环端口运行，使用同一产品图/文本参考截图；主页面白底、左上导航、左下地图/吸附/边显示/适配/缩放控件已实际验证 | 完整页面同态视觉验收尚未结束；文档/Agent、完整素材库等能力未接，不用占位控件冒充 |
| 节点骨架 | 220px节点、卡外14px标题/32px标题栏、原图比例、图标空态、文本16px/110px预览 | 分组与节点尺寸调整正在接入，完整节点类型仍缺 |
| 生成编辑区 | 660px提示词卡、48px引用缩略图、16px/70px输入区、32px底栏 | x-sender 引用标签、@/回车插入、重排后保留 edge 身份和保存已实测；模型能力目录与完整多模态引用仍未完成 |
| 节点操作 | 右键复制/副本/剪贴板、跨项目素材限制、下载/替换/预览已接；裁切及2×2切分真实入库入图保存通过 | 嵌套组剪贴板正在复审；蒙版/超分/多角度及完整工具偏好仍缺；来源可视边未接 |
| 文本节点 | 独立文本编辑弹窗、即时编辑与撤销已验证 | 文本生成未接；完整引用编辑能力仍缺 |

定向测试及逐项浏览器证据记录于 `progress.md`。旧 `visual-realignment-*.png` 是历史混合方案证据，不能用于本次统一主视觉验收；新参考截图为 `toonflow-reference-*.png`，裁切切分宿主证据为 `toonflow-crop-split-host.png`。以下完整差异均继续有效，不将样式或局部功能变化标记为商用交付。

## 固定源码与复用边界

| 来源 | 固定版本、许可证及亲读功能源码 | 直接依赖与接入判断 |
|---|---|---|
| Toonflow | [HBAI-Ltd/Toonflow-app@d86cce6](https://github.com/HBAI-Ltd/Toonflow-app/tree/d86cce6b689916ab73f75bc4c1f42a2a7424516c)，[MIT LICENSE](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/LICENSE)；[imageGenerationNode](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodes/imageGenerationNode/src/index.vue)、[generationSettings](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodes/imageGenerationNode/src/components/generationSettings.vue)、[imageNode](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodes/imageNode/src/index.vue)、[textNode](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodes/textNode/src/index.vue)、[nodeSkeleton](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodeScaffold/src/nodeSkeleton.vue)、[nodeInputs](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodeScaffold/src/nodeInputs.ts)、[useNodeReferences](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodeScaffold/src/useNodeReferences.ts)、[useNodeGeneration](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodeScaffold/src/useNodeGeneration.ts)、[promptInput](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodeScaffold/src/components/promptInput.vue)。 | Vue/Vue Flow、Element Plus、x-sender、Toonflow workspace files/AI runtime 不能直接装进本项目 React/XYFlow + FastAPI。可复制纯算法、交互结构与样式规则，替换组件壳及后端接线；版权/版本须在实际复制文件旁保留。已适配节点骨架、页面控件、搜索、嵌套组与整理，并直接接入固定 x-sender 1.4.6；详见 `vendor/Toonflow/SOURCE.md`，完整节点运行与多模态能力仍未对齐。 |
| Infinite Canvas | [basketikun/infinite-canvas@dab19ad](https://github.com/basketikun/infinite-canvas/tree/dab19adc0847e32e39b7fc8ff90cb392561fb826)，[MIT LICENSE](https://github.com/basketikun/infinite-canvas/blob/dab19adc0847e32e39b7fc8ff90cb392561fb826/LICENSE)；[canvas-node-hover-toolbar](https://github.com/basketikun/infinite-canvas/blob/dab19adc0847e32e39b7fc8ff90cb392561fb826/web/src/components/canvas/canvas-node-hover-toolbar.tsx)、[canvas-image-toolbar-tools](https://github.com/basketikun/infinite-canvas/blob/dab19adc0847e32e39b7fc8ff90cb392561fb826/web/src/components/canvas/canvas-image-toolbar-tools.tsx)、[canvas-node](https://github.com/basketikun/infinite-canvas/blob/dab19adc0847e32e39b7fc8ff90cb392561fb826/web/src/components/canvas/canvas-node.tsx)。 | React 工具定义、浮动定位和动作分组可按许可复制并接本项目节点/项目素材状态；原文件依赖 Ant Design、i18n、plugin registry、canvas store、自有节点与批量图片模型，不能无适配整包迁入。不能仅做一个“查看”按钮就称工具栏已对齐。 |

其他项目（XYFlow、BeatDesign、ComfyUI、tldraw、AFFiNE/BlockSuite、Excalidraw、ComfyStudio、OpenStory、Huobao Drama）仍是项目 `AGENTS.md` 指定的综合参考；本审计没有把它们逐功能源码核查完毕，故不得据此声称全项目综合参考 100% 对齐。现有 XYFlow 画布继续作为布局与连线基础，不强制替换引擎。

## 整改前代码事实（保留差异起点）

以下是本轮开始时的源码事实，不随开发进度自动改写为完成。整改后的检查证据见 `progress.md`；逐项完整验收通过前，下表仍保持未达标。

- `frontend/src/CreativeCanvasNodes.tsx`：文本节点是 textarea；媒体节点显示项目素材并外链放大；生成节点有 prompt/image 两个输入、预览、三个固定像素尺寸、全局模型标签与生成按钮。生成节点**没有图片输出 Handle**、引用列表、节点模型选择、比例、停止或图片替换；媒体图片也没有节点内替换动作。
- `frontend/src/CreativeCanvas.tsx`：仅允许 text/media 作为来源，生成节点不能作为下游图片来源；每个目标端口限一条边；生成结果只取当前运行首张图片；分支会另建媒体节点。节点删除在检查器；无节点附近悬浮工具栏、复制、下载、快捷重试、快捷入库及可逆编辑记录。生成服务模型名来自全局配置。
- `backend/app/creative_canvas.py` 与 `creative_canvas_api.py`：节点无 `modelId`/`ratio`/结果选择；边的 `sourceHandle=image` 只准媒体节点；每端口唯一；运行快照单个 `inputAssetIds`、一个 `input_path`；只准三个硬编码尺寸；安装一张结果。只有 POST run/refresh，没有取消、替换或输出端口绑定接口。
- `backend/app/image_generation.py`：当前 OpenAI 风格 `/images/generations` JSON 与 `/images/edits` multipart、`n=1`、单图片输入并要求直接 `b64_json`。它不自动兼容火山方舟 Seedream 的 JSON 多图语义。现有豆包 coding/chat 配置也不是图片 API 授权或密钥。

## 首批节点逐项差异与可执行验收

下表“未达标”只针对参考动作的完整语义；已有局部能力单独写明，不能用同名控件冲抵。

| 行为与源码依据 | 当前缺口 | 最小复用/适配依据 | 完成时必须观察到的事实 | 现状 |
|---|---|---|---|---|
| 生成图片**输出端口**：Toonflow imageGenerationNode `handles` 定义 `in` 和 `image`，生成后写 `outputs.image`（约 94–96、202–236 行）；nodeInputs 从来源输出读取值。 | 只有两个目标端口；结果不能直接连下游生成。 | 沿用 XYFlow Handle；在持久图定义中增加 generate `image` source，运行完成后把确定的输出资产绑定为当前输出；历史 run 保持不可变。 | A 成功产图后，从 A 的 image 端口连到 B；B 在保存刷新后仍能看到/执行该输入。A 选择旧结果或替换当前结果时，B 的输入来源明确变化并标记待重跑；旧 run 快照不变。 | 未达标 |
| 多图/文本**引用列表、排序、移除及 @ 标记**：Toonflow useNodeReferences 的 `refList/referenceOrder/referenceMentions/removeReference`、nodeInputs 的多边取值；promptInput 识别 `{{ref n}}`、可预览缩略图；imageGenerationNode 31–36、140–146、202–220 行。 | 每个生成目标端口最多一边；只读取第一图；没有列表、排序、移除或引用标记。 | 复制引用顺序和来源键的纯数据规则，React 中实现列表与 prompt mention；输入来自多条有效 image/text 边及媒体/历史结果，按固定顺序冻结。需明确去重策略，不能默认单输入就是参考行为。 | 连入两张图片与一段文本；节点内出现三条可辨引用，可重排、单条移除、点击预览，prompt 的 `@`/`{{ref n}}` 与顺序保持一致；保存刷新后不乱序；运行记录中的有序 asset ID 和 prompt 快照与当次 UI 一致；缺失引用阻止提交并指出哪条。 | 未达标 |
| **按节点选模型**：Toonflow imageGenerationNode 39–57、111–139、184–199 行从 provider/model 分组列表选择并存 `data.model`。 | 所有节点只展示同一全局 `modelLabel`，`CanvasNode` 无模型字段；随全局变更历史含义不清。 | 节点保存 `providerId + modelId`（或稳定 catalog key），配置只保存供应商连接/凭据；模型目录按已配置能力返回。 | 同图两个生成节点可选不同已配置图片模型；保存刷新后各自保留；运行快照准确写入模型与配置版本；未配置或该模型不可用时控件和错误清楚，不假装可出图。 | 未达标 |
| **模型驱动尺寸与比例**：Toonflow generationSettings 以 `sizes/ratios` 动态渲染；imageGenerationNode 58–63、124–138、309–315 行校验并随模型切换修正不兼容值。 | 前端固定三个像素尺寸，后端固定三项白名单；无独立比例。 | 由能力目录决定 size/ratio 选项与组合校验；显示实际传给供应商的值/像素解释。不要把 Toonflow 的默认 2K/16:9 或现有三个像素值当任意提供商通用事实。 | 切模型后选项随目录变化；已选不兼容值被显式处理；保存刷新与运行快照一致；服务端拒绝伪造的非法组合；生成结果实际比例与记录匹配。 | 未达标 |
| **停止生成**：Toonflow imageGenerationNode 64–70、119–120、202–236 行使用 AbortController；useNodeGeneration 18–25 行明确 `cancellationRequested` 不保证供应商撤销任务/费用。 | 生成后只禁用按钮、显示等待；无取消 API、队列移除、请求中断或状态区分。 | 增加 run 取消请求/状态机：本地未发送可确认取消；已发供应商时只可记录本地中断或远端取消结果，不能把 abort 解释成已撤销计费。 | 排队取消后保证未发送；运行中点击停止后 UI 显示“停止请求中/本地已停止但云端状态未知/供应商确认取消”等真实状态；重复点击幂等；旧输出保留；服务重启和晚到响应不覆盖取消/历史决策。 | 未达标 |
| **图片替换**：Toonflow imageGenerationNode 顶部替换输入与 `replaceOutput`（13–14、160–181 行）、imageNode 上传替换及输出更新（约 11–38、116–135 行）；Infinite Canvas image-toolbar-tools `replace`（61–67 行）。 | 上传只创建新 media 节点；没有原位替换媒体节点或生成节点当前输出的可审计操作。 | 复用项目素材上传/ID 与验证；节点输出指针更新采用新资产 ID，旧结果/旧 run 保留，明示对下游连线的影响。 | 替换后节点预览与端口引用立即更新，保存刷新仍正确；撤销可回到旧 asset ID；旧素材未被静默删除，历史运行仍指向旧结果；无效格式/超限不更改输出。 | 未达标 |
| **完整图片节点悬浮工具栏**：Infinite Canvas hover-toolbar 107–215 行按节点类型与内容构建、定位、停留、更多设置；image-toolbar-tools 7–8、39–166 行给出默认/全部工具、显隐与标签配置；Toonflow nodeSkeleton 25–76 行顶部媒体工具栏。 | 媒体节点只有外链查看；删除在检查器；无悬浮、更多、显隐偏好或完整动作。 | 优先移植 Infinite Canvas 的 React 工具定义与浮动层交互，接 XYFlow 坐标、项目素材及当前 action handler；按用户确认的画布设计例外采用开源界面基准，不受旧全站配色与组件外观限制，保留可访问性。不能把不可用动作装饰性展示为可用。 | 鼠标/键盘选中后工具栏邻接节点，移动到工具栏不消失，缩放/平移/窄屏不越界；每项动作进入真实处理流，禁用有原因；可配置显隐/标签持久化；节点操作不误触画布拖动。完整动作矩阵见下节。 | 未达标 |
| **文本节点双角色**：Toonflow textNode 11–40 行展示输出、可编辑文本、引用、模型与生成；Infinite Canvas hover-toolbar 151–154 行文本生图、字号快捷动作。 | 当前只有手写 textarea 与 text source；没有文本输出版本、引用或生成，文本快捷动作缺失。 | 首批至少保留手写文本编辑/作为 prompt 源，完整参考的文本生成与动作列入后续差异，不宣称 textNode 对齐。 | 输入、保存、引用和编辑可逆；文本生成/字号等未实现动作继续显示在差异表，直至真实路径验收。 | 部分基础能力，节点未达标 |

## 浮动工具栏完整动作清单（任何未实现项均保留为差异）

来源分别为 Infinite Canvas `canvas-node-hover-toolbar.tsx` 140–161 行、`canvas-image-toolbar-tools.tsx` 7–8/43–133 行，及 Toonflow `nodeSkeleton.vue` 13–63、102–111 行。下列功能不因第一批开发范围而被隐式删除。

| 节点/动作 | 当前事实 | 验收事实与状态 |
|---|---|---|
| 通用：节点信息、删除、复制、创建副本、复制到剪贴板；Toonflow 另有刷新远端节点。 | 检查器能删除；其他均缺。 | 信息显示实际类型、来源、尺寸/状态；删除提示连接影响并可撤销；复制/副本含明确边和结果策略；刷新不得重提交生成。**未达标**。 |
| 已有媒体/文本：保存到素材库、下载、全屏/查看；失败时重试。 | 结果自动入项目素材；媒体有外链查看；没有就近保存/下载/全屏/重试。 | 操作对应当前版本/选定结果；下载实际文件；重试创建新的 run，不覆盖旧 run 或对未知结果盲重试。**未达标**。 |
| 图片：复制提示词、反推提示词、替换、锁定/自由缩放、蒙版编辑、裁切、切分、放大、超分辨率、角度、查看。 | 只有外链放大，其他缺。 | 每项操作能对指定资产/结果运行、展示处理中的状态、产生可追溯新版本，并可恢复原图；不支持的供应商能力应给出明确原因，直到有真实实现前均计未达标。**未达标**。 |
| 工具栏设置：默认可见集合、“更多”配置、显示标签、保存偏好。 | 缺。 | 重新打开页面配置不丢，动作不会因隐藏而失去通过菜单访问的路径。**未达标**。 |
| 文本：生图、字号增减；视频：编辑/上传或替换；音频：上传或替换；配置节点：打开配置；组节点：解组。 | 文本仅可手写；视频只可浏览；音频/配置/组节点不在当前三类节点内。 | 这些是参考完整清单的后续未达标范围，不得据当前三节点完成而称整个工具栏 100%。**未达标**。 |
| 批量图片：主图选择、展开、逐张查看/下载/复制/重试/删除。Infinite Canvas `canvas-node.tsx` 734–915 行。 | 当前只取一张结果并安装一张资产。 | 多结果分别有身份、状态、来源；更换主图不丢其他结果，逐张动作命中正确 asset ID。**未达标**。 |

## 推荐数据/API 契约（供实施直接采用，具体字段名可与现有代码一致）

1. **图定义版本化。** `CanvasNode(generate)` 增加 `modelSelection:{providerId,modelId}`、`imageSettings:{size,ratio}`、`selectedOutput:{runId,assetId}|null`；`CanvasNode(media)` 继续用 `assetId`，原位替换是一次可撤销的 assetId 变更。`CanvasEdge` 的 `sourceHandle` 支持 text/media/generate 的合法输出；目标生成节点采用一个多值 `in` 端口（与 Toonflow 一致），或兼容保留 `prompt`/`image` 但各允许多边，二者只选一种存储规范并做旧文档迁移。建议统一内部 `inputs:[{edgeId,sourceNodeId,sourceHandle,order}]` 视图，按稳定 `edgeId` 排序并持久化 `referenceOrder`，禁止循环、跨项目素材和非图片接图片。输出绑定必须指向真实已登记项目资产，不得只放远程 URL。
2. **运行冻结。** `POST /nodes/{id}/runs {revision,idempotencyKey}` 只读取已保存修订；服务端冻结 `providerId/modelId/catalogVersion/configurationRevision/prompt/size/ratio/orderedReferences:[{edgeId,sourceNodeId,sourceHandle,assetId?,text?,sourceRunId?}]`。`inputAssetIds` 是按顺序投影，不能成为唯一语义来源。响应持久 `runId`、状态、提交/远端任务标识（若供应商真返回）、输出资产数组；旧 run 不随 prompt/模型/引用重排、删除或输出选择改变。`GET /.../runs/{id}` 只查状态，绝不再次提交。
3. **能力目录。** `GET /api/image-generation/models` 返回已配置供应商的 `[{providerId,modelId,label,capabilities:{imageInputs:{min,max},sizes,ratios,combinations?,supportsRemoteCancel,outputCount...}}]` 及目录版本/凭据状态；只暴露能真实调用且有来源证据的模型，不用自由文本模型名伪装可选目录。保存与运行时服务端同时检查该模型支持的输入张数、尺寸/比例组合；目录变化时旧节点保持值、标“当前不可用”并要求用户重新选择，不能静默改历史。
4. **供应商适配。** 用 provider adapter 接收标准化冻结请求并返回真实结果/远端任务状态。当前 OpenAI 风格 `/images/edits` multipart 是一个独立 adapter；若用户选择火山方舟 Seedream，必须根据该服务实际接口实现 JSON 多图/模型参数映射与下载/结果登记，不复用现有 multipart 作为“已兼容”。豆包 coding/chat 配置不能隐式共享为图片服务授权；当前没有图片 API 凭据，真实链路验收保持阻塞。
5. **取消语义。** `POST /runs/{id}/cancel` 幂等返回 `{requestedAt,localDisposition:'queued_cancelled'|'transport_aborted'|'none',providerDisposition:'confirmed_cancelled'|'pending'|'unsupported'|'unknown',billingUnknown:boolean,status}`。队列中可确定未发送；已送达时 AbortController 仅中断本地等待，并保留 `remoteTaskId` 查询或标记未知。只有供应商明确确认才能写“供应商已取消”，不能仅依据本地 abort 写 `cancelled`；若晚到结果，应按既定资产安装/舍弃政策留痕，不悄悄重试。
6. **可逆编辑与就近动作。** 本地 undo/redo 命令覆盖新增/删除、连接/断开、拖动、文本、引用排序、模型参数、输出选择与图片替换；已提交的 run 只可另起新 run，不能撤销其历史。浮动工具栏 action registry 明确 `id/label/availability/reason/handler`，下载、查看、复制、删除、替换、处理与重试均通过当前 node/output asset ID 调用，后台失败显示具体错误。上传替换创建新项目素材版本，不覆盖文件或旧 run；素材保护检查包含当前输出、run 输入/输出和引用。

## 实施与验收顺序

1. 先修图和运行契约：多引用、generate 图片输出、每节点模型/尺寸/比例、冻结快照、迁移旧文档；验证两图一文本 → 生成 A → A 输出接 B → 保存刷新后继续创作，服务端拒绝非法组合、循环和悬空资产。
2. 再接节点 UI 和工具栏：引用顺序/移除/预览、模型能力选择、比例、停止、图片原位替换、输出版本选择；复制适配 Infinite Canvas 工具定义和 Toonflow 节点附近上下浮层的主体交互；逐动作验收可达性与实际效果。
3. 最后做真实供应商链路：取得用户明确可用的图片 API 连接后，核对其官方接口与能力；真实多图出图、结果入库、取消语义、重启恢复、下游再生成；记录真实 run ID/资产 ID 和界面证据。无凭据时只能验证配置/协议/本地交互，**不得**关闭真实服务验收项。

## 后续未达标范围与完成判定

第一批完成也只可能证明“文本/图片/生成节点指定动作”通过，不代表参考产品或 AI 无限画布整体对齐。完整剩余范围至少包括：上表全部图片处理工具及批量结果、文本生成、视频/音频/配置/组节点动作、更多处理节点与连线类型、跨节点/结果版本的撤销重做、键盘和触摸等价路径、批量任务与商用审核交付路径，以及项目 `AGENTS.md` 所列其他参考项目的逐功能源码核查与差异闭合。每项须有源码来源、数据流、真实操作证据；不适用的功能须由产品范围明确排除并记录原因，不能默默删项。只有约定完整范围逐项通过且差异为零，才可使用“100% 对齐/可商用”的表述。

## 完整源码能力盘点

本节是对以上首批差异的增量盘点。固定源码为 Toonflow `d86cce6b689916ab73f75bc4c1f42a2a7424516c`、Infinite Canvas `dab19adc0847e32e39b7fc8ff90cb392561fb826`；均已核对仓库 `LICENSE` 为 MIT。表中“当前”依据本项目 `frontend/src/CreativeCanvas.tsx`、`CreativeCanvasNodes.tsx`、`creativeCanvasApi.ts`、`backend/app/creative_canvas.py` 和 `creative_canvas_api.py` 的已读代码，仅表示源码状态，不代替真实操作证据。参考的 Vue/Vue Flow/Element Plus/工作区文件服务和 React/自有状态/媒体存储不能未经适配混用；复用时须核查直接依赖并保留来源和许可。

| ID | 固定来源文件／符号与真实行为 | 当前代码状态、关键依赖 | 完成时的验收事实 |
| --- | --- | --- | --- |
| FC-01 多画布管理 | Toonflow `apps/web/src/pages/workspace/panels/canvas/canvasHost.vue` 的 `entries/activateCanvas/flushSave` 保留后台画布实例和任务；`components/canvasMenu.vue` 管理工作区画布文件。 | 当前仅有项目内单份 `CreativeCanvasDocument`，API 按项目读写；缺工作区内画布新建、切换、重命名、删除及跨画布任务生命周期。依赖多文档存储、保存队列和任务归属。 | 多画布切换后节点、视口、后台任务不串扰；改名/删除与保存并发时不复活旧文件；重启后逐画布恢复。 |
| FC-02 画布整体交互 | Toonflow `canvas/index.vue`、`components/canvasControls.vue` 有双击加节点、拖拽导入、缩放/平移、框选、网格吸附、隐藏连线、小地图、整理与适应视图；Infinite Canvas `web/src/components/canvas/infinite-canvas.tsx`、`canvas-toolbar.tsx`、`project.tsx` 有选择/平移工具、缩放与背景、区域选择、删除/清空。 | 当前 `CreativeCanvas.tsx` 基于 XYFlow，已有创建、拖动、连线、撤销重做和视口；参考的控制项与多输入方式尚未逐一对齐。依赖 XYFlow 事件、快捷键、持久视口。 | 鼠标/触控板/键盘从空白、节点、边、框选各入口操作一致；缩放和平移后浮层不漂移；整理、吸附、隐藏边与视口保存恢复逐项实测。 |
| FC-03 选择、复制与分组 | Toonflow `components/selectionToolbar.vue` 的 `duplicateNodes/groupNodes/ungroupNodes` 保留组树、边与相对位置；`components/nodeMenu.vue` 有上下文菜单；Infinite Canvas `project.tsx` 的 `groupSelection/ungroupSelection/copySelectedNodes/pasteCopiedNodes` 与 `canvas-context-menu.tsx` 提供复制粘贴和组操作。 | 当前有单节点 `duplicateNode/deleteNode`、撤销重做及多选 ID，但 `creativeCanvasApi.ts` 图类型没有 group，未见跨节点剪贴板与组数据。依赖组树、子节点坐标、边复制策略。 | 多选复制/粘贴和组/解组后边、引用、资产身份正确；跨项目复制非法素材被拒绝；撤销重做与保存刷新仍一致。 |
| FC-04 内建节点注册与扩展 | Toonflow `canvas/index.vue` 的 `loadRemoteNodes/loadNode` 从节点服务动态加载，七个内建包为 `textNode/imageNode/videoNode/audioNode/imageGenerationNode/videoGenerationNode/director3dNode`；Infinite Canvas `web/src/components/canvas/nodes/builtin-nodes.tsx`、`web/src/lib/canvas/node-registry.ts` 注册 text/image/video/audio/config/group 六类及插件节点。 | 当前 `creativeCanvasNodeTypes` 仅 text/media/generate 三类，后端 `CanvasNode.kind` 同样限三类；media 节点虽可引用视频/音频资产，UI 只有 image 输出 Handle。依赖类型注册、端口 schema、运行器、失联插件占位与迁移。 | 七类 Toonflow 对应能力及六类 Infinite Canvas 语义逐项决策和验证；禁用/缺失扩展后旧图可见、可恢复，不损坏数据。 |
| FC-05 端口与引用图语义 | Toonflow `packages/nodeScaffold/src/connection.ts`、`nodeInputs.ts` 校验类型并汇总多边输入；Infinite Canvas `web/src/components/canvas/canvas-node-generation.ts` 的 `buildNodeGenerationContext/buildNodeGenerationInputs` 展开组内资源，并处理文本、图片、视频、音频与 `@[node:id]` 引用。 | 当前 `CanvasEdge` 只有 text/image 到 prompt/image，`snapshot_for` 只冻结文本/图片。已有图片多引用、顺序和生成输出，但无视频/音频/组输入语义。依赖类型兼容矩阵、资产可用性、冻结快照。 | 各资源类型及组输入按确定顺序进入正确模型；循环、悬空、错类型、缺失引用明确拒绝；保存刷新与历史 run 输入不变。 |
| FC-06 文本生成 | Toonflow `packages/nodes/textNode/src/index.vue` 的 `generateText/loadText/saveText` 接收流式增量，把长文本落在节点文件；Infinite Canvas `project.tsx` 的 `handleGenerateNode` 分支生成批量文本，`canvas-node.tsx` 的 `ExpandedTextCard` 选择主文本。 | 当前 text 节点可手写和连线，`CanvasTextNode` 为 textarea；无文本模型执行、流式状态、版本或批量结果。依赖文本模型目录/流式服务和文本资产。 | 引用图文输入生成文本、过程中取消/失败、保存恢复、批量选择及下游引用均可真实操作；不能将手写等同文本生成。 |
| FC-07 视频生成与视频节点 | Toonflow `packages/nodes/videoGenerationNode/src/index.vue` 的 `startGeneration/getMatchingModes` 按模型处理首尾帧、图/视频/音频引用、时长/分辨率/比例与可选音频；`videoNode/src/index.vue` 实现上传替换、播放、下载。Infinite Canvas `project.tsx` 的 `handleGenerateNode/completeVideoNodeTask/pollVideoNodeTask/captureVideoNodeFrame` 处理异步任务及抽帧。 | 当前只有图片 `runCreativeCanvasNode`，media 视频无视频输出端口或生成执行器。依赖视频模型能力目录、任务状态、媒体存储与播放器。 | 合法模式提交真实服务，任务可查/停/恢复，输出播放下载入库并可作下游引用；首尾帧非法组合和远端失败留痕。 |
| FC-08 音频节点与生成 | Toonflow `packages/nodes/audioNode/src/index.vue` 的 `setAudio/uploadAudio/enterFullscreen` 提供音频输出、替换和预览；`packages/tools/mediaGeneration/src/runtime.ts` 定义 `audioGenerationSchema` 的语音、速度、格式等参数。Infinite Canvas `project.tsx` 的 `handleGenerateNode` 音频分支调用 `requestAudioGeneration/storeGeneratedAudio`。 | 当前媒体资产索引能列音频，画布没有音频输出端口、预览/替换或生成节点。依赖音频服务、格式验证、存储与播放。 | 真实上传/生成/预览/下载/连下游；无效格式或失败不覆盖旧资产，保存刷新后仍可用。 |
| FC-09 配置节点与多模态生成 | Infinite Canvas `web/src/components/canvas/canvas-config-composer.tsx`、`canvas-config-node-panel.tsx`、`canvas-node-prompt-panel.tsx` 与 `project.tsx` 的 `handleGenerateNode` 允许配置节点选择 text/image/video/audio 模式和各模式参数；结果形成可连接节点。 | 当前 generate 节点只运行图片，`params` 主要是图片尺寸/模型，`settings` 仅图片服务连接字段。依赖按能力分组的模型目录与各模态生成适配。 | 同画布四模式配置各自持久，切模型时参数合法且不静默改历史；真实结果可形成分支。 |
| FC-10 图片批量与处理 | Infinite Canvas `web/src/components/canvas/canvas-node.tsx` 的 `ImageContent/ExpandedImageCard`、`project.tsx` 的 `setBatchPrimary/retryBatchImage/deleteBatchImage` 保留逐张身份与主图；`cropImageNode/splitImageNode/maskEditImageNode/upscaleImageNode/generateAngleNode` 执行处理后创建结果。 | 当前 `CreativeCanvasRun.outputAssetIds` 支持数组，但 `CanvasGenerateNode` 只展示一张，工具栏没有上述处理链。依赖项目资产版本、处理服务、逐张运行身份。 | 每张单独查看/下载/重试/删除，主图切换影响下游且不改旧 run；裁切等动作真实产出可追溯结果，取消和失败可恢复。 |
| FC-11 3D 导演台 | Toonflow `packages/nodes/director3dNode/src/index.vue` 的 `loadModel/writeModel/generate/exportToCanvas`，结合 `sceneEditor.vue`、`scene.ts`、`motion.ts`、`sceneAnimation.ts`、`renderMedia.ts` 保存场景/镜头动画、按引用生成草案、渲染预览并导出图片或视频节点；导出失败会清理临时节点/文件。 | 当前三类 schema 和前端无 3D 节点或场景文档。依赖 Three.js 运行时、场景模型/动画、工作区文件、图片视频输出。 | 场景增改、镜头/动画预览、复制保存恢复、导出与失败清理均经真实路径验证；3D 输出能接画布后续创作。 |
| FC-12 插件系统 | Infinite Canvas `web/src/lib/canvas/node-registry.ts`、`plugin-loader.ts`、`web/src/pages/canvas/hooks/use-plugin-host.tsx` 和 `canvas-plugin-manager-modal.tsx` 管理扩展节点、面板、工具及生命周期；Toonflow `canvas/index.vue` 的 `loadRemoteNodes` 支持刷新节点包。 | 当前节点类型静态常量，无画布插件注册、禁用/恢复或旧图缺失插件降级。依赖插件契约、加载边界和权限；是否作为最终产品范围须明确，但不能因缺入口而称完整对齐。 | 安装/停用/重载后节点定义与现有实例一致，异常插件不破坏文档；旧图和未知类型可辨、可恢复。 |
| FC-13 Agent 操作画布 | Toonflow `packages/tools/canvas/src/runtime.ts` 的 `canvasSchemas/canvasOperations` 覆盖读/建/切画布、增删移改节点、连断边、整理、适应视图和节点工具；Infinite Canvas `web/src/pages/canvas/hooks/use-agent-bridge.ts`、`web/src/lib/canvas/canvas-agent-ops.ts` 将 Agent 操作接入画布。 | 当前 `creativeCanvasApi.ts` 只有人工画布读写、图片 run/刷新/取消；无 Agent 画布操作协议。依赖身份授权、工具 schema、事务/幂等和运行上下文。 | Agent 工具操作与人工图编辑同样校验、可观察、可撤销且持久；错误不造成半提交；未授权调用拒绝。 |
| FC-14 项目导入导出与素材 | Infinite Canvas `web/src/pages/canvas/index.tsx` 的 `importCanvas`、`project.tsx` 的 `exportCurrentProject` 和 `web/src/lib/canvas/canvas-export.ts` 打包项目图与媒体；Toonflow `canvas/index.vue` 的 `dropFiles/importCanvasFiles`、`assetLibrary.vue` 接工作区素材。 | 当前可从项目资产挑选/上传，但本画布没有独立项目包导入导出和参考的素材库直接操作全集。依赖资产 ID 映射、文件校验、导入事务。 | 导出再导入节点、边、版本、媒体和视口一致；重复/坏包/缺文件有明确回滚或部分导入报告，原项目不被覆盖。 |
| FC-15 持久化与恢复 | Toonflow `canvas/index.vue` 的 `saveCanvasState/scheduleCanvasSave` 和 `canvasHost.vue` 的 `flushSave` 在节点 `save` 钩子后串行持久化；Infinite Canvas `web/src/stores/canvas/use-canvas-store.ts` 管项目状态。 | 当前后端 `CreativeCanvasStore/DocumentRunStore` 持久图和 run；前端 `save` 用 revision 防冲突，生成前先保存。未覆盖上述多模态/组/插件数据及其关联文件。 | 编辑时切项目、崩溃、重启、运行中恢复不丢已确认状态；冲突保留草稿且不把未保存版本提交生成。 |

**缺口优先级：** P0 为节点/端口及资源图契约、完整多模态执行与结果身份、持久恢复；P1 为画布级选择/组/剪贴板、视频音频与批量图片实际操作；P2 为多画布管理、3D 导演、插件与 Agent、项目包导入导出。此排序仅供排期，不构成排除后项或降低“全部对齐”验收口径。用户已选择 Toonflow 作为统一主视觉；其他参考的功能范围保持不变。

**尚未逐功能核查：** Toonflow `apps/web/src/pages/workspace/panels/canvas/components/canvasMenu.vue`、`assetLibrary.vue`、`useCanvasTools.ts` 的完整异常路径，全部节点工具 `nodeTools.register` 的参数与错误处理、3D 的 `sceneEditor.vue/scene.ts/motion.ts/sceneAnimation.ts/renderMedia.ts` 内部算法，以及服务端实际各媒体提供商适配；Infinite Canvas 插件加载的权限/失败边界、Agent bridge 的完整事务、项目导入导出格式及所有图像处理对话框的参数与异常路径；本项目真实供应商凭据和端到端运行。上述能力仍需按固定源码逐一阅读和真实验收，不能把本次盘点写成穷尽清单或通过证据。
