# Huobao Drama 对本项目的借鉴

核对日期：2026-09-23。目标是 [chatfire-AI/huobao-drama](https://github.com/chatfire-AI/huobao-drama)，核对源码版本 [`27f07fc`](https://github.com/chatfire-AI/huobao-drama/tree/27f07fc0a177c00a2c9b3ab9a56fba15266504f0)。`raceli/huobao-drama` 是另一仓库；`dav-niu474/huobao-drama-ai` 自称参考设计，也不是本次对象。以下“已实现”指仓库中存在相应数据结构和调用路径，未经实际部署或模型端到端运行验证。

## 结论

**有借鉴空间，重点是“剧本/角色/场景/道具 → 分镜 → 带引用的提示词”的结构化前置流程，以及阶段状态与失败任务定位。** 本项目的定位是视频逆向、素材准备与本地剪辑；Huobao 面向从故事到短剧成片的正向生产。按本项目 [ADR 0007](../adr/0007-preproduction-with-shot-steps.md) 与 [ADR 0008](../adr/0008-local-timeline-and-audio.md)，最终 AI 视频生成在外部完成，现有本地时间线负责剪辑与导出。因此宜借鉴数据关系和交互，不照搬“一键生成短剧”、模型任务系统或整集拼接页。

## 源码核实与适配判断

| 能力 | Huobao 的实际实现证据 | 本项目现状与可借鉴点 |
| --- | --- | --- |
| 剧本和分镜 | [Agent 定义](https://github.com/chatfire-AI/huobao-drama/blob/27f07fc0a177c00a2c9b3ab9a56fba15266504f0/backend/src/agents/index.ts) 包含剧本改写、角色/场景/道具提取、分镜拆解；[分镜工具](https://github.com/chatfire-AI/huobao-drama/blob/27f07fc0a177c00a2c9b3ab9a56fba15266504f0/backend/src/agents/tools/storyboard-tools.ts) 分批保存分镜、按镜号 upsert，并写入角色/道具关系。 | 本项目已有[分镜导入与镜头步骤](../../frontend/src/PreproductionWorkspace.tsx)、素材 ID 绑定。**可借鉴**分镜字段：景别、角度、运镜、氛围、对白、时长与镜头级素材关系；只在确有剧本文本输入需求时加入自动拆解，不把短剧剧集模型强加给视频逆向项目。 |
| 一致性素材与 `@名称` 引用 | [数据模型](https://github.com/chatfire-AI/huobao-drama/blob/27f07fc0a177c00a2c9b3ab9a56fba15266504f0/backend/src/db/schema.ts) 将剧级角色、场景、道具和分镜关联分开；[分镜页](https://github.com/chatfire-AI/huobao-drama/blob/27f07fc0a177c00a2c9b3ab9a56fba15266504f0/frontend/app/views/drama/episode.vue) 的 `getShotReferenceImages` 按场景、角色、道具收集参考图，`getShotReferenceIndexMap`/`resolveVideoPromptRefs` 把提示词的 `@名称` 映射为 `@图片N名称`，再作为 `reference_image_urls` 提交。 | 本项目已有[素材库、镜头素材绑定、提示词](../../frontend/src/preproductionApi.ts)，但角色/场景的一致性身份及提示词与素材的**可核验关联**仍可补强。建议先做“插入素材引用 + 显示实际引用图片 + 缺失/重名提示”，导出给外部生成工具；不要仅依靠字符串替换。Huobao 当前实现有按名称前缀匹配、超出参考图上限截断等限制。 |
| 统一画风 | [剧集数据](https://github.com/chatfire-AI/huobao-drama/blob/27f07fc0a177c00a2c9b3ab9a56fba15266504f0/backend/src/db/schema.ts) 有项目 `style`、画幅，角色/场景/道具有单独的 `finalPrompt`；[提示词服务](https://github.com/chatfire-AI/huobao-drama/blob/27f07fc0a177c00a2c9b3ab9a56fba15266504f0/backend/src/services/final-prompt.ts) 为这些对象生成持久化描述。 | 本项目[需求 brief](../../frontend/src/preproductionApi.ts) 已有风格和画幅。**可借鉴**“全局风格 + 各角色/场景固定描述 + 镜头局部动作”的提示词分层，让跨镜头一致性可检查；先扩展交接说明，不必引入其生图服务。 |
| 阶段进度与批量失败重试 | [分镜工作台](https://github.com/chatfire-AI/huobao-drama/blob/27f07fc0a177c00a2c9b3ab9a56fba15266504f0/frontend/app/views/drama/episode.vue) 根据剧本、素材、镜头视频和导出结果计算阶段状态；`retryFailedVideos` 只选失败镜头，经过批量确认再调用 `genVid`。仓库有 [v4.0.4 发布记录](https://github.com/chatfire-AI/huobao-drama/releases/tag/v4.0.4)，并非仅有 README 构想。 | 本项目已有[镜头步骤状态、过期标记与交付检查](../../frontend/src/PreproductionWorkspace.tsx)。**可借鉴**“按阶段显示尚缺几项”和“只重试失败的本地处理步骤”；不应把视频模型生成失败重试接进应用内。Huobao 的阶段完成条件依赖是否有生成视频，不能原样复用。 |
| 素材、时间线、导出 | Huobao 有[素材表](https://github.com/chatfire-AI/huobao-drama/blob/27f07fc0a177c00a2c9b3ab9a56fba15266504f0/backend/src/db/schema.ts)及[整集 FFmpeg 拼接](https://github.com/chatfire-AI/huobao-drama/blob/27f07fc0a177c00a2c9b3ab9a56fba15266504f0/backend/src/services/ffmpeg-merge.ts)：按镜号取已有视频，可选择部分镜头，检查文件后拼接。未见独立多轨剪辑时间线。 | 本项目已具备[素材库](../../frontend/src/AssetManager.tsx)、[本地时间线](../../frontend/src/TimelineEditor.tsx)和导出。Huobao 的简单拼接不提供新的核心能力；其“先检查媒体文件存在，再导出”的细节可用于核对现有导出前检查。 |

“九宫格素材”需谨慎理解：[代码中的 9/10 张](https://github.com/chatfire-AI/huobao-drama/blob/27f07fc0a177c00a2c9b3ab9a56fba15266504f0/frontend/app/views/drama/episode.vue)是不同视频模型的**参考图数量上限**；在 README 和本次检索的实现里，未核实到独立的九宫格分镜生成或九宫格素材管理功能。不能把它列为该项目已有能力。

## 优先顺序与限制

1. **镜头级语义素材引用**：把现有 `assetIds` 升级为可读的角色/场景/道具引用，在提示词编辑和交付包中显示引用关系，并校验缺失、重名与上限。价值最高，且符合外部生成边界。
2. **跨镜头一致性卡片**：在现有素材库上保存角色形象、场景布局和全局画风的固定描述，分镜只写变化部分。避免每个镜头重新解释同一角色。
3. **分阶段缺项定位**：利用现有交付检查，在需求、素材、镜头准备、剪辑四个阶段显示完成数和可点击的问题位置。
4. **局部重试**：仅针对本地裁切、首尾帧、缩放等已经存在的处理节点批量重试失败项，保留单项错误；优先级低于前 3 项。

技术上它使用 [Nuxt/Vue、Hono、Drizzle/SQLite、Mastra、FFmpeg 和 Electron](https://github.com/chatfire-AI/huobao-drama/blob/27f07fc0a177c00a2c9b3ab9a56fba15266504f0/README.zh-CN.md)，与本项目 React/Python 本地服务差异较大，宜学习产品数据和交互，避免整体移植。仓库 [LICENSE](https://github.com/chatfire-AI/huobao-drama/blob/27f07fc0a177c00a2c9b3ab9a56fba15266504f0/LICENSE) 与 [README 许可说明](https://github.com/chatfire-AI/huobao-drama/blob/27f07fc0a177c00a2c9b3ab9a56fba15266504f0/README.zh-CN.md#-license) 声明 CC BY-NC-SA 4.0、限制商业使用；不要直接复制其源码或界面资源到可能商用的本项目。发布记录证明项目持续迭代，但不等于所有模型适配、桌面打包和端到端生成均已由本次核实。
