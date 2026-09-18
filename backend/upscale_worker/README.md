# 本地视频超分引擎

使用官方 Real-ESRGAN ncnn Vulkan 可执行程序和 `realesrgan-x4plus` 模型。无需 Fal、云端费用、语义分析或 ComfyUI。执行器在 `backend/app/video_upscale.py`，后台共用本地串行计算队列。

## 安装

从 [Real-ESRGAN 官方 Releases](https://github.com/xinntao/Real-ESRGAN/releases) 下载适合操作系统的 ncnn Vulkan 包，解压为：

```text
backend/upscale_worker/vendor/realesrgan/
  realesrgan-ncnn-vulkan
  models/
    realesrgan-x4plus.bin
    realesrgan-x4plus.param
```

macOS/Linux 为二进制添加执行权限。也可在启动后端前设置 `REALESRGAN_BINARY`（可执行文件的绝对路径）和 `REALESRGAN_MODELS`（模型目录绝对路径）。Windows 应指向 `.exe` 文件。需要 FFmpeg/ffprobe 在 PATH 中或使用项目既有的 FFMPEG_PATH、FFPROBE_PATH 配置。

本机已使用官方 v0.2.5.0 的 `realesrgan-ncnn-vulkan-20220424-macos.zip`（包含 arm64/x86_64）完成真实 2 秒片段验证。下载包 SHA-256：`e0ad05580abfeb25f8d8fb55aaf7bedf552c375b5b4d9bd3c8d59764d2cc333a`。该摘要记录本次下载，不代表其他版本的摘要。二进制与权重均被 Git 忽略，不随源码分发；应用不会自动下载它们。

## 行为与限制

- 上传参考视频后，项目页「视频超分」选择 1080P 或 2K（1440P）。生成成片可上传到新项目作为参考视频进行增强。
- 模型原生4×推理后用 Lanczos 调整为目标尺寸。1080P短边1080、2K短边1440，保持横竖方向和比例，尺寸四舍五入至偶数；16:9分别1920×1080、2560×1440。来源已达目标则禁止再次超分到该档位。低清视频超过模型4倍的部分仅额外缩放，不代表新增细节。
- 仅SDR；HDR明确拒绝，避免无意丢失色彩。最高输出长边7680、短边4320。处理视频旋转，按来源平均帧率规范化为CFR，保留完整时间线（允许一帧误差），尾部音频长于画面时保持末帧补足；首条音轨转为AAC。不保留字幕、其他音轨或来源元数据。不增加帧率。
- 推理使用128像素tile、每批最多8帧，大尺寸时减小批次。临时帧有界落盘，最终视频H.264编码。逐帧超分可能出现纹理闪烁，应目视检查；不自动补帧。
- 环境检查确认程序、模型文件和FFmpeg存在；GPU是否可推理在任务运行时验证。失败会保留任务记录，可重新开始。服务重启将未结束任务标记为失败并清理临时文件。
- 每项目同时一个超分任务，输入在提交时复制快照。替换参考视频不会影响已排队任务；当前页面仅显示当前素材任务。
- 状态与产物保存在 `<data_dir>/project-files/<project-id>/upscale/<run-id>/`。`GET /api/projects/{id}/upscale`读取环境与历史，`POST`传入 `{sourceId, outputResolution: "1080p" | "2k"}`创建任务。视频`GET /api/projects/{id}/upscale/{run-id}/video`支持Range，追加`?download=true`下载。

## 验证

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_video_upscale.py
npm --prefix frontend test -- --run src/videoUpscaleApi.test.ts src/VideoUpscalePanel.test.tsx
```

自动测试以轻量假引擎替代GPU推理，真实执行FFmpeg解码、编码、音轨合并和媒体校验；不能替代真实模型画质验证。

历史2×/4×任务保留倍率语义和产物，兼容旧API的scale参数；新页面只提交outputResolution，不能同时指定这两个字段。
