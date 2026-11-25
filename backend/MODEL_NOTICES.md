# 本地模型来源与许可

| 用途 | 模型与固定版本 | 权重校验 | 许可与来源 |
| --- | --- | --- | --- |
| 片段关键帧与中文检索文字的本地图文向量 | `google/siglip2-base-patch16-224` @ `75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2` | `model.safetensors` SHA-256 `612923381c76ec5a9bed335d1c48827e3f2e506ac31b044b63b2031fadee6a0b` | 模型卡明确标注 Apache-2.0，并将图文检索列为预期用途：[固定版本模型卡](https://huggingface.co/google/siglip2-base-patch16-224/tree/75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2) |
| 商用混剪主体构图 | TensorFlow EfficientDet `tfLite/lite0-detection-metadata/1`，由 MediaPipe `0.10.21` 本地加载 | `backend/person_worker/models/efficientdet_lite0.tflite` SHA-256 `2e04c53bfeac0ac2a30c057c7e2a777594ce39baaac35a92f74fb1e8c4fc4e0b` | 精确固定版本从 [Kaggle 官方下载 API](https://www.kaggle.com/api/v1/models/tensorflow/efficientdet/tfLite/lite0-detection-metadata/1/download) 获取；Kaggle 官方 models/list 搜索 `efficientdet` 对此变体标注 `Apache2.0`。仅在本机 `person_worker` 环境运行，不上传素材。 |

权重不随仓库分发，也绝不在运行时隐式下载。复现安装时，人工下载上述固定 URL，校验 SHA-256 后保存为被 Git 忽略的 `backend/person_worker/models/efficientdet_lite0.tflite`。模型只处理项目关键帧与本地查询文字，不接收素材文件名、备注、音频或完整视频。
