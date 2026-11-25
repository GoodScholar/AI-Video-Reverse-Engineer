# 12: 裁切、切分与缩放锁定

Status: in-progress
Blocked by: 无

## What to build

直接操作当前图片进行裁切切分和锁定/自由缩放，保存为可追溯结果。

依据：../spec.md 和 ../parity-acceptance.md。继承源码复用优先、商用验收及已授权持续执行要求。完成本票不代表整个画布完成。

## Acceptance criteria

- [ ] 复制适配固定图片编辑器源码，不只建立入口
- [ ] 坐标/缩放/输出尺寸正确，原图保持
- [ ] 新素材可预览下载续创并可撤销，异常不改原输出

## Source reuse and module boundary (2026-09-30)

- 已核对并适配 `basketikun/infinite-canvas@dab19adc0847e32e39b7fc8ff90cb392561fb826`（MIT，版权声明保留在新模块注释中）的 `canvas-node-crop-dialog.tsx`、`canvas-node-split-dialog.tsx`、`use-image-editor-viewport.ts`、`canvas-image-data.ts` 及 `project.tsx` 调用链。React/Ant Design/i18n 壳替换为当前项目的独立 React 组件；裁切比例、拖拽切线、切分编辑撤销重做、缩放和平移与 Canvas 像素导出规则保留。
- `CreativeCanvasImageEditor` 只负责生成 PNG 和通过既有 `uploadPreproductionAsset` 安装新项目素材；上传的 `derivation` 记录包含 sourceAssetId、operation、params、operationId、index/count 与服务端 createdAt。后端核验同项目图片来源、操作参数和导出像素尺寸，并保护来源素材不被派生成果仍引用时删除。
- 尚未接入 `CreativeCanvas.tsx` / `CreativeCanvasNodes.tsx`：宿主须提供裁切/切分入口、逐件 `onAssetCreated` 回调，将新素材加入画布并登记撤销历史。参考项目的锁定/自由缩放是节点显示尺寸（`width`、`height`、`position`、`freeResize`），不是图片像素处理；须由宿主另行接入 NodeResizer 和文档持久化后验收。
