# 隔离的 Video Depth Anything worker

本 worker 只支持 Python 3.11，且必须在应用进程之外手工安装。运行时还需要可用的 Git，并要求上游 checkout 的 HEAD 固定为指定 commit、工作树（含暂存与未跟踪文件）干净。应用不会创建虚拟环境、安装依赖、下载源码或下载 checkpoint。

```bash
cd backend/depth_worker
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.lock
git clone https://github.com/DepthAnything/Video-Depth-Anything.git vendor/Video-Depth-Anything
cd vendor/Video-Depth-Anything
git checkout --detach 4f5ae23172ba60fd7bc11ef671cca678842c7072
git rev-parse HEAD
```

手工下载官方 Relative Small checkpoint `video_depth_anything_vits.pth`，放入 `backend/depth_worker/checkpoints/`，再验证它（期望摘要为 `13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609`）：

```bash
mkdir -p checkpoints
curl -L https://huggingface.co/depth-anything/Video-Depth-Anything-Small/resolve/main/video_depth_anything_vits.pth \
  -o checkpoints/video_depth_anything_vits.pth
shasum -a 256 checkpoints/video_depth_anything_vits.pth
```

worker 使用 OpenCV 读取和抽帧；不导入上游 `utils/dc_utils.py`。因此锁文件刻意不含 `decord`；可移植路径也不含 `xformers`，使用上游 PyTorch attention 回退。

只在已完成上述人工准备后执行：

```bash
.venv/bin/python run_depth.py --input input.mp4 --output /tmp/depth-output \
  --checkpoint checkpoints/video_depth_anything_vits.pth \
  --upstream-root vendor/Video-Depth-Anything --device cpu \
  --target-fps 8 --input-size 350 --max-res 640
```
