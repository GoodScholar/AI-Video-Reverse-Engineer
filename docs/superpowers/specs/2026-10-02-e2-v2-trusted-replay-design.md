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
4. **每次调用独立进程组。** setup 和 test invocation 各自拥有独立进程组；父进程成功、失败或超时后都必须检查并终止残留后代，确认进程组消失后才可接受该调用。
5. **真实 reporter 合同。** `pytest-json-v1` 兼容 `pytest-json-report 1.5.0` 对零值 outcome 字段的省略，同时严格闭合 `collected / deselected / total / tests`；`vitest-json-v1` 支持 Task 11 的“测试文件参数 + `-t` 精确测试名”选择形态。
6. **不再信任主机工具。** Git、uv、Node、npm CLI 运行时闭包以及 profile 需要的 Python 运行时都必须来自内容寻址工具链包；不得发现、验证后复制或执行主机 `PATH` 中的同名程序。
7. **所有直接读取都有上限。** Git 控制元数据、machine report、receipt 和工具链 manifest 均有固定单文件及聚合字节上限；超限使用稳定阻塞码，不读取后再“尽量继续”。

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
| 父进程成功退出后，后台后代仍可存活 | 每次 invocation 独立进程组；成功、失败、超时均执行残留探测、终止和消失确认 |
| pytest 真实 summary 省略零值字段；Task 11 Vitest 选择不是完整 ID 单参数；npm 只是 JS launcher 而非单文件 | parser 采用真实 schema 兼容规则；selection 与 argv 分离建模；工具链 package 显式封闭 npm CLI 模块树 |
| artifact 指针无效时仍重放并写新 CAS | 全操作阶段 A 先预检全部 artifact；验证路径阶段 B 不写 CAS |
| `.git` 指针、`commondir`、`packed-refs` 等读取无上限 | 所有直接 Git 元数据读取走统一有界读取器，并受单文件/聚合预算约束 |

### 3.2 额外防护对象

协议还必须防止：

- 通过主机 `PATH`、shell、shebang `/usr/bin/env`、npm 的宿主模块树或可替换 symlink 注入未登记工具；
- 用错误 OS/architecture 的二进制包、缺文件工具链包或部分 npm 安装冒充受信闭包；
- stale report、超限 report、报告行重复、summary 自相矛盾、选择零测试或报告额外测试；
- 用测试名正则“可能匹配”代替对本轮实际测试身份的证明；
- 把任意 report node ID、失败输出中的秘密、完整环境或主机绝对路径写入 receipt；
- 进程在输出管道保持打开时让 runner 永久等待 EOF；
- 在 policy/catalog/contract 快照变化后提交旧验证结果。

### 3.3 信任边界外

本协议不抵抗同时拥有受保护 Git 历史、registry/profile、工具链包内容与其摘要发布权限的治理攻击者。OS 内核、已固定平台 ABI 和系统动态库属于平台信任根，但任何宿主可执行文件、包管理器、解释器、用户配置和 `PATH` 都不属于信任根。

## 4. 强制不变量

以下不变量没有“尽力而为”降级路径：

1. **A-before-B：** 一次验证操作选中的全部 E2 artifact 未全部通过阶段 A 前，不得进入任一 E2 阶段 B。
2. **验证零写入：** `E2SuiteVerifier.verify`、正式 `validate`、`promote` 的验证阶段和 `reconcile` 的验证阶段不得调用 `ContentAddressedStore.put`，也不得修改 catalog、contract 或 trust 输入。
3. **采集后写入：** `collect` 只可在成功重放、报告闭合、进程组归零、输出脱敏完成后写一次规范 receipt；失败运行不产生 receipt 对象。
4. **无宿主回退：** 工具链 entrypoint 缺失、摘要不符或平台不符时阻塞；不得搜索主机 `PATH`，不得用系统同名工具补位。
5. **每调用一进程组：** setup/test invocation 不共享进程组；一个 invocation 的后代不得跨越到下一个 invocation。
6. **先脱敏后截断：** 只有已从流式脱敏器释放的安全字节可以计入持久化输出上限。
7. **身份最小化：** receipt 的测试身份必须是 `expectedTestIds` 的有序子集；成功 receipt 必须恰好覆盖该 invocation 的预期集合，不能持久化任何额外报告 ID。
8. **报告闭合：** process exit、report exit/success、summary counts、tests rows 和 registry selection 必须互相一致。
9. **有界读取：** 任一受控文件在打开前、读取中或读取后超限、变型、变 inode 或变 mtime，均阻塞本轮验证。
10. **固定主体：** checkout、lockfile、package script 和测试文件均来自精确 `git://product@<40-sha>`；当前工作树不参与重放。

## 5. 总体架构与顺序

```text
pointer 集合 + registry/profile/contract 快照
                     │
                     ▼
         阶段 A：E2 artifact barrier
         ├─ 六字段 pointer/profile/verifier/subject 配对
         ├─ CAS 派生路径、类型、大小、稳定读取、SHA-256
         └─ receipt/v2 规范 JSON、schema、policy/toolchain 绑定
                     │ 全部通过
                     ▼
         阶段 B：TrustedReplaySession
         ├─ 验证并展开 trusted toolchain package
         ├─ 有界核验 Git 元数据并构造固定对象 checkout
         ├─ 核对 lockfiles / package scripts / dependency policy
         ├─ 每 invocation 独立进程组执行
         ├─ 流式脱敏 → 安全截断；有界解析 machine report
         └─ 清理所有后代并生成内存 VerificationResult
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
  "setupInvocations": [
    {
      "id": "npm-ci",
      "tool": "npm",
      "args": ["ci", "--ignore-scripts"]
    }
  ],
  "testInvocations": [
    {
      "id": "frontend-persistence-1",
      "tool": "npm",
      "args": ["test", "--", "--run", "src/CreativeCanvas.test.tsx", "-t", "<精确测试名>", "--reporter=json", "--outputFile=<受控相对路径>"],
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
  "lockfileDigests": {"frontend/package-lock.json": "sha256:<digest>"},
  "packageScriptDigests": {"frontend/package.json#scripts.test": "sha256:<digest>"}
}
```

规则：

- `tool` 是工具链 manifest 的逻辑 entrypoint，不是文件名搜索请求；
- setup/test 都是非空、不可变的 invocation 对象；v1 的裸 `setupArgv/argv` 不自动升级；
- parser 与 selection 分开。selection 描述 registry 预期身份，report 证明实际身份；二者不能通过“字符串在 argv 中出现”替代；
- 每个 Task 11 Vitest 精确测试名使用一个 invocation，文件参数和 `-t` 参数分别核对；
- Python suite 使用 `pytest-nodeids/v1`，argv 中逐一携带精确 node ID；
- report 路径必须位于隔离 checkout 的受控临时子目录，执行前确认不存在，执行后只读一次；
- `packageScriptDigests` 绑定 npm 间接执行的已提交脚本内容，防止 `npm test` 把未审查脚本变成第二命令入口。

### 6.2 `TrustedToolchainManifestV1`

工具链包位于 acceptance root 下的预配置只读内容仓库：

```text
trust/toolchains/sha256/<digest前2位>/<64位digest>
```

registry 只保存 `toolchainRef`。包整体 SHA-256 必须等于引用；缺包直接阻塞，不允许运行时联网取得替代工具包。

包内 manifest 至少包含：

```json
{
  "schema": "trusted-e2-toolchain/v1",
  "platform": {"os": "darwin", "arch": "arm64"},
  "entrypoints": {
    "git": {"argvPrefix": ["bin/git"], "version": "git version 2.53.0"},
    "uv": {"argvPrefix": ["bin/uv"], "version": "uv 0.11.14 (...)"},
    "node": {"argvPrefix": ["bin/node"], "version": "v24.20.0"},
    "npm": {"argvPrefix": ["bin/node", "lib/node_modules/npm/bin/npm-cli.js"], "version": "11.19.0"},
    "python": {"argvPrefix": ["python/bin/python3"], "version": "Python 3.13.x"}
  },
  "closures": {
    "npm": {"root": "lib/node_modules/npm", "treeDigest": "sha256:<digest>"}
  },
  "files": [
    {"path": "<relative path>", "type": "file", "size": 123, "mode": "0500", "sha256": "sha256:<digest>"}
  ]
}
```

强制要求：

- `platform.os/arch` 必须与运行平台精确相等；不同平台使用不同 package digest；
- `files` 是完整闭包清单，拒绝绝对路径、`..`、设备文件、FIFO、socket、越界 symlink 和未列出的额外条目；
- npm 必须包含完整 `npm-cli.js` 模块树，或在 manifest 中声明并审核一个真正自包含的 npm launcher；单独复制 `bin/npm` symlink 或 `npm-cli.js` 不构成闭包；
- shebang 不通过 `/usr/bin/env` 解析。脚本 entrypoint 展开为显式、已列入 manifest 的解释器前缀；npm 固定由包内 Node 启动；
- Task 11 的 Python profile 必须含包内 CPython。uv 创建的 venv 解释器只能指向该 CPython，不能指向宿主 Python；
- runner 不传入宿主 `PATH`。若某工具需要 PATH，只能构造为工具链包内 entrypoint/shim 目录和已由 lockfile 安装到 checkout 的受控项目 bin 目录；
- runner 自身始终 `shell=False`。npm 可以按其受信实现执行已提交的 package script，但该 script 文本必须由 `packageScriptDigests` 绑定，子工具只能从 lockfile 安装的项目 bin 目录解析，不能继承宿主 PATH；
- 产品依赖不等同于工具链闭包。它们仍由固定 subject 的 lockfile、package script 摘要和 setup policy 约束；若允许下载，profile 必须固定内容摘要。不得用未锁定的实时解析补齐依赖。

### 6.3 `trusted-e2-receipt/v2`

receipt 是审计对象，不是缓存真相。它至少包含：

- `schema`、`suiteId`、`profileId`、`subjectRef`；
- `suitePolicyDigest`、`toolchainRef`、`toolchainManifestDigest`；
- lockfile 与 package script 的实际摘要；
- 每个 invocation 的 registry ID、规范 policy argv、exit code、只含预期身份的 `selectedTestIds`；
- `processGroupCleanup: "verified-empty"`；
- 已脱敏后截断的 stdout/stderr、是否截断及安全字节数；
- parser kind、report digest、report 大小和闭合 counts；
- 总时长和观察时间。

receipt 和失败 `VerificationResult.facts` 都不得包含任意未预期 node ID；receipt 还不得包含原始 machine report、原始 stdout/stderr、完整环境、宿主绝对路径、私钥/agent socket、Provider key、Cookie、认证头或主机工具位置。

阶段 A 校验 receipt 的不可变绑定和规范结构；阶段 B 重新取得当前事实。时长和安全摘要输出属于审计信息，不要求跨两次重放逐字相等；subject、suite policy、toolchain、lockfiles、选择闭合和成功语义必须相等。

## 7. 阶段 A：无副作用 artifact 预检

阶段 A 是一次验证操作的全局 barrier，顺序固定：

1. 冻结并摘要 contract、catalog、registry、profile、revocation 和 trust 输入；
2. 解析全部目标 pointer，严格限制为六字段并核对 profile/verifier/subject；
3. 从 `artifactRef` 派生唯一 CAS 路径；拒绝 symlink、非普通文件和路径逃逸；
4. 使用有界稳定读取校验对象大小和 SHA-256；
5. 拒绝重复 JSON key、非 UTF-8、非规范 JSON、未知/缺失字段；
6. 核对 `trusted-e2-receipt/v2`、suite policy digest、toolchainRef、subject 和仅预期测试身份；
7. 对本次选中的全部 E2 pointer 完成同样校验。

阶段 A 允许内存分配和只读文件描述符；禁止：

- 启动子进程或执行 Git；
- 创建临时 checkout、venv、node_modules、锁文件或新 CAS 对象；
- 修改访问时间以外的验收数据（实现应使用不会产生业务可见写入的读取方式）；
- 在某个 pointer 通过后提前重放，等待后续 pointer 再失败。

阶段 A 失败只返回稳定阻塞码和最小路径类别，不回显 artifact 内容。

## 8. 阶段 B：可信重放

### 8.1 工具链验证与展开

runner 先以流式 SHA-256 校验整个 package，再读取有界 manifest，并在同一临时根安全展开。每个条目按 manifest 校验类型、大小、权限和摘要；展开目录在校验完成前不可执行。校验后目录改为只读，仅 checkout、受控 dependency 目录和 report 临时目录可写。

entrypoint 直接解析为包内绝对 argv prefix。版本输出是 manifest 的附加一致性检查，不是工具身份根；身份根是 package digest + 完整 file manifest。版本不符仍阻塞。

### 8.2 Git 固定主体与有界元数据

Git 可执行文件必须来自工具链包。源仓库安全检查和 checkout 创建不得调用宿主 Git。所有 runner 直接读取的 Git 控制文件使用统一 `BoundedStableReader`：`O_NOFOLLOW`、普通文件检查、读取前后 stat 一致、精确上限、无部分接受。

固定上限如下：

| 对象 | 单文件上限 | 额外约束 |
| --- | ---: | --- |
| 工作树 `.git` gitdir 指针 | 4 KiB | 只允许单行 `gitdir: <path>`，解析后仍在受信 repository 边界 |
| `commondir`、单个 loose ref、HEAD | 4 KiB | 禁止 NUL、多行注入和路径逃逸 |
| `config`、`config.worktree` | 256 KiB | 只允许 v2 白名单键 |
| `packed-refs` | 8 MiB | 最多 100,000 条 ref，逐行语法校验 |
| `shallow`、`info/grafts` | 8 MiB | 存在即按策略拒绝；也不得无界读取 |
| runner 直接读取的全部 Git 元数据 | 16 MiB 聚合 | 超出即停止，不继续调用 Git |
| 单次 Git 命令 stdout/stderr | 各 64 KiB | 超限视为不可验证，不截断后继续解析 |

Git object pack 是对象数据流，不按上述“直接元数据”预算读取；其生产/消费必须管道化、超时化，并受临时目录配额与进程组清理约束。

### 8.3 Invocation 与进程组生命周期

每个 setup/test invocation 严格执行：

1. 创建新 session/process group，记录不可变 PGID；
2. 启动 manifest entrypoint 的绝对 argv prefix，`shell=False`；
3. 同时读取 stdout/stderr，并分别送入跨分块脱敏器；
4. 一旦 leader 退出，不等待后台后代自行关闭管道：立即进入进程组收尾；
5. 对仍存在的进程组先发送 TERM，短暂等待，再发送 KILL；成功、非零和超时路径相同；
6. 反复探测直至进程组不存在，并回收 leader；不能证明归零则返回 `PROCESS_GROUP_CLEANUP_FAILED`；
7. 只有归零后才解析/接受该 invocation 的结果并进入下一调用。

runner 不复用进程组。若平台无法可靠提供上述语义，该平台的 toolchain profile 不可注册为受信 E2 平台。

### 8.4 跨分块流式脱敏与安全截断

stdout/stderr 各有独立 `StreamingRedactor`：

- 使用增量 UTF-8 解码；非法字节序列返回 `OUTPUT_REDACTION_UNSAFE`；
- 保留跨 chunk 的未决前缀和当前逻辑记录，不在 secret token/字段可能继续延伸时提前释放；
- secret key、认证头、Cookie、URL userinfo、Provider token、私钥块和 agent socket 模式必须跨 chunk 生效；
- 未决原始窗口最大 64 KiB。到达上限仍无法判定安全边界时立即失败、清理进程组且不持久化任何输出；
- 只有已脱敏且通过残留扫描的安全字节进入 32 KiB 持久化缓冲；达到上限后丢弃后续安全字节，最终追加固定 `...[truncated-after-redaction]` 标记；
- 截断逻辑永远看不到未脱敏原文。原始 chunk 只存在于当前进程内存，不写临时文件、CAS 或日志。

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
- argv 必须含规范化测试文件参数，以及 `-t <exactName>` 或语义等价的两个独立参数；不得要求完整 expected ID 作为单个 argv 元素；
- 每个 Task 11 精确名称单独 invocation，禁止用一个正则同时代表多个必测项；
- `success`、suite/test counts、`testResults`、`assertionResults`、file identity、`fullName` 和 status 必须闭合；
- report 中实际 assertion 集合必须恰好等于该 invocation 的 expected ID；失败、pending、todo、额外或零 assertion 均不能提供 E2 通过；
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
- 阶段 B 可以按确定顺序串行重放，但每个 invocation 进程组独立；
- 任一验证器阻塞/失败仍按既有逐矩阵项聚合，不允许用旧 receipt 成功替代本轮重放；
- 所有验证完成后重新核对初始快照摘要；变化则返回 `ACCEPTANCE_STATE_CHANGED`，不写 override；
- contract 投影仍由唯一 writer 以临时文件、flush、fsync、原子替换提交。

### 9.3 `collect`

- 先校验 suite/subject/toolchain 的只读策略闭包，再重放；
- 失败、超时、报告不闭合、脱敏不安全或进程组未归零时 CAS 新对象数必须为 0；
- 成功时先在内存生成规范 receipt，执行最终残留扫描，再通过 CAS 原子 `put-if-absent` 写一次；
- collect 不写 catalog/contract。register 仍是独立命令，只登记既有 artifact。

## 10. 稳定失败码与状态映射

| 错误码 | 含义 | 状态分类 |
| --- | --- | --- |
| `ARTIFACT_MISSING` | artifact 路径不存在 | 阻塞 |
| `DIGEST_MISMATCH` | artifact/toolchain 内容摘要与引用不符 | 阻塞 |
| `E2_ARTIFACT_TOO_LARGE` | receipt 超出固定上限 | 阻塞 |
| `E2_ARTIFACT_SCHEMA_INVALID` | receipt 非规范或与 suite/profile/subject 不绑定 | 阻塞 |
| `E2_TOOLCHAIN_UNAVAILABLE` | 内容寻址工具链包缺失或不可读 | 阻塞 |
| `E2_TOOLCHAIN_PLATFORM_MISMATCH` | OS/architecture 不匹配 | 阻塞 |
| `E2_TOOLCHAIN_CLOSURE_INVALID` | manifest、npm 模块树、entrypoint 或 file closure 不完整 | 阻塞 |
| `E2_HOST_TOOL_FALLBACK` | 检测到主机 PATH/解释器/工具回退 | 阻塞 |
| `GIT_METADATA_TOO_LARGE` | Git 控制元数据超单文件或聚合预算 | 阻塞 |
| `GIT_METADATA_UNSAFE` | Git 元数据为 symlink、变型、越界或语法不安全 | 阻塞 |
| `REPORT_TOO_LARGE` | machine report 超 1 MiB | 阻塞 |
| `REPORT_SCHEMA_INVALID` | report schema/count/exit 自相矛盾 | 阻塞 |
| `TEST_SELECTION_MISSING` | 实际测试未精确覆盖 registry 预期集合 | 阻塞 |
| `E2_SETUP_FAILED` | 依赖/环境 setup 失败，未得到可信测试结论 | 阻塞 |
| `TEST_REPLAY_TIMEOUT` | invocation 超时且已完成进程组收尾 | 阻塞 |
| `PROCESS_GROUP_CLEANUP_FAILED` | 无法证明 invocation 后代已归零 | 阻塞 |
| `OUTPUT_REDACTION_UNSAFE` | 输出无法安全增量解码、脱敏或通过残留扫描 | 阻塞 |
| `TEST_REPLAY_FAILED` | 有效、闭合的受信报告证明测试失败 | 失败 |

`POINTER_INVALID`、`SUITE_NOT_ALLOWLISTED`、`SOURCE_REF_UNREACHABLE`、`ACCEPTANCE_STATE_CHANGED` 等既有错误继续有效。实现不得把上表阻塞类错误折叠成 `TEST_REPLAY_FAILED`，也不得因 process exit 非零而覆盖更早的报告/清理阻塞码。

## 11. Task 11 真实最小验收形态

E2 v2 在进入 Ticket 23 真实 profile 前，必须用与生产命令形态相同的最小 fixture 验收，而非手写“看起来像”report 的 JSON：

### 11.1 Python fixture

- 工具链包内 CPython 3.13.x、uv、Git；
- 固定安装 `pytest==8.3.5` 与 `pytest-json-report==1.5.0`；
- 运行至少一个通过测试，并实际产生 `.report.json`；
- 验证真实 summary 可以只有 `collected / passed / total`，零值 `failed/skipped` 省略仍通过；
- 再覆盖失败、零收集、deselected 和超限 report，确认 counts/exit/tests 闭合及稳定错误码。

### 11.2 Vitest fixture

- 工具链包内 Node、完整 npm CLI closure；项目 lock 固定 Vitest `3.0.8`；
- 用 `npm ci --ignore-scripts` 或等价受信 setup 安装固定依赖；
- 每次执行采用 `测试文件参数 + -t + 精确测试名 + JSON reporter`；
- 至少验证两个不同精确名称分别运行，实际 assertion ID 与各自 expected ID 一一对应；
- 缺 `-t`、使用多测试正则、错误文件、零 assertion、额外 assertion、缺 npm 模块和主机 PATH 投毒全部 fail closed。

### 11.3 Ticket 23 profile 映射

- 后端四个 pytest node ID 可在一个显式 invocation 中运行，或按 profile 分组；实际集合必须等于注册集合；
- 前端持久化五项、API 两项、结构两项均采用“一精确测试名一 invocation”；
- 所有 suite 固定同一个 Ticket 23 product commit、对应 lockfile 摘要和平台工具链 digest；
- macOS arm64 与其他平台使用不同工具链 package/ref，不允许“版本相同”替代 digest/平台相同。

## 12. 验收矩阵

| 场景 | 必须观察到的结果 |
| --- | --- |
| token 跨两个、三个读取 chunk，且截断点位于 token 中间 | receipt 中无任何 token 片段；先完整脱敏再出现截断标记 |
| 单条未决输出超过 64 KiB 且无法证明安全边界 | `OUTPUT_REDACTION_UNSAFE`；进程组归零；无 CAS 新对象 |
| leader exit 0 后后台 child 保持运行/持有 stdout | child 被终止并确认进程组消失；无法确认则 `PROCESS_GROUP_CLEANUP_FAILED`，不得成功 |
| leader 非零或超时并留下 child | 同样完成收尾；超时返回 `TEST_REPLAY_TIMEOUT`，清理失败优先返回 cleanup code |
| pointer artifact 缺失、摘要错、schema 错或超限 | 阶段 B 进程启动数为 0；checkout/temp/CAS/catalog/contract 均无变化 |
| 多个 E2 pointer 中最后一个预检失败 | 前面通过预检的 suite 也不重放，证明 barrier 是操作级而非逐 pointer 交错 |
| 主机 PATH 放置同名假 Git/uv/node/npm/python | 假工具执行标记不存在；runner 只执行 package entrypoint |
| npm 包只含 `npm-cli.js` 而缺依赖模块树 | `E2_TOOLCHAIN_CLOSURE_INVALID`，不回退主机 npm |
| pytest 1.5.0 真实通过报告省略零值字段 | 接受；`total == len(tests)`，`collected/deselected` 闭合 |
| pytest summary 与 tests、exitcode 或 deselected 矛盾 | `REPORT_SCHEMA_INVALID` 或 `TEST_SELECTION_MISSING` |
| Vitest 使用文件参数和 `-t` 精确名 | 接受正确单项；无需完整 expected ID 作为单个 argv 参数 |
| Vitest 一个正则匹配多个预期项 | profile schema 或 selection 校验拒绝 |
| `.git` 指针 4097 字节、`packed-refs` 超 8 MiB、聚合元数据超 16 MiB | `GIT_METADATA_TOO_LARGE`，不部分解析、不调用后续测试 |
| report 超 1 MiB 或读取中变化 | `REPORT_TOO_LARGE` 或 `REPORT_SCHEMA_INVALID`；不持久化 report 原文 |
| 成功 collect | 仅新增一个规范 v2 CAS receipt；catalog/contract 字节不变 |
| 成功 verify/promote/reconcile 的验证阶段 | CAS 对象数和字节不变；只允许最终 projection writer 按既有规则改 contract |
| Task 11 最小 Python + Vitest fixture | 使用真实工具和真实 reporter 全部通过后，才允许编写真实 profile 数据 |

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
- 不承诺跨所有操作系统；只有具备可靠进程组语义并有对应 toolchain package 的平台才能注册。
- 不把工具链 package 当成产品依赖 lockfile 的替代，也不把 package manager 成功当成测试成功。
- 不把安全输出摘要、machine report 或 receipt 暴露为业务日志系统。

## 16. 规格自审结论

- 无未决占位决策；具体 package digest、产品 commit 和 reviewer 公钥属于未来真实输入，不是本规格缺口。
- 五项最终独立审查问题分别有不变量、失败码和验收行。
- 两阶段顺序、验证零写入、collect 后写入和 contract 原子投影之间无冲突。
- pytest/Vitest 兼容规则与 Task 11 的真实命令形态一致；npm CLI 闭包和主机 PATH 边界明确。
- 固定参考、138 项冻结合同、E1/E3/E4 和三引擎职责均未被扩大或替代。
- 本规格范围可由一个后续实施计划覆盖，但本次任务停在书面规格审查门。
