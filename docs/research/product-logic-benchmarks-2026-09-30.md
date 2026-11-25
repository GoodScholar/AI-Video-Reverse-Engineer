# 完整产品业务与模块闭环对标：六个一手来源

日期：2026-09-30。性质：官方文档与公开源码研究，服务本项目产品审查；没有登录竞品账号或声称掌握商业产品内部数据库。以下“本项目取法”均为推论与建议，不代表已实现。资料按业务机制选取，避免把通用画布、招聘系统或营销 CRM 当作视频产品模板。历史研究见 [模块业务逻辑对照](module-business-logic-benchmark-2026-09-27.md)；当前范围以根目录 `AGENTS.md`、[商用混剪决策](../adr/0012-commercial-montage-stays-in-existing-production-chain.md) 和 `.scratch/commercial-ai-montage/spec.md` 为准。

## 1. 对标结论

完整产品不应只有“若干可点击模块”，而应有两条能交付成果的主链：

1. **商业自动混剪**：商品目标与事实、素材及授权 → 片段索引/检索 → 脚本与素材匹配 → 可解释混剪提案 → 人工采用为现有变体编辑序列 → 配音、字幕、包装和渲染 → 真实媒体质检 → 指定版本人工审核 → 批量交付。批次只聚合进度，每条变体保留独立失败、重试与审核状态。
2. **独立创作画布**：素材、文本、处理节点或历史结果任一入口 → 有类型、有来源的连接 → 能力校验 → 冻结输入与节点配置 → 实际执行/外部提交 → 带来源的媒体结果 → 基于结果分支续创 → 按需采用到项目素材或时间线。镜头是可选关联对象，画布节点没有强制 `Shot` 归属。画布要展示真实生成运行及结果，不能以空图、静态连线或“已保存计划”冒充生成完成。

六个样本分别回答不同问题：HubSpot 说明商业目标如何贯穿内容资产；Linear 说明进入生产前如何分流并保持对象状态清楚；n8n 说明图执行与每次运行如何区分；Dify 说明画布草稿、发布、节点运行和人工意见如何衔接；ComfyUI 说明媒体生成的提交、队列与结果协议；Greenhouse 说明评价证据和正式批准为何是两种行为。它们均不提供可直接替代本项目视频业务链的完整实现。

## 2. 六个产品的实际机制及边界

| 样本 | 一手证据：信息架构、任务流和状态 | 本项目取法 | 不应照搬 |
| --- | --- | --- | --- |
| **HubSpot Marketing Studio** | 官方帮助描述“活动 brief → 画布中的营销资产 → 编辑/复用内容”；brief 为 campaign agent 生成内容和自动化提供上下文，资产卡可从现有资产 remix 为另一内容类型。工作流另有对象入组触发、动作、重新入组与发布检查；默认不会反复入组。来源：[活动创建与资产管理](https://knowledge.hubspot.com/campaigns/create-campaigns-using-marketing-studio)、[工作流创建](https://knowledge.hubspot.com/workflows/create-workflows)、[触发条件](https://knowledge.hubspot.com/workflows/set-your-workflow-enrollment-triggers)。 | 将客户目标、商品事实、受众/渠道与禁止宣称作为可追溯简报；脚本、变体、成片都是关联资产。自动混剪以一次明确命令作用于已选变体，避免旧项目因配置变化被意外重新生产。 | 不复制 CRM 的联系人、交易、营销邮件对象，也不把营销活动画布当成媒体生成画布。官方“AI 建议资产”并不证明视频镜头由模型生成。 |
| **Linear** | Issue 属团队工作流，Project 聚合同一成果的 Issues，View 只是过滤视图；Triage 可接受、拒绝、合并重复或暂缓。AI Triage 对团队、负责人、标签与关联提出建议，规则可自动应用部分属性。来源：[概念模型](https://linear.app/docs/conceptual-model)、[Triage](https://linear.app/docs/triage)、[Triage Intelligence](https://linear.app/docs/triage-intelligence)、[状态类别](https://linear.app/docs/configuring-workflows)。 | 项目首页可聚合“待补素材、待确认脚本、待处理运行、待审成片”；每项需要清楚的对象、负责人和动作。AI 选片与脚本推荐先呈候选及理由，再由制作人员采用；“已分流”不等于“已制作”。 | 不照搬软件团队的周期、Issue 层级或所有状态。一个内容项目的业务状态不能硬塞进单一任务看板列。 |
| **n8n** | Workflow 是定义，Execution 是一次运行；运行列表可按 Failed、Running、Success、Waiting 过滤。失败重试可选“原工作流+旧输入”或“当前工作流+旧输入”。官方 Gmail 节点可发送并等待批准后再继续，复杂批准建议用 Wait 节点。来源：[执行与重试](https://docs.n8n.io/workflows/executions/all-executions/)、[发送并等待批准](https://docs.n8n.io/integrations/builtin/app-nodes/n8n-nodes-base.gmail/message-operations/)。 | 画布定义、某次生成运行、某个媒体结果分开存。重试须显示所用图版本和输入版本；商业混剪批次中的失败条目单独恢复。运行进入 Waiting 不等于作品已获准交付。 | 不把任意自动化节点或跨 SaaS 集成搬入视频创作主界面；n8n 的运行状态不能替代素材、变体、审片等业务状态。 |
| **Dify** | Workflow 编辑器区分 Current Draft、Latest Version 与历史发布版本；Human Input 节点可暂停、收集编辑/反馈、按动作分支，超时也有独立分支。Logs 按运行展示输入/输出、状态、节点轨迹及出错处。来源：[版本控制](https://docs.dify.ai/en/cloud/use-dify/build/version-control)、[Human Input 节点](https://docs.dify.ai/en/cloud/use-dify/nodes/human-input)、[运行日志](https://docs.dify.ai/en/cloud/use-dify/monitor/logs)。 | 画布提供草稿、已执行快照、历史结果和节点级错误定位；在生成结果选择、事实确认、审片交付处设置业务化人工决策。节点输出能继续作为下一分支输入。 | 不把 Dify 的通用 AI App 发布机制当成本项目成片发布；通用 Human Input 节点不能替代带时间码、产物版本和责任人的正式视频审片。 |
| **ComfyUI** | 官方 API 中 `/prompt` 校验并入队，返回 `prompt_id` 或 `node_errors`；`/queue`、`/history/{prompt_id}`、WebSocket 分别用于排队、结果和进度，`/object_info` 查询节点能力。官方文生图教程明确从模型、提示词、采样、解码到保存真实图像。来源：[服务端路由](https://docs.comfy.org/development/comfyui-server/comms_routes)、[文生图实例](https://docs.comfy.org/tutorials/basic/text-to-image)。 | 画布只有在有真实执行器、可用节点和实际媒体结果时才显示“生成完成”；保存外部运行 ID、图/模型版本、输入引用、输出文件和节点错误。历史结果可回接下一分支或收入项目素材，关联镜头按需发生。 | ComfyUI 的图格式和队列 ID 不拥有商品事实、素材授权、成片时间线或审核；不能把“提交成功”显示为“媒体已生成”。 |
| **Greenhouse** | 候选人页面把阶段、待交/已交 scorecard、offer 批准、任务和活动流放在同一对象上下文。Scorecard 按预定属性记录评价；工作/offer 批准控制后续动作，属于明确的签核步骤。来源：[候选人页结构](https://support.greenhouse.io/hc/en-us/articles/30352015432987-Candidate-profile-redesign-overview)、[Scorecard](https://support.greenhouse.io/hc/en-us/articles/4414777492891-Scorecard-overview)、[批准概览](https://support.greenhouse.io/hc/en-us/articles/17679895458587-Approvals-overview)。 | 单条成片详情聚合真实预览、质检证据、时码意见、负责人、批准记录和交付物。自动质检提供评价证据；人工批准指向冻结媒体及输入版本，编辑后须重新判定。 | 不引入招聘阶段或僵硬统一评分；质检报告与“可交付”是不同结论，不能由模型评分自动签核。 |

## 3. 面向本项目的模块和对象审查口径

这些是审查边界，不要求拆微服务，也不代表现有代码已实现目标。当前 README 已记录项目素材、批量混剪、变体编辑、配音字幕、预览审核与导出；[ADR 0012](../adr/0012-commercial-montage-stays-in-existing-production-chain.md)明确自动选片与包装仍应进入原有生产链。独立创作画布属于新目标；不能以现有镜头关系页推定已具备自由节点创作或真实生成。

| 模块/对象 | 应有的唯一责任 | 关键交接与可验收问题 |
| --- | --- | --- |
| 内容项目与简报 | 目标、事实、受众、渠道、风险表达和批次范围 | AI 候选能否追溯已确认事实？改简报后哪些候选失效？ |
| 项目素材与片段索引 | 原件身份、授权、源区间、派生片段和可重建索引 | 同一长视频能否返回多个有源入/出点的候选？ |
| 脚本与混剪提案 | 已确认脚本、段落与视觉证据、片段排序和选择理由 | 提案可否被替换/禁用，并在采用前保持候选身份？ |
| 变体编辑与渲染 | 一条短视频的声画字幕、裁剪、转场、花字及冻结渲染输入 | 自动提案是否写入既有编辑序列；预览与交付是否同版？ |
| 创作画布 | 自由节点图、类型连接、分支、草稿与执行快照 | 无 Shot 时能否从素材或文本开始并成功生成新媒体？ |
| 生成运行与结果素材 | 能力检查、提交、进度、失败恢复、真实结果与来源血缘 | 返回成功是否仅表示入队；结果能否继续生成或被采用？ |
| 质检、审核与交付 | 工程检测、时码意见、版本批准、导出包与历史 | 修改画面/字幕后旧批准能否阻止误交付？批次失败是否局部重试？ |

跨两条主链最重要的共享契约是 `ProjectAsset` 稳定身份、不可变输入/编辑快照、运行 ID、结果来源和批准所指版本；共享的是事实和引用，不是把画布强制套入 Shot 或让混剪另建一套时间线。借鉴 [n8n 执行重试](https://docs.n8n.io/workflows/executions/all-executions/)、[Dify 版本](https://docs.dify.ai/en/cloud/use-dify/build/version-control) 与 [ComfyUI 运行接口](https://docs.comfy.org/development/comfyui-server/comms_routes) 后，本项目应明确“草稿、候选、已提交、运行中、已产出、已采用、已审、已交付”的对象归属，而非为项目根记录一个含义模糊的 `completed`。

## 4. 具体产品验收用例

1. 一个项目导入两段已授权长视频，系统建立片段级索引；对已确认脚本的五个段落给出带源区间、画面证据和理由的候选。制作人员替换一段、采用提案，得到正常可编辑变体，渲染真实预览，质检后逐条批准并导出；其中一条失败不改变其余四条状态。
2. 在独立画布从一张项目图片和一段文本开始，连接处理/生成节点，校验输入与执行能力后获得实际图片或视频；从该结果分出两条续创，能查看两次运行各自输入、模型、状态、产物和来源。全程可不创建 Shot；随后任选一个结果收入项目素材并用于混剪。
3. 画布运行排队时改动草稿，旧运行仍指向提交时快照；失败时显示节点错误与重试语义；外部超时先对账。只保存图、只拿到 `prompt_id`、或只有预览占位图均不能通过“真实生成”验收。
4. 修改已批准成片的脚本、裁剪、字幕或画布采用素材后，旧审核仍可追溯，但新版本不能沿用批准状态；质检报告可以提示工程问题，却不代替审片人签核。

## 5. 逻辑参考与源码复用分开决策

以上产品的流程、对象关系和状态机只作为**逻辑参考**。公开仓库不等于可复制源码。已核对 [n8n LICENSE](https://github.com/n8n-io/n8n/blob/master/LICENSE.md)对内部/非商业用途有限制，且企业文件另有规则；[Dify LICENSE](https://github.com/langgenius/dify/blob/main/LICENSE)为带附加条件的修改版 Apache-2.0；[ComfyUI LICENSE](https://github.com/Comfy-Org/ComfyUI/blob/master/LICENSE)为 GPLv3。它们在本报告中没有任何源码被迁入。HubSpot、Linear、Greenhouse 的公开帮助只证明产品行为，不能当成代码许可。

后续若找到真正合适的画布控件、节点运行、媒体处理或审核组件，应按根目录 `AGENTS.md` 先核实际源码、技术栈、固定版本、依赖和许可证，再优先迁入许可兼容的最小部分并保留来源声明。本文的许可提示不是针对具体复制方案的法律结论；逐文件、逐依赖与拟定分发方式的复核仍是复制前置条件。对当前两条主链，业务对象与项目 API 的适配只写必要代码，不为借鉴上述系统而移植完整工作流平台、CRM 或 ATS。
