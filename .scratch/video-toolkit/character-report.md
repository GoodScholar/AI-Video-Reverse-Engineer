# 角色动画/动作迁移报告

## 实现

- 后端：`backend/app/character_motion.py`、`character_motion_templates.py`、`character_motion_api.py`；在 `main.py` 挂载 `/api/projects/{projectId}/character-motion`。
- 前端：`CharacterMotionPanel.tsx`、`characterMotionApi.ts` 与样式，并在项目页挂载。
- 独立角色图会解码、尺寸/20 MB 限制并标准化为 PNG，保存于 `project-files/<projectId>/character-motion/characters/<imageId>.png`；不会替换 `referenceMedia`。
- 驱动严格使用当前项目的安全托管 MP4/MOV 路径。保存的方案包含提示词、参数、私网/回环 ComfyUI 地址、角色图和驱动视频来源快照，revision 冲突返回 409。
- 导出 ZIP 包含 `input/character.png`、当前 `input/driver.*`、`workflow-api.json`、完整固定的 `official-template.json`、manifest 与说明。
- 生成输出固定保存于 `project-files/<projectId>/character-motion/runs/<runId>/outputs/<filename>`。状态 API 的 `runs[].outputs[].url` 为 `/api/projects/<projectId>/character-motion/runs/<runId>/output/<index>`。

## 官方模板与依赖

固定快照来自 [Comfy-Org workflow templates](https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_wan2_2_14B_animate.json)：commit `90c71fb78b3726392d010ff62a8e79e92d7296ad`，SHA-256 为 `06ad8b95e64215328a2a3d2f90495b5bab3e251175e4258d01b977f6dcdecb69`，保存为 `backend/app/workflow_templates/video_wan2_2_14B_animate.official.json`。

模板采用官方 Move 模式：DW 人脸与身体/手部姿态驱动角色图背景动画，`WanAnimateToVideo` 后接真实采样、解码与视频封装链。没有将深度或 MediaPipe 当作兼容姿态输入，也不接入要求固定选点的 SAM2 主体替换分支，因此不会触发模型自动下载。环境检查列出 `comfyui_controlnet_aux`、Wan Animate、T5、CLIP Vision、VAE 与 LoRA 依赖。

## 验证

- `PYTHONPATH=. ../.venv/bin/pytest tests/test_character_motion.py -q` → `6 passed`
- `PYTHONPATH=. ../.venv/bin/pytest tests/test_character_motion.py tests/test_reproduction_api.py tests/test_projects_api.py -q` → `45 passed`
- `npm test -- CharacterMotionPanel.test.tsx` → `1 passed`
- `py_compile` 通过：角色模块和 `main.py`。

`npm run build` 此时被并行模块的 `src/VideoToolkitPanel.test.tsx` 阻断：该测试已引用但 `VideoToolkitPanel` 尚未出现。这不是角色模块的 TypeScript 错误；待该并行文件落地后需重跑全量构建。

## 审查修复

- 官方 API 图现展开真实的条件编码、Wan Animate latent、KSampler、TrimVideoLatent、VAE 解码和 CreateVideo 链，并采用官方双 DW Move 姿态链。
- 保存请求只传后端 `SavePlan` 接受的四个字段。角色上传使 sourceHash 为空时，UI 也会显示为待保存。
- 确定性 ComfyUI 拒绝记录为 failed；未知记录提供手动刷新、粘贴 prompt ID 继续跟踪和“确认未排队”恢复动作；轮询失败会安排下一次重试。
- 素材变更会重新读取驱动状态；上传/保存的迟到响应按项目 ID 丢弃。
- 输入快照的角色图与视频叶子路径经过存储路径符号链接校验，并通过临时文件后原子替换。

## 限制

本机没有 ComfyUI/GPU，未下载权重或尝试真实生成。所有 UI、环境检查和导出包都明确为 candidate；只有已保存且针对相同 revision/地址的检查报告 `ready` 时，界面才允许提交生成。网络中断会保留 `unknown` 记录，绝不显示为完成。
