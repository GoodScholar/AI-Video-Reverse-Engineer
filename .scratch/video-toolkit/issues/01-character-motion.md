Status: resolved
Type: task

# 角色动画与动作迁移

Read ../spec.md. Implement end-to-end character motion generation using existing ComfyUI client and project architecture. User has NO ComfyUI. No auto installation, downloads of weights or real generation. Do not spawn subagents or commit. Own new character_motion* backend files, new CharacterMotionPanel* frontend files and matching tests. You may modify comfyui_client.py with backwards-compatible improvements and mount router/panel in main.py/App.tsx narrowly. Preserve all current work.

Requirements:
- Independent character image upload, retaining current video as driver (if needed optional independent driver upload too). Do not replace project's referenceMedia. Decode/sanitize image and limit upload; validate driver file identity/safe path.
- Standalone saved plan without semantic-analysis dependency: prompt, generation settings, local/private ComfyUI URL, character image and current driver. Revision concurrency protection.
- Fixed official-source Wan Animate workflow, consult current official Comfy-Org template and native node schemas. It must use real motion/face preprocessing required by model, never mislabel depth/MediaPipe as compatible pose. If workflow depends on extra nodes show exact dependencies. Official template https://github.com/Comfy-Org/workflow_templates/blob/main/templates/video_wan2_2_14B_animate.json; newer video_wan_animate2.json can be evaluated. Choose smallest verifiable interface. Do not invent nodes. Preserve license/provenance and pin downloaded template. Clearly candidate until actual GPU validation.
- GET state, save, upload image, check, export runnable package with assets offline, explicit start/refresh/output similar reproduction route. Source snapshots to prevent replacement changes; persistent states and duplicate/unknown protections. Proper environment-unavailable handling. Reuse existing client not clone it.
- Chinese UI: role image, reference driver, editable prompt/settings, environment instructions official ComfyUI link, save, export, environment check and generation disabled unless exact saved plan checked. Result preview and download. Project-switch guard. Mobile readable.
- Tests: absent ComfyUI; upload invalid image/path; missing source; stale revision; export assets correctness; required preprocess/template nodes; simulate queue completion/failure/unknown; frontend no-server usable plan and no false completion. First write important failing tests then implementation.
- Main middleware must protect new mutation routes via existing origin/intent pattern. See reproduction_api.py and main.py.

## Answer

已实现角色图独立上传、当前项目视频驱动、离线方案保存/导出、ComfyUI 检查和显式提交/轮询/未知状态恢复。固定导出 Comfy-Org `video_wan2_2_14B_animate.json` 的本地快照（commit `90c71fb78b3726392d010ff62a8e79e92d7296ad`，SHA-256 `06ad8b95e64215328a2a3d2f90495b5bab3e251175e4258d01b977f6dcdecb69`），并列出 DW、SAM2 与官方模板的自定义节点依赖。实际 GPU 队列尚未验证，界面与导出包均标注为 candidate。

Report to ../character-report.md with files, test commands/results, limitations and template provenance. Final response short.

## Answer

实现及验收见 character-report.md、character-ui-report.md、character-backend-review.md、character-ui-review.md（位于上级目录）。后端14项角色相关回归通过；前端最终11项角色相关回归通过，构建通过。真实浏览器无ComfyUI上传/保存/导出/环境缺失路径已实测。未运行真实GPU，模板保持candidate。
