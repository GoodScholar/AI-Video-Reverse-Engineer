# 动作与镜头理解：三个公开项目的增量价值

核验日期：2026-09-18。只读研究；未安装依赖、下载权重或执行模型。使用官方仓库、作者项目页和官方权重页，不按 Star 数排序。采用 research 技能的一手来源与单文件记录方式；本文件本身由主任务委派的研究代理完成。

## 结论

1. **优先研究 CoTracker3 的交互与数据契约**：任意点轨迹、可见性、遮挡后的续接，最直接补现有动作观察缺口。但代码主体与官方权重都限制非商业使用，不能作为商业产品的默认组件直接落地。
2. **VGGT 适合相机轨迹的独立技术验证**：真正新增的是相机内外参及跨帧空间关系，不是再加一个深度模型。当前官方演示依赖 CUDA，不能据此承诺本地 Mac 可用。
3. **DWPose 排在后面**：相比现有单人 MediaPipe 33 点，它能提供多人逐帧全身、手、脸关键点，但没有直接补齐跨帧身份和遮挡恢复。只有明确需要多人/手脸控制素材时，才值得引入。

现有能力按主任务提供的上下文：MediaPipe 33 点单人骨架、VideoDepthAnything 单目相对深度、SAM2 接口、WanAnimate Move ComfyUI 接口。本报告不把替换这些模块当成新增功能。

## 1. CoTracker3：任意点与遮挡轨迹

**官方能力。** 接收视频与查询点，输出二维轨迹及逐帧可见性；支持人工选点或网格采样，有 offline 与更省内存的 online 模式。作者展示了遮挡和离开视野后的点续接，同时明确列出天空、水面等缺少纹理区域的失败例子。[官方仓库](https://github.com/facebookresearch/co-tracker)、[作者项目页](https://cotracker3.github.io/)

**具体借鉴（产品推断）。**

- 在参考视频上点选“手腕、道具角点、衣角”等，复刻工作台显示轨迹和遮挡区间；补足骨架点之外的运动证据。
- 使用既有 SAM2 主体遮罩限制采样区域，将轨迹归属到所选主体；这属于点轨迹能力的新增，不替换分割功能。
- 借鉴 `query_frame / point_id / xy / visibility` 数据结构；可见帧与遮挡预测分开显示，避免把模型补全当成直接观察。
- 用背景点轨迹辅助检验“画面整体移动还是主体移动”；单靠二维点跟踪并不能得到可信的三维相机路径。

**限制。** 点身份不是人物身份；没有自动给出“某个人在做某动作”的语义，也不提供完整多人关联系统。遮挡恢复是预测能力，不是保证。快速切镜应分段，长遮挡、低纹理和多个相似目标需单独验证（最后一句为工程验证建议）。

**许可证。** 仓库主体为 CC-BY-NC，部分第三方代码另有 MIT / Apache-2.0；官方 `facebook/cotracker3` 权重页明确标记 CC-BY-NC-4.0。不能用第三方宽松许可证概括整个仓库或权重。[代码许可说明](https://github.com/facebookresearch/co-tracker#license)、[权重许可](https://huggingface.co/facebook/cotracker3)

**Mac 证据。** 官方 `demo.py` 显式按 CUDA → MPS → CPU 选设备，README 明确小任务可用 CPU，推荐 GPU。这是三个候选中最直接的 Apple GPU 代码证据；没有本机运行数据，不能承诺速度、内存或所有算子兼容。[官方设备选择代码](https://github.com/facebookresearch/co-tracker/blob/main/demo.py)

**适用判断。** 技术贴合度高；适合作为非商业研究基准与交互设计参考。若产品涉及商业用途，采用代码/权重前需解决授权，不应默认打包。

## 2. VGGT：相机参数与场景几何

**官方能力。** 输入一张或多张图像，预测相机内外参、深度、点图及点轨迹；相机 head 与密集输出 head 分工明确。官方另提供 COLMAP 格式导出及可选 bundle adjustment。[作者项目页](https://vgg-t.github.io/)、[官方仓库](https://github.com/facebookresearch/vggt)

**具体借鉴（产品推断）。**

- 按镜头抽帧，生成“相机相对位置/方向随时间变化”的可视化证据，辅助用户修改运镜描述。
- 将相机变化与已有主体骨架、相对深度结果对齐，避免只凭画面位移判断主体移动。
- 借鉴相机结果与深度结果分开的输出契约、坐标转换及 COLMAP 导出设计；继续保留现有 VideoDepthAnything 的时间连续深度控制素材路径。

**限制。** 基础输出是几何估计，不能直接等同“电影镜头语言识别”或可直接驱动当前 ComfyUI Workflow 的相机控制。多主体身份、人物动作语义仍然缺失。人物大范围运动、切镜、变焦、少视差等情形的产品可靠性本次未实测，应作为验证集而非声称已支持；不得把单目结果宣称为精确米制相机运动。以上是基于接口能力边界的工程判断。

**许可证必须区分版本。** 当前代码使用 VGGT 自定义许可及可接受使用政策。2025-07-29 官方公告允许商业用途，但**只有 `VGGT-1B-Commercial` 新权重允许商业用途，旧 `VGGT-1B` 仍为非商业**。商业权重需要登录、接受条件并提供联系信息；不是无条件 Apache/MIT。README 示例仍加载旧权重，复制示例会选错版本。[官方版本说明](https://github.com/facebookresearch/vggt#updates)、[代码许可全文](https://github.com/facebookresearch/vggt/blob/main/LICENSE.txt)、[商业权重页](https://huggingface.co/facebook/VGGT-1B-Commercial)

**Mac 证据。** README 表面含 CPU 设备分支，但随后直接调用 CUDA capability / autocast；官方 Gradio `run_model` 在无 CUDA 时主动报错。本次没有查到官方 Mac/MPS 成功验证，不能把 CPU 分支当作开箱即用的证明。[官方演示代码](https://github.com/facebookresearch/vggt/blob/main/demo_gradio.py)

**维护状态。** 官方 2026-05-15 公告修复中间张量冗余、降低多帧内存压力，2026-05-18 公告后续项目；因此不能把旧版内存数字直接用作当前性能结论。本报告不扩大到第四个候选。[更新记录](https://github.com/facebookresearch/vggt#updates)

**适用判断。** 适合作为后续相机轨迹实验，不适合现在承诺“Mac 原生精确运镜复刻”。先验证真实素材的几何稳定性，再决定是否形成用户功能。

## 3. DWPose：多人逐帧全身姿态

本轮在 ViTPose / DWPose 中选择 DWPose，因为它的官方仓库直接提供全身模型、ControlNet 姿态图转换与 ONNX 路径，和项目的控制素材链路更接近；没有把未深入核验的 ViTPose 加入候选或作性能比较。

**官方能力。** 先检测人物框，再对多个框分别预测全身关键点；输出可拆分身体、脸和手，官方示例带 OpenPose 顺序映射。模型覆盖 tiny 到 large。[官方仓库](https://github.com/IDEA-Research/DWPose)、[检测与姿态代码](https://github.com/IDEA-Research/DWPose/blob/main/ControlNet-v1-1-nightly/annotator/dwpose/wholebody.py)、[控制图拆分代码](https://github.com/IDEA-Research/DWPose/blob/main/ControlNet-v1-1-nightly/annotator/dwpose/__init__.py)

**具体借鉴（产品推断）。** 新增价值是同帧多人的手脸细节与可选控制图。可以借鉴关键点低置信度过滤、坐标归一化、身体/手/脸分层导出；这些比单纯把 MediaPipe 换成另一种骨架检测器更有意义。输出与既有 WanAnimate Move 工作流是否兼容仍须按工作流接口验证，不能因支持 ControlNet 就宣称已经兼容 WanAnimate。

**限制。** 已核验的推理代码逐张处理图像，没有跨帧人物 ID、重识别、遮挡后身份续接。多人关键点不是多人时序理解；自动人员排序不稳定也不能当成主体身份。若目标仅是改善已有单人粗骨架，它主要是替换方案，优先级低。

**许可证。** 仓库根代码许可为 Apache-2.0；README 链接的作者权重仓库 `yzd-v/DWPose` 也标记 Apache-2.0，但权重模型卡正文为空。实际管线还依赖人物检测器，不能把 DWPose 的许可标记自动外推到任意替换检测器或第三方封装。[代码许可](https://github.com/IDEA-Research/DWPose/blob/main/LICENSE)、[官方所链接权重页](https://huggingface.co/yzd-v/DWPose)

**Mac 证据。** 主分支示例硬编码 `cuda:0`；官方 ONNX 分支有 CPUExecutionProvider 代码路径，但默认仍是 CUDA，需调整设备设置。官方还公布 OpenCV ONNX 分支。这说明存在避开 MMCV/CUDA 的候选实现路径，**不等于已核验 Mac/Apple GPU 可用**；本轮没有本机性能或兼容性结果。[ONNX 分支代码](https://github.com/IDEA-Research/DWPose/blob/onnx/ControlNet-v1-1-nightly/annotator/dwpose/wholebody.py)

**适用判断。** 仅在需求明确扩到多人或手脸姿态时采用；对于当前最缺的遮挡稳定轨迹与相机运动，它不优先。

## 下一阶段的最小验证建议（未执行）

| 候选 | 应验证的新增价值 | 最小材料 | 不能用来冒充完成的结果 |
| --- | --- | --- | --- |
| CoTracker3 | 人工选点遇到遮挡后能否续接，并正确标可见性 | 道具遮挡、交叉人物、低纹理背景各一段 | 只展示无遮挡轨迹 |
| VGGT | 相机相对轨迹与人工运镜判断是否一致 | 静景运镜、固定机位人物移动、两者同时发生各一段 | 只展示漂亮点云或深度图 |
| DWPose | 多人手脸关键点是否比现有单人路径提供可用新增控制 | 双人交叉、近景手势各一段 | 把每帧多人输出称为稳定身份跟踪 |

三者都不是完整的多主体时序理解方案。就当前产品而言，应先确定要向用户交付的是“可编辑轨迹证据”“相机轨迹”还是“多人手脸控制素材”，再选择组件；目前最明确的功能缺口是第一项。
