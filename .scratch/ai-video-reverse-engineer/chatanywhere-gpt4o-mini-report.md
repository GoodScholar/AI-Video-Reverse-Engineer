# ChatAnywhere `gpt-4o-mini` 切换报告

日期：2026-09-14

## 变更结果

- 将 ChatAnywhere 的唯一产品模型从 `gpt-5.6-sol` 改为 `gpt-4o-mini`，目录版本从 `2026-09-14.1` 提升至 `2026-09-14.2`。
- 目录是唯一模型来源：适配器通过 `model_is_allowed` 校验并将请求模型透传至固定 Responses 端点；连接、语义任务、冒烟输出与前端选择器均消费目录或后端响应，没有新增前端模型常量。
- 旧配置迁移会删除不再被目录允许的 ChatAnywhere 配置，并清空同一供应商的选中状态。该操作不会读写系统钥匙串，因此既有密钥保留在安全存储中；用户必须重新保存 `gpt-4o-mini` 并重新验证。
- 固定端点 `https://api.chatanywhere.tech/v1/responses`、Bearer 鉴权、安全存储、禁用环境代理/重定向的加固传输，以及 ChatAnywhere 远程 `baseUrl` 拒绝规则均未修改。

## TDD 记录

1. 先改测试为 `gpt-4o-mini`，新增 `sol` 拒绝、旧目录迁移失效和 ChatAnywhere 冒烟输出断言。
2. 首次使用工作树中的 `.venv/bin/python` 运行失败，原因是该工作树不含该解释器路径；随后使用共享项目虚拟环境重新执行，未做依赖安装或外部调用。一次组合验证也错误地在仓库根目录调用了 `npm test`（根目录没有 `package.json`）；已从 `frontend/` 重跑完整前端验证。
3. RED：定向后端测试收集 21 项，其中 9 项按预期失败。直接原因是目录仍只允许 `gpt-5.6-sol`，所以新模型被拒绝、旧模型仍可保存，且旧配置迁移仍保留 `sol`。目录驱动的前端和冒烟测试无需生产改动即可通过，证明未存在第二份模型清单。
4. GREEN：仅更新统一目录、目录版本和失效配置迁移；同一组 21 项测试全部通过。

## 覆盖范围

- 目录：仅公开 `gpt-4o-mini`，并拒绝 `gpt-5.6-sol`。
- 适配器：固定 Responses 请求携带 `gpt-4o-mini`，继续验证固定端点、Bearer、无状态请求和严格图像结构化输出。
- 设置：新模型可保存和连接验证；旧模型拒绝；远程 `baseUrl` 仍拒绝；旧目录中的已验证 `sol` 配置会失效并被移除。
- 语义任务：配置后的 `gpt-4o-mini` 可完成并在重启后保持检查点。
- 冒烟：从本机 API 快照确认并输出 `chatanywhere gpt-4o-mini connected`，不读取密钥或供应商响应体。
- 前端：从 API `models` 渲染 `GPT-4o mini` 并保存该模型，未在生产组件中加入 ChatAnywhere 模型硬编码。

## 验证

- 定向后端 GREEN：21 passed。
- 全量后端：904 passed，8 skipped。
- Python：`compileall -q backend/app backend/scripts` 通过。
- 前端目录测试：10 passed；全量前端：11 个测试文件、129 passed。
- 前端生产构建：通过。
- 颜色对比度验证：通过。
- `git diff --check`：通过。

## 外部验证边界

本任务未读取真实 Key、未修改系统钥匙串、未发起网络请求。合并后由用户在本机重新保存已有 Key，并通过 UI 或脱敏本地冒烟命令执行一次真实图片连接测试。
