# 07: 多画布管理与导入导出

Status: in-progress
Blocked by: 05

## What to build

同一项目创建、命名、切换、保存和转移画布，素材引用可验证。

依据：../spec.md 和 ../parity-acceptance.md。继承源码复用优先、商用验收及已授权持续执行要求。完成本票不代表整个画布完成。

## Acceptance criteria

- [ ] 旧单画布文档兼容迁移
- [ ] 切换、删除恢复、保存冲突不丢草稿
- [ ] 导出导入涵盖节点、连线、视口和来源依赖

## 实施记录（进行中）

- 参考已核：`basketikun/infinite-canvas` 固定工作副本 `/tmp/basketikun-canvas-review`（提交 `dab19adc`，MIT）的 `web/src/stores/canvas/use-canvas-store.ts`、`web/src/pages/canvas/index.tsx`、`web/src/components/canvas/canvas-project-card.tsx`、`canvas-delete-projects-dialog.tsx`、`canvas-context-menu.tsx`、`canvas-create-menus.tsx` 及 `web/src/lib/canvas/canvas-export.ts`、`web/src/types/canvas-export.ts`。复用其稳定项目 ID、创建/重命名/软删除与导出清单的数据边界；适配为本项目项目内后端持久化，未迁入其浏览器存储 UI。
- 当前后端保持旧默认画布的 `creative-canvas/state.json` 与 `runs.json` 原路径；新增画布在 `creative-canvas/canvases/<canvasId>/`，目录索引只记录元数据。GET/PUT、运行轮询、取消和幂等均以 `canvasId` 隔离；软删除保留文件和素材来源引用，活跃运行返回冲突。
- 导入导出包尚未实现：后续契约应包含版本化 manifest、画布 metadata、节点/连线/视口、运行快照和素材来源依赖，二进制素材以受控路径清单表示；避免在本票后端管理改动中无验证地扩展传输格式。
