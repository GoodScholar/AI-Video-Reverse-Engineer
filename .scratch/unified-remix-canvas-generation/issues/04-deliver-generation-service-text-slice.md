# 04: 交付统一文本生成纵向切片

**What to build:** 以一个现有且可验证的文本 Provider 为首个适配器，建立 `GenerationService` 门面、适配器注册表和能力目录，从界面提交普通或结构化文本请求，持久化运行，读取流式结果，取消或超时，并将选中结果明确采用为画布文本、脚本版本或 `RemixPlan` 输入。

**Blocked by:** 03: 统一项目素材结果安装与版本失效传播

**Status:** resolved

## Scope

- 建立公共请求信封和文本专属参数，冻结模态、Provider、模型、配置、输入、披露、幂等键和调用来源。
- 能力目录声明流式、稳定引用、结构化 Schema、输出预算、取消和超时能力；未知能力不展示也不接受。
- 复用现有文本生成实现和凭据存储，满足统一契约后作为适配器保留。
- 明确表达完整、部分、不完整、失败、取消、超时、状态未知和计费未知。

## Source reuse prerequisite

- 先阅读现有文本生成、供应商配置、显式发送披露、流式响应、取消和脚本候选源码。
- 记录直接复用、必要适配和自行实现部分；不得为统一门面无故重写已验证实现。

## Acceptance criteria

- [x] 用户能从至少一个现有入口提交文本请求并看到 Provider、模型、外发文字和能力限制。
- [x] 普通流式结果按顺序持久化，刷新后可继续查看，断流明确标记不完整。
- [x] 结构化 Schema 和输出预算只在适配器声明支持时可用，非法组合在服务端拒绝。
- [x] 取消、超时、失败和未知状态不会静默切换 Provider 或自动重提。
- [x] 文本结果保持不可变，明确采用后才成为画布文本、脚本版本或 `RemixPlan` 输入。
- [x] Provider 凭据不进入前端、项目数据、日志或错误回显。

## Verification evidence

- 协议假服务覆盖流式分块、乱序/中断、超时、取消、非法参数、幂等和错误映射。
- 领域/API/UI 测试覆盖提交、状态、结果查看、采用和版本失效。
- 真正 Provider 的通过证据由票据 08 完成，本票不得用模拟结果宣称真实支持。

## Out of scope

- 图片、视频和 ComfyUI 适配器。
- 自动把生成文本写入任何领域对象。

## Comments

### 2026-10-01 第二轮复审修复与证据

- 按复审意见重建为受控 `GenerationAdapterRegistry`：注册项冻结 adapter id/version、模态、execute、能力解析器，并可选取消与结果校验；生产 `main.py` 显式注册 analysis 结构化文本 adapter。运行保存公共 `commonRunId`、`domainKind`、`inputSnapshot`、`inputFingerprint`、幂等键、attempt、logs 和共享公共状态；生成结论放入 `outcome/reason`。重启中的运行转可备份终态 `failed + outcome=unknown`，不自动重提。
- disclosure 仅允许 Provider、模型、完整外发 prompt、digest、briefRevision、accepted；schema 移入受控 `textParams`。结构化 schema、references、outputBudget、timeoutSeconds、stream 均逐项按版本化能力拒绝；canary 密钥字段不能进入项目记录或错误。首个 analysis adapter 仅声明 schema，其余能力为 false。
- fake generator 验证每个 sequence chunk 在下次 yield 前已原子写入固定 resultId；仅明确 done 才完成，缺口/中断保留已收文本并标记 incomplete/unknown。同步字符串只是单 chunk 非流响应。adapter 边界用 `_CandidateResponse` 校验结构化 AIGC 输出，非法结果不可采用。
- queued cancel 在 worker 调用 adapter 前终止；running cancel 使用 cancel_requested 后确认，迟到 chunk/完成不会覆盖终态。timeoutSeconds 由内部 deadline timer 自动进入 timed_out；网络/Provider 异常默认 billingUnknown=true。
- AIGC UI 确认提交前查询 capability catalog，展示完整外发文字与 schema/streaming/references/outputBudget/cancel/timeout；运行卡刷新后展示 Provider、模型、外发、outcome、计费未知和 partial chunks，支持手动刷新、自动轮询与已声明的取消。sessionStorage 加内存 pending key 保证未知响应重试复用同一幂等键。
- TDD 红灯：`PYTHONPATH=. .venv/bin/pytest -q tests/test_generation_service_review.py` 初始因不存在 registry 类型收集失败；运行卡 Vitest 初始找不到外发文字/能力/刷新按钮。修复后后端定向、前端定向、构建与 acceptance 校验通过。正式矩阵未写入通过；真实 Provider E3/E4 仍为票据 08。

### 2026-10-01 最终复审修复

- 协议未知与远端取消失败统一收敛为可备份的 `failed + outcome=unknown + billingUnknown=true`，不再把顶层 `unknown` 留作活动任务；重启恢复同步把 pending/running 输出项标为 failed，避免运行与结果状态分叉或被备份活动任务检查误判。AIGC 自动轮询一次读取失败后保留错误提示并按原间隔继续读取，后续成功即可恢复状态显示。
