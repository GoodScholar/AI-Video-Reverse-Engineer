# ChatAnywhere 图片语义分析供应商实施报告

日期：2026-09-14

## 目标与边界

- 新增固定供应商 `chatanywhere`（显示名 `ChatAnywhere`），唯一模型为 `gpt-5.6-sol`。
- 仅使用固定接入点 `https://api.chatanywhere.tech/v1/responses`；不提供远程 Base URL 配置。
- 沿用现有 Responses 图片输入、严格 JSON Schema、最多一次无图片的纯文本修复、稳定错误映射和加固 HTTP 传输。
- 未使用对话中的占位 Key，未执行真实供应商或付费调用。

## TDD 证据

### RED

先新增目录、凭据、适配器契约、401 映射、严格探针/一次修复、API 列表/保存/连接、冒烟脚本参数和前端目录渲染测试。后端 RED 命令：

```text
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_provider_models.py backend/tests/test_credential_store.py backend/tests/analysis_providers/test_chatanywhere.py backend/tests/test_analysis_settings.py backend/tests/test_smoke_analysis_provider.py -q
```

预期失败：`ModuleNotFoundError: No module named 'app.analysis_providers.chatanywhere'`，测试收集在新增适配器导入处停止。此前工作树没有 `.venv`，依 README 创建隔离环境并安装锁定依赖后重跑，未跳过 RED。

前端测试在新增供应商仅由 API 目录提供、组件无需硬编码的前提下先行通过；类型联合为让该真实目录值可被 TypeScript 表达而作的最小类型扩展。

### GREEN

实现固定端点适配器、统一目录、默认注册、系统安全存储允许列表派生、CLI 参数、前端类型与文档后，定向回归通过：

```text
86 passed in 2.90s
14 passed (前端相关测试)
frontend build: passed
```

## 实现摘要

- `backend/app/analysis_providers/chatanywhere.py`：新固定端点适配器；共享 `responses_api` 和 `post_provider_json`，因此复用无重定向、禁用环境代理、响应体上限、总时限及取消支持。
- `backend/app/provider_models.py`：统一目录增加供应商、模型和目录版本；凭据存储允许范围自动继承该目录。
- `backend/app/main.py`：注册默认适配器，并把执行期的凭据要求统一为“除本地兼容服务外均需要 Key”。
- 前端仅扩展 API 返回值的供应商类型；设置界面继续按 `models` 目录渲染，未添加 ChatAnywhere 硬编码表单或模型清单。
- 设置 API 仍只接受本地兼容服务的 `baseUrl`；ChatAnywhere 的密钥只经系统安全存储写入，响应和设置文件均不包含密钥。

## 验证清单

```text
PYTHONPATH=backend .venv/bin/python -m pytest -q backend/tests
900 passed, 8 skipped

npm test -- --run
11 test files, 129 passed

npm run build
passed (tsc -b + vite build)

node frontend/scripts/verify-color-contrast.mjs
颜色对比度验证通过

PYTHONPATH=backend .venv/bin/python -m compileall -q backend/app backend/scripts
passed

git diff --check
passed
```

## 未完成的外部验证

有效真实 Key 的连接和图片语义冒烟未执行：需求明确禁止使用占位 Key，当前未提供真实 Key。模拟 HTTP 401 契约已验证为 `authentication_failed`；真实连通性须在持有有效 Key 的本机通过安全存储配置后运行既有本地冒烟脚本。

## 修复轮：语义任务领域类型

审查发现 `SemanticAnalysis.provider` 是另一处手写供应商 `Literal`，遗漏 `chatanywhere` 会让配置保存成功后，启动语义分析在创建任务时返回 500。新增回归覆盖 ChatAnywhere 的“启动 → 队列/worker → 完成 → 应用重启后已完成检查点恢复”完整路径。

### RED

```text
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_semantic_analysis_api.py -q -k chatanywhere
1 failed: expected 202, received 500

直接领域模型校验：literal_error
```

### GREEN

- 将 `chatanywhere` 加入 `SemanticAnalysis.provider` 的领域枚举。
- 检索手写供应商集合后，将 `ProviderSecretStore` 的默认允许集合改为统一目录派生，并添加 ChatAnywhere 回归；应用实际使用的 `CredentialStore` 仍保持系统安全存储边界。

```text
semantic ChatAnywhere 回归：1 passed
analysis_service_secrets：22 passed

修复后全量后端回归：901 passed, 8 skipped；前端全量：129 passed；生产构建、颜色对比度、`compileall` 与 `git diff --check` 均通过。
```
