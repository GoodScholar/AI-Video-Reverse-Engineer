# 16: 新增 ChatAnywhere 图片语义分析供应商

**What to build:** 在现有多供应商图片语义分析流程中新增固定地址的 ChatAnywhere 供应商，复用 OpenAI Responses 协议、严格结构化输出、系统安全存储与连接验证。

**Blocked by:** 04/支持参考图片与可恢复的多供应商语义分析

**Status:** resolved

- [x] 供应商标识为 `chatanywhere`，显示名为 `ChatAnywhere`，固定接入点为 `https://api.chatanywhere.tech/v1/responses`。
- [x] 首个锁定模型为 `gpt-5.6-sol`；前端从统一模型目录自动展示，不硬编码重复清单。
- [x] 复用 Responses API、图片输入、JSON Schema 结构化输出、最多一次纯文本修复和稳定错误映射。
- [x] 复用加固传输：不跟随重定向、不读取代理环境、限制响应体、支持取消和总时限。
- [x] API Key 只写入系统安全存储，不进入项目文件、日志、前端持久化或读取响应。
- [x] 不开放任意远程 Base URL；本地兼容供应商仍只允许回环地址。
- [x] 为目录、凭据、适配器、API 注册与前端交互补充 TDD 回归测试。
- [ ] 有效真实 Key 可通过连接测试并完成图片语义分析；无效 Key 返回 `authentication_failed`。

## Comments

- 2026-09-14：用户确认采用固定供应商方案。当前提供的是占位 Key，直接调用模型列表返回 401，未写入安全存储；真实冒烟测试等待有效 Key。
- 2026-09-14：已完成固定端点适配、目录、系统安全存储、API 注册和目录驱动前端接入。模拟的 401 映射为 `authentication_failed`；未使用占位 Key，也未执行真实付费调用。
- 2026-09-14：审查补充修复 `SemanticAnalysis.provider` 的领域枚举遗漏；ChatAnywhere 现在覆盖启动、队列 worker 完成与重启后已完成检查点恢复。默认 `ProviderSecretStore` 的手写允许集合也已改为目录派生，避免同类遗漏。

## Answer

ChatAnywhere 已作为固定地址的云端分析供应商接入，模型锁定为 `gpt-5.6-sol`。适配器复用 Responses API、严格结构化输出、一次纯文本修复和既有加固传输；密钥仅经系统安全存储处理。真实密钥冒烟仍由持有有效 Key 的人工环境执行。
