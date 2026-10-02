# E2 v2 可信重放协议 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用内容寻址工具链、产品源码与依赖仓库、操作级 artifact barrier、不可逃逸 containment、确定性离线重放和 v2 receipt 取代已阻塞的 E2 v1，并装配 Ticket 23 冻结的最低 E1+E2+E4 组合。

**Architecture:** E2 仍分为阶段 A 全操作只读预检与阶段 B 可信重放；所有冻结输入进入同一个规范 `AcceptanceSnapshotV1`，runner 只消费已验证的 `ProductRepositorySourceV1`，不接受宿主路径。containment 先冻结协议、消息和唯一 Linux 内核机制，再实现 backend；Ticket 23 的 E1/E4 是旧计划未执行的既有前置任务，仅为完成 C01–C03 profile 纳入，不改变 E2 v2 规格或三引擎职责。

**Tech Stack:** Python 3.13、pytest 8.3.5、pytest-json-report 1.5.0、Vitest 3.0.8、Node 24.20.0、npm 11.19.0、Git 2.53.0、uv 0.11.15、zstandard 0.23.0、规范 POSIX ustar + 单帧 zstd、Linux cgroup v2 + clone3 + seccomp user notification、Rust（锁定 Cargo.lock）、OpenSSH `ssh-keygen -Y`、现有 SHA-256 CAS 与 JSON 验收资产。

**Spec:** `docs/superpowers/specs/2026-10-02-e2-v2-trusted-replay-design.md`

## Global Constraints

- 历史计划 `docs/superpowers/plans/2026-10-02-trusted-acceptance-evidence-bridge.md` 保持不变；旧 Task 5 保持 `blocked / superseded`，旧 Task 6 未开始。
- 新增 E1/E4 Tasks 23–28 是完成 Ticket 23 profile 所需、旧计划 Tasks 9–12 尚未执行的前置工作；不扩展 E2 v2 规格、不改变 138 项合同、四状态、零容忍规则或 E1/E2/E3/E4 职责。
- `trusted-e2-suite/v2`、`trusted-e2-receipt/v2`、`trusted-e2-toolchain-tar-zstd/v1`、`trusted-e2-dependency-repository/v1`、`trusted-product-repository-source/v1` 均拒绝未知、缺失、重复字段；v1 suite/receipt 不迁移、不猜测升级。
- `validate/promote/reconcile` 的全部 E2 requirement 先通过同一阶段 A barrier；失败时 `spawnCount=checkoutCount=casWrites=contractWrites=0`。verify 永不写 CAS；collect 仅完整成功后原子写一次 receipt。
- `AcceptanceSnapshotV1` 必须绑定 contract、catalog、registry、profile、revocation、reviewer policy、toolchain、dependency repository、product source、containment policy 的规范 digest；阶段 B 前后复核同一 digest。
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
- Task 12 先形成独立设计审查门，Task 13 才可实现。若审查判定固定机制不能满足无丢失边界，后续任务停止并修订规格/计划，不得临时换机制。
- 每 leaf 最多 4,096 exec events、200,000 write events；序号跳跃、摘要链断裂、observer EOF、overflow、未知消息均 fail closed。macOS 无真实 VM 时返回 `E2_CONTAINMENT_UNAVAILABLE`，不降级 PGID。
- Ticket 23 四个 E2 suites 共用同一 product source/snapshot 和精确 lock/container/toolchain/containment digests；产品专属 Python/npm containers 不得复用最小 fixture digest。
- 先提交真实 registry/profile/revocation 与全部 digest 的固定 policy commit；下一独立 commit 才 collect/register，并提交 CAS objects、catalog pointer 与审计记录。`matrix-contract.json` 在 collect/register commit 中字节不变。
- E4 私钥始终在仓库外；仓库只保存 allowed-signers、公钥指纹、reviewer policy、规范 attestation、签名和附件摘要。缺真实 E4 或任一 E1/E2 时 C01–C03 必须阻塞。
- 每个 Task 使用新实现会话，提交后使用新独立审查会话；Critical/Important 清零且命令通过才进入下一 Task。实现前读取任务所涉现有/上游源码、版本与许可证，并记录在既有 SDD ledger。

## Review Focus

- snapshot、最后一个 pointer 或 policy digest 在 barrier 前后变化必须零 spawn/checkout/CAS/contract 写；Tasks 4、16 覆盖。
- 32 KiB 后才出现跨 chunk token、非法 UTF-8、未闭合 quote/JSON 或 agent socket 标点仍须读到 EOF并返回 `OUTPUT_REDACTION_UNSAFE`；Tasks 2、19 覆盖。
- leader 任意结局后 double-fork+`setsid/setpgid`+关闭 stdio 仍须处于 leaf 并被清空；Tasks 13、20 覆盖。
- uv probe cwd/env/script/cache/extra child、恶意 entry-point 越界、project/workspace/build backend、DNS/socket、derived entrypoint replacement 均须命中固定错误码且不得进入测试；Tasks 18–19 覆盖。
- Ticket 23 缺 E4、双 active pointer、产品 source/dependency digest 不符或 collect 时 contract 改变都须阻塞且不晋升；Tasks 26–28 覆盖。

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
class ProductRepositorySourceV1:
    subject_repository: str
    source_ref: str
    source_digest: str
    container_format: Literal["git-bundle/v1"]
    platform: Literal["git-sha1"]
    budget: ProductSourceBudgetV1
    snapshot_binding: str

@dataclass(frozen=True)
class VerificationOperationV1:
    operation_id: str
    kind: Literal["verify", "validate", "promote", "reconcile", "collect"]
    requirement_ids: tuple[str, ...]
    started_snapshot_digest: str

@dataclass(frozen=True)
class AcceptanceSnapshotV1:
    contract_digest: str
    catalog_digest: str
    registry_digest: str
    profile_digests: tuple[NamedDigest, ...]
    revocation_digest: str
    reviewer_policy_digest: str
    toolchain_digests: tuple[str, ...]
    dependency_digests: tuple[str, ...]
    product_source_digests: tuple[str, ...]
    containment_policy_digests: tuple[str, ...]
    canonical_digest: str
```

`canonical_digest` 固定为上述其他字段按字段名排序、UTF-8 紧凑规范 JSON 编码后的 SHA-256；计算输入排除 `canonical_digest` 自身。

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

### Task 1: 冻结 operation、snapshot、policy、product-source 与错误合同

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/e2_policy.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/registry.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/errors.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py`

**Interfaces:**
- Consumes: 规格 4、6.1、7、9、10、14 和既有六字段 pointer。
- Produces: 上述 immutable 类型、`build_acceptance_snapshot(root, kind) -> AcceptanceSnapshotV1`、`E2SuiteDefinitionV2.from_policy(...)`。

- [ ] **Step 1:** 写 `test_snapshot_binds_every_frozen_input`、`test_product_source_rejects_host_path`、`test_v1_is_not_upgraded`。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py`；预期缺类型而 RED。
- [ ] **Step 3:** 实现严格字段、tuple/frozen 深不可变与规范 JSON digest；缺任一 snapshot 输入即阻塞。
- [ ] **Step 4:** 重跑同命令；预期 GREEN 且 production profile 仍为空。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{e2_policy.py,registry.py,errors.py} .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py && git commit -m "feat: define trusted replay operation policy"`

### Task 2: 提前建立唯一共享 lexer 与 StreamingRedactor

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

### Task 3: 建立 bounded stable I/O 与 receipt v2 codec

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/bounded_io.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/receipts.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/storage.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_receipts_v2.py`

**Interfaces:**
- Consumes: Task 1 policy/snapshot、Task 2 唯一 scanner。
- Produces: `BoundedStableReader.read(...) -> StableRead`、`TrustedE2ReceiptV2.decode/encode_canonical`；receipt 绑定 snapshot/source/toolchain/dependency/containment/proof digests。

- [ ] **Step 1:** 写超限/symlink/race/未知字段/非预期 ID/秘密残留/digest mismatch tests。
- [ ] **Step 2:** 运行 `backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_receipts_v2.py`；预期 module RED。
- [ ] **Step 3:** 实现 O_NOFOLLOW、前后 stat、1 MiB 上限；codec 只调用 Task 2 scanner。
- [ ] **Step 4:** 重跑同命令；预期规范 round-trip GREEN。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{bounded_io.py,receipts.py,storage.py} .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_receipts_v2.py && git commit -m "feat: decode bounded E2 v2 receipts"`

### Task 4: 实现 operation-wide artifact barrier

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/artifact_barrier.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_artifact_barrier.py`

**Interfaces:**
- Consumes: Tasks 1、3。
- Produces: `preflight(operation, snapshot) -> PrevalidatedE2Batch`、`recheck(batch, current_snapshot) -> None`。

- [ ] **Step 1:** 写 missing/ambiguous/revoked/last-invalid/snapshot-race tests，全部断言零 spawn/checkout/CAS。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_artifact_barrier.py`；预期 module RED。
- [ ] **Step 3:** 冻结 requirement/candidate/active set，全部 receipt 预检后一次 gate。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_artifact_barrier.py`；预期 race 为 `ACCEPTANCE_STATE_CHANGED`。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/artifact_barrier.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_artifact_barrier.py && git commit -m "feat: preflight E2 artifacts operation wide"`

### Task 5: 验证 canonical tar+zstd 并锁定依赖

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/canonical_archive.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_canonical_archive.py`
- Modify: `backend/requirements.txt`
- Modify: `backend/requirements.lock`

**Interfaces:**
- Consumes: Task 3、规格 6.2/6.3。
- Produces: `CanonicalTarZstdReader.validate_and_extract(...) -> ValidatedArchive`；固定 `zstandard==0.23.0` wheel hash。

- [ ] **Step 1:** 写 checksum/frame/manifest-first/parent/order/header/path/type/budget RED tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_canonical_archive.py`；预期 module/dependency RED。
- [ ] **Step 3:** 用登记 uv 0.11.15 运行 `sha256sum backend/requirements.lock > /tmp/e2-lock.before && /opt/trusted-e2/bin/uv pip sync --python=backend/.venv/bin/python backend/requirements.lock --offline --no-index --find-links=/opt/trusted-e2/bootstrap-wheels --no-build --require-hashes --no-sources --config-file=/opt/trusted-e2/config/uv-empty.toml --no-python-downloads --cache-dir=/tmp/e2-zstd-cache && sha256sum backend/requirements.lock > /tmp/e2-lock.after && diff -u /tmp/e2-lock.before /tmp/e2-lock.after`。
- [ ] **Step 4:** 实现并重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_canonical_archive.py`；预期 GREEN、攻击输入无 payload 残留。
- [ ] **Step 5:** `git add backend/requirements.{txt,lock} .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/canonical_archive.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_canonical_archive.py && git commit -m "feat: validate canonical replay archives"`

### Task 6: 验证 toolchain、安全审核与平台身份

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/toolchain.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/platform_identity.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_toolchain.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_platform_identity.py`

**Interfaces:**
- Consumes: Tasks 1、5 与 uv/Git/Node/npm 实际源码/许可。
- Produces: `ToolchainPackage.open`、`measure_platform`、`compare_platform_identity`。

- [ ] **Step 1:** 写 closure/uv identity/advisory/dynamic library/kernel ABI/PATH marker RED tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_toolchain.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_platform_identity.py`；预期 modules RED。
- [ ] **Step 3:** 实现 exact bytes/build identity 和 offline security snapshot。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_toolchain.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_platform_identity.py`；预期所有异常在 setup spawn 前阻塞。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{toolchain.py,platform_identity.py} .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_{toolchain,platform_identity}.py && git commit -m "feat: verify trusted replay toolchains"`

### Task 7: 实现 wheel-only Python dependency view

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/dependency_repository.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/python_dependencies.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_python_dependencies.py`

**Interfaces:**
- Consumes: Tasks 1、5、6。
- Produces: `DependencyRepository.open`、`PythonFindLinksView.build`、精确 lock/package/install-tree mapping。

- [ ] **Step 1:** 写 exact-pin/hash、sdist/source/editable/project/build、wheel metadata、extra/missing blob、two-root identity tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_python_dependencies.py`；预期 RED。
- [ ] **Step 3:** 实现纯函数只读 flat view，预检不启动 uv。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_python_dependencies.py`；预期 source 为 `E2_PYTHON_SDIST_FORBIDDEN`，mapping/view 使用固定码。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{dependency_repository.py,python_dependencies.py} .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_python_dependencies.py && git commit -m "feat: build trusted Python dependency views"`

### Task 8: 实现 npm cacache offline view

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/npm_dependencies.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_npm_dependencies.py`

**Interfaces:**
- Consumes: Tasks 6–7 与 npm/cacache 实际源码。
- Produces: `NpmCacacheView.build`、`validate_npm_lock_mapping`。

- [ ] **Step 1:** 写 lock v3、content-v2/index-v5、line hash/key/SRI/path、extra mapping、two-root identity tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_npm_dependencies.py`；预期 RED。
- [ ] **Step 3:** 实现规范 cacache 只读 lower view。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_npm_dependencies.py`；预期篡改在 npm spawn 前为 `E2_DEPENDENCY_VIEW_INVALID`。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/npm_dependencies.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_npm_dependencies.py && git commit -m "feat: build trusted npm cache views"`

### Task 9: 加载并绑定内容寻址产品 Git source

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/product_source.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_product_source.py`

**Interfaces:**
- Consumes: Tasks 1、3、5。
- Produces: `ProductSourceLoader.load(source, snapshot) -> VerifiedProductRepositorySourceV1`、`snapshot_product_source`。

- [ ] **Step 1:** 写逻辑名/ref/digest/budget/platform/format/snapshot binding、symlink/race、宿主路径拒绝 tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_product_source.py`；预期 RED。
- [ ] **Step 3:** 只从 content store 读取固定 bundle，返回 opaque verified handle。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_product_source.py`；预期 source/snapshot 改一字节即阻塞。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/product_source.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_product_source.py && git commit -m "feat: bind trusted product repository sources"`

### Task 10: 从 verified source 构造 sanitized Git checkout

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/git_subject.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_git_subject.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e2_suite.py`

**Interfaces:**
- Consumes: Task 6 包内 Git、Task 9 verified source；不接受裸 path/cwd。
- Produces: `SanitizedGitView.build(source, commit, toolchain_git, destination) -> FixedCheckout`。

- [ ] **Step 1:** 写 config/ref/object/alternate/replace/graft/promisor/pack sidecar/symlink/budget tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_git_subject.py`；预期旧 host Git path RED。
- [ ] **Step 3:** 自有 parser 预筛，包内 Git `index-pack --strict`/`fsck --strict`，仅固定 closure。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_git_subject.py`；预期 API 无 repository_root/cwd。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{git_subject.py,verifiers/e2_suite.py} .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_git_subject.py && git commit -m "feat: sanitize verified product sources"`

### Task 11: 闭合真实 pytest/Vitest reporter

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/reporters.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_reporters.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/reporters/pytest-pass.json`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/reporters/vitest-pass.json`

**Interfaces:**
- Consumes: Tasks 1、3 与 reporter 实际源码。
- Produces: `parse_pytest_json_v1`、`parse_vitest_json_v1`。

- [ ] **Step 1:** 写 pytest zero/deselected/exit/count 与 Vitest expected pass+same-file skipped/extra/skip/wrong-name tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_reporters.py`；预期 RED。
- [ ] **Step 3:** 实现 expected-set closure，禁止持久化任意 report ID。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_reporters.py`；预期 schema/count 为 `REPORT_SCHEMA_INVALID`，selection 为 `TEST_SELECTION_MISSING`。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/reporters.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_reporters.py .scratch/unified-remix-canvas-generation/acceptance/fixtures/reporters/{pytest-pass.json,vitest-pass.json} && git commit -m "feat: parse trusted replay reports"`

### Task 12: 冻结 containment 协议与唯一内核机制（独立设计门）

**Files:**
- Create: `docs/superpowers/specs/2026-10-02-e2-v2-linux-containment-profile.md`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/__init__.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/base.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/protocol.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_protocol.py`

**Interfaces:**
- Consumes: Tasks 1、2、6。
- Produces: 精确 API、canonical supervisor schema、proof digest、已批准 `linux-cgroup-v2-seccomp-notify/v1` gate。

```python
class ReplayContainmentV1(Protocol):
    def create_session(self, request: CreateSessionRequestV1) -> SessionHandleV1: ...
    def create_invocation(self, session: SessionHandleV1, request: CreateInvocationRequestV1) -> InvocationHandleV1: ...
    def spawn_into(self, invocation: InvocationHandleV1, request: SpawnRequestV1, stdout: SafeSink, stderr: SafeSink) -> SpawnHandleV1: ...
    def terminate_all(self, invocation: InvocationHandleV1, reason: TerminationReason) -> TerminationProofV1: ...
    def prove_empty(self, invocation: InvocationHandleV1) -> EmptyProofV1: ...
    def destroy(self, session: SessionHandleV1) -> DestroyProofV1: ...
```

- [ ] **Step 1:** 写消息 RED tests；仅允许 `hello/session-created/invocation-created/spawned/exec/write/exit/terminated/empty/destroyed/error`，每条含 schema/sessionId/invocationId/seq/kind/payload/prevDigest/digest。
- [ ] **Step 2:** 冻结无丢失与权限模型：继承 `SOCK_SEQPACKET`；seq 从 0 连续且 digest chain；EOF 仅 destroyed 后；root-owned supervisor 管理 delegated cgroup/namespace/seccomp listener，child 入组后降权；binary SHA-256、ELF build-id、Cargo.lock、executor image digest 绑定 snapshot/receipt。
- [ ] **Step 3:** 冻结唯一机制：clone3 首次入组；seccomp user notification 观察 execve/execveat 与固定 write/network syscall，拒绝 io_uring/bpf/perf 旁路；只读 mounts+writable allowlist；pidfd/waitid+cgroup.kill+populated=0+rmdir。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_protocol.py`；预期 loss/duplicate/reorder/unknown/overflow/bad digest/binary mismatch 全部 fail closed。
- [ ] **Step 5:** `git add docs/superpowers/specs/2026-10-02-e2-v2-linux-containment-profile.md .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_protocol.py && git commit -m "design: freeze trusted replay containment protocol"`；独立审查 0 Critical/Important 才进入 Task 13。

### Task 13: 实现获批 Linux cgroup v2 backend

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux_cgroup_v2.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/vm_cgroup.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux-supervisor/Cargo.toml`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux-supervisor/Cargo.lock`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux-supervisor/src/main.rs`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_linux.py`

**Interfaces:**
- Consumes: Task 12 approved protocol，不得改机制/schema。
- Produces: `LinuxCgroupV2Containment`；无 guest proof 的 VM 固定 unavailable。

- [ ] **Step 1:** 写 leader 0/nonzero/timeout × double-fork/setsid/setpgid/closed-stdio、census/protected digest/macOS unavailable tests。
- [ ] **Step 2:** 运行 `cargo test --locked --manifest-path .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux-supervisor/Cargo.toml && backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_linux.py`；预期 RED。
- [ ] **Step 3:** 严格实现 Task 12，无 fallback；proof 缺失为 `E2_CONTAINMENT_CLEANUP_FAILED`。
- [ ] **Step 4:** 重跑 `cargo test --locked --manifest-path .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux-supervisor/Cargo.toml && PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_linux.py`；预期首个 exec 至 destroyed transcript 连续且后代全清。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_linux.py && git commit -m "feat: contain replay with cgroup v2"`

### Task 14: 编排 TrustedReplaySession

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/replay.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e2_suite.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py`

**Interfaces:**
- Consumes: Tasks 1–13；只接受 suite、verified source、snapshot、containment。
- Produces: `TrustedReplaySession.replay(...) -> ReplayResult`，含全部 input/proof/report/output digests。

- [ ] **Step 1:** 写严格顺序、裸 path/cwd 类型拒绝、lock/install/entrypoint/census tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py`；预期旧 host-tool/PGID RED。
- [ ] **Step 3:** 实现 source/toolchain→checkout→views→setup leaves→entrypoint→test leaves→report→empty/destroy。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_{toolchain,platform_identity,python_dependencies,npm_dependencies,product_source,git_subject,reporters,containment_protocol,containment_linux}.py`；预期 GREEN 且无 `shutil.which/os.killpg/receipt-v1`。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{replay.py,verifiers/e2_suite.py} .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py && git commit -m "feat: orchestrate trusted replay sessions"`

### Task 15: 分离 collect/verify

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/collection.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e2_suite.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_collection_v2.py`

**Interfaces:**
- Consumes: Tasks 2–4、14。
- Produces: `verify_prevalidated`（无 writer）、`E2Collector.collect`（成功一次 put-if-absent）。

- [ ] **Step 1:** 写 verify=0 CAS、失败=0 CAS、成功 collect=1、expected-only receipt tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_collection_v2.py`；预期 façade RED。
- [ ] **Step 3:** 内存构造→唯一 scanner→snapshot recheck→唯一 CAS put；不写 catalog/contract。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_collection_v2.py`；预期 write spy 精确为 0/1。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{collection.py,verifiers/e2_suite.py} .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_collection_v2.py && git commit -m "feat: collect E2 v2 receipts after replay"`

### Task 16: 接入 workflow 与原子 projection

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/evaluation.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/projection.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/runtime.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/validate_acceptance.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_workflow_v2.py`

**Interfaces:**
- Consumes: Tasks 1、4、15。
- Produces: `evaluate_operation`、`AtomicContractProjector.commit(expected_snapshot, overrides)`。

- [ ] **Step 1:** 写全操作 barrier、validate read-only、snapshot CAS、fsync/replace、register 不晋升 tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_workflow_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_acceptance_contract.py`；预期 RED。
- [ ] **Step 3:** 阶段 A 一次 preflight，阶段 B 确定顺序 replay，最终 recheck 后唯一投影。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_workflow_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_acceptance_contract.py`；预期 138 项、四状态、空不适用不变。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{evaluation.py,projection.py,runtime.py} .scratch/unified-remix-canvas-generation/acceptance/validate_acceptance.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_workflow_v2.py && git commit -m "feat: gate acceptance with trusted replay"`

### Task 17: 构建真实 production toolchain/platform artifact 并执行负向 fixture

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/`
- Create generated artifact: `.scratch/unified-remix-canvas-generation/acceptance/trust/toolchains/sha256/<aa>/<digest>`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_toolchain_platform.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/README.md`

**Interfaces:**
- Consumes: Tasks 5–6、13、16。
- Produces: 独立安全审核后可供 Ticket 23 使用的 `TICKET23_TOOLCHAIN_REF`、`TICKET23_PLATFORM_DIGEST`、`TICKET23_SUPERVISOR_DIGEST`；恶意变体只在临时 fixture 根生成，绝不注册。

- [ ] **Step 1:** 写 archive→`E2_TOOLCHAIN_ARCHIVE_INVALID`、uv/advisory→`E2_TOOLCHAIN_CLOSURE_INVALID`、ABI→`E2_TOOLCHAIN_PLATFORM_MISMATCH`、PATH marker→`E2_HOST_TOOL_FALLBACK`。
- [ ] **Step 2:** 在登记 Linux executor 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_toolchain_platform.py`；预期真实包 RED。
- [ ] **Step 3:** 从显式预审 bytes 构建唯一 canonical positive artifact，绑定 Task 13 supervisor binary/Cargo.lock、真实 security snapshot 与 platform measurement；各负向变体只从它在临时根逐项突变。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_toolchain_platform.py`；预期正负例 GREEN 并记录真实 digest。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain .scratch/unified-remix-canvas-generation/acceptance/trust/toolchains .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_toolchain_platform.py .scratch/unified-remix-canvas-generation/acceptance/README.md && git commit -m "build: freeze trusted replay toolchain"`

### Task 18: 真实 Python 离线安装与攻击 fixture

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/python/`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_python.py`

**Interfaces:**
- Consumes: Tasks 7、14、17。
- Produces: 真实 wheel-only view/install/report fixture；不供 Ticket 23 复用 digest。

- [ ] **Step 1:** 写 probe cwd/env/script/cache/extra child→`E2_PYTHON_SDIST_FORBIDDEN`；project/workspace/build→同码；恶意 entry-point→`E2_SETUP_FAILED` 且 canary/write-set 不变；DNS/socket→`E2_LIVE_REGISTRY_ACCESS`；entrypoint replacement→`E2_DERIVED_ENTRYPOINT_INVALID`。
- [ ] **Step 2:** 在登记 Linux executor 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_python.py`；预期 repository/lock RED。
- [ ] **Step 3:** 用真实 uv 0.11.15、两独占 cache 离线 venv/sync，lock 前后 sha256 相同，真实 reporter 1.5.0。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_python.py`；正例 GREEN、每个负例唯一错误码且 test spawn=0。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/python .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_python.py && git commit -m "test: validate offline Python replay"`

### Task 19: 真实 npm、reporter 与输出 fixture

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/npm/`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_npm_reporter.py`

**Interfaces:**
- Consumes: Tasks 2、8、11、14、17。
- Produces: 真实 cacache/Vitest/redaction fixture；不供 Ticket 23 复用 digest。

- [ ] **Step 1:** 写 cache篡改→`E2_DEPENDENCY_VIEW_INVALID`；DNS/socket→`E2_LIVE_REGISTRY_ACCESS`；vitest replacement→`E2_DERIVED_ENTRYPOINT_INVALID`；expected skip/extra→`TEST_SELECTION_MISSING`；count contradiction→`REPORT_SCHEMA_INVALID`；尾部秘密→`OUTPUT_REDACTION_UNSAFE`。
- [ ] **Step 2:** 在登记 Linux executor 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_npm_reporter.py`；预期 container RED。
- [ ] **Step 3:** 用包内 Node/npm 离线 ci，再直接加载 verified vitest.mjs 逐名运行。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_npm_reporter.py`；每 invocation 1 passed+same-file skipped，receipt 只含 expected ID。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/npm .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_npm_reporter.py && git commit -m "test: validate offline npm replay"`

### Task 20: 真实 containment 无丢失与逃逸 fixture

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/containment/escape_helper.c`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_containment.py`

**Interfaces:**
- Consumes: Tasks 12–13。
- Produces: 首个 untrusted exec 至 empty/destroyed 的真实 transcript/proof。

- [ ] **Step 1:** 写 leader outcomes/escape reaped、seq/loss/overflow/binary mismatch→`E2_CONTAINMENT_CLEANUP_FAILED`、无能力→`E2_CONTAINMENT_UNAVAILABLE`、socket→`E2_LIVE_REGISTRY_ACCESS`。
- [ ] **Step 2:** 在登记 Linux executor 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_containment.py`；预期 helper RED。
- [ ] **Step 3:** 用登记 compiler 构建 helper 并重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_containment.py`；预期 membership/termination/empty/destroyed/digest chain 闭合。
- [ ] **Step 4:** macOS 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_containment.py -k unavailable`；预期 unavailable，无 PGID fallback。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/containment .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_fixture_containment.py && git commit -m "test: prove replay containment cleanup"`

### Task 21: 准备并冻结 Ticket 23 产品主体

**Files:**
- Create in isolated product worktree: `frontend/src/CreativeCanvasTicket23Acceptance.test.tsx`
- Modify in isolated product worktree: `backend/requirements.lock`

**Interfaces:**
- Consumes: 产品实际源码，不消费 fixture digest。
- Produces: clean `TICKET23_PRODUCT_COMMIT`、四 pytest node IDs、结构两项/持久化五项/API 两项 Vitest fullName、两个 lock digests。

- [ ] **Step 1:** 先读产品实现与三个现有测试，核对真实数据流/许可/测试身份，只补缺失结构 tests 和 wheel-only lock；记录四个 pytest node IDs 与九个 Vitest fullName。
- [ ] **Step 2:** 写两个专用结构测试；以测试文件缺失为 RED 证据，不在依赖物化前运行产品测试或使用宿主临时安装。
- [ ] **Step 3:** 逐项静态核对 requirements lock 仅含 exact pin+SHA-256 wheel、package-lock v3 与两个专用测试名；不把静态核对冒充行为 GREEN。
- [ ] **Step 4:** 在产品 worktree 提交 `git add backend/requirements.lock frontend/src/CreativeCanvasTicket23Acceptance.test.tsx && git commit -m "test: prepare ticket 23 trusted replay subject" && git status --short && git rev-parse HEAD`；预期 clean worktree 与 40 位 SHA。实际行为 RED/GREEN 固定由 Task 22 在产品专属依赖物化后执行。
- [ ] **Step 5:** 用包内 Git 生成 product bundle，计算 commit/bundle/two-lock digests，作为 Task 22 唯一输入。

### Task 22: 构建 Ticket 23 产品专属 dependency containers

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trust/dependencies/ticket-23/manifest.json`
- Create generated blobs: `.scratch/unified-remix-canvas-generation/acceptance/trust/dependencies/sha256/<aa>/<digest>`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_dependency_repositories.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/README.md`

**Interfaces:**
- Consumes: Task 21 commit/locks、Tasks 7–8、17–20；不得复用 fixture digest。
- Produces: 产品专属 Python/npm refs、真实 wheel/tgz、mapping/view/install-tree/native-closure digests。

- [ ] **Step 1:** 写 RED：两个 locks 无唯一 mapping 时 `E2_DEPENDENCY_REPOSITORY_INVALID`。
- [ ] **Step 2:** 记录 `sha256sum backend/requirements.lock frontend/package-lock.json > /tmp/t23-locks.before`；仅从预审 blobs 构建 containers，禁止网络/宿主临时安装。
- [ ] **Step 3:** 明确 RED/GREEN：将 Tasks 17、21、22 验证输出赋给 `TICKET23_TOOLCHAIN_ROOT`、`TICKET23_PYTHON_VIEW`、`TICKET23_NPM_VIEW`；运行 `"$TICKET23_TOOLCHAIN_ROOT/bin/uv" venv backend/.venv --python="$TICKET23_TOOLCHAIN_ROOT/bin/python" --no-project --no-config --no-python-downloads --cache-dir="$TICKET23_UV_CACHE_1"`，再运行 `"$TICKET23_TOOLCHAIN_ROOT/bin/uv" pip sync --python=backend/.venv/bin/python backend/requirements.lock --offline --no-index --find-links="$TICKET23_PYTHON_VIEW" --no-build --require-hashes --no-sources --config-file="$TICKET23_TOOLCHAIN_ROOT/config/uv-empty.toml" --no-python-downloads --cache-dir="$TICKET23_UV_CACHE_2"`，并运行 `"$TICKET23_TOOLCHAIN_ROOT/bin/node" "$TICKET23_TOOLCHAIN_ROOT/lib/node_modules/npm/bin/npm-cli.js" ci --ignore-scripts --offline --replace-registry-host=never --cache="$TICKET23_NPM_VIEW"`；containers 前 RED，后 setup GREEN。
- [ ] **Step 4:** 运行后端四项与前端九项真实选择；预期 4 passed，九次各 1 passed、同文件未匹配项只 skipped：

```bash
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
```
- [ ] **Step 5:** `sha256sum backend/requirements.lock frontend/package-lock.json > /tmp/t23-locks.after && diff -u /tmp/t23-locks.before /tmp/t23-locks.after`，核对 closure/预算；然后 `git add .scratch/unified-remix-canvas-generation/acceptance/trust/dependencies .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_dependency_repositories.py .scratch/unified-remix-canvas-generation/acceptance/README.md && git commit -m "build: freeze ticket 23 dependency repositories"`。

### Task 23: 实现 E4 attestation、SSH 验签、scope 与 revocation

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e4_ssh.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/models.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/registry.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py`

**Interfaces:**
- Consumes: 旧计划 Task 9、namespace `trusted-acceptance-e4`、Task 1 snapshot/revocation。
- Produces: `parse_attestation`、`canonicalize_attestation`、`SSHReviewVerifier.verify`、`prepare-e4`、`collect-e4`。

- [ ] **Step 1:** 写 canonical JSON/signature/namespace/principal/fingerprint/scope/criteria/attachment/expiry/revocation/private-key-ban RED tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py`；预期 RED。
- [ ] **Step 3:** stdin 传 payload 给登记 `ssh-keygen -Y verify`；生产 CLI 永不接收私钥或 agent socket。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py && ! rg -n 'PRIVATE KEY|E4_PRIVATE_KEY|SSH_AUTH_SOCK' .scratch/unified-remix-canvas-generation/acceptance --glob '!test_trusted_evidence_e4.py'`；预期 GREEN 且无私钥。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{models.py,registry.py,verifiers/e4_ssh.py} .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py && git commit -m "feat: verify signed E4 attestations"`

### Task 24: 固化真实 Toonflow E1 与产品 E1 映射

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trust/git/toonflow-d86cce6b689916ab73f75bc4c1f42a2a7424516c.bundle`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trust/product-sources/sha256/<aa>/<digest>`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trust/e1-ticket23-sources.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_e1_sources.py`

**Interfaces:**
- Consumes: Task 9、Task 21；Toonflow fixed commit/MIT license。
- Produces: 两个 E1 refs、Toonflow/license 与产品实现/测试/lock blob mappings。

- [ ] **Step 1:** 写 RED：bundle 必含 fixed commit/objects/MIT；product source 绑定 Task 21 commit 与逐 blob digests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_e1_sources.py`；预期真实 inputs RED。
- [ ] **Step 3:** 包内 Git bundle verify/cat-file；禁止 vendor HEAD、工作区或截图替代。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_e1_sources.py`；预期 commit/license/all blobs 闭合。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trust/{git,product-sources,e1-ticket23-sources.json} .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_e1_sources.py && git commit -m "build: freeze ticket 23 E1 sources"`

### Task 25: 固化 allowed-signers、reviewer policy 与八项 criteria

**Files:**
- Create from governance input: `.scratch/unified-remix-canvas-generation/acceptance/trust/e4-allowed-signers`
- Create from governance input: `.scratch/unified-remix-canvas-generation/acceptance/trust/reviewer-policy.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_reviewer_policy.py`

**Interfaces:**
- Consumes: Task 23 与真实 reviewer 公钥/principal/fingerprint；私钥不入仓库。
- Produces: allowed-signers/reviewer policy digest，scope 仅 ticket-23/v1、C01–C03、八项 criteria。

- [ ] **Step 1:** 写 scope/criteria/expiry/fingerprint/duplicate/revocation/缺 E4 必阻塞 RED tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_reviewer_policy.py`；预期真实 policy 缺失而 RED。
- [ ] **Step 3:** 运行 `awk '{for (i=1;i<=NF;i++) if ($i ~ /^ssh-/) {print $i, $(i+1); exit}}' .scratch/unified-remix-canvas-generation/acceptance/trust/e4-allowed-signers | ssh-keygen -l -E sha256 -f -`；输出必须等于治理批准 fingerprint。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_reviewer_policy.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py`；预期 GREEN 且无私钥。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trust/{e4-allowed-signers,reviewer-policy.json} .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_reviewer_policy.py && git commit -m "policy: trust ticket 23 acceptance reviewer"`

### Task 26: 先固定 Ticket 23 policy commit

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/profiles/ticket-23.json`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted-registry.json`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trust/revocations.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile_v2.py`

**Interfaces:**
- Consumes: Tasks 17–25 的真实 toolchain/platform/supervisor/source/dependency/E1/E4/public-policy digests。
- Produces: 固定 `TICKET23_POLICY_COMMIT`；E1/E2/E4 verifier、四 suites、C01–C03 criteria、v1 typed revocation。

- [ ] **Step 1:** 写 only-C01–C03、四 pytest、九 Vitest、same source/locks/digests、E1+E2+E4 minimum、缺 E4 阻塞 tests。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile_v2.py`；预期 RED。
- [ ] **Step 3:** 写全部真实 digest，禁止占位/fixture digest；撤销 v1 pointer/artifact，不改 receipt。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile_v2.py && git diff --exit-code HEAD -- .scratch/unified-remix-canvas-generation/acceptance/matrix-contract.json`；预期 GREEN 且 contract 未改。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/{profiles/ticket-23.json,trusted-registry.json,trust/revocations.json,test_ticket23_trusted_profile_v2.py} && git commit -m "policy: freeze ticket 23 trusted evidence profile" && git rev-parse HEAD`

### Task 27: 从固定 policy commit collect/register

**Files:**
- Create generated immutable objects: `.scratch/unified-remix-canvas-generation/acceptance/evidence/sha256/<aa>/<digest>`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/evidence-catalog.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/audit/ticket-23-trusted-evidence.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_collect_register.py`

**Interfaces:**
- Consumes: Task 26 exact commit/snapshot；仓库外私钥产生的规范 E4 signature/attachment。
- Produces: E1 两个、E2 四个、E4 一个 artifacts/pointers；contract 不变。

- [ ] **Step 1:** 写 wrong-policy/snapshot-race/missing-E4/source-dependency-mismatch 零 catalog 写、register 不晋升 tests。
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

预期六次均输出“已登记，不代表通过”，contract digest 不变。

- [ ] **Step 3:** 运行 `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py prepare-e4 --profile ticket-23/v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --review-input "$E4_REVIEW_INPUT_FILE" --output "$E4_ATTESTATION_FILE"`；审核者只在仓库外运行 `ssh-keygen -Y sign -n trusted-acceptance-e4 -f "$E4_PRIVATE_KEY_FILE" "$E4_ATTESTATION_FILE"`；随后运行 `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect-e4 --profile ticket-23/v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --attestation "$E4_ATTESTATION_FILE" --signature "$E4_ATTESTATION_FILE.sig" --attachment "$E4_OPERATION_RECORD_FILE"`，将返回值保存为 `E4_REVIEW_ARTIFACT`，再运行 `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py register --profile ticket-23/v1 --verifier e4.ticket23.canvas-review.v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --artifact "$E4_REVIEW_ARTIFACT"`。缺真实签名即停止，禁止测试 key 替代。
- [ ] **Step 4:** audit 记录 policy/snapshot/subject/toolchain/dependency/source/supervisor/fingerprint/七 artifacts+pointers；contract 前后 sha256 相同。
- [ ] **Step 5:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_collect_register.py && git diff --exit-code HEAD -- .scratch/unified-remix-canvas-generation/acceptance/matrix-contract.json`；预期 GREEN/contract unchanged；再执行 `git add .scratch/unified-remix-canvas-generation/acceptance/{evidence,evidence-catalog.json,audit/ticket-23-trusted-evidence.json,test_ticket23_collect_register.py} && git commit -m "evidence: register ticket 23 trusted replay"`。

### Task 28: 晋升 C01–C03 并验证失效重对账

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_promotion.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/matrix-contract.json`
- Modify: `.scratch/unified-remix-canvas-generation/issues/23-align-toonflow-canvas-shell-and-persistence.md`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/README.md`

**Interfaces:**
- Consumes: Task 27 七个有效 pointers、Task 16 workflow。
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
| 规格 1–5、operation/snapshot、A-before-B、零写入 | 1、4、15–16 |
| 6.1 suite/selection/entrypoint | 1、14、18–19、26 |
| 6.2 archive/toolchain/security/platform | 5–6、17 |
| 6.3 Python/npm repository | 7–8、18–19、22 |
| 6.4 receipt 与唯一 scanner | 2–3、15 |
| 7 barrier/snapshot race | 1、4、16、27 |
| 8.1–8.2 product source/sanitized Git | 1、9–10、14 |
| 8.3 containment | 12–13、20 |
| 8.4 redaction | 2、19 |
| 8.5 reporter | 11、19、22、26 |
| 9 workflow/atomicity | 15–16、26–28 |
| 10 exact errors | 1、17–20 |
| 11 real fixtures | 17 toolchain/platform、18 Python、19 npm/reporter、20 containment |
| 11.6 Ticket 23 | 21–22、26 |
| 13 E1/E4/engines | 23–28 |
| 14 migration/history | 1、26 |
| Critical 1 | 23–25、27–28 |
| Critical 2 | 1、9–10、14、26 |
| Critical 3 | 12–13、20 |
| Critical 4 | 22 |
| Critical 5 | 26 policy commit → 27 evidence commit |
| Important 1–4 | 2–3；1/4/16；17–20；5/22 |

### 自包含接口、依赖拓扑与真实命令

- 主链：1→2→3→4→5→6→7→8→9→10→11→12（独立批准门）→13→14→15→16。
- 真实基础验收：16→17→18→19→20；任一未通过，不得写 production profile。
- Ticket 23：20→21 product commit→22 product dependencies→23 E4 code→24 E1 refs→25 reviewer policy→26 policy commit→27 collect/register→28 promote/reconcile。
- `ProductRepositorySourceV1` 在 Task 1 定义、Task 9 验证、Tasks 10/14 只消费 verified handle、Tasks 15/26 绑定；无裸路径旁路。
- `AcceptanceSnapshotV1` 在 Task 1 构造，Tasks 4/16/27 前后复核；全部冻结输入都有字段和 canonical digest。
- containment 六方法、消息字段、序号/摘要链、权限、binary identity 与唯一 kernel mechanism 在 Task 12 冻结；Task 13 不能重新选型。
- Task 2 是唯一 scanner owner；receipt/barrier/collect 只调用该接口，无依赖循环。
- zstandard 同步、Ticket 23 npm ci/uv venv+sync、lock 前后摘要、Cargo/Linux、真实 pytest/Vitest 均给出版本、输入与预期；禁止临时宿主安装。
- Review Focus 五项分别由 Tasks 4/16、2/19、13/20、18/19、26–28 持有，编号无漂移。
- 28 个 Task 均为单独实现会话、单独 commit、单独新审查会话；Task 12、26、27 是不可跳过的硬门。

## 执行与批准状态

- E2 v2 书面规格已批准；本修订计划等待用户复审。
- 执行方式固定为 subagent-driven：逐 Task 新实现会话 + 新独立审查会话，前一 Task 通过才进入下一 Task，最后全分支独立审查。
- 本次规划会话不授权实现或启动任何 Task；复审通过后从 Task 1 开始，不恢复旧 Task 5，也不跳过 Task 12、26、27。
