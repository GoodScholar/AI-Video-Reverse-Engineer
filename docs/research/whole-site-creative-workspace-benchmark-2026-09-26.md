# 全站创作工作台：导航、项目、媒体与设置对照研究

日期：2026-09-26。状态：只读研究；不代表产品已实施或完成视觉验收。

本报告提供模式与导航候选；最终范围、四步商品流程和新视觉方向以 [全站综合改版建议](content-studio-redesign-recommendation-2026-09-26.md) 为准。全局素材库为备选模式，不表示当前支持跨项目共享。

## 1. 范围与证据

本报告补充此前的商品制作流程研究，目标是全站统一，覆盖项目首页、项目内制作、单条精修、批量审片、媒体库、设置和既有复刻工具。商品资料 → 5 脚本确认 → 9 音色试听 → 本地配音混剪字幕 → 审核导出保持主流程。数字人暂停。

实际读取下列新增官方页面正文；没有登录、安装、付费或下载。**文档中的截图说明不等于实际看过截图**：尝试打开 Descript 首页图片返回 403，Linear 设置及刷新图片加载失败，因此本轮不宣称已视觉验收竞品界面。所有颜色、间距和本项目布局建议均为设计推论，不能把帮助页文字提取当作产品实测。

| 新官方来源 | 证据类型 | 实际观察与本项目启发 |
| --- | --- | --- |
| [Frame.io Workspace Overview](https://help.frame.io/en/articles/9101001-workspace-overview) | 帮助正文，未登录 | 首页汇总所有项目，项目标注归属 workspace；左侧选择 workspace；设置置于其菜单。启发：先找到工作对象，再进入工具。 |
| [Frame.io Project Layout](https://help.frame.io/en/articles/9101037-project-layout-overview) | 帮助正文，未观看嵌入教程 | Grid 便于缩略图浏览，List 便于密集信息；可改变卡片尺寸和比例；字段含 Status、Duration、Transcript；视图偏好默认只影响个人。启发：媒体/成片共用视图控件，筛选不改变业务内容。 |
| [Descript Drive view](https://help.descript.com/descript-tour/drive-view) | 帮助正文；图片 403，未看图 | 顶部侧栏为 Home、Recents、Shared with me；下方 workspace；底部品牌、媒体、声音等工具；页头搜索、新建项目和设置；项目左上 Home 返回。启发：全局入口与项目工作层分开。 |
| [Adobe Projects overview](https://helpx.adobe.com/creative-cloud/apps/manage-projects/projects-overview.html) | 帮助正文，未登录 | 项目集中关联文件、文件夹、品牌与库；不同应用访问同样内容。启发：制作/精修/审核使用同一项目上下文，避免导出导入式交接。 |
| [Adobe 资产组织](https://helpx.adobe.com/creative-cloud/apps/create-and-manage-libraries/organize-manage-creative-cloud-assets.html) | 帮助正文，未登录 | Projects 围绕客户/活动目标，Brands 保存标识和样式，Libraries 保存可复用元素。启发：区分业务项目、品牌默认与素材，不把它们平铺成工具。 |
| [Linear UI refresh，2026-03-12](https://linear.app/changelog/2026-03-12-ui-refresh) | 官方设计变更说明；图片失败，未看图 | 页头、导航、视图控件在 projects/issues/reviews/documents 保持一致；侧栏视觉减弱让内容突出。启发：整体改版应统一跨页结构，不只美化批量模块。 |
| [Linear Settings are not a design failure](https://linear.app/now/settings-are-not-a-design-failure) | 官方设计文章，2022 年；未看截图 | 区分应做好的产品默认与个人偏好；设置首页加入教程和说明；workspace 与 user settings 分层。启发：设置是有解释的管理入口，不是主界面的参数堆。 |
| [Linear Custom Views](https://linear.app/docs/custom-views) | 帮助正文，未登录 | 过滤后的列表可保存/收藏；视图有范围和负责人；右侧补充上下文。启发：审片页的“待审核/失败/已通过”是同一作品集合的视图，不是复制的任务库。 |

## 2. 全站信息结构（本项目设计推论）

建议一个稳定应用壳：**全局侧栏 + 当前项目页头 + 内容区域 + 按需详情面板**。全局侧栏提供“项目”“素材库”“设置”；项目区优先“制作商品视频”，既有“参考复刻”作为明确的独立工具入口。暂停数字人不作为可执行主入口。项目内使用“概览 / 制作 / 审片 / 素材”局部导航，精修从具体作品进入。依据是 Descript 全局/项目分层与 Linear 跨页面一致性；名称与层数为本项目取舍。[Descript](https://help.descript.com/descript-tour/drive-view)、[Linear](https://linear.app/changelog/2026-03-12-ui-refresh)

不要照搬 Workspace → Team → Folder → Project 多层组织。本地内部团队先用现有复刻项目作为工作单元；“客户/商品”可作为项目属性和筛选，未来客户自助再依据真实权限需求引入组织层。Frame.io 的多 workspace 面向更复杂账户，不应因为参考它就新增未需的概念。[Frame.io](https://help.frame.io/en/articles/9101001-workspace-overview)

| 全站位置 | 默认看到什么 | 核心操作 | 一致性要求 |
| --- | --- | --- | --- |
| 项目首页 | 最近项目、商品/客户、封面、当前阶段、待审核数、更新时间 | 新建商品视频、继续上次工作 | 卡片状态词与审片状态一致；不用节点数充当进展 |
| 项目概览 | 商品摘要、素材数量、已确认脚本、已制作/待审核/可导出数量 | 按阶段继续 | 不重新填写资料；任何入口到达同一项目与批次 |
| 制作页 | 准备资料、确认脚本、选声制作、审核导出四步 | 当前阶段唯一主动作 | 用业务动词；技术详情展开，不另造一套工具导航 |
| 精修页 | 当前作品播放器、文案/声音/字幕/镜头详情，底部可展开时间线 | 修正当前作品、更新预览 | 固定项目返回入口，保留当前版本与审核失效提示 |
| 审片页 | 可播放作品卡片或紧凑列表、状态筛选、选择条数 | 通过、退回修改、导出通过项 | 与制作页同一作品标识；保留返回筛选和滚动位置 |
| 项目素材 | 当前项目素材的缩略图、类型、时长、用途、授权/可用状态 | 上传、选择、查看、从项目移除 | 使用既有 asset ID；移除引用和删除源文件语义分开 |
| 全局素材库 | 已有可复用素材及项目归属；未支持跨项目时明确说明范围 | 搜索、筛选、预览、加入当前项目 | 不假装有云共享/跨项目复用；同一素材不重复上传 |
| 设置 | 分析服务、本地声音与渲染状态、保存/导出、偏好 | 配置、验证、查看具体原因 | 应用级凭据集中；项目只保存实际选取，不复制密钥 |

项目集中资产参考 Adobe；Grid/List 切换参考 Frame.io；收藏筛选视图参考 Linear。上表的具体导航是本项目建议，需结合现有页面实现验证。[Adobe](https://helpx.adobe.com/creative-cloud/apps/manage-projects/projects-overview.html)、[Frame.io](https://help.frame.io/en/articles/9101037-project-layout-overview)、[Linear](https://linear.app/docs/custom-views)

## 3. 全站视觉规则（建议，未宣称竞品截图实测）

1. **页头统一**：项目名、当前位置、当前状态、一个主动作保持位置一致；制作页、审片页、素材页采用同一标题与工具栏结构。播放器精修可扩大内容区，但项目身份和返回路径不消失。[Linear 跨页头/导航统一](https://linear.app/changelog/2026-03-12-ui-refresh)
2. **层级用空间和对比表达**：侧栏与工具栏用低对比中性色，主内容清晰；品牌强调只用于当前选择和主要动作。最终配色以综合改版建议为准。不要让每个功能块都成为独立重边框大卡片。此为根据 Linear 侧栏减弱原则提出的视觉推论。[Linear](https://linear.app/changelog/2026-03-12-ui-refresh)
3. **状态设计统一**：候选、已确认、制作中、待审核、需修改、已通过、失败分别有固定文字和图标；颜色仅辅助。项目首页不发明另一个“完成”来混淆已生成与已审核。Frame.io 将状态放进资产字段，支持作品层状态被浏览视图统一呈现。[Frame.io Fields](https://help.frame.io/en/articles/9101037-project-layout-overview)
4. **列表控件统一**：搜索、状态筛选、排序、Grid/List 切换位置在项目、素材、审片三页一致；视频缩略图根据 9:16 真实比例展示，卡片可缩放但播放验收始终打开实际视频。[Frame.io Appearance](https://help.frame.io/en/articles/9101037-project-layout-overview)
5. **上下文修正统一**：列表选择对象后右侧详情或专注页处理；返回列表时不丢筛选、所选对象和当前位置。声音与字幕使用明确标签，不在各页分别称 voice/speaker/TTS。右侧上下文模式参考 Linear，返回状态保留是本项目建议。[Linear View sidebars](https://linear.app/docs/custom-views)
6. **密度按任务变化**：制作页适合少量可解释的选择；精修页适合播放器与控件；审片页适合密集比较。共用壳和组件规格即可，不强迫所有页采用完全相同卡片布局。[Linear 一致控件](https://linear.app/changelog/2026-03-12-ui-refresh)、[Frame.io Grid/List](https://help.frame.io/en/articles/9101037-project-layout-overview)

## 4. 设置与媒体库的分层

设置首页先显示“可用 / 需要配置 / 最近验证失败”等实际状态及用途，再进入配置详情。建议分类：服务连接、本地制作、文件与导出、界面偏好。AI 服务地址/凭据属于应用设置；音色、脚本、字幕样式属于本项目制作决定。服务失败时在制作页给“前往该设置”的具体入口，配置后返回原任务。设置解释与默认/偏好区分参考 Linear，分类为本项目推论。[Linear Settings](https://linear.app/now/settings-are-not-a-design-failure)

媒体库不要直接暴露 source path、codec、revision 等技术字段。默认显示画面、名称、类型、时长、用途与可用状态；原文件参数展开查看。全局可复用内容、项目所选素材、当前参考素材应清楚区别，保持现有领域定义和引用规则。Adobe 的 Project/Brand/Library 区别可帮助厘清职责，但本项目不应在没有需求时照搬四种资产容器。[Adobe 资产组织](https://helpx.adobe.com/creative-cloud/apps/create-and-manage-libraries/organize-manage-creative-cloud-assets.html)

## 5. 整体改版覆盖矩阵与验收

全站改版应至少覆盖下列区域，不能以“BatchEditor 已改”宣布整体完成：

| 区域 | 必须统一的内容 | 验收任务 |
| --- | --- | --- |
| 应用入口与导航 | 侧栏、主入口、项目返回、选中态、窄屏折叠 | 从任意页指出当前项目与回到项目列表的方法 |
| 项目首页与概览 | 卡片、封面、阶段、继续动作、空状态 | 从近期项目继续未完成的脚本/制作/审核 |
| 制作与脚本/声音 | 主动作、表单、音色卡、候选确认、失败恢复 | 生成并确认 5 条脚本，试听真实声音，沿用当前五条交接契约 |
| 精修 | 播放器、属性区、时间线层级、保存反馈 | 修正一条字幕后返回原审片筛选位置 |
| 审片与导出 | 卡片/列表、版本、审核状态、批量范围 | 只导出当前版本已通过的选取作品 |
| 素材库与参考复刻 | 统一素材卡、搜索、选择范围；复刻保持独立目标 | 商品素材不会误替换当前参考素材；既有复刻入口可达 |
| 设置与状态页 | 分类、连接验证、错误语言、返回原任务 | 处理服务不可用后继续原批次，不重复创建项目 |

先做以上统一结构和组件规格，再逐页适配既有能力；不因为统一导航而恢复数字人、迁移框架或复制竞品品牌视觉。开源代码移植仍需固定提交与许可核验，商业产品文档只能提供流程参考。对应商品制作细节见 [前一份研究](video-workflow-usability-benchmark-2026-09-26.md)。
