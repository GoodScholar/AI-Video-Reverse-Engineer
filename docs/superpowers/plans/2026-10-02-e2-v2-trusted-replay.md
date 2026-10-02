# E2 v2 可信重放协议 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用内容寻址工具链、产品源码与依赖仓库、操作级 artifact barrier、不可逃逸 containment、确定性离线重放和 v2 receipt 取代已阻塞的 E2 v1，并装配 Ticket 23 冻结的最低 E1+E2+E4 组合。

**Architecture:** E2 仍分为阶段 A 全操作只读预检与阶段 B 可信重放：可变 catalog/contract 进入短生命周期 `AcceptanceOperationSnapshotV1`，稳定 suite/profile/toolchain/dependency/raw-Git-source/containment policy 进入 `ReplayPolicyBindingV1`，receipt 只绑定后者。runner 只消费已验证的规范 raw Git repository container，不接受宿主路径；containment 先冻结协议、消息和唯一 Linux 内核机制，再实现 backend。Ticket 23 的 E1/E4 是旧计划未执行的既有前置任务，仅为完成 C01–C03 profile 纳入，不改变 E2 v2 规格或三引擎职责。

**Tech Stack:** Python 3.13、pytest 8.3.5、pytest-json-report 1.5.0、Vitest 3.0.8、Node 24.20.0、npm 11.19.0、Git 2.53.0、uv 0.11.15、zstandard 0.23.0、规范 POSIX ustar + 单帧 zstd、Linux cgroup v2 + clone3 + seccomp user notification、Rust（锁定 Cargo.lock）、OpenSSH `ssh-keygen -Y`、现有 SHA-256 CAS 与 JSON 验收资产。

**Spec:** `docs/superpowers/specs/2026-10-02-e2-v2-trusted-replay-design.md`

## Global Constraints

- 历史计划 `docs/superpowers/plans/2026-10-02-trusted-acceptance-evidence-bridge.md` 保持不变；旧 Task 5 保持 `blocked / superseded`，旧 Task 6 未开始。
- Ticket 23 闭环 Tasks 24–29 包含旧计划 Tasks 9–12 尚未执行的 E1/E4 前置工作；不扩展 E2 v2 规格、不改变 138 项合同、四状态、零容忍规则或 E1/E2/E3/E4 职责。
- `trusted-e2-suite/v2`、`trusted-e2-receipt/v2`、`trusted-e2-toolchain-tar-zstd/v1`、`trusted-e2-dependency-repository/v1`、`trusted-product-raw-git-tar-zstd/v1` 均拒绝未知、缺失、重复字段；v1 suite/receipt 不迁移、不猜测升级。
- `validate/promote/reconcile` 的全部 E2 requirement 先通过同一阶段 A barrier；失败时 `spawnCount=checkoutCount=casWrites=contractWrites=0`。verify 永不写 CAS；collect 仅完整成功后原子写一次 receipt。
- `AcceptanceOperationSnapshotV1` 只绑定 catalog、contract 与本轮 `ReplayPolicyBindingV1` digest，用于 operation barrier/投影前 recheck；`ReplayPolicyBindingV1` 绑定 registry、suite/profile、revocation、reviewer policy、toolchain、dependency repository、raw product source 与 containment policy，明确排除 catalog、contract 和 observation time。receipt 只保存 replay binding digest。
- runner 只接收 `VerifiedProductRepositorySourceV1`；裸 `repository_root`、`cwd`、宿主绝对路径和工作区 Git 目录不能进入 replay 或 receipt。
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
- Ticket 23 四个 E2 suites 共用同一 verified product source、`ReplayPolicyBindingV1` 和精确 lock/container/toolchain/containment digests；产品专属 Python/npm containers 不得复用最小 fixture digest。形式化 E1/E2 证据只能消费 Task 11 产出的 verified source/sanitized checkout；`$TICKET23_PRODUCT_ROOT` 只用于 Task 22–23 准备和行为验证，不得直接进入 receipt。
- 先提交真实 registry/profile/revocation 与全部 digest 的固定 policy commit；下一独立 commit 才 collect/register，并提交 CAS objects、catalog pointer 与审计记录。`matrix-contract.json` 在 collect/register commit 中字节不变。
- E4 私钥始终在仓库外；仓库只保存 allowed-signers、公钥指纹、reviewer policy、规范 attestation、签名和附件摘要。缺真实 E4 或任一 E1/E2 时 C01–C03 必须阻塞。
- 每个 Task 使用新实现会话，提交后使用新独立审查会话；Critical/Important 清零且命令通过才进入下一 Task。实现前读取任务所涉现有/上游源码、版本与许可证，并记录在既有 SDD ledger。

## Review Focus

- operation snapshot、最后一个 pointer 或 policy digest 在 barrier 前后变化必须零 spawn/checkout/CAS/contract 写；Tasks 5、17 覆盖。
- 32 KiB 后才出现跨 chunk token、非法 UTF-8、未闭合 quote/JSON 或 agent socket 标点仍须读到 EOF并返回 `OUTPUT_REDACTION_UNSAFE`；Tasks 3、20 覆盖。
- leader 任意结局后 double-fork+`setsid/setpgid`+关闭 stdio 仍须处于 leaf 并被清空；Tasks 14、21 覆盖。
- uv probe cwd/env/script/cache/extra child、恶意 entry-point 越界、project/workspace/build backend、DNS/socket、derived entrypoint replacement 均须命中固定错误码且不得进入测试；Tasks 19–20 覆盖。
- Ticket 23 缺 E4、双 active pointer、产品 source/dependency digest 不符或 collect 时 contract 改变都须阻塞且不晋升；Tasks 27–29 覆盖。

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
class ProductRepositorySourceV1:
    subject_repository: str
    source_ref: str
    source_digest: str
    container_format: Literal["trusted-product-raw-git-tar-zstd/v1"]
    object_format: Literal["sha1"]
    budget: ProductSourceBudgetV1

@dataclass(frozen=True)
class VerificationOperationV1:
    operation_id: str
    kind: Literal["verify", "validate", "promote", "reconcile", "collect"]
    requirement_ids: tuple[str, ...]
    started_operation_snapshot_digest: str

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

`NamedDigest.name` 必须是 NFC、非空、无控制字符的逻辑名，`digest` 必须是小写 `sha256:<64hex>`。每个 tuple 由 builder 以 `(name.encode("utf-8"), digest.encode("ascii"))` bytewise 升序规范化；同名条目无论摘要是否相同都拒绝，不同名称可共享同一摘要。suite/profile/toolchain/source/containment 集合对 E2 binding 必须非空；dependency 集合仅在所有 suite 都声明零 ecosystem 时允许空，否则按 `<suite-id>/<ecosystem>` 唯一命名。空集合编码为 JSON `[]`，文件系统创建/遍历顺序不进入结果。

两个 `canonical_digest` 都对除自身外的字段按字段名排序、UTF-8 紧凑规范 JSON 编码后取 SHA-256。`build_replay_policy_binding(...)` 先规范化上述集合；`build_acceptance_operation_snapshot(root, binding)` 再读取 catalog/contract。任何 wall-clock、mtime、review observation time 都不得进入二者。顺序 collect/register 改变 catalog 只生成新的 operation snapshot，不改变已有 receipt 的 replay binding。

| 文件/目录 | 单一职责 |
| --- | --- |
| `trusted_evidence/e2_policy.py` | v2 suite、operation、snapshot、product-source schema 与 loader。 |
| `trusted_evidence/redaction.py` | 唯一 shared lexer、streaming redactor、安全缓冲。 |
| `trusted_evidence/bounded_io.py` / `receipts.py` | 有界稳定读取与 receipt codec。 |
| `trusted_evidence/artifact_barrier.py` | operation candidate/pointer/artifact 预检与 snapshot recheck。 |
| `trusted_evidence/canonical_archive.py` | zstd+ustar 规范、预算、no-exec 展开。 |
| `trusted_evidence/toolchain.py` / `platform_identity.py` | toolchain closure/security 与 exact platform identity。 |
| `trusted_evidence/dependency_repository.py` / `*_dependencies.py` | lock mapping 与 deterministic Python/npm consumer views。 |
| `trusted_evidence/product_source.py` / `git_subject.py` | content source loader、sanitized object view、fixed checkout。 |
| `trusted_evidence/reporters.py` | 真实 pytest/Vitest report 与 selection closure。 |
| `trusted_evidence/containment/` | 六方法协议、wire transcript、Linux supervisor/backend。 |
| `trusted_evidence/replay.py` / `collection.py` | 内存 replay 与成功后唯一 CAS receipt 写入。 |
| `trusted_evidence/evaluation.py` / `projection.py` / `runtime.py` | operation 编排与唯一 contract writer。 |
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
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/registry.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/errors.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py`

**Interfaces:**
- Consumes: 规格 4、6.1、7、9、10、14 和既有六字段 pointer。
- Produces: 上述 immutable 类型、`canonical_named_digests(kind, entries) -> tuple[NamedDigest, ...]`、`build_replay_policy_binding(...) -> ReplayPolicyBindingV1`、`build_acceptance_operation_snapshot(root, binding) -> AcceptanceOperationSnapshotV1`、`E2SuiteDefinitionV2.from_policy(...)`。

- [ ] **Step 1:** 写 `test_replay_binding_excludes_catalog_contract_and_observation_time`、`test_operation_snapshot_binds_catalog_contract_and_replay_binding`、`test_named_digest_order_is_identical_for_different_creation_order`、`test_duplicate_name_and_illegal_empty_set_reject`、`test_product_source_rejects_host_path`、`test_v1_is_not_upgraded`。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py`；预期缺类型而 RED。
- [ ] **Step 3:** 实现严格 `NamedDigest`、按集合定义的排序/重复/空语义、tuple/frozen 深不可变与两类规范 JSON digest；缺任一 policy 输入即阻塞。
- [ ] **Step 4:** 重跑同命令；预期 GREEN 且 production profile 仍为空。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{e2_policy.py,registry.py,errors.py} .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py && git commit -m "feat: define trusted replay operation policy"`

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
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/product_source.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_product_source.py`

**Interfaces:**
- Consumes: Tasks 2、4、6；规格 §8.2 的完整 raw Git metadata threat matrix。
- Produces: `build_product_source(raw_repository, manifest, output) -> ProductRepositorySourceV1`、`ProductSourceLoader.load(source, expected_source_digest) -> ValidatedProductRepositoryContainerV1`；source artifact 自身不反向绑定包含它的 replay policy，避免摘要环；尚未通过 Task 11 inspector 的 container 不能称为 verified source。

- [ ] **Step 1:** 写 container RED tests：manifest 首项；payload 只位于 `repository/` 且完整保留 raw `.git/config`、objects、refs、packed-refs、alternates、replace、grafts、shallow/promisor 与 pack sidecars 的原始 bytes/type/mode；member/path/header/parent/sort/size/count/ratio 必须规范。hardlink、special file、path escape、重复/case-fold collision、read race 由 builder 拒绝；危险 Git metadata 不得在 builder 中被删除、改写或 bundle 化，留给 Task 11 inspector 按稳定码拒绝。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_product_source.py`；预期 RED。
- [ ] **Step 3:** builder 对 raw repository 树生成 `.trusted-product-source/manifest.json` 首项和规范单帧 zstd+ustar；`source_ref` 是完整压缩 container SHA-256，manifest 另存 raw-tree digest/预算/object format/subject commit。loader 从 `trust/product-sources/sha256/<aa>/<digest>` 有界、no-follow、no-exec 展开并重算两个 digest，不接受 Git bundle、宿主 repository path 或工作区回退。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_product_source.py`；预期相同 raw tree 在两空根生成 byte-identical container/source digest，任一 archive/raw member 变化阻塞，危险 metadata fixture 仍完整交给 inspector。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/product_source.py .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_product_source.py && git commit -m "feat: bind canonical raw Git product sources"`

### Task 11: 从 verified source 构造 sanitized Git checkout

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/git_subject.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_git_subject.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e2_suite.py`

**Interfaces:**
- Consumes: Task 7 包内 Git、Task 10 `ValidatedProductRepositoryContainerV1`；不接受裸 path/cwd/bundle。
- Produces: `GitMetadataInspector.inspect(container) -> VerifiedProductRepositorySourceV1`、`SanitizedGitView.build(source, commit, toolchain_git, destination) -> FixedCheckout`。

- [ ] **Step 1:** 写 §8.2 全矩阵 tests：config/packed-refs/refs/objects 单项与聚合预算；unknown/dangerous config；alternates/http-alternates、replace、grafts、shallow、promisor；loose/pack/index/rev/bitmap/mtimes sidecars 配对；`.git` 内任意 symlink、duplicate/case-fold path、外部 object source 与 race；每项断言对应 `GIT_*` 稳定码且包内 Git spawn=0。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_git_subject.py`；预期旧 host Git path RED。
- [ ] **Step 3:** 对 Task 10 原样 raw `.git` staging 运行 bounded inspector，先完成 config/ref/object/sidecar allow/deny；通过后才以包内 Git `index-pack --strict`/`fsck --strict` 构造 sanitized bare view，仅复制固定 closure，永不把 raw config/hooks/attributes/log/index/remote metadata 带入 checkout。
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
- Produces: 精确 API、canonical supervisor schema、proof digest、已批准 `linux-cgroup-v2-seccomp-notify/v1` gate。

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
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/e1_collection.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e2_suite.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/registration.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_collection_v2.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_evidence_bridge_cli.py`

**Interfaces:**
- Consumes: Tasks 2–5、15，既有 E1 Git verifier、CAS/registration。
- Produces: canonical `trusted-e1-git-receipt/v1` 与 `trusted-e2-receipt/v2` artifact schemas；`E1Collector.collect(...)`、`E2Collector.collect(...)`；`collect_artifact(registry, profile, verifier, subject)` 按 verifier implementation type 唯一 dispatch；generic CLI `collect`/`register`，后续 Task 24 只追加 E4 subcommands。

- [ ] **Step 1:** 写 E1 schema/source/license/blob mapping、E2 replay-binding/expected-only/proof schema、unknown implementation dispatch、verify=0 CAS、失败=0 CAS、成功 collect=1 tests；CLI regression 必须在临时 raw Git source 上完成一次 E1 collect→register，并用 stubbed trusted replay 完成一次 E2 collect→register，均核对六字段 pointer 和“已登记，不代表通过”。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_collection_v2.py`；预期 façade RED。
- [ ] **Step 3:** E1 collector 只从 verified raw Git source 生成规范 receipt；E2 collector 内存构造→唯一 scanner→operation snapshot recheck→唯一 CAS put；generic CLI 加载 registry 后 dispatch，collect 不写 catalog/contract，register 只追加 pointer。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_collection_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_evidence_bridge_cli.py`；预期两类真实 CLI 路径 GREEN，CAS write spy 精确为 0/1。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{artifact_schemas.py,e1_collection.py,collection.py,registration.py,verifiers/e2_suite.py} .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_collection_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_evidence_bridge_cli.py && git commit -m "feat: collect and register canonical E1 E2 artifacts"`

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
- Create generated artifact: `.scratch/unified-remix-canvas-generation/acceptance/trust/toolchains/sha256/<aa>/<digest>`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_toolchain_platform.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/README.md`

**Interfaces:**
- Consumes: Tasks 1、6–7、14、17。
- Produces: 独立安全审核后可供 Ticket 23 使用的 `TICKET23_TOOLCHAIN_REF`、`TICKET23_PLATFORM_DIGEST`、`TICKET23_SUPERVISOR_DIGEST`；恶意变体只在临时 fixture 根生成，绝不注册。

- [ ] **Step 1:** 写 archive→`E2_TOOLCHAIN_ARCHIVE_INVALID`、uv/advisory→`E2_TOOLCHAIN_CLOSURE_INVALID`、ABI→`E2_TOOLCHAIN_PLATFORM_MISMATCH`、PATH marker→`E2_HOST_TOOL_FALLBACK`。
- [ ] **Step 2:** 在登记 Linux executor 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_toolchain_platform.py`；预期真实包 RED。
- [ ] **Step 3:** `input-manifest.json` 按逻辑名列出每个本地输入路径、expected SHA-256、mode、target member，`PROVENANCE.json` 固定来源 URL/release/许可证/审核者/取得方式；builder 先完整验证 manifest/provenance 和 Task 14 supervisor binary/Cargo.lock、security snapshot、platform measurement，任一外部 bytes 缺失或不匹配立即停止，代码中不得有 HTTP client/download fallback。精确运行 `python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py --input-manifest .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/input-manifest.json --provenance .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/PROVENANCE.json --output .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/toolchain.tar.zst --content-store-root .scratch/unified-remix-canvas-generation/acceptance/trust/toolchains --offline`，只生成唯一 canonical positive artifact、完全相同 bytes 的 content-store object 与 `out/build-record.json`；各负向变体只从它在临时根逐项突变。
- [ ] **Step 4:** 运行 `sha256sum .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/toolchain.tar.zst`，并与 build record、content-store ref 完全一致；重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_toolchain_platform.py`；预期正负例 GREEN 并记录真实 digest。
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

**Interfaces:**
- Consumes: 产品实际源码，不消费 fixture digest。
- Produces: 绝对路径、专用且隔离的 `$TICKET23_PRODUCT_ROOT`，clean `TICKET23_PRODUCT_COMMIT`、四 pytest node IDs、结构两项/持久化五项/API 两项 Vitest fullName、两个 lock digests；该 worktree 仅用于准备与行为验证，不能作为形式化 E1/E2 source。

- [ ] **Step 1:** 先建立/解析专用 worktree 的绝对路径并 `export TICKET23_PRODUCT_ROOT=/absolute/path/to/ticket23-product-worktree`；运行 `test "${TICKET23_PRODUCT_ROOT#/}" != "$TICKET23_PRODUCT_ROOT" && test -d "$TICKET23_PRODUCT_ROOT/.git" && test -z "$(git -C "$TICKET23_PRODUCT_ROOT" status --porcelain)"`，要求目标唯一且初始 clean。随后显式 `cd "$TICKET23_PRODUCT_ROOT"` 读取产品实现与三个现有测试，核对真实数据流/许可/测试身份，只补缺失结构 tests 和 wheel-only lock；记录四个 pytest node IDs 与九个 Vitest fullName。
- [ ] **Step 2:** 写两个专用结构测试；以测试文件缺失为 RED 证据，不在依赖物化前运行产品测试或使用宿主临时安装。
- [ ] **Step 3:** 在 `(cd "$TICKET23_PRODUCT_ROOT" && ...)` 中逐项静态核对 requirements lock 仅含 exact pin+SHA-256 wheel、package-lock v3 与两个专用测试名；不把静态核对冒充行为 GREEN。
- [ ] **Step 4:** 在产品 worktree 运行 `(cd "$TICKET23_PRODUCT_ROOT" && git add backend/requirements.lock frontend/src/CreativeCanvasTicket23Acceptance.test.tsx && git commit -m "test: prepare ticket 23 trusted replay subject" && test -z "$(git status --porcelain)" && git rev-parse HEAD)`；预期 clean worktree 与 40 位 SHA。实际行为 RED/GREEN 固定由 Task 23 在产品专属依赖物化后执行。
- [ ] **Step 5:** 设置仓库外绝对空目录 `$TICKET23_PRODUCT_STAGING`，用 Task 10 builder 和包内 Git 从 `$TICKET23_PRODUCT_ROOT` 构造 canonical raw Git repository container：`python .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py --repository-root "$TICKET23_PRODUCT_ROOT" --subject-commit "$TICKET23_PRODUCT_COMMIT" --output-root "$TICKET23_PRODUCT_STAGING" --offline`；计算 commit/container/two-lock digests。Task 25 必须先经 Task 11 inspector 生成 `VerifiedProductRepositorySourceV1`/sanitized checkout，才把完全相同的 container bytes 导入正式 content store；原 worktree path、staging path 及未验证 container 均不得进入形式化证据。

### Task 23: 构建 Ticket 23 产品专属 dependency containers

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trust/dependencies/ticket-23/manifest.json`
- Create generated blobs: `.scratch/unified-remix-canvas-generation/acceptance/trust/dependencies/sha256/<aa>/<digest>`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_dependency_repositories.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/README.md`

**Interfaces:**
- Consumes: Task 22 commit/locks、Tasks 8–9、18–22；不得复用 fixture digest。
- Produces: 产品专属 Python/npm refs、真实 wheel/tgz、mapping/view/install-tree/native-closure digests。

- [ ] **Step 1:** 写 RED：两个 locks 无唯一 mapping 时 `E2_DEPENDENCY_REPOSITORY_INVALID`。
- [ ] **Step 2:** 重新验证 `$TICKET23_PRODUCT_ROOT` 为绝对专用 clean worktree；运行 `(cd "$TICKET23_PRODUCT_ROOT" && sha256sum backend/requirements.lock frontend/package-lock.json > /tmp/t23-locks.before)`；仅从预审 blobs 构建 containers，禁止网络/宿主临时安装。
- [ ] **Step 3:** 明确 RED/GREEN：将 Task 18 验证输出赋给 `TICKET23_TOOLCHAIN_ROOT`，将本 Task 从 Task 22 locks 与预审 blobs 新建的两个产品专属 view 赋给 `TICKET23_PYTHON_VIEW`、`TICKET23_NPM_VIEW`；在 `(cd "$TICKET23_PRODUCT_ROOT" && ...)` 中依次运行 `"$TICKET23_TOOLCHAIN_ROOT/bin/uv" venv backend/.venv --python="$TICKET23_TOOLCHAIN_ROOT/bin/python" --no-project --no-config --no-python-downloads --cache-dir="$TICKET23_UV_CACHE_1"`、`"$TICKET23_TOOLCHAIN_ROOT/bin/uv" pip sync --python=backend/.venv/bin/python backend/requirements.lock --offline --no-index --find-links="$TICKET23_PYTHON_VIEW" --no-build --require-hashes --no-sources --config-file="$TICKET23_TOOLCHAIN_ROOT/config/uv-empty.toml" --no-python-downloads --cache-dir="$TICKET23_UV_CACHE_2"`、`"$TICKET23_TOOLCHAIN_ROOT/bin/node" "$TICKET23_TOOLCHAIN_ROOT/lib/node_modules/npm/bin/npm-cli.js" ci --ignore-scripts --offline --replace-registry-host=never --cache="$TICKET23_NPM_VIEW"`；containers 前 RED，后 setup GREEN。
- [ ] **Step 4:** 运行后端四项与前端九项真实选择；预期 4 passed，九次各 1 passed、同文件未匹配项只 skipped：

```bash
(set -e
cd "$TICKET23_PRODUCT_ROOT"
backend/.venv/bin/python -m pytest -q backend/tests/test_creative_canvas.py::test_canvas_document_saves_without_shot_and_returns_project_assets backend/tests/test_creative_canvas.py::test_canvas_save_rejects_stale_document_revision backend/tests/test_creative_canvas.py::test_canvas_catalog_keeps_legacy_default_and_isolates_canvas_documents backend/tests/test_creative_canvas.py::test_soft_deleted_canvas_keeps_asset_references_and_can_restore --json-report --json-report-file=.ticket23-report.json
"$TICKET23_TOOLCHAIN_ROOT/bin/node" frontend/node_modules/vitest/vitest.mjs run frontend/src/CreativeCanvasTicket23Acceptance.test.tsx -t '^独立入口不依赖 Shot 并保留项目上下文$' --reporter=json --outputFile=.ticket23-structure-1.json
"$TICKET23_TOOLCHAIN_ROOT/bin/node" frontend/node_modules/vitest/vitest.mjs run frontend/src/CreativeCanvasTicket23Acceptance.test.tsx -t '^Toonflow 主体控件可通过可访问路径打开和关闭$' --reporter=json --outputFile=.ticket23-structure-2.json
"$TICKET23_TOOLCHAIN_ROOT/bin/node" frontend/node_modules/vitest/vitest.mjs run frontend/src/CreativeCanvas.test.tsx -t '^首次读取画布期间禁止写入草稿$' --reporter=json --outputFile=.ticket23-persistence-1.json
"$TICKET23_TOOLCHAIN_ROOT/bin/node" frontend/node_modules/vitest/vitest.mjs run frontend/src/CreativeCanvas.test.tsx -t '^保存冲突时保留本地草稿并显示重新读取入口$' --reporter=json --outputFile=.ticket23-persistence-2.json
"$TICKET23_TOOLCHAIN_ROOT/bin/node" frontend/node_modules/vitest/vitest.mjs run frontend/src/CreativeCanvas.test.tsx -t '^保存中继续编辑会保留草稿并用返回的新 revision 进行下一次保存$' --reporter=json --outputFile=.ticket23-persistence-3.json
"$TICKET23_TOOLCHAIN_ROOT/bin/node" frontend/node_modules/vitest/vitest.mjs run frontend/src/CreativeCanvas.test.tsx -t '^从画布目录切换时按 canvasId 加载独立工作区$' --reporter=json --outputFile=.ticket23-persistence-4.json
"$TICKET23_TOOLCHAIN_ROOT/bin/node" frontend/node_modules/vitest/vitest.mjs run frontend/src/CreativeCanvas.test.tsx -t '^保留本地草稿后切回会继续标记为未保存$' --reporter=json --outputFile=.ticket23-persistence-5.json
"$TICKET23_TOOLCHAIN_ROOT/bin/node" frontend/node_modules/vitest/vitest.mjs run frontend/src/creativeCanvasApi.test.ts -t '^不传 canvasId 时保留旧画布请求 URL 与载荷$' --reporter=json --outputFile=.ticket23-api-1.json
"$TICKET23_TOOLCHAIN_ROOT/bin/node" frontend/node_modules/vitest/vitest.mjs run frontend/src/creativeCanvasApi.test.ts -t '^目录 CRUD 与所有画布工作区请求都显式携带 canvasId$' --reporter=json --outputFile=.ticket23-api-2.json
)
```
- [ ] **Step 5:** 运行 `(cd "$TICKET23_PRODUCT_ROOT" && sha256sum backend/requirements.lock frontend/package-lock.json > /tmp/t23-locks.after)` 后 `diff -u /tmp/t23-locks.before /tmp/t23-locks.after`，核对 closure/预算；该直接 worktree 运行结果只作准备验证。形式化 E2 collect 后续必须从 Task 11 sanitized checkout 重放。然后在计划仓库提交 `git add .scratch/unified-remix-canvas-generation/acceptance/trust/dependencies .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_dependency_repositories.py .scratch/unified-remix-canvas-generation/acceptance/README.md && git commit -m "build: freeze ticket 23 dependency repositories"`。

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
- Produces: `parse_attestation`、`canonicalize_attestation`、`SSHReviewVerifier.verify`、`prepare-e4`、`collect-e4`。

- [ ] **Step 1:** 写 canonical JSON/signature/namespace/principal/fingerprint/scope/criteria/attachment/expiry/revocation/private-key-ban RED tests；secret-audit fixtures 覆盖 OpenSSH、PKCS#1 RSA、SEC1 EC、PKCS#8、encrypted PKCS#8 私钥头与配套 base64 payload，治理登记的 forbidden private-key byte value/SHA-256，过宽文件权限，以及 production JSON 禁止键名 `privateKey/privateKeyPath/passphrase/sshAuthSock/agentSocket`。单纯说明文字关键词不得误报。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py`；预期 RED。
- [ ] **Step 3:** stdin 传 payload 给登记 `ssh-keygen -Y verify`；生产 CLI 永不接收私钥或 agent socket。`audit_e4_secrets.py` 对 regular files no-follow 扫描上述完整私钥结构和值/digest，解析 production JSON key tree，并校验 `trust/` 公共 policy/signer 文件恰为 0644、任何私钥形态文件一律拒绝；显式 allowlist 只排除测试 fixture bytes 与 `redaction.py` 的 lexer grammar 源码，production CLI/config/trust/evidence/audit 永不豁免。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py .scratch/unified-remix-canvas-generation/acceptance/test_audit_e4_secrets.py`，再运行 `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/audit_e4_secrets.py --root .scratch/unified-remix-canvas-generation/acceptance --production-json .scratch/unified-remix-canvas-generation/acceptance/trusted-registry.json --allow-test .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py --allow-test .scratch/unified-remix-canvas-generation/acceptance/test_audit_e4_secrets.py --allow-grammar .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/redaction.py`；预期 GREEN、无结构化私钥/禁止值/禁止 JSON 字段/权限错误。关键词 grep 不作为验收；Task 26 在 reviewer policy 创建后必须再次扫描该 JSON。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{models.py,registry.py,verifiers/e4_ssh.py} .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py .scratch/unified-remix-canvas-generation/acceptance/tools/audit_e4_secrets.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py .scratch/unified-remix-canvas-generation/acceptance/test_audit_e4_secrets.py && git commit -m "feat: verify signed E4 attestations"`

### Task 25: 固化真实 Toonflow E1 与产品 E1 映射

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trust/git/toonflow-d86cce6b689916ab73f75bc4c1f42a2a7424516c.bundle`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trust/product-sources/sha256/<aa>/<digest>`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trust/e1-ticket23-sources.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_e1_sources.py`

**Interfaces:**
- Consumes: Tasks 10–11、22 的仓库外 staged raw container；Toonflow fixed commit/MIT license。
- Produces: 两个 E1 refs、Toonflow/license 与产品实现/测试/lock blob mappings。

- [ ] **Step 1:** 写 RED：Toonflow bundle 必含 fixed commit/objects/MIT；product raw Git container 必先通过 Task 11 inspector，E1 映射只绑定 `VerifiedProductRepositorySourceV1` 的 Task 22 commit 与逐 blob digests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_e1_sources.py`；预期真实 inputs RED。
- [ ] **Step 3:** Toonflow 用包内 Git bundle verify/cat-file；产品 staged container 先经 Task 11 全量 inspector/sanitized checkout 验证，再按 container digest 将相同 bytes 原子导入 `trust/product-sources/sha256/<aa>/<digest>`。禁止 vendor HEAD、工作区、staging path 或截图替代正式 ref。
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
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile_v2.py`

**Interfaces:**
- Consumes: Tasks 18–26 的真实 toolchain/platform/supervisor/source/dependency/E1/E4/public-policy digests。
- Produces: 固定 `TICKET23_POLICY_COMMIT`；E1/E2/E4 verifier、四 suites、C01–C03 criteria、v1 typed revocation。

- [ ] **Step 1:** 写 only-C01–C03、四 pytest、九 Vitest、same source/locks/digests、E1+E2+E4 minimum、缺 E4 阻塞 tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile_v2.py`；预期 RED。
- [ ] **Step 3:** 写全部真实 digest，禁止占位/fixture digest；撤销 v1 pointer/artifact，不改 receipt。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile_v2.py && git diff --exit-code HEAD -- .scratch/unified-remix-canvas-generation/acceptance/matrix-contract.json`；预期 GREEN 且 contract 未改。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/{profiles/ticket-23.json,trusted-registry.json,trust/revocations.json,test_ticket23_trusted_profile_v2.py} && git commit -m "policy: freeze ticket 23 trusted evidence profile" && git rev-parse HEAD`

### Task 28: 从固定 policy commit collect/register

**Files:**
- Create generated immutable objects: `.scratch/unified-remix-canvas-generation/acceptance/evidence/sha256/<aa>/<digest>`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/evidence-catalog.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/audit/ticket-23-trusted-evidence.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_collect_register.py`

**Interfaces:**
- Consumes: Task 27 exact commit 与稳定 `ReplayPolicyBindingV1`；每次命令各自构造短生命周期 `AcceptanceOperationSnapshotV1`；仓库外私钥产生的规范 E4 signature/attachment。
- Produces: E1 两个、E2 四个、E4 一个 artifacts/pointers；contract 不变。

- [ ] **Step 1:** 写 wrong-policy/operation-snapshot-race/missing-E4/source-dependency-mismatch 零 catalog 写、register 不晋升 tests；新增命名回归 `test_sequential_collect_register_keeps_all_prior_receipts_valid_in_final_catalog`：按 E1→E2→E4 顺序逐个 collect/register，每次 register 后 catalog digest 与 operation snapshot 都变化，但 replay-policy-binding digest 恒定；在最终 catalog 上重新 verify 全部先前 receipt，七个均有效。
- [ ] **Step 2:** 在登记 Linux executor 先运行 `test "$(git rev-parse HEAD)" = "$TICKET23_POLICY_COMMIT" && test -z "$(git status --porcelain)"`，再从固定 policy commit 执行以下 collect；每行返回值中的 `artifactRef` 分别保存为同名 `*_ARTIFACT`：

```bash
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e1.ticket23.toonflow-source.v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e1.ticket23.product-adaptation.v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.backend-persistence.v2 --subject "git://product@$TICKET23_PRODUCT_COMMIT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.frontend-persistence.v2 --subject "git://product@$TICKET23_PRODUCT_COMMIT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.api.v2 --subject "git://product@$TICKET23_PRODUCT_COMMIT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.canvas-structure.v2 --subject "git://product@$TICKET23_PRODUCT_COMMIT"
```

然后逐项运行：

```bash
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py register --profile ticket-23/v1 --verifier e1.ticket23.toonflow-source.v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --artifact "$E1_TOONFLOW_ARTIFACT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py register --profile ticket-23/v1 --verifier e1.ticket23.product-adaptation.v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --artifact "$E1_PRODUCT_ARTIFACT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py register --profile ticket-23/v1 --verifier e2.ticket23.backend-persistence.v2 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --artifact "$E2_BACKEND_ARTIFACT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py register --profile ticket-23/v1 --verifier e2.ticket23.frontend-persistence.v2 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --artifact "$E2_FRONTEND_ARTIFACT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py register --profile ticket-23/v1 --verifier e2.ticket23.api.v2 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --artifact "$E2_API_ARTIFACT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py register --profile ticket-23/v1 --verifier e2.ticket23.canvas-structure.v2 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --artifact "$E2_STRUCTURE_ARTIFACT"
```

预期六次均输出“已登记，不代表通过”，contract digest 不变；每次 collect/register 的 barrier 记录独立 operation snapshot，receipt 只记录同一个 stable replay-policy-binding digest。

- [ ] **Step 3:** 运行 `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py prepare-e4 --profile ticket-23/v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --review-input "$E4_REVIEW_INPUT_FILE" --output "$E4_ATTESTATION_FILE"`；审核者只在仓库外运行 `ssh-keygen -Y sign -n trusted-acceptance-e4 -f "$E4_PRIVATE_KEY_FILE" "$E4_ATTESTATION_FILE"`；随后运行 `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect-e4 --profile ticket-23/v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --attestation "$E4_ATTESTATION_FILE" --signature "$E4_ATTESTATION_FILE.sig" --attachment "$E4_OPERATION_RECORD_FILE"`，将返回值保存为 `E4_REVIEW_ARTIFACT`，再运行 `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py register --profile ticket-23/v1 --verifier e4.ticket23.canvas-review.v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --artifact "$E4_REVIEW_ARTIFACT"`。缺真实签名即停止，禁止测试 key 替代。
- [ ] **Step 4:** audit 逐命令记录 operation snapshot digest、同一 replay-policy-binding digest、subject/toolchain/dependency/verified source/supervisor/fingerprint/七 artifacts+pointers；最终 catalog 再校验七个 receipt，contract 前后 sha256 相同。
- [ ] **Step 5:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_collect_register.py -k 'sequential_collect_register or wrong_policy or snapshot_race or source_dependency'`，再运行完整 `test_ticket23_collect_register.py` 与 `git diff --exit-code HEAD -- .scratch/unified-remix-canvas-generation/acceptance/matrix-contract.json`；预期 sequential regression 在 final catalog 重验全部七项 GREEN、contract unchanged；再执行 `git add .scratch/unified-remix-canvas-generation/acceptance/{evidence,evidence-catalog.json,audit/ticket-23-trusted-evidence.json,test_ticket23_collect_register.py} && git commit -m "evidence: register ticket 23 trusted replay"`。

### Task 29: 晋升 C01–C03 并验证失效重对账

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_promotion.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/matrix-contract.json`
- Modify: `.scratch/unified-remix-canvas-generation/issues/23-align-toonflow-canvas-shell-and-persistence.md`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/README.md`

**Interfaces:**
- Consumes: Task 28 七个有效 pointers、Task 17 workflow。
- Produces: 仅 C01–C03 原子 projection/reconciliation；其他 135 项不变。

- [ ] **Step 1:** 写逐项 requirement、criteria reject/missing、revoked/tampered/unreachable、snapshot-race、deny-list tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_promotion.py`；预期尚未投影 RED。
- [ ] **Step 3:** 仅七项证据均有效才 promote/reconcile；缺 E4 保持阻塞。
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

### 自包含接口、依赖拓扑与真实命令

- bootstrap 与主链：1→2→3→4→5→6→7→8→9→10→11→12→13（独立批准门）→14→15→16→17。
- 真实基础验收：17→18→19→20→21；任一未通过，不得写 production profile。
- Ticket 23：21→22 product commit/raw container→23 product dependencies→24 E4 code→25 E1 refs→26 reviewer policy→27 policy commit→28 collect/register→29 promote/reconcile。
- `ProductRepositorySourceV1` 在 Task 2 定义、Task 10 构建/加载 raw container、Task 11 才产出 verified handle，Tasks 15–16/25/28 只消费 verified handle；无裸路径旁路。
- `ReplayPolicyBindingV1` 与 `AcceptanceOperationSnapshotV1` 在 Task 2 分开构造，Tasks 5/16–17/28 只重检 operation snapshot，receipt 始终只绑定稳定 replay policy；Task 28 final-catalog 回归证明顺序 register 不使旧 receipt 失效。
- containment 六方法、消息字段、序号/摘要链、权限、binary identity 与唯一 kernel mechanism 在 Task 13 冻结；Task 14 不能重新选型。
- Task 3 是唯一 scanner owner；receipt/barrier/collect 只调用该接口，无依赖循环。
- Task 1 给出真实 bootstrap hashes；Tasks 18–21 各有 script/input-manifest/provenance/output/digest 命令并对缺 bytes 停止；Ticket 23 npm ci/uv venv+sync、lock 前后摘要、Cargo/Linux、真实 pytest/Vitest 均禁止临时宿主安装。
- Review Focus 五项分别由 Tasks 5/17、3/20、14/21、19–20、27–29 持有，编号无漂移。
- 29 个 Task 均为单独实现会话、单独 commit、单独新审查会话；Task 13、27、28 是不可跳过的硬门。

## 执行与批准状态

- E2 v2 书面规格已批准；本修订计划等待用户复审。
- 执行方式固定为 subagent-driven：逐 Task 新实现会话 + 新独立审查会话，前一 Task 通过才进入下一 Task，最后全分支独立审查。
- 本次规划会话不授权实现或启动任何 Task；复审通过后从 Task 1 开始，不恢复旧 Task 6，也不跳过 Task 13、27、28。
