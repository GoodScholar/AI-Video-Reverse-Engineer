# 本机 Qwen3-TTS 试跑准备检查

日期：2026-09-26

状态：用户已明确同意下载与测试；下载、校验及离线性能测试已完成。用户已初步认可四种试听候选，并要求每种音色附描述；正式接入尚未完成。实际结果见 [试跑结果](local-qwen-tts-test-results-2026-09-26.md)。

## 下载批准与路径

用户在“运行库用 uv、模型文件用 Hugging Face 下载工具”的说明之后回复“确认”，批准此前列明的单个 0.6B 8bit 模型、必要运行库及合计 3 GB 上限。用户随后要求明确提供目录，已回复如下路径：

- 根目录：`/Users/shen/.cache/aivre/local-qwen-tts-test/`
- 运行环境：根目录下 `.venv/`
- 模型：根目录下 `model/`
- 试听：根目录下 `samples/`
- 性能结果：根目录下 `results/`
- uv 依赖缓存：`/Users/shen/.cache/uv/`

uv 已解析锁定 36 个包，锁文件为根目录 `pylock.toml`。按锁文件把所有候选 wheel 大小合计作保守估算为 247.4 MB，连同模型约 2.221 GB，低于批准上限；实际安装只选择平台对应 wheel，复用已有缓存时会更少。缺少任何额外模型或超出剩余预算的资源时重新征求同意。

## 用户约束

用户要求先测试本机运行速度与音色；任何所需下载必须经过其同意。只读取已安装环境和远端元数据不代表下载批准。未收到明确同意前，不执行包安装、模型下载或会隐式拉取模型的命令。

## 本机检查结果

- MacBook Pro，Apple M5，10 核 CPU，24 GB 内存。
- 工作目录所在磁盘剩余约 487 GiB；检查时系统 memory_pressure 显示可用百分比 52%。这些是瞬时资源信息，不是推理性能证据。
- 已有 uv、FFmpeg、ffprobe，以及 uv 管理的 Python 3.12/3.13，可建立独立测试环境，无须再下载 Python。
- 项目后端环境 Python 3.13.13，未安装 mlx、mlx-audio、qwen-tts、torch、transformers、soundfile 或 huggingface-hub。
- 检查 ~/.cache/uv、~/.cache/huggingface、~/.cache/aivre、~/.local/share、~/Library/Caches 未找到名称包含 qwen3-tts、qwen_tts、qwen-tts、mlx-audio 或 mlx_audio 的资源；Hugging Face 缓存仅发现现有 Whisper 模型。该检查不宣称全盘不存在任意改名的模型。

## 已批准的最小方案

采用 [MLX-Audio](https://github.com/Blaizzy/mlx-audio) 的 Apple Silicon 实现，先测试一个预置音色模型，不使用克隆或参考音频输入。

- 模型：[mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit)。固定修订 `049ef77fe8816b536193c0c25f9a214d17921282`。
- Hugging Face 文件元数据合计 1,973,575,388 字节，约 1.97 GB / 1.84 GiB，包括约 1.287 GB 主权重、约 0.682 GB speech tokenizer，以及配置与文本词表。只是读取清单，未取得权重。
- 候选运行库：PyPI `mlx-audio==0.5.6`，基础依赖含 MLX、NumPy、SciPy、Transformers、Hugging Face Hub 等；MLX 需要配套 mlx-metal。最终依赖由独立环境解析并锁定，不能升级项目环境。
- 依赖下载暂按数百 MB 估算，尚未完整解析其平台依赖闭包。向用户申请的合计下载上限为 **3 GB**；若预计超出、需要其他模型或额外工具，重新说明并请求同意。
- 拟保存到 ~/.cache/aivre/local-qwen-tts-test/，使用现有 Python 建立独立环境。试听音频与性能记录同目录保留，正式项目依赖不变。

8bit 模型和框架分别声明 Apache 2.0 / MIT；当前只做技术与试听验证，不据此宣称音色已被用户接受或正式客户交付权利已验收。

## 批准后测试步骤

1. 在批准范围内建立环境、获取固定模型；随后启用离线模式，禁止测试过程自动下载第二个模型。
2. 用同一句中性中文测试文案生成至少四种支持的预置音色，覆盖男女声，输出可直接播放的 WAV。具体音色 ID 以模型配置与加载结果为准。
3. 记录首次加载、首条冷启动和后续生成耗时、音频真实长度、峰值内存及错误；用 ffprobe 确认每个音频文件有有效声音流。
4. 生成一段约 30–60 秒的较长文案，记录实时率 RTF = 生成秒数 / 音频秒数。暖机后 RTF ≤1 作为“生成速度达到实时”的参考，同时明确批量任务的总等待时间；不以框架广告推断流畅。
5. 展示样本由用户判断音色是否满意；速度通过不等于音质通过。若失败，在现有批准资源内诊断，不自动改用云端付费服务或拉取其他模型。

实际执行记录：模型与环境已经就绪，六份音频生成完成；本次短文与 51.2 秒长文的生成速度达到预定参考标准。不能据此宣称用户声音满意或持续批量任务通过，详见上述试跑结果。
