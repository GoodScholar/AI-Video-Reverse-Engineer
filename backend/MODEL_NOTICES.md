# 本地模型来源与许可

| 用途 | 模型与固定版本 | 权重校验 | 许可与来源 |
| --- | --- | --- | --- |
| 片段关键帧与中文检索文字的本地图文向量 | `google/siglip2-base-patch16-224` @ `75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2` | `model.safetensors` SHA-256 `612923381c76ec5a9bed335d1c48827e3f2e506ac31b044b63b2031fadee6a0b` | 模型卡明确标注 Apache-2.0，并将图文检索列为预期用途：[固定版本模型卡](https://huggingface.co/google/siglip2-base-patch16-224/tree/75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2) |

权重由运行环境首次使用时直接下载到被 Git 忽略的 `backend/data/models/`，不随本仓库分发。模型只处理项目关键帧与本地查询文字，不接收素材文件名、备注、音频或完整视频。
