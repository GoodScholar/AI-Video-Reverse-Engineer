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
- `RegisteredRepositoryBoundaryV1` 只能由同一受信 source-runner 进程打开、持有并关闭 owning directory handles，禁止 JSON/pickle/路径形式序列化或跨进程恢复；同一命令必须先按规格 §8.2 对原始 `.git/gitdir/commondir` 字节与拓扑执行完整 `RawGitLayoutInspectorV1`，通过后才规范化和构建 container。跨 Task 唯一存储由编排器在每个 executor 启动前提供并验证为绝对路径 `$TRUSTED_HANDOFF_STORE_ROOT`；该目录位于仓库/worktree 之外，宿主路径、mount 点、认证租约不得进入 record、proof 或 digest。CAS 路径恰为 `sha256/<前两位>/<后 62 位>`，pointer/transfer manifest/authenticated sync seal 分别为 `pointers/<handoff-id>.json`、`transfers/<handoff-id>.json`、`seals/<handoff-id>.json`，均 immutable/no-replace；consumer fetch/read 每次重算 pointer、完整 record bytes 与 referenced blobs。`mktemp` 只作单 Task local cache。
- 工具链、依赖、产品源码分别只来自 `trust/toolchains/sha256`、`trust/dependencies/sha256`、`trust/product-sources/sha256`；禁止宿主 PATH/shell/`/usr/bin/env`、live download、主机 cache 或同名工具回退。
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
- 真实 linked worktree 必须在规范化前形成 raw-layout inspection proof；handoff full-bytes/no-replace/cross-executor transport、真实 setup/test 零网络、七项 semantic batch preflight 任一失败均须阻塞且零晋升；Tasks 2、10–11、22–23、27–29 覆盖。

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
    raw_topology_digest: str
    passed_rule_digests: tuple[NamedDigest, ...]
    budget_observations: tuple[NamedDigest, ...]
    subject_commit: str
    container_digest: str

@dataclass(frozen=True)
class TrustedHandoffRecordV1:
    schema: Literal["trusted-acceptance-handoff/v1"]
    handoff_id: str
    kind: Literal["product-source", "policy"]
    producer_task: int
    producer_commit: str
    consumer_tasks: tuple[int, ...]
    bindings: tuple[NamedDigest, ...]

@dataclass(frozen=True)
class ProductRepositorySourceV1:
    subject_repository: str
    subject_commit: str
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
    policy_commit: str
    product_handoff_record_digest: str
    policy_handoff_record_digest: str
    candidates: tuple[RegistrationCandidateV1, ...]
    canonical_digest: str

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

`RegisteredRepositoryBoundaryV1` 是仅存于 source-runner 进程内、拥有 handle 生命周期的 capability：`build` CLI 接收三个 root path 后立即 no-follow 打开为 owning handles，`RawGitLayoutInspectorV1.inspect(boundary, subject_commit)` 在任何逻辑 alias/规范化之前，以 `BoundedStableReader` 读取原始 pointer/control bytes，执行规格 §8.2 全部预算与拒绝规则，返回内存 inspection result；同一命令随后构建 container，最后把 inspection result finalize 为独立 sidecar `RawGitInspectionProofV1`，以 container digest 和 raw-topology digest 闭合。proof 只保存原始字节/拓扑的摘要、规则/profile/version/预算结果，不保存宿主路径或危险原始控制记录；所有后续遍历只从 handles `openat`，直到 container/build-record/proof/handoff 全部发布后才关闭。Task 11 对 proof schema、inspector/profile/rules、raw-topology/build-record/container bindings 做正式验证，再检查 canonical container；不能把 builder 自报的布尔值当作证明。

`TrustedHandoffRecordV1` 的规范 JSON 字段恰为 `schema/handoffId/kind/producerTask/producerCommit/consumerTasks/bindings`，record 内不含自摘要字段；`recordDigest = sha256(full canonical record bytes)`，`recordRef` 与之相等。pointer 固定字段恰为 `schema/handoffId/recordRef/recordDigest/producerTask/producerCommit`，其中 schema=`trusted-acceptance-handoff-pointer/v1` 且 `recordRef == recordDigest`。record schema=`trusted-acceptance-handoff/v1`、`producerCommit` 为小写 40-hex、`consumerTasks` 为升序无重复正整数、每个 binding 恰为 `name/digest`。CAS blob/record 使用 `O_CREAT|O_EXCL` 写临时 regular file、fsync 后以 hard-link-to-final 的 no-replace publish；pointer 同样以临时文件+`linkat` 原子 no-replace 发布并 fsync 父目录。目标已存在时只读回、重算并接受 byte-identical 内容，任何不同内容或并发竞态均拒绝，绝不 replace/覆盖。

`TrustedHandoffStoreV1.open_from_environment()` 要求 `$TRUSTED_HANDOFF_STORE_ROOT` 为编排器验证的绝对、no-symlink、权限/owner/项目租约匹配的外部 durable store。producer `publish` 先写 CAS/record/pointer，再输出不含路径的 `trusted-handoff-transfer/v1` manifest（handoff ID、pointer digest、record ref、全部 blob refs）；编排器以当前 executor 的认证租约上传/同步 manifest 所列 bytes，并返回同摘要 seal。consumer `fetch` 只把该 manifest 的闭包下载到单 Task 空 cache，逐 byte 重算后再 `read`；macOS/Linux 仅传输规范 bytes 与 hex refs，不传输路径、inode、mtime 或 capability。缺认证 seal、跨项目租约、部分同步、重复/未知 ref、pointer/record/blob 不一致均 fail closed。

`NamedDigest.name` 必须是 NFC、非空、无控制字符的逻辑名，`digest` 必须是小写 `sha256:<64hex>`。每个 tuple 由 builder 以 `(name.encode("utf-8"), digest.encode("ascii"))` bytewise 升序规范化；同名条目无论摘要是否相同都拒绝，不同名称可共享同一摘要。suite/profile/toolchain/source/containment 集合对 E2 binding 必须非空；dependency 集合仅在所有 suite 都声明零 ecosystem 时允许空，否则按 `<suite-id>/<ecosystem>` 唯一命名。空集合编码为 JSON `[]`，文件系统创建/遍历顺序不进入结果。

`ReplayPolicyBindingV1`、`AcceptanceOperationSnapshotV1` 与 `ValidatedRegistrationBatchV1` 的 `canonical_digest` 都对除自身外字段按字段名排序、UTF-8 紧凑规范 JSON 编码后取 SHA-256；它们不同于不含内嵌摘要的 handoff record full-bytes digest。任何 wall-clock、mtime、review observation time 都不得进入；顺序 collect/register 改变 catalog 只生成新的 operation snapshot，不改变已有 receipt 的 replay binding。

| 文件/目录 | 单一职责 |
| --- | --- |
| `trusted_evidence/e2_policy.py` | v2 suite、operation、snapshot、product-source schema 与 loader。 |
| `trusted_evidence/redaction.py` | 唯一 shared lexer、streaming redactor、安全缓冲。 |
| `trusted_evidence/bounded_io.py` / `receipts.py` | 有界稳定读取与 receipt codec。 |
| `trusted_evidence/artifact_barrier.py` | operation candidate/pointer/artifact 预检与 snapshot recheck。 |
| `trusted_evidence/canonical_archive.py` | zstd+ustar 规范、预算、no-exec 展开。 |
| `trusted_evidence/toolchain.py` / `platform_identity.py` | toolchain closure/security 与 exact platform identity。 |
| `trusted_evidence/dependency_repository.py` / `*_dependencies.py` | lock mapping 与 deterministic Python/npm consumer views。 |
| `trusted_evidence/raw_git_inspection.py` / `product_source.py` / `git_subject.py` | 原始布局 inspection proof、content source loader、sanitized object view、fixed checkout。 |
| `trusted_evidence/reporters.py` | 真实 pytest/Vitest report 与 selection closure。 |
| `trusted_evidence/containment/` | 七方法协议、wire transcript、Linux supervisor/backend。 |
| `trusted_evidence/replay.py` / `collection.py` | 内存 replay 与成功后唯一 CAS receipt 写入。 |
| `trusted_evidence/evaluation.py` / `projection.py` / `runtime.py` | operation 编排与唯一 contract writer。 |
| `trusted_evidence/handoffs.py` / `tools/trusted_handoff_store.py` | 外部 authenticated store、内容寻址 record、no-replace pointer、跨 executor publish/fetch/read。 |
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
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/registry.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/errors.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_handoffs.py`

**Interfaces:**
- Consumes: 规格 4、6.1、7、9、10、14 和既有六字段 pointer。
- Produces: 上述 immutable 类型、owning-FD context、`TrustedHandoffStoreV1.open_from_environment()`、`HandoffRegistry.publish/fetch/read`；CLI `verify-root` 验绝对根/owner/mode/project lease，`publish` 写 external store，`fetch --store-root ... --cache-root <empty-task-root>` 经认证传输闭包，`read --cache-root ... --handoff-id --expected-kind --expected-producer-task --consumer-task --field producerCommit` 只从 cache 重验并输出字段。另产出 policy/snapshot builders 与 suite factory。

- [ ] **Step 1:** 写 `test_replay_binding_excludes_catalog_contract_and_observation_time`、`test_operation_snapshot_binds_catalog_contract_and_replay_binding`、`test_named_digest_order_is_identical_for_different_creation_order`、`test_duplicate_name_and_illegal_empty_set_reject`、`test_product_source_rejects_host_path`、`test_v1_is_not_upgraded`；另写 handoff RED tests：record 不含自摘要且 full canonical bytes SHA-256 精确等于 pointer `recordRef/recordDigest`；owning handle 拒绝 JSON/pickle/copy；store root 非绝对、symlink、权限/owner/租约/认证 seal 不符，partial/cross-project/macOS→Linux transport 篡改均拒绝；CAS/pointer no-replace；两个 producer 并发发布不同 record 时恰一方成功、另一方稳定失败，最终 bytes 不混合；相同 bytes 幂等接受。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_handoffs.py`；预期缺类型与 handoff owner/registry 而 RED。
- [ ] **Step 3:** 实现严格 `NamedDigest`、规范 JSON 与两个 policy/snapshot digests；实现外部 authenticated handoff store：blob/record 以 full bytes digest 写入 `sha256/<前两位>/<后 62 位>`，临时文件和最终 hard-link 都 no-follow/no-replace，pointer 用相同 `linkat` publish，全部 fsync；已存在只比较完整 bytes。`publish` 输出 transfer manifest 并等待编排器认证 seal，`fetch` 在目标 executor 验 seal 后下载闭包到单 Task cache，`read` 重算 pointer/record/blob 并核对 producer/consumer/kind/commit/bindings。缺任一 policy/handoff/transport 输入即阻塞，禁止 capability、store root、租约或宿主 path 入 record/digest。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_handoffs.py`；预期 GREEN 且 production profile 仍为空。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{e2_policy.py,handoffs.py,registry.py,errors.py} .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_{e2_policy_v2,handoffs}.py && git commit -m "feat: define trusted replay operation policy"`

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
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_toolchain.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_platform_identity.py`

**Interfaces:**
- Consumes: Tasks 2、6 与 uv/Git/Node/npm 实际源码/许可。
- Produces: `ToolchainPackage.open`、`measure_platform`、`compare_platform_identity`。

- [ ] **Step 1:** 写 closure/uv identity/advisory/dynamic library/kernel ABI/PATH marker RED tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_toolchain.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_platform_identity.py`；预期 modules RED。
- [ ] **Step 3:** 实现 exact bytes/build identity 和 offline security snapshot。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_toolchain.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_platform_identity.py`；预期所有异常在 setup spawn 前阻塞。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{toolchain.py,platform_identity.py} .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_{toolchain,platform_identity}.py && git commit -m "feat: verify trusted replay toolchains"`

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
- Produces: `RawGitLayoutInspectorV1.inspect(boundary, subject_commit) -> RawGitInspectionResultV1`、`build_product_source(boundary, inspection, subject_commit) -> ProductRepositorySourceV1`、`finalize_raw_git_proof(inspection, container_digest) -> RawGitInspectionProofV1`、`ProductSourceLoader.load(source, proof_ref, expected_source_digest) -> ValidatedProductRepositoryContainerV1`；唯一 `build_product_source.py build --checkout-root --gitdir-root --commondir-root --subject-commit --handoff-store-root "$TRUSTED_HANDOFF_STORE_ROOT" --handoff-id --producer-task --consumer-task ... --offline` CLI 在同一进程严格执行 open handles→raw inspect→build→finalize proof→publish。source artifact 不反向绑定 replay policy；未经 Task 11 proof/container 正式验证不能称为 verified source。

- [ ] **Step 1:** 写 `test_real_linked_worktree_is_inspected_before_normalization_and_proof_binds_container`：真实 `.git` regular pointer、相对/绝对 gitdir/commondir 写法、精确换行/backlink/control bytes 先由 inspector 读取，proof 绑定 raw-topology/profile/rules/budgets/subject/container；同 commit 在不同绝对空根得到 byte-identical container/build-record/proof/handoff，证明宿主路径未进入摘要。写 path swap/capability serialization 反例；再逐项覆盖规格 §8.2 pointer/ref/config/objects/sidecar/预算/unknown class、非法 UTF-8/NUL/多行/换行、越界/symlink/case-fold/alias/二次重定向/循环/backlink 不精确，全部在规范化或 payload copy 前拒绝；伪造 proof、错 inspector/profile/rule/budget/raw-topology/container binding 也拒绝。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_product_source.py`；预期 RED。
- [ ] **Step 3:** 唯一 source-runner 进程打开并持有三个 handles 后，先调用完整 `RawGitLayoutInspectorV1`；只有 inspection result 成功才把拓扑规范为 `@checkout/@gitdir/@commondir` 并用自有有界 SHA-1 loose/pack/idx/tree reader复制 subject tree 的精确 tracked bytes。危险原始 pointer/control bytes不进 container，ignored/untracked 不捕获；finalize proof 后，将 container/build-record/proof 三者作为 blobs 交给 authenticated handoff publish，全部完成再关闭 handles。普通 `.git` directory 仍只发一份 control tree；任何 handle identity/mount/race 变化拒绝。
- [ ] **Step 4:** container manifest 首项绑定 subject、normalized boundary、`boundaryDigest/trackedCheckoutDigest/controlMetadataDigest/sourceDigest`；proof sidecar 绑定原始 topology digest、inspector/profile、全部规则/预算结果与最终 container digest；build record 绑定 proof ref/digest、subject/tree/container/source/boundary/topology，不含路径/device/inode/mtime。handoff bindings 恰含 `product-source-container`、`product-source-build-record`、`raw-git-inspection-proof`、`boundary-topology`、`tracked-checkout`、`control-metadata`、`source` 七项。loader 重算 container/build-record/proof digests；重跑 tests，预期原始布局矩阵、proof/container 换绑、双根一致性与逃逸反例 GREEN。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{raw_git_inspection.py,product_source.py} .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_product_source.py && git commit -m "feat: bind canonical raw Git product sources"`

### Task 11: 从 verified source 构造 sanitized Git checkout

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/git_subject.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_git_subject.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e2_suite.py`

**Interfaces:**
- Consumes: Task 7 包内 Git、Task 10 `ValidatedProductRepositoryContainerV1`、`RawGitInspectionProofV1` 及 normalized `@checkout/@gitdir/@commondir` boundary；不接受裸 path/cwd/bundle、缺 proof 或 container 外 control root。
- Produces: `GitMetadataInspector.inspect(container) -> VerifiedProductRepositorySourceV1`、`SanitizedGitView.build(source, commit, toolchain_git, destination) -> FixedCheckout`。

- [ ] **Step 1:** 写 proof formal-verification tests：缺失/未知/伪造 inspector/profile/rule/budget/raw-topology digest、proof/build-record/container/subject 任一换绑均在包内 Git spawn 前拒绝；再对 canonical container 覆盖 boundary graph、tracked tree、config/packed-refs/refs/objects 预算、unknown/dangerous config、alternates/http-alternates、replace/grafts/shallow/promisor、sidecars、symlink/duplicate/case-fold、外部 object source 与 race。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_git_subject.py`；预期旧 host Git path RED。
- [ ] **Step 3:** `GitMetadataInspector.inspect(container, raw_proof, build_record)` 先重算 proof 与 container bytes，核对 raw inspector 的固定版本/profile/完整规则集/预算结果、raw-topology digest、subject 和 container binding；再对 canonical `repository/gitdir/common` 做后置 bounded checks。全部通过后才以包内 Git `index-pack --strict`/`fsck --strict` 构造 sanitized bare view并重建 checkout；原始危险记录仍不进入 view。
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
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_protocol.py`

**Interfaces:**
- Consumes: Tasks 2、3、7。
- Produces: 精确七方法 API、canonical supervisor schema、proof digest、已批准 `linux-cgroup-v2-seccomp-notify/v1` gate。

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

- [ ] **Step 1:** 写消息 RED tests；仅允许 `hello/session-created/invocation-created/spawned/exec/write/exit/terminated/empty/invocation-destroyed/session-destroyed/error`，每条含 schema/sessionId/invocationId/seq/kind/scope/objectId/payload/prevDigest/digest；`scope` 仅 `session|invocation` 且必须与 `objectId`、消息 kind 一致。
- [ ] **Step 2:** 冻结无丢失与权限模型：继承 `SOCK_SEQPACKET`；seq 从 0 连续且 digest chain；EOF 仅 `session-destroyed` 后；root-owned supervisor 管理 delegated cgroup/namespace/seccomp listener，child 入组后降权；binary SHA-256、ELF build-id、Cargo.lock、executor image digest 绑定 replay policy/receipt。
- [ ] **Step 3:** 冻结唯一机制：clone3 首次入组；seccomp user notification 观察 execve/execveat 与固定 write/network syscall，拒绝 io_uring/bpf/perf 旁路；只读 mounts+writable allowlist；pidfd/waitid+cgroup.kill+populated=0+rmdir。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_protocol.py`；预期 loss/duplicate/reorder/unknown/overflow/bad digest/binary mismatch 全部 fail closed，并强制每次 invocation 顺序为 `terminate_all → prove_empty → destroy_invocation`，下一 invocation 只能在前一 leaf destroyed 后创建，最后才 `destroy_session`。
- [ ] **Step 5:** `git add docs/superpowers/specs/2026-10-02-e2-v2-linux-containment-profile.md .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_protocol.py && git commit -m "design: freeze trusted replay containment protocol"`；独立审查 0 Critical/Important 才进入 Task 14。

### Task 14: 实现获批 Linux cgroup v2 backend

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux_cgroup_v2.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/vm_cgroup.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux-supervisor/Cargo.toml`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux-supervisor/Cargo.lock`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux-supervisor/src/main.rs`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_linux.py`

**Interfaces:**
- Consumes: Task 13 approved protocol，不得改机制/schema。
- Produces: `LinuxCgroupV2Containment`；无 guest proof 的 VM 固定 unavailable。

- [ ] **Step 1:** 写 leader 0/nonzero/timeout × double-fork/setsid/setpgid/closed-stdio、census/protected digest/macOS unavailable tests。
- [ ] **Step 2:** 运行 `cargo test --locked --manifest-path .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux-supervisor/Cargo.toml && backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_linux.py`；预期 RED。
- [ ] **Step 3:** 严格实现 Task 13，无 fallback；proof 缺失为 `E2_CONTAINMENT_CLEANUP_FAILED`。
- [ ] **Step 4:** 重跑 `cargo test --locked --manifest-path .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux-supervisor/Cargo.toml && PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_linux.py`；预期首个 exec 至 invocation-destroyed/session-destroyed transcript 连续，leaf rmdir proof 在下一个 invocation 前成立，后代全清。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_linux.py && git commit -m "feat: contain replay with cgroup v2"`

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
- Produces: canonical E1/E2 artifact schemas、collectors/dispatch、`RegistrationBatchPreflight.validate(root, product_handoff, policy_handoff, expected_policy_commit, candidates) -> ValidatedRegistrationBatchV1`，以及 `AtomicBatchRegistrar.commit(validated_batch)`；generic CLI `collect`/`register`/`register-batch`。`register-batch` 在同一进程构造 typed preflight，批量验证 handoffs/commit/source/dependencies/profile/verifier/subject/policy 与全部 artifact canonical bytes/receipt bindings，成功后才一次原子更新 catalog 七 pointers；任一候选失败时 catalog/pointers 写入数为零。collect result JSON/capture/`validate-many` 合同保持不变。

- [ ] **Step 1:** 保留 E1/E2 collect/capture/register RED tests；新增 batch preflight RED：product/policy handoff、fixed commit、source/container/proof/dependency/network-proof ref、profile/verifier/subject、replay-policy binding、artifact canonical bytes/receipt semantic binding 任一不符，或七候选缺失/重复/多余时，`preflightSuccess=0`、catalog/pointer writes=0；全部正确时返回深不可变、固定顺序 batch，register-batch 恰一次 atomic catalog commit。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_collection_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_collect_result_capture.py .scratch/unified-remix-canvas-generation/acceptance/test_registration_batch_preflight.py`；预期 façade/result codec/preflight RED。
- [ ] **Step 3:** 实现 collectors/result codec；实现 typed batch preflight，必须从验证对象重算所有语义而非信任 CLI 自报 digest。`AtomicBatchRegistrar` 在同一 process/lock 下 recheck operation snapshot 与 validated batch digest，以 temp catalog+fsync+atomic replace 一次提交七 pointers；不能逐项产生可见中间状态。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_collection_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_evidence_bridge_cli.py .scratch/unified-remix-canvas-generation/acceptance/test_collect_result_capture.py .scratch/unified-remix-canvas-generation/acceptance/test_registration_batch_preflight.py`；预期 collect CAS write 0/1、capture 精确返回 ref、任一 batch candidate 错误均零 catalog 写、完整 batch 只出现全七或全零。
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
- Produces: `evaluate_operation`、`AtomicContractProjector.commit(expected_operation_snapshot, overrides)`。

- [ ] **Step 1:** 写全操作 barrier、validate read-only、operation-snapshot CAS、稳定 replay-policy binding、fsync/replace、register 不晋升 tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_workflow_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_acceptance_contract.py`；预期 RED。
- [ ] **Step 3:** 阶段 A 构造一次 `AcceptanceOperationSnapshotV1` 并 preflight，阶段 B 只把其绑定的 `ReplayPolicyBindingV1` 传给 replay；最终只重算/recheck operation snapshot 后唯一投影。catalog 的预期 register 变化产生新 operation snapshot，但不改变既有 receipt 的 replay binding。
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
- Produces: 独立安全审核后可供 Ticket 23 使用的 build record 中 `artifactRef`、`platformIdentityDigest`、`supervisorDigest`，独立规范 `out/platform-identity.json`，以及 `build_e2_toolchain_fixture.py materialize --artifact-ref <ref> --output-root <empty-root> --offline`；恶意变体只在临时 fixture 根生成，绝不注册。

- [ ] **Step 1:** 写 archive→`E2_TOOLCHAIN_ARCHIVE_INVALID`、uv/advisory→`E2_TOOLCHAIN_CLOSURE_INVALID`、ABI→`E2_TOOLCHAIN_PLATFORM_MISMATCH`、PATH marker→`E2_HOST_TOOL_FALLBACK`。
- [ ] **Step 2:** 在登记 Linux executor 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_toolchain_platform.py`；预期真实包 RED。
- [ ] **Step 3:** `input-manifest.json` 按逻辑名列出每个本地输入路径、expected SHA-256、mode、target member，`PROVENANCE.json` 固定来源 URL/release/许可证/审核者/取得方式；builder 先完整验证 manifest/provenance 和 Task 14 supervisor binary/Cargo.lock、security snapshot、platform measurement，任一外部 bytes 缺失或不匹配立即停止，代码中不得有 HTTP client/download fallback。精确运行 `python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py --input-manifest .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/input-manifest.json --provenance .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/PROVENANCE.json --output .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/toolchain.tar.zst --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --platform-identity .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/platform-identity.json --content-store-root .scratch/unified-remix-canvas-generation/acceptance/trust/toolchains --offline`，只生成唯一 canonical positive artifact、完全相同 bytes 的 content-store object、build record 与独立 platform identity；record 精确绑定后二者 digest，各负向变体只从它在临时根逐项突变。
- [ ] **Step 4:** 运行 `sha256sum .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/toolchain.tar.zst .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/platform-identity.json`，并与 build record 的 artifact/platform identity digests、content-store ref 完全一致；另在两个空 output roots 运行 `materialize`，要求展开树 byte/mode digest 一致且拒绝非空 root。重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_toolchain_platform.py`；预期正负例 GREEN 并记录真实 digest。
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
- Generated external transfer/seal: `$TRUSTED_HANDOFF_STORE_ROOT/{transfers,seals}/ticket-23-product-source-v1.json` (not tracked)

**Interfaces:**
- Consumes: Task 10 registered-boundary builder CLI、Task 11 inspector contract 与产品实际源码；不消费 fixture digest。
- Produces: Task 22 内部专用的真实 linked worktree 与 clean subject commit；外部 durable store 中 `ticket-23-product-source-v1` handoff，record kind=`product-source`、producerTask=`22`、consumerTasks=`[23,25,27,28]`，并绑定 product-source container/build-record/raw-inspection-proof/boundary-topology/tracked-checkout/control-metadata/source 七个 digest；四 pytest node IDs、结构两项/持久化五项/API 两项 Vitest fullName、两个 lock digests。linked worktree path、store root 与 owning handles 均不进入 record 或跨 Task 作为数据传递。

- [ ] **Step 1:** 编排器先注入并验证唯一绝对 `$TRUSTED_HANDOFF_STORE_ROOT`，运行 `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py verify-root --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --project-id ai-video-reverse-engineer --producer-task 22`；再由受控 provisioner 建立真实 linked worktree，并仅在本 Task shell 定义 `TASK22_PRODUCT_ROOT/GITDIR_ROOT/COMMONDIR_ROOT`。核对三根、`.git` regular file、clean 状态及不得登记 common parent/`/tmp`；阅读产品源码和测试，只补缺失结构 tests 与 wheel-only lock。
- [ ] **Step 2:** 写两个专用结构测试；以测试文件缺失为 RED 证据，不在依赖物化前运行产品测试或使用宿主临时安装。
- [ ] **Step 3:** 在 `(cd "$TASK22_PRODUCT_ROOT" && ...)` 中逐项静态核对 requirements lock 仅含 exact pin+SHA-256 wheel、package-lock v3 与两个专用测试名；不把静态核对冒充行为 GREEN。
- [ ] **Step 4:** 在产品 worktree 运行 `(cd "$TASK22_PRODUCT_ROOT" && git add backend/requirements.lock frontend/src/CreativeCanvasTicket23Acceptance.test.tsx && git commit -m "test: prepare ticket 23 trusted replay subject" && test -z "$(git status --porcelain)")`，随后仅在本 Task shell 定义 `TASK22_PRODUCT_COMMIT="$(git -C "$TASK22_PRODUCT_ROOT" rev-parse HEAD)"` 并校验 40 位 SHA；实际行为 RED/GREEN 固定由 Task 23 在产品专属依赖物化后执行。
- [ ] **Step 5:** 定义本 Task 空根 `TASK22_VERIFY_CACHE="$(mktemp -d)"`，只运行一次 `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py build --checkout-root "$TASK22_PRODUCT_ROOT" --gitdir-root "$TASK22_GITDIR_ROOT" --commondir-root "$TASK22_COMMONDIR_ROOT" --subject-commit "$TASK22_PRODUCT_COMMIT" --handoff-store-root "$TRUSTED_HANDOFF_STORE_ROOT" --handoff-id ticket-23-product-source-v1 --producer-task 22 --consumer-task 23 --consumer-task 25 --consumer-task 27 --consumer-task 28 --offline`。同一 source-runner 进程严格执行 raw inspect→canonical build→proof finalize→authenticated publish，等待 sync seal 后才关闭 handles；再以完整路径的 `trusted_handoff_store.py fetch/read` 向该 cache 重验 pointer、full-record digest、七 bindings、proof/container/build record 与 subject。禁止相对 store、boundary manifest、跨进程 FD、shell export 或临时 path 交接。

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

**Interfaces:**
- Consumes: `ticket-23-product-source-v1` handoff 中经过摘要验证的 product container/build record/subject/source，Task 11 sanitized checkout contract，产品内 `backend/requirements.lock` 与 `frontend/package-lock.json` 两个有界稳定输入，以及 Task 18 的 toolchain build record/artifact ref/platform identity；不得读取 Task 22 worktree 或复用 fixture digest。
- Produces: 两份 dependency containers/records；`Ticket23ValidationPlanV1.from_fixed_identities()` 唯一枚举两个 uv、一个 npm、四 pytest 与九 Vitest argv/cwd；`run_ticket23_dependency_validation.py` 先 Task 7 platform compare，再把 plan 全部 invocation 交给 Task 14 containment 的无接口 network namespace与 DNS/socket census，终态闭合后才生成 `network-proof.json`。Task 27/28 绑定 records/proof，不复用临时路径。

- [ ] **Step 1:** 写 RED：handoff/proof/container/build-record、两个 bounded locks、Task 18 build record/ref/platform identity、input/provenance/blob、双空根确定性任一不符均失败。validation tests 断言 platform mismatch 在 uv/npm/test spawn=0 时阻塞；成功路径的两个 uv setup、npm ci、四 pytest、九 Vitest 全部拥有 containment transcript、空 network namespace、DNS/socket census=0；缺 bytes、错误 lock、toolchain/platform/materialize/setup/test 失败路径同样零 live-network event、完成 terminate→empty→destroy。`--offline` 缺失也拒绝，但不能作为网络证明。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_dependency_repositories.py`，预期 builder/output 缺失而 RED。测试和 builder 均先 authenticated fetch，再由 `HandoffRegistry.read` 重验 pointer/record/proof/container/build record；经 Task 11 inspector 物化 subject，仅在 sanitized checkout 内读取两 locks。
- [ ] **Step 3:** `input-manifest.json` 逐项固定两个实际 lock digest、每个 wheel/tgz 本地预审路径、SHA-256/SRI、package ID、mapping、install-tree 与 derived-entrypoint digest；`PROVENANCE.json` 逐 blob 固定 upstream URL/release/license/auditor/acquisition。从 Task 18 build record 取得并校验 task-local `TASK23_TOOLCHAIN_REF=sha256:<64hex>`，同时验证 `out/platform-identity.json` digest，再精确执行：

```bash
TASK23_TOOLCHAIN_REF="sha256:<Task-18-build-record-artifactRef>"
TASK23_HANDOFF_CACHE="$(mktemp -d)"
TASK23_SANITIZED_CHECKOUT="$(mktemp -d)"
TASK23_DEP_REPRO_ROOT_A="$(mktemp -d)"
TASK23_DEP_REPRO_ROOT_B="$(mktemp -d)"
TASK23_TOOLCHAIN_ROOT="$(mktemp -d)"
TASK23_PYTHON_VIEW_ROOT="$(mktemp -d)"
TASK23_NPM_VIEW_ROOT="$(mktemp -d)"
TASK23_NPM_COW_UPPER="$(mktemp -d)"
TASK23_UV_CACHE_1="$(mktemp -d)"
TASK23_UV_CACHE_2="$(mktemp -d)"
TASK23_REPORT_ROOT="$(mktemp -d)"
TASK23_PLATFORM_MEASUREMENT="$(mktemp -d)/platform-measurement.json"
for root in "$TASK23_HANDOFF_CACHE" "$TASK23_SANITIZED_CHECKOUT" "$TASK23_DEP_REPRO_ROOT_A" "$TASK23_DEP_REPRO_ROOT_B" "$TASK23_TOOLCHAIN_ROOT" "$TASK23_PYTHON_VIEW_ROOT" "$TASK23_NPM_VIEW_ROOT" "$TASK23_NPM_COW_UPPER" "$TASK23_UV_CACHE_1" "$TASK23_UV_CACHE_2" "$TASK23_REPORT_ROOT"; do
  test -d "$root" && test -z "$(find "$root" -mindepth 1 -print -quit)"
done
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py fetch \
  --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --handoff-id ticket-23-product-source-v1 \
  --expected-kind product-source --expected-producer-task 22 --consumer-task 23 --cache-root "$TASK23_HANDOFF_CACHE"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize \
  --artifact-ref "$TASK23_TOOLCHAIN_REF" --output-root "$TASK23_TOOLCHAIN_ROOT" --offline
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_ticket23_dependency_repositories.py build \
  --product-handoff-cache-root "$TASK23_HANDOFF_CACHE" \
  --product-handoff-id ticket-23-product-source-v1 --consumer-task 23 \
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

builder 必须在两个空根分别构建 containers/views，逐 byte/digest 比较后才发布；两个 records 都绑定 product subject/source/container/build-record/proof、两 lock、toolchain artifact/build-record/platform identity 及各自 input/provenance/container/view/install/entrypoint digests。`materialize` 从 published refs 重验；COW/cache 均是当次空根。
- [ ] **Step 4:** 运行单一 validation runner；它在任何真实 executor spawn 前调用 `measure_platform`/`compare_platform_identity` 并写 task-local measurement，随后将以下固定 invocation specs 交给 `LinuxContainmentSupervisor`，每个 leaf 都使用无接口 network namespace、继承式 seccomp DNS/socket census 和完整 destroy protocol：backend cwd=`$TASK23_SANITIZED_CHECKOUT`，依次运行 uv venv、uv pip sync、四个 pytest node IDs；frontend cwd=`$TASK23_SANITIZED_CHECKOUT/frontend`，依次运行 npm ci、九个 `node node_modules/vitest/vitest.mjs run src/... -t <exact-fullName>`。精确命令：

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
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_e1_sources.py`

**Interfaces:**
- Consumes: Tasks 10–11、从 `$TRUSTED_HANDOFF_STORE_ROOT` authenticated fetch 的 `ticket-23-product-source-v1`（producer=22、consumer=25）中 product container/build-record/raw-inspection-proof/boundary/source digests；Toonflow fixed commit/MIT license。
- Produces: 两个 E1 refs、Toonflow/license 与产品实现/测试/lock blob mappings。

- [ ] **Step 1:** 写 RED：Toonflow bundle 必含 fixed commit/objects/MIT；product raw Git container 必先通过 Task 11 inspector，E1 映射只绑定 `VerifiedProductRepositorySourceV1` 的 Task 22 commit 与逐 blob digests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_e1_sources.py`；预期真实 inputs RED。
- [ ] **Step 3:** Toonflow 用包内 Git bundle verify/cat-file；产品先以 `fetch --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --cache-root "$TASK25_HANDOFF_CACHE" ...` 取得认证闭包，再以 `read --cache-root "$TASK25_HANDOFF_CACHE" --expected-kind product-source --expected-producer-task 22 --consumer-task 25` 重验，逐项匹配 proof/build-record/boundary/subject/tracked/control/source/container，经 Task 11 inspector 后导入正式 store。`TASK25_HANDOFF_CACHE` 是本 Task 空根；禁止相对/跨 Task cache。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_e1_sources.py`；预期 commit/license/all blobs 闭合。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trust/{git,product-sources,e1-ticket23-sources.json} .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_e1_sources.py && git commit -m "build: freeze ticket 23 E1 sources"`

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
- Generated external transfer/seal: `$TRUSTED_HANDOFF_STORE_ROOT/{transfers,seals}/ticket-23-policy-v1.json` (not tracked)

**Interfaces:**
- Consumes: Tasks 18–26 的 toolchain/platform/supervisor/source/dependency/E1/E4/public-policy digests；authenticated fetch product handoff（producer=22、consumer=27），并核对两 dependency records 绑定同一 product source/proof/locks/toolchain/platform 及 network-proof。
- Produces: fixed ref；`publish_ticket23_policy_handoff.py` 发布 kind=`policy`、producerTask=`27`、consumerTasks=`[28,29]` 的 handoff，精确绑定 profile/registry/revocation/product-source+raw-proof/Python+npm dependency/toolchain/platform/supervisor/network-proof/reviewer-policy digests；E1/E2/E4 verifier、四 suites、C01–C03 criteria 与 v1 revocation。

- [ ] **Step 1:** 写 profile RED 及 publish RED：fixed ref 已被不同 commit 占用、policy pointer 已被不同 record 占用、缺 store auth/sync seal、任一精确 binding（含 raw proof/network proof）不符均零 publish；相同 ref/record 幂等通过。保留 only-C01–C03、四 pytest、九 Vitest、same source/locks/toolchain/platform、E1+E2+E4 minimum tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_policy_handoff.py`；预期 profile/publisher 缺失而 RED。
- [ ] **Step 3:** 写全部真实 digest，禁止占位/fixture digest；撤销 v1 pointer/artifact，不改 receipt。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_policy_handoff.py && git diff --exit-code HEAD -- .scratch/unified-remix-canvas-generation/acceptance/matrix-contract.json`；预期 GREEN。publisher 必须从已验证 files/records 构造 fixed bindings，不能接收调用方自报 digest。
- [ ] **Step 5:** 先执行 `git add .scratch/unified-remix-canvas-generation/acceptance/{profiles/ticket-23.json,trusted-registry.json,trust/revocations.json,tools/publish_ticket23_policy_handoff.py,test_ticket23_trusted_profile_v2.py,test_ticket23_policy_handoff.py} && git commit -m "policy: freeze ticket 23 trusted evidence profile"`，再在 `set -euo pipefail` 中精确执行：

```bash
TASK27_POLICY_COMMIT="$(git rev-parse HEAD)"
TASK27_VERIFY_CACHE="$(mktemp -d)"
if ! git update-ref refs/tags/trusted-acceptance/ticket-23-policy-v1 "$TASK27_POLICY_COMMIT" 0000000000000000000000000000000000000000; then
  test "$(git rev-parse refs/tags/trusted-acceptance/ticket-23-policy-v1)" = "$TASK27_POLICY_COMMIT"
fi
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/publish_ticket23_policy_handoff.py \
  --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --handoff-id ticket-23-policy-v1 --policy-commit "$TASK27_POLICY_COMMIT" \
  --profile .scratch/unified-remix-canvas-generation/acceptance/profiles/ticket-23.json \
  --registry .scratch/unified-remix-canvas-generation/acceptance/trusted-registry.json \
  --revocations .scratch/unified-remix-canvas-generation/acceptance/trust/revocations.json \
  --product-handoff-id ticket-23-product-source-v1 \
  --python-build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/python-build-record.json \
  --npm-build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/npm-build-record.json \
  --network-proof .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/network-proof.json \
  --toolchain-build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json \
  --platform-identity .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/platform-identity.json \
  --reviewer-policy .scratch/unified-remix-canvas-generation/acceptance/trust/reviewer-policy.json \
  --producer-task 27 --consumer-task 28 --consumer-task 29
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py fetch --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --handoff-id ticket-23-policy-v1 --expected-kind policy --expected-producer-task 27 --consumer-task 28 --cache-root "$TASK27_VERIFY_CACHE"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read --cache-root "$TASK27_VERIFY_CACHE" --handoff-id ticket-23-policy-v1 --expected-kind policy --expected-producer-task 27 --consumer-task 28 --field producerCommit | grep -Fx "$TASK27_POLICY_COMMIT"
```

`TASK27_VERIFY_CACHE` 为本 Task 空根；publish 必须等待认证 sync seal。ref/pointer 已存在但不同即停止，禁止 shell export、当前 branch 或相对 store 交接。

### Task 28: 从固定 policy commit collect/register

**Files:**
- Create generated immutable objects: `.scratch/unified-remix-canvas-generation/acceptance/evidence/sha256/<aa>/<digest>`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/evidence-catalog.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/audit/ticket-23-trusted-evidence.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_collect_register.py`

**Interfaces:**
- Consumes: 从 `$TRUSTED_HANDOFF_STORE_ROOT` authenticated fetch 的 product/policy handoffs、fixed policy ref、两 dependency refs/network proof 与稳定 replay binding；每次 collect 构造短生命周期 snapshot。E2 replay 每 leaf 新空 cache，不读取 Task 22/23 临时根。
- Produces: E1 两个、E2 四个、E4 一个 artifacts/pointers；contract 不变。

- [ ] **Step 1:** 保留 wrong-policy/snapshot-race/missing-E4/source-dependency/handoff/ref tests 与 sequential historical regression；新增 Ticket 23 batch test：一次 typed preflight 必须验证两个 authenticated handoffs、fixed commit、source/container/raw proof、dependency records/network proof、profile/verifier/subject/policy bindings 与七 artifacts 的 canonical bytes/receipt semantics。任一空值、污染、错 ref/subject/binding/E4 时第一次 catalog write 前失败；成功只允许全七原子出现。
- [ ] **Step 2:** Steps 2–4 必须在同一个 `set -euo pipefail` harness 中运行，任何 command substitution/parser/签名/CAS 校验失败立即退出且在 register spy 中保持零调用。在登记 Linux executor 先重载两个 handoffs，并只把已验证字段保存为本 Task 变量：

```bash
set -euo pipefail
TASK28_POLICY_HANDOFF_CACHE="$(mktemp -d)"
TASK28_PRODUCT_HANDOFF_CACHE="$(mktemp -d)"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py fetch --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --handoff-id ticket-23-policy-v1 --expected-kind policy --expected-producer-task 27 --consumer-task 28 --cache-root "$TASK28_POLICY_HANDOFF_CACHE"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py fetch --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --handoff-id ticket-23-product-source-v1 --expected-kind product-source --expected-producer-task 22 --consumer-task 28 --cache-root "$TASK28_PRODUCT_HANDOFF_CACHE"
TASK28_POLICY_COMMIT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read --cache-root "$TASK28_POLICY_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 --expected-kind policy --expected-producer-task 27 --consumer-task 28 --field producerCommit)"
TASK28_PRODUCT_COMMIT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read --cache-root "$TASK28_PRODUCT_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 --expected-kind product-source --expected-producer-task 22 --consumer-task 28 --field producerCommit)"
if ! git update-ref refs/tags/trusted-acceptance/ticket-23-policy-v1 "$TASK28_POLICY_COMMIT" 0000000000000000000000000000000000000000; then
  test "$(git rev-parse refs/tags/trusted-acceptance/ticket-23-policy-v1)" = "$TASK28_POLICY_COMMIT"
fi
test "$(git rev-parse refs/tags/trusted-acceptance/ticket-23-policy-v1)" = "$TASK28_POLICY_COMMIT"
test "$(git rev-parse HEAD)" = "$TASK28_POLICY_COMMIT"
test -z "$(git status --porcelain)"
```

两个 `read` 必须重算 pointer、record、所引 artifacts 与 bindings，输出只能是已验证的 40 位 commit；再核对 profile 的 subject/source/toolchain/platform/dependency/registry/revocation digests 与两个 handoff records 完全一致。随后执行六次 collect，先保存完整 stdout，再由 capture 工具按 operation/profile/verifier/subject 解析；此时禁止 register：

```bash
E1_TOONFLOW_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e1.ticket23.toonflow-source.v1 --subject "git://product@$TASK28_PRODUCT_COMMIT")"
E1_TOONFLOW_ARTIFACT="$(printf '%s' "$E1_TOONFLOW_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect --profile ticket-23/v1 --verifier e1.ticket23.toonflow-source.v1 --subject "git://product@$TASK28_PRODUCT_COMMIT")"
E1_PRODUCT_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e1.ticket23.product-adaptation.v1 --subject "git://product@$TASK28_PRODUCT_COMMIT")"
E1_PRODUCT_ARTIFACT="$(printf '%s' "$E1_PRODUCT_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect --profile ticket-23/v1 --verifier e1.ticket23.product-adaptation.v1 --subject "git://product@$TASK28_PRODUCT_COMMIT")"
E2_BACKEND_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.backend-persistence.v2 --subject "git://product@$TASK28_PRODUCT_COMMIT")"
E2_BACKEND_ARTIFACT="$(printf '%s' "$E2_BACKEND_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect --profile ticket-23/v1 --verifier e2.ticket23.backend-persistence.v2 --subject "git://product@$TASK28_PRODUCT_COMMIT")"
E2_FRONTEND_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.frontend-persistence.v2 --subject "git://product@$TASK28_PRODUCT_COMMIT")"
E2_FRONTEND_ARTIFACT="$(printf '%s' "$E2_FRONTEND_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect --profile ticket-23/v1 --verifier e2.ticket23.frontend-persistence.v2 --subject "git://product@$TASK28_PRODUCT_COMMIT")"
E2_API_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.api.v2 --subject "git://product@$TASK28_PRODUCT_COMMIT")"
E2_API_ARTIFACT="$(printf '%s' "$E2_API_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect --profile ticket-23/v1 --verifier e2.ticket23.api.v2 --subject "git://product@$TASK28_PRODUCT_COMMIT")"
E2_STRUCTURE_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.canvas-structure.v2 --subject "git://product@$TASK28_PRODUCT_COMMIT")"
E2_STRUCTURE_ARTIFACT="$(printf '%s' "$E2_STRUCTURE_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect --profile ticket-23/v1 --verifier e2.ticket23.canvas-structure.v2 --subject "git://product@$TASK28_PRODUCT_COMMIT")"
```

- [ ] **Step 3:** 审核者只在仓库外使用真实私钥；在同一 fail-closed harness 中精确执行：

```bash
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py prepare-e4 --profile ticket-23/v1 --subject "git://product@$TASK28_PRODUCT_COMMIT" --review-input "$E4_REVIEW_INPUT_FILE" --output "$E4_ATTESTATION_FILE"
ssh-keygen -Y sign -n trusted-acceptance-e4 -f "$E4_PRIVATE_KEY_FILE" "$E4_ATTESTATION_FILE"
E4_REVIEW_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect-e4 --profile ticket-23/v1 --subject "git://product@$TASK28_PRODUCT_COMMIT" --attestation "$E4_ATTESTATION_FILE" --signature "$E4_ATTESTATION_FILE.sig" --attachment "$E4_OPERATION_RECORD_FILE")"
E4_REVIEW_ARTIFACT="$(printf '%s' "$E4_REVIEW_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect-e4 --profile ticket-23/v1 --verifier e4.ticket23.canvas-review.v1 --subject "git://product@$TASK28_PRODUCT_COMMIT")"
```

缺真实签名或 parser 失败即停止，禁止测试 key 替代，仍不得 register。
- [ ] **Step 4:** 先运行 `capture_collect_result.py validate-many` 重算七个 CAS refs；随后只调用一次 `register-batch`。该进程重新 authenticated fetch 两 handoffs并构造 `ValidatedRegistrationBatchV1`，完成全部语义预检、snapshot recheck 后才允许唯一 catalog commit：

```bash
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py validate-many --cas-root .scratch/unified-remix-canvas-generation/acceptance/evidence "$E1_TOONFLOW_ARTIFACT" "$E1_PRODUCT_ARTIFACT" "$E2_BACKEND_ARTIFACT" "$E2_FRONTEND_ARTIFACT" "$E2_API_ARTIFACT" "$E2_STRUCTURE_ARTIFACT" "$E4_REVIEW_ARTIFACT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py register-batch \
  --profile ticket-23/v1 --subject "git://product@$TASK28_PRODUCT_COMMIT" --expected-policy-commit "$TASK28_POLICY_COMMIT" \
  --handoff-store-root "$TRUSTED_HANDOFF_STORE_ROOT" --product-handoff-id ticket-23-product-source-v1 --policy-handoff-id ticket-23-policy-v1 \
  --candidate "e1.ticket23.toonflow-source.v1=$E1_TOONFLOW_ARTIFACT" \
  --candidate "e1.ticket23.product-adaptation.v1=$E1_PRODUCT_ARTIFACT" \
  --candidate "e2.ticket23.backend-persistence.v2=$E2_BACKEND_ARTIFACT" \
  --candidate "e2.ticket23.frontend-persistence.v2=$E2_FRONTEND_ARTIFACT" \
  --candidate "e2.ticket23.api.v2=$E2_API_ARTIFACT" \
  --candidate "e2.ticket23.canvas-structure.v2=$E2_STRUCTURE_ARTIFACT" \
  --candidate "e4.ticket23.canvas-review.v1=$E4_REVIEW_ARTIFACT"
```

预期单次输出“七项已原子登记，不代表通过”；任何 preflight 候选错误时 catalog digest 完全不变。audit 记录 typed batch digest、operation snapshot、同一 replay binding、两个 handoff record digests、subject/toolchain/platform/dependency/network-proof/source/proof/supervisor/fingerprint 与七 pointers；最终 catalog 重验七 receipts，contract 前后相同。
- [ ] **Step 5:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_collect_register.py -k 'batch_preflight or sequential_collect_register or wrong_policy or snapshot_race or source_dependency'`，再运行完整文件与 contract zero-diff；预期任一 batch 错误零 catalog 写、完整 batch final catalog 七项 GREEN。最后执行 `git add .scratch/unified-remix-canvas-generation/acceptance/{evidence,evidence-catalog.json,audit/ticket-23-trusted-evidence.json,test_ticket23_collect_register.py} && git commit -m "evidence: register ticket 23 trusted replay"`。

### Task 29: 晋升 C01–C03 并验证失效重对账

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_promotion.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/matrix-contract.json`
- Modify: `.scratch/unified-remix-canvas-generation/issues/23-align-toonflow-canvas-shell-and-persistence.md`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/README.md`

**Interfaces:**
- Consumes: Task 28 七个有效 pointers、Task 17 workflow，以及从绝对 `$TRUSTED_HANDOFF_STORE_ROOT` authenticated fetch/read 的 policy handoff（producer=27、consumer=29）与 fixed policy ref；不接受相对路径、Task 28 cache、shell 变量或当前 branch 推断 identity。
- Produces: 仅 C01–C03 原子 projection/reconciliation；其他 135 项不变。

- [ ] **Step 1:** 写逐项 requirement、criteria reject/missing、revoked/tampered/unreachable、snapshot-race、deny-list tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_promotion.py`；预期尚未投影 RED。
- [ ] **Step 3:** 先执行 `fetch --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --cache-root "$TASK29_HANDOFF_CACHE" ...` 与 `read --cache-root "$TASK29_HANDOFF_CACHE" ...`；从已验证 producerCommit 用 `git update-ref <ref> <new> <zero>` zero-or-same 规则重建/验证本 executor fixed ref，再重验 profile/replay binding、network proof 与七 evidence pointers；cache 为本 Task 空根，仅全部有效才 promote/reconcile。
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

### 自包含接口、依赖拓扑与真实命令

- bootstrap 与主链：1→2→3→4→5→6→7→8→9→10→11→12→13（独立批准门）→14→15→16→17。
- 真实基础验收：17→18→19→20→21；任一未通过，不得写 production profile。
- Ticket 23：21→22 raw-layout inspect+canonical build+proof+authenticated publish→23 跨 executor fetch/sanitized checkout/双空根 dependencies+contained zero-network validation→24 E4→25 proof/container E1→26 reviewer policy→27 executable fixed-ref+policy publish→28 双 handoff fetch/collect/typed seven-candidate preflight+atomic batch register→29 authenticated fetch 后 promote。
- `ProductRepositorySourceV1`/`RawGitInspectionProofV1` 在 Task 2 定义；Task 10 同一受信命令先 inspect 原始布局再构建/闭合 proof，Task 11 正式验证 proof/container 并产出 verified handle；危险原始路径/bytes 不进入 container。
- `ReplayPolicyBindingV1` 与 `AcceptanceOperationSnapshotV1` 在 Task 2 分开构造，Tasks 5/16–17/28 只重检 operation snapshot，receipt 始终只绑定稳定 replay policy；Task 28 final-catalog 回归证明顺序 register 不使旧 receipt 失效。
- containment 七方法、消息字段、序号/摘要链、权限、binary identity 与唯一 kernel mechanism 在 Task 13 冻结；Task 14 不能重新选型。
- Task 3 是唯一 scanner owner；receipt/barrier/collect 只调用该接口，无依赖循环。
- Task 1 给出 bootstrap hashes；Tasks 18–21/23 有 exact builders/records。Task 23 在 platform compare 后把真实 uv/npm/pytest/Vitest 全部送入 Task 14 containment，无接口 network namespace+census 证明而非只依赖 `--offline`。
- 跨 Task 只允许已提交 records/CAS 与编排器验证的绝对外部 `$TRUSTED_HANDOFF_STORE_ROOT`；producer publish+认证 sync seal，consumer fetch/read+full-bytes rehash。store path/租约不入 digest，Task-local cache/mktemp 不交接；policy 另与 fixed Git ref 闭合。
- Review Focus 五项分别由 Tasks 5/17、3/20、14/21、19–20、10–11/22–23/27–29 持有，编号无漂移。
- 29 个 Task 均为单独实现会话、单独 commit、单独新审查会话；Task 13、27、28 是不可跳过的硬门。

## 执行与批准状态

- E2 v2 书面规格已批准；本修订计划等待用户复审。
- 规格审批历史只改变 `Status` 行；本轮提交必须同时通过“相对 `06827f2e` 规格零 diff”和“相对 `2f304fe` 删除第 3 行后正文零 diff”，不得笼统声称两个 blob 相同。
- 执行方式固定为 subagent-driven：逐 Task 新实现会话 + 新独立审查会话，前一 Task 通过才进入下一 Task，最后全分支独立审查。
- 本次规划会话不授权实现或启动任何 Task；复审通过后从 Task 1 开始，不恢复旧 Task 6，也不跳过 Task 13、27、28。
