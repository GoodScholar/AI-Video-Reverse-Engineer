# 09: 收口可选 ComfyUI 适配器与能力声明

**What to build:** 把现有 ComfyUI 相关生成入口收口为 `GenerationService` 的可选 `ComfyUIAdapter`，在未配置时不影响其他 Provider；若界面宣称支持，则以一个真实 Workflow 贯通输入映射、队列、状态、取消、结果入库和下游使用。

**Blocked by:** 07: 收口 Provider 路由、取消、恢复与支持声明

**Status:** ready-for-agent

## Scope

- 复用现有 ComfyUI Workflow、连接检查和外部运行记录，不复制 GPL-3.0 核心代码进入闭源前后端。
- Workflow ID、版本、输入映射和能力描述纳入适配器目录；保存配置或连接成功不等于 Workflow 已验证。
- 未配置或未验证时明确显示可选、不可用或待验证，不阻塞其他 Provider 和首期整体能力。
- 若当前产品继续显示“支持”，必须完成一个真实队列、WebSocket/轮询、取消和结果安装闭环。

## Source reuse prerequisite

- 阅读 ComfyUI 固定版本的 API、队列、WebSocket 和取消源码/官方资料，以及项目现有 Workflow 导出与连接实现。
- 记录 GPL-3.0 边界、独立服务方式、复制与未复制范围和模型/自定义节点依赖。

## Acceptance criteria

- [ ] 未配置 ComfyUI 时，文本、图片、视频其他适配器正常工作且 UI 不宣称其已支持。
- [ ] Workflow ID、输入映射、版本和能力校验由服务端执行，未知输入不接受。
- [ ] 真实 Workflow 提交后可观察队列和进度，重启后能恢复查询。
- [ ] 本地停止与真实远端取消结果明确区分，迟到结果不自动采用。
- [ ] 成功媒体通过统一项目素材安装链入库并可被画布下游引用。
- [ ] 若无法完成真实验证，支持状态保持阻塞/未验证，不从分母删除。

## Verification evidence

- 协议测试覆盖输入映射、队列、轮询/WebSocket、取消和异常。
- E3 真实 ComfyUI 运行记录与项目素材；缺少环境时记录阻塞证据。
- 许可审计说明独立服务和代码复用边界。

## Out of scope

- 把 ComfyUI 设为首期强制依赖。
- 分发模型权重或自动安装未知自定义节点。
