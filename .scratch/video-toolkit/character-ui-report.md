# 角色动画 UI 修复报告

## 已修复

- 以“项目 ID + 参考视频 ID + generation”作为异步会话身份。切换项目或同项目替换参考视频时，立即清空状态、草稿、动作、检查和未知任务输入；所有读取、上传、保存、检查、生成、导出、手动刷新、未知状态恢复和轮询响应均在回写前验证会话。
- ComfyUI 检查结果绑定保存 revision、地址和完整草稿指纹。编辑检查中的草稿后，迟到的 ready 响应不会重新使生成可用。
- `stale` 的相同方案可以重新保存，重新绑定角色/驱动素材；上传后 `sourceHash: null` 不会被 UI 覆盖。
- 提交生成失败时重新读取持久化状态，后端已落盘的 `unknown` 运行会显示在界面，而错误仍会保留。
- 未知运行支持填写 prompt ID 继续跟踪；“确认未排队并恢复提交”要求勾选明确确认。手动刷新和恢复操作在忙碌时禁用，并带错误和会话保护。
- 轮询失败后递增重试代次并继续调度，清理、卸载或切换会话后不会回写。
- 保存请求只发送 `revision`、`prompt`、`settings`、`comfyUrl` 四个后端允许字段。
- UI 说明 Move 模式背景和驱动截取规则；说明未安装 ComfyUI 时仍可保存/导出，并链接官方安装说明。环境检查明确不验证 DWPose 私有权重缓存。
- 生成前要求确认目标 ComfyUI 已运行 DWPose，且 `yolox_l.onnx` 和 `dw-ll_ucoco_384_bs5.torchscript.pt` 已缓存；保存、地址编辑、项目或参考视频变化后会重置确认。生成请求传递 `preprocessorConfirmed: true`。
- 输出视频下方提供 `${output.url}?download=true` 下载链接。

## 回归验证

- `npm test -- CharacterMotionPanel.test.tsx characterMotionApi.test.ts`：2 个文件、8 个测试通过。
- `npm run build`：TypeScript 编译和 Vite 生产构建通过。
- `npm test`：19 个文件、185 个测试全部通过。

覆盖的新增实际回归场景：保存请求字段白名单、旧项目上传迟到、同项目参考视频切换、stale 重新保存、检查慢响应期间编辑、轮询首次失败后的继续刷新，以及 unknown 的 prompt ID / 明确未排队恢复流程。

## 补充修复：手动状态操作保留草稿

- 手动刷新和未知状态的继续跟踪/恢复现在与自动轮询使用相同的草稿保留规则：当前草稿相对持久状态存在修改（或素材绑定仍 stale）时，只更新运行状态，不会用响应中的新 revision 或服务端提示词覆盖草稿。
- 新增两条回归测试，分别覆盖手动刷新和继续跟踪未知任务时保留未保存提示词。
- 补充验证：`npm test -- CharacterMotionPanel.test.tsx characterMotionApi.test.ts`：2 个文件、10 个测试通过。

## 补充修复：草稿并发版本

- 保存请求使用 `draft.revision`，而非手动状态刷新后更新的持久 `state.revision`。因此旧草稿保存会正确触发后端的 409 冲突校验，不会绕过并发保护。
- 回归测试覆盖 revision 1 的未保存草稿、手动刷新返回 revision 2 后，保存仍发送 revision 1。
- stale 回归夹具明确模拟角色和驱动均存在、`sourceHash: null`、`stale: true` 的上传后状态，并验证含提示词的方案可以重新保存。
- 补充验证：`npm test -- CharacterMotionPanel.test.tsx characterMotionApi.test.ts`：2 个文件、11 个测试通过。

## 边界

本机没有 ComfyUI，因此未执行真实生成。上述验证覆盖前端请求、会话隔离、状态恢复和界面门控。
