# 本地视频工具

工具面板复用项目的单个计算队列，支持批量（最多 12 份素材）、取消、失败重试、输入快照和产物下载。不会自动安装模型或转发云端。输入快照保留在 `backend/data/project-files/<id>/toolkit/<run-id>/input.mp4`，用于重试和同步对比，会占用磁盘空间。

## 分镜检测（已用真实工具验证）

在仓库根目录执行：

```sh
python3.11 -m venv backend/toolkit_worker/.venv
backend/toolkit_worker/.venv/bin/python -m pip install -r backend/toolkit_worker/requirements.lock
```

默认读取此虚拟环境，也可设置 `VIDEO_TOOLKIT_PYTHON`。使用 PySceneDetect 的 AdaptiveDetector（默认阈值 3、最短镜头 0.5 秒）。切点编辑保存到本次结果 `scenes.json`，结果含时间、时长、检测器及修订号。在「逐镜头前置准备」刷新后可显式应用当前参考视频的结果，也可恢复原检测边界；原预处理文件保持不变。应用使用来源和修订校验，范围未变的草稿保留，变化的镜头重新准备；时间线变化会隔离旧人物控制结果。

## 自动处理流程

选择一份素材及至少两个步骤，按分镜、字幕、主体跟踪、补帧、超分的顺序执行所选步骤。分析步骤始终读取该流程的输入快照，补帧和超分串接最近的视频产物。提交前检查所选工具依赖，主体跟踪仍需首帧前景选点。

状态与输入保存在 `toolkit/pipelines/<id>/`。每步单独保存产物，成功步骤不会因后续失败而重做；取消阻止下游，服务重启后需明确续跑。正在停止的步骤退出后才可重试，重试清理未完成的中间文件。单工具与流程共同占用每项目最多 12 个活动任务的额度。产物下载仅开放已完成步骤；已完成流程的视频可再次进入素材列表。

真实端到端验证已覆盖 PySceneDetect → Real-ESRGAN 1080P 的两步流程。其余模型仍按下面各节所述验收边界处理，不以调度测试代替模型推理验证。

## 字幕：whisper.cpp

安装 [whisper.cpp](https://github.com/ggml-org/whisper.cpp) 并准备多语言模型，然后在启动后端前设置：

```sh
export WHISPER_BINARY=/absolute/path/to/whisper-cli
export WHISPER_MODEL=/absolute/path/to/ggml-small.bin
```

先提取单声道 16 kHz 音频，执行 `-osrt -oj`，提供 SRT 和带时间信息的 JSON。没有音轨时明确失败。未做本机真实模型识别验收。

## 主体跟踪：SAM 2

使用单独 Python >=3.10 环境，按 [官方安装说明](https://github.com/facebookresearch/sam2) 安装 SAM 2、匹配的 PyTorch、NumPy、Pillow，准备 checkpoint。不要在 Web 服务环境中导入 Torch。

```sh
export SAM2_PYTHON=/absolute/path/to/sam2-venv/bin/python
export SAM2_CHECKPOINT=/absolute/path/to/sam2.1_hiera_tiny.pt
export SAM2_CONFIG=configs/sam2.1/sam2.1_hiera_t.yaml
```

界面选择单份视频并在首帧添加前景/背景点。输出 8 FPS、最长边 640、最长 30 秒的 `mask.mp4`、`overlay.mp4` 和 `tracking.json`。跟踪数据含逐帧包围框、面积和丢失帧数，不是三维骨骼。设备按 CUDA、MPS、CPU 顺序选择；模型兼容性和实际效果需在安装后验证。

## 补帧：RIFE ncnn Vulkan

从 [官方仓库](https://github.com/nihui/rife-ncnn-vulkan) 获取匹配平台程序和支持任意时间步的 v4 模型：

```sh
export RIFE_BINARY=/absolute/path/to/rife-ncnn-vulkan
export RIFE_MODELS=/absolute/path/to/rife-v4.6
```

支持输出 30/60 FPS，要求目标帧率高于输入，最长 60 秒、最长边 3840；预估帧缓存不得超过 3 GB。逐相邻帧按时间步推理；明显画面跳变使用原帧，启发式切镜保护不保证识别所有转场。保持输入时长及第一音轨，输出 SDR H.264。真实 FFmpeg 解码、编码、音轨合并已测试；RIFE 推理在测试中被替代程序模拟，未做真实 GPU 补帧验收。

## 超分、素材和执行边界

复用现有 Real-ESRGAN 1080P/2K 引擎及配置；素材库包含当前参考视频和已保存的生成、超分、工具输出。仅已完成且安全归属当前项目的文件可被选择。原先超分面板继续可用。

FFmpeg/FFprobe 是公共依赖。单素材最多 1 GB；共享媒体校验最长约五分钟，SAM/RIFE 另有更短上限。外部推理两小时超时；取消或正常关闭服务会终止工作进程组。强制杀死服务不等于正常关闭，重启会把未结束记录标成中断，允许重试。

状态“依赖文件已就绪”仅指路径/轻量模块检查通过，不代表模型已加载或 GPU 推理已验证。

## 开源来源

PySceneDetect：BSD-3-Clause；whisper.cpp：MIT；SAM 2 代码与模型：Apache-2.0（可选第三方组件另附许可）；RIFE ncnn Vulkan：MIT。此模块通过公开 CLI/Python API 调用，不复制第三方模型实现或分发其权重。
