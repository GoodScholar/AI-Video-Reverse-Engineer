# 开源无限画布项目对视频前置工作台的借鉴

核对日期：2026-09-23。仅使用项目官方仓库、官方文档和本仓库代码；“借鉴”指交互与数据设计参考，不表示引入依赖或复制代码。许可证是仓库当前声明，实际复用前仍须检查目标文件和依赖的许可。

## 本项目的产品边界与已有能力

[ADR 0007](../adr/0007-preproduction-with-shot-steps.md) 已确定以“需求 → 素材库 → 镜头步骤 → 准备工具 → 交付检查”取代无限画布，最终 AI 视频生成在外部完成。[ADR 0008](../adr/0008-local-timeline-and-audio.md) 增加本地剪辑、音频和导出。当前 [前置工作台](../../frontend/src/PreproductionWorkspace.tsx) 已有镜头内有序节点、素材绑定、运行状态、过期标记、交付检查；[候选版本](../../frontend/src/ShotResultVersions.tsx)、[参考与结果对比](../../frontend/src/ShotResultComparison.tsx)、[素材引用位置](../../frontend/src/AssetManager.tsx) 也已存在。因此应补强这些既有路径，而不是重做画布、版本或素材库。

## 项目比较

| 项目 | 一手证据与可借鉴点 | 对本项目的判断 | 许可证、维护状态与约束 |
| --- | --- | --- | --- |
| [BeatDesign](https://github.com/BeatAPI/BeatDesign) | [产品状态文档](https://github.com/BeatAPI/BeatDesign/blob/main/docs/PRODUCT_PLAN_AND_STATUS.md) 把 Asset 定为媒体事实来源，Canvas Node 与 Timeline Clip 只是引用；生成血缘和视觉连线分开，时间线片段锁定具体 Asset。其状态文档列出尾帧续写、多视频加入时间线、Take、revision/CAS 与导出诊断。 | **最高参考价值**：借鉴“镜头结果 → 不可变素材 → 时间线片段”的显式来源和锁定关系，以及跨页面跳转；现有候选和剪辑已有基础，可优先改善来源展示与跨页追踪。无需采用画布。 | [Apache-2.0](https://github.com/BeatAPI/BeatDesign/blob/main/LICENSE)，另有[第三方与商标约束](https://github.com/BeatAPI/BeatDesign#license)。[发布页](https://github.com/BeatAPI/BeatDesign/releases) 已有 v0.2.x，仍属早期产品；其状态文档明确区分已实现、计划与已发布，应逐项核对，不能只凭 README 的功能列表。 |
| [React Flow / xyflow](https://github.com/xyflow/xyflow) | 官方 [README](https://github.com/xyflow/xyflow/blob/main/README.md) 展示 React 节点和连线；[保存与恢复](https://reactflow.dev/examples/interaction/save-and-restore) 需由应用保存节点、边与视口；[Sub Flows](https://reactflow.dev/learn/layouting/sub-flows) 可按父节点分组；[TypeScript 用法](https://reactflow.dev/learn/advanced-use/typescript) 支持自定义节点类型。 | 借鉴**输入依赖的可读性**：在单个镜头的步骤列表里显示输入来源、上游产物和下游受影响步骤，必要时做只读局部关系图。React Flow 本身是图编辑 UI，业务校验、执行、持久化仍由本项目现有服务端负责；完整拖拽图与 ADR 0007 不符。 | [MIT](https://github.com/xyflow/xyflow/blob/main/packages/react/package.json)，[发布页](https://github.com/xyflow/xyflow/releases) 持续有 React Flow 12 更新；Pro 示例与开源包须分开看。 |
| [ComfyUI](https://github.com/Comfy-Org/ComfyUI) | 官方 [README](https://github.com/Comfy-Org/ComfyUI/blob/master/README.md) 列出可复用子图、模板、局部重新执行、异步队列、JSON 工作流与本地 API；[OpenAPI](https://github.com/Comfy-Org/ComfyUI/blob/master/openapi.yaml) 定义工作流 JSON 与版本请求。 | 借鉴**步骤模板与依赖失效规则**，让重复的镜头准备流程可复用；交付时按明确的目标工具输出可验证的工作流或参数说明。当前通用 ZIP 不应宣称可被任意 ComfyUI 节点直接执行，也不应把最终生成引回本应用。 | [GPL-3.0](https://github.com/Comfy-Org/ComfyUI/blob/master/LICENSE)；[发布页](https://github.com/Comfy-Org/ComfyUI/releases) 持续更新。宜参考概念、文件格式和独立接口；复制/嵌入源码需单独核查 GPL 义务，第三方自定义节点和模型还各有依赖与许可。 |
| [basketikun/infinite-canvas](https://github.com/basketikun/infinite-canvas) | 官方 [README](https://github.com/basketikun/infinite-canvas#readme) 描述素材沉淀、生成记录、音视频生成与按上游节点继续创作；可参考“保留旧方案，再从选定结果继续”的用户路径。 | 本项目已有镜头候选版本，适合继续完善候选间对照、采用原因和输入来源；其画布布局与前台直连模型接口不适合作为本项目架构。这里只把 README 作为产品方向证据，未将每项宣称当成已验证实现。 | [MIT](https://github.com/basketikun/infinite-canvas/blob/main/LICENSE)；[发布页](https://github.com/basketikun/infinite-canvas/releases) 有近期版本，但 [README](https://github.com/basketikun/infinite-canvas#readme) 明确提示开发中且不保证历史数据兼容，不能直接照搬存储结构。 |
| [tldraw](https://github.com/tldraw/tldraw) | [Shapes](https://tldraw.dev/docs/shapes) 的 JSON 记录、[Assets](https://tldraw.dev/docs/assets) 的按 ID 引用与复用，以及 [README](https://github.com/tldraw/tldraw/blob/main/README.md) 的自定义组件可启发素材卡片和关系可视化。 | 素材 ID 复用已在本项目实现；若以后需要只读分镜注释，可参考局部交互。引入完整 SDK 会增加与当前列表工作流不相称的复杂度。 | **tldraw SDK 并非通常意义的开源许可**：[官方许可说明](https://tldraw.dev/community/license) 写明生产使用需有效的试用、商业或 hobby license key；部分独立包/示例为 MIT，不能据此推断整个 SDK 可自由用于生产。[发布页](https://github.com/tldraw/tldraw/releases) 持续更新。 |
| [AFFiNE / BlockSuite](https://github.com/toeverything/AFFiNE) | AFFiNE [README](https://github.com/toeverything/AFFiNE#readme) 将文档、画布和表格整合；其编辑器基础 [BlockSuite](https://github.com/toeverything/blocksuite#readme) 同时提供 PageEditor 与 EdgelessEditor，体现“同一内容有不同视图”。 | 可借鉴镜头表、详细镜头页、交付说明共享同一份镜头数据的原则；当前工作台已经有这些结构，不需要引入其编辑器框架或 CRDT。 | AFFiNE [根许可证](https://github.com/toeverything/AFFiNE/blob/canary/LICENSE) 为分目录授权，不能笼统称整个仓库 MIT；[发布页](https://github.com/toeverything/AFFiNE/releases) 有近期 canary 预发布。 |
| [Excalidraw](https://github.com/excalidraw/excalidraw) | [README](https://github.com/excalidraw/excalidraw#readme) 和[组件类型](https://github.com/excalidraw/excalidraw/blob/master/packages/excalidraw/types.ts) 表明它是可嵌入的手绘式白板，有初始场景和变更回调。 | 仅在用户需要**局部分镜草图或镜头标注**时考虑独立的辅助视图；自由白板无法替代可验证的镜头、素材与步骤数据。 | [MIT](https://github.com/excalidraw/excalidraw/blob/master/LICENSE)；[发布页](https://github.com/excalidraw/excalidraw/releases) 有维护记录。其白板场景模型与本项目视频语义不同。 |

## 建议优先级

1. **先做来源链的展示**：在既有镜头候选、步骤与剪辑片段之间增加“由哪个素材/步骤得到、被哪里使用”的可点击信息。BeatDesign 对 Asset、Canvas 引用、Clip 和血缘的区分提供了清晰样例；本项目已有不可变素材 ID 与引用检查，可增量扩展，不另建图编辑器。[BeatDesign 状态文档](https://github.com/BeatAPI/BeatDesign/blob/main/docs/PRODUCT_PLAN_AND_STATUS.md)
2. **再做镜头步骤模板**：把本项目现有“首帧 → 裁切/缩放 → 提示词”等有序准备步骤保存为可复用模板，实例化时仍检查镜头素材、输入顺序和过期状态。ComfyUI 的模板/子图说明了复用价值，但本项目仍使用有序步骤和现有执行器。[ComfyUI README](https://github.com/Comfy-Org/ComfyUI/blob/master/README.md)
3. **改善候选比较与交付说明**：当前已有参考与采用结果对比、候选切换；可研究候选 A/B 的同轴对照及“为何采用此候选”的简短记录，并将所选候选与具体素材 ID 写入交接说明。basketikun 的持续迭代路径和 BeatDesign 的 Take 锁定原则可作参考。[infinite-canvas README](https://github.com/basketikun/infinite-canvas#readme)、[BeatDesign 状态文档](https://github.com/BeatAPI/BeatDesign/blob/main/docs/PRODUCT_PLAN_AND_STATUS.md)

以上是研究建议，并非本轮实现范围。引入新 UI 库之前，应先验证这些改进是否可直接在现有镜头列表与检查页完成。
