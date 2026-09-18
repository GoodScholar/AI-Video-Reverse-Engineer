# 角色动画独立审查

- Spec verdict：**不通过**。离线保存主路径被阻断，导出的 API 图不满足官方节点契约，未知状态恢复和项目切换保护不完整。
- Standards verdict：**不通过**。请求契约、图类型连线、异常分类与异步状态存在确定性缺陷。
- 范围：任务、spec、character-report、`/tmp/aivre-character-review.diff`、当前实现及 main/App 集成、仓库内固定官方模板。未修改实现，未重跑已有测试，未进行真实 GPU 执行。

## 重要发现

### 1. [P1] 导出及提交图不是有效的官方 Wan Animate 图

位置：`backend/app/character_motion_templates.py:27-35`。

固定官方快照的子图内，`WanAnimateToVideo` 节点 62/90 必须接收 positive/negative CONDITIONING 与加载后的 VAE，其输出 0 是 CONDITIONING，输出 2 是 LATENT；后续实际链路包含 KSampler、VAEDecode、CreateVideo，再接 SaveVideo。当前图把模型文件名字符串放进 WanAnimateToVideo，并把输出 0 直接连接 SaveVideo 的 VIDEO 输入，缺少条件编码、模型加载、采样和解码。这是可从已有官方快照直接证明的类型错误，不属于等待 GPU 才能确认的 candidate 风险。`prompt` 仅做非空检查，完全没有进入图；`face_video` 直接使用原视频，也没有官方两组 DWPreprocessor 区分人脸和身体的流程。导出包和实际提交都会使用该图。

建议：从固定模板展开真实子图，保留完整模型/条件/采样/解码链路，按真实输入输出契约校验。现有仅断言节点名存在、mock check 永远 ready 的测试不能证明可运行。

### 2. [P1] UI 每次保存都发送后端禁止的额外字段，返回 422

位置：`frontend/src/CharacterMotionPanel.tsx:40`，`frontend/src/characterMotionApi.ts:17`，`backend/app/character_motion_api.py:26-32`。

界面直接把完整 `draft` 传给 saveCharacterMotion；TypeScript 的 Pick 只约束类型，不会裁剪对象，JSON.stringify 仍发送 character/sourceHash/runs/driver/stale/template。SavePlan 使用 extra=forbid。独立执行 SavePlan.model_validate(完整状态) 已复现这六个字段全部 extra_forbidden。因此用户即使没有 ComfyUI，也无法通过 UI 保存方案、更不能进入导出。

建议：构造明确的 revision/prompt/settings/comfyUrl 请求体，并增加跨前后端契约验证。

### 3. [P1] 明确未提交的失败错误标记 unknown，并永久挡住后续生成

位置：`backend/app/character_motion_api.py:245-264`；`frontend/src/CharacterMotionPanel.tsx:20,30-36,60`。

先落盘 submitting 后再检查环境。环境缺失触发本地 422，或 ComfyUI 明确拒绝 /prompt 返回 400 时，均进入未绑定异常变量的 `except HTTPException`；locals().get("error") 为 None，默认值使其无条件写入 unknown。后续 start 被未完成保护挡住。UI 没有 resolve 操作或手动刷新，unknown 也不再自动轮询；真实网络抖动后已有 promptId 的任务同样无法从界面恢复。一次普通不可用就可能让项目生成入口永久阻塞。

建议：区分提交前失败/确定拒绝与提交结果未知；界面暴露刷新及明确人工恢复动作，并在提交异常后重新读取持久化状态。

### 4. [P1] 保存/上传/检查/生成迟到响应会污染切换后的项目

位置：`frontend/src/CharacterMotionPanel.tsx:38-43`；`frontend/src/App.tsx:279`。

App 不按项目 key 重挂载面板；只有初始 GET 和轮询有项目 guard，其他异步操作 await 后直接 apply/setCheck/setError。复现：A 项目发起慢上传或保存，切换 B 并完成 B 的 GET，再让 A 请求返回，会把 A 的素材/版本/运行列表覆盖到 B 的面板。随后编辑保存会把 A 草稿提交给 B。检查结果也没有 revision/地址绑定，不能保证展示的 ready 属于当前保存方案。

建议：全部异步响应校验项目和操作代次；检查记录绑定保存 revision、URL、输入身份，切换立即清空旧状态。

### 5. [P2] 上传角色图或更换驱动后，界面没有可用的重新保存路径

位置：`frontend/src/CharacterMotionPanel.tsx:19,23-28,38-40,58`；`backend/app/character_motion_api.py:197-198`。

已有提示词方案上传/更换角色图后，后端清空 sourceHash，apply 却同时把 state/draft 替换成相同内容；same 只比较提示词/参数/地址，保存按钮保持禁用，导出/check 被后端判 stale。用户必须无意义修改提示词才能保存。更换同项目参考视频时 effect 只依赖 project.id，不重新获取 driver/stale；面板会持续显示旧驱动，首次添加驱动后也可能一直无法启用导出。上述问题独立于第 2 项请求体修复。

建议：把待绑定/过期的素材身份纳入需要保存条件，并在 referenceMedia.id 变化时更新后端状态。

### 6. [P2] 单次轮询请求失败后，自动刷新永久停止

位置：`frontend/src/CharacterMotionPanel.tsx:30-36`。

轮询使用一次性 setTimeout，只有状态依赖变化才重建；请求 reject 后 catch 吞掉错误且不修改任何依赖。一次暂时 HTTP/网络失败后不会再安排下一次请求，运行记录永久停留排队/执行，界面也没有刷新按钮。可用拒绝一次 refresh 的 deferred promise 复现。

建议：无论本次是否成功都在有效项目范围内安排下一次刷新，或提供可恢复的明确重试状态。

### 7. [P2] 输入快照文件绕过逐级符号链接检查

位置：`backend/app/character_motion_api.py:97-102`。

store.path 只验证 inputs-N 目录；之后通过 `/ "character.png"`、`/ "driver.mp4"` 构造叶子路径，再无条件 shutil.copyfile。导出一次后把叶子文件替换为指向项目外文件的 symlink，再次导出会沿链接覆盖项目外文件，违背本任务显式要求的素材路径/符号链接保护。输出路径使用 store.path 完整叶子校验，输入快照应采用同等保护。

建议：对目标文件完整路径进行归属/符号链接验证，并用安全临时文件原子替换；避免复用可变快照覆盖已提交运行输入。

## 验证与边界

- 直接解析固定官方模板，核对 WanAnimateToVideo、KSampler、VAEDecode、CreateVideo 的 inputs/outputs，证据来自真实快照而非 mock。
- 单独运行 SavePlan 对 UI 完整状态的 schema 校验，复现六项 extra_forbidden；未重跑既有同范围测试。
- 已有测试报告可复用，但无法覆盖以上图契约、真实保存请求、迟到响应及恢复状态问题。
- 缺少 ComfyUI 本身不阻止后端保存/打包；当前阻断源于实现缺陷。candidate 标识是正确的，但不能替代静态可确认的图正确性。
