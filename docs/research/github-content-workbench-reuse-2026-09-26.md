# 内容工作台 GitHub 源码选型

日期：2026-09-26

状态：源码与许可核验；尚未迁入正式功能，也未运行候选项目或完成真实服务验收。

## 1. 已确定的方向

用户已选择 **A 制作入口 + B 单条精修 + C 批量审核**，要求已有相同功能时优先复用 GitHub 源码，只补必要适配。此次查阅官方仓库、固定提交下的源文件、依赖清单和许可证；不以 star 数或 README 功能列表作为实现证据。

在本次核验的候选中，推荐 **现有工作台 + MoneyPrinterTurbo 配音模块 + OpenShorts 步骤组件 + react-resizable-panels 分栏库**。这是针对当前 React 18 / Vite / FastAPI / FFmpeg 和已实现的审核版本规则所作的选择，并不宣称所有 GitHub 项目中的绝对最优。

完整顺序保持：商品事实与授权素材 → 展示并确认发送内容 → 生成 5 条脚本 → 编辑确认 → 多音色试听并选择 → 对应配音与混剪 → 同一 MP4 声画字幕预览 → 审核 → 导出。数字人阶段继续暂停；本选型不缩减第二阶段的全新画面、音乐与客户网页工作台目标。

## 2. 可实施的复用清单

### A. 制作入口：OpenShorts 的独立步骤组件

仓库：`Warabellum/openshorts`，固定提交 `dc22ac715710260d59b02b61a2ce877dda8d9d95`。

可直接迁入并适配 [StepIndicator.jsx](https://github.com/Warabellum/openshorts/blob/dc22ac715710260d59b02b61a2ce877dda8d9d95/dashboard/src/components/ui/StepIndicator.jsx)：只有 React 与 lucide 图标依赖，已有步骤列表、返回回调与 `aria-current`。其 [依赖清单](https://github.com/Warabellum/openshorts/blob/dc22ac715710260d59b02b61a2ce877dda8d9d95/dashboard/package.json) 属于 React 18 系列，与本项目较接近。

必要适配：补 TypeScript 类型；将 Tailwind 类映射至现有 CSS；完成状态由真实脚本确认、音色选择和运行版本计算，不能沿用“位于当前步骤之前即已完成”；窄屏仍提供步骤名称。保留源代码结构、出处与许可，记录这些修改。

[根许可证](https://github.com/Warabellum/openshorts/blob/dc22ac715710260d59b02b61a2ce877dda8d9d95/LICENSE) 为 MIT，但明确排除 `cloud/`，该目录采用 [独立商业许可](https://github.com/Warabellum/openshorts/blob/dc22ac715710260d59b02b61a2ce877dda8d9d95/cloud/LICENSE)。此次只选根 MIT 范围的独立 UI 模块。

### B. 单条精修：OpenCut 布局参考 + 分栏库源码复用

OpenCut Classic 的 [resizable.tsx](https://github.com/opencut-app/opencut-classic/blob/cf5e79e919144200294fb9fed22a222592a0aeea/apps/web/src/components/ui/resizable.tsx) 实际调用 `react-resizable-panels`。直接使用其上游库更适合当前项目：省去 Classic 的 Next.js、Tailwind、工具别名和编辑器状态迁移。

上游：`bvaughn/react-resizable-panels`，固定提交 `ffa22a1dae86779093bbf40179e4c22e038867cb`，源码版本 4.13.3。[package.json](https://github.com/bvaughn/react-resizable-panels/blob/ffa22a1dae86779093bbf40179e4c22e038867cb/package.json) 声明 React 18/19 peer；[MIT 许可](https://github.com/bvaughn/react-resizable-panels/blob/ffa22a1dae86779093bbf40179e4c22e038867cb/LICENSE.md)。实施时核对 npm 对应发布版本及锁文件，使用该版本公开 API；不能把 Classic 旧版本 wrapper 的 API 原样套到 4.x。

复用库的拖动分栏实现，承载现有素材、播放器、属性和时间线。本项目已经具备时间线、音频波形、字幕编辑与 FFmpeg 渲染，不重复搬入一套浏览器渲染引擎。

### C. 批量审核：保留现有审核模型

OpenShorts 的 [ResultCard.jsx](https://github.com/Warabellum/openshorts/blob/dc22ac715710260d59b02b61a2ce877dda8d9d95/dashboard/src/components/ResultCard.jsx) 是 991 行组件，绑定账号、社交发布、远端接口、Remotion、字幕弹窗和下载回退。它不是可独立嵌入的审核组件，整段搬入会引入与本任务无关的功能，也没有本项目的审批失效契约。

C 使用现有 BatchEditor 的真实变体与审核数据，调整为已选的批量表布局；它属于本项目已有代码复用。参考 OpenShorts 的样片与状态展示，不迁入其发布和下载调用。所有出口继续通过本项目后端校验当前预览、审核与修订。

### 配音：MoneyPrinterTurbo 优先，NarratoAI 补充参考

首选迁入 MoneyPrinterTurbo 的音色目录、TTS 请求和音频校验相关函数，再接本项目凭据、披露确认、任务存储及版本规则。其 Streamlit 试听页面不能直接嵌入 React；试听缓存与完整试听复用逻辑可随后端适配使用。具体固定提交、函数和异常处理差异见 [配音源码核验](github-voice-source-reuse-2026-09-26.md)。

这里选择的是实现来源，尚未授权切换供应商。MoneyPrinterTurbo 的 MiniMax 直连与现有 Fal 账户不同；原 Fal 余额不足仍未解除。标准音色、服务可用性和商用使用范围必须按实际服务确认，源码 MIT 不能替代音色授权。

## 3. 其他候选为什么不整体接入

| 项目 / 固定提交 | 查到的实现与限制 | 本轮决定 |
| --- | --- | --- |
| OpenCut `e668010778568641babef2cc40be4703ae6916d6` | [README](https://github.com/OpenCut-app/OpenCut/blob/e668010778568641babef2cc40be4703ae6916d6/README.md) 明确主版本重写；[前端清单](https://github.com/OpenCut-app/OpenCut/blob/e668010778568641babef2cc40be4703ae6916d6/apps/web/package.json) React 19 / Vite 8 | UI 参考；未来计划中的 API/MCP 不能视为可直接使用的完成能力 |
| OpenCut Classic `cf5e79e919144200294fb9fed22a222592a0aeea` | [仓库](https://github.com/opencut-app/opencut-classic) 已归档；Next 16 / React 19；MIT | 保留布局参考和独立模块线索，避免整体编辑器迁移 |
| Twick `3044c23e282b87c639c3f0b3fd5fb3fd972aa04c` | [Studio 清单](https://github.com/ncounterspecialist/twick/blob/3044c23e282b87c639c3f0b3fd5fb3fd972aa04c/packages/studio/package.json) 支持 React 18；[SUL 许可](https://github.com/ncounterspecialist/twick/blob/3044c23e282b87c639c3f0b3fd5fb3fd972aa04c/LICENSE.md) 对 SaaS、竞争产品等要求商业协议 | 与未来客户网页工作台相关，当前不纳入免费直接复用范围 |
| free-react-video-editor `458f24db0a3bdbe31d4fa5a2c24e9af9642f357c` | [组件](https://github.com/reactvideoeditor/free-react-video-editor/blob/458f24db0a3bdbe31d4fa5a2c24e9af9642f357c/components/react-video-editor.tsx) 明确是单组件演示、桌面范围；[清单](https://github.com/reactvideoeditor/free-react-video-editor/blob/458f24db0a3bdbe31d4fa5a2c24e9af9642f357c/package.json) 引入 Next/Remotion | 不比本项目已有时间线更适合本轮交付，MIT 不代表其所有依赖采用同一许可 |
| 开片 `jnMetaCode/openshorts`，`48df3591750ea9d0d08f58fb9ce154605e61dfa2` | 与 Warabellum 同名但不同项目；[清单](https://github.com/jnMetaCode/openshorts/blob/48df3591750ea9d0d08f58fb9ce154605e61dfa2/package.json) 为 2.0 alpha、Node 服务；[storyboard](https://github.com/jnMetaCode/openshorts/blob/48df3591750ea9d0d08f58fb9ce154605e61dfa2/shared/storyboard.mjs) 含估算时长、预设纸片分镜 | 后续生成素材编排参考；当前声音驱动的时间线不能直接用估算帧数代替真实时长 |
| PySceneDetect `81c414cb4b706e58648f98efd381024790b1565f` | [检测器源码](https://github.com/Breakthrough/PySceneDetect/blob/81c414cb4b706e58648f98efd381024790b1565f/scenedetect/detectors/adaptive_detector.py)、[BSD-3-Clause](https://github.com/Breakthrough/PySceneDetect/blob/81c414cb4b706e58648f98efd381024790b1565f/LICENSE)；[清单](https://github.com/Breakthrough/PySceneDetect/blob/81c414cb4b706e58648f98efd381024790b1565f/pyproject.toml) 需 Python ≥3.10 与额外数值/解码依赖 | 当前后端声明 ≥3.9，已有 FFmpeg 镜头检测；先复用已有能力，出现明确检测不足再评估引入 |

OpenShorts 的 [clip_selection.py](https://github.com/Warabellum/openshorts/blob/dc22ac715710260d59b02b61a2ce877dda8d9d95/clip_selection.py) 实现转录窗口与单视频选段边界，不能直接替代多素材按卖点组镜头。其 [scene_detection.py](https://github.com/Warabellum/openshorts/blob/dc22ac715710260d59b02b61a2ce877dda8d9d95/scene_detection.py) 还引入 TransNetV2、Torch 与自动回退，不作为本轮默认依赖。

## 4. 接入顺序与验证目标

1. **正式制作入口**：迁入步骤源码并承载现有 AigcCreator，保持已有发送披露、5 条候选与确认接口；返回编辑保留草稿，修改后重算下游资格。验证真实脚本流程，不能调用原型模拟生成。
2. **真实多音色试听到成片**：适配选定 TTS 源码，多个标准音色用同一句文字对比、同时只播一个；用户选择后生成对应配音，以真实时长编排并进入现有渲染。验证余额失败不重试/换服务，脚本或声音变化使旧审核失效；必须实际试听并播放有画面、声音、字幕的同一 MP4。供应商与音色条件未满足时如实标为未验收。
3. **B 精修 / C 批审**：使用分栏库与现有编辑器、批量审核代码；验证改一条内容不会替换其他草稿，逐条通过后才允许导出，过期审核被后端拒绝；检查桌面、窄屏与键盘操作。
4. **片段库与结构化计划**：复用当前本地检测/转录/分析能力建立各项目素材的时间段索引；画面外发单独披露。计划携带素材范围、脚本/声音/索引修订，按真实配音安排片段。现有名称备注匹配不能充当画面理解验收。

## 5. 迁入规则与此次验证边界

每份迁入文件记录上游 URL、SHA、文件路径、许可证、原始内容摘要及适配说明，保留版权声明；库依赖固定版本并进入锁文件。已有功能沿用本项目代码，真正缺少的功能才接候选模块。账号、计费、发布、分析追踪以及无关引擎不随组件整仓迁入。

本轮只读取源码和生成决策文档，没有安装候选依赖、运行第三方代码、发出模型请求或改变正式业务实现。许可与源码核验属于接入依据，不等于构建兼容、音色质量、模型效果或客户交付已经验收。后续在实际适配点测试，不重复用原型演示作为完成证据。
