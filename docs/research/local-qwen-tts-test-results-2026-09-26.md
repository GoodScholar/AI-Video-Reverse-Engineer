# 本地 Qwen3-TTS 性能与试听结果

日期：2026-09-26

状态：下载、权重校验及本机离线生成测试完成；用户认可四种试听音色可作为候选；正式工作台接入及客户交付验收尚未完成。

## 结论

在这台 Apple M5 / 24 GB MacBook Pro 上，Qwen3-TTS 0.6B CustomVoice 8bit 经 MLX-Audio 可以离线生成多种预置音色。三个暖机短句的 RTF 为 0.377–0.387；51.2 秒长文的 RTF 为 0.402，均低于预先约定的实时生成参考标准 RTF ≤1。此次短文和约一分钟长文的速度测试通过。

这不是长时间连续批量任务、并发任务或整套工作台验收。用户已反馈四种试听音色“都还可以”，并要求每种音色展示描述，便于先筛选再试听；实际成片仍需接入时间线、字幕、修订与审核流程。

## 模型、环境与位置

- 主机：Apple M5，10 核 CPU，24 GB 内存，macOS 26.2；MLX 实测可用 Metal GPU，设备名称 Apple M5。
- 模型：[mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit](https://huggingface.co/mlx-community/Qwen3-TTS-12Hz-0.6B-CustomVoice-8bit)，固定修订 `049ef77fe8816b536193c0c25f9a214d17921282`。
- 运行实现：[MLX-Audio](https://github.com/Blaizzy/mlx-audio)，版本 0.5.6；MLX / mlx-metal 0.32.2，Python 3.12.13。uv 锁定并安装 36 个包，`uv pip check` 通过；完整平台包列表与哈希见本机 `pylock.toml`。
- 根目录：`/Users/shen/.cache/aivre/local-qwen-tts-test/`。
- 环境：根目录 `.venv/`；模型：`model/`；音频：`samples/`；记录：`results/`；uv 下载缓存：`/Users/shen/.cache/uv/`。
- 原始结果：`results/benchmark.json`、`results/benchmark.log`；模型来源与文件大小/哈希：`model-manifest.json`。
- 可复跑脚本：`benchmark.py`。使用上游 `load_model()` 与 `model.generate()`，自写代码仅测量和保存输出，未重写模型或声码器。

用户明确批准单个模型和必要依赖、合计下载 3 GB 上限。模型文件清单约 1.974 GB，锁文件所有候选 wheel 合计约 247 MB；未下载其他模型。下载中的 Xet 尝试进度慢，停止后使用 Hugging Face SDK 的 HTTP Range 恢复此前连续前缀；探针返回 206，确认复用约 398.5 MB、续传约 888.3 MB。最终主权重与 speech tokenizer 权重 SHA-256 均通过。校验完成后清理本轮中止的 Xet 临时文件。上述是文件体积与续传记录，不是精确的网络流量账单。

## 测量方式

- 下载校验完成后，测试设置 `HF_HUB_OFFLINE=1`、`TRANSFORMERS_OFFLINE=1`，从绝对本地目录加载；没有云端生成请求。
- 全部采用预置音色，没有参考录音、真人克隆或额外模型。
- 同一短文，语言 Chinese，随机种子 42，temperature 0.9，max_tokens 1600。未使用语速参数；所用上游 Qwen 实现尚未直接支持该参数，不能据此承诺语速控件有效。
- 模型加载耗时 17.308 秒，包括权重和 tokenizer 加载、MLX 同步；不包括 Python 进程启动及库导入。
- 每次生成以实际墙钟计时，等待 MLX 执行完成后结束；音频长度经 ffprobe 核验。RTF = 生成秒数 / 音频秒数。
- 输出均为 24 kHz、单声道 PCM16 WAV；六份文件均有有效音频流、非静音、有限采样值。原始峰值均低于 1，没有通过音量归一化改变试听。
- MLX 峰值来自 `mx.get_peak_memory()`，每次测试前重置；它是 MLX 分配器统计，不等于系统总内存或进程 RSS。RSS 单独记录，不能将二者简单相加。

## 实际结果

| 样本 | 生成秒数 | 音频秒数 | RTF | MLX 峰值 GB |
| --- | ---: | ---: | ---: | ---: |
| Vivian，首次短文 | 10.719 | 14.88 | 0.720 | 6.556 |
| Serena，暖机短文 | 5.439 | 14.08 | 0.386 | 6.326 |
| Uncle Fu，暖机短文 | 7.403 | 19.12 | 0.387 | 5.363 |
| Dylan，暖机短文 | 5.544 | 14.72 | 0.377 | 6.510 |
| Vivian，暖机长文 | 20.604 | 51.20 | 0.402 | 9.133 |
| Vivian，暖机流式短文 | 6.114 | 14.88 | 0.411 | 3.809 |

流式首段返回约 **0.913 秒**；该值是 Python 获得首段音频的时间，未测浏览器/声卡实际开始播放的延迟。此次最大 MLX 分配峰值约 9.13 GB；进程峰值 RSS 约 1.46 GB。加载加首次生成约 28 秒，不含进程启动/导入；后续生成应复用已加载模型，反复启动进程会重新等待加载。

## 音色说明与用户反馈

2026-09-26，用户反馈 Vivian、Serena、Uncle Fu、Dylan “都还可以”，四种保留为候选，尚未指定最终默认音色。每张卡片应在试听按钮前展示性别、音质特点、语言/口音及适用场景，不要求用户逐个试听才能知道区别。

声音特点依据 [Qwen3-TTS 官方音色说明](https://github.com/QwenLM/Qwen3-TTS#custom-voice-generate)；适用场景是本项目的选用建议，不代表模型官方用途或客户成片验收。

| 音色 | 声音特点 | 语言/口音 | 建议场景 |
| --- | --- | --- | --- |
| Vivian | 年轻感女声，明亮，个性鲜明 | 中文 | 新品介绍、促销口播、年轻生活方式 |
| Serena | 年轻感女声，温暖、柔和 | 中文 | 咖啡美食、生活分享、温和品牌介绍 |
| Uncle Fu | 成熟感男声，低沉、圆润 | 中文 | 品牌故事、产品讲解、舒缓叙述 |
| Dylan | 年轻感男声，清晰、自然 | 中文，北京口音 | 本地商家、日常分享、轻松聊天；严格普通话要求先核对口音 |

现有九份实测同文案音频已在 DEV 内容工作台原型中对应展示。固定样音没有按用户当前脚本重新生成；上传自定义音频后标明需要自行核对对应声音。新增音色同样必须有描述与对应真实试听后才能展示。本轮补测的五种只确认生成可用，用户尚未反馈听感。

## 可试听文件

同一句文案：

> 这是一段本地配音试听。清晨，磨好咖啡豆，缓缓注入热水，让香气慢慢展开。你可以比较不同声音的语气、节奏和清晰度，再选择喜欢的音色。

- Vivian：`/Users/shen/.cache/aivre/local-qwen-tts-test/samples/01-vivian.wav`
- Serena：`/Users/shen/.cache/aivre/local-qwen-tts-test/samples/02-serena.wav`
- Uncle Fu：`/Users/shen/.cache/aivre/local-qwen-tts-test/samples/03-uncle-fu.wav`
- Dylan：`/Users/shen/.cache/aivre/local-qwen-tts-test/samples/04-dylan.wav`
- 长文：`/Users/shen/.cache/aivre/local-qwen-tts-test/samples/05-vivian-long.wav`
- 流式重组样本：`/Users/shen/.cache/aivre/local-qwen-tts-test/samples/06-vivian-stream.wav`

另复用已存在的 whisper.cpp 1.9.2 与本地 ggml-small 模型，对 Vivian 短文做一次内容检查，没有下载 ASR 模型。转录能覆盖整段文案，但存在“试听/视听”“清晨/京城”等识别差异；不能据此区分 TTS 发音问题和 ASR 误识别，也不能宣称逐字读音验收通过。原始转录保存在 `results/01-vivian-transcript.txt`，音色和读音最终仍需试听。

## 其余五种音色补测

ask-matt 根据当前范围选择继续已有音色比较原型：复用已下载模型和环境，离线生成其余五种同文案中文 WAV，没有新增下载或安装。原始结果为本机 `results/remaining-voices.json`，日志为 `results/remaining-voices.log`，复跑脚本为 `remaining_voices.py`；原来的 `benchmark.json` 保留。

| 音色 | 官方声音特点 | 原生语言/口音 | 建议场景 |
| --- | --- | --- | --- |
| Eric | 活泼男声，略带沙哑的明亮感 | 中文，四川口音 | 四川本地商家、亲切口播 |
| Ryan | 动感男声，节奏感强 | 英语 | 动感广告、英文商品介绍 |
| Aiden | 阳光男声，中音清晰 | 美式英语 | 轻松介绍、英文生活方式内容 |
| Ono Anna | 俏皮女声，轻盈灵动 | 日语 | 轻快短片、日语内容 |
| Sohee | 温暖女声，情感丰富 | 韩语 | 情感叙述、韩语生活分享 |

声音特点沿用前述官方来源，建议场景为本项目建议。当前五份都生成中文以便同文案比较，不代表原生外语效果已测试；官方推荐使用各音色原生语言以获得更好效果，中文发音与口音需用户试听核对。

| 音色 | 生成秒数 | 音频秒数 | RTF |
| --- | ---: | ---: | ---: |
| eric | 5.588 | 14.56 | 0.384 |
| ryan | 9.038 | 22.00 | 0.411 |
| aiden | 5.863 | 15.52 | 0.378 |
| ono_anna | 6.142 | 17.28 | 0.355 |
| sohee | 6.284 | 18.00 | 0.349 |

五份文件均经 ffprobe 核验为 24 kHz 单声道 PCM WAV，长度与结果记录一致；生成时检查有限采样值及非静音。这些是文件与生成检查，不等于用户已接受五种新增音色或逐字发音通过。

## 接入建议与未完成项

本机测试支持将该模型作为本地配音候选：工作台先显示并试听预置音色，再选择配音，配音资产携带脚本、音色、模型及生成参数修订。运行时复用模型，串行限制资源占用；现有 FFmpeg 按实际音频时长编排并使旧审批失效。

上述为初次本机试跑时的接入建议。后续已按用户批准接入正式工作台，并用公开咖啡素材验证五条真实声画字幕成片、逐条审核、导出和版本失效；详见 [正式接入验收](../../.scratch/aigc-ecommerce-content/local-voice-acceptance.md) 与 README。仍未取得真实客户脚本与素材，不宣称客户读音、营销质量或正式商业交付已验收；长期持续批量和并发压力未验收，当前按串行队列运行。数字人阶段保持暂停。
