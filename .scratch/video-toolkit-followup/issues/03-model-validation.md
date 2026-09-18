# 03: 实际模型验收条件核查
Type: task
Status: claimed

## 环境核查（2026-09-18）

通过 `ToolkitConfig.local().environment()` 检查默认环境：分镜与 Real-ESRGAN 可用；whisper-cli 已位于 /opt/homebrew/bin/whisper-cli，但未配置多语言模型；SAM 2 和 RIFE 未满足配置。仓库与常见本地缓存未发现相应权重。

- [ ] 对新增流程运行已有真实工具的端到端冒烟。
- [ ] whisper.cpp / SAM 2 / RIFE 实际模型验收（缺少已配置权重/程序）。
- [ ] ComfyUI 实际角色动画验收（未提供执行环境）。

本轮不下载安装大模型，不把缺环境写成通过。原 300 秒深度压力测试仍以既有独立任务追踪。

## 已有真实工具冒烟

2026-09-18 使用 FFmpeg 生成 64×48、4 FPS、1 秒测试视频，不读取或修改用户项目。直接通过生产 `execute_tool` 执行：

- PySceneDetect 分镜：1.076 秒，真实 `scenes.json` 产物。
- Real-ESRGAN 1080P：1.520 秒，FFprobe 确认输出 1440×1080、4 帧。
- 临时复现脚本 `/tmp/aivre-followup-validation/real_tools.py`；完整元数据 `/tmp/aivre-followup-validation/real-tools.json`。

以上只证明已有真实工具在本机可执行；新增流程的整体 API 冒烟另行记录。

## 新增自动流程真实 API 验收

2026-09-18 使用独立临时数据目录和真实共享队列，通过 HTTP POST `/toolkit/pipelines` 提交 `scenes → upscale(1080p)`。流程 `29c8b88a-dca4-4ea3-ba38-441b24b16320` 两步骤 completed；PySceneDetect 与 Real-ESRGAN 均为真实调用。输入为 2 秒、64×48、8 FPS 的红蓝切换样片，输出 FFprobe 确认为 1440×1080、16 帧、2 秒。

证据：`/tmp/aivre-followup-validation/pipeline-api.json`、`pipeline-probe.json`，复现入口 `pipeline_api.py`。测试数据与用户项目完全隔离。

## 分镜联动真实 API 验收

通过生产分镜工具生成结果，编辑切点为 0.5、1.0 秒，然后应用到镜头准备：2 个镜头变为 3 个镜头；原 1.0–2.0 秒镜头的备注保留，拆分的前两镜草稿为空；复用旧 revision 返回 409；准备包下载成功；恢复后回到 0–1、1–2 秒。证据 `/tmp/aivre-followup-validation/scene-api.json`、`scene-package.zip`；复现脚本 `scene_api.py`。

QA 使用规范路径 `/private/tmp/aivre-followup-validation/app-data`。旧接口对 macOS `/tmp` 符号链接别名存在路径表示不一致；本轮未扩大修改该既有路径规则。

## 浏览器与 2K 续跑

浏览器在独立项目提交分镜 → 2K 超分，开发服务重载导致超分步骤取消，已完成分镜保留。重启后点击“从失败步骤继续”，流程 `d70fb49b-3da6-4ee1-808a-fefb3f9d8155` 完成，输出 1920×1440、16 帧、2 秒。证据 `/tmp/aivre-followup-validation/pipeline-resume-2k.json`。

浏览器完成单工具分镜应用/恢复；390px 宽度的 document.scrollWidth=390，无横向溢出。查看了桌面与移动视口截图（`desktop-toolkit-viewport.png`、`mobile-toolkit-viewport.png`）；初次离屏元素截图为空，未当作有效视觉证据。

最终浏览器验证：从候选列表选择自动流程 `d70fb49b` 的分镜并应用成功，来源绑定已持久化；证据 `pipeline-scene-applied.json`、`scene-applied.png`。新增自动流程和切点操作区域的最终 axe 审计均为 0 violations、0 incomplete；既有人物控制/视频区域不计入本次新增区域的审计结论。
