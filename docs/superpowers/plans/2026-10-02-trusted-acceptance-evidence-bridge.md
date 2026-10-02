# 受信验收证据桥 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不建立第二套业务状态的前提下，为现有 138 项验收矩阵交付可审计、可重放、默认拒绝的 E1/E2/E4 证据登记、晋升与失效重对账链路，并以 Ticket 23 的 C01–C03 作为首个真实 profile。

**Architecture:** 在现有验收目录内增加一个纯 Python 深模块：静态注册表和 profile 决定信任，catalog 只保存内容寻址指针，运行时验证器从 Git、固定测试 suite 或 SSH 签名重新取得事实；聚合器只把验证结果投影到现有 `matrix-contract.json/statusPolicy.overrides`。`register` 永不写状态，`validate` 永远只读，只有显式 `promote`/`reconcile` 可通过带快照校验的原子替换写 override；E3 首期只有协议边界，不注册生产 adapter。

**Tech Stack:** Python 3.13 标准库、pytest 8.3.5、Git 2.53、OpenSSH `ssh-keygen -Y`、React 18.3/Vitest 3.0.8（仅 Ticket 23 专用行为测试）、现有 JSON 验收资产。

**Spec:** `docs/superpowers/specs/2026-10-02-trusted-acceptance-evidence-bridge-design.md`（批准提交 `4e35bfef34fa49080da87cea3bbe23a6ecf23d62`）

> **状态更新（2026-10-02）：** Task 5 在五轮修复后的最终独立审查仍有 2 个 Critical、3 个 Important，保持 `blocked`，其 E2 v1 合同由 `docs/superpowers/specs/2026-10-02-e2-v2-trusted-replay-design.md` 取代（`superseded`）；这不是额外修复轮。Task 6 未开始。Task 10、Task 11 后续涉及 E2 的测试形态、registry、工具链和 receipt 必须遵循 v2，需另行编写并批准实施计划；本文现有步骤不再授权继续 E2 实施。

## Global Constraints

- 五张矩阵固定为 A01–A18、R01–R40、C01–C35、G01–G27、X01–X18，共 138 项；不得删项、换号、缩小分母或改变 `2026-09-30` 的空不适用基线。
- 合法状态必须恰为“通过 / 失败 / 阻塞 / 明确不适用”；本子系统不得新增“明确不适用”。
- E1/E2/E3/E4 最低证据组合和“黑边 / 越界时码 / 错误素材身份 / 未披露外发 / 未经批准交付”五类零容忍规则保持 `matrix-contract.json` 原样。
- `RemixEngine`、`CanvasEngine`、`GenerationService` 的职责不变；项目、素材、运行、编辑序列、预览、审核、批准与交付继续由既有业务真相源拥有。
- catalog 仅允许 `id/profileId/verifierId/subjectRef/artifactRef/registeredAt`；`level`、`kind`、`supports`、命令、退出码、人工决定和状态一律来自受信静态定义或本轮验证结果。
- `register(pointer)` 只做结构、配对、内容摘要和 append-only 校验，前后 `matrix-contract.json` 必须字节不变；禁止提供 `register --promote`。
- 正式 `validate` 和 `gaps` 只读；仅 `promote(profile_id, subject_ref)` 与 `reconcile()` 可写 `statusPolicy.overrides`，且必须临时文件、flush、fsync、原子替换和提交前快照摘要复核。
- 缺失、过期、撤销、不可访问、摘要不符、签名不符、选择项缺失或并发快照变化全部 fail closed；受信行为失败或零容忍违规为“失败”，证据/基础设施不可验证为“阻塞”。
- 阶段一只交付 core + E1 + E2；任何需要 E4 的正式项（含 C01–C03）继续“阻塞”。不得注册空壳 E3/E4，不得将旧日志、旧截图或当前脏工作区结果迁移为正式证据。
- 阶段二只交付 E4 + `ticket-23/v1` + C01–C03 晋升；A14、A17–A18、C10、C33、C35 属支持范围，不得由 Ticket 23 profile 晋升。
- 阶段三不在本计划实施。只保留 `TruthSourceAdapter.resolve(ref, policy)` 协议和“生产 registry 无 E3 条目”守卫；不得预建业务镜像或生产 E3 adapter。
- Ticket 23 正式主体必须是范围清晰、可审查、完整 40 位 SHA 的产品提交；当前含未跟踪画布文件的脏工作区不是主体，不能由实现者临时打包冒充。
- Toonflow 信任输入必须能读取固定提交 `d86cce6b689916ab73f75bc4c1f42a2a7424516c` 的真实 Git objects（计划固定使用受信 Git bundle）并核对 MIT 许可证；当前 `vendor/Toonflow/` 宿主仓库 HEAD 不能替代。
- Ticket 23 E4 必须由 `canvas-acceptance-reviewer` 的仓库外 SSH 私钥、namespace `trusted-acceptance-e4` 签署；仓库只保存 allowed-signers、公钥指纹、策略、规范负载和签名，绝不保存私钥、私钥路径、口令或 agent socket。
- 现有提交 `6e27eb43c46559997189e865f90720e5510f244f` 的截图仅可作为新签名声明的辅助附件；不得直接升级候选截图、票据文字或旧测试输出。
- 所有子进程均以 argv 直接启动，不经 shell；E2 在精确 subject 的干净隔离 checkout 中运行，最小环境只保留静态 allowlist，落盘前先脱敏。
- 本计划不修改产品业务逻辑、Provider 路由、画布持久化、混剪计划、生成运行或交付门禁。

## Review Focus

- catalog 注入 `level/supports/exitCode/decision/status/argv/cwd/env` 或未知字段必须以 `POINTER_INVALID` 拒绝，且不能触发验证或状态写入；Task 1、Task 3、Task 7 覆盖。
- 当前工作区有未提交或未跟踪改动时，E1/E2 仍只读取固定 Git commit；commit/object/bundle 不可达时阻塞，不回退读取工作区；Task 4、Task 5、Task 10 覆盖。
- 已通过证据被篡改、删除、撤销、签名失效或受信输入不可访问时，`validate` 非零但不写，`reconcile` 才原子降为“阻塞”或“失败”；Task 6、Task 8、Task 9、Task 12 覆盖。
- Ticket 23 profile 越权引用 supporting ranges、未知 principal、错误 role/scope、错误 subject 或不完整 criteria 时不得晋升；C01、C02、C03 必须允许独立结果；Task 9、Task 11、Task 12 覆盖。
- catalog 并发追加、晋升期间 registry/profile/catalog/contract 变化或进程在 replace 前中断时不得丢指针、覆盖其他 override 或留下部分结果；Task 3、Task 6、Task 12 覆盖。

---

## 文件职责与稳定接口

| 文件 | 单一职责 |
| --- | --- |
| `acceptance/trusted_evidence/models.py` | 不可变领域类型、严格 JSON 解析、`Verifier` 与未来 E3 `TruthSourceAdapter` 协议。 |
| `acceptance/trusted_evidence/errors.py` | 稳定错误码及“失败/阻塞”映射，不包含中文展示拼装。 |
| `acceptance/trusted_evidence/registry.py` | 只读加载静态 verifier/suite/profile/reviewer/revocation 定义并交叉校验。 |
| `acceptance/trusted_evidence/storage.py` | 规范 JSON、SHA-256 对象仓库、catalog CAS/锁和原子文件工具。 |
| `acceptance/trusted_evidence/registration.py` | append-only `EvidenceRegistrar`；不导入 promotion 代码。 |
| `acceptance/trusted_evidence/verifiers/e1_git.py` | 从真实 Git object database/bundle 核验提交、树、blob、remote 与许可证。 |
| `acceptance/trusted_evidence/verifiers/e2_suite.py` | 干净 checkout、字面 argv、最小环境、超时、测试选择、脱敏 receipt。 |
| `acceptance/trusted_evidence/verifiers/e4_ssh.py` | 规范人工声明、allowed-signers/policy/scope/撤销与 OpenSSH 签名验证。 |
| `acceptance/trusted_evidence/evaluation.py` | 读取冻结门槛，选择指针，执行 verifier，并逐矩阵项聚合结果。 |
| `acceptance/trusted_evidence/projection.py` | 生成 override 变更、快照 compare-and-swap 与原子写；唯一写 contract 的模块。 |
| `acceptance/trusted_evidence/collection.py` | 运行受信采集并把脱敏 receipt/签名包写入内容仓库，不登记、不晋升。 |
| `acceptance/trusted_evidence/runtime.py` | 装配 registry、profile、store、verifier、registrar、promotion/reconciliation；不持有业务状态。 |
| `acceptance/evidence_bridge.py` | `collect/register/promote/reconcile/prepare-e4/collect-e4` CLI 薄入口。 |
| `acceptance/validate_acceptance.py` | 保留现有合同、样例与 gaps 行为；正式 `validate` 调用桥接层只读验证。 |
| `acceptance/trusted-registry.json` | 静态 verifier、Git fact、E2 suite 和工具策略；运行者不能覆盖。 |
| `acceptance/profiles/ticket-23.json` | 固定 subject、C01–C03 verifier 组合、criteria、reviewer scope 和 supporting deny-list。 |
| `acceptance/trust/*` | Toonflow bundle、公钥 allowed-signers、reviewer policy 与 revocation；无私钥。 |
| `acceptance/evidence/sha256/<aa>/<digest>` | 脱敏、内容寻址且不可覆盖的证据对象。 |

以下签名在任务间保持一致：

```python
class Verifier(Protocol):
    verifier_id: str
    level: Literal["E1", "E2", "E3", "E4"]
    def verify(self, pointer: EvidencePointer, profile: AcceptanceProfile, context: VerificationContext) -> VerificationResult: ...

class TruthSourceAdapter(Protocol):
    schemes: frozenset[str]
    def resolve(self, ref: str, policy: TruthQueryPolicy) -> CanonicalTruthRecord: ...

class EvidenceRegistrar:
    def register(self, pointer: EvidencePointer) -> RegistrationResult: ...

class PromotionService:
    def promote(self, profile_id: str, subject_ref: str) -> PromotionReport: ...

class ReconciliationService:
    def reconcile(self) -> ReconciliationReport: ...
```

## 阶段一：core + E1 + E2

### Task 1: 冻结核心类型、错误语义与未来 E3 边界

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/__init__.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/models.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/errors.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_models.py`

**Interfaces:**
- Consumes: `matrix-contract.json` 的四状态、E1–E4 与零容忍字面值；规格第 5、6.3、9 节。
- Produces: `EvidencePointer.from_dict(raw: Mapping[str, object]) -> EvidencePointer`、`VerificationResult`、`VerificationContext`、`AcceptanceProfile`、`Verifier`、`TruthSourceAdapter`、`TruthQueryPolicy`、`CanonicalTruthRecord`；`classify_error(code: str) -> Literal["失败", "阻塞"]`。

- [ ] **Step 1: 写严格模型和错误分类失败测试**

新增并固定断言：

```python
def test_pointer_accepts_only_six_immutable_fields(): ...
def test_pointer_rejects_self_reported_level_supports_command_exit_decision_and_status(): ...
def test_verification_result_cannot_be_loaded_from_catalog_json(): ...
def test_error_codes_map_capability_and_zero_tolerance_to_failure_and_unverifiable_input_to_blocked(): ...
def test_truth_source_adapter_is_protocol_only_and_has_no_production_registration(): ...
```

- [ ] **Step 2: 运行模型测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_models.py`

Expected: FAIL，`trusted_evidence.models` 尚不存在。

- [ ] **Step 3: 实现最小不可变类型与稳定错误表**

`EvidencePointer` 只含 `id/profile_id/verifier_id/subject_ref/artifact_ref/registered_at`；解析时拒绝未知键、非完整 `git://product@<40-hex>` subject、非 `sha256:<64-hex>` artifact 和非 RFC3339 时间。`VerificationResult` 由验证器构造，含 `valid/error_code/facts/observed_at/input_digest/zero_tolerance_violations`，不提供 catalog 反序列化入口。错误表完整列出规格第 9 节错误码，未知错误码默认“阻塞”。

- [ ] **Step 4: 运行模型测试并确认 GREEN**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_models.py`

Expected: PASS，5 tests passed。

- [ ] **Step 5: 提交核心合同**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/__init__.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/models.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/errors.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_models.py
git commit -m "feat: define trusted acceptance evidence contracts"
```

### Task 2: 加载静态 registry、profile、reviewer 与 revocation 策略

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/registry.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted-registry.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trust/revocations.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_registry.py`

**Interfaces:**
- Consumes: Task 1 的模型和错误码；阶段一 production registry 只声明 `e1.git-license.v1`、`e2.argv-suite.v1` 实现类型，不含 Ticket 23 profile、suite 实例、E3 或 E4 条目。
- Produces: `load_trusted_registry(root: Path) -> TrustedRegistry`、`load_profiles(root: Path) -> dict[str, AcceptanceProfile]`、`load_reviewer_policy(root: Path) -> ReviewerPolicy`、`load_revocations(root: Path) -> RevocationSet`、`validate_registry_graph(...) -> None`。

- [ ] **Step 1: 写静态定义闭包失败测试**

```python
def test_phase_one_registry_has_e1_e2_implementations_but_no_production_e3_or_e4_entry(): ...
def test_registry_rejects_shell_strings_unknown_verifier_types_and_nonliteral_argv(): ...
def test_profile_rejects_unknown_items_verifiers_levels_and_supporting_range_ownership(): ...
def test_revocations_accept_only_fingerprints_signature_digests_and_evidence_digests(): ...
```

- [ ] **Step 2: 运行 registry 测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_registry.py`

Expected: FAIL，registry loader 和静态文件不存在。

- [ ] **Step 3: 实现严格 loader 与阶段一空 profile 集**

使用 `json.load(..., object_pairs_hook=...)` 拒绝重复键；所有路径先 `resolve()` 再确认位于 acceptance root；suite 的 `setupArgv`/`argv` 必须是非空字符串数组列表，禁止 `shell`、运行者参数和目录外 cwd。`profiles/` 不存在或为空时返回空映射；生产 E3 verifier 条目直接报 `VERIFIER_NOT_REGISTERED`，而不是自动发现 adapter。

- [ ] **Step 4: 运行 registry 与模型测试**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_models.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_registry.py`

Expected: PASS，且 production registry 的 E3/E4 条目数均为 0。

- [ ] **Step 5: 提交静态信任定义 loader**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/registry.py .scratch/unified-remix-canvas-generation/acceptance/trusted-registry.json .scratch/unified-remix-canvas-generation/acceptance/trust/revocations.json .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_registry.py
git commit -m "feat: load static acceptance trust policy"
```

### Task 3: 建立内容寻址对象仓库和 append-only 登记

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/storage.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/registration.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_registration.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/evidence-catalog.json:1-4`

**Interfaces:**
- Consumes: Task 1 `EvidencePointer`；Task 2 的 profile/verifier 配对；空 v1 catalog。
- Produces: `canonical_json_bytes(value: object) -> bytes`、`ContentAddressedStore.put(payload: bytes) -> str`、`ContentAddressedStore.read(ref: str) -> bytes`、`CatalogRepository.append(pointer: EvidencePointer) -> CatalogSnapshot`、`EvidenceRegistrar.register(pointer: EvidencePointer) -> RegistrationResult`。

- [ ] **Step 1: 写 CAS、幂等与并发登记失败测试**

```python
def test_content_store_derives_path_from_sha256_and_never_overwrites_different_bytes(): ...
def test_register_is_idempotent_only_for_byte_identical_pointer(): ...
def test_register_rejects_missing_or_mismatched_artifact_digest(): ...
def test_register_rejects_unknown_profile_verifier_pair_and_self_reported_fields(): ...
def test_concurrent_catalog_appends_preserve_both_entries(): ...
def test_register_leaves_matrix_contract_byte_identical(): ...
```

- [ ] **Step 2: 运行登记测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_registration.py`

Expected: FAIL，CAS、catalog repository 和 registrar 尚不存在。

- [ ] **Step 3: 实现 v2 catalog 与原子追加**

把空 catalog 幂等迁移为 `{"version": 2, "evidence": []}`；`CatalogRepository.migrate_empty_v1_for_write()` 只可由显式写命令调用，`validate`/`gaps` 不调用迁移。对象固定落在 `evidence/sha256/<digest前2位>/<64位digest>`；摘要不符返回 `DIGEST_MISMATCH`。catalog 写入使用 acceptance root 内的独占锁、旧内容摘要 compare-and-swap、同目录临时文件、flush/fsync 和 `os.replace`；同 ID 不同内容返回 `POINTER_INVALID`。

- [ ] **Step 4: 运行登记、registry 与模型测试**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_models.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_registry.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_registration.py`

Expected: PASS；并发测试重复运行 10 次仍无丢条目。

- [ ] **Step 5: 提交对象存储和登记路径**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/storage.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/registration.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_registration.py .scratch/unified-remix-canvas-generation/acceptance/evidence-catalog.json
git commit -m "feat: add immutable acceptance evidence registration"
```

### Task 4: 实现 E1 Git object 与许可证验证器

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/__init__.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e1_git.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e1.py`

**Interfaces:**
- Consumes: Task 2 的 `E1GitDefinition(repository_id, object_source, remote_urls, commit, blobs, license_fact, reuse)`；Task 3 的摘要校验。
- Produces: `GitObjectReader.read_blob(source: GitObjectSource, commit: str, path: str) -> bytes`、`GitLicenseVerifier.verify(pointer, profile, context) -> VerificationResult`；只读 Git CLI 通过 argv 执行。

- [ ] **Step 1: 写临时 Git 仓库的 E1 失败测试**

```python
def test_e1_verifies_exact_commit_remote_source_blobs_and_license_digest(): ...
def test_e1_ignores_untracked_and_uncommitted_workspace_changes(): ...
def test_e1_blocks_when_commit_or_bundle_is_unreachable_without_workspace_fallback(): ...
def test_e1_fails_on_remote_blob_path_attribution_or_license_mismatch(): ...
def test_e1_rejects_host_repository_head_as_toonflow_source(): ...
```

- [ ] **Step 2: 运行 E1 测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e1.py`

Expected: FAIL，`GitLicenseVerifier` 尚不存在。

- [ ] **Step 3: 实现只读 object 读取和事实核验**

仓库 source 支持受信 bare/worktree Git object database 与已登记 SHA-256 的 Git bundle；bundle 先核摘要和 `git bundle verify`，再在临时 bare repo 中 fetch 固定 commit。读取统一使用 `git cat-file`/`git ls-tree`，禁止读取 checkout 文件；不可访问映射 `SOURCE_REF_UNREACHABLE`，remote/commit/path/blob/归属不符映射 `GIT_FACT_MISMATCH`，许可证类型或摘要不符映射 `LICENSE_MISMATCH`。

- [ ] **Step 4: 运行 E1 与基础合同测试**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e1.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_models.py`

Expected: PASS，5 tests passed 或更多；测试只创建临时 Git 对象。

- [ ] **Step 5: 提交 E1 验证器**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/__init__.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e1_git.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e1.py
git commit -m "feat: verify acceptance git and license facts"
```

### Task 5: 实现 E2 干净 checkout 字面 argv 重放器

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e2_suite.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/redaction.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py`

**Interfaces:**
- Consumes: `E2SuiteDefinition(id, subject_repository, working_directory, setup_argv, argv, expected_test_ids, parser, allowed_environment, timeout_seconds, lockfile_digests, tool_versions)`；精确 `git://product@<sha>`。
- Produces: `SuiteRunner.run(suite: E2SuiteDefinition, subject_ref: str, context: VerificationContext) -> VerificationResult`、`E2SuiteVerifier.verify(...)`、`redact_output(data: bytes, patterns: tuple[Pattern[str], ...]) -> bytes`。

- [ ] **Step 1: 写固定提交重放与 fail-closed 失败测试**

```python
def test_e2_runs_only_registry_argv_in_detached_clean_checkout_with_minimal_environment(): ...
def test_e2_dirty_current_workspace_cannot_change_fixed_commit_result(): ...
def test_e2_rejects_catalog_command_argument_cwd_and_environment_injection(): ...
def test_e2_distinguishes_assertion_failure_missing_selection_timeout_and_missing_tool(): ...
def test_e2_blocks_when_lockfile_digest_or_tool_version_changes(): ...
def test_e2_redacts_secrets_before_storing_receipt_and_detects_receipt_tampering(): ...
```

- [ ] **Step 2: 运行 E2 测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py`

Expected: FAIL，E2 runner 尚不存在。

- [ ] **Step 3: 实现隔离 checkout、最小环境和 receipt**

用临时目录执行 `git worktree add --detach <temp> <40-sha>`，finally 只删除该临时 worktree；所有 setup/test 命令来自 registry 的 argv 数组并以 `subprocess.run(..., shell=False)` 执行。环境从空字典构造，只注入 suite allowlist 中的非秘密值；receipt 记录实际退出码、测试列表、stdout/stderr 脱敏摘要、工具版本、锁文件摘要和时长。退出非零为 `TEST_REPLAY_FAILED`，选择缺失为 `TEST_SELECTION_MISSING`，超时为 `TEST_REPLAY_TIMEOUT`，工具/checkout 不可用为阻塞类错误。

- [ ] **Step 4: 运行 E2 与 registry 测试**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_registry.py`

Expected: PASS；测试确认 subprocess 调用没有 shell 字符串。

- [ ] **Step 5: 提交 E2 重放器**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e2_suite.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/redaction.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py
git commit -m "feat: replay allowlisted acceptance suites"
```

### Task 6: 聚合最低证据并原子投影 promote/reconcile

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/evaluation.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/projection.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_promotion.py`

**Interfaces:**
- Consumes: Tasks 1–5 的 pointer、profile、registry、verifier；现有 `minimumEvidenceRules`、status default/overrides、零容忍规则。
- Produces: `EvidenceEvaluator.evaluate(profile_id: str, subject_ref: str, snapshot: AcceptanceSnapshot) -> EvaluationReport`、`PromotionService.promote(...) -> PromotionReport`、`ReconciliationService.reconcile() -> ReconciliationReport`、`ContractProjectionWriter.apply(changes: Mapping[str, StatusOverride], expected: SnapshotDigests) -> str`。

- [ ] **Step 1: 写逐项聚合、越权和原子写失败测试**

```python
def test_promote_requires_every_frozen_level_and_every_profile_verifier_for_each_item(): ...
def test_profile_cannot_promote_supporting_ranges_or_create_not_applicable_status(): ...
def test_items_in_one_profile_receive_independent_pass_fail_or_blocked_results(): ...
def test_zero_tolerance_and_trusted_behavior_failure_map_to_failure_while_unverifiable_evidence_blocks(): ...
def test_reconcile_downgrades_stale_pass_without_changing_denominator_or_other_overrides(): ...
def test_snapshot_change_aborts_before_replace_with_acceptance_state_changed(): ...
def test_interrupted_projection_write_leaves_original_contract_byte_identical(): ...
```

- [ ] **Step 2: 运行 promotion 测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_promotion.py`

Expected: FAIL，聚合器与唯一写入器尚不存在。

- [ ] **Step 3: 实现逐项计算和唯一状态写入口**

按 `profile.requirements[item_id].verifierIds` 选取同 profile、同 exact subject 的 pointer；缺 pointer/撤销/过期/不可访问一律阻塞，任一 `ZERO_TOLERANCE_VIOLATION` 或受信行为失败一律失败，其余全部成功才通过。override 保存实际 pointer IDs；“通过”清空 gap/blockedReason，“失败”写稳定 gap，“阻塞”同时写 blockedReason/gap。提交前重算 contract/catalog/registry/profile/trust 摘要，任一变化返回 `ACCEPTANCE_STATE_CHANGED` 且不写。

- [ ] **Step 4: 运行 promotion、登记和 verifier 测试**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_registration.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e1.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_promotion.py`

Expected: PASS；临时合同的矩阵数始终为 138 且不适用清单为空。

- [ ] **Step 5: 提交聚合与原子投影**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/evaluation.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/projection.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_promotion.py
git commit -m "feat: project verified evidence into acceptance status"
```

### Task 7: 分离 collect/register/promote/reconcile CLI

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/collection.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/runtime.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_evidence_bridge_cli.py`

**Interfaces:**
- Consumes: Tasks 2–6 的 loader、CAS、registrar、verifier、promotion/reconciliation。
- Produces: `EvidenceCollector.collect(profile_id: str, verifier_id: str, subject_ref: str) -> CollectedArtifact`、`build_runtime(root: Path) -> TrustedEvidenceRuntime`；CLI 子命令 `collect --profile --verifier --subject`、`register --profile --verifier --subject --artifact`、`promote --profile --subject`、`reconcile`。CLI 在构造 pointer 时生成本轮 RFC3339 `registeredAt`，该字段只用于审计。

- [ ] **Step 1: 写命令路径隔离失败测试**

```python
def test_collect_writes_content_addressed_receipt_but_not_catalog_or_contract(): ...
def test_register_appends_pointer_and_prints_registered_does_not_mean_passed(): ...
def test_register_command_has_no_promote_flag_and_never_constructs_verification_result(): ...
def test_promote_is_the_only_single_profile_command_that_writes_overrides(): ...
def test_reconcile_revalidates_current_passes_instead_of_trusting_saved_receipts(): ...
```

- [ ] **Step 2: 运行 CLI 测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_evidence_bridge_cli.py`

Expected: FAIL，CLI 和 runtime 尚不存在。

- [ ] **Step 3: 实现薄 CLI 和确定性 pointer ID**

`collect` 运行 verifier 后把规范、脱敏 receipt 写 CAS，但结果不作为后续缓存真相；`register` 生成 `<profile-slug>-<level-lower>-<verifier-slug>-<artifact摘要前12位>` ID 并只调用 registrar。所有命令输出统一 JSON 和稳定错误码；未注册 profile/verifier、采集失败或脱敏不确定时不写 catalog。

- [ ] **Step 4: 运行 CLI 与所有阶段一单元测试**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_*.py .scratch/unified-remix-canvas-generation/acceptance/test_evidence_bridge_cli.py`

Expected: PASS，0 failures。

- [ ] **Step 5: 提交桥接命令入口**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/collection.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/runtime.py .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py .scratch/unified-remix-canvas-generation/acceptance/test_evidence_bridge_cli.py
git commit -m "feat: separate acceptance evidence commands"
```

### Task 8: 兼容接入正式 validate 并闭合阶段一合同

**Files:**
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/validate_acceptance.py:14-23,110-255,353-429,499-515`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/test_acceptance_contract.py:1-489`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/README.md:1-45`

**Interfaces:**
- Consumes: Task 7 `build_runtime(root)` 与 Task 6 的只读 `evaluate_projection()`；保留 `validate_sample(...)` 的受控 fixture verifier。
- Produces: `validate_matrix_contract(...)` 原报告字段不变并增加 `trustedEvidence` 子报告；`validate` 对失效投影返回非零但不写，`gaps` 继续只读；阶段一正式状态仍为 138 个“阻塞”。

- [ ] **Step 1: 扩充正式合同兼容失败测试**

```python
def test_phase_one_validate_keeps_all_138_items_blocked_and_denominator_unchanged(): ...
def test_validate_detects_stale_or_tampered_pass_projection_without_writing_files(): ...
def test_validate_sample_remains_isolated_from_production_registry(): ...
def test_empty_v1_catalog_migrates_idempotently_on_explicit_write_but_never_during_validate(): ...
def test_validate_and_gaps_are_byte_for_byte_read_only(): ...
```

- [ ] **Step 2: 运行合同测试并确认 RED**

Run: `backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_acceptance_contract.py`

Expected: FAIL，新 trusted projection/只读失效检查尚未接入。

- [ ] **Step 3: 替换生产自填 verifier 路径并更新使用说明**

正式 `validate` 不再把 catalog v2 pointer 送入旧 `evidence_index`，而是先跑现有矩阵结构/覆盖/金标检查，再调用 runtime 只读复验 override；`validate-sample` 保留旧受控四状态路径且永不加载 production registry。README 明确阶段一命令、三命令写权限和 E3 未实现边界。

- [ ] **Step 4: 运行阶段一完整验收**

Run: `backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_acceptance_contract.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_*.py .scratch/unified-remix-canvas-generation/acceptance/test_evidence_bridge_cli.py`

Expected: PASS，0 failures。

Run: `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/validate_acceptance.py validate`

Expected: exit 0，`totalItems=138`、`statusCounts={"通过":0,"失败":0,"阻塞":138,"明确不适用":0}`，production E3/E4 count 为 0。

Run: `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/validate_acceptance.py gaps`

Expected: exit 0，`totalGaps=138`。

- [ ] **Step 5: 提交阶段一兼容接入**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/validate_acceptance.py .scratch/unified-remix-canvas-generation/acceptance/test_acceptance_contract.py .scratch/unified-remix-canvas-generation/acceptance/README.md
git commit -m "feat: validate trusted acceptance projections read only"
```

## 阶段二：E4 + Ticket 23 profile / 晋升

阶段二开始前必须同时满足：验收负责人给出 `canvas-acceptance-reviewer` 的真实 SSH 公钥/principal；审核者控制仓库外私钥；存在包含专用行为测试的固定 Ticket 23 产品提交；存在可读取 Toonflow 固定提交的真实 Git bundle。任一缺失即停止阶段二真实 profile/晋升，不生成测试公钥、伪 SHA、空 bundle 或当前脏工作区替代物。

### Task 9: 实现规范 E4 声明和 SSH 签名验证

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e4_ssh.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/attestation.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/collection.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/runtime.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py`

**Interfaces:**
- Consumes: Task 2 reviewer/revocation loader、Task 3 CAS、固定 namespace `trusted-acceptance-e4`。
- Produces: `parse_attestation(raw: bytes) -> ReviewAttestation`、`canonicalize_attestation(attestation: ReviewAttestation) -> bytes`、`SSHReviewVerifier.verify(...) -> VerificationResult`；CLI `prepare-e4 --profile --subject --review-input --output` 与 `collect-e4 --profile --subject --attestation --signature --attachment`。

- [ ] **Step 1: 用测试临时密钥写 E4 失败测试**

```python
def test_e4_accepts_canonical_payload_signed_for_trusted_namespace_principal_role_and_scope(): ...
def test_e4_rejects_duplicate_keys_floats_wrong_namespace_unknown_principal_and_wrong_scope(): ...
def test_e4_rejects_payload_subject_attachment_or_signature_tampering(): ...
def test_e4_rejects_incomplete_criteria_expired_attestation_and_revoked_key_signature_or_evidence(): ...
def test_prepare_and_collect_e4_never_receive_or_record_private_key_path_agent_socket_or_secret_environment(): ...
def test_repository_scan_finds_no_openssh_private_key_or_private_credential_fields(): ...
```

- [ ] **Step 2: 运行 E4 测试并确认 RED**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py`

Expected: FAIL，attestation 与 SSH verifier 尚不存在。

- [ ] **Step 3: 实现规范 JSON、外部签名包采集和验签**

规范 JSON 使用 UTF-8、键排序、紧凑分隔符，解析拒绝重复键和浮点数。验证器以 stdin 向 `ssh-keygen -Y verify -n trusted-acceptance-e4 -f <allowed-signers> -I <principal> -s <signature>` 传规范负载；随后独立核对公钥指纹、role/profile/item scope、subject、criteria、附件摘要、有效期和撤销。测试私钥只生成在 pytest `tmp_path`，实现/CLI 永不接受私钥参数。

- [ ] **Step 4: 运行 E4、CLI 和安全扫描测试**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py .scratch/unified-remix-canvas-generation/acceptance/test_evidence_bridge_cli.py`

Expected: PASS；临时测试目录删除后仓库中不存在私钥。

- [ ] **Step 5: 提交 E4 代码但不注册生产 signer**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e4_ssh.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/attestation.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/collection.py .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/runtime.py .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py
git commit -m "feat: verify signed acceptance reviews"
```

### Task 10: 在隔离的 Ticket 23 产品工作树补齐专用 C01/C02 行为测试并固定主体

**Files:**
- Create in the isolated Ticket 23 product worktree: `frontend/src/CreativeCanvasTicket23Acceptance.test.tsx`
- Verify without modifying: `backend/tests/test_creative_canvas.py`
- Verify without modifying: `frontend/src/CreativeCanvas.test.tsx`
- Verify without modifying: `frontend/src/creativeCanvasApi.test.ts`

**Interfaces:**
- Consumes: 已由负责人准备的、只含 Ticket 23 所需产品实现与依赖的干净 worktree；不得从当前脏工作树 `git add` 画布文件。
- Produces: 两个固定测试名“独立入口不依赖 Shot 并保留项目上下文”“Toonflow 主体控件可通过可访问路径打开和关闭”；包含该文件及 Ticket 23 实现的 40 位 `TICKET23_PRODUCT_COMMIT`。

- [ ] **Step 1: 运行尚不存在的专用测试并确认 RED**

先在隔离 worktree 执行 `cd frontend && npm ci --ignore-scripts`，再运行：

Run: `cd frontend && npm test -- --run src/CreativeCanvasTicket23Acceptance.test.tsx`

Expected: FAIL，Vitest 报告该测试文件尚不存在或没有测试。

- [ ] **Step 2: 写专用 C01/C02 测试并确认 GREEN**

测试只覆盖独立入口、项目上下文、顶栏、添加入口、基础节点、节点就近浮动工具、画布控制、按需编辑器，以及键盘/可访问名称的打开关闭路径；不断言 Ticket 24–28 的生成、批量、Agent 或完整画布能力。若现有固定候选实现不能通过，停止本证据桥任务，由 Ticket 23 产品任务在其隔离 worktree 修复并重新提交；本任务不得在当前脏工作树补产品代码。

Run: `cd frontend && npm test -- --run src/CreativeCanvasTicket23Acceptance.test.tsx`

Expected: PASS，恰好收集并通过“独立入口不依赖 Shot 并保留项目上下文”“Toonflow 主体控件可通过可访问路径打开和关闭”两个测试。

- [ ] **Step 3: 重放 Ticket 23 的现有精确测试选择**

先在隔离 worktree 执行 `uv venv backend/.venv && uv pip sync --python backend/.venv/bin/python backend/requirements.lock`，再运行：

Run: `cd backend && .venv/bin/python -m pytest -q tests/test_creative_canvas.py::test_canvas_document_saves_without_shot_and_returns_project_assets tests/test_creative_canvas.py::test_canvas_save_rejects_stale_document_revision tests/test_creative_canvas.py::test_canvas_catalog_keeps_legacy_default_and_isolates_canvas_documents tests/test_creative_canvas.py::test_soft_deleted_canvas_keeps_asset_references_and_can_restore`

Expected: PASS，4 passed。

Run: `cd frontend && npm test -- --run src/CreativeCanvas.test.tsx -t "首次读取画布期间禁止写入草稿|保存冲突时保留本地草稿并显示重新读取入口|保存中继续编辑会保留草稿并用返回的新 revision 进行下一次保存|从画布目录切换时按 canvasId 加载独立工作区|保留本地草稿后切回会继续标记为未保存"`

Expected: PASS，5 passed，JSON reporter 后续必须能读出五个完整 node ID。

Run: `cd frontend && npm test -- --run src/creativeCanvasApi.test.ts -t "不传 canvasId 时保留旧画布请求 URL 与载荷|目录 CRUD 与所有画布工作区请求都显式携带 canvasId"`

Expected: PASS，2 passed，JSON reporter 后续必须能读出两个完整 node ID。

- [ ] **Step 4: 提交专用测试并冻结产品主体**

```bash
git add frontend/src/CreativeCanvasTicket23Acceptance.test.tsx
git commit -m "test: pin ticket 23 canvas acceptance behavior"
git rev-parse HEAD
git status --short
```

Expected: 输出一个完整 40 位 SHA；worktree 无未提交/未跟踪文件。该 SHA 连同其可审查父提交共同构成阶段二真实输入，不能用当前主工作区 HEAD 代替。

- [ ] **Step 5: 从证据桥工作树验证主体可读取**

Run: `git cat-file -e "$TICKET23_PRODUCT_COMMIT^{commit}" && git diff-tree --no-commit-id --name-only -r "$TICKET23_PRODUCT_COMMIT"`

Expected: exit 0；文件清单只含已审查的 Ticket 23 产品范围和专用测试，无其他票据未审查改动。

### Task 11: 固化 Ticket 23 真实信任输入、E1/E2/E4 registry 与 profile

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/profiles/ticket-23.json`
- Create from governance input: `.scratch/unified-remix-canvas-generation/acceptance/trust/e4-allowed-signers`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trust/reviewer-policy.json`
- Create from verified upstream input: `.scratch/unified-remix-canvas-generation/acceptance/trust/git/toonflow-d86cce6b689916ab73f75bc4c1f42a2a7424516c.bundle`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted-registry.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile.py`

**Interfaces:**
- Consumes: Task 10 的真实 `TICKET23_PRODUCT_COMMIT`；真实 Toonflow bundle；验收治理提供的 principal、公钥、指纹和 `canvas-acceptance-reviewer` scope。私钥不交给实现者或仓库。
- Produces: profile ID `ticket-23/v1`，只拥有 C01/C02/C03；E1 verifier `e1.ticket23.toonflow-source.v1`、`e1.ticket23.product-adaptation.v1`；E2 suite `e2.ticket23.backend-persistence.v1`、`e2.ticket23.frontend-persistence.v1`、`e2.ticket23.api.v1`、`e2.ticket23.canvas-structure.v1`；E4 verifier `e4.ticket23.canvas-review.v1`。

- [ ] **Step 1: 先验证三个外部输入，任一失败即停止本任务**

Run: `git cat-file -e "$TICKET23_PRODUCT_COMMIT^{commit}"`

Expected: exit 0，且 SHA 与 Task 10 审查记录完全一致。

Run: `git bundle verify .scratch/unified-remix-canvas-generation/acceptance/trust/git/toonflow-d86cce6b689916ab73f75bc4c1f42a2a7424516c.bundle`

Expected: exit 0；随后从 bundle 可读取 `d86cce6b689916ab73f75bc4c1f42a2a7424516c^{commit}`。

Run: `awk '{for (i=1; i<=NF; i++) if ($i ~ /^ssh-/) {print $i, $(i+1); exit}}' .scratch/unified-remix-canvas-generation/acceptance/trust/e4-allowed-signers | ssh-keygen -l -E sha256 -f -`

Expected: exit 0；指纹与验收治理交接记录一致，文件只含公钥。

- [ ] **Step 2: 写 profile/registry 闭包失败测试**

```python
def test_ticket23_profile_owns_exactly_c01_c02_c03_and_denies_all_supporting_ranges(): ...
def test_ticket23_profile_requires_frozen_e1_e2_e4_combinations_per_item(): ...
def test_ticket23_e1_maps_exact_upstream_and_product_blobs_license_and_attribution(): ...
def test_ticket23_e2_suites_use_literal_argv_exact_node_ids_lock_digests_and_clean_subject(): ...
def test_ticket23_e4_requires_canvas_reviewer_and_all_eight_criteria(): ...
def test_ticket23_profile_contains_real_product_commit_bundle_digest_and_public_key_fingerprint(): ...
```

- [ ] **Step 3: 写入真实静态定义，不使用占位值**

Toonflow E1 至少固定以下 blobs：

- `apps/web/src/pages/workspace/panels/canvas/canvasHost.vue`
- `apps/web/src/pages/workspace/panels/canvas/index.vue`
- `apps/web/src/pages/workspace/panels/canvas/components/canvasControls.vue`
- `apps/web/src/pages/workspace/panels/canvas/components/canvasMenu.vue`
- `apps/web/src/pages/workspace/panels/canvas/components/nodeSearch.vue`
- `apps/web/src/pages/workspace/panels/canvas/components/selectionToolbar.vue`
- `packages/nodeScaffold/src/nodeSkeleton.vue`
- `packages/nodeScaffold/src/components/promptInput.vue`
- `packages/nodeScaffold/src/connection.ts`
- `packages/nodes/textNode/src/index.vue`
- `packages/nodes/imageNode/src/index.vue`
- `packages/nodes/imageGenerationNode/src/index.vue`
- `LICENSE`

产品 E1 至少固定 `backend/app/{__init__,main,creative_canvas,creative_canvas_api}.py`，`backend/tests/test_creative_canvas.py`，`frontend/src/{ContentStudioApp,CreativeCanvas,CreativeCanvasNodes,CreativeCanvasPrompt}.tsx`，`frontend/src/{creativeCanvas,contentStudio}.css`，`frontend/src/{creativeCanvasApi,creativeCanvasConnection}.ts`，三个 Ticket 23 前端测试文件及 `frontend/package*.json`；每个 blob 摘要直接从 `TICKET23_PRODUCT_COMMIT` 计算。

E2 后端 suite 使用 Task 10 的四个完整 pytest node ID；前端持久化 suite 使用五个完整 Vitest node ID，API suite 使用两个，结构 suite 使用专用文件的两个。registry 为每个 Vitest 名称建立独立字面 argv invocation并解析 JSON reporter，避免一个正则中缺一项仍误过。后端 setup 固定 `uv venv backend/.venv` 与 `uv pip sync --python backend/.venv/bin/python backend/requirements.lock`，前端 setup 固定 `npm ci --ignore-scripts`；锁文件摘要来自产品 commit。

profile requirements 固定为：C01=`product E1 + backend E2 + structure E2 + E4`；C02=`Toonflow E1 + product E1 + structure E2 + E4`；C03=`product E1 + backend E2 + frontend-persistence E2 + API E2 + E4`。E4 固定规格 10.4 的八项 criteria，并允许各矩阵项按对应 criteria 独立判定。

- [ ] **Step 4: 运行真实输入与 profile 测试**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_registry.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e1.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py`

Expected: PASS；测试输出回显真实 40 位产品 SHA、Toonflow commit、bundle SHA-256 和 reviewer 公钥指纹，不回显秘密。

- [ ] **Step 5: 提交受信输入与 Ticket 23 profile**

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/profiles/ticket-23.json .scratch/unified-remix-canvas-generation/acceptance/trust/e4-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/reviewer-policy.json .scratch/unified-remix-canvas-generation/acceptance/trust/git/toonflow-d86cce6b689916ab73f75bc4c1f42a2a7424516c.bundle .scratch/unified-remix-canvas-generation/acceptance/trusted-registry.json .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile.py
git commit -m "feat: register trusted ticket 23 acceptance profile"
```

### Task 12: 采集新证据、仓库外签署 E4、逐项晋升并验证失效重对账

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_promotion.py`
- Create generated immutable objects: `.scratch/unified-remix-canvas-generation/acceptance/evidence/sha256/<aa>/<digest>`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/evidence-catalog.json`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/matrix-contract.json:statusPolicy.overrides`
- Modify: `.scratch/unified-remix-canvas-generation/issues/23-align-toonflow-canvas-shell-and-persistence.md`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/README.md`

**Interfaces:**
- Consumes: Task 11 的完整 profile/registry/公钥/bundle/subject；审核者在仓库外持有的私钥；规格 10.4 八项真实操作记录与附件。
- Produces: 新采集的 E1/E2/E4 pointer、逐项 C01/C02/C03 `PromotionReport`、可重放 `ReconciliationReport`；不触碰其他 135 项或产品业务数据。

- [ ] **Step 1: 写端到端晋升和失效回归测试**

```python
def test_ticket23_promotes_only_c01_c02_c03_when_each_required_verifier_is_current(): ...
def test_ticket23_keeps_each_item_independent_when_one_e4_criterion_is_rejected_or_missing(): ...
def test_ticket23_never_promotes_a14_a17_a18_c10_c33_or_c35(): ...
def test_validate_is_read_only_and_reconcile_downgrades_tampered_revoked_or_unreachable_evidence(): ...
def test_ticket23_promotion_aborts_atomically_when_acceptance_snapshot_changes(): ...
```

- [ ] **Step 2: 运行测试并确认 RED，再完成最小集成修复**

Run: `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_promotion.py`

Expected: 初次 FAIL 于真实 profile 尚无登记 pointer；测试 fixture 使用临时 Git repo/临时 SSH key 模拟完整链路，不把测试 key 加入 production allowed-signers。补齐 runtime 的 E4 逐 criteria 投影和 Ticket 23 verifier 装配后重跑为 PASS。

- [ ] **Step 3: 在固定 subject 上重新采集并登记 E1/E2**

逐一执行以下命令；每次 `collect` 成功后，从 JSON 输出取得 `artifactRef`，再用同 profile/verifier/subject 调用 `register`。旧日志、当前 workspace 测试结果和候选截图不作为这些命令的输入。

```bash
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e1.ticket23.toonflow-source.v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e1.ticket23.product-adaptation.v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.backend-persistence.v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.frontend-persistence.v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.api.v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.canvas-structure.v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT"
```

Expected: 六次 exit 0；receipt 均显示 fixed subject、实际完整 test IDs、工具/锁摘要和脱敏状态。将每次 JSON 输出中的 `artifactRef` 分别保存为 `E1_TOONFLOW_ARTIFACT`、`E1_PRODUCT_ARTIFACT`、`E2_BACKEND_ARTIFACT`、`E2_FRONTEND_ARTIFACT`、`E2_API_ARTIFACT`、`E2_STRUCTURE_ARTIFACT`，再逐一运行：

```bash
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py register --profile ticket-23/v1 --verifier e1.ticket23.toonflow-source.v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --artifact "$E1_TOONFLOW_ARTIFACT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py register --profile ticket-23/v1 --verifier e1.ticket23.product-adaptation.v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --artifact "$E1_PRODUCT_ARTIFACT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py register --profile ticket-23/v1 --verifier e2.ticket23.backend-persistence.v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --artifact "$E2_BACKEND_ARTIFACT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py register --profile ticket-23/v1 --verifier e2.ticket23.frontend-persistence.v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --artifact "$E2_FRONTEND_ARTIFACT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py register --profile ticket-23/v1 --verifier e2.ticket23.api.v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --artifact "$E2_API_ARTIFACT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py register --profile ticket-23/v1 --verifier e2.ticket23.canvas-structure.v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --artifact "$E2_STRUCTURE_ARTIFACT"
```

Expected: 六次均输出“已登记，不代表通过”，且 contract 字节不变。

- [ ] **Step 4: 准备新 E4 声明，由审核者在仓库外签署后采集登记**

先在真实 1280×900 视口完成规格 10.4 八项操作；声明引用固定产品 commit、C01–C03、真实 reviewer principal、八项 criteria 结论、稳定项目/画布/素材/review 引用、操作记录或录像摘要、已知差异和零容忍检查。候选六张截图只按其提交摘要作为辅助附件。

Run: `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py prepare-e4 --profile ticket-23/v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --review-input "$E4_REVIEW_INPUT_FILE" --output "$E4_ATTESTATION_FILE"`

Expected: exit 0；输出是规范 JSON，不含签名、私钥字段或自由文本替代身份。

审核者在仓库外运行：

```bash
ssh-keygen -Y sign -n trusted-acceptance-e4 -f "$E4_PRIVATE_KEY_FILE" "$E4_ATTESTATION_FILE"
```

`E4_PRIVATE_KEY_FILE` 只存在审核者环境，不写入 shell 历史导出、仓库、receipt 或日志。随后运行：

```bash
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect-e4 --profile ticket-23/v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --attestation "$E4_ATTESTATION_FILE" --signature "$E4_ATTESTATION_FILE.sig" --attachment "$E4_OPERATION_RECORD_FILE"
```

Expected: exit 0；返回 E4 `artifactRef`。用 `register --profile ticket-23/v1 --verifier e4.ticket23.canvas-review.v1 ...` 登记，仍不改变任何矩阵状态。

将返回值保存为 `E4_REVIEW_ARTIFACT` 后运行：

```bash
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py register --profile ticket-23/v1 --verifier e4.ticket23.canvas-review.v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT" --artifact "$E4_REVIEW_ARTIFACT"
```

Expected: 输出“已登记，不代表通过”，`matrix-contract.json` 字节不变。

- [ ] **Step 5: 显式晋升、只读校验、重对账和全量测试**

Run: `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py promote --profile ticket-23/v1 --subject "git://product@$TICKET23_PRODUCT_COMMIT"`

Expected: exit 0；报告分别列出 C01/C02/C03。只有实际 E1/E2/E4 全部成功且无零容忍违规的项为“通过”；criteria 拒绝为“失败”，证据缺失/不可验证为“阻塞”。其他 135 项不变。

Run: `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/validate_acceptance.py validate`

Expected: exit 0 且文件字节不变；138 项分母、四状态集合、最低证据和空不适用基线不变。

Run: `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py reconcile`

Expected: exit 0；重新执行 E1/E2/E4 后状态与刚晋升结果一致。篡改/撤销/不可达降级只在 `test_ticket23_promotion.py` 的临时副本中演练，不污染真实 catalog、trust 或 contract。

Run: `backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance`

Expected: PASS，0 failures。

Run: `git diff --check -- .scratch/unified-remix-canvas-generation/acceptance .scratch/unified-remix-canvas-generation/issues/23-align-toonflow-canvas-shell-and-persistence.md`

Expected: exit 0。

- [ ] **Step 6: 记录实际结果并提交，不夸大通过范围**

Ticket 23 只记录本轮真实 subject、bundle、公钥指纹、E1/E2/E4 pointer IDs、逐项 C01–C03 结果和未闭合差异；仅当三项均通过才把 Ticket 23 状态从 `claimed` 改为 `resolved`，否则保留非 resolved 状态。

```bash
git add .scratch/unified-remix-canvas-generation/acceptance/evidence/sha256 .scratch/unified-remix-canvas-generation/acceptance/evidence-catalog.json .scratch/unified-remix-canvas-generation/acceptance/matrix-contract.json .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_promotion.py .scratch/unified-remix-canvas-generation/acceptance/README.md .scratch/unified-remix-canvas-generation/issues/23-align-toonflow-canvas-shell-and-persistence.md
git commit -m "chore: record ticket 23 trusted acceptance outcome"
```

## Self-review

- 规格覆盖：Tasks 1–8 覆盖阶段一 core、静态 registry/profile schema、CAS、分离采集/登记/晋升/重对账、E1、E2、只读 validate 与迁移；Tasks 9–12 覆盖 E4、真实 Ticket 23 subject/bundle/public key、profile、签名、逐项晋升与失效重对账。阶段三只留下 E3 协议和无生产注册守卫。
- 步骤粒度：12 个任务各自有明确 RED、最小实现、GREEN、命令/预期和独立提交；外部输入检查是阶段二硬门，不与实现步骤混写。
- 类型一致性：全计划统一使用 `EvidencePointer`、`VerificationResult`、`VerificationContext`、`AcceptanceProfile`、`EvidenceRegistrar.register`、`PromotionService.promote`、`ReconciliationService.reconcile`；catalog 字段和 CLI 参数命名无漂移。
- Review Focus：五类最高风险均绑定具体任务与测试；尤其覆盖自填注入、脏工作区、篡改/撤销、Ticket 23 越权和并发原子性。
- 篇幅比例：计划锁定文件职责、接口、测试名、固定值与验证命令，不抄写函数体；复杂度集中在信任边界和 fail-closed 行为，没有预实现 E3 或未来票据 profile。
- 外部阻塞：阶段二真实闭合仍取决于范围清晰的 Ticket 23 产品提交、真实 Toonflow Git bundle、验收治理提供的 allowed-signers/公钥指纹，以及持有仓库外私钥的独立审核者。缺一项时阶段一仍可完成，但不得创建替代数据或宣称 C01–C03 通过。
