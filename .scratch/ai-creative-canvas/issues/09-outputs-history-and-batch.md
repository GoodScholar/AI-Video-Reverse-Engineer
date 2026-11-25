# 09: 多结果、历史与批量执行

Status: verified
Blocked by: 08

## What to build

批量运行生成链并逐张处理结果、选择当前输出与继续创作。

依据：../spec.md 和 ../parity-acceptance.md。继承源码复用优先、商用验收及已授权持续执行要求。完成本票不代表整个画布完成。

## Acceptance criteria

- [x] 每张结果有身份/状态/来源，逐张动作命中正确对象
- [x] 上游输出变化标记下游失效，历史快照不变
- [x] 取消、未知、晚到响应、重启及幂等重试均验证

## 2026-09-30 源码核查与最小实施方案

### 参考实现与可复用边界

已完整核查固定参考 `/tmp/basketikun-canvas-review` 的提交
`dab19adc0847e32e39b7fc8ff90cb392561fb826`（MIT）：

- `web/src/components/canvas/canvas-node-generation.ts`
- `web/src/components/canvas/canvas-node.tsx`
- `web/src/pages/canvas/project.tsx`（批量、主图、重试、删除）
- `web/src/types/canvas.ts`
- `web/src/services/image-storage.ts`
- `web/src/components/image-generation-pending.tsx`
- `web/src/components/canvas/nodes/builtin-nodes.tsx`
- `web/src/lib/canvas/canvas-generation-helpers.ts`

参考的 `CanvasNodeImage` 以单项 `id/status/errorDetails/content/storageKey` 表示候选结果；父节点保存 `metadata.images[]` 与 `primaryImageId`，并将当前主图同步到兼容展示字段。批量为每一张先建占位项、分别请求和落结果；任一成功即保留，失败只影响该项。重试替换同一候选项，删除仅移除该项并把主图移到剩余候选项。此交互和候选结果身份可适配。

参考的批量调度与素材存储在浏览器本地完成，没有本项目所需的 durable run、跨重启恢复、不可变快照或后端幂等保证。因此不能直接复制其调度/删除实现；本票以现有 `CreativeCanvasRun`、项目素材记录和 durable queue 为权威。

同时核查 Toonflow 固定源码的 `apps/server/src/utils/media/generation.ts` 与图片生成节点：服务返回媒体数组，但节点目前取首项。它不提供本票要求的候选管理或批量拓扑语义。

### 最小兼容结果模型

为 `CreativeCanvasRun` 新增可选 `outputItems`，保留已有 `outputAssetIds` 与节点 `outputAssetId`：

```json
{
  "resultId": "稳定的 run 内结果身份",
  "assetId": "项目素材 ID，可在 pending/failed 时省略",
  "mediaType": "image",
  "status": "pending | installed | failed | cancelled | unknown",
  "sourceRunId": "产生它的 run ID",
  "createdAt": "ISO-8601",
  "error": "失败说明（可选）"
}
```

- 旧 run 不迁移重写：读时把 `outputAssetIds[index]` 投影为 `"${runId}:legacy:${index}"`。旧接口和旧画布继续读 `outputAssetIds`。
- 节点新增可选 `currentOutputRef: { runId, resultId }`；旧 `outputAssetId` 继续兼容。两者同时存在时，服务端必须验证二者解析到同画布、同 run、同 asset，否则拒绝，避免静默指向另一结果。
- 下游运行快照的素材引用使用 `{ runId, resultId, assetId }`，不能只用候选序号或当前节点输出。下游记录的该三元组与当前选择不一致时标记为 stale；既有 run、素材和快照不被改写。

### 落库、恢复与候选动作

1. 每个供应商结果在安装前分配稳定 `resultId`；每一项素材安装独立原子记录。批量部分成功必须返回已安装项与失败项，不能报为整体成功。
2. 响应丢失、重启恢复或重试只可重做已知结果的素材安装；不得再次调用可能计费的生成服务。未知状态不自动重试。
3. 项目素材引用扫描需包含 `outputItems[].assetId`，并在选中当前输出、下游快照和 run 历史间保留可追溯关系。
4. “删除候选”先定义为取消该 run 中的可用/当前选择，并按引用关系决定何时允许删除项目素材；不得因删除当前候选而改写旧快照。删除当前项时，选择剩余的首个已安装项或清空 `currentOutputRef`。
5. 逐项重试使用同一 `resultId` 和原始不可变请求快照；仅允许明确 `failed` 项重新提交，不能把 `unknown` 当作失败。

### 批量拓扑执行

批量操作独立持久化为 `CanvasBatchRun`（或与 durable run 等价的记录），最小字段为：

```json
{
  "batchId": "稳定批次身份",
  "projectId": "项目身份",
  "canvasId": "画布身份",
  "documentRevision": 0,
  "steps": [
    {
      "nodeId": "节点身份",
      "dependsOn": ["上游 nodeId"],
      "idempotencyKey": "单步骤稳定键",
      "runId": "实际生成 run（可选）",
      "status": "queued | running | completed | failed | blocked | cancelled | unknown",
      "selectedOutputRef": { "runId": "", "resultId": "" },
      "error": "可选"
    }
  ]
}
```

- 提交前依据保存的文档验证节点存在、边可执行且拓扑无环；用 Kahn 顺序启动独立根节点。
- 依赖步骤仅在所有依赖步骤完成并已确定 `selectedOutputRef` 后启动；其输入快照解析该精确 `{runId,resultId,assetId}`。批次选择写在 batch step，不能通过悄悄改画布节点当前输出来改变历史语义。
- 失败、取消或未知状态使未开始的后继步骤变为 `blocked` 并记录原因。`unknown` 不自动恢复或重跑；用户对明确失败项发起新步骤键的重试。
- 每个实际生成仍沿用现有独立 run 幂等键、canvasId 隔离、取消和轮询语义。切换画布或离开页面不能使服务端 batch 记录丢失。

### 实施回归清单

- 旧 `outputAssetIds` run 的响应与节点继续可读；新旧当前输出字段交叉不一致被拒绝。
- 两项及以上结果部分安装成功时，重试/恢复不重复生成也不重复创建素材；每个逐项动作只命中其 `resultId`。
- 切换当前候选使精确下游输入变更并标记 stale，旧 run 快照保持不变。
- 多根节点并发、依赖链按拓扑启动；上游 failed/cancelled/unknown 时后继 blocked，跨 canvas 同 nodeId 不串。
- 队列取消、响应晚到、进程重启与项目素材引用保护覆盖 batch/run/result 三级身份。

## 2026-09-30 验收结论

- 新 run 以稳定 `outputItems/resultId` 管理多候选，保留旧 `outputAssetIds` 读取；选择、删除、定点重试、单候选取消及取消后晚到均按候选身份处理。显式选择取消后晚到的已安装候选仍可用于下游，自动采用和隐式回退始终跳过该候选。
- 批次持久化拓扑步骤、文档修订与快照；提交时冻结所有计划步骤的外部生成输入到 `externalOutputRefs[{edgeId,targetNodeId,sourceNodeId,runId,resultId,assetId}]`。派发、删除保护和素材引用共用该精确身份；无法解析的外部输入在提交时拒绝。
- 上游当前输出变化会以可撤销文档事务标记后继生成节点 `stale`，旧 run 快照不改写。失败、取消和 unknown 会阻断后继，unknown 不自动重交供应商。
- 后端根复跑画布、图片生成和 durable runs 共 114 项通过；前端全量 62 个文件、610 项通过；独立复核另跑前端定向 67 项和生产构建通过。`git diff --check` 通过。仅保留既有 Vite chunk-size 提示。
- 本票范围已验证；文本生成、视频/音频生成、项目包导入导出及后续票据仍未完成，不能据此宣称整个 AI 无限画布已全部对齐。真实图片供应商仍缺用户配置，协议测试不计作真实出图证据。
