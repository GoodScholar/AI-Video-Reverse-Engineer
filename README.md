# AI Video Reverse Engineer

本地 Web MVP：React + TypeScript 前端与 Python + FastAPI 本地服务。

## 安装

在仓库根目录安装本地服务依赖：

```sh
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.lock
```

`backend/requirements.lock` 包含后端的 multipart 上传与 Pillow 图片探测依赖；请始终通过上述锁定文件安装。

安装前端依赖：

```sh
cd frontend
npm ci
```

## 最小运行方式

在第一个终端启动本地服务：

```sh
cd backend
../.venv/bin/uvicorn app.main:app --app-dir . --reload --port 8000
```

在第二个终端启动前端：

```sh
cd frontend
npm run dev
```

前端开发与预览服务器都会将 `/api` 请求转发到 `127.0.0.1:8000`。

## 参考素材前置条件与数据边界

启动服务前请先运行 `ffprobe -version`。若命令不可用，请安装 FFmpeg 后再启动服务。

每个复刻项目在任一时刻只保存一份参考素材，可以是图片或视频。图片仅接受 JPG/JPEG、PNG、WebP，文件最大 `30,000,000` 字节；按旋转后的显示尺寸计算，宽和高均为 256～5760 像素，宽高比为 2:5～5:2；不接受动画 WebP。服务端按真实内容校验格式，而非仅信任扩展名或浏览器 MIME。图片透明区域会被记录；白底合成属于后续图片预处理的本地策略，当前版本不会启动图片预处理。

参考视频仅接受 MP4/MOV，时长为 2～10 秒，文件最大 `200,000,000` 字节；按旋转后的显示尺寸计算，短边至少 480 像素，最高为 UHD 3840×2160。

原始参考素材字节会复制到 `<data_dir>/project-files/<project-id>/reference-media/`。从旧版项目迁移而来的参考视频仍可从旧 `reference-videos/` 目录读取，以保证已有项目可继续打开。`projects.json` 仅保存参考素材元数据，不记录来源路径。

通过 `PUT /api/projects/{project_id}/reference-media` 上传一个 `file`，可首次添加或替换图片、视频。服务端在一次项目写入中提交校验后的素材；替换失败时保留旧素材与现有状态。成功替换会清空当前 `localPreprocessing`、清除 `activeDepthCaptureId`，并清理旧参考素材及旧本地预处理产物；历史 `depthCaptures` 记录仍保留。只要本地预处理或深度捕捉处于排队或运行中，即禁止替换。通过 `GET /api/projects/{project_id}/reference-media/content` 读取当前参考素材内容；旧 `GET /api/projects/{project_id}/reference-video/content` 仅为旧视频客户端兼容保留。完整参考素材当前不会发送到外部服务；本 Ticket 不执行外部语义分析，也不为图片生成视频预处理、深度素材或 ComfyUI Workflow。

## 视频本地预处理与分析代理

参考视频上传成功后不会自动处理。在宽度至少 1024px 的项目页点击“开始本地预处理”，本地服务会依次完成解码、镜头检测、关键帧提取、运动分析和初步可复刻性判断。参考图片当前不启动这条视频专属流程。

当前 FFmpeg 构建必须包含 `scdet`、`scale`、`metadata`、`drawtext` 和 `tile` 滤镜。可以运行 `ffmpeg -filters` 检查。

macOS 使用 Homebrew 时，可安装 `brew install ffmpeg-full`。它是独立安装的完整构建；在启动后端或运行后端测试的同一终端中，先执行 `export PATH="$(brew --prefix ffmpeg-full)/bin:$PATH"`，再检查 `ffmpeg -version`、`ffprobe -version` 和上述滤镜。

版本化视频产物保存在 `<data_dir>/project-files/<project-id>/local-preprocessing/<preprocessing-id>/`。可发送给后续语义分析的分析代理只有 `contact-sheet.jpg` 和 `analysis-proxy.json`；完整参考视频、独立关键帧和本地路径不会进入分析代理。

本地阶段只判断多镜头和运动强度。显示“待语义分析确认”不代表已经处于可复刻范围；主要主体数量和复杂交互需要后续语义分析确认。

## 生产预览

先在 `frontend/` 中构建，再启动预览服务器：

```sh
cd frontend
npm run build
npm run preview
```

## 验证

以下命令均从仓库根目录运行：

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
npm --prefix frontend test
node frontend/scripts/verify-color-contrast.mjs
npm --prefix frontend run build
```
