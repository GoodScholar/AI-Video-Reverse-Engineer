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

### 文件

- 修改：`.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/handoffs.py`、`.scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py`、`.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_handoffs.py`。
- 新增：`.scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_orchestrator.py`。
