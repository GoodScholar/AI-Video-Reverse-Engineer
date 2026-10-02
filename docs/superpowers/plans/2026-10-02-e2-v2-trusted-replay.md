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
- `RegisteredRepositoryBoundaryV1` 只能由同一受信 source-runner 进程打开、持有并关闭 owning directory handles，禁止 JSON/pickle/路径形式序列化或跨进程恢复；同一命令必须先按规格 §8.2 对原始 `.git/gitdir/commondir` 字节与拓扑执行完整 `RawGitLayoutInspectorV1`，通过后才规范化和构建 container。原始 pointer/control bytes 只在该进程 owning-FD 生命期内检查，不持久化、不参与可重现 digest；持久 proof 只绑定去路径的规范逻辑拓扑。跨 Task/权限域唯一存储由编排器在每个 executor 启动前提供并验证为绝对路径 `$TRUSTED_HANDOFF_STORE_ROOT`；该目录位于仓库/worktree 之外，宿主路径、mount 点、认证租约不得进入 record、proof 或 digest。Task 22 producer-only harness 在无签名/agent 环境中只把 canonical blobs/record/pointer/request 写入 broker-owned、consumer 不可见且位于 durable store 外的 quarantine；producer 完全退出且 broker destroy/verify/baseline proof 成功后，broker 才把 proof-bound bytes 原子 no-replace 发布到 durable CAS/pointer/export-request。orchestrator-only harness 只按已发布 request ref 重算 closure，不能接收 producer 路径。CAS 路径恰为 `sha256/<前两位>/<后 62 位>`，pointer 为 `pointers/<handoff-id>.json`；每个 `(handoffId, consumerTask, destinationExecutorId)` 使用唯一 `transportId`，其 transfer/seal 为 `transfers/<handoff-id>/<transport-id>.json` 与 `seals/<handoff-id>/<transport-id>.json`。编排器只在目标 durable store 完整导入 closure、签名并将 transfer/seal 原子 no-replace install+fsync 后才标记该 consumer transport 可用；consumer 依次 fetch/import/finalize/read 并每次重算 request、pointer、完整 record bytes 与 referenced blobs。同 `transportId` 且全部 bytes 一致是幂等 retry；nonce/sequence/consumer/destination 任一改变或不匹配是 replay/tamper 并 fail closed。`mktemp` 只作单 Task local cache，并须由安全 helper 先清理验证后解除 EXIT trap。
- 工具链、依赖、产品源码分别只来自 `trust/toolchains/sha256`、`trust/dependencies/sha256`、`trust/product-sources/sha256`；禁止宿主 PATH/shell/`/usr/bin/env`、live download、主机 cache 或同名工具回退。
- 任何会形成产品 subject、固定 policy commit/ref、canonical container 或验收授权状态的 Git 读写（包括 `index-pack/fsck/bundle verify/cat-file/diff/rev-parse/update-ref/commit`）只能经单一 `TrustedGitExecutorV1` process capability：Git 2.53.0 executable 与 SHA-256 来自已验证 Task 18 toolchain；该进程先 no-follow 打开 repository/config/attribute/executable handles，构造只读、身份固定的 trusted config+empty-attribute+worktree snapshot view，之后 Git 只看 snapshot/私有 object upper，绝不按原始路径重开。
- Trusted Git 的环境逐项固定为：`HOME`、`XDG_CONFIG_HOME` 指向只读空目录，`GIT_CONFIG_NOSYSTEM=1`、`GIT_CONFIG_GLOBAL=/dev/null`、`GIT_ATTR_NOSYSTEM=1`、`GIT_OPTIONAL_LOCKS=0`、`GIT_TERMINAL_PROMPT=0`、`GIT_DISCOVERY_ACROSS_FILESYSTEM=0`、`GIT_NO_REPLACE_OBJECTS=1`、`LC_ALL=C`；清除 `GIT_DIR`、`GIT_WORK_TREE`、`GIT_COMMON_DIR`、`GIT_OBJECT_DIRECTORY`、`GIT_ALTERNATE_OBJECT_DIRECTORIES`、`GIT_REPLACE_REF_BASE`、`GIT_SSH`、`GIT_SSH_COMMAND`、`SSH_AUTH_SOCK`、`GIT_ASKPASS` 和全部调用方 `GIT_CONFIG_*`；`PATH` 只含工具链 entrypoint/helper 目录。每次适用 Git 命令必须显式使用 `--no-replace-objects`、`-c core.hooksPath=<受信只读空目录>`、`-c core.autocrlf=false`，commit 另固定 `--no-gpg-sign --no-verify`、author/committer name/email/date、typed `expected_old_head`，且不允许网络协议、credential/helper、自动 GC/maintenance。
- Trusted Git 的原始 config parser 完整实现批准规格三类矩阵：结构键仅允许 `core.repositoryformatversion`、`core.filemode`、`core.bare`、`core.logallrefupdates`、`core.ignorecase`、`core.precomposeunicode`、`extensions.worktreeconfig`，其中 format version 恰为 `0`、其余为规范布尔值并由 runner 重建；惰性键仅允许 `remote.<name>.{url,pushurl,fetch,tagopt,mirror,prune,prunetags,skipdefaultupdate}`、`branch.<name>.{remote,merge,mergeoptions,rebase,pushremote,description,vscode-merge-base}`、`codex.localenvironmentconfigpath`，只做批准的 name/value 有界检查后丢弃；危险键 `include.*`、`includeIf.*`、`core.hooksPath`、`core.attributesFile`、`core.excludesFile`、`core.fsmonitor`、`core.sshCommand`、`core.alternateRefsCommand`、`filter.*`、`credential.*`、`url.*`、`http.*`、`protocol.*`、`submodule.*`、`diff.*`、`merge.*`（除结构化允许的 `branch.<name>.merge`）、`difftool.*`、`mergetool.*`、`alias.*`、`safe.*`、`remote.<name>.promisor`、`remote.<name>.partialclonefilter`、`remote.<name>.proxy`、`remote.<name>.uploadpack`、`remote.<name>.receivepack`、`extensions.partialClone`、`extensions.objectFormat` 以及任何未命中前两类的未知键存在即拒绝。原始 config/config.worktree 任一 NUL、无效 UTF-8、重复 section/key、续行、畸形 quoted subsection 也在 Git spawn 前拒绝；原始值永不复制到 snapshot。
- `$GIT_DIR/info/attributes` 或任何 external attributes 文件存在即拒绝，不能解析后复制或因内容为空放行；subject tree 的每个 `.gitattributes` 均由自有 parser 有界检查，任何 `filter`、`working-tree-encoding`、`ident` 声明即拒绝。`objects/info/{alternates,http-alternates}`、环境 alternate/object directory、`refs/replace/**`、`info/grafts`、`shallow`、`.promisor`/partial clone、真实 hook、symlink/special control component 同样逐项 fail closed。执行前后重验原 root/file dev+inode+bytes digest，任一竞态使当前阶段零可见 artifact/ref/contract 写入。在 Task 22/27 credential domains，seccomp supervisor 只接受 executor 通过 broker capability 发起的 pinned Git exec request，producer 直接 exec 同一 binary 或任何 host Git 都拒绝。普通 Task 实现 checkpoint commit 不产生可信 artifact，不得被后续证据读取。
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
- Task 26 的实现 commit 只有在独立审查通过后，才能由 root broker 生成内容认证且持久的 `Task26CompletionRecordV1`；broker必须物化Task18固定toolchain并在同一进程持有`TrustedGitExecutionViewV1`，reviewed commit/base/ref与plan/spec blobs全部只经`resolve_ref/cat_file/compare_blob_bytes`等有界接口重验，禁止普通repository capability或host Git旁路。Task 27只能消费broker验证并通过只读capability FD注入的该record，不能信任环境变量、当前HEAD/current branch或调用方自报base/ref。Task27在所有product/policy输入预检完成后才创建持久`PolicyPublishTransactionJournalV1`；正式refs与core handoff按write-ahead/fsync状态机恢复。ACTIVATED后broker把intent→marker→activated完整单向链签成内容寻址`PolicyActivationProofV1`，后置policy handoff/export request及每个transport都绑定proof ref/full-bytes digest；Tasks28/29只依赖固定registry trust root离线验签proof，不能读取root-only journal。
- transport-seal与policy-activation signing domains必须在schema、registry、broker/profile启动和每次验签时同时互斥：key ID、raw public-key digest/fingerprint、certificate/wrapper解析后的leaf public-key digest/fingerprint四组均不得相交，purpose/domain缺失或错配在任何proof/seal/ready/authorization write前失败。Task28/29不得用whole-repository clean作为授权条件；只接受Task2治理固定且Task7 pinned view一次性封存的phase-aware `AllowedTaskOutputsV1`：源码dirty必须按path/status/digest精确绑定，catalog/contract必须从base bytes出发，audit必须不存在，CAS只能是七个candidate refs对应的本轮新增或byte-identical幂等对象，所有事务输出还必须绑定唯一allowed transition与expected post bytes，禁止通过刷新当前工作树快照扩大许可。
- E4 私钥始终在仓库外；仓库只保存 allowed-signers、公钥指纹、reviewer policy、规范 attestation、签名和附件摘要。缺真实 E4 或任一 E1/E2 时 C01–C03 必须阻塞。
- 每个 Task 使用新实现会话，提交后使用新独立审查会话；Critical/Important 清零且命令通过才进入下一 Task。实现前读取任务所涉现有/上游源码、版本与许可证，并记录在既有 SDD ledger。

## Review Focus

- operation snapshot、最后一个 pointer 或 policy digest 在 barrier 前后变化必须零 spawn/checkout/CAS/contract 写；Tasks 5、17 覆盖。
- 32 KiB 后才出现跨 chunk token、非法 UTF-8、未闭合 quote/JSON 或 agent socket 标点仍须读到 EOF并返回 `OUTPUT_REDACTION_UNSAFE`；Tasks 3、20 覆盖。
- leader 任意结局后 double-fork+`setsid/setpgid`+关闭 stdio 仍须处于 leaf 并被清空；Tasks 14、21 覆盖。
- uv probe cwd/env/script/cache/extra child、恶意 entry-point 越界、project/workspace/build backend、DNS/socket、derived entrypoint replacement 均须命中固定错误码且不得进入测试；Tasks 19–20 覆盖。
- 真实 linked worktree 必须 detached 于 capability 绑定的 base OID，共享 commondir/ODB 只读、对象写入 private upper，正常结束或 SIGKILL 都由 broker lease 清除 checkout/gitdir/backlink/upper；Task 22 只有取得绑定 staged digests 的 `BrokerLifecycleProofV1` 后才可发布 durable product artifacts，destroy/baseline/timeout 失败均须 durable artifactWrites=0。Task 26 broker 必须物化Task18 toolchain并在同一进程持有`TrustedGitExecutionViewV1`，reviewed commit/base/ref与plan/spec blobs只经有界接口恢复和签发前复验，完整Git负矩阵任一失败recordWrites=0；Task 27 的 refs+handoff journal 在每个 crash 点只能恢复到精确 ACTIVATED 终态或保持消费者不可见，未知漂移 fail closed，ACTIVATED后由broker签发内容寻址`PolicyActivationProofV1`。transport与activation registry必须在load/start/verify三处证明key ID、raw key与resolved leaf key集合互斥并强制不同domain tag；任一复用或purpose错配writes=0。Tasks28/29必须在无root journal权限时以固定trust root离线验证proof、三entry单向链及本consumer/destination transport绑定；其`AllowedTaskOutputsV1`必须区分源码dirty与事务输出，拒绝预脏catalog/contract、预建audit、七候选以外CAS，并以同一不可刷新transition plan在入口、持锁写前和写后验证expected bytes。Trusted Git 的 config/attributes/replace/ident/autocrlf/prompt/optional-lock/discovery 矩阵逐项覆盖且所有 artifact-bearing Git 子命令只走 pinned snapshot executor；producer/orchestrator 必须不同 OS credential/namespace/FD 边界；真实 setup/test 零网络、七项 semantic batch preflight任一失败均阻塞且零晋升；Tasks 2、7、10–11、13–14、18、22–23、25–29 覆盖。

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
    lease_id: str
    ownership_token: str
    base_commit: ProductGitObjectIdV1
    head_mode: Literal["detached"]
    checkout_root: str
    linked_gitdir_root: str
    shared_commondir_root: str
    private_object_upper_root: str
    checkout_identity: FileIdentityV1
    linked_gitdir_identity: FileIdentityV1
    shared_commondir_identity: FileIdentityV1
    shared_object_store_identity: FileIdentityV1
    private_object_upper_identity: FileIdentityV1
    shared_control_baseline_digest: str
    shared_object_store_baseline_digest: str

@dataclass(frozen=True)
class SealedPrivateObjectClosureV1:
    schema: Literal["trusted-private-object-closure/v1"]
    broker_key_id: str
    lifecycle_id: str
    closure_token: str
    base_commit: ProductGitObjectIdV1
    result_commit: ProductGitObjectIdV1
    lower_identity: FileIdentityV1
    upper_identity: FileIdentityV1
    closure_digest: str

@dataclass(frozen=True)
class BrokerLifecycleProofV1:
    schema: Literal["trusted-broker-lifecycle-proof/v1"]
    broker_key_id: str
    lifecycle_id: str
    lease_id: str
    subject_commit: ProductGitObjectIdV1
    staged_artifact_digests: tuple[NamedDigest, ...]
    producer_exit_digest: str
    destroyed_resources_digest: str
    shared_control_baseline_digest: str
    shared_object_store_baseline_digest: str
    proof_payload_digest: str
    broker_signature: str

@dataclass(frozen=True)
class Task26CompletionPayloadV1:
    project_id: Literal["ai-video-reverse-engineer"]
    task_id: Literal[26]
    reviewed_commit: GitObjectIdV1
    review_base_commit: GitObjectIdV1
    expected_old_head: GitObjectIdV1
    target_head_ref: str
    review_verdict: Literal["pass"]
    review_evidence_ref: NamedDigest
    plan_ref: NamedDigest
    spec_ref: NamedDigest
    producer_id: Literal["task26-review-controller/v1"]
    executor_id: str
    execution_profile_digest: str
    toolchain_build_record_ref: str
    trusted_git_executable_digest: str
    trusted_git_view_digest: str

@dataclass(frozen=True)
class Task26CompletionRecordV1:
    schema: Literal["trusted-task-completion-record/v1"]
    payload: Task26CompletionPayloadV1
    payload_digest: str
    broker_key_id: str
    broker_signature: str

@dataclass(frozen=True)
class PolicyPublishTransactionJournalV1:
    schema: Literal["trusted-policy-publish-transaction/v1"]
    operation_id: str
    completion_record_digest: str
    expected_old_head: GitObjectIdV1
    target_head_ref: str
    fixed_ref: Literal["refs/tags/trusted-acceptance/ticket-23-policy-v1"]
    fixed_ref_must_be_absent: Literal[True]
    policy_commit: GitObjectIdV1 | None
    staged_payload_digest: str
    record_ref: str | None
    pointer_digest: str | None
    export_request_ref: str | None
    activation_marker_digest: str | None
    state: Literal["PREPARED", "QUARANTINED", "OBJECTS_PROMOTED", "REFS_INSTALLED", "HANDOFF_INSTALLED", "ACTIVATED", "FAILED_BLOCKED"]
    pending_transition: Literal["QUARANTINE", "PROMOTE_OBJECTS", "INSTALL_REFS", "INSTALL_HANDOFF", "ACTIVATE"] | None
    previous_entry_digest: str | None
    entry_digest: str

@dataclass(frozen=True)
class PolicyActivationMarkerV1:
    schema: Literal["trusted-policy-activation/v1"]
    operation_id: str
    activation_intent_digest: str
    policy_commit: GitObjectIdV1
    fixed_ref: str
    record_ref: str
    pointer_digest: str
    export_request_ref: str

@dataclass(frozen=True)
class EmbeddedCanonicalBytesV1:
    encoding: Literal["base64"]
    canonical_bytes_b64: str
    digest: str

@dataclass(frozen=True)
class SigningKeyRegistrationV1:
    key_id: str
    purpose: Literal["transport-seal", "policy-activation"]
    signature_domain: Literal["ai-video-reverse-engineer/transport-seal/v1", "ai-video-reverse-engineer/policy-activation/v1"]
    registry_entry_bytes_digest: str
    raw_public_key_bytes_digest: str
    raw_public_key_fingerprint: str
    resolved_public_key_bytes_digest: str
    resolved_public_key_fingerprint: str

@dataclass(frozen=True)
class VerifiedSigningDomainsV1:
    transport_keys: tuple[SigningKeyRegistrationV1, ...]
    activation_keys: tuple[SigningKeyRegistrationV1, ...]
    canonical_digest: str

@dataclass(frozen=True)
class PolicyActivationProofV1:
    schema: Literal["trusted-policy-activation-proof/v1"]
    operation_id: str
    state: Literal["ACTIVATED"]
    activate_intent: EmbeddedCanonicalBytesV1
    activation_marker: EmbeddedCanonicalBytesV1
    activated_entry: EmbeddedCanonicalBytesV1
    completion_record_ref: str
    completion_record_digest: str
    policy_commit: GitObjectIdV1
    target_head_ref: str
    target_head_value: GitObjectIdV1
    fixed_ref: Literal["refs/tags/trusted-acceptance/ticket-23-policy-v1"]
    fixed_ref_value: GitObjectIdV1
    policy_record_ref: str
    policy_record_full_bytes_digest: str
    policy_pointer_name: str
    policy_pointer_bytes_digest: str
    policy_core_export_request_ref: str
    policy_core_export_request_full_bytes_digest: str
    profile_digest: str
    toolchain_build_record_ref: str
    trusted_git_executable_digest: str
    trusted_git_view_digest: str
    activation_trust_root_digest: str
    broker_key_id: str
    signing_key_purpose: Literal["policy-activation"]
    signature_domain: Literal["ai-video-reverse-engineer/policy-activation/v1"]
    signature_algorithm: Literal["ssh-ed25519"]
    broker_signature: str

@dataclass
class TrustedGitExecutionViewV1:
    executable: OwnedFileHandle
    repository: OwnedDirectoryHandle
    config_snapshot: OwnedDirectoryHandle
    attribute_snapshot: OwnedDirectoryHandle
    worktree_snapshot: OwnedDirectoryHandle
    private_object_upper: OwnedDirectoryHandle
    original_identities: tuple[FileIdentityV1, ...]
    config_snapshot_digest: str
    attribute_snapshot_digest: str
    worktree_snapshot_digest: str

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
    policy_activation_proof_ref: str | None
    policy_activation_proof_full_bytes_digest: str | None
    signing_key_purpose: Literal["transport-seal"]
    signature_domain: Literal["ai-video-reverse-engineer/transport-seal/v1"]
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
class Ticket23PolicyHandoffV1:
    schema: Literal["trusted-ticket23-policy-handoff/v1"]
    core_record_ref: str
    core_record_full_bytes_digest: str
    core_pointer_name: str
    core_pointer_bytes_digest: str
    core_export_request_ref: str
    core_export_request_full_bytes_digest: str
    policy_activation_proof_ref: str
    policy_activation_proof_full_bytes_digest: str

@dataclass(frozen=True)
class ActivatedPolicyExportRequestV1:
    schema: Literal["trusted-activated-policy-export-request/v1"]
    handoff_id: Literal["ticket-23-policy-v1"]
    policy_handoff_ref: str
    policy_handoff_full_bytes_digest: str
    core_export_request_ref: str
    core_export_request_full_bytes_digest: str
    policy_activation_proof_ref: str
    policy_activation_proof_full_bytes_digest: str
    consumer_tasks: tuple[int, ...]

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
    policy_activation_proof_ref: str | None
    policy_activation_proof_full_bytes_digest: str | None
    signing_key_fd: int
    key_id: str
    signing_key_purpose: Literal["transport-seal"]
    signature_domain: Literal["ai-video-reverse-engineer/transport-seal/v1"]

@dataclass(frozen=True)
class HandoffTransferManifestV1:
    schema: Literal["trusted-handoff-transfer/v1"]
    export_request_ref: str
    export_request_full_bytes_digest: str
    store_namespace: str
    handoff_id: str
    transport_id: str
    consumer_task: int
    record_ref: str
    record_full_bytes_digest: str
    pointer_name: str
    pointer_bytes_digest: str
    source_executor_id: str
    destination_executor_id: str
    nonce: str
    sequence: int
    expires_at: str | None
    policy_activation_proof_ref: str | None
    policy_activation_proof_full_bytes_digest: str | None
    blob_refs: tuple[str, ...]

@dataclass(frozen=True)
class TransportReadyMarkerV1:
    schema: Literal["trusted-handoff-transport-ready/v1"]
    store_namespace: str
    handoff_id: str
    transport_id: str
    consumer_task: int
    source_executor_id: str
    destination_executor_id: str
    nonce: str
    sequence: int
    transfer_manifest_digest: str
    transport_seal_digest: str
    policy_activation_proof_ref: str | None
    policy_activation_proof_full_bytes_digest: str | None

@dataclass(frozen=True)
class SourceDirtyAllowanceV1:
    repository_relative_path: str
    expected_status: Literal["added", "modified", "untracked"]
    expected_content_digest: str

@dataclass(frozen=True)
class ExpectedTaskOutputV1:
    status: Literal["modified", "added", "untracked"]
    content_bytes_b64: str
    content_digest: str

@dataclass(frozen=True)
class TaskOutputTransitionV1:
    repository_relative_path: str
    phase: Literal["task28-register", "task29-promote"]
    output_kind: Literal["catalog", "audit", "evidence-cas", "contract"]
    precondition: Literal["equals-base-bytes", "must-not-exist", "absent-or-byte-identical-cas"]
    expected_pre_digest: str | None
    allowed_transition: Literal["replace-with-exact-bytes", "create-exact-bytes", "retain-or-create-exact-cas"]
    expected_post: ExpectedTaskOutputV1
    candidate_ref: str | None

@dataclass(frozen=True)
class AllowedTaskOutputsV1:
    schema: Literal["trusted-allowed-task-outputs/v1"]
    task_id: Literal[28, 29]
    phase: Literal["task28-register", "task29-promote"]
    base_commit: GitObjectIdV1
    task_output_policy_digest: str
    protected_paths_digest: str
    source_dirty_inputs: tuple[SourceDirtyAllowanceV1, ...]
    transitions: tuple[TaskOutputTransitionV1, ...]
    candidate_refs: tuple[str, ...]
    new_candidate_refs: tuple[str, ...]
    idempotent_candidate_refs: tuple[str, ...]
    expected_post_tree_digest: str
    canonical_digest: str

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

`PolicyActivationProofV1`的三个`EmbeddedCanonicalBytesV1`分别携带ACTIVATE intent、activation marker与ACTIVATED entry的完整canonical JSON bytes（base64）及SHA-256；verifier必须解码、重算canonical form/digest/previous-entry chain，并验证intent→marker→activated单向引用。proof同时绑定completion record、policy commit、HEAD/fixed ref最终值、core policy record/pointer/export-request refs+full-bytes digests、profile、Task18 toolchain build record、pinned Git executable/view、activation trust root与broker signer。activation签名消息逐字为UTF-8 domain tag `ai-video-reverse-engineer/policy-activation/v1`、单个NUL字节、再接除`broker_signature`外的完整proof canonical bytes；`signing_key_purpose/signature_domain`必须与activation registry不可变注册项完全相同。`policyActivationProofRef = sha256(full signed proof bytes)`，ref/digest只存在于外层CAS引用，不写回proof自身，因而无自引用。journal的ACTIVATED entry只绑定activation-independent core handoff；broker在终态后签proof，再发布含core+proof refs的`Ticket23PolicyHandoffV1`与`ActivatedPolicyExportRequestV1`。后置envelope不参与proof digest，依靠自身full-bytes digest及与proof中core字段逐项相等来闭合，避免proof↔request摘要环。

transport与activation trust root必须作为一个联合配置加载：每个`SigningKeyRegistrationV1`固定purpose、signature domain、registry entry bytes、raw key bytes digest/fingerprint，以及certificate/allowed-signers wrapper解析后的leaf public-key bytes digest/fingerprint。联合loader在Task2 registry load、Task14 broker/profile启动及每次sign/verify前双向比较两组注册项，要求key ID集合、raw public-key digest/fingerprint集合、resolved leaf public-key digest/fingerprint集合全部不相交；缺purpose/domain、同一key换ID、同ID换wrapper、不同certificate wrapper解析到同一leaf key、跨purpose signer或任一解析歧义都在proof/seal/ready/authorization首次写入前fail closed。broker activation signer只能从purpose=`policy-activation` registry slot加载；orchestrator transport signer只能从purpose=`transport-seal` registry slot加载。`TransportSealV1`的签名消息使用不同前缀`ai-video-reverse-engineer/transport-seal/v1\0`加除`signature`外canonical bytes；两类verifier都必须把expected purpose/domain纳入验签，不能只验证Ed25519数学签名。

`TrustedHandoffStoreV1.open_from_environment()` 要求 `$TRUSTED_HANDOFF_STORE_ROOT` 为编排器验证的绝对、no-symlink、权限/owner/项目租约匹配的外部 durable store。普通producer只`publish-local`一次CAS/record/pointer，再发布不含路径/transport identity/密钥的`HandoffExportRequestV1`；但Task22必须先写store外broker quarantine并持`BrokerLifecycleProofV1`发布，Task27必须经`PolicyPublishTransactionJournalV1`到ACTIVATED后，由broker发布`PolicyActivationProofV1`、`Ticket23PolicyHandoffV1`与`ActivatedPolicyExportRequestV1`，二者禁止调用普通publish捷径。core request绑定record/pointer full bytes digest、精确consumer set与binding-set digest；activated policy request再绑定core request、policy handoff envelope与activation proof ref/full-bytes digest。producer与orchestrator只通过已激活、内容寻址request ref交接，不共享shell变量、临时根、trap或路径capability。编排器从固定source-store trust boundary重开request/record/pointer/CAS/proof并全量重算，拒绝producer manifest路径或自报digest，再针对每个consumer执行`export→upload→sign-seal→install-transport`。policy transport的`TransportJobV1`、`HandoffTransferManifestV1`、`TransportSealV1`与`TransportReadyMarkerV1`全部必须携带同一proof ref/full-bytes digest；product transport对应两字段必须为`None`。`export`产生无路径manifest/closure；`upload`no-replace导入并重算；`sign-seal`绑定完整identity/digests/proof；`install-transport`以temp+`linkat`no-replace并fsync。目标已存在仅byte-identical才幂等。transport与activation proof验签根只来自固定registry policy。consumer按`fetch→import→finalize→read`重算全部identity，并离线验证proof内嵌三份canonical bytes、broker签名与本consumer/destination transport绑定；另一consumer seal不能复用。

`TransportJobV1` 只是在 `run-transport` 进程内由已验证 export request 和完整显式 CLI 参数构造的不可变调用对象，不得序列化、持久化或作为 producer transport manifest；三个 root path、signing-key path/value 与 FD number 都不进入任何 record、request、manifest、seal、digest 或日志。共享子命令不能省略每个 consumer 的参数表，也不能改变底层四步的验收与独立签名边界。producer OS credential/domain 中必须不存在 signing key mount/FD、私钥、agent/socket 与可读 orchestrator `/proc`；只有 orchestrator domain 得到 broker no-follow 打开的 `signing_key_fd`，且它不能接收或重新信任 producer checkout/boundary/stage 路径。

`AllowedTaskOutputsV1`不是授权调用方任意放宽clean检查，也不是从当前dirty树学习许可；它是Task7 pinned view按治理固定`task-output-policy.json`、明确phase与外部只读candidate bytes一次性封存的深不可变transition plan。`source_dirty_inputs`只允许policy预列的实现源码/任务记录路径（Task28测试文件；Task29测试文件、issue record与README），逐项绑定封存时status与bounded bytes SHA-256并要求全过程不变；事务输出只能出现在`transitions`，每条固定precondition、唯一allowed transition、内嵌expected post bytes及digest，调用方不能增加路径或重新分类。Task28的catalog precondition必须是与`base_commit` blob逐字相同，audit必须不存在；七个唯一candidate refs按CAS路径一一映射，`new_candidate_refs`与`idempotent_candidate_refs`不相交且并集恰为七项，前者路径写前必须不存在，后者只能是已存在且full bytes SHA-256等于candidate ref，managed evidence root不得出现二者并集之外的新增dirty对象。Task29的contract写前必须逐字等于base blob，并在promote前绑定预计算的完整post bytes/digest；写后必须逐字等于该expected bytes，不能以“当前contract”重新计算。index必须为空，任何rename均拒绝，symlink或逃逸路径一律拒绝；policy同时固定受保护集合（至少plan/spec、profiles、trust、registry、revocations、policy发布/验签代码）并写入摘要。

Task28的六个自动collect与E4 collect只把canonical candidate bytes写到task-local外部staging root，绝不提前写repository evidence/CAS/catalog/audit；七refs及预计catalog/audit bytes就绪后，`plan-register-transition`从base blob与staged candidates封存唯一manifest。Task29在任何promotion write前由`plan-promotion-transition`从base contract blob和verified七refs计算唯一expected contract bytes并封存manifest。`verify_fixed_ref(..., allowed_task_outputs)`及事务入口只接受该manifest原始canonical bytes/digest；写前、取得catalog/contract lock后且尚未创建temp/write、每个持久写完成后都重开同一pinned view并按同一transition规则验证，不存在refresh/replace-owned-output API。Task28额外在register前复核candidate refs及new/idempotent集合；Task29额外在promote入口复核expected contract bytes。任何预脏catalog/contract、预建audit、额外格式合法CAS、source-dirty变化或post digest不符均在对应授权写前fail closed；不再要求整个repository clean，粗粒度`git status --clean`也不能恢复授权。

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
- Create from governance input: `.scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-registry.json`
- Create from governance input: `.scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-allowed-signers`
- Create from governance input: `.scratch/unified-remix-canvas-generation/acceptance/trust/task-output-policy.json`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/registry.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/errors.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_handoffs.py`

**Interfaces:**
- Consumes: 规格 4、6.1、7、9、10、14 和既有六字段 pointer。
- Produces: 上述immutable类型（含`SigningKeyRegistrationV1`、`SourceDirtyAllowanceV1`、`ExpectedTaskOutputV1`、`TaskOutputTransitionV1`、`AllowedTaskOutputsV1`、`BrokerLifecycleProofV1`、`Task26CompletionRecordV1`、`PolicyPublishTransactionJournalV1`、`PolicyActivationMarkerV1`、`PolicyActivationProofV1`、`Ticket23PolicyHandoffV1`、`ActivatedPolicyExportRequestV1`、transfer/seal/ready schemas）、Git OID codecs、owning-FD context、`load_disjoint_signing_domains(transport_registry, activation_registry) -> VerifiedSigningDomainsV1`、`verify_policy_activation_proof(proof_bytes, signing_domains, expected_transport) -> VerifiedPolicyActivationV1`、`TrustedHandoffStoreV1.open_from_environment()`、`HandoffRegistry.publish_local/publish_export_request/fetch/import_/finalize/read/read_product_subject`；普通producer CLI保留`verify-root/publish-local/publish-export-request`，但schema明确禁止Tasks22/27使用。编排面CLI为`verify-signing-domains/verify-export-request/export/upload/sign-seal/install-transport/run-transport`，consumer/生命周期CLI为`verify-cache/fetch/import/finalize/read/read-product-subject/verify-policy-activation/cleanup-owned-tree`。policy consumer必须联合加载transport+activation registries/allowed-signers，先证明key ID、raw与resolved public-key集合互斥，再从transport closure取得proof bytes并按固定activation purpose/domain离线验签，不接root journal路径或FD。`run-transport`共享完整五步且每次显式接收全部transport参数。`read-product-subject`从verified product-source派生精确`git://product@<40-sha>`；任何缺失或未通过activation proof的policy handoff均不能被fetch/finalize/read。

- [ ] **Step 1:** 写 `test_replay_binding_excludes_catalog_contract_and_observation_time`、`test_operation_snapshot_binds_catalog_contract_and_replay_binding`、`test_named_digest_order_is_identical_for_different_creation_order`、`test_duplicate_name_and_illegal_empty_set_reject`、`test_product_source_rejects_host_path`、`test_v1_is_not_upgraded`；policy `GitObjectIdV1` 覆盖 sha1/40、sha256/64 正例及未知算法/长度错配，产品 `ProductGitObjectIdV1` 只接受 sha1/40，并把 sha256/64、39/41 位、任何 `extensions.objectFormat`、带 algorithm 段的 product subject 和 Git ID 塞入 `NamedDigest` 全部作为 RED。另写 handoff RED tests：`Ticket23ProductSourceHandoffV1` 的 record/source/view 三份 product commit 必须相同，record 不含自摘要且 full canonical bytes SHA-256 精确等于 pointer `recordRef/recordDigest`；`read-product-subject` 只从已验证 product-source CAS 解码固定 SHA-1 subject，拒绝调用方自报 commit、objectFormat、SHA-256 product object 或篡改 CAS；policy record/pointer 的 producer commit 独立覆盖 sha1/sha256。owning handle 拒绝 JSON/pickle/copy；store root 非绝对、symlink、权限/owner/租约不符均拒绝；CAS/pointer/request no-replace；两个 producer 并发发布不同 record/request 时恰一方成功、另一方稳定失败，最终 bytes 不混合；相同 bytes 幂等接受。transport 用固定测试签名者覆盖两个 consumer 各自独立底层四步与 `run-transport`，断言 orchestrator 必须从 source store 按 request ref 重算 closure、拒绝非 CAS/非规范/未通过 full-bytes digest 认证的 manifest/ref，且不能接收 producer manifest/path；未 install seal 时不可 fetch、相同 `transportId`+相同 bytes retry 幂等、不同 bytes 不覆盖；一个 consumer 的 seal 用于另一 consumer，或 request/nonce/sequence/consumer/destination/expiry/签名/manifest 任一不符均 fail closed。policy transport另断言proof ref/full-bytes digest在policy handoff/activated request/job/manifest/seal/ready六处完全相等；篡改三份embedded bytes/digest/previous chain、proof ref/signature/core record/pointer/request、commit/HEAD/fixed ref、completion record、operation/state/profile/toolchain/pinned Git view/trust root、consumer/destination任一项，或漏任一entry，均在fetch/finalize/read前fail closed；product transport出现非空proof字段也拒绝。signing-domain RED矩阵必须覆盖同一raw/leaf公钥换key ID、相同key ID换wrapper、同fingerprint不同certificate wrapper、transport key签activation proof、activation key签transport seal、purpose/domain缺失或错误，所有case在proof/seal/ready/authorization writes=0时拒绝。`AllowedTaskOutputsV1`正例只允许policy预列实现源码dirty保持原status/digest，以及catalog/contract从base bytes按sealed expected post bytes迁移、audit absent→create-exact、七candidate CAS按exact-ref新增或byte-identical幂等；负例覆盖预脏catalog、预脏contract、预建audit、额外格式合法CAS、candidate refs非七项/重复/集合不闭合、source digest漂移、expected post bytes换绑、phase/precondition/transition错配、刷新/替换manifest、未声明或protected路径、任意staged/index、任意rename、symlink/`..`逃逸，全部在授权写=0时拒绝。`cleanup-owned-tree` 覆盖只读树成功、广根/symlink/清理期间 identity swap/残留失败，且失败时 EXIT trap 保持安装；多根 cleanup 回归分别注入“首根失败/次根成功”“首根成功/次根失败”，两种情况都断言两根 helper 调用数各为 1、最终非零且 trap 仍安装。再覆盖Task22 `BrokerLifecycleProofV1`必须完整绑定待发布record payload在内的staged digest set，proof缺失、签名错误或staged bytes换绑时broker在首个durable write前拒绝；`Task26CompletionRecordV1`只接受broker签名PASS payload。
  `AllowedTaskOutputsV1`严格拒绝任何staged/index变更与任何rename，即使路径本身在policy内；实现源码只允许manifest绑定的unstaged added/modified/untracked并保持不变，事务输出不允许从当前dirty状态学习precondition或expected post digest。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_handoffs.py`；预期缺类型与 handoff owner/registry 而 RED。
- [ ] **Step 3:** 实现严格 `NamedDigest`、两条互不扩权的 Git OID codec、规范 JSON 与两个 policy/snapshot digests；product record/source/proof 只走 `ProductGitObjectIdV1`，policy record/pointer 才走通用 `GitObjectIdV1`，bindings 仍只允许 SHA-256。实现外部 authenticated handoff store：blob/record/pointer/export request/proof/envelope 以 full bytes digest/no-replace/fsync 发布。`trusted-handoff-transfer/v1` 严格包含 export request digest、store namespace、handoff/transport ID、consumer task、record ref/full-bytes digest、pointer name/bytes digest、有序 blob refs、source/destination executor ID、nonce/sequence/expiry与policy-only activation proof ref/full-bytes digest。transport与activation registries的每个key固定`SigningKeyRegistrationV1` purpose/domain及raw/resolved key identity；`load_disjoint_signing_domains`解析allowed-signers/certificate wrapper到leaf key后执行双向集合不相交检查，任一失败在store写入前终止。`TransportSealV1`与`PolicyActivationProofV1`分别按两个固定domain-tag+NUL+canonical payload签名和验签；只有匹配purpose的slot可加载对应私钥FD。`verify-policy-activation`离线重算proof full bytes ref、按联合trust domains验证broker签名、解码三份embedded canonical bytes及intent→marker→activated链，并逐项匹配core artifacts/refs/final values。只有编排面`sign-seal`从broker注入的只读FD读取仓库外transport私钥，拒绝key path/value CLI。`upload`先导入并重算closure；`sign-seal`只签该upload receipt指向的manifest；`install-transport`以两次temp+`linkat`no-replace安装manifest/seal，若第二次安装失败则transport不可见，清理未入口temp；只有两文件完整且摘要互绑、policy proof已包含并验签时才把包含三者digest的ready marker也用temp+`linkat`no-replace安装，并依次fsync文件和父目录。已存在manifest/seal/marker只接受完整bytes全部一致的幂等retry，任一不同拒绝；崩溃恢复不得观测到半安装运输。`run-transport`先从source-store owning capability验activated request/policy handoff/core request/record/pointer/CAS/proof与联合signing domains，再按参数构造深不可变`TransportJobV1`并顺序调用四个公开transport步骤，不增加默认值或旁路；`fetch/import/finalize/read`从target store的ready marker定位同一`transportId`，重算全部bytes并核对consumer/destination/replay/proof/domain状态；`finalize`之前`read`必须拒绝。`task-output-policy.json`固定Task28/29的源码dirty角色、每phase事务输出角色、precondition与allowed transition；pinned view只从base blobs、外部candidate canonical bytes及固定policy构造`AllowedTaskOutputsV1`，catalog/contract/audit/CAS不得用当前dirty bytes填充expected字段，也不接受调用方扩展路径或manifest refresh。实现上述no-follow`cleanup-owned-tree`，所有后续task harness只调用该清理入口；多根shell cleanup必须按顶层聚合规则逐根尝试。并实现新增envelope/state类型与producer-class gate：Task22普通publish CLI必须拒绝且只接受lifecycle-proof-gated broker publish；Task27普通publish CLI必须拒绝，core handoff只可由journal transaction写入，activated envelope/request只可由broker在签发proof后写入；缺失或未验证proof的policy不能transport/fetch/finalize/read。
- [ ] **Step 4:** 重跑 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e2_policy_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_handoffs.py`；预期 GREEN 且 production profile 仍为空。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{e2_policy.py,handoffs.py,registry.py,errors.py} .scratch/unified-remix-canvas-generation/acceptance/tools/{trusted_handoff_store.py,trusted_handoff_orchestrator.py} .scratch/unified-remix-canvas-generation/acceptance/trust/{handoff-transport-registry.json,handoff-transport-policy.json,handoff-transport-allowed-signers,policy-activation-registry.json,policy-activation-allowed-signers,task-output-policy.json} .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_{e2_policy_v2,handoffs}.py && git commit -m "feat: define trusted replay operation policy"`

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
- Produces: `ToolchainPackage.open`、`measure_platform`、`compare_platform_identity`，以及 `TrustedGitExecutorV1.open(repository, pinned_git, expected_git_digest, empty_home, empty_xdg, empty_hooks) -> TrustedGitExecutionViewV1`。view 在单一进程生命期内持有 executable/repository/config/attribute/worktree snapshot/private object upper handles，并提供 `resolve_ref(ref) -> GitObjectIdV1`、`resolve_commit_parent(commit) -> GitObjectIdV1`、`cat_file(object_id, expected_type, byte_budget) -> BoundedGitObjectV1`、`compare_blob_bytes(commit, path, expected_digest) -> bool`、`plan_register_transition(task_output_policy, staged_candidates) -> AllowedTaskOutputsV1`、`plan_promotion_transition(task_output_policy, validated_batch) -> AllowedTaskOutputsV1`、`verify_allowed_task_transition(manifest, checkpoint) -> None`、`commit_detached(paths, message, identity, expected_old_head) -> GitObjectIdV1`、`commit_install_ref(paths, message, identity, expected_old_head, ref, expected_old_ref) -> GitObjectIdV1`、`import_pack_strict`、`fsck_strict`、`bundle_verify`、`verify_fixed_ref`；每个方法只调用同一已验证 Git FD/proc-fd entrypoint。transition plan方法只允许由`register-batch`/`promote-transaction`持有的同进程capability调用一次，CLI不暴露plan/refresh/replace-owned-output入口；公开CLI严格为`trusted_git.py commit-detached/commit-install-ref/verify-fixed-ref/read-result`。broker内的Task26 completion publisher直接持有同一Python capability，不新增raw Git CLI。所有入口只能接收Task18 materialized Git entrypoint+digest与当次空home/xdg/hooks/config/attribute snapshot roots；`verify-fixed-ref`不使用whole-repository clean，而按Task2治理固定policy重验fixed ref、protected inputs、index边界、source dirty state及phase transition pre/postconditions。`commit-install-ref`在同一process capability中完成private commit、原root post-check、对象O_EXCL append-only promotion、CAS ref install与read-back verify，不能拆成可竞态的commit/update-ref/verify三进程。

- [ ] **Step 1:** 写 closure/uv identity/advisory/dynamic library/kernel ABI/PATH marker RED tests；另写 trusted Git RED，逐项断言 Global Constraints 的批准规格矩阵。负例至少覆盖：可执行 bytes/digest/version 不符、宿主 PATH fallback；config/config.worktree 的 NUL、非法 UTF-8、重复 section/key、续行、畸形 quoted subsection、全部危险键族、未知键、非法结构值/惰性 name/value；`objects/info/alternates`、`objects/info/http-alternates`、`refs/replace/**`、`info/grafts`、`shallow`、`.promisor`、真实/symlink/special hook；`info/attributes` 只要存在（含空文件）即拒绝，以及根/子目录 `.gitattributes` 的 `filter`、`working-tree-encoding`、`ident`。注入/旁路覆盖 `GIT_DIR/GIT_WORK_TREE/GIT_COMMON_DIR/GIT_OBJECT_DIRECTORY/GIT_ALTERNATE_OBJECT_DIRECTORIES/GIT_REPLACE_REF_BASE/GIT_SSH/GIT_SSH_COMMAND/SSH_AUTH_SOCK/GIT_ASKPASS`、`GIT_CONFIG_COUNT/KEY_n/VALUE_n/PARAMETERS`、replace object、prompt、optional locks、跨文件系统 discovery、autocrlf、network/credential/helper；这些负例均在 Git spawn=0 或当前阶段 visible writes=0 时拒绝。另覆盖 preflight 后 config/attribute/path/inode/bytes 替换、对象名碰撞、部分 object promotion、expected_old_head/ref 不符。`AllowedTaskOutputsV1`正例覆盖policy预列实现源码modified/added/untracked保持原digest、catalog/contract base→exact-post、audit absent→create与七CAS exact新增/幂等；负例覆盖预脏catalog/contract、预建audit、额外格式合法CAS、七refs集合不闭合、source dirty漂移、phase/precondition/transition/expected-post换绑、任何manifest refresh、未声明/protected、任意staged/index、任意rename、symlink/`..`、base commit替换，逐checkpoint全部拒绝。正例 spy 必须逐项观测只读空 HOME/XDG、`GIT_CONFIG_NOSYSTEM=1`、`GIT_CONFIG_GLOBAL=/dev/null`、`GIT_ATTR_NOSYSTEM=1`、`GIT_OPTIONAL_LOCKS=0`、`GIT_TERMINAL_PROMPT=0`、`GIT_DISCOVERY_ACROSS_FILESYSTEM=0`、`GIT_NO_REPLACE_OBJECTS=1`、`LC_ALL=C`、toolchain-only PATH、snapshot-only `--git-dir/--work-tree`、private object upper，并在每个适用命令观测 `--no-replace-objects`、`-c core.hooksPath=<受信空目录>`、`-c core.autocrlf=false`；commit 还必须观测 `--no-gpg-sign --no-verify` 与固定 author/committer identity/date。任何 gpg/hook/filter/ident/replace/credential/prompt/agent/network/maintenance canary 调用数为零。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_toolchain.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_platform_identity.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_trusted_git.py`；预期 `TrustedGitExecutionViewV1`、snapshot 与全部 Git 方法缺失而 RED。
- [ ] **Step 3:** 实现 exact bytes/build identity 和 offline security snapshot。`open` 先 no-follow 持有原 repository/config/config.worktree、可能存在的 attributes 路径、worktree 与 executable handles；用 `BoundedStableReader` 按 Global Constraints 中逐字列出的三类 config 矩阵验证并只把 runner 常量生成的最小 config 写入只读 snapshot，所有惰性原值丢弃。`info/attributes` 或 external attributes 存在立即拒绝；只读 empty-attribute snapshot 明确固定其缺失，subject tree 的 `.gitattributes` 只在拒绝 `filter/working-tree-encoding/ident` 后进入 worktree snapshot，Git 永不重开原路径。每次 spawn 从空环境精确设置 `HOME/XDG_CONFIG_HOME/GIT_CONFIG_NOSYSTEM/GIT_CONFIG_GLOBAL/GIT_ATTR_NOSYSTEM/GIT_OPTIONAL_LOCKS/GIT_TERMINAL_PROMPT/GIT_DISCOVERY_ACROSS_FILESYSTEM/GIT_NO_REPLACE_OBJECTS/LC_ALL/PATH`，清除规格列出的 Git/object/SSH/askpass/config 注入，且每个适用命令添加 `--no-replace-objects -c core.hooksPath=<empty> -c core.autocrlf=false`。所有方法完成后、任何 object/ref/result 发布前重验原 handles 的 dev/inode/digest；`commit_detached`/`commit_install_ref` 只接收显式 path、typed expected_old_head、固定 identity/date 与 `--no-gpg-sign --no-verify`，对象先写 private upper。policy object promotion 对每个 loose/pack object以 digest/object-id 验证、temp+fsync+`linkat`/O_EXCL append-only 安装；已存在只接受 byte-identical。`commit_install_ref` 在 repository lock 下再次核对 expected head/ref/index/worktree，fsync recovery journal与原 index backup，再安装 snapshot index并用单个 Git ref transaction CAS 更新指定 HEAD ref+fixed ref；ref transaction失败立即恢复原 index，crash recovery在任何新授权读取前按 journal完成恢复或验证成功态，最后 read-back ref/index/clean tree 才输出 result。任何碰撞/中断不产生可用 result，未引用 temp由恢复流程删除；Task 27 是否可对消费者授权另由其外层持久 publish journal/activation marker 决定。
- [ ] **Step 4:** 重跑 Step 2 全命令；预期所有 config 三分类、`info/attributes` 存在、`.gitattributes ident/filter/working-tree-encoding`、replace-object、autocrlf、prompt、optional locks、discovery、environment/path race 与外部对象源用例逐项 GREEN；所有 Git 方法只出现 Task 18 executable digest，恶意执行 canary 与失败阶段 visible write 数均为零。
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
- Produces: `RawGitLayoutInspectorV1.inspect(boundary, subject_commit: ProductGitObjectIdV1) -> RawGitInspectionResultV1`、`CanonicalLogicalRepositoryTopologyV1.from_inspection(inspection)`、`build_product_source(boundary, inspection, subject_commit: ProductGitObjectIdV1) -> ProductRepositorySourceV1`、`finalize_raw_git_proof(inspection, container_digest) -> RawGitInspectionProofV1`、`ProductSourceLoader.load(source, proof_ref, expected_source_digest) -> ValidatedProductRepositoryContainerV1`；`build_product_source.py build --stage-only` 只接受 Task 22 broker-authenticated sealed closure、typed 小写 SHA-1/40hex subject 与 checkout/gitdir/commondir owning roots，不提供 raw Git/read-head 子命令或 algorithm 选择参数，在同一进程严格执行 open handles→raw inspect→broker readonly closure import→path-free topology→build→finalize proof→quarantine staged payload。staged digest set绑定 container/build-record/raw-proof及hand-off record payload；broker取得验证该完整集合的`BrokerLifecycleProofV1`后才允许原样发布，proof作为broker授权与审计记录留在生命周期journal中，不改变既有handoff record七字段契约。source artifact 不反向绑定 replay policy；未经 Task 11 proof/container 正式验证不能称为 verified source。

- [ ] **Step 1:** 写 `test_real_linked_worktree_is_inspected_before_normalization_and_proof_binds_container`：真实 `.git` regular pointer、相对/绝对 gitdir/commondir 写法、精确换行/backlink/control bytes 先由 inspector 在 owning-FD 进程内读取；用 test spy 证明这些 bytes 实际被检查，但不出现于 proof/container/build-record/handoff 任一序列化字段或 digest 输入。proof 绑定 path-free logical-topology/profile/rules/budgets/typed SHA-1 product subject/container；只保留 sha1/40 repository/object ID 正例，把 SHA-256 product repository/OID、39/41/64 位、`extensions.objectFormat` 任意出现及带 algorithm 段 subject 都在 traversal 前拒绝。双根测试中同 typed commit 在不同绝对空根、保持相同逻辑关系与形式类别时，只要求 canonical topology/proof 语义字段、container/build-record/handoff 一致，不要求两个根的原始 pointer/control bytes 相同或其 digest 存在。写 path swap/capability serialization 反例；再逐项覆盖规格 §8.2 pointer/ref/config/objects/sidecar/预算/unknown class、非法 UTF-8/NUL/多行/换行、越界/symlink/case-fold/alias/二次重定向/循环/backlink 不精确，全部在规范化或 payload copy 前拒绝；伪造 proof、错 inspector/profile/rule/budget/logical-topology/container binding 也拒绝。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_product_source.py`；预期 RED。
- [ ] **Step 3:** 唯一 source-runner 进程打开并持有三个 handles 后，先调用完整 `RawGitLayoutInspectorV1`；只有 inspection result 成功才把已验证关系派生为 `@checkout/@gitdir/@commondir` 的 path-free canonical logical topology，再固定使用自有有界 SHA-1 loose/pack/idx/tree reader 复制 subject tree 的精确 tracked bytes；不存在产品 SHA-256 分支。原始 pointer/control bytes 仅留在 inspection 调用栈/内存中，不写 artifact、不做 digest；ignored/untracked 不捕获。finalize proof 后，将 container/build-record/proof 与record payload交给 authenticated broker quarantine，fsync staged manifest后关闭handles；该Task不写durable CAS/pointer/request。普通 `.git` directory 仍只发一份 control tree；任何 handle identity/mount/race 变化拒绝。
- [ ] **Step 4:** container manifest 首项绑定 subject、normalized boundary、`boundaryDigest/trackedCheckoutDigest/controlMetadataDigest/sourceDigest`；proof sidecar 的 `logicalTopologyDigest` 只是 `CanonicalLogicalRepositoryTopologyV1` 的规范摘要，并绑定 inspector/profile、全部规则/预算结果、subject/source commit 与最终 container digest；build record 绑定 proof ref/digest、subject/tree/container/source/boundary/logical topology，不含原始 bytes/digest、路径/device/inode/mtime。handoff bindings 恰含 `product-source-container`、`product-source-build-record`、`raw-git-inspection-proof`、`boundary-topology`、`tracked-checkout`、`control-metadata`、`source` 七项；broker lifecycle proof重算并绑定包括该规范record payload在内的完整staged digest set，handoff record本身仍严格保持既有七字段且无自摘要/后置proof字段。loader 重算 container/build-record/proof digests；重跑 tests，预期原始布局恶意 bytes 拒绝、proof/container 换绑、双根 canonical 语义一致性与逃逸反例 GREEN。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{raw_git_inspection.py,product_source.py} .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_product_source.py && git commit -m "feat: bind canonical raw Git product sources"`

### Task 11: 从 verified source 构造 sanitized Git checkout

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/git_subject.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_git_subject.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/verifiers/e2_suite.py`

**Interfaces:**
- Consumes: Task 7 `TrustedGitExecutorV1`/包内 Git、Task 10 `ValidatedProductRepositoryContainerV1`、`RawGitInspectionProofV1` 及 normalized `@checkout/@gitdir/@commondir` boundary；不接受裸 path/cwd/bundle、缺 proof、原宿主 config/attribute 路径或 container 外 control root。
- Produces: `GitMetadataInspector.inspect(container, raw_proof, build_record) -> VerifiedProductRepositorySourceV1`、`SanitizedGitView.build(source, commit: ProductGitObjectIdV1, toolchain_git, destination) -> FixedCheckout`。

- [ ] **Step 1:** 写 proof formal-verification tests：缺失/未知/伪造 inspector/profile/rule/budget/path-free logical-topology digest、proof/build-record/container/typed subject/source commit 任一换绑，以及非 sha1、非 40hex、任何 `extensions.objectFormat` 或 SHA-256 product object 任一出现，均在包内 Git spawn 前拒绝；显式拒绝 proof/build-record 中出现 raw pointer/control bytes、其 digest 或宿主路径字段。再对 canonical container 覆盖 boundary graph、tracked tree、config/packed-refs/refs/objects 预算、Global Constraints完整config三分类、alternates/http-alternates、replace/grafts/shallow/promisor、sidecars、symlink/duplicate/case-fold、外部 object source与race；`info/attributes`只要存在（含空文件）即拒绝，层级`.gitattributes`分别覆盖`filter/working-tree-encoding/ident`，并覆盖replace-object、autocrlf、prompt、optional-lock、discovery与preflight后identity/bytes替换。所有负例断言零`index-pack/fsck` spawn、零verified-source输出；正例逐项断言规格固定environment/argv且所有Git plumbing只经同一executor view。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_git_subject.py`；预期旧 host Git path RED。
- [ ] **Step 3:** `GitMetadataInspector.inspect(container, raw_proof, build_record)` 先重算 proof 与 container bytes，核对 raw inspector 的固定版本/profile/完整规则集/预算结果、path-free canonical logical-topology digest、subject/source commit 和 container binding；再对 canonical `repository/gitdir/common` 做后置 bounded checks。全部通过后只调用同一 `TrustedGitExecutionViewV1.import_pack_strict/fsck_strict/cat_file` 构造 sanitized bare view并重建 checkout；executor 持有 config/attribute/worktree snapshot handles 到 verified source 完成，任何原路径竞态使输出为零。禁止 subprocess/host Git 或直接 `index-pack/fsck/cat-file`，原始危险记录仍不进入 view。
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
- Produces: 精确七方法 API、canonical supervisor schema、proof digest、已批准 `linux-cgroup-v2-seccomp-notify/v1` gate；并冻结 `CredentialIsolatedHandoffRunnerV1.open_pty(role, session_label, profile) -> IsolatedPtyHandleV1`，role 只允许 `producer|orchestrator`，两者不同 UID/GID/user+mount+PID namespace，FD/capability allowlist 只能由 profile 按 session label 解析而不能由调用方自报，唯一跨域 payload 为 authenticated export-request CAS ref。仅`task22-producer`可持有profile固定的provisioner client/RPC FD；root broker独占capability MAC key、shared lower/private upper、quarantine与lifecycle lease/proof journal。Task26完成后controller-only broker endpoint必须物化Task18固定toolchain，并在同一broker进程持有`TrustedGitExecutionViewV1`来验证review evidence和签发completion record；普通reviewed repository capability、host Git或child均不能恢复commit/base/ref/blob事实。root broker另独占与transport signing key分离的policy-activation signing key；只有重验ACTIVATED终态后可签发`PolicyActivationProofV1`，私钥永不注入producer/orchestrator，consumer只持固定公开registry trust root。`task27-producer`只可获broker预验证的completion record只读FD与root-owned policy-journal broker channel FD，不能获worktree provisioner、completion签发或activation签名能力；该channel本身不创建operation journal，首个PREPARED append只能发生在全输入preflight重验后。lease绑定supervisor pidfd、PTY control channel与heartbeat deadline；producer trap只能清理自身scratch，正常退出、SIGKILL/crash/断连/超时均由broker在确认producer终止后独立触发destroy→verify→proof-or-abort→release。

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

- [ ] **Step 1:** 写消息 RED tests；仅允许 `hello/session-created/invocation-created/spawned/exec/write/exit/terminated/empty/invocation-destroyed/session-destroyed/error`，每条含 schema/sessionId/invocationId/seq/kind/scope/objectId/payload/prevDigest/digest；`scope` 仅 `session|invocation` 且必须与 `objectId`、消息 kind 一致。handoff-domain contract tests 断言 role/UID/GID/namespace/profile digest 不同，producer FD allowlist 不含 signing key/agent，orchestrator 只能接收 broker 打开的只读 signing-key FD，任何 path/value/env 形式 key 参数 schema reject；只有 profile 中 `task22-producer` 会话可获得 provisioner RPC，且仅允许固定 create-detached/seal-private-closure/stage-quarantine 方法、固定 project/executor/profile；destroy/verify/finalize-lifecycle-proof/abort-quarantine/release只能由broker在pidfd确认producer退出后内部执行，controller broker RPC另只允许 resolve-proof/publish-quarantine。伪造 session label/token/lease/proof、修改 capability/staged digest、请求删除 shared commondir/lower、publish-before-destroy，或把 provisioner FD/客户端转交 Task 27 producer/orchestrator 均 schema reject。lease contract tests 固定正常退出与 pidfd exit、SIGKILL、PTY 断连、heartbeat timeout、broker restart journal recovery都必须幂等进入 producer-exited→destroy→verify→proof-or-abort→release；错误 token/lease 不能清理其他 lifecycle，proof前 durable store写入数恒为零。
- [ ] **Step 2:** 冻结无丢失与权限模型：继承 `SOCK_SEQPACKET`；seq 从 0 连续且 digest chain；EOF 仅 `session-destroyed` 后；root-owned supervisor 管理 delegated cgroup/namespace/seccomp listener，child 入组后降权；binary SHA-256、ELF build-id、Cargo.lock、executor image digest 绑定 replay policy/receipt。
- [ ] **Step 3:** 冻结唯一机制：clone3 首次入组；seccomp user notification 观察 execve/execveat 与固定 write/network syscall，拒绝 io_uring/bpf/perf 旁路；只读 mounts+writable allowlist；pidfd/waitid+cgroup.kill+populated=0+rmdir。handoff domain 另固定 root-owned broker、预配 `trusted-handoff-producer`/`trusted-handoff-orchestrator` UID/GID、独立 user+mount+PID namespace、`/proc`=`hidepid=2,subset=pid`、`PR_SET_DUMPABLE=0`、空 capability bounding set、`no_new_privs`、exec 前 `close_range(3, UINT_MAX, CLOSE_RANGE_UNSHARE)` 后按 role 重开白名单 FD，以及 seccomp 拒绝 ptrace/process_vm_readv/process_vm_writev/pidfd_getfd/open_by_handle_at。producer mount table不含 key/agent；orchestrator 才以 0400、orchestrator UID 所有的transport key mount no-follow打开 FD；policy-activation signing key只存在于root broker且不进入任一child mount/FD表。worktree provisioner 通过 task22 producer-only 已认证 `SOCK_SEQPACKET` RPC FD 服务；broker 先记录 base OID 与 shared config/refs/packed-refs/logs/objects baseline，再以 detached HEAD 创建专属 checkout/linked gitdir，把 shared ODB lower 只读 bind-mount、全部新对象定向到生命周期私有 upper。broker 用 child 不可见 secret 对 canonical capability+executor/profile+lifecycle/lease 做 MAC，fsync lease journal 后才发布 JSON；pidfd确认producer终止后，destroy只unlink本次backlink并删除checkout/linked gitdir/private upper，永不写shared commondir/lower，然后必须验证shared control/baseline objects逐项未变才能proof-or-abort并release。broker restart 从journal重放未release lease，重复destroy/verify只接受同一lifecycle的已销毁状态。另冻结controller-only `publish/verify-task-completion-record`、`open/resume-policy-journal-channel`与`sign/verify-policy-activation-proof`协议：completion publisher物化Task18固定Git binary/digest及临时toolchain view，在同一broker进程持有`TrustedGitExecutionViewV1`，只能从认证review evidence经`resolve_ref`、`resolve_commit_parent`、有界`cat_file`、`compare_blob_bytes`恢复OID/ref/blob事实并在签名前复验全部handle；普通repository capability和host Git没有对应权限。Task27 completion FD只读且绑定record digest，journal channel FD只允许在全输入preflight重验通过后创建operation并对同operation append+fsync；activation proof只能由broker在重验同一transaction的ACTIVATED entry、最终refs、core artifacts与pinned view identity后签发。任何child不能签发/替换record或proof、截断journal或取得activation私钥。
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
- Consumes: Task 13 approved protocol及Task2 `VerifiedSigningDomainsV1`，不得改机制/schema。
- Produces: `LinuxCgroupV2Containment`、`CredentialIsolatedHandoffRunnerV1`、`run_handoff_domain.py exec-pty/verify-role` 与 profile 固定、只通过 broker RPC 的 `trusted_worktree_provisioner.py`；无 guest proof 的 VM 固定 unavailable。`exec-pty` 必须由 root-owned supervisor broker 先把 session label 解析为固定 role/capability set，再切换到 profile 固定 UID/GID/namespace 后 exec shell；调用方不能自报 UID、mount、key path、provisioner secret 或额外 FD。broker/profile启动必须先联合加载transport+activation registry/allowed-signers，解析raw与certificate/wrapper leaf public key，验证key ID/raw/resolved fingerprint集合全部不相交及purpose/domain正确；失败时childExec=0、proof/seal/ready/authorizationWrites=0。仅task22-producer measurement绑定provisioner client/digest、broker key id与RPC peer；Task26 controller broker endpoint绑定Task18 build record、pinned Git executable/view与临时toolchain view identity；Task27 producer measurement必须证明worktree/completion-signing/activation-signing/transport-signing capability不可见且只绑定broker验证的completion record FD和无持久operation的policy-journal broker channel FD；orchestrator measurement证明completion/journal/provisioner/activation-signing均不可见，只能按profile中purpose=`transport-seal`获得transport signing-key FD。activation signer只从purpose=`policy-activation`的broker slot加载；公开registries可读，两个私钥均不可跨slot/role读取。

- [ ] **Step 1:** 写 leader 0/nonzero/timeout × double-fork/setsid/setpgid/closed-stdio、census/protected digest/macOS unavailable tests；真实 handoff-domain integration 分别从 producer 尝试 open signing-key path、connect agent socket、读取 orchestrator `/proc/<pid>/environ`、读取未列入白名单的继承 sentinel FD、ptrace/process_vm_readv/pidfd_getfd，全部必须得到路径不存在/`EBADF`/`EPERM` 且 broker transcript 证明不同 UID/GID/user+mount+PID namespace。orchestrator 正例只能通过注入transport FD签名；把transport或activation key path/value、额外 FD、同 UID profile、伪造/错配 session label/capability 或未认证 manifest 传入均在 child exec/sign=0 时拒绝。启动矩阵覆盖同一公钥不同key ID、相同key ID不同wrapper、同fingerprint不同certificate wrapper、purpose/domain缺失或错误、transport key装入activation slot、activation key装入transport slot，全部在childExec/proof/seal/ready/authorizationWrites=0时拒绝。producer 直接 exec pinned Git FD/path 或 host Git 必须被 seccomp broker 拒绝，只有 Task 7 executor-issued single-use exec capability 正例通过。provisioner integration另覆盖Task27请求provisioner、客户端/path/digest/RPC peer替换、伪造/重放/字段突变token/lease/proof、create中途失败、checkout/gitdir/upper identity替换、lower写入、对象碰撞/部分closure、共享ref/reflog/config/packed-refs污染；destroy/baseline/proof前durable artifact写入恒为零。completion/journal integration覆盖review evidence非PASS/错commit/ref/plan/spec/producer、伪造record FD、Task27环境变量替代FD、journal截断/跨operation append/broker restart，全部fail closed；另覆盖Task26无Task18 build record/view、pinned Git/config/attribute/repository handle替换时recordWrites=0，以及activation key对producer/orchestrator不可见、非ACTIVATED/错transaction/错final refs/core digests时proofWrites=0、ACTIVATED后broker crash/restart只能重放相同proof bytes。kill points覆盖create、commit、quarantine、destroy、lifecycle proof、publish、activation proof与activated envelope前后，断言reaper清理私有状态、保留shared baseline，并且未满足终态时无可验证授权。
- [ ] **Step 2:** 运行 `cargo test --locked --manifest-path .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/containment/linux-supervisor/Cargo.toml && backend/.venv/bin/python -m pytest -q -m e2_linux_integration .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_containment_linux.py`；预期 RED。
- [ ] **Step 3:** 严格实现 Task 13，无 fallback；proof 缺失为 `E2_CONTAINMENT_CLEANUP_FAILED`。broker/profile在创建role domain前调用Task2联合loader并持有`VerifiedSigningDomainsV1`；activation slot只能解析purpose/domain固定的activation key，orchestrator slot只能解析purpose/domain固定的transport key，任一ID/raw/resolved key交集或wrapper歧义均停止且零授权写。`run_handoff_domain.py verify-role` 输出 canonical domain measurement（profile/supervisor digest、role、UID/GID、user/mount/PID namespace inode、proc mount flags、seccomp/capability/dumpable、closed-FD census、联合signing-domain digest，以及适用角色的 provisioner client/RPC peer 或transport signing-key FD measurement），`exec-pty` 仅在 measurement 匹配后进入长生命周期 PTY；transport signing-key FD 只在 orchestrator allowlist，policy-activation signing key只在root broker地址空间，producer/orchestrator永远无对应mount/FD。broker 只为已验证 `TrustedGitExecutionViewV1` 发放绑定 executable/view/method digest 的 single-use exec capability，直接 Git exec 无 token即拒绝；Task26 controller endpoint在broker内物化Task18 toolchain/view并通过同进程有界接口恢复与复验全部reviewed Git事实。provisioner client 不持有 MAC/lease key，只把 canonical request 送给 root broker；broker 在 profile 固定 entry/byte budgets 内 no-follow 枚举 shared config/refs/packed-refs/logs 与既有 objects，按相对路径、mode、size、SHA-256 排序生成 control/object baselines，超预算即零创建。`create-detached` 原子建立 readonly lower/private upper、登记并 fsync 包含 producer scratch-root identity 的 lease journal、签发 capability；`seal-private-closure` 只接受 expected base/new commit，调用 Task 7 pinned executor 对 upper+lower 做 strict fsck、拒绝 lower 改写/既有 object 覆盖/对象名碰撞，再为 source builder 提供 broker-authenticated readonly closure stream。canonical artifacts只写 durable store外的root-owned quarantine；broker先fsync staged digest manifest，等待pidfd/waitid确认producer退出后才完成destroy/verify/baseline检查、签发`BrokerLifecycleProofV1`并release lease。controller的publish请求必须重验proof签名、session/lifecycle/lease与quarantine bytes，随后才no-replace+fsync发布CAS/record/pointer/request；任一失败abort quarantine且durable store零写入。Task27达到ACTIVATED后，仅broker可从root journal重读intent/marker/activated canonical bytes，重验final refs/core artifacts/completion及pinned Git view identity，用activation domain-tag确定性签发proof；broker restart对相同terminal bytes只接受相同proof CAS/ref，任何producer/orchestrator请求签名或跨purpose key均拒绝。
- [ ] **Step 4:** 重跑 Step 2 全命令；预期首个exec至invocation-destroyed/session-destroyed transcript连续，leaf rmdir proof在下一个invocation前成立，后代全清；producer的key/agent/env/FD/ptrace探针不可达，orchestrator transport signing-key FD正例通过但activation key同样不可达；联合registry的key ID/raw/resolved leaf集合互斥且purpose/domain逐项正确，任一复用case零child/proof/seal/ready/authorization写；Task22每个kill point最终都有broker journal/reaper destroy+verify+proof-or-abort，shared lower/control baseline零变化、private state为ENOENT；Task26 completion只能由匹配Task18 pinned view的broker签发，Task27 journal FD只能由匹配profile/session获得；restart后completion、journal与ACTIVATED后的activation proof仍验证同一digest chain并byte-identical幂等。
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
- Produces: canonical E1/E2 artifact schemas、collectors/dispatch、`RegistrationBatchPreflight.validate(root, product_handoff, policy_handoff, expected_policy_commit, candidates) -> ValidatedRegistrationBatchV1`，以及 `AtomicBatchRegistrar.commit(validated_batch, output_transition)`；generic CLI `collect`/`register`/`register-batch`。`collect`/`collect-e4`可用`--candidate-output-root --defer-repository-writes`只在task-local staging生成canonical bytes/ref，禁止提前写repository CAS/catalog/audit；`validate-many --candidate-root`只重算该staging中的七项canonical bytes/ref。`register-batch`在同一进程构造typed preflight并调用Task7 `plan_register_transition`一次性封存`AllowedTaskOutputsV1`，批量验证handoffs/commit/source/dependencies/profile/verifier/subject/policy、恰好七项candidate refs及全部artifact canonical bytes/receipt bindings；随后同一对象贯穿入口、catalog lock后写前及每次写后检查，成功后才安装本轮exact CAS/audit bytes并一次原子更新catalog七pointers，任一候选或transition错误时CAS/audit/catalog writes均为零。collect result JSON/capture合同保持不变；`capture_collect_result.py read-catalog-ref`以profile/verifier/subject精确寻址，重算catalog pointer与CAS canonical bytes后只输出唯一`sha256:<64hex>` ref，供后续晋级显式传参。

- [ ] **Step 1:** 保留 E1/E2 collect/capture/register RED tests；新增 batch preflight RED：product/policy handoff、typed fixed policy commit、固定 sha1/40 product subject/source binding、container/proof/dependency/network-proof ref、profile/verifier/subject、replay-policy binding、artifact canonical bytes/receipt semantic binding 任一不符，或产品出现 SHA-256/objectFormat、七候选缺失/重复/多余时，`preflightSuccess=0`、catalog/pointer writes=0；预脏catalog、预建audit、额外格式合法CAS、candidate path预存不同bytes、七refs的new/idempotent集合非不交并集、持锁后transition漂移均CAS/audit/catalog writes=0。全部正确时返回深不可变、固定顺序 batch与单个phase-aware transition plan，允许实现测试源码dirty，register-batch只安装七候选对应CAS并恰一次atomic catalog commit。另为 `read-catalog-ref` 写缺失/重复/错 subject/悬空 CAS/篡改 bytes/未知字段 RED，成功 stdout 必须只有规范 ref。
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
- Produces: `evaluate_operation`、`AtomicContractProjector.commit(expected_operation_snapshot, overrides)`、`PromotionTransaction.run(request, policy_cache_capability) -> PromotionTransactionResultV1`，以及 `validate_acceptance.py promote-transaction/read-result`。`promote-transaction` 在唯一进程中 no-follow 打开 finalized policy cache 为 owning dirfd，严格重建 SHA-1-only `ProductGitObjectIdV1` subject、独立 `GitObjectIdV1` policy commit 与内存 `ValidatedPromotionBatchV1`，构造初始 `AcceptanceOperationSnapshotV1`，先从base contract blob与verified七refs计算完整expected post bytes并调用Task7 `plan_promotion_transition`一次性封存`AllowedTaskOutputsV1`；Task29测试文件、issue record与README仅作为source dirty按初始status/digest保持不变。进程持有cache、repository与该不可变plan直到同一contract lock内完成入口、cache/pointer/record/manifest/seal/CAS/catalog、锁后写前及写后复核，contract最终bytes必须逐字等于plan中的expected bytes/digest。随后按预计算candidate contract构造post-projection snapshot并reconcile，只有最终状态确定后才一次temp+fsync+atomic replace，绝不暴露promotion/reconcile中间态。任何preflight/transition JSON只允许写到`--diagnostic-output-root`供审计，绝不被重新读入、刷新或充当授权token。`read-result`只从strict `PromotionTransactionResultV1`返回请求字段。

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
- Ephemeral only: producer-only 0700 `TASK22_BOUNDARY_PARENT/{registered-boundary.json,sealed-object-closure.json,staged-artifacts.json,toolchain,git-home,git-xdg,empty-hooks,git-config-view,git-attribute-view,git-write-result.json}`、provisioner 专属 checkout/linked-gitdir/private-object-upper、durable store 外 root-owned quarantine 与 lifecycle lease/proof journal entry；controller 只接收内容寻址 `BrokerLifecycleProofV1` ref；orchestrator-only 0700 `TASK22_ORCHESTRATOR_PARENT/{c23,c25,c27,c28}` stage roots。共享 commondir/object lower 只读且只验证 baseline；quarantine 在 publish 前对 consumer 不可见，失败时由 broker 删除。两套 harness 不共享 UID/GID、namespace、FD、变量、临时根或 trap，不跟踪、不跨 Task 复用。

**Interfaces:**
- Consumes: Tasks 7/18 的 `TrustedGitExecutorV1` 与 fixed Git entrypoint/digest、Task 14 credential-domain runner/profile 及 profile 固定的 provisioner client/digest/broker RPC capability、Task 10 registered-boundary builder CLI、Task 11 inspector contract 与产品实际源码；不消费 fixture digest。
- Produces: Task 22 producer-only credential domain 内部专用、显式 detached 于 capability `base_commit` 的真实 linked worktree与 clean SHA-1 subject commit；provisioner canonical JSON 携带 broker MAC、lease、专属 checkout/linked-gitdir/private-upper identities、只读 shared commondir/lower identities，以及创建前 shared control/object baseline digests。新对象只进入 private upper；broker `seal-private-closure` 经 Task 7 pinned fsck 后把必要 closure 作为 readonly stream 交给 source builder写入 broker quarantine，绝不回写 shared ODB或 durable store。producer 退出后 broker 生成 `BrokerLifecycleProofV1`，绑定 subject、全部 staged artifact digests、checkout/gitdir/upper/backlink 已销毁证明与 unchanged shared control/object baselines；只有该 proof 验证成功才原子 no-replace 发布外部 `ticket-23-product-source-v1` handoff。record kind=`product-source`、producerTask=`22`、consumerTasks=`[23,25,27,28]`，严格保留既有七字段record与七个bindings；lifecycle proof作为root broker授权/审计记录留在生命周期journal，不注入record/request。随后发布 full-bytes SHA-256 authenticated immutable export request，orchestrator-only domain 只从该 ref 重建四个 transports。ownership/lease token、paths、store root 与 handles 均不进入 record/request 或跨 harness 传递。

- [ ] **Step 1:** 先写四组 RED。执行域保持 key/agent/proc/FD/ptrace 与不同 UID/GID/namespaces 的既有探针。worktree/lease 生命周期：canonical JSON 必须绑定 provisioner、broker key、MAC token、lease、base SHA-1 OID、detached mode、checkout/linked-gitdir/private-upper、只读 commondir/lower identities、shared config/refs/packed-refs/logs 与 object baselines；并发 base/HEAD 改变、共享 branch/ref/reflog/config 污染、lower 写入、既有 object 覆盖/对象名碰撞、部分 closure promotion、错误/重放 token/lease、任一 path/identity 替换均 fail closed。producer kill matrix在create后、commit后、quarantine后分别注入SIGKILL/crash/PTY断连/heartbeat timeout；producer退出后另在broker destroy前后、baseline check前后、proof签发前后、durable publish前后注入broker crash/restart/fsync失败。所有路径都必须证明producer终止先于destroy/proof；destroy失败、任一shared baseline污染、broker超时/restart未完成proof时必须清理quarantine，且durable CAS/record/pointer/request `artifactWrites=0`。proof 必须以 canonical digest/signature绑定 lifecycle、lease、pidfd/waitid producer-exit evidence、subject与完整 staged digest set，错 proof/token/exit evidence/staged bytes不能发布。Git 写入另逐项复用 Task 7 规格矩阵；交接仍只允许最终已发布的 authenticated `HandoffExportRequestV1` CAS ref。

  编排器必须以 `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/run_handoff_domain.py exec-pty --profile .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-execution-domains.json --role producer --session-label task22-producer` 启动 producer 长生命周期 PTY；broker 在 child exec 前建立 pidfd/control heartbeat lease，producer 无权取消 reaper。下列首段、Steps 2–4 与 Step 5 producer 段写入同一 session；EXIT trap只清理producer scratch，broker必须等pidfd证明producer退出后才destroy/verify、签proof或abort quarantine并release lease；SIGKILL等无trap路径由broker执行相同顺序。`validate-boundary` 只接受上述完整 ownership/lease/detached/private-upper/shared-baseline schema，拒绝未知/缺失/重复字段、非绝对路径、symlink/identity重用或变化、过宽根与不合法 topology；`read-boundary-field` stdout 恰为一个已验证单值，禁止 `eval`：

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
TASK22_OBJECT_CLOSURE="$TASK22_BOUNDARY_PARENT/sealed-object-closure.json"
TASK22_STAGED_MANIFEST="$TASK22_BOUNDARY_PARENT/staged-artifacts.json"
TASK22_TOOLCHAIN_ROOT="$TASK22_BOUNDARY_PARENT/toolchain"
TASK22_GIT_HOME="$TASK22_BOUNDARY_PARENT/git-home"
TASK22_GIT_XDG="$TASK22_BOUNDARY_PARENT/git-xdg"
TASK22_GIT_HOOKS="$TASK22_BOUNDARY_PARENT/empty-hooks"
TASK22_GIT_CONFIG_VIEW="$TASK22_BOUNDARY_PARENT/git-config-view"
TASK22_GIT_ATTRIBUTE_VIEW="$TASK22_BOUNDARY_PARENT/git-attribute-view"
TASK22_GIT_RESULT="$TASK22_BOUNDARY_PARENT/git-write-result.json"
TASK22_GIT_BIN=""
TASK22_GIT_DIGEST=""
cleanup_task22_producer() {
  local parent_ok=0
  if backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py cleanup-owned-tree --root "$TASK22_BOUNDARY_PARENT" --expected-mode 0700; then
    test ! -e "$TASK22_BOUNDARY_PARENT" && parent_ok=1
  fi
  test "$parent_ok" -eq 1
}
trap 'cleanup_task22_producer' EXIT
mkdir -m 0700 "$TASK22_TOOLCHAIN_ROOT" "$TASK22_GIT_HOME" "$TASK22_GIT_XDG" "$TASK22_GIT_HOOKS" "$TASK22_GIT_CONFIG_VIEW" "$TASK22_GIT_ATTRIBUTE_VIEW"
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
"$TRUSTED_WORKTREE_PROVISIONER" create-detached-linked-worktree \
  --project-id ai-video-reverse-engineer --output-json "$TASK22_BOUNDARY_JSON" \
  --broker-rpc-fd "$TRUSTED_WORKTREE_PROVISIONER_RPC_FD" \
  --expected-provisioner-digest "$TRUSTED_WORKTREE_PROVISIONER_DIGEST" \
  --expected-broker-key-id "$TRUSTED_WORKTREE_PROVISIONER_KEY_ID" \
  --ownership-schema trusted-linked-worktree-ownership/v1 --exclusive-checkout --exclusive-linked-gitdir \
  --head-mode detached --shared-commondir-policy readonly-measure-control \
  --shared-object-policy readonly-lower-private-upper --register-lifecycle-lease \
  --git-bin "$TASK22_GIT_BIN" --git-digest "$TASK22_GIT_DIGEST" \
  --git-home "$TASK22_GIT_HOME" --git-xdg "$TASK22_GIT_XDG" --hooks-root "$TASK22_GIT_HOOKS"
"$TRUSTED_WORKTREE_PROVISIONER" verify-capability \
  --boundary-json "$TASK22_BOUNDARY_JSON" --broker-rpc-fd "$TRUSTED_WORKTREE_PROVISIONER_RPC_FD" \
  --expected-project-id ai-video-reverse-engineer \
  --expected-provisioner-digest "$TRUSTED_WORKTREE_PROVISIONER_DIGEST" \
  --expected-broker-key-id "$TRUSTED_WORKTREE_PROVISIONER_KEY_ID" \
  --require-exclusive-checkout --require-exclusive-linked-gitdir --require-private-object-upper \
  --require-detached-head --require-readonly-shared-lower --require-active-lease
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py validate-boundary \
  --boundary-json "$TASK22_BOUNDARY_JSON" --expected-schema trusted-registered-repository-boundary/v1 \
  --expected-ownership-schema trusted-linked-worktree-ownership/v1 \
  --require-owner-current --expected-mode 0700 --require-git-pointer-regular --require-clean
TASK22_PRODUCT_ROOT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py read-boundary-field --boundary-json "$TASK22_BOUNDARY_JSON" --field checkoutRoot)"
TASK22_GITDIR_ROOT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py read-boundary-field --boundary-json "$TASK22_BOUNDARY_JSON" --field gitdirRoot)"
TASK22_COMMONDIR_ROOT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py read-boundary-field --boundary-json "$TASK22_BOUNDARY_JSON" --field commondirRoot)"
TASK22_OBJECT_UPPER_ROOT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py read-boundary-field --boundary-json "$TASK22_BOUNDARY_JSON" --field privateObjectUpperRoot)"
TASK22_BASE_COMMIT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py read-boundary-field --boundary-json "$TASK22_BOUNDARY_JSON" --field baseCommit.hex)"
printf '%s\n' "$TASK22_BASE_COMMIT" | grep -Eq '^[0-9a-f]{40}$'
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py verify-boundary-identities \
  --boundary-json "$TASK22_BOUNDARY_JSON" --checkout-root "$TASK22_PRODUCT_ROOT" \
  --gitdir-root "$TASK22_GITDIR_ROOT" --commondir-root "$TASK22_COMMONDIR_ROOT" \
  --private-object-upper-root "$TASK22_OBJECT_UPPER_ROOT" --require-detached-head "$TASK22_BASE_COMMIT"
```

之后阅读该产品根内的实际源码/测试，只补缺失结构 tests 与 wheel-only lock。
- [ ] **Step 2:** 写两个专用结构测试；以测试文件缺失为 RED 证据，不在依赖物化前运行产品测试或使用宿主临时安装。
- [ ] **Step 3:** 在 `(cd "$TASK22_PRODUCT_ROOT" && ...)` 中逐项静态核对 requirements lock 仅含 exact pin+SHA-256 wheel、package-lock v3 与两个专用测试名；不把静态核对冒充行为 GREEN。
- [ ] **Step 4:** 在同一 producer PTY 中仅通过 `TrustedGitExecutorV1` snapshot capability 形成 detached 产品 commit。executor 验证 capability base OID 等于 `expected_old_head`，持有 config/attributes/worktree snapshot handles，把对象只写 private upper；原 base/HEAD、shared refs/reflogs/control/lower 或任一 snapshot source 在前后变化均不返回结果。精确执行：

```bash
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_git.py commit-detached \
  --repository-root "$TASK22_PRODUCT_ROOT" --boundary-json "$TASK22_BOUNDARY_JSON" \
  --git-bin "$TASK22_GIT_BIN" --git-digest "$TASK22_GIT_DIGEST" \
  --home-root "$TASK22_GIT_HOME" --xdg-root "$TASK22_GIT_XDG" --hooks-root "$TASK22_GIT_HOOKS" \
  --config-view-root "$TASK22_GIT_CONFIG_VIEW" --attribute-view-root "$TASK22_GIT_ATTRIBUTE_VIEW" \
  --object-upper-root "$TASK22_OBJECT_UPPER_ROOT" --expected-old-head "$TASK22_BASE_COMMIT" --require-detached-head \
  --path backend/requirements.lock --path frontend/src/CreativeCanvasTicket23Acceptance.test.tsx \
  --message "test: prepare ticket 23 trusted replay subject" \
  --author-name "Trusted Evidence Builder" --author-email trusted-evidence@invalid --author-date 2000-01-01T00:00:00Z \
  --committer-name "Trusted Evidence Builder" --committer-email trusted-evidence@invalid --committer-date 2000-01-01T00:00:00Z \
  --expected-object-format sha1 --no-gpg-sign --no-verify --require-clean-after --result "$TASK22_GIT_RESULT"
TASK22_PRODUCT_COMMIT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_git.py read-result --result "$TASK22_GIT_RESULT" --field commit.hex)"
printf '%s\n' "$TASK22_PRODUCT_COMMIT" | grep -Eq '^[0-9a-f]{40}$'
"$TRUSTED_WORKTREE_PROVISIONER" seal-private-closure \
  --boundary-json "$TASK22_BOUNDARY_JSON" --broker-rpc-fd "$TRUSTED_WORKTREE_PROVISIONER_RPC_FD" \
  --expected-provisioner-digest "$TRUSTED_WORKTREE_PROVISIONER_DIGEST" \
  --expected-broker-key-id "$TRUSTED_WORKTREE_PROVISIONER_KEY_ID" \
  --expected-base "$TASK22_BASE_COMMIT" --expected-result "$TASK22_PRODUCT_COMMIT" \
  --git-bin "$TASK22_GIT_BIN" --git-digest "$TASK22_GIT_DIGEST" --output-json "$TASK22_OBJECT_CLOSURE"
```

实际行为 RED/GREEN 固定由 Task 23 在产品专属依赖物化后执行；SHA-256 object format、`extensions.objectFormat` 或任何非 40-hex 在 raw traversal 前拒绝。
- [ ] **Step 5:** producer-only harness 不得写 `$TRUSTED_HANDOFF_STORE_ROOT`。`build --stage-only` 在 owning handles 生命期内完成 raw inspect、canonical build 与 raw proof，把 canonical blobs/record/pointer/request candidate 送入 root broker 位于 durable store 外、consumer 不可见的 quarantine，并输出只含 canonical digest set 的 `staged-artifacts.json`；producer 不执行 durable publish/transport、不读取 signing key、不 fetch consumer seal。producer清理自身scratch并退出后，broker才执行destroy→verify→baseline check→`BrokerLifecycleProofV1`：

```bash
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_product_source.py build \
  --boundary-json "$TASK22_BOUNDARY_JSON" --checkout-root "$TASK22_PRODUCT_ROOT" \
  --gitdir-root "$TASK22_GITDIR_ROOT" --commondir-root "$TASK22_COMMONDIR_ROOT" \
  --sealed-object-closure "$TASK22_OBJECT_CLOSURE" \
  --broker-rpc-fd "$TRUSTED_WORKTREE_PROVISIONER_RPC_FD" --require-readonly-closure-stream \
  --expected-base-commit "$TASK22_BASE_COMMIT" \
  --subject-commit "$TASK22_PRODUCT_COMMIT" --stage-only --quarantine-owner broker \
  --staged-manifest-output "$TASK22_STAGED_MANIFEST" \
  --handoff-id ticket-23-product-source-v1 --producer-task 22 \
  --consumer-task 23 --consumer-task 25 --consumer-task 27 --consumer-task 28 --offline
cleanup_task22_producer
test ! -e "$TASK22_BOUNDARY_PARENT"
trap - EXIT
exit
```

producer shell 完全退出后，root broker以pidfd/waitid确认exit，才从fsynced lease journal重开并销毁checkout/linked-gitdir/private-upper/backlink、验证shared control/object baselines、核对quarantine staged digests、签发proof并release lease；producer无destroy/verify/proof/release RPC。controller随后只按supervisor session identity向root broker取proof，不能接收producer path、manifest path或自报digest。先调用`trusted_worktree_provisioner.py resolve-lifecycle-proof`，参数固定为broker RPC FD、producer session ID、project=`ai-video-reverse-engineer`、task=`22`及`--require-producer-exited --require-destroyed --require-baselines-unchanged --require-quarantine-complete`，输出必须是唯一`sha256:<64hex>` proof ref。再调用`publish-quarantine`，只传该proof ref、broker RPC FD、store root、handoff ID、kind、producer task、四个consumer及`--atomic-no-replace --fsync-parents --field exportRequestRef`。broker必须验证`BrokerLifecycleProofV1`签名、lease/lifecycle/session、destroyed resources、shared baselines与quarantine实际完整bytes，再以temp+fsync+no-replace顺序原样安装CAS blobs、既有七字段record、pointer，最后以export request作为activation point并返回唯一`sha256:<64hex>` ref；proof只保留在root-owned生命周期journal中。proof缺失/超时、destroy/baseline失败或staged digest不符发生在首个durable write前，broker删除quarantine并证明durable store相对操作前`artifactWrites=0`；proof通过后的安装冲突允许留下byte-identical不可达CAS blob，但pointer/request不得可见，retry只接受精确bytes并继续，否则fail closed。

只有上述 publish 成功后，编排器才用全新的 `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/run_handoff_domain.py exec-pty --profile .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-execution-domains.json --role orchestrator --session-label task22-orchestrator --key-slot handoff-transport-v1` 启动 orchestrator-only PTY。root-owned broker 从外部 secret slot no-follow 打开 key，仅把只读 FD 注入 orchestrator；key path/value 不进入 controller environment/argv。该域只获得受控 source/target store roots、由固定 resolver 注入且匹配 stable request pointer 的 `$TRUSTED_PRODUCT_EXPORT_REQUEST_REF`、各 consumer destination/nonce/sequence/expiry 与 signing-key FD，不获得 producer checkout/boundary/临时路径。它自行创建 0700 stage parent/四个空子根并安装独立 trap；`verify-role` 证明新 UID/GID/namespaces/FD 后，`verify-export-request` 重算 request/record/pointer/CAS与四 consumer，下面四行才执行。每行参数独立完整，不能跨行复用 manifest、seal、nonce 或 stage：

```bash
set -euo pipefail
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/run_handoff_domain.py verify-role \
  --profile .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-execution-domains.json \
  --expected-role orchestrator --require-distinct-peer-credentials --require-isolated-user-mount-pid \
  --require-proc-hidepid --require-signing-key-fd "$TRUSTED_HANDOFF_ORCHESTRATOR_SIGNING_KEY_FD" --require-ptrace-denied
test "$TRUSTED_HANDOFF_ORCHESTRATOR_SIGNING_KEY_FD" -ge 3
test -z "${TRUSTED_WORKTREE_PROVISIONER:-}${TRUSTED_WORKTREE_PROVISIONER_DIGEST:-}${TRUSTED_WORKTREE_PROVISIONER_RPC_FD:-}${TRUSTED_WORKTREE_PROVISIONER_KEY_ID:-}${TASK22_BOUNDARY_PARENT:-}${TASK22_BOUNDARY_JSON:-}${TASK22_OBJECT_CLOSURE:-}${TASK22_PRODUCT_ROOT:-}${TASK22_GITDIR_ROOT:-}${TASK22_COMMONDIR_ROOT:-}${TASK22_OBJECT_UPPER_ROOT:-}${TASK22_BASE_COMMIT:-}"
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

worktree provisioner 不得假设 checkout、linked gitdir、private upper 或 shared roots 位于 `TASK22_BOUNDARY_PARENT` 内；MAC capability 绑定 project/executor/profile、lifecycle/lease、base OID、detached mode、全部 roots/identities、shared control/object baselines。producer 看见的 shared commondir/object lower 是只读 bind view，Git 的 index/HEAD/reflog/objects 只写专属 linked gitdir/private upper；不推进共享 branch/ref/reflog，也不把新对象回写 shared ODB。`seal-private-closure` 对 lower+upper 做 pinned strict fsck 与 collision检查，只把 subject 必需 closure 通过 broker readonly stream导入 store 外 quarantine；碰撞/部分导入不生成 staged manifest。producer无destroy/verify/proof/release RPC；broker必须先由pidfd/waitid确认producer已经退出，才从root-owned lease journal重开全部身份，`destroy-linked-worktree`只unlink本次backlink并删除checkout/linked gitdir/upper，`verify-destroyed`证明三者和backlink为ENOENT且shared config/refs/packed-refs/logs/objects baselines逐项不变，然后签发绑定staged digests的proof并`release-lease`。proof时间序列严格为stage→producer exit→destroy/baseline proof→durable publish。正常退出、SIGKILL/crash/PTY断连/heartbeat超时均由broker reaper执行，restart从fsynced journal恢复。没有有效proof时quarantine必须回收且durable store保持逐字节不变；相同proof+bytes的publish可幂等，proof或目标碰撞则fail closed。错误token/lease、identity/baseline/reaper proof任一失败均不能发布artifact或清理他人lifecycle。该capability只服务Task22临时生命周期，与进程内`RegisteredRepositoryBoundaryV1`、source digest和跨Task handoff分离。

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
- [ ] **Step 3:** stdin 传 payload 给登记 `ssh-keygen -Y verify`；生产 CLI 永不接收私钥或 agent socket。`collect-e4` 复用 Task 16 canonical result writer并支持同一`--candidate-output-root --defer-repository-writes`合同：Task28模式只向外部task-local staging写canonical candidate bytes/ref，普通模式才按既有CAS流程；stdout 无额外文本。`audit_e4_secrets.py` 对 regular files no-follow 扫描上述完整私钥结构和值/digest，解析 production JSON key tree，并校验 `trust/` 公共 policy/signer 文件恰为 0644、任何私钥形态文件一律拒绝；显式 allowlist 只排除测试 fixture bytes 与 `redaction.py` 的 lexer grammar 源码，production CLI/config/trust/evidence/audit 永不豁免。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py .scratch/unified-remix-canvas-generation/acceptance/test_audit_e4_secrets.py`，再运行 `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/audit_e4_secrets.py --root .scratch/unified-remix-canvas-generation/acceptance --production-json .scratch/unified-remix-canvas-generation/acceptance/trusted-registry.json --allow-test .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py --allow-test .scratch/unified-remix-canvas-generation/acceptance/test_audit_e4_secrets.py --allow-grammar .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/redaction.py`；预期 GREEN、无结构化私钥/禁止值/禁止 JSON 字段/权限错误。关键词 grep 不作为验收；Task 26 在 reviewer policy 创建后必须再次扫描该 JSON。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trusted_evidence/{models.py,registry.py,verifiers/e4_ssh.py} .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py .scratch/unified-remix-canvas-generation/acceptance/tools/audit_e4_secrets.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py .scratch/unified-remix-canvas-generation/acceptance/test_audit_e4_secrets.py && git commit -m "feat: verify signed E4 attestations"`

### Task 25: 固化真实 Toonflow E1 与产品 E1 映射

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trust/git/toonflow-d86cce6b689916ab73f75bc4c1f42a2a7424516c.bundle`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trust/product-sources/sha256/<aa>/<digest>`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/trust/e1-ticket23-sources.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/build_ticket23_e1_sources.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_e1_sources.py`
- Ephemeral only: 0700 `TASK25_CACHE_PARENT/{handoff-cache,toolchain,git-home,git-xdg,empty-hooks,git-config-view,git-attribute-view}` 及 finalized marker；EXIT trap 清理，不跟踪、不跨 Task 复用。

**Interfaces:**
- Consumes: Tasks 7/10–11/18 的 `TrustedGitExecutorV1` 与 pinned Git、从 `$TRUSTED_HANDOFF_STORE_ROOT` authenticated fetch 的 `ticket-23-product-source-v1`（producer=22、consumer=25）中 product container/build-record/raw-inspection-proof/boundary/source digests；Toonflow fixed commit/MIT license。
- Produces: `build_ticket23_e1_sources.py verify-product/import-sources/parse-import-result`、两个 E1 refs、Toonflow/license 与产品实现/测试/lock blob mappings；`parse-import-result` strict parse 完整 stdin 后以 `--field toonflowSourceRef|productSourceRef` 只输出一个已验证 ref。

- [ ] **Step 1:** 写 RED：保留 Toonflow commit/objects/MIT、product consumer-25 transport、七 bindings 与 typed SHA-1 subject 全部检查；另外断言 bundle verify/cat-file 与产品 index-pack/fsck/cat-file 只经同一 Task 18 `TrustedGitExecutionViewV1`，host/path Git spawn=0。bundle/container逐项复用Task7完整Git安全矩阵，尤其`info/attributes`存在即拒绝、`.gitattributes`的`filter/working-tree-encoding/ident`、replace-object、autocrlf、prompt、optional-lock、discovery与pre/post identity/bytes替换；所有负例均零product-source/E1可见写入。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_e1_sources.py`；预期真实 inputs RED。
- [ ] **Step 3:** Toonflow 的 `bundle_verify/cat_file` 与产品的 `index_pack_strict/fsck_strict/cat_file` 全部由 Task 7 `TrustedGitExecutionViewV1` 方法执行；builder 不含 subprocess Git fallback。`verify-cache` 在 fetch 前验根是当次新建、0700、空、no-symlink 且只有唯一打开 identity；`import/finalize` 逐入口拒绝 symlink、非 regular file、hard-linked regular file、额外 entry 或 root identity 变化；trap 无论成败都删除整个 task-local parent：

```bash
set -euo pipefail
TASK25_CACHE_PARENT="$(mktemp -d)"
chmod 0700 "$TASK25_CACHE_PARENT"
TASK25_HANDOFF_CACHE="$TASK25_CACHE_PARENT/handoff-cache"
TASK25_TOOLCHAIN_ROOT="$TASK25_CACHE_PARENT/toolchain"
TASK25_GIT_HOME="$TASK25_CACHE_PARENT/git-home"
TASK25_GIT_XDG="$TASK25_CACHE_PARENT/git-xdg"
TASK25_GIT_HOOKS="$TASK25_CACHE_PARENT/empty-hooks"
TASK25_GIT_CONFIG_VIEW="$TASK25_CACHE_PARENT/git-config-view"
TASK25_GIT_ATTRIBUTE_VIEW="$TASK25_CACHE_PARENT/git-attribute-view"
mkdir -m 0700 "$TASK25_HANDOFF_CACHE" "$TASK25_TOOLCHAIN_ROOT" "$TASK25_GIT_HOME" "$TASK25_GIT_XDG" "$TASK25_GIT_HOOKS" "$TASK25_GIT_CONFIG_VIEW" "$TASK25_GIT_ATTRIBUTE_VIEW"
chmod 0555 "$TASK25_GIT_HOOKS"
cleanup_task25() {
  backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py cleanup-owned-tree --root "$TASK25_CACHE_PARENT" --expected-mode 0700
}
trap 'cleanup_task25' EXIT
TASK25_TOOLCHAIN_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py read-artifact-ref --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --content-store-root .scratch/unified-remix-canvas-generation/acceptance/trust/toolchains)"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize --artifact-ref "$TASK25_TOOLCHAIN_REF" --output-root "$TASK25_TOOLCHAIN_ROOT" --offline
TASK25_GIT_BIN="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize-entrypoint --materialized-root "$TASK25_TOOLCHAIN_ROOT" --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --name git --field path)"
TASK25_GIT_DIGEST="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize-entrypoint --materialized-root "$TASK25_TOOLCHAIN_ROOT" --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --name git --field digest)"
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
  --git-bin "$TASK25_GIT_BIN" --git-digest "$TASK25_GIT_DIGEST" --git-home "$TASK25_GIT_HOME" --git-xdg "$TASK25_GIT_XDG" \
  --hooks-root "$TASK25_GIT_HOOKS" --config-view-root "$TASK25_GIT_CONFIG_VIEW" --attribute-view-root "$TASK25_GIT_ATTRIBUTE_VIEW" \
  --handoff-cache-root "$TASK25_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 \
  --transport-id ticket-23-product-source-v1-c25 --consumer-task 25 --expected-destination-executor-id "$TRUSTED_PRODUCT_C25_DESTINATION_EXECUTOR_ID" --expected-commit "$TASK25_PRODUCT_COMMIT" \
  --container-ref "$TASK25_CONTAINER_REF" --build-record-ref "$TASK25_BUILD_RECORD_REF" --inspection-proof-ref "$TASK25_PROOF_REF" \
  --boundary-digest "$TASK25_BOUNDARY_DIGEST" --tracked-checkout-digest "$TASK25_TRACKED_DIGEST" \
  --control-metadata-digest "$TASK25_CONTROL_DIGEST" --source-digest "$TASK25_SOURCE_DIGEST" \
  --verified-source-output "$TASK25_VERIFIED_SOURCE"
TASK25_IMPORT_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_ticket23_e1_sources.py import-sources \
  --verified-product-source "$TASK25_VERIFIED_SOURCE" \
  --git-bin "$TASK25_GIT_BIN" --git-digest "$TASK25_GIT_DIGEST" --git-home "$TASK25_GIT_HOME" --git-xdg "$TASK25_GIT_XDG" \
  --hooks-root "$TASK25_GIT_HOOKS" --config-view-root "$TASK25_GIT_CONFIG_VIEW" --attribute-view-root "$TASK25_GIT_ATTRIBUTE_VIEW" \
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
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/task_completion_record.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_reviewer_policy.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_task26_completion_record.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_task26_completion_trusted_git.py`
- Generated external CAS record: `$TRUSTED_HANDOFF_STORE_ROOT/sha256/<aa>/<remaining-62-hex>` (`Task26CompletionRecordV1`; not tracked)
- Generated external stable no-replace pointer: `$TRUSTED_HANDOFF_STORE_ROOT/completion-records/ticket-23-task26.json` (not tracked)
- Ephemeral only: root-broker 0700 `TASK26_BROKER_ROOT/{toolchain,git-home,git-xdg,empty-hooks,git-config-view,git-attribute-view}` 与同进程 repository/executable/config/attribute/worktree owning handles；签发成功或失败均由broker安全删除（not tracked）

**Interfaces:**
- Consumes: Tasks 7/14/18/24–25、Task18 toolchain build record/artifact ref/platform identity、`TrustedGitExecutionViewV1`，真实reviewer公钥/principal/fingerprint，以及完成本Task commit后由独立reviewer生成、内容寻址且broker可验证的review evidence；私钥不入仓库。broker只从固定profile读取Task18 refs/digests与repository root identity，不接受调用方Git binary/digest、OID/ref/blob或config参数。
- Produces: allowed-signers/reviewer policy digest，scope仅ticket-23/v1、C01–C03、八项criteria；独立审查PASS后另由root broker生成持久`Task26CompletionRecordV1`。broker先从Task18 build record/ref离线物化并重算Git2.53.0 executable/digest与platform，创建只读空HOME/XDG/hooks/config/attribute snapshot，在单一broker进程内持有`TrustedGitExecutionViewV1`；reviewed commit、review base、target HEAD ref及plan/spec blob只允许通过该view的`resolve_ref/resolve_commit_parent/cat_file/compare_blob_bytes`恢复和交叉验证。payload绑定reviewed commit、review base commit、`expected_old_head == reviewed_commit`、target head ref、PASS verdict/review evidence ref、该commit中的plan/spec blob refs、producer/executor/profile identity与pinned Git view/toolchain identity；envelope对canonical payload digest做broker signature，stable pointer no-replace。Task27只消费该record的broker-verified capability FD。

- [ ] **Step 1:** 写 scope/criteria/expiry/fingerprint/duplicate/revocation/缺 E4 必阻塞 RED tests；另写completion record RED：未知/缺字段、review verdict非PASS、review evidence签名或CAS错误、reviewed commit/base/head-ref错配、expected_old_head不等于reviewed commit、plan/spec blob不匹配、producer/toolchain/Git view identity错、payload digest/signature错、stable pointer替换/并发冲突全部拒绝。正例从固定review evidence经pinned view恢复typed commit/base/ref与plan/spec blobs；调用方提供环境变量、当前HEAD/branch、直接OID/ref/blob、Git binary/digest或普通reviewed repository capability必须schema拒绝。`test_task26_completion_trusted_git.py`逐项复用Global Constraints完整矩阵：host/system/global/local/worktree config、全部`GIT_CONFIG_*`/object/SSH/askpass注入、replace/grafts/shallow/promisor/alternates、`info/attributes`、层级`.gitattributes` filter/working-tree-encoding/ident、hook/signing/prompt/optional-lock/discovery/autocrlf/network/helper、repository/config/attribute/executable pre/post snapshot swap与host Git fallback；任一负例必须recordWrites=0且恶意canary spawn=0。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_reviewer_policy.py .scratch/unified-remix-canvas-generation/acceptance/test_task26_completion_record.py .scratch/unified-remix-canvas-generation/acceptance/test_task26_completion_trusted_git.py`；预期真实policy、completion broker与Task18-backed publisher缺失而RED。
- [ ] **Step 3:** 运行 `awk '{for (i=1;i<=NF;i++) if ($i ~ /^ssh-/) {print $i, $(i+1); exit}}' .scratch/unified-remix-canvas-generation/acceptance/trust/e4-allowed-signers | ssh-keygen -l -E sha256 -f -`；输出必须等于治理批准 fingerprint。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_reviewer_policy.py .scratch/unified-remix-canvas-generation/acceptance/test_task26_completion_record.py .scratch/unified-remix-canvas-generation/acceptance/test_task26_completion_trusted_git.py .scratch/unified-remix-canvas-generation/acceptance/test_trusted_evidence_e4.py`，再运行 Task 24 secret audit并额外传入`--production-json .scratch/unified-remix-canvas-generation/acceptance/trust/reviewer-policy.json`；预期GREEN，signer/policy为0644且无私钥/production禁止键，全部Git读取只出现Task18 executable digest，record codec拒绝调用方OID/ref/blob/env/Git inputs，签发前post-snapshot recheck覆盖reviewed commit/base/ref、plan/spec blob与view identity。
- [ ] **Step 5:** `git add .scratch/unified-remix-canvas-generation/acceptance/trust/{e4-allowed-signers,reviewer-policy.json} .scratch/unified-remix-canvas-generation/acceptance/tools/task_completion_record.py .scratch/unified-remix-canvas-generation/acceptance/test_{ticket23_reviewer_policy,task26_completion_record,task26_completion_trusted_git}.py && git commit -m "policy: trust ticket 23 acceptance reviewer"`。随后必须由fresh independent reviewer审查该commit；只有PASS evidence已写入review CAS时，controller才调用root broker：`backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/task_completion_record.py publish --broker-rpc-fd "$TRUSTED_COMPLETION_BROKER_RPC_FD" --review-evidence-ref "$TRUSTED_TASK26_REVIEW_EVIDENCE_REF" --expected-project-id ai-video-reverse-engineer --expected-task 26 --expected-target-head-ref-from-review --toolchain-build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --toolchain-content-store-root .scratch/unified-remix-canvas-generation/acceptance/trust/toolchains --platform-identity .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/platform-identity.json --trusted-git-executor-id trusted-git-executor/v1 --require-pinned-git-view --plan-path docs/superpowers/plans/2026-10-02-e2-v2-trusted-replay.md --spec-path docs/superpowers/specs/2026-10-02-e2-v2-trusted-replay-design.md --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --stable-pointer completion-records/ticket-23-task26.json --atomic-no-replace --fsync-parents`。这些路径只能与profile固定身份逐字匹配，不能覆盖trust；broker从review evidence取得期望值，但只经Task18-backed view解析target ref、commit parent与commit blobs，签发前再次重验executable/repository/config/attribute/worktree handles和reviewed commit/base/ref/plan/spec。普通repository capability不得提供任何OID/ref/blob事实；review非PASS、pinned view变化或任一错配时recordWrites=0。再以`verify --stable-pointer ... --require-broker-signature --require-review-pass --require-pinned-git-identity --field recordRef`读取唯一`sha256:<64hex>`作为Task26完成证据；此步成功前Task27不得启动。

### Task 27: 先固定 Ticket 23 policy commit

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/profiles/ticket-23.json`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trusted-registry.json`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/trust/revocations.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/publish_ticket23_policy_handoff.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/policy_publish_transaction.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/tools/policy_activation_proof.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile_v2.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_policy_handoff.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_policy_publish_transaction.py`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_policy_activation_proof.py`
- Generated Git ref: `refs/tags/trusted-acceptance/ticket-23-policy-v1`
- Generated external CAS record/blobs: `$TRUSTED_HANDOFF_STORE_ROOT/sha256/<aa>/<remaining-62-hex>` (not tracked)
- Generated external pointer: `$TRUSTED_HANDOFF_STORE_ROOT/pointers/ticket-23-policy-v1.json` (not tracked)
- Generated core export request: `$TRUSTED_HANDOFF_STORE_ROOT/sha256/<aa>/<remaining-62-hex>`（activation-independent；not tracked）
- Generated external activation marker: `$TRUSTED_HANDOFF_STORE_ROOT/activations/ticket-23-policy-v1.json` (`PolicyActivationMarkerV1`; installed last, not tracked)
- Generated root-owned persistent journal: broker journal store 中 `PolicyPublishTransactionJournalV1` append/fsync chain（not tracked，consumer不可直接读取）
- Generated signed activation proof: `$TRUSTED_HANDOFF_STORE_ROOT/sha256/<aa>/<remaining-62-hex>` (`PolicyActivationProofV1`; proof ref/full-bytes digest；not tracked)
- Generated activated policy handoff/export request: `$TRUSTED_HANDOFF_STORE_ROOT/sha256/<aa>/<remaining-62-hex>`（`Ticket23PolicyHandoffV1`与`ActivatedPolicyExportRequestV1`）及`$TRUSTED_HANDOFF_STORE_ROOT/export-requests/ticket-23-policy-v1.json` no-replace pointer（not tracked）
- Generated external per-consumer transports: target stores 中 `transfers/ticket-23-policy-v1/{ticket-23-policy-v1-c28,ticket-23-policy-v1-c29}.json`、对应 `seals/` 文件与 ready markers (not tracked)
- Ephemeral only: policy-producer 0700 `TASK27_TEMP_PARENT/{product-handoff-cache,toolchain,git-home,git-xdg,empty-hooks,git-config-view,git-attribute-view,preflight.json,transaction-result.json}`、broker-owned private object/handoff quarantine，与独立 orchestrator 0700 `TASK27_ORCHESTRATOR_PARENT/{c28,c29}`；两套 credential domains 各自用 `cleanup-owned-tree` 清理，不共享 UID/GID、namespace、FD、变量、临时根或 trap。

**Interfaces:**
- Consumes: Tasks 2/7/14/18–26 的 trusted Git、credential-domain runner/profile、toolchain/platform/supervisor/source/dependency/E1/E4/public-policy digests，以及固定transport+activation registries/allowed-signers组成的`VerifiedSigningDomainsV1`；authenticated fetch product handoff（producer=22、consumer=27），并核对两dependency records绑定同一product source/proof/locks/toolchain/platform及network-proof；Task26 completion stable pointer只能由controller交给root broker验证，producer仅获只读`TRUSTED_TASK26_COMPLETION_FD` capability，不获commit/ref环境变量。activation broker signing key仅从activation-purpose slot进入root broker，transport key仅从transport-purpose slot进入orchestrator；两者都不得进入producer，且不得跨slot复用。
- Produces: `policy_publish_transaction.py preflight/run/read-result`、`policy_activation_proof.py verify`与持久`PolicyPublishTransactionJournalV1`。`preflight`先联合验证两个registry的key ID、raw/resolved public-key集合互斥与purpose/domain，再完整验证completion record、product transport/record/proof/container、dependency/network/toolchain/platform/reviewer policy、profile/registry/revocation与deterministic policy payload，输出闭合canonical input snapshot（每个文件/根identity与full-bytes digest），且不建journal、不写object/ref/handoff。`run`必须no-follow重开该snapshot及其全部输入并逐项重算；只有全部仍与snapshot、completion FD及联合signing-domain digest相同后才创建首条PREPARED journal，否则journalWrites=0。随后才以completion FD中的typed expected old head/ref调用Task7同进程`TrustedGitExecutionViewV1.commit_install_ref`：private quarantine形成commit/core handoff候选，append-only promotion，单个Git ref transaction原子CAS推进指定HEAD ref+fixed ref，之后安装core record/pointer/request；最后先fsync绑定精确marker bytes的ACTIVATE intent，no-replace安装引用该intent digest的`PolicyActivationMarkerV1`，再append/fsync绑定marker full-bytes digest的ACTIVATED entry。root broker从journal读取三份canonical bytes，结合completion/core artifacts/final refs/profile/toolchain/trust root生成`PolicyActivationProofV1`，只用activation-purpose key对activation domain-tag消息签名，以full signed bytes SHA-256发布proof CAS，再发布`Ticket23PolicyHandoffV1`和`ActivatedPolicyExportRequestV1`。`publish_ticket23_policy_handoff.py`不接收product store root、handoff ID、调用方OID/ref或独立publish权限，只为transaction构造deterministic core candidate bytes。orchestrator只用transport-purpose key对不同transport domain-tag消息签`TransportSealV1`。Tasks28/29不读取journal；它们只从transport closure取得proof并联合加载固定trust registries离线验签、重算三entry链与全部refs/bindings。未生成一致proof与activated request的物理object/ref/core CAS不构成可见授权状态。

- [ ] **Step 1:** 写profile/publisher/transaction/activation-proof RED。保留product consumer-27、typed subject、fixed ref、bindings、两policy transports与credential-domain测试；completion record另覆盖缺失/错签名/错review/错plan-spec/错producer、调用方env或OID/ref输入，均在preflight/journal/object/ref/policy write=0时拒绝。preflight必须在任何journal创建前验证全部product与policy输入及deterministic candidate bytes；preflight输出被替换、未知字段、root identity变化或任一输入在preflight/run之间变化时也必须journalWrites=0。signing-domain矩阵逐项覆盖同一公钥不同key ID、相同key ID不同wrapper、同fingerprint不同certificate wrapper、transport key签activation proof、activation key签transport seal、purpose/domain缺失或错误，全部proof/seal/ready/authorizationWrites=0。journal crash matrix覆盖PREPARED、QUARANTINED、OBJECTS_PROMOTED、Git ref transaction中“HEAD update已queue”“fixed ref update已queue”（commit前均零可见ref）、REFS_INSTALLED、record安装后、pointer后、core request后、activation marker前后、ACTIVATED entry后、proof CAS前后、policy handoff envelope前后、activated request前后；每个phase都注入进程kill/broker restart/fsync失败。无journal retry只接受精确Task26初态；ACTIVATED无proof retry只允许从同一terminal bytes确定性生成proof/envelopes，完整终态retry只接受proof与全部bytes精确一致；不一致proof/signature/ref或未知漂移进入FAILED_BLOCKED且activated request不可见。activation proof tests逐项篡改intent/marker/activated canonical bytes或digest、漏entry、previous chain、transaction/state、commit/HEAD/fixed ref、core record/pointer/request、completion/profile/toolchain/pinned Git view/trust root/signer/signature/domain及proof CAS ref；另将c28 proof/manifest/seal用于c29或错误destination，均在transport ready=0时拒绝。Git安全负例逐项复用Task7完整矩阵。成功只走同一Task18 executor view与单个Git ref transaction，禁止宿主Git或分离raw commit/update-ref/verify；失败不承诺零物理写入，但在activated request发布前精确断言`visibleAuthorizationCount=0`、journal/proof恢复无歧义。
- [ ] **Step 2:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_policy_handoff.py .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_policy_publish_transaction.py .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_policy_activation_proof.py`；预期profile/publisher/transaction/proof broker缺失而RED。
- [ ] **Step 3:** 写全部真实 digest，禁止占位/fixture digest；撤销 v1 pointer/artifact，不改 receipt。
- [ ] **Step 4:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile_v2.py .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_policy_handoff.py .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_policy_publish_transaction.py .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_policy_activation_proof.py`，再执行 `backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/publish_ticket23_policy_handoff.py verify-static-input --profile .scratch/unified-remix-canvas-generation/acceptance/profiles/ticket-23.json --field matrixContractDigest --path .scratch/unified-remix-canvas-generation/acceptance/matrix-contract.json`；后者只做bounded file bytes SHA-256与profile fixed digest比较，不调用Git。预期GREEN，并覆盖product c27全消费链、publisher参数禁止表、联合registry全domain-separation负矩阵及零写、全phase crash/retry、精确终态proof幂等重取、离线proof验签与policy c28/c29 transport隔离。
- [ ] **Step 5:** controller以`backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/run_handoff_domain.py exec-pty --profile .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-execution-domains.json --role producer --session-label task27-producer --completion-record-pointer completion-records/ticket-23-task26.json --require-completion-project ai-video-reverse-engineer --require-completion-task 26 --open-policy-journal-channel ticket-23-policy-v1 --transport-registry .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-registry.json --transport-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-allowed-signers --activation-registry .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-registry.json --activation-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-allowed-signers --require-disjoint-signing-domains`请求启动。root broker必须在任何child或授权写前联合解析两个registry，验证key ID/raw/resolved leaf key集合互斥及purpose/domain，再验证stable pointer/CAS、payload digest/signature、PASS evidence、reviewed/expected-old commit、target ref、plan/spec refs与producer identity；只有成功才在`close_range`之后重开只读`TRUSTED_TASK26_COMPLETION_FD`与root-owned`TRUSTED_POLICY_JOURNAL_FD` broker channel，绝不把base/ref展开为环境变量或argv，也不在此时创建persistent operation journal。producer先fetch/finalize product c27并完成全部静态/动态policy preflight；仅preflight重验成功后`run`才通过该channel创建或恢复journal。producer安全清理并完全退出，且broker重验ACTIVATED终态、用activation-purpose/domain签发`PolicyActivationProofV1`并发布activated request后，才启动orchestrator domain：

```bash
set -euo pipefail
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/run_handoff_domain.py verify-role \
  --profile .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-execution-domains.json \
  --expected-role producer --require-distinct-peer-credentials --require-isolated-user-mount-pid \
  --require-proc-hidepid --require-no-key-mount --require-no-agent-socket --require-closed-fds-from 3 --require-ptrace-denied \
  --require-completion-record-fd "$TRUSTED_TASK26_COMPLETION_FD" --require-policy-journal-fd "$TRUSTED_POLICY_JOURNAL_FD"
test "$TRUSTED_TASK26_COMPLETION_FD" -ge 3
test "$TRUSTED_POLICY_JOURNAL_FD" -ge 3
test -z "${TRUSTED_TASK26_BASE_COMMIT_ALGORITHM:-}${TRUSTED_TASK26_BASE_COMMIT:-}${TRUSTED_TASK26_HEAD_REF:-}${TRUSTED_HANDOFF_ORCHESTRATOR_SIGNING_KEY_FD:-}${TRUSTED_WORKTREE_PROVISIONER:-}${TRUSTED_WORKTREE_PROVISIONER_DIGEST:-}${TRUSTED_WORKTREE_PROVISIONER_RPC_FD:-}${TRUSTED_WORKTREE_PROVISIONER_KEY_ID:-}${E4_PRIVATE_KEY_FILE:-}${SSH_AUTH_SOCK:-}${SSH_AGENT_PID:-}${GIT_SSH:-}${GIT_SSH_COMMAND:-}${GIT_CONFIG_COUNT:-}${GIT_CONFIG_PARAMETERS:-}"
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
TASK27_GIT_CONFIG_VIEW="$TASK27_TEMP_PARENT/git-config-view"
TASK27_GIT_ATTRIBUTE_VIEW="$TASK27_TEMP_PARENT/git-attribute-view"
TASK27_PREFLIGHT="$TASK27_TEMP_PARENT/preflight.json"
TASK27_TRANSACTION_RESULT="$TASK27_TEMP_PARENT/transaction-result.json"
mkdir -m 0700 "$TASK27_PRODUCT_CACHE" "$TASK27_TOOLCHAIN_ROOT" "$TASK27_GIT_HOME" "$TASK27_GIT_XDG" "$TASK27_GIT_HOOKS" "$TASK27_GIT_CONFIG_VIEW" "$TASK27_GIT_ATTRIBUTE_VIEW"
chmod 0555 "$TASK27_GIT_HOOKS"
TASK27_TOOLCHAIN_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py read-artifact-ref \
  --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json \
  --content-store-root .scratch/unified-remix-canvas-generation/acceptance/trust/toolchains)"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize \
  --artifact-ref "$TASK27_TOOLCHAIN_REF" --output-root "$TASK27_TOOLCHAIN_ROOT" --offline
TASK27_GIT_BIN="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize-entrypoint --materialized-root "$TASK27_TOOLCHAIN_ROOT" --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --name git --field path)"
TASK27_GIT_DIGEST="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize-entrypoint --materialized-root "$TASK27_TOOLCHAIN_ROOT" --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --name git --field digest)"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py verify-cache --cache-root "$TASK27_PRODUCT_CACHE" --require-empty --expected-mode 0700
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py fetch --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c27 --consumer-task 27 --expected-destination-executor-id "$TRUSTED_PRODUCT_C27_DESTINATION_EXECUTOR_ID" --cache-root "$TASK27_PRODUCT_CACHE"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py import --cache-root "$TASK27_PRODUCT_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c27 --consumer-task 27 --expected-destination-executor-id "$TRUSTED_PRODUCT_C27_DESTINATION_EXECUTOR_ID"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py finalize --cache-root "$TASK27_PRODUCT_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c27 --expected-kind product-source --expected-producer-task 22 --consumer-task 27 --expected-destination-executor-id "$TRUSTED_PRODUCT_C27_DESTINATION_EXECUTOR_ID"
TASK27_PRODUCT_COMMIT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read-product-subject --cache-root "$TASK27_PRODUCT_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c27 --consumer-task 27 --expected-destination-executor-id "$TRUSTED_PRODUCT_C27_DESTINATION_EXECUTOR_ID" --field hex)"
TASK27_PRODUCT_SUBJECT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read-product-subject --cache-root "$TASK27_PRODUCT_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c27 --consumer-task 27 --expected-destination-executor-id "$TRUSTED_PRODUCT_C27_DESTINATION_EXECUTOR_ID" --field canonicalSubject)"
printf '%s\n' "$TASK27_PRODUCT_COMMIT" | grep -Eq '^[0-9a-f]{40}$'
test "$TASK27_PRODUCT_SUBJECT" = "git://product@$TASK27_PRODUCT_COMMIT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/policy_publish_transaction.py preflight \
  --completion-record-fd "$TRUSTED_TASK26_COMPLETION_FD" \
  --product-handoff-cache-root "$TASK27_PRODUCT_CACHE" \
  --profile .scratch/unified-remix-canvas-generation/acceptance/profiles/ticket-23.json \
  --registry .scratch/unified-remix-canvas-generation/acceptance/trusted-registry.json \
  --revocations .scratch/unified-remix-canvas-generation/acceptance/trust/revocations.json \
  --transport-registry .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-registry.json \
  --transport-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-allowed-signers \
  --activation-registry .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-registry.json \
  --activation-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-allowed-signers \
  --require-disjoint-signing-domains \
  --python-build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/python-build-record.json \
  --npm-build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/npm-build-record.json \
  --network-proof .scratch/unified-remix-canvas-generation/acceptance/fixtures/ticket-23/dependencies/out/network-proof.json \
  --toolchain-build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json \
  --platform-identity .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/platform-identity.json \
  --reviewer-policy .scratch/unified-remix-canvas-generation/acceptance/trust/reviewer-policy.json \
  --matrix-contract .scratch/unified-remix-canvas-generation/acceptance/matrix-contract.json \
  --policy-path .scratch/unified-remix-canvas-generation/acceptance/profiles/ticket-23.json \
  --policy-path .scratch/unified-remix-canvas-generation/acceptance/trusted-registry.json \
  --policy-path .scratch/unified-remix-canvas-generation/acceptance/trust/revocations.json \
  --policy-path .scratch/unified-remix-canvas-generation/acceptance/tools/publish_ticket23_policy_handoff.py \
  --policy-path .scratch/unified-remix-canvas-generation/acceptance/tools/policy_publish_transaction.py \
  --policy-path .scratch/unified-remix-canvas-generation/acceptance/tools/policy_activation_proof.py \
  --policy-path .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_trusted_profile_v2.py \
  --policy-path .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_policy_handoff.py \
  --policy-path .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_policy_publish_transaction.py \
  --policy-path .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_policy_activation_proof.py \
  --producer-task 27 --consumer-task 28 --consumer-task 29 --output "$TASK27_PREFLIGHT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/policy_publish_transaction.py run \
  --preflight "$TASK27_PREFLIGHT" --completion-record-fd "$TRUSTED_TASK26_COMPLETION_FD" \
  --require-preflight-input-revalidation --journal-create-after-preflight \
  --journal-fd "$TRUSTED_POLICY_JOURNAL_FD" --repository-root "$PWD" \
  --git-bin "$TASK27_GIT_BIN" --git-digest "$TASK27_GIT_DIGEST" \
  --home-root "$TASK27_GIT_HOME" --xdg-root "$TASK27_GIT_XDG" --hooks-root "$TASK27_GIT_HOOKS" \
  --config-view-root "$TASK27_GIT_CONFIG_VIEW" --attribute-view-root "$TASK27_GIT_ATTRIBUTE_VIEW" \
  --fixed-ref refs/tags/trusted-acceptance/ticket-23-policy-v1 \
  --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --handoff-id ticket-23-policy-v1 \
  --activation-marker activations/ticket-23-policy-v1.json \
  --activation-registry .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-registry.json \
  --activation-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-allowed-signers \
  --transport-registry .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-registry.json \
  --transport-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-allowed-signers \
  --require-disjoint-signing-domains --activation-signing-key-purpose policy-activation \
  --activation-signature-domain ai-video-reverse-engineer/policy-activation/v1 \
  --require-broker-signed-activation-proof --publish-activated-policy-envelope \
  --message "policy: freeze ticket 23 trusted evidence profile" \
  --author-name "Trusted Evidence Builder" --author-email trusted-evidence@invalid --author-date 2000-01-01T00:00:00Z \
  --committer-name "Trusted Evidence Builder" --committer-email trusted-evidence@invalid --committer-date 2000-01-01T00:00:00Z \
  --allow-object-format sha1 --allow-object-format sha256 --no-gpg-sign --no-verify \
  --resume-exact --result "$TASK27_TRANSACTION_RESULT"
TASK27_POLICY_COMMIT_ALGORITHM="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/policy_publish_transaction.py read-result --result "$TASK27_TRANSACTION_RESULT" --require-state ACTIVATED --field commit.algorithm)"
TASK27_POLICY_COMMIT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/policy_publish_transaction.py read-result --result "$TASK27_TRANSACTION_RESULT" --require-state ACTIVATED --field commit.hex)"
TASK27_ACTIVATION_PROOF_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/policy_publish_transaction.py read-result --result "$TASK27_TRANSACTION_RESULT" --require-state ACTIVATED --require-activation-proof-signed --field activationProofRef)"
TASK27_EXPORT_REQUEST_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/policy_publish_transaction.py read-result --result "$TASK27_TRANSACTION_RESULT" --require-state ACTIVATED --require-activation-proof-signed --field activatedExportRequestRef)"
case "$TASK27_POLICY_COMMIT_ALGORITHM" in
  sha1) printf '%s\n' "$TASK27_POLICY_COMMIT" | grep -Eq '^[0-9a-f]{40}$' ;;
  sha256) printf '%s\n' "$TASK27_POLICY_COMMIT" | grep -Eq '^[0-9a-f]{64}$' ;;
  *) exit 1 ;;
esac
printf '%s\n' "$TASK27_ACTIVATION_PROOF_REF" "$TASK27_EXPORT_REQUEST_REF" | grep -Ec '^sha256:[0-9a-f]{64}$' | grep -qx '2'
cleanup_task27_producer
test ! -e "$TASK27_TEMP_PARENT"
trap - EXIT
exit
```

producer shell完全退出后，controller让broker从ACTIVATED journal、marker、signed activation proof、policy handoff envelope与activated export request重新解析唯一proof/request refs；producer变量、result path、completion FD与journal FD均不跨域。只有intent/marker/activated chain、refs、core record/pointer/request、proof/envelope/request全字节一致且proof签名命中固定activation registry时，编排器才以全新的`backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/run_handoff_domain.py exec-pty --profile .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-execution-domains.json --role orchestrator --session-label task27-orchestrator --key-slot handoff-transport-v1`启动不同UID/GID/user+mount+PID namespace的orchestrator domain；broker只注入只读transport signing-key FD、`$TRUSTED_POLICY_ACTIVATION_PROOF_REF`和`$TRUSTED_POLICY_EXPORT_REQUEST_REF`，不传journal/path/value或producer capability。该域再为policy consumers 28/29独立执行全链：

```bash
set -euo pipefail
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/run_handoff_domain.py verify-role \
  --profile .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-execution-domains.json \
  --expected-role orchestrator --require-distinct-peer-credentials --require-isolated-user-mount-pid \
  --require-proc-hidepid --require-signing-key-fd "$TRUSTED_HANDOFF_ORCHESTRATOR_SIGNING_KEY_FD" \
  --require-signing-key-purpose transport-seal --require-signature-domain ai-video-reverse-engineer/transport-seal/v1 --require-ptrace-denied
test "$TRUSTED_HANDOFF_ORCHESTRATOR_SIGNING_KEY_FD" -ge 3
test -z "${TRUSTED_WORKTREE_PROVISIONER:-}${TRUSTED_WORKTREE_PROVISIONER_DIGEST:-}${TRUSTED_WORKTREE_PROVISIONER_RPC_FD:-}${TRUSTED_WORKTREE_PROVISIONER_KEY_ID:-}${TRUSTED_TASK26_COMPLETION_FD:-}${TRUSTED_POLICY_JOURNAL_FD:-}${TASK27_TEMP_PARENT:-}${TASK27_PRODUCT_CACHE:-}${TASK27_POLICY_COMMIT:-}${TASK27_PRODUCT_COMMIT:-}${TASK27_PRODUCT_SUBJECT:-}${TASK27_ACTIVATION_PROOF_REF:-}${TASK27_EXPORT_REQUEST_REF:-}"
printf '%s\n' "$TRUSTED_POLICY_ACTIVATION_PROOF_REF" "$TRUSTED_POLICY_EXPORT_REQUEST_REF" | grep -Ec '^sha256:[0-9a-f]{64}$' | grep -qx '2'
TASK27_ORCHESTRATOR_PARENT="$(mktemp -d)"
chmod 0700 "$TASK27_ORCHESTRATOR_PARENT"
cleanup_task27_orchestrator() {
  backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py cleanup-owned-tree --root "$TASK27_ORCHESTRATOR_PARENT" --expected-mode 0700
}
trap 'cleanup_task27_orchestrator' EXIT
TASK27_POLICY_C28_STAGE_ROOT="$TASK27_ORCHESTRATOR_PARENT/c28"
TASK27_POLICY_C29_STAGE_ROOT="$TASK27_ORCHESTRATOR_PARENT/c29"
mkdir -m 0700 "$TASK27_POLICY_C28_STAGE_ROOT" "$TASK27_POLICY_C29_STAGE_ROOT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_orchestrator.py verify-export-request --source-store-root "$TRUSTED_HANDOFF_STORE_ROOT" --export-request-ref "$TRUSTED_POLICY_EXPORT_REQUEST_REF" --handoff-id ticket-23-policy-v1 --expected-kind policy --expected-producer-task 27 --consumer-task 28 --consumer-task 29 --policy-activation-proof-ref "$TRUSTED_POLICY_ACTIVATION_PROOF_REF" --transport-registry .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-registry.json --transport-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-allowed-signers --activation-registry .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-registry.json --activation-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-allowed-signers --require-disjoint-signing-domains --expected-transport-purpose transport-seal --expected-transport-domain ai-video-reverse-engineer/transport-seal/v1 --expected-activation-purpose policy-activation --expected-activation-domain ai-video-reverse-engineer/policy-activation/v1 --offline
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_orchestrator.py run-transport --source-store-root "$TRUSTED_HANDOFF_STORE_ROOT" --target-store-root "$TRUSTED_POLICY_C28_TARGET_STORE_ROOT" --stage-root "$TASK27_POLICY_C28_STAGE_ROOT" --export-request-ref "$TRUSTED_POLICY_EXPORT_REQUEST_REF" --handoff-id ticket-23-policy-v1 --transport-id ticket-23-policy-v1-c28 --consumer-task 28 --store-namespace ai-video-reverse-engineer/ticket-23 --source-executor-id "$TRUSTED_SOURCE_EXECUTOR_ID" --destination-executor-id "$TRUSTED_POLICY_C28_DESTINATION_EXECUTOR_ID" --nonce "$TRUSTED_POLICY_C28_NONCE" --sequence "$TRUSTED_POLICY_C28_SEQUENCE" --expires-at "$TRUSTED_POLICY_C28_EXPIRES_AT" --signing-key-fd "$TRUSTED_HANDOFF_ORCHESTRATOR_SIGNING_KEY_FD" --key-id "$TRUSTED_HANDOFF_ORCHESTRATOR_KEY_ID" --signing-key-purpose transport-seal --signature-domain ai-video-reverse-engineer/transport-seal/v1 --policy-activation-proof-ref "$TRUSTED_POLICY_ACTIVATION_PROOF_REF" --transport-registry .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-registry.json --transport-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-allowed-signers --activation-registry .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-registry.json --activation-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-allowed-signers --require-disjoint-signing-domains --offline
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_orchestrator.py run-transport --source-store-root "$TRUSTED_HANDOFF_STORE_ROOT" --target-store-root "$TRUSTED_POLICY_C29_TARGET_STORE_ROOT" --stage-root "$TASK27_POLICY_C29_STAGE_ROOT" --export-request-ref "$TRUSTED_POLICY_EXPORT_REQUEST_REF" --handoff-id ticket-23-policy-v1 --transport-id ticket-23-policy-v1-c29 --consumer-task 29 --store-namespace ai-video-reverse-engineer/ticket-23 --source-executor-id "$TRUSTED_SOURCE_EXECUTOR_ID" --destination-executor-id "$TRUSTED_POLICY_C29_DESTINATION_EXECUTOR_ID" --nonce "$TRUSTED_POLICY_C29_NONCE" --sequence "$TRUSTED_POLICY_C29_SEQUENCE" --expires-at "$TRUSTED_POLICY_C29_EXPIRES_AT" --signing-key-fd "$TRUSTED_HANDOFF_ORCHESTRATOR_SIGNING_KEY_FD" --key-id "$TRUSTED_HANDOFF_ORCHESTRATOR_KEY_ID" --signing-key-purpose transport-seal --signature-domain ai-video-reverse-engineer/transport-seal/v1 --policy-activation-proof-ref "$TRUSTED_POLICY_ACTIVATION_PROOF_REF" --transport-registry .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-registry.json --transport-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-allowed-signers --activation-registry .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-registry.json --activation-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-allowed-signers --require-disjoint-signing-domains --offline
cleanup_task27_orchestrator
test ! -e "$TASK27_ORCHESTRATOR_PARENT"
trap - EXIT
exit
```

两个 stage root 均是编排器预建的空、0700、no-symlink 绝对根；任一 transport 失败不影响另一 transport，但对应 consumer 不得启动。

`PolicyPublishTransactionJournalV1`是append-only、digest-chained、每条及父目录fsync的root-owned journal；每个动作前先写`pending_transition` intent并fsync，动作完成后再写清除intent的新state并fsync。各entry显式绑定target HEAD ref/expected old OID、fixed ref必须不存在、policy commit，以及随状态出现的core record ref、pointer digest、core export-request ref和activation-marker digest，不能只用笼统phase名恢复。状态语义固定为：`PREPARED`绑定已完成的全输入preflight与精确初态；`QUARANTINED`绑定private commit/object及deterministic core record/pointer/request候选；`OBJECTS_PROMOTED`只允许不可达object append-only进入共享ODB；`REFS_INSTALLED`表示单个Git ref transaction已同时CAS安装HEAD与fixed ref；`HANDOFF_INSTALLED`表示core record/pointer/request物理bytes已no-replace安装但consumer仍拒绝。ACTIVATE intent先绑定待安装marker的全部规范字段并fsync；marker的`activation_intent_digest`只引用该前驱entry，避免摘要环；marker no-replace安装、fsync并read-back后，最终`ACTIVATED` entry再绑定marker full-bytes digest并fsync。broker只有在重读该终态、refs与core artifacts完全一致后才签`PolicyActivationProofV1`并发布后置envelopes；proof内嵌三份bytes使目标executor无需journal权限即可重演整条链。`FAILED_BLOCKED`保留诊断并永不授权。初次执行只接受精确Task26初态；无proof的ACTIVATED retry只从相同terminal bytes确定性补发相同proof/envelopes，完整终态retry只接受proof与全部bytes精确一致；其余状态必须由同一operation ID、completion record digest与staged payload digest的journal恢复。REFS_INSTALLED前可删除quarantine/未引用temp作补偿；REFS_INSTALLED后不得猜测rollback或覆盖第三方状态，只能在所有既有bytes精确匹配journal时向前恢复，否则进入FAILED_BLOCKED。Tasks28/29、transport resolver与fixed-ref verifier只验证activated request携带的proof ref/full-bytes digest、固定trust root及proof内嵌marker→ACTIVATE intent→ACTIVATED entry完整链，不读取root journal；activated request前即使已有objects、refs、core CAS、marker或journal终态也不构成可见授权状态。每个intent/state边界、Git ref transaction queue HEAD后/queue fixed-ref后/commit后，以及record、pointer、core request、marker、proof、policy envelope、activated request各安装后都必须有kill+restart测试。

### Task 28: 从固定 policy commit collect/register

**Files:**
- Create generated immutable objects: `.scratch/unified-remix-canvas-generation/acceptance/evidence/sha256/<aa>/<digest>`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/evidence-catalog.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/audit/ticket-23-trusted-evidence.json`
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_collect_register.py`
- Ephemeral only: `$TASK28_RUNTIME_ROOT/{candidates,allowed-task-outputs.json}`（七个canonical candidates只写外部staging；Task28独立`AllowedTaskOutputsV1`只作为同进程授权计划的审计副本，不重读、不刷新、不跨Task复用）

**Interfaces:**
- Consumes: 从 `$TRUSTED_HANDOFF_STORE_ROOT` authenticated fetch 的 product/policy handoffs、policy transport closure内的`PolicyActivationProofV1` ref/full-bytes digest、fixed policy ref、两dependency refs/network proof与稳定replay binding；manifest/seal/ready marker必须绑定同一proof，目标executor联合加载固定transport+activation registries/allowed-signers，证明key ID/raw/resolved leaf key集合互斥并按purpose/domain离线验签，再重算intent→marker→activated链，不读取root journal。另消费Task2 `task-output-policy.json`与Task7 pinned view在`register-batch`同一进程一次性生成的Task28 phase-aware `AllowedTaskOutputsV1`：只有测试源码可按初始status/digest保持dirty；catalog必须等于base blob、audit必须不存在、CAS managed root不得有七candidate refs以外新增对象，七项按new/byte-identical-idempotent不交并集处理。plan/spec/profile/trust/registry/revocations/policy代码均受保护。每次collect构造短生命周期snapshot且candidate bytes只落task-local外部staging。E2 replay每leaf新空cache，不读取Task22/23临时根。
- Produces: E1 两个、E2 四个、E4 一个 artifacts/pointers；contract 不变。

- [ ] **Step 1:** 保留 wrong-policy/snapshot-race/missing-E4/source-dependency/handoff/ref tests 与 sequential historical regression；新增 Ticket 23 batch test：一次 typed preflight 必须验证两个 authenticated handoffs、独立 typed fixed policy commit、从 product source 派生的固定 sha1/40 typed subject、container/raw proof、dependency records/network proof、profile/verifier/subject/policy bindings 与七 artifacts 的 canonical bytes/receipt semantics。policy activation proof缺失/错签名/错trust root、三entry任一bytes/digest/ref/chain错、core record/pointer/request或completion/transaction/state/profile/toolchain/pinned Git view/final refs换绑、manifest/seal/ready proof字段不一致、错误consumer/destination或仅部分安装时，都在第一次catalog write前失败；测试明确撤销目标executor的journal访问仍可离线成功。signing domain另覆盖同key不同ID、同ID不同wrapper、同leaf fingerprint不同certificate、cross-purpose签名与缺/错purpose/domain，均catalogWrites=0。合法测试源码dirty正例必须通过；预脏catalog、预建audit、七refs之外额外格式合法CAS、candidate CAS预存不同bytes、new/idempotent集合不闭合、锁前/锁后/写后manifest或源码digest漂移，以及未声明/protected dirty、任意staged/index、任意rename、symlink逃逸均CAS/audit/catalogWrites=0。产品SHA-256、`extensions.objectFormat`、错ref/subject/binding/E4同样失败。policy commit单独覆盖SHA-1/SHA-256。fixed-ref验证逐项复用Task7安全矩阵且catalogWrites=0。成功只允许全七原子出现。另对三根aggregate cleanup注入首/中/尾失败，逐例断言三个helper均调用、成功根ENOENT、总体非零且EXIT trap保留；全成功才解除trap。
  Task28输出策略同样拒绝任何staged/index变更与任何rename，即使源/目标均在allowlist；只有manifest匹配的unstaged声明输出可dirty。
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
TASK28_GIT_CONFIG_VIEW="$TASK28_RUNTIME_ROOT/git-config-view"
TASK28_GIT_ATTRIBUTE_VIEW="$TASK28_RUNTIME_ROOT/git-attribute-view"
TASK28_CANDIDATE_ROOT="$TASK28_RUNTIME_ROOT/candidates"
TASK28_ALLOWED_OUTPUTS="$TASK28_RUNTIME_ROOT/allowed-task-outputs.json"
mkdir -m 0700 "$TASK28_TOOLCHAIN_ROOT" "$TASK28_GIT_HOME" "$TASK28_GIT_XDG" "$TASK28_GIT_HOOKS" "$TASK28_GIT_CONFIG_VIEW" "$TASK28_GIT_ATTRIBUTE_VIEW" "$TASK28_CANDIDATE_ROOT"
chmod 0555 "$TASK28_GIT_HOOKS"
TASK28_TOOLCHAIN_REF="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py read-artifact-ref --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --content-store-root .scratch/unified-remix-canvas-generation/acceptance/trust/toolchains)"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize --artifact-ref "$TASK28_TOOLCHAIN_REF" --output-root "$TASK28_TOOLCHAIN_ROOT" --offline
TASK28_GIT_BIN="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize-entrypoint --materialized-root "$TASK28_TOOLCHAIN_ROOT" --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --name git --field path)"
TASK28_GIT_DIGEST="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/build_e2_toolchain_fixture.py materialize-entrypoint --materialized-root "$TASK28_TOOLCHAIN_ROOT" --build-record .scratch/unified-remix-canvas-generation/acceptance/fixtures/e2-v2/toolchain/out/build-record.json --name git --field digest)"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py verify-cache --cache-root "$TASK28_POLICY_HANDOFF_CACHE" --require-empty --expected-mode 0700
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py verify-cache --cache-root "$TASK28_PRODUCT_HANDOFF_CACHE" --require-empty --expected-mode 0700
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py fetch --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --handoff-id ticket-23-policy-v1 --transport-id ticket-23-policy-v1-c28 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_POLICY_C28_DESTINATION_EXECUTOR_ID" --cache-root "$TASK28_POLICY_HANDOFF_CACHE" --require-policy-activation-proof --transport-registry .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-registry.json --transport-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-allowed-signers --activation-registry .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-registry.json --activation-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-allowed-signers --require-disjoint-signing-domains --offline
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py fetch --store-root "$TRUSTED_HANDOFF_STORE_ROOT" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c28 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_PRODUCT_C28_DESTINATION_EXECUTOR_ID" --cache-root "$TASK28_PRODUCT_HANDOFF_CACHE"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py import --cache-root "$TASK28_POLICY_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 --transport-id ticket-23-policy-v1-c28 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_POLICY_C28_DESTINATION_EXECUTOR_ID"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py import --cache-root "$TASK28_PRODUCT_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c28 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_PRODUCT_C28_DESTINATION_EXECUTOR_ID"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py verify-policy-activation --cache-root "$TASK28_POLICY_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 --transport-id ticket-23-policy-v1-c28 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_POLICY_C28_DESTINATION_EXECUTOR_ID" --transport-registry .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-registry.json --transport-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-allowed-signers --activation-registry .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-registry.json --activation-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-allowed-signers --require-disjoint-signing-domains --expected-transport-purpose transport-seal --expected-transport-domain ai-video-reverse-engineer/transport-seal/v1 --expected-activation-purpose policy-activation --expected-activation-domain ai-video-reverse-engineer/policy-activation/v1 --expected-state ACTIVATED --offline
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py finalize --cache-root "$TASK28_POLICY_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 --transport-id ticket-23-policy-v1-c28 --expected-kind policy --expected-producer-task 27 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_POLICY_C28_DESTINATION_EXECUTOR_ID" --require-policy-activation-proof --expected-activation-state ACTIVATED
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py finalize --cache-root "$TASK28_PRODUCT_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c28 --expected-kind product-source --expected-producer-task 22 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_PRODUCT_C28_DESTINATION_EXECUTOR_ID"
TASK28_POLICY_COMMIT_ALGORITHM="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read --cache-root "$TASK28_POLICY_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 --transport-id ticket-23-policy-v1-c28 --expected-kind policy --expected-producer-task 27 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_POLICY_C28_DESTINATION_EXECUTOR_ID" --field producerCommit.algorithm)"
TASK28_POLICY_COMMIT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read --cache-root "$TASK28_POLICY_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 --transport-id ticket-23-policy-v1-c28 --expected-kind policy --expected-producer-task 27 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_POLICY_C28_DESTINATION_EXECUTOR_ID" --field producerCommit.hex)"
TASK28_PRODUCT_COMMIT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read-product-subject --cache-root "$TASK28_PRODUCT_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c28 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_PRODUCT_C28_DESTINATION_EXECUTOR_ID" --field hex)"
TASK28_PRODUCT_SUBJECT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py read-product-subject --cache-root "$TASK28_PRODUCT_HANDOFF_CACHE" --handoff-id ticket-23-product-source-v1 --transport-id ticket-23-product-source-v1-c28 --consumer-task 28 --expected-destination-executor-id "$TRUSTED_PRODUCT_C28_DESTINATION_EXECUTOR_ID" --field canonicalSubject)"
printf '%s\n' "$TASK28_PRODUCT_COMMIT" | grep -Eq '^[0-9a-f]{40}$'
test "$TASK28_PRODUCT_SUBJECT" = "git://product@$TASK28_PRODUCT_COMMIT"
```

所有 `read/read-product-subject` 都必须重算 pointer、record、所引 artifacts 与 bindings；product commit 只能通过 `ProductGitObjectIdV1` 输出 40hex，policy producer commit 通过独立 `GitObjectIdV1` 输出匹配算法长度的值。再核对 profile 的 subject/source/toolchain/platform/dependency/registry/revocation digests 与两个 handoff records 完全一致。随后执行六次 collect，先保存完整 stdout，再由 capture 工具按 operation/profile/verifier/subject 解析；此时禁止 register：

```bash
E1_TOONFLOW_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e1.ticket23.toonflow-source.v1 --subject "$TASK28_PRODUCT_SUBJECT" --candidate-output-root "$TASK28_CANDIDATE_ROOT" --defer-repository-writes)"
E1_TOONFLOW_ARTIFACT="$(printf '%s' "$E1_TOONFLOW_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect --profile ticket-23/v1 --verifier e1.ticket23.toonflow-source.v1 --subject "$TASK28_PRODUCT_SUBJECT")"
E1_PRODUCT_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e1.ticket23.product-adaptation.v1 --subject "$TASK28_PRODUCT_SUBJECT" --candidate-output-root "$TASK28_CANDIDATE_ROOT" --defer-repository-writes)"
E1_PRODUCT_ARTIFACT="$(printf '%s' "$E1_PRODUCT_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect --profile ticket-23/v1 --verifier e1.ticket23.product-adaptation.v1 --subject "$TASK28_PRODUCT_SUBJECT")"
E2_BACKEND_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.backend-persistence.v2 --subject "$TASK28_PRODUCT_SUBJECT" --candidate-output-root "$TASK28_CANDIDATE_ROOT" --defer-repository-writes)"
E2_BACKEND_ARTIFACT="$(printf '%s' "$E2_BACKEND_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect --profile ticket-23/v1 --verifier e2.ticket23.backend-persistence.v2 --subject "$TASK28_PRODUCT_SUBJECT")"
E2_FRONTEND_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.frontend-persistence.v2 --subject "$TASK28_PRODUCT_SUBJECT" --candidate-output-root "$TASK28_CANDIDATE_ROOT" --defer-repository-writes)"
E2_FRONTEND_ARTIFACT="$(printf '%s' "$E2_FRONTEND_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect --profile ticket-23/v1 --verifier e2.ticket23.frontend-persistence.v2 --subject "$TASK28_PRODUCT_SUBJECT")"
E2_API_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.api.v2 --subject "$TASK28_PRODUCT_SUBJECT" --candidate-output-root "$TASK28_CANDIDATE_ROOT" --defer-repository-writes)"
E2_API_ARTIFACT="$(printf '%s' "$E2_API_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect --profile ticket-23/v1 --verifier e2.ticket23.api.v2 --subject "$TASK28_PRODUCT_SUBJECT")"
E2_STRUCTURE_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect --profile ticket-23/v1 --verifier e2.ticket23.canvas-structure.v2 --subject "$TASK28_PRODUCT_SUBJECT" --candidate-output-root "$TASK28_CANDIDATE_ROOT" --defer-repository-writes)"
E2_STRUCTURE_ARTIFACT="$(printf '%s' "$E2_STRUCTURE_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect --profile ticket-23/v1 --verifier e2.ticket23.canvas-structure.v2 --subject "$TASK28_PRODUCT_SUBJECT")"
```

- [ ] **Step 3:** 审核者只在仓库外使用真实私钥；在同一 fail-closed harness 中精确执行：

```bash
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py prepare-e4 --profile ticket-23/v1 --subject "$TASK28_PRODUCT_SUBJECT" --review-input "$E4_REVIEW_INPUT_FILE" --output "$E4_ATTESTATION_FILE"
ssh-keygen -Y sign -n trusted-acceptance-e4 -f "$E4_PRIVATE_KEY_FILE" "$E4_ATTESTATION_FILE"
E4_REVIEW_RESULT="$(backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py collect-e4 --profile ticket-23/v1 --subject "$TASK28_PRODUCT_SUBJECT" --attestation "$E4_ATTESTATION_FILE" --signature "$E4_ATTESTATION_FILE.sig" --attachment "$E4_OPERATION_RECORD_FILE" --candidate-output-root "$TASK28_CANDIDATE_ROOT" --defer-repository-writes)"
E4_REVIEW_ARTIFACT="$(printf '%s' "$E4_REVIEW_RESULT" | backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py parse --operation collect-e4 --profile ticket-23/v1 --verifier e4.ticket23.canvas-review.v1 --subject "$TASK28_PRODUCT_SUBJECT")"
```

缺真实签名或 parser 失败即停止，禁止测试 key 替代，仍不得 register。
- [ ] **Step 4:** 先运行 `capture_collect_result.py validate-many` 从外部candidate staging重算七个唯一refs，确认repository CAS/catalog/audit尚未被collect写入；随后只调用一次 `register-batch`。该唯一进程重开Step 2已authenticated的两个caches，验证finalized marker/seal/pointer/record/blob、联合signing domains与fixed ref，构造`ValidatedRegistrationBatchV1`，再由Task7 pinned view从base catalog、audit/CAS preconditions、七candidate bytes与固定policy一次性封存Task28 `AllowedTaskOutputsV1`；不从当前catalog/audit/CAS dirty bytes学习expected值，也无刷新入口。进程在每个checkpoint调用同一view的`verify_fixed_ref(ref, expected_commit, output_transition)`与`verify_allowed_task_transition`，该同一对象在首次写前、catalog lock后写前及每次CAS/audit/catalog写后验证；七refs的new/idempotent集合必须为不交并集且managed CAS新增集合不能多出任何对象，任一差异零写或明确失败：

```bash
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/capture_collect_result.py validate-many --candidate-root "$TASK28_CANDIDATE_ROOT" "$E1_TOONFLOW_ARTIFACT" "$E1_PRODUCT_ARTIFACT" "$E2_BACKEND_ARTIFACT" "$E2_FRONTEND_ARTIFACT" "$E2_API_ARTIFACT" "$E2_STRUCTURE_ARTIFACT" "$E4_REVIEW_ARTIFACT"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/evidence_bridge.py register-batch \
  --profile ticket-23/v1 --subject "$TASK28_PRODUCT_SUBJECT" --expected-policy-commit-algorithm "$TASK28_POLICY_COMMIT_ALGORITHM" --expected-policy-commit "$TASK28_POLICY_COMMIT" \
  --repository-root "$PWD" --git-bin "$TASK28_GIT_BIN" --git-digest "$TASK28_GIT_DIGEST" \
  --home-root "$TASK28_GIT_HOME" --xdg-root "$TASK28_GIT_XDG" --hooks-root "$TASK28_GIT_HOOKS" --config-view-root "$TASK28_GIT_CONFIG_VIEW" --attribute-view-root "$TASK28_GIT_ATTRIBUTE_VIEW" \
  --transport-registry .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-registry.json --transport-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-allowed-signers \
  --activation-registry .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-registry.json --activation-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-allowed-signers --require-disjoint-signing-domains \
  --product-handoff-cache-root "$TASK28_PRODUCT_HANDOFF_CACHE" --product-handoff-id ticket-23-product-source-v1 --product-transport-id ticket-23-product-source-v1-c28 --product-consumer-task 28 --product-expected-destination-executor-id "$TRUSTED_PRODUCT_C28_DESTINATION_EXECUTOR_ID" \
  --policy-handoff-cache-root "$TASK28_POLICY_HANDOFF_CACHE" --policy-handoff-id ticket-23-policy-v1 --policy-transport-id ticket-23-policy-v1-c28 --policy-consumer-task 28 --policy-expected-destination-executor-id "$TRUSTED_POLICY_C28_DESTINATION_EXECUTOR_ID" \
  --fixed-ref refs/tags/trusted-acceptance/ticket-23-policy-v1 --require-fixed-ref-equals-policy-commit \
  --task-output-policy .scratch/unified-remix-canvas-generation/acceptance/trust/task-output-policy.json --task 28 --base HEAD \
  --candidate-root "$TASK28_CANDIDATE_ROOT" --output-transition-manifest "$TASK28_ALLOWED_OUTPUTS" \
  --require-index-empty --require-protected-inputs-unchanged --seal-transition-once \
  --revalidate-transition-before-write --revalidate-transition-under-lock --revalidate-transition-after-each-write \
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

预期单次输出“七项已原子登记，不代表通过”；任何preflight、signing domain、protected input、index或transition错误时CAS/audit/catalog均保持原状。audit记录typed batch digest、operation snapshot、同一replay binding、两个handoff record digests、subject/toolchain/platform/dependency/network-proof/source/proof/supervisor/fingerprint、transition phase、七candidate refs、new/idempotent集合及七pointers，但不反向写入外层transition manifest digest，避免expected audit bytes与manifest自引用；最终catalog、audit与CAS逐字等于plan的expected post bytes，contract前后相同。
- [ ] **Step 5:** 运行 `PYTHONPATH=.scratch/unified-remix-canvas-generation/acceptance backend/.venv/bin/python -m pytest -q .scratch/unified-remix-canvas-generation/acceptance/test_ticket23_collect_register.py -k 'batch_preflight or sequential_collect_register or wrong_policy or snapshot_race or source_dependency'`，再运行完整文件与 contract zero-diff；预期任一 batch 错误零 catalog 写、完整 batch final catalog 七项 GREEN。最后执行 `git add .scratch/unified-remix-canvas-generation/acceptance/{evidence,evidence-catalog.json,audit/ticket-23-trusted-evidence.json,test_ticket23_collect_register.py} && git commit -m "evidence: register ticket 23 trusted replay"`。

### Task 29: 晋升 C01–C03 并验证失效重对账

**Files:**
- Create: `.scratch/unified-remix-canvas-generation/acceptance/test_ticket23_promotion.py`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/matrix-contract.json`
- Modify: `.scratch/unified-remix-canvas-generation/issues/23-align-toonflow-canvas-shell-and-persistence.md`
- Modify: `.scratch/unified-remix-canvas-generation/acceptance/README.md`
- Ephemeral only: 0700 `TASK29_CACHE_PARENT/{handoff-cache,promotion/diagnostics,toolchain,git-home,git-xdg,empty-hooks,git-config-view,git-attribute-view,allowed-task-outputs.json}` 与 `promotion/result.json`；Task29独立`AllowedTaskOutputsV1`由promote进程一次性封存，文件仅为审计副本且不被重读/刷新、不跨Task复用，EXIT trap清理且不跟踪。

**Interfaces:**
- Consumes: Task28七个有效pointers、Task17 workflow、Tasks2/7/18 trusted Git，以及从绝对`$TRUSTED_HANDOFF_STORE_ROOT` authenticated fetch/import/verify-policy-activation/finalize/read的policy handoff（producer=27、consumer=29）、transport closure内broker签名`PolicyActivationProofV1`与fixed policy ref；目标executor联合加载固定transport+activation registries/allowed-signers，证明key ID/raw/resolved leaf key集合互斥并按purpose/domain离线验证embedded intent/marker/activated bytes及全部core/ref bindings，不接受root journal访问、相对路径、Task28 cache、shell变量或当前branch推断identity。另消费Task2 `task-output-policy.json`及Task7 pinned view在同一promote进程一次性生成的Task29 phase-aware `AllowedTaskOutputsV1`：测试文件、issue record与README可按初始status/digest保持dirty；matrix contract启动时必须逐字等于base blob，七verified refs确定后先计算完整expected post bytes/digest再允许写。policy/trust/profile/registry/spec/plan及其他index/worktree路径必须保持base bytes。
- Produces: 仅 C01–C03 原子 projection/reconciliation；其他 135 项不变。

- [ ] **Step 1:** 写逐项 requirement、criteria reject/missing、revoked/tampered/unreachable、snapshot-race、deny-list tests；覆盖policy transport必须为`ticket-23-policy-v1-c29`/consumer29/当前destination executor，错transport、篡改cache、七catalog refs错subject/换绑、缺product-source/network-proof/replay-policy binding、非法product subject均在contract write=0时拒绝。activation proof缺失、错broker签名/trust root、intent/marker/activated任一bytes/digest/chain错、proof ref与policy handoff/export request/manifest/seal/ready任一不一致、core refs/final Git refs/completion/transaction/state/profile/toolchain/pinned Git view换绑或Task27未产生完整activated request都必须contractWrites=0；显式在无journal权限目标executor验证成功。signing domain覆盖同key不同ID、同ID不同wrapper、同leaf fingerprint不同certificate、cross-purpose签名与缺/错purpose/domain，均contractWrites=0。合法测试/issue/README源码dirty可通过；预脏matrix contract、expected contract bytes或digest换绑、promote入口/contract lock后/写后plan或source digest漂移，以及未声明/protected dirty、任意staged/index、任意rename、symlink逃逸均contractWrites=0。policy commit独立覆盖SHA-1/SHA-256。fixed-ref验证逐项复用Task7安全矩阵。用phase hook替换diagnostic preflight、cache identity、pointer、manifest、seal、record/CAS/proof/envelope/transition audit copy：diagnostic/audit-copy替换无授权效果，其余全部contractWrites=0。成功路径断言同一进程只封存一次plan、一次原子投影C01–C03、最终contract逐字等于expected bytes、投影后新snapshot立即reconcile。
  Task29输出策略拒绝任何staged/index变更与任何rename；测试/issue/README只允许manifest绑定的unstaged状态保持不变，contract必须从base bytes按唯一transition到预计算post bytes，不能把启动时dirty内容重新快照为合法。
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
TASK29_GIT_CONFIG_VIEW="$TASK29_CACHE_PARENT/git-config-view"
TASK29_GIT_ATTRIBUTE_VIEW="$TASK29_CACHE_PARENT/git-attribute-view"
TASK29_ALLOWED_OUTPUTS="$TASK29_CACHE_PARENT/allowed-task-outputs.json"
mkdir -m 0700 "$TASK29_HANDOFF_CACHE"
mkdir -m 0700 "$TASK29_PROMOTION_ROOT"
mkdir -m 0700 "$TASK29_DIAGNOSTIC_ROOT"
mkdir -m 0700 "$TASK29_TOOLCHAIN_ROOT" "$TASK29_GIT_HOME" "$TASK29_GIT_XDG" "$TASK29_GIT_HOOKS" "$TASK29_GIT_CONFIG_VIEW" "$TASK29_GIT_ATTRIBUTE_VIEW"
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
  --cache-root "$TASK29_HANDOFF_CACHE" --require-policy-activation-proof \
  --transport-registry .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-registry.json \
  --transport-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-allowed-signers \
  --activation-registry .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-registry.json \
  --activation-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-allowed-signers \
  --require-disjoint-signing-domains --offline
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py import \
  --cache-root "$TASK29_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 \
  --transport-id ticket-23-policy-v1-c29 --consumer-task 29 \
  --expected-destination-executor-id "$TRUSTED_POLICY_C29_DESTINATION_EXECUTOR_ID"
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py verify-policy-activation \
  --cache-root "$TASK29_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 \
  --transport-id ticket-23-policy-v1-c29 --consumer-task 29 \
  --expected-destination-executor-id "$TRUSTED_POLICY_C29_DESTINATION_EXECUTOR_ID" \
  --transport-registry .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-registry.json \
  --transport-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-allowed-signers \
  --activation-registry .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-registry.json \
  --activation-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-allowed-signers \
  --require-disjoint-signing-domains \
  --expected-transport-purpose transport-seal --expected-transport-domain ai-video-reverse-engineer/transport-seal/v1 \
  --expected-activation-purpose policy-activation --expected-activation-domain ai-video-reverse-engineer/policy-activation/v1 \
  --expected-state ACTIVATED --offline
backend/.venv/bin/python .scratch/unified-remix-canvas-generation/acceptance/tools/trusted_handoff_store.py finalize \
  --cache-root "$TASK29_HANDOFF_CACHE" --handoff-id ticket-23-policy-v1 \
  --transport-id ticket-23-policy-v1-c29 \
  --expected-kind policy --expected-producer-task 27 --consumer-task 29 \
  --expected-destination-executor-id "$TRUSTED_POLICY_C29_DESTINATION_EXECUTOR_ID" \
  --require-policy-activation-proof --expected-activation-state ACTIVATED
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
  --repository-root "$PWD" --git-bin "$TASK29_GIT_BIN" --git-digest "$TASK29_GIT_DIGEST" \
  --home-root "$TASK29_GIT_HOME" --xdg-root "$TASK29_GIT_XDG" --hooks-root "$TASK29_GIT_HOOKS" --config-view-root "$TASK29_GIT_CONFIG_VIEW" --attribute-view-root "$TASK29_GIT_ATTRIBUTE_VIEW" \
  --transport-registry .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-registry.json --transport-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/handoff-transport-allowed-signers \
  --activation-registry .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-registry.json --activation-allowed-signers .scratch/unified-remix-canvas-generation/acceptance/trust/policy-activation-allowed-signers --require-disjoint-signing-domains \
  --task-output-policy .scratch/unified-remix-canvas-generation/acceptance/trust/task-output-policy.json --task 29 --base HEAD \
  --output-transition-manifest "$TASK29_ALLOWED_OUTPUTS" --seal-transition-once \
  --require-index-empty --require-protected-inputs-unchanged \
  --revalidate-transition-at-entry --revalidate-transition-under-lock --revalidate-transition-after-write \
  --policy-handoff-cache-root "$TASK29_HANDOFF_CACHE" --policy-handoff-id ticket-23-policy-v1 --policy-transport-id ticket-23-policy-v1-c29 --policy-consumer-task 29 --policy-expected-destination-executor-id "$TRUSTED_POLICY_C29_DESTINATION_EXECUTOR_ID" \
  --fixed-ref refs/tags/trusted-acceptance/ticket-23-policy-v1 --require-fixed-ref-equals-policy-commit \
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

`promote-transaction` 必须在唯一进程中重开finalized policy cache、fixed ref、catalog/CAS、profile/registry/revocations、matrix contract、联合signing domains，并将命令行expected values与验证后对象逐项等值比较；首先要求contract逐字等于base blob，仅允许测试/issue/README按初始manifest保持dirty，然后从verified七refs在内存确定完整expected contract bytes/digest并一次性封存Task29 `AllowedTaskOutputsV1`。进程持有cache/repository dirfd与同一plan，并在promote入口、contract lock内写前及写后调用同一view的`verify_fixed_ref(ref, expected_commit, output_transition)`与`verify_allowed_task_transition`，复核全部身份、operation snapshot、protected paths、index、source dirty及precondition；写后要求contract逐字等于plan expected bytes，再构造post-projection snapshot与reconcile。任何预脏contract都不能通过重新snapshot被接受，plan审计副本也不能被重读或刷新。整个流程不要求repo clean，也不接受粗粒度git status结果；未声明输出、protected输入、staged/rename/symlink逃逸或plan换绑均在contract write前失败。`diagnostic-output-root`中任何文件都不被transaction重读，替换它不能影响决定；其他race/failure发生在contract temp创建前。禁止保留独立`preflight-promotion/promote/reconcile`写CLI，也禁止从当前branch、Task28 cache、相对路径或未验证shell值推断identity。
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
| 第十一轮 Critical：config/attributes snapshot 与所有 Git 子命令统一 executor | Global Constraints、7、11、22、25、27–29 |
| 第十一轮 Important 1：detached base OID、expected_old_head 与 shared control baseline | 固定跨任务接口、7、14、22、27 |
| 第十一轮 Important 2：shared ODB readonly lower + private upper/verified closure | 固定跨任务接口、7、13–14、22 |
| 第十一轮 Important 3：broker lease/reaper 覆盖 SIGKILL/crash/断连/超时 | 13–14、22 |
| 第十一轮 Minor：trusted Git 新测试先进入 RED 命令 | 7 |
| 第十二轮 Critical 1：Task 26 completion record 与 broker-verified FD | 固定跨任务接口、13–14、26–27 |
| 第十二轮 Important：Task 27 全输入先预检、初态/终态精确 retry | 固定跨任务接口、26–29 |
| 第十二轮 Critical 2：批准规格 Trusted Git 环境/config/attributes 完整矩阵 | Global Constraints、7、11、25、27–29 |
| 第十二轮 Critical 3：Task 22 quarantine→destroy/baseline proof→durable publish | 固定跨任务接口、10、13–14、22 |
| 第十二轮 Critical 4：Task 27 refs+handoff 可恢复 journal 与 activation marker | 固定跨任务接口、7、13–14、27–29 |
| 第十三轮 Critical 1：Task 26 completion publisher 全链固定 Trusted Git | Global Constraints、7、13–14、18、26 |
| 第十三轮 Critical 2：跨 executor broker 签名 activation proof 与离线验证 | 固定跨任务接口、2、13–14、27–29 |
| 第十四轮 Critical：transport/activation key、purpose 与签名 domain 强制隔离 | Global Constraints、固定跨任务接口、2、14、27–29 |
| 第十四轮 Important：Task 28/29 允许输出 manifest 与受保护输入复验 | Global Constraints、固定跨任务接口、2、7、28–29 |
| 第十五轮 Critical：phase-aware 输出迁移计划拒绝预脏事务输出 | Global Constraints、Review Focus、固定跨任务接口、2、7、16–17、28–29 |

### 自包含接口、依赖拓扑与真实命令

- bootstrap 与主链：1→2→3→4→5→6→7→8→9→10→11→12→13（独立批准门）→14→15→16→17。
- 真实基础验收：17→18→19→20→21；任一未通过，不得写 production profile。
- Ticket 23：21→22 broker lease 创建 detached linked worktree、readonly shared lower+private upper、pinned Git snapshot commit、sealed closure→raw-layout inspect并写store外quarantine→producer退出→destroy/verify/baseline proof→proof-bound durable product publish→不同credential orchestrator transport→23跨executor fetch/sanitized checkout/双空根dependencies+contained zero-network validation→24 E4→25 pinned executor proof/container/bundle E1→26 reviewer policy commit→fresh independent review PASS→broker物化Task18 toolchain并以同进程`TrustedGitExecutionViewV1`恢复/复验全部reviewed Git事实后签名`Task26CompletionRecordV1`→27 broker/profile先证明transport/activation key ID、raw/resolved leaf key与purpose/domain隔离，再由broker FD恢复expected old head/ref、全输入preflight、持久journal依次quarantine/object promotion/atomic refs/core handoff/activation→broker以activation domain签发内容寻址`PolicyActivationProofV1`及后置envelopes→不同credential orchestrator以transport domain签seal→28在无journal权限下离线验证双handoff+activation proof、只在外部staging生成七candidates并以不可刷新phase transition plan从base catalog/absent audit/exact CAS集合完成register→29以独立plan要求contract从base bytes迁移到预计算post bytes、同时保持任务源码/记录dirty不变后单进程promote transaction。
- `ProductRepositorySourceV1`/`RawGitInspectionProofV1` 在 Task 2 定义；Task 10 同一受信命令先在 owning-FD 进程内 inspect 原始布局，raw bytes 用后丢弃，再从已验证关系构建 path-free canonical logical topology/proof；Task 11 正式验证 proof/container 并产出 verified handle。宿主路径、raw bytes 及其 digest 均不进入可重现 artifact。
- `ReplayPolicyBindingV1` 与 `AcceptanceOperationSnapshotV1` 在 Task 2 分开构造，Tasks 5/16–17/28 只重检 operation snapshot，receipt 始终只绑定稳定 replay policy；Task 28 final-catalog 回归证明顺序 register 不使旧 receipt 失效。
- containment 七方法、消息字段、序号/摘要链、权限、binary identity、唯一 kernel mechanism 与 handoff credential-domain contract 在 Task 13 冻结；Task 14 不能重新选型。Tasks 22/27 只能通过 Task 14 root-owned broker 的 `exec-pty` 进入 producer/orchestrator 不同 UID/GID/user+mount+PID namespace；broker/profile启动前必须联合验证transport/activation registries的key ID、raw/resolved leaf key集合互斥及purpose/domain。broker另独占Task22 lifecycle proof/quarantine、Task26 completion record签名/验证、Task27 journal capability与policy-activation-purpose signer；orchestrator只获transport-seal-purpose signer，fresh zsh/env allowlist本身不算隔离证据。activation私钥不进入任何child；Tasks28/29只从transport closure取得broker签名proof并以两个固定公开registry联合离线验证，不能读取root journal。
- Task 3 是唯一 scanner owner；receipt/barrier/collect 只调用该接口，无依赖循环。
- Task 1 给出 bootstrap hashes；Tasks 18–21/23 有 exact builders/records。Task 23 在 platform compare 后把真实 uv/npm/pytest/Vitest 全部送入 Task 14 containment，无接口 network namespace+census 证明而非只依赖 `--offline`。
- 跨 Task/权限域只允许已提交records/CAS/export requests、broker签名completion/lifecycle/activation proofs与编排器验证的绝对外部`$TRUSTED_HANDOFF_STORE_ROOT`；producer无signing key/agent且与orchestrator使用不同OS credential/namespaces。transport seal与activation proof分别使用不可互换的purpose/domain tag，两registry任何ID/raw/resolved key交集都在首次授权写前失败。Task22 provisioner先在store外quarantine，正常release或reaper完成destroy→verify→baseline proof后才durable publish；destroy/污染/超时失败durable artifactWrites=0。Task26 review PASS后broker物化Task18固定toolchain并在同一进程持有pinned Git view，从review evidence经有界接口恢复及复验commit/base/ref/plan/spec blobs后生成completion record；Task27仅从只读FD读取typed old head/ref并以persistent journal+activation marker协调Git refs与core policy handoff，ACTIVATED后broker签发proof和后置envelopes。Tasks28/29在无journal权限下离线验证proof，拒绝缺失、篡改、错transaction/consumer/destination或非ACTIVATED状态；两者分别在事务进程内一次性封存`AllowedTaskOutputsV1`，源码dirty保持原digest，catalog/contract等事务输出只能从固定base/absence precondition迁移到预计算post bytes，入口、持锁写前与写后都复用同一plan，任何refresh或额外CAS零写拒绝。orchestrator只获transport signing-key FD并执行transport全链，consumer执行`verify-cache→fetch→import→verify-policy-activation→finalize→read`。所有artifact/authorization-bearing Git plumbing都只走Task7/18 pinned executor的trusted config+empty-attribute+worktree snapshot capability，无host/path Git旁路。Tasks22–23、25、27–29的0700 roots用`cleanup-owned-tree`，Task28多根全尝试聚合后才解除trap。
- Review Focus 五项分别由 Tasks 5/17、3/20、14/21、19–20、7/10–11/13–14/22–29 持有，编号无漂移。
- 29 个 Task 均为单独实现会话、单独 commit、单独新审查会话；Task 13、27、28 是不可跳过的硬门。

## 执行与批准状态

- E2 v2 书面规格已批准；本修订计划等待用户复审。
- 规格审批历史只改变 `Status` 行；本轮提交必须同时通过“相对 `06827f2e` 规格零 diff”和“相对 `2f304fe` 删除第 3 行后正文零 diff”，不得笼统声称两个 blob 相同。
- 执行方式固定为 subagent-driven：逐 Task 新实现会话 + 新独立审查会话，前一 Task 通过才进入下一 Task，最后全分支独立审查。
- 本次规划会话不授权实现或启动任何 Task；复审通过后从 Task 1 开始，不恢复旧 Task 6，也不跳过 Task 13、27、28。
