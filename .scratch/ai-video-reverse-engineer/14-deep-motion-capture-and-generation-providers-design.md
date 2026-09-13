# Ticket 14：深度动作捕捉与多生成服务——设计规格

Status: approved

## 背景

当前产品已能在本地使用 FFmpeg 生成分析代理和帧变化强度，但这些数据只能判断运动范围，不能表达逐帧空间结构，也不能作为深度控制视频驱动视频生成模型。

本设计增加“深度动作捕捉”：从参考视频逐帧估计相对深度，生成时间连续的深度控制素材，并以本地 ComfyUI + Wan2.2 Fun Control 作为正式效果基准。同时，为 MiniMax H3、豆包 Seedance、Grok Imagine、Google Veo 等云端视频服务建立能力分级的生成接口。

这里的“深度动作捕捉”不是骨骼动作捕捉，也不输出关节、骨架、BVH 或 FBX；它输出的是随时间变化的单目相对深度视频，用于保持主体轮廓、前后层次、动作节奏和镜头空间变化。

## 已确认约束

- 对齐目标是 LibTV 类似的最终动作与空间控制效果，不复制其完整产品形态。
- 参考视频完整文件默认只在本地处理，不自动上传。
- 首版可复刻范围仍是单镜头、2～10 秒、一个清晰主要主体、轻度至中度运动。
- Apple Silicon M1 / 8GB 是本地深度提取的最低目标，不要求其运行 Wan2.2 Fun Control。
- 同时支持 Apple Silicon、CUDA，并提供较慢的 CPU 回退。
- 首版链路必须从参考视频走到深度素材预览、复刻方案、可执行 ComfyUI Workflow 和 Queue。
- 本地 ComfyUI + Wan2.2 Fun Control 是正式的原生深度控制路径。
- 首批云端候选是 MiniMax H3 与豆包 Seedance 2.0；Grok 为实验路径，Veo 仅提供非深度降级，OpenAI Sora 暂不接入。
- 云端服务不得静默切换，发送任何派生素材前必须向用户披露并获得本次操作授权。

## 目标

1. 从参考视频在本地生成时间连续、可预览、可复用的相对深度控制素材。
2. 用明确的质量检查阻止严重闪烁、反转或无效深度素材进入正式工作流。
3. 为 Wan2.2 Fun Control 生成经过验证的可执行 ComfyUI Workflow，并发送到本地 ComfyUI Queue。
4. 通过统一生成服务接口接入能力不同的云端服务，同时保留各服务真实能力边界。
5. 让用户清楚区分原生深度控制、参考视频引导、实验性视频编辑和图像提示降级。

## 非目标

- 不输出人体骨架、关节角度、动作文件或三维网格。
- 不做多人遮挡、复杂交互、快速切镜或大幅高速运动的正式效果承诺。
- 不在 M1 / 8GB 上运行 Wan2.2 Fun Control。
- 不托管云端推理，不代用户管理供应商账户或承担生成费用。
- 不把所有生成服务包装成效果等价的统一入口。
- 不自动把完整参考视频发送给第三方。
- 不自动在供应商失败时切换到另一家。
- 不用单一主观“复刻百分比”描述效果。

## 三种架构方案

### 方案 A：所有服务采用最低公共能力

只向所有生成服务提供提示词、首帧和末帧，忽略深度控制、视频参考和视频编辑能力。

优点是接口最简单；缺点是丢失本功能的核心价值，无法达到深度动作对齐目标。拒绝采用。

### 方案 B：每个服务维护独立端到端流程

为 Wan2.2、MiniMax、Seedance、Grok、Veo 分别实现深度生成、素材转换、提交、轮询和结果保存。

优点是能够充分使用供应商特性；缺点是深度提取、隐私授权、错误处理和项目状态会被重复实现，测试组合迅速膨胀。拒绝采用。

### 方案 C：深度捕捉模块 + 能力驱动的生成服务接口

本地 `DepthCapture` 模块只负责生成稳定的领域产物；`VideoGenerationProvider` 接口位于外部生成服务接缝上，各适配器声明真实能力，由策略选择器构造对应的生成请求。

该方案保持深度捕捉模块的深度和局部性，同时允许供应商适配器使用各自特性。采用此方案。

## 总体架构

```text
参考视频
  -> 本地深度捕捉 DepthCapture
       -> depth-control.mp4
       -> depth-preview.mp4
       -> depth-metadata.json
       -> depth-quality.json
  -> 深度质量门禁
  -> 复刻方案 StrategyPlanner
  -> 生成服务能力匹配 ProviderCapabilityMatcher
       -> LocalComfyUIAdapter（原生深度）
       -> MiniMaxH3Adapter（参考视频，待验收）
       -> SeedanceAdapter（参考视频，待验收）
       -> GrokImagineAdapter（实验性视频编辑）
       -> VeoAdapter（首尾帧/参考图降级）
  -> 执行记录与生成结果
```

### 模块边界

#### `DepthCapture`

这是一个深模块。公开接口只接受参考视频标识、执行设备偏好和固定配置版本，返回深度捕捉记录；模型加载、帧批处理、时序平滑、编码和资源降级均封装在内部。

它不感知 ComfyUI、MiniMax、Seedance 或任何云端 API，也不决定最终使用哪个生成服务。

#### `DepthQualityGate`

读取深度元数据和抽样帧，输出结构化质量结论。它负责判断素材是否能进入正式原生深度工作流，而不是预测最终生成视频的主观相似度。

#### `StrategyPlanner`

结合可复刻范围、深度质量、用户选择和生成服务能力，生成明确的复刻策略。它不得把缺少原生深度能力的服务描述为深度控制。

#### `VideoGenerationProvider`

这是外部供应商接缝上的稳定接口。适配器负责鉴权、请求转换、上传、提交、状态轮询、取消能力声明和供应商错误归一化。

接口不强迫所有供应商接受相同输入；它接受规范化素材集合，再由能力匹配器决定该适配器是否满足当前策略。

## 深度捕捉方案

### 模型选择

首版使用 Video Depth Anything Small 生成相对深度。Small 模型参数规模和许可更适合作为产品默认路径；Base/Large 不进入首版正式依赖。

模型与推理代码必须通过独立版本清单管理，不能把模型文件纳入仓库。安装和环境检查需要明确展示模型是否可用、设备后端和预计性能等级。

### 设备优先级

```text
用户显式选择
  -> auto：CUDA -> Apple MPS -> CPU
  -> cuda：只使用 CUDA，不可用则报可操作错误
  -> mps：只使用 Apple MPS，不可用则报可操作错误
  -> cpu：使用 CPU 慢速路径
```

运行时不得因某一后端失败而无提示地把素材发送到云端。设备级本地回退允许发生，但必须记录最终执行设备；从 GPU/MPS 回退到 CPU 时在开始长任务前提示预计更慢。

### 输入与推理

- 读取经过 Ticket 02 校验的本地参考视频。
- 保留原始时间基，按固定目标帧率生成深度帧；默认不超过参考视频帧率和工作流模板上限。
- 推理分辨率由设备配置档确定，保持宽高比，并填充到模型所需尺寸。
- M1 / 8GB 使用 Small 模型、较低推理边长和小批量或逐帧执行，优先保证不触发系统内存压力。
- CUDA 根据可用显存选择批量大小，但不改变输出格式与质量检查口径。
- CPU 使用相同模型与输出口径，仅标记性能等级为 `slow`。

### 时序稳定

深度动作捕捉的核心不是单帧深度精度，而是时间连续性。首版采用以下有限处理：

- 使用视频深度模型自身的时序能力作为主要稳定来源。
- 对深度范围按镜头内稳定分位点归一化，避免每帧独立 min/max 导致亮度呼吸。
- 对异常帧执行有限的时间中值或指数平滑，但不改变运动边缘的时间位置。
- 保留未经可视化色表处理的单通道控制数据；彩色预览只用于人眼检查。

首版不增加光流重投影、分割模型或三维重建作为前置依赖，避免在最低硬件上形成过深依赖链。

## 深度产物

每次深度捕捉使用唯一内部 ID：

```text
<data_dir>/project-files/<project-id>/depth-captures/<capture-id>/
  depth-control.mp4
  depth-preview.mp4
  depth-metadata.json
  depth-quality.json
  manifest.json
```

### `depth-control.mp4`

- 单通道相对深度映射编码为工作流可消费的视频容器。
- 近远方向在整个产品中固定，不由适配器自行翻转。
- 分辨率、帧率和时长与生成策略中记录的控制规格一致。
- 不叠加色表、文字、水印或 UI 标记。

### `depth-preview.mp4`

- 仅供本地界面预览。
- 可使用高对比灰度或固定色表，并可与参考视频并排或擦除对比。
- 不默认发送给生成服务。

### `depth-metadata.json`

至少记录：

- `schemaVersion`
- `algorithmVersion`
- `modelId` 与模型文件校验值
- `sourceReferenceVideoId`
- 输入与输出尺寸、帧率、帧数、时长
- 归一化方向与分位点
- 最终执行设备、精度和推理配置档
- 各阶段耗时和生成时间

不得记录绝对路径、密钥或供应商信息。

### `depth-quality.json`

保存可解释的检查项、指标、阈值版本、结论和抽样时间点，不保存主观综合分数。

## 深度质量门禁

首版检查以下可验证问题：

| 检查 | 目的 | 失败行为 |
| --- | --- | --- |
| 完整性 | 帧数、时长、可解码性与策略一致 | 阻止执行 |
| 动态范围 | 避免接近全黑、全白或几乎常量 | 阻止正式路径 |
| 时间闪烁 | 检测无对应画面运动的大幅深度跳变 | 标记需检查或阻止 |
| 方向稳定 | 检测近远方向意外反转 | 阻止执行 |
| 边缘连续性 | 检测主体轮廓的大面积破碎 | 标记需检查 |
| 时间对齐 | 深度帧与参考视频时间轴一致 | 阻止执行 |

结论固定为：

- `passed`：允许生成正式原生深度工作流。
- `review_required`：用户必须预览并显式确认后才能继续；生成记录标记为实验性。
- `failed`：不能进入深度控制策略，可重新生成或选择非深度降级策略。

质量门禁只判断控制素材的技术有效性，不承诺生成结果一定保持人物身份、服装细节或逐帧姿态。

## 生成服务能力模型

```text
ProviderCapabilities
  nativeDepthControl: boolean
  arbitraryReferenceVideo: boolean
  videoEdit: boolean
  referenceImages: boolean
  firstLastFrames: boolean
  maxDurationSeconds: number | null
  maxReferenceVideoBytes: number | null
  acceptedVideoFormats: string[]
  asynchronousJobs: boolean
  cancellation: supported | unsupported | unknown
  dataBoundary: local | cloud
  supportLevel: verified | beta | experimental | fallback
```

能力来自适配器版本化配置和集成测试，不根据营销名称猜测。供应商 API 变化后，旧项目继续保存执行时的能力快照。

### 支持等级

| 生成服务 | 策略 | 首版产品状态 |
| --- | --- | --- |
| 本地 ComfyUI + Wan2.2 Fun Control | `native_depth_control` | `verified`，正式可执行工作流 |
| MiniMax H3 | `reference_video_motion` | 完成专项验收后可升为 `beta`，此前为 `experimental` |
| 豆包 Seedance 2.0 | `reference_video_motion` | 完成专项验收后可升为 `beta`，此前为 `experimental` |
| Grok Imagine | `video_edit_experimental` | `experimental` |
| Google Veo | `image_prompt_fallback` | `fallback`，不得称为深度动作控制 |
| OpenAI Sora | 不生成 | 不实现适配器 |

`reference_video_motion` 只表示云端服务能够接收参考视频类输入。除非供应商公开接口和本项目验收都证明其能正确解释深度视频，否则界面不得显示“原生深度控制”。

## 统一接口

概念接口如下，具体语言类型在实施计划中锁定：

```text
VideoGenerationProvider
  describeCapabilities() -> ProviderCapabilities
  validate(request, assets) -> ValidationResult
  submit(request, authorizedAssets) -> ExternalGenerationJob
  getStatus(jobId) -> ExternalGenerationStatus
  cancel(jobId) -> CancelResult
  fetchResult(jobId) -> GeneratedVideoResult
```

规范化请求：

```text
VideoGenerationRequest
  projectId
  strategy
  promptSnapshot
  outputSettings
  assetBindings
  providerId
  providerConfigVersion
  capabilitySnapshot
  disclosureAcceptanceId | null
```

适配器返回稳定领域错误，例如鉴权失败、余额不足、输入不支持、内容策略拒绝、速率限制、超时、供应商不可用和结果过期。原始错误可保存在受限诊断信息中，但前端不直接依赖供应商响应结构。

## 素材绑定与禁止静默降级

策略明确指定每个素材的角色：

```text
AssetBindings
  depthControlVideo
  referenceMotionVideo
  firstFrame
  lastFrame
  subjectReferenceImages
  audioReference
```

能力匹配失败时，系统返回可解释建议，而不是自动换策略。例如：

- 用户选择 Veo，但当前策略需要 `nativeDepthControl`：提示只能改用本地 Wan2.2，或由用户明确选择首尾帧降级。
- MiniMax H3 当前适配器未通过深度视频专项验收：允许实验性参考视频生成，但不能显示“已验证深度动作复刻”。
- 云端提交失败：保留当前请求和素材，不切换到 Seedance 或 Grok。

## 云端数据边界与授权

完整参考视频默认不上传。每次云端提交前展示：

- 供应商名称。
- 即将发送的具体素材及大小、时长。
- 是否包含从参考视频派生的深度控制视频、关键帧或主体参考图。
- 供应商处理发生在云端，受其条款和保留政策约束。
- 预计计费单位；若无法可靠读取实时价格，只提示前往供应商查看。

首版优先发送派生的 `depth-control.mp4`、用户确认的参考图和提示词。只有某个已确认策略确实需要完整参考视频时，界面才单独请求该文件的本次上传授权；授权不跨供应商复用，也不默认记住。

供应商密钥保存在系统安全存储中，不写入项目 JSON、日志、导出包或 Workflow。

## 项目数据模型

```text
Project
  depthCaptures: DepthCapture[]
  activeDepthCaptureId: string | null
  generationProviderSettings: ProviderSelection | null
  generationRuns: GenerationRun[]

DepthCapture
  id
  sourceReferenceVideoId
  algorithmVersion
  status: queued | running | completed | failed
  currentStage
  devicePreference
  executionDevice
  outputSummary
  qualityAssessment
  error
  createdAt / updatedAt / completedAt

GenerationRun
  id
  providerId
  strategy
  supportLevel
  capabilitySnapshot
  inputSnapshot
  disclosureAcceptance
  status: validating | awaiting_authorization | submitting | submission_unknown |
          queued | running | completed | failed | cancelled
  externalJobReference
  output
  error
  createdAt / updatedAt / completedAt
```

`DepthCapture` 与 `GenerationRun` 分离：重新使用同一个深度素材生成多次不会重新推理；供应商失败也不会破坏已经完成的本地深度结果。

## 本地任务与并发

深度捕捉沿用本地异步任务模式，但不与 FFmpeg 预处理并行抢占最低配置设备。MVP 每次只运行一个本地重计算任务；项目状态文件仍是持久化真值。

云端生成任务由供应商执行，可以同时处于等待状态，但同一项目对同一请求的重复提交必须通过幂等键阻止。应用重启后根据保存的外部任务标识恢复轮询；无法恢复时显示“状态待确认”，不得自动重新计费提交。

## API 边界

建议新增以下本地 API：

```http
POST /api/projects/{project_id}/depth-captures
GET  /api/projects/{project_id}/depth-captures/{capture_id}
POST /api/projects/{project_id}/depth-captures/{capture_id}/confirm-review

GET  /api/video-generation/providers
POST /api/projects/{project_id}/generation-runs/validate
POST /api/projects/{project_id}/generation-runs
GET  /api/projects/{project_id}/generation-runs/{run_id}
POST /api/projects/{project_id}/generation-runs/{run_id}/cancel
```

深度捕捉启动接口接收设备偏好，但模型、阈值和输出格式由算法版本固定。生成校验接口只检查能力与素材，不产生费用；真正提交必须包含最新的数据披露接受标识。

本地 ComfyUI 继续使用现有环境检查和 Queue 接缝，不伪装成云端供应商任务。它实现相同的高层生成接口，以便工作台复用状态展示，但保留本地 Workflow 下载、查看和发送行为。

## 工作台体验

在现有复刻工作台中增加“动作与空间控制”区域：

1. 展示当前深度捕捉状态和执行设备。
2. 并排播放参考视频与深度预览，播放、暂停和拖动时间保持同步。
3. 展示质量检查项及 `passed`、`review_required`、`failed` 结论。
4. 展示推荐策略和生成服务支持等级。
5. 用户选择服务后，立即解释将使用哪些素材以及哪些能力会丢失。
6. 本地 Wan2.2 路径生成 Workflow 后继续环境检查并发送 Queue。
7. 云端路径先校验，再展示数据披露和预计计费，确认后提交。

移动端遵循现有只读原则：可以查看深度状态、预览、质量结论和生成结果，不能启动深度捕捉、授权云端上传或提交生成。

## Wan2.2 Fun Control 工作流

首版正式工作流使用经过锁定和集成测试的 Wan2.2 Fun Control 深度控制模板。Workflow 生成器负责：

- 注入深度控制视频的本地可访问路径。
- 注入用户确认后的正负提示词、首帧/参考图和输出设置。
- 根据模板锁定模型、节点、采样参数和控制强度的默认值。
- 在高级设置中只暴露已经验证不会破坏模板的数据项。
- 在 Queue 前检查 ComfyUI 版本、模型、节点、输入文件和显存档位。

Workflow 导出包包含控制素材、Workflow、复刻方案摘要和版本清单，但不包含云端密钥或完整参考视频，除非用户显式选择导出参考视频。

## 云端适配器验收

MiniMax H3 与 Seedance 只有通过固定测试集后，才能从 `experimental` 升为 `beta`。测试集至少覆盖：

- 横向走动与前后景变化。
- 手臂或产品主体的中等幅度动作。
- 缓慢推拉或平移镜头。
- 静态背景与轻微遮挡。
- 不同画面比例和 2 秒、5 秒、10 秒时长。

验收不使用单一相似度百分比，分别记录：

- 动作时间点是否保持。
- 主体轮廓和位置轨迹是否保持。
- 前后层次是否保持。
- 镜头方向与节奏是否保持。
- 身份、纹理和提示词服从是否出现明显退化。
- 同一输入多次生成的稳定性。

如果供应商把灰度深度素材当成画面外观而不是控制信号，该路径不得宣传为深度动作捕捉支持；可以继续保留为普通参考视频能力。

## 错误恢复

- 深度推理失败：保留现有本地预处理，允许从失败阶段重试，不自动改用云端。
- 深度质量失败：允许更换本地设备或重新生成；也允许用户明确选择非深度降级策略。
- ComfyUI 环境不满足：保留 Workflow 和控制素材，展示缺失项，修复后重新检查。
- 云端鉴权或余额错误：不丢弃本地素材，不自动切换供应商。
- 云端轮询中断：保存外部任务标识，恢复后继续查询，不重复提交。
- 供应商返回内容策略拒绝：记录稳定错误与供应商名称，不把它解释为技术故障。
- 结果下载失败：保留已完成的外部任务标识并允许重试下载。

## 测试与验收标准

### 本地深度捕捉

- 在 Apple Silicon、CUDA 和 CPU 路径验证相同输入输出契约。
- 在 M1 / 8GB 目标机验证 2～10 秒正式范围内不会因内存压力崩溃；具体耗时门槛由实施前基准测试锁定。
- 验证输出帧率、帧数、时长、尺寸和近远方向一致。
- 验证每帧独立归一化不会进入实现，并使用闪烁样本覆盖时序质量门禁。
- 验证替换参考视频后旧深度捕捉失效，但已有执行记录仍可审计。
- 验证项目重启后能恢复完成结果和失败状态。

### 本地 ComfyUI

- 使用锁定版本的真实 ComfyUI 导入生成的 Workflow。
- 验证所有模型和自定义节点检查能够给出具体缺失项。
- 使用正式范围样本完成真实 Queue，并保存输出与执行记录。
- 验证质量门禁失败时不能生成标记为正式的可执行工作流。

### 云端适配器

- 使用契约测试验证能力声明、请求转换和稳定错误。
- 使用伪适配器验证不支持能力时不会提交或计费。
- 验证没有披露接受标识时任何云端素材上传都会被拒绝。
- 验证完整参考视频不会因选择云端供应商而自动加入请求。
- 验证 API 失败、超时、限流和应用重启都不会造成重复提交。
- 对 MiniMax H3 与 Seedance 执行真实专项效果验收后再调整支持等级。

### 前端

- 验证参考视频和深度预览同步。
- 验证各支持等级文案与策略一致，不出现能力夸大。
- 验证切换服务时素材清单和隐私披露同步变化。
- 验证键盘操作、可见焦点、状态播报和颜色对比满足 WCAG 2.1 AA。
- 验证移动端只能查看，不能执行或授权。

## 版本与兼容性

- 旧项目没有 `depthCaptures`、`generationProviderSettings` 或 `generationRuns` 时按空集合读取。
- 深度模型、算法、质量阈值、工作流模板和供应商适配器分别版本化。
- 已完成记录保留执行时的版本与能力快照；升级不能改写历史结论。
- 只有通过真实导入和 Queue 测试的 Wan2.2 模板才能标为“可执行工作流”。

## 对产品边界的影响

批准本设计前，`PRODUCT.md` 包含以下约束：

- “MVP 不提供云端生成服务”。
- “可执行工作流仅支持 Wan2.2 14B I2V 与 Wan2.2 Fun Camera 模板”。

建议将深度动作捕捉拆为一个扩展里程碑：

1. **14A 本地正式能力**：深度捕捉、质量预览、Wan2.2 Fun Control Workflow、环境检查和 Queue。
2. **14B 云端实验能力**：统一生成接口、MiniMax H3 与 Seedance 专项验收；通过前保持 `experimental`。
3. **14C 后续扩展**：Grok 实验适配器与 Veo 降级适配器。

批准后已将 14A 同步为当前 MVP 扩展，并将 14B 记录为独立实验里程碑。若未来把 14B 纳入同一个 MVP，仍需再次明确修改 `PRODUCT.md`、项目规格和相关 ADR；不能只修改代码。

## 已批准决策

1. 采用“本地深度捕捉模块 + 能力驱动生成服务接口”的方案 C。
2. 将 Wan2.2 Fun Control 设为唯一正式原生深度控制基准。
3. MiniMax H3 与 Seedance 在专项验收前只标为实验性参考视频路径。
4. 完整参考视频默认不上传；每次云端提交都进行按供应商、按素材授权。
5. 按 14A、14B、14C 分阶段交付，不把 Grok、Veo 或 Sora 放入首个开发批次。
6. 先把 14A 作为当前 MVP 扩展，14B 作为紧随其后的实验里程碑；保留当前 MVP “不提供云端生成服务”的正式承诺，直到 14B 验收通过再修订。
