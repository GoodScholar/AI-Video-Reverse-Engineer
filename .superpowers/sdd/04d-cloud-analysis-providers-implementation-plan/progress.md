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
| Task 2 | OpenAI、Grok Responses 适配器 | Task 1、1.5 | 已完成 |
| Task 3 | Claude Messages 适配器 | Task 1、1.5 | 已完成 |
| Task 4 | Gemini generateContent 适配器 | Task 1、1.5 | 已完成 |
| Task 5 | 豆包 Ark Responses 适配器 | Task 1、1.5 | 已完成（模拟契约） |
| Task 6 | 工厂注册、CAS 验证状态、前端、冒烟入口、文档 | Task 2～5 | 已完成（模拟与本地 API 路径） |

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
- 2026-09-13：Task 2 RED：新增 OpenAI/Grok 测试后，`PYTHONPATH=backend /Users/shen/SZG/AI Agent/AI Video Reverse Engineer/.venv/bin/pytest -q backend/tests/analysis_providers/test_openai.py backend/tests/analysis_providers/test_grok.py` 因缺少 `app.analysis_providers.grok` 失败。探针提示契约测试随后以旧 `{"ok":true}` 提示对完整分析提示的偏差稳定失败。GREEN：适配器与共享契约回归 `125 passed`；完整后端 `797 passed, 8 skipped`；`compileall -q backend/app` 与 `git diff --check` 均通过。未使用真实密钥，因此云端验证状态仍为 `unverified`。
- 2026-09-13：Task 2 审查修复 RED：Responses wire schema 的根 `version` 不在 `required`，且 `message.status: incomplete` 仍被提取为文本；新增图片、视频、repair 递归 schema 与未完成 message 回归后为 `4 failed`。GREEN：在不修改领域/共享 schema 的前提下，出站 Responses schema 深拷贝后递归将每个 `properties` 集合精确写入 `required`；非 `completed` 的显式 message 状态被拒绝。定向 `129 passed`，完整后端 `801 passed, 8 skipped`，`compileall` 和 `git diff --check` 通过。
- 2026-09-13：Task 3 RED：新增 Claude 核心适配器测试后，因构造器尚无凭据注入而为 `20 failed`；旧三参数契约测试随后按预期以 `TypeError` 暴露尚未迁移到显式 `analyze_legacy` 的调用点。GREEN：Claude、共享和遗留供应商契约回归 `99 passed`；完整后端 `822 passed, 8 skipped`；`PYTHONPATH=backend /Users/shen/SZG/AI Agent/AI Video Reverse Engineer/.venv/bin/python -m compileall -q backend/app` 与 `git diff --check` 通过。未使用真实密钥，云端验证状态保持 `unverified`。
- 2026-09-13：Task 4 RED：新增 Gemini 图片、视频、repair、wire schema、候选提取、稳定错误、探针和显式 legacy 测试后，`PYTHONPATH=backend ../../.venv/bin/pytest -q backend/tests/analysis_providers/test_gemini.py` 为 `23 failed`；失败均指向旧适配器没有核心凭据注入、单参数 `analyze` 或 `analyze_legacy`。GREEN：同一命令为 `23 passed`；Gemini、共享契约和基础供应商回归为 `115 passed`，遗留供应商契约为 `62 passed`，完整后端为 `845 passed, 8 skipped`；`compileall` 与 `git diff --check` 通过。未使用真实密钥，Gemini 验证状态保持 `unverified`。
- 2026-09-13：Task 5 RED：新增豆包图片、视频联系表、repair、Responses 输出遍历、稳定错误、探针、关键字凭据与显式 legacy 测试，并把遗留供应商契约改调 `analyze_legacy`；`PYTHONPATH=backend backend/.venv/bin/python -m pytest -q backend/tests/analysis_providers/test_doubao.py backend/tests/test_analysis_provider_contract.py` 为 `23 failed, 50 passed`，失败指向旧构造器不接受 `credential` 且没有 `analyze_legacy`。GREEN：豆包与共享契约 `27 passed`，供应商/语义 API 回归 `144 passed, 1 warning`，完整后端 `856 passed, 8 skipped, 1 warning`；`compileall` 与 `git diff --check` 通过。未使用真实密钥，豆包保持 `unverified`。

## 执行记录

- 2026-09-13：Task 1 完成不可变七供应商模型目录；凭据、设置、配置 API 和前端供应商类型改由目录扩展。云端模型改为严格白名单，本地仍允许非空自填模型；百炼公开模型从目录派生。未注册五家新适配器工厂，未实施 Task 1.5。
- 2026-09-13：Task 1 审查修复：设置页在 Task 6 前只显示并操作百炼与本地服务，统一目录增加供应商标签与旧目录兼容投影，恢复语义 POST 非法型号回归，并以不同本地模型覆盖失败回滚。
- 2026-09-13：gpt-6 Astra/medium 完成计划审计；修正目录双来源、提前工厂注册、旧三参数适配器入口、共享 HTTP 边界、错误码、结构化输出、连接验证与前端范围。
- 2026-09-13：Task 1.5 将 04c 的 socket 级取消、120 秒总期限、禁代理/禁重定向和 256,000-byte 流式限制抽取为 `http_transport`。新边界对成功和 HTTP 错误统一返回受限 `ProviderHTTPResult`，仅传输失败抛出类型化错误；百炼和本地兼容服务继续通过聊天适配器保留既有 payload、解析与稳定错误映射。TDD 先以缺失共享模块的契约测试记录 RED，再完成最小实现并回归。
- 2026-09-13：Task 1.5 审查修复：JSON 解析改为严格从受限响应 bytes 解码，诊断用 `body_text` 仍可替换解码；非法 UTF-8 与深嵌套 `RecursionError` 均只令 `json_body=None`。新增回归证明 HTTP 200 的深嵌套体稳定映射 `invalid_analysis_response`、HTTP 401 仍优先映射 `authentication_failed`，以及替换解码后看似有效的 JSON 仍被拒绝。RED：`3 failed`；GREEN：同一集 `3 passed`，计划定向 `48 passed`，完整后端 `776 passed, 8 skipped`，`git diff --check` 与 `compileall` 通过。
- 2026-09-13：Task 2 完成 OpenAI `gpt-5.6-luna` 与 Grok `grok-4.6` 固定 Responses 适配器：端点、Bearer、严格目录、`store:false`、真实 MIME data URL、`input_text`/`input_image`、`text.format` JSON Schema 和 `message/output_text` 遍历均固定在适配器/协议层；空、拒绝、未完成或非结构化响应使用核心 `invalid_analysis_response`。OpenAI 旧三参数契约改由显式 `analyze_legacy` 保留，既有遗留契约测试随之调用该入口。两家探针都走 `connection_test_request(model)` 与既有 `validate_or_repair` 单次修复/严格结构校验路径；共享探针已改用完整图片分析提示。
- 2026-09-13：Task 3 完成 Claude `claude-sonnet-5` 固定 Messages 适配器：核心 `analyze(ProviderRequest)` 复用强化 HTTP 边界，使用 `x-api-key`、`anthropic-version`、`output_config.format` 和真实 PNG/JPEG base64 image source；repair 仅发送完整文本。Claude wire schema 深拷贝后移除官方不支持的约束、补足每个对象的 `additionalProperties:false`，不改领域 schema。响应仅拼接 `text` 内容块，跳过 thinking/signature 等非文本块，并拒绝拒绝、截断、空文本和畸形响应。探针发送固定 PNG，并经 `validate_or_repair` 的一次文本修复和严格结构校验；未注册主工厂。遗留三参数入口显式重命名为 `analyze_legacy`。
- 2026-09-13：Task 4 完成 Gemini `gemini-2.5-flash` 固定 GenerateContent 适配器：核心 `analyze(ProviderRequest)` 使用目录白名单与安全 URL 组成、官方 `x-goog-api-key` Header、真实 PNG/JPEG `inlineData` Base64、完整提示与媒体上下文、`generationConfig.responseMimeType`/`responseJsonSchema`。出站 schema 深拷贝后内联本地引用、将可空对象改为 Gemini 方言的类型数组并移除不支持约束，领域 `StructuredVisualAnalysis` 仍由本地最终严格校验。响应仅收集 `thought` 以外的文本，拒绝空候选、安全阻断、非 `STOP`、thought-only 与畸形响应；HTTP/网络/超时均映射稳定且不回显正文。探针发送固定 PNG，并通过完整提示、一次纯文本修复和严格验证；遗留三参数入口显式保留为 `analyze_legacy`。未注册主工厂，未使用真实密钥，状态保持 `unverified`。
- 2026-09-13：Task 5 完成豆包 `doubao-seed-2-0-lite-260428` 固定 Ark Responses 适配器：核心 `analyze(ProviderRequest)` 与 `test_connection(model=None)` 使用固定 `https://ark.cn-beijing.volces.com/api/v3/responses`、目录白名单、Bearer、共享强化传输、`store:false`、真实 PNG/JPEG data URL、`input_text`/`input_image`、完整 `text.format` JSON Schema 与 `message/output_text` 遍历。探针只发送 16×16 内置 PNG，并通过正常的单次修复和严格结构校验路径；HTTP/传输/解析错误使用既有稳定错误码且不回显供应商正文。旧 endpoint binding 继续位于第二个位置参数，核心 credential 强制关键字传入，旧三参数实现保留为 `analyze_legacy`；未注册主工厂。方舟 260428 的图片/结构化组合仍是协议兼容推断，未使用真实密钥或伪造冒烟，目录状态保持 `unverified`，未改用 260215。
- 2026-09-13：Task 6 注册七家默认工厂；每次创建均取得独立强化 HTTP client。设置保存非敏感 `configurationRevision`、`catalogVersion` 和验证状态，旧设置读入时稳定迁移为 `unverified`；密钥、模型、回环地址或目录版本变化使验证失效，同配置不会无意义改修订。连接测试捕获修订/目录版本并以 CAS 写回成功或稳定失败码，原始供应商响应不持久化。前端直接使用 API 目录标签和模型清单，显示“已配置”与“未验证/可用/验证失败”两个维度。新增脚本仅调用 UI 所用的本地 connection-test API。Gemini wire-schema 与豆包一次修复测试已补齐。未执行真实供应商冒烟：当前工作环境没有运行中、已配置的本地 API/密钥；所有云端状态仍应保持 `unverified`。
- 2026-09-14：Task 6 审查修复完成。Ruling: 配置 API 的凭据写入与设置提交不可构成跨系统原子事务；在同一设置文件内，`commit()` 必须以最新同身份记录合并验证字段，并向调用方返回最终持久化记录，避免两阶段快照覆盖并发验证或让响应与磁盘状态分叉。代价：若以后引入多个进程写同一设置文件，需要增加跨进程锁；当前线程模型由测试覆盖。冒烟入口先 GET 本地回环 API 的目标配置快照，随后 POST UI 相同的连接测试；仅在 `available` 且 model、revision、catalogVersion 均匹配时退出 0，响应限 64 KiB 且禁重定向。前端统一使用 API label、缺失时回退 provider id，云端受控 select/本地自填与已存密钥复用均有回归。独立审查 `task-6-review.md`：Spec PASS / Quality PASS；Task 6: complete（审查修复）。
- 2026-09-14：最终验收：`PYTHONPATH=backend backend/.venv/bin/python -m pytest -q backend/tests` → `880 passed, 8 skipped`（仅 Starlette 弃用警告）；`npm --prefix frontend test` → `11 files / 128 tests passed`；`node frontend/scripts/verify-color-contrast.mjs` → PASS；`npm --prefix frontend run build` → PASS；`PYTHONPATH=backend backend/.venv/bin/python -m compileall -q backend/app` 与 `git diff --check` → PASS。未运行真实云端冒烟，原因是未提供/授权供应商密钥；模拟验证不写入 `available`。
- 2026-09-14：外部复审补充发现跨供应商 prepared 快照回滚：旧 OpenAI prepared 在 local 保存新 model/revision 后提交，会回写 local 旧身份，使旧验证 CAS 错误成功。Ruling: prepare 结果私有携带目标 provider、目标 prepare 前身份和当时 selectedProvider；commit 仅在目标身份仍匹配时应用候选，非目标项与更新后的选择一律保留当前值。新增确定性交错回归断言 local 新配置/选择保留且旧 revision CAS 为 False。RED 已记录于 `cross-provider-cas-report.md`；GREEN 相关 66 passed。最终重验：后端 `881 passed, 8 skipped`，前端 `11 files / 128 tests passed`，颜色对比、生产构建、compileall 与 diff check 均通过。
- 2026-09-14：继续修复同一目标 provider 的两阶段竞态。Ruling: 若 prepare 后该目标配置身份已变化，`commit()` 抛出显式冲突而不是静默保留当前配置；配置 API 需要回滚本请求刚写入的凭据，并返回 `409 analysis_provider_configuration_changed`，不得以 HTTP 200 声称保存成功。成功响应的 `selectedProvider` 以实际 `commit()` 返回的持久选择为准。新增 API/存储确定性交错回归覆盖凭据回滚、`available` 验证状态保留和并发选择项回显。RED/Green 细节见 `credential-commit-cas-report.md`；相关 `68 passed`，完整后端 `883 passed, 8 skipped`，前端 `11 files / 128 tests passed`，颜色对比、生产构建、compileall 与 diff check 均通过。
- 2026-09-14：继续修复两个完整配置请求的凭据回滚竞态。Ruling: 在单个 `create_app()` 实例内，用应用级 `asyncio.Lock` 串行化 PUT 的设置准备、凭据读取/写入、提交和失败回滚；JSON 与静态输入校验仍在锁外。确定性 ASGI 协程回归先占有该锁、并发提交 A/B 两个完整 PUT，再释放并断言两者均成功、最终 Key 与 revision 属于 B，且 B revision 可写入 `available`。不将独立凭据/设置存储伪装成跨进程事务，绕过 API 的设置竞争继续明确返回 CAS 冲突。RED/Green 细节见 `put-transaction-lock-report.md`；相关 `69 passed`，完整后端 `884 passed, 8 skipped`，前端 `11 files / 128 tests passed`，颜色对比、生产构建、compileall 与 diff check 均通过。
- 2026-09-14：修复 Python 3.9 的事件循环绑定问题。Ruling: `create_app()` 不构造 `asyncio.Lock`；完整配置事务改为应用局部 `threading.Lock` 包围的同步函数，异步端点在 JSON/静态校验后经 `run_in_threadpool` 调用，避免事件循环线程阻塞锁争用。多循环回归覆盖 `asyncio.run()` 销毁临时循环后重新创建应用并用 TestClient 保存；独立 TestClient 线程的 A/B 完整 PUT 回归覆盖竞争期间最终 Key、revision 与 `available` 都属于 B。CAS 409 语义保留；不扩大为跨进程/跨存储事务。RED/Green 细节见 `threadpool-transaction-lock-report.md`；相关 `70 passed`，完整后端 `885 passed, 8 skipped`，前端 `11 files / 128 tests passed`，颜色对比、生产构建、compileall 与 diff check 均通过。
- 2026-09-14：修复保存事务与读取端的混合快照。Ruling: 设置、凭据和 selected provider 通过同一配置事务锁内的小型同步 helper 一次读取；供应商列表在一次锁内完成，不嵌套非重入锁。connection-test、语义分析预检与后台 worker 复用该 helper；网络调用与 verification CAS 始终锁外。两条独立 TestClient 交错回归覆盖提交失败后探针不会用新 Key 把已回滚旧 revision 误标为 `available`，以及成功保存后探针等待并用新 Key/revision 返回 connected。详情见 `consistent-snapshot-report.md`；相关 `72 passed`，完整后端 `887 passed, 8 skipped`，前端 `11 files / 128 tests passed`，颜色对比、生产构建、compileall 与 diff check 均通过。
- 2026-09-14：修复百炼与本地 OpenAI 兼容服务探针将任意 HTTP 200 标为可用。Ruling: 两家 `test_connection()` 都以固定 PNG 探针先取得文本，再统一经过 `validate_or_repair()` 的严格 `StructuredVisualAnalysis` 校验；最多一次、纯文本的修复请求。实际 MockTransport 适配器回归覆盖无效文本在两次请求后稳定为 `invalid_analysis_response`、修复失败，以及修复成功返回结构化结果；修复 payload 不含图片或原始敏感文本。详情见 `probe-validate-repair-report.md`；相关 `72 passed`，完整后端 `891 passed, 8 skipped`，前端 `11 files / 128 tests passed`，颜色对比、生产构建、compileall 与 diff check 均通过。
