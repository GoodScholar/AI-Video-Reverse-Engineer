# E2 v2 可信重放协议 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用内容寻址工具链、产品源码与依赖仓库、操作级 artifact barrier、不可逃逸 containment、确定性离线重放和 v2 receipt 取代已阻塞的 E2 v1，并装配 Ticket 23 冻结的最低 E1+E2+E4 组合。

**Architecture:** E2 仍分为阶段 A 全操作只读预检与阶段 B 可信重放：可变 catalog/contract 进入短生命周期 `AcceptanceOperationSnapshotV1`，稳定 suite/profile/toolchain/dependency/raw-Git-source/containment policy 进入 `ReplayPolicyBindingV1`，receipt 只绑定后者。runner 只消费已验证的规范 raw Git repository container，不接受宿主路径；containment 先冻结协议、消息和唯一 Linux 内核机制，再实现 backend。Ticket 23 的 E1/E4 是旧计划未执行的既有前置任务，仅为完成 C01–C03 profile 纳入，不改变 E2 v2 规格或三引擎职责。

**Tech Stack:** Python 3.13、pytest 8.3.5、pytest-json-report 1.5.0、Vitest 3.0.8、Node 24.20.0、npm 11.19.0、Git 2.53.0、uv 0.11.15、zstandard 0.23.0、规范 POSIX ustar + 单帧 zstd、Linux cgroup v2 + clone3 + seccomp user notification、Rust（锁定 Cargo.lock）、OpenSSH `ssh-keygen -Y`、现有 SHA-256 CAS 与 JSON 验收资产。

**Spec:** `docs/superpowers/specs/2026-10-02-e2-v2-trusted-replay-design.md`

## Global Constraints

- 历史计划 `docs/superpowers/plans/2026-10-02-trusted-acceptance-evidence-bridge.md` 保持不变；旧 Task 5 保持 `blocked / superseded`，旧 Task 6 未开始。
- 本轮修订基线为 `06827f2e439f84578d1e8b9d02346c1a75e10295`，不得修改已批准规格。审批前固定 commit `2f304fe25af5a3422613efbc3122ff290287c81d` 的规格 blob 与当前已批准 blob 只在第 3 行 `Status: proposed-for-review → approved` 不同；规范性正文机械验证为 `diff -u <(git show 2f304fe:docs/superpowers/specs/2026-10-02-e2-v2-trusted-replay-design.md | sed '3d') <(git show HEAD:docs/superpowers/specs/2026-10-02-e2-v2-trusted-replay-design.md | sed '3d')` 无输出，本轮零改动验证为 `git diff --exit-code 06827f2e HEAD -- docs/superpowers/specs/2026-10-02-e2-v2-trusted-replay-design.md`。
- Ticket 23 闭环 Tasks 24–29 包含旧计划 Tasks 9–12 尚未执行的 E1/E4 前置工作；不扩展 E2 v2 规格、不改变 138 项合同、四状态、零容忍规则或 E1/E2/E3/E4 职责。
- `trusted-e2-suite/v2`、`trusted-e2-receipt/v2`、`trusted-e2-toolchain-tar-zstd/v1`、`trusted-e2-dependency-repository/v1`、`trusted-product-raw-git-tar-zstd/v1` 均拒绝未知、缺失、重复字段；v1 suite/receipt 不迁移、不猜测升级。
- `validate/promote/reconcile` 的全部 E2 requirement 先通过同一阶段 A barrier；失败时 `spawnCount=checkoutCount=casWrites=contractWrites=0`。verify 永不写 CAS；collect 仅完整成功后原子写一次 receipt。
- `AcceptanceOperationSnapshotV1` 只绑定 catalog、contract 与本轮 `ReplayPolicyBindingV1` digest，用于 operation barrier/投影前 recheck；`ReplayPolicyBindingV1` 绑定 registry、suite/profile、revocation、reviewer policy、toolchain、dependency repository、raw product source 与 containment policy，明确排除 catalog、contract 和 observation time。receipt 只保存 replay binding digest。
- runner 只接收 `VerifiedProductRepositorySourceV1`；裸 `repository_root`、`cwd`、宿主绝对路径和未注册工作区 Git 目录不能进入 replay 或 receipt。source builder 可接收运行时 `RegisteredRepositoryBoundaryV1` capability，但其绝对路径、device/inode 与原始 `.git/gitdir/commondir` 宿主路径不得写入 container、build record、source digest 或 receipt。
- 产品仓库协议逐字服从批准规格：subject 恰为规格原文 `git://product@<40-sha>`，其中 `<40-sha>` 只允许小写 SHA-1/40hex；`extensions.objectFormat` 任何出现及 SHA-256 product repository/object/ref 一律 fail closed。policy repository commit 使用独立 codec，可按其真实 object format 为 SHA-1 或 SHA-256；两者都不得塞入 SHA-256-only `NamedDigest`。
- `RegisteredRepositoryBoundaryV1` 只能由同一受信 source-runner 进程打开、持有并关闭 owning directory handles，禁止 JSON/pickle/路径形式序列化或跨进程恢复；同一命令必须先按规格 §8.2 对原始 `.git/gitdir/commondir` 字节与拓扑执行完整 `RawGitLayoutInspectorV1`，通过后才规范化和构建 container。原始 pointer/control bytes 只在该进程 owning-FD 生命期内检查，不持久化、不参与可重现 digest；持久 proof 只绑定去路径的规范逻辑拓扑。跨 Task/权限域唯一存储由编排器在每个 executor 启动前提供并验证为绝对路径 `$TRUSTED_HANDOFF_STORE_ROOT`；该目录位于仓库/worktree 之外，宿主路径、mount 点、认证租约不得进入 record、proof 或 digest。producer-only harness 在无签名/agent 环境中发布 CAS/record/pointer 与内容寻址 export request 后退出；orchestrator-only harness 只按 request ref 重算 closure，不能接收 producer 路径。CAS 路径恰为 `sha256/<前两位>/<后 62 位>`，pointer 为 `pointers/<handoff-id>.json`；每个 `(handoffId, consumerTask, destinationExecutorId)` 使用唯一 `transportId`，其 transfer/seal 为 `transfers/<handoff-id>/<transport-id>.json` 与 `seals/<handoff-id>/<transport-id>.json`。编排器只在目标 durable store 完整导入 closure、签名并将 transfer/seal 原子 no-replace install+fsync 后才标记该 consumer transport 可用；consumer 依次 fetch/import/finalize/read 并每次重算 request、pointer、完整 record bytes 与 referenced blobs。同 `transportId` 且全部 bytes 一致是幂等 retry；nonce/sequence/consumer/destination 任一改变或不匹配是 replay/tamper 并 fail closed。`mktemp` 只作单 Task local cache，并须由安全 helper 先清理验证后解除 EXIT trap。
- 工具链、依赖、产品源码分别只来自 `trust/toolchains/sha256`、`trust/dependencies/sha256`、`trust/product-sources/sha256`；禁止宿主 PATH/shell/`/usr/bin/env`、live download、主机 cache 或同名工具回退。
- 任何会形成产品 subject、固定 policy commit/ref 或验收授权状态的 Git 读写只能经 `TrustedGitExecutorV1`：Git 2.53.0 executable 与 SHA-256 来自已验证 Task 18 toolchain；环境从空 allowlist 构造，固定 `GIT_CONFIG_NOSYSTEM=1`、`GIT_CONFIG_SYSTEM=/dev/null`、`GIT_CONFIG_GLOBAL=/dev/null`、受控空 `HOME/XDG_CONFIG_HOME`、只读空 `core.hooksPath`、`gc.auto=0`、`maintenance.auto=false`、`LC_ALL=C/LANG=C/TZ=UTC`，拒绝调用方 `GIT_CONFIG_COUNT/GIT_CONFIG_KEY_n/GIT_CONFIG_VALUE_n/GIT_CONFIG_PARAMETERS` 与所有 signing/agent 注入。commit 必须显式 `--no-gpg-sign --no-verify` 并固定 author/committer name/email/date；raw config preflight 在首个 Git spawn 前拒绝 system/global/local include、hooks、signing、gpg、credential、filter 与 shell-command 配置。普通 Task 实现 checkpoint commit 不产生可信 artifact，不得被后续证据读取。
- Task 22/27 producer 与 orchestrator 必须由 Task 14 的 `CredentialIsolatedHandoffRunnerV1` 在登记 Linux executor 上以不同预配 UID/GID 和不同 user+mount+PID namespace 启动，不能只靠 fresh shell/env allowlist。root-owned broker 在 exec 前执行 `close_range(3, UINT_MAX, CLOSE_RANGE_UNSHARE)`，再仅注入角色所需 FD；producer namespace 不挂载 key/agent、以 `hidepid=2,subset=pid` 重挂 `/proc`、`PR_SET_DUMPABLE=0`、空 capability bounding set+`no_new_privs`，seccomp 拒绝 ptrace/process_vm_readv/process_vm_writev/pidfd_getfd/open_by_handle_at。signing key 只由 orchestrator namespace no-follow 打开为只读 FD，CLI 只收 `--signing-key-fd`；两域唯一数据交接仍为 durable store 中内容寻址 export-request ref。仅 profile 固定的 `task22-producer` 会话允许 `linked-worktree-provisioner-v1` 能力，通过 broker 注入的已认证 `SOCK_SEQPACKET` RPC FD 调用固定客户端；Task 27 producer 与所有 orchestrator 均不得获得它。客户端 executable/digest 与 RPC FD 均由 profile 固定，MAC key 只在 root broker，绝不进入任一 child。broker 对 capability 除 `ownership_token` 外的全部 canonical 字段、executor/profile identity、随机 lifecycle nonce 做 MAC；destroy/verify 必须由 broker 重算 MAC、重开三根并核对 identity 后执行，producer 不能自行签发或扩大授权。
- 工具链预算固定为压缩 1 GiB、100,000 members、单文件 256 MiB、展开 4 GiB、100:1；依赖预算固定为压缩 1 GiB、100,000 members、20,000 packages、单 archive 256 MiB、blob 2 GiB、view 200,000 entries/2 GiB、安装树 4 GiB、100:1。
- 固定版本为 uv 0.11.15（commit `3cffe97c2e48c9e49422c738da3af95919dd0bf5`）、Git 2.53.0、Node 24.20.0、npm 11.19.0、pytest 8.3.5、pytest-json-report 1.5.0、Vitest 3.0.8、zstandard 0.23.0；版本相同但 bytes/build identity 不同也拒绝。
- Python setup 恰为包内 uv 的 `venv`、`pip sync` 两次 invocation，只消费 exact-pin+SHA-256 requirements lock 与 wheel-only view；禁止 project/workspace/editable/sdist/source/build backend。
- uv probe 的 query-script、argv、environment digest 分别固定为 `sha256:dcd303ccdd0d36553eb514ac50462a23e69330b09f850af091d961e87411c370`、`sha256:31007ef393f4db03a5a488a04a53bde0d7f8188c7e2375941df8e2ba13cb8382`、`sha256:a14ece45d0cc6ff0a970dd6c2a81e1212da2d9d492108427031cfaefb4894c38`；cwd 必须是 verified checkout root，cache 必须是当次 leaf 独占空根。
- npm setup 恰为包内 Node 加载 npm CLI 执行 `ci --ignore-scripts --offline --replace-registry-host=never --cache=@dependency-repository/npm`；测试由包内 Node 直接加载 verified `vitest.mjs`。
- receipt/report/manifest 各 1 MiB；Git pointer/ref 4 KiB、config 256 KiB、packed-refs 8 MiB、Git 元数据聚合 16 MiB；全部使用 `BoundedStableReader`。
- stdout/stderr pending record 各 64 KiB，安全摘要每 invocation 合计 32 KiB；唯一 `scan_sensitive_lexemes_v1` 同时服务主替换与 receipt/barrier residual scan，先脱敏、后截断、满缓冲后仍读到 EOF。
- containment 固定为 cgroup v2 membership + `clone3(CLONE_INTO_CGROUP)` 首次入组、只读 mount namespace、无接口 network namespace、继承式 seccomp BPF user-notification census、pidfd/waitid 与 `cgroup.kill`；禁止 ptrace、audit、eBPF、事后 `/proc` 或 PGID 充当证明。
- Task 13 先形成独立设计审查门，Task 14 才可实现。若审查判定固定机制不能满足无丢失边界，后续任务停止并修订规格/计划，不得临时换机制。
- 每 leaf 最多 4,096 exec events、200,000 write events；序号跳跃、摘要链断裂、observer EOF、overflow、未知消息均 fail closed。macOS 无真实 VM 时返回 `E2_CONTAINMENT_UNAVAILABLE`，不降级 PGID。
- Ticket 23 四个 E2 suites 共用同一 verified product source、`ReplayPolicyBindingV1` 和精确 lock/container/toolchain/containment digests；产品专属 Python/npm containers 不得复用最小 fixture digest。形式化 E1/E2 证据只能消费 Task 11 产出的 verified source/sanitized checkout；Task 22 的 linked worktree 只在本 Task 内准备主体，Task 23 必须从持久 handoff 重新验证并物化 sanitized checkout，任何宿主工作区 path 都不得跨 Task 或进入 receipt。
- 先提交真实 registry/profile/revocation 与全部 digest 的固定 policy commit；下一独立 commit 才 collect/register，并提交 CAS objects、catalog pointer 与审计记录。`matrix-contract.json` 在 collect/register commit 中字节不变。
- E4 私钥始终在仓库外；仓库只保存 allowed-signers、公钥指纹、reviewer policy、规范 attestation、签名和附件摘要。缺真实 E4 或任一 E1/E2 时 C01–C03 必须阻塞。
- 每个 Task 使用新实现会话，提交后使用新独立审查会话；Critical/Important 清零且命令通过才进入下一 Task。实现前读取任务所涉现有/上游源码、版本与许可证，并记录在既有 SDD ledger。

## Review Focus

- operation snapshot、最后一个 pointer 或 policy digest 在 barrier 前后变化必须零 spawn/checkout/CAS/contract 写；Tasks 5、17 覆盖。
- 32 KiB 后才出现跨 chunk token、非法 UTF-8、未闭合 quote/JSON 或 agent socket 标点仍须读到 EOF并返回 `OUTPUT_REDACTION_UNSAFE`；Tasks 3、20 覆盖。
- leader 任意结局后 double-fork+`setsid/setpgid`+关闭 stdio 仍须处于 leaf 并被清空；Tasks 14、21 覆盖。
- uv probe cwd/env/script/cache/extra child、恶意 entry-point 越界、project/workspace/build backend、DNS/socket、derived entrypoint replacement 均须命中固定错误码且不得进入测试；Tasks 19–20 覆盖。
- 真实 linked worktree 必须在规范化前形成 raw-layout inspection proof并以 capability destroy 清除专属 checkout/gitdir/backlink且保留共享 commondir；artifact-bearing Git 只用 pinned executor，producer/orchestrator 必须不同 OS credential/namespace/FD 边界；handoff full-bytes/no-replace/cross-executor transport、真实 setup/test 零网络、七项 semantic batch preflight 任一失败均须阻塞且零晋升；Tasks 2、7、10–11、13–14、18、22–23、27–29 覆盖。

## Ticket 23 固定测试身份

- 后端四个 node ID：`backend/tests/test_creative_canvas.py::test_canvas_document_saves_without_shot_and_returns_project_assets`、`::test_canvas_save_rejects_stale_document_revision`、`::test_canvas_catalog_keeps_legacy_default_and_isolates_canvas_documents`、`::test_soft_deleted_canvas_keeps_asset_references_and_can_restore`。
- 结构 fullName：`独立入口不依赖 Shot 并保留项目上下文`、`Toonflow 主体控件可通过可访问路径打开和关闭`，文件 `frontend/src/CreativeCanvasTicket23Acceptance.test.tsx`。
- 持久化 fullName：`首次读取画布期间禁止写入草稿`、`保存冲突时保留本地草稿并显示重新读取入口`、`保存中继续编辑会保留草稿并用返回的新 revision 进行下一次保存`、`从画布目录切换时按 canvasId 加载独立工作区`、`保留本地草稿后切回会继续标记为未保存`，文件 `frontend/src/CreativeCanvas.test.tsx`。
- API fullName：`不传 canvasId 时保留旧画布请求 URL 与载荷`、`目录 CRUD 与所有画布工作区请求都显式携带 canvasId`，文件 `frontend/src/creativeCanvasApi.test.ts`。

Ticket 23 E4 criteria 固定为旧规格 10.4 的八项，ID 及顺序不得另行发明：

1. 1280×900 独立入口打开且无需 Shot；
2. 对照 Toonflow 固定提交核查顶栏、添加入口、基础节点、就近浮动工具、画布控制、按需编辑器及关键开关；
3. 创建/命名画布，加入文本与稳定项目素材节点，保存刷新后恢复内容和视图；
4. 创建并切换第二画布，确认文档隔离；
5. 第二会话制造 stale revision，确认拒绝覆盖、保留本地草稿且可明确恢复/放弃；
6. 项目素材节点使用稳定素材身份且不强制归属 Shot；
7. 不把后续生成、批量执行或 Agent 能力宣称为已验证；
8. 核查适用零容忍项，特别是错误素材身份和未经批准旁路。

---

## 固定跨任务接口

```python
@dataclass(frozen=True)
class NamedDigest:
    name: str
    digest: str

@dataclass(frozen=True)
class GitObjectIdV1:
    algorithm: Literal["sha1", "sha256"]
    hex: str

@dataclass(frozen=True)
class ProductGitObjectIdV1:
    algorithm: Literal["sha1"]
    hex: str

@dataclass(frozen=True)
class LinkedWorktreeOwnershipCapabilityV1:
    schema: Literal["trusted-linked-worktree-ownership/v1"]
    project_id: str
    provisioner_id: Literal["trusted-linked-worktree-provisioner/v1"]
    provisioner_digest: str
    broker_key_id: str
    lifecycle_id: str
    ownership_token: str
    checkout_root: str
    linked_gitdir_root: str
    shared_commondir_root: str
    checkout_identity: FileIdentityV1
    linked_gitdir_identity: FileIdentityV1
    shared_commondir_identity: FileIdentityV1
    shared_object_store_identity: FileIdentityV1
    shared_object_store_baseline_digest: str

@dataclass(frozen=True)
class RegisteredControlRootV1:
    logical_name: Literal["gitdir", "commondir"]
    handle: OwnedDirectoryHandle
    opened_identity: FileIdentityV1

@dataclass
class RegisteredRepositoryBoundaryV1:
    checkout: OwnedDirectoryHandle
    control_roots: tuple[RegisteredControlRootV1, ...]
    topology: RepositoryTopologyV1

@dataclass(frozen=True)
class RawGitInspectionProofV1:
    schema: Literal["trusted-raw-git-inspection-proof/v1"]
    inspector_id: Literal["raw-git-layout-inspector/v1"]
    profile_digest: str
    logical_topology_digest: str
    passed_rule_digests: tuple[NamedDigest, ...]
    budget_observations: tuple[NamedDigest, ...]
    subject_commit: ProductGitObjectIdV1
    container_digest: str

@dataclass(frozen=True)
class TransportSealV1:
    schema: Literal["trusted-handoff-transport-seal/v1"]
    store_namespace: str
    handoff_id: str
    transport_id: str
    consumer_task: int
    record_ref: str
    record_full_bytes_digest: str
    pointer_name: str
    pointer_bytes_digest: str
    transfer_manifest_digest: str
    source_executor_id: str
    destination_executor_id: str
    nonce: str
    sequence: int
    expires_at: str | None
    signature_algorithm: Literal["ssh-ed25519"]
    key_id: str
    signature: str

@dataclass(frozen=True)
class HandoffExportRequestV1:
    schema: Literal["trusted-handoff-export-request/v1"]
    handoff_id: str
    record_ref: str
    record_full_bytes_digest: str
    pointer_bytes_digest: str
    consumer_tasks: tuple[int, ...]
    binding_set_digest: str

@dataclass(frozen=True)
class TransportJobV1:
    source_store_root: str
    target_store_root: str
    stage_root: str
    export_request_ref: str
    handoff_id: str
    transport_id: str
    consumer_task: int
    store_namespace: str
    source_executor_id: str
    destination_executor_id: str
    nonce: str
    sequence: int
    expires_at: str | None
    signing_key_fd: int
    key_id: str

@dataclass(frozen=True)
class TrustedHandoffRecordV1:
    schema: Literal["trusted-acceptance-handoff/v1"]
    handoff_id: str
    kind: Literal["product-source", "policy"]
    producer_task: int
    producer_commit: GitObjectIdV1 | ProductGitObjectIdV1
    consumer_tasks: tuple[int, ...]
    bindings: tuple[NamedDigest, ...]

@dataclass(frozen=True)
class ProductRepositorySourceV1:
    subject_repository: str
    subject_commit: ProductGitObjectIdV1
    source_ref: str
    source_digest: str
    container_format: Literal["trusted-product-raw-git-tar-zstd/v1"]
    object_format: Literal["sha1"]
    budget: ProductSourceBudgetV1
    boundary_digest: str
    tracked_checkout_digest: str
    control_metadata_digest: str
    raw_inspection_proof_ref: str

@dataclass(frozen=True)
class Ticket23ProductSourceHandoffV1:
    record: TrustedHandoffRecordV1
    source: ProductRepositorySourceV1
    producer_commit: ProductGitObjectIdV1

@dataclass(frozen=True)
class VerificationOperationV1:
    operation_id: str
    kind: Literal["verify", "validate", "promote", "reconcile", "collect"]
    requirement_ids: tuple[str, ...]
    started_operation_snapshot_digest: str

@dataclass(frozen=True)
class RegistrationCandidateV1:
    profile_id: str
    verifier_id: str
    subject: str
    artifact_ref: str

@dataclass(frozen=True)
class ValidatedRegistrationBatchV1:
    operation_snapshot_digest: str
    policy_commit: GitObjectIdV1
    product_handoff_record_digest: str
    policy_handoff_record_digest: str
    candidates: tuple[RegistrationCandidateV1, ...]
    canonical_digest: str

@dataclass(frozen=True)
class ValidatedPromotionBatchV1:
    operation_snapshot_digest: str
    policy_handoff_record_digest: str
    policy_commit: GitObjectIdV1
    subject: str
    subject_commit: ProductGitObjectIdV1
    product_source_ref: str
    network_proof_ref: str
    replay_policy_binding_digest: str
    evidence_refs: tuple[NamedDigest, ...]
    canonical_digest: str

@dataclass(frozen=True)
class PromotionTransactionResultV1:
    operation: Literal["promote-transaction"]
    requirement_ids: tuple[str, ...]
    initial_snapshot_digest: str
    promotion_batch_digest: str
    final_contract_digest: str
    reconciled_snapshot_digest: str
    result_digest: str

@dataclass(frozen=True)
class ReplayPolicyBindingV1:
    registry_digest: str
    suite_digests: tuple[NamedDigest, ...]
    profile_digests: tuple[NamedDigest, ...]
    revocation_digest: str
    reviewer_policy_digest: str
    toolchain_digests: tuple[NamedDigest, ...]
    dependency_digests: tuple[NamedDigest, ...]
    product_source_digests: tuple[NamedDigest, ...]
    containment_policy_digests: tuple[NamedDigest, ...]
    canonical_digest: str

@dataclass(frozen=True)
class AcceptanceOperationSnapshotV1:
    catalog_digest: str
    contract_digest: str
    replay_policy_binding_digest: str
    canonical_digest: str
```

`RegisteredRepositoryBoundaryV1` 是仅存于 source-runner 进程内、拥有 handle 生命周期的 capability：`build` CLI 接收三个 root path 后立即 no-follow 打开为 owning handles，`RawGitLayoutInspectorV1.inspect(boundary, subject_commit)` 在任何逻辑 alias/规范化之前，以 `BoundedStableReader` 读取原始 pointer/control bytes，执行规格 §8.2 全部预算与拒绝规则，返回内存 inspection result；这些 raw bytes 仅用于当次判定，既不写入任何 artifact，也不直接或间接进入 digest。inspector 只从已验证关系派生 `CanonicalLogicalRepositoryTopologyV1`：它仅含 `@checkout/@gitdir/@commondir` aliases、关系类别、允许的 pointer/commondir 形式类别及 backlink 关系，不含 path、path bytes、device/inode/mtime。`RawGitInspectionProofV1.logical_topology_digest` 恰为该去路径逻辑拓扑的规范 JSON SHA-256，proof 只绑定此 digest、inspector/profile/rule set/budget observations、subject/source commit 和 container digest。同一命令随后构建 container 并 finalize proof；所有后续遍历只从 handles `openat`，直到 container/build-record/proof/handoff 全部发布后才关闭。Task 11 对 proof schema、inspector/profile/rules、逻辑 topology/build-record/container bindings 做正式验证，再检查 canonical container；不能把 builder 自报的布尔值当作证明。

`GitObjectIdV1` 的 canonical JSON 恰为 `algorithm/hex`，只用于 policy repository 等非产品 Git 对象：`sha1` 接受小写 40-hex，`sha256` 接受小写 64-hex，未知算法、长度/算法错配、大小写或前缀形式一律拒绝。冻结规格中的产品主体另由 `ProductGitObjectIdV1` 收窄为 `algorithm="sha1"` 与小写 40-hex；`ProductRepositorySourceV1.object_format` 必须恰为 `sha1`，原始仓库 config 中只要出现 `extensions.objectFormat`（即使值写成 `sha1`）或产品 container/ref/object ID 为 SHA-256 都 fail closed。产品 subject 语法逐字固定为规格原文 `git://product@<40-sha>`，不得加入 algorithm 段。`Ticket23ProductSourceHandoffV1` 是内存 validated view：其 record 必须为 kind=`product-source`/producerTask=22，record 的 typed `producerCommit`、从七 bindings 解出的 source subject 与 view 的 `producer_commit` 三者必须相同且都通过 `ProductGitObjectIdV1`；policy handoff 的 producer commit 则继续由独立 `GitObjectIdV1` 路径支持 SHA-1/SHA-256。

`TrustedHandoffRecordV1` 的规范 JSON 字段恰为 `schema/handoffId/kind/producerTask/producerCommit/consumerTasks/bindings`，其中 `producerCommit` 按 handoff kind 选择并验证对应 typed codec，record 内不含自摘要字段；`recordDigest = sha256(full canonical record bytes)`，`recordRef` 与之相等。pointer 固定字段恰为 `schema/handoffId/recordRef/recordDigest/producerTask/producerCommit`，其中 schema=`trusted-acceptance-handoff-pointer/v1`、`recordRef == recordDigest` 且 typed commit 与 record 完全相等。`consumerTasks` 为升序无重复正整数；每个 binding 恰为 `name/digest`，binding digest 继续只允许小写 `sha256:<64hex>`，Git object ID 永不编码为 `NamedDigest`。CAS blob/record 使用 `O_CREAT|O_EXCL` 写临时 regular file、fsync 后以 hard-link-to-final 的 no-replace publish；pointer 同样以临时文件+`linkat` 原子 no-replace 发布并 fsync 父目录。目标已存在时只读回、重算并接受 byte-identical 内容，任何不同内容或并发竞态均拒绝，绝不 replace/覆盖。

`TrustedHandoffStoreV1.open_from_environment()` 要求 `$TRUSTED_HANDOFF_STORE_ROOT` 为编排器验证的绝对、no-symlink、权限/owner/项目租约匹配的外部 durable store。producer 只 `publish-local` 一次 CAS/record/pointer，再发布一份不含路径、transport identity 或密钥的 `HandoffExportRequestV1` CAS object 与 no-replace pointer；request 绑定 record/pointer full bytes digest、精确 consumer set 与 binding-set digest。producer 与 orchestrator 只通过该持久、内容寻址 request ref 交接，不共享 shell 变量、临时根、trap 或路径 capability。编排器从固定 source-store trust boundary 重开 request/record/pointer/CAS 并全量重算，拒绝 producer 提供的 manifest 路径或自报 digest，再针对每个 record 所列 consumer 独立执行 `export→upload→sign-seal→install-transport`。`export` 产生不含路径的 `trusted-handoff-transfer/v1` manifest/closure，其规范 digest 进入 `TransportSealV1`；`upload` 先将 closure no-replace 导入目标 store 并全量重算；`sign-seal` 对 store namespace、handoff/transport ID、consumer task、record/pointer/manifest digests、source/destination executor identity、nonce/sequence/expiry 签名；`install-transport` 再把 transfer manifest 和 seal 分别以 temp regular file+`linkat` 原子 no-replace 写入目标 store 的固定路径，fsync 文件与两个父目录。目标已存在时只有完整 bytes 一致才幂等成功，任何不同都拒绝；没有 install 成功的 transport 不得 fetch。验签根只来自仓库内固定 registry profile 解析的 `handoff-transport-policy.json` 和 `handoff-transport-allowed-signers`，所有 CLI 均不接受替代 trust root。consumer 按 `fetch→import→finalize→read` 以安装的 `transportId` 把闭包落入单 Task 空 cache，重算并核对 consumer task/destination identity/nonce/sequence/expiry；另一 consumer 的 seal 不能被复用。

`TransportJobV1` 只是在 `run-transport` 进程内由已验证 export request 和完整显式 CLI 参数构造的不可变调用对象，不得序列化、持久化或作为 producer transport manifest；三个 root path、signing-key path/value 与 FD number 都不进入任何 record、request、manifest、seal、digest 或日志。共享子命令不能省略每个 consumer 的参数表，也不能改变底层四步的验收与独立签名边界。producer OS credential/domain 中必须不存在 signing key mount/FD、私钥、agent/socket 与可读 orchestrator `/proc`；只有 orchestrator domain 得到 broker no-follow 打开的 `signing_key_fd`，且它不能接收或重新信任 producer checkout/boundary/stage 路径。

`trusted_handoff_store.py cleanup-owned-tree` 是所有 task-local 清理的唯一 helper：调用开始时只接受绝对、当前用户拥有、mode=0700 且不等于 workspace/home/root/其祖先的 owning root；若根存在，helper 以 no-follow dirfd 打开 root 与 parent、记录当次 identity，并在整个遍历和最终 unlink 前后持续复核。它只为当前用户恢复删除只读条目所需的最小 owner 权限，再 unlink/rmdir、fsync parent 并验证根为 `ENOENT`；安全校验后的根若已不存在则幂等成功，symlink、mount 变化、清理期间 identity swap、越界或残留均非零退出。每个 harness 的 EXIT trap 调用该 helper；成功路径必须先显式调用并验证删除，再执行 `trap - EXIT`，因此显式清理失败时 trap 仍保持有效并以非零状态退出。一个 harness 若拥有多个相互独立的 root，cleanup function 必须在 `if ...; then ... else ... fi`/`||` 受控分支中逐根调用 helper、记录每根结果且无论前根成败都尝试后根；全部 helper 成功并逐根确认 `ENOENT` 后才返回 0，任一失败统一返回非零且不得解除 trap。

`NamedDigest.name` 必须是 NFC、非空、无控制字符的逻辑名，`digest` 必须是小写 `sha256:<64hex>`。每个 tuple 由 builder 以 `(name.encode("utf-8"), digest.encode("ascii"))` bytewise 升序规范化；同名条目无论摘要是否相同都拒绝，不同名称可共享同一摘要。suite/profile/toolchain/source/containment 集合对 E2 binding 必须非空；dependency 集合仅在所有 suite 都声明零 ecosystem 时允许空，否则按 `<suite-id>/<ecosystem>` 唯一命名。空集合编码为 JSON `[]`，文件系统创建/遍历顺序不进入结果。

`ReplayPolicyBindingV1`、`AcceptanceOperationSnapshotV1`、`ValidatedRegistrationBatchV1` 与 `ValidatedPromotionBatchV1` 的 `canonical_digest`，以及 `PromotionTransactionResultV1.result_digest`，都对除自身摘要字段外的字段按字段名排序、UTF-8 紧凑规范 JSON 编码后取 SHA-256；它们不同于不含内嵌摘要的 handoff record full-bytes digest。`ValidatedPromotionBatchV1` 只存在于 `PromotionTransaction` 内存，允许把 canonical bytes 写入 diagnostics，但没有 decode/authorize API，外部文件与自摘要永远不能恢复 capability。任何 wall-clock、mtime、review observation time 都不得进入；顺序 collect/register 改变 catalog 只生成新的 operation snapshot，不改变已有 receipt 的 replay binding。

| 文件/目录 | 单一职责 |
| --- | --- |
| `trusted_evidence/e2_policy.py` | v2 suite、operation、snapshot、product-source schema 与 loader。 |
| `trusted_evidence/redaction.py` | 唯一 shared lexer、streaming redactor、安全缓冲。 |
| `trusted_evidence/bounded_io.py` / `receipts.py` | 有界稳定读取与 receipt codec。 |
| `trusted_evidence/artifact_barrier.py` | operation candidate/pointer/artifact 预检与 snapshot recheck。 |
| `trusted_evidence/canonical_archive.py` | zstd+ustar 规范、预算、no-exec 展开。 |
| `trusted_evidence/toolchain.py` / `platform_identity.py` / `tools/trusted_git.py` | toolchain closure/security、exact platform identity 与所有 artifact-bearing Git 操作。 |
| `trusted_evidence/dependency_repository.py` / `*_dependencies.py` | lock mapping 与 deterministic Python/npm consumer views。 |
| `trusted_evidence/raw_git_inspection.py` / `product_source.py` / `git_subject.py` | 原始布局 inspection proof、content source loader、sanitized object view、fixed checkout。 |
| `trusted_evidence/reporters.py` | 真实 pytest/Vitest report 与 selection closure。 |
| `trusted_evidence/containment/` / `tools/run_handoff_domain.py` | 七方法协议、wire transcript、Linux supervisor/backend 与 producer/orchestrator credential domains。 |
| `trusted_evidence/replay.py` / `collection.py` | 内存 replay 与成功后唯一 CAS receipt 写入。 |
| `trusted_evidence/evaluation.py` / `projection.py` / `runtime.py` | operation 编排与唯一 contract writer。 |
| `trusted_evidence/handoffs.py` / `tools/trusted_handoff_{store,orchestrator}.py` | 外部 authenticated store、内容寻址 record、no-replace pointer、跨 executor export/upload/sign/fetch/import/finalize/read。 |
| `trusted_evidence/verifiers/e4_ssh.py` | E4 canonical attestation、scope/revocation、SSH 验签。 |
| `acceptance/trust/` / `profiles/ticket-23.json` | 实际工具链、依赖、源码、signer 和 Ticket 23 policy 输入。 |

### Task 1: 固定离线 bootstrap artifacts，消除工具链依赖循环

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/bootstrap/manifest.json`
- Create from audited offline input: `.scratch/unified-remix-canvas-generation/acceptance/bootstrap/artifacts/uv-aarch64-apple-darwin-0.11.15.tar.gz`
- Create from audited offline input: `.scratch/unified-remix-canvas-generation/acceptance/bootstrap/artifacts/uv-x86_64-unknown-linux-gnu-0.11.15.tar.gz`
- Create from audited offline input: `.scratch/unified-remix-canvas-generation/acceptance/bootstrap/artifacts/zstandard-0.23.0-cp313-cp313-macosx_11_0_arm64.whl`
- Create from audited offline input: `.scratch/unified-remix-canvas-generation/acceptance/bootstrap/artifacts/zstandard-0.23.0-cp313-cp313-manylinux_2_17_x86_64.manylinux2014_x86_64.whl`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/bootstrap/requirements.lock`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/bootstrap/uv-empty.toml`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/verify_e2_bootstrap.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_bootstrap.py`

**Interfaces:**
- Consumes: 预先离线取得的官方 immutable release bytes；执行期间禁止网络。uv 来源为 `astral-sh/uv` 0.11.15 release/official versions registry，zstandard 来源为 PyPI 0.23.0 release；缺任一 bytes、provenance 或摘要不符时本 Task 停止。
- Produces: `BootstrapArtifactsV1(uv_archive, uv_executable, zstandard_wheel, empty_config, manifest_digest)`；Tasks 6、18 的唯一 bootstrap 输入。

- [ ] **Step 1:** 写 RED tests：manifest 必须按平台唯一选择 artifact；缺文件、非 regular file、摘要/文件名/tag/version 不符、empty config 非零字节、环境启用 proxy/network 均拒绝。
- [ ] **Step 2:** 固定真实摘要：macOS arm64 uv archive=`sha256:7e5b336108f8576eda1939920ca0a805b4a9a3c3d3eb2f6140e38b7092fbe4f3`，Linux x86_64 GNU uv archive=`sha256:b03e572f010bea94a4a52d42671ba72981e12894f71576181a1d26ff68546da7`，macOS arm64 zstandard wheel=`sha256:38302b78a850ff82656beaddeb0bb989a0322a8bbb1bf1ab10c17506681d772a`，manylinux x86_64 wheel=`sha256:6f77fa49079891a4aab203d0b1744acc85577ed16d767b52fc089d83faf8d8ed`，零字节 config=`sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`；`requirements.lock` 只含 `zstandard==0.23.0`、对应 platform marker 与上述 wheel hash。
- [ ] **Step 3:** 运行 `env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/verify_e2_bootstrap.py --manifest .scratch/unified-remix-canvas-generation/acceptance/bootstrap/manifest.json --offline`；预期先因 artifacts 未预置而 RED。预置经治理审核的离线 bytes 后，脚本逐文件 SHA-256、只读提取 uv、执行 `uv --version` 并精确核对 0.11.15/build identity，再验证 wheel RECORD/tag 和空 config；全程不得下载。
- [ ] **Step 4:** 用脚本输出的 `BOOTSTRAP_UV` 运行 `env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY "$BOOTSTRAP_UV" pip install --python=backend/.venv/bin/python --offline --no-index --find-links=.scratch/unified-remix-canvas-generation/acceptance/bootstrap/artifacts --require-hashes --no-build -r .scratch/unified-remix-canvas-generation/acceptance/bootstrap/requirements.lock`，再运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_bootstrap.py`；预期 GREEN。此步骤不读取或改写无 hash 的 `backend/requirements.lock`。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/bootstrap .scratch/unified-remix-canvas-generation/acceptance/tools/verify_e2_bootstrap.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_bootstrap.py && git commit -m "build: freeze trusted replay bootstrap"`

### Task 2: 冻结 operation、snapshot、policy、product-source 与错误合同

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/e2_policy.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/handoffs.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_orchestrator.py`
- Create from governance input: `.scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-registry.json`
- Create from governance input: `.scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-policy.json`
- Create from governance input: `.scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-allowed-signers`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/registry.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/errors.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_handoffs.py`

**Interfaces:**
- Consumes: 规格 4、6.1、7、9、10、14 和既有六字段 pointer。
- Produces: 上述 immutable 类型、policy 用 `GitObjectIdV1.decode/encode_canonical`、产品用 `ProductGitObjectIdV1.decode/encode_canonical`、owning-FD context、`TrustedHandoffStoreV1.open_from_environment()`、`HandoffRegistry.publish_local/publish_export_request/fetch/import_/finalize/read/read_product_subject`；producer CLI 只有 `verify-root`、`publish-local`、`publish-export-request`，编排面 CLI 严格子命令为 `verify-export-request`、`export`、`upload`、`sign-seal`、`install-transport`、`run-transport`，consumer/生命周期 CLI 严格子命令为 `verify-cache`、`fetch`、`import`、`finalize`、`read`、`read-product-subject`、`cleanup-owned-tree`。`run-transport` 是 verify-export-request→export→upload→sign-seal→install-transport 的同进程共享实现，但每次调用仍必须显式接收 content-addressed `export-request-ref`、完整 source/target roots、handoff/transport/consumer、source/destination executor、nonce/sequence/expiry、stage root、orchestrator-only signing-key FD 与 key ID，不读取隐含默认值；四个 transport 底层步骤继续独立可测。consumer 的 `fetch/import/finalize/read` 四步都显式接收 `transport-id`、`consumer-task` 和 destination executor identity；`read` 只从 finalized cache 重验并输出 record 字段。`read-product-subject` 对 product-source handoff 从已验证 build-record/container 派生、对 policy handoff 从已验证 `binding.product-source` CAS object 派生同一 `ProductRepositorySourceV1.subject_commit`，只以 `--field hex|canonicalSubject` 单值输出；canonical subject 恰为规格原文 `git://product@<40-sha>`。

- [ ] **Step 1:** 写 `test_replay_binding_excludes_catalog_contract_and_observation_time`、`test_operation_snapshot_binds_catalog_contract_and_replay_binding`、`test_named_digest_order_is_identical_for_different_creation_order`、`test_duplicate_name_and_illegal_empty_set_reject`、`test_product_source_rejects_host_path`、`test_v1_is_not_upgraded`；policy `GitObjectIdV1` 覆盖 sha1/40、sha256/64 正例及未知算法/长度错配，产品 `ProductGitObjectIdV1` 只接受 sha1/40，并把 sha256/64、39/41 位、任何 `extensions.objectFormat`、带 algorithm 段的 product subject 和 Git ID 塞入 `NamedDigest` 全部作为 RED。另写 handoff RED tests：`Ticket23ProductSourceHandoffV1` 的 record/source/view 三份 product commit 必须相同，record 不含自摘要且 full canonical bytes SHA-256 精确等于 pointer `recordRef/recordDigest`；`read-product-subject` 只从已验证 product-source CAS 解码固定 SHA-1 subject，拒绝调用方自报 commit、objectFormat、SHA-256 product object 或篡改 CAS；policy record/pointer 的 producer commit 独立覆盖 sha1/sha256。owning handle 拒绝 JSON/pickle/copy；store root 非绝对、symlink、权限/owner/租约不符均拒绝；CAS/pointer/request no-replace；两个 producer 并发发布不同 record/request 时恰一方成功、另一方稳定失败，最终 bytes 不混合；相同 bytes 幂等接受。transport 用固定测试签名者覆盖两个 consumer 各自独立底层四步与 `run-transport`，断言 orchestrator 必须从 source store 按 request ref 重算 closure、拒绝非 CAS/非规范/未通过 full-bytes digest 认证的 manifest/ref，且不能接收 producer manifest/path；未 install seal 时不可 fetch、相同 `transportId`+相同 bytes retry 幂等、不同 bytes 不覆盖；一个 consumer 的 seal 用于另一 consumer，或 request/nonce/sequence/consumer/destination/expiry/签名/manifest 任一不符均 fail closed。`cleanup-owned-tree` 覆盖只读树成功、广根/symlink/清理期间 identity swap/残留失败，且失败时 EXIT trap 保持安装；多根 cleanup 回归分别注入“首根失败/次根成功”“首根成功/次根失败”，两种情况都断言两根 helper 调用数各为 1、最终非零且 trap 仍安装。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_handoffs.py`；预期缺类型与 handoff owner/registry 而 RED。
- [ ] **Step 3:** 实现严格 `NamedDigest`、两条互不扩权的 Git OID codec、规范 JSON 与两个 policy/snapshot digests；product record/source/proof 只走 `ProductGitObjectIdV1`，policy record/pointer 才走通用 `GitObjectIdV1`，bindings 仍只允许 SHA-256。实现外部 authenticated handoff store：blob/record/pointer/export request 以 full bytes digest/no-replace/fsync 发布。`trusted-handoff-transfer/v1` 严格包含 export request digest、store namespace、handoff/transport ID、consumer task、record ref/full-bytes digest、pointer name/bytes digest、有序 blob refs、source/destination executor ID、nonce/sequence/expiry；`TransportSealV1` 字段恰为顶层接口所列，签名消息为除 `signature` 外字段的规范 JSON。registry 固定 trust root，生产 CLI 不接受覆盖；只有编排面 `sign-seal` 从 broker 注入的只读 FD 读取仓库外私钥，拒绝 key path/value CLI。`upload` 先导入并重算 closure；`sign-seal` 只签该 upload receipt 指向的 manifest；`install-transport` 以两次 temp+`linkat` no-replace 安装 manifest/seal，若第二次安装失败则 transport 不可见，清理未入口 temp；只有两文件完整且摘要互绑时才把包含两者 digest 的 ready marker 也用 temp+`linkat` no-replace 安装，并依次 fsync 三个文件和各父目录。已存在 manifest/seal/marker 只接受三者完整 bytes 全部一致的幂等 retry，任一不同拒绝；崩溃恢复不得观测到半安装运输。`run-transport` 先从 source-store owning capability 验 request/record/pointer/CAS，再按参数构造深不可变 `TransportJobV1` 并顺序调用四个公开 transport 步骤，不增加默认值或旁路；`fetch/import/finalize/read` 从 target store 的 ready marker 定位同一 `transportId`，重算全部 bytes 并核对 consumer/destination/replay 状态；`finalize` 之前 `read` 必须拒绝。实现上述 no-follow `cleanup-owned-tree`，所有后续 task harness 只调用该清理入口；多根 shell cleanup 必须按顶层聚合规则逐根尝试。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_handoffs.py`；预期 GREEN 且 production profile 仍为空。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{e2_policy.py,handoffs.py,registry.py,errors.py} .scratch/unified-remix-canvas-generation/acceptance/tools/{trusted_handoff_store.py,trusted_handoff_orchestrator.py} .scratch/unified-remix-canvas-generation/acceptance/trust/{handoff-transport-registry.json,handoff-transport-policy.json,handoff-transport-allowed-signers} .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_{e2_policy_v2,handoffs}.py && git commit -m "feat: define trusted replay operation policy"`

### Task 3: 提前建立唯一共享 lexer 与 StreamingRedactor

**Files:**
- Rewrite: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/redaction.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_streaming_redaction.py`

**Interfaces:**
- Consumes: 规格 8.4、11.4、12。
- Produces: `scan_sensitive_lexemes_v1(record) -> ScanResult`、`StreamingRedactorV1.feed/finish`、`SafeOutputBuffer.accept`；不得出现第二 scanner。

- [ ] **Step 1:** 写 all-split、agent/file URL suffix、post-limit secret/invalid UTF-8 RED tests。
- [ ] **Step 2:** 运行 `backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_streaming_redaction.py`；预期旧 regex API RED。
- [ ] **Step 3:** 实现固定状态机、64 KiB pending、32 KiB safe buffer；主替换/residual 共用 lexer。
- [ ] **Step 4:** 重跑同命令；预期所有 chunk 切分等价。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/redaction.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_streaming_redaction.py && git commit -m "feat: stream redaction before persistence"`

### Task 4: 建立 bounded stable I/O 与 receipt v2 codec

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/bounded_io.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/receipts.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/storage.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_receipts_v2.py`

**Interfaces:**
- Consumes: Task 2 policy/snapshot、Task 3 唯一 scanner。
- Produces: `BoundedStableReader.read(...) -> StableRead`、`TrustedE2ReceiptV2.decode/encode_canonical`；receipt 只绑定 `ReplayPolicyBindingV1.canonical_digest` 及 replay result/proof digests，schema 中禁止 catalog/contract/observation-time 字段。

- [ ] **Step 1:** 写超限/symlink/race/未知字段/非预期 ID/秘密残留/replay-binding mismatch tests，并断言 catalog/contract 变化不改变 receipt validity。
- [ ] **Step 2:** 运行 `backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_receipts_v2.py`；预期 module RED。
- [ ] **Step 3:** 实现 O_NOFOLLOW、前后 stat、1 MiB 上限；codec 只调用 Task 3 scanner。
- [ ] **Step 4:** 重跑同命令；预期规范 round-trip GREEN。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{bounded_io.py,receipts.py,storage.py} .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_receipts_v2.py && git commit -m "feat: decode bounded E2 v2 receipts"`

### Task 5: 实现 operation-wide artifact barrier

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/artifact_barrier.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_artifact_barrier.py`

**Interfaces:**
- Consumes: Tasks 2、4。
- Produces: `preflight(operation, operation_snapshot, replay_binding) -> PrevalidatedE2Batch`、`recheck(batch, current_operation_snapshot) -> None`；receipt 只与 replay binding 比较。

- [ ] **Step 1:** 写 missing/ambiguous/revoked/last-invalid/operation-snapshot-race tests，全部断言零 spawn/checkout/CAS；另写 catalog 追加后旧 receipt 的 replay binding 仍有效。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_artifact_barrier.py`；预期 module RED。
- [ ] **Step 3:** 冻结 requirement/candidate/active set，全部 receipt 预检后一次 gate。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_artifact_barrier.py`；预期 race 为 `ACCEPTANCE_STATE_CHANGED`。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/artifact_barrier.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_artifact_barrier.py && git commit -m "feat: preflight E2 artifacts operation wide"`

### Task 6: 验证 canonical tar+zstd 并锁定依赖

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/canonical_archive.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_canonical_archive.py`

**Interfaces:**
- Consumes: Task 1 已验证的 zstandard 0.23.0 bootstrap、Task 4 bounded I/O、规格 6.2/6.3。
- Produces: `CanonicalTarZstdReader.validate_and_extract(...) -> ValidatedArchive`；不读取、不改写项目 `backend/requirements.lock`。

- [ ] **Step 1:** 写 checksum/frame/manifest-first/parent/order/header/path/type/budget RED tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_canonical_archive.py`；Task 1 未完成时必须因 bootstrap manifest/artifact 不可用而停止，Task 1 完成后预期仅 module RED。
- [ ] **Step 3:** 只调用已验证 bootstrap wheel 提供的 zstandard API，实现 single-frame/checksum/ustar/no-exec/budget 验证；不得访问 `/opt/trusted-e2` 或网络。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_canonical_archive.py`；预期 GREEN、攻击输入无 payload 残留。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/canonical_archive.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_canonical_archive.py && git commit -m "feat: validate canonical replay archives"`

### Task 7: 验证 toolchain、安全审核与平台身份

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/toolchain.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/platform_identity.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/trusted_git.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_toolchain.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_platform_identity.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_trusted_git.py`

**Interfaces:**
- Consumes: Tasks 2、6 与 uv/Git/Node/npm 实际源码/许可。
- Produces: `ToolchainPackage.open`、`measure_platform`、`compare_platform_identity`、`TrustedGitExecutorV1.commit(repository, paths, message, identity, expected_old_head) -> GitObjectIdV1`、`update_ref_no_replace(repository, ref, new, zero_oid)`、`verify_fixed_ref(repository, ref, expected, require_head, require_clean)`；CLI `trusted_git.py commit/update-ref-no-replace/verify-fixed-ref/read-result` 只能接收 Task 18 materialized `git` entrypoint+expected digest 与当次空 config/home/hooks roots。

- [ ] **Step 1:** 写 closure/uv identity/advisory/dynamic library/kernel ABI/PATH marker RED tests；另写 trusted Git RED：可执行 bytes/digest/version 不符、宿主 PATH fallback、system/global/local `include*`、`commit.gpgSign`、`tag.gpgSign`、`gpg.format`/`gpg.*.program`、`core.hooksPath`、`gc.auto`/`maintenance.*`、credential/filter/shell-command config、`GIT_CONFIG_COUNT/KEY_n/VALUE_n/PARAMETERS`、`SSH_AUTH_SOCK` 与恶意 pre-commit/commit-msg/post-commit hook 均须在 Git write spawn=0 时拒绝。正例 spy 必须观测 `GIT_CONFIG_NOSYSTEM=1`、system/global=`/dev/null`、空 HOME/XDG、只读空 hooks、`gc.auto=0`、`maintenance.auto=false`、`--no-gpg-sign --no-verify`、固定 author/committer name/email/date 与 `LC_ALL=C/LANG=C/TZ=UTC`；任何 gpg/hook/agent/maintenance canary 调用数为零。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_toolchain.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_platform_identity.py`；预期 modules RED。
- [ ] **Step 3:** 实现 exact bytes/build identity 和 offline security snapshot；`TrustedGitExecutorV1` 对 repository/config/executable 先 no-follow 有界读取并固定身份，首个 Git spawn 前完成全部拒绝检查，再用同一已验证 executable FD/proc-fd path 执行 commit/ref/verify。每次 spawn 都显式注入 `gc.auto=0`、`maintenance.auto=false` 且拒绝调用方覆盖，禁止自动 GC/maintenance 改写共享 object store。commit 只接收显式 path 列表，固定 identity 为 `Trusted Evidence Builder <trusted-evidence@invalid>`、author/committer date=`2000-01-01T00:00:00Z`，显式 `--no-gpg-sign --no-verify`；调用方不能覆盖任何环境或 config。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_toolchain.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_platform_identity.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_trusted_git.py`；预期所有异常在 setup/Git spawn 前阻塞，恶意 signing/hooks/agent canary 均为零。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{toolchain.py,platform_identity.py} .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_git.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_{toolchain,platform_identity,trusted_git}.py && git commit -m "feat: verify trusted replay toolchains"`

### Task 8: 实现 wheel-only Python dependency view

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/dependency_repository.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/python_dependencies.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_python_dependencies.py`

**Interfaces:**
- Consumes: Tasks 2、6、7。
- Produces: `DependencyRepository.open`、`PythonFindLinksView.build`、精确 lock/package/install-tree mapping。

- [ ] **Step 1:** 写 exact-pin/hash、sdist/source/editable/project/build、wheel metadata、extra/missing blob、two-root identity tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_python_dependencies.py`；预期 RED。
- [ ] **Step 3:** 实现纯函数只读 flat view，预检不启动 uv。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_python_dependencies.py`；预期 source 为 `E2_PYTHON_SDIST_FORBIDDEN`，mapping/view 使用固定码。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{dependency_repository.py,python_dependencies.py} .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_python_dependencies.py && git commit -m "feat: build trusted Python dependency views"`

### Task 9: 实现 npm cacache offline view

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/npm_dependencies.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_npm_dependencies.py`

**Interfaces:**
- Consumes: Tasks 7–8 与 npm/cacache 实际源码。
- Produces: `NpmCacacheView.build`、`validate_npm_lock_mapping`。

- [ ] **Step 1:** 写 lock v3、content-v2/index-v5、line hash/key/SRI/path、extra mapping、two-root identity tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_npm_dependencies.py`；预期 RED。
- [ ] **Step 3:** 实现规范 cacache 只读 lower view。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_npm_dependencies.py`；预期篡改在 npm spawn 前为 `E2_DEPENDENCY_VIEW_INVALID`。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/npm_dependencies.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_npm_dependencies.py && git commit -m "feat: build trusted npm cache views"`

### Task 10: 构建并加载规范 raw Git repository container

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/raw_git_inspection.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/product_source.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_product_source.py`

**Interfaces:**
- Consumes: Tasks 2、4、6；规格 §8.2 的完整 raw Git metadata threat matrix。
- Produces: `RawGitLayoutInspectorV1.inspect(boundary, subject_commit: ProductGitObjectIdV1) -> RawGitInspectionResultV1`、`CanonicalLogicalRepositoryTopologyV1.from_inspection(inspection)`、`build_product_source(boundary, inspection, subject_commit: ProductGitObjectIdV1) -> ProductRepositorySourceV1`、`finalize_raw_git_proof(inspection, container_digest) -> RawGitInspectionProofV1`、`ProductSourceLoader.load(source, proof_ref, expected_source_digest) -> ValidatedProductRepositoryContainerV1`；`build_product_source.py read-head-sha1/build` CLI 只接收/输出小写 40-hex product commit，不提供 algorithm 选择参数，接收 checkout/gitdir/commondir roots、handoff store/ID、producer 与可重复 consumer task、offline 标志，在同一进程严格执行 open handles→raw inspect→派生 path-free logical topology→build→finalize proof→publish-local。source artifact 不反向绑定 replay policy；未经 Task 11 proof/container 正式验证不能称为 verified source。

- [ ] **Step 1:** 写 `test_real_linked_worktree_is_inspected_before_normalization_and_proof_binds_container`：真实 `.git` regular pointer、相对/绝对 gitdir/commondir 写法、精确换行/backlink/control bytes 先由 inspector 在 owning-FD 进程内读取；用 test spy 证明这些 bytes 实际被检查，但不出现于 proof/container/build-record/handoff 任一序列化字段或 digest 输入。proof 绑定 path-free logical-topology/profile/rules/budgets/typed SHA-1 product subject/container；只保留 sha1/40 repository/object ID 正例，把 SHA-256 product repository/OID、39/41/64 位、`extensions.objectFormat` 任意出现及带 algorithm 段 subject 都在 traversal 前拒绝。双根测试中同 typed commit 在不同绝对空根、保持相同逻辑关系与形式类别时，只要求 canonical topology/proof 语义字段、container/build-record/handoff 一致，不要求两个根的原始 pointer/control bytes 相同或其 digest 存在。写 path swap/capability serialization 反例；再逐项覆盖规格 §8.2 pointer/ref/config/objects/sidecar/预算/unknown class、非法 UTF-8/NUL/多行/换行、越界/symlink/case-fold/alias/二次重定向/循环/backlink 不精确，全部在规范化或 payload copy 前拒绝；伪造 proof、错 inspector/profile/rule/budget/logical-topology/container binding 也拒绝。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_product_source.py`；预期 RED。
- [ ] **Step 3:** 唯一 source-runner 进程打开并持有三个 handles 后，先调用完整 `RawGitLayoutInspectorV1`；只有 inspection result 成功才把已验证关系派生为 `@checkout/@gitdir/@commondir` 的 path-free canonical logical topology，再固定使用自有有界 SHA-1 loose/pack/idx/tree reader 复制 subject tree 的精确 tracked bytes；不存在产品 SHA-256 分支。原始 pointer/control bytes 仅留在 inspection 调用栈/内存中，不写 artifact、不做 digest；ignored/untracked 不捕获。finalize proof 后，将 container/build-record/proof 三者作为 blobs 交给 authenticated handoff `publish-local`，全部完成再关闭 handles。普通 `.git` directory 仍只发一份 control tree；任何 handle identity/mount/race 变化拒绝。
- [ ] **Step 4:** container manifest 首项绑定 subject、normalized boundary、`boundaryDigest/trackedCheckoutDigest/controlMetadataDigest/sourceDigest`；proof sidecar 的 `logicalTopologyDigest` 只是 `CanonicalLogicalRepositoryTopologyV1` 的规范摘要，并绑定 inspector/profile、全部规则/预算结果、subject/source commit 与最终 container digest；build record 绑定 proof ref/digest、subject/tree/container/source/boundary/logical topology，不含原始 bytes/digest、路径/device/inode/mtime。handoff bindings 恰含 `product-source-container`、`product-source-build-record`、`raw-git-inspection-proof`、`boundary-topology`、`tracked-checkout`、`control-metadata`、`source` 七项。loader 重算 container/build-record/proof digests；重跑 tests，预期原始布局恶意 bytes 拒绝、proof/container 换绑、双根 canonical 语义一致性与逃逸反例 GREEN。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{raw_git_inspection.py,product_source.py} .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_product_source.py && git commit -m "feat: bind canonical raw Git product sources"`

### Task 11: 从 verified source 构造 sanitized Git checkout

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/git_subject.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_git_subject.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e2_suite.py`

**Interfaces:**
- Consumes: Task 7 包内 Git、Task 10 `ValidatedProductRepositoryContainerV1`、`RawGitInspectionProofV1` 及 normalized `@checkout/@gitdir/@commondir` boundary；不接受裸 path/cwd/bundle、缺 proof 或 container 外 control root。
- Produces: `GitMetadataInspector.inspect(container, raw_proof, build_record) -> VerifiedProductRepositorySourceV1`、`SanitizedGitView.build(source, commit: ProductGitObjectIdV1, toolchain_git, destination) -> FixedCheckout`。

- [ ] **Step 1:** 写 proof formal-verification tests：缺失/未知/伪造 inspector/profile/rule/budget/path-free logical-topology digest、proof/build-record/container/typed subject/source commit 任一换绑，以及非 sha1、非 40hex、任何 `extensions.objectFormat` 或 SHA-256 product object 任一出现，均在包内 Git spawn 前拒绝；显式拒绝 proof/build-record 中出现 raw pointer/control bytes、其 digest 或宿主路径字段。再对 canonical container 覆盖 boundary graph、tracked tree、config/packed-refs/refs/objects 预算、unknown/dangerous config、alternates/http-alternates、replace/grafts/shallow/promisor、sidecars、symlink/duplicate/case-fold、外部 object source 与 race。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_git_subject.py`；预期旧 host Git path RED。
- [ ] **Step 3:** `GitMetadataInspector.inspect(container, raw_proof, build_record)` 先重算 proof 与 container bytes，核对 raw inspector 的固定版本/profile/完整规则集/预算结果、path-free canonical logical-topology digest、subject/source commit 和 container binding；再对 canonical `repository/gitdir/common` 做后置 bounded checks。全部通过后才以包内 Git `index-pack --strict`/`fsck --strict` 构造 sanitized bare view并重建 checkout；原始危险记录仍不进入 view。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_git_subject.py`；预期 API 无 repository_root/cwd。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{git_subject.py,verifiers/e2_suite.py} .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_git_subject.py && git commit -m "feat: sanitize verified product sources"`

### Task 12: 闭合真实 pytest/Vitest reporter

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/reporters.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_reporters.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/reporters/pytest-pass.json`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/reporters/vitest-pass.json`

**Interfaces:**
- Consumes: Tasks 2、3 与 reporter 实际源码。
- Produces: `parse_pytest_json_v1`、`parse_vitest_json_v1`。

- [ ] **Step 1:** 写 pytest zero/deselected/exit/count 与 Vitest expected pass+same-file skipped/extra/skip/wrong-name tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_reporters.py`；预期 RED。
- [ ] **Step 3:** 实现 expected-set closure，禁止持久化任意 report ID。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_reporters.py`；预期 schema/count 为 `REPORT_SCHEMA_INVALID`，selection 为 `TEST_SELECTION_MISSING`。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/reporters.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_reporters.py .scratch/unified-remix-canvas-generation/acceptance/fixtures/reporters/{pytest-pass.json,vitest-pass.json} && git commit -m "feat: parse trusted replay reports"`

### Task 13: 冻结 containment 协议与唯一内核机制（独立设计门）

**Files:**
- Create: `docs/superpowers/specs/2026-10-02-e2-v2-linux-containment-profile.md`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/__init__.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/base.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/protocol.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/handoff_domain.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_protocol.py`

**Interfaces:**
- Consumes: Tasks 2、3、7。
- Produces: 精确七方法 API、canonical supervisor schema、proof digest、已批准 `linux-cgroup-v2-seccomp-notify/v1` gate；并冻结 `CredentialIsolatedHandoffRunnerV1.open_pty(role, session_label, profile) -> IsolatedPtyHandleV1`，role 只允许 `producer|orchestrator`，两者不同 UID/GID/user+mount+PID namespace，FD/capability allowlist 只能由 profile 按 session label 解析而不能由调用方自报，唯一跨域 payload 为 authenticated export-request CAS ref。仅 `task22-producer` 可额外持有 profile 固定的 provisioner client executable 与 broker RPC FD，但 capability MAC key、shared commondir 删除权和任意路径创建权始终只在 root broker，不能作为跨域 payload。

```python
class ReplayContainmentV1(Protocol):
    def create_session(self, request: CreateSessionRequestV1) -> SessionHandleV1: ...
    def create_invocation(self, session: SessionHandleV1, request: CreateInvocationRequestV1) -> InvocationHandleV1: ...
    def spawn_into(self, invocation: InvocationHandleV1, request: SpawnRequestV1, stdout: SafeSink, stderr: SafeSink) -> SpawnHandleV1: ...
    def terminate_all(self, invocation: InvocationHandleV1, reason: TerminationReason) -> TerminationProofV1: ...
    def prove_empty(self, invocation: InvocationHandleV1) -> EmptyProofV1: ...
    def destroy_invocation(self, invocation: InvocationHandleV1) -> InvocationDestroyProofV1: ...
    def destroy_session(self, session: SessionHandleV1) -> SessionDestroyProofV1: ...
```

- [ ] **Step 1:** 写消息 RED tests；仅允许 `hello/session-created/invocation-created/spawned/exec/write/exit/terminated/empty/invocation-destroyed/session-destroyed/error`，每条含 schema/sessionId/invocationId/seq/kind/scope/objectId/payload/prevDigest/digest；`scope` 仅 `session|invocation` 且必须与 `objectId`、消息 kind 一致。handoff-domain contract tests 断言 role/UID/GID/namespace/profile digest 不同，producer FD allowlist 不含 signing key/agent，orchestrator 只能接收 broker 打开的只读 signing-key FD，任何 path/value/env 形式 key 参数 schema reject；只有 profile 中 `task22-producer` 会话可获得 provisioner RPC，且仅允许固定 create/destroy/verify 方法、固定 project/executor/profile。伪造 session label/token、修改 capability 字段、请求删除 shared commondir，或把 provisioner FD/客户端转交 Task 27 producer/orchestrator 均 schema reject。
- [ ] **Step 2:** 冻结无丢失与权限模型：继承 `SOCK_SEQPACKET`；seq 从 0 连续且 digest chain；EOF 仅 `session-destroyed` 后；root-owned supervisor 管理 delegated cgroup/namespace/seccomp listener，child 入组后降权；binary SHA-256、ELF build-id、Cargo.lock、executor image digest 绑定 replay policy/receipt。
- [ ] **Step 3:** 冻结唯一机制：clone3 首次入组；seccomp user notification 观察 execve/execveat 与固定 write/network syscall，拒绝 io_uring/bpf/perf 旁路；只读 mounts+writable allowlist；pidfd/waitid+cgroup.kill+populated=0+rmdir。handoff domain 另固定 root-owned broker、预配 `trusted-handoff-producer`/`trusted-handoff-orchestrator` UID/GID、独立 user+mount+PID namespace、`/proc`=`hidepid=2,subset=pid`、`PR_SET_DUMPABLE=0`、空 capability bounding set、`no_new_privs`、exec 前 `close_range(3, UINT_MAX, CLOSE_RANGE_UNSHARE)` 后按 role 重开白名单 FD，以及 seccomp 拒绝 ptrace/process_vm_readv/process_vm_writev/pidfd_getfd/open_by_handle_at。producer mount table不含 key/agent；orchestrator 才以 0400、orchestrator UID 所有的 key mount no-follow 打开 FD。worktree provisioner 通过 producer-only 已认证 `SOCK_SEQPACKET` RPC FD 服务；broker 用不挂载给 child 的 secret 对 canonical capability fields+executor/profile identity+lifecycle nonce 做 MAC，create 成功前不发布 JSON，destroy 只按已认证 capability unlink 对应 backlink 并删除独占 checkout/linked gitdir，永不递归删除或重建 shared commondir/object store。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_protocol.py`；预期 loss/duplicate/reorder/unknown/overflow/bad digest/binary mismatch 全部 fail closed，并强制每次 invocation 顺序为 `terminate_all → prove_empty → destroy_invocation`，下一 invocation 只能在前一 leaf destroyed 后创建，最后才 `destroy_session`。
- [ ] **Step 5:** `git add docs/superpowers/specs/2026-10-02-e2-v2-linux-containment-profile.md .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_protocol.py && git commit -m "design: freeze trusted replay containment protocol"`；独立审查 0 Critical/Important 才进入 Task 14。

### Task 14: 实现获批 Linux cgroup v2 backend

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux_cgroup_v2.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/vm_cgroup.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/run_handoff_domain.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/trusted_worktree_provisioner.py`
- Create from governance input: `.scratch/unified-remix-canvas-generation/acceptance/trust/handoff-execution-domains.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux-supervisor/Cargo.toml`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux-supervisor/Cargo.lock`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux-supervisor/src/main.rs`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_linux.py`

**Interfaces:**
- Consumes: Task 13 approved protocol，不得改机制/schema。
- Produces: `LinuxCgroupV2Containment`、`CredentialIsolatedHandoffRunnerV1`、`run_handoff_domain.py exec-pty/verify-role` 与 profile 固定、只通过 broker RPC 的 `trusted_worktree_provisioner.py`；无 guest proof 的 VM 固定 unavailable。`exec-pty` 必须由 root-owned supervisor broker 先把 session label 解析为固定 role/capability set，再切换到 profile 固定 UID/GID/namespace 后 exec shell；调用方不能自报 UID、mount、key path、provisioner secret 或额外 FD。仅 task22-producer measurement 必须绑定 provisioner client executable/digest、broker key id 与 RPC peer credential；Task 27 producer 和 orchestrator measurement 必须证明这些项均不可见。

- [ ] **Step 1:** 写 leader 0/nonzero/timeout × double-fork/setsid/setpgid/closed-stdio、census/protected digest/macOS unavailable tests；真实 handoff-domain integration 分别从 producer 尝试 open signing-key path、connect agent socket、读取 orchestrator `/proc/<pid>/environ`、读取未列入白名单的继承 sentinel FD、ptrace/process_vm_readv/pidfd_getfd，全部必须得到路径不存在/`EBADF`/`EPERM` 且 broker transcript 证明不同 UID/GID/user+mount+PID namespace。orchestrator 正例只能通过注入 FD 签名；把 key path/value、额外 FD、同 UID profile、伪造/错配 session label/capability 或未认证 manifest 传入均在 child exec/sign=0 时拒绝。provisioner integration 另覆盖 Task 27 producer/orchestrator 请求 provisioner、客户端/path/digest/RPC peer 被替换、伪造/重放/字段突变 token、create 中途失败、checkout/gitdir identity 替换、backlink 残留与 shared commondir/object identity 改变；前五类零 worktree publish，后四类 destroy/verify 非零且不得触及 shared store。
- [ ] **Step 2:** 运行 `cargo test --locked --manifest-path .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux-supervisor/Cargo.toml && backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_linux.py`；预期 RED。
- [ ] **Step 3:** 严格实现 Task 13，无 fallback；proof 缺失为 `E2_CONTAINMENT_CLEANUP_FAILED`。`run_handoff_domain.py verify-role` 输出 canonical domain measurement（profile/supervisor digest、role、UID/GID、user/mount/PID namespace inode、proc mount flags、seccomp/capability/dumpable、closed-FD census，以及适用角色的 provisioner client/RPC peer 或 signing-key FD measurement），`exec-pty` 仅在 measurement 匹配后进入长生命周期 PTY；signing-key FD 只在 orchestrator allowlist，producer 永远无对应 mount/FD。provisioner client 不持有 MAC key，只把 canonical request 送给 root broker；broker 在 profile 固定 entry/byte budgets 内 no-follow 枚举既有 shared objects，按相对路径、mode、size、SHA-256 排序生成 baseline digest，超预算即零创建；随后原子 create+签发 capability，或在任何中途失败时完整回滚且不发布 JSON。
- [ ] **Step 4:** 重跑 `cargo test --locked --manifest-path .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux-supervisor/Cargo.toml && PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_linux.py`；预期首个 exec 至 invocation-destroyed/session-destroyed transcript 连续，leaf rmdir proof 在下一个 invocation 前成立，后代全清；producer 的 key/agent/env/FD/ptrace 五类探针全部不可达，orchestrator signing-key FD 正例通过。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment .scratch/unified-remix-canvas-generation/acceptance/tools/{run_handoff_domain.py,trusted_worktree_provisioner.py} .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-execution-domains.json .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_linux.py && git commit -m "feat: contain replay with cgroup v2"`

### Task 15: 编排 TrustedReplaySession

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/replay.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e2_suite.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py`

**Interfaces:**
- Consumes: Tasks 2–14；只接受 suite、`VerifiedProductRepositorySourceV1`、`ReplayPolicyBindingV1`、containment；`AcceptanceOperationSnapshotV1` 只由调用方用于操作门和前后 recheck，不进入 replay 输入或 receipt。
- Produces: `TrustedReplaySession.replay(...) -> ReplayResult`，含稳定 replay-policy-binding digest 及全部 input/proof/report/output digests。

- [ ] **Step 1:** 写严格顺序、裸 path/cwd 类型拒绝、lock/install/entrypoint/census tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py`；预期旧 host-tool/PGID RED。
- [ ] **Step 3:** 实现 source/toolchain→checkout→views→setup/test leaf；每 leaf 无论 leader 结局都严格执行 terminate→empty→destroy_invocation 后才允许下一个 invocation，全部 report 闭合后 destroy_session。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_{toolchain,platform_identity,python_dependencies,npm_dependencies,product_source,git_subject,reporters,containment_protocol,containment_linux}.py`；预期 GREEN 且无 `shutil.which/os.killpg/receipt-v1`。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{replay.py,verifiers/e2_suite.py} .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py && git commit -m "feat: orchestrate trusted replay sessions"`

### Task 16: 建立 E1/E2 canonical collectors、generic dispatch 与 CLI

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/collection.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/artifact_schemas.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/cli_results.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/e1_collection.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/registration_preflight.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e2_suite.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/registration.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_collection_v2.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_evidence_bridge_cli.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_collect_result_capture.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_registration_batch_preflight.py`

**Interfaces:**
- Consumes: Tasks 2–5、15，既有 E1 Git verifier、CAS/registration。
- Produces: canonical E1/E2 artifact schemas、collectors/dispatch、`RegistrationBatchPreflight.validate(root, product_handoff, policy_handoff, expected_policy_commit, candidates) -> ValidatedRegistrationBatchV1`，以及 `AtomicBatchRegistrar.commit(validated_batch)`；generic CLI `collect`/`register`/`register-batch`。`register-batch` 在同一进程构造 typed preflight，批量验证 handoffs/commit/source/dependencies/profile/verifier/subject/policy 与全部 artifact canonical bytes/receipt bindings，成功后才一次原子更新 catalog 七 pointers；任一候选失败时 catalog/pointers 写入数为零。collect result JSON/capture/`validate-many` 合同保持不变；`capture_collect_result.py read-catalog-ref` 以 profile/verifier/subject 精确寻址，重算 catalog pointer 与 CAS canonical bytes 后只输出唯一 `sha256:<64hex>` ref，供后续晋级显式传参。

- [ ] **Step 1:** 保留 E1/E2 collect/capture/register RED tests；新增 batch preflight RED：product/policy handoff、typed fixed policy commit、固定 sha1/40 product subject/source binding、container/proof/dependency/network-proof ref、profile/verifier/subject、replay-policy binding、artifact canonical bytes/receipt semantic binding 任一不符，或产品出现 SHA-256/objectFormat、七候选缺失/重复/多余时，`preflightSuccess=0`、catalog/pointer writes=0；全部正确时返回深不可变、固定顺序 batch，register-batch 恰一次 atomic catalog commit。另为 `read-catalog-ref` 写缺失/重复/错 subject/悬空 CAS/篡改 bytes/未知字段 RED，成功 stdout 必须只有规范 ref。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_collection_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_collect_result_capture.py .scratch/unified-remix-canvas-generation/acceptance/test_registration_batch_preflight.py`；预期 façade/result codec/preflight RED。
- [ ] **Step 3:** 实现 collectors/result codec 与只读 `read-catalog-ref`；后者 strict parse 整个 catalog，精确匹配 profile/verifier/subject，重开对应 CAS object、校验 regular/no-symlink/no-hardlink/canonical bytes 与 ref digest 后才输出。实现 typed batch preflight，必须从验证对象重算所有语义而非信任 CLI 自报 digest。`AtomicBatchRegistrar` 在同一 process/lock 下 recheck operation snapshot 与 validated batch digest，以 temp catalog+fsync+atomic replace 一次提交七 pointers；不能逐项产生可见中间状态。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_collection_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_evidence_bridge_cli.py .scratch/unified-remix-canvas-generation/acceptance/test_collect_result_capture.py .scratch/unified-remix-canvas-generation/acceptance/test_registration_batch_preflight.py`；预期 collect CAS write 0/1、capture 精确返回 ref、`read-catalog-ref` 对污染/悬空/篡改零输出且成功只返回一行 ref、任一 batch candidate 错误均零 catalog 写、完整 batch 只出现全七或全零。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{artifact_schemas.py,cli_results.py,e1_collection.py,collection.py,registration.py,registration_preflight.py,verifiers/e2_suite.py} .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py .scratch/unified-remix-canvas-generation/acceptance/test_{trusted_evidence_collection_v2,evidence_bridge_cli,collect_result_capture,registration_batch_preflight}.py && git commit -m "feat: collect and register canonical E1 E2 artifacts"`

### Task 17: 接入 workflow 与原子 projection

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/evaluation.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/projection.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/runtime.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/validate_acceptance.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_workflow_v2.py`

**Interfaces:**
- Consumes: Tasks 2、4、15–16。
- Produces: `evaluate_operation`、`AtomicContractProjector.commit(expected_operation_snapshot, overrides)`、`PromotionTransaction.run(request, policy_cache_capability) -> PromotionTransactionResultV1`，以及 `validate_acceptance.py promote-transaction/read-result`。`promote-transaction` 在唯一进程中 no-follow 打开 finalized policy cache 为 owning dirfd，严格重建 SHA-1-only `ProductGitObjectIdV1` subject、独立 `GitObjectIdV1` policy commit 与内存 `ValidatedPromotionBatchV1`，构造初始 `AcceptanceOperationSnapshotV1`，持有 capability 直到同一 contract lock 内完成 cache/pointer/record/manifest/seal/CAS/catalog/contract 最后复核；随后先在内存形成 C01–C03 candidate contract，按其 digest 构造 post-projection snapshot 并 reconcile，只有最终状态确定后才一次 temp+fsync+atomic replace，绝不暴露 promotion/reconcile 中间态。任何 preflight JSON 只允许写到 `--diagnostic-output-root` 供审计，绝不被重新读入或充当授权 token。`read-result` 只从 strict `PromotionTransactionResultV1` 返回请求字段。

- [ ] **Step 1:** 写全操作 barrier、validate read-only、operation-snapshot CAS、稳定 replay-policy binding、fsync/replace、register 不晋升 tests；新增 transaction RED：缺任一 explicit binding/ref、product subject 非 sha1/40、出现 SHA-256/objectFormat、初始 snapshot race、最终 lock 内 snapshot race均 contractWrites=0；在内存 preflight 完成与 projection 之间分别替换 diagnostic preflight file、cache path/root identity、pointer、manifest、seal、record/CAS bytes，除 diagnostic 文件替换必须完全无效外，其余全部 fail closed 且 contractWrites=0。成功 transaction 恰一次原子写 C01–C03、在同一进程用投影后新 snapshot reconcile；`read-result` 对未知字段、非 canonical bytes、错 operation 或伪造 digest 零输出。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_workflow_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_acceptance_contract.py`；预期 RED。
- [ ] **Step 3:** 阶段 A 在 `PromotionTransaction` 内构造初始 `AcceptanceOperationSnapshotV1` 与内存 typed batch，阶段 B 只把其绑定的 `ReplayPolicyBindingV1` 传给 replay；外部 diagnostic preflight/result 文件不参与任何 equality 或 authorization 判定。transaction 从 finalized cache capability 重新派生 policy commit、`binding.product-source`、network proof、replay binding，并通过 Task 2 `read_product_subject` 从 verified product-source CAS 派生 typed subject commit；命令行的显式值只作为 expected values 与派生结果比较。进入同一 contract lock 后，对仍持有的 cache dirfd、root identity、ready marker、transfer/seal trust root、pointer/record/CAS、catalog、contract 与 operation snapshot 做最后复核；在内存 candidate contract 上完成 C01–C03 projection、post-projection snapshot 与 reconcile，最终 contract bytes 确定后才单次 temp+fsync+atomic replace，最后输出 canonical transaction result。禁止任何 `promote --validated-preflight FILE` 或独立 `reconcile` 写接口。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_workflow_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_acceptance_contract.py`；预期 138 项、四状态、空不适用不变。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{evaluation.py,projection.py,runtime.py} .scratch/unified-remix-canvas-generation/acceptance/validate_acceptance.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_workflow_v2.py && git commit -m "feat: gate acceptance with trusted replay"`

### Task 18: 构建真实 production toolchain/platform artifact 并执行负向 fixture

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/input-manifest.json`
- Create from governance input: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/PROVENANCE.json`
- Create generated artifact: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/toolchain.tar.zst`
- Create generated record: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json`
- Create generated record: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/platform-identity.json`
- Create generated artifact: `.scratch/unified-remix-canvas-generation/acceptance/trust/toolchains/sha256/<aa>/<digest>`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_toolchain_platform.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/README.md`

**Interfaces:**
- Consumes: Tasks 1、6–7、14、17。
- Produces: 独立安全审核后可供 Ticket 23 使用的 build record 中 `artifactRef`、`platformIdentityDigest`、`supervisorDigest`、Git 2.53.0 logical entrypoint 与 executable SHA-256，独立规范 `out/platform-identity.json`，以及 `build_e2_toolchain_fixture.py read-artifact-ref/materialize/materialize-entrypoint`；`read-artifact-ref` 严格解码闭合 build-record schema、重算 record 与 content-store bytes 后只输出小写 `sha256:<64hex>`，`materialize` 接受该 ref、当次空 output root 与 `--offline`，`materialize-entrypoint --name git --field path|digest` 只从 freshly materialized owning root 返回同一固定 Git binary/digest。恶意变体只在临时 fixture 根生成，绝不注册。

- [ ] **Step 1:** 写 archive→`E2_TOOLCHAIN_ARCHIVE_INVALID`、uv/advisory→`E2_TOOLCHAIN_CLOSURE_INVALID`、ABI→`E2_TOOLCHAIN_PLATFORM_MISMATCH`、PATH marker→`E2_HOST_TOOL_FALLBACK`。
- [ ] **Step 2:** 在登记 Linux executor 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_toolchain_platform.py`；预期真实包 RED。
- [ ] **Step 3:** `input-manifest.json` 按逻辑名列出每个本地输入路径、expected SHA-256、mode、target member，`PROVENANCE.json` 固定来源 URL/release/许可证/审核者/取得方式；builder 先完整验证 manifest/provenance 和 Task 14 supervisor binary/Cargo.lock、security snapshot、platform measurement，任一外部 bytes 缺失或不匹配立即停止，代码中不得有 HTTP client/download fallback。精确运行 `python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py --input-manifest .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/input-manifest.json --provenance .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/PROVENANCE.json --output .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/toolchain.tar.zst --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --platform-identity .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/platform-identity.json --content-store-root .scratch/unified-remix-canvas-generation/acceptance/trust/toolchains --offline`，只生成唯一 canonical positive artifact、完全相同 bytes 的 content-store object、build record 与独立 platform identity；record 精确绑定后二者 digest，各负向变体只从它在临时根逐项突变。
- [ ] **Step 4:** 运行 `sha256sum .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/toolchain.tar.zst .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/platform-identity.json`，并与 build record 的 artifact/platform identity digests、content-store ref 完全一致；另在两个空 output roots 运行 `materialize`，要求展开树 byte/mode digest 一致且拒绝非空 root。分别调用 `materialize-entrypoint --name git --field path` 与 `--field digest`，重算 executable SHA-256、执行固定 version/build-identity probe 并与 record 一致；篡改 binary、同版本异 bytes、宿主 PATH 同名 Git 都拒绝。重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_toolchain_platform.py`；预期正负例 GREEN 并记录真实 digest。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain .scratch/unified-remix-canvas-generation/acceptance/trust/toolchains .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_toolchain_platform.py .scratch/unified-remix-canvas-generation/acceptance/README.md && git commit -m "build: freeze trusted replay toolchain"`

### Task 19: 真实 Python 离线安装与攻击 fixture

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_python_fixture.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/python/input-manifest.json`
- Create from governance input: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/python/PROVENANCE.json`
- Create generated artifact: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/python/out/dependency-repository.tar.zst`
- Create generated record: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/python/out/build-record.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_python.py`

**Interfaces:**
- Consumes: Tasks 8、15、18。
- Produces: 真实 wheel-only view/install/report fixture；不供 Ticket 23 复用 digest。

- [ ] **Step 1:** 写 probe cwd/env/script/cache/extra child→`E2_PYTHON_SDIST_FORBIDDEN`；project/workspace/build→同码；恶意 entry-point→`E2_SETUP_FAILED` 且 canary/write-set 不变；DNS/socket→`E2_LIVE_REGISTRY_ACCESS`；entrypoint replacement→`E2_DERIVED_ENTRYPOINT_INVALID`。
- [ ] **Step 2:** 在登记 Linux executor 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_python.py`；预期 repository/lock RED。
- [ ] **Step 3:** manifest 逐项固定 wheel/lock/reporter 输入路径、SHA-256、target，provenance 固定来源/release/许可证；精确运行 `python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_python_fixture.py --input-manifest .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/python/input-manifest.json --provenance .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/python/PROVENANCE.json --output .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/python/out/dependency-repository.tar.zst --offline`。任一外部 bytes 缺失/摘要不符立即停止且不得下载；随后用真实 uv 0.11.15、两独占 cache 离线 venv/sync，lock 前后 sha256 相同，真实 reporter 1.5.0。
- [ ] **Step 4:** 运行 `sha256sum .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/python/out/dependency-repository.tar.zst` 并与 build record 一致；重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_python.py`；正例 GREEN、每个负例唯一错误码且 test spawn=0。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_python_fixture.py .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/python .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_python.py && git commit -m "test: validate offline Python replay"`

### Task 20: 真实 npm、reporter 与输出 fixture

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_npm_fixture.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/npm/input-manifest.json`
- Create from governance input: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/npm/PROVENANCE.json`
- Create generated artifact: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/npm/out/dependency-repository.tar.zst`
- Create generated record: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/npm/out/build-record.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_npm_reporter.py`

**Interfaces:**
- Consumes: Tasks 3、9、12、15、18。
- Produces: 真实 cacache/Vitest/redaction fixture；不供 Ticket 23 复用 digest。

- [ ] **Step 1:** 写 cache篡改→`E2_DEPENDENCY_VIEW_INVALID`；DNS/socket→`E2_LIVE_REGISTRY_ACCESS`；vitest replacement→`E2_DERIVED_ENTRYPOINT_INVALID`；expected skip/extra→`TEST_SELECTION_MISSING`；count contradiction→`REPORT_SCHEMA_INVALID`；尾部秘密→`OUTPUT_REDACTION_UNSAFE`。
- [ ] **Step 2:** 在登记 Linux executor 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_npm_reporter.py`；预期 container RED。
- [ ] **Step 3:** manifest 逐项固定 tgz/package-lock/reporter 输入路径、SHA-256、target，provenance 固定来源/release/许可证；精确运行 `python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_npm_fixture.py --input-manifest .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/npm/input-manifest.json --provenance .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/npm/PROVENANCE.json --output .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/npm/out/dependency-repository.tar.zst --offline`。任一外部 bytes 缺失/摘要不符立即停止且不得下载；再用包内 Node/npm 离线 ci，直接加载 verified vitest.mjs 逐名运行。
- [ ] **Step 4:** 运行 `sha256sum .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/npm/out/dependency-repository.tar.zst` 并与 build record 一致；重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_npm_reporter.py`；每 invocation 1 passed+same-file skipped，receipt 只含 expected ID。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_npm_fixture.py .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/npm .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_npm_reporter.py && git commit -m "test: validate offline npm replay"`

### Task 21: 真实 containment 无丢失与逃逸 fixture

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_containment_fixture.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/containment/escape_helper.c`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/containment/input-manifest.json`
- Create from governance input: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/containment/PROVENANCE.json`
- Create generated artifact: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/containment/out/escape-helper`
- Create generated record: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/containment/out/build-record.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_containment.py`

**Interfaces:**
- Consumes: Tasks 13–14、18。
- Produces: 首个 untrusted exec 至 empty/destroyed 的真实 transcript/proof。

- [ ] **Step 1:** 写 leader outcomes/escape reaped、seq/loss/overflow/binary mismatch→`E2_CONTAINMENT_CLEANUP_FAILED`、无能力→`E2_CONTAINMENT_UNAVAILABLE`、socket→`E2_LIVE_REGISTRY_ACCESS`。
- [ ] **Step 2:** 在登记 Linux executor 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_containment.py`；预期 helper RED。
- [ ] **Step 3:** manifest 固定 `escape_helper.c`、登记 compiler binary/sysroot/flags 的路径与 SHA-256，provenance 固定 compiler/source 许可证；精确运行 `python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_containment_fixture.py --input-manifest .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/containment/input-manifest.json --provenance .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/containment/PROVENANCE.json --output .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/containment/out/escape-helper --offline`。任一外部 bytes 缺失/摘要不符立即停止且不得下载；运行 `sha256sum .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/containment/out/escape-helper` 与 build record 对照后，重跑 Linux integration；预期 membership/termination/empty/invocation-destroyed/next-invocation/session-destroyed/digest chain 闭合。
- [ ] **Step 4:** macOS 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_containment.py -k unavailable`；预期 unavailable，无 PGID fallback。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_containment_fixture.py .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/containment .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_containment.py && git commit -m "test: prove replay containment cleanup"`

### Task 22: 准备并冻结 Ticket 23 产品主体

**Files:**
- Create in isolated product worktree: `frontend/src/CreativeCanvasTicket23Acceptance.test.tsx`
- Modify in isolated product worktree: `backend/requirements.lock`
- Generated external CAS blobs/record: `$TRUSTED_HANDOFF_STORE_ROOT/sha256/<aa>/<remaining-62-hex>` (product container、build record、raw inspection proof、handoff record；not tracked)
- Generated external pointer: `$TRUSTED_HANDOFF_STORE_ROOT/pointers/ticket-23-product-source-v1.json` (not tracked)
- Generated immutable export request: `$TRUSTED_HANDOFF_STORE_ROOT/sha256/<aa>/<remaining-62-hex>` 与 `$TRUSTED_HANDOFF_STORE_ROOT/export-requests/ticket-23-product-source-v1.json` no-replace pointer (not tracked)
- Generated external per-consumer transports: target stores 中 `transfers/ticket-23-product-source-v1/{ticket-23-product-source-v1-c23,ticket-23-product-source-v1-c25,ticket-23-product-source-v1-c27,ticket-23-product-source-v1-c28}.json`、对应 `seals/` 文件与 ready markers (not tracked)
- Ephemeral only: producer-only 0700 `TASK22_BOUNDARY_PARENT/{registered-boundary.json,toolchain,git-home,git-xdg,empty-hooks,git-write-result.json}` 与 provisioner 专属 checkout/linked-gitdir；orchestrator-only 0700 `TASK22_ORCHESTRATOR_PARENT/{c23,c25,c27,c28}` stage roots。共享 commondir/object store 只验证 identity，绝不删除。两套 harness 各自用 `cleanup-owned-tree` 清理本域临时树，不共享 UID/GID、namespace、FD、变量、临时根或 trap，不跟踪、不跨 Task 复用。

**Interfaces:**
- Consumes: Tasks 7/18 的 `TrustedGitExecutorV1` 与 fixed Git entrypoint/digest、Task 14 credential-domain runner/profile 及 profile 固定的 provisioner client/digest/broker RPC capability、Task 10 registered-boundary builder CLI、Task 11 inspector contract 与产品实际源码；不消费 fixture digest。
- Produces: Task 22 producer-only credential domain 内部专用的真实 linked worktree 与 clean SHA-1 subject commit；provisioner canonical JSON 携带外部 broker 认证、不可伪造且不进入 artifact 的 `LinkedWorktreeOwnershipCapabilityV1`，只授权销毁本次专属 checkout、linked gitdir 与 backlink，并绑定创建时既有 shared object 的有界 canonical baseline inventory digest。外部 durable store 中 `ticket-23-product-source-v1` handoff，record kind=`product-source`、producerTask=`22`、consumerTasks=`[23,25,27,28]`，并绑定 product-source container/build-record/raw-inspection-proof/boundary-topology/tracked-checkout/control-metadata/source 七个 digest；另发布只含 record/pointer/consumer/binding 摘要、以完整规范 bytes SHA-256 内容认证的 immutable export request。orchestrator-only credential domain 只从该 request ref 重建四个独立 transports。四 pytest node IDs、结构两项/持久化五项/API 两项 Vitest fullName、两个 lock digests。ownership token、linked worktree path、store root 与 owning handles 均不进入 record/request 或跨 harness 作为数据传递。

- [ ] **Step 1:** 先写四组 RED。执行域：producer 无法 open orchestrator key、connect agent、读 orchestrator `/proc/<pid>/environ`、读非白名单继承 FD 或执行 ptrace/process_vm_readv/pidfd_getfd；UID/GID 或 user/mount/PID namespace 任一相同、`/proc` 非 `hidepid=2,subset=pid`、额外 capability/FD 或 producer 可见 key mount 均在 provision/build=0 时拒绝。orchestrator 拒绝 checkout/boundary/stage manifest path、自报 record digest、非 CAS/未认证 request ref 或 producer shell state。worktree 生命周期：canonical JSON 必须包含 profile 固定 provisioner id/digest、broker key id、broker MAC ownership token、专属 checkout/linked-gitdir identities、共享 commondir/object-store identities 与既有 object baseline inventory digest；替换 provisioner executable/RPC peer、错误/伪造/重放 token、任一已认证字段或 checkout/gitdir path/identity 替换、请求删除共享 commondir、destroy 后任一专属路径/backlink 残留、删除/替换任一 baseline object 或共享 object-store identity 变化全部 fail closed；本次 commit 新增 object 保留是正例，create 中途失败必须自行撤销且 JSON publish=0。Git 写入：恶意 system/global/local signing、`gpg.format`/program、`commit.gpgSign`、hooks、agent socket、`GIT_CONFIG_COUNT/KEY_n/VALUE_n/PARAMETERS` 都必须在 Git write spawn/canary=0 时拒绝；正例只用 Task 18 Git digest、空 config/home/hooks、固定 identity/time/locale、禁用 auto-GC/maintenance 与 `--no-gpg-sign --no-verify`。交接：两域唯一允许的数据是 source durable store 中 full-bytes SHA-256 重算通过的 `HandoffExportRequestV1` CAS ref。

  编排器必须以 `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/run_handoff_domain.py exec-pty --profile .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-execution-domains.json --role producer --session-label task22-producer` 启动 producer 长生命周期 PTY；该命令由 Task 14 root-owned broker 切换到 producer UID/GID/namespaces并先关闭全部非白名单 FD，下列首段、Steps 2–4 与 Step 5 producer 段全部写入返回的同一 terminal session。producer 不拥有 signing key mount/FD；唯一 EXIT trap 必须先 capability-destroy worktree、验证共享 commondir，再安全清理 parent，全部成功后才解除。`validate-boundary` 只接受 schema=`trusted-registered-repository-boundary/v1` 与嵌入的 `trusted-linked-worktree-ownership/v1`、恰好 checkout/linkedGitdir/sharedCommondir 三根及各自 identity，拒绝未知/缺失/重复字段、非绝对路径、控制字符、symlink 组件、owner/mode 不符、identity 重用/变化、common parent/`/tmp` 过宽根与不合法 topology；`read-boundary-field` stdout 恰为一个已验证路径，禁止 `eval`：

```bash
set -euo pipefail
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/run_handoff_domain.py verify-role \
  --profile .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-execution-domains.json \
  --expected-role producer --require-distinct-peer-credentials --require-isolated-user-mount-pid \
  --require-proc-hidepid --require-no-key-mount --require-no-agent-socket --require-closed-fds-from 3 --require-ptrace-denied \
  --require-provisioner-bin "$TRUSTED_WORKTREE_PROVISIONER" \
  --require-provisioner-digest "$TRUSTED_WORKTREE_PROVISIONER_DIGEST" \
  --require-provisioner-rpc-fd "$TRUSTED_WORKTREE_PROVISIONER_RPC_FD" \
  --require-provisioner-key-id "$TRUSTED_WORKTREE_PROVISIONER_KEY_ID"
test -z "${TRUSTED_HANDOFF_ORCHESTRATOR_SIGNING_KEY_FD:-}${E4_PRIVATE_KEY_FILE:-}${SSH_AUTH_SOCK:-}${SSH_AGENT_PID:-}${GIT_SSH:-}${GIT_SSH_COMMAND:-}${GIT_CONFIG_COUNT:-}${GIT_CONFIG_PARAMETERS:-}"
printf '%s\n' "$TRUSTED_WORKTREE_PROVISIONER_DIGEST" | grep -Eq '^sha256:[0-9a-f]{64}$'
test "$TRUSTED_WORKTREE_PROVISIONER_RPC_FD" -ge 3
TASK22_BOUNDARY_PARENT="$(mktemp -d)"
chmod 0700 "$TASK22_BOUNDARY_PARENT"
TASK22_BOUNDARY_JSON="$TASK22_BOUNDARY_PARENT/registered-boundary.json"
TASK22_TOOLCHAIN_ROOT="$TASK22_BOUNDARY_PARENT/toolchain"
TASK22_GIT_HOME="$TASK22_BOUNDARY_PARENT/git-home"
TASK22_GIT_XDG="$TASK22_BOUNDARY_PARENT/git-xdg"
TASK22_GIT_HOOKS="$TASK22_BOUNDARY_PARENT/empty-hooks"
TASK22_GIT_RESULT="$TASK22_BOUNDARY_PARENT/git-write-result.json"
TASK22_GIT_BIN=""
TASK22_GIT_DIGEST=""
cleanup_task22_producer() {
  local worktree_ok=0
  local parent_ok=0
  if test -e "$TASK22_BOUNDARY_JSON"; then
    if "$TRUSTED_WORKTREE_PROVISIONER" destroy-linked-worktree \
      --boundary-json "$TASK22_BOUNDARY_JSON" --ownership-schema trusted-linked-worktree-ownership/v1 \
      --broker-rpc-fd "$TRUSTED_WORKTREE_PROVISIONER_RPC_FD" \
      --expected-provisioner-digest "$TRUSTED_WORKTREE_PROVISIONER_DIGEST" \
      --expected-broker-key-id "$TRUSTED_WORKTREE_PROVISIONER_KEY_ID" \
      --preserve-shared-commondir --git-bin "$TASK22_GIT_BIN" --git-digest "$TASK22_GIT_DIGEST" \
      --git-home "$TASK22_GIT_HOME" --git-xdg "$TASK22_GIT_XDG" --hooks-root "$TASK22_GIT_HOOKS"; then
      if "$TRUSTED_WORKTREE_PROVISIONER" verify-destroyed \
        --boundary-json "$TASK22_BOUNDARY_JSON" --broker-rpc-fd "$TRUSTED_WORKTREE_PROVISIONER_RPC_FD" \
        --expected-provisioner-digest "$TRUSTED_WORKTREE_PROVISIONER_DIGEST" \
        --expected-broker-key-id "$TRUSTED_WORKTREE_PROVISIONER_KEY_ID" \
        --require-checkout-absent --require-linked-gitdir-absent \
        --require-backlink-absent --require-shared-commondir-present \
        --require-shared-object-identity-unchanged --require-shared-object-baseline-unchanged; then
        worktree_ok=1
      fi
    fi
  else
    worktree_ok=1
  fi
  if test "$worktree_ok" -eq 1; then
    if backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py cleanup-owned-tree --root "$TASK22_BOUNDARY_PARENT" --expected-mode 0700; then
      test ! -e "$TASK22_BOUNDARY_PARENT" && parent_ok=1
    fi
  fi
  test "$worktree_ok" -eq 1 && test "$parent_ok" -eq 1
}
trap 'cleanup_task22_producer' EXIT
mkdir -m 0700 "$TASK22_TOOLCHAIN_ROOT" "$TASK22_GIT_HOME" "$TASK22_GIT_XDG" "$TASK22_GIT_HOOKS"
chmod 0555 "$TASK22_GIT_HOOKS"
TASK22_TOOLCHAIN_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py read-artifact-ref \
  --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json \
  --content-store-root .scratch/unified-remix-canvas-generation/acceptance/trust/toolchains)"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize \
  --artifact-ref "$TASK22_TOOLCHAIN_REF" --output-root "$TASK22_TOOLCHAIN_ROOT" --offline
TASK22_GIT_BIN="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize-entrypoint --materialized-root "$TASK22_TOOLCHAIN_ROOT" --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --name git --field path)"
TASK22_GIT_DIGEST="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize-entrypoint --materialized-root "$TASK22_TOOLCHAIN_ROOT" --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --name git --field digest)"
printf '%s\n' "$TASK22_GIT_DIGEST" | grep -Eq '^sha256:[0-9a-f]{64}$'
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py verify-root \
  --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --project-id ai-video-reverse-engineer --producer-task 22
"$TRUSTED_WORKTREE_PROVISIONER" create-linked-worktree \
  --project-id ai-video-reverse-engineer --output-json "$TASK22_BOUNDARY_JSON" \
  --broker-rpc-fd "$TRUSTED_WORKTREE_PROVISIONER_RPC_FD" \
  --expected-provisioner-digest "$TRUSTED_WORKTREE_PROVISIONER_DIGEST" \
  --expected-broker-key-id "$TRUSTED_WORKTREE_PROVISIONER_KEY_ID" \
  --ownership-schema trusted-linked-worktree-ownership/v1 --exclusive-checkout --exclusive-linked-gitdir \
  --shared-commondir-policy preserve-and-measure --git-bin "$TASK22_GIT_BIN" --git-digest "$TASK22_GIT_DIGEST" \
  --git-home "$TASK22_GIT_HOME" --git-xdg "$TASK22_GIT_XDG" --hooks-root "$TASK22_GIT_HOOKS"
"$TRUSTED_WORKTREE_PROVISIONER" verify-capability \
  --boundary-json "$TASK22_BOUNDARY_JSON" --broker-rpc-fd "$TRUSTED_WORKTREE_PROVISIONER_RPC_FD" \
  --expected-project-id ai-video-reverse-engineer \
  --expected-provisioner-digest "$TRUSTED_WORKTREE_PROVISIONER_DIGEST" \
  --expected-broker-key-id "$TRUSTED_WORKTREE_PROVISIONER_KEY_ID" \
  --require-exclusive-checkout --require-exclusive-linked-gitdir --require-shared-commondir
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py validate-boundary \
  --boundary-json "$TASK22_BOUNDARY_JSON" --expected-schema trusted-registered-repository-boundary/v1 \
  --expected-ownership-schema trusted-linked-worktree-ownership/v1 \
  --require-owner-current --expected-mode 0700 --require-git-pointer-regular --require-clean
TASK22_PRODUCT_ROOT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py read-boundary-field --boundary-json "$TASK22_BOUNDARY_JSON" --field checkoutRoot)"
TASK22_GITDIR_ROOT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py read-boundary-field --boundary-json "$TASK22_BOUNDARY_JSON" --field gitdirRoot)"
TASK22_COMMONDIR_ROOT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py read-boundary-field --boundary-json "$TASK22_BOUNDARY_JSON" --field commondirRoot)"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py verify-boundary-identities \
  --boundary-json "$TASK22_BOUNDARY_JSON" --checkout-root "$TASK22_PRODUCT_ROOT" \
  --gitdir-root "$TASK22_GITDIR_ROOT" --commondir-root "$TASK22_COMMONDIR_ROOT"
```

之后阅读该产品根内的实际源码/测试，只补缺失结构 tests 与 wheel-only lock。
- [ ] **Step 2:** 写两个专用结构测试；以测试文件缺失为 RED 证据，不在依赖物化前运行产品测试或使用宿主临时安装。
- [ ] **Step 3:** 在 `(cd "$TASK22_PRODUCT_ROOT" && ...)` 中逐项静态核对 requirements lock 仅含 exact pin+SHA-256 wheel、package-lock v3 与两个专用测试名；不把静态核对冒充行为 GREEN。
- [ ] **Step 4:** 在同一 producer PTY 中仅通过 `TrustedGitExecutorV1` 形成产品 commit。wrapper 在首个 Git spawn 前重验 Task 18 Git binary/digest、domain/env、raw local config 与 ownership capability；任何 host/system/global/local signing、hook、filter、credential、agent 或 `GIT_CONFIG_*` 注入均零 spawn。精确执行：

```bash
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_git.py commit \
  --repository-root "$TASK22_PRODUCT_ROOT" --boundary-json "$TASK22_BOUNDARY_JSON" \
  --git-bin "$TASK22_GIT_BIN" --git-digest "$TASK22_GIT_DIGEST" \
  --home-root "$TASK22_GIT_HOME" --xdg-root "$TASK22_GIT_XDG" --hooks-root "$TASK22_GIT_HOOKS" \
  --path backend/requirements.lock --path frontend/src/CreativeCanvasTicket23Acceptance.test.tsx \
  --message "test: prepare ticket 23 trusted replay subject" \
  --author-name "Trusted Evidence Builder" --author-email trusted-evidence@invalid --author-date 2000-01-01T00:00:00Z \
  --committer-name "Trusted Evidence Builder" --committer-email trusted-evidence@invalid --committer-date 2000-01-01T00:00:00Z \
  --expected-object-format sha1 --no-gpg-sign --no-verify --require-clean-after --result "$TASK22_GIT_RESULT"
TASK22_PRODUCT_COMMIT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_git.py read-result --result "$TASK22_GIT_RESULT" --field commit.hex)"
printf '%s\n' "$TASK22_PRODUCT_COMMIT" | grep -Eq '^[0-9a-f]{40}$'
```

实际行为 RED/GREEN 固定由 Task 23 在产品专属依赖物化后执行；SHA-256 object format、`extensions.objectFormat` 或任何非 40-hex 在 raw traversal 前拒绝。
- [ ] **Step 5:** producer-only harness 只发布一份不含 transport identity、以完整规范 bytes SHA-256 内容认证的 record/pointer/export request；它们不带 orchestrator 签名，producer 也不执行 transport、不读取 signing key、不 fetch/import/finalize/read consumer seal。`build` 内部 source-runner 在 owning handles 生命期内完成 raw inspect、canonical build、proof 与 local no-replace publish，随后关闭 handles；`publish-export-request` 从 durable store 重开刚发布的 record/pointer/CAS，绑定固定 consumer set 后 no-replace 发布 request：

```bash
test "$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py read-head-sha1 --boundary-json "$TASK22_BOUNDARY_JSON" --checkout-root "$TASK22_PRODUCT_ROOT")" = "$TASK22_PRODUCT_COMMIT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py build \
  --boundary-json "$TASK22_BOUNDARY_JSON" --checkout-root "$TASK22_PRODUCT_ROOT" \
  --gitdir-root "$TASK22_GITDIR_ROOT" --commondir-root "$TASK22_COMMONDIR_ROOT" \
  --subject-commit "$TASK22_PRODUCT_COMMIT" --handoff-store-root "$TRUSTED_HANDOFF_STORE_ROOT" \
  --handoff-id ticket-23-product-source-v1 --producer-task 22 \
  --consumer-task 23 --consumer-task 25 --consumer-task 27 --consumer-task 28 --offline
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py publish-export-request \
  --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --handoff-id ticket-23-product-source-v1 \
  --expected-kind product-source --expected-producer-task 22 \
  --consumer-task 23 --consumer-task 25 --consumer-task 27 --consumer-task 28
cleanup_task22_producer
test ! -e "$TASK22_BOUNDARY_PARENT"
test ! -e "$TASK22_PRODUCT_ROOT"
test ! -e "$TASK22_GITDIR_ROOT"
test -d "$TASK22_COMMONDIR_ROOT"
trap - EXIT
exit
```

producer shell 完全退出且 capability destroy/验证成功后，编排器必须用全新的 `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/run_handoff_domain.py exec-pty --profile .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-execution-domains.json --role orchestrator --session-label task22-orchestrator --key-slot handoff-transport-v1` 启动 orchestrator-only PTY。root-owned broker 从外部 secret slot no-follow 打开 key，仅把只读 FD 注入 orchestrator；key path/value 不进入 controller environment/argv。该域只获得受控 source/target store roots、由固定 resolver 注入且匹配 stable request pointer 的 `$TRUSTED_PRODUCT_EXPORT_REQUEST_REF`、各 consumer destination/nonce/sequence/expiry 与 signing-key FD，不获得 producer checkout/boundary/临时路径。它自行创建 0700 stage parent/四个空子根并安装独立 trap；`verify-role` 证明新 UID/GID/namespaces/FD 后，`verify-export-request` 重算 request/record/pointer/CAS 且核对 SHA-1 product subject 与四 consumer，下面四行才执行。每行参数独立完整，不能跨行复用 manifest、seal、nonce 或 stage：

```bash
set -euo pipefail
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/run_handoff_domain.py verify-role \
  --profile .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-execution-domains.json \
  --expected-role orchestrator --require-distinct-peer-credentials --require-isolated-user-mount-pid \
  --require-proc-hidepid --require-signing-key-fd "$TRUSTED_HANDOFF_ORCHESTRATOR_SIGNING_KEY_FD" --require-ptrace-denied
test "$TRUSTED_HANDOFF_ORCHESTRATOR_SIGNING_KEY_FD" -ge 3
test -z "${TRUSTED_WORKTREE_PROVISIONER:-}${TRUSTED_WORKTREE_PROVISIONER_DIGEST:-}${TRUSTED_WORKTREE_PROVISIONER_RPC_FD:-}${TRUSTED_WORKTREE_PROVISIONER_KEY_ID:-}${TASK22_BOUNDARY_PARENT:-}${TASK22_BOUNDARY_JSON:-}${TASK22_PRODUCT_ROOT:-}${TASK22_GITDIR_ROOT:-}${TASK22_COMMONDIR_ROOT:-}"
printf '%s\n' "$TRUSTED_PRODUCT_EXPORT_REQUEST_REF" | grep -Eq '^sha256:[0-9a-f]{64}$'
TASK22_ORCHESTRATOR_PARENT="$(mktemp -d)"
chmod 0700 "$TASK22_ORCHESTRATOR_PARENT"
cleanup_task22_orchestrator() {
  backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py cleanup-owned-tree --root "$TASK22_ORCHESTRATOR_PARENT" --expected-mode 0700
}
trap 'cleanup_task22_orchestrator' EXIT
TASK22_PRODUCT_C23_STAGE_ROOT="$TASK22_ORCHESTRATOR_PARENT/c23"
TASK22_PRODUCT_C25_STAGE_ROOT="$TASK22_ORCHESTRATOR_PARENT/c25"
TASK22_PRODUCT_C27_STAGE_ROOT="$TASK22_ORCHESTRATOR_PARENT/c27"
TASK22_PRODUCT_C28_STAGE_ROOT="$TASK22_ORCHESTRATOR_PARENT/c28"
mkdir -m 0700 "$TASK22_PRODUCT_C23_STAGE_ROOT" "$TASK22_PRODUCT_C25_STAGE_ROOT" "$TASK22_PRODUCT_C27_STAGE_ROOT" "$TASK22_PRODUCT_C28_STAGE_ROOT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_orchestrator.py verify-export-request --source-store-root "$TRUSTED_HANDOFF_STORE_ROOT" --export-request-ref "$TRUSTED_PRODUCT_EXPORT_REQUEST_REF" --handoff-id ticket-23-product-source-v1 --expected-kind product-source --expected-producer-task 22 --consumer-task 23 --consumer-task 25 --consumer-task 27 --consumer-task 28
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_orchestrator.py run-transport --source-store-root "$TRUSTED_HANDOFF_STORE_ROOT" --target-store-root "$TRUSTED_PRODUCT_C23_TARGET_STORE_ROOT" --stage-root "$TASK22_PRODUCT_C23_STAGE_ROOT" --export-request-ref "$TRUSTED_PRODUCT_EXPORT_REQUEST_REF" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c23 --consumer-task 23 --store-namespace ai-video-reverse-engineer/ticket-23 --source-executor-id "$TRUSTED_SOURCE_EXECUTOR_ID" --destination-executor-id "$TRUSTED_PRODUCT_C23_DESTINATION_EXECUTOR_ID" --nonce "$TRUSTED_PRODUCT_C23_NONCE" --sequence "$TRUSTED_PRODUCT_C23_SEQUENCE" --expires-at "$TRUSTED_PRODUCT_C23_EXPIRES_AT" --signing-key-fd "$TRUSTED_HANDOFF_ORCHESTRATOR_SIGNING_KEY_FD" --key-id "$TRUSTED_HANDOFF_ORCHESTRATOR_KEY_ID"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_orchestrator.py run-transport --source-store-root "$TRUSTED_HANDOFF_STORE_ROOT" --target-store-root "$TRUSTED_PRODUCT_C25_TARGET_STORE_ROOT" --stage-root "$TASK22_PRODUCT_C25_STAGE_ROOT" --export-request-ref "$TRUSTED_PRODUCT_EXPORT_REQUEST_REF" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c25 --consumer-task 25 --store-namespace ai-video-reverse-engineer/ticket-23 --source-executor-id "$TRUSTED_SOURCE_EXECUTOR_ID" --destination-executor-id "$TRUSTED_PRODUCT_C25_DESTINATION_EXECUTOR_ID" --nonce "$TRUSTED_PRODUCT_C25_NONCE" --sequence "$TRUSTED_PRODUCT_C25_SEQUENCE" --expires-at "$TRUSTED_PRODUCT_C25_EXPIRES_AT" --signing-key-fd "$TRUSTED_HANDOFF_ORCHESTRATOR_SIGNING_KEY_FD" --key-id "$TRUSTED_HANDOFF_ORCHESTRATOR_KEY_ID"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_orchestrator.py run-transport --source-store-root "$TRUSTED_HANDOFF_STORE_ROOT" --target-store-root "$TRUSTED_PRODUCT_C27_TARGET_STORE_ROOT" --stage-root "$TASK22_PRODUCT_C27_STAGE_ROOT" --export-request-ref "$TRUSTED_PRODUCT_EXPORT_REQUEST_REF" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c27 --consumer-task 27 --store-namespace ai-video-reverse-engineer/ticket-23 --source-executor-id "$TRUSTED_SOURCE_EXECUTOR_ID" --destination-executor-id "$TRUSTED_PRODUCT_C27_DESTINATION_EXECUTOR_ID" --nonce "$TRUSTED_PRODUCT_C27_NONCE" --sequence "$TRUSTED_PRODUCT_C27_SEQUENCE" --expires-at "$TRUSTED_PRODUCT_C27_EXPIRES_AT" --signing-key-fd "$TRUSTED_HANDOFF_ORCHESTRATOR_SIGNING_KEY_FD" --key-id "$TRUSTED_HANDOFF_ORCHESTRATOR_KEY_ID"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_orchestrator.py run-transport --source-store-root "$TRUSTED_HANDOFF_STORE_ROOT" --target-store-root "$TRUSTED_PRODUCT_C28_TARGET_STORE_ROOT" --stage-root "$TASK22_PRODUCT_C28_STAGE_ROOT" --export-request-ref "$TRUSTED_PRODUCT_EXPORT_REQUEST_REF" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c28 --consumer-task 28 --store-namespace ai-video-reverse-engineer/ticket-23 --source-executor-id "$TRUSTED_SOURCE_EXECUTOR_ID" --destination-executor-id "$TRUSTED_PRODUCT_C28_DESTINATION_EXECUTOR_ID" --nonce "$TRUSTED_PRODUCT_C28_NONCE" --sequence "$TRUSTED_PRODUCT_C28_SEQUENCE" --expires-at "$TRUSTED_PRODUCT_C28_EXPIRES_AT" --signing-key-fd "$TRUSTED_HANDOFF_ORCHESTRATOR_SIGNING_KEY_FD" --key-id "$TRUSTED_HANDOFF_ORCHESTRATOR_KEY_ID"
cleanup_task22_orchestrator
test ! -e "$TASK22_ORCHESTRATOR_PARENT"
trap - EXIT
exit
```

四个 stage root 只由 orchestrator harness 创建为空、0700、no-symlink 绝对根并在交付后安全清理；四个 target store 可位于不同 executor。任一 request 校验失败时 transport/sign 调用数为零；任一 install 失败只使对应 consumer transport 不可用，不改写 record/pointer/request 或其他 consumer transport。

worktree provisioner 不得假设 checkout、linked gitdir 或 shared commondir 位于 `TASK22_BOUNDARY_PARENT` 内；ownership capability 逐一绑定它们的绝对 no-symlink identity，并以 broker-only MAC 绑定 project、provisioner executable digest、broker key id、executor/profile identity、lifecycle nonce、全部根/identity 与创建时既有 shared object 的有界 canonical baseline inventory digest。`destroy-linked-worktree` 对同一有效 token+已销毁状态幂等，只 unlink 本次 backlink并删除专属 checkout/linked gitdir；token 重放到另一 lifecycle/executor、字段替换或删除 shared commondir/object store 的任何请求必须拒绝。`verify-destroyed` 必须重新打开 shared commondir/object store，验证原 identity、全部 baseline object 路径/identity/bytes 仍在且未变（允许本次提交新增对象保留），同时确认 checkout、linked gitdir、backlink 都为 ENOENT。正常发布后显式 cleanup 与失败/取消 EXIT trap 都调用同一 destroy→verify-destroyed 流；destroy/identity/backlink/shared-store 任一验证失败时保留 boundary JSON 和 trap、最终非零退出，不得把“临时 parent 已删”误报为生命周期闭合。该 capability 只服务 Task 22 临时生命周期，与进程内 `RegisteredRepositoryBoundaryV1`、source digest 和跨 Task handoff 分离。

### Task 23: 构建 Ticket 23 产品专属 dependency containers

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/build_ticket23_dependency_repositories.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/run_ticket23_dependency_validation.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/input-manifest.json`
- Create from governance input: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/PROVENANCE.json`
- Create generated artifact: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/python-repository.tar.zst`
- Create generated record: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/python-build-record.json`
- Create generated artifact: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/npm-repository.tar.zst`
- Create generated record: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/npm-build-record.json`
- Create generated record: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/network-proof.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trust/dependencies/ticket-23/manifest.json`
- Create generated blobs: `.scratch/unified-remix-canvas-generation/acceptance/trust/dependencies/sha256/<aa>/<digest>`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_dependency_repositories.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/README.md`
- Ephemeral only: 单一 0700 `TASK23_TEMP_PARENT` 下的 handoff/sanitized/repro/toolchain/view/COW/cache/report 子根与 platform measurement；一个 EXIT trap 清理。

**Interfaces:**
- Consumes: `ticket-23-product-source-v1` handoff 中经过摘要验证的 product container/build record/subject/source，Task 11 sanitized checkout contract，产品内 `backend/requirements.lock` 与 `frontend/package-lock.json` 两个有界稳定输入，以及 Task 18 的 toolchain build record/artifact ref/platform identity；不得读取 Task 22 worktree 或复用 fixture digest。
- Produces: 两份 dependency containers/records；`Ticket23ValidationPlanV1.from_fixed_identities()` 唯一枚举两个 uv、一个 npm、四 pytest 与九 Vitest argv/cwd；`run_ticket23_dependency_validation.py` 先 Task 7 platform compare，再把 plan 全部 invocation 交给 Task 14 containment 的无接口 network namespace与 DNS/socket census，终态闭合后才生成 `network-proof.json`。Task 27/28 绑定 records/proof，不复用临时路径。

- [ ] **Step 1:** 写 RED：handoff/proof/container/build-record、两个 bounded locks、Task 18 build record/ref/platform identity、input/provenance/blob、双空根确定性任一不符均失败。validation tests 断言 platform mismatch 在 uv/npm/test spawn=0 时阻塞；成功路径的两个 uv setup、npm ci、四 pytest、九 Vitest 全部拥有 containment transcript、空 network namespace、DNS/socket census=0；缺 bytes、错误 lock、toolchain/platform/materialize/setup/test 失败路径同样零 live-network event、完成 terminate→empty→destroy。`--offline` 缺失也拒绝，但不能作为网络证明。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_dependency_repositories.py`，预期 builder/output 缺失而 RED。测试和 builder 均先 authenticated fetch，再由 `HandoffRegistry.read` 重验 pointer/record/proof/container/build record；经 Task 11 inspector 物化 subject，仅在 sanitized checkout 内读取两 locks。
- [ ] **Step 3:** `input-manifest.json` 逐项固定两个实际 lock digest、每个 wheel/tgz 本地预审路径、SHA-256/SRI、package ID、mapping、install-tree 与 derived-entrypoint digest；`PROVENANCE.json` 逐 blob 固定 upstream URL/release/license/auditor/acquisition。`read-toolchain-ref` 严格解码 Task 18 `trusted-e2-toolchain-build-record/v1`，拒绝未知/缺失/重复字段，重算完整 record、artifactRef 指向的 content-store bytes 和 `out/platform-identity.json` digest，stdout 恰为已验证的小写 `sha256:64hex` 一行；禁止手工转录或字面占位。Steps 3–4 必须在同一个长生命周期 PTY shell session 中运行：以 `exec zsh -f` 初始化，两个代码段写入同一 session ID，唯一 EXIT trap 只在 Step 4 runner 完成后解除；禁止两个独立 `exec`。再精确执行：

```bash
exec zsh -f
set -euo pipefail
TASK23_TOOLCHAIN_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_ticket23_dependency_repositories.py read-toolchain-ref \
  --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json \
  --content-store-root .scratch/unified-remix-canvas-generation/acceptance/trust/toolchains \
  --platform-identity .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/platform-identity.json)"
printf '%s\n' "$TASK23_TOOLCHAIN_REF" | grep -Eq '^sha256:[0-9a-f]{64}$'
TASK23_TEMP_PARENT="$(mktemp -d)"
chmod 0700 "$TASK23_TEMP_PARENT"
cleanup_task23() {
  backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py cleanup-owned-tree --root "$TASK23_TEMP_PARENT" --expected-mode 0700
}
trap 'cleanup_task23' EXIT
TASK23_HANDOFF_CACHE="$TASK23_TEMP_PARENT/handoff-cache"
TASK23_SANITIZED_CHECKOUT="$TASK23_TEMP_PARENT/sanitized-checkout"
TASK23_DEP_REPRO_ROOT_A="$TASK23_TEMP_PARENT/repro-a"
TASK23_DEP_REPRO_ROOT_B="$TASK23_TEMP_PARENT/repro-b"
TASK23_TOOLCHAIN_ROOT="$TASK23_TEMP_PARENT/toolchain"
TASK23_PYTHON_VIEW_ROOT="$TASK23_TEMP_PARENT/python-view"
TASK23_NPM_VIEW_ROOT="$TASK23_TEMP_PARENT/npm-view"
TASK23_NPM_COW_UPPER="$TASK23_TEMP_PARENT/npm-cow-upper"
TASK23_UV_CACHE_1="$TASK23_TEMP_PARENT/uv-cache-venv"
TASK23_UV_CACHE_2="$TASK23_TEMP_PARENT/uv-cache-sync"
TASK23_REPORT_ROOT="$TASK23_TEMP_PARENT/reports"
TASK23_PLATFORM_MEASUREMENT="$TASK23_TEMP_PARENT/platform-measurement.json"
mkdir -m 0700 "$TASK23_HANDOFF_CACHE" "$TASK23_SANITIZED_CHECKOUT" "$TASK23_DEP_REPRO_ROOT_A" "$TASK23_DEP_REPRO_ROOT_B" "$TASK23_TOOLCHAIN_ROOT" "$TASK23_PYTHON_VIEW_ROOT" "$TASK23_NPM_VIEW_ROOT" "$TASK23_NPM_COW_UPPER" "$TASK23_UV_CACHE_1" "$TASK23_UV_CACHE_2" "$TASK23_REPORT_ROOT"
for root in "$TASK23_HANDOFF_CACHE" "$TASK23_SANITIZED_CHECKOUT" "$TASK23_DEP_REPRO_ROOT_A" "$TASK23_DEP_REPRO_ROOT_B" "$TASK23_TOOLCHAIN_ROOT" "$TASK23_PYTHON_VIEW_ROOT" "$TASK23_NPM_VIEW_ROOT" "$TASK23_NPM_COW_UPPER" "$TASK23_UV_CACHE_1" "$TASK23_UV_CACHE_2" "$TASK23_REPORT_ROOT"; do
  test -d "$root" && test ! -L "$root" && test -z "$(find "$root" -mindepth 1 -print -quit)"
done
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_ticket23_dependency_repositories.py verify-empty-roots \
  --parent "$TASK23_TEMP_PARENT" --expected-mode 0700 \
  --root "$TASK23_HANDOFF_CACHE" --root "$TASK23_SANITIZED_CHECKOUT" \
  --root "$TASK23_DEP_REPRO_ROOT_A" --root "$TASK23_DEP_REPRO_ROOT_B" \
  --root "$TASK23_TOOLCHAIN_ROOT" --root "$TASK23_PYTHON_VIEW_ROOT" --root "$TASK23_NPM_VIEW_ROOT" \
  --root "$TASK23_NPM_COW_UPPER" --root "$TASK23_UV_CACHE_1" --root "$TASK23_UV_CACHE_2" --root "$TASK23_REPORT_ROOT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py verify-cache \
  --cache-root "$TASK23_HANDOFF_CACHE" --require-empty --expected-mode 0700
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py fetch \
  --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --handoff-id ticket-23-product-source-v1 \
  --transport-id ticket-23-product-source-v1-c23 --consumer-task 23 \
  --expected-destination-executor-id "$TRUSTED_PRODUCT_C23_DESTINATION_EXECUTOR_ID" --cache-root "$TASK23_HANDOFF_CACHE"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py import \
  --cache-root "$TASK23_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 \
  --transport-id ticket-23-product-source-v1-c23 --consumer-task 23 \
  --expected-destination-executor-id "$TRUSTED_PRODUCT_C23_DESTINATION_EXECUTOR_ID"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py finalize \
  --cache-root "$TASK23_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 \
  --transport-id ticket-23-product-source-v1-c23 --expected-kind product-source --expected-producer-task 22 --consumer-task 23 \
  --expected-destination-executor-id "$TRUSTED_PRODUCT_C23_DESTINATION_EXECUTOR_ID"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize \
  --artifact-ref "$TASK23_TOOLCHAIN_REF" --output-root "$TASK23_TOOLCHAIN_ROOT" --offline
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_ticket23_dependency_repositories.py build \
  --product-handoff-cache-root "$TASK23_HANDOFF_CACHE" \
  --product-handoff-id ticket-23-product-source-v1 --product-transport-id ticket-23-product-source-v1-c23 --consumer-task 23 \
  --expected-destination-executor-id "$TRUSTED_PRODUCT_C23_DESTINATION_EXECUTOR_ID" \
  --sanitized-checkout-root "$TASK23_SANITIZED_CHECKOUT" \
  --python-lock backend/requirements.lock --npm-lock frontend/package-lock.json \
  --toolchain-build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json \
  --toolchain-ref "$TASK23_TOOLCHAIN_REF" \
  --platform-identity .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/platform-identity.json \
  --input-manifest .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/input-manifest.json \
  --provenance .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/PROVENANCE.json \
  --repro-root-a "$TASK23_DEP_REPRO_ROOT_A" --repro-root-b "$TASK23_DEP_REPRO_ROOT_B" \
  --python-output .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/python-repository.tar.zst \
  --python-build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/python-build-record.json \
  --npm-output .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/npm-repository.tar.zst \
  --npm-build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/npm-build-record.json \
  --content-store-root .scratch/unified-remix-canvas-generation/acceptance/trust/dependencies --offline
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_ticket23_dependency_repositories.py materialize \
  --python-build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/python-build-record.json \
  --npm-build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/npm-build-record.json \
  --python-view-root "$TASK23_PYTHON_VIEW_ROOT" --npm-view-root "$TASK23_NPM_VIEW_ROOT" \
  --npm-cow-upper "$TASK23_NPM_COW_UPPER" --offline
TASK23_PYTHON_VIEW="$TASK23_PYTHON_VIEW_ROOT/python-find-links"
TASK23_NPM_VIEW="$TASK23_NPM_VIEW_ROOT/npm-cache-root"
```

`verify-empty-roots` 用 no-follow owning handle 核对 parent/全部子根的绝对归属、owner/mode、root identity、no-symlink、空初态与无 hard-linked regular entry，任一变化都在 fetch/materialize 前拒绝。builder 必须在两个空根分别构建 containers/views，逐 byte/digest 比较后才发布；两个 records 都绑定 product subject/source/container/build-record/proof、两 lock、toolchain artifact/build-record/platform identity 及各自 input/provenance/container/view/install/entrypoint digests。`materialize` 从 published refs 重验；COW/cache 均是当次空根。
- [ ] **Step 4:** 在 Step 3 返回的同一 PTY session 中运行单一 validation runner；它在任何真实 executor spawn 前调用 `measure_platform`/`compare_platform_identity` 并写 task-local measurement，随后将以下固定 invocation specs 交给 `LinuxContainmentSupervisor`，每个 leaf 都使用无接口 network namespace、继承式 seccomp DNS/socket census 和完整 destroy protocol：backend cwd=`$TASK23_SANITIZED_CHECKOUT`，依次运行 uv venv、uv pip sync、四个顶部“Ticket 23 固定测试身份”所列 pytest node IDs；frontend cwd=`$TASK23_SANITIZED_CHECKOUT/frontend`，依次运行 npm ci、九个顶部已列测试文件/fullName 的包内 Node+Vitest invocation。精确命令：

```bash
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/run_ticket23_dependency_validation.py \
  --checkout-root "$TASK23_SANITIZED_CHECKOUT" --toolchain-root "$TASK23_TOOLCHAIN_ROOT" \
  --toolchain-build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json \
  --expected-platform .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/platform-identity.json \
  --platform-measurement "$TASK23_PLATFORM_MEASUREMENT" \
  --python-build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/python-build-record.json \
  --npm-build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/npm-build-record.json \
  --python-view "$TASK23_PYTHON_VIEW" --npm-view "$TASK23_NPM_VIEW" --npm-cow-upper "$TASK23_NPM_COW_UPPER" \
  --uv-cache-venv "$TASK23_UV_CACHE_1" --uv-cache-sync "$TASK23_UV_CACHE_2" --report-root "$TASK23_REPORT_ROOT" \
  --network-proof .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/network-proof.json --offline
cleanup_task23
test ! -e "$TASK23_TEMP_PARENT"
trap - EXIT
exit
```
- [ ] **Step 5:** 校验 outputs/records/content-store、A/B、locks 与 network proof 的 platform/command-set/dependency/transcript/socket bindings；运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_dependency_repositories.py` 覆盖成功及缺 bytes/lock/toolchain/platform/materialize/setup/test 失败路径。最后执行 `git add .scratch/unified-remix-canvas-generation/acceptance/tools/{build_ticket23_dependency_repositories.py,run_ticket23_dependency_validation.py} .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies .scratch/unified-remix-canvas-generation/acceptance/trust/dependencies .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_dependency_repositories.py .scratch/unified-remix-canvas-generation/acceptance/README.md && git commit -m "build: freeze ticket 23 dependency repositories"`。

### Task 24: 实现 E4 attestation、SSH 验签、scope 与 revocation

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e4_ssh.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/models.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/registry.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/audit_e4_secrets.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_audit_e4_secrets.py`

**Interfaces:**
- Consumes: 旧计划 Task 9 boundary、namespace `trusted-acceptance-e4`、Task 2 operation snapshot/revocation。
- Produces: `parse_attestation`、`canonicalize_attestation`、`SSHReviewVerifier.verify`、`prepare-e4`、`collect-e4`；后者复用 Task 16 result codec，stdout 恰为 operation=`collect-e4`、verifierId=`e4.ticket23.canvas-review.v1` 的 canonical result，diagnostic 只写 stderr。

- [ ] **Step 1:** 写 canonical JSON/signature/namespace/principal/fingerprint/scope/criteria/attachment/expiry/revocation/private-key-ban RED tests，并断言 `collect-e4` stdout 通过 Task 16 同一 strict codec/parser、stderr diagnostic 不污染 stdout；secret-audit fixtures 覆盖 OpenSSH、PKCS#1 RSA、SEC1 EC、PKCS#8、encrypted PKCS#8 私钥头与配套 base64 payload，治理登记的 forbidden private-key byte value/SHA-256，过宽文件权限，以及 production JSON 禁止键名 `privateKey/privateKeyPath/passphrase/sshAuthSock/agentSocket`。单纯说明文字关键词不得误报。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py`；预期 RED。
- [ ] **Step 3:** stdin 传 payload 给登记 `ssh-keygen -Y verify`；生产 CLI 永不接收私钥或 agent socket。`collect-e4` 复用 Task 16 canonical result writer 并以真实 CAS bytes 重算 `artifactRef/artifactDigest`；stdout 无额外文本。`audit_e4_secrets.py` 对 regular files no-follow 扫描上述完整私钥结构和值/digest，解析 production JSON key tree，并校验 `trust/` 公共 policy/signer 文件恰为 0644、任何私钥形态文件一律拒绝；显式 allowlist 只排除测试 fixture bytes 与 `redaction.py` 的 lexer grammar 源码，production CLI/config/trust/evidence/audit 永不豁免。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py .scratch/unified-remix-canvas-generation/acceptance/test_audit_e4_secrets.py`，再运行 `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/audit_e4_secrets.py --root .scratch/unified-remix-canvas-generation/acceptance --production-json .scratch/unified-remix-canvas-generation/acceptance/trusted-registry.json --allow-test .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py --allow-test .scratch/unified-remix-canvas-generation/acceptance/test_audit_e4_secrets.py --allow-grammar .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/redaction.py`；预期 GREEN、无结构化私钥/禁止值/禁止 JSON 字段/权限错误。关键词 grep 不作为验收；Task 26 在 reviewer policy 创建后必须再次扫描该 JSON。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{models.py,registry.py,verifiers/e4_ssh.py} .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py .scratch/unified-remix-canvas-generation/acceptance/tools/audit_e4_secrets.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py .scratch/unified-remix-canvas-generation/acceptance/test_audit_e4_secrets.py && git commit -m "feat: verify signed E4 attestations"`

### Task 25: 固化真实 Toonflow E1 与产品 E1 映射

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trust/git/toonflow-d86cce6b689916ab73f75bc4c1f42a2a7424516c.bundle`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trust/product-sources/sha256/<aa>/<digest>`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trust/e1-ticket23-sources.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/build_ticket23_e1_sources.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_e1_sources.py`
- Ephemeral only: 0700 `TASK25_CACHE_PARENT/handoff-cache` 及 finalized marker；EXIT trap 清理，不跟踪、不跨 Task 复用。

**Interfaces:**
- Consumes: Tasks 10–11、从 `$TRUSTED_HANDOFF_STORE_ROOT` authenticated fetch 的 `ticket-23-product-source-v1`（producer=22、consumer=25）中 product container/build-record/raw-inspection-proof/boundary/source digests；Toonflow fixed commit/MIT license。
- Produces: `build_ticket23_e1_sources.py verify-product/import-sources/parse-import-result`、两个 E1 refs、Toonflow/license 与产品实现/测试/lock blob mappings；`parse-import-result` strict parse 完整 stdin 后以 `--field toonflowSourceRef|productSourceRef` 只输出一个已验证 ref。

- [ ] **Step 1:** 写 RED：Toonflow bundle 必含 fixed commit/objects/MIT；product transport 必为 consumer 25 的独立 seal；container/build-record/inspection-proof/boundary/tracked/control/source 七 bindings 必须逐项显式匹配，Task 11 inspector 产出 `VerifiedProductRepositorySourceV1` 前不得导入；E1 mapping 只绑定该 typed source 的 `ProductGitObjectIdV1` SHA-1/40hex subject 与逐 blob digests，SHA-256 product object、`extensions.objectFormat`、错误长度或 `git://product@sha1:<hex>` 均为反例。错 transport/consumer/destination、七项任一换绑、Task 11 拒绝或 import result 非严格 schema 均零 product-source/E1 写入。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_e1_sources.py`；预期真实 inputs RED。
- [ ] **Step 3:** Toonflow 用包内 Git bundle verify/cat-file；产品只执行下列完整消费命令。`verify-cache` 在 fetch 前验根是当次新建、0700、空、no-symlink 且只有唯一打开 identity；`import/finalize` 逐入口拒绝 symlink、非 regular file、hard-linked regular file、额外 entry 或 root identity 变化；trap 无论成败都删除整个 task-local parent：

```bash
set -euo pipefail
TASK25_CACHE_PARENT="$(mktemp -d)"
chmod 0700 "$TASK25_CACHE_PARENT"
TASK25_HANDOFF_CACHE="$TASK25_CACHE_PARENT/handoff-cache"
mkdir -m 0700 "$TASK25_HANDOFF_CACHE"
cleanup_task25() {
  backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py cleanup-owned-tree --root "$TASK25_CACHE_PARENT" --expected-mode 0700
}
trap 'cleanup_task25' EXIT
test -d "$TASK25_HANDOFF_CACHE"
test ! -L "$TASK25_HANDOFF_CACHE"
test -z "$(find "$TASK25_HANDOFF_CACHE" -mindepth 1 -print -quit)"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py verify-cache \
  --cache-root "$TASK25_HANDOFF_CACHE" --require-empty --expected-mode 0700
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py fetch \
  --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --handoff-id ticket-23-product-source-v1 \
  --transport-id ticket-23-product-source-v1-c25 --consumer-task 25 \
  --expected-destination-executor-id "$TRUSTED_PRODUCT_C25_DESTINATION_EXECUTOR_ID" --cache-root "$TASK25_HANDOFF_CACHE"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py import \
  --cache-root "$TASK25_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 \
  --transport-id ticket-23-product-source-v1-c25 --consumer-task 25 \
  --expected-destination-executor-id "$TRUSTED_PRODUCT_C25_DESTINATION_EXECUTOR_ID"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py finalize \
  --cache-root "$TASK25_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 \
  --transport-id ticket-23-product-source-v1-c25 --expected-kind product-source --expected-producer-task 22 --consumer-task 25 \
  --expected-destination-executor-id "$TRUSTED_PRODUCT_C25_DESTINATION_EXECUTOR_ID"
TASK25_PRODUCT_COMMIT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read-product-subject --cache-root "$TASK25_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c25 --consumer-task 25 --expected-destination-executor-id "$TRUSTED_PRODUCT_C25_DESTINATION_EXECUTOR_ID" --field hex)"
TASK25_PRODUCT_SUBJECT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read-product-subject --cache-root "$TASK25_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c25 --consumer-task 25 --expected-destination-executor-id "$TRUSTED_PRODUCT_C25_DESTINATION_EXECUTOR_ID" --field canonicalSubject)"
printf '%s\n' "$TASK25_PRODUCT_COMMIT" | grep -Eq '^[0-9a-f]{40}$'
test "$TASK25_PRODUCT_SUBJECT" = "git://product@$TASK25_PRODUCT_COMMIT"
TASK25_CONTAINER_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read --cache-root "$TASK25_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c25 --consumer-task 25 --expected-destination-executor-id "$TRUSTED_PRODUCT_C25_DESTINATION_EXECUTOR_ID" --field binding.product-source-container)"
TASK25_BUILD_RECORD_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read --cache-root "$TASK25_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c25 --consumer-task 25 --expected-destination-executor-id "$TRUSTED_PRODUCT_C25_DESTINATION_EXECUTOR_ID" --field binding.product-source-build-record)"
TASK25_PROOF_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read --cache-root "$TASK25_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c25 --consumer-task 25 --expected-destination-executor-id "$TRUSTED_PRODUCT_C25_DESTINATION_EXECUTOR_ID" --field binding.raw-git-inspection-proof)"
TASK25_BOUNDARY_DIGEST="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read --cache-root "$TASK25_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c25 --consumer-task 25 --expected-destination-executor-id "$TRUSTED_PRODUCT_C25_DESTINATION_EXECUTOR_ID" --field binding.boundary-topology)"
TASK25_TRACKED_DIGEST="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read --cache-root "$TASK25_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c25 --consumer-task 25 --expected-destination-executor-id "$TRUSTED_PRODUCT_C25_DESTINATION_EXECUTOR_ID" --field binding.tracked-checkout)"
TASK25_CONTROL_DIGEST="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read --cache-root "$TASK25_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c25 --consumer-task 25 --expected-destination-executor-id "$TRUSTED_PRODUCT_C25_DESTINATION_EXECUTOR_ID" --field binding.control-metadata)"
TASK25_SOURCE_DIGEST="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read --cache-root "$TASK25_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c25 --consumer-task 25 --expected-destination-executor-id "$TRUSTED_PRODUCT_C25_DESTINATION_EXECUTOR_ID" --field binding.source)"
TASK25_VERIFIED_SOURCE="$TASK25_CACHE_PARENT/verified-product-source.json"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_ticket23_e1_sources.py verify-product \
  --handoff-cache-root "$TASK25_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 \
  --transport-id ticket-23-product-source-v1-c25 --consumer-task 25 --expected-destination-executor-id "$TRUSTED_PRODUCT_C25_DESTINATION_EXECUTOR_ID" --expected-commit "$TASK25_PRODUCT_COMMIT" \
  --container-ref "$TASK25_CONTAINER_REF" --build-record-ref "$TASK25_BUILD_RECORD_REF" --inspection-proof-ref "$TASK25_PROOF_REF" \
  --boundary-digest "$TASK25_BOUNDARY_DIGEST" --tracked-checkout-digest "$TASK25_TRACKED_DIGEST" \
  --control-metadata-digest "$TASK25_CONTROL_DIGEST" --source-digest "$TASK25_SOURCE_DIGEST" \
  --verified-source-output "$TASK25_VERIFIED_SOURCE"
TASK25_IMPORT_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_ticket23_e1_sources.py import-sources \
  --verified-product-source "$TASK25_VERIFIED_SOURCE" \
  --toonflow-bundle .scratch/unified-remix-canvas-generation/acceptance/trust/git/toonflow-d86cce6b689916ab73f75bc4c1f42a2a7424516c.bundle \
  --toonflow-commit d86cce6b689916ab73f75bc4c1f42a2a7424516c --toonflow-license MIT \
  --product-source-store-root .scratch/unified-remix-canvas-generation/acceptance/trust/product-sources \
  --mapping-output .scratch/unified-remix-canvas-generation/acceptance/trust/e1-ticket23-sources.json)"
TASK25_TOONFLOW_E1_REF="$(printf '%s' "$TASK25_IMPORT_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_ticket23_e1_sources.py parse-import-result \
  --expected-schema ticket23-e1-source-import/v1 --expected-product-commit "$TASK25_PRODUCT_COMMIT" \
  --expected-toonflow-commit d86cce6b689916ab73f75bc4c1f42a2a7424516c --field toonflowSourceRef)"
TASK25_PRODUCT_E1_REF="$(printf '%s' "$TASK25_IMPORT_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_ticket23_e1_sources.py parse-import-result \
  --expected-schema ticket23-e1-source-import/v1 --expected-product-commit "$TASK25_PRODUCT_COMMIT" \
  --expected-toonflow-commit d86cce6b689916ab73f75bc4c1f42a2a7424516c --field productSourceRef)"
printf '%s\n' "$TASK25_TOONFLOW_E1_REF" "$TASK25_PRODUCT_E1_REF" | grep -Ec '^sha256:[0-9a-f]{64}$' | grep -qx '2'
cleanup_task25
test ! -e "$TASK25_CACHE_PARENT"
trap - EXIT
exit
```

`verify-product` 内部只调用 Task 11 `GitMetadataInspector.inspect(container, proof, build_record)` 的正式路径，重算七 bindings 后产出强类型 `VerifiedProductRepositorySourceV1` canonical JSON；`import-sources` 只接收该 typed output，不接收裸 container/path/digest 自报。两次 `parse-import-result` 都重验完整相同 canonical stdout、schema、两个 commit、两个 refs 与 mapping digest；未知字段、额外 stdout 或两次结果不一致均在 refs 被使用前终止。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_e1_sources.py`；预期 commit/license/all blobs 闭合。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trust/{git,product-sources,e1-ticket23-sources.json} .scratch/unified-remix-canvas-generation/acceptance/tools/build_ticket23_e1_sources.py .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_e1_sources.py && git commit -m "build: freeze ticket 23 E1 sources"`

### Task 26: 固化 allowed-signers、reviewer policy 与八项 criteria

**Files:**
- Create from governance input: `.scratch/unified-remix-canvas-generation/acceptance/trust/e4-allowed-signers`
- Create from governance input: `.scratch/unified-remix-canvas-generation/acceptance/trust/reviewer-policy.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_reviewer_policy.py`

**Interfaces:**
- Consumes: Task 24 与真实 reviewer 公钥/principal/fingerprint；私钥不入仓库。
- Produces: allowed-signers/reviewer policy digest，scope 仅 ticket-23/v1、C01–C03、八项 criteria。

- [ ] **Step 1:** 写 scope/criteria/expiry/fingerprint/duplicate/revocation/缺 E4 必阻塞 RED tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_reviewer_policy.py`；预期真实 policy 缺失而 RED。
- [ ] **Step 3:** 运行 `awk '{for (i=1;i<=NF;i++) if ($i ~ /^ssh-/) {print $i, $(i+1); exit}}' .scratch/unified-remix-canvas-generation/acceptance/trust/e4-allowed-signers | ssh-keygen -l -E sha256 -f -`；输出必须等于治理批准 fingerprint。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_reviewer_policy.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py`，再运行 Task 24 secret audit 并额外传入 `--production-json .scratch/unified-remix-canvas-generation/acceptance/trust/reviewer-policy.json`；预期 GREEN 且 signer/policy 文件为 0644、无私钥、无 production 禁止键。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trust/{e4-allowed-signers,reviewer-policy.json} .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_reviewer_policy.py && git commit -m "policy: trust ticket 23 acceptance reviewer"`

### Task 27: 先固定 Ticket 23 policy commit

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/profiles/ticket-23.json`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted-registry.json`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trust/revocations.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/publish_ticket23_policy_handoff.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile_v2.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_policy_handoff.py`
- Generated Git ref: `refs/tags/trusted-acceptance/ticket-23-policy-v1`
- Generated external CAS record/blobs: `$TRUSTED_HANDOFF_STORE_ROOT/sha256/<aa>/<remaining-62-hex>` (not tracked)
- Generated external pointer: `$TRUSTED_HANDOFF_STORE_ROOT/pointers/ticket-23-policy-v1.json` (not tracked)
- Generated immutable export request: `$TRUSTED_HANDOFF_STORE_ROOT/sha256/<aa>/<remaining-62-hex>` 与 `$TRUSTED_HANDOFF_STORE_ROOT/export-requests/ticket-23-policy-v1.json` no-replace pointer (not tracked)
- Generated external per-consumer transports: target stores 中 `transfers/ticket-23-policy-v1/{ticket-23-policy-v1-c28,ticket-23-policy-v1-c29}.json`、对应 `seals/` 文件与 ready markers (not tracked)
- Ephemeral only: policy-producer 0700 `TASK27_TEMP_PARENT/{product-handoff-cache,toolchain,git-home,git-xdg,empty-hooks,git-write-result.json}` 与独立 orchestrator 0700 `TASK27_ORCHESTRATOR_PARENT/{c28,c29}`；两套 credential domains 各自用 `cleanup-owned-tree` 清理，不共享 UID/GID、namespace、FD、变量、临时根或 trap。

**Interfaces:**
- Consumes: Tasks 7/14/18–26 的 trusted Git、credential-domain runner/profile、toolchain/platform/supervisor/source/dependency/E1/E4/public-policy digests；authenticated fetch product handoff（producer=22、consumer=27），并核对两 dependency records 绑定同一 product source/proof/locks/toolchain/platform 及 network-proof。
- Produces: fixed ref；`publish_ticket23_policy_handoff.py` 只接收 consumer 27 的 finalized product-handoff cache root，在内部重验 transport/record/proof/container 并构造强类型 `VerifiedProductRepositorySourceV1`，不接收 product store root、handoff ID 或调用方自报 digest。它发布 kind=`policy`、producerTask=`27`、consumerTasks=`[28,29]` 的单一 record，并在 record 中以合法 `NamedDigest` 显式绑定 `product-source`、`network-proof`、`replay-policy`；原始 Git commit 不进入 bindings，后续 consumer 必须从 `product-source` CAS object 的 `ProductGitObjectIdV1` SHA-1 subject 派生。policy repository 的 producerCommit 独立使用 `GitObjectIdV1`，允许当前仓库实际 object format 为 SHA-1 或 SHA-256。publisher 后再发布 policy export request，编排器为 Tasks 28/29 分别安装独立 transport。

- [ ] **Step 1:** 写 profile/publisher RED：Task 27 必须以 transport=`ticket-23-product-source-v1-c27`、consumerTask=27、当前 destination executor 执行 verify-cache→fetch→import→finalize→read；错 transport/consumer/destination、篡改 cache/finalized marker，或试图以 product input 直接传 product store root/handoff ID，均在 policy record write=0 时拒绝。policy bindings 中出现原始 40/64-hex `product-commit` 必须 schema reject；product subject 只从 verified `product-source` CAS object 派生且必须为 sha1/40，SHA-256/objectFormat/长度错配零写；policy producer commit 则分别覆盖 sha1/40 与 sha256/64。fixed ref/policy pointer/request 冲突、任一精确 binding 不符也零写；两个 policy consumer 必须有独立 transport/seal，跨 consumer 复用拒绝，byte-identical retry 幂等。复用 Task 22 handoff-domain 探针，证明 producer 无法访问 key/agent/orchestrator env/FD/ptrace 且两角色不同 UID/GID/namespaces；orchestrator 只接受认证 request ref。policy commit/ref 的恶意 system/global/local signing、gpg、hooks、agent 与 `GIT_CONFIG_*` 注入全部 Git write spawn/canary=0，正例只走 Task 18 Git + `TrustedGitExecutorV1` 固定环境/identity/time/locale/`--no-gpg-sign --no-verify`。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_policy_handoff.py`；预期 profile/publisher 缺失而 RED。
- [ ] **Step 3:** 写全部真实 digest，禁止占位/fixture digest；撤销 v1 pointer/artifact，不改 receipt。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_policy_handoff.py && git diff --exit-code HEAD -- .scratch/unified-remix-canvas-generation/acceptance/matrix-contract.json`；预期 GREEN，并覆盖 product c27 全消费链、publisher 参数禁止表与 policy c28/c29 transport 隔离。
- [ ] **Step 5:** 编排器必须以 `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/run_handoff_domain.py exec-pty --profile .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-execution-domains.json --role producer --session-label task27-producer` 启动独立 producer credential domain；root-owned broker 切换 producer UID/GID/user+mount+PID namespace、关闭非白名单 FD，且不挂载 signing key/agent。该 harness 经 trusted Git 提交 policy files、消费 product c27、调用 publisher，并发布以完整规范 bytes SHA-256 内容认证、但不带 orchestrator 签名的 export request。publisher 只接收 finalized cache root，不存在 `--product-store-root`/`--product-handoff-id` 参数。producer 安全清理并完全退出后才启动下方独立 orchestrator domain：

```bash
set -euo pipefail
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/run_handoff_domain.py verify-role \
  --profile .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-execution-domains.json \
  --expected-role producer --require-distinct-peer-credentials --require-isolated-user-mount-pid \
  --require-proc-hidepid --require-no-key-mount --require-no-agent-socket --require-closed-fds-from 3 --require-ptrace-denied
test -z "${TRUSTED_HANDOFF_ORCHESTRATOR_SIGNING_KEY_FD:-}${TRUSTED_WORKTREE_PROVISIONER:-}${TRUSTED_WORKTREE_PROVISIONER_DIGEST:-}${TRUSTED_WORKTREE_PROVISIONER_RPC_FD:-}${TRUSTED_WORKTREE_PROVISIONER_KEY_ID:-}${E4_PRIVATE_KEY_FILE:-}${SSH_AUTH_SOCK:-}${SSH_AGENT_PID:-}${GIT_SSH:-}${GIT_SSH_COMMAND:-}${GIT_CONFIG_COUNT:-}${GIT_CONFIG_PARAMETERS:-}"
TASK27_TEMP_PARENT="$(mktemp -d)"
chmod 0700 "$TASK27_TEMP_PARENT"
cleanup_task27_producer() {
  backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py cleanup-owned-tree --root "$TASK27_TEMP_PARENT" --expected-mode 0700
}
trap 'cleanup_task27_producer' EXIT
TASK27_PRODUCT_CACHE="$TASK27_TEMP_PARENT/product-handoff-cache"
TASK27_TOOLCHAIN_ROOT="$TASK27_TEMP_PARENT/toolchain"
TASK27_GIT_HOME="$TASK27_TEMP_PARENT/git-home"
TASK27_GIT_XDG="$TASK27_TEMP_PARENT/git-xdg"
TASK27_GIT_HOOKS="$TASK27_TEMP_PARENT/empty-hooks"
TASK27_GIT_RESULT="$TASK27_TEMP_PARENT/git-write-result.json"
mkdir -m 0700 "$TASK27_PRODUCT_CACHE" "$TASK27_TOOLCHAIN_ROOT" "$TASK27_GIT_HOME" "$TASK27_GIT_XDG" "$TASK27_GIT_HOOKS"
chmod 0555 "$TASK27_GIT_HOOKS"
TASK27_TOOLCHAIN_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py read-artifact-ref \
  --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json \
  --content-store-root .scratch/unified-remix-canvas-generation/acceptance/trust/toolchains)"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize \
  --artifact-ref "$TASK27_TOOLCHAIN_REF" --output-root "$TASK27_TOOLCHAIN_ROOT" --offline
TASK27_GIT_BIN="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize-entrypoint --materialized-root "$TASK27_TOOLCHAIN_ROOT" --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --name git --field path)"
TASK27_GIT_DIGEST="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize-entrypoint --materialized-root "$TASK27_TOOLCHAIN_ROOT" --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --name git --field digest)"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_git.py commit \
  --repository-root "$PWD" --git-bin "$TASK27_GIT_BIN" --git-digest "$TASK27_GIT_DIGEST" \
  --home-root "$TASK27_GIT_HOME" --xdg-root "$TASK27_GIT_XDG" --hooks-root "$TASK27_GIT_HOOKS" \
  --path .scratch/unified-remix-canvas-generation/acceptance/profiles/ticket-23.json \
  --path .scratch/unified-remix-canvas-generation/acceptance/trusted-registry.json \
  --path .scratch/unified-remix-canvas-generation/acceptance/trust/revocations.json \
  --path .scratch/unified-remix-canvas-generation/acceptance/tools/publish_ticket23_policy_handoff.py \
  --path .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile_v2.py \
  --path .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_policy_handoff.py \
  --message "policy: freeze ticket 23 trusted evidence profile" \
  --author-name "Trusted Evidence Builder" --author-email trusted-evidence@invalid --author-date 2000-01-01T00:00:00Z \
  --committer-name "Trusted Evidence Builder" --committer-email trusted-evidence@invalid --committer-date 2000-01-01T00:00:00Z \
  --allow-object-format sha1 --allow-object-format sha256 --no-gpg-sign --no-verify --require-clean-after --result "$TASK27_GIT_RESULT"
TASK27_POLICY_COMMIT_ALGORITHM="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_git.py read-result --result "$TASK27_GIT_RESULT" --field commit.algorithm)"
TASK27_POLICY_COMMIT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_git.py read-result --result "$TASK27_GIT_RESULT" --field commit.hex)"
case "$TASK27_POLICY_COMMIT_ALGORITHM" in
  sha1) printf '%s\n' "$TASK27_POLICY_COMMIT" | grep -Eq '^[0-9a-f]{40}$'; TASK27_ZERO_OID=0000000000000000000000000000000000000000 ;;
  sha256) printf '%s\n' "$TASK27_POLICY_COMMIT" | grep -Eq '^[0-9a-f]{64}$'; TASK27_ZERO_OID=0000000000000000000000000000000000000000000000000000000000000000 ;;
  *) exit 1 ;;
esac
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py verify-cache --cache-root "$TASK27_PRODUCT_CACHE" --require-empty --expected-mode 0700
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py fetch --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c27 --consumer-task 27 --expected-destination-executor-id "$TRUSTED_PRODUCT_C27_DESTINATION_EXECUTOR_ID" --cache-root "$TASK27_PRODUCT_CACHE"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py import --cache-root "$TASK27_PRODUCT_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c27 --consumer-task 27 --expected-destination-executor-id "$TRUSTED_PRODUCT_C27_DESTINATION_EXECUTOR_ID"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py finalize --cache-root "$TASK27_PRODUCT_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c27 --expected-kind product-source --expected-producer-task 22 --consumer-task 27 --expected-destination-executor-id "$TRUSTED_PRODUCT_C27_DESTINATION_EXECUTOR_ID"
TASK27_PRODUCT_COMMIT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read-product-subject --cache-root "$TASK27_PRODUCT_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c27 --consumer-task 27 --expected-destination-executor-id "$TRUSTED_PRODUCT_C27_DESTINATION_EXECUTOR_ID" --field hex)"
TASK27_PRODUCT_SUBJECT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read-product-subject --cache-root "$TASK27_PRODUCT_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c27 --consumer-task 27 --expected-destination-executor-id "$TRUSTED_PRODUCT_C27_DESTINATION_EXECUTOR_ID" --field canonicalSubject)"
printf '%s\n' "$TASK27_PRODUCT_COMMIT" | grep -Eq '^[0-9a-f]{40}$'
test "$TASK27_PRODUCT_SUBJECT" = "git://product@$TASK27_PRODUCT_COMMIT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_git.py update-ref-no-replace \
  --repository-root "$PWD" --git-bin "$TASK27_GIT_BIN" --git-digest "$TASK27_GIT_DIGEST" \
  --home-root "$TASK27_GIT_HOME" --xdg-root "$TASK27_GIT_XDG" --hooks-root "$TASK27_GIT_HOOKS" \
  --ref refs/tags/trusted-acceptance/ticket-23-policy-v1 --new-algorithm "$TASK27_POLICY_COMMIT_ALGORITHM" \
  --new "$TASK27_POLICY_COMMIT" --expected-old "$TASK27_ZERO_OID" --accept-identical-existing
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_git.py verify-fixed-ref \
  --repository-root "$PWD" --git-bin "$TASK27_GIT_BIN" --git-digest "$TASK27_GIT_DIGEST" \
  --home-root "$TASK27_GIT_HOME" --xdg-root "$TASK27_GIT_XDG" --hooks-root "$TASK27_GIT_HOOKS" \
  --ref refs/tags/trusted-acceptance/ticket-23-policy-v1 --expected-algorithm "$TASK27_POLICY_COMMIT_ALGORITHM" \
  --expected "$TASK27_POLICY_COMMIT" --require-head --require-clean
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/publish_ticket23_policy_handoff.py \
  --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --handoff-id ticket-23-policy-v1 --policy-commit-algorithm "$TASK27_POLICY_COMMIT_ALGORITHM" --policy-commit "$TASK27_POLICY_COMMIT" \
  --product-handoff-cache-root "$TASK27_PRODUCT_CACHE" \
  --profile .scratch/unified-remix-canvas-generation/acceptance/profiles/ticket-23.json \
  --registry .scratch/unified-remix-canvas-generation/acceptance/trusted-registry.json \
  --revocations .scratch/unified-remix-canvas-generation/acceptance/trust/revocations.json \
  --python-build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/python-build-record.json \
  --npm-build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/npm-build-record.json \
  --network-proof .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/network-proof.json \
  --toolchain-build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json \
  --platform-identity .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/platform-identity.json \
  --reviewer-policy .scratch/unified-remix-canvas-generation/acceptance/trust/reviewer-policy.json \
  --producer-task 27 --consumer-task 28 --consumer-task 29 --publish-local-only
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py publish-export-request \
  --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --handoff-id ticket-23-policy-v1 \
  --expected-kind policy --expected-producer-task 27 --consumer-task 28 --consumer-task 29
cleanup_task27_producer
test ! -e "$TASK27_TEMP_PARENT"
trap - EXIT
exit
```

producer shell 完全退出后，编排器以全新的 `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/run_handoff_domain.py exec-pty --profile .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-execution-domains.json --role orchestrator --session-label task27-orchestrator --key-slot handoff-transport-v1` 启动不同 UID/GID/user+mount+PID namespace 的 orchestrator domain；broker 只注入只读 signing-key FD，不传 path/value。该域再为 policy consumers 28/29 独立执行全链：

```bash
set -euo pipefail
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/run_handoff_domain.py verify-role \
  --profile .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-execution-domains.json \
  --expected-role orchestrator --require-distinct-peer-credentials --require-isolated-user-mount-pid \
  --require-proc-hidepid --require-signing-key-fd "$TRUSTED_HANDOFF_ORCHESTRATOR_SIGNING_KEY_FD" --require-ptrace-denied
test "$TRUSTED_HANDOFF_ORCHESTRATOR_SIGNING_KEY_FD" -ge 3
test -z "${TRUSTED_WORKTREE_PROVISIONER:-}${TRUSTED_WORKTREE_PROVISIONER_DIGEST:-}${TRUSTED_WORKTREE_PROVISIONER_RPC_FD:-}${TRUSTED_WORKTREE_PROVISIONER_KEY_ID:-}${TASK27_TEMP_PARENT:-}${TASK27_PRODUCT_CACHE:-}${TASK27_POLICY_COMMIT:-}${TASK27_PRODUCT_COMMIT:-}${TASK27_PRODUCT_SUBJECT:-}"
printf '%s\n' "$TRUSTED_POLICY_EXPORT_REQUEST_REF" | grep -Eq '^sha256:[0-9a-f]{64}$'
TASK27_ORCHESTRATOR_PARENT="$(mktemp -d)"
chmod 0700 "$TASK27_ORCHESTRATOR_PARENT"
cleanup_task27_orchestrator() {
  backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py cleanup-owned-tree --root "$TASK27_ORCHESTRATOR_PARENT" --expected-mode 0700
}
trap 'cleanup_task27_orchestrator' EXIT
TASK27_POLICY_C28_STAGE_ROOT="$TASK27_ORCHESTRATOR_PARENT/c28"
TASK27_POLICY_C29_STAGE_ROOT="$TASK27_ORCHESTRATOR_PARENT/c29"
mkdir -m 0700 "$TASK27_POLICY_C28_STAGE_ROOT" "$TASK27_POLICY_C29_STAGE_ROOT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_orchestrator.py verify-export-request --source-store-root "$TRUSTED_HANDOFF_STORE_ROOT" --export-request-ref "$TRUSTED_POLICY_EXPORT_REQUEST_REF" --handoff-id ticket-23-policy-v1 --expected-kind policy --expected-producer-task 27 --consumer-task 28 --consumer-task 29
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_orchestrator.py run-transport --source-store-root "$TRUSTED_HANDOFF_STORE_ROOT" --target-store-root "$TRUSTED_POLICY_C28_TARGET_STORE_ROOT" --stage-root "$TASK27_POLICY_C28_STAGE_ROOT" --export-request-ref "$TRUSTED_POLICY_EXPORT_REQUEST_REF" --handoff-id ticket-23-policy-v1 --transport-id ticket-23-policy-v1-c28 --consumer-task 28 --store-namespace ai-video-reverse-engineer/ticket-23 --source-executor-id "$TRUSTED_SOURCE_EXECUTOR_ID" --destination-executor-id "$TRUSTED_POLICY_C28_DESTINATION_EXECUTOR_ID" --nonce "$TRUSTED_POLICY_C28_NONCE" --sequence "$TRUSTED_POLICY_C28_SEQUENCE" --expires-at "$TRUSTED_POLICY_C28_EXPIRES_AT" --signing-key-fd "$TRUSTED_HANDOFF_ORCHESTRATOR_SIGNING_KEY_FD" --key-id "$TRUSTED_HANDOFF_ORCHESTRATOR_KEY_ID"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_orchestrator.py run-transport --source-store-root "$TRUSTED_HANDOFF_STORE_ROOT" --target-store-root "$TRUSTED_POLICY_C29_TARGET_STORE_ROOT" --stage-root "$TASK27_POLICY_C29_STAGE_ROOT" --export-request-ref "$TRUSTED_POLICY_EXPORT_REQUEST_REF" --handoff-id ticket-23-policy-v1 --transport-id ticket-23-policy-v1-c29 --consumer-task 29 --store-namespace ai-video-reverse-engineer/ticket-23 --source-executor-id "$TRUSTED_SOURCE_EXECUTOR_ID" --destination-executor-id "$TRUSTED_POLICY_C29_DESTINATION_EXECUTOR_ID" --nonce "$TRUSTED_POLICY_C29_NONCE" --sequence "$TRUSTED_POLICY_C29_SEQUENCE" --expires-at "$TRUSTED_POLICY_C29_EXPIRES_AT" --signing-key-fd "$TRUSTED_HANDOFF_ORCHESTRATOR_SIGNING_KEY_FD" --key-id "$TRUSTED_HANDOFF_ORCHESTRATOR_KEY_ID"
cleanup_task27_orchestrator
test ! -e "$TASK27_ORCHESTRATOR_PARENT"
trap - EXIT
exit
```

两个 stage root 均是编排器预建的空、0700、no-symlink 绝对根；任一 transport 失败不影响另一 transport，但对应 consumer 不得启动。

### Task 28: 从固定 policy commit collect/register

**Files:**
- Create generated immutable objects: `.scratch/unified-remix-canvas-generation/acceptance/evidence/sha256/<aa>/<digest>`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/evidence-catalog.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/audit/ticket-23-trusted-evidence.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_collect_register.py`

**Interfaces:**
- Consumes: 从 `$TRUSTED_HANDOFF_STORE_ROOT` authenticated fetch 的 product/policy handoffs、fixed policy ref、两 dependency refs/network proof 与稳定 replay binding；每次 collect 构造短生命周期 snapshot。E2 replay 每 leaf 新空 cache，不读取 Task 22/23 临时根。
- Produces: E1 两个、E2 四个、E4 一个 artifacts/pointers；contract 不变。

- [ ] **Step 1:** 保留 wrong-policy/snapshot-race/missing-E4/source-dependency/handoff/ref tests 与 sequential historical regression；新增 Ticket 23 batch test：一次 typed preflight 必须验证两个 authenticated handoffs、独立 typed fixed policy commit、从 product source 派生的固定 sha1/40 typed subject、container/raw proof、dependency records/network proof、profile/verifier/subject/policy bindings 与七 artifacts 的 canonical bytes/receipt semantics。产品 SHA-256、`extensions.objectFormat`、长度错配、空值、污染、错 ref/subject/binding/E4 任一出现时第一次 catalog write 前失败；policy commit 单独覆盖 SHA-1/SHA-256；成功只允许全七原子出现。另对三根 aggregate cleanup 注入 policy-cache 首根失败/其余成功、product-cache 中根失败/其余成功、runtime 尾根失败/其余成功，逐例断言三个 helper 都恰调用一次、成功根为 `ENOENT`、总体返回非零且 EXIT trap 仍安装；三根全成功才允许解除 trap。
- [ ] **Step 2:** Steps 2–4 必须在同一个长生命周期 PTY shell session 中运行：以 `exec zsh -f` 初始化，三个代码段写入同一 session ID，唯一 EXIT trap 在 Step 4 register 完成后才解除；禁止独立 `exec`。任何 command substitution/parser/签名/CAS 校验失败立即退出且在 register spy 中保持零调用。在登记 Linux executor 先重载两个 handoffs，并只把已验证字段保存为本 Task 变量：

```bash
exec zsh -f
set -euo pipefail
TASK28_POLICY_HANDOFF_CACHE="$(mktemp -d)"
TASK28_PRODUCT_HANDOFF_CACHE="$(mktemp -d)"
TASK28_RUNTIME_ROOT="$(mktemp -d)"
chmod 0700 "$TASK28_POLICY_HANDOFF_CACHE" "$TASK28_PRODUCT_HANDOFF_CACHE" "$TASK28_RUNTIME_ROOT"
cleanup_task28() {
  local policy_rc=0
  local product_rc=0
  local runtime_rc=0
  backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py cleanup-owned-tree --root "$TASK28_POLICY_HANDOFF_CACHE" --expected-mode 0700 || policy_rc=$?
  backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py cleanup-owned-tree --root "$TASK28_PRODUCT_HANDOFF_CACHE" --expected-mode 0700 || product_rc=$?
  backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py cleanup-owned-tree --root "$TASK28_RUNTIME_ROOT" --expected-mode 0700 || runtime_rc=$?
  if test "$policy_rc" -eq 0; then test ! -e "$TASK28_POLICY_HANDOFF_CACHE" || policy_rc=$?; fi
  if test "$product_rc" -eq 0; then test ! -e "$TASK28_PRODUCT_HANDOFF_CACHE" || product_rc=$?; fi
  if test "$runtime_rc" -eq 0; then test ! -e "$TASK28_RUNTIME_ROOT" || runtime_rc=$?; fi
  test "$policy_rc" -eq 0 && test "$product_rc" -eq 0 && test "$runtime_rc" -eq 0
}
trap 'cleanup_task28' EXIT
TASK28_TOOLCHAIN_ROOT="$TASK28_RUNTIME_ROOT/toolchain"
TASK28_GIT_HOME="$TASK28_RUNTIME_ROOT/git-home"
TASK28_GIT_XDG="$TASK28_RUNTIME_ROOT/git-xdg"
TASK28_GIT_HOOKS="$TASK28_RUNTIME_ROOT/empty-hooks"
mkdir -m 0700 "$TASK28_TOOLCHAIN_ROOT" "$TASK28_GIT_HOME" "$TASK28_GIT_XDG" "$TASK28_GIT_HOOKS"
chmod 0555 "$TASK28_GIT_HOOKS"
TASK28_TOOLCHAIN_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py read-artifact-ref --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --content-store-root .scratch/unified-remix-canvas-generation/acceptance/trust/toolchains)"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize --artifact-ref "$TASK28_TOOLCHAIN_REF" --output-root "$TASK28_TOOLCHAIN_ROOT" --offline
TASK28_GIT_BIN="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize-entrypoint --materialized-root "$TASK28_TOOLCHAIN_ROOT" --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --name git --field path)"
TASK28_GIT_DIGEST="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize-entrypoint --materialized-root "$TASK28_TOOLCHAIN_ROOT" --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --name git --field digest)"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py verify-cache --cache-root "$TASK28_POLICY_HANDOFF_CACHE" --require-empty --expected-mode 0700
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py verify-cache --cache-root "$TASK28_PRODUCT_HANDOFF_CACHE" --require-empty --expected-mode 0700
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py fetch --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --handoff-id ticket-23-policy-v1 --transport-id ticket-23-policy-v1-c28 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_POLICY_C28_DESTINATION_EXECUTOR_ID" --cache-root "$TASK28_POLICY_HANDOFF_CACHE"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py fetch --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c28 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_PRODUCT_C28_DESTINATION_EXECUTOR_ID" --cache-root "$TASK28_PRODUCT_HANDOFF_CACHE"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py import --cache-root "$TASK28_POLICY_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 --transport-id ticket-23-policy-v1-c28 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_POLICY_C28_DESTINATION_EXECUTOR_ID"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py import --cache-root "$TASK28_PRODUCT_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c28 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_PRODUCT_C28_DESTINATION_EXECUTOR_ID"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py finalize --cache-root "$TASK28_POLICY_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 --transport-id ticket-23-policy-v1-c28 --expected-kind policy --expected-producer-task 27 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_POLICY_C28_DESTINATION_EXECUTOR_ID"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py finalize --cache-root "$TASK28_PRODUCT_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c28 --expected-kind product-source --expected-producer-task 22 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_PRODUCT_C28_DESTINATION_EXECUTOR_ID"
TASK28_POLICY_COMMIT_ALGORITHM="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read --cache-root "$TASK28_POLICY_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 --transport-id ticket-23-policy-v1-c28 --expected-kind policy --expected-producer-task 27 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_POLICY_C28_DESTINATION_EXECUTOR_ID" --field producerCommit.algorithm)"
TASK28_POLICY_COMMIT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read --cache-root "$TASK28_POLICY_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 --transport-id ticket-23-policy-v1-c28 --expected-kind policy --expected-producer-task 27 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_POLICY_C28_DESTINATION_EXECUTOR_ID" --field producerCommit.hex)"
TASK28_PRODUCT_COMMIT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read-product-subject --cache-root "$TASK28_PRODUCT_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c28 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_PRODUCT_C28_DESTINATION_EXECUTOR_ID" --field hex)"
TASK28_PRODUCT_SUBJECT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read-product-subject --cache-root "$TASK28_PRODUCT_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c28 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_PRODUCT_C28_DESTINATION_EXECUTOR_ID" --field canonicalSubject)"
printf '%s\n' "$TASK28_PRODUCT_COMMIT" | grep -Eq '^[0-9a-f]{40}$'
test "$TASK28_PRODUCT_SUBJECT" = "git://product@$TASK28_PRODUCT_COMMIT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_git.py verify-fixed-ref \
  --repository-root "$PWD" --git-bin "$TASK28_GIT_BIN" --git-digest "$TASK28_GIT_DIGEST" \
  --home-root "$TASK28_GIT_HOME" --xdg-root "$TASK28_GIT_XDG" --hooks-root "$TASK28_GIT_HOOKS" \
  --ref refs/tags/trusted-acceptance/ticket-23-policy-v1 --expected-algorithm "$TASK28_POLICY_COMMIT_ALGORITHM" \
  --expected "$TASK28_POLICY_COMMIT" --require-head --require-clean
```

所有 `read/read-product-subject` 都必须重算 pointer、record、所引 artifacts 与 bindings；product commit 只能通过 `ProductGitObjectIdV1` 输出 40hex，policy producer commit 通过独立 `GitObjectIdV1` 输出匹配算法长度的值。再核对 profile 的 subject/source/toolchain/platform/dependency/registry/revocation digests 与两个 handoff records 完全一致。随后执行六次 collect，先保存完整 stdout，再由 capture 工具按 operation/profile/verifier/subject 解析；此时禁止 register：

```bash
E1_TOONFLOW_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e1.ticket23.toonflow-source.v1 --subject "$TASK28_PRODUCT_SUBJECT")"
E1_TOONFLOW_ARTIFACT="$(printf '%s' "$E1_TOONFLOW_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect --profile ticket-23/v1 --verifier e1.ticket23.toonflow-source.v1 --subject "$TASK28_PRODUCT_SUBJECT")"
E1_PRODUCT_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e1.ticket23.product-adaptation.v1 --subject "$TASK28_PRODUCT_SUBJECT")"
E1_PRODUCT_ARTIFACT="$(printf '%s' "$E1_PRODUCT_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect --profile ticket-23/v1 --verifier e1.ticket23.product-adaptation.v1 --subject "$TASK28_PRODUCT_SUBJECT")"
E2_BACKEND_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.backend-persistence.v2 --subject "$TASK28_PRODUCT_SUBJECT")"
E2_BACKEND_ARTIFACT="$(printf '%s' "$E2_BACKEND_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect --profile ticket-23/v1 --verifier e2.ticket23.backend-persistence.v2 --subject "$TASK28_PRODUCT_SUBJECT")"
E2_FRONTEND_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.frontend-persistence.v2 --subject "$TASK28_PRODUCT_SUBJECT")"
E2_FRONTEND_ARTIFACT="$(printf '%s' "$E2_FRONTEND_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect --profile ticket-23/v1 --verifier e2.ticket23.frontend-persistence.v2 --subject "$TASK28_PRODUCT_SUBJECT")"
E2_API_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.api.v2 --subject "$TASK28_PRODUCT_SUBJECT")"
E2_API_ARTIFACT="$(printf '%s' "$E2_API_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect --profile ticket-23/v1 --verifier e2.ticket23.api.v2 --subject "$TASK28_PRODUCT_SUBJECT")"
E2_STRUCTURE_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.canvas-structure.v2 --subject "$TASK28_PRODUCT_SUBJECT")"
E2_STRUCTURE_ARTIFACT="$(printf '%s' "$E2_STRUCTURE_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect --profile ticket-23/v1 --verifier e2.ticket23.canvas-structure.v2 --subject "$TASK28_PRODUCT_SUBJECT")"
```

- [ ] **Step 3:** 审核者只在仓库外使用真实私钥；在同一 fail-closed harness 中精确执行：

```bash
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py prepare-e4 --profile ticket-23/v1 --subject "$TASK28_PRODUCT_SUBJECT" --review-input "$E4_REVIEW_INPUT_FILE" --output "$E4_ATTESTATION_FILE"
ssh-keygen -Y sign -n trusted-acceptance-e4 -f "$E4_PRIVATE_KEY_FILE" "$E4_ATTESTATION_FILE"
E4_REVIEW_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect-e4 --profile ticket-23/v1 --subject "$TASK28_PRODUCT_SUBJECT" --attestation "$E4_ATTESTATION_FILE" --signature "$E4_ATTESTATION_FILE.sig" --attachment "$E4_OPERATION_RECORD_FILE")"
E4_REVIEW_ARTIFACT="$(printf '%s' "$E4_REVIEW_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect-e4 --profile ticket-23/v1 --verifier e4.ticket23.canvas-review.v1 --subject "$TASK28_PRODUCT_SUBJECT")"
```

缺真实签名或 parser 失败即停止，禁止测试 key 替代，仍不得 register。
- [ ] **Step 4:** 先运行 `capture_collect_result.py validate-many` 重算七个 CAS refs；随后只调用一次 `register-batch`。该进程重新打开 Step 2 已经 authenticated fetch/import/finalize 的两个 task-local caches，再次验 finalized marker/seal/pointer/record/blob 并构造 `ValidatedRegistrationBatchV1`，完成全部语义预检、snapshot recheck 后才允许唯一 catalog commit：

```bash
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py validate-many --cas-root .scratch/unified-remix-canvas-generation/acceptance/evidence "$E1_TOONFLOW_ARTIFACT" "$E1_PRODUCT_ARTIFACT" "$E2_BACKEND_ARTIFACT" "$E2_FRONTEND_ARTIFACT" "$E2_API_ARTIFACT" "$E2_STRUCTURE_ARTIFACT" "$E4_REVIEW_ARTIFACT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py register-batch \
  --profile ticket-23/v1 --subject "$TASK28_PRODUCT_SUBJECT" --expected-policy-commit-algorithm "$TASK28_POLICY_COMMIT_ALGORITHM" --expected-policy-commit "$TASK28_POLICY_COMMIT" \
  --product-handoff-cache-root "$TASK28_PRODUCT_HANDOFF_CACHE" --product-handoff-id ticket-23-product-source-v1 --product-transport-id ticket-23-product-source-v1-c28 --product-consumer-task 28 --product-expected-destination-executor-id "$TRUSTED_PRODUCT_C28_DESTINATION_EXECUTOR_ID" \
  --policy-handoff-cache-root "$TASK28_POLICY_HANDOFF_CACHE" --policy-handoff-id ticket-23-policy-v1 --policy-transport-id ticket-23-policy-v1-c28 --policy-consumer-task 28 --policy-expected-destination-executor-id "$TRUSTED_POLICY_C28_DESTINATION_EXECUTOR_ID" \
  --candidate "e1.ticket23.toonflow-source.v1=$E1_TOONFLOW_ARTIFACT" \
  --candidate "e1.ticket23.product-adaptation.v1=$E1_PRODUCT_ARTIFACT" \
  --candidate "e2.ticket23.backend-persistence.v2=$E2_BACKEND_ARTIFACT" \
  --candidate "e2.ticket23.frontend-persistence.v2=$E2_FRONTEND_ARTIFACT" \
  --candidate "e2.ticket23.api.v2=$E2_API_ARTIFACT" \
  --candidate "e2.ticket23.canvas-structure.v2=$E2_STRUCTURE_ARTIFACT" \
  --candidate "e4.ticket23.canvas-review.v1=$E4_REVIEW_ARTIFACT"
cleanup_task28
test ! -e "$TASK28_POLICY_HANDOFF_CACHE"
test ! -e "$TASK28_PRODUCT_HANDOFF_CACHE"
test ! -e "$TASK28_RUNTIME_ROOT"
trap - EXIT
exit
```

预期单次输出“七项已原子登记，不代表通过”；任何 preflight 候选错误时 catalog digest 完全不变。audit 记录 typed batch digest、operation snapshot、同一 replay binding、两个 handoff record digests、subject/toolchain/platform/dependency/network-proof/source/proof/supervisor/fingerprint 与七 pointers；最终 catalog 重验七 receipts，contract 前后相同。
- [ ] **Step 5:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_collect_register.py -k 'batch_preflight or sequential_collect_register or wrong_policy or snapshot_race or source_dependency'`，再运行完整文件与 contract zero-diff；预期任一 batch 错误零 catalog 写、完整 batch final catalog 七项 GREEN。最后执行 `git add .scratch/unified-remix-canvas-generation/acceptance/{evidence,evidence-catalog.json,audit/ticket-23-trusted-evidence.json,test_ticket23_collect_register.py} && git commit -m "evidence: register ticket 23 trusted replay"`。

### Task 29: 晋升 C01–C03 并验证失效重对账

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_promotion.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/matrix-contract.json`
- Modify: `.scratch/unified-remix-canvas-generation/issues/23-align-toonflow-canvas-shell-and-persistence.md`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/README.md`
- Ephemeral only: 0700 `TASK29_CACHE_PARENT/{handoff-cache,promotion/diagnostics,toolchain,git-home,git-xdg,empty-hooks}` 与 `promotion/result.json`；diagnostics 只供审计、不被 transaction 读取，EXIT trap 清理，不跟踪、不跨 Task 复用。

**Interfaces:**
- Consumes: Task 28 七个有效 pointers、Task 17 workflow、Tasks 7/18 trusted Git，以及从绝对 `$TRUSTED_HANDOFF_STORE_ROOT` authenticated fetch/import/finalize/read 的 policy handoff（producer=27、consumer=29）与 fixed policy ref；不接受相对路径、Task 28 cache、shell 变量或当前 branch 推断 identity。
- Produces: 仅 C01–C03 原子 projection/reconciliation；其他 135 项不变。

- [ ] **Step 1:** 写逐项 requirement、criteria reject/missing、revoked/tampered/unreachable、snapshot-race、deny-list tests；覆盖 policy transport 必须为 `ticket-23-policy-v1-c29`/consumer 29/当前 destination executor，错 consumer transport、篡改 finalized cache、七 catalog refs 任一错 subject/悬空/换绑、缺任一显式 product-source/network-proof/replay-policy binding、product subject 非 sha1/40、SHA-256 product object 或出现 `extensions.objectFormat` 均在 contract write=0 时拒绝；policy commit 仍独立覆盖 SHA-1/SHA-256。用 phase hook 在 transaction 内存 preflight 后分别替换 diagnostic preflight、cache path/root identity、pointer、manifest、seal、record/CAS：diagnostic 替换无授权效果，其余全部 contractWrites=0。成功路径断言同一进程一次原子投影 C01–C03、投影后新 snapshot 立即 reconcile，且输出 parser 不接受未知字段或伪造 digest。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_promotion.py`；预期尚未投影 RED。
- [ ] **Step 3:** 仅执行下列完整命令重载 policy handoff、从 verified product-source CAS 派生 typed subject、读取七个 catalog refs，并调用唯一 `promote-transaction` 完成内存 snapshot→typed preflight→最后复核→单次原子 projection→投影后 snapshot→reconcile。`verify-cache` 在 fetch 前验根是当次新建、0700、空、no-symlink 且只有唯一打开 identity；transaction 自己 no-follow 重开并持有 cache dirfd 到写入完成，逐入口拒绝 symlink、非 regular file、hard-linked regular file、额外 entry、root identity 变化或跨 consumer transport；所有 command substitution 都由 strict parser 约束，trap 无论成败都删除 task-local parent：

```bash
set -euo pipefail
TASK29_CACHE_PARENT="$(mktemp -d)"
chmod 0700 "$TASK29_CACHE_PARENT"
TASK29_HANDOFF_CACHE="$TASK29_CACHE_PARENT/handoff-cache"
TASK29_PROMOTION_ROOT="$TASK29_CACHE_PARENT/promotion"
TASK29_DIAGNOSTIC_ROOT="$TASK29_PROMOTION_ROOT/diagnostics"
TASK29_TOOLCHAIN_ROOT="$TASK29_CACHE_PARENT/toolchain"
TASK29_GIT_HOME="$TASK29_CACHE_PARENT/git-home"
TASK29_GIT_XDG="$TASK29_CACHE_PARENT/git-xdg"
TASK29_GIT_HOOKS="$TASK29_CACHE_PARENT/empty-hooks"
mkdir -m 0700 "$TASK29_HANDOFF_CACHE"
mkdir -m 0700 "$TASK29_PROMOTION_ROOT"
mkdir -m 0700 "$TASK29_DIAGNOSTIC_ROOT"
mkdir -m 0700 "$TASK29_TOOLCHAIN_ROOT" "$TASK29_GIT_HOME" "$TASK29_GIT_XDG" "$TASK29_GIT_HOOKS"
chmod 0555 "$TASK29_GIT_HOOKS"
cleanup_task29() {
  backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py cleanup-owned-tree --root "$TASK29_CACHE_PARENT" --expected-mode 0700
}
trap 'cleanup_task29' EXIT
TASK29_TOOLCHAIN_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py read-artifact-ref --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --content-store-root .scratch/unified-remix-canvas-generation/acceptance/trust/toolchains)"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize --artifact-ref "$TASK29_TOOLCHAIN_REF" --output-root "$TASK29_TOOLCHAIN_ROOT" --offline
TASK29_GIT_BIN="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize-entrypoint --materialized-root "$TASK29_TOOLCHAIN_ROOT" --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --name git --field path)"
TASK29_GIT_DIGEST="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize-entrypoint --materialized-root "$TASK29_TOOLCHAIN_ROOT" --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --name git --field digest)"
test -d "$TASK29_HANDOFF_CACHE"
test ! -L "$TASK29_HANDOFF_CACHE"
test -z "$(find "$TASK29_HANDOFF_CACHE" -mindepth 1 -print -quit)"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py verify-cache \
  --cache-root "$TASK29_HANDOFF_CACHE" --require-empty --expected-mode 0700
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py fetch \
  --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --handoff-id ticket-23-policy-v1 \
  --transport-id ticket-23-policy-v1-c29 --consumer-task 29 \
  --expected-destination-executor-id "$TRUSTED_POLICY_C29_DESTINATION_EXECUTOR_ID" \
  --cache-root "$TASK29_HANDOFF_CACHE"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py import \
  --cache-root "$TASK29_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 \
  --transport-id ticket-23-policy-v1-c29 --consumer-task 29 \
  --expected-destination-executor-id "$TRUSTED_POLICY_C29_DESTINATION_EXECUTOR_ID"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py finalize \
  --cache-root "$TASK29_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 \
  --transport-id ticket-23-policy-v1-c29 \
  --expected-kind policy --expected-producer-task 27 --consumer-task 29 \
  --expected-destination-executor-id "$TRUSTED_POLICY_C29_DESTINATION_EXECUTOR_ID"
TASK29_POLICY_COMMIT_ALGORITHM="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read \
  --cache-root "$TASK29_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 \
  --transport-id ticket-23-policy-v1-c29 \
  --expected-kind policy --expected-producer-task 27 --consumer-task 29 --expected-destination-executor-id "$TRUSTED_POLICY_C29_DESTINATION_EXECUTOR_ID" --field producerCommit.algorithm)"
TASK29_POLICY_COMMIT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read \
  --cache-root "$TASK29_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 \
  --transport-id ticket-23-policy-v1-c29 \
  --expected-kind policy --expected-producer-task 27 --consumer-task 29 --expected-destination-executor-id "$TRUSTED_POLICY_C29_DESTINATION_EXECUTOR_ID" --field producerCommit.hex)"
TASK29_PRODUCT_SOURCE_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read --cache-root "$TASK29_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 --transport-id ticket-23-policy-v1-c29 --expected-kind policy --expected-producer-task 27 --consumer-task 29 --expected-destination-executor-id "$TRUSTED_POLICY_C29_DESTINATION_EXECUTOR_ID" --field binding.product-source)"
TASK29_NETWORK_PROOF_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read --cache-root "$TASK29_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 --transport-id ticket-23-policy-v1-c29 --expected-kind policy --expected-producer-task 27 --consumer-task 29 --expected-destination-executor-id "$TRUSTED_POLICY_C29_DESTINATION_EXECUTOR_ID" --field binding.network-proof)"
TASK29_REPLAY_POLICY_BINDING="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read --cache-root "$TASK29_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 --transport-id ticket-23-policy-v1-c29 --expected-kind policy --expected-producer-task 27 --consumer-task 29 --expected-destination-executor-id "$TRUSTED_POLICY_C29_DESTINATION_EXECUTOR_ID" --field binding.replay-policy)"
TASK29_PRODUCT_COMMIT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read-product-subject --cache-root "$TASK29_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 --transport-id ticket-23-policy-v1-c29 --consumer-task 29 --expected-destination-executor-id "$TRUSTED_POLICY_C29_DESTINATION_EXECUTOR_ID" --product-source-ref "$TASK29_PRODUCT_SOURCE_REF" --field hex)"
TASK29_SUBJECT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read-product-subject --cache-root "$TASK29_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 --transport-id ticket-23-policy-v1-c29 --consumer-task 29 --expected-destination-executor-id "$TRUSTED_POLICY_C29_DESTINATION_EXECUTOR_ID" --product-source-ref "$TASK29_PRODUCT_SOURCE_REF" --field canonicalSubject)"
printf '%s\n' "$TASK29_PRODUCT_COMMIT" | grep -Eq '^[0-9a-f]{40}$'
test "$TASK29_SUBJECT" = "git://product@$TASK29_PRODUCT_COMMIT"
printf '%s\n' "$TASK29_PRODUCT_SOURCE_REF" "$TASK29_NETWORK_PROOF_REF" "$TASK29_REPLAY_POLICY_BINDING" | grep -Ec '^sha256:[0-9a-f]{64}$' | grep -qx '3'
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_git.py verify-fixed-ref \
  --repository-root "$PWD" --git-bin "$TASK29_GIT_BIN" --git-digest "$TASK29_GIT_DIGEST" \
  --home-root "$TASK29_GIT_HOME" --xdg-root "$TASK29_GIT_XDG" --hooks-root "$TASK29_GIT_HOOKS" \
  --ref refs/tags/trusted-acceptance/ticket-23-policy-v1 --expected-algorithm "$TASK29_POLICY_COMMIT_ALGORITHM" \
  --expected "$TASK29_POLICY_COMMIT" --require-clean

TASK29_E1_TOONFLOW_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py read-catalog-ref --catalog .scratch/unified-remix-canvas-generation/acceptance/evidence-catalog.json --cas-root .scratch/unified-remix-canvas-generation/acceptance/evidence --profile ticket-23/v1 --verifier e1.ticket23.toonflow-source.v1 --subject "$TASK29_SUBJECT")"
TASK29_E1_PRODUCT_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py read-catalog-ref --catalog .scratch/unified-remix-canvas-generation/acceptance/evidence-catalog.json --cas-root .scratch/unified-remix-canvas-generation/acceptance/evidence --profile ticket-23/v1 --verifier e1.ticket23.product-adaptation.v1 --subject "$TASK29_SUBJECT")"
TASK29_E2_BACKEND_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py read-catalog-ref --catalog .scratch/unified-remix-canvas-generation/acceptance/evidence-catalog.json --cas-root .scratch/unified-remix-canvas-generation/acceptance/evidence --profile ticket-23/v1 --verifier e2.ticket23.backend-persistence.v2 --subject "$TASK29_SUBJECT")"
TASK29_E2_FRONTEND_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py read-catalog-ref --catalog .scratch/unified-remix-canvas-generation/acceptance/evidence-catalog.json --cas-root .scratch/unified-remix-canvas-generation/acceptance/evidence --profile ticket-23/v1 --verifier e2.ticket23.frontend-persistence.v2 --subject "$TASK29_SUBJECT")"
TASK29_E2_API_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py read-catalog-ref --catalog .scratch/unified-remix-canvas-generation/acceptance/evidence-catalog.json --cas-root .scratch/unified-remix-canvas-generation/acceptance/evidence --profile ticket-23/v1 --verifier e2.ticket23.api.v2 --subject "$TASK29_SUBJECT")"
TASK29_E2_STRUCTURE_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py read-catalog-ref --catalog .scratch/unified-remix-canvas-generation/acceptance/evidence-catalog.json --cas-root .scratch/unified-remix-canvas-generation/acceptance/evidence --profile ticket-23/v1 --verifier e2.ticket23.canvas-structure.v2 --subject "$TASK29_SUBJECT")"
TASK29_E4_REVIEW_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py read-catalog-ref --catalog .scratch/unified-remix-canvas-generation/acceptance/evidence-catalog.json --cas-root .scratch/unified-remix-canvas-generation/acceptance/evidence --profile ticket-23/v1 --verifier e4.ticket23.canvas-review.v1 --subject "$TASK29_SUBJECT")"
printf '%s\n' "$TASK29_E1_TOONFLOW_REF" "$TASK29_E1_PRODUCT_REF" "$TASK29_E2_BACKEND_REF" "$TASK29_E2_FRONTEND_REF" "$TASK29_E2_API_REF" "$TASK29_E2_STRUCTURE_REF" "$TASK29_E4_REVIEW_REF" | grep -Ec '^sha256:[0-9a-f]{64}$' | grep -qx '7'

TASK29_TRANSACTION_RESULT="$TASK29_PROMOTION_ROOT/result.json"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/validate_acceptance.py promote-transaction \
  --profile ticket-23/v1 --expected-subject "$TASK29_SUBJECT" --expected-subject-commit "$TASK29_PRODUCT_COMMIT" \
  --policy-handoff-cache-root "$TASK29_HANDOFF_CACHE" --policy-handoff-id ticket-23-policy-v1 --policy-transport-id ticket-23-policy-v1-c29 --policy-consumer-task 29 --policy-expected-destination-executor-id "$TRUSTED_POLICY_C29_DESTINATION_EXECUTOR_ID" \
  --expected-policy-commit-algorithm "$TASK29_POLICY_COMMIT_ALGORITHM" --expected-policy-commit "$TASK29_POLICY_COMMIT" --product-source-ref "$TASK29_PRODUCT_SOURCE_REF" --network-proof-ref "$TASK29_NETWORK_PROOF_REF" --replay-policy-binding "$TASK29_REPLAY_POLICY_BINDING" \
  --evidence-ref "e1.ticket23.toonflow-source.v1=$TASK29_E1_TOONFLOW_REF" --evidence-ref "e1.ticket23.product-adaptation.v1=$TASK29_E1_PRODUCT_REF" \
  --evidence-ref "e2.ticket23.backend-persistence.v2=$TASK29_E2_BACKEND_REF" --evidence-ref "e2.ticket23.frontend-persistence.v2=$TASK29_E2_FRONTEND_REF" \
  --evidence-ref "e2.ticket23.api.v2=$TASK29_E2_API_REF" --evidence-ref "e2.ticket23.canvas-structure.v2=$TASK29_E2_STRUCTURE_REF" \
  --evidence-ref "e4.ticket23.canvas-review.v1=$TASK29_E4_REVIEW_REF" --requirement-id C01 --requirement-id C02 --requirement-id C03 \
  --diagnostic-output-root "$TASK29_DIAGNOSTIC_ROOT" --output "$TASK29_TRANSACTION_RESULT"
TASK29_TRANSACTION_DIGEST="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/validate_acceptance.py read-result --input "$TASK29_TRANSACTION_RESULT" --expected-operation promote-transaction --field resultDigest)"
TASK29_POST_RECONCILE_SNAPSHOT_DIGEST="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/validate_acceptance.py read-result --input "$TASK29_TRANSACTION_RESULT" --expected-operation promote-transaction --field reconciledSnapshotDigest)"
printf '%s\n' "$TASK29_TRANSACTION_DIGEST" "$TASK29_POST_RECONCILE_SNAPSHOT_DIGEST" | grep -Ec '^sha256:[0-9a-f]{64}$' | grep -qx '2'
cleanup_task29
test ! -e "$TASK29_CACHE_PARENT"
trap - EXIT
exit
```

`promote-transaction` 必须在唯一进程中重开 finalized policy cache、fixed ref、catalog/CAS、profile/registry/revocations、matrix contract，并将命令行 expected values 与验证后对象逐项等值比较；内存 preflight 完成后仍持有 cache dirfd，在 contract lock 内最后复核全部身份和 operation snapshot，再对内存 candidate contract 完成 projection、post-projection snapshot 与 reconcile，最终 bytes 只原子写一次。`diagnostic-output-root` 中任何文件都不被 transaction 重读，替换它不能影响决定；任何其他 race/failure 都发生在 contract temp file 创建前。禁止保留独立 `preflight-promotion/promote/reconcile` 写 CLI，也禁止从当前 branch、Task 28 cache、相对路径或未验证 shell 值推断 identity。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance`；预期 138 项与其他 135 项不变，失效正确降级。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/{matrix-contract.json,README.md,test_ticket23_promotion.py} .scratch/unified-remix-canvas-generation/issues/23-align-toonflow-canvas-shell-and-persistence.md && git commit -m "feat: promote ticket 23 trusted acceptance"`

## Self-review

### 规格与审查覆盖

| 范围 | Task / 证据 |
| --- | --- |
| 规格 1–5、operation snapshot、A-before-B、零写入 | 2、5、16–17 |
| 6.1 suite/selection/entrypoint | 2、15、19–20、27 |
| 6.2 archive/toolchain/security/platform | 1、6–7、18 |
| 6.3 Python/npm repository | 8–9、19–20、23 |
| 6.4 receipt 与唯一 scanner | 3–4、16、28 |
| 7 barrier/operation-snapshot race | 2、5、17、28 |
| 8.1–8.2 raw Git source/sanitized checkout | 2、10–11、15、25 |
| 8.3 containment | 13–14、21 |
| 8.4 redaction | 3、20 |
| 8.5 reporter | 12、20、23、27 |
| 9 workflow/atomicity | 16–17、27–29 |
| 10 exact errors | 2、18–21 |
| 11 real fixtures | 18 toolchain/platform、19 Python、20 npm/reporter、21 containment |
| 11.6 Ticket 23 | 22–23、27–29 |
| 13 E1/E4/engines | 24–29 |
| 14 migration/history | 2、27 |
| Critical 1：receipt 不自失效 | 2、4–5、16–17、28 |
| Critical 2：raw Git container | 2、10–11、15、22、25 |
| Critical 3：bootstrap 闭环 | 1、6、18 |
| Critical 4：双层销毁 | 13–15、21 |
| Critical 5：generic CLI/E1/E2 | 16、24 |
| Important 1：NamedDigest | 2 |
| Important 2：产品根与证据边界 | 22–23、25、28 |
| Important 3：私钥扫描 | 24、26 |
| Important 4：fixture builders | 18–21 |
| 第三轮 Important 1：linked-worktree registered boundary | 2、10–11、22、25 |
| 第三轮 Important 2：Ticket 23 dependency reproducibility | 8–9、18、23、27–28 |
| 第三轮 Minor 1：规格审批差异说明 | Global Constraints、执行状态 |
| 第三轮 Minor 2：containment 七方法 | 13–15、21 |
| 第四轮 Critical 1：单进程 owning-FD capability | 2、10、22 |
| 第四轮 Important 1：持久 content-addressed handoff | 2、22、25、27–29 |
| 第四轮 Important 2：collect stdout 捕获与七 ref barrier | 16、24、28 |
| 第四轮 Important 3：产品依赖输入与零网络证明 | 18、22–23、27–28 |
| 第四轮 Important 4：frontend cwd | 23 |
| 第五轮 Critical 1：原始 Git 布局 inspection proof | 10–11、22、25 |
| 第五轮 Critical 2：handoff full-bytes digest、无自引用 | 2、22、27–29 |
| 第五轮 Important 1：handoff no-replace 并发发布 | 2、22、27 |
| 第五轮 Important 2：外部 authenticated 跨 executor store | 2、22–23、25、27–29 |
| 第五轮 Important 3：可执行 policy publish | 27 |
| 第五轮 Important 4：真实 setup/test containment 零网络 | 7、14、18、23、27–28 |
| 第五轮 Important 5：七项 typed semantic batch preflight | 16、28 |
| 第五轮 Minor：generated/ref outputs 清单 | 22、27 |
| 第六轮 Critical：raw bytes 仅内存检查，proof 只绑定 path-free logical topology | Global Constraints、固定跨任务接口、10–11 |
| 第六轮 Important 1：可执行 transport seal 状态机与固定 trust root | 2、22、27–29 |
| 第六轮 Important 2：Task 18 toolchain ref 严格提取与全命令消费 | 23、25、29 |
| 第六轮 Minor：Task 25/29 cache 安全初始化、link 检查与清理 | 2、25、29 |
| 第七轮 Critical：Task 27 只能消费 consumer 27 finalized product cache | 2、22、27 |
| 第七轮 Important 1：target transport 原子 no-replace install、fsync 与 byte-identical retry | Global Constraints、固定跨任务接口、2、22、27 |
| 第七轮 Important 2：每 consumer 独立 transport/seal/nonce/sequence/destination | 2、22–23、25、27–29 |
| 第七轮 Important 3：Task 25/29 全命令核心与 strict output capture | 16–17、25、29 |
| 第七轮 Minor：logical topology 字段、provisioner JSON 与单临时父根 | 10–11、22–23 |
| 第八轮 Critical 1：GitObjectIdV1 与 NamedDigest 严格分型 | 固定跨任务接口、2、10–11、16、22、25、27–29 |
| 第八轮 Critical 2：单进程 capability 持有的 promote transaction | 17、29 |
| 第八轮 Minor 1：共享 run-transport、显式 per-consumer 参数表 | 2、22、27 |
| 第八轮 Minor 2：Task 22/23/28 长生命周期 PTY harness | 22–23、28 |
| 第九轮 Critical：产品 OID/subject 恢复冻结 SHA-1/40hex 契约 | 固定跨任务接口、2、10–11、16–17、22、25、27–29 |
| 第九轮 Important：Task 22 producer/orchestrator 权限与状态隔离 | 2、22 |
| 第九轮 Minor：只读树安全清理、先清理后解除 trap | 固定跨任务接口、2、22–23、25、27–29 |
| 第十轮 Important 1：多根 cleanup 全尝试、聚合失败与 trap 保留 | 固定跨任务接口、2、28 |
| 第十轮 Important 2：linked worktree ownership capability 与精确销毁 | 固定跨任务接口、10、22 |
| 第十轮 Important 3：artifact-bearing Git 固定 executable/config/identity/hooks/signing | Global Constraints、7、18、22、27–29 |
| 第十轮 Important 4：producer/orchestrator 不同 OS credential/namespace/FD | Global Constraints、2、13–14、22、27 |

### 自包含接口、依赖拓扑与真实命令

- bootstrap 与主链：1→2→3→4→5→6→7→8→9→10→11→12→13（独立批准门）→14→15→16→17。
- 真实基础验收：17→18→19→20→21；任一未通过，不得写 production profile。
- Ticket 23：21→22 credential-isolated producer 用 pinned Git 形成主体、raw-layout inspect+SHA-1 canonical build/proof/request publish、capability destroy 专属 linked worktree→不同 credential 的 orchestrator-only authenticated transport→23 跨 executor fetch/sanitized checkout/双空根 dependencies+contained zero-network validation→24 E4→25 proof/container E1→26 reviewer policy→27 credential-isolated producer 用 pinned Git 固定 policy commit/ref并发布 policy request、不同 credential orchestrator transport→28 双 handoff fetch/collect/typed seven-candidate preflight+atomic batch register→29 authenticated fetch 后单进程 promote transaction。
- `ProductRepositorySourceV1`/`RawGitInspectionProofV1` 在 Task 2 定义；Task 10 同一受信命令先在 owning-FD 进程内 inspect 原始布局，raw bytes 用后丢弃，再从已验证关系构建 path-free canonical logical topology/proof；Task 11 正式验证 proof/container 并产出 verified handle。宿主路径、raw bytes 及其 digest 均不进入可重现 artifact。
- `ReplayPolicyBindingV1` 与 `AcceptanceOperationSnapshotV1` 在 Task 2 分开构造，Tasks 5/16–17/28 只重检 operation snapshot，receipt 始终只绑定稳定 replay policy；Task 28 final-catalog 回归证明顺序 register 不使旧 receipt 失效。
- containment 七方法、消息字段、序号/摘要链、权限、binary identity、唯一 kernel mechanism 与 handoff credential-domain contract 在 Task 13 冻结；Task 14 不能重新选型。Tasks 22/27 只能通过 Task 14 root-owned broker 的 `exec-pty` 进入 producer/orchestrator 不同 UID/GID/user+mount+PID namespace，fresh zsh/env allowlist 本身不算隔离证据。
- Task 3 是唯一 scanner owner；receipt/barrier/collect 只调用该接口，无依赖循环。
- Task 1 给出 bootstrap hashes；Tasks 18–21/23 有 exact builders/records。Task 23 在 platform compare 后把真实 uv/npm/pytest/Vitest 全部送入 Task 14 containment，无接口 network namespace+census 证明而非只依赖 `--offline`。
- 跨 Task/权限域只允许已提交 records/CAS/export requests 与编排器验证的绝对外部 `$TRUSTED_HANDOFF_STORE_ROOT`；producer 在无 signing key mount/FD/agent/socket、不同 UID/GID/user+mount+PID namespace 的独立 domain 中执行一次 `publish-local` 与 `publish-export-request` 后安全清理退出，orchestrator 在另一 credential domain 中只获 broker 注入的 signing-key FD，按 request ref 重算 closure，再按 consumer 以完整显式 `TransportJobV1` 参数调用共享 `run-transport`，其内部严格执行 `verify-export-request→export→upload→sign-seal→install-transport`；consumer 再执行 `verify-cache→fetch→import→finalize→read`。每个 transport 的 manifest/seal/ready marker 在 target durable store 原子 no-replace install 并 fsync；seal 认证根只来自固定 policy/allowed-signers；store path/租约不入 digest，Task-local cache/mktemp/trap 不交接。Task 22 发布 request 后必须用 authenticated ownership capability 销毁专属 checkout/linked gitdir/backlink且验证共享 commondir/object store identity不变。Tasks 22–23、25、27–29 的 task-local 0700 roots 都以 `cleanup-owned-tree` 处理只读树，先验证删除再解除 EXIT trap；Task 28 三个独立根无论单根失败都全尝试并聚合结果；policy 另与 fixed Git ref 闭合。所有 artifact-bearing Git 读写只走 Task 7/18 pinned `TrustedGitExecutorV1`。
- Review Focus 五项分别由 Tasks 5/17、3/20、14/21、19–20、10–11/22–23/27–29 持有，编号无漂移。
- 29 个 Task 均为单独实现会话、单独 commit、单独新审查会话；Task 13、27、28 是不可跳过的硬门。

## 执行与批准状态

- E2 v2 书面规格已批准；本修订计划等待用户复审。
- 规格审批历史只改变 `Status` 行；本轮提交必须同时通过“相对 `06827f2e` 规格零 diff”和“相对 `2f304fe` 删除第 3 行后正文零 diff”，不得笼统声称两个 blob 相同。
- 执行方式固定为 subagent-driven：逐 Task 新实现会话 + 新独立审查会话，前一 Task 通过才进入下一 Task，最后全分支独立审查。
- 本次规划会话不授权实现或启动任何 Task；复审通过后从 Task 1 开始，不恢复旧 Task 6，也不跳过 Task 13、27、28。
