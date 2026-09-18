# Wan Move 图与媒体路径限定复查

结论：**图的前三项结构问题已修复；仍有 2 项需要处理。** 本次只读审查最新模板、character_motion_media.py、API 输入快照及输出下载路径；未审 UI、未重跑测试、未运行模型。

## 前次问题关闭情况

1. WanAnimateToVideo 已显式提供 length=frames、batch_size、continue_motion_max_frames、video_frame_offset，缺必填输入问题关闭。
2. SAM2 及其下载节点已删除，SAM2 自动下载路径关闭。
3. 背景/character_mask 输入和固定主体点已移除，符合官方快照说明的 Move 模式，固定 Mix/错误点位问题关闭。

目前模型→条件→WanAnimate→KSampler→TrimVideoLatent→VAEDecode→ImageFromBatch→CreateVideo→SaveVideo 连接类型合理。两个 DW 节点分别生成脸部与身体/手部条件，未发现新的确定性节点必填/连接错误。仍未经真实 GPU 验证。

## 仍存在的问题

### [P1] DW 预处理模型缺失仍可能自动下载，检查会误报环境完整

模板节点 100/101 使用 yolox_l.onnx 与 dw-ll_ucoco_384_bs5.torchscript.pt；现有 ComfyUIClient.check 只提取 .safetensors，完全没有验证这两个文件存在。真实扩展 INPUT_TYPES 也是固定选项列表，不能证明磁盘已有模型；estimate_pose 调用 from_pretrained，后者对两者调用 custom_hf_download。仅安装自定义节点而未安装 DW 模型时，环境仍可能 ready，显式生成会触发额外模型下载，继续违反任务的禁止自动下载权重约束。

建议：对无法验证预处理资产的环境保持 not ready，并明确提示安装/核验方法；或者仅在能确认相应缓存文件存在的受控执行配置下开放生成。不能把模型枚举选项当作安装证据。

证据：[DW 节点封装源码](https://github.com/Fannovel16/comfyui_controlnet_aux/blob/main/node_wrappers/dwpose.py)、[DwposeDetector.from_pretrained](https://github.com/Fannovel16/comfyui_controlnet_aux/blob/main/src/custom_controlnet_aux/dwpose/__init__.py)。仅读取源码，没有下载模型。

### [P2] 输出过滤后仍使用原始索引构造 URL，会导致已完成视频 404

位置：`backend/app/character_motion_api.py` refresh_run 输出循环。

当前对 result.outputs 枚举后跳过非视频，再将剩余项压缩保存到 run.outputs，但 URL 仍使用原始 enumerate index。例如历史输出为 preview.png、final.mp4，保存后只有 outputs[0]，其 URL 却为 /output/1，output_file 检查 index >= len(outputs) 返回 404。这是新增过滤逻辑的索引错位，与文件验证本身无关。

建议 URL 使用当前输出列表长度（append 前 len(outputs)），文件名仍可保留原始索引。

## 媒体路径观察

- 驱动统一为 MP4，按保存 fps/frames 截取，使用 tpad 对短素材重复末帧；图和导出资产都指向 driver.mp4，路径一致。
- 临时文件写入后原子替换，完整快照叶子路径经过 store.path；原有叶子 symlink 缺口已补齐。
- 完成输出先写临时文件、ffprobe 确认存在有效尺寸视频流再公开；相较任意字节标记完成已有改进。
- 以上媒体行为为代码审查结论，不宣称实际运行验证通过。

## 后续执行边界决策

主审提出显式 `preprocessorConfirmed`（默认 false）作为服务端提交门槛，并说明 DWPose 缓存没有自动验证。该方案可接受，不要求通过 HTTP 无法取得的私有缓存自动证明：确认应绑定当前 ComfyUI 地址/保存 revision，地址或预处理模型改变后失效，文案明确两个所需缓存文件。按此实现后，第 1 项可关闭；用户错误声明不是本轮需要额外推演的风险。此处只是对具体方案的评审，尚未验证其代码已落地。
