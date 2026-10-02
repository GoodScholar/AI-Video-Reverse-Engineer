# E2 v2 可信重放协议 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用内容寻址工具链、操作级 artifact barrier、不可逃逸 containment、确定性离线依赖视图和 v2 receipt，替换已阻塞的 E2 v1 重放路径，并让 Ticket 23 的真实 E2 profile 能在已登记受支持平台上重新采集和验证。

**Architecture:** 保留既有六字段 pointer、catalog/register 权限和 E1/E3/E4 边界，把 E2 拆为“阶段 A 全操作只读预检”和“阶段 B 可信重放”两个门。阶段 B 只从规范 tar+zstd 工具链与依赖仓库构造 sanitized Git checkout，所有 setup/test invocation 进入独立的 `ReplayContainmentV1` leaf，stdout/stderr 先流式脱敏再截断，report 只产生内存事实；只有成功 `collect` 可在完整清理后原子写一份 `trusted-e2-receipt/v2`，验证路径永不写 CAS。

**Tech Stack:** Python 3.13、pytest 8.3.5、pytest-json-report 1.5.0、Vitest 3.0.8、Node 24.20.0、npm 11.19.0、Git 2.53.0、uv 0.11.15、规范 POSIX ustar + 单帧 zstd、Linux cgroup v2/clone3、Rust（Linux containment supervisor，锁定 Cargo.lock）、现有 SHA-256 CAS 与 JSON 验收资产。

**Spec:** `docs/superpowers/specs/2026-10-02-e2-v2-trusted-replay-design.md`

## Global Constraints

- 本计划是 E2 v2 的唯一实施计划；`docs/superpowers/plans/2026-10-02-trusted-acceptance-evidence-bridge.md` 保留为历史计划，不覆写、不继续其旧 Task 5/6/10/11 的 E2 步骤。
- 旧 Task 5 始终为 `blocked / superseded`，旧 Task 6 始终为未开始；不得改写五轮修复、230 tests 或独立审查历史。
- `trusted-e2-suite/v2`、`trusted-e2-receipt/v2`、`trusted-e2-toolchain-tar-zstd/v1`、`trusted-e2-dependency-repository/v1` 均拒绝未知/缺失/重复字段；v1 suite/receipt 不自动迁移或猜测升级。
- 一次 `validate`、`promote` 或 `reconcile` 的全部 E2 requirement 必须先通过同一阶段 A barrier；阶段 A 失败时 `spawnCount=checkoutCount=casWrites=0`，catalog/contract/trust 输入字节不变。
- `E2SuiteVerifier.verify` 与正式 `validate/promote/reconcile` 的验证阶段不得调用 `ContentAddressedStore.put`；只有成功 `collect` 可在 replay、report、redaction、write-set、exec census、leaf/session empty proof 全部通过后执行一次原子 `put-if-absent`。
- 工具链只接受内容地址 `trust/toolchains/sha256/<aa>/<digest>`；依赖仓库只接受 `trust/dependencies/sha256/<aa>/<digest>`；运行时禁止主机 `PATH`、shell、`/usr/bin/env`、live download 或系统同名工具回退。
- 工具链容器固定为带 checksum 的单帧 zstd + 规范 POSIX ustar：manifest 必须是首个 regular member，保留虚拟父目录不得作为 member，payload 显式父目录先行并按 UTF-8 path bytes 排序；预算按规格 6.2/6.3 原值执行。
- 工具链预算固定为：压缩包 1 GiB、payload 100,000 members、单 regular file 256 MiB、展开总量 4 GiB、展开比 100:1；依赖容器固定为 1 GiB/100,000 members/20,000 packages、单 archive 256 MiB、blob 总量 2 GiB、consumer view 200,000 entries 与 2 GiB logical bytes、安装树 4 GiB、展开比 100:1。
- 工具版本固定：uv `0.11.15`（commit `3cffe97c2e48c9e49422c738da3af95919dd0bf5`）、Git `2.53.0`、Node `24.20.0`、npm `11.19.0`、pytest `8.3.5`、pytest-json-report `1.5.0`、Vitest `3.0.8`；相同 SemVer 但 build identity/digest 不同不得放行。
- Python setup 恰为包内 uv 的 `venv` 后 `pip sync` 两次 invocation；只消费 exact-pin + SHA-256 requirements lock 和 wheel-only flat view，禁止 project/workspace/editable/sdist/source/build backend，Ticket 23 路径固定为 `backend/.venv` 与 `backend/requirements.lock`。
- Python setup 环境固定为 `UV_OFFLINE=1`、`PIP_NO_INDEX=1`、`UV_PYTHON_DOWNLOADS=never`；`@toolchain/uv-empty-config` 必须是零字节 `config/uv-empty.toml`，SHA-256 固定为 `sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855`。
- uv interpreter probe 的规范化 query-script digest 固定为 `sha256:dcd303ccdd0d36553eb514ac50462a23e69330b09f850af091d961e87411c370`，argv digest 固定为 `sha256:31007ef393f4db03a5a488a04a53bde0d7f8188c7e2375941df8e2ba13cb8382`，environment digest 固定为 `sha256:a14ece45d0cc6ff0a970dd6c2a81e1212da2d9d492108427031cfaefb4894c38`；只允许 checkout-root cwd、包内 CPython、`-I -B -c` 与独占空 cache 下受限随机目录。
- npm setup 恰为 `npm ci --ignore-scripts --offline --replace-registry-host=never --cache=@dependency-repository/npm`；由包内 Node 直接加载验证后的 `vitest.mjs`，不得经 package script、`.bin` 或宿主模块树。
- Git、receipt、machine report、toolchain manifest 与所有控制元数据必须通过 `BoundedStableReader`；receipt/report/manifest 各 1 MiB，Git pointer/ref 4 KiB、config 256 KiB、packed-refs 8 MiB、Git 直接元数据聚合 16 MiB，读取前后 inode/mode/size/mtime 必须一致。
- stdout/stderr 每条未决 record 最多 64 KiB；持久化安全摘要每 invocation 合计 32 KiB。`StreamingRedactor/v1` 必须先脱敏并 residual scan，再截断；缓冲满后仍消费到真实 EOF。
- 每个 setup/test invocation 必须进入独立 `ReplayContainmentV1` leaf；PGID 只允许辅助发信号，不能作为 membership 或 empty proof。正式平台首序只实现并验收 `linux-cgroup-v2/v1`；`vm-cgroup-v1` 先保留严格适配接口，只有真实 guest/cgroup empty proof 接入后才可注册。
- 每个 leaf 的 exec census 最多 4,096 条、write-related syscall census 最多 200,000 条；任一事件丢失、观察器失效或超限均 fail closed，setup 许可根外任一写尝试或 protected tree bytes 变化返回 `E2_SETUP_FAILED`。
- 当前 macOS arm64 本机不能注册 native PGID profile，也不假设天然满足 Linux cgroup v2；没有受信一次性 VM profile 时正式 E2 返回 `E2_CONTAINMENT_UNAVAILABLE`。
- 先完成并独立验收一个具备不可逃逸 containment、exec census、write-set proof 和精确平台 measurement 的 Linux 支持平台，再允许真实 toolchain/package/profile 数据进入 registry；其他平台继续阻塞。
- receipt 只持久化 registry 拥有的预期测试身份；Vitest 的未匹配 skipped assertion 只在内存参与 count 闭合，任意 report node ID、原始 report、完整环境、宿主路径或秘密不得进入 receipt/facts。
- 138 项冻结分母、四种状态、空不适用基线、E1/E3/E4 责任、Ticket 23 只拥有 C01–C03、E4 独立签名和三引擎边界保持不变。
- 实施采用已批准的 subagent-driven 顺序：每个 Task 使用新的实现会话；提交后使用新的独立审查会话；只有 Critical/Important 清零且该 Task 的命令通过，才进入下一 Task。不得并行实现共享接口任务。
- 每项实现前先读取任务列出的固定上游实际源码和许可证，只复制许可证允许且与当前接口一致的部分；在现有 SDD ledger 记录源文件、版本、许可、复用/适配范围，不另建重复研究文档。

## Review Focus

- 最后一个 selected pointer 损坏、存在两个 active pointer 或 barrier 中途 catalog 变化时，整个操作必须零 spawn、零 checkout、零 CAS/contract 写；Task 3、Task 13 的 `test_barrier_rejects_last_invalid_pointer_before_any_replay` 与并发快照测试固定此行为。
- 输出达到 32 KiB 后才出现跨 chunk token、非法 UTF-8、未闭合 quote/JSON 或 agent socket 标点时仍须消费到 EOF 并返回 `OUTPUT_REDACTION_UNSAFE`；Task 8 的全 split-point 与 post-limit 测试固定此行为。
- leader exit 0/非零/timeout 后 double-fork + `setsid()` + `setpgid()` + 关闭 stdio 的后代仍须被 containment leaf 捕获和清空；Task 9 的真实 Linux integration fixture 禁止用 PGID 空替代 cgroup/VM empty proof。
- 工具链/依赖 archive 的 manifest 顺序、父目录、压缩预算、平台 build identity、uv security review、额外 blob/package 或 PATH 投毒任一异常都必须在 setup/test spawn 前阻塞；Tasks 4–6、14 的 fixture 固定这些失败码。
- pytest 零值字段省略、deselected 闭合与 Vitest 同文件 skipped 行必须兼容真实 reporter，但任何 non-expected executed、未锚定正则、source/build backend、live registry access 或 derived entrypoint 替换都必须 fail closed；Tasks 10–12、14 固定此行为。

---

## 文件职责与稳定接口

| 文件 | 单一职责 |
| --- | --- |
| `acceptance/trusted_evidence/e2_policy.py` | `trusted-e2-suite/v2`、invocation、selection、report、derived entrypoint、containment profile 的不可变类型与严格 loader。 |
| `acceptance/trusted_evidence/registry.py` | 顶层 registry/profile/revocation 装配；E2 只接受 v2，保留 E1 loader，不承载 replay 算法。 |
| `acceptance/trusted_evidence/errors.py` | v2 全部稳定错误码及失败/阻塞映射。 |
| `acceptance/trusted_evidence/bounded_io.py` | `O_NOFOLLOW`、前后 stat 一致、单文件与聚合预算的 `BoundedStableReader`。 |
| `acceptance/trusted_evidence/receipts.py` | `TrustedE2ReceiptV2` 严格解析、规范编码、最小身份和 residual-secret 校验。 |
| `acceptance/trusted_evidence/storage.py` | CAS 派生路径、bounded read、原子 `put-if-absent` 和 catalog 持久化；不解释 E2 receipt 语义。 |
| `acceptance/trusted_evidence/artifact_barrier.py` | 冻结 requirement/candidate/active set、唯一 pointer 选择、全部 receipt 预检和 gate 前快照复核。 |
| `acceptance/trusted_evidence/canonical_archive.py` | toolchain/dependency 共用的单帧 zstd + canonical ustar 解码、排序、路径、权限与预算验证。 |
| `acceptance/trusted_evidence/toolchain.py` | 工具链 manifest、entrypoint/closure/dynamic dependency、安全公告、uv build identity 与只读展开。 |
| `acceptance/trusted_evidence/platform_identity.py` | `PlatformMeasurementV1`、numeric kernel predicate、ABI/loader/libc/library exact comparison。 |
| `acceptance/trusted_evidence/dependency_repository.py` | dependency manifest、package/lock mapping、安装树摘要与共享预算。 |
| `acceptance/trusted_evidence/python_dependencies.py` | requirements 子集、wheel-only 校验、deterministic find-links view、uv probe/write-set 规则。 |
| `acceptance/trusted_evidence/npm_dependencies.py` | package-lock v3 精确映射、deterministic cacache content-v2/index-v5 view。 |
| `acceptance/trusted_evidence/git_subject.py` | 有界原始 Git 布局核验、config/ref/object allow/deny、sanitized bare view 与固定 commit checkout。 |
| `acceptance/trusted_evidence/redaction.py` | `StreamingRedactor/v1` 状态机、共享 lexer/residual scan 与先脱敏后截断缓冲。 |
| `acceptance/trusted_evidence/reporters.py` | pytest-json-report 1.5.0 与 Vitest 3.0.8 report/selection/count/exit 闭合。 |
| `acceptance/trusted_evidence/containment/base.py` | `ReplayContainmentV1` 协议、proof/exec/write census 数据类型和 backend registry。 |
| `acceptance/trusted_evidence/containment/linux_cgroup_v2.py` | Linux profile/measurement/supervisor 协议适配与 cgroup empty/destroyed proof 验证。 |
| `acceptance/trusted_evidence/containment/vm_cgroup.py` | 一次性 VM 适配接口；无真实 guest proof 时固定 unavailable，不做 PGID 降级。 |
| `acceptance/trusted_evidence/containment/linux-supervisor/` | Rust native supervisor：clone3 into cgroup、namespace/capability 隔离、exec/write census、cgroup.kill。 |
| `acceptance/trusted_evidence/replay.py` | `TrustedReplaySession` 顺序编排；只产生内存 `ReplayResult`，不读取 pointer、不写 CAS。 |
| `acceptance/trusted_evidence/verifiers/e2_suite.py` | `E2SuiteVerifier`/`E2Collector` 薄门面；验证与采集共享 replay，不保留 v1 runner。 |
| `acceptance/trusted_evidence/evaluation.py` | 逐 profile/item 聚合，并在阶段 B 前调用全操作 barrier。 |
| `acceptance/trusted_evidence/projection.py` | contract 唯一 writer、最终快照 CAS、flush/fsync/atomic replace。 |
| `acceptance/trusted_evidence/collection.py` | `collect` 成功后构造并原子写 v2 receipt；失败零 CAS。 |
| `acceptance/trusted_evidence/runtime.py` | 装配 registry/store/barrier/backend/verifier/evaluator，不持有业务状态。 |
| `acceptance/evidence_bridge.py` | `collect/register/promote/reconcile` 薄 CLI；`register` 不晋升。 |

任务间统一使用以下接口和命名：

```python
class BoundedStableReader:
    def read(self, path: Path, *, max_bytes: int, too_large_code: str) -> StableRead: ...

class ReplayContainmentV1(Protocol):
    def create_session(self, profile: ContainmentProfileV1) -> SessionRealm: ...

class TrustedReplaySession:
    def replay(self, suite: E2SuiteDefinitionV2, subject_ref: str, context: VerificationContext) -> ReplayResult: ...

class E2ArtifactBarrier:
    def preflight(self, operation: VerificationOperation, snapshot: AcceptanceSnapshot) -> PrevalidatedE2Batch: ...

class E2SuiteVerifier:
    def verify_prevalidated(self, item: PrevalidatedE2Pointer, context: VerificationContext) -> VerificationResult: ...

class E2Collector:
    def collect(self, suite: E2SuiteDefinitionV2, subject_ref: str, context: VerificationContext) -> CollectedArtifact: ...
```

### Task 1: 冻结 E2 v2 policy、revocation 与错误合同

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/e2_policy.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/registry.py:17-258,568-617,766-803`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/errors.py:6-51`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trust/revocations.json:1-6`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_registry.py:28-45,234-700,745-766`

**Interfaces:**
- Consumes: 规格 6.1、7、10、14；既有 `TrustedRegistry`、`AcceptanceProfile`、六字段 `EvidencePointer`。
- Produces: `E2SuiteDefinitionV2.from_policy(root, verifier_id, raw)`、`SetupInvocationV1`、`TestInvocationV1`、`TestSelectionV1`、`ReportDefinitionV1`、`DerivedEntrypointV1`、`ContainmentProfileV1`；`RevocationSet.pointer_ids/artifact_refs`；完整 v2 错误码。

- [ ] **Step 1: 读取固定参考源码并记录复用边界**

读取现有 `registry.py`/测试以及 uv `0.11.15` 的 `crates/uv-cli/src/version.rs`、`crates/uv-python/src/interpreter.rs`（Apache-2.0 OR MIT）；在 SDD ledger 记录仅复用真实 argv/identity 语义，不复制实现。

- [ ] **Step 2: 写 v2 schema 与迁移墙失败测试**

```python
def test_v2_suite_is_deeply_immutable_and_closes_every_invocation_field(): ...
def test_v2_loader_derives_anchored_vitest_pattern_from_exact_name(): ...
def test_v2_loader_requires_exact_python_and_npm_setup_shapes(): ...
def test_v1_suite_and_receipt_are_not_silently_upgraded(): ...
def test_revocation_supports_typed_pointer_id_and_artifact_ref(): ...
def test_every_v2_error_code_is_stably_blocked_except_test_replay_failed(): ...
```

- [ ] **Step 3: 运行测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_registry.py`

Expected: FAIL，`e2_policy` 不存在且 registry 仍接受 `e2.argv-suite.v1`。

- [ ] **Step 4: 实现最小 v2 typed policy 与拒绝式迁移**

`trusted-registry.json` 的实现类型改为 `e2.trusted-replay.v2`；E2 verifier 必须持有 `suite.schema == "trusted-e2-suite/v2"`。loader 根据 `exactName` 唯一生成 ECMAScript 转义并锚定的 `-t` 值，禁止 registry 自填 regex；Python/npm invocation、token、path、environment、lock/toolchain/dependency refs 按规格逐字段闭合。revocation schema 升为 v2 并仅增加 `pointerIds/artifactRefs`，不改旧 pointer。

- [ ] **Step 5: 运行 policy/registry/models 测试并确认 GREEN**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_registry.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_models.py`

Expected: PASS；生产 registry 仍无真实 E2 suite/profile，v1 fixture 明确被拒绝。

- [ ] **Step 6: 提交 v2 policy 合同**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/e2_policy.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/registry.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/errors.py .scratch/unified-remix-canvas-generation/acceptance/trust/revocations.json .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_registry.py
git commit -m "feat: define E2 v2 replay policy"
```

### Task 2: 建立 bounded stable I/O 与 v2 receipt codec

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/bounded_io.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/receipts.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/storage.py:28-130`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_bounded_io.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_receipt_v2.py`

**Interfaces:**
- Consumes: Task 1 的 `E2SuiteDefinitionV2` 与稳定错误码；既有 `canonical_json_bytes`、CAS 派生路径。
- Produces: `StableRead(data, identity, sha256)`、`BoundedStableReader.read(...)`、`parse_trusted_e2_receipt_v2(raw, suite) -> TrustedE2ReceiptV2`、`ContentAddressedStore.read_bounded(ref, max_bytes, too_large_code)`、`put_if_absent(payload)`。

- [ ] **Step 1: 读取现有 storage/receipt 验证路径并锁定职责**

确认 `ContentAddressedStore.read` 当前无上限、旧 `_receipt_pointer` 在重放后才读 artifact；在 ledger 记录本任务只引入 bounded I/O/codec，不改变 workflow 顺序。

- [ ] **Step 2: 写稳定读取与 receipt 闭包失败测试**

```python
def test_reader_rejects_symlink_non_regular_growth_shrink_inode_and_mtime_changes(): ...
def test_reader_enforces_exact_single_and_aggregate_limits_before_partial_acceptance(): ...
def test_receipt_v2_rejects_duplicate_unknown_noncanonical_and_over_1mib_payloads(): ...
def test_receipt_selected_ids_are_an_ordered_subset_of_registry_expected_ids(): ...
def test_put_if_absent_is_atomic_and_idempotent_without_overwrite(): ...
```

- [ ] **Step 3: 运行测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_bounded_io.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_receipt_v2.py`

Expected: FAIL，bounded reader、v2 receipt 类型和 bounded CAS API 尚不存在。

- [ ] **Step 4: 实现最小稳定读取器与规范 receipt codec**

使用单次 fd、`O_NOFOLLOW`、`fstat` 前后五元组、read-loop 上限和聚合 budget；receipt 解析拒绝重复键、float、原始 report/环境/host path/未知 test ID，保留规格 6.4 的 digest/proof/count 字段。`put_if_absent` 只负责字节原子性，不由 codec 隐式调用。

- [ ] **Step 5: 运行测试并确认 GREEN**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_bounded_io.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_receipt_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_registration.py`

Expected: PASS；既有 register 并发/幂等测试不回归。

- [ ] **Step 6: 提交 bounded I/O 与 receipt codec**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/bounded_io.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/receipts.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/storage.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_bounded_io.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_receipt_v2.py
git commit -m "feat: bound E2 artifacts and receipts"
```

### Task 3: 实现 operation-wide E2 artifact barrier

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/artifact_barrier.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/models.py:169-221`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_artifact_barrier.py`

**Interfaces:**
- Consumes: Task 1 `RevocationSet`/suite policy；Task 2 bounded CAS/receipt codec；catalog/profile/registry/contract snapshots。
- Produces: `RequirementKey(profile_id, subject_ref, item_id, verifier_id)`、`PrevalidatedE2Pointer`、`PrevalidatedE2Batch`、`E2ArtifactBarrier.preflight(operation, snapshot)`；本任务不启动 replay。

- [ ] **Step 1: 写 candidate/active set 与全局 gate 失败测试**

```python
def test_barrier_requires_exactly_one_active_pointer_per_requirement(): ...
def test_barrier_does_not_deduplicate_two_pointers_to_the_same_artifact(): ...
def test_barrier_rejects_last_invalid_pointer_before_any_replay(): ...
def test_barrier_rechecks_catalog_profile_registry_revocation_and_contract_digests(): ...
def test_barrier_failure_has_zero_spawn_checkout_cas_and_contract_writes(): ...
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_artifact_barrier.py`

Expected: FAIL，`artifact_barrier` 与 prevalidated batch 类型不存在。

- [ ] **Step 3: 实现只读阶段 A**

从冻结 snapshot 构造完整 candidate set，再应用 typed revocation 得 active set；每个 requirement 只接受恰一 active pointer，对全部唯一 pointer 做路径/类型/大小/digest/canonical/schema/policy/toolchain/subject/expected-ID 校验，最后立即复核所有 snapshot digest。错误只返回最小类别，不回显 artifact。

- [ ] **Step 4: 运行 barrier 与 receipt 测试并确认 GREEN**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_artifact_barrier.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_receipt_v2.py`

Expected: PASS；所有 barrier 失败 fixture 的 replay spy、temp-root spy 和 CAS put spy 均为 0。

- [ ] **Step 5: 提交 artifact barrier**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/artifact_barrier.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/models.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_artifact_barrier.py
git commit -m "feat: preflight E2 artifacts operation wide"
```

### Task 4: 验证 canonical tar+zstd 容器

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/canonical_archive.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_canonical_archive.py`
- Modify: `backend/requirements.lock:1-11`

**Interfaces:**
- Consumes: Task 2 `BoundedStableReader`；规格 6.2/6.3 共用容器规则。
- Produces: `ArchiveLimits`、`ValidatedArchive`、`CanonicalTarZstdReader.validate_and_extract(package, kind, destination) -> ValidatedArchive`；kind 只为 `toolchain`/`dependency`。

- [ ] **Step 1: 读取固定上游源码与许可证**

读取 `python-zstandard` `0.23.0` 的 frame parameter/decompressor 实现（BSD）和 CPython `tarfile.py` ustar header 解析（PSF）；ledger 记录仅复用格式语义，runtime 依赖精确 pin `zstandard==0.23.0`，不复制上游代码。

- [ ] **Step 2: 写 canonical container RED 测试**

```python
def test_minimal_manifest_first_archive_accepts_virtual_parent_exception(): ...
def test_reserved_parent_missing_duplicate_or_late_parent_and_wrong_order_fail(): ...
def test_rejects_multiframe_no_checksum_dictionary_trailing_pax_gnu_hardlink_and_special_files(): ...
def test_rejects_path_casefold_symlink_escape_mode_header_and_digest_mismatch(): ...
def test_limits_entry_count_single_size_total_size_window_and_ratio_before_write(): ...
```

- [ ] **Step 3: 运行测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_canonical_archive.py`

Expected: FAIL，canonical archive reader 不存在。

- [ ] **Step 4: 实现共享解码器和 no-exec extraction**

toolchain/dependency 分别映射稳定错误码；manifest 先在 1 MiB 上限内解析，再逐 member 对照 `files`，越界 entry 在写出前失败。展开根先 no-exec，完整 digest/tree 校验后才返回 validated handle。

- [ ] **Step 5: 运行 archive 测试并确认 GREEN**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_canonical_archive.py`

Expected: PASS；最小合法 toolchain/dependency archive 均接受，攻击 fixture 无 payload 残留。

- [ ] **Step 6: 提交 canonical container**

```bash
git add backend/requirements.lock .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/canonical_archive.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_canonical_archive.py
git commit -m "feat: validate canonical E2 containers"
```

### Task 5: 验证工具链 closure、安全审核与平台身份

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/toolchain.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/platform_identity.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_toolchain.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_platform_identity.py`

**Interfaces:**
- Consumes: Task 1 suite/profile；Task 4 `ValidatedArchive`；规格 6.2、8.1、11.5。
- Produces: `ToolchainPackage.open(ref) -> ValidatedToolchain`、`PlatformMeasurementV1`、`compare_platform_identity(expected, measured) -> PlatformMatch`、逻辑 entrypoint 到包内绝对 argv prefix 的唯一解析。

- [ ] **Step 1: 读取固定上游源码和公告证据**

读取 uv `0.11.15` 的 `version.rs/build.rs/cache/interpreter.rs`、官方 GHSA-4gg8-gxpx-9rph 与 0.11.x changelog、Git `2.53.0` helper layout、Node/npm CLI 入口；记录 Apache-2.0 OR MIT/GPL-2.0/Node/npm 各自许可证与“只核真实 closure，不复制实现”。

- [ ] **Step 2: 写 toolchain/platform RED 测试**

```python
def test_toolchain_ref_manifest_payload_closures_entrypoints_and_dynamic_dependencies_close(): ...
def test_uv_requires_01115_exact_reported_build_binary_and_security_review_identity(): ...
def test_missing_cpython_stdlib_npm_tree_git_helpers_or_system_library_identity_blocks(): ...
def test_platform_accepts_min_and_max_minus_one_but_rejects_below_min_and_max(): ...
def test_same_kernel_version_or_soname_with_different_build_identity_rejects(): ...
def test_host_path_poisoning_never_runs_a_marker_tool(): ...
```

- [ ] **Step 3: 运行测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_toolchain.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_platform_identity.py`

Expected: FAIL，toolchain/platform modules 不存在。

- [ ] **Step 4: 实现工具链和精确平台比较**

验证 package digest、manifest/payload/closure digests、entrypoint files、ELF/Mach-O dependency allowlist、uv semantic/reported/build identity、安全公告 snapshot/policy；平台比较只用 exact fields + numeric triplet + kernel ABI build digest，不调用主机版本比较器或 PATH 工具。

- [ ] **Step 5: 运行测试并确认 GREEN**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_toolchain.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_platform_identity.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_canonical_archive.py`

Expected: PASS；错误 build/ABI/closure 在任意 setup/test spawn 前返回规格稳定码。

- [ ] **Step 6: 提交工具链与平台身份验证**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/toolchain.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/platform_identity.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_toolchain.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_platform_identity.py
git commit -m "feat: verify trusted E2 toolchains"
```

### Task 6: 实现 wheel-only Python dependency repository 与 find-links view

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/dependency_repository.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/python_dependencies.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_python_dependencies.py`

**Interfaces:**
- Consumes: Tasks 1、2、4、5；规格 6.3/6.3.1、11.1。
- Produces: `DependencyRepository.open(ref, ecosystem)`、`parse_hashed_requirements_lock(read, platform)`、`PythonFindLinksView.build(repository, lock, destination) -> ConsumerView`、package/install tree digests。

- [ ] **Step 1: 读取固定 uv/wheel 源码与格式**

读取 uv `0.11.15` 的 wheel selection、`pip sync`/`--no-build`/`--require-hashes` 和 interpreter probe 源码，以及 PEP 427/503/440；记录只复用真实命令/映射语义。

- [ ] **Step 2: 写 Python repository/view RED 测试**

```python
def test_hashed_requirements_accepts_only_unique_exact_pins_and_fixed_markers(): ...
def test_sdist_zip_source_editable_direct_path_project_and_build_backend_fail_before_uv(): ...
def test_wheel_filename_metadata_tags_hash_and_lock_mapping_are_unique_and_exact(): ...
def test_two_empty_roots_build_byte_identical_flat_views_and_tree_digests(): ...
def test_extra_missing_conflicting_package_blob_mapping_or_install_entry_fails(): ...
```

- [ ] **Step 3: 运行测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_python_dependencies.py`

Expected: FAIL，dependency/Python view modules 不存在。

- [ ] **Step 4: 实现 deterministic wheel-only view**

只从已验证 manifest/blob 纯函数生成 `view/python-find-links/<originalFilename>.whl`；核对每个 package/mapping/archive/install tree/native closure 和 top-level tree，extra/ambiguous/missing 均拒绝；返回 token 对应 flat directory，不调用 uv。

- [ ] **Step 5: 运行测试并确认 GREEN**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_python_dependencies.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_canonical_archive.py`

Expected: PASS；所有可预检 sdist/build/mapping 错误的 uv spawn spy 为 0。

- [ ] **Step 6: 提交 Python dependency view**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/dependency_repository.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/python_dependencies.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_python_dependencies.py
git commit -m "feat: build trusted Python dependency views"
```

### Task 7: 实现 npm cacache offline view

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/npm_dependencies.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_npm_dependencies.py`

**Interfaces:**
- Consumes: Task 6 `DependencyRepository`；Task 5 npm `11.19.0`/cacache `20.0.4` closure；规格 6.3.2、11.2。
- Produces: `NpmCacacheView.build(repository, package_lock, destination) -> ConsumerView`；`validate_npm_lock_mapping(...)`。

- [ ] **Step 1: 读取固定 npm/cacache 源码与许可证**

读取 npm `11.19.0` 的 `npm-cli.js`/`ci` 路径和 cacache `20.0.4` 的 `lib/entry-index.js`、`lib/content/path.js`（ISC）；记录只复制规范 content-v2/index-v5 算法所需常量与字节格式。

- [ ] **Step 2: 写 cacache RED 测试**

```python
def test_canonical_resolved_integrity_cache_key_and_package_lock_v3_fields_close(): ...
def test_generated_content_v2_and_index_v5_bytes_match_exact_tree_digest(): ...
def test_two_empty_roots_build_byte_identical_npm_cache_views(): ...
def test_tampered_line_hash_json_key_sri_size_content_path_or_lock_is_rejected(): ...
def test_extra_package_blob_or_mapping_is_rejected_before_npm_spawn(): ...
```

- [ ] **Step 3: 运行测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_npm_dependencies.py`

Expected: FAIL，npm view builder 不存在。

- [ ] **Step 4: 实现 deterministic cacache view**

按规格固定 SHA-512 content path、SHA-256 bucket、RFC 8785 entry JSON、SHA-1 line checksum、leading LF/no trailing LF；spawn 前重新解析 lower view，COW upper 只供本 invocation bookkeeping，不进入信任输入。

- [ ] **Step 5: 运行测试并确认 GREEN**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_npm_dependencies.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_python_dependencies.py`

Expected: PASS；index/content/lock 任一字节变更在 npm spawn 前返回 `E2_DEPENDENCY_VIEW_INVALID`。

- [ ] **Step 6: 提交 npm offline view**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/npm_dependencies.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_npm_dependencies.py
git commit -m "feat: build trusted npm cache views"
```

### Task 8: 构造 sanitized Git object view 与固定 checkout

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/git_subject.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_git_subject.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e2_suite.py:1-1517`

**Interfaces:**
- Consumes: Task 2 bounded I/O；Task 5 包内 Git entrypoint；规格 8.2。
- Produces: `GitMetadataInspector.inspect(repository_root) -> ValidatedGitSource`、`SanitizedGitView.build(source, commit, toolchain_git, destination) -> FixedCheckout`；旧 `SuiteRunner` 的宿主 Git/PGID 实现删除。

- [ ] **Step 1: 读取 Git 2.53.0 实际源码与许可证**

读取 `config.c`、`refs/packed-backend.c`、`object-file.c`、`packfile.c`、`builtin/index-pack.c`（GPL-2.0）；记录只复用格式与命令语义，不复制 Git 实现。

- [ ] **Step 2: 写 Git 安全视图 RED 测试**

```python
def test_bounded_git_pointer_config_refs_and_aggregate_metadata_limits(): ...
def test_inert_remote_branch_and_codex_metadata_is_parsed_then_discarded(): ...
def test_dangerous_unknown_config_hooks_attributes_alternate_replace_graft_shallow_promisor_fail(): ...
def test_refs_and_pack_layout_reject_symlink_duplicate_sidecar_pair_and_budget_attacks(): ...
def test_toolchain_git_only_reads_sanitized_view_and_checkout_matches_exact_commit(): ...
```

- [ ] **Step 3: 运行测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_git_subject.py`

Expected: FAIL，`git_subject` 不存在；旧 E2 路径仍直接调用宿主 Git。

- [ ] **Step 4: 实现 bounded inspector 与 sanitized bare view**

runner 自有 parser 先检查 config/ref/object layout；受控 pack 交给包内 `git index-pack --strict` 重建 idx，再 `fsck --strict`；sanitized view 只含固定 closure、内部 ref 和常量 config，绝不复制原始 config/hooks/attributes/log/index/remote 元数据。

- [ ] **Step 5: 运行 Git/E2 façade测试并确认 GREEN**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_git_subject.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py`

Expected: PASS；旧 E2 tests 中依赖 host-tool/PGID/v1 receipt 的断言已迁移或删除，新的 façade 不再含 `shutil.which`、`os.killpg` 或 `trusted-e2-receipt/v1`。

- [ ] **Step 6: 提交 sanitized Git view**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/git_subject.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e2_suite.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_git_subject.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py
git commit -m "feat: sanitize E2 Git subjects"
```

### Task 9: 实现 `StreamingRedactor/v1` 与安全截断

**Files:**
- Rewrite: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/redaction.py:1-57`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_streaming_redaction.py`

**Interfaces:**
- Consumes: 规格 8.4、11.4、12；不消费 subprocess 或 receipt。
- Produces: `scan_sensitive_lexemes_v1(record) -> ScanResult`、`StreamingRedactorV1.feed(chunk) -> bytes`、`finish() -> bytes`、`SafeOutputBuffer.accept(safe_bytes)`、`SafeOutputSummary`。

- [ ] **Step 1: 写 chunk-independent lexer/state-machine RED 测试**

```python
def test_every_split_point_matches_single_chunk_for_all_supported_secret_forms(): ...
def test_agent_socket_suffix_and_file_url_boundaries_preserve_exact_punctuation(): ...
def test_assignment_json_header_url_path_raw_token_and_private_key_share_residual_scanner(): ...
def test_trailing_escape_quote_percent_json_structure_and_utf8_fail_closed(): ...
def test_64k_record_limit_and_32k_post_redaction_truncation_continue_to_eof(): ...
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_streaming_redaction.py`

Expected: FAIL，旧 regex API 无增量状态、split invariance 或 post-limit consumption。

- [ ] **Step 3: 实现固定状态机和共享 scanner**

严格实现 `UTF8_PENDING/RECORD_PENDING/PRIVATE_KEY_BLOCK/JSON_VALUE_PENDING/SANITIZE_RECORD/EOF`；主替换与 residual scan 调同一 `scan_sensitive_lexemes_v1`；只有安全 bytes 进入 32 KiB buffer，满后继续 feed/finish 并追加固定 `...[truncated-after-redaction]`。

- [ ] **Step 4: 运行 redaction 测试并确认 GREEN**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_streaming_redaction.py`

Expected: PASS；单 chunk、每 byte 和每个相邻 split point 输出/错误码完全相同。

- [ ] **Step 5: 提交流式脱敏器**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/redaction.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_streaming_redaction.py
git commit -m "feat: redact E2 output before truncation"
```

### Task 10: 实现 containment 协议并首先支持 Linux cgroup v2

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/__init__.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/base.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux_cgroup_v2.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/vm_cgroup.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux-supervisor/Cargo.toml`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux-supervisor/Cargo.lock`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux-supervisor/src/main.rs`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_linux.py`

**Interfaces:**
- Consumes: Task 1 profile；Task 5 `PlatformMeasurementV1`；Task 9 streaming sink；规格 8.3、11.4。
- Produces: `ReplayContainmentV1`、`SessionRealm`、`InvocationLeaf`、`ExecCensusProofV1`、`WriteSetProofV1`、`EmptyProofV1`；`LinuxCgroupV2Containment`；未配置 guest 的 `VmCgroupContainment` 固定 unavailable。

- [ ] **Step 1: 读取 Linux cgroup/clone3/seccomp 实际源码与许可**

读取固定 Linux kernel 源的 `kernel/cgroup/cgroup.c`、`kernel/fork.c`、`kernel/seccomp.c` 和 UAPI headers（GPL-2.0 WITH Linux-syscall-note）；ledger 记录 supervisor 仅适配这些内核接口，不复制 kernel 实现。

- [ ] **Step 2: 写协议与 fail-closed RED 测试**

```python
def test_backend_registry_rejects_digest_capability_platform_and_profile_mismatch(): ...
def test_native_macos_or_pgid_only_backend_returns_containment_unavailable(): ...
def test_exec_and_write_census_loss_overflow_or_unknown_child_fails_closed(): ...
def test_leaf_requires_kernel_empty_and_destroyed_proofs_not_process_group_absence(): ...
```

- [ ] **Step 3: 写真实 Linux escape fixture RED 测试**

```python
@pytest.mark.e2_linux_integration
def test_linux_leaf_reaps_double_fork_setsid_setpgid_closed_stdio_for_all_leader_outcomes(): ...

@pytest.mark.e2_linux_integration
def test_linux_leaf_captures_exec_and_write_attempts_and_proves_protected_trees_unchanged(): ...
```

- [ ] **Step 4: 运行单元测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment.py`

Expected: FAIL，containment 协议/backend 不存在。

- [ ] **Step 5: 实现 supervisor 与 Linux backend**

supervisor 使用 `clone3(CLONE_INTO_CGROUP)` 在首条不受信指令前入 leaf，隔离 user/mount/PID/network/IPC namespace、移除 capability/可写 cgroupfs/宿主 sockets，以内核观察记录 bounded exec/write census；所有 leader outcome 都执行 `cgroup.kill`，验证 `cgroup.events populated 0` 后删除 leaf/session。控制协议使用独立继承 fd，不与 child stdout/stderr 混流。

- [ ] **Step 6: 运行单元与受支持 Linux 集成测试**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment.py`

Expected: PASS；macOS/无 profile fixture 稳定返回 `E2_CONTAINMENT_UNAVAILABLE`。

Run on the registered Linux executor: `cargo test --locked --manifest-path .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux-supervisor/Cargo.toml && PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_linux.py`

Expected: PASS；每个 escape fixture 均有 cgroup membership、terminate-all、empty 和 destroyed proof，exec/write census 不丢事件。

- [ ] **Step 7: 提交 containment 平台**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_linux.py
git commit -m "feat: contain E2 replay with cgroup v2"
```

### Task 11: 闭合真实 pytest/Vitest reporter 与 selection

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/reporters.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_reporters.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/reporters/pytest-pass.json:1-20`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/reporters/vitest-pass.json:1-34`

**Interfaces:**
- Consumes: Task 1 selection/report types；Task 2 bounded report read；规格 8.5。
- Produces: `parse_pytest_json_v1(read, selection, process_exit) -> ReportFacts`、`parse_vitest_json_v1(read, selection, process_exit) -> ReportFacts`；facts 只含 expected IDs 与闭合 counts。

- [ ] **Step 1: 读取 reporter 固定源码与许可证**

读取 pytest-json-report `1.5.0` 的 `pytest_jsonreport/plugin.py`（MIT）和 Vitest `3.0.8` 的 JSON reporter/CLI selection 源码（MIT）；记录真实零字段省略、deselected 和 skipped 行语义。

- [ ] **Step 2: 写 reporter RED 测试**

```python
def test_pytest_accepts_omitted_zero_counts_and_closes_collected_deselected_total_rows(): ...
def test_pytest_rejects_duplicate_extra_missing_failed_and_exit_mismatch(): ...
def test_vitest_accepts_one_expected_pass_and_same_file_nonexpected_skipped(): ...
def test_vitest_rejects_nonexpected_execution_expected_skip_wrong_file_or_counts(): ...
def test_vitest_policy_requires_file_plus_escaped_anchored_exact_name(): ...
def test_report_over_limit_or_changed_during_read_uses_stable_error_code(): ...
```

- [ ] **Step 3: 运行测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_reporters.py`

Expected: FAIL，reporter module 不存在，旧 fixture 不含真实 Vitest skipped 行。

- [ ] **Step 4: 实现严格 parser/selection closure**

pytest 零值 outcome 缺失按 0，`deselected` 缺失只在 `collected==total` 接受；Vitest 对所有 assertion/suite/count/status 闭合，只有 non-expected `skipped` 可忽略且不持久化；schema/count 错误为 `REPORT_SCHEMA_INVALID`，selection 错误为 `TEST_SELECTION_MISSING`。

- [ ] **Step 5: 运行测试并确认 GREEN**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_reporters.py`

Expected: PASS；fixture 与真实 1.5.0/3.0.8 schema 一致。

- [ ] **Step 6: 提交 reporter closure**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/reporters.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_reporters.py .scratch/unified-remix-canvas-generation/acceptance/fixtures/reporters/pytest-pass.json .scratch/unified-remix-canvas-generation/acceptance/fixtures/reporters/vitest-pass.json
git commit -m "feat: parse real E2 machine reports"
```

### Task 12: 编排 `TrustedReplaySession`、离线 setup 与 derived entrypoint

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/replay.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e2_suite.py:1-200`
- Rewrite: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py:1-1415`

**Interfaces:**
- Consumes: Tasks 1、5–11 的 suite/toolchain/dependency/Git/redaction/containment/reporter 接口。
- Produces: `TrustedReplaySession.replay(...) -> ReplayResult`；`InvocationResultV2`；setup/test 各自 leaf；本模块不读 pointer、不写 CAS。

- [ ] **Step 1: 写 replay 顺序、Python/npm setup 与零写入 RED 测试**

```python
def test_replay_uses_only_package_entrypoints_and_never_host_path_or_shell(): ...
def test_python_setup_is_exact_uv_venv_then_pip_sync_with_distinct_empty_caches(): ...
def test_python_probe_exec_census_matches_exact_identity_argv_cwd_env_and_zero_backend_count(): ...
def test_npm_setup_is_exact_offline_ci_and_test_loads_verified_vitest_module_with_node(): ...
def test_derived_entrypoint_replacement_type_digest_interpreter_or_tree_escape_blocks_test(): ...
def test_live_dns_socket_registry_attempt_and_outside_write_block_even_on_exit_zero(): ...
def test_every_outcome_cleans_leaf_and_session_and_replay_never_calls_cas_put(): ...
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py`

Expected: FAIL，`TrustedReplaySession` 不存在，旧 v1 host-tool runner 测试已被 v2 合同替换。

- [ ] **Step 3: 实现严格 A-after-gate 阶段 B 顺序**

验证/展开工具链 → 测量平台/创建 session → 构造 sanitized checkout → 构造 dependency views → 每 setup 独立 leaf → 重建/核对安装树和 derived entrypoint → 每 test 独立 leaf → bounded report/parser → session destroyed proof。Python lock before/after bytes/digest 相同；cache 每 leaf 独占空根；network deny、exec/write census、protected tree digest 全部闭合。

- [ ] **Step 4: 运行 replay 及其组件测试并确认 GREEN**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_{toolchain,python_dependencies,npm_dependencies,git_subject,streaming_redaction,containment,reporters}.py`

Expected: PASS；任一失败 fixture 无 receipt/CAS 写，所有 leaf/session 都有 verified-empty/destroyed proof。

- [ ] **Step 5: 提交可信 replay session**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/replay.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e2_suite.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py
git commit -m "feat: replay E2 suites from trusted closures"
```

### Task 13: 分离 collect/verify 并生成最小 v2 receipt

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/collection.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/receipts.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e2_suite.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_collection.py`

**Interfaces:**
- Consumes: Task 2 receipt codec/CAS；Task 3 `PrevalidatedE2Pointer`；Task 12 replay result。
- Produces: `E2Collector.collect(...) -> CollectedArtifact`、`E2SuiteVerifier.verify_prevalidated(...) -> VerificationResult`；collect/verify 共用 `receipt_from_replay`，只有 collector 注入 store writer。

- [ ] **Step 1: 写 collect/verify 原子性 RED 测试**

```python
def test_collect_writes_exactly_one_v2_receipt_only_after_replay_and_cleanup_success(): ...
def test_failed_timeout_report_redaction_write_or_cleanup_produces_zero_objects(): ...
def test_verify_prevalidated_replays_in_memory_and_cannot_resolve_a_cas_writer(): ...
def test_receipt_binds_policy_toolchain_platform_locks_views_entries_reports_and_proofs(): ...
def test_receipt_and_failed_facts_never_persist_unexpected_test_ids_or_secret_paths(): ...
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_collection.py`

Expected: FAIL，collector 与 prevalidated verifier API 不存在。

- [ ] **Step 3: 实现采集后写入与验证零写入**

collector 在内存构造规范 receipt、执行最终 shared residual scan，再 `put_if_absent` 一次；verifier 比对预检 receipt 不变量与本轮 replay facts，不比较时长/安全摘要逐字节，不写任何对象。删除所有 v1 receipt 生成/读取分支。

- [ ] **Step 4: 运行测试并确认 GREEN**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_collection.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_receipt_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py`

Expected: PASS；verify CAS put spy 始终 0，collect 只有成功路径为 1。

- [ ] **Step 5: 提交 v2 collection/verifier**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/collection.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/receipts.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e2_suite.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_collection.py
git commit -m "feat: collect and verify E2 v2 receipts"
```

### Task 14: 接入 validate/promote/reconcile 与唯一原子 projection writer

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/evaluation.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/projection.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/runtime.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/registration.py:20-49`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/validate_acceptance.py:1-515`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_workflow_v2.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/test_acceptance_contract.py`

**Interfaces:**
- Consumes: Task 3 barrier；Task 13 collector/verifier；既有 matrix contract/catalog/register。
- Produces: `EvidenceEvaluator.evaluate_prevalidated(batch)`、`PromotionService.promote`、`ReconciliationService.reconcile`、`ContractProjectionWriter.apply`、`build_runtime`；CLI `collect/register/promote/reconcile`。

- [ ] **Step 1: 写 operation atomicity 与命令隔离 RED 测试**

```python
def test_validate_promote_and_reconcile_finish_full_barrier_before_first_e2_replay(): ...
def test_register_never_promotes_and_collect_never_registers_or_writes_contract(): ...
def test_validate_is_byte_for_byte_read_only_and_never_repairs_bad_artifacts(): ...
def test_promote_and_reconcile_recheck_snapshot_before_atomic_contract_replace(): ...
def test_one_blocked_verifier_preserves_per_item_aggregation_without_old_receipt_fallback(): ...
def test_v1_receipts_and_suites_remain_historical_and_cannot_enter_v2_workflow(): ...
```

- [ ] **Step 2: 运行测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_workflow_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_acceptance_contract.py`

Expected: FAIL，evaluation/projection/runtime/CLI 尚不存在，正式 validate 未接 barrier。

- [ ] **Step 3: 实现唯一 workflow 顺序和 writer**

runtime 先冻结完整 snapshot，barrier 全量预检后才按确定顺序 replay；最终再次复核 snapshot，只有 promote/reconcile 调 projection writer。writer 使用同目录 temp + flush/fsync + replace；validate 只报告 stale projection，register 仍只 append pointer。

- [ ] **Step 4: 运行 workflow/contract 测试并确认 GREEN**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_workflow_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_acceptance_contract.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_registration.py`

Expected: PASS；138 项、四状态、空不适用基线不变，正式 production profile 仍未注册。

- [ ] **Step 5: 提交 workflow 集成**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/evaluation.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/projection.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/runtime.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/registration.py .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py .scratch/unified-remix-canvas-generation/acceptance/validate_acceptance.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_workflow_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_acceptance_contract.py
git commit -m "feat: gate E2 promotion with trusted replay"
```

### Task 15: 用真实工具构建并验收最小 E2 v2 fixture

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/product/backend/requirements.lock`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/product/backend/tests/test_fixture.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/product/frontend/package.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/product/frontend/package-lock.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/product/frontend/src/fixture.test.ts`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/build_fixture_packages.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_v2_integration.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/README.md`

**Interfaces:**
- Consumes: Tasks 4–14 全链路；真实 Linux `linux-cgroup-v2/v1` executor；预置且经审核的 Git/uv 0.11.15/CPython/Node/npm bytes 与 wheel/tgz blobs。
- Produces: 最小真实 toolchain/dependency packages、平台 measurement、Python/Vitest replay receipt；这些 fixture 不注册 production profile。

- [ ] **Step 1: 写真实最小 fixture RED 集成测试**

```python
def test_real_container_ordering_and_toolchain_security_identity(): ...
def test_real_uv_wheel_only_setup_and_pytest_json_report_150(): ...
def test_real_npm_cacache_setup_and_vitest_308_exact_name_selection(): ...
def test_real_platform_measurement_and_linux_containment_escape_cleanup(): ...
def test_real_collect_writes_once_while_verify_writes_zero_objects(): ...
```

断言覆盖 canonical container ordering；真实 uv `0.11.15 --version` 和 security snapshot；wheel-only `pytest==8.3.5` + `pytest-json-report==1.5.0`；真实 Vitest `3.0.8` 同文件两测试/一 pass 一 skipped；byte-identical Python/npm views；Linux escape/empty proof；平台版本边界；host PATH poisoning；collect/verify CAS write count。

- [ ] **Step 2: 运行受支持 Linux 集成测试并确认 RED**

Run on the registered Linux executor: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_v2_integration.py`

Expected: FAIL，最小 fixture packages/locks 尚不存在。

- [ ] **Step 3: 构建真实固定 fixture 输入**

Python lock 冻结完整传递 wheel closure、exact pins 和每项 SHA-256；frontend lock 冻结 Vitest 3.0.8 完整 npm closure；builder 只从显式 blob 输入生成 canonical packages 和 manifest/security review，不在 replay 时联网。产品 fixture 同时放置恶意 `pyproject.toml`/workspace/build marker，证明 setup 不解析项目。

- [ ] **Step 4: 运行全套真实 fixture 验收**

Run on the registered Linux executor: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_v2_integration.py`

Expected: PASS；真实 pytest report 允许零字段省略，真实 Vitest 每 invocation 为 1 passed + 1 skipped；receipt 只含 expected ID；所有 failure variants 在规定 spawn 阶段前阻塞。

Run on the current macOS host: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment.py -k unavailable`

Expected: PASS，证明 macOS 未配置 VM 时稳定阻塞而非 PGID 降级。

- [ ] **Step 5: 更新 README 的平台/fixture 证据并提交**

README 记录实际 package digests、supervisor digest、平台 measurement、上游版本/许可证和 Linux 集成命令；不得写“所有平台支持”或“Ticket 23 已通过”。

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2 .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_v2_integration.py .scratch/unified-remix-canvas-generation/acceptance/README.md
git commit -m "test: validate real E2 v2 replay fixtures"
```

### Task 16: 准备 Ticket 23 固定产品主体

**Files:**
- Create in the isolated Ticket 23 product worktree: `frontend/src/CreativeCanvasTicket23Acceptance.test.tsx`
- Modify in the isolated Ticket 23 product worktree: `backend/requirements.lock`
- Verify without modifying unless a test is genuinely absent: `backend/tests/test_creative_canvas.py`
- Verify without modifying unless a test is genuinely absent: `frontend/src/CreativeCanvas.test.tsx`
- Verify without modifying unless a test is genuinely absent: `frontend/src/creativeCanvasApi.test.ts`

**Interfaces:**
- Consumes: Task 15 已通过的真实 fixture 命令形态；负责人准备的、只含 Ticket 23 产品范围的隔离 worktree。
- Produces: 干净的 `TICKET23_PRODUCT_COMMIT`、wheel-only `backend/requirements.lock`、真实 pytest/Vitest node IDs/fullNames；不修改证据桥 registry。

- [ ] **Step 1: 在隔离产品 worktree 先核对源码和现有测试**

完整读取 Ticket 23 画布实现与三个现有测试文件，确认四个 pytest node ID 以及以下真实 fullName 实际存在；不存在时只在该产品 worktree 补测试/依赖，不在证据桥工作树改产品逻辑：

- `首次读取画布期间禁止写入草稿`
- `保存冲突时保留本地草稿并显示重新读取入口`
- `保存中继续编辑会保留草稿并用返回的新 revision 进行下一次保存`
- `从画布目录切换时按 canvasId 加载独立工作区`
- `保留本地草稿后切回会继续标记为未保存`
- `不传 canvasId 时保留旧画布请求 URL 与载荷`
- `目录 CRUD 与所有画布工作区请求都显式携带 canvasId`

- [ ] **Step 2: 运行专用行为测试和真实 reporter 并确认 RED**

Run: `cd frontend && node node_modules/vitest/vitest.mjs run src/CreativeCanvasTicket23Acceptance.test.tsx -t '^独立入口不依赖 Shot 并保留项目上下文$' --reporter=json --outputFile=.ticket23-structure-1.json`

Expected: FAIL，专用 Ticket 23 测试文件尚不存在。

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_creative_canvas.py::test_canvas_document_saves_without_shot_and_returns_project_assets --json-report --json-report-file=.ticket23-report.json`

Expected: FAIL，固定产品 lock 尚未提供 `pytest-json-report==1.5.0`。

- [ ] **Step 3: 写专用 C01/C02 测试、冻结 wheel-only lock 并确认 GREEN**

新增两个测试 `独立入口不依赖 Shot 并保留项目上下文` 与 `Toonflow 主体控件可通过可访问路径打开和关闭`；`backend/requirements.lock` 加入 `pytest-json-report==1.5.0` 及完整 wheel-only hash closure，然后运行以下真实选择。

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_creative_canvas.py::test_canvas_document_saves_without_shot_and_returns_project_assets tests/test_creative_canvas.py::test_canvas_save_rejects_stale_document_revision tests/test_creative_canvas.py::test_canvas_catalog_keeps_legacy_default_and_isolates_canvas_documents tests/test_creative_canvas.py::test_soft_deleted_canvas_keeps_asset_references_and_can_restore --json-report --json-report-file=.ticket23-report.json`

Expected: PASS，4 passed；report 由 pytest-json-report 1.5.0 产生。

Run: `cd frontend && node node_modules/vitest/vitest.mjs run src/CreativeCanvasTicket23Acceptance.test.tsx -t '^独立入口不依赖 Shot 并保留项目上下文$' --reporter=json --outputFile=.ticket23-structure-1.json`

Expected: PASS，expected assertion passed，同文件另一 assertion skipped。

Run: `cd frontend && node node_modules/vitest/vitest.mjs run src/CreativeCanvasTicket23Acceptance.test.tsx -t '^Toonflow 主体控件可通过可访问路径打开和关闭$' --reporter=json --outputFile=.ticket23-structure-2.json`

Expected: PASS，expected assertion passed，同文件另一 assertion skipped。

Run each command separately:

```bash
cd frontend && node node_modules/vitest/vitest.mjs run src/CreativeCanvas.test.tsx -t '^首次读取画布期间禁止写入草稿$' --reporter=json --outputFile=.ticket23-persistence-1.json
cd frontend && node node_modules/vitest/vitest.mjs run src/CreativeCanvas.test.tsx -t '^保存冲突时保留本地草稿并显示重新读取入口$' --reporter=json --outputFile=.ticket23-persistence-2.json
cd frontend && node node_modules/vitest/vitest.mjs run src/CreativeCanvas.test.tsx -t '^保存中继续编辑会保留草稿并用返回的新 revision 进行下一次保存$' --reporter=json --outputFile=.ticket23-persistence-3.json
cd frontend && node node_modules/vitest/vitest.mjs run src/CreativeCanvas.test.tsx -t '^从画布目录切换时按 canvasId 加载独立工作区$' --reporter=json --outputFile=.ticket23-persistence-4.json
cd frontend && node node_modules/vitest/vitest.mjs run src/CreativeCanvas.test.tsx -t '^保留本地草稿后切回会继续标记为未保存$' --reporter=json --outputFile=.ticket23-persistence-5.json
cd frontend && node node_modules/vitest/vitest.mjs run src/creativeCanvasApi.test.ts -t '^不传 canvasId 时保留旧画布请求 URL 与载荷$' --reporter=json --outputFile=.ticket23-api-1.json
cd frontend && node node_modules/vitest/vitest.mjs run src/creativeCanvasApi.test.ts -t '^目录 CRUD 与所有画布工作区请求都显式携带 canvasId$' --reporter=json --outputFile=.ticket23-api-2.json
```

Expected: 七次均 PASS；每次只能有一个 expected passed，其余同文件 assertion 只能 skipped。

- [ ] **Step 4: 提交并冻结 Ticket 23 产品主体**

```bash
git add backend/requirements.lock frontend/src/CreativeCanvasTicket23Acceptance.test.tsx
git commit -m "test: prepare ticket 23 for trusted E2 replay"
git rev-parse HEAD
git status --short
```

Expected: 完整 40 位 `TICKET23_PRODUCT_COMMIT`，产品 worktree 干净；提交只含 Ticket 23 测试/lock 范围。

### Task 17: 迁移 Ticket 23 真实 E2 profile

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/profiles/ticket-23.json`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted-registry.json`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trust/revocations.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile_v2.py`

**Interfaces:**
- Consumes: Task 15 的真实平台/toolchain/dependency/supervisor digests；Task 16 的 `TICKET23_PRODUCT_COMMIT`、lock digests 与实际测试身份；旧计划 E1/E4 真实输入门。
- Produces: Ticket 23 v2 E2 suites `e2.ticket23.backend-persistence.v2`、`e2.ticket23.frontend-persistence.v2`、`e2.ticket23.api.v2`、`e2.ticket23.canvas-structure.v2`；旧 E2 pointer/artifact typed revocation；不迁移旧 receipt。

- [ ] **Step 1: 写 v2 profile/registry RED 测试**

```python
def test_ticket23_v2_profile_owns_only_c01_c02_c03_and_one_fixed_subject(): ...
def test_ticket23_backend_uses_exact_four_nodeids_and_wheel_only_python_setup(): ...
def test_each_ticket23_vitest_name_has_one_file_exact_name_invocation(): ...
def test_ticket23_suites_share_platform_toolchain_and_exact_lock_dependency_digests(): ...
def test_old_v1_suite_receipt_and_pointer_are_revoked_not_migrated(): ...
def test_ticket23_profile_blocks_on_macos_without_registered_vm_containment(): ...
```

- [ ] **Step 2: 运行 profile 测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile_v2.py`

Expected: FAIL，Ticket 23 v2 profile/registry 尚未写入。

- [ ] **Step 3: 写入真实 v2 registry/profile 和 typed revocation**

只使用 Task 15 已验收形态生成的真实 toolchain/dependency/supervisor/platform digests 与 Task 16 的真实 product commit/lock digests；后端四 node ID 可一 invocation，前端五+二+二个 fullName 必须一名一 invocation。先撤销被取代的 v1 pointer/artifact，再登记新 pointer；不得把 v1 receipt 改 schema 或复制为 v2。

- [ ] **Step 4: 在受支持平台采集 v2 E2，登记但不自动晋升**

Run on the registered Linux executor: `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.backend-persistence.v2 --subject "git://product@$TICKET23_PRODUCT_COMMIT"`

重复执行 frontend-persistence/API/structure 三个 verifier；每次成功输出 v2 `artifactRef` 后单独运行 `register`。

Expected: collect 只新增 v2 CAS receipt，register 只追加 pointer；`matrix-contract.json` 字节不变。E1/E4 未全部满足时 C01–C03 继续阻塞。

- [ ] **Step 5: 运行 profile/workflow/full acceptance 验证并提交**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_workflow_v2.py .scratch/unified-remix-canvas-generation/acceptance`

Expected: PASS；138 项分母和其他 135 项不变；没有真实 E4 时不得宣称 Ticket 23 resolved。

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/profiles/ticket-23.json .scratch/unified-remix-canvas-generation/acceptance/trusted-registry.json .scratch/unified-remix-canvas-generation/acceptance/trust/revocations.json .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile_v2.py
git commit -m "feat: migrate ticket 23 to E2 v2"
```

## Self-review

### 规格条款 → Task / 测试覆盖

| 规格条款 | Task / 主要测试 |
| --- | --- |
| 1–5 决策、不变量、A-before-B、零写入 | Tasks 2、3、12–14；barrier zero-spawn、verify no-CAS、collect one-write tests |
| 6.1 suite v2、selection、derived entrypoint | Tasks 1、12、17；policy closure、anchored exact-name、entrypoint replacement tests |
| 6.2 canonical toolchain、closure、uv/security/platform | Tasks 4、5、15；archive ordering/budget、uv identity/advisory、real package tests |
| 6.3 dependency repository、Python/npm deterministic view | Tasks 6、7、12、15；two-root byte identity、mapping/extra blob、real offline install tests |
| 6.4 receipt v2 与最小身份 | Tasks 2、13；strict codec、expected-only IDs、no raw report/path/secret tests |
| 7 candidate set、unique active pointer、gate recheck | Task 3、14；missing/ambiguous/same-artifact/concurrent snapshot tests |
| 8.1 工具链展开与只读写边界 | Tasks 4、5、12；no-exec extraction、write-set/protected digest tests |
| 8.2 sanitized Git 与有界元数据 | Task 8；inert config、dangerous config、ref/object/budget tests |
| 8.3 Linux cgroup/VM containment、exec/write census | Task 10、15；escape fixture、proof/census loss、macOS unavailable tests |
| 8.4 StreamingRedactor/v1、先脱敏后截断 | Task 9、15；all split points、agent suffix/file URL、EOF/post-limit tests |
| 8.5 pytest/Vitest 真实 reporter | Task 11、15–17；zero omission/deselected、1 pass + skipped、exact-name tests |
| 8.6 所有固定上限 | Tasks 2、4、8、9、11；boundary/over-limit/stable error tests |
| 9 collect/verify/promote/reconcile 原子性 | Tasks 13、14；command separation、snapshot CAS、atomic replace tests |
| 10 稳定失败码 | Task 1 及每个 owning task 的错误码断言 |
| 11 真实最小 Python/Vitest/container/platform/containment fixture | Task 15；`test_trusted_evidence_e2_v2_integration.py` |
| 11.6 Ticket 23 profile 映射 | Tasks 16–17；四 pytest、五+二+二 Vitest、同 commit/digest tests |
| 12 验收矩阵全部攻击/正常场景 | Tasks 3–17 的对应 fixture；Review Focus 五项均有 owning test |
| 13 固定参考与三引擎/E1/E3/E4 边界 | Tasks 14、17 contract/profile tests |
| 14 迁移：旧 receipt/suite 不迁移、Task 5 历史不改 | Tasks 1、14、17 migration-wall/revocation tests；本计划不修改旧计划/历史 |

### 类型与命名一致性

- Task 1 定义的 `E2SuiteDefinitionV2`、`ContainmentProfileV1`、selection/report/entrypoint 类型被 Tasks 3–17 原名消费；没有 `setupArgv/argv/toolVersions` 的 v1 别名。
- Task 2 的 `StableRead`/`BoundedStableReader` 被 receipt、archive、Git、report 和 lock parser 共用；每个 caller 提供规格固定上限与稳定码。
- Task 3 只输出 `PrevalidatedE2Batch/PrevalidatedE2Pointer`；只有 Task 13 的 `verify_prevalidated` 接受它，防止 workflow 绕过 barrier。
- Task 10 的 `ReplayContainmentV1` proof 类型直接进入 Task 12 `ReplayResult` 和 Task 13 receipt；PGID 不出现在任何 proof 类型。
- Task 12 的 `ReplayResult` 是内存对象；CAS writer 只存在于 Task 13 `E2Collector`，验证器构造路径没有 writer 参数。

### 步骤、Review Focus 与比例检查

- 17 个 Task 均是可独立拒绝/接受的审查单元，每个都有上游源码核查（需要时）、RED、最小实现、GREEN 和单独提交；真实平台、产品主体、profile 分别留在 Tasks 15/16/17，不混入基础模块或同一提交。
- Review Focus 五项分别由 Tasks 3/14、9、10、4–7/15、11–12/15 的命名测试覆盖，不是只写在全局说明里。
- 计划给出固定接口、文件、值、命令和期望，不抄函数体；复杂算法只在 redactor/container/containment 任务描述不变量。
- 命令均基于当前仓库真实路径；当前已确认 `backend/.venv/bin/python`、pytest 8.3.5、Vitest 3.0.8、Node 24.20.0、npm 11.19.0、Git 2.53.0 可定位。当前宿主 uv 0.11.14 与 macOS 平台不作为正式重放输入。
- Tasks 15–17 的 Linux/真实 package/产品 commit/治理公钥属于硬门：输入缺失时保持 `E2_CONTAINMENT_UNAVAILABLE` 或 profile 未注册，不生成占位 digest、伪 toolchain、PGID profile 或“已通过”结论。

## 执行与批准状态

- 书面规格已由用户批准；本计划尚待用户审核。
- 执行方式已确定为 subagent-driven：逐 Task 新实现会话 + 新独立审查会话，前一 Task 通过后才进入下一 Task，最后再做一次全分支独立审查。
- 本计划本身不授权当前规划会话启动实现；用户审核计划后，实施会话必须从 Task 1 开始，不恢复旧 Task 5，也不直接跳到 Task 17 profile 数据。
