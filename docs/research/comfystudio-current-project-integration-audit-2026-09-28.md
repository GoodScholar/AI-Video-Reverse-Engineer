# ComfyStudio × 当前项目源码级融合审计

审计日期：2026-09-28。参考仓库 `XmYx/ComfyStudio` 固定在提交 [`46416846d44b544396e66d9c6f29fccc1b30971d`](https://github.com/XmYx/ComfyStudio/tree/46416846d44b544396e66d9c6f29fccc1b30971d)（本地克隆 `git rev-parse HEAD` 确认）。以下上游源码链接均指向这个提交；当前项目以审计时工作树为准，含未提交的画布与 API 变更，未修改这些文件。本报告是融合判定，不表示任何迁移已实施或运行验证已通过。

判定词只使用：**直接复用、迁移后适配、部分复用、只参考设计、重新实现、不引入**。本次没有满足“直接复用”的上游代码单元；特别是看似纯粹的 UI-format 转换器仍有 ComfyUI 语义缺口。

## 1. 参考项目核心

ComfyStudio 是 PyQt6 桌面镜头设计器：一个 `QMainWindow` 聚合镜头列表、工作流/参数 Dock、预览 Dock、镜头内工作流图 Dock、嵌入式 ComfyUI 浏览器 Dock；通过 ComfyUI API 提交工作流，再按镜头排列和 FFmpeg 导出。源码入口为 [`main.py`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/main.py)、[`core/mainwindow.py#L57`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/mainwindow.py#L57) 和 [`core/ui.py#L64`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/ui.py#L64)。

它的基本数据是 `Shot` 内嵌多个 `WorkflowAssignment`，每个 assignment 保存外部 workflow 文件路径、参数、版本和最近输出；镜头还包含内嵌连接图与媒体路径。见 [`cs_datastruts.py#L61`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/cs_datastruts.py#L61)、[`#L135`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/cs_datastruts.py#L135)。

## 2. 当前项目核心

当前项目顶层是**内容项目**，在其中分别管理参考复刻、制作镜头、项目素材、本地时间线、渲染运行和审核交付。最终 AI 视频生成位于外部工具；前置准备可在没有 ComfyUI 时使用。这是 [CONTEXT.md](../../CONTEXT.md)、[ADR 0007](../adr/0007-preproduction-with-shot-steps.md)、[ADR 0008](../adr/0008-local-timeline-and-audio.md)、[ADR 0009](../adr/0009-content-project-and-workflow-boundaries.md) 的明确边界。[ADR 0010](../adr/0010-shot-workflow-complementary-views.md) 规定列表、镜头关系图、分镜板、时间线来源定位共享同一个制作镜头身份，画布布局与内容修订分离。

实现上，[`PreproductionStore`](../../backend/app/preproduction.py) 保存项目内 `schemaVersion`/`revision`，[`ProjectAssets`](../../backend/app/project_assets.py) 统一托管素材，[`ShotProduction`](../../backend/app/shot_production.py) 拥有镜头候选和采用语义，[`TimelineStore`](../../backend/app/timeline.py) 与 [`timeline_api.py`](../../backend/app/timeline_api.py) 管理编辑/运行版本；[`durable_runs.py`](../../backend/app/durable_runs.py) 只拥有运行状态与冻结输入。前端 [`preproductionWorkspaceStore.ts`](../../frontend/src/preproductionWorkspaceStore.ts) 的 Zustand 状态投影同一实体到 [`SceneShotList.tsx`](../../frontend/src/SceneShotList.tsx) 和 [`WorkflowCanvas.tsx`](../../frontend/src/WorkflowCanvas.tsx)；[`TimelineEditor.tsx`](../../frontend/src/TimelineEditor.tsx) 是现有剪辑入口。

## 3. 定位差异

| 维度 | ComfyStudio | 当前项目 | 融合结论 |
|---|---|---|---|
| 首要对象 | `Shot` 和其内嵌 ComfyUI workflow 文件、渲染版本 | 内容项目内的制作镜头、稳定素材 ID、流程节点及候选 | 上游对象只能作为导入数据，不能成为第二套领域对象 |
| 生成边界 | 应用连接/启动 ComfyUI 并批量生成 | 前置工作台准备与导出；最终 AI 生成在外部 | 运行队列不能整体移植 |
| 编辑框架 | PyQt6/QGraphicsView/QWebEngine | React/Zustand/React Flow + FastAPI | 交互经验可借鉴，Qt Widget 不可直接嵌入 |
| 时间线 | `new_widget.py` 多轨 `TimelineClip` 持有 `Shot` 副本，镜头缩略图可设入/出点；FFmpeg 单独导出 | 项目素材 ID 的多轨时间线、独立修订与渲染运行 | 上游的嵌套 Shot 快照不能成为第二时间线真相 |

依据：上游 [`mainwindow.py#L33`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/mainwindow.py#L33) 实际导入 `new_widget.ShotManagerWidget`，其 [`new_widget.py#L119`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/new_widget.py#L119)、[`#L1278`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/new_widget.py#L1278) 定义时间线 Clip/保存；[`widgets.py`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/widgets.py) 存在另一套列表控件但不是该窗口导入路径。本地参见 [ADR 0010](../adr/0010-shot-workflow-complementary-views.md)、[`timeline.py`](../../backend/app/timeline.py)。

## 4. 模块审计

| 上游模块 | 源码事实 | 判定 |
|---|---|---|
| 窗口与 Dock | [`core/mainwindow.py`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/mainwindow.py)、[`core/ui.py`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/ui.py) 组合镜头、预览、参数和 WebEngine | 只参考设计 |
| Shot/Workflow 数据 | [`cs_datastruts.py`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/cs_datastruts.py) 内嵌路径、版本、图连接、媒体 | 迁移后适配，仅作读取/转换 DTO |
| 镜头图 | [`shot_node_editor.py#L398`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/shot_node_editor.py#L398) 用 QGraphicsScene 渲染 workflow assignment，检查类型和环 | 只参考设计 |
| ComfyUI 集成 | [`core/base.py#L242`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/base.py#L242) 读 JSON；[`core/comfyhandler.py#L631`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/comfyhandler.py#L631) 绑定、缓存并提交 | 转换器仅部分复用算法；提交运行时不引入 |
| 时间线/导出 | [`new_widget.py#L540`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/new_widget.py#L540) 多轨编辑、[`#L1357`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/new_widget.py#L1357) FFmpeg 时间线导出；[`plugins/exporter.py#L188`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/plugins/exporter.py#L188) 另有按镜头媒体导出 | 只参考设计，不移植数据/渲染器 |
| 插件、示例与安装 | [`core/ui.py#L1310`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/ui.py#L1310)、[`workflow_browser.py`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/workflow_browser.py)、[`comfy_installer.py`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/comfy_installer.py) | 不引入运行时；示例导入只参考设计 |

## 5. 业务流程

上游路径是选 ComfyUI 工作流文件 → 暴露 node input 为参数 → 赋给一个或多个镜头 → 镜头内图连接上游结果 → 按镜头/按 workflow 批量提交 → 保存文件路径和版本 → 将镜头结果按顺序导出。`onRenderAll` 实际固定使用 `per_workflow` 交错调度，而 `onRenderSelected` 在多镜头时可选两种方式。见 [`core/base.py#L284`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/base.py#L284)、[`core/comfyhandler.py#L339`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/comfyhandler.py#L339)、[`#L396`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/comfyhandler.py#L396)。

当前流程是保存制作镜头与步骤 → 导出交付包/候选工作流 → 外部工具生成 → 将真实视频作为项目素材关联到镜头候选 → 可导入本地时间线 → 预览/导出；批量混剪另有脚本、配音、预览、审核、交付的版本链。对应 [`preproduction_api.py`](../../backend/app/preproduction_api.py)、[`shot_production.py`](../../backend/app/shot_production.py)、[`timeline_api.py`](../../backend/app/timeline_api.py)、[`batch_production.py`](../../backend/app/batch_production.py)。

## 6. UI/UX

可取的上游体验：镜头缩略图悬停擦看、时间线的缩放/导航和刀片分割、版本下拉、流程节点连线的即时类型/环提示、工作流参数可见性。实际窗口导入的源码为 [`new_widget.py#L160`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/new_widget.py#L160)、[`#L540`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/new_widget.py#L540)、[`core/ui.py#L975`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/ui.py#L975)、[`shot_node_editor.py#L550`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/shot_node_editor.py#L550)。当前 [`SceneShotList.tsx`](../../frontend/src/SceneShotList.tsx) 已有场景分组、虚拟列表与镜头多选， [`WorkflowCanvas.tsx`](../../frontend/src/WorkflowCanvas.tsx) 已有 React Flow 视图和统一选择；应在这些视图加需求明确的交互，而不是复制 Qt Dock/ShotGraph。上游嵌入式 ComfyUI 编辑器通过 [`core/ui.py#L180`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/ui.py#L180) 注入脚本，并经本地 [`workflow_edit_server.py#L21`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/workflow_edit_server.py#L21) 写回，权限/路径模型不适合直接搬入 Web 工作台。

## 7. 技术栈与运行边界

上游 [`setup.py`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/setup.py) 依赖 PyQt6、QtWebEngine、`requests`、OpenCV、PyAV、Hugging Face Hub 等；`RenderWorker` 用 `QRunnable` 直接向 ComfyUI `/prompt` 提交并轮询，见 [`worker.py#L28`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/worker.py#L28)。当前是 FastAPI/React；[`comfyui_client.py`](../../backend/app/comfyui_client.py) 对 URL、上传/提交、未知结果、输出下载已有独立协议边界，[`durable_runs.py`](../../backend/app/durable_runs.py) 对本地/外部运行的恢复与冻结快照有契约。移植上游 Qt 运行时会重复已有服务能力和状态生命周期。

## 8. 数据与持久化

上游项目文件为 JSON：`shots` 数组和可选 `timeline`，`Shot.to_dict()` 把绝对或外部 workflow/媒体路径、assignment 的 `versions` 和镜头图一起序列化。更关键的是，时间线每个 `TimelineClip` 直接保存一份 `Shot.to_dict()`，恢复时又 `Shot.from_dict()` 成副本，剪辑与镜头因此可能分叉；当前项目不应复制这个关系。见 [`shot_manager.py#L286`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/shot_manager.py#L286)、[`new_widget.py#L1278`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/new_widget.py#L1278)、[`cs_datastruts.py#L90`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/cs_datastruts.py#L90)、[`#L215`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/cs_datastruts.py#L215)。固定树未见数据库 schema/迁移或 tests 目录；保存代码是普通 `json.dump`，不能假设并发版本控制。当前 [`preproduction.py`](../../backend/app/preproduction.py) 与 [`timeline.py`](../../backend/app/timeline.py) 使用项目分区 JSON、原子替换及修订；[`project_assets.py`](../../backend/app/project_assets.py) 使用稳定 ID 与受控文件路径。上游路径和数组索引不可成为本项目引用主键。

## 9. 当前项目优势

一份 `PreproductionShot.id` 权威身份和投影视图、独立 `CanvasLayout.layoutRevision`、项目素材 ID、结果候选关联快照、时间线修订以及运行输入指纹使源数据、视图与执行状态可分别演进。证据：[`preproductionWorkspaceStore.ts`](../../frontend/src/preproductionWorkspaceStore.ts)、[`preproduction_api.py`](../../backend/app/preproduction_api.py)、[`shot_production.py`](../../backend/app/shot_production.py)、[`durable_runs.py`](../../backend/app/durable_runs.py)。批量交付还要求当前版本预览且审核通过，见 [`batch_production.py`](../../backend/app/batch_production.py) 的 `_approved_export_context`。这些语义不能被上游 `lastOutput`/文件时间戳代替。

## 10. 参考项目优势

上游对一部分现代 ComfyUI UI 图到 API 图的离线转换、工作流本地 literal 参数提取、结果端口与节点输出端口、镜头内拓扑排序、跨镜头交错运行及 workflow 文件监视有集中实现。证据：[`comfy_workflow_convert.py`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/comfy_workflow_convert.py)、[`core/base.py#L284`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/base.py#L284)、[`core/comfyhandler.py#L469`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/comfyhandler.py#L469)、[`core/ui.py#L306`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/ui.py#L306)。这是可学习的能力清单，不是对可靠性或生产适配的承诺。

## 11. 功能映射

| 上游功能 | 当前已有承载 | 判定与原因 |
|---|---|---|
| Shot 图标列表/拖入时间线 | [`SceneShotList.tsx`](../../frontend/src/SceneShotList.tsx)、Zustand `order` | 只参考设计；上游拖动传 `Shot` 媒体路径快照，本地 `rank`/`sceneId` 和素材 ID 是权威 |
| Shot 内 workflow 图 | [`WorkflowCanvas.tsx`](../../frontend/src/WorkflowCanvas.tsx)、[`workflowCanvasProjection.ts`](../../frontend/src/workflowCanvasProjection.ts) | 只参考设计；当前图是制作镜头/素材/受控流程节点，不是 ComfyUI 节点图 |
| UI 图转 API 图 | [`workflow_templates.py`](../../backend/app/workflow_templates.py) 的 API 图边界、[`comfyui_client.py`](../../backend/app/comfyui_client.py) 的 `/prompt` | 部分复用格式识别与基础映射；优先用户提供官方 API-format 导出，UI-format 转换须重建校验 |
| 参数面板/依赖端口 | [`preproductionApi.ts`](../../frontend/src/preproductionApi.ts)、受控 `NodeKind` | 部分复用映射思想；任意模型参数不直接进入镜头规范模型 |
| 渲染全部/版本缓存 | [`durable_runs.py`](../../backend/app/durable_runs.py)、[`shot_production.py`](../../backend/app/shot_production.py) | 不引入执行器；只参考拓扑/交错调度 |
| 悬停擦看/入出点 | [`SceneShotList.tsx`](../../frontend/src/SceneShotList.tsx)、[`TimelineEditor.tsx`](../../frontend/src/TimelineEditor.tsx) | 只参考设计；入出点归时间线 clip |
| FFmpeg 导出 | [`timeline_render.py`](../../backend/app/timeline_render.py)、[`timeline_api.py`](../../backend/app/timeline_api.py) | 不引入上游渲染器 |
| 插件式 API 接收图片 | [`preproduction_api.py`](../../backend/app/preproduction_api.py) 受控上传 | 不引入无认证服务；需求出现时重新实现 |

## 12. 直接复用

**无。** 原本看似最接近的是上游 [`src/comfystudio/sdmodules/comfy_workflow_convert.py`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/comfy_workflow_convert.py)。它是标准库纯 Python，输出的 node-id → `class_type`/`inputs` 形状能被当前 [`ComfyUIClient.submit`](../../backend/app/comfyui_client.py) 放进 `/prompt`；但**形状相同不等于语义兼容**。`ui_workflow_to_api` 把 `mode=4` bypass 节点跳过，却在 `_link_source_map` 中保留它作为连线源，所以下游可能留下指向不存在节点的 `[node_id,slot]`；它还按名字/类型硬编码跳过 widget，无法完整复现官方前端 `widget.serialize` 与 `widget.options.serialize` 分别控制 workflow/prompt 序列化的规则，对 PrimitiveNode、reroute、subgraph 也没有完整处理。证据：上游 [`comfy_workflow_convert.py#L30`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/comfy_workflow_convert.py#L30) 和 [ComfyUI 官方 Widget Serialization 说明（固定提交）](https://github.com/Comfy-Org/ComfyUI_frontend/blob/787ed0b53e7785136a3637e18ce9ba163b91affd/docs/WIDGET_SERIALIZATION.md)。因此文件整体判为**迁移后适配**，基础 link/widget 算法仅**部分复用**，在修复并验证前不可声称直接复用。

## 13. 迁移后适配

上游 [`comfy_workflow_convert.py`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/comfy_workflow_convert.py) → 拟议 `backend/app/comfy_workflow_convert.py` **只能迁移后适配**：保留 UI/API 格式判别、list/dict links 映射及基础 widget 位置读取，重新实现 bypass/reroute/subgraph 的安全处理，按 ComfyUI 前端 prompt 序列化语义过滤 widget，用 `object_info` 或等价目标环境事实验证节点及输入，最后拒绝所有悬空引用。入口仍优先要求用户提供 ComfyUI 官方 API-format 导出；若无法可靠转换某种 UI 图，应拒绝而不是猜测。当前 [`workflow_templates.py`](../../backend/app/workflow_templates.py) 只支持固定候选模板，尚无任意导入入口。依赖标准库，复制 MIT 代码须保留版权与许可声明；不复制 Qt 部分。测试必须覆盖 bypass 悬空复现、reroute/PrimitiveNode/subgraph、widget prompt 序列化、旧格式、坏链、未知节点、资源上限；上游无现成自动化测试，本审计未运行这些测试。

仅在**真实用户持有 ComfyStudio 项目文件且明确需要迁移**时，上游 [`Shot.from_dict`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/cs_datastruts.py#L238) 与 [`WorkflowAssignment.from_dict`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/cs_datastruts.py#L108) 可作为一次性迁移输入格式，需重新写读取器：分配/匹配项目内稳定 `shot.id`；把 `stillPath`/`videoPath` 经显式授权和文件校验导入 `ProjectAssets`，而非保留任意绝对路径；`inPoint/outPoint` 转成时间线 clip 时依据真实媒体时长计算；workflow 路径仅做外部参考，不能自动执行；`imageVersions`、`videoVersions`、`versions` 只能保留来源记录，不能直接标为已检查、已采用或审核通过。目标边界为 [`preproduction_api.py`](../../backend/app/preproduction_api.py)、[`project_assets.py`](../../backend/app/project_assets.py)、[`shot_production.py`](../../backend/app/shot_production.py) 和 [`timeline_api.py`](../../backend/app/timeline_api.py)，不写第二份项目 JSON。

## 14. 部分复用

可抽取算法或映射规则，但须重新实现其周边语义：[`comfy_workflow_convert.py#L35`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/comfy_workflow_convert.py#L35) 的格式识别、list/dict links 读取及位置 widget 算法属于部分复用；[`core/base.py#L284`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/base.py#L284) 的 literal 参数提取只处理标量并跳过图连接，可做未来候选工作流参数 UI 的解析参考；[`comfy_datatypes.py`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/comfy_datatypes.py) 的媒体端口类型可借鉴校验规则。上游 [`core/comfyhandler.py#L469`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/comfyhandler.py#L469) 的拓扑排序/交错顺序只具有研究价值，不能据此恢复应用内最终视频生成或队列。上游 [`workflow_browser.py#L119`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/workflow_browser.py#L119) 从媒体元数据抽取 workflow 的解析思路也只适于受控导入，必须有源文件大小/内容校验。

## 15. 只参考设计

参考上游镜头版本下拉、缩略图擦看、流程图类型/环错误提示、外部工作流保存后参数重新同步、缺失媒体重链界面，分别见 [`core/ui.py#L975`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/ui.py#L975)、[`new_widget.py#L225`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/new_widget.py#L225)、[`shot_node_editor.py#L569`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/shot_node_editor.py#L569)、[`plugins/media_linker.py#L131`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/plugins/media_linker.py#L131)。在当前 UI 中，版本下拉应读 `ShotResultVersion`，重链应走素材 ID 与引用检查；画布只能编辑受控关系，不能注入任意 ComfyUI node。

## 16. 不引入

不引入 Qt/PyQt6/WebEngine 桌面 Shell、任意 Python 插件自动扫描执行、对外监听的 API 图片服务、无认证的本地保存桥、ComfyUI 安装/节点/模型自动下载、上游 `renderQueue`、镜头内媒体路径作为权威、直接以 FFmpeg 命令拼接导出。源码边界：[`setup.py`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/setup.py)、[`core/ui.py#L1310`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/ui.py#L1310)、[`plugins/api_handler.py#L65`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/plugins/api_handler.py#L65)（绑定 `''`）、[`workflow_edit_server.py#L21`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/workflow_edit_server.py#L21)（`Access-Control-Allow-Origin: *`，按当前目标路径写文件）、[`worker.py#L116`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/worker.py#L116)、[`plugins/exporter.py#L250`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/plugins/exporter.py#L250)。这些决定来自产品边界、权限与重复状态风险，不是断言上游软件不可用。

## 17. 当前缺失或待补足

当前没有用户导入任意 ComfyUI UI/API workflow 的受控入口、节点/模型能力快照到项目镜头步骤的统一映射，也没有将任意 workflow 参数映射到 `PreproductionNode` 的规范流程。`workflow_templates.py` 只构建固定候选，`CharacterMotionPanel.tsx` 的准备模式只导出候选包。需要时优先补“导入文件 → 只读预检 → 候选包”能力，且不得将其解释为已验证可执行。当前关系图仅投影受控节点，未承载上游 workflow assignment 端口模型；这是有意边界而非漏实现。证据：[`workflow_templates.py`](../../backend/app/workflow_templates.py)、[`CharacterMotionPanel.tsx`](../../frontend/src/CharacterMotionPanel.tsx)、[`preproductionWorkspaceStore.ts`](../../frontend/src/preproductionWorkspaceStore.ts)。

## 18. 删除/替换

本次审计**不建议删除任何当前实现**。若未来做 ComfyStudio JSON 导入，替换的是导入时的数据表示：`shots[]` 的数组位置 → 当前 `shot.id/rank/sceneId`；`Shot.graph.positions` → 独立 `CanvasLayout`；`videoPath/stillPath` 和 workflow `lastOutput` → 项目素材 ID/候选记录；镜头的 `inPoint/outPoint` → 时间线 clip 的秒单位字段；上游 `selected_version_index` → 仅局部 UI 选择，不能改写已采用候选。上游字段见 [`cs_datastruts.py#L61`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/cs_datastruts.py#L61)；本地目标见 [`preproductionApi.ts`](../../frontend/src/preproductionApi.ts)、[`timeline.py`](../../backend/app/timeline.py)。

## 19. 融合架构

```text
官方 ComfyUI API-format workflow（优先外部输入）
  → 受控解析 / 节点与引用校验 / 来源提示
  → 候选 workflow 预检/导出（不拥有 Shot）
  ↗ 仅需要 UI-format 时：适配后的受限转换器，无法保证语义则拒绝
  → 内容项目内 PreproductionShot + ProjectAssets（唯一权威）
  → SceneShotList / WorkflowCanvas（同一 Store 的投影）
  → 外部生成后由用户关联结果候选
  → TimelineStore → 渲染运行 → 当前版本审核/交付
```

流程节点仍由 [`ShotProduction`](../../backend/app/shot_production.py) 定义有效输入和产物；[`durable_runs.py`](../../backend/app/durable_runs.py) 只保存执行事实；[`batch_production.py`](../../backend/app/batch_production.py) 决定审核/交付，不由任何 ComfyUI prompt 完成状态推导。

## 20. 融合流程

1. 若产品确有工作流导入需求，用户优先提供 ComfyUI 官方 API-format 导出。读取器检查结构、大小、节点输入与引用，不立刻保存或运行。
2. UI-format 仅在适配后的转换器可证明语义正确时转换；bypass/reroute/subgraph 等无法安全处理则明确拒绝并要求官方 API-format。输出经目标环境 `object_info` 与完整图引用校验，仍标为“候选”。
3. 用户确认候选 workflow 与已有制作镜头/素材引用的关系；持久化仍经当前 `PreproductionStore`/`ProjectAssets` 和 revision 冲突控制。只有真实 ComfyStudio 文件迁移需求时，才另做 Shot JSON 一次性迁移。
4. 用户在当前列表/画布查看同一 Shot；最终 AI 视频在外部生成后，由现有流程关联视频素材与结果候选。时间线必须显式导入被采用结果，不从镜头排序自动覆盖剪辑。
5. 当前本地剪辑运行、预览、审核及交付继续走已有快照和有效性检查。上游“按阶段批量生成”只作设计参考，不恢复应用内最终生成队列。

## 21. 目录影响

本次只有本报告。若确需开放工作流导入，先为官方 API-format 新增受控导入 API/DTO 与对应前端选择 UI，按现有 [`preproduction_api.py`](../../backend/app/preproduction_api.py)、[`preproductionApi.ts`](../../frontend/src/preproductionApi.ts) 约定接线。只有 UI-format 导入有真实需求时，才新增适配后的 `backend/app/comfy_workflow_convert.py`（MIT 归属随采用代码保留）及 `backend/tests/test_comfy_workflow_convert.py`。无需移入 `src/comfystudio/`、Qt 资源、`plugins/`、上游 `defaults/models.json` 或其 workflow 模板。UI 若实际变更须先读项目根 [design.md](../../design.md)。

## 22. 数据模型

提议导入 DTO 与领域对象分层：优先 `ImportedWorkflow{sourceHash,apiGraph,validationStatus}`，仅存在于预览/规范化边界，不作为持久化第二真相。若实际进行 ComfyStudio JSON 迁移，再增加一次性 `ImportedComfyProject{sourceHash,shots[]}`。持久层继续使用 `PreproductionShot{id,sceneId,rank,assetIds,nodes,resultAssetId,resultVersions}`、项目素材 `id/kind/role/file`、`CanvasLayout{scope,layoutRevision,nodes,viewport}`、时间线 `track/clip{assetId,start,inPoint,duration,...}`。映射时不得把上游 `Shot.videoVersions` 当作 `ShotResultVersion.reviewed` 或批量 `review`，不得把 `WorkflowAssignment.lastSignature` 当成冻结运行输入指纹。证据：上游 [`cs_datastruts.py#L61`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/cs_datastruts.py#L61)；本地 [`preproductionApi.ts`](../../frontend/src/preproductionApi.ts)、[`timeline.py`](../../backend/app/timeline.py)、[`durable_runs.py`](../../backend/app/durable_runs.py)。

## 23. Store 与状态

不建 ComfyStudio 全局 Store。当前 [`preproductionWorkspaceStore.ts`](../../frontend/src/preproductionWorkspaceStore.ts) 将 `entities`、`order`、`selection`、`layoutPersistence`、`persistence` 分开，`projectWorkflow` 从镜头/素材投影节点边；导入草稿可作为组件局部状态，成功保存后通过 `hydrate`/`acceptServerWorkspace` 收敛。列表、画布、检查面板使用同一 `selectedShotIds`/`primaryEntity`；时间线保持独立版本。上游 [`mainwindow.py#L57`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/sdmodules/core/mainwindow.py#L57) 的 `self.shots` 与 `renderQueue` 不进前端状态。

## 24. API 与服务

建议未来工作流导入接口采用“预览 → 确认”二阶段：`POST /api/projects/{id}/preproduction/comfy-import/inspect` 只返回受限 DTO/警告和候选图摘要；`POST .../commit` 带项目 `revision`、预览指纹及用户确认的映射，调用当前项目边界，不得接收客户端标记为 `reviewed/adopted` 的字段。优先接受官方 API-format；若 UI-format 规范化验证不足则返回需重新导出的错误。导入服务不调用 `ComfyUIClient.submit`，也不恢复新的最终生成提交。已存在 [`preproduction_api.py`](../../backend/app/preproduction_api.py) 的项目范围与修订接口、[`comfyui_client.py`](../../backend/app/comfyui_client.py) 的受控协议和 [`timeline_api.py`](../../backend/app/timeline_api.py) 的渲染 API；不新开上游 [`api_handler.py`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/plugins/api_handler.py) 服务器。路径是**设计建议**，当前不存在。

## 25. P0/P1/P2 与验收门槛

| 优先级 | 内容 | 完成门槛 |
|---|---|---|
| P0 | 守住现有生成边界与唯一真相；若决定支持导入，先接受官方 API-format 并受控预检，不默认导入整个 ComfyStudio 项目 | 尺寸/节点数/坏链/未知节点限制；无隐式执行、无第二份项目状态、无应用内最终生成提交 |
| P1 | 只有 UI-format 导入确有需求时，迁移后适配转换器及新增自动化测试；镜头列表交互仅按当前设计系统补齐 | bypass/悬空引用、widget 序列化、reroute、PrimitiveNode/subgraph、旧格式拒绝；跨视图选择一致 |
| P2 | 仅真实用户文件迁移需求时做 ComfyStudio JSON 一次性迁移；参数可见性与类型提示作后续探索 | 修订冲突、缺失文件、重复 ID、版本不误标审核；不恢复应用内最终生成或第二队列 |

**License/来源门槛。** 根 [`LICENSE`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/LICENSE) 是 MIT；固定树没有发现子目录独立 `LICENSE`/`COPYING` 文件，因此只能把仓库自有代码按根 MIT 处理。[`setup.py`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/setup.py) 依赖 PyQt6/PyQt6-WebEngine；[Riverbank 官方说明](https://www.riverbankcomputing.com/software/pyqt/) 指明 PyQt 是 GPLv3/商业双许可，**不是 LGPL**。[Qt 官方许可说明](https://doc.qt.io/qt-6/licensing.html) 将 Qt 本体的 LGPL/GPL/商业选项及模块例外分开；[Qt WebEngine 官方说明](https://doc.qt.io/qt-6/qtwebengine-licensing.html) 还要求核 Chromium 第三方许可。结论是**不迁移 Qt UI/桌面壳/插件服务器**；即使选择纯标准库转换片段，也须保留 MIT 声明并先完成上述语义适配。OpenCV、PyAV 等依赖各有自己的许可；[`defaults/custom_nodes.json`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/defaults/custom_nodes.json) 指向第三方节点， [`defaults/models.json`](https://github.com/XmYx/ComfyStudio/blob/46416846d44b544396e66d9c6f29fccc1b30971d/src/comfystudio/defaults/models.json) 指向外部权重；ComfyUI 主程序、节点、模型权重各自边界未在本次固定仓库中获得完整许可证明，所以不复制、打包或自动安装它们。当前模板也有单独的 [`COMFY_TEMPLATE_LICENSE.txt`](../../backend/app/workflow_templates/COMFY_TEMPLATE_LICENSE.txt)，不能用上游 MIT 覆盖该来源。

**验证状态。** 本次以固定提交和当前源码进行静态审计，未运行 ComfyStudio GUI、ComfyUI、FFmpeg 或本项目测试；上游固定树未见 `tests/`、`test_*.py` 或 pytest/unittest 测试资产，不能声称测试通过。未使用动态 star 数或近期活跃度作证据。本报告没有代码级直接复用项；转换器迁移前必须补本项目自动化测试。

**累计登记表待收紧。** 当前 [参考项目累计融合决策登记表](./reference-project-integration-register.md) 对 ComfyStudio 写有“镜头绑定 ComfyUI Workflow、参数映射、生成版本和阶段式批量运行”，并把“ComfyUI/媒体生成”列为 P2 Adapter。它应按本报告收紧为“受控 Workflow 导入/规范化/校验/候选导出（确有需求时）”，删除可能被读成恢复应用内最终 AI 生成、媒体生成 Adapter 或第二队列的表述。该台账由汇总任务维护，本审计未修改。
