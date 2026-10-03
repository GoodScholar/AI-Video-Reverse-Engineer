# Task 2 report — PARTIAL / NOT READY TO COMMIT

## 状态

补充授权已指定实施计划第 99–1244 行为精确接口来源，故此前的 `NEEDS_CONTEXT` 判断已撤销。当前已提交 slice A/B 的可移植 codec 与 durable store；Task 2 的后续 broker、transport、治理输入、CLI 和 golden 范围仍在实现中。

## 已解析的接口上下文

binding spec 没有重列下列类型；补充授权明确由实施计划第 99–1244 行提供其字段和 codec 定义：

- `AcceptanceOperationSnapshotV1` / `ReplayPolicyBindingV1` / `NamedDigest`
- `Ticket23ProductSourceHandoffV1` / `HandoffExportRequestV1`
- `RegistrationBrokerAuthKeyRegistrationV1` / `BrokerAuthLeaseIssueRequestV1`
- `RegistrationRecoveryLeaseIdentityPreimageV1`

`rg -F` 对上述八个类型在指定 binding spec 的结果均为 `0`；这不是冲突。§4、§6.1、§7、§9、§10、§14 仍作为语义和 fail-closed 不变量来源。

## 已完成的只读检查

- 已确认受管 worktree：`/Users/shen/.codex/worktrees/trusted-evidence-bridge/AI Video Reverse Engineer`，分支 `codex/trusted-evidence-bridge`，基线 `8447b73`。
- 已阅读简报、指定 binding spec 章节、现有 `models.py`、`registry.py`、`errors.py`、`storage.py`、`registration.py` 和既有 tests。
- 已确认 Task 1 的 BootstrapArtifactsV1 没有被作为 Task 2 依赖。

## RED / GREEN 证据

- 首批 RED：同一命令在新增测试后因 `ModuleNotFoundError: trusted_evidence.e2_policy` 收集失败，确认测试确实捕获缺失 codec/handoff 实现。
- 首批 GREEN：同一命令结果为 `16 passed in 0.02s`。
- Slice B RED：新增 store 测试后因 `ImportError: TrustedHandoffStoreV1` 失败。
- Slice B GREEN：`test_trusted_evidence_handoffs.py` 为 `5 passed in 0.02s`；提交 `1082645 feat: add durable handoff store`。
- 现有受影响回归：`test_trusted_evidence_models.py`、`test_trusted_evidence_registry.py`、`test_trusted_evidence_registration.py`。
  - 结果：`126 passed in 0.25s`。

## 已实现的变更

- `trusted_evidence/e2_policy.py`：严格 SHA-256 digest、`NamedDigest` 排序/唯一名、policy 与 product Git OID codec、`ReplayPolicyBindingV1`、`AcceptanceOperationSnapshotV1`。
- `trusted_evidence/handoffs.py`：product-source handoff record/pointer/source/view 的最小严格 canonical bytes 与 commit 一致性验证。
- 两个新测试文件：16 项行为测试，golden digest 由不导入被测模块的标准库 canonical JSON 计算。
- `TrustedHandoffStoreV1`：外部 0700 root、content-addressed blob、父目录 fsync、hard-link no-replace pointer 与 byte-identical retry。

## 内部切片 checklist

- [x] A — policy/OID/snapshot/product-source codec（`10bf135`）
- [x] B.1 — durable blob/pointer store（`1082645`）
- [ ] B.2 — export request、consumer lifecycle CLI、cleanup helper
- [ ] C — signing domains、transport/activation proof、orchestrator 五步
- [ ] D — output/transaction/lifecycle codecs
- [ ] E — broker-auth/lease/envelope portable codecs
- [ ] F — governance input、goldens、registry/errors、完整 matrix

## 尚未实现（禁止提交）

- broker-auth registry、lease issuance/state machine、capability envelope、FD closure、transaction contracts；
- export request、consumer registry/CLI、cleanup、transport orchestrator；
- 治理 JSON、两份 broker-auth golden vector、task-output policy；
- 简报要求的完整 RED 矩阵和最终完整回归。

不应将当前变更视为完整 Task 2 或用于后续 Task 14/18；它尚不具备所要求的 fail-closed runtime/transport 合同。

## D.1 — phase-aware output 与 pre-state codecs

### RED / GREEN

- RED：先以手工固定的 Task28/29 字面量、七个 candidate ref、以及 pre-state/staging/quarantine 反例扩展 `test_trusted_evidence_e2_policy_v2.py`，运行
  `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py`
  在收集阶段因 `ImportError: cannot import name 'AllowedTaskOutputsV1'` 失败，证明新断言命中缺失的 D.1 codec。
- GREEN（定向）：实现后，同一 policy 文件与受影响 handoff 文件的联合命令为 `61 passed in 11.27s`。
- 初次全量 acceptance（在最后一轮输入类型防御加固前）：
  `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance`
  为 `306 passed in 143.47s`。
- 最后一轮全量 acceptance（输入类型防御加固后）为 `305 passed, 1 failed in 223.38s`；失败项是未改动的 `test_trusted_evidence_e2.py::test_e2_kills_the_process_group_when_the_parent_exits_before_a_pipe_holding_child`。它在 codec 文件之外，失败为 `background.pid` 尚未出现即读取；单独复跑仍同样失败。D.1 定向 policy+handoff 回归保持 `61 passed`，因此该既有 E2 process-group 时序失败未在本切片内修改。
- 附加检查：`backend/.venv/bin/python -m py_compile .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/e2_policy.py` 与 `git diff --check` 均为 exit 0。

### 实现与覆盖

- `e2_policy.py` 新增严格不可变 codec：`SourceDirtyAllowanceV1`、`ExpectedTaskOutputV1`、`StagedCandidateIdentityV1`、`TaskOutputTransitionV1`、`AllowedTaskOutputsV1`、`RegistrationPreStatePathV1`、`TrustedRegistrationPreStateV1`、`StagingCandidateProvenanceV1`、`StagedCandidateSetV1`、`QuarantineObjectIdentityV1` 与 `QuarantineObjectSetV1`。
- 所有 root schema decode 都拒绝未知、缺失、重复、非 NFC、非 canonical JSON 与自摘要篡改；自摘要均按去除 `canonicalDigest` 的 canonical JSON 仅计算一次。`AllowedTaskOutputsV1` 强制 64 KiB 上限、Task28/29 phase 对应关系、七 ref 闭包、排序唯一性与 new/idempotent CAS transition 一一对应。
- Task28 只接受 catalog/audit/七个 evidence-CAS 的精确 transition；Task29 只接受 contract transition。source dirty 只可为原样的 unstaged added/modified/untracked；不存在 index/staged/rename 的可接受编码状态。
- pre-state、staging provenance 和 quarantine 记录逐字段绑定 Git base/ref、角色/分类、device/inode、type、owner/mode/link count、length、digest/ref；quarantine 恰为 output plan、expected audit、七 candidate 的 regular file 闭包。
- 新增的显式 `VisibleAuthorizationWriteRecorder` 在所有拒绝分支断言 `visible_authorization_writes == 0`；它观察实际 codec 调用结果，不以 mock 行为替代 codec 验证。

### 范围

- 此切片仅提供 portable codec；不包含 transaction、broker、真实文件 descriptor 读取、repository mutation 或 recovery 执行路径。Task 2 仍为 PARTIAL，D/E/F 的其余范围继续未完成。

## B.2 — handoff consumer lifecycle and cleanup

### RED / GREEN

- RED（先添加真实临时目录与 subprocess CLI 行为测试）：
  `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_handoffs.py`
  的收集阶段以 `ImportError: cannot import name 'HandoffRegistryV1'` 失败，证明新测试命中了缺失的生命周期实现。
- GREEN（focused）：同一命令在实现后为 `10 passed in 0.17s`。
- GREEN（受影响回归）：
  `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_models.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_registry.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_registration.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py`
  为 `139 passed in 0.24s`。
- 附加检查：`backend/.venv/bin/python -m py_compile .../handoffs.py .../trusted_handoff_store.py` 与 `git diff --check` 均为 exit 0。

### 实现与自审

- 新增严格 canonical JSON decoder、record/pointer/request/product-source full-bytes codec、绑定集合摘要，以及 `HandoffRegistryV1` 的 local publish、export-request、source-store fetch 和 cache import；所有闭包 ref 重新哈希，混用 request/record/pointer、缺 blob、重复 ref 与非 canonical JSON 都拒绝。
- CLI 的 `verify-root`、`publish-local`、`publish-export-request`、`verify-cache`、`fetch`、`import`、`finalize`、`read`、`read-product-subject` 与 `cleanup-owned-tree` 均已接到真实实现；Task 22/27 普通 producer class 仍拒绝。
- `finalize/read/read-product-subject` 在本切片仍要求 C 所有的 transport seal 与 ready marker，故明确 fail-closed，未引入未签名生产传输。
- cleanup 仅处理明确的、当前用户 0700 窄根；以 `dirfd`/`O_NOFOLLOW` 递归、持续 inode 复核、只为删除恢复 owner write bit，并 fsync 父目录。多根 shell 聚合由后续 harness 按计划调用此唯一入口。

### 文件与提交

- 修改：`.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/handoffs.py`、`.scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py`、`.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_handoffs.py`。
- Scoped commit：`feat: close trusted handoff consumer lifecycle`（最终 revision 见交付消息）。

## C.1 — signing domains and policy activation proof

### RED / GREEN

- RED（先新增固定 signer、完整 activation chain、跨域 identity collision 与 purpose/domain 反例）：
  `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_handoffs.py`
  在收集期以 `ImportError: cannot import name 'ActivatedPolicyExportRequestV1'` 失败，证明 C.1 的 codec/verifier 尚不存在。
- GREEN（focused + affected policy）：
  `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_handoffs.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py`
  为 `38 passed in 0.57s`。

### 实现与自审

- `handoffs.py` 增加严格 canonical codec：signing registration/domain、embedded bytes、activation marker/proof、policy handoff 和 activated export request；unknown/missing/duplicate/non-canonical JSON 均拒绝。
- 联合 loader 验证 transport、activation、broker-auth 的 key ID、raw digest/fingerprint、wrapper identity、resolved leaf digest/fingerprint 两两互斥，并闭合 broker allowed-signers 与 revocation；broker startup challenge 暂以明确 fail-closed stub 保留给 slice E。
- activation verifier 用固定 `policy-activation` domain + NUL + unsigned canonical bytes 做纯公钥 Ed25519 验证，重算 proof ref、三个 embedded bytes/digest、intent→marker→ACTIVATED 链、core/final-ref fields 和 transport proof binding；不读 journal、路径或私钥。
- 固定 RFC 8032 派生签名向量不调用被测实现生成 expected bytes/signature；篡改 embedded fields、commit/completion/signature，及跨域、缺/错 purpose/domain、revoked/empty/duplicate closure 都有真实 fail-closed 测试。
- 范围刻意不含 transport install/orchestrator 或 broker-auth 私钥/slot；product transport proof-null enforcement 属 C.2 transport schema。

### 文件与提交

- 修改：`.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/handoffs.py`、`.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_handoffs.py`。
- Scoped commit：`eb6b1ea feat: verify trusted policy activation proofs`。

## C.2 — authenticated product transport state machine

### RED / GREEN

- RED：新增两消费者、真实 0700 临时 source/target/stage 根与固定 Ed25519 signer 的 transport 测试后，
  `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_handoffs.py -k 'run_transport_creates_distinct'`
  因 `ModuleNotFoundError: tools.trusted_handoff_orchestrator` 失败；随后 CLI surface 测试因缺少 `upload/sign-seal/install-transport` 子命令失败。
- GREEN：同一 transport 测试通过；最终
  `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_handoffs.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py`
  为 `40 passed`。`py_compile` 三个实现文件与 `git diff --check` 均为 exit 0。

### 实现与验证

- 新增严格 canonical `TransportJobV1`（内存调用对象）、`HandoffTransferManifestV1`、`TransportSealV1`、`TransportReadyMarkerV1`；全量绑定 request/record/pointer/CAS closure、consumer/destination、nonce/sequence/expiry、key purpose/domain 及 manifest/seal digest。
- 新增 orchestrator 公共阶段与 CLI：`verify-signing-domains`、`verify-export-request`、`export`、`upload`、`sign-seal`、`install-transport`、`run-transport`。私钥只从 read-only regular-file FD 读取，拒绝 key path/value 接口、错误 FD 类型、可写 FD 或非 32-byte slot；签名和验签均固定 transport domain。
- 安装使用已有 temp + hard-link no-replace + fsync 发布原语，manifest/seal 先于 ready；same-byte retry 幂等、变更字节拒绝，且 replay marker 固定 consumer/destination/sequence/nonce/transport ID。消费者 fetch/import/finalize/read/read-product-subject 重验 ready→seal→manifest→CAS closure，finalize 前不可读。
- 真实路径测试覆盖两个消费者独立 transport ID/nonce/seal/ready、交叉 consumer seal 拒绝、同字节 retry、同 consumer replay 拒绝、ready 前不可读及 finalize 后仅返回 verified SHA-1 product subject；没有 mock 存在性断言。

### 已知边界

- 当前 durable `TrustedHandoffRecordV1` 仍是既有 product-source-only codec；因此本切片对 product transport 强制 proof fields 为 null。C.1 的 policy activation proof codec 已复用为后续 policy record/activated-request bridge 的验证基础，但完整 policy handoff durable closure 尚无现有 record/pointer codec，不能声称 policy transport 已闭环。

### Policy bridge follow-up

- RED：`test_policy_record_pointer_uses_general_git_oid_and_full_bytes_digest` 首次因缺少 `PolicyHandoffRecordV1` / `PolicyHandoffPointerV1` import 失败。
- GREEN：新增严格 policy record/pointer canonical codec，policy commit 只接受通用 `GitObjectIdV1`（SHA-1 或 SHA-256），pointer 只绑定完整 canonical record bytes 的 digest；固定 product-only `ProductGitObjectIdV1` 被拒绝。定向验证为 `2 passed`（policy codec 与 product transport 回归）。
- **仍未完成**：该 codec 尚未接入 `Ticket23PolicyHandoffV1` / `ActivatedPolicyExportRequestV1` 的 durable source closure、同一四阶段 transport 与消费者 proof 验证；因此 C.2 不能标记 complete，后续必须补成功 policy 全链与 proof/ref/digest 篡改矩阵。

### 文件

- 修改：`.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/handoffs.py`、`.scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py`、`.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_handoffs.py`。
- 新增：`.scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_orchestrator.py`。

## C.2b — policy transport bridge

### RED / GREEN

- RED：新增真实临时 store 的 policy transport 用例后，定向命令因 `ImportError: cannot import name 'run_policy_transport'` 失败，直接覆盖缺失 runner。
- GREEN：完整 policy source closure 经既有 `export -> upload -> sign-seal -> install-transport` 生成 seal/ready，并由 consumer 的 `fetch -> import -> finalize -> read` 离线复验。
- 篡改矩阵：activated request、policy handoff、activation proof、manifest、seal、ready 六个 durable 位置的破坏均在 `verify_transport_closure` 前失败；错误 destination 与同 ID 不同 nonce 的重试也拒绝。

### 实现与验证

- policy closure 重算 record/pointer/core request/proof/handoff/activated request 的 refs、full-byte digests、消费者与 activation signature/commit/HEAD/fixed-ref/completion/profile/toolchain/trusted-Git/trust-root 绑定。proof ref/digest 贯穿 handoff、activated request、job、manifest、seal、ready。
- policy `run-transport` 仅在 closure 验证后构造带 proof 的 `TransportJobV1`，复用 product 的四个 transport 阶段；consumer 在 finalize/read 前重验 ready、seal、manifest 和 policy activation closure。
- `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_handoffs.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py`：`49 passed in 10.89s`。
- `backend/.venv/bin/python -m py_compile .../handoffs.py .../trusted_handoff_store.py .../trusted_handoff_orchestrator.py` 与 `git diff --check`：exit 0。

## E.1 — broker-auth registry, sequence journal and lease identity

### RED / GREEN

- RED：在两个 Task 2 测试文件先加入固定 Ed25519 startup 与 lease-identity 向量、request → payload → entry → state 的闭合断言后，命令
  `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_handoffs.py`
  以缺少 `BrokerAuthLeaseIssueRequestV1` / `RegistrationBrokerAuthKeyRegistrationV1` 的 `ImportError` 失败。
- GREEN：同一命令为 `67 passed in 11.28s`；`py_compile trusted_evidence/e2_policy.py` 和 `git diff --check` 均为 exit 0。

### 实现与边界

- `e2_policy.py` 新增严格 broker-auth registry、revocation、pre-state-only lease request、typed issue/sign/rotate/revoke payload、连续 sequence journal/state、稳定 worker-family authorization、唯一 lease-identity preimage 与 recovery lease portable codec。u64、digest、canonical JSON、自摘要、state-MAC 形状、固定 event sequence、policy/head 闭合与 lease expiry/revocation 等号边界均在 codec 层拒绝。
- `RegistrationBrokerAuthPrivateKeySlotV1` 只可由既打开的只读 regular-file FD 加载 32-byte Ed25519 seed；它不可 copy/pickle/serialize，关闭后不可再签。startup challenge 使用固定 domain + NUL + canonical `{keyId, nonce, registryDigest}`，并在本地以解析后的 public key复验。测试采用固定 RFC 8032 seed、固定 nonce 与固定 128-char lower-hex signature。
- 只实现 E.1 的 portable/in-memory contract；没有实现 E.2/E.3 的 SCM_RIGHTS/envelope、Linux root broker、journal durable crash protocol、worker spawn 或 repository mutation runtime。Task 2 仍未完成。
- 完整 acceptance 本次实际结果：`311 passed, 1 failed in 253.00s`。失败仍是既有 `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py::test_e2_kills_the_process_group_when_the_parent_exits_before_a_pipe_holding_child`：测试读取尚未创建的 `background.pid`，与本切片无共享改动文件；未扩大修复范围。

### 范围

- 未进入 D/E/F；broker-auth 启动能力仍由 slice E 负责。

## D.2 — transaction, lifecycle and policy journal contracts

### RED / GREEN

- RED：先为 literal canonical pending-transition bytes、真实目录 FD、不可复制 capability、registration chain、lifecycle/completion proof 与完整 policy forward sequence 增加测试；运行
  `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py`
  在收集阶段以 `ImportError: cannot import name 'BrokerLifecycleProofV1'` 失败，确认新增断言命中缺失 D.2 contract。
- GREEN（Task 2 两个测试文件）：
  `... pytest -q test_trusted_evidence_e2_policy_v2.py test_trusted_evidence_handoffs.py` → `64 passed in 10.98s`。
- GREEN（受影响回归 + 编译 + diff 检查）：`py_compile e2_policy.py handoffs.py`、`git diff --check` 通过；models、registry、registration、policy 与 handoffs 五个测试文件合计 `190 passed in 11.22s`。
- 完整 acceptance（最终代码）：`308 passed, 1 failed in 214.74s`。失败是既有的 `test_trusted_evidence_e2.py::test_e2_kills_the_process_group_when_the_parent_exits_before_a_pipe_holding_child`，在未修改 E2 runner 的时序测试中读取尚未生成的 `background.pid`；此前 D.1 全量运行已记录同一失败，未在本切片扩大修复范围。

### 实现与边界

- `e2_policy.py` 增加 strict canonical codec：`RegistrationRepositoryRecoveryBindingV1`、`RegistrationTransactionIntentV1`、`RegistrationPendingTransitionV1`、`RegistrationJournalEntryV1`、`RegistrationTransactionStateV1`、`BrokerLifecycleProofV1`、`Task26CompletionPayloadV1`、`Task26CompletionRecordV1` 与 `PolicyPublishTransactionJournalV1`。自摘要只排除自身字段；decoder 拒绝未知、缺失、重复与非 canonical bytes。
- registration journal 固定 PREPARED 起点、全链 hash/intent/operation/catalog bindings 与前向 phase；visible catalog 只能处于 catalog-committing/activated，activated 必须已 parent-fsync。policy journal 仅接受固定六步 forward sequence，并复用 C.1 `PolicyActivationMarkerV1` 验证 ACTIVATE intent、marker bytes digest 与最终 entry 的完整绑定。
- `RegistrationTransactionCapabilityV1.open_precreated(...)` 仅接受四个已经打开的、互异目录 FD，复制并持有其 FD identity；没有 path/cwd/environment constructor 或 serialize/copy 恢复入口。没有实现 Linux broker、SCM_RIGHTS、repository mutation 或 fsync runtime，这些仍属于 Task 14。
- lifecycle proof 要求包含 `record-payload` 与完整有序 staged digest set；Task 26 record 只接受 broker-signature-shaped 的 `pass` payload 并重算 payload digest。签名验证和 durable write executor 仍待后续 broker/auth slice 提供，不以此 portable codec 冒充真实生成或持久化闭环。

### 文件

- 修改：`.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/e2_policy.py`、`.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py`。
- Scoped commit：本切片提交后在交付消息记录。
