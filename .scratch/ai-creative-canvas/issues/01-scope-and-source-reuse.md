Status: resolved
Type: grilling

# AI 无限画布：首版范围与源码复用

## 已确认要求

- AI 无限画布与 AI 商业自动混剪为当前并列重点。
- 独立画布页面，画布占主工作区；紧凑顶部栏、左侧添加工具、按需属性面板、角落视图控制与节点快捷操作。全站业务导航可收起或返回项目，不套多层表单。
- 素材、文本、流程节点和结果可独立参与创作，不要求先创建 Shot；镜头/Storyboard/Timeline 为可选后续去向。
- 多项目共同参考布局、实际操作与数据关系，不局限 Toonflow。
- 优先复制或集成许可允许且可适配的成熟源码，保留来源、固定版本与许可；不能复用时明确原因。
- 验收必须覆盖有素材、有节点、有结果分支的操作路径，不以空画布截图代替。

## 当前事实

- 已使用 XYFlow；现有独立入口仍复用 PreproductionWorkspace。
- 前端 WorkflowNode 的 process 分支要求 ownerShotId，节点执行接口也要求 shotId，需调整业务归属与执行边界。
- 既有研究的“无需采用画布”和“受约束镜头关系图”范围已被新规则覆盖；源码与许可记录仍可作为核查起点。
- 本地任务系统、分流状态与 single-context 文档配置已存在，复用，不重复初始化。

## 已决定

用户确认首版接入真实生成：先打通一条可用服务链路，再扩展。用户已确认产品业务审查方向，继续实施 P0。首条链路优先复用现有 ComfyUI 客户端，提供文生图与图片分支续创；真实验收依赖可用 ComfyUI 与模型，不能用模拟响应宣称出图成功。

## Answer

范围已确认：独立页面、无需 Shot、多参考项目、真实生成与结果分支。核查后发现本机无 ComfyUI，用户未表示已部署；首条实现按云端 Images API 继续，具体调整及接口见 ../implementation-plan.md。现有聊天分析配置不等于生成能力，生成服务独立配置；待真实凭据可用后做出图验收。

## Comments

2026-09-30：按 ask-matt 路由进入 grill-with-docs。已确认要求不重复询问；当前进行只读源码核查，尚未启动产品实现或外部生成调用。

### 首轮只读核查结果

- `backend/app/preproduction_api.py` 的 SaveWorkspace 不接收独立 workflow；workflow 由 Shot/assetIds/nodes/input 投影。
- `backend/app/canvas_layout.py` 已有独立布局和视口持久化及 revision 冲突处理，但会过滤不在领域投影中的节点；不能直接承载自由新增节点。9/28 研究关于布局未持久化的描述已过时。
- 项目素材服务、素材 ID、文件服务、引用检查和 XYFlow 可复用；独立节点身份、保存和执行需要设计清晰接口。
- Toonflow 固定提交 d86cce6b689916ab73f75bc4c1f42a2a7424516c 的 connection.ts、selectionNodes.ts、arrangeCanvas.ts 和 canvasShortcuts.ts 是下一阶段实际源码核对候选；Vue/Bun 实现不能原样运行于当前 React/FastAPI。
- BeatDesign 与 basketikun/infinite-canvas 的引用模型和结果分支是补充核对对象。当前仅使用已有研究定位来源，尚未重新获取或复制源码，许可也须在实际采用时按固定文件核实。
