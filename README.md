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

每个复刻项目在任一时刻只保存一份参考素材，可以是图片或视频。图片仅接受 JPG/JPEG、PNG、WebP，文件最大 `30,000,000` 字节；按旋转后的显示尺寸计算，宽和高均为 256～5760 像素，宽高比为 2:5～5:2；不接受动画 WebP。服务端按真实内容校验格式，而非仅信任扩展名或浏览器 MIME。图片透明区域会在本地预处理时使用白色背景合成，并记录实际处理结果。

参考视频仅接受 MP4/MOV，时长为 2～10 秒，文件最大 `200,000,000` 字节；按旋转后的显示尺寸计算，短边至少 480 像素，最高为 UHD 3840×2160。

原始参考素材字节会复制到 `<data_dir>/project-files/<project-id>/reference-media/`。从旧版项目迁移而来的参考视频仍可从旧 `reference-videos/` 目录读取，以保证已有项目可继续打开。`projects.json` 仅保存参考素材元数据，不记录来源路径。

通过 `PUT /api/projects/{project_id}/reference-media` 上传一个 `file`，可首次添加或替换图片、视频。服务端在一次项目写入中提交校验后的素材；替换失败时保留旧素材与现有状态。成功替换会清空当前 `localPreprocessing`、清除 `activeDepthCaptureId`，并清理旧参考素材及旧本地预处理产物；历史 `depthCaptures` 记录仍保留。只要本地预处理、深度捕捉或语义分析处于排队或运行中，即禁止替换。通过 `GET /api/projects/{project_id}/reference-media/content` 读取当前参考素材内容；旧 `GET /api/projects/{project_id}/reference-video/content` 仅为旧视频客户端兼容保留。完整参考素材不会发送到外部服务。

## 本地预处理与分析代理

参考素材上传成功后不会自动处理。在宽度至少 1024px 的项目页点击“开始本地预处理”：视频依次完成解码、镜头检测、关键帧提取、运动分析和初步可复刻性判断；图片则依次完成图像解码、方向与色彩标准化、分析代理生成和初步可复刻性判断。图片处理会写入去除来源元数据的 `normalized.png` 与 `analysis-proxy.jpg`，并在存在实际透明区域时合成白色背景。图片代理的尺寸和白底处理结果来自本地实际处理产物；图片深度素材和可执行工作流生成仍是后续独立能力。

当前 FFmpeg 构建必须包含 `scdet`、`scale`、`metadata`、`drawtext` 和 `tile` 滤镜。可以运行 `ffmpeg -filters` 检查。

macOS 使用 Homebrew 时，可安装 `brew install ffmpeg-full`。它是独立安装的完整构建；在启动后端或运行后端测试的同一终端中，先执行 `export PATH="$(brew --prefix ffmpeg-full)/bin:$PATH"`，再检查 `ffmpeg -version`、`ffprobe -version` 和上述滤镜。

版本化视频产物保存在 `<data_dir>/project-files/<project-id>/local-preprocessing/<preprocessing-id>/`。视频语义分析只发送 `contact-sheet.jpg` 和 `analysis-proxy.json`；图片只发送去元数据的 `analysis-proxy.jpg` 与基础尺寸信息。完整参考素材、独立关键帧和本地路径不会进入分析代理。

本地阶段只判断多镜头和运动强度。显示“待语义分析确认”不代表已经处于可复刻范围；主要主体数量和复杂交互由语义分析确认。

## 语义分析

04c 已支持图片和视频的统一、可恢复语义分析。当前设置目录包含阿里云百炼 `qwen3.7-flash`、本地 OpenAI 兼容服务（回环地址、自填非空模型）、OpenAI `gpt-5.6-luna`、火山方舟豆包 `doubao-seed-2-0-lite-260428`、Google Gemini `gemini-2.5-flash`、xAI Grok `grok-4.6`、Anthropic Claude `claude-sonnet-5` 与 ChatAnywhere `gpt-5.6-sol`。六家云端与百炼必须配置 API Key；本地服务的 Key 可选，Base URL 仅允许回环主机。

目录中的“未验证/可用/验证失败”是当前本机配置的连接测试结果，不是供应商可用性的承诺。连接测试只发送内置的 16×16 PNG 和完整结构化分析提示；它不会发送参考素材。豆包图片与结构化输出组合基于方舟 Responses 兼容协议推断，在真实密钥的本地连接测试成功前保持“未验证”。模拟契约测试不能替代真实冒烟。

用户在宽度至少 1024px 的项目页先查看接收方、将发送的代理内容和明确不发送内容，再确认提交。凭据仅由本地服务的系统安全存储管理，前端、项目数据和日志不保存或回显密钥。任务会保存检查点以便重新打开后恢复状态；失败保留本地预处理结果，且不会自动切换供应商或模型。窄屏仅可查看配置、任务和结果。

可用本地服务的同一路径执行脱敏连接冒烟：`PYTHONPATH=backend .venv/bin/python backend/scripts/smoke_analysis_provider.py --provider openai`。它只调用本机 `/api/analysis-providers/<id>/test-connection`，不会直接读取安全存储、打印凭据、请求体或供应商原始响应；未配置或失败时非零退出。

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
