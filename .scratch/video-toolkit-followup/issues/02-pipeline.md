Status: resolved
Type: task

# 视频工具持久化自动处理流水线

每份参考素材可建立一条至少两步的本地处理流水线。分析步骤（分镜、字幕、主体跟踪）始终读取不可变输入快照；补帧和超分按固定顺序串接上一个视频产物。创建时校验步骤顺序、依赖、遮罩前景点与项目级 12 个活动任务上限。

失败或取消会阻止后续步骤。重试只从第一个失败/取消步骤继续，成功步骤及其产物保留。重启会把中断步骤标为可重试失败；取消中的步骤尚未退出时禁止立即重试，防止旧工作进程覆盖新状态。重试和重启前都会清理每步的临时帧目录及未完成产物。

界面提供步骤选择、环境说明、补帧帧率、超分清晰度和遮罩选点入口，展示逐步状态、产物下载、取消及从失败步骤继续。流水线完成后的视频产物重新加入可选素材列表。

## Answer

- 后端：`video_toolkit_api.py` 新增持久化 pipelines API 与串行调度；`video_toolkit.py` 把已完成流水线视频列入项目素材。
- 前端：`VideoToolkitPanel.tsx` 与 `videoToolkitApi.ts` 新增流水线创建、状态、失败续跑与下载交互。
- 验证：`PYTHONPATH=. ../.venv/bin/pytest tests/test_video_toolkit.py tests/test_video_upscale.py -q`（41 passed）；`npm test -- VideoToolkitPanel.test.tsx --run`（6 passed）；`npm run build` 通过。
