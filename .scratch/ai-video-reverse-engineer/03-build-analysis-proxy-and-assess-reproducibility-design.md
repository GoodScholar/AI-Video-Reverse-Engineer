# Ticket 03：生成分析代理并判断可复刻范围——设计规格

Status: approved

## 目标

对 Ticket 02 已校验并由应用托管的参考视频执行可恢复的本地预处理，生成供后续语义分析使用的分析代理，并根据本地可验证信号给出初步可复刻性判断。

本 Ticket 只负责本地能够可靠判断的多镜头和运动强度边界。主要主体数量与复杂交互留给 Ticket 04 的语义分析确认；只有 Ticket 04 合并语义结果后，项目才能最终标记为处于可复刻范围。

## 用户与核心体验

首要用户仍是 ComfyUI 创作者，次要用户是已具备本地 ComfyUI 环境的普通创作者。

参考视频上传或替换成功后不自动执行预处理。用户在项目准备页明确点击“开始本地预处理”，再看到解码、镜头检测、关键帧提取、运动分析和初步判断的阶段状态。该过程只在本机运行，不要求分析服务或本地 ComfyUI 已连接，也不会发送任何内容到外部服务。

成功后，用户看到关键帧数量、镜头数量、运动强度摘要和以下两种本地阶段结论之一：

- `out_of_scope`：本地已经确认多镜头或运动强度超限。
- `pending_semantic_confirmation`：本地检查通过，但主要主体数量和复杂交互仍需 Ticket 04 确认。

超出可复刻范围不阻止 Ticket 04 生成分析报告，但界面必须明确说明当前不承诺生成可靠的可执行工作流。

## 范围

- 手动启动、排队、执行、查看和重试本地预处理。
- 使用现有 FFmpeg 完成完整解码检查、镜头检测、关键帧提取、联系表生成和运动数据计算。
- 为最高 UHD 4K 的参考视频生成低分辨率分析流和分析代理，不改变本地托管的参考视频。
- 按固定、可测试的阈值判断多镜头和运动强度是否超出可复刻范围。
- 持久化阶段状态、版本化产物、错误和初步可复刻性结论。
- 服务重启或项目重新打开后复用仍然有效的完成结果；中断任务保留已安全提交的阶段并可重试。
- 替换参考视频时安全失效和清理旧预处理结果。
- 桌面端提供启动和重试；窄屏只读展示状态与结果。

## 非目标

- 不调用分析服务，不配置或读取阿里云百炼密钥。
- 不判断主要主体数量或复杂交互，只将两项标记为待语义分析确认。
- 不生成主体、场景、动作、运镜或光线等语义结果。
- 不使用 OpenCV、NumPy、PyAV、本地视觉模型或第三方镜头检测库。
- 不输出光流方向、人物轨迹、动作识别或相机运动语义。
- 不自动开始预处理，不提供用户可调的分析阈值。
- 不提供取消任务、强制重新生成或并行处理多个视频。
- 不展示播放器、关键帧胶片条、时间轴或三栏复刻工作台。
- 不生成 Prompt、复刻方案、控制素材或 ComfyUI Workflow。
- 不计算或展示总体相似度百分比。

## 技术方案选择

采用 FFmpeg 单一媒体引擎方案。Ticket 02 已要求 FFmpeg/ffprobe 作为本地前置条件；继续使用 FFmpeg 可以保持媒体解码口径一致，并避免增加大型二进制 Python 依赖和第二套 codec 行为。

FFmpeg 的 `scdet` 滤镜提供逐帧平均绝对差异 `lavfi.scd.mafd`、场景变化分数 `lavfi.scd.score` 和满足阈值的时间点。Ticket 03 将这些数据用于镜头切换和运动强度的可解释启发式判断。

该方案有意不把运动信号描述为光流或运动语义。`motion.json` 保存的是固定采样率下的帧变化强度时间序列，足以支持本 Ticket 的轻、中、高运动边界和 Ticket 04 的辅助语义输入。

## 总体架构

采用本地、异步、单进程任务模型。FastAPI 接收启动请求后立即返回，应用内全局单工作线程依次处理排队项目。前端在任务排队或运行期间轮询项目接口。MVP 不引入 Celery、Redis、数据库、WebSocket 或服务器发送事件。

执行流程：

```text
用户手动启动
  -> 进入单工作线程队列
  -> 完整解码检查
  -> 镜头检测
  -> 关键帧提取与联系表生成
  -> 运动分析
  -> 初步可复刻性判断
  -> 原子提交完成状态
```

每个阶段只读取当前参考视频和已完成的前置阶段产物。阶段在自己的临时路径中写入，必需文件全部完成后再原子提交并更新项目状态。失败阶段的半成品被清理，已完成阶段继续保留。

阶段与完成产物的对应关系固定为：

| 阶段 | 完成产物 |
| --- | --- |
| `decoding` | `decode.json` |
| `sceneDetection` | `scene-changes.json` |
| `keyframeExtraction` | `keyframes/` 中的全部清单帧及 `contact-sheet.jpg` |
| `motionAnalysis` | `motion.json` |
| `reproducibilityAssessment` | `analysis-proxy.json` 与最终 `manifest.json` |

### 后端文件边界

- `backend/app/main.py`
  - 扩展 `Project`，增加可空的 `localPreprocessing`。
  - 装配预处理任务队列和项目状态更新回调。
  - 提供启动/重试接口，并扩展参考视频替换的并发与失效规则。
  - 应用启动时协调遗留的排队或运行状态。
- `backend/app/local_preprocessing.py`
  - 定义阶段、任务、错误、代理摘要和初步可复刻性模型。
  - 解析 FFmpeg 元数据，筛选镜头切换点，计算运动统计、关键帧时间点和初步结论。
  - 保存所有固定算法常量和算法版本。
- `backend/app/local_preprocessing_runner.py`
  - 使用参数数组调用 FFmpeg，不通过 shell 拼接路径。
  - 按阶段执行命令、设置超时、检查退出状态并把供应方输出转换为稳定领域错误。
  - 启动前验证当前 FFmpeg 构建包含 `scdet`、`scale`、`metadata`、`drawtext` 和 `tile` 滤镜。
- `backend/app/local_preprocessing_storage.py`
  - 推导版本目录和相对产物路径。
  - 原子写入 JSON、提交阶段目录、验证复用所需文件、清理半成品和失效版本。
- `backend/app/local_preprocessing_jobs.py`
  - 管理单工作线程队列、同项目去重和阶段回调。
  - 队列只保存运行期控制对象；项目文件是用户可观察状态的持久化真值。
- `backend/tests/test_local_preprocessing.py`
  - 验证纯算法、边界常量、元数据解析和领域模型。
- `backend/tests/test_local_preprocessing_runner.py`
  - 验证阶段命令、真实 FFmpeg 样本、错误转换与产物生成。
- `backend/tests/test_local_preprocessing_api.py`
  - 从 HTTP 边界验证启动、排队、重试、复用、重启与参考视频替换。

不把 FFmpeg 阶段逻辑继续堆入 `main.py`，也不借本 Ticket 重构 Ticket 01/02 的无关项目存储代码。

### 前端文件边界

- `frontend/src/models.ts`
  - 增加预处理任务、阶段、摘要、错误和初步可复刻性类型。
- `frontend/src/localPreprocessingApi.ts`
  - 封装启动/重试请求和稳定错误读取。
- `frontend/src/LocalPreprocessingPanel.tsx`
  - 承载手动启动、阶段状态、摘要、范围说明和失败重试。
- `frontend/src/LocalPreprocessingPanel.test.tsx`
  - 验证所有用户可观察状态、轮询、焦点和响应式边界。
- `frontend/src/App.tsx`
  - 将预处理面板接入当前项目页，并把轮询结果同步回当前项目和首页集合。
- `frontend/src/App.test.tsx`
  - 验证项目级数据同步、重新打开与旧项目兼容。
- `frontend/src/ReferenceVideoPanel.tsx`
  - 在处理期间禁用替换，并在已有完成结果时扩展替换确认文案。
- `frontend/src/ReferenceVideoPanel.test.tsx`
  - 验证禁止替换和成功/失败替换后的预处理结果规则。
- `frontend/src/styles.css`
  - 增加阶段列表、结论、错误和窄屏只读样式。
- `README.md`
  - 说明 FFmpeg 同时承担预处理、本地数据目录新增产物和分析代理不会包含完整视频。

## API 设计

### 启动或重试本地预处理

```http
POST /api/projects/{project_id}/local-preprocessing
```

请求没有正文。

- 新任务或失败重试返回 `202 Accepted` 和更新后的完整 `Project`。
- 当前算法版本的有效完成结果存在时，幂等返回 `200 OK` 和现有完整 `Project`，不重新排队。
- 项目不存在返回 `404 project_not_found`。
- 项目没有参考视频返回 `409 reference_video_required`。
- 同一项目已经排队或运行返回 `409 preprocessing_in_progress`。
- 无法安全读取或写入项目状态返回 `503 storage_unavailable`。

异步 FFmpeg 或产物错误不会改变已经返回的启动响应；它们写入 `localPreprocessing.error`，由后续项目读取观察。

### 读取状态

继续使用现有接口：

```http
GET /api/projects/{project_id}
```

前端只在任务总状态为 `queued` 或 `running` 时每秒读取一次。任务完成、失败、项目切换或组件卸载时停止轮询。单次读取失败只显示“暂时无法刷新状态”和重试入口，不把后端任务错误地标记为失败。

## 项目模型

```text
Project
  id: string
  name: string
  createdAt: ISO-8601 string
  updatedAt: ISO-8601 string
  referenceVideo: ReferenceVideo | null
  localPreprocessing: LocalPreprocessing | null

LocalPreprocessing
  id: string
  sourceReferenceVideoId: string
  algorithmVersion: 1
  status: queued | running | completed | failed
  currentStage: PreprocessingStageName | null
  stages: PreprocessingStageState[]
  queuedAt: ISO-8601 string
  startedAt: ISO-8601 string | null
  updatedAt: ISO-8601 string
  completedAt: ISO-8601 string | null
  proxySummary: AnalysisProxySummary | null
  reproducibilityAssessment: ReproducibilityAssessment | null
  error: LocalPreprocessingError | null

PreprocessingStageName
  decoding
  sceneDetection
  keyframeExtraction
  motionAnalysis
  reproducibilityAssessment

PreprocessingStageState
  name: PreprocessingStageName
  status: pending | running | completed | failed
  startedAt: ISO-8601 string | null
  completedAt: ISO-8601 string | null

AnalysisProxySummary
  keyframeCount: integer
  contactSheetCount: 1
  sceneChangeCount: integer
  motionP50: number | null
  motionP90: number | null
  motionPeak: number | null
  motionLevel: light | moderate | high | unavailable

ReproducibilityAssessment
  status: out_of_scope | pending_semantic_confirmation
  checks: ReproducibilityCheck[]

ReproducibilityCheck
  criterion: single_shot | motion_range | primary_subject_count | complex_interaction
  status: passed | failed | pending | not_assessed
  message: string
  evidence: string

LocalPreprocessingError
  code: string
  message: string
  stage: PreprocessingStageName
  retryable: true
```

`localPreprocessing` 默认为 `null`，因此 Ticket 01/02 保存的旧项目仍能读取。阶段数组始终按固定五阶段顺序返回，便于前端稳定展示。

开始、阶段变化、完成、失败和成功替换参考视频都会更新项目 `updatedAt`。仅轮询读取不会更新时间。

## 分析代理与本地产物

每次预处理使用唯一内部 ID，目录如下：

```text
<data_dir>/project-files/<project-id>/local-preprocessing/<preprocessing-id>/
  decode.json
  scene-changes.json
  motion.json
  analysis-proxy.json
  contact-sheet.jpg
  manifest.json
  keyframes/
    frame-0001.jpg
    frame-0002.jpg
    frame-0012.jpg  # 最多 12 张；实际目录只包含已选帧
```

### `decode.json`

记录当前参考视频 ID、完整解码完成时间、由已校验时长与帧率推导的预期帧数和 FFmpeg 版本摘要，用作解码阶段的完成标记。不把预期帧数描述为重新探测得到的精确帧数，也不保存绝对输入路径。

### `scene-changes.json`

记录格式版本、算法版本、采样率，以及每个样本的 `timeSeconds`、`mafd` 和 `sceneScore`。另存经过开头忽略和相邻合并规则后的有效切换点。

### `motion.json`

记录格式版本、算法版本、有效运动样本、因镜头切换邻域而排除的时间点、P50、P90、峰值和运动等级。有效样本使用 `scene-changes.json` 中的 MAFD，不增加第二次视频解码。

### `analysis-proxy.json`

这是 Ticket 04 可以读取并发送的稳定数据边界，固定包含：

- `schemaVersion: 1`
- 参考视频时长、显示宽高和帧率
- 关键帧顺序与时间点，不包含本地文件路径
- 镜头数量与有效切换时间点
- 最多 80 个按时间排序的 `timeSeconds + mafd` 运动样本
- P50、P90、峰值和运动等级；无法可靠计算时为 `null + unavailable`
- 联系表文件名固定为 `contact-sheet.jpg`

该文件不包含项目名称、原文件名、绝对路径、用户密钥或供应商信息。

### 关键帧

- 候选集包含首帧、最后一个可读取帧、每个镜头区间的时间中点，以及覆盖完整时长的均匀时间点。
- 目标数量为 `min(12, max(4, ceil(durationSeconds)))`；相距不超过一个 8fps 采样间隔的候选只保留较早者，最后一帧除外。
- 始终保留首尾帧；其余位置先覆盖各镜头中点，再使用最远时间点优先法补齐，使选中时间在完整时长内尽量均匀。若镜头区间多于剩余名额，同样以最远时间点优先法选择代表区间。
- 每张 JPEG 应用视频旋转信息，保持原始宽高比，最长边不超过 768px。
- 文件名只使用内部顺序编号；时间点、显示宽高和相对路径写入 `manifest.json`。

### 联系表

使用最终关键帧生成一个 JPEG 联系表，最多三列，每格最长边不超过 384px。每格包含可读时间码；联系表不包含项目名称、原文件名或本地路径。

Ticket 04 可发送的分析代理严格定义为：

```text
contact-sheet.jpg
analysis-proxy.json
```

`keyframes/`、`decode.json`、`scene-changes.json`、`motion.json` 和 `manifest.json` 是保留在本地的预处理产物。分析代理中不创建压缩视频、低清视频或任何完整参考视频副本。

### `manifest.json`

记录：

- `schemaVersion: 1`
- `algorithmVersion: 1`
- `sourceReferenceVideoId`
- 生成时间
- 固定算法参数
- 各产物相对路径
- 关键帧的顺序、时间点和显示尺寸

不得记录绝对路径、用户密钥、供应商信息或完整参考视频字节。

## 固定算法

### 分析流

- FFmpeg 自动应用视频旋转信息。
- 固定采样率为 8fps。
- 保持宽高比，将最长边缩放至 320px，另一边取合法偶数。
- 使用灰度或亮度平面参与 `scdet` 计算。
- 不把该低分辨率分析流编码成视频文件。

### 镜头检测

- `SCENE_SCORE_THRESHOLD = 10.0`。
- 小于 `0.5` 秒的候选切换点忽略，避免把起始解码差异当成切换。
- 距离小于或等于 `0.5` 秒的连续候选合并，只保留场景分数最高的时间点。
- 有效切换点数量至少为 1 即说明参考视频不是单镜头，`single_shot` 检查失败。

### 运动分析

- 从运动统计中排除每个有效切换点前后各 `0.25` 秒的样本。
- 其余样本使用 `lavfi.scd.mafd` 作为归一化帧变化强度。
- P90 使用排序后的线性插值百分位算法，算法实现和测试固定，不依赖 NumPy。
- 计算至少需要 8 个未排除样本，相当于累计 1 秒分析数据。少于 8 个时，P50、P90 和峰值均为 `null`，等级为 `unavailable`，不得把样本不足解释为低运动。
- 等级边界：
  - `light`：`P90 <= 2.5`
  - `moderate`：`2.5 < P90 <= 8.0`
  - `high`：`P90 > 8.0`
- `high` 说明超出轻中度运动边界，`motion_range` 检查失败。

镜头阈值与运动阈值只作为版本化产品常量存在，界面展示实测摘要和判断依据，但不提供修改入口。未来任何阈值变更都必须提升 `algorithmVersion`，使旧结果明确失效。

## 初步可复刻性判断

四项检查固定为：

| criterion | Ticket 03 状态规则 |
| --- | --- |
| `single_shot` | 无有效切换点为 `passed`；否则为 `failed`。 |
| `motion_range` | `light` 或 `moderate` 为 `passed`；`high` 为 `failed`；`unavailable` 为 `not_assessed`。 |
| `primary_subject_count` | 固定为 `pending`，由 Ticket 04 确认。 |
| `complex_interaction` | 固定为 `pending`，由 Ticket 04 确认。 |

只要 `single_shot` 或 `motion_range` 任一失败，总状态就是 `out_of_scope`，并列出全部本地失败原因。两项都通过时，总状态为 `pending_semantic_confirmation`。运动样本不足只会出现在密集镜头切换排除了大部分样本的情况下；此时 `single_shot` 已经失败，总状态仍为 `out_of_scope`，但界面必须把运动显示为“无法独立评估”，不得伪造运动结论。

Ticket 03 绝不返回 `in_scope`。后续 Ticket 04 合并语义结果时，必须保留本地已确认的失败，不允许语义结果覆盖多镜头或高运动事实。

## 阶段、队列与并发

### 总状态

- `queued`：已经持久化任务，但尚未获得全局工作线程。
- `running`：工作线程正在执行 `currentStage`。
- `completed`：五个阶段全部完成，摘要和初步判断可用。
- `failed`：具体阶段失败，`error` 指明原因和重试位置。

### 单工作线程

本地服务一次只执行一个视频的 FFmpeg 预处理，其他项目按启动顺序排队。这样避免多个最高 UHD 4K 输入同时争抢 CPU、内存和磁盘。排队状态必须持久化，不能只存在内存中。

同一项目已经 `queued` 或 `running` 时拒绝重复启动。不同项目可以进入队列，但服务进程重启不会尝试恢复原运行期队列顺序；所有遗留 `queued` 或 `running` 项目统一转为可重试的中断失败。

### 服务重启协调

应用启动时读取项目数据：

- 遗留 `queued` 项目标记为 `failed / preprocessing_interrupted`，失败阶段为第一个未完成阶段。
- 遗留 `running` 项目标记为 `failed / preprocessing_interrupted`，失败阶段为当时的 `currentStage`。
- 清理失败阶段对应的临时路径，保留此前已提交阶段。
- 已完成项目不改写状态；读取时验证复用有效性。

### 阶段恢复

- 每次重试复用同一个 `localPreprocessing.id`，清除旧错误并重新排队。
- 从第一个不是 `completed` 的阶段开始。
- 已标记完成但必需产物缺失时，该阶段及其后续阶段重置为 `pending`。
- 参考视频 ID 或算法版本不一致时不允许局部复用，创建新的预处理版本并从解码开始。
- 完成结果再次启动时先验证参考视频 ID、算法版本和必需文件；全部有效则幂等返回，缺失则创建新版本。

## 原子存储与参考视频替换

阶段 JSON 先写入同目录唯一临时文件，刷新并 `fsync` 后使用 `os.replace` 提交。关键帧和联系表先共同写入唯一的关键帧阶段临时目录；全部生成并验证后，先把 `keyframes/` 目录移动到最终位置，再以 `os.replace` 提交联系表。若进程在两次提交之间退出，阶段状态仍不是 `completed`，下次启动或重试会删除该阶段已经出现的全部最终产物并重新生成。只有阶段要求的所有产物均已提交，项目阶段才标记为 `completed`。

参考视频替换规则：

1. 项目预处理为 `queued` 或 `running` 时，上传中间件在读取大文件前返回 `409 preprocessing_in_progress`；前端同时禁用替换入口。
2. 已完成或失败的预处理不阻止替换，但确认文案说明成功后旧结果失效。
3. 新参考视频仍按 Ticket 02 的临时文件、探测、项目写锁和原子提交顺序处理。
4. 新文件和项目元数据提交时，把 `localPreprocessing` 原子设为 `null`。
5. 只有新视频与新项目元数据均成功后，才清理旧参考视频和旧预处理目录。
6. 新视频上传、校验或元数据提交失败时，旧参考视频和旧预处理结果都保持有效。
7. 旧预处理目录清理失败不回滚已经有效的新视频，只记录本地日志并留下未引用目录。

## 错误契约

同步 API 错误继续使用 Ticket 02 的结构：

```json
{
  "detail": {
    "code": "reference_video_required",
    "message": "请先添加并校验参考视频。"
  }
}
```

后台阶段错误写入 `localPreprocessing.error`：

| code | stage | message 要点 |
| --- | --- | --- |
| `preprocessing_interrupted` | 中断时的阶段 | 本地服务曾退出，已保留完成阶段，可继续重试。 |
| `ffmpeg_unavailable` | `decoding` | 未找到或无法启动 FFmpeg，提示运行 `ffmpeg -version`。 |
| `ffmpeg_filters_unavailable` | `decoding` | 当前 FFmpeg 构建缺少预处理所需滤镜，列出缺失滤镜。 |
| `video_decode_failed` | `decoding` | 当前托管视频无法完整解码，说明可重新添加参考视频。 |
| `scene_detection_failed` | `sceneDetection` | 无法生成或解析镜头数据，可从该阶段重试。 |
| `keyframe_extraction_failed` | `keyframeExtraction` | 无法提取完整关键帧或联系表，可从该阶段重试。 |
| `motion_analysis_failed` | `motionAnalysis` | 镜头数据缺失或运动统计无法计算，可从该阶段重试。 |
| `assessment_failed` | `reproducibilityAssessment` | 必需摘要不完整，无法形成初步判断。 |
| `preprocessing_storage_unavailable` | 当前阶段 | 数据目录不可写或产物无法原子提交。 |
| `preprocessing_unexpected_error` | 当前阶段 | 发生未预期的本地处理错误，详细技术信息只写入本地日志。 |

FFmpeg 调用设置有限超时，使用参数数组，不信任文件名或 stderr 作为用户消息。stderr 只进入经过截断和脱敏的本地日志；界面显示稳定中文错误。所有后台错误在本 Ticket 中均可重试，`retryable` 固定为 `true`。

每个 FFmpeg 子进程的超时固定为 120 秒；超时归入该进程所属阶段的稳定错误码。算法逻辑和 JSON 计算不另开子进程。

## 项目页交互

在现有 `ReferenceVideoPanel` 下方增加独立的“本地预处理”区域。

| 状态 | 界面行为 |
| --- | --- |
| 无参考视频 | 说明需要先添加参考视频，不显示启动按钮。 |
| 未开始 | 说明当前步骤仅在本机处理且尚不会发送分析代理；显示“开始本地预处理”。 |
| `queued` | 显示“正在等待本机处理资源”和五阶段列表。 |
| `running` | 显示五阶段的已完成、进行中和等待状态，不显示百分比。 |
| `completed` | 显示关键帧数量、镜头数量、运动强度摘要与初步判断。 |
| `failed` | 显示失败阶段、具体原因和“从失败阶段重试”。 |
| 状态刷新失败 | 保留最后一次成功状态，说明暂时无法刷新并提供重新读取。 |

`out_of_scope` 使用完整文字列出实测原因，并说明后续仍可生成分析报告，但不承诺可靠的可执行工作流。`pending_semantic_confirmation` 明确列出主要主体数量和复杂交互仍待分析，不使用“已通过”或“可复刻”等提前结论。

本 Ticket 不展示实际关键帧或联系表；这些视觉资产在 Ticket 05 的复刻工作台中呈现。

## 响应式与无障碍

- 宽度至少 1024px 时提供启动和重试。
- 窄于 1024px 时只读展示状态、摘要和错误；未开始或失败时提示在桌面设备继续，不挂载启动或重试控件。
- 启动按钮使用原生 `button`，支持 Enter 和 Space，操作目标至少 44px。
- 启动成功后焦点移到可聚焦的任务状态标题；提交失败时焦点保留在启动按钮。
- 阶段更新使用 `role="status"` 与 `aria-live="polite"`；后台失败使用 `role="alert"`。
- 阶段和判断同时使用图标与完整文字，不只依赖颜色。
- 完成时 live region 宣告“本地预处理完成”及初步结论；失败时宣告失败阶段和可重试说明。
- 视觉保持暖灰台面、深炭灰信息层级、锈红范围警告与蓝灰辅助状态，不增加阴影、大圆角或装饰动画。
- 尊重 `prefers-reduced-motion`，忙碌状态不依赖旋转动画表达。

## 测试设计

### 纯算法测试

- 解析合法和缺字段的 `scdet` 元数据。
- 忽略 `0.5` 秒之前的切换候选。
- 合并相距恰好 `0.5` 秒及更近的候选，保留最高分。
- `sceneScore` 恰好 `10.0` 算有效切换，低于 `10.0` 不算。
- 从运动样本中排除切换前后恰好 `0.25` 秒的点。
- 固定线性插值百分位算法在空、单值、偶数和奇数样本上的结果。
- P90 恰好 `2.5` 为 `light`，恰好 `8.0` 为 `moderate`，高于 `8.0` 为 `high`。
- 无切换且轻中度运动返回 `pending_semantic_confirmation`，主体与交互检查为 `pending`。
- 多镜头、高运动以及两项同时失败均返回全部对应原因。
- 关键帧目标数量、单采样间隔去重、镜头中点优先和最远时间点补齐结果均确定且不超过 12 张。
- 运动有效样本少于 8 个时返回 `unavailable`，不得落入 `light`。

### 真实 FFmpeg 集成测试

使用测试临时目录和确定性 FFmpeg 合成素材：

- 静态单镜头：零或接近零运动，无切换，待语义确认。
- 连续常规运动：P90 落在轻中度范围，无切换，待语义确认。
- 一次硬切：得到一个有效切换点，判为多镜头超范围。
- 剧烈随机变化：P90 高于 `8.0`，判为高运动超范围。
- 旋转元数据素材：关键帧使用正确显示方向和比例。
- 2 秒 UHD 4K 素材：分析流最长边为 320px，关键帧最长边不超过 768px，原托管文件哈希不变。
- 包含音频的素材：仅处理首个有效视频流，不分析或修改音频。
- 解码失败和 FFmpeg 超时：保存具体阶段错误，不留下半成品。

### 存储与任务测试

- 每阶段只有完成产物才进入正式版本目录。
- 阶段提交失败时清理临时路径并保留前序产物。
- 从每个失败阶段重试时不再次调用已完成阶段。
- 完整结果在同一视频和算法版本下幂等复用。
- 参考视频 ID、算法版本或必需文件不匹配时从解码重新开始。
- 单工作线程按启动顺序处理不同项目，同一项目重复启动被拒绝。
- 服务启动将遗留排队和运行任务变成可重试中断错误。
- 两个项目的产物目录和状态完全隔离。

### API 测试

- 旧项目缺少 `localPreprocessing` 时返回 `null`。
- 无参考视频不能启动；项目不存在返回稳定错误。
- 新启动和失败重试返回 `202`，有效完成结果返回 `200`。
- 排队、五阶段转换、完成和失败均可通过 GET 项目观察。
- 项目重新打开和应用重建后，完成结果仍能读取和验证。
- 运行期间替换在读取大文件前被拒绝。
- 完成后替换成功会清空预处理引用并清理旧目录。
- 替换上传、校验或项目提交失败会保留旧视频和旧预处理结果。

### 前端测试

- 上传完成后不会自动调用预处理接口。
- 无视频、未开始、排队、五阶段运行、完成、失败和状态刷新失败均有正确界面。
- 只在排队或运行期间轮询；完成、失败、项目切换和卸载后停止。
- `out_of_scope` 展示实测原因和后续报告说明。
- `pending_semantic_confirmation` 不显示已处于可复刻范围。
- 运行期间替换入口禁用并有文字原因。
- 已有结果的替换确认说明失效影响；替换失败保留旧摘要。
- 启动、重试、完成和失败后的焦点及 live region 行为符合约定。
- 1024px 提供操作，1023px 及以下只读且无隐藏可操作控件。

### 全量自动验证

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
npm --prefix frontend test
node frontend/scripts/verify-color-contrast.mjs
npm --prefix frontend run build
```

### 真实浏览器验收

1. 在 1280px 项目页上传合法参考视频，确认不会自动开始。
2. 点击“开始本地预处理”，观察排队和五阶段文字状态。
3. 使用单镜头常规运动样本完成，确认只显示“待语义分析确认”。
4. 刷新并重新打开项目，确认结果无需重复执行；再次启动幂等复用。
5. 分别处理硬切双镜头和高运动样本，核对实测原因与超范围说明。
6. 模拟关键帧阶段失败，确认前序阶段保留；重试从关键帧阶段继续。
7. 在处理中尝试替换参考视频，确认入口禁用且接口拒绝。
8. 完成后替换：失败时保留旧视频和旧结果，成功时清除旧结果并回到未开始。
9. 仅使用键盘完成启动和重试，核对可见焦点、状态宣告及错误提醒。
10. 在 1024px 验证完整流程，在 1023px 和常见手机宽度确认只读、无横向溢出。

## 性能验收

Ticket 03 不以供应商网络耗时为变量。在 Apple Silicon M1、8GB 内存或性能不低于该基线的设备上，使用 10 秒、UHD 4K、30fps、单视频流的固定样本，从手动启动到本地预处理完成目标为 30 秒内。性能记录必须单独列出完整解码、镜头与运动分析、关键帧和联系表耗时。

该性能目标使用固定样本做真实环境验收，不作为共享 CI 的绝对时间断言，避免不同硬件负载造成不稳定测试。

## 成功标准

- Ticket 03 的八项验收条件均由自动测试或真实浏览器步骤覆盖。
- 参考视频上传后不会自动开始；桌面用户可以明确启动和从失败阶段重试。
- FFmpeg 完成完整解码检查、镜头检测、关键帧提取、联系表和运动数据生成。
- 原托管参考视频字节、元数据和音频不会被修改。
- 分析代理不包含完整视频副本，只包含联系表和字段固定的 `analysis-proxy.json`。
- 多镜头和高运动使用固定算法版本与稳定实测原因判为 `out_of_scope`。
- 本地检查通过时只返回 `pending_semantic_confirmation`，不提前宣称处于可复刻范围。
- 主体数量和复杂交互明确留待 Ticket 04，界面不伪装成本地已判断。
- 任一失败都能定位到具体阶段；阶段半成品被清理，安全完成的前序阶段保留。
- 重新打开项目、刷新页面和服务重启后，完成结果能复用，中断结果能重试。
- 新参考视频成功替换后旧结果失效；替换失败时旧视频和旧结果都不丢失。
- 1024px 及以上支持鼠标和键盘操作；窄屏仅只读且不会暴露启动、重试或替换动作。
- Ticket 01/02 的既有测试、对比度检查和前端构建保持通过。
- 实现未引入分析服务、密钥、语义结果、播放器、三栏工作台、Prompt、策略或 ComfyUI 能力。

## 已解决决定

- 参考视频上传后由用户手动启动本地预处理，不自动运行。
- Ticket 03 只本地确认多镜头与运动强度；主体数量和复杂交互由 Ticket 04 确认。
- 本地阶段只产生 `out_of_scope` 或 `pending_semantic_confirmation`，不产生最终 `in_scope`。
- 使用 FFmpeg 单一媒体引擎，不引入 OpenCV、NumPy、PyAV 或本地视觉模型。
- 运动数据是固定采样率下的帧变化强度时间序列，不宣称光流方向或动作语义。
- 固定阈值属于版本化产品常量，用户不可修改。
- 使用应用内全局单工作线程、持久化阶段状态和前端轮询，不引入外部任务系统或推送协议。
- 项目准备页只展示阶段、摘要和判断，不提前实现 Ticket 05 的视觉工作台。
- 重试从第一个未完成阶段继续；服务重启把遗留任务转换为可重试中断状态。
- 运行期间禁止替换参考视频；完成或失败后可替换，且只在新视频提交成功后失效旧结果。
