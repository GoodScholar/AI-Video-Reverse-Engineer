# 角色动画后端最终限定审查

- **Spec verdict：通过（本轮后端候选功能范围）。**
- **Standards verdict：通过；未发现本次范围内尚未关闭的阻断问题。**
- 范围：`/tmp/aivre-character-backend-fixed.diff`、最新 character_motion/template/media/API 实现，复用此前官方模板/native schema 核对结果。不包含 UI，不代表真实 GPU 生成验收。

## 已确认关闭

| 前次问题 | 最终状态 |
| --- | --- |
| 错误 WanAnimate 图、遗漏提示词/采样/解码链 | 已形成真实 Move 图，提示词进入 CLIPTextEncode，WanAnimate 条件和 latent 接采样，后续解码/封装类型匹配 |
| WanAnimate 四项必填输入缺失 | length、batch_size、continue_motion_max_frames、video_frame_offset 均已显式提供 |
| 固定 SAM2 点、背景替换模式错误 | 已移除 SAM2、背景与角色 mask 输入，采用官方 Move 模式 |
| 明确失败误写 unknown | HTTPException 已绑定并依据 outcomeUnknown 分类；环境缺失及确定拒绝标记 failed，结果不明才 unknown |
| 快照叶子符号链接绕过 | 完整目标路径经 store.path 校验，再以临时文件原子替换；源素材快照期间持有 source_lock 并重读 sourceHash |
| 过滤输出后 URL 索引错位 | 使用 append 前 len(outputs)，与持久化 outputs 索引一致 |
| 任意字节标记视频完成 | 视频后缀过滤、真实 ffprobe 流/尺寸验证；失败清空公开 outputs，下载路由要求 completed |
| 预处理缓存无法自动验证却宣称就绪 | 检查明确区分主模型与人工 DW 缓存；preprocessorVerified=false；preprocessorConfirmed 默认 false，在创建 run 前强制拒绝未确认请求 |

提交 revision 校验保证本次确认与该 revision 的保存地址/方案对应。预处理权重实际缓存由用户在目标 ComfyUI 人工核验，该执行边界已经明确接受；服务端没有声称自动验证缓存。

驱动以 FFmpeg 按保存 fps 转换并限制 frames，短素材补末帧，打包及上传一致引用 driver.mp4。运行保留保存时地址/工作流/源身份；重启后的 submitting 转为 unknown，后端 resolve 与 refresh 路由保留，未误报完成。

首次审查中的保存请求体、项目切换迟到响应、素材变化保存可用性和轮询恢复属于前端/集成项，交由独立 UI 审查关闭，本报告不替其宣称通过。

## 验证说明

本次未重跑测试。主审报告角色相关 13 项检查正在最后确认，此前全后端 1072 passed / 2 skipped；这里只复用所报告结果，不将“正在运行”写成已通过。新差异包含预处理确认门槛、连续输出索引、明确失败分类、符号链接保护及真实媒体归一/验证的回归覆盖。

真实 ComfyUI/GPU 未验证，最终状态继续保持 candidate；本次通过指源码契约与已授权离线/生命周期范围，不宣称模型效果或真实环境兼容性已验收。
