# 视频工具修复复查

## 结论

**本次限定范围复查通过。原两项 P2 均可关闭，未发现修复引入的新阻断。**

复查依据：最新源码、`/tmp/aivre-toolkit-fixed.diff`、实施报告、worker README/requirements.lock，以及新增测试源码。复用主任务已提供的成功结果，未重复运行测试。

## 1. 独立 worker 依赖：关闭

- `video_toolkit.py` 把主应用的存储/Pydantic 导入延迟至路径与素材函数，worker 启动及 scene 执行不触发这些函数。
- README 提供独立 Python 3.11 venv 和 requirements.lock 安装命令；锁文件明确包含 Pillow、PySceneDetect、OpenCV 及其依赖。SAM 2 的独立环境也明确要求 Pillow。
- `test_real_scene_detector_in_isolated_environment` 通过真实 `execute_tool` 进入默认独立解释器，验证两秒红蓝视频检出一秒切点；主任务报告此次测试已实际通过，未跳过。
- 环境状态仍是轻量检查，不是模型加载保证，README 已明确这一契约；未宣称 SAM 2/whisper/RIFE 模型实测。

## 2. 正常关闭服务时 worker 停止：关闭

- router shutdown 先在状态锁下将 queued/running 标为 cancelled。
- main 将共享 `compute_jobs.shutdown` 注册放到 toolkit router 挂载之后，因此停止信号先于线程池等待执行。
- 运行中的 `execute_tool` 通过现有取消轮询进入 finally，向独立进程组发送 TERM，超时升级 KILL；排队任务不会再启动推理。
- 新增 API shutdown 测试覆盖退出时队列状态取消及随后队列调用不执行；新增外部 worker 测试真实产生 CLI 及 sleep 后代，验证取消后两者均退出。主任务报告上述检查已通过，worker 组共 5 项通过。

## 验收边界

- SIGKILL 等强制杀死主进程仍不具备自动收割独立 worker 的保证；README 如实限定正常关闭与强制结束的差别。这一残余运维边界不影响已修复的正常 shutdown 行为，不应宣称强杀场景也已验证。
- 本次通过指实现及已测生命周期路径；真实 SAM 2、whisper、RIFE 推理效果仍未验收。
- 实施报告尚有“追加测试待最终计数”的临时文字，交付前应按最终结果更新计数；这属于报告收尾，不是代码阻断。
