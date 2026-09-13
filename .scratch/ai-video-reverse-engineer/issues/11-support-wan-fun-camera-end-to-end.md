# 11: 支持 Wan2.2 Fun Camera 完整复刻路径

**What to build:** 为适合镜头控制的参考视频提供 Wan2.2 Fun Camera 推荐、参数校验、复刻包生成、下载和本地 ComfyUI Queue 的完整路径。

**Blocked by:** 09/覆盖高级设置并管理 Workflow 新鲜度, 10/检查环境并发送到本地 ComfyUI

**Status:** ready-for-agent

- [ ] 系统能够依据运镜分析和可复刻范围，在适用时推荐 Wan2.2 Fun Camera 并解释原因。
- [ ] 不适用 Wan2.2 Fun Camera 时不会把它呈现为默认可执行策略，仍可显示符合能力边界的说明。
- [ ] 用户能够使用智能匹配或合法高级设置生成 Wan2.2 Fun Camera Workflow。
- [ ] Workflow 生成前校验镜头控制所需的分析内容、控制素材和参数。
- [ ] 用户能够下载包含对应控制素材、Prompt、参数和 Workflow 的完整复刻包。
- [ ] 环境检查能够识别该 Workflow 特有的模型和节点依赖，并报告具体缺失项。
- [ ] 环境完整时 Workflow 能够发送到本地 ComfyUI、进入 Queue 并记录执行状态。
- [ ] 修改运镜分析、Prompt 或参数后，Workflow 新鲜度规则与 Wan2.2 14B I2V 保持一致。
- [ ] 两种可执行工作流在界面、项目历史和导出清单中能够被清楚区分。

