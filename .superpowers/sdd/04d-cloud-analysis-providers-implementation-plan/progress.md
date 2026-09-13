.scratch/ai-video-reverse-engineer/04d-cloud-analysis-providers-implementation-plan.md

# 04d 云端图片语义分析供应商执行台账

## 状态

- 计划：已按 04c 实际接口与官方协议证据完成审计，等待最终 Approved。
- 基线：后端 `754 passed, 8 skipped`；前端 `121 passed`；构建与颜色对比度通过。
- 真实密钥：未使用；模拟测试通过不能写入 `available`。

## 任务边界

| 任务 | 产出 | 依赖 | 状态 |
| --- | --- | --- | --- |
| Task 1 | 七供应商统一目录、凭据与配置契约 | 04c | 已完成 |
| Task 1.5 | 共享强化 HTTP 传输边界 | Task 1 | 已完成 |
| Task 2 | OpenAI、Grok Responses 适配器 | Task 1、1.5 | 待实现 |
| Task 3 | Claude Messages 适配器 | Task 1、1.5 | 待实现 |
| Task 4 | Gemini generateContent 适配器 | Task 1、1.5 | 待实现 |
| Task 5 | 豆包 Ark Responses 适配器 | Task 1、1.5 | 待实现 |
| Task 6 | 工厂注册、CAS 验证状态、前端、冒烟入口、文档 | Task 2～5 | 待实现 |

## 固定裁决

- 核心请求保留 `ProviderRequest(analysisInput, prompt, model, isRepair)` 与 `connection_test_request(model)`。
- 七供应商目录以 `provider_models.py` 为唯一来源；工厂注册留到 Task 6。
- 所有新核心适配器复用同一禁代理、禁重定向、受限响应、120 秒总期限、可取消的传输边界。
- 配置验证绑定 `configurationRevision` 与 `catalogVersion`，用 CAS 阻止迟到结果覆盖新配置。
- 豆包 260428 在真实密钥验证前始终为 `unverified`；不得用其他版本代替。

## 验证记录

- 2026-09-13：Task 1 定向回归 `PYTHONPATH=backend /Users/shen/SZG/AI Agent/AI Video Reverse Engineer/.venv/bin/pytest -q backend/tests/test_provider_models.py backend/tests/test_analysis_settings.py backend/tests/analysis_providers/test_contract.py backend/tests/test_semantic_analysis_api.py backend/tests/analysis_providers/test_bailian.py backend/tests/analysis_providers/test_local_openai_compatible.py` → `99 passed`。
- 2026-09-13：Task 1 完整后端回归 `PYTHONPATH=backend /Users/shen/SZG/AI Agent/AI Video Reverse Engineer/.venv/bin/pytest -q backend/tests` → `771 passed, 8 skipped`。
- 2026-09-13：Task 1 前端回归 `npm --prefix frontend test -- --run` → `11 files / 121 tests passed`；`npm --prefix frontend run build` → PASS。
- 2026-09-13：Task 1 审查修复完整回归 `PYTHONPATH=backend /Users/shen/SZG/AI Agent/AI Video Reverse Engineer/.venv/bin/pytest -q backend/tests` → `772 passed, 8 skipped`；`npm --prefix frontend test -- --run` → `11 files / 122 tests passed`；`npm --prefix frontend run build` → PASS。
- 2026-09-13：`PYTHONPATH=backend .venv/bin/pytest -q backend/tests` → `754 passed, 8 skipped`。
- 2026-09-13：`npm test -- --run` → `11 files / 121 tests passed`。
- 2026-09-13：`npm run build` → PASS。
- 2026-09-13：`node frontend/scripts/verify-color-contrast.mjs` → PASS。
- 2026-09-13：Task 1.5 定向回归 `PYTHONPATH=backend /Users/shen/SZG/AI Agent/AI Video Reverse Engineer/.venv/bin/pytest -q backend/tests/analysis_providers/test_bailian.py backend/tests/analysis_providers/test_local_openai_compatible.py backend/tests/test_semantic_analysis_api.py` → `45 passed`；供应商与网络边界回归 `PYTHONPATH=backend /Users/shen/SZG/AI Agent/AI Video Reverse Engineer/.venv/bin/pytest -q backend/tests/analysis_providers backend/tests/test_analysis_provider.py` → `115 passed`；完整后端 `PYTHONPATH=backend /Users/shen/SZG/AI Agent/AI Video Reverse Engineer/.venv/bin/pytest -q backend/tests` → `773 passed, 8 skipped`；`git diff --check` → PASS。

## 执行记录

- 2026-09-13：Task 1 完成不可变七供应商模型目录；凭据、设置、配置 API 和前端供应商类型改由目录扩展。云端模型改为严格白名单，本地仍允许非空自填模型；百炼公开模型从目录派生。未注册五家新适配器工厂，未实施 Task 1.5。
- 2026-09-13：Task 1 审查修复：设置页在 Task 6 前只显示并操作百炼与本地服务，统一目录增加供应商标签与旧目录兼容投影，恢复语义 POST 非法型号回归，并以不同本地模型覆盖失败回滚。
- 2026-09-13：gpt-6 Astra/medium 完成计划审计；修正目录双来源、提前工厂注册、旧三参数适配器入口、共享 HTTP 边界、错误码、结构化输出、连接验证与前端范围。
- 2026-09-13：Task 1.5 将 04c 的 socket 级取消、120 秒总期限、禁代理/禁重定向和 256,000-byte 流式限制抽取为 `http_transport`。新边界对成功和 HTTP 错误统一返回受限 `ProviderHTTPResult`，仅传输失败抛出类型化错误；百炼和本地兼容服务继续通过聊天适配器保留既有 payload、解析与稳定错误映射。TDD 先以缺失共享模块的契约测试记录 RED，再完成最小实现并回归。
