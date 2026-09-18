# GitHub 视频项目接入筛选

核查日期：2026-09-18。本文为选型建议，不表示新增功能已实现；没有安装模型或改动应用代码。优先级是根据当前项目现状作出的工程判断。

## 已有基础

现有 FFmpeg 预处理、PySceneDetect、Video Depth Anything、MediaPipe、Real-ESRGAN、RIFE/whisper.cpp/SAM2 worker 接口、外部 ComfyUI Wan Animate、镜头准备与自动处理流程。重复增加同类包装器的收益较低。

## 优先接入

| 项目与一手来源 | 可以加入的功能 | 接入方式和限制 |
|---|---|---|
| [OpenTimelineIO](https://github.com/AcademySoftwareFoundation/OpenTimelineIO) | 将当前镜头边界、素材引用、备注导出为可交换剪辑时间线 | Apache-2.0；Python bindings。先支持 .otio，EDL/FCP XML等走独立adapter并对目标剪辑软件验收；OTIO不是媒体容器，不承诺所有软件无损往返。 |
| [LosslessCut](https://github.com/mifi/lossless-cut) | 镜头范围导出、关键帧定位、音轨选择、片段标签与撤销操作 | GPL-2.0；借鉴交互和基于FFmpeg的工作流，应用自己的实现，不直接复制其代码。无损截取受关键帧约束，精确截取需重编码或混合策略。 |
| [Netflix VMAF](https://github.com/Netflix/vmaf) + [FFmpeg filters](https://ffmpeg.org/ffmpeg-filters.html) | 输出质量报告、逐帧对比曲线、黑帧/冻结片段/静音位置标记 | VMAF LICENSE是BSD-2-Clause-Patent；分辨率和时间先对齐，有参考时比较。低清输入不能充当高分辨率真值，VMAF不代表超分恢复了真实细节。黑帧/静音只标疑点，不能直接当错误。 |
| [vid.stab](https://github.com/georgmartius/vid.stab) | 手持抖动平滑、原片/稳定片对比、防抖裁切程度设置 | 通过FFmpeg已有滤镜接入；原片保留，防抖只输出派生视频，不改变作为动作分析依据的参考素材。当前主分支[已改LGPL-2.1-or-later](https://github.com/georgmartius/vid.stab/blob/master/RELICENSE.md)，截至v1.1.2旧版本是GPL，实际分发按锁定版本核查。 |
| [TransNet V2](https://github.com/soCzech/TransNetV2) | 在现有快速分镜之外增加神经网络检测模式，评估复杂转场、闪光等难例 | MIT；官方有PyTorch推理。固定样例比较后再推荐，不能假定对所有素材优于PySceneDetect。 |
| [Real-CUGAN ncnn Vulkan](https://github.com/nihui/realcugan-ncnn-vulkan) | 为二次元线稿增加超分引擎选项及降噪档位 | MIT实现；官方提供Mac/Apple-Silicon支持，无需CUDA/PyTorch。复用现有视频解码编码和1080P/2K目标。它是逐帧图像超分，不自动保证时间连续性；毛绒3D角色不等于二次元线稿，仍需对比。 |
| [WhisperX](https://github.com/m-bain/whisperX) | 词级时间戳、点击字幕跳转、说话人分段 | BSD-2-Clause代码；官方Mac路径为CPU示例，不能承诺MPS加速。对齐/说话人模型单独管理，pyannote需获取模型并接受对应条款；基础whisper.cpp保留。 |

## 重型或实验选项

- [SeedVR2](https://github.com/ByteDance-Seed/SeedVR)：高质量视频修复候选，官方Apache-2.0，当前官方安装包含flash-attn/apex和CUDA路径。README的硬件示例为H100 80GB并行，不应把它当作本机轻量超分，也不应把官方配置说成所有社区实现的绝对最低要求。可按外部GPU worker设计“高质量修复”模式；其生成式恢复可能过锐化或增加不真实细节，先短片预览。来源同README Limitations/Inference。
- [ProPainter](https://github.com/sczhou/ProPainter)：可以配合SAM2做指定区域视频修复/移除物体。但代码和模型为NTU S-Lab非商业许可，并非可任意商用的宽松开源，当前建议只列研究候选。
- 动作、点跟踪和相机几何研究见 `oss-motion-research.md`，其代码与模型许可分别核查。

## 借鉴架构，不重复替换

[Video2X](https://github.com/k4yt3x/video2x) 有多超分/插帧引擎框架，可研究解码、推理、编码组织方式。我们已具备Real-ESRGAN和RIFE适配及持久化队列，整体替换收益有限；其AGPL-3.0代码不能视为随意拷贝的工具片段。

## 维护信号

2026-09-18 GitHub API核查：上述OTIO、VMAF、vid.stab、LosslessCut、TransNetV2、SeedVR、Real-CUGAN、WhisperX、Video2X均未归档。最近推送分别为2026-09-18、2026-09-17、2026-08-14、2026-09-14、2023-12-04、2026-01-27、2023-03-12、2026-08-30、2026-03-07。推送时间只是维护信号，不等同发布频率或质量；TransNetV2和Real-CUGAN以成熟旧版本候选对待，先验安装兼容性。来源各仓库GitHub API `/repos/<owner>/<name>`。

## 本机检查

只读执行 `ffmpeg -hide_banner -filters`，确认已有 `libvmaf`、`vidstabdetect`、`vidstabtransform`、`blackdetect`、`freezedetect`、`silencedetect`。这表示构建包含滤镜，不等于新功能和真实处理效果已验收。

## 推荐顺序

先做时间线/镜头片段导出与质量报告，再防抖、增强分镜和二次元超分；动作细化和字幕精对齐作为独立worker增量接入；重型修复保留外部执行器。所有新工具应复用已有快照、队列、取消/重试和产物存储，不新增第二套任务系统。
