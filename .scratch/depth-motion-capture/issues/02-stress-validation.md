# 02: 长视频与复杂素材验证
Type: task
Status: claimed

承接用户“继续完善”，补齐此前明确未验证的边界，不安装 ComfyUI、不启动后端服务、不改动用户项目数据。

- [ ] 真实模型运行 300 秒 720P 压力素材，检查帧数、时长、质量与资源记录。
- [x] 使用仓库自带街头行人与过山车样例检查多人、遮挡、快速运动输出，目视抽样并如实记录局限。
- [x] 增加真实 VFR 时间点和显示旋转的自动回归检查。
- [x] 验证本机 CPU 路径；明确 CUDA 无硬件实测条件。
- [x] 记录可复现命令、输入来源、实测结果与必要修复，不重复宣称完整 LibTV 生成能力。

长片由官方仓库 13 秒 Tokyo-Walk_rgb.mp4 循环至 300 秒，用于持续运行与完整性压力测试，不等同于 300 秒独立复杂场景覆盖。原样例与源码均已有本地副本，不修改 vendor。

临时验证目录：`/var/folders/jk/nlrws6j16tv20qd4p5ctf6700000gn/T/depth-hardening-lljal4_k`。

## 本轮修复与验证（2026-09-16）

- 300 秒 720P 任务被中断，无完成产物，不算通过。中断后确认无残留推理进程。此前日志峰值进程内存足迹约 12.3 GB，不能据此认定内存泄漏。
- MPS 分配器限制为 Metal 推荐工作集的 50%，各分段推理后同步并释放空闲缓存；CPU/CUDA 路径不变。此限制不等于进程总物理内存上限。
- 内存限额与异常清理测试先失败（2 项），补丁后通过。
- 相关自动测试：`PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_depth_capture.py backend/tests/test_depth_capture_api.py backend/tests/test_depth_capture_runner.py backend/tests/test_depth_media_api.py backend/tests/test_depth_input_timeline.py -q`，147 passed、1 skipped。`git diff --check` 通过。
- 真模型 MPS 街头样例 13 秒 → 1280×720、104 帧、8 fps、13 秒，耗时 64.09 秒；6 项自动质量检查通过。进程峰值内存足迹 11,554,982,504 字节（约 11.6 GB），仍然较高，不宣称低内存运行。
- 真模型 MPS 过山车 2.916667 秒 → 1280×720、23 帧、8 fps、2.875 秒，耗时 20.27 秒；6 项自动质量检查通过，时长误差小于一帧。
- 目视对照街头 1/6/11 秒、过山车 0.25/1.25/2.5 秒：人物、路面与远景深度可区分；快速移动的车体和轨道结构可见。没有深度真值，不能据此证明度量精度或复杂多人舞蹈遮挡质量。
- 输入来源为已有固定版本 Video-Depth-Anything 仓库 `assets/example_videos/Tokyo-Walk_rgb.mp4` 和 `davis_rollercoaster.mp4`，没有修改 vendor。
- 独立只读审查未发现阻断问题。已知非阻断边界：若 Metal 同步本身报错，释放空闲缓存无法执行，worker 会退出。
- CUDA 本机不可用，没有 CUDA 真机验证；未安装 ComfyUI、未启动后端服务。

复现入口（临时验证目录保留在本机）：`PYTHONPATH=backend .venv/bin/python <临时验证目录>/validate.py <素材名> <480p|720p> <mps|cpu>`。同素材/档位/设备组合已有成功产物时，需换新的 capture ID，避免覆盖。

- 30 秒循环素材 MPS 720P 验证通过：1280×720、240 帧、8 fps，时长 30 秒，耗时 124.25 秒，6 项自动质量检查通过；峰值进程内存足迹 11,918,379,864 字节（约 11.9 GB）。本轮未重启 300 秒压力任务，长片验证仍待完成。

- CPU 480P 过山车样例实测通过：854×480、23 帧、8 fps、2.875 秒，耗时 14.65 秒，6 项自动质量检查通过。四个成功产物均确认 metadata 中的 `worker.mpsMemoryFraction` 与设备对应（MPS 为 0.5，CPU 为 null）。
