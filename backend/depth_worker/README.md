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

应用先用 FFmpeg 按真实时间戳规范化为固定帧率并处理旋转，worker 使用 OpenCV 分段读取规范化视频；不导入上游 `utils/dc_utils.py`。因此锁文件刻意不含 `decord`；可移植路径也不含 `xformers`，使用上游 PyTorch attention 回退。

只在已完成上述人工准备后执行：

```bash
ffmpeg -i input.mp4 -vf 'fps=8,scale=640:640:force_original_aspect_ratio=decrease:force_divisible_by=2' -an -c:v libx264 -pix_fmt yuv420p prepared.mp4
mkdir /tmp/depth-output
.venv/bin/python run_depth.py --input prepared.mp4 --output /tmp/depth-output \
  --checkpoint checkpoints/video_depth_anything_vits.pth \
  --upstream-root vendor/Video-Depth-Anything --device cpu \
  --target-fps 8 --input-size 350 --max-res 640 --output-short-side 480 --backbone-microbatch 2
```

应用提供短边 480 / 720 输出档位，分别使用 350/640 与 518/1280 的模型输入/解码长边配置。输出保留比例与偶数尺寸，低分输入放大不等于新增细节。worker 每段最多读取 64 帧，保留 8 帧重叠用于相对深度对齐，再使用全片采样的统一 P2/P98 归一化。中间结果落盘为灰度原始帧，应用按需映射进行质量检查和编码。

为限制注意力计算峰值，DINO 空间特征在 MPS 上逐帧计算，在 CPU/CUDA 上每两帧计算，再按原顺序拼回；32 帧时序模型保持不变。MPS 导入 Torch 前默认启用不支持算子的 CPU fallback，既有显式环境设置保持优先。运行档位、空间微批大小、兼容路径以及推理与输出尺寸写入 metadata。此配置在真实设备上仍需结合素材检查质量和耗时。

MPS 推理将分配器上限设为 Metal 推荐工作集的 50%，每段结束（包括失败退出）后同步并释放空闲缓存，降低长片持续占用统一内存的风险。该比例记录为 `mpsMemoryFraction`，不是整个进程的物理内存上限；720P 仍有较高内存需求，实际不足时任务会报错。CPU/CUDA 不应用此限制。
