Status: claimed
Type: task
Blocked by: 02

# 独立 AI 无限画布操作页面

已确认页面结构与接口见 ../implementation-plan.md。独立导航，全幅画布，紧凑顶部，左添加工具，按需右属性；无需先创建镜头。

## 验收
- 文本与图片素材可独立添加、连接生成节点；非法端口拒绝。
- 保存刷新、跨页草稿保留、保存冲突保留本地修改。
- 显式运行、历史结果、结果加入画布和分支续创；配置入口不丢画布草稿。
- 桌面与窄屏真实操作非空画布，区分模拟协议测试和真实生成验收。
- 来源与 MIT 声明保留，沿用现有 XYFlow；画布页面适用独立设计例外，可完整参考开源页面、节点和浮动工具，不受此前全站布局与组件外观限制。

## Comments
2026-09-30 商用整改说明：下方早期“已实现”与局部检查记录只证明当时列出的基础操作，不代表完整参考功能对齐或商用达标。当前状态为未达标整改；主体交互、异常路径及缺失能力以 ../parity-acceptance.md 和 ../progress.md 为准。

2026-09-30：开始实施，云端图片服务配置独立于聊天分析服务。

用户指出节点不符合参考体验，初版“文字卡片＋侧栏表单”未通过。已重新核对对应功能源码，再调整节点本体：
- Toonflow `d86cce6b689916ab73f75bc4c1f42a2a7424516c`：`packages/nodes/imageGenerationNode/src/index.vue`、`imageNode/src/index.vue`、`textNode/src/index.vue`。主体预览、选中时节点附近的提示词/模型/尺寸/执行、图片查看与替换；MIT。Vue/Element Plus/runtime 不原样迁入 React，媒体预览/布局样式与操作位置适配既有 XYFlow/ProjectAssets。
- basketikun/infinite-canvas `dab19adc0847e32e39b7fc8ff90cb392561fb826`：`web/src/components/canvas/canvas-node.tsx`、`canvas-node-hover-toolbar.tsx`、`nodes/builtin-nodes.tsx`。React 媒体内容、结果候选和节点附近查看/下载/继续操作；MIT。其自有画布、antd/i18n/plugin store 不整包引入，保留真实媒体预览与就近操作。
- 已复制 Toonflow `packages/nodeScaffold/src/connection.ts` 的纯类型兼容函数，固定版本及 MIT 声明在 `vendor/Toonflow/`；不得以这一局部复用声称主体节点已完成参考。

后续验收增加：提示词与生成在节点上即可操作；媒体节点显示真实内容；结果可看后再分支；侧栏只承载次要信息。首版支持一项生成服务配置，节点显示实际配置的模型和设置入口，未配置时明确提示，不伪造多模型下拉。

2026-09-30 收尾：独立导航、全幅页面、节点内提示词/预览/尺寸/执行、素材选择、连线、保存/恢复、运行历史与分支操作已实现。13 项目标测试、7 项导航测试、构建及独立审查通过。浏览器验证非空画布上传/引用/文本编辑/连线/拖动/键盘删除/保存可见/刷新恢复，桌面和窄屏截图见 ../evidence/。实际检查发现的 XYFlow 尺寸丢失、窄屏工具栏和提示遮挡问题已修复。真实生成服务未配置，基于真实结果的续创验收仍待完成，状态保持 claimed。
