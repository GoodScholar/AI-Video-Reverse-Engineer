# Ticket 04：配置分析服务并完成可恢复的语义分析——设计规格

Status: superseded

Superseded by: [`04-run-recoverable-multi-provider-semantic-analysis-design.md`](./04-run-recoverable-multi-provider-semantic-analysis-design.md)

> 本文件保留为历史记录，不得用于后续实施。2026-09-12 已确认将 Ticket 04 升级为用户显式选择的五供应商分析架构。

## 目标

在 Ticket 03 已完成且仍有效的本地分析代理上，让用户配置自己的阿里云百炼 API 密钥，在明确知晓外发内容后手动发起一次语义分析，并获得可恢复、可重试、可重新打开的结构化分析报告。

本 Ticket 完成主体数量和复杂交互两项语义判断，将其与 Ticket 03 的多镜头、运动范围结论合并为最终适用性状态。模型只能补充语义判断，不能覆盖本地已确认的失败事实。

## 已确认决策

- MVP 固定使用阿里云百炼北京地域和 `qwen3.7-flash`，固定接口为 `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`。
- 单次语义分析调用完成本 Ticket 所需的结构化分析，不自动追加第二次模型请求。
- 只发送 Ticket 03 生成的 `contact-sheet.jpg` 与 `analysis-proxy.json`；完整参考视频、独立关键帧、本地路径、项目名和原文件名不发送。
- 分析前在当前项目页展示接收方、发送内容和用途，由用户点击后才外发。
- API 密钥只保存在操作系统安全存储中，界面保存成功后只显示“已配置”，不回显完整密钥。
- 失败后由用户手动重试。每个 attempt 最多发送一次模型请求，后台不自动重试，也不静默更换供应商、地域、模型或请求参数。
- 首版使用已明确支持的 `response_format.type=json_object`，在可信系统提示中给出完整 JSON 契约，再用本地 Pydantic 模型严格校验。`json_schema` strict 模式留待专项兼容验收，不在失败时自动改用另一响应模式。
- 结构化结果先写入已验证检查点，再提交项目完成状态。若模型已成功但本地最终提交失败，下次手动重试复用检查点，不再次产生模型请求。
- Ticket 04 在当前项目页提供只读基础报告。视频定位、时间轴联动和三栏工作台由 Ticket 05 实现；人工编辑由 Ticket 06 实现；策略和 Prompt 由 Ticket 07 按需生成。

## 范围

- 查询分析服务配置状态，配置并验证用户自己的百炼 API 密钥。
- 使用操作系统安全存储读写密钥，明确拒绝明文文件和空安全后端。
- 在真实密钥验证前披露该操作会发送一个不含项目数据的最小请求，并可能产生一次极小费用。
- 在用户点击分析后读取、验证和哈希两个固定分析代理文件。
- 通过固定北京地域接口向 `qwen3.7-flash` 发出一次图像加 JSON 文本请求。
- 限制输入文件大小、模型响应大小、条目数量、文本长度、时间范围和证据引用。
- 把模型 JSON 转换为稳定领域结构，并合并本地可复刻性结论。
- 持久化排队、运行、完成、失败、attempt 和稳定错误；支持服务重启后的手动恢复。
- 在当前项目页展示披露、配置状态、分析阶段、错误、重试和只读基础报告。
- 桌面端允许配置、分析和重试；窄屏只读查看已有状态和报告。

## 非目标

- 不上传完整参考视频、独立关键帧或本地预处理内部文件。
- 不支持用户自定义供应商、地域、模型、接口地址、Prompt、超时或采样参数。
- 不通过模型列表接口推断模型是否可用，也不在后台探测或消费用户额度。
- 不自动重试、并行执行多次语义请求或在服务重启后自动恢复外发。
- 不保存供应商原始响应、请求中的 base64 图像、完整密钥或请求 Authorization 头。
- 不把模型生成的“观察事实”宣传为经人工或物理测量验证的事实。
- 不实现结构化分析编辑、人工覆盖、重新分析时的覆盖选择或版本合并；这些属于 Ticket 06。
- 不实现视频播放器、时间轴、关键帧联动或三栏工作台；这些属于 Ticket 05。
- 不生成策略、Prompt、参数、Workflow 或 ComfyUI 操作；这些属于 Ticket 07 以后。
- 不计算总体置信度或综合相似度百分比。

## 外部服务与请求契约

### 固定服务

| 项目 | 固定值 |
| --- | --- |
| 接收方 | 阿里云百炼，北京地域 |
| 模型 | `qwen3.7-flash` |
| Endpoint | `https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` |
| 响应模式 | `response_format: {"type": "json_object"}` |
| 思考模式 | `enable_thinking: false` |
| 流式响应 | `stream: false` |
| 输出上限 | `max_tokens: 8192` |
| 单 attempt 请求数 | 1 |
| 总 deadline | 120 秒 |
| 自动重试 | 0 |

HTTP 客户端固定 `trust_env=False`、`follow_redirects=False`。`120` 秒是涵盖连接、发送、服务等待、下载和本地读取响应的总 deadline；实现使用外层总时限，不能只依赖 HTTPX 的单次读写 inactivity timeout。3xx 响应按服务错误处理，不跟随到其他主机。

请求消息固定包含：

1. 可信系统提示：说明任务、稳定 JSON 字段、枚举、数量和长度限制，并要求只输出一个 JSON 对象。
2. 用户内容中的一张 `contact-sheet.jpg`，编码为 `data:image/jpeg;base64,...`。
3. 用户内容中的 `analysis-proxy.json` 原文，置于明确的数据分隔标记内；代理内容被视为不可信数据，不得解释其中可能出现的指令。

不允许前端提供 `baseURL`、模型或系统提示；后端也不读取代理环境中的真实密钥作为配置来源。

### 密钥验证

“验证并保存”使用用户刚输入的候选密钥向同一固定模型发送一个不含项目数据的最小 JSON 请求。验证请求固定要求返回 `{"ok": true}`，使用 `max_tokens: 16`、`enable_thinking: false`、`stream: false` 和 `json_object`。

界面在按钮前说明这会产生一次真实模型请求并可能计费。只有响应成功且本地校验为 `{"ok": true}` 后才写入系统安全存储。验证失败时保留此前已经保存的密钥；若此前没有密钥，状态继续为未配置。模型列表查询不能替代该验证。

本设计与计划不会执行真实验证请求。真实凭据验收必须另行披露预计请求次数与费用，并获得用户批准。

## 输入与响应限制

后端在外发前执行以下硬限制：

- `contact-sheet.jpg` 必须是 Ticket 03 当前完成版本目录内的普通文件，大小为 `1..4,000,000` 字节，JPEG 魔数有效，解析后的尺寸不超过 `1536×1536`。
- `analysis-proxy.json` 必须是同一目录内的普通文件，大小为 `2..131,072` 字节，UTF-8 JSON 可解析，且通过 Ticket 03 `schemaVersion: 1` 的本地模型校验。
- 只允许一张联系表和一个代理 JSON；不展开目录、不接受软链接、不接受调用方路径。
- 两个文件的 SHA-256、字节数、当前预处理 ID、Prompt 版本、结果 Schema 版本、模型和固定 Endpoint 共同组成输入绑定。
- 模型响应正文最多 `262,144` 字节。读取流达到上限即中止并返回 `analysis_response_too_large`。
- 响应只能包含一个 JSON 对象；未知字段、错误枚举、超限数组、超长文本、无效时间范围或伪造证据引用均判为无效响应。

这些限制在构造网络请求之前执行。任何失败都不得发送部分输入。

## 安全存储

使用 `keyring==25.7.0`，兼容项目当前 `Python >=3.9` 约束；实施时在本机 Python 3.9.6 上进行真实系统后端兼容测试，不升级 Python。

固定条目：

```text
service: ai-video-reverse-engineer.analysis-service
username: bailian-api-key
```

应用启动时检查选中的 keyring backend：

- backend 必须可用且不是 `keyring.backends.fail.Keyring`、`keyring.backends.null.Keyring` 或任何明文文件后端。
- `get_password`、`set_password` 的异常转换为 `secure_storage_unavailable`，日志不包含密钥、异常原文中的密钥或密钥前后缀。
- 密钥不写入 `projects.json`、预处理/语义分析目录、浏览器 `localStorage/sessionStorage/IndexedDB`、导出数据、日志或 API 响应。
- 前端密钥字段只存在于组件内存；请求结束后清空。读取配置状态只返回 `configured: boolean` 和固定服务信息。
- 运行语义分析时只从系统安全存储读取；不从环境变量、历史对话、代理环境或项目文件读取。

如果系统后端不可用，配置和分析均被阻止，但项目、本地预处理和已有报告仍可读取。

## 本地 API 防护

应用按 loopback 本地部署。涉及密钥或付费外发的 mutation 固定为：

```http
PUT /api/analysis-service/key
POST /api/projects/{project_id}/semantic-analysis
Content-Type: application/json
X-AIVRE-Intent: semantic-analysis
Origin: http://127.0.0.1:5173
```

允许的前端 Origin 只有：

- `http://127.0.0.1:5173`
- `http://localhost:5173`
- `http://127.0.0.1:4173`
- `http://localhost:4173`

不启用通配 CORS。敏感 mutation 缺少固定自定义头、Content-Type 不符、Origin 缺失或不在允许集合时返回 `403 request_origin_rejected`，并且在读取密钥正文或构造外部请求前拒绝。

`PUT /api/analysis-service/key` 使用专用安全解析路径，非法 JSON、空值、类型错误和超过 512 字符统一返回稳定错误，不让 FastAPI/Pydantic 默认 `422` 把原始密钥值放入错误响应。日志只记录稳定 code、项目/attempt ID、输入/响应字节数和耗时，不记录请求正文、模型正文、Authorization 或底层异常全文。

## 领域模型

### 项目任务状态

```text
Project
  ...Ticket 01～03 fields
  semanticAnalysis: SemanticAnalysis | null

SemanticAnalysis
  id: string
  sourcePreprocessingId: string
  inputBinding: SemanticInputBinding
  status: queued | running | completed | failed
  attemptNumber: integer >= 1
  queuedAt: ISO-8601 string
  startedAt: ISO-8601 string | null
  updatedAt: ISO-8601 string
  completedAt: ISO-8601 string | null
  result: StructuredAnalysis | null
  error: SemanticAnalysisError | null

SemanticInputBinding
  contactSheetSha256: 64-char lowercase hex
  analysisProxySha256: 64-char lowercase hex
  contactSheetBytes: integer
  analysisProxyBytes: integer
  promptVersion: 1
  resultSchemaVersion: 1
  model: qwen3.7-flash

SemanticAnalysisError
  code: string
  message: string
  retryable: boolean
```

`semanticAnalysis` 默认为 `null`，旧项目继续可读。项目只保存当前语义任务与稳定结果，不保存密钥、原始模型响应或 base64 联系表。

### 稳定报告

```text
StructuredAnalysis
  schemaVersion: 1
  baseFacts: BaseFacts
  applicability: FinalApplicability
  subjects: AnalysisItem[]
  scenes: AnalysisItem[]
  actions: AnalysisItem[]
  camera: AnalysisItem[]
  lighting: AnalysisItem[]

BaseFacts
  durationSeconds: number
  width: integer
  height: integer
  frameRate: number
  sceneCount: integer
  motionLevel: light | moderate | high | unavailable

FinalApplicability
  status: in_scope | out_of_scope | needs_review
  checks: ApplicabilityCheck[]

ApplicabilityCheck
  criterion: single_shot | motion_range | primary_subject_count | complex_interaction
  status: passed | failed | needs_review
  message: string
  evidence: string

AnalysisItem
  id: string
  category: subject | scene | action | camera | lighting
  kind: observation | reproduction_suggestion | unsupported_capability
  text: string
  timeRange: TimeRange | null
  evidence: EvidenceReference[]
  reviewStatus: supported | needs_review
  uncertaintyReason: string | null

TimeRange
  startSeconds: number
  endSeconds: number

EvidenceReference
  keyframeIndex: integer
  timeSeconds: number
```

`baseFacts` 完全由当前 `referenceVideo`、`analysis-proxy.json` 和 Ticket 03 摘要复制或推导，模型不能填写或覆盖。模型只返回语义适用性与五类条目的候选内容；后端生成 item ID、填入本地事实并合并适用性。

数量和文本限制固定为：

| 字段 | 上限 |
| --- | --- |
| `subjects` | 8 |
| `scenes` | 12 |
| `actions` | 24 |
| `camera` | 16 |
| `lighting` | 16 |
| 五类条目总数 | 64 |
| 单条 `text` | 500 个 Unicode 字符 |
| `uncertaintyReason` | 300 个 Unicode 字符 |
| 单条证据引用 | 12 |

每个 `observation` 至少引用一个真实代理关键帧；`keyframeIndex` 与 `timeSeconds` 必须匹配代理中的同一关键帧，允许的浮点误差为 `0.001` 秒。时间范围必须满足 `0 <= startSeconds <= endSeconds <= durationSeconds`。`reviewStatus=needs_review` 时必须提供非空 `uncertaintyReason`；`supported` 时原因必须为 `null`。证据校验仅证明模型引用了已发送画面，不表示该观察已经由人类确认。

## 可复刻性合并

模型语义契约额外返回：

```text
primarySubjectCount: integer 0..8 | null
primarySubjectReviewReason: string | null
complexInteraction: absent | present | needs_review
complexInteractionReviewReason: string | null
```

最终四项检查规则固定：

| 检查 | 合并规则 |
| --- | --- |
| `single_shot` | 原样保留 Ticket 03 的 `passed/failed`；模型无权改变。 |
| `motion_range` | Ticket 03 `passed/failed` 原样保留；原 `not_assessed` 转为 `needs_review`。模型无权改变。 |
| `primary_subject_count` | `1` 为 `passed`；`0`、`2..8` 为 `failed`；`null` 为 `needs_review`。 |
| `complex_interaction` | `absent` 为 `passed`；`present` 为 `failed`；`needs_review` 为 `needs_review`。 |

总状态按优先级计算：

1. 任一检查为 `failed`，总状态为 `out_of_scope`。
2. 没有失败但任一检查为 `needs_review`，总状态为 `needs_review`。
3. 四项全部为 `passed`，总状态为 `in_scope`。

因此模型不能把多镜头或高运动的本地 `out_of_scope` 改为 `in_scope`。Ticket 03 的证据文案保留在最终检查中。语义不明确时必须给出 `needs_review`，不使用 `not_assessed` 作为 Ticket 04 的最终状态。

## 任务、attempt 与恢复

### 启动条件

```http
POST /api/projects/{project_id}/semantic-analysis
```

请求正文固定为空 JSON 对象 `{}`，并通过敏感 mutation 防护。

- 没有参考视频或当前有效的完成预处理：`409 preprocessing_required`。
- 预处理分析代理缺失、越界、哈希/Schema 无效：`409 analysis_proxy_invalid`，不外发。
- 系统安全存储不可用：`503 secure_storage_unavailable`。
- 没有已保存密钥：`409 analysis_service_unconfigured`。
- 当前项目语义分析已排队或运行：`409 semantic_analysis_in_progress`。
- 当前绑定的完成结果存在：幂等返回 `200`，不再次调用模型。
- 新任务、普通失败重试或绑定变化：返回 `202` 并排队。

替换参考视频会使本地预处理和语义分析一起失效。语义分析 `queued/running` 期间替换视频返回 `409 semantic_analysis_in_progress`。启动时重新计算输入绑定；预处理 ID、任一哈希、Prompt 版本、Schema 版本或模型变化都创建新的语义分析 ID，从 attempt 1 开始。

### 单工作线程

语义分析使用独立于 FFmpeg 队列的最小单工作线程。不同项目按启动顺序排队，同一项目只允许一个活跃任务。可以复用现有单工作线程队列实现，但不为了本 Ticket 大范围重构本地预处理队列。

状态变化：

```text
用户点击 -> queued -> running -> completed
                             \-> failed
```

普通失败后再次点击，复用同一个语义分析 ID，`attemptNumber += 1`，清除旧错误并排队。每个 attempt 最多调用模型一次。鉴权、超时、网络、限流、服务、响应过大和无效响应不会触发后台重试。

### 检查点

```text
<data_dir>/project-files/<project-id>/semantic-analysis/<analysis-id>/
  attempt-0001/
    request-manifest.json
    validated-result.json
  manifest.json
```

`request-manifest.json` 只记录 attempt、输入绑定、请求字节数、固定供应商/地域/模型、开始结束时间和稳定结果 code，不保存请求消息、图片 base64、密钥或原始响应。

响应通过全部本地校验后，以原子写入提交 `validated-result.json`。随后将同一稳定结果写入 `Project.semanticAnalysis.result` 并提交 `completed` 状态，最后写完成 `manifest.json`。

若 `validated-result.json` 已提交但项目 finalize 失败，项目状态写为 `failed / analysis_finalize_failed`。下次用户手动重试时，后端先校验检查点的分析 ID、attempt、输入绑定和 Schema；有效则直接 finalize，不增加 attempt、不发网络请求。检查点无效时清理该 attempt 的检查点，再开始一个新的手动 attempt。

服务启动时把遗留 `queued/running` 统一转为 `failed / semantic_analysis_interrupted`。重启不自动排队、不读取密钥、不外发；用户点击重试后才恢复。

## 错误模型

| code | 含义 | retryable |
| --- | --- | --- |
| `analysis_service_unconfigured` | 未保存密钥 | `false` |
| `secure_storage_unavailable` | 系统安全存储不可用 | `false` |
| `analysis_authentication_failed` | 密钥无效或无权限 | `false` |
| `analysis_rate_limited` | 供应商限流或额度限制 | `true` |
| `analysis_timeout` | 120 秒总 deadline 超时 | `true` |
| `analysis_network_error` | DNS、TLS、连接或传输失败 | `true` |
| `analysis_service_error` | 供应商非鉴权、非限流错误或重定向 | `true` |
| `analysis_response_too_large` | 响应超过 262,144 字节 | `true` |
| `analysis_invalid_response` | JSON 或稳定契约校验失败 | `true` |
| `analysis_proxy_invalid` | 本地代理输入无效 | `false` |
| `analysis_finalize_failed` | 已验证检查点无法提交到项目 | `true` |
| `semantic_analysis_interrupted` | 服务在排队或运行期间退出 | `true` |

面向用户的信息只描述可操作原因。日志使用上述 code，并可附项目 ID、分析 ID、attempt、输入/响应字节数和耗时；不得记录底层响应正文或可能含密钥的异常字符串。

## API 设计

### 查询配置

```http
GET /api/analysis-service
```

成功返回：

```json
{
  "provider": "阿里云百炼",
  "region": "北京",
  "model": "qwen3.7-flash",
  "configured": true,
  "secureStorageAvailable": true
}
```

不返回密钥掩码、长度、前缀或更新时间。`GET /api/capabilities` 的 `analysisService` 同步返回 `connected/已配置`、`unconfigured/未配置` 或 `unavailable/系统安全存储不可用`。

### 验证并保存密钥

```http
PUT /api/analysis-service/key
Content-Type: application/json
X-AIVRE-Intent: semantic-analysis

{"apiKey":"..."}
```

成功返回与查询配置相同的无密钥状态。解析错误统一返回 `400 invalid_api_key_input`；验证失败使用稳定服务错误。失败不会删除或覆盖既有密钥。

### 启动、重试和读取分析

启动与重试使用同一个 `POST /api/projects/{project_id}/semantic-analysis`。状态和报告继续通过现有 `GET /api/projects/{project_id}` 读取。前端只在 `queued/running` 时每秒轮询；读取失败只显示刷新错误，不改写后端任务状态。

## 前端体验

当前项目页在本地预处理面板之后增加“语义分析”区域：

1. 固定服务卡展示“阿里云百炼 · 北京 · qwen3.7-flash”和已配置/未配置/不可用状态。
2. 未配置时，桌面端显示密码输入框与“验证并保存”按钮，并在按钮前写明会发送一次不含项目数据的最小请求且可能计费。
3. 预处理完成后展示外发披露：接收方、`contact-sheet.jpg`、`analysis-proxy.json`、用途，以及“完整参考视频不会上传”。
4. 桌面端用户点击“开始语义分析”后才发起请求；排队或运行时禁用密钥替换、重复分析和参考视频替换。
5. 失败时显示区别化错误和“重新分析”按钮。该按钮表示一个新的手动 attempt；若后端有有效检查点，服务会本地恢复。
6. 完成后展示基础事实、最终适用性以及主体、场景、动作、运镜、光线五个只读分组。
7. 每条内容同时展示类型文字、时间范围、证据关键帧和局部“需人工确认”原因；不能只用颜色区分。
8. 报告中的 `observation` 显示为“模型观察”，避免暗示人工验证；`reproduction_suggestion` 显示为“复刻建议”；`unsupported_capability` 显示为“当前不支持”。
9. 阶段区串联展示 Ticket 03 的五个本地阶段、“语义分析”和“策略准备”。策略准备在本 Ticket 固定显示“等待后续步骤”，不会触发模型或策略生成。

移动宽度只允许查看配置状态、分析状态和已有报告，不显示密钥输入、验证、开始或重试按钮。Ticket 04 不实现时间点击、播放器定位或报告编辑。

## 并发与替换规则

- `semanticAnalysis.status` 为 `queued/running` 时，密钥替换返回 `409 semantic_analysis_in_progress`，避免运行中凭据语义变化。
- 任一项目语义任务活跃时可读取配置和项目，但不能对该项目重复提交。
- 参考视频替换中间件在读取上传正文前检查语义运行状态并返回 409。
- 替换成功提交后把 `localPreprocessing` 和 `semanticAnalysis` 一起设为 `null`，再尽力清理旧目录；替换失败保留旧视频、预处理和语义报告。
- 本地预处理与语义分析各自单线程；本 Ticket 不建立跨队列全局互斥。语义任务只读取已完成、不可变的代理版本，不会启动 FFmpeg。

## 文件边界

### 后端新增

- `backend/app/semantic_analysis.py`：领域模型、严格模型响应、条目/证据/时间校验和本地结论合并。
- `backend/app/semantic_analysis_storage.py`：输入绑定、代理安全读取、attempt 清单、已验证检查点和完成清单的原子存储。
- `backend/app/analysis_service_secrets.py`：系统安全存储接口、keyring backend 拒绝规则和无密钥状态。
- `backend/app/bailian_client.py`：固定 Endpoint、请求体、总 deadline、响应大小限制和稳定服务错误。
- `backend/app/semantic_analysis_jobs.py`：最小单工作线程队列；沿用已有队列行为，保留语义命名和独立生命周期。
- 对应后端测试文件分别覆盖纯模型、存储、密钥、客户端、队列和 API。

### 后端修改

- `backend/app/main.py`：扩展 `Project.semanticAnalysis`，装配密钥/客户端/语义队列，新增配置与启动接口、重启协调、项目更新、替换锁和失效清理。
- `backend/pyproject.toml`、`backend/requirements.lock`：加入 `httpx==0.28.1` 和 `keyring==25.7.0` 及锁定的运行依赖；维持 `requires-python = ">=3.9"`。
- `backend/tests/test_projects_api.py`、`backend/tests/test_reference_video_upload_api.py`：旧项目默认值和替换事务回归。
- `README.md`：北京地域服务、系统安全存储、外发边界、手动调用和运行前置条件。

### 前端新增

- `frontend/src/semanticAnalysisApi.ts`：配置查询、密钥验证保存、语义启动和稳定错误转换。
- `frontend/src/SemanticAnalysisPanel.tsx`：配置、外发披露、手动启动/重试、轮询、阶段与基础只读报告。
- 对应测试覆盖费用披露、密钥清空、请求防护头、报告、失败和响应式行为。

### 前端修改

- `frontend/src/models.ts`：增加服务配置、语义任务、错误、最终适用性和稳定报告类型；`Project.semanticAnalysis` 默认可空。
- `frontend/src/App.tsx`：挂载语义面板、同步项目轮询结果、刷新能力状态，并把语义运行锁传给参考视频面板。
- `frontend/src/App.test.tsx`：旧项目恢复、完成报告重开、项目集合同步和过期轮询保护。
- `frontend/src/ReferenceVideoPanel.tsx` 及测试：语义任务活跃时禁用替换，成功替换清空语义结果的可见状态。
- `frontend/src/styles.css`：配置、披露、阶段、报告、错误、可见焦点与 1024px 窄屏只读样式。

`main.py` 仍作为装配和 HTTP 边界，不承载供应商解析、领域校验或检查点细节。

## 验证策略

### 自动化

- 纯模型：严格 JSON、未知字段、枚举、条目数、文本、时间、证据、局部不确定性和本地结论不可覆盖。
- 密钥：真实系统 backend 识别规则、fail/null/plaintext 拒绝、无回显、保存失败和验证失败保留旧密钥。
- 客户端：固定 URL/model/body、单请求、无 redirect、无代理环境、总 deadline、响应流上限、鉴权/限流/网络/服务/无效响应映射。
- 存储：路径约束、无软链接、字节限制、哈希绑定、原子检查点、finalize 恢复以及不落盘密钥/原始响应/base64。
- API：敏感 mutation 的 Origin/Header/Content-Type 防护；旧项目兼容；开始、幂等、去重、失败重试、重启中断、检查点恢复、替换锁和事务。
- 前端：费用与数据披露、密码不回显、手动开始、轮询、错误区分、报告类型/时间/证据、窄屏只读、键盘与焦点。
- 回归：Ticket 01～03 全部后端和前端测试、颜色对比和生产构建。

供应商客户端自动化使用本地 mock transport，不执行真实百炼请求，不需要真实密钥。

### 真实验收

真实验收前单独向用户披露请求次数和可能费用。最低验收集为：

- 1 次最小密钥验证请求。
- 1 次当前受支持样本的语义分析请求。
- 失败分类优先使用 mock，不通过故意提交错误真实请求消耗费用或触发账户风险。

真实验收确认系统安全存储在当前 Python 3.9.6 环境可用、密钥未落盘、外发只有两个代理内容、结构化结果可重新打开，并单独记录供应商网络耗时。常规样本从用户确认到报告就绪应纳入总规格的两分钟目标，其中供应商排队和网络耗时单独记录。

## 参考依据

- [百炼 OpenAI Chat Completions 兼容接口](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)
- [百炼结构化输出说明](https://help.aliyun.com/en/model-studio/qwen-structured-output)
- [keyring 25.7.0](https://pypi.org/project/keyring/25.7.0/)
- [keyring 官方文档](https://keyring.readthedocs.io/en/stable/)
- [`docs/adr/0004-bailian-analysis-with-user-owned-key.md`](../../docs/adr/0004-bailian-analysis-with-user-owned-key.md)
- [`03-build-analysis-proxy-and-assess-reproducibility-design.md`](./03-build-analysis-proxy-and-assess-reproducibility-design.md)
