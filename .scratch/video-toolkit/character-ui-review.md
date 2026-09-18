# 角色动画 UI 最终限定审查

## 结论

**通过。原审查 2/3/4/5/6 的前端问题及本轮新增 P2 草稿丢失均已修复关闭。**

范围：最新 `CharacterMotionPanel.tsx`、API 客户端及测试、App 挂载、`/tmp/aivre-character-ui-fixed.diff` 和 UI 实施报告。未修改代码，未重跑测试；复用主任务报告的全前端 185 项测试与构建通过结果。后端工作流、异常分类与文件安全由另一审查负责，不在此重复给 verdict。

## 已关闭：P2 手动刷新/未知恢复静默丢失未保存草稿

限定复查确认：`apply` 新增 `preserveDirtyDraft` 参数，通过 stateRef/draftRef 指纹差异或 stale 判断草稿需保留；`refreshRun` 和 `resolveRun` 均传入 true。响应仍更新服务器状态，但不会覆盖未保存草稿及其 revision；干净草稿继续正常同步，保存/上传的既有完整替换语义不变。新增手动刷新和继续跟踪两条测试，均使用返回不同服务器提示词及新 revision 的场景验证原草稿保留。复用主任务提供的相关 2 文件、10 项测试通过结果，未重跑；本次限定修改未发现新阻断。

以下为原问题记录，已不代表当前实现：

位置：`frontend/src/CharacterMotionPanel.tsx` 的 `refreshRun`、`resolveRun` 及公共 `apply`。

当前用户可以在有 running/unknown 记录时编辑提示词、地址和参数。自动轮询明确使用 `replaceDraft` 保留已编辑草稿，但点击“手动刷新”“继续跟踪”或“确认未排队并恢复提交”后，响应走 `apply(next)`，无条件 `setDraft(next)`。这些动作的含义是更新运行记录，不是撤销编辑，因此会没有提示地把未保存内容替换为服务器旧方案。

确定路径：载入一份含运行记录的方案 → 编辑动作提示词但不保存 → 点击该记录的手动刷新 → 成功返回原已保存 prompt 的状态 → `apply` 覆盖新 prompt。未知状态的两个恢复入口同理。现有 unknown 回归没有在恢复前编辑草稿，不能发现此问题。

建议：仅更新运行/服务器状态时保留 dirty draft，与自动轮询保持一致；保存/上传等确需替换完整方案的动作继续使用现有 apply。补一个带未保存提示词的刷新/恢复回归即可。

## 原发现关闭情况

| 原编号 | 前端复查结果 |
| --- | --- |
| 2：保存发送额外字段 | 关闭。save 明确构造 revision/prompt/settings/comfyUrl 四字段，没有再透传整个 state。API 接受 Pick 仍不裁剪，但当前唯一调用端已按真实契约构造。 |
| 3：unknown 无恢复入口 | 前端部分关闭。未知任务有 prompt ID 绑定、明确未排队确认、手动刷新；生成请求失败后 GET 回读持久化状态，并保留原错误。后端的 unknown 分类另审。 |
| 4：项目/来源迟到响应污染 | 关闭。App 按项目/参考视频 key 重挂载；面板每次 scope 变化递增 generation，各类状态/错误/action 回写有会话校验。check 额外绑定保存及草稿 planKey，编辑会清理 ready，迟到检查不能覆盖新草稿。 |
| 5：换素材后不能重新保存 | 关闭。requiresSave 包含 state.stale；上传后的 sourceHash:null 原样保留；参考视频 ID 进入 scope/effect/key。 |
| 6：轮询一次失败后停止 | 关闭。失败增加 pollAttempt 重新调度；成功更新 runs 继续调度；effect cleanup 和会话 guard 阻止旧轮询回写。 |

## 其他核对

- 没有 ComfyUI 时保存/导出不依赖 check.ready；usable 只依赖本应用连接、已绑定素材和已保存方案。没有本应用后端时不能持久化属于不同边界，文案已区分。
- DWPose 缓存确认是生成按钮的必要条件；编辑/保存/切换来源会重置；API 发送 preprocessorConfirmed。
- 生成失败后的回读是会话受保护的，旧项目失败不会覆盖新项目。
- 下载 helper 在导出返回后会完成原请求的文件下载；面板没有将其写入新项目状态。此行为不构成项目数据污染。
- 未执行真实 ComfyUI 生成，前端测试通过不能用于宣称真实模型效果已经验收。
