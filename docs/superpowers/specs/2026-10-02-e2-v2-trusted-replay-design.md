# E2 v2 可信重放协议设计规格

Status: proposed-for-review
Type: architectural-design / specification-amendment
Decision date: 2026-10-02
Subsystem: Trusted Acceptance Evidence Bridge / E2 trusted replay
Approved architecture: 可信工具链包 + 两阶段验证

## 1. 决策摘要

E2 v2 以“先证明输入可验证，再允许执行”为核心顺序，取代旧 E2 重放合同：

1. **阶段 A：无副作用 artifact 预检。** 对一次 `promote`、`reconcile` 或正式只读 `validate` 选中的全部 E2 指针先完成结构、路径、大小、摘要、规范 JSON、schema 和策略绑定校验。任一指针无效时，不启动任何 E2 子进程、不创建 checkout、不写 CAS、不修改 catalog/contract。
2. **阶段 B：可信重放。** 只有阶段 A 全部通过后，才从内容寻址、按平台和架构封闭的可信工具链包启动固定命令。验证重放只在内存中生成本轮事实，永不写 CAS；新 `collect` 只有在整轮重放、报告解析、输出脱敏和残留进程清理全部成功后，才原子写入新的 v2 receipt。
3. **输出先脱敏，再截断。** stdout/stderr 经过跨分块流式脱敏器后才进入有界安全缓冲；原始字节不会因硬截断留下 token 片段。receipt 只持久化注册表拥有的预期测试身份，不保存报告中任意、未受信的 ID。
4. **每次调用进入不可逃逸 containment leaf。** setup 和 test invocation 各自拥有内核/VM 强制继承的成员边界；父进程成功、失败或超时后都必须终止 leaf 全部成员并取得 empty proof。PGID 仅辅助发信号。
5. **真实 reporter 合同。** `pytest-json-v1` 兼容 `pytest-json-report 1.5.0` 对零值 outcome 字段的省略，同时严格闭合 `collected / deselected / total / tests`；`vitest-json-v1` 支持 Task 11 的“测试文件参数 + `-t` 精确测试名”选择形态。
6. **不再信任主机工具。** Git、uv、Node、npm CLI 运行时闭包以及 profile 需要的 Python 运行时都必须来自内容寻址工具链包；不得发现、验证后复制或执行主机 `PATH` 中的同名程序。
7. **所有直接读取都有上限。** Git 控制元数据、machine report、receipt 和工具链 manifest 均有固定单文件及聚合字节上限；超限使用稳定阻塞码，不读取后再“尽量继续”。
8. **Python setup 不解析项目。** v1 先用包内 uv 与包内 CPython 创建空 venv，再以 `uv pip sync` 从带 hash 的冻结 requirements lock 和 wheel-only flat view 安装依赖；不存在项目级同步步骤，不发现、安装或构建当前 project/workspace/editable。
9. **工具链安全公告闭合。** uv 精确固定为修复 GHSA-4gg8-gxpx-9rph 的最小版本 `0.11.15`；工具链构建/审核时冻结官方公告快照与审核时间，运行时只验证包内证据，不在线查询公告服务。

本规格不宣称 Task 5 已完成。Task 5 的五轮修复和最终独立审查仍是失败/阻塞历史；E2 v2 是对协议的后继修订，不是第六轮隐式修补。

## 2. 取代范围与保留边界

本规格取代 `2026-10-02-trusted-acceptance-evidence-bridge-design.md` 中以下 E2 条款：

- 第 3.3 节信任根中的旧 E2 工具/命令信任描述；
- 第 6.2 节 E2 allowlist suite 重放合同；
- 第 7.1、8.1、9、10.3、11、13.3、13.6、14 节中与 E2 执行、receipt、工具发现、报告解析或 Task 11 命令形态相关的条款。

未被取代并继续有效的内容包括：138 项冻结分母、四种状态、E1/E3/E4 责任、catalog 六字段指针、register 不晋升、validate 只读、promote/reconcile 的唯一状态写权限、Ticket 23 仅拥有 C01–C03、E4 独立签名要求、零容忍规则以及三引擎边界。

旧实施计划不是 E2 v2 的实施授权：Task 5 保持 `blocked / superseded`；Task 6 未开始；Task 10 和 Task 11 后续必须依据本规格重写其 E2 部分。本文不创建新的实施计划。

## 3. 威胁模型

### 3.1 必须闭合的已知缺陷

| 已知缺陷 | v2 防线 |
| --- | --- |
| 输出先硬截断再脱敏，边界处可留下 Provider token 片段 | 原始流先进入跨分块脱敏器，只有脱敏后的字节可进入截断缓冲 |
| 父进程成功退出后，后台后代可通过 double-fork、`setsid()` 或 `setpgid()` 逃离原 PGID | invocation 必须运行在平台不可逃逸 containment realm；PGID 只辅助发信号，不作为“全部后代已清空”的证明 |
| pytest 真实 summary 省略零值字段；Vitest 3.0.8 的 `-t` 会把同文件未匹配项保留为 skipped；npm 只是 JS launcher 而非单文件 | parser 采用真实 schema 兼容规则；Vitest 允许非预期 skipped、拒绝非预期 executed；工具链 package 显式封闭 npm CLI 模块树 |
| artifact 指针无效时仍重放并写新 CAS | 全操作阶段 A 先预检全部 artifact；验证路径阶段 B 不写 CAS |
| `.git` 指针、`commondir`、`packed-refs` 等读取无上限 | 所有直接 Git 元数据读取走统一有界读取器，并受单文件/聚合预算约束 |
| Python setup 读取 `pyproject.toml`/workspace、更新 lock 或执行 first-party editable build backend | Python v1 只接受 hash-pinned requirements lock，以 `uv pip sync --no-build --no-cache` 消费受信 wheel view；checkout 项目元数据不进入依赖输入，lock 前后 bytes 必须相同，setup exec census 必须证明零 build-backend exec |
| 受 GHSA-4gg8-gxpx-9rph 影响的 uv 可让恶意 wheel entry-point 名逃逸环境 scripts 目录并任意写文件 | 精确使用 uv `0.11.15`；工具链绑定离线安全审核快照；setup 只开放声明安装根并证明许可根外 write set 与 bytes 均未变化 |

### 3.2 额外防护对象

协议还必须防止：

- 通过主机 `PATH`、shell、shebang `/usr/bin/env`、npm 的宿主模块树或可替换 symlink 注入未登记工具；
- 用错误 OS/architecture/ABI 的二进制包、歧义 archive、压缩炸弹、缺文件工具链包或部分 npm 安装冒充受信闭包；
- stale report、超限 report、报告行重复、summary 自相矛盾、选择零测试或报告额外测试；
- 用测试名正则“可能匹配”代替对本轮实际测试身份的证明；
- 把任意 report node ID、失败输出中的秘密、完整环境或主机绝对路径写入 receipt；
- 进程在输出管道保持打开时让 runner 永久等待 EOF；
- 旧 pointer 未撤销时追加新 pointer，令运行者任意挑选“较方便”的证据；
- 让 Git 消费宿主 config、attributes、hooks、helper、alternate object store、replace/graft/shallow/promisor 或未审查 pack sidecar；
- 在 policy/catalog/contract 快照变化后提交旧验证结果。

### 3.3 信任边界外

本协议不抵抗同时拥有受保护 Git 历史、registry/profile、工具链包内容与其摘要发布权限的治理攻击者。已登记的 containment supervisor、OS 内核、固定平台 ABI 和 manifest 明确允许的系统动态库属于平台信任根；任何宿主可执行文件、包管理器、解释器、用户配置、`PATH` 和单纯 POSIX PGID 都不属于信任根。

## 4. 强制不变量

以下不变量没有“尽力而为”降级路径：

1. **A-before-B：** 一次验证操作选中的全部 E2 artifact 未全部通过阶段 A 前，不得进入任一 E2 阶段 B。
2. **验证零写入：** `E2SuiteVerifier.verify`、正式 `validate`、`promote` 的验证阶段和 `reconcile` 的验证阶段不得调用 `ContentAddressedStore.put`，也不得修改 catalog、contract 或 trust 输入。
3. **采集后写入：** `collect` 只可在成功重放、报告闭合、containment realm 空且销毁、输出脱敏完成后写一次规范 receipt；失败运行不产生 receipt 对象。
4. **无宿主回退：** 工具链 entrypoint 缺失、摘要不符或平台不符时阻塞；不得搜索主机 `PATH`，不得用系统同名工具补位。
5. **不可逃逸 containment：** 每个 setup/test invocation 都在独立 containment leaf 中执行；double-fork、`setsid()`、`setpgid()` 和关闭 stdio 不能改变 membership。PGID 为空不能证明 leaf 为空。
6. **先脱敏后截断：** 只有已从流式脱敏器释放的安全字节可以计入持久化输出上限。
7. **身份最小化：** receipt 的测试身份必须是 `expectedTestIds` 的有序子集；Vitest 未匹配 skipped 身份只用于内存 count 闭合，不进入 receipt/facts。成功 receipt 必须恰好覆盖预期 executed 集合。
8. **报告闭合：** process exit、report exit/success、summary counts、tests rows 和 registry selection 必须互相一致。
9. **有界读取：** 任一受控文件在打开前、读取中或读取后超限、变型、变 inode 或变 mtime，均阻塞本轮验证。
10. **固定主体：** checkout、lockfile、依赖 entrypoint 和测试文件均来自精确 `git://product@<40-sha>` 与内容寻址离线仓库；当前工作树不参与重放。
11. **唯一证据选择：** 对冻结快照中的每个 requirement，必须恰有一个未撤销 matching pointer；零个或多个都在阶段 A 阻塞，运行者无选择权。
12. **Python wheel-only 安装：** Python setup 只能按固定的 `uv venv` + `uv pip sync` 两步消费唯一冻结 requirements lock；禁止项目/workspace/editable/source/build backend，lockfile 不能被检查更新或改写。
13. **声明写边界：** checkout、toolchain、dependency view 与 lock 默认只读；setup 只能写 suite 声明的安装根和 invocation 临时根，完整 write-set proof 中任何许可根外写尝试或 bytes 变化都阻塞。

## 5. 总体架构与顺序

```text
pointer 集合 + registry/profile/contract 快照
                     │
                     ▼
         阶段 A：E2 artifact barrier
         ├─ 冻结 candidate set；每 requirement 唯一 active pointer
         ├─ 六字段 pointer/profile/verifier/subject 配对
         ├─ CAS 派生路径、类型、大小、稳定读取、SHA-256
         └─ receipt/v2 规范 JSON、schema、policy/toolchain 绑定
                     │ 全部通过
                     ▼
         阶段 B：TrustedReplaySession
         ├─ 验证并展开 trusted toolchain package
         ├─ 有界核验 Git 元数据并构造固定对象 checkout
         ├─ 核对 lockfiles / 离线依赖仓库 / dependency entrypoints
         ├─ 每 invocation 独立不可逃逸 containment leaf
         ├─ 流式脱敏 → 安全截断；有界解析 machine report
         └─ 证明 containment 为空并生成内存 VerificationResult
                     │
                     ▼
         快照复核 → 聚合 → 原子状态投影
```

`collect` 没有既有 pointer，因此不执行 artifact barrier；它仍必须先完成 suite policy、subject 和 toolchain 引用的只读结构预检，再进入同一个 `TrustedReplaySession`。成功后生成 `trusted-e2-receipt/v2` 并原子写 CAS；该对象随后可由独立 `register` 登记。

## 6. 数据与清单结构

### 6.1 `E2SuiteDefinitionV2`

v2 suite 是 registry 拥有的闭合策略，pointer 不能提供或覆盖任何字段：

```json
{
  "schema": "trusted-e2-suite/v2",
  "subjectRepository": "product",
  "workingDirectory": "frontend",
  "toolchainRef": "sha256:<toolchain-package-digest>",
  "containmentProfile": "linux-cgroup-v2/v1",
  "dependencyRepositoryRefs": {
    "npm": "sha256:<offline-npm-repository-digest>"
  },
  "derivedEntrypoints": {
    "vitest": {
      "path": "frontend/node_modules/vitest/vitest.mjs",
      "type": "js-module",
      "digest": "sha256:<installed-entry-file-digest>",
      "interpreter": "node"
    }
  },
  "setupInvocations": [
    {
      "id": "npm-ci",
      "tool": "npm",
      "args": ["ci", "--ignore-scripts", "--offline", "--replace-registry-host=never", "--cache=@dependency-repository/npm"]
    }
  ],
  "testInvocations": [
    {
      "id": "frontend-persistence-1",
      "tool": "node",
      "args": ["node_modules/vitest/vitest.mjs", "run", "src/CreativeCanvas.test.tsx", "-t", "^<转义后的精确 fullName>$", "--reporter=json", "--outputFile=<受控相对路径>"],
      "selection": {
        "kind": "vitest-file-exact-name/v1",
        "file": "frontend/src/CreativeCanvas.test.tsx",
        "exactName": "<精确测试名>",
        "expectedId": "frontend/src/CreativeCanvas.test.tsx::<完整 fullName>"
      },
      "report": {
        "kind": "vitest-json-v1",
        "path": "<受控相对路径>"
      }
    }
  ],
  "expectedTestIds": ["frontend/src/CreativeCanvas.test.tsx::<完整 fullName>"],
  "allowedEnvironment": {"CI": "1", "TZ": "UTC"},
  "timeoutSeconds": 120,
  "lockfileDigests": {"frontend/package-lock.json": "sha256:<digest>"}
}
```

规则：

- `tool` 是工具链 manifest 的逻辑 entrypoint，不是文件名搜索请求；
- `derivedEntrypoints` 是按逻辑名排序的对象；每项只允许且必须包含 `path/type/digest/interpreter`。`path` 是 NFC、无 `.`/`..` 的 checkout 相对路径；`type` 仅为 `js-module`、`python-module`、`python-venv-symlink` 或 `native-executable`；`digest` 对普通文件取原始 bytes SHA-256，对 symlink 取 UTF-8 规范 target bytes SHA-256；`interpreter` 必须是同一 suite 的 toolchain entrypoint 逻辑名，`native-executable` 固定为 `null`；
- setup 后 runner 必须从已验证 lock mapping 重建预期安装树，以 repository manifest 的 `installTreeDigest` 核对完整树，再在该树内按 `path/type/digest/interpreter` 重新解析每个入口。`python-venv-symlink` 的规范 target 必须精确指向包内 CPython；`js-module/python-module` 只能由登记解释器直接加载；`native-executable` 还必须属于包条目的 `nativeClosure`。路径被替换、类型变化、摘要不符、解释器不符、入口离开安装树或通过 `.bin`/PATH 发现均为 `E2_DERIVED_ENTRYPOINT_INVALID`；
- setup/test 都是非空、不可变的 invocation 对象；v1 的裸 `setupArgv/argv` 不自动升级；
- parser 与 selection 分开。selection 描述 registry 预期身份，report 证明实际身份；二者不能通过“字符串在 argv 中出现”替代；
- 每个 Task 11 Vitest 精确测试名使用一个 invocation，文件参数和 `-t` 参数分别核对；`-t` 值必须由 loader 对 `exactName` 做 ECMAScript RegExp 元字符转义后加 `^...$` 生成，registry 不接受任意正则；
- Python suite 使用 `pytest-nodeids/v1`，argv 中逐一携带精确 node ID；
- report 路径必须位于隔离 checkout 的受控临时子目录，执行前确认不存在，执行后只读一次；
- npm setup 的规范 argv 必须含 `ci --ignore-scripts --offline --replace-registry-host=never --cache=@dependency-repository/npm`。Python v1 setup 恰有以下两个 invocation，顺序不可交换，也不得附加第三个 setup：先以包内 uv 执行 `venv <venv-path> --python=@toolchain/python --no-project --no-config --no-python-downloads --no-cache`，再执行 `pip sync --python=<venv-path>/bin/python <requirements-lock-path> --offline --no-index --find-links=@dependency-repository/python --no-build --require-hashes --no-sources --config-file=@toolchain/uv-empty-config --no-python-downloads --no-cache`；禁止 `--seed`、`--project`、`--group`、`--extra` 或其他 source/config 参数。`<venv-path>` 与 `<requirements-lock-path>` 必须是 suite 中具体、无占位符的 checkout-root 相对路径，Task 11 固定为 `backend/.venv` 与 `backend/requirements.lock`；
- Python setup 固定 `UV_OFFLINE=1`、`PIP_NO_INDEX=1`、`UV_PYTHON_DOWNLOADS=never`。仅 `@dependency-repository/<ecosystem>`、`@toolchain/python` 与 `@toolchain/uv-empty-config` 是受支持的 argv token：repository token 分别替换为 containment 内含 `_cacache` 的已验证 npm COW cache-root 或 Python flat find-links 绝对目录，两个 toolchain token 分别替换为已验证 CPython entrypoint 和零字节受信 `uv.toml` 的单个绝对路径；registry 不接受其他变量、路径模板或环境展开。测试由包内 Node 或 venv 中链接回包内 CPython 的解释器直接加载已验证 derived entrypoint，不经过 npm package-script shell、项目 `.bin` PATH 或 venv activation script；
- `dependencyRepositoryRefs` 只能引用 6.3 节的内容地址。network namespace 默认无 egress；任何 DNS/socket/live registry 探测即 `E2_LIVE_REGISTRY_ACCESS`，即使本地缓存最终令命令退出 0 也不能接受。

### 6.2 `trusted-e2-toolchain-tar-zstd/v1`

v2 只接受一种工具链容器：**zstd 单帧压缩的规范 POSIX ustar**，schema/format 字面值为 `trusted-e2-toolchain-tar-zstd/v1`。工具链包位于 acceptance root 下的预配置只读内容仓库：

```text
trust/toolchains/sha256/<digest前2位>/<64位digest>
```

`toolchainRef` 是对完整压缩包字节的 SHA-256，不是 manifest 或展开目录的摘要。缺包直接阻塞，不允许运行时联网取得替代包。

manifest 必须是 archive 的第一个 member，固定路径 `.trusted-e2-toolchain/manifest.json`，UTF-8 规范 JSON，最大 1 MiB。manifest 不自列举；保留路径及其虚拟父目录 `.trusted-e2-toolchain` 不进入 archive 或 `files`。这是且只是父目录先行规则的正式例外；`files` 必须逐条、且只逐条列出 archive 的其余所有 member：

```json
{
  "schema": "trusted-e2-toolchain/v1",
  "containerFormat": "trusted-e2-toolchain-tar-zstd/v1",
  "payloadTreeDigest": "sha256:<canonical-files-list-digest>",
  "platform": {
    "schema": "e2-platform-identity/v1",
    "executionKind": "native-rootfs",
    "executionOs": "linux",
    "arch": "arm64",
    "executionImageDigest": "sha256:<canonical-rootfs-image-digest>",
    "osReleaseDigest": "sha256:<canonical-os-release-digest>",
    "loader": {"path": "/lib/ld-linux-aarch64.so.1", "sha256": "sha256:<digest>", "buildId": "hex:<elf-build-id>"},
    "libc": {"family": "glibc", "version": "2.41", "sha256": "sha256:<digest>", "buildId": "hex:<elf-build-id>"},
    "containmentBackend": "linux-cgroup-v2/v1",
    "hostCompatibility": {
      "schema": "linux-host-compat/v1",
      "kernelVersionScheme": "numeric-triplet/v1",
      "kernelMinInclusive": [6, 2, 0],
      "kernelMaxExclusive": [7, 0, 0],
      "kernelAbiBuildIds": ["sha256:<allowed-kernel-abi-build-id>"],
      "requiredCapabilities": ["cgroup.kill", "clone3.into-cgroup", "namespace.user", "namespace.mount", "namespace.pid", "namespace.network", "namespace.ipc"]
    },
    "allowedSystemLibraries": [
      {"path": "/lib/libc.so.6", "soname": "libc.so.6", "sha256": "sha256:<digest>", "buildId": "hex:<elf-build-id>"},
      {"path": "/lib/libm.so.6", "soname": "libm.so.6", "sha256": "sha256:<digest>", "buildId": "hex:<elf-build-id>"}
    ]
  },
  "entrypoints": {
    "git": {"argvPrefix": ["bin/git"], "version": "git version 2.53.0"},
    "uv": {"argvPrefix": ["bin/uv"], "version": "uv 0.11.15 (<pinned-build-metadata>)"},
    "node": {"argvPrefix": ["bin/node"], "version": "v24.20.0"},
    "npm": {"argvPrefix": ["bin/node", "lib/node_modules/npm/bin/npm-cli.js"], "version": "11.19.0"},
    "python": {"argvPrefix": ["python/bin/python3"], "version": "Python 3.13.x"}
  },
  "configFiles": {
    "uvEmpty": {"path": "config/uv-empty.toml", "size": 0, "sha256": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}
  },
  "closures": {
    "cpython": {"roots": ["python/bin", "python/lib/python3.13", "lib/libpython3.13.so.1.0"], "treeDigest": "sha256:<digest>"},
    "npm": {"roots": ["lib/node_modules/npm"], "treeDigest": "sha256:<digest>"},
    "git": {"roots": ["bin/git", "libexec/git-core", "share/git-core/templates"], "treeDigest": "sha256:<digest>"}
  },
  "runtimePolicies": {
    "uvSetup": {
      "uvVersion": "0.11.15",
      "allowedChildExecs": [
        {
          "entrypoint": "python",
          "argvDigest": "sha256:<normalized-uv-0.11.15-interpreter-probe-argv-digest>",
          "cwdToken": "@venv-root",
          "environmentDigest": "sha256:<canonical-probe-environment-digest>"
        }
      ]
    }
  },
  "securityReview": {
    "schema": "toolchain-security-review/v1",
    "policyDigest": "sha256:<toolchain-security-policy-v1-digest>",
    "reviewedAt": "<RFC3339-toolchain-review-time>",
    "advisorySnapshot": {"path": "security/advisories.json", "size": 1234, "sha256": "sha256:<canonical-advisory-snapshot-digest>"},
    "applicableHighRiskAdvisories": [],
    "resolvedAdvisories": [{"id": "GHSA-4gg8-gxpx-9rph", "affected": "<0.11.15", "fixedIn": "0.11.15"}]
  },
  "dynamicDependencies": {
    "bin/git": ["lib/libz.so.1", "system:/lib/libc.so.6@sha256:<digest>"],
    "bin/node": ["system:/lib/libc.so.6@sha256:<digest>", "system:/lib/libm.so.6@sha256:<digest>"]
  },
  "files": [
    {"path": "bin", "type": "dir", "mode": "0555"},
    {"path": "bin/git", "type": "file", "size": 123, "mode": "0555", "sha256": "sha256:<digest>"},
    {"path": "bin/python3", "type": "symlink", "mode": "0777", "target": "../python/bin/python3"},
    {"path": "config", "type": "dir", "mode": "0555"},
    {"path": "config/uv-empty.toml", "type": "file", "size": 0, "mode": "0444", "sha256": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"},
    {"path": "security", "type": "dir", "mode": "0555"},
    {"path": "security/advisories.json", "type": "file", "size": 1234, "mode": "0444", "sha256": "sha256:<canonical-advisory-snapshot-digest>"}
  ]
}
```

`payloadTreeDigest` 是对按 path byte 升序后的完整 `files` 数组做规范 JSON 编码所得 SHA-256；每个 closure `treeDigest` 对其 `roots` 覆盖的 file entries 用同一算法计算。receipt 的 `toolchainManifestDigest` 是 manifest 规范 JSON bytes 的 SHA-256。三者都不等同于 `toolchainRef`，从而避免 manifest 自引用。

容器规则固定如下：

- 只接受 ustar regular file、directory 和 symbolic link；拒绝 hard link、sparse、PAX/GNU extension、device、FIFO、socket 和未知 typeflag；
- path 必须是 NFC UTF-8、相对路径、无空/NUL、无 `.`/`..` 组件、无反斜杠；除首项 manifest 的虚拟父目录例外外，每个 payload path 恰好出现一次且每个非根父目录都必须作为显式 directory member 更早出现；拒绝大小写折叠后重复；
- symlink `target` 必须是 manifest 中的相对字面值，逐组件解析后仍留在展开根内；symlink 不可作为 archive 后续 member 的父目录；
- file 必须有 `size/mode/sha256`；dir 必须有 `mode` 且 size 为 0；regular file/dir 拒绝 setuid、setgid、sticky 和 group/other writable。symlink 必须有 `mode/target` 且不接受 digest/size，mode 固定为 `0777` 但不作为目标权限；
- archive header 的 uid/gid/mtime/uname/gname 固定为 0/空。member 顺序唯一确定为：首项 manifest regular file；随后严格按 manifest `files` 的 UTF-8 path byte 升序逐项出现。manifest 的保留父目录不得作为 member 出现；payload 的显式父目录因其 path 是子 path 的严格前缀而自然先行。缺 payload 父目录、重复父目录、保留父目录出现、manifest 非首项或 payload 错序均为 `E2_TOOLCHAIN_ARCHIVE_INVALID`；
- zstd 必须恰好一个带 checksum 的 frame，禁用 dictionary、skippable/trailing frame，window 上限 256 MiB；ustar header checksum 必须正确，archive 以恰好两个 512-byte zero block 结束且之后无字节；
- 压缩包最大 1 GiB、最多 100,000 个 payload member、单个 regular file 最大 256 MiB、展开总量最大 4 GiB、展开量/压缩包大小最大 100:1；在写出越界 entry 前即中止；
- archive 解码、路径检查和摘要核验在 no-exec 临时根完成；所有 member 与 manifest 一致且 `payloadTreeDigest` 匹配后才把目录切为只读可执行。

运行时闭包规则：

- `executionKind` 仅为 `native-rootfs` 或 `vm-guest-rootfs`。`platform` 是执行环境身份，不是打包机器标签；`schema/executionKind/executionOs/arch/executionImageDigest/osReleaseDigest/loader/libc/containmentBackend` 逐字段精确比较。`osReleaseDigest` 是 image 内规范 `/etc/os-release` regular-file bytes 的 SHA-256；native backend 的 `executionImageDigest` 必须等于 containment profile 登记的 `executorImageDigest`，VM backend 则必须等于精确 `guestImageDigest` manifest 登记的 rootfs digest。不存在自由文本 `minimumOs/minimumKernel` 比较；
- `linux-host-compat/v1` 是唯一允许的 Linux host predicate：实际 `uname.release` 必须先匹配 `^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)(?:[-+].*)?$`，仅前三个十进制分量作为无符号 32-bit tuple，按 major/minor/patch 数值字典序满足 `minInclusive <= actual < maxExclusive`；后缀不参与排序。`kernelAbiBuildId` 定义为规范 JSON `{executionOs,arch,unameRelease,kernelGnuBuildId}` bytes 的 SHA-256，其中 `kernelGnuBuildId` 必须由只读 `/sys/kernel/notes` 的 GNU build-id note 唯一取得；缺失/多个/畸形 note 即 `E2_CONTAINMENT_UNAVAILABLE`。所得 digest 必须精确命中 `kernelAbiBuildIds`，从而约束相同 numeric version 的发行版变体。allowlist 必须非空、bytewise 排序、无重复；能力集合也必须与实测集合逐项满足；
- loader、libc 和每个系统动态库按规范绝对路径、文件 SHA-256 与 ELF build ID 三者精确核对；SONAME 仅作附加一致性检查。同 SONAME、不同 bytes/build ID 必须 `E2_TOOLCHAIN_PLATFORM_MISMATCH`，不得以版本字符串或“ABI 看起来兼容”放行；
- manifest 必须列出每个 ELF/Mach-O entrypoint 和 helper 的完整动态依赖。依赖要么是包内 file，要么按规范绝对路径命中 `allowedSystemLibraries` 的完整 identity；运行前离线扫描，发现未列依赖、RPATH 越界、SONAME-only 命中或宿主任意搜索路径即 `E2_DYNAMIC_DEPENDENCY_UNTRUSTED`；
- CPython closure 必须包括可执行文件、`libpython`、完整 stdlib、`lib-dynload`、编码和 CA/zoneinfo 等运行必需资源；venv 解释器只能链接回该 closure；
- `configFiles.uvEmpty` 必须逐字段等于零字节 regular file `config/uv-empty.toml` 及其固定 SHA-256；`uv pip sync --config-file=@toolchain/uv-empty-config` 用它关闭 checkout/用户/系统配置发现，不能用宿主 `/dev/null`、临时配置或 checkout 文件替代；
- `runtimePolicies.uvSetup` 必须固定 uv 版本，并以 `allowedChildExecs` 完整列出该版本允许的包内 Python interpreter probe identity、规范 argv digest、cwd token 与环境 digest；规范化只把已验证 toolchain root 与 suite venv root 分别替换为固定 token，其他 argv/env bytes 逐字节绑定。数组按 `(entrypoint,argvDigest,cwdToken,environmentDigest)` bytewise 排序且无重复；未命中项不得作为“uv 内部行为”放行；
- `securityReview` 是每个实际工具链 artifact 定稿前重新执行的离线构建证据，不是运行时扫描替代品，也不能跨工具版本/build 复用。`reviewedAt` 是真实 RFC 3339 审核时间；`advisorySnapshot` 指向包内规范 JSON，逐项保存官方 maintainer advisory/release source URI、获取时间、原文 SHA-256、工具名/精确版本、affected/fixed range、severity/weakness 与适用判断。snapshot 必须覆盖审核时已发布的适用公告，完整列入 `files` 并绑定 manifest/receipt；runtime 不联网刷新或放宽结果；
- `toolchain-security-policy/v1` 拒绝任何仍适用于包内工具版本的 Critical/High 公告，也拒绝任意 severity 的任意文件写/删除、代码执行、路径逃逸、archive extraction 或 package-install root escape 公告。uv `0.11.15` 必须把 GHSA-4gg8-gxpx-9rph 记录为 `<0.11.15` 受影响、`0.11.15` 已修复；`applicableHighRiskAdvisories` 必须为空。证据缺失、未在该 artifact 定稿前重新生成、source/digest/version/range 矛盾或仍有适用项均为 `E2_TOOLCHAIN_CLOSURE_INVALID`；
- 本次 `0.11.15` 最小升级决策只依据 uv maintainer 的官方 advisory `https://github.com/astral-sh/uv/security/advisories/GHSA-4gg8-gxpx-9rph` 与官方 `0.11.x` changelog；它们是规格来源记录，不是 replay runtime 要访问的 URL；
- npm closure 必须包括 `npm-cli.js` 的完整模块树和包内 Node；Git closure 必须包括 `libexec/git-core` helpers、templates 及实际会调用的 shims；单个 launcher 文件不构成闭包；
- shebang 不通过 `/usr/bin/env` 解析。entrypoint 展开为 manifest 中的绝对 argv prefix；runner 不传入宿主 PATH；
- runner 始终 `shell=False`。npm v2 仅允许不执行 lifecycle script 的离线安装；测试由包内 Node/Python 直接加载已验证依赖 entrypoint，不经过 package-script shell；
- v2 禁止 live dependency download。`dependencyRepositoryRefs` 必须符合下一节的唯一映射与精确安装树规则；uv/npm 必须启用 offline/no-index 模式。
- 离线依赖中的 native extension/`.node` 文件也要做动态依赖扫描并遵守平台 allowlist；setup 禁止 lifecycle/postinstall script，缺少预构建闭包时阻塞而不是现场编译或联网下载。

稳定容器错误码为：`E2_TOOLCHAIN_PACKAGE_TOO_LARGE`、`E2_TOOLCHAIN_ARCHIVE_INVALID`、`E2_TOOLCHAIN_ENTRY_LIMIT`、`E2_TOOLCHAIN_EXPANSION_LIMIT`、`E2_TOOLCHAIN_CLOSURE_INVALID`、`E2_DYNAMIC_DEPENDENCY_UNTRUSTED` 和 `E2_DEPENDENCY_REPOSITORY_INVALID`。

### 6.3 `trusted-e2-dependency-repository/v1`

每个 `dependencyRepositoryRef` 指向一个 suite-scope、只读、内容寻址的 dependency container。唯一容器格式为 `trusted-e2-dependency-repository-tar-zstd/v1`：其 zstd/ustar/header/path/member/symlink 和首项例外规则与 6.2 节完全相同，manifest 固定为首个 member `.trusted-e2-dependencies/manifest.json`，虚拟父目录 `.trusted-e2-dependencies` 同样不得出现在 archive 或 `files`；其余 payload 必须显式父目录先行并按 path byte 排序。container 存放于：

```text
trust/dependencies/sha256/<digest前2位>/<64位digest>
```

引用 digest 对完整压缩 bytes 取 SHA-256。manifest 最大 1 MiB，不自列举；其余 member 必须由 `files` 完整列举。包 archive 只能位于固定 CAS member 路径 `blobs/sha256/<digest前2位>/<64位digest>`，路径 digest 必须等于 archive 原始 bytes digest：

```json
{
  "schema": "trusted-e2-dependency-repository/v1",
  "containerFormat": "trusted-e2-dependency-repository-tar-zstd/v1",
  "ecosystem": "npm",
  "payloadTreeDigest": "sha256:<canonical-files-list-digest>",
  "platformIdentityDigest": "sha256:<canonical-e2-platform-identity-digest>",
  "installationTreeDigest": "sha256:<canonical-complete-installation-tree-digest>",
  "consumerView": {
    "schema": "npm-cacache-view/v1",
    "npmVersion": "11.19.0",
    "cacacheVersion": "20.0.4",
    "contentVersion": 2,
    "indexVersion": 5,
    "treeDigest": "sha256:<canonical-generated-view-tree-digest>"
  },
  "packages": [
    {
      "packageId": "npm:vitest@3.0.8",
      "name": "vitest",
      "version": "3.0.8",
      "archiveKind": "npm-tgz",
      "archivePath": "blobs/sha256/ab/ab<62-hex>",
      "archiveDigest": "sha256:ab<62-hex>",
      "archiveSize": 12345,
      "consumer": {
        "originalFilename": "vitest-3.0.8.tgz",
        "resolved": "https://registry.npmjs.org/vitest/-/vitest-3.0.8.tgz",
        "integrity": "sha512-<canonical-base64-digest>",
        "cacheKey": "make-fetch-happen:request-cache:https://registry.npmjs.org/vitest/-/vitest-3.0.8.tgz"
      },
      "installTreeDigest": "sha256:<canonical-installed-files-digest>",
      "nativeClosure": []
    }
  ],
  "lockMappings": [
    {
      "lockfilePath": "frontend/package-lock.json",
      "lockfileDigest": "sha256:<digest>",
      "lockNode": "node_modules/vitest",
      "packageId": "npm:vitest@3.0.8",
      "archiveDigest": "sha256:ab<62-hex>",
      "resolved": "https://registry.npmjs.org/vitest/-/vitest-3.0.8.tgz",
      "integrity": "sha512-<canonical-base64-digest>",
      "installTreeDigest": "sha256:<canonical-installed-files-digest>"
    }
  ],
  "files": [
    {"path": "blobs", "type": "dir", "mode": "0555"},
    {"path": "blobs/sha256", "type": "dir", "mode": "0555"},
    {"path": "blobs/sha256/ab", "type": "dir", "mode": "0555"},
    {"path": "blobs/sha256/ab/ab<62-hex>", "type": "file", "size": 12345, "mode": "0444", "sha256": "sha256:ab<62-hex>"}
  ]
}
```

Python repository 使用同一 manifest schema，但 `consumerView` 与每个 package 的 `consumer` 固定为：

```json
{
  "consumerView": {
    "schema": "python-find-links-view/v1",
    "uvVersion": "0.11.15",
    "layout": "flat-directory",
    "treeDigest": "sha256:<canonical-generated-view-tree-digest>"
  },
  "consumer": {
    "originalFilename": "pytest-8.3.5-py3-none-any.whl",
    "extension": ".whl",
    "normalizedName": "pytest",
    "version": "8.3.5",
    "pythonTags": ["py3-none-any"],
    "hash": "sha256:<archive-digest>"
  }
}
```

正式规则：

- manifest 是拒绝未知/缺失/重复字段的规范 JSON；`payloadTreeDigest/platformIdentityDigest/installationTreeDigest/consumerView.treeDigest` 和所有 package/mapping/file digest 都是必填 64-hex SHA-256，不能使用版本字符串或文件名替代；
- `ecosystem` 仅为 `npm` 或 `python`，一个 container 只含一个 ecosystem。`packageId` 的规范形式分别为 `npm:<NFC-name>@<exact-semver>` 与 `python:<PEP-503-normalized-name>==<PEP-440-exact-version>`；禁止 version range、tag、latest、URL、VCS/path dependency 和重复 `(name, version)`；
- `archiveKind` 仅为与 ecosystem 一致的 `npm-tgz` 或 `python-wheel`；Python container 出现 sdist、`.tar.gz`、`.zip`、source tree 或任何需要 build backend 的输入，固定返回 `E2_PYTHON_SDIST_FORBIDDEN`。archive 内嵌 metadata 的 name/version 必须与条目逐字段一致。runner 在 no-exec 临时根先做路径、类型、member 数、单项/总量和压缩比检查，再向 package manager 暴露只读 view；archive path escape、特殊文件、hardlink、重复/大小写折叠 path 或未登记 symlink 均为 `E2_DEPENDENCY_REPOSITORY_INVALID`；
- `consumerView.schema` 必须与 ecosystem 一一对应，且其中 npm/cacache/uv 版本必须逐字段等于 toolchain manifest 的受信 closure。consumer view 是运行时派生物，不是 archive member，也不进入 `files/payloadTreeDigest`；`consumerView.treeDigest` 只绑定生成后的逻辑树，避免容器自引用。view builder 是只读纯函数：唯一输入是已验证 manifest 与 blob bytes；禁止读取 host cache、clock、locale、PATH 或 registry。view root 自身不进入摘要；其下所有生成目录 mode 为 `0555`、文件 mode 为 `0444`、mtime/uid/gid 不参与语义。生成完成后按非空相对 path byte 排序，对每个 directory/file 的 `path/type/mode/size/sha256`（directory 的 size 固定 0 且无 sha256）数组取规范 JSON SHA-256，必须等于 `consumerView.treeDigest` 才可暴露；
- `packages` 按 `packageId` bytewise 升序，`lockMappings` 按 `(lockfilePath, lockNode)` bytewise 升序，均不得重复。npm `lockNode` 是 package-lock v3 的规范 packages key；Python `lockNode` 是锁文件内规范化的 distribution name + version + marker/environment tuple；marker 必须在 suite 的精确平台身份下先确定求值；
- suite 使用的每个 ecosystem 在 `dependencyRepositoryRefs` 中必须恰有一个引用，未使用的 ecosystem 不得出现；suite 中每个 lockfile node 必须在对应 repository 中取得一条 mapping，且 mapping 的 `lockfilePath/lockfileDigest` 必须精确命中 suite `lockfileDigests`。`packageId/archiveDigest/installTreeDigest` 必须与 package 条目逐字段相等。零条、两条、同包多摘要、同 node 多包或 lockfile 中未固定 archive hash 均为 `E2_DEPENDENCY_MAPPING_INVALID`；
- Python suite 必须恰有一个 requirements-format lockfile。runner 在任何 setup spawn 前以 8.2.3 节同一 `BoundedStableReader` 语义固定其原始 bytes 与 SHA-256，并只接受 UTF-8 requirements 子集：每个生效 requirement 都是唯一的 PEP 503 规范 name、`==` 精确 PEP 440 version、可选且已在固定平台求值的 marker，以及至少一个 `--hash=sha256:<64hex>`；禁止 direct URL、VCS/path、`-e/--editable`、`-r/--requirement`、`-c/--constraint`、index/find-links/config option、未固定版本、无 hash、重复 requirement 和 `pyproject.toml`/`pylock.toml`/`setup.py`/`setup.cfg` 输入。任一 source/editable/build 请求在 uv/venv spawn 前返回 `E2_PYTHON_SDIST_FORBIDDEN`，其他 lock 映射歧义返回 `E2_DEPENDENCY_MAPPING_INVALID`；
- repository 必须是该 suite 的精确闭包：每个 `packages` 条目和每个 blob 都必须由一个 lock mapping 引用，且每个 mapping 必须被安装。额外包、额外 blob、未使用 mapping、缺包或 archive 路径与 digest 不一致均为 `E2_DEPENDENCY_REPOSITORY_INVALID`；
- 每个 package 的 `installTreeDigest` 对该包完成规范解包后、执行任何项目代码前得到的完整安装条目数组取规范 JSON SHA-256；top-level `installationTreeDigest` 对所有实际安装 package 的条目并集取摘要。条目 schema 与 toolchain `files` 相同并额外要求 `ownerPackageId`；排序、路径、mode、symlink 和大小写折叠规则不变。runner 必须同时匹配逐包 digest 和 top-level digest，不接受包间覆盖；
- `nativeClosure` 必须逐项列出安装树内 ELF/Mach-O/`.node`/Python extension 的 `path/digest/format/needed/rpath`；`needed` 只能精确引用同安装树文件、toolchain file 或 platform identity 中 path+digest+buildId 已固定的 system library。非空 native file 未列、同 SONAME 不同 ABI、RPATH 越界或运行时编译均为 `E2_DYNAMIC_DEPENDENCY_UNTRUSTED`；
- 解包预算固定为：container 压缩 bytes 最大 1 GiB、最多 100,000 members、最多 20,000 package 条目、单 archive 最大 256 MiB、全部 blob 最大 2 GiB、生成 consumer view 最多 200,000 entries/2 GiB logical file bytes、全部安装树最大 4 GiB、container 和任一 archive 的展开比都不得超过 100:1。manifest/member/archive/view/install tree 任一预算在写出越界 bytes 前失败；
- npm 从校验后的 canonical view 创建 invocation-scope copy-on-write cache root，并继续禁止 install/lifecycle script。Python 只消费预构建 wheel，禁止 sdist、source build、build isolation、build backend、setup.py/pyproject build hook 或本轮编译；`--no-cache` 禁止复用宿主 cache 或由 sdist 预先构建的缓存 wheel，不存在 Python build closure 或降级路径；
- Python setup 前的 lock bytes/digest、`uv pip sync` 返回后的 lock bytes/digest 与 suite `lockfileDigests` 必须三者相等；lock 路径在 setup 期间只读，任何变化返回 `E2_DEPENDENCY_MAPPING_INVALID` 且不进入 test。checkout 中即使存在 `pyproject.toml [build-system]`、uv workspace、当前 project 或 editable metadata，也不得读取为依赖输入、不得安装当前 project、不得改变 exact repository closure；
- setup 前后网络 namespace 维持无 egress，并记录 socket-deny counter。runner 同时把 npm/Python registry URL 固定到不可达 sentinel；counter 非零或工具尝试解析/连接 sentinel 时，无论退出码为何都返回 `E2_LIVE_REGISTRY_ACCESS`。

#### 6.3.1 `python-find-links-view/v1`

每个 Python package 的 `consumer` 都必须完整符合上例，生成算法固定如下：

1. `originalFilename` 必须是 NFC ASCII basename，只含 `[A-Za-z0-9._+-]`，不得含 path separator；`extension` 必须且只能是 `.whl`，`archiveKind` 必须是 `python-wheel`。任何其他 extension/kind 在读取 archive 内容前即 `E2_PYTHON_SDIST_FORBIDDEN`。
2. wheel filename 按 PEP 427 唯一解析 distribution/version/build/python/ABI/platform tags；`normalizedName` 使用 PEP 503 规则；`version` 是锁文件精确版本；`pythonTags` 是展开后按 bytewise 排序、无重复的完整 tag 集合。wheel 内唯一 `.dist-info/METADATA` 的 name/version 和唯一 `.dist-info/WHEEL` 的 tag 集合必须相等。
3. `hash` 必须是 `sha256:<64hex>`，逐字段等于 `archiveDigest` 和锁文件记录的 artifact hash；对应 `lockMappings` 还必须重复并匹配 `originalFilename/hash`。同 basename 多 blob、同 `(normalizedName,version,pythonTags)` 多 filename/digest、`.whl` filename/tag 与 metadata 矛盾或 lock 未固定 filename/hash 均为 `E2_DEPENDENCY_VIEW_INVALID`。
4. builder 创建唯一根 `view/python-find-links/`，按 `originalFilename` bytewise 升序把每个 blob 原始 bytes 复制为顶层 regular file；不得生成 HTML、子目录、alias、symlink 或额外 metadata。相同 filename 即使 digest 相同也视为重复并拒绝。
5. runner 用自身的 requirements parser 将唯一冻结 lock 的每个生效 requirement/hash 与 `lockMappings`、wheel filename/tag/hash 和当前平台逐项闭合；view 中每个 wheel 恰被一个生效 requirement 使用。缺 wheel、hash 不匹配、tag 不兼容、额外 wheel 或同 requirement 多候选均在 uv spawn 前返回 `E2_DEPENDENCY_VIEW_INVALID`。
6. `@dependency-repository/python` 只展开为该 `view/python-find-links` 绝对目录。固定 uv `0.11.15` 的唯一安装命令形态是 `uv pip sync --python=<venv-path>/bin/python <requirements-lock-path> --offline --no-index --find-links=@dependency-repository/python --no-build --require-hashes --no-sources --config-file=@toolchain/uv-empty-config --no-python-downloads --no-cache`；`--require-hashes` 与上述 lock 子集共同强制全部 exact pin 带 SHA-256，`--no-build` 拒绝构建 sdist，`--no-cache` 排除缓存旁路，显式空 config 排除 checkout 配置发现。
7. `uv venv` 与 `uv pip sync` 各自位于独立 containment leaf。受信 supervisor 从 leader exec 到 empty proof 记录不可伪造的 bounded exec census；两个 Python setup invocation 都必须是包内 uv leader，任何 child exec 只能命中 toolchain manifest 对固定 uv 版本登记的包内解释器探针身份/规范 argv，且 `buildBackendProcessCount=0`。未登记 child exec、project/workspace/editable 安装或 build backend 迹象均返回 `E2_PYTHON_SDIST_FORBIDDEN`；network deny counter 仍必须为 0。

#### 6.3.2 `npm-cacache-view/v1`

本 schema 固定对应 npm `11.19.0`、cacache `20.0.4`、`content-v2/index-v5`；其他版本必须使用新的 view schema。每个 npm package 的 `consumer` 和 package-lock v3 node 必须闭合：

- `originalFilename` 必须等于 canonical `resolved` URL 的最后一个 path segment，以 `.tgz` 结束；`resolved` 只允许 canonical HTTPS URL：小写 scheme/host、无 userinfo/default port/query/fragment、percent escape 大写、path 无 `.`/`..`。package-lock 的 `name/version/resolved/integrity` 必须逐字段等于 package/consumer/mapping；
- `integrity` 必须是唯一、规范、带 padding 的 `sha512-<RFC4648-base64>` SRI；解码后恰为 64 bytes，重新计算 archive bytes SHA-512 必须相等，archive bytes 的 SHA-256 仍必须等于 `archiveDigest`；
- `cacheKey` 不是自由输入，而是 builder 从 lock URL 重新派生并与 manifest 比较的唯一值 `make-fetch-happen:request-cache:<canonical-resolved>`；不得添加 `pacote:`、packument、tag 或其他 index key。缺项、额外 key、重复 key 或 lock 字段不一致均为 `E2_DEPENDENCY_VIEW_INVALID`。

builder 在 `view/npm-cache-root/_cacache` 下生成以下唯一结构：

1. 将 SRI digest 转为 lowercase hex `H`，把 archive bytes 写入 `_cacache/content-v2/sha512/<H[0:2]>/<H[2:4]>/<H[4:]>`；相同 SRI 可共享一个 content file，但 bytes 必须相同。
2. 对每个 cache key `K`，计算 lowercase hex `B = SHA-256(UTF-8(K))`，bucket 路径为 `_cacache/index-v5/<B[0:2]>/<B[2:4]>/<B[4:]>`。entry 对象字段固定为 `key/integrity/time/size/metadata`，`time` 固定为 JSON number `0`、`size` 等于 archive byte length；metadata 固定为 `{time:0,url:<resolved>,reqHeaders:{},resHeaders:{cache-control:"public, max-age=31557600, immutable",content-length:<十进制字符串>,content-type:"application/octet-stream"},options:{compress:true}}`。
3. entry 用 RFC 8785 规范 JSON 编码为 bytes `J`；line 为 `lowerhex(SHA-1(J)) + HTAB + J`。这里 SHA-1 只复现 cacache v5 的行损坏校验，不是信任摘要；archive 仍由 SHA-256 + SHA-512 SRI 绑定。同 bucket 的 lines 按完整 key bytewise 排序；bucket bytes 恰为 `LF + join(lines, LF)`，末尾无额外 LF。directory/file mode 和 view tree digest 仍按共同规则计算。
4. `@dependency-repository/npm` 展开为 `view/npm-cache-root` 的 invocation-scope copy-on-write 根，而不是 `_cacache` 本身；因此 npm `--cache` 正好消费其 `_cacache` 子目录。spawn 前 runner 重新解析全部 content/index：path hash、line hash、JSON schema、key、SRI、size、metadata、blob bytes 和 `consumerView.treeDigest` 必须同时闭合；index/content 缺失、额外文件或任一 byte 被改写均为 `E2_DEPENDENCY_VIEW_INVALID`。
5. 固定 pacote closure 必须用 package-lock 的 exact `resolved/integrity` 先按 SRI 命中 content；index-v5 同时提供同一 resolved URL 的闭合 request-cache 映射，不允许用 index 指向不同 content。真实 `npm ci --ignore-scripts --offline --replace-registry-host=never --cache=@dependency-repository/npm` fixture 必须全程零网络；`replace-registry-host=never` 保证 cache key 与锁文件 URL 不被 registry 配置改写。COW upperdir 可接收 npm 自身 cache bookkeeping，但不可改变已验证 lower view，且不会进入 receipt 或后续信任输入。

### 6.4 `trusted-e2-receipt/v2`

receipt 是审计对象，不是缓存真相。它至少包含：

- `schema`、`suiteId`、`profileId`、`subjectRef`；
- `suitePolicyDigest`、`toolchainRef`、`toolchainManifestDigest`，以及工具链 `securityReview.reviewedAt/advisorySnapshot.sha256/policyDigest`；
- 精确 `executionImageDigest`，以及实测 host/guest kernel numeric tuple、kernel ABI build digest、loader/libc/library bytes/build identity 和满足的 capability set；
- lockfile、`dependencyRepositoryRef`、repository manifest、每个 package archive、生成 consumer view tree、完整安装树与每个 derived entrypoint 的实际摘要；Python receipt 还保存 requirements lock 的 before/after digest（两者必须相同）；不保存 lock 原文、cache index 原文或宿主/COW 路径；
- 每个 invocation 的 registry ID、规范 policy argv、exit code、只含预期身份的 `selectedTestIds`；Python setup invocation 额外保存 exec-census proof digest、实际 `childExecCount`、`buildBackendProcessCount: 0`、声明 writable roots、write-set proof digest 与 `outsideAllowedRootWriteCount: 0`；
- containment backend/profile，以及每个 invocation 的 `teardown: "verified-empty"` 证明摘要；不保存宿主 realm 路径或原始 ID；
- 已脱敏后截断的 stdout/stderr、是否截断及安全字节数；
- parser kind、report digest、report 大小和闭合 counts；
- 总时长和观察时间。

receipt 和失败 `VerificationResult.facts` 都不得包含任意未预期 node ID；receipt 还不得包含原始 machine report、原始 stdout/stderr、完整环境、宿主绝对路径、私钥/agent socket、Provider key、Cookie、认证头或主机工具位置。

阶段 A 校验 receipt 的不可变绑定和规范结构；阶段 B 重新取得当前事实。时长和安全摘要输出属于审计信息，不要求跨两次重放逐字相等；subject、suite policy、toolchain、lockfiles、选择闭合和成功语义必须相等。

## 7. 阶段 A：无副作用 artifact 预检

阶段 A 是一次验证操作的全局 barrier，顺序固定：

1. 一次性冻结并摘要 contract、catalog、registry、profile、revocation 和 trust 输入；后续选择只能读取这组不可变 snapshot；
2. 从 profile 构造完整 requirement key 集合：`(profileId, subjectRef, itemId, verifierId)`；同一 verifier 被多个 item 使用时仍保留多个 requirement key，但可共享最终选中的 pointer；
3. 对每个 requirement 构造完整 candidate set：snapshot catalog 中 `profileId/verifierId/subjectRef` 全部相等的六字段 pointer；不得按登记时间、ID 排序或 artifact 是否看起来有效来预选；
4. 用同一冻结 revocation snapshot 派生 active set：pointer ID 或 `artifactRef` 被撤销的 candidate 只保留审计身份，不进入 active set；
5. 每个 requirement 的 active set 必须恰有一个 pointer。零个返回 `E2_POINTER_MISSING`；多于一个返回 `E2_POINTER_AMBIGUOUS`，即使多个 pointer 指向同一 artifact 也不自动去重；
6. 对全部唯一 selected pointer 严格解析六字段并核对 profile/verifier/subject；
7. 从 `artifactRef` 派生唯一 CAS 路径；拒绝 symlink、非普通文件和路径逃逸；
8. 使用有界稳定读取校验对象大小和 SHA-256；
9. 拒绝重复 JSON key、非 UTF-8、非规范 JSON、未知/缺失字段；
10. 核对 `trusted-e2-receipt/v2`、suite policy digest、toolchainRef、subject 和仅预期测试身份；
11. 对本次 selected pointer 全部完成预检后，立刻重新摘要 catalog/profile/revocation/registry/contract。与步骤 1 不同则返回 `ACCEPTANCE_STATE_CHANGED`；只有相等才打开阶段 B gate。

阶段 A 允许内存分配和只读文件描述符；禁止：

- 启动子进程或执行 Git；
- 创建临时 checkout、venv、node_modules、锁文件或新 CAS 对象；
- 修改访问时间以外的验收数据（实现应使用不会产生业务可见写入的读取方式）；
- 在某个 pointer 通过后提前重放，等待后续 pointer 再失败；
- 在多个 active pointer 中尝试 artifact，直到找到可用的一个。

阶段 A 失败只返回稳定阻塞码和最小路径类别，不回显 artifact 内容。多个匹配、旧 pointer 未撤销、最后一个 selected artifact 损坏或 gate 前并发追加时，`spawnCount=0`、`checkoutCount=0`、`casWrites=0`，catalog/contract 字节不变。阶段 B 开始后的并发变化仍由最终快照复核阻止状态写入，但不能倒推为“未执行过重放”。

为使 append-only catalog 可恢复歧义，v2 revocation schema 必须支持 typed `pointerId` 与 `artifactRef` 撤销；这只决定 E2 candidate 是否 active，不修改旧 pointer，也不改变 E1/E3/E4 的验证语义。新增 pointer 前必须先由治理提交撤销被取代的旧 pointer，并在下一冻结快照生效；同一操作中“边追加边撤销”不能绕过 gate。

## 8. 阶段 B：可信重放

### 8.1 工具链验证与展开

runner 先以流式 SHA-256 校验整个 package，再读取有界 manifest，并在同一临时根安全展开。每个条目按 manifest 校验类型、大小、权限和摘要；展开目录在校验完成前不可执行。校验后 toolchain、dependency view、lockfile 与 checkout 默认只读；每个 setup 仅以独立 mount 开放 suite 声明的安装根（Task 11 为 `backend/.venv` 或 `frontend/node_modules`）、package-manager invocation 临时根/COW cache，test 仅开放 report 临时根。父目录或相邻 checkout path 不因子挂载而获得写权限。

entrypoint 直接解析为包内绝对 argv prefix。版本输出是 manifest 的附加一致性检查，不是工具身份根；身份根是 package digest + 完整 file manifest。版本不符仍阻塞。

### 8.2 Git 固定主体与有界元数据

Git 可执行文件及 helper 必须来自工具链包。源仓库安全检查和 checkout 创建不得调用宿主 Git，也不得让工具链 Git 直接打开未经净化的原始 `.git`。runner 先用自有有界读取器检查原始布局，再在 containment 内构造只含允许对象的 sanitized bare view；工具链 Git 只接触该 view。

#### 8.2.1 固定隔离环境

Git 调用统一使用以下环境/参数；suite、仓库和宿主都不能覆盖：

- `HOME`、`XDG_CONFIG_HOME` 指向 containment 内只读空目录；`GIT_CONFIG_NOSYSTEM=1`、`GIT_CONFIG_GLOBAL=/dev/null`、`GIT_ATTR_NOSYSTEM=1`；
- 清除 `GIT_DIR`、`GIT_WORK_TREE`、`GIT_COMMON_DIR`、`GIT_OBJECT_DIRECTORY`、`GIT_ALTERNATE_OBJECT_DIRECTORIES`、`GIT_REPLACE_REF_BASE`、`GIT_SSH`、`GIT_SSH_COMMAND`、`SSH_AUTH_SOCK`、`GIT_ASKPASS` 和全部 `GIT_CONFIG_*` 注入变量；
- 固定 `GIT_OPTIONAL_LOCKS=0`、`GIT_TERMINAL_PROMPT=0`、`GIT_DISCOVERY_ACROSS_FILESYSTEM=0`、`GIT_NO_REPLACE_OBJECTS=1`、`LC_ALL=C`；
- 每次命令显式使用 `--no-replace-objects`、`-c core.hooksPath=<受信空目录>`、`-c core.autocrlf=false`；`PATH` 只含工具链 entrypoint/helper 目录；
- 禁止网络协议和 credential/helper 调用；E2 从本地固定对象闭包工作，不 fetch、不 submodule update。

#### 8.2.2 config、attributes 与外部对象源

runner 用自有 parser 有界读取原始 `config` 和 `config.worktree`，先拒绝 NUL、无效 UTF-8、重复 section/key、续行、畸形 quoted subsection 和 include 展开，再把 ASCII 大小写不敏感的规范键分为以下三类。原始 config 绝不交给 Git，任何原始值都不复制进 sanitized view。

**结构键：允许存在并验证，但仍由 runner 重建而非复制。**

```text
core.repositoryformatversion
core.filemode
core.bare
core.logallrefupdates
core.ignorecase
core.precomposeunicode
extensions.worktreeconfig
```

`core.repositoryformatversion` 必须恰为 `0`；其余六键只能是 Git 规范布尔值。

**惰性元数据键：允许存在，但只做语法/大小检查并丢弃。** 键名中的 `<name>` 必须是 1–255 个 NFC 字符，不含控制字符、`[`、`]`、NUL 或换行；值最大 64 KiB，runner 不将 URL/path 解引用，不把值写入日志或 receipt：

```text
remote.<name>.url
remote.<name>.pushurl
remote.<name>.fetch
remote.<name>.tagopt
remote.<name>.mirror
remote.<name>.prune
remote.<name>.prunetags
remote.<name>.skipdefaultupdate
branch.<name>.remote
branch.<name>.merge
branch.<name>.mergeoptions
branch.<name>.rebase
branch.<name>.pushremote
branch.<name>.description
branch.<name>.vscode-merge-base
codex.localenvironmentconfigpath
```

这组键只解释“仓库通常携带的 UI/协作元数据”；它们不能影响固定对象 closure 或 checkout。普通 `remote.origin.url/fetch`、`branch.main.remote/merge/vscode-merge-base` 和 `codex.localEnvironmentConfigPath` 因而不会阻塞，但 sanitized view 中仍不存在这些键。

**危险执行/重定向键：存在即 `GIT_CONFIG_UNSAFE`。** 至少包括 `include.*`、`includeIf.*`、`core.hooksPath`、`core.attributesFile`、`core.excludesFile`、`core.fsmonitor`、`core.sshCommand`、`core.alternateRefsCommand`、`filter.*`、`credential.*`、`url.*`、`http.*`、`protocol.*`、`submodule.*`、`diff.*`、`merge.*`（结构键 `branch.<name>.merge` 除外）、`difftool.*`、`mergetool.*`、`alias.*`、`safe.*`、`remote.<name>.promisor`、`remote.<name>.partialclonefilter`、`remote.<name>.proxy`、`remote.<name>.uploadpack`、`remote.<name>.receivepack`、`extensions.partialClone` 和 `extensions.objectFormat`。未命中结构键或惰性键的未知键也 fail closed；不能因“不打算使用”就把未知键归入惰性类。

以下对象存在即拒绝，不以“看起来为空”放行：

- `objects/info/alternates`、`objects/info/http-alternates`、环境 alternate/object directory；
- `refs/replace/**`、`info/grafts`、`shallow`、任何 `.promisor`、promisor/partial clone config；
- 任一真实 hook basename、symlink 或特殊文件；只允许有界普通 `*.sample` 且从不复制到 sanitized view。`info/attributes` 与外部 attributes 文件一律拒绝；
- subject commit 树内任意 `.gitattributes` 若声明 `filter`、`working-tree-encoding` 或 `ident`；
- symlinked `.git`、gitdir/common-dir/object/refs/config/pack 组件或任何越过受信 repository 边界的指针。

#### 8.2.3 refs 与 object layout

- `.git` gitdir 指针只允许一行 `gitdir: <path>`；`commondir` 只允许一个规范相对/绝对路径，解析后必须留在注册的 repository root；
- HEAD 可为一个 40 位 SHA-1 或指向允许 refs tree 的规范 symbolic ref；其他 loose ref 必须是普通文件、单行 40 位 SHA-1。ref path 需通过 Git refname 语法，拒绝 symlink、特殊文件和 `refs/replace`；
- `packed-refs` 只接受 Git v2 规范 header、40 位 object ID、合法 refname 和与前项相邻的 peeled 行；refs 必须 bytewise 升序，拒绝 packed 内重复、与 loose ref 重名、排序歧义、NUL、未知 header 和超过 100,000 条；
- objects tree 只允许 fan-out loose objects 与 `objects/pack/pack-<40hex>.pack/.idx` 成对普通文件；最多 1,024 个 pack。v2 拒绝 `.bitmap`、`.rev`、`.mtimes`、`.promisor`、multi-pack-index、commit-graph、cruft/keep sidecar 和未知 pack sidecar；
- 单个 pack 最大 4 GiB、单个 idx 最大 256 MiB、全部 object 文件最大 16 GiB；loose object 最大 256 MiB。超限为 `GIT_OBJECT_BUDGET_EXCEEDED`；布局、配对或类型不符为 `GIT_OBJECT_LAYOUT_UNSAFE`；
- 原始 `.idx` 只做有界类型/配对检查，不交给 Git；runner 把每个受控 pack stream 输入 sanitized view 内的 `git index-pack --strict` 重新生成 idx，随后以 `git fsck --strict` 核对目标 commit closure。loose object 复制后同样由该 sanitized view 校验；
- sanitized bare view 只写入固定 commit 所需的已验证 object closure、一个内部 ref 和由 runner 常量生成的最小受信 config；不复制原 hooks、attributes、index、logs、reflogs、remote/branch/codex 元数据或宿主 config。

所有 runner 直接读取的 Git 控制文件使用统一 `BoundedStableReader`：`O_NOFOLLOW`、普通文件检查、读取前后 stat 一致、精确上限、无部分接受。

固定上限如下：

| 对象 | 单文件上限 | 额外约束 |
| --- | ---: | --- |
| 工作树 `.git` gitdir 指针 | 4 KiB | 只允许单行 `gitdir: <path>`，解析后仍在受信 repository 边界 |
| `commondir`、单个 loose ref、HEAD | 4 KiB | 禁止 NUL、多行注入和路径逃逸 |
| `config`、`config.worktree` | 256 KiB | 只允许结构键或明确惰性键；危险/未知键拒绝，惰性值不复制 |
| `packed-refs` | 8 MiB | 最多 100,000 条 ref，逐行语法校验 |
| `shallow`、`info/grafts`、alternates | 8 MiB | 存在即拒绝；仍先做有界类型/大小检查 |
| runner 直接读取的全部 Git 元数据 | 16 MiB 聚合 | 超出即停止，不继续调用 Git |
| 单次 Git 命令 stdout/stderr | 各 64 KiB | 超限视为不可验证，不截断后继续解析 |

Git object pack 是对象数据流，不按上述 16 MiB“直接元数据”预算计算，但受本节独立 object budget、containment、超时和 no-symlink 约束。任何类别越界都在启动测试前阻塞；工具链 Git 不得消费被拒绝的原始输入。

### 8.3 Invocation 与不可逃逸 containment 生命周期

POSIX session/PGID 只用于方便发送信号，不能证明全部后代已清空：后代可 double-fork 后 `setsid()`/`setpgid()`，或关闭 stdio 后继续运行。v2 的权威边界是 `ReplayContainmentV1`：

```text
create_session(profile) -> session_realm
create_invocation(session_realm) -> invocation_leaf
spawn_into(invocation_leaf, absolute_argv, env) -> leader
terminate_all(invocation_leaf)
prove_empty(invocation_leaf) -> empty_proof
destroy(invocation_leaf)
destroy_session(session_realm) -> destroyed_proof
```

containment backend 必须满足：

- membership 由内核/虚拟机边界继承，非受信进程无法自行移出 invocation/session realm；
- 第一个非受信指令执行前已加入 realm，不存在“先 exec 再移动”的竞争窗；
- `setsid()`、`setpgid()`、double-fork、daemonize、关闭/转移 stdio 都不改变 membership；
- supervisor 能内核级终止 realm 内全部成员，并取得“成员数为 0 / realm 已销毁”的可验证证明；
- child 看不到可写的 containment 控制面，也没有移动自己或创建外部 realm 所需的 capability；
- child 位于隔离 user/mount/PID/network/IPC 环境，不能访问 systemd/launchd、Docker socket、SSH agent 或其他可代为在 realm 外启动进程的宿主服务；
- supervisor 从 leader 的 `execve/execveat` 到 leaf empty proof 捕获不可由 child 删除或伪造的 exec census，逐项绑定规范 executable file identity、parent relation 与 argv digest；单 leaf 最多 4,096 条，丢事件、观察器失效或超限均为 `E2_CONTAINMENT_UNAVAILABLE`，不能用结束时进程列表替代历史；
- supervisor 同期捕获带 write/create/truncate flag 的 `open/openat` 以及 `rename/link/symlink/unlink/mkdir` 等全部写相关 syscall 的已解析 mount identity、规范 path、operation 与结果，最多 200,000 条；runner 在 invocation 前后分别计算许可根外受保护 executor rootfs、checkout、toolchain、dependency 与 lock 树的完整 entry+bytes digest。任何许可根外写尝试、前后 digest 变化、事件丢失或预算超限都使 setup 以 `E2_SETUP_FAILED` 阻塞；只看进程退出码或最终目录列表不构成 write-set proof；
- backend 的类型、版本和 host/guest 兼容性由 registry 与 toolchain manifest 双重固定。

`containmentProfile` 不是自由字符串：静态 registry 必须固定 backend schema、host OS/arch、规范 `hostCompatibility` predicate、content-addressed supervisor binary/code digest、required kernel capabilities，并为 native 模式固定 `executorImageDigest`、为 VM 模式固定 `guestImageDigest` 与 guest rootfs digest。profile 的 predicate 规范 JSON digest 必须与 toolchain manifest 绑定；supervisor 通过绝对受信引用装配，不从 PATH 发现。

启动前 supervisor 必须产生规范 `PlatformMeasurementV1`：host/guest OS、arch、从 `uname.release` 派生的 numeric triplet、kernel ABI build digest、execution/guest/rootfs image digest、loader/libc path+bytes+build ID、每个系统库 identity 和 capability probe 结果。比较器只执行 6.2 节定义的 exact-field + numeric-tuple predicate，不调用发行版自带版本比较器，也不接受缺字段或未知 scheme。native backend 的 measurement 与 receipt 必须包含实测 host kernel ABI、固定 rootfs、loader/libc identity；VM backend 必须包含 host predicate 结果和精确 guest image/rootfs measurement。边界版本恰等于 `minInclusive` 接受、恰等于 `maxExclusive` 拒绝；发行版变体未命中 kernel ABI build allowlist、或同 SONAME 但 bytes/build ID 不同均返回 `E2_TOOLCHAIN_PLATFORM_MISMATCH`。

v1 允许两类 backend：

1. `linux-cgroup-v2/v1`：supervisor 创建 session cgroup 与每 invocation 独立 leaf，使用 `clone3(CLONE_INTO_CGROUP)` 在 exec 前原子加入；child 使用隔离 user/mount/PID/network/IPC namespace，不挂载可写 cgroupfs或宿主运行时 socket，且无 `CAP_SYS_ADMIN`。收尾使用 `cgroup.kill`，以 leaf `cgroup.events` 的 `populated 0` 加成功删除 leaf 作为 empty proof。仅 PGID/`killpg` 不计证明。
2. `vm-cgroup-v1`：整个 replay session 位于一次性 VM，guest 内仍使用上述 cgroup leaf；invocation 以 guest empty proof 收尾，session 最终以 VM 停止并销毁内存/临时磁盘作为第二层证明。macOS 等无原生不可逃逸 job primitive 的 host 只能注册此类后端，不能注册“native PGID”后端。

每个 setup/test invocation 严格执行：

1. 在 replay session 内创建新的 invocation leaf；
2. 通过 backend `spawn_into` 启动 manifest entrypoint 的绝对 argv prefix，runner 自身 `shell=False`；
3. 同时消费 stdout/stderr，并分别送入 `StreamingRedactor/v1`；
4. leader 成功、非零、超时、输出/report 失败或 EOF 异常后都调用 `terminate_all`，不等待后台后代自行退出或关闭管道；
5. `prove_empty` 成功并销毁 leaf 后，才解析/接受调用结果并进入下一 invocation；
6. 不能证明为空返回 `E2_CONTAINMENT_CLEANUP_FAILED`；backend 不可用或 capability 不足返回 `E2_CONTAINMENT_UNAVAILABLE`；两者都不得产生成功 receipt。

setup 仅对声明安装根的文件变更保留在 session 文件系统中；checkout 其余路径保持只读且前后 digest 相同，invocation realm 不复用。最后一个调用后还必须销毁 session realm；session 销毁失败同样使整轮阻塞。

### 8.4 `StreamingRedactor/v1` 与安全截断

stdout/stderr 各有独立、确定性的 `StreamingRedactor/v1`。同一 byte stream 无论以单块、每字节一块或任意 chunk 边界输入，必须得到相同安全输出/错误码。

状态机固定为：

1. **UTF8_PENDING：** 使用严格增量 UTF-8 decoder，只保留一个未完成 code point 所需的最多 3 个尾字节；非法序列立即 `OUTPUT_REDACTION_UNSAFE`，EOF 时残缺序列同样失败。
2. **RECORD_PENDING：** 解码字符累积到当前逻辑记录。LF 结束记录；CRLF 作为同一个结束符并规范为 LF；孤立 CR 是记录正文。没有终止换行的最后记录在 EOF 走同一 sanitize/flush 路径。原始记录（不含结束符）允许最大 64 KiB；在结束符前收到第 64 KiB+1 byte 才失败，不释放任何部分。
3. **PRIVATE_KEY_BLOCK：** 完整记录匹配 `-----BEGIN <TYPE> PRIVATE KEY-----` 后，只输出一次 `[REDACTED_PRIVATE_KEY]\n`，持续丢弃后续记录直到匹配对应 END；BEGIN/END 可跨输入 chunk 但不能跨逻辑记录。EOF 前未出现 END 仍安全地保持整块删除并完成；孤立 END 或嵌套 BEGIN 返回 `OUTPUT_REDACTION_UNSAFE`。
4. **JSON_VALUE_PENDING：** 完整记录出现 secret JSON key 与冒号、且冒号后只有 JSON whitespace 时，不释放该 fragment；后续记录只允许继续 whitespace，直到同一 JSON scalar 开始。string/number/literal 一旦开始，必须按下述 grammar 唯一闭合；string 内出现原始换行、结构值 `{`/`[`、无效 escape 或 EOF 未闭合均失败。pending fragment 与普通 record 共用 64 KiB 上限。
5. **SANITIZE_RECORD：** 对完整记录按下列固定顺序替换，再执行同一词汇的 residual scan。只有 residual scan 通过的规范记录可释放到安全流。
6. **EOF：** flush 增量 decoder；处理无换行尾记录；确认不处于歧义 JSON 状态；完成 residual scan。任何失败都丢弃本轮 receipt。

v1 先采用 RFC 5234 的 `ALPHA/DIGIT/HEXDIG/SP/HTAB`，再使用以下封闭词法；`OWS = *(SP / HTAB)`，所有位置以 Unicode scalar index 计，ASCII 大小写不敏感只应用于明确标注的 key/header，不做 locale case fold。

**共同引号与 escape。** `dq = DQUOTE *(dq-char / dq-escape) DQUOTE`，其中 `dq-char` 排除 `DQUOTE`、反斜杠、C0 control，`dq-escape = "\\" (DQUOTE / "\\" / "/" / "b" / "f" / "n" / "r" / "t" / ("u" 4HEXDIG))`；Unicode surrogate 必须成对。`sq = "'" *(sq-char / sq-escape) "'"`，其中 `sq-char` 排除单引号、反斜杠、C0 control，`sq-escape = "\\" ("'" / "\\")`。候选 secret token 中的其他 escape、尾随反斜杠或未闭合 quote 一律 `OUTPUT_REDACTION_UNSAFE`，不以 record 末端隐式闭合。

**assignment。** key 先 trim，再把一个或多个 `_`、`-`、SP 折叠为单个 SP 并转 ASCII lowercase；规范 key 集合为 `api key/client secret/password/access token/refresh token/token/secret/credential/ssh auth sock`，或原始环境名匹配 `(?i)^[A-Z_][A-Z0-9_]*(?:_KEY|_SECRET|_TOKEN|_PASSWORD|_CREDENTIAL)$`。grammar 为：

```text
assignment = boundary secret-key OWS (":" / "=") OWS assign-value
boundary   = BOF / SP / HTAB / ";" / "," / "(" / "{" / "["
assign-value = dq / sq / bare / empty
bare       = 1*(Unicode scalar except SP / HTAB / ";" / "," / C0 control)
empty      = zero chars only when next token is EOR / SP / HTAB / ";" / ","
```

quoted value 可含空格与已转义 quote/backslash；bare 在第一个 delimiter 前终止，因此 `TOKEN=abc PASSWORD=def` 是两个 assignment。每个 secret value（包括 empty）只替换为 `[REDACTED]`；key 与 delimiter 保留。secret key 后出现无法归入四种 value 的字符或 quoted value 未闭合时 fail closed。

**JSON member。** 仅接受 `DQUOTE json-secret-key DQUOTE OWS ":" json-ws json-scalar`；key string 按 RFC 8259 escape 恰好解码一次，所得 Unicode scalar 序列再按 assignment 的 key 规则规范化，非法 escape/surrogate 立即失败。`json-ws` 只允许 SP/HTAB 或逻辑记录分隔，`json-scalar` 仅为 RFC 8259 string、`-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?`、`true`、`false`、`null`。string 使用上面的 `dq` 规则；secret value 为 object/array、非法 number/literal、跨记录 string、incomplete escape 或 EOF 前没有 scalar 时 fail closed。完整 scalar 替换为 JSON string `"[REDACTED]"`。

**认证 header。** grammar 为 `BOF OWS header-name OWS ":" header-value EOR`，`header-name` 仅为 ASCII case-insensitive 的 `Authorization/Proxy-Authorization/Cookie/Set-Cookie`，`header-value` 是除 C0 control（HTAB 除外）外的任意 scalar，可为空；冒号后的完整值替换为 ` [REDACTED]`。header 名只在 record 起始位置生效，避免正文中的同名片段产生不同解释。

**URL userinfo。** `scheme = ALPHA *(ALPHA / DIGIT / "+" / "-" / ".")`；识别 `scheme "://" authority`，但 ASCII case-insensitive `file` scheme 明确排除并交给 path lexer。其他 scheme 的 authority 在首个 `/`、`?`、`#`、SP、HTAB 或 EOR 前结束，必须恰有一个 literal `@`，其前 userinfo 必须含至少一个 literal `:`，以第一个 colon 分隔 user/password；userinfo 字符只允许 RFC 3986 unreserved、sub-delims、colon 或 `pct-encoded = "%" 2HEXDIG`，hostport 必须非空。percent encoding 只校验一次、不解码为结构字符；残缺 `%`、第二个 literal `@`、空 hostport 或其他歧义均 fail closed。只把完整 userinfo 替换为 `[REDACTED]`，保留 `scheme://[REDACTED]@hostport`。

**path 与 agent socket。** path lexer 的入口、core 与诊断后缀唯一确定：

```text
path-left-boundary = BOF / SP / HTAB / ":" / "," / ";" / "=" / "(" / "[" / "{" / DQUOTE / "'"
path-prefix        = "/" / "./" / "../" / "~/" / ALPHA ":\\" / ALPHA ":/"
file-url-prefix    = ASCII-case-insensitive "file://" (empty-authority / "localhost") "/"
diagnostic-punct   = ":" / "," / ";" / "." / "!" / "?" / ")" / "]" / "}" / DQUOTE / "'"
```

`path-left-boundary` 是 token 前一个 scalar，BOF 视为虚拟边界。scanner 在边界后的当前位置按以下顺序唯一分派：

1. 当前字符为 quote 时只尝试 `quoted-path`，使用上述 `dq/sq` 恰好解码一次，解码值必须以 `path-prefix` 开始；这是表示含空格 path 的唯一形式。
2. 当前字符开始匹配 `file-url-prefix` 时进入 `file-url-path`。authority 只允许 empty 或 ASCII case-insensitive `localhost`，禁止 userinfo/query/fragment；其后的绝对 path 使用与 bare path 相同的 maximal-lexeme、suffix 和 sensitive-basename 算法。path 中 `%` 必须为合法 `%HH`，恰好解码一次用于分类；编码 `/`、`\\`、NUL 或 C0 control 立即 fail closed。
3. 否则只有匹配 `path-prefix` 才进入 `bare-path`：读取到 SP/HTAB、C0 control、`<`、`>`、`|` 或 EOR 为止得到 maximal lexeme `L`，再从右向左剥离由 `diagnostic-punct` 组成的最长 suffix `P`，余下非空 `pathCore`。

quote 只可作为 `P` 的一部分；出现在 bare `pathCore` 内即解析失败。SP/HTAB 确定地终止 bare path，lexer 不猜测相邻词是否属于同一路径。因为 URL-userinfo 规则排除 `file` 且扫描优先级不变，`https://user:pass@host/` 仍只走 URL userinfo，`file:///...` 只走 path lexer，两者不会双重匹配。

统一 `pathCore` 中 `/` 与 `\\` 为 separator 后，以下 path 属于 sensitive：组件 `.ssh` 后的 basename 匹配 `^id_[^/]+$`；任一组件 ASCII lowercase 后包含 `private`；basename 匹配 `^(?:agent\.[0-9]+|agent\.sock|ssh-agent\.sock)$`。bare sensitive path 输出 `[REDACTED_PATH] + P`；quoted sensitive path 保留原 quote pair，输出 `<quote>[REDACTED_PATH]<quote> + P`；file URL 保留原 scheme/authority/分隔斜杠：empty authority 输出 `file:///[REDACTED_PATH] + P`，`localhost` authority 输出 `file://localhost/[REDACTED_PATH] + P`。因此 `socket:/tmp/ssh-X/agent.123:`、`value,/tmp/agent.sock,`、`file:///tmp/ssh-agent.sock` 分别输出 `socket:[REDACTED_PATH]:`、`value,[REDACTED_PATH],`、`file:///[REDACTED_PATH]`；`"/tmp/My Dir/agent.sock":` 输出 `"[REDACTED_PATH]":`。`SSH_AUTH_SOCK` assignment 无论值是否匹配 basename 都按 assignment 脱敏；quoted path/value 的 quote/escape 在 record/EOF 不完整仍 fail closed。

**raw token 与 private-key block。** raw token 正则固定为 `gh[pousr]_[A-Za-z0-9]{20,}`、`github_pat_[A-Za-z0-9_]{20,}`、`sk-(proj-)?[A-Za-z0-9_-]{16,}`、`(?:AKIA|ASIA)[A-Z0-9]{16}`、`(?:sig|signature)=[A-Za-z0-9%+/_=-]{16,}`。private block 的整条记录 grammar 为 `-----BEGIN (?:[A-Z0-9]+(?: [A-Z0-9]+)* )?PRIVATE KEY(?: BLOCK)?-----`；END 必须使用逐 ASCII byte 相同的完整 interior label，因而同时覆盖 `PRIVATE KEY`、`RSA PRIVATE KEY`、`OPENSSH PRIVATE KEY` 和 `PGP PRIVATE KEY BLOCK`。

一条记录先判定完整 header/private-block，再对正文做 leftmost-longest scan；同起点优先级固定为 JSON member、assignment、URL userinfo、path、raw token。替换区间不得重叠；若同一字节可形成两个不同结束位置且无法由 longest 规则唯一决定、或任一候选词法未完整闭合，则 fail closed。主替换器与 residual scan 必须调用同一个版本化 `scanSensitiveLexemesV1(record)`，包括同一 `path-left-boundary/maximal-L/longest-P/pathCore` 算法；禁止 residual scan 另写简化 regex。替换后再次调用该函数，仍返回任何 candidate/ambiguity 即失败。

key/header/path/token 可跨输入 chunk，因为判定只发生在完整逻辑 token/record；多行私钥由独立 block state 覆盖。64 KiB 是最大 raw record/pending fragment/look-behind 边界，不存在更早释放的“安全前缀”。EOF 对未终止 record 走完全相同 grammar：它可以终止已经完整的 bare value/path/header value/raw token，但不能替 quote、escape、percent encoding 或 JSON scalar 猜测闭合。

只有已脱敏且 residual scan 通过的字节进入 32 KiB 持久化缓冲。缓冲满后继续消费、解码、状态转换、脱敏和 residual scan 直到真实 EOF，只是不再保存后续安全正文；最终追加固定 `...[truncated-after-redaction]` 标记。不得因摘要已满而关闭 pipe、停止验证或跳过 EOF flush。

截断逻辑永远看不到未脱敏原文。原始 chunk/record 只存在于当前进程内存，不写临时文件、CAS 或日志。

machine report 使用独立有界通道，不复用已截断的人类输出。report 原文只在内存中解析，不进入 receipt；持久化时仅保留报告摘要、闭合 counts 和预期测试身份。

### 8.5 Reporter 兼容规则

#### `pytest-json-v1`

目标版本为 `pytest 8.3.5` + `pytest-json-report 1.5.0`：

- 顶层 `exitcode`、`summary`、`tests` 必须存在且类型正确；
- `summary.total` 与 `summary.collected` 必须存在，为非负整数；`tests` 长度必须等于 `total`；
- `passed`、`failed`、`skipped` 等 outcome count 允许在值为 0 时省略；省略按 0 处理，出现时必须与 `tests[*].outcome` 精确一致；
- `deselected` 可省略；省略时必须 `collected == total`，出现时必须 `collected == total + deselected`；
- `collected >= total > 0`，不再错误要求每个零值 count 字段都出现，也不错误要求存在 deselection 时 `collected == len(tests)`；
- node ID 不得重复，实际集合必须恰好等于该 invocation 注册的 expected IDs；所有结果必须为 `passed`；
- report `exitcode` 必须等于真实 process exit code，且成功必须为 0。

#### `vitest-json-v1`

目标版本为 Vitest `3.0.8`：

- invocation selection 必须显式建模为 `file + exactName + expectedId`；
- argv 必须含规范化测试文件参数，以及 `-t ^<escaped exactName>$` 或语义等价的两个独立参数；loader 使用 ECMAScript RegExp 转义集合 `\\ ^ $ . * + ? ( ) [ ] { } | /` 逐字符转义并加首尾锚点，不接受 registry 自填 pattern；不得要求完整 expected ID 作为单个 argv 元素；
- 每个 Task 11 精确名称单独 invocation，禁止用一个正则同时代表多个必测项；
- `success`、suite/test counts、`testResults`、`assertionResults`、file identity、`fullName` 和 status 必须对报告中的全部 assertion（含 skipped）闭合；Vitest 3.0.8 的 `numPendingTests` 必须等于 skipped assertion 数；
- expected assertion 必须按 `file + fullName` 恰好出现一次且为 `passed`；同文件非 expected assertion 只允许 `skipped`，任何非 expected `passed/failed/pending/todo`、expected skipped/failed、重复 expected 或零 expected 都拒绝；
- suite status 必须与“一项 expected passed、零 failed、其余只 skipped”一致，process exit 为 0 且 report `success=true`；
- skipped assertion 的 fullName 只在内存中参与去重/count 闭合，不进入 receipt 或失败 facts；
- report file 在执行前必须不存在，执行后按有界稳定读取一次。默认上限 1 MiB。

### 8.6 其他有界对象

| 对象 | 上限 | 超限结果 |
| --- | ---: | --- |
| E2 receipt artifact | 1 MiB | `E2_ARTIFACT_TOO_LARGE` |
| toolchain manifest | 1 MiB | `E2_TOOLCHAIN_MANIFEST_TOO_LARGE` |
| pytest/Vitest machine report | 1 MiB | `REPORT_TOO_LARGE` |
| stdout/stderr 未决脱敏窗口 | 各 64 KiB | `OUTPUT_REDACTION_UNSAFE` |
| stdout/stderr receipt 安全摘要 | 每 invocation 合计 32 KiB | 脱敏后截断并标记 |

profile 可以降低这些上限，不能在同一 schema 版本中提高。

## 9. 工作流、顺序与原子性

### 9.1 `verify` / `validate`

- 对目标集合完成阶段 A barrier；失败立即返回，阶段 B 调用数必须为 0；
- 阶段 B 只生成内存 `VerificationResult`；不写 CAS；
- `validate` 继续只读，即使发现 artifact、toolchain 或重放失败也不修复文件。

### 9.2 `promote` / `reconcile`

- E2 阶段 A 对本轮所需全部 E2 pointer 一次性完成；
- 阶段 B 可以按确定顺序串行重放，但每个 invocation containment leaf 独立；
- 任一验证器阻塞/失败仍按既有逐矩阵项聚合，不允许用旧 receipt 成功替代本轮重放；
- 所有验证完成后重新核对初始快照摘要；变化则返回 `ACCEPTANCE_STATE_CHANGED`，不写 override；
- contract 投影仍由唯一 writer 以临时文件、flush、fsync、原子替换提交。

### 9.3 `collect`

- 先校验 suite/subject/toolchain 的只读策略闭包，再重放；
- 失败、超时、报告不闭合、脱敏不安全或 containment 无法证明为空时 CAS 新对象数必须为 0；
- 成功时先在内存生成规范 receipt，执行最终残留扫描，再通过 CAS 原子 `put-if-absent` 写一次；
- collect 不写 catalog/contract。register 仍是独立命令，只登记既有 artifact。

## 10. 稳定失败码与状态映射

| 错误码 | 含义 | 状态分类 |
| --- | --- | --- |
| `ARTIFACT_MISSING` | artifact 路径不存在 | 阻塞 |
| `DIGEST_MISMATCH` | artifact/toolchain 内容摘要与引用不符 | 阻塞 |
| `E2_ARTIFACT_TOO_LARGE` | receipt 超出固定上限 | 阻塞 |
| `E2_ARTIFACT_SCHEMA_INVALID` | receipt 非规范或与 suite/profile/subject 不绑定 | 阻塞 |
| `E2_POINTER_MISSING` | requirement 没有未撤销 matching pointer | 阻塞 |
| `E2_POINTER_AMBIGUOUS` | requirement 有多个未撤销 matching pointer | 阻塞 |
| `E2_TOOLCHAIN_UNAVAILABLE` | 内容寻址工具链包缺失或不可读 | 阻塞 |
| `E2_TOOLCHAIN_PLATFORM_MISMATCH` | exact image/rootfs/OS/arch/loader/libc/library identity 或规范 host predicate 不匹配 | 阻塞 |
| `E2_TOOLCHAIN_MANIFEST_TOO_LARGE` | 固定 manifest 超 1 MiB | 阻塞 |
| `E2_TOOLCHAIN_CLOSURE_INVALID` | manifest、CPython/npm/Git closure、entrypoint、file tree 或离线安全公告审核不完整/不合格 | 阻塞 |
| `E2_TOOLCHAIN_PACKAGE_TOO_LARGE` | 压缩包超 1 GiB | 阻塞 |
| `E2_TOOLCHAIN_ARCHIVE_INVALID` | 非规范 tar+zstd、重复/危险 member 或 manifest 位置错误 | 阻塞 |
| `E2_TOOLCHAIN_ENTRY_LIMIT` | payload member 超 100,000 | 阻塞 |
| `E2_TOOLCHAIN_EXPANSION_LIMIT` | 单文件、总展开量或压缩比超限 | 阻塞 |
| `E2_DYNAMIC_DEPENDENCY_UNTRUSTED` | 动态库、RPATH 或系统 ABI 不在 manifest 平台闭包 | 阻塞 |
| `E2_DEPENDENCY_REPOSITORY_INVALID` | 离线仓库容器/manifest/CAS 路径/预算/精确 package closure 无效或含额外包 | 阻塞 |
| `E2_DEPENDENCY_VIEW_INVALID` | wheel-only find-links 或 npm cacache 视图无法唯一生成、tree digest/filename/tag/hash/index/content/lock 绑定不符 | 阻塞 |
| `E2_PYTHON_SDIST_FORBIDDEN` | Python repository/lock 请求 sdist、source、project/workspace/editable，或出现任何 build backend exec / 未登记 child exec | 阻塞 |
| `E2_DEPENDENCY_MAPPING_INVALID` | lockfile node 到 package/archive/install tree 不是恰好一个精确映射，或 lock setup 前后不变性失败 | 阻塞 |
| `E2_DERIVED_ENTRYPOINT_INVALID` | derived entrypoint 路径、类型、摘要、解释器或安装树归属不符 | 阻塞 |
| `E2_LIVE_REGISTRY_ACCESS` | setup 尝试 DNS/socket/live npm/Python registry 访问 | 阻塞 |
| `E2_HOST_TOOL_FALLBACK` | 检测到主机 PATH/解释器/工具回退 | 阻塞 |
| `GIT_METADATA_TOO_LARGE` | Git 控制元数据超单文件或聚合预算 | 阻塞 |
| `GIT_METADATA_UNSAFE` | Git 元数据为 symlink、变型、越界或语法不安全 | 阻塞 |
| `GIT_CONFIG_UNSAFE` | config 出现危险/未知键或非法结构；明确惰性 remote/branch/codex 键不在此列 | 阻塞 |
| `GIT_EXTERNAL_OBJECT_SOURCE` | alternate/http-alternate/replace/graft/shallow/promisor | 阻塞 |
| `GIT_REF_UNSAFE` | loose/packed ref 类型、语法、重复或路径不安全 | 阻塞 |
| `GIT_OBJECT_LAYOUT_UNSAFE` | pack/index/loose object 配对、类型或 sidecar 不允许 | 阻塞 |
| `GIT_OBJECT_BUDGET_EXCEEDED` | pack/index/loose object 单项、数量或总量超预算 | 阻塞 |
| `REPORT_TOO_LARGE` | machine report 超 1 MiB | 阻塞 |
| `REPORT_SCHEMA_INVALID` | report schema/count/exit 自相矛盾 | 阻塞 |
| `TEST_SELECTION_MISSING` | 实际测试未精确覆盖 registry 预期集合 | 阻塞 |
| `E2_SETUP_FAILED` | 依赖/环境 setup 失败，未得到可信测试结论 | 阻塞 |
| `TEST_REPLAY_TIMEOUT` | invocation 超时且已取得 containment empty proof | 阻塞 |
| `E2_CONTAINMENT_UNAVAILABLE` | 注册 backend/能力不可用，无法建立不可逃逸 realm | 阻塞 |
| `E2_CONTAINMENT_CLEANUP_FAILED` | 无法取得 kernel/VM empty 或 destroyed proof | 阻塞 |
| `OUTPUT_REDACTION_UNSAFE` | 输出无法安全增量解码、脱敏或通过残留扫描 | 阻塞 |
| `TEST_REPLAY_FAILED` | 有效、闭合的受信报告证明测试失败 | 失败 |

`POINTER_INVALID`、`SUITE_NOT_ALLOWLISTED`、`SOURCE_REF_UNREACHABLE`、`ACCEPTANCE_STATE_CHANGED` 等既有错误继续有效。实现不得把上表阻塞类错误折叠成 `TEST_REPLAY_FAILED`，也不得因 process exit 非零而覆盖更早的报告/清理阻塞码。

## 11. Task 11 真实最小验收形态

E2 v2 在进入 Ticket 23 真实 profile 前，必须用与生产命令形态相同的最小 fixture 验收，而非手写“看起来像”report 的 JSON：

### 11.1 Python fixture

- 工具链包内 CPython 3.13.x、uv `0.11.15`、Git；`entrypoints.uv.version`、`runtimePolicies.uvSetup.uvVersion`、Python consumer-view `uvVersion` 和 0.11.15 探针 argv digest 全部逐字段相等；
- toolchain `securityReview` 冻结真实审核时间、官方 maintainer advisory/release snapshot digest，并明确 GHSA-4gg8-gxpx-9rph 对 `<0.11.15` 受影响、`0.11.15` 已修复。分别使用 uv `0.11.14`、缺 snapshot、篡改 reviewedAt/source/digest/range、或非空 applicable high-risk 集合，全部在 setup spawn 前返回 `E2_TOOLCHAIN_CLOSURE_INVALID`；fixture 禁止联网补查；
- runner 先执行包内 `uv venv backend/.venv --python=@toolchain/python --no-project --no-config --no-python-downloads --no-cache`；venv 不 seed pip/setuptools/wheel，解释器规范 symlink 回包内 CPython；
- `backend/requirements.lock` 以 exact pin + `--hash=sha256:<64hex>` 冻结 `pytest==8.3.5`、`pytest-json-report==1.5.0` 及其完整传递闭包；随后真实执行 `uv pip sync --python=backend/.venv/bin/python backend/requirements.lock --offline --no-index --find-links=@dependency-repository/python --no-build --require-hashes --no-sources --config-file=@toolchain/uv-empty-config --no-python-downloads --no-cache`，每个 wheel 的 filename/tag/hash、lock mapping、install tree 与 Python derived entrypoint 闭合；
- setup 前后 `backend/requirements.lock` bytes 与 digest 必须完全相同；receipt 的两个 digest 相等，两个 setup leaf 的 exec census 都是包内 uv leader、child 若存在只命中登记的包内 CPython 探针，且 `buildBackendProcessCount=0`；
- 合法包内 CPython interpreter probe 的 executable digest、规范 argv digest、cwd/env 与 `runtimePolicies.uvSetup` 完全匹配时 setup 通过；逐项篡改 executable identity/argv digest/cwd/env 构造未登记 probe，以及实际启动任意 PEP 517/build backend，均返回 `E2_PYTHON_SDIST_FORBIDDEN`、不进入 test；合法 probe 不计 build backend；
- 从同一 manifest/blob 集合在两个空临时根独立生成 find-links view，要求相对 path、每个 file bytes/mode 和 `consumerView.treeDigest` 完全相同；错误 `.whl` filename/tag/hash、同 filename 冲突、缺包或额外文件均返回 `E2_DEPENDENCY_VIEW_INVALID`；
- 分别放入 `.tar.gz`、`.zip`、`archiveKind=python-sdist`、source tree、editable/direct path 和要求 build backend 的 lock node，全部在任一 uv spawn 前返回 `E2_PYTHON_SDIST_FORBIDDEN`；删除所需 wheel或把已有 hash 改为不匹配 wheel 返回 `E2_DEPENDENCY_VIEW_INVALID`，移除 hash 返回 `E2_DEPENDENCY_MAPPING_INVALID`，都发生在 uv spawn 前；合法 wheel-only flat view 必须由真实 uv 离线安装成功；
- checkout 同时放置带可观察 marker 的 `pyproject.toml [build-system]`、uv workspace 与当前 project/editable 声明；完整 setup/test 仍不得创建 marker、不得安装当前 project，exec census 的 `buildBackendProcessCount` 仍为 0；
- 构造 hash/filename/tag/metadata/lock mapping 均合法、但 `entry_points.txt` 的 `console_scripts` 名含 `../../outside` 的恶意 wheel；真实 uv `0.11.15` 必须拒绝安装并使 setup 返回 `E2_SETUP_FAILED`。fixture 在 checkout 与 venv 许可根外放置 canary，要求 write-set proof 的 `outsideAllowedRootWriteCount=0`、全部受保护树前后 digest 相等、canary bytes 不变；部分 venv 安装结果随失败 session 丢弃；
- 运行至少一个通过测试，并实际产生 `.report.json`；
- 验证真实 summary 可以只有 `collected / passed / total`，零值 `failed/skipped` 省略仍通过；
- 再覆盖失败、零收集、deselected 和超限 report，确认 counts/exit/tests 闭合及稳定错误码。

### 11.2 Vitest fixture

- 工具链包内 Node、完整 npm CLI closure；项目 lock 固定 Vitest `3.0.8`；
- 从 `trusted-e2-dependency-repository/v1` npm container 用 `npm ci --ignore-scripts --offline --replace-registry-host=never --cache=@dependency-repository/npm` 安装固定依赖，并核对完整 install tree 与 `vitest.mjs` derived entrypoint；
- 从同一 manifest/blob 集合在两个空临时根独立生成 npm cache-root，要求 content/index 相对 path 与 bytes、mode、tree digest 完全相同；分别篡改 index line hash/JSON/key/SRI/size、content bytes/path、package-lock `resolved/integrity`，均须在 npm spawn 前返回 `E2_DEPENDENCY_VIEW_INVALID`；
- 同一个真实测试文件至少定义两个测试；每次采用 `测试文件参数 + -t + 转义并锚定的精确 fullName + JSON reporter` 分别执行；
- 对每次真实 Vitest 3.0.8 report，验证一个 expected assertion 为 passed、同文件另一个未匹配 assertion 为 skipped，`numTotalTests=2`、`numPassedTests=1`、`numPendingTests=1` 且 receipt 只保存 expected ID；
- 再构造非 expected passed/failed、expected skipped、未锚定/多测试正则、错误文件、零 expected、缺 npm 模块和主机 PATH 投毒，全部 fail closed；
- 两种生态都要分别构造额外 repository package/blob、同 lock node 两个 mapping、安装后替换 entrypoint、registry socket 探测，确认对应稳定码且不进入 test invocation。

### 11.3 Container ordering fixture

- toolchain 与 dependency container 都必须接受“manifest regular file 首项、保留虚拟父目录不作为 member、payload 显式父目录先行且按 path byte 排序”的最小合法 archive；
- 分别构造 manifest 之前加入保留父目录、payload 缺父目录、父目录重复、manifest 非首项和 payload 错序；toolchain 稳定返回 `E2_TOOLCHAIN_ARCHIVE_INVALID`，dependency container 返回 `E2_DEPENDENCY_REPOSITORY_INVALID`，且不写出可执行 entry/consumer view。

### 11.4 Containment 与流式脱敏 fixture

- containment helper 依次覆盖普通后台 child、double-fork、`setsid()`、`setpgid()`、关闭 stdio、持续持有 stdout/stderr，以及上述形态在 leader exit 0、非零和 timeout 下的组合；
- 每次都以 cgroup/VM membership 观察到成员并由 backend 终止，最终取得 empty/destroyed proof；只清空 PGID 而 realm 仍 populated 的 fixture 必须失败；
- 同一秘密语料以单 chunk、每个 byte 一个 chunk 和所有相邻 split point 重放，输出必须完全相同；覆盖 LF、CRLF、无尾换行、UTF-8 code point 跨块、私钥 block、多个 assignment、empty value、escaped quote/backslash、percent encoding、带空格 quoted path、agent socket、URL userinfo、JSON/header token、EOF flush，以及 64 KiB 恰好/64 KiB+1；
- 对 `agent.123`、`agent.sock`、`ssh-agent.sock` 分别追加 `:`、`,`、`;`、`.`、`!`、`?`、`)`、`]`、`}`、`"`、`'` 及多标点组合，单 chunk、每 byte chunk 和所有相邻 split point 都必须脱敏同一 path core、逐字节保留 suffix；residual scan 必须与主扫描得到同一 token 边界；
- `socket:/tmp/ssh-X/agent.123:`、`value,/tmp/agent.sock,`、`file:///tmp/ssh-agent.sock`、`file://localhost/tmp/agent.sock);` 必须在单 chunk、每 byte chunk 和所有相邻 split point 下得到规范脱敏输出；控制样例 `https://user:password@host/path` 仍只按 URL userinfo 脱敏，file URL 含 userinfo/query/fragment 或编码 separator 必须 fail closed；
- 对 trailing backslash、未闭合 quoted path/value、残缺 `%HH`、跨行 JSON string、secret object/array 和 EOF 未完成 scalar 均必须得到 `OUTPUT_REDACTION_UNSAFE`；同一语料仍逐字节和所有相邻 split point 重放；
- 安全摘要达到 32 KiB 后，fixture 继续发送尾部秘密和非法 UTF-8，runner 必须继续消费到 EOF 并拒绝，而不是因缓冲已满提前成功。

### 11.5 平台身份 fixture

- native fixture 固定 exact executor/rootfs image digest，并实测 kernel numeric triplet、kernel ABI build digest、loader/libc/system library bytes 与 build ID；VM fixture固定 exact guest image 与 guest rootfs digest；
- 分别覆盖 `minInclusive - 1 patch`、恰等于 `minInclusive`、`maxExclusive - 1 patch`、恰等于 `maxExclusive`，只接受闭区间下界和开区间上界内值；
- 用相同 numeric kernel version 的不同发行版 ABI build、以及相同 SONAME/版本字符串但不同 library bytes/build ID 运行，均须 `E2_TOOLCHAIN_PLATFORM_MISMATCH`；receipt 必须保存规范 measurement，不保存自由文本“兼容”判断。

### 11.6 Ticket 23 profile 映射

- 后端四个 pytest node ID 可在一个显式 invocation 中运行，或按 profile 分组；实际集合必须等于注册集合；
- 前端持久化五项、API 两项、结构两项均采用“一精确测试名一 invocation”；
- 所有 suite 固定同一个 Ticket 23 product commit、对应 lockfile 摘要和平台工具链 digest；
- 原生 macOS PGID profile 不可注册。macOS arm64 host 若执行正式 E2，必须使用已登记 `vm-cgroup-v1` 后端与匹配 guest OS/arm64/ABI 的工具链 package；不同执行平台使用不同 digest，不能以版本字符串相同替代。

## 12. 验收矩阵

| 场景 | 必须观察到的结果 |
| --- | --- |
| 同一秘密语料以单 chunk、每字节 chunk、每个 split point 输入；覆盖 LF/CRLF、UTF-8 跨块、无尾换行、私钥、多个/空 assignment、escaped quote/backslash、percent-encoded userinfo、带空格 quoted path、agent socket、URL/JSON/header token | 输出/错误码与 chunk 划分无关；receipt 无 token/path 片段 |
| `agent.123` / `agent.sock` / `ssh-agent.sock` 后接 `:`、`,`、`;`、右括号、quote 或多标点组合，并按每 byte/所有 split point 输入 | path core 始终替换为 `[REDACTED_PATH]`，诊断 suffix 逐字节保留；主扫描与 residual scan 边界完全相同 |
| `socket:/tmp/ssh-X/agent.123:`、`value,/tmp/agent.sock,`、`file:///tmp/ssh-agent.sock`，以及 HTTPS userinfo 控制样例 | 前三者按扩展左边界/file URL path 规范脱敏；HTTPS 仍只走 URL userinfo；所有 chunk 划分同输出且无双重匹配 |
| trailing escape、未闭合 quoted path/value、残缺 `%HH`、跨行 JSON string、secret object/array、EOF 未完成 scalar | `OUTPUT_REDACTION_UNSAFE`；EOF 不替解析器猜测闭合，所有 chunk 划分同码 |
| 安全输出已满 32 KiB，随后出现秘密或非法 UTF-8 | runner 仍消费到 EOF 并返回 `OUTPUT_REDACTION_UNSAFE`；不得提前成功 |
| 未决 record 恰好 64 KiB 与 64 KiB+1 | 前者仅在完整安全边界可 flush；后者稳定失败；两者都无截断前泄漏 |
| leader exit 0 后 child double-fork + `setsid()` + 关闭 stdio并持续运行 | child 仍属于 containment leaf，被 `terminate_all` 清除；以 kernel/VM empty proof 验证，不用 PGID 空替代 |
| leader 非零/超时，后代持有 stdout 或另建 PGID | 同样取得 empty proof；超时返回 `TEST_REPLAY_TIMEOUT`，无法证明清空则 `E2_CONTAINMENT_CLEANUP_FAILED` |
| 平台只有 PGID/killpg、没有注册 containment backend | `E2_CONTAINMENT_UNAVAILABLE`；该平台/profile 不可注册 |
| supervisor digest 或所需 kernel capability 与 containment profile 不符 | `E2_CONTAINMENT_UNAVAILABLE`；不创建 replay session |
| exact executor/guest/rootfs image digest、OS/arch、loader/libc/library bytes/build ID 不符 | `E2_TOOLCHAIN_PLATFORM_MISMATCH`；版本字符串或 SONAME 相同不能放行 |
| kernel 版本恰等于 min、恰低于 min、恰低于 max、恰等于 max | 仅接受 `min <= actual < max`；同版本不同发行版 ABI build ID 未列入 allowlist 仍拒绝；receipt 含实测 identity |
| pointer artifact 缺失、摘要错、schema 错或超限 | 阶段 B 进程启动数为 0；checkout/temp/CAS/catalog/contract 均无变化 |
| requirement 有两个未撤销 matching pointer，或新 pointer 已追加但旧 pointer 未撤销 | `E2_POINTER_AMBIGUOUS`；不按时间/ID/可用性挑选；spawn/checkout/CAS/catalog/contract 均零变化 |
| requirement 唯一 active pointer 是最后一个且 artifact 损坏 | 前面 requirement 也不重放，证明 barrier 是操作级 |
| 阶段 A 期间并发追加 catalog pointer | gate 前摘要复核返回 `ACCEPTANCE_STATE_CHANGED`；spawn/checkout/CAS/catalog/contract 均零变化（外部并发追加本身除外） |
| 主机 PATH 放置同名假 Git/uv/node/npm/python | 假工具执行标记不存在；runner 只执行 package entrypoint |
| toolchain 固定 uv `0.11.14`，或 `securityReview` 缺失/篡改/仍含适用的 high-risk 公告 | `E2_TOOLCHAIN_CLOSURE_INVALID`；setup/test spawn 均为 0；runtime 不联网替工具链补做公告检查 |
| toolchain/dependency archive 的 manifest 为首项、保留虚拟父目录不作为 member、payload 父目录显式先行且 bytewise 排序 | 两类最小合法 archive 都接受；从第一 payload 开始应用同一顺序规则 |
| archive 出现保留父目录 member、payload 缺/重复父目录、manifest 非首项、payload 错序、hardlink、PAX、越界 symlink、100,001 项、超 4 GiB 或压缩比超 100:1 | toolchain 返回 archive/entry/expansion 稳定码，dependency 返回 `E2_DEPENDENCY_REPOSITORY_INVALID`；不产生可执行 entry/view |
| npm 包只含 `npm-cli.js`，或缺 CPython stdlib/Git helper/动态库声明 | `E2_TOOLCHAIN_CLOSURE_INVALID` 或 `E2_DYNAMIC_DEPENDENCY_UNTRUSTED`，不回退宿主 |
| dependency container 含额外 package/blob、缺包、非固定 CAS path 或超预算 | `E2_DEPENDENCY_REPOSITORY_INVALID`；不进入 setup/test |
| 同一 manifest/blob 在两个空根生成 Python find-links 或 npm cacache view | 相对 path、file bytes/mode 与 `consumerView.treeDigest` 逐项相同；suite token 分别解析到 flat directory 与含 `_cacache` 的 cache root |
| Python wheel filename/tag/hash 歧义或冲突、所需 wheel 缺失；npm index line/key/SRI/content/path 被改、package-lock `resolved/integrity` 不符 | `E2_DEPENDENCY_VIEW_INVALID`；在 uv/npm spawn 前拒绝，缺包不得触发网络 |
| Python requirements lock 缺 hash/非 exact pin/映射不唯一、hash 与唯一 wheel 不符，或发生 setup 改写 | 前三类分别固定为 `E2_DEPENDENCY_MAPPING_INVALID`、`E2_DEPENDENCY_VIEW_INVALID`、`E2_DEPENDENCY_MAPPING_INVALID`；能在 setup 前发现的 uv spawn 数为 0，任何情况都不得进入 test |
| Python repository/lock 含 `.tar.gz`、`.zip`、`python-sdist`、source tree、editable/direct path 或 build backend 请求 | `E2_PYTHON_SDIST_FORBIDDEN`；uv spawn 数为 0，不创建 build environment；合法 wheel-only flat view 可真实离线安装 |
| checkout 含 `pyproject.toml [build-system]`、uv workspace、当前 project/editable 与会写 marker 的 backend | 仍只执行固定 `uv venv` + `uv pip sync`；lock bytes 不变，marker 不存在，当前 project 未安装，两个 setup leaf 的 `buildBackendProcessCount` 均为 0，其他 child 只可命中登记的包内 CPython 探针 |
| 合法登记的包内 CPython interpreter probe；未登记/identity 或 argv 不符的 probe；任意 build backend exec | 合法 probe 允许且不计 backend；后两类固定返回 `E2_PYTHON_SDIST_FORBIDDEN`，`buildBackendProcessCount` 不得被未登记 child 规则误报或漏报 |
| wheel 的 console/gui entry-point 名使用 `../../outside` 逃逸 scripts 目录 | uv `0.11.15` 拒绝并返回 `E2_SETUP_FAILED`；许可根外 write count 为 0，checkout/toolchain/dependency/lock/canary 前后 bytes digest 完全相同，不进入 test |
| 同一 lock node 有零个/多个 mapping、同 package 多 archive/install tree 摘要 | `E2_DEPENDENCY_MAPPING_INVALID`；不得挑一个“可安装”的候选 |
| setup 后 derived entrypoint 被替换、变型、移出安装树、摘要/解释器不符或经 `.bin`/PATH 发现 | `E2_DERIVED_ENTRYPOINT_INVALID`；test invocation 数为 0 |
| setup 试图 DNS/socket/live npm/Python registry，即使 cache 命中且退出 0 | `E2_LIVE_REGISTRY_ACCESS`；无网络回退，不接受 setup |
| pytest 1.5.0 真实通过报告省略零值字段 | 接受；`total == len(tests)`，`collected/deselected` 闭合 |
| pytest summary 与 tests、exitcode 或 deselected 矛盾 | `REPORT_SCHEMA_INVALID` 或 `TEST_SELECTION_MISSING` |
| Vitest 3.0.8 同文件两个测试，以锚定 `-t` 分别运行 | 每次接受 1 passed expected + 1 skipped non-expected；counts 全闭合；receipt 只含 expected ID |
| Vitest 非 expected assertion 为 passed/failed，或 registry 提供未锚定/未转义 regex | `TEST_SELECTION_MISSING` 或 policy 拒绝；skipped 之外的额外执行不可忽略 |
| `.git` 指针 4097 字节、`packed-refs` 超 8 MiB、聚合元数据超 16 MiB | `GIT_METADATA_TOO_LARGE`，不部分解析、不调用后续测试 |
| 普通仓库 config 含 `remote.origin.url/fetch`、`branch.main.remote/merge/vscode-merge-base`、`codex.localEnvironmentConfigPath` | 有界解析后成功构造 sanitized view；这些键和值均未复制、未解引用、未进入 receipt |
| Git config include/helper/filter/hook、alternate/http-alternate、replace/graft/shallow/promisor | 对应 `GIT_CONFIG_UNSAFE` 或 `GIT_EXTERNAL_OBJECT_SOURCE`；工具链 Git 从未打开被拒绝的原始 `.git` |
| refs 重复/越界/symlink，pack 缺 idx、未知 bitmap/rev/promisor sidecar，object budget 超限 | `GIT_REF_UNSAFE`、`GIT_OBJECT_LAYOUT_UNSAFE` 或 `GIT_OBJECT_BUDGET_EXCEEDED`；不构造 checkout |
| report 超 1 MiB 或读取中变化 | `REPORT_TOO_LARGE` 或 `REPORT_SCHEMA_INVALID`；不持久化 report 原文 |
| 成功 collect | 仅新增一个规范 v2 CAS receipt；catalog/contract 字节不变 |
| 成功 verify/promote/reconcile 的验证阶段 | CAS 对象数和字节不变；只允许最终 projection writer 按既有规则改 contract |
| Task 11 最小 Python + Vitest + dependency/view + container-ordering + platform + containment fixture | 使用真实工具、byte-identical offline consumer view、真实 derived entrypoint/reporter/skipped 行、精确平台 measurement、真实 cgroup/VM empty proof 全部通过后，才允许编写真实 profile 数据 |

## 13. 固定参考与三引擎职责一致性

- Toonflow 固定提交 `d86cce6b689916ab73f75bc4c1f42a2a7424516c`、MIT 许可证、上游文件映射继续由 E1 验证；E2 v2 不把工具链包或测试通过冒充源码/许可证据。
- Ticket 23 E2 仍绑定一个范围清晰的 40 位 product commit。工作区、未跟踪文件、旧日志和候选截图不进入 subject。
- `RemixEngine` 继续负责商业混剪编排，`CanvasEngine` 继续负责创作画布文档与直接操作，`GenerationService` 继续负责真实生成运行。E2 runner 只验证固定主体，不调用这些模块建立新的业务真相，也不写项目、素材、运行、审核或交付状态。
- E3 仍负责真实平台/Provider/媒体事实，E4 仍负责人工视觉、内容、权利与交付判断。E2 v2 只证明确定性自动化行为，不能替代 E3/E4。
- C01–C03 仍需冻结的 E1 + E2 + E4 组合；E2 v2 通过不等于 Ticket 23 resolved，更不等于整个产品或 138 项全部达标。

## 14. 迁移与取代关系

1. `trusted-e2-receipt/v1` 不自动迁移为 v2，也不能作为 v2 pointer 的 artifact。需要 v2 runner 在固定主体上重新 collect、register。
2. 旧 `E2SuiteDefinition` 的 `setupArgv/argv/toolVersions` 不足以表达 toolchain closure 和 selection；后续实施必须引入 v2 schema，不在 loader 中静默猜测转换。
3. Task 1–4 的既有成果按其独立审查结论保留；Task 5 实现和五轮修复保留审计历史，但其 E2 协议被本规格取代，状态为 `blocked / superseded`。
4. Task 6 尚未开始，不得在 v2 计划批准前继续；未来聚合器必须实现阶段 A 的操作级 barrier。
5. Task 10 未来应补充真实 reporter/依赖闭包准备；Task 11 必须使用 v2 suite、toolchainRef、selection 和 receipt schema。旧计划中的主机工具版本表不能直接复用。
6. v2 实施完成并独立审查通过前，所有依赖 E2 的正式晋升继续阻塞；不得沿用 Task 5 的 `230 passed` 测试数宣称协议达标。

## 15. 非目标

- 本规格不实现代码、不编写 v2 实施计划、不恢复 Task 5、不启动 Task 6。
- 不打包或提交真实生产私钥，不改变 E4 签名治理。
- 不重新设计 E1、E3、E4、catalog、矩阵状态或产品三引擎。
- 不承诺跨所有操作系统；只有具备不可逃逸 containment backend、对应平台/ABI toolchain package 和 empty/destroyed proof 的执行环境才能注册。POSIX PGID 不满足条件。
- 不把工具链 package 当成产品依赖 lockfile 的替代，也不把 package manager 成功当成测试成功。
- 不在 replay runtime 联网查询安全公告；每个未来实际工具链构建必须在定稿前记录新的审核时点与官方公告快照证据，再由内容摘要离线验证。
- 不把安全输出摘要、machine report 或 receipt 暴露为业务日志系统。

## 16. 规格自审结论

- 无未决占位决策；具体 package digest、产品 commit 和 reviewer 公钥属于未来真实输入，不是本规格缺口。
- 既有五项、随后六项、第二轮四项、第三轮三项、第四轮两项、第五轮一项与本轮两项独立审查问题均有不变量、失败码和验收行；PGID 不再冒充后代证明，Vitest skipped 行按真实 3.0.8 schema 闭合。
- 两阶段顺序、验证零写入、collect 后写入和 contract 原子投影之间无冲突。
- 工具链与依赖仓库统一规定 manifest 首项的唯一虚拟父目录例外；payload 缺/重复父目录和错序都有稳定拒绝与 fixture。
- dependency manifest 能纯函数重建 byte-identical 的 Python wheel-only flat find-links 与 npm content-v2/index-v5 view；Python 精确固定修复 GHSA-4gg8-gxpx-9rph 的 uv `0.11.15`，只用固定 `uv venv` + hash-required `uv pip sync --no-build --no-cache`，不解析 project/workspace/editable，lock 前后不变，合法 interpreter probe 可执行而未登记 child/build backend 阻塞；恶意 entry-point traversal 由修复版本拒绝且 write-set proof 证明许可根外 bytes 不变。
- exact executor/guest/rootfs identity 与规范 host predicate 分离；native measurement、版本边界、发行版 ABI 和 library build identity 均有唯一比较与 receipt 语义。
- `StreamingRedactor/v1` 的 chunk-independent 状态、assignment/JSON/header/URL/path/agent-socket 词法、扩展左边界、file URL path、诊断标点 suffix、共享 residual scanner、escape/EOF、record 上限和截断后继续消费规则明确；URL userinfo 优先级未改变。
- candidate set/唯一 active pointer/gate 前并发复核明确；Git config 区分结构键、明确惰性元数据与危险/未知键，普通 remote/branch 仓库可净化，ref/object allow/deny 与预算不变。
- pytest/Vitest 兼容规则与真实 Task 11 命令形态一致；同文件 skipped assertion 不再造成系统性误拒绝。
- 固定参考、138 项冻结合同、E1/E3/E4 和三引擎职责均未被扩大或替代。
- 本规格范围可由一个后续实施计划覆盖，但本次任务停在书面规格审查门。
