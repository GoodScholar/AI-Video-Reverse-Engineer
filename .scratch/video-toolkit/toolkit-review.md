# 视频工具独立审查

## 结论

需求完整性：主要功能入口与真实执行适配均已实现。初审发现两项 P2；复查时 worker 的 FastAPI/Pydantic 隐式入口导入已消除，完整安装说明/真实轻量验证由主任务补齐；服务退出修复正在进行。质量结论：**待服务退出修复与验证后通过**。

范围：本次 toolkit diff、实际挂载、复用的计算队列/超分/素材存储。未改代码，未重跑已有测试。既有 13 项 API/core、3 项真实 FFmpeg + 替代 RIFE 推理测试、4 项前端测试和构建结果由主任务提供；不把替代推理器当成真实 RIFE 模型验收。

## 问题

### P2：独立 worker 的真实导入依赖未纳入预检/安装说明（入口导入已修复）

位置：`backend/toolkit_worker/run_tool.py:12`、`backend/app/video_toolkit.py:140`。

worker 顶层导入 `app.video_toolkit`，继而导入 `reference_media_storage` 的 FastAPI、`reference_video` 的 Pydantic v2，以及 `video_upscale` 的 Pillow。环境检测仅检查 `find_spec("scenedetect")`，界面要求仅为 PySceneDetect/OpenCV。因此按界面在干净 venv 安装这些依赖后，环境会显示就绪，但执行在入口 try 之前因缺少 FastAPI/Pydantic/Pillow 失败；SAM 2 独立环境也有同类隐式依赖。应消除仅主应用使用的导入传播，或提供完整 worker requirements/安装命令，并让预检覆盖真实导入链。正在进行的轻量 worker 实测应采用该独立环境，避免由主应用环境掩盖问题。

后续复查已确认 `reference_media_storage` / `reference_video` 导入移入主进程路径函数；worker 入口不再触发这些依赖。Pillow 仍是合理的公共媒体依赖，需在独立安装说明列出。主任务正补 requirements 与独立 scene 实测，完成后此项可关闭。

### P2：服务退出未取消正在运行的独立 worker

位置：`backend/app/video_toolkit.py:164`，关联 `backend/app/main.py:1179` 和 `backend/app/depth_capture_jobs.py:38`。

worker 使用 `start_new_session=True`，但取消仅由用户修改持久化状态触发；服务 shutdown 直接 `ThreadPoolExecutor.shutdown(wait=True)`，没有先发送 toolkit 停止信号。模型任务运行期间正常关闭服务会等待当前任务（超时上限两小时）；强制结束主进程则可能保留独立 worker。下次启动只把任务改成失败，没有处理仍运行的旧进程，用户重试可能与旧推理同时占用资源。应在队列等待之前终止/取消活动 toolkit，并为异常父进程退出提供 worker 存活约束或安全恢复机制。

## 功能覆盖

| 验收项 | 审查结果 |
| --- | --- |
| SAM 2 点选跟踪 | 首帧正负点、独立进程、遮罩/叠加/跟踪 JSON 已接入；真实模型未验收 |
| PySceneDetect 与切点修改 | AdaptiveDetector、增删切点、原子文件与 revision 冲突检测已实现；独立依赖问题待解决 |
| whisper.cpp 字幕 | 真实 WAV 提取、CLI、SRT/JSON 导出已实现；真实模型未验收 |
| RIFE / 超分 | 真实 CLI 调用、帧率/时长/音轨检查与超分复用已实现；真实 RIFE 未验收 |
| 素材目录 | 当前参考视频、完成的生成/角色动画/超分/工具视频；服务端 ID 与路径归属检查 |
| 批量、取消、重试 | 输入快照、最多 12 项、取消、从快照新建重试、失败产物不发布；服务退出缺口见上 |
| 同步对比 | 主播放器同步播放/暂停/定位/倍速，静音结果与 100%/200% 检查已实现 |
| 生命周期及安全 | 原子状态、项目迟到响应隔离、重启标失败、符号链接拒绝已实现；未发现直接客户端任意路径读写入口 |

本报告不覆盖角色动画模块的独立 verdict；也不将缺少模型本身视为实现失败，最终交付应明确列出未经真实模型验证的项目。
