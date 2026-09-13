# 参考图片、多供应商分析与 MiniMax H3 工作流——设计规格

Status: approved

## 背景

当前产品只接受参考视频，并围绕视频建立了 `referenceVideo`、视频上传、FFprobe 校验、五阶段本地预处理和分析代理。用户希望单个复刻项目可以在参考图片或参考视频之间二选一，并让参考图片作为首帧生成图生视频方案。

本设计同时扩展语义分析供应商和可执行生成策略：语义分析支持百炼、OpenAI、火山方舟豆包、Google Gemini、xAI Grok、Anthropic Claude 及本地 OpenAI 兼容服务；生成策略在现有 Wan2.2 I2V 之外增加 MiniMax H3 I2V。

该设计会替代“仅参考视频”和“仅百炼分析服务”的部分既有决策，但继续遵守本地优先、用户自带密钥、明确披露外发内容、不静默切换供应商，以及只有经过真实验证的模板才能称为可执行工作流等原则。

## 目标

- 一个复刻项目绑定且只绑定一个参考素材，素材可以是图片或视频。
- 参考图片经过本地校验和标准化后作为 Wan2.2 或 MiniMax H3 的首帧输入。
- 图片分析只把主体、场景、构图、视角、光线、色彩和视觉风格作为可观察事实；动作、环境动态、运镜、节奏和音频只能作为系统建议。
- 图片与视频共用一套语义分析任务、结构化结果、供应商配置和失败恢复语义。
- 用户明确选择分析供应商。系统不得因为请求失败而自动改用另一家供应商。
- 系统依据本地环境、用户输出要求和已验证模板能力，在 Wan2.2 I2V 与 MiniMax H3 I2V 之间给出可解释的推荐，并允许在高级设置中切换。
- 旧视频项目无需用户手工迁移即可继续打开和处理。

## 非目标

- 不支持一个项目同时绑定图片和视频，也不支持多张参考图片。
- 不把参考图片用于静态图片生成；本设计的输出目标仍是图生视频。
- 不从静态图片中声称观察到了真实动作、运镜、节奏或声音。
- 不在首版支持 HEIC、HEIF、GIF、动画 WebP 或 RAW 图片。
- 不允许云端供应商使用任意自定义 API 地址；只有“本地 OpenAI 兼容服务”允许配置地址，且首版只连接回环地址。
- 不接入 Mistral、智谱、Kimi、DeepSeek、SiliconFlow、Bedrock、Azure AI 或 Vertex AI。后续可通过相同接口增加，但不属于本设计的首版验收范围。
- 不支持多供应商并行分析、自动质量投票、自动降级或跨供应商修复。
- 不允许用户导入任意 ComfyUI Workflow 作为生成模板。
- 不承诺不同分析供应商输出完全一致，也不使用未经基准测试的“画质更好”等主观模型推荐理由。

## 已确认的产品决策

1. 参考图片与参考视频在单个项目内二选一。
2. 图片作为首帧，输出图生视频方案，不输出静态图片复刻方案。
3. 图片只分析可观察的静态视觉事实；动作、运镜和音频属于系统建议。
4. MiniMax H3 通过本地 ComfyUI 接入，不调用 MiniMax 云端生成 API。
5. 智能模式推荐 Wan2.2 或 MiniMax H3，高级设置允许用户切换。
6. 视频只外发关键帧联系表与运动数据；图片只外发去元数据后的分析代理图与基础尺寸数据。
7. 语义分析支持七种方式：百炼、OpenAI、豆包、Gemini、Grok、Claude、本地 OpenAI 兼容服务。
8. 图片项目默认匹配原图比例，默认时长为 5 秒。
9. MiniMax H3 音频默认关闭；用户在高级设置显式开启后，才生成环境音和音效建议。

## 总体架构

```text
Project
  -> ReferenceMedia (image | video)
  -> LocalPreprocessing (mediaType 决定阶段和代理产物)
  -> SemanticAnalysis (用户选定的 AnalysisProvider)
  -> StrategyRecommendation (确定性规则，不信任供应商直接选模型)
  -> ReproductionPlan
  -> Versioned ComfyUI Workflow (Wan2.2 I2V | MiniMax H3 I2V)
```

媒体接收、持久化和项目生命周期共用一条主链路；只有媒体校验和本地预处理在明确的媒体类型边界处分支。语义分析从统一的分析输入读取代理产物，供应商适配器不直接读取项目目录或原始参考素材。生成策略选择器只读取已经校验的结构化分析、输出设置和本地环境能力。

## 领域模型

### Project

```text
Project
  id: string
  name: string
  createdAt: ISO-8601 string
  updatedAt: ISO-8601 string
  referenceMedia: ReferenceImage | ReferenceVideo | null
  localPreprocessing: LocalPreprocessing | null
  semanticAnalysis: SemanticAnalysis | null
```

### ReferenceMedia

```text
ReferenceImage
  type: image
  id: string
  originalName: string
  format: jpeg | png | webp
  sizeBytes: integer
  width: integer
  height: integer
  hasTransparency: boolean

ReferenceVideo
  type: video
  id: string
  originalName: string
  format: mp4 | mov
  sizeBytes: integer
  width: integer
  height: integer
  durationSeconds: number
  frameRate: number
```

`width` 和 `height` 均表示应用旋转信息后的显示尺寸。`hasTransparency` 表示图片中是否存在实际透明像素，而不只是存在 Alpha 通道。项目数据只保存原文件名和上述事实，不保存用户来源路径、临时路径或托管文件的绝对路径。

### LocalPreprocessing

保留现有 `localPreprocessing` 名称，避免为了媒体泛化引入第二套任务概念。

```text
LocalPreprocessing
  id: string
  sourceReferenceMediaId: string
  mediaType: image | video
  algorithmVersion: integer
  status: queued | running | completed | failed
  currentStage: StageName | null
  stages: StageState[]
  queuedAt, startedAt, updatedAt, completedAt
  proxySummary: ImageProxySummary | VideoProxySummary | null
  reproducibilityAssessment: ReproducibilityAssessment | null
  error: LocalPreprocessingError | null
```

视频阶段保持不变：

```text
decoding
sceneDetection
keyframeExtraction
motionAnalysis
reproducibilityAssessment
```

图片阶段为：

```text
imageDecoding
imageNormalization
proxyGeneration
reproducibilityAssessment
```

图片的 `proxySummary` 只包含原始显示尺寸、标准化尺寸、代理尺寸、透明区域处理结果和适用性状态，不包含镜头数、关键帧数或运动强度。

### SemanticAnalysis

```text
SemanticAnalysis
  id: string
  sourceReferenceMediaId: string
  sourcePreprocessingId: string
  provider: bailian | openai | doubao | gemini | grok | claude | local_openai_compatible
  model: string
  promptVersion: integer
  schemaVersion: integer
  status: queued | running | completed | failed
  createdAt, startedAt, updatedAt, completedAt
  result: StructuredVisualAnalysis | null
  error: SemanticAnalysisError | null
```

`StructuredVisualAnalysis` 分成两个不可混淆的顶级区块。静态视觉事实适用于两种媒体；时间事实只允许出现在视频结果中，图片结果必须为 `null`：

```text
version: integer
observedFacts
  staticVisual
    subject
    scene
    composition
    viewpoint
    lighting
    color
    visualStyle
  temporal: null | TemporalFacts
    subjectMotion
    environmentalMotion
    cameraMotion
    rhythm

generationSuggestions
  subjectMotion
  environmentalMotion
  cameraMotion
  rhythm
  suggestedDuration
  audio
```

`version` 初始为 1；Ticket 06 保存人工校正时递增，用作 Prompt 与工作流的新鲜度依据。对参考视频，实际可见的动作、环境动态、运镜和节奏写入 `observedFacts.temporal`；对参考图片，这些内容只能写入 `generationSuggestions`。音频仍不做语义分析，任何音频描述都是生成建议。供应商可以生成系统建议，但不得直接决定最终生成模型。`StrategyRecommendation` 由本地确定性规则产生，并记录推荐原因和不适用原因。

## 旧项目兼容迁移

读取 `projects.json` 时，在 Pydantic 校验之前执行一次纯数据迁移：

- 将旧 `referenceVideo` 映射为 `referenceMedia`，并补充 `type: video`。
- 将旧 `sourceReferenceVideoId` 映射为 `sourceReferenceMediaId`，并补充 `mediaType: video`。
- 保留既有视频阶段、任务 ID、算法版本和本地预处理目录，不重新运行有效结果。
- 下一次正常项目写入时只保存新字段，不在公开 API 中长期返回两套同义字段。
- 无法明确迁移的损坏数据继续按现有“项目数据已损坏”语义失败，不猜测或丢弃字段。

迁移函数必须是幂等的，并用旧版真实 JSON 固件覆盖：无参考视频、只有参考视频、预处理运行中、预处理失败、预处理完成五种状态。

## 参考素材上传与存储

统一上传接口：

```http
PUT /api/projects/{project_id}/reference-media
Content-Type: multipart/form-data

file=<binary>
```

接口同时承担首次添加、同类型替换和跨类型替换。服务端根据真实内容识别媒体类型，不信任扩展名或浏览器 MIME。上传成功返回完整 `Project`；替换失败完整保留原参考素材及下游结果。

视频继续使用既有边界：MP4/MOV、2～10 秒、最大 200,000,000 字节、最低 480P、最高 UHD 4K。

图片使用以下固定边界：

- JPG/JPEG、PNG、WebP。
- 最大 30,000,000 字节。
- 宽和高均为 256～5760 像素。
- 宽高比为 2:5～5:2。
- 只接受单帧静态图片；动画 WebP 返回稳定错误。
- 上传校验会记录是否存在实际透明像素，以便在用户开始预处理前披露白底合成行为。

托管目录按媒体类型分开：

```text
<data_dir>/project-files/<project-id>/reference-media/
  <media-id>.<format>
```

仍使用数据目录内临时文件、分块写入、精确字节计数和原子替换。正在排队或运行本地预处理、语义分析或工作流生成时禁止替换。替换成功后失效并清理旧预处理、语义分析、复刻方案和工作流产物；清理失败只记录不含敏感路径的诊断日志，不回滚已经提交的新项目状态。

## 图片本地预处理

原图始终原样保存在本地，不被覆盖。用户手动开始本地预处理后，任务生成：

```text
<data_dir>/project-files/<project-id>/local-preprocessing/<preprocessing-id>/
  normalized.png
  analysis-proxy.jpg
  manifest.json
```

各阶段职责：

1. `imageDecoding`
   - 完整解码并确认只有一个静态帧。
   - 读取实际像素尺寸、色彩模式、EXIF 方向和是否存在透明像素。
2. `imageNormalization`
   - 应用 EXIF 方向。
   - 使用源 ICC 配置完成 sRGB 转换后丢弃源 ICC，并移除全部来源元数据。
   - 输出按 sRGB 像素解释，不保留原文件名或路径。
   - 对实际透明像素使用白色背景合成，并在摘要中记录该事实。
3. `proxyGeneration`
   - 从标准化图片生成最长边不超过 2048px、文件不超过 8,000,000 字节的 RGB JPEG；必要时逐级降低 JPEG 质量直到满足上限。
   - 不放大较小图片，不写入 EXIF、文件名、项目名或本地路径。
4. `reproducibilityAssessment`
   - 验证标准化产物和代理产物完整可读。
   - 记录素材是否满足当前已验证 I2V 模板的技术输入条件。
   - 主体数量、复杂交互和生成可行性仍等待语义分析，不在本地阶段猜测。

阶段产物先写入临时路径，必需文件全部完成后再原子提交。失败时删除当前阶段半成品，保留已完成阶段并允许从失败阶段重试。替换素材或算法版本变化时不复用旧结果。

## 分析输入与隐私边界

供应商适配器只能接收以下已经构造好的 `AnalysisInput`：

```text
ImageAnalysisInput
  analysis-proxy.jpg
  width
  height
  aspectRatio

VideoAnalysisInput
  contact-sheet.jpg
  analysis-proxy.json
```

图片原图、`normalized.png`、视频原文件、项目名称、原始文件名、绝对路径、API Key 和 ComfyUI 信息不得进入分析请求。

每次启动语义分析前，前端必须展示：

- 目标供应商与模型。
- 将发送的代理文件和结构化数据。
- 明确不会发送的原始素材与本地项目信息。
- 供应商失败不会触发自动切换。

用户确认后才创建分析任务。确认只授权本次任务，不作为跨项目或永久授权。

## 多供应商分析内核

后端定义统一接口：

```text
AnalysisProvider
  analyze(input, prompt, model, credential) -> provider response
```

供应商适配器只处理鉴权、请求结构、超时、响应提取和供应商错误映射。提示词组装、数据边界、结构校验、一次修复、持久化和重试由共享分析服务负责。

首版适配器：

| 标识 | 服务 | 接口族 |
| --- | --- | --- |
| `bailian` | 阿里云百炼 Qwen | 百炼兼容接口 |
| `openai` | OpenAI GPT | Responses API |
| `doubao` | 火山方舟豆包 | 方舟 API |
| `gemini` | Google Gemini | Gemini generateContent |
| `grok` | xAI Grok | Responses API |
| `claude` | Anthropic Claude | Messages API |
| `local_openai_compatible` | 本地视觉模型 | OpenAI 兼容接口 |

云端模型只能从应用内经过验证的模型清单选择，避免用户输入与接口不兼容的任意模型名。模型清单有独立版本，更新清单不改变历史分析记录。本地 OpenAI 兼容服务允许用户输入模型名，但 Base URL 首版只接受 `localhost`、`127.0.0.1` 或 `[::1]`，防止把“本地服务”配置变成任意服务器请求入口。

每个供应商使用自己的 API Key。密钥由本地服务调用系统安全存储，前端只能获知“未配置/已配置/连接失败/可用”，不能读回密钥。密钥不得写入 `projects.json`、浏览器存储、分析产物、日志或错误响应。

供应商原始响应经过以下共享流程：

1. 提取候选结构化文本。
2. 用固定 Pydantic 模型做严格校验，拒绝未知顶级结构和缺失必需字段。
3. 校验失败时，向同一供应商发送一次仅包含错误摘要的修复请求。
4. 第二次仍失败则记录稳定错误，保留本地预处理结果并允许用户重试。

修复不得改用其他供应商。应用默认不长期保存完整供应商原始响应，只保存标准化结果、请求追踪 ID（供应商提供时）、供应商、模型、提示词版本、结构版本和时间。

## 语义分析 API

建议接口边界：

```http
GET  /api/analysis-providers
PUT  /api/analysis-providers/{provider}/configuration
POST /api/analysis-providers/{provider}/test
POST /api/projects/{project_id}/semantic-analysis
GET  /api/projects/{project_id}
```

配置接口写入或替换密钥，但响应永不回显密钥。测试接口只验证当前配置和选定模型可用，不上传用户参考素材。启动语义分析接口接收明确的 `provider` 与 `model`，验证预处理已完成后返回 `202 Accepted`；当前有效结果存在时可以幂等返回 `200 OK`。

语义任务采用与本地预处理一致的可恢复异步状态。前端只在 `queued` 或 `running` 时轮询项目，不使用 WebSocket、SSE、Celery、Redis 或数据库。

## 策略推荐

`StrategyRecommendation` 由本地确定性规则生成：

```text
StrategyRecommendation
  recommendedStrategy: wan22_i2v | minimax_h3_i2v | null
  alternativeStrategies: StrategyOption[]
  recommendationReasons: string[]
  incompatibilityReasons: string[]
  rulesVersion: integer
```

规则只使用：

- 本地 ComfyUI 版本、已安装模型和节点、可用资源检查。
- 图片比例、尺寸和标准化结果。
- 已校验结构化分析中明确的生成需求，例如动作复杂度、运镜类型和是否需要原生音频；不读取供应商给出的模型名称或自然语言排名。
- 用户要求的时长、分辨率、比例和是否生成音频。
- 模板已验证的参数边界。

规则不得根据供应商的一句自然语言结论直接选模型，也不得声称某模型“更真实”或“质量更高”，除非未来有项目内可复现的基准证据。

默认设置：

- 输出比例跟随参考图片。
- 时长为 5 秒。
- 分辨率在所选模板的已验证档位中智能匹配。
- MiniMax H3 音频关闭。
- Wan2.2 输出无音频视频。

开启 MiniMax H3 音频时，应用只把 `generationSuggestions.audio` 作为环境音和音效建议，不把它描述为图片中观察到的声音。切换模型、修改关键参数、重新分析或替换素材后，旧工作流必须标记为过期。

## ComfyUI 工作流

首版维护两份受控、版本化模板：

- Wan2.2 14B I2V。
- MiniMax H3 I2V。

在参考图片项目中，两份模板都使用 `normalized.png` 作为首帧输入，并从复刻方案写入画面、动作、环境动态、运镜、节奏和可选音频提示。用户可以通过高级设置覆盖已暴露参数，但不能编辑任意节点 JSON。

模板状态分为：

- `candidate`：已经实现，但尚未完成真实环境导入与 Queue 验证，只能显示为后续建议。
- `executable`：已锁定 ComfyUI 版本、模板版本、模型资产要求，并在真实环境完成导入和 Queue 验证。

任何文档、界面和 API 都不得把 `candidate` 称为可执行工作流。

## 界面设计

### 项目准备页

- 现有“参考视频”区域改为“参考素材”。
- 一个拖放/文件选择入口同时接受图片和视频，用户无需预先选择媒体类型。
- 上传成功后明确展示“参考图片”或“参考视频”及类型对应的元数据。
- 替换动作允许跨类型替换，并说明会清除哪些已有结果。
- 图片显示四个本地预处理阶段；视频保留现有五个阶段。

### 分析服务设置

- 设置区列出七种分析方式及独立状态。
- 云端服务配置 API Key 和内置模型；本地服务配置回环地址、可选密钥和模型名。
- 每种方式提供不上传参考素材的连接测试。
- 首页环境状态只展示当前选中分析服务，详情入口展示其他已配置服务。

### 分析确认

分析按钮打开页面内确认区，不使用浏览器原生 `confirm`。确认区展示供应商、模型、将发送的内容、不会发送的内容和不自动切换说明。确认、取消、失败重试和焦点恢复均支持键盘。

### 复刻工作台

- 视频继续使用播放器、关键帧和时间关联信息。
- 图片使用静态预览，不显示时间轴、镜头数、关键帧数或运动强度。
- “可观察事实”与“生成建议”始终为两个独立区块。
- 策略区展示推荐模型、推荐原因、不适用原因和高级设置。
- 工作流区展示模板版本、候选/可执行状态、新鲜/过期状态及发送到本地 ComfyUI 的入口。

宽度小于 1024px 时保持只读：可以查看参考素材、分析结果、策略和工作流状态，但不能上传、替换、启动分析、切换模型或发送工作流。

## 错误处理

错误保持稳定代码和面向用户的中文说明，至少区分：

- 不支持的媒体类型或文件格式。
- 文件过大、尺寸或比例越界、动画图片。
- 图片不可读、标准化失败、代理生成失败。
- 分析供应商未配置、鉴权失败、配额不足、限流、超时、内容拒绝和响应格式错误。
- 本地兼容服务地址无效、连接失败或模型不支持图片。
- ComfyUI 未连接、版本不匹配、模型或节点缺失、模板未验证和 Queue 失败。

错误信息不得包含 API Key、Authorization 头、Base64 图片、供应商完整响应、绝对路径或内部堆栈。供应商失败保留本地预处理结果；用户明确重试后才创建新请求。若用户主动重新分析，旧语义结果在新任务开始时失效，避免界面同时展示来源不一致的旧结果与新任务状态。

## 实施拆分

### 增量 1：统一参考素材

- 增加 `ReferenceMedia` 联合模型和旧数据迁移。
- 将视频上传接口、存储和前端面板升级为参考素材。
- 增加图片真实内容校验和替换事务。
- 验证全部既有视频测试继续通过。

### 增量 2：图片本地预处理

- 增加图片四阶段任务、产物存储、恢复和清理。
- 增加图片代理摘要与适用性检查。
- 前端接入图片状态与结果。

### 增量 3：统一语义分析内核

- 定义分析输入、结构化结果、任务状态、提示词和供应商契约。
- 实现安全配置、连接测试、分析确认和异步任务。
- 先以百炼和本地 OpenAI 兼容服务验证接口边界。

### 增量 4：云端供应商

- 依次接入 OpenAI、豆包、Gemini、Grok、Claude。
- 每个适配器通过相同契约测试。
- 每家完成独立真实 API 冒烟测试后才在界面标记可用。

### 增量 5：策略与工作流

- 实现确定性策略推荐和高级覆盖。
- 接入 Wan2.2 I2V 与 MiniMax H3 I2V 模板。
- 实现模板状态、工作流过期检测、环境检查和 Queue 验证。

每个增量必须独立完成测试与审查后再进入下一增量，避免同时迁移领域模型、外部 API 和工作流而无法定位回归。

## 测试策略

### 后端

- `ReferenceMedia` 联合模型、旧项目幂等迁移和损坏数据测试。
- 图片扩展名、MIME 与真实内容不一致的校验测试。
- JPG、PNG、WebP、透明图片、动画 WebP、尺寸、比例和字节边界测试。
- 图片标准化方向、sRGB、白底合成、无元数据、代理尺寸和原子产物测试。
- 图片阶段失败、服务重启、失败重试、算法版本变化和素材替换测试。
- 七种供应商的共享契约测试：请求数据边界、鉴权脱敏、错误映射、严格结构校验和一次修复。
- 本地兼容服务的回环地址限制和模型不支持图片测试。
- 策略规则版本、模型不可用原因、参数边界和工作流过期测试。

CI 中只使用模拟供应商响应，不保存或要求真实密钥。真实 API 冒烟测试使用本地显式命令，由操作者自行配置密钥，不进入默认测试套件。

### 前端

- 图片/视频上传、同类型替换、跨类型替换和失败保留测试。
- 两种媒体各自的预处理阶段、轮询停止、重试和项目切换测试。
- 七种分析方式的配置状态、连接测试、分析确认和错误展示测试。
- 可观察事实与生成建议的语义分区测试。
- 模型推荐、高级切换、H3 音频默认关闭和工作流过期测试。
- 1024px 桌面能力边界、键盘导航、焦点恢复、可见焦点和颜色对比测试。

### 真实环境验收

- 使用至少一张 JPG、一张透明 PNG 和一张 WebP 完成本地预处理，并人工检查标准化图和代理图。
- 对七种分析方式分别完成一次图片分析；对支持批量图片输入的服务仍只发送本设计允许的单张代理图。
- 使用同一张标准化图片分别生成 Wan2.2 和 MiniMax H3 工作流。
- 在锁定版本的本地 ComfyUI 中导入两份工作流并真实 Queue。
- 验证 H3 默认无音频，显式开启后生成音频；Wan2.2 始终不包含音频生成节点。

## 完成标准

- 旧视频项目无需手工迁移即可打开，并能继续或重试现有本地预处理。
- 图片和视频能够互相替换，失败不会破坏原素材或已有结果。
- 图片代理不含 EXIF、原文件名、项目名或本地路径。
- 七种分析方式返回同一结构化领域模型，且失败不静默切换。
- API Key 不出现在项目文件、浏览器持久化、日志、产物或错误响应中。
- 图片分析结果严格区分可观察事实和动作、运镜、节奏、音频建议。
- Wan2.2 与 MiniMax H3 只有通过真实 ComfyUI 导入和 Queue 后才标记为可执行。
- 后端、前端、迁移、无障碍和既有视频回归测试全部通过。

## 文档与决策更新

实施时需要同步：

- 更新 `PRODUCT.md`，将参考视频边界扩展为参考素材边界，并加入多供应商分析和 MiniMax H3。
- 更新 `CONTEXT.md`，新增“参考素材”“参考图片”“可观察事实”“生成建议”“分析供应商”等稳定术语。
- 新增 ADR，记录 `ReferenceMedia` 联合模型和按媒体类型分支的本地预处理。
- 新增 ADR，明确替代现有 `0005-explicit-multi-provider-analysis.md` 的五供应商范围（并保留其替代 ADR 0004 的历史链），记录用户显式选择、七种分析方式和禁止静默切换。
- 新增 ADR，记录 MiniMax H3 本地 ComfyUI 模板及候选/可执行验证门槛。
- 更新 README 中的图片依赖、存储目录、外发边界、供应商配置和 ComfyUI 前置条件。

## 官方能力依据

- MiniMax H3 支持首帧/尾帧图生视频及多模态输入：<https://platform.minimax.io/docs/guides/video-generation>
- ComfyUI 提供 MiniMax H3 原生支持与 I2V 模板：<https://blog.comfy.org/p/minimax-h3-day-0-support-in-comfyui>
- OpenAI 图片理解：<https://developers.openai.com/api/docs/guides/images-vision>
- Google Gemini 图片理解：<https://ai.google.dev/gemini-api/docs/image-understanding>
- xAI Grok 图片理解：<https://docs.x.ai/developers/model-capabilities/images/understanding>
- 火山方舟图片理解：<https://www.volcengine.com/docs/82379/1362931>
- Anthropic Claude 图片理解：<https://platform.claude.com/docs/en/build-with-claude/vision>

这些链接只证明供应商和模型具备相应输入能力。应用内的具体模型清单、参数范围和“可用”状态仍必须由实现时的契约测试与真实 API 冒烟测试锁定。
