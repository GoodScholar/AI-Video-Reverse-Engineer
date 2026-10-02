# 受信验收证据桥设计规格

Status: proposed-for-review
Type: architectural-design
Decision date: 2026-10-02
Subsystem: Trusted Acceptance Evidence Bridge（受信验收证据桥）

## 1. 背景与目的

`.scratch/unified-remix-canvas-generation/acceptance/` 已由票据 01 冻结五张验收矩阵、三条金标记录模板、四种合法状态和 E1–E4 最低证据门槛：

- A01–A18、R01–R40、C01–C35、G01–G27、X01–X18，共 138 项；
- 状态只能是“通过 / 失败 / 阻塞 / 明确不适用”；
- 黑边、越界时码、错误素材身份、未披露外发和未经批准交付是零容忍错误；
- `matrix-contract.json` 冻结日期为 `2026-09-30`，冻结时不适用清单为空；
- E1 为固定源码、提交、源文件和许可证映射，E2 为确定性自动化行为测试，E3 为真实平台、真实 Provider 或真实媒体运行记录，E4 为人工视觉、内容、权利或交付验收。

当前 `validate_acceptance.py` 的 `PRODUCTION_EVIDENCE_VERIFIERS` 故意为空。现有结构可以拒绝自填证据，但尚不能把真正受信的 E1–E4 结果登记并安全晋升到矩阵状态。票据 23 已有源码核查说明、自动化测试记录和浏览器操作候选材料，但 `23-align-toonflow-canvas-shell-and-persistence.md` 明确说明这些材料尚无正式受信验证器，因此 C01–C03 仍为“阻塞”。

本子系统的目的，是在不建立第二套业务状态的前提下，为正式验收提供一条可审计、可重放、默认拒绝的证据链：

1. 用静态注册表定义哪些验证器、命令、来源、签名者和票据范围受信；
2. 让证据目录只保存不可变引用，不接受实现者自填的等级、退出码、人工结论或矩阵结果作为事实；
3. 分离“采集”“登记”和“晋升/重对账”，使登记材料本身永远不能改变矩阵状态；
4. 在晋升时实际核对 Git 与许可证、实际重放 allowlist 测试、直接查询既有业务真相源或验证仓库外私钥签名；
5. 在证据过期、撤销、不可访问或遭篡改时 fail closed，并把已有“通过”降回非通过状态。

成功标准是：任一矩阵项只有在其冻结最低证据组合全部由受信验证器本轮验证成功时才可标记“通过”；任何人仅编辑 `evidence-catalog.json`、证据附件、命令输出或状态覆盖，都不能完成晋升。

## 2. 非目标与冻结边界

本设计不改变下列既有决定：

- 不改变 138 项分母、编号、说明、最低证据组合、四种状态、零容忍错误和冻结时不适用基线。
- 不修改 `.scratch/unified-remix-canvas-generation/spec.md` 所定义的 `RemixEngine`、`CanvasEngine`、`GenerationService` 职责。
- 不建立第二套内容项目、项目素材、运行、编辑序列、预览、审核、批准或交付状态。
- 不把验收目录变成业务数据库；`project://`、`asset://`、`run://`、`review://`、`delivery://`、`source://` 和 `provider-job://` 仍指向既有业务真相源。
- 不改变产品逻辑、Provider 路由、画布持久化、混剪计划、生成运行或交付门禁。
- 不让静态截图、旧日志、测试文件存在、接口 2xx、Issue 状态或规格文本单独构成通过证据。
- 不在本规格阶段实现桥接层，不调用实施计划流程，也不改变 Ticket 23 的当前正式状态。

受信证据桥只拥有验收基础设施中的三类数据：静态受信定义、不可变证据指针和从验证结果投影出的矩阵状态覆盖。它不拥有被验收业务对象。

## 3. 信任模型

### 3.1 威胁模型

桥接层必须防止以下错误或滥用：

- 实现者把自写 JSON、命令字符串、`exitCode: 0`、测试名称、截图或任意业务 ID 登记为“通过”；
- 目录条目把未获允许的验证器、测试命令、矩阵项或来源类型注入晋升流程；
- 测试在带有未提交改动的工作区运行，却被描述为某个固定提交的结果；
- E3 附件复制业务状态后与真实业务状态分叉；
- 人工审核者身份只是自由文本，或仓库内保存可用于伪造审核的私钥；
- 已通过证据后来被修改、删除、撤销、过期或变得不可访问，但矩阵仍保持“通过”；
- 一个票据 profile 越权晋升其主责范围以外的支持项。

桥接层不承诺抵抗能够同时重写受保护 Git 历史、替换受信验证器代码、修改受信公钥并取得仓库治理权限的攻击者。受保护分支、代码审查和仓库历史完整性属于外部治理信任边界。E4 额外使用仓库外私钥，使普通实现者即使能够准备代码和候选证据，也不能仅靠自填人工结论完成验收。

### 3.2 角色

| 角色 | 可做 | 不可做 |
| --- | --- | --- |
| 实现者 / 证据采集者 | 固定实现提交，运行采集命令，准备脱敏附件，请求登记 | 自定义正式验证命令、填写受信结果、用自己的候选材料直接改成“通过” |
| 注册表维护者 | 通过正常代码审查新增验证器定义、suite allowlist、票据 profile 和受信公钥 | 在同一操作中伪造验证输出或绕过最低证据门槛 |
| 证据登记者 | 把已存在的不可变对象引用追加到目录 | 修改矩阵状态、改变 profile 的等级或支持范围、覆盖既有目录项 |
| 人工审核者 | 使用仓库外 SSH 私钥签署规范化 E4 审核声明 | 把私钥、口令或代理套接字写入仓库；为未授权范围签名 |
| 晋升 / 重对账执行者 | 调用固定验证器，汇总结果并原子更新既有矩阵状态投影 | 接受目录中的自报结果、跳过失败验证器、删除冻结分母 |

同一个自然人可以承担实现和登记工作，但 Ticket 23 的 E4 接受签名必须来自 profile 授权的独立审核身份。该约束以受信公钥 principal 和 profile scope 校验，不依赖目录中的 `reviewer` 自由文本。

### 3.3 信任根

信任根由以下对象组成：

1. 受保护 Git 历史中的桥接层代码、静态注册表和票据 profile；
2. E1 注册表中的仓库身份、完整 40 位提交 SHA、源文件路径、许可证路径与预期许可证事实；
3. E2 注册表中的字面 argv、工作目录、测试选择、允许环境和超时；
4. E3 adapter 代码对既有业务真相源的直接读取；
5. 仓库内的受信 E4 公钥、principal、指纹、角色与 scope；
6. 仓库外、由审核者保管的 SSH 私钥。

私钥、私钥路径、私钥口令和 SSH agent 凭据都不属于仓库数据。

## 4. 总体架构

桥接层采用“通用 E1–E4 验证器注册表 + 少量票据 profile”的方案 B。

```text
候选源码 / 测试 / 业务运行 / 人工审核
                │
                ▼
         Collector（采集）
                │ 生成内容寻址对象或稳定业务引用
                ▼
         Registrar（登记）
                │ 只追加不可变指针，不写结果
                ▼
 evidence-catalog.json + evidence/sha256/
                │
                ▼
 Promotion / Reconciliation（晋升 / 重对账）
                │
                ├─ E1 Git + license verifier
                ├─ E2 allowlist suite replay
                ├─ E3 truth-source adapter query
                └─ E4 SSH signed review verifier
                │
                ▼
 matrix-contract.json / statusPolicy.overrides
```

建议的实现落点均位于现有验收目录，不创建新的产品服务：

- `.scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py`：命令入口；
- `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/`：模型、注册表加载、聚合和 E1–E4 验证器；
- `.scratch/unified-remix-canvas-generation/acceptance/trusted-registry.json`：通用静态验证器与 suite 定义；
- `.scratch/unified-remix-canvas-generation/acceptance/profiles/ticket-23.json`：首个票据 profile；
- `.scratch/unified-remix-canvas-generation/acceptance/trust/e4-allowed-signers`：OpenSSH allowed-signers 格式的受信公钥；
- `.scratch/unified-remix-canvas-generation/acceptance/trust/reviewer-policy.json`：principal、指纹、角色和 profile scope；
- `.scratch/unified-remix-canvas-generation/acceptance/trust/revocations.json`：受信密钥或证据引用的撤销记录；
- `.scratch/unified-remix-canvas-generation/acceptance/evidence/sha256/`：内容寻址的脱敏证据对象；
- 现有 `validate_acceptance.py`：继续作为正式只读校验与差异入口，并调用桥接层完成正式证据验证。

目录和类名是后续实现的稳定边界；内部文件可在实施计划中按模块大小细分，但不得把验证逻辑重新塞成目录数据可控制的任意命令执行器。

## 5. 核心接口与数据结构

### 5.1 `Verifier`

所有等级实现相同的只读接口：

```python
class Verifier(Protocol):
    verifier_id: str
    level: Literal["E1", "E2", "E3", "E4"]

    def verify(
        self,
        pointer: EvidencePointer,
        profile: AcceptanceProfile,
        context: VerificationContext,
    ) -> VerificationResult: ...
```

`VerificationResult` 只由验证器在运行时生成，包含 `valid`、稳定错误码、已验证事实摘要、观察时间、输入摘要和零容忍违规；它不从 catalog 反序列化，也不能由 catalog 覆盖。为审计保存的 receipt 只是内容寻址附件，后续晋升仍重新执行或重新查询验证器。

### 5.2 `EvidencePointer`

`evidence-catalog.json` 升级后，每条记录只允许以下语义：

```json
{
  "id": "ticket-23-e2-canvas-persistence-<digest-prefix>",
  "profileId": "ticket-23/v1",
  "verifierId": "e2.ticket23.canvas-persistence.v1",
  "subjectRef": "git://product@<40-char-commit>",
  "artifactRef": "sha256:<64-char-digest>",
  "registeredAt": "<RFC3339 timestamp>"
}
```

约束如下：

- `profileId` 和 `verifierId` 必须已经存在于受信静态定义中；
- 等级、kind、支持矩阵项、测试命令、审核结论和状态不由 catalog 提供；它们由注册表、profile 和验证结果决定；
- `subjectRef` 必须是 profile 允许的不可变主体，首期为完整 Git commit；
- `artifactRef` 必须是 SHA-256 内容地址，对象位于验收目录的派生路径；
- 目录是 append-only；相同 `id` 只能幂等重放完全相同内容，不允许覆盖；
- 纠错通过新增指针和撤销旧指针完成，不改写旧条目；
- 路径、URL、命令、状态或业务对象快照不能替代内容摘要与受信解析规则。

`registeredAt` 只用于审计登记时间，不参与证明能力已通过。

### 5.3 `AcceptanceProfile`

票据 profile 是少量、静态、受代码审查的策略，不是每张票据都复制一套验证代码。它定义：

- profile ID 与版本；
- 精确主体提交策略；
- 可负责晋升的矩阵项；
- 每个矩阵项需要的 verifier ID；
- E4 允许的 reviewer role/principal scope；
- 证据有效期或主体变化失效规则；
- 零容忍检查与失败映射；
- 不能被本 profile 晋升的 supporting ranges。

profile 不保存执行结果、人工决定、业务状态或“当前已通过”标记。

### 5.4 `EvidenceRegistrar`

`register(pointer)` 只完成结构校验、摘要存在性校验、profile/verifier 配对校验和 append-only 写入。它不运行晋升、不修改 `matrix-contract.json`，也不根据附件内容推断通过。

### 5.5 `PromotionService` 与 `ReconciliationService`

`promote(profile_id, subject_ref)` 对 profile 主责项执行一次完整验证；`reconcile()` 对所有当前“通过”项及其引用重新验证。两者共用同一聚合器：

1. 从 `matrix-contract.json` 读取冻结最低证据组合；
2. 从 profile 解析每项允许的 verifier；
3. 从 catalog 选择与 profile、verifier 和精确主体匹配的不可变指针；
4. 执行验证器，不接受缓存结果作为唯一依据；
5. 按矩阵项聚合 E1–E4 和零容忍结果；
6. 在全部要求满足时写“通过”；
7. 在受信检查证明能力行为失败或出现零容忍错误时写“失败”；
8. 在证据缺失、过期、撤销、不可访问、篡改或基础设施错误时写“阻塞”；
9. 使用临时文件、fsync 和原子替换更新现有 `statusPolicy.overrides`；验证中断时不写部分结果。

`reconcile()` 允许把既有“通过”降为“阻塞”或“失败”，但不改变冻结分母，也不产生“明确不适用”。“明确不适用”继续只服从 `2026-09-30` 冻结基线；本子系统无权新增。

## 6. E1–E4 验证设计

### 6.1 E1：固定 Git 与许可证事实

E1 验证器从静态注册表取得预期事实，而不是从证据附件取得：

- 标准化仓库身份或允许的 remote URL；
- 完整提交 SHA；
- 要核对的源文件路径与 blob 摘要；
- 许可证路径、许可证类型和许可证文件摘要；
- 复用类型（直接复制、算法移植、结构适配或仅参考）；
- 本项目适配落点与归属/许可声明要求。

验证器通过 Git object database 读取提交、树和 blob；工作区文件、未跟踪文件和未提交差异不能成为 E1。仓库、提交或 blob 不可访问时为“阻塞”；提交、路径、blob、许可证或归属与静态预期不一致时为 E1 验证失败。

Ticket 23 需要同时核对：

- Toonflow 上游仓库固定提交 `d86cce6b689916ab73f75bc4c1f42a2a7424516c` 与 MIT 许可证；
- `23-align-toonflow-canvas-shell-and-persistence.md` 已列出的上游页面、画布控制、菜单、搜索、选择工具、节点骨架和提示输入源码范围；
- 本项目 `backend/app/creative_canvas.py`、`backend/app/creative_canvas_api.py`、`frontend/src/CreativeCanvas.tsx`、`frontend/src/CreativeCanvasNodes.tsx`、`frontend/src/creativeCanvas.css`、`frontend/src/creativeCanvasApi.ts` 等实际适配文件在一个固定产品提交中的 blob；
- 直接算法复用 `frontend/src/creativeCanvasConnection.ts` 的归属说明。

当前上述主要实现文件在工作树中仍是未跟踪文件，`vendor/Toonflow/` 也不是固定上游源码 checkout。它们不能以当前状态通过 E1；后续必须先形成不夹带其他票据范围、可审查的产品提交，并提供可读取的固定上游 Git 对象。

### 6.2 E2：allowlist suite 实际重放

E2 注册表保存字面 argv 数组、固定工作目录、精确测试选择、允许环境变量、超时、依赖锁文件摘要和预期主体提交。catalog 不能提供 shell 字符串或追加参数。

执行规则：

- 在精确 `subjectRef` 的干净隔离 checkout 中运行，不使用当前脏工作树；
- 直接以 argv 启动进程，不经 shell 解析；
- 默认清空环境，只注入 suite allowlist 明确需要且不含秘密的变量；
- 捕获退出码、测试列表、标准输出/错误、工具版本、锁文件摘要和时长；
- 对输出先脱敏，再生成规范化 receipt 和内容摘要；
- 实际退出非零、测试未收集、选择项缺失、依赖摘要不符、超时或进程被中断均不能提供 E2 通过；
- 只有 profile 静态映射的矩阵项可以消费该 suite 结果。

Ticket 23 的首期 allowlist 应拆成小型 suite，而不是把包含票据 24–28 行为的整个测试文件笼统算作 C01–C03 证据：

- 后端持久化 suite：从 `backend/tests/test_creative_canvas.py` 精确选择 `test_canvas_document_saves_without_shot_and_returns_project_assets`、`test_canvas_save_rejects_stale_document_revision`、`test_canvas_catalog_keeps_legacy_default_and_isolates_canvas_documents` 和 `test_soft_deleted_canvas_keeps_asset_references_and_can_restore`；
- 前端持久化 suite：从 `frontend/src/CreativeCanvas.test.tsx` 精确选择“首次读取画布期间禁止写入草稿”“保存冲突时保留本地草稿并显示重新读取入口”“保存中继续编辑会保留草稿并用返回的新 revision 进行下一次保存”“从画布目录切换时按 canvasId 加载独立工作区”和“保留本地草稿后切回会继续标记为未保存”；
- API suite：从 `frontend/src/creativeCanvasApi.test.ts` 精确选择“不传 canvasId 时保留旧画布请求 URL 与载荷”和“目录 CRUD 与所有画布工作区请求都显式携带 canvasId”；
- C02 结构 suite：在 `frontend/src/CreativeCanvasTicket23Acceptance.test.tsx` 增加一个只覆盖独立入口、顶栏、添加入口、基础节点、就近浮动工具、画布控制、按需编辑器及可访问打开/关闭路径的专用行为测试，避免借用后续票据测试扩大口径；
- 构建检查可作为辅助 suite，但不能单独证明 C01–C03。

上述测试选择以当前源码中的名称为基线；实施时必须把测试框架解析出的完整 node ID 原样写入静态注册表。一旦 profile 版本发布，测试重命名不会自动放宽选择，而会 fail closed，直到通过审查发布新 profile 版本。

### 6.3 E3：直接查询既有业务真相源

E3 不在 catalog 中接受运行快照。每类后续票据通过窄 adapter 查询现有真相源：

```python
class TruthSourceAdapter(Protocol):
    schemes: frozenset[str]

    def resolve(self, ref: str, policy: TruthQueryPolicy) -> CanonicalTruthRecord: ...
```

adapter 必须：

- 解析 profile 允许的稳定 URI scheme；
- 调用现有领域 store/service 或其只读接口，不复制业务对象到验收目录作为权威状态；
- 核对项目、素材、运行、输入快照、输出、审核或交付之间的身份关系；
- 只返回验收所需的最小规范化字段与摘要；
- 对来源不存在、版本不匹配、权限不足、服务不可达、状态未知或数据关系不完整返回“阻塞”或受信失败；
- 按 profile 的 freshness policy 核对观察时间；过期记录不得继续支持“通过”。

现有可供后续 adapter 核查的真实代码入口包括 `backend/app/main.py` 的项目读取、`backend/app/project_assets.py` 的项目素材读取、`backend/app/durable_runs.py` 的持久运行、各领域运行 store，以及既有审核/交付链。每个后续票据必须先确认它实际使用的真相源和稳定身份，再新增 adapter；不得预先建立通用影子数据库。

首两阶段不注册任何生产 E3 adapter。Ticket 23 的 C01–C03 冻结最低证据为 E1/E2/E4，不以虚构 E3 补位。

### 6.4 E4：仓库外 SSH 私钥签署的人工审核

首个 E4 实现使用 OpenSSH 签名；GPG 可作为未来新增 verifier，共用相同 attestation schema，但不是 Ticket 23 的前置实现。

人工审核声明使用规范 JSON：UTF-8、键排序、紧凑分隔符、禁止浮点数和重复键。负载至少包含：

- schema 与 profile 版本；
- 精确产品 `subjectRef`；
- 被审核矩阵项；
- reviewer principal；
- 审核时间与决定；
- profile 定义的 criteria ID 及逐项结论；
- 项目、画布、素材、审核记录或媒体的稳定引用；
- 操作步骤记录或录像等不可变附件摘要；
- 已知差异、零容忍检查和适用的有效期。

审核者在仓库外执行等价于以下语义的签名：

```text
ssh-keygen -Y sign -n trusted-acceptance-e4 -f <external-private-key> attestation.json
```

验证器使用 `trust/e4-allowed-signers`、固定 namespace `trusted-acceptance-e4`、principal、指纹和 `reviewer-policy.json` 的 scope 执行 `ssh-keygen -Y verify`。仓库内只保存公钥、指纹、授权策略、签名文件和已签名负载，不保存私钥或私钥访问方式。

以下任一情况都使 E4 无效：签名错误、签名者不在 allowlist、principal 或 scope 不匹配、主体提交不一致、附件摘要不一致、criteria 不完整、接受决定已过期、签名或密钥被撤销。自由文本姓名不能替代密钥身份。

## 7. 采集、登记、晋升与重对账

### 7.1 采集

采集器按 verifier 类型生成内容寻址对象：

- E1：Git/许可证核查清单与验证 receipt；
- E2：实际重放的脱敏日志与规范 receipt；
- E3：稳定业务引用列表与查询 receipt，业务状态仍留在原真相源；
- E4：规范 attestation、分离签名和操作步骤/录像摘要。

采集失败不写 catalog，也不改变矩阵状态。

### 7.2 登记

`register` 接受一个现有内容摘要、profile、verifier 和主体引用，核对对象确实存在且摘要匹配，然后 append-only 写入 catalog。它必须在输出中明确“已登记，不代表通过”。

登记和晋升必须是两个独立命令与两个独立代码路径；禁止 `register --promote` 一类组合开关。

### 7.3 晋升

`promote` 是显式动作，只处理一个 profile 和一个精确主体提交。它实际执行相应验证器，并逐项核对 `matrix-contract.json` 的最低等级。Ticket 23 profile 只能晋升主责范围 C01–C03；A14、A17–A18、C10、C33、C35 虽是其支持范围，也必须由各自主责票据/profile 完成后才可晋升。

同一 profile 的不同矩阵项可独立得到结果。例如 C01 全部要求满足而 C03 的冲突场景人工 criteria 未接受时，C01 可通过，C03 保持阻塞或失败；不能把票据整体状态当成矩阵结果。

### 7.4 重对账

`reconcile` 至少在以下时机显式运行：

- 受信注册表、profile、公钥、撤销表或 validator 发生变化后；
- E3 freshness 到期后；
- 交付前的完整矩阵审计。

重对账重新验证所有当前通过项。若验证器不可运行或证据不可访问，状态降为“阻塞”，并记录稳定错误码和差异；若受信结果证明行为失败或出现零容忍错误，状态改为“失败”。重对账不删除 catalog 历史。正式 `validate` 使用同一聚合器做只读检查：如果状态投影已经失效，它返回非零并要求执行 `reconcile`，但自身不写文件。

## 8. 过期、撤销、篡改与并发

### 8.1 过期

- E1 绑定不可变提交与 blob，默认无时间有效期；对象不可读取或静态策略撤销时失效。
- E2 每次晋升/重对账实际重放，因此不存在仅靠旧成功日志续期；主体、suite、锁文件或工具策略变化必须发布新 profile 版本。
- E3 使用 profile 明确的 freshness window；超期即阻塞。
- E4 绑定精确主体提交。主体代码变化自动失效；profile 还可要求审核时间有效期。Ticket 23 首个 profile 不使用纯日历过期，而以主体提交、criteria 版本、签名和撤销状态为有效边界。

### 8.2 撤销

`trust/revocations.json` 可以撤销公钥指纹、签名摘要或证据摘要，记录生效时间和理由。撤销是验收治理数据，不是产品业务状态。被撤销对象不能用于新晋升；已通过项在下一次重对账时降级。密钥因疑似泄露而撤销时默认追溯使其全部签名无效，除非撤销记录由验收治理明确限定生效后的签名。

### 8.3 篡改

任何内容对象、签名负载、receipt 或附件的实际 SHA-256 与引用不符，都返回 `DIGEST_MISMATCH`，不尝试“修复”目录或重新计算后继续。E4 还必须同时通过内容摘要和密码学签名两层校验。

### 8.4 并发

登记使用 compare-and-swap 或文件锁加原子替换；并发追加不得丢失既有条目。晋升开始时读取 contract/catalog/profile 快照并计算摘要，提交状态覆盖前再次核对摘要；期间文件变化则以 `ACCEPTANCE_STATE_CHANGED` 中止，不写部分结果。

## 9. 错误语义与敏感信息

桥接层使用稳定机器错误码和简短中文说明。至少区分：

- `POINTER_INVALID`、`ARTIFACT_MISSING`、`DIGEST_MISMATCH`；
- `PROFILE_NOT_FOUND`、`VERIFIER_NOT_REGISTERED`、`PROFILE_SCOPE_MISMATCH`；
- `SOURCE_REF_UNREACHABLE`、`GIT_FACT_MISMATCH`、`LICENSE_MISMATCH`；
- `SUITE_NOT_ALLOWLISTED`、`TEST_SELECTION_MISSING`、`TEST_REPLAY_FAILED`、`TEST_REPLAY_TIMEOUT`；
- `TRUTH_SOURCE_UNAVAILABLE`、`TRUTH_SOURCE_STALE`、`TRUTH_RELATION_MISMATCH`；
- `SIGNER_UNTRUSTED`、`SIGNATURE_INVALID`、`ATTESTATION_SCOPE_MISMATCH`；
- `EVIDENCE_EXPIRED`、`EVIDENCE_REVOKED`、`ACCEPTANCE_STATE_CHANGED`；
- `ZERO_TOLERANCE_VIOLATION`。

日志和 receipt 必须遵守：

- 不记录 Provider key、Cookie、token、私钥路径、agent socket、认证头或完整环境；
- E2 子进程使用最小环境，输出在落盘前按项目秘密模式脱敏；
- E3 只保存稳定 ID、版本/摘要和验收所需字段，不复制素材正文、用户会话或业务对象全量快照；
- E4 principal、公钥和指纹可公开审计，但私钥永不进入仓库、日志或命令回显；
- 无法确认脱敏成功时不登记附件，并让对应证据保持阻塞。

## 10. Ticket 23 首个 profile

### 10.1 主体与范围

profile ID 为 `ticket-23/v1`，只拥有 C01–C03：

- C01：独立全幅画布入口不依赖制作镜头且保留项目内容；
- C02：Toonflow 固定版本主体结构对齐；
- C03：创建、命名、切换、保存、恢复、并发冲突和草稿保护完整。

按照 `matrix-contract.json`，三项都必须同时具有 E1、E2、E4。profile 不为它们要求 E3，也不晋升票据 23 的 supporting ranges。

### 10.2 E1 要求

- 核验 Toonflow 固定提交与 MIT 许可证；
- 核验票据中列出的上游源文件 blob；
- 核验本项目画布 shell、节点、样式、API、持久化及直接算法适配文件位于精确产品提交；
- 核验归属与适配说明；
- 拒绝当前未跟踪实现文件、宿主仓库伪装的 `vendor/Toonflow/` HEAD 或只有 Issue 文本的声明。

### 10.3 E2 要求

- 使用第 6.2 节的小型 allowlist suite；
- 在精确产品提交的干净 checkout 中重放；
- C01 至少覆盖无 Shot 保存/恢复和独立入口契约；
- C02 的自动化部分覆盖主体控件可访问结构与打开/关闭路径，但视觉判断仍由 E4；
- C03 覆盖目录 CRUD、画布隔离、刷新恢复、revision 冲突、本地草稿保留和明确恢复/放弃路径。

### 10.4 E4 要求

Ticket 23 使用 SSH reviewer role `canvas-acceptance-reviewer`。签名 criteria 固定为：

1. 在 1280×900 固定视口从独立入口打开画布，确认无需 Shot；
2. 对照 Toonflow 固定提交，核查顶栏、添加入口、基础节点、就近浮动工具、画布控制和按需编辑器的主体结构及关键开关路径；
3. 创建并命名画布，加入文本节点和稳定项目素材节点，保存后刷新恢复同一内容与视图；
4. 创建并切换第二画布，确认文档隔离；
5. 在第二会话制造 stale revision，确认服务端拒绝覆盖、本地草稿保留，并可明确恢复或放弃；
6. 核查项目素材节点使用稳定素材身份且不强制归属 Shot；
7. 核查界面没有把后续生成、批量执行或 Agent 能力宣称为已验证；
8. 核查适用零容忍项，特别是错误素材身份和未经批准旁路。

已提交于 Git commit `6e27eb43c46559997189e865f90720e5510f244f` 的六张截图可以作为签名 attestation 的辅助附件，但不能替代受信签名、操作步骤记录或实际审核。现有票据文字中的“E1/E2/E4 candidate”不自动迁移为正式证据。

### 10.5 晋升条件

C01、C02、C03 各自只有在 profile 映射的 E1、E2、E4 全部成功、没有撤销/过期/篡改、没有零容忍违规时才晋升。E4 完成前三项继续保持“阻塞”，Ticket 23 也继续保持当前未 resolved 的正式结论。

## 11. 分阶段实施边界

### 阶段一：core + E1 + E2

交付：

- 核心模型、静态注册表、profile schema、内容寻址对象存储；
- 分离的采集、登记、晋升/重对账接口；
- E1 Git/许可证验证器；
- E2 allowlist suite 干净 checkout 重放器；
- 对 `validate_acceptance.py` 的兼容接入和合同测试。

阶段一不得：

- 把 Ticket 23 或任何需要 E4 的项标为通过；
- 注册不能读取真实来源或不能验签的空壳 E3/E4 验证器；
- 将旧测试日志或当前脏工作树测试结果迁移为受信 E2；
- 修改产品业务状态或产品逻辑。

### 阶段二：E4 + Ticket 23

交付：

- SSH 签名验证、受信公钥与 reviewer scope；
- Ticket 23 profile 的固定产品提交、E1 映射和精确 E2 suite；
- 按固定 criteria 完成的新一轮人工审核、签名、登记与晋升；
- 对 C01–C03 的独立状态结果和重对账验证。

阶段二只有在验收负责人提供受信 SSH 公钥、审核者持有仓库外私钥，且 Ticket 23 实现形成可审查固定提交后才能闭合。

### 阶段三：按后续票据增加 E3 adapter

仅当某张后续票据的冻结最低门槛包含 E3 时，读取该票据实际业务代码并新增窄 adapter 与 profile 映射。每个 adapter 独立核对既有项目、素材、运行、审核或交付关系；不预建统一业务镜像，不因未来可能需要而一次性迁移所有 store。

## 12. 迁移与兼容

- 现有 `validate`、`gaps` 和 `validate-sample` CLI 行为继续保留；正式 `validate` 增加对桥接层的只读验证，受控样例验证器仍严格隔离，不能进入生产 registry。
- 当前 `evidence-catalog.json` 为 version 1 且为空，可幂等升级到只含不可变指针的新版本；空目录升级不改变 138 项状态。
- 现有 `matrix-contract.json` 的矩阵、最低证据、状态、冻结日期和不适用基线原样保留，只由晋升/重对账写 `statusPolicy.overrides`。
- 既有证据附件、截图和票据评论不自动登记。只有能绑定不可变摘要、精确主体并通过对应 verifier 的材料才能作为新指针加入。
- 既有“通过”项若未来出现，升级后必须完成一次重对账；无法验证时降为“阻塞”，不沿用历史自报结果。
- `golden-records.json`、`ticket-coverage.json` 和三条金标的业务身份关系不迁入新状态模型；桥接层只读取其冻结定义。
- 兼容迁移失败时保留原文件，不覆盖原 contract/catalog，并以非零退出和稳定错误码报告。

## 13. 测试与验收

### 13.1 核心合同测试

- catalog 只能保存已注册 profile/verifier 的不可变指针；自填 level、kind、supports、exit code、decision 或 status 被拒绝；
- register 幂等、append-only，且前后 `matrix-contract.json` 字节不变；
- promote 在缺任一最低等级时不通过；supporting ranges 不能被越权晋升；
- reconcile 能把失效“通过”降为“阻塞”或“失败”，且不改变分母或新增不适用项；
- 并发目录更新和状态更新不会丢数据或写入部分结果。

### 13.2 E1 测试

- 临时 Git 仓库覆盖正确提交、错误 remote、缺失 commit、路径/blob 不符、许可证摘要不符；
- 未跟踪和未提交文件不能提供 E1；
- 上游对象不可访问时阻塞，不退回读取任意工作区文件；
- 票据 23 的上游与产品提交必须同时满足。

### 13.3 E2 测试

- 只执行注册表字面 argv；catalog 注入命令、参数、cwd 或环境被拒绝；
- 测试在干净固定提交运行，当前工作区改动不能影响结果；
- 覆盖成功、断言失败、零测试收集、选择缺失、超时、工具缺失和锁文件变化；
- receipt 经脱敏并内容寻址，修改日志导致摘要不符；
- Ticket 23 suite 只支持 C01–C03 的精确映射。

### 13.4 E3 adapter 合同测试

- 用受控假 adapter 验证 URI、稳定身份、freshness、不可达和关系不匹配语义；
- 生产 registry 在真实 adapter 实现前保持无 E3 条目；
- 后续每个生产 adapter 必须有对应真相源集成测试，证明它查询现有 store/service 而非验收附件。

### 13.5 E4 测试

- 测试专用临时密钥覆盖正确签名、错误 namespace、未知 principal、错误 scope、负载篡改、主体不符、过期和撤销；
- 仓库扫描确认不存在生产私钥、私钥口令或 agent 凭据；
- 测试密钥只存在于测试临时目录，不进入生产 allowed-signers；
- Ticket 23 criteria 缺任一项、附件摘要不匹配或 reviewer role 不符时不能晋升。

### 13.6 端到端验收

阶段一通过标准：在不改变任何正式矩阵结果的情况下，能够登记 E1/E2 指针、实际验证固定 Git/许可证、重放精确 suite，并证明手改 catalog 或旧日志不能晋升。

阶段二通过标准：以固定 Ticket 23 产品提交完成 E1、E2 和 SSH 签名 E4；`promote ticket-23/v1` 只按逐项结果更新 C01–C03；随后篡改附件、撤销签名或改变主体引用，`reconcile` 会让对应项 fail closed。现有 138 项分母和其他 135 项状态不受越权改变。

验证命令的具体字面 argv 由后续实施计划根据仓库当时可执行环境冻结；任何命令一旦进入 registry 即作为代码审查对象，不由运行者自由输入。

## 14. 明确不确定性与当前阻塞

以下事实不会阻碍本设计规格成立，但会阻碍相应实施阶段完成：

1. Ticket 23 的主要实现与测试文件当前仍未被 Git 跟踪，尚无可供 E1/E2/E4 共同绑定的产品 commit。阶段二必须先得到范围清晰的固定提交；不能把当前工作树状态当成主体。
2. `vendor/Toonflow/` 当前是归属目录而非固定上游源码 checkout。E1 实施需要可读取 Toonflow 固定 commit 的真实 Git object database，或等价的受信 Git bundle；宿主仓库 HEAD 不可替代。
3. 受信 SSH reviewer 的 principal、公钥与指纹尚未提供。阶段二必须由验收治理者建立 `canvas-acceptance-reviewer` 授权；规格和阶段一不需要私钥。
4. 当前没有生产 E3 adapter。本设计刻意不提前选择统一 adapter；每个后续票据按实际真相源实现，避免建立第二套业务状态。
5. commit `6e27eb43c46559997189e865f90720e5510f244f` 中的 Ticket 23 截图只有辅助价值，且现有测试记录来自非受信工作区。正式晋升需要在固定主体上重新采集、重放和签名。

这些不确定性都有确定的 fail-closed 结果：缺少固定产品提交或上游对象时 E1/E2 阻塞，缺少受信签名时 E4 阻塞，缺少后续真相源 adapter 时 E3 阻塞；任何一种情况都不能通过缩小分母或自填结果绕过。
