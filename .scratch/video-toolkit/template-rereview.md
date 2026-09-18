# Wan 工作流二次独立审查

结论：**仍不通过**。此次只审查 `character_motion_templates.py` 的图及其真实依赖契约，没有复查尚在修改的 UI/生命周期，也没有重跑既有测试。

相比首次审查，模型加载、文本条件、采样、潜空间裁剪、解码和视频封装链路已经补齐，原 CONDITIONING 直连 VIDEO 的错误已消失；但以下问题仍阻断真实使用。

## 1. [P1] WanAnimateToVideo 缺少四个必填输入，frames 参数没有进入图

位置：`backend/app/character_motion_templates.py` 的节点 `62`。

当前只填写 width/height，遗漏 `length`、`batch_size`、`continue_motion_max_frames`、`video_frame_offset`。真实 native schema 将四者声明为非 optional 输入，执行方法也没有 Python 默认值。界面中的 `frames` 只参与合法性判断，不决定生成长度。因此即使安装完整环境，提交仍会缺少必填输入，不能靠 UI widget 默认值修复 API 图。

建议显式绑定 `length=frames, batch_size=1, continue_motion_max_frames=5, video_frame_offset=0`，并验证全部节点必填字段。依据：[官方 native 节点源码](https://github.com/Comfy-Org/ComfyUI/blob/master/comfy_extras/nodes_wan.py) 的 WanAnimateToVideo.define_schema/execute；固定模板节点 62 的 widgets_values 也包含这些数值。

## 2. [P1] SAM2 下载节点与现有检查组合会在未安装模型时自动下载

位置：节点 `108`（DownloadAndLoadSAM2Model）及其现有 `ComfyUIClient.check` 依赖判断。

固定扩展版本的 INPUT_TYPES 静态列举 `sam2_hiera_base_plus.safetensors`，不代表本地存在该权重。现有 check 在 object_info 的任意字符串中搜索文件名，所以仅安装扩展、没有 SAM2 权重时仍会把该依赖判为就绪。该节点运行时检测缺文件后会执行 Hugging Face snapshot_download。本任务明确禁止自动下载权重，当前环境检查无法守住该约束。DW 的 ONNX/TorchScript 权重也没有纳入当前仅识别 .safetensors 的模型检查。

建议在不能证实模型文件已安装时阻止含下载节点的运行；不要将枚举值等同于安装状态。若选择官方 Move 模式，可移除 SAM2/背景链，降低可验证接口范围。

依据：[模板固定 SAM2 扩展源码](https://github.com/kijai/ComfyUI-segment-anything-2/blob/c59676b008a76237002926f684d0ca3a9b29ac54/nodes.py) 的 DownloadAndLoadSAM2Model.INPUT_TYPES/loadmodel，已只读核对，未运行或下载模型。

## 3. [P2] 固定 SAM2 点位替代了必要的主体选择，并把所有任务锁定为背景替换

位置：节点 `107` 的 coordinates_positive，以及节点 `62` 的 background_video/character_mask。

图将正点硬编码 `(256,256)`，没有源图坐标选择或检测依据。主体位于画面右侧/左侧时，该点可能落在背景；当允许尺寸为 256 时，还可能落在图像边界外。模板的 PointsEditor 原本允许用户标记目标，常量只是示例初始值，不能作为任意视频的主体分割方案。

同时始终接入 background_video/character_mask，固定的是模板 Mix（character replace），并非 Move（pose transfer）。官方快照 MarkdownNote 节点 227 明确说明，Move 必须断开这两项，不能 bypass。当前界面没有模式/分割点输入，故无法正确支持普通动作迁移或可靠替换任意主体。

建议本轮明确选择一个可验证模式：若选 Move，断开两条背景输入并去掉不需要的分割节点；若选 Mix，保存用户主体点/掩码并将其正确映射到预处理后的坐标。

## 范围与剩余不确定性

- 只读比对真实官方快照及上游 native/扩展 schema；没有使用项目 mock 来证明图有效。
- 当前主干 native 源码用于补足模板 UI JSON 未展开的 widget 必填字段；真正部署仍应核对目标 ComfyUI object_info 的输入 schema。
- 固定快照与 API 图现在具有基本相同的采样输出结构；以上修复后仍只能称 candidate，不能宣称经过 GPU 验证。
