# 人物姿态与遮罩 worker

该独立 Python 3.11 环境只使用 Google MediaPipe 的 `pose_landmarker_full.task` 任务模型，在 CPU 上提取 MediaPipe 33 点姿态和人体概率遮罩；它不是生成模型，也不依赖 ComfyUI。依赖锁定为 `mediapipe==0.10.21`：在本机的纵向视频验证中它能安全读取 float32 分割遮罩。0.10.31、0.10.32 和 0.10.35 在读取该遮罩时会终止进程；1.0.1 在本机初始化时终止，因此都未采用。

```sh
backend/person_worker/.venv/bin/python backend/person_worker/run_person.py \
  --check --model backend/person_worker/models/pose_landmarker_full.task

backend/person_worker/.venv/bin/python backend/person_worker/run_person.py \
  --input prepared.mp4 --output ./empty-output \
  --model backend/person_worker/models/pose_landmarker_full.task
```

提取模式要求输出目录已创建且为空，输出 `pose.mp4`、`mask.mp4`、`overlay.mp4`、`landmarks.jsonl`、`quality.json` 与 `metadata.json`。视频会用 FFmpeg 转为 H.264/yuv420p。模型来源、SHA-256、体积及依赖版本在 `models/provenance.json` 和 `requirements.lock` 中固定。
