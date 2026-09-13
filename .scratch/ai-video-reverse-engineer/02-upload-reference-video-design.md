# Ticket 02：上传并校验参考视频设计

Status: approved

## 目标与受众

为已经创建的复刻项目增加一个本地参考视频输入。用户可在宽度至少 1024px 的桌面界面通过文件选择或拖放上传 MP4、MOV，并在分析前获得格式、体积、时长、分辨率、帧率和可读性的明确校验结果。合法视频由本地服务复制到应用管理的数据目录，项目重新打开后无需再次选择。

首要用户是 ComfyUI 创作者，次要用户是已具备本地 ComfyUI 环境的普通创作者。本 Ticket 不要求分析服务或本地 ComfyUI 已连接。

## 范围

- 为单个复刻项目上传或替换一个参考视频。
- 选择前展示 MP4/MOV、2～10 秒、最大 200 MB、最低 480P、最高 UHD 4K 的限制。
- 服务端使用 ffprobe 校验容器、视频帧、时长、显示分辨率和帧率。
- 返回并持久化稳定的参考视频元数据。
- 对每种阻止原因返回稳定错误码和包含实测值的中文说明。
- 替换前在页面内确认；替换失败时保留旧视频。
- 桌面端提供上传和替换，窄于 1024px 的界面仅显示已有信息和桌面操作提示。
- 文件选择、确认、取消和重试均可使用键盘，状态不只依赖颜色表达。

## 非目标

- 不播放或预览参考视频。
- 不生成低分辨率分析代理。
- 不提取关键帧，不做镜头检测或运动计算。
- 不增加分析按钮，不调用分析服务。
- 不接入、检查或调用 ComfyUI。
- 不建立 codec 白名单，不转码、不裁剪、不压缩视频。
- 不扩展到 Ticket 03/04 的任何分析流水线能力。

## 架构与文件边界

本功能采用模块化同步上传：浏览器以单次 `multipart/form-data` 请求发送文件，本地服务完成磁盘缓冲、托管目录分块复制、ffprobe 探测、校验和项目提交后再返回结果。不增加后台任务、轮询、WebSocket 或自制 multipart parser。

### 后端

- `backend/app/main.py`
  - 扩展 `Project` 模型和应用装配。
  - 提供上传路由及轻量 Content-Length 快速拒绝中间件。
  - 继续复用现有项目写锁和 `projects.json` 原子写入。
- `backend/app/reference_video.py`
  - 定义 `ReferenceVideo`、ffprobe 探测结果和校验错误。
  - 封装 ffprobe 调用、旋转处理、帧率解析和硬性限制校验。
- `backend/app/reference_video_storage.py`
  - 将 `UploadFile` 分块复制到数据目录内的临时文件并执行精确字节计数。
  - 生成内部引用 ID、推导托管路径并负责提交、回滚和旧文件清理。
- `backend/tests/test_video_probe.py`
  - 验证 ffprobe 结果转换及全部媒体边界。
- `backend/tests/test_reference_video_upload_api.py`
  - 从 HTTP 边界验证上传、错误、持久化、替换和故障回滚。
- `backend/pyproject.toml`、`backend/requirements.lock`
  - 增加 FastAPI multipart 所需的锁定依赖 `python-multipart==0.0.20`。

### 前端

- `frontend/src/App.tsx`
  - 扩展项目类型和上传 API 调用。
  - 上传成功后同步更新当前项目与首页项目集合。
- `frontend/src/ReferenceVideoPanel.tsx`
  - 独立承载限制说明、文件选择、拖放、替换确认、状态和元数据展示。
  - 监听 1024px 桌面能力边界；窄屏不挂载上传或替换控件。
- `frontend/src/ReferenceVideoPanel.test.tsx`
  - 验证用户可观察的上传、替换、响应式和无障碍行为。
- `frontend/src/App.test.tsx`
  - 验证项目重新打开和上传后应用级状态同步。
- `frontend/src/styles.css`
  - 增加符合“胶片检测台”的平面上传区、状态、错误和响应式样式。
- `README.md`
  - 写明 ffprobe 前置条件、精确限制与本地文件保存位置。

## API 与领域模型

### 上传接口

```http
PUT /api/projects/{project_id}/reference-video
Content-Type: multipart/form-data

file=<binary>
```

请求只接受一个名为 `file` 的文件。成功返回 `200` 和更新后的完整 `Project`；不存在的项目返回 `404`。同一路径同时承担首次上传与替换，替换语义由项目当前是否已有 `referenceVideo` 决定。

### 项目模型

```text
Project
  id: string
  name: string
  createdAt: ISO-8601 string
  updatedAt: ISO-8601 string
  referenceVideo: ReferenceVideo | null

ReferenceVideo
  id: string
  originalName: string
  format: "mp4" | "mov"
  sizeBytes: integer
  durationSeconds: number
  width: integer
  height: integer
  frameRate: number
```

`referenceVideo` 默认为 `null`，因此 Ticket 01 已保存且缺少该字段的项目仍可读取。`width`、`height` 是应用旋转元数据后的显示尺寸。`projects.json` 只保存上述元数据和内部引用 ID，不保存用户来源路径、临时路径或绝对托管路径。托管文件位置由 `project_id + referenceVideo.id + format` 推导。

上传成功必须更新 `updatedAt`，使首页最近项目排序反映真实修改时间。前端展示值可格式化，但不得改变接口中的原始数值。

## multipart 与体积上限

体积上限固定为 `200_000_000` 字节。

- 使用 FastAPI `UploadFile` 和 Starlette 的磁盘缓冲行为，不在内存中一次性读取整个文件。
- 若请求包含 `Content-Length`，路径限定的轻量中间件可在 multipart 解析前快速拒绝明显超限请求。考虑 multipart 边界和文件名等包络，快速拒绝阈值为 `201_000_000` 字节；它只是资源保护优化，不是文件体积真值。
- 若没有 `Content-Length`，或请求体未超过快速拒绝阈值，路由仍必须继续执行硬计数。
- 从 `UploadFile` 以固定大小块复制到应用数据目录内的 `.part` 文件；累计到第 `200_000_001` 字节时立即停止复制、删除 `.part` 并返回 `video_too_large`。
- 只有分块复制得到的实际文件字节数用于业务判定；不信任 Content-Length、MIME 或客户端报告的大小。
- 上传中断、请求取消、磁盘写入失败和任何后续校验失败都清理本次 `.part` 文件。

Content-Length 快速拒绝不能替代分块硬计数，也不对缺少该请求头的客户端作额外假设。

## ffprobe 与校验规则

ffprobe 使用参数数组调用，禁止通过 shell 拼接文件名，并设置有限超时。探测至少读取容器名、首个非封面视频流、视频帧计数、时长、宽高、平均帧率、回退帧率和旋转信息。

校验顺序及规则如下：

1. **文件名格式**：扩展名大小写不敏感，只允许 `.mp4` 和 `.mov`。
2. **文件体积**：实际字节数不得超过 `200_000_000`。
3. **容器格式**：ffprobe 报告必须属于 MP4/MOV 的 QuickTime/ISO Base Media 容器族；只改扩展名不能通过。
4. **可读性**：ffprobe 必须成功退出，存在非 attached-picture 视频流，必需字段有效，并且至少读取到一个有效视频帧。不能只凭文件头可解析认定为可读。
5. **时长**：包含端点，`2.0 <= durationSeconds <= 10.0`。
6. **旋转**：先应用 display matrix 或 rotate 元数据；90°/270°交换宽高，再进行显示尺寸判定。
7. **最低分辨率**：旋转后的显示短边至少 480 像素。
8. **最高分辨率**：UHD 4K 为硬上限；横屏不超过 3840×2160，竖屏不超过 2160×3840。方形按两个方向都必须落入上限处理，即任一边不得超过 2160。
9. **帧率**：优先解析 `avg_frame_rate`，无效时回退 `r_frame_rate`；结果必须是有限正数。分数字符串如 `30000/1001` 转为数值后保存。

不设置 codec 白名单。只要容器符合 MP4/MOV 且本机 ffprobe 能确认存在有效可读视频帧，即可通过编码层校验。

## 原子存储与替换

托管目录结构：

```text
<data_dir>/
  projects.json
  project-files/
    <project-id>/
      reference-videos/
        <reference-video-id>.<mp4|mov>
```

单次上传按以下顺序提交：

1. 在 `<data_dir>` 所在文件系统创建唯一 `.part` 文件，分块复制并执行硬计数。
2. 刷新并 `fsync` 临时文件，在该文件上完成 ffprobe 探测和全部校验。
3. 获取现有项目写锁，重新读取 `projects.json`，确认项目仍存在并取得当前旧引用。
4. 为新视频生成唯一内部 ID，以 `os.replace` 将 `.part` 移到最终托管路径；新路径不得覆盖旧视频路径。
5. 更新项目的 `referenceVideo` 和 `updatedAt`，使用现有临时 JSON、文件 `fsync` 和 `os.replace` 提交 `projects.json`。
6. 若项目元数据提交失败，删除新托管文件并保留旧元数据与旧文件，然后返回存储错误。
7. 只有新文件和新元数据均提交成功后才删除旧托管文件。旧文件删除失败不回滚已经有效的新引用，可留下未引用文件并报告到本地日志。

该顺序保证运行时失败不会让项目指向缺失文件。进程若在文件和 JSON 两次原子替换之间崩溃，最多留下未引用的新文件；若在 JSON 提交后、旧文件删除前崩溃，最多留下未引用的旧文件。项目可恢复记录始终指向完整的新文件或完整的旧文件。

同一进程内的项目写锁覆盖重新读取、文件提交和 JSON 提交，避免并发替换产生丢失更新。用户原文件名只用于展示，绝不参与托管路径拼接。

## UI 状态机

同步接口不提供服务器阶段事件，因此前端不虚构上传百分比或独立的探测进度；请求进行中统一显示“正在上传并校验参考视频…”。

| 状态 | 界面行为 |
| --- | --- |
| `empty` | 展示限制、拖放区和“选择参考视频”按钮。选择或拖入后立即上传。 |
| `uploading` | 显示文字状态和忙碌图标，禁用重复选择与提交。 |
| `ready` | 展示原文件名、格式、体积、时长、显示分辨率和帧率，以及“更换参考视频”。 |
| `error` | 以图标、标题和具体中文原因展示失败；无旧视频时允许重新选择。 |
| `confirmingReplacement` | 同时显示当前视频与待替换文件名，提供“取消”和“替换参考视频”；确认前不发送文件。 |
| `replacementError` | 保留并继续展示旧视频摘要，在其旁显示新文件失败原因和重试入口。 |

首次上传成功后进入 `ready`。已有视频时，文件选择和拖放都先进入 `confirmingReplacement`；取消时清除待选文件且不发请求。替换成功后才用响应中的项目替换前端当前值；请求失败不得先行清空旧值。

## 响应式与无障碍

- 以 `min-width: 1024px` 作为添加和替换能力边界。组件监听媒体查询并只在桌面能力成立时挂载文件选择、拖放和替换控件。
- 窄于 1024px 时，有参考视频则只读展示摘要；无参考视频则展示限制和“请在宽度至少 1024px 的桌面设备添加参考视频”。
- 文件选择由原生 `button` 触发文件输入，按钮天然支持 Enter/Space。拖放区不是唯一操作入口，也不伪装成没有完整键盘行为的按钮。
- 文件输入使用 `accept` 提示 MP4/MOV，但服务端始终是校验权威。
- 请求状态使用 `role="status"` 和 `aria-live="polite"`；错误使用 `role="alert"`。状态同时包含文字与图标，不只依赖边框或颜色。
- 取消替换或替换失败后，焦点回到“更换参考视频”；首次上传失败后回到“选择参考视频”；上传成功后将焦点移到可聚焦的视频摘要标题并由 live region 宣告成功。
- 所有操作目标至少 44px，可见焦点使用现有蓝灰轮廓；错误使用锈红但保留完整文字。
- 样式保持暖灰台面、细分隔线、方正容器和稀疏锈红信号，不增加阴影、大圆角或装饰动画。
- 不添加必须关闭的动效；减少动态效果偏好下不产生额外运动。

## 错误契约

本接口错误体统一为：

```json
{
  "detail": {
    "code": "video_too_short",
    "message": "参考视频时长为 1.42 秒，至少需要 2 秒。"
  }
}
```

| HTTP | code | message 要点 |
| --- | --- | --- |
| 400 | `invalid_multipart` | 请求缺少单个 `file` 字段或 multipart 无法读取。 |
| 404 | `project_not_found` | 指定复刻项目不存在。 |
| 413 | `video_too_large` | 包含实测字节数和 200,000,000 字节上限；快速拒绝时说明请求体明显超限，不伪造文件实测值。 |
| 415 | `unsupported_video_format` | 说明只接受 MP4/MOV，并指出扩展名或探测容器。 |
| 422 | `video_unreadable` | ffprobe 无法读取、无有效视频流、无有效帧或必需元数据损坏。 |
| 422 | `video_too_short` | 包含实测时长和 2 秒下限。 |
| 422 | `video_too_long` | 包含实测时长和 10 秒上限。 |
| 422 | `video_resolution_too_low` | 包含实测显示尺寸和短边 480 下限。 |
| 422 | `video_resolution_too_high` | 包含实测显示尺寸和 UHD 4K 上限。 |
| 422 | `video_frame_rate_invalid` | 说明无法得到有限正帧率。 |
| 503 | `ffprobe_unavailable` | 本地未安装或无法启动 ffprobe，并给出检查命令。 |
| 503 | `storage_unavailable` | 本地数据目录不可写或项目元数据无法安全提交。 |

前端优先显示 `detail.message`，并兼容 Ticket 01 已存在的字符串型 `detail`。未知响应使用本地、可操作的兜底说明，但不得把具体服务端错误降级为“上传失败”。

## 测试设计

### 后端 TDD

1. 先为旧项目缺少 `referenceVideo`、新项目返回 `null` 和上传成功契约编写失败测试，再扩展模型和接口。
2. 使用可控的 ffprobe 边界替身验证扩展名、容器、帧、时长、旋转尺寸和帧率转换；测试只断言公开结果与错误，不断言 subprocess 私有调用顺序。
3. 通过可注入的小体积上限测试恰好等于上限、分块后多 1 字节、缺少 Content-Length 和伪造 Content-Length，确认硬计数始终生效。
4. 用固定、小型 MP4/MOV 样本运行真实 ffprobe 集成测试；损坏的 `.mp4` 必须返回 `video_unreadable`。
5. 覆盖 2 秒、10 秒、短边 480、3840×2160 和 2160×3840 的包含边界，以及每个边界外的失败值。
6. 上传后使用同一数据目录重建应用，确认 GET 项目仍返回元数据且托管文件存在。
7. 覆盖替换成功、替换校验失败、项目 JSON 写入失败、不同项目隔离、路径穿越文件名和并发替换；每次都核对记录所指文件存在且失败不会删除旧视频。
8. 覆盖 ffprobe 缺失、超时、磁盘写入失败及所有临时文件清理。

### 前端测试

- 选择前可见全部限制和桌面操作入口。
- 文件选择与 drop 均提交正确的 `FormData(file)`。
- 上传中、成功摘要、每类服务端具体错误和重新选择行为可观察。
- 已有视频时先出现页面内确认；取消不请求，失败保留旧摘要，成功才替换。
- 取消和失败后的焦点恢复、live region、alert、44px 操作目标和非颜色单一传意符合约定。
- 从最近项目重新打开时直接显示已保存摘要。
- 上传成功返回首页后，更新时间和当前项目数据已经同步。
- 1023px 及以下不渲染添加/替换控件，1024px 及以上提供完整交互。

### 全量自动验证

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
npm --prefix frontend test
node frontend/scripts/verify-color-contrast.mjs
npm --prefix frontend run build
```

### 真实浏览器验收

在独立临时数据目录启动后端与 Vite，通过真实浏览器完成：

1. 在 1280px 视口创建项目，确认选择前可见全部限制。
2. 用文件选择上传合法 2 秒 480P MP4，核对上传状态和全部元数据。
3. 刷新并重新打开项目，确认无需再次选择。
4. 通过真实 `DataTransfer` drop 上传 MOV，确认拖放路径有效。
5. 对已有视频选择新文件，确认页面内替换提示；取消后旧值不变且焦点恢复。
6. 依次上传低于 480P、短于 2 秒、超过 UHD 4K 和损坏 MP4，核对具体错误且旧视频不丢失。
7. 成功替换后返回首页并重开，确认新视频持久化。
8. 仅用 Tab、Enter 和 Space 完成选择入口、取消及确认替换，检查可见焦点和状态宣告。
9. 在 1024px 验证完整流程；在 1023px 和常见手机宽度确认仅有只读内容与桌面提示、无上传控件和横向溢出。

## 成功标准

- Ticket 02 的八项验收条件全部由自动测试或真实浏览器步骤覆盖。
- MP4/MOV、2～10 秒、200,000,000 字节、480P 至 UHD 4K 和可读帧边界均由服务端强制执行。
- 合法参考视频与项目持久关联，服务重启或页面刷新后无需重选。
- 替换成功后只有新引用生效；任何上传、探测或元数据提交失败都保留旧引用和旧文件。
- 每个阻止原因有稳定 code 和包含实测信息的中文 message。
- 1024px 及以上支持鼠标、拖放和键盘；窄屏只读且不会暴露添加或替换动作。
- Ticket 01 的既有行为与测试保持通过。
- 实现未引入播放、代理生成、关键帧、镜头检测、运动计算、分析服务或 ComfyUI 能力。

## 已解决决定

- 采用同步 PUT 上传，不引入后台任务或自制 multipart parser。
- 使用 UploadFile 磁盘缓冲、可选 Content-Length 快速拒绝和复制阶段精确硬计数。
- 200 MB 固定解释为 200,000,000 字节。
- 保存原始字节的应用托管副本；项目只记录元数据和内部 ID。
- 扩展名与 ffprobe 容器必须同时属于 MP4/MOV，不限制 codec，但必须存在有效可读帧。
- 分辨率基于旋转后的显示尺寸；短边至少 480，最高为 UHD 3840×2160，超出即阻止。
- `referenceVideo` 可空，兼容 Ticket 01 数据。
- 替换使用页面内确认，新文件和元数据提交成功后才删除旧文件。
- 错误采用稳定 code 和包含实测值的中文 message。
- 只有宽度至少 1024px 的桌面界面提供添加和替换，移动端只读。
