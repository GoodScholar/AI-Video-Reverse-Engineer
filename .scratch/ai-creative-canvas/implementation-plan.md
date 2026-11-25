# AI 创作画布完整对齐实施计划

## 2026-09-30 全部对齐（覆盖首条闭环交付边界）

用户明确要求全部对齐。统一范围见 spec.md，完整差异见 parity-acceptance.md，新增任务 04–17 按依赖推进。既有 01–03 及下面首版记录保留追溯，不能用其中局部范围代替完整目标。主视觉已选择 Toonflow 固定版本；先运行参考应用建立同视口对照，同时推进公共操作及完整能力。已有授权不重复请求。


## 商用整改要求（2026-09-30，优先于下方初版约束）

当前状态：**未达标**。下方初版中“单引用、固定三档尺寸、无输出端口、无停止操作”属于待整改差异，不能继续作为验收边界。旧记录保留用于追溯，不代表用户接受功能删减。

用户新增明确要求：无限画布不受之前全站设计约束，可以完全参考开源画布。节点主体与选中编辑层按实际参考布局迁入，不再保留旧表单卡片外壳作为限制；仅作 React/XYFlow 与项目数据的必要适配。此规则已同步 AGENTS.md / DESIGN.md。

验收依据为固定参考功能源码及 `parity-acceptance.md`，必须覆盖页面、主体操作、数据关系、失败/取消/恢复。部分测试通过只记录该检查结果，不能标记整体对齐。参考项目中的图片处理、工作流、视频等尚未补齐能力继续列为差异，不因本轮先处理生成节点而隐去。

### 整改执行顺序

1. 核对 Toonflow 与 basketikun 固定版本的生成节点、引用、输出、浮动操作及直接依赖，建立实际差异矩阵。
2. 接入独立豆包/火山方舟图片协议：按官方 SDK 的 JSON `/images/generations` 适配，多图全部传入，结果返回图片内容；保留凭据隔离与配置版本校验。只有用户配置的模型进入选择器，不能伪造模型可用性。
3. 扩展画布后端：多引用及顺序、生成输出引用、运行输入冻结、当前结果选择、停止请求与晚到结果保留，避免后台改写画布草稿。
4. 完整迁入对应节点交互：可编辑自身提示词、引用预览/移除/排序、输出端口、节点模型与尺寸参数、停止状态、替换与预览下载、就近操作。保留现有保存/轮询/测量修复。
5. 独立审查与真实浏览器验收；真实生成必须由用户已配置的生成服务验证，Mock 响应不计入真实服务证据。

### 已协调的接口变更

- 配置增加 `provider`、`models` 和公开 `protocolCapabilities`；旧配置兼容 OpenAI 协议。`models` 为用户实际配置的模型白名单，不等同供应商授权探测。
- `generate` 节点增加 `outputAssetId`、`referenceOrder`（edge ID 顺序）；`params.model` 从白名单选择。宽高仍为实际像素，比例与尺寸不能冒充供应商已验证的能力。
- 允许多个文本/图片输入与生成节点图片输出；拒绝重复边、自环和循环。执行前冻结每条引用及全部素材 ID。
- `POST /runs/{id}/cancel`：排队任务取消后不得发送；已发送任务记为 `cancel_requested`，不能保证供应商停止/退费；晚到结果保留历史，不自动替换当前输出。
- 服务调用改为 `generate(snapshot, input_images=[...])`，不支持多图的协议明确拒绝，禁止静默只取第一张。

### 文件分工与检查

| 工作 | 文件范围 | 验证 |
| --- | --- | --- |
| 来源与差异审查 | parity-acceptance.md | 实际固定源码路径、许可、验收项 |
| 豆包协议 | image_generation.py / image_generation_api.py / 对应测试 / vendor/VolcengineArk | 协议请求、配置迁移、模型白名单、图片解码与异常 |
| 引用与状态机 | creative_canvas.py / creative_canvas_api.py / 对应测试 | 多引用、输出、循环、取消竞态、资产保护 |
| 节点与设置界面 | CreativeCanvas* / creativeCanvas* / ImageGenerationSettings* / imageGenerationApi.ts | 交互测试、构建、浏览器实际操作 |

交叉接口以本节为准。执行记录继续写入 progress.md，不另建重复任务体系，不提交当前混合工作区。

**Goal:** 独立画布无需 Shot，文本/项目素材 → 生成 → 项目素材结果 → 选定结果继续创作。
**Spec:** 项目 AGENTS.md、用户确认的 `.scratch/product-business-audit/report-2026-09-30.md` P0 2B、issues/01-scope-and-source-reuse.md。
**Architecture:** React/XYFlow 独立页面；项目级 CanvasDocument 单独持久化；结果复用 ProjectAssets；云端 Images API 负责生成。首版每项目一个画布，可修改/保存定义，运行冻结输入，结果历史独立于图编辑。
**Execution:** 使用 subagent-driven-development 分任务实施与独立审查。保留当前工作区已有改动，不提交混合变更、不创建替代空白 worktree。已有方向已批准，不再重复审批。

## 约束
- 独立导航 `canvas` / AI 无限画布，保留镜头编排兼容功能。
- 无需 Shot；首版 text / media / generate 三类节点，显式 prompt / image 输入端口。
- media 只引用项目素材 ID；生成历史保存 inputAssetIds、输入快照、outputAssetIds；修改节点不能改变旧结果。当前同步 Images API 不提供可恢复的远程任务 ID。
- 后端验证端口、重复标识、引用、每端口唯一输入；revision 冲突返回 409，不能静默覆盖。
- 执行状态 queued / running / completed / failed / unknown；unknown 不自动重新提交。首个云端 Images API 是同步响应，服务重启后中断的 running 标记 unknown，不伪造可恢复的远程任务 ID；排队未发送任务标记可重试失败。
- 既有 ComfyUI 客户端及 XYFlow 集成优先复用；Toonflow 类型兼容算法保留固定提交与 MIT 声明。Vue/Bun 页面不能直接用于 React/FastAPI，新增部分限于必要的界面/API/持久化适配。
- 本机 8188 不可达，现有配置仅聊天分析服务；用户询问模型含义后，已解释并询问云端/ComfyUI，默认按云端实现配置入口继续，不要求用户安装模型、不使用分析密钥冒充生成密钥。

## 接口约定
Base: `/api/projects/{project_id}/creative-canvas`。
GET 返回 `{document, runs, assets}`。
document: `{revision:number,nodes:CanvasNode[],edges:CanvasEdge[],viewport:{x,y,zoom},settings:{comfyUrl,checkpoint}}`。
CanvasNode: `{id,kind:'text'|'media'|'generate',position:{x,y},label,text:string,assetId:string,params:{negativePrompt:string,width:number,height:number,steps:number,seed:number,denoise:number}}`；非适用字段可省略，服务端默认值。
CanvasEdge: `{id,source,target,sourceHandle:'text'|'image',targetHandle:'prompt'|'image'}`。
PUT body=document，返回完整 GET shape。画布设置属于 document；生成服务配置单独管理，执行采用已保存画布 revision 并冻结服务配置版本。
全局 GET/PUT `/api/image-generation/configuration`；返回 `{baseUrl,model,credentialState,revision}`，PUT `{baseUrl,model,apiKey?,revision?}`；密钥只存系统安全存储，不放 document/run。
POST `/nodes/{node_id}/runs` body `{revision}` 返回完整 GET shape。
POST `/runs/{run_id}/refresh` 返回完整 GET shape；刷新绝不再次提交。
Run: `{id,nodeId,status,error:string|null,createdAt,documentRevision,configurationRevision,inputAssetIds:string[],outputAssetIds:string[],snapshot:object}`。
上传复用 `uploadPreproductionAsset(projectId,'reference',file)`；结果资产复用 preproduction 文件 URL。

## Task 1：独立定义和真实执行后端
- [ ] 新增 backend/app/creative_canvas.py、creative_canvas_api.py，注册 main.py，素材删除引用加入 preproduction_api.py。
- [ ] 先写 backend/tests/test_creative_canvas.py，验证无 Shot 保存恢复、CAS 冲突、非法/重复边与素材引用、输入快照、真实客户端边界的失败/unknown、结果安装与删除保护。
- [ ] 复用安全密钥存储与HTTP基础设施，接 Images API generations/edits；有图片输入时 multipart 上传。图像需实际解码验证，结果入项目素材，不保存外部临时 URL 为资产。无配置给出设置入口，不能返回模拟成功。
- [ ] 运行专属后端测试与受影响素材引用测试，记录命令结果。

## Task 2：独立操作页面
- [ ] 新增 creativeCanvasApi.ts、CreativeCanvas.tsx、creativeCanvas.css、必要的节点组件/Toonflow 适配。
- [ ] ContentStudioApp / ContentProductionWorkspace 新增独立入口，铺满空间、返回项目、左侧添加工具、按需面板、缩放控制。
- [ ] 文本/生成节点编辑、上传/引用素材、连线、删除、保存恢复、运行与历史结果、从结果添加媒体节点并继续创作。错误有原因，不以空白屏掩盖失败。
- [ ] 保留草稿，异步轮询不覆盖未保存图；生成前先保存。跨项目卸载隔离，隐藏页面停止轮询。失败重试显式创建新 run，unknown 只允许刷新查询。
- [ ] 前端行为测试覆盖输入→保存→运行、冲突不丢草稿、结果分支、页面进入/退出；运行对应测试和 build。

## Task 3：集成审查与真实操作验收
- [ ] 独立审查 Task 1/2 的接口、状态与回归；修复实质问题并验证。
- [ ] 本地浏览器操作非空画布、编辑连线、保存刷新、结果续创路径（有可用服务时真实生成）。
- [ ] 记录实现范围与真实服务阻碍。其余 P0 商业检索采用/作品交接/单条修订/任务收件箱继续保持待实施，不能宣称整体重构完成。

## 实施决定
2026-09-30：首条服务由 ComfyUI 调整为云端图片 API。依据：本机无 ComfyUI 服务，用户未表示正在使用它，现有豆包 coding/chat 配置不等于图片生成授权。保留 ComfyUI 既有客户端供后续适配。本次先完成无需模型知识的应用配置、画布操作与可调用协议；真实付费出图需要用户在设置中提供生成服务凭据，不能用测试替代真实验收。

## 2026-09-30 只读源码核查：08–11 最小兼容契约

核查基线：项目 [AGENTS.md](../../AGENTS.md)、[spec.md](spec.md)、[08](issues/08-model-capability-and-references.md)–[11](issues/11-video-and-audio-nodes.md)；Toonflow-app 固定 `d86cce6b689916ab73f75bc4c1f42a2a7424516c`，[MIT 许可](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/LICENSE)。以下是实现输入，不是已通过验收；其他参考能力仍按 spec/验收矩阵保留。

- **08 模型与引用**：模型目录最少按 `{providerId,modelId,mediaType,configured,capabilitySource,capabilityVersion,capabilities}` 表达；`configured`/白名单只说明用户填过配置，具体 `imageSizes/imageRatios/maxReferences`、视频 `mode/durationResolutionMap/audio` 须来自该模型的已核实适配器或实测，未知则不展示为可用并由服务端拒绝非法组合。节点保留稳定的供应商与模型标识、配置版本及参数；切换模型只归一化已核实的不兼容参数，旧运行快照不变。输入维持现有稳定 edge ID 和 `referenceOrder`，扩展到 text/image/video/audio；富文本提及应存稳定引用 ID（edge ID 或独立引用 ID）及有序内容片段，`{{ref 1}}` 只在执行快照按当时顺序解析，不能把序号当身份。冻结每项 `{edgeId,sourceNodeId,sourceHandle,dataType,assetId或textVersion,sourceRunId,order}`，移除、排序、缺失素材和类型不匹配由前后端一致校验。依据：[useNodeReferences.ts](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodeScaffold/src/useNodeReferences.ts)、[promptInput.vue](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodeScaffold/src/components/promptInput.vue)、[connection.ts](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodeScaffold/src/connection.ts)、[媒体目录](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/server/src/utils/media/generation.ts)；本项目 `backend/app/creative_canvas.py`、`creative_canvas_api.py`、`image_generation.py`。
- **09 结果、历史、批量**：保留现有不可变 `run` 身份、文档/配置 revision、输入快照与幂等键及 `outputAssetIds:string[]`；新增逐项 `outputItems:[{resultId,assetId,mediaType,status,sourceRunId,createdAt}]`，旧运行由原字符串数组读取映射，旧 API 字段不换类型。节点只持有 `{runId,resultId}` 当前输出指针，切主图、替换、删除或晚到结果不改旧 run。下游快照记录上游 run/result/asset，指针变化标记依赖过期；批量链按拓扑提交，每步独立幂等键、状态、错误与重试依据，`unknown` 不自动重交。排队取消可保证未发送；已发送只记录本地停止请求、远端处置未知及计费未知，除非供应商明确支持并证实取消/查询。Toonflow [服务端媒体生成](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/server/src/utils/media/generation.ts) 返回数组，但其 [图片节点](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodes/imageGenerationNode/src/index.vue)、[视频节点](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodes/videoGenerationNode/src/index.vue) 仅消费首项；逐项动作/主图选择须继续按其他已列参考验收，不能归称 Toonflow 已提供。
- **10 文本**：在旧 `text` 节点基础上兼容增加 `{role:manual|generated,prompt,modelSelection,referenceOrder,currentTextVersionId}`；文本版本 `{versionId,runId,text或受管文件引用,createdAt}` 独立保存，`text` 旧值迁入首个手写版本。生成 run 冻结引用和模型、保留流式增量/取消/错误，完成后显式选当前文本版本供下游读取；预览、弹窗编辑、下载、入库、字号和生图动作分别接真实路径。Toonflow [textNode](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodes/textNode/src/index.vue) 的 `loadText/saveText/generateText`、[nodeAi.ts](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodeScaffold/src/nodeAi.ts) 可复用交互和事件语义；其 Vue、XSender、工作区文件路径及 Bun/SSE 请求不能直接接入 React/FastAPI。
- **11 视频/音频**：旧 `media` 资产 ID 和旧 image 连线原样可读；扩端口类型为 STRING/IMAGE/VIDEO/AUDIO，增加独立视频生成节点的 `{modelSelection,mode,duration,resolution,ratio,generateAudio,firstFrame/lastFrame引用}`，参数只按所选模型已证实能力呈现、执行端复验。输出必须安装为可用的项目视频/音频资产并记录真实 MIME、来源 run 和当前版本，支持替换、预览、下载及下游连线；失败不覆盖旧输出。参考 [videoGenerationNode](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodes/videoGenerationNode/src/index.vue)、[generationSettings.vue](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodes/videoGenerationNode/src/components/generationSettings.vue)、[videoNode](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodes/videoNode/src/index.vue)、[audioNode](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/nodes/audioNode/src/index.vue) 可复用交互/校验；项目已有 `preproduction_api.py` 的视频/音频上传探测和 `ProjectAssets.install` 可接资产层。Toonflow [媒体 schema](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/packages/tools/mediaGeneration/src/runtime.ts) 定义音频生成字段，但固定版 [前端媒体路由](https://github.com/HBAI-Ltd/Toonflow-app/blob/d86cce6b689916ab73f75bc4c1f42a2a7424516c/apps/server/src/routes/ai/media/generate.ts) 只暴露 image/video，内建媒体 provider 未见 `generateAudio`：音频生成服务不能算已具备。

**适配边界与真实服务门槛**：Toonflow 的 `useNodeReferences`、类型匹配、节点 UI、模型分组/模式过滤与媒体输入检查可按 MIT 条件移植并保留来源与许可；其 Vue/VueFlow/Element Plus、XSender、Bun 工作区文件服务、供应商配置与本项目 React/XYFlow/FastAPI/项目素材模型不同，需写薄的状态、API、资产 ID 适配，不能直接复制服务端 provider 后宣称已接通。当前 `CreativeCanvasStore.save` 把文档包在 `schemaVersion:1` 存储信封内，`CanvasDocument` 本身只有 revision 且 `extra=forbid`；扩字段/端口须版本迁移并读回旧草稿。当前图片协议仅 `openai-compatible` 单图或 `volcengine-ark` 多图，固定像素输入、单张 PNG 响应及本地停止语义见 `backend/app/image_generation.py`、`creative_canvas_api.py`，不支持把 Toonflow 的 K 尺寸/比例直接传入或宣称多结果。图片凭据状态应通过运行中服务的 `GET /api/image-generation/configuration` 只读取 `credentialState`、协议和模型白名单，不读取/输出密钥；默认路径缺文件不能推断实际运行目录。现有聊天分析配置曾可用，但不等于图片/视频/音频生成授权，也不能未经核验直接算作画布文本生成闭环。真实闭环需用户在对应设置中提供/启用具备该模态、输入数量和参数权限的服务及凭据，并允许实际付费调用；服务端能力核查与小额真实请求确认后才开放选项、记录运行与入库证据。普通本地上传、预览、裁切、切分、缩放、音视频探测及格式处理可复用已有本地服务继续实施和验收，不依赖生成凭据；模型超分、局部生成编辑和云端生成另算。未配置期间继续实现和验证非付费路径，17 的真实服务验收保持未完成。

## 2026-09-30 只读源码核查：07 多画布最小兼容路径

固定参考为 Toonflow-app `d86cce6b689916ab73f75bc4c1f42a2a7424516c`（MIT）：`apps/web/src/pages/workspace/panels/canvas/components/canvasMenu.vue` 创建独立 JSON、改名、切换、删除前冲刷保存；`canvasHost.vue` 使非活动画布保留实例与后台任务，并在改名/删除期间暂停各实例保存；`index.vue` 用项目目录与画布 ID 绑定历史和异步生命周期。固定版删除明确不可撤销，`canvasDrop.ts` 的“导入”是媒体/文本文件变节点，未提供删除恢复或完整画布包导出。07 的恢复和图文档转移须按本项目 `spec.md` 独立补齐，不能归称 Toonflow 已实现。

当前 `CreativeCanvasStore` 每项目固定 `state.json` 和 `runs.json`，`GET/PUT /creative-canvas` 与运行/刷新/取消 API 只有 `project_id`，运行记录只存 `nodeId`；素材删除保护只扫描这一份文档和项目运行。最小改造契约如下：

1. 项目下增加稳定的 `canvasId` 和可更改的显示名清单；画布文档按 `(projectId, canvasId)` 独立持久化、独立 revision，旧 `state.json` 原子映射为默认画布并保留旧节点、组、边、视口和运行。名称不作存储路径或运行身份。创建/重命名/切换/删除/恢复接口均验证项目归属；并发创建、改名和 CAS 冲突不得覆盖现有文件或丢本地草稿。
2. 原项目级 `runs.json` 可继续存放运行历史，但每条新增不可变 `canvasId`；列出运行、查找结果、幂等键、取消与刷新均按项目及画布身份筛选，生成输入仍取已保存文档并冻结快照。队列可暂保留项目级串行边界，不能因切换或删除画布中断已提交的后台任务、把相同 `nodeId` 的其他画布结果显示或回填到当前画布；服务重启继续按现有 `unknown/failed` 恢复规则处理。
3. 切换前完成或明确阻止当前草稿保存，保存失败/409 时保留草稿与当前画布；异步加载、轮询、上传、编辑器回调和 undo/redo 均绑定 `(projectId, canvasId)`，离页/切换后旧响应不能安装到新画布。至少保留一个可用画布。删除采用可恢复的软删除状态，恢复沿用原 `canvasId`；有活动运行时保留其可查询快照，防止保存任务复活已删画布。素材删除保护扫描所有活动及可恢复画布、全部运行输入/引用/输出；画布删除本身不直接删除项目素材。
4. 导出版本化画布包包含文档全量（节点、分组、边、视口、设置）、引用素材及派生来源依赖清单与可用文件，并标出缺失素材；导入先校验格式、容量、端口/父级/循环、资产文件与来源，再为目标项目分配新画布/素材身份，重映射全部引用后一次提交或回滚。跨项目不得按相同 asset ID 猜测为同一文件；历史 run 若不能完整保真迁移则明确作为来源元数据而非可刷新任务。验证旧单画布迁移、切换与删除恢复、运行中切换、跨项目导入以及保存冲突不丢草稿。

## 2026-09-30 08–09 实施契约与分工

以下细化上述 08–09 核查结论，供并行实施使用；后端先定接口，前端依同一类型接入。固定 Toonflow 的媒体目录返回 provider/model 与能力元数据，图片生成节点按能力过滤参数，但只消费服务端数组首项；逐项结果行为须在本项目补齐。

1. **08 模型目录与旧字段**：后端目录项采用稳定 `{providerId, modelId, mediaType, label, configured, capabilitySource, capabilityVersion, capabilities}`；`capabilitySource` 区分协议通用限制、已核实适配器/供应商资料、真实账户验证，不能把 `models[]` 白名单或用户填写的选项当模型能力。当前配置只有单个 `provider`、`baseUrl`、`models[]`、revision 与一份凭据；先在既有配置接口兼容增加目录/能力，不更改旧响应字段和密钥存储。节点增加可选 `modelSelection:{providerId,modelId}`，保留旧 `params.model` 可读可存；旧值只在当前配置能唯一解析时映射，未知值留在草稿中提示修正，绝不暗中切模型。`CanvasDocument` 的 `extra=forbid` 需同步验证新字段及旧 `schemaVersion:1` 信封读取；切模型仅调整确知不兼容的参数，旧 run 快照不改。当前 `size` 是像素串，`ratio` 与 `size` 由后端现有优先级裁定；不得直接传 Toonflow 的 K 尺寸或凭目录声称任意模型支持这些参数。
2. **08 引用身份与执行快照**：现有 edge ID 与 `referenceOrder` 继续作为连线身份及次序；富文本保存有序 `text/reference(edgeId)` 片段，显示序号仅是当前次序的投影。旧 `{{ref N}}` prompt 在编辑或提交时按当时 `referenceOrder` 解析；无法解析的 token 保留文本并给出错误，不覆盖原文。连线重排只改变顺序，删边/素材缺失使关联 token 失效，不让旧序号悄悄指向别的素材。后端提交时按已保存文档冻结 `{edgeId,sourceNodeId,sourceHandle,dataType,assetId或textVersionId,sourceRunId,order}`、模型身份与配置/能力版本；复验项目素材、引用类型/数量及模型 size/ratio 组合，再调用收费服务。不能仅靠前端筛选。图文档、API 类型、复制粘贴和导入导出须接受新增可选字段且保持旧画布可读。
3. **09 结果身份与兼容**：运行记录继续返回旧 `outputAssetIds:string[]`，并逐项增加 `{resultId,assetId,mediaType,status,createdAt,sourceRunId?}`；旧运行可按 `(runId,index)` 派生稳定 `resultId`，不重写历史。节点新增可选 `currentOutputRef:{runId,resultId}`，保留 `outputAssetId` 供旧文档和旧消费方读取；当二者都有值必须指向同一项目素材，冲突由服务端拒绝或显式迁移。一次响应的全部合法结果逐项安装并记录；若安装部分失败，已安装资产与记录必须可对账，重试不得再次发起计费请求或把同一结果装两次。逐张替换/主图/分支/下载按 `resultId` 定位，历史运行快照不因当前选择改变；逐张删除只移除当前可用性并遵守资产引用保护，保留来源记录。
4. **09 依赖与批量**：下游运行快照冻上游 `{runId,resultId,assetId}`。当前输出指针或来源版本变化时，以这组三元组比较并标记下游 stale，不重写旧结果。批量链先保存草稿并按拓扑逐节点提交，逐步记录身份、独立幂等键、状态和错误；失败/取消/`unknown` 使依赖步骤停住，`unknown` 不自动重交收费请求。排队取消可保证未发送；发送后仅记录本地停止/供应商处置与计费未知，除非适配器验证了远端取消/查询。切画布、离页、晚到响应和重启仍按 `(projectId,canvasId,runId)` 回收，不能写到当前显示的另一画布。
5. **可并行模块边界**：后端 08 owner 独占 `image_generation.py`、`image_generation_api.py`、`creative_canvas.py`、`creative_canvas_api.py` 的目录、校验和冻结快照，先稳定公开类型/错误码；前端 08 owner 独占 `CreativeCanvas.tsx`、`CreativeCanvasNodes.tsx`、`creativeCanvasApi.ts` 的模型分组、参数及富文本引用 UI，按后端契约接入。09 后端 owner 之后在运行存储/API/安装路径实现多结果及批量编排，前端 owner 再接逐项结果/主图/历史操作；媒体播放器可独立实施，但不得假设云端生成或多结果已可用。交接验证覆盖旧单图/旧 `params.model`、切模型非法组合、重排删边保身份、跨项目素材拒绝、多结果部分失败和幂等、上游换主图下游 stale、`unknown`/取消/晚到/重启及跨画布隔离。真正付费生成仍以已配置且已验证权限的服务实测为门槛。

## 2026-09-30 续接：文本 OpenAI 兼容生产契约

用户确认采用 OpenAI 兼容 Chat Completions；保留文本节点、持久运行与现有独立配置边界。依据固定 Toonflow textNode/index.vue、nodeAi.ts、server/routes/ai/generate.ts 的完整生成/流式/取消代码（MIT），以及本项目 image_generation.py 的配置 CAS/安全存储、analysis_providers/http_transport.py 的取消与预算机制。接口格式核对 OpenAI 官方 Chat Completions 与 streaming 文档；Vue/Bun/pi-ai 不直接嵌入 Python 服务，必要适配使用已有 httpx。

- 新 `text_generation.py`/`text_generation_api.py` 与独立 `text-generation.json`、Keyring service `ai-video-reverse-engineer.text-generation`。不复用分析服务固定模型目录、JSON输出或其现有密钥；用户在文本设置录入兼容服务。
- `GET/PUT /api/text-generation/configuration`：`providerId:"default"`、`baseUrl:string|null`、`models:[{modelId,label?}]`、`revision:string|null`、`credentialState:configured|unconfigured`、`modelCatalog:[{providerId,providerLabel,modelId,label,configured,configurationVersion,textVerified,imageVerified,maxImageReferences}]`。PUT 只接 `baseUrl,models,apiKey?,revision`，空密钥保留原值，错误不回显输入；配置实质变化使验证失效。目录 configured 表示凭据就绪且文本SSE已验证，可执行与已填写分离。单供应商下允许多个模型，不限制指定模型名称。
- `POST /api/text-generation/test-connection` 接 `revision,modelId,mode:"text"|"image"`，界面按钮明确“可能计费”。固定不含用户内容的探测；text验证自由文本SSE并有效结束，image探测两张本地红/蓝测试图并核对结果次序后才设置 `imageVerified=true,maxImageReferences=2`。不据通用协议宣称任意多图能力；失配不覆盖并发新配置。返回更新后的同一公开配置，真实验证证据含时间及模式但无密钥。
- `TextGenerationService.validate(snapshot)` 强制检查配置版本、模型已验证、引用类型/数量与预算；生成前再复验配置，不把排队请求改送新endpoint。自由文本 `POST <baseUrl>/chat/completions` 使用stream=true和有序messages，逐SSE `choices[0].delta.content`，无response_format、不启工具。响应字节、总时间、正文12000字符有限；未完整结束不报告成功。HTTP读取中断保持远端计费未知。
- 为服务补足 projectId/canvasId/runId/versionId 运行上下文；图片只从已登记项目素材读取并检查实际图像后生成data URL，公开快照不含base64/凭据；输入素材引用保护沿用现有 run 快照。视频/音频明确不支持。adapter返回可取消迭代流，并与候选级取消句柄对接。
- 前端新增 TextGenerationSettings，复用现有设置表单样式；画布进入/重新激活时读目录并接文本模型选择，未验证显示原因与设置入口。保存设置不自动测试或计费；模型探测需用户明确点击。
- 本轮采用最小独立服务，非整个画布功能收口。真实账户测试仍需有效baseUrl/model/key及实际调用证据；不得用协议夹具冒充。
