# AI YouTube Shorts Generator 源码复用与对齐审计（2026-09-30）

## 审计边界与结论

参考仓库为 [`Anil-matcha/AI-Youtube-Shorts-Generator`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/tree/a57bb938ba50bf9654c2d5ca2af1295163454349)，本次以 `main`/`HEAD` 的 **`a57bb938ba50bf9654c2d5ca2af1295163454349`** 固定；当前项目检查点为 `5bd75e3a1d08c26474d531935d58fa9f9fbbfdea` 加审计时工作树。只读检查两边实际源码、参考仓库根 LICENSE、依赖清单和本项目相关规格/测试；未运行参考服务、调用 MuAPI/OpenAI/Gemini、安装模型或做视频质量验收。本次只新增本文件。工作树另有未提交改动，因此当前项目判断限于所见源码，不把测试文件存在写成测试通过。

**结论：不能宣称“源码深度复用、代码实现 100% 对齐”。** 识别仓库只解决了参考对象不明的问题。当前项目未见此仓库源码的引入、来源声明或 `shorts_generator.generate_shorts` 调用；已有的场景索引、视觉检索、脚本驱动混剪、Whisper 字幕与视频渲染是不同契约的相邻能力。参考仓库自身也不能被概括为“完整本地离线、稳定人脸身份跟踪、任意主体平滑居中、商用品质批量生产”：默认模式依赖外部 MuAPI，本地选段仍调用 OpenAI/Gemini，裁切仅按最大 Haar 正脸框追随，质量与故障场景没有附带自动化验收证据。依据本项目 [`AGENTS.md`](../../AGENTS.md) 的“源码复用优先”“商用交付与对齐验收”，既不能将源码已读等同于已复用，也不能将局部能力等同于约定范围差异清零。

## 上游真实调用链

入口 [`main.py`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/main.py#L21-L45) 的 `--mode` 默认 `api`，`--num-clips` 默认 3。实际编排在 [`pipeline.py`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/shorts_generator/pipeline.py#L17-L87)：下载或取得来源 → 转录 → `get_highlights` → 按分数取前 N → 逐条裁切 → 返回 transcript、全部候选、shorts。单条裁切失败被记录为 `clip_url: null`，但整体仍可返回；所以请求 N 不保证生成 N 个成功视频。仓库没有项目素材库、审核、版本化时间线或交付状态机。

| 能力 | 固定提交中的实现与边界 |
| --- | --- |
| 下载与转录 | 默认模式调用 MuAPI [`youtube-download`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/shorts_generator/downloader.py#L26-L36)、[`openai-whisper`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/shorts_generator/transcriber.py#L49-L76)，仅含 HTTP 提交/轮询客户端 [`muapi.py`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/shorts_generator/muapi.py#L26-L97)，服务端算法不在仓库。`local` 模式支持本地路径或 yt-dlp 下载 [`local/downloader.py`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/shorts_generator/local/downloader.py#L61-L135)，使用 `faster-whisper` 转录并按源文件 basename/mtime 缓存 SRT [`local/transcriber.py`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/shorts_generator/local/transcriber.py#L98-L162)；缓存键不包含语言或模型版本。 |
| 长视频与精彩片段 | [`highlights.py`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/shorts_generator/highlights.py#L23-L68) 对大于等于 1800 秒的**转录文本**分块，名义块长 1200 秒、重叠 60 秒；内容分类、钩子/情绪等“病毒传播”评分由提示词要求 LLM 输出，并非仓库自带训练好的评分模型。默认 LLM 经 MuAPI `gpt-5-mini`，本地模式调用远端 OpenAI 或 Gemini [`local/llm.py`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/shorts_generator/local/llm.py#L11-L61)。解析后约束时间和分数、无效响应最多尝试三次；候选按分数降序去重，若与已保留片段重叠超过**当前候选时长的 50%**则删除 [`highlights.py`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/shorts_generator/highlights.py#L127-L160) [`highlights.py`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/shorts_generator/highlights.py#L251-L306)。 |
| 9:16、人脸与批量 | 默认模式逐片调用 MuAPI `autocrop`，其检测/追踪算法不可由本仓库源码验证 [`clipper.py`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/shorts_generator/clipper.py#L12-L41)。本地模式 FFmpeg 截段，OpenCV Haar 正脸检测逐帧取**最大人脸**，以 `0.15` 指数移动中心平滑，未检测到脸时延用旧中心或画面中心，再裁切、写视频、复用源音频 [`local/clipper.py`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/shorts_generator/local/clipper.py#L25-L122)。它顺序输出 `short_01.mp4` 等多个短片，连续运行同一输出目录会复用名称 [`local/clipper.py`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/shorts_generator/local/clipper.py#L143-L167)。README 的多 URL 批处理是外部 `xargs` 使用建议，并非内置项目级批次队列 [`README.md`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/README.md#L178-L202)。 |

**需先验证的上游缺陷/宣传边界。** 长视频路径的 [`build_transcript_text`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/shorts_generator/highlights.py#L174-L197) 送给 LLM 的段时间戳仍是全片绝对秒数，但 [`get_highlights`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/shorts_generator/highlights.py#L287-L300) 按块长裁限结果、随后又加 `_offset`。若 LLM 返回提示中的绝对时码，非首块的候选会被错误裁限或丢失；这是从源码可推导的坐标契约风险，尚未做外部模型实测。去重只计算当前候选时长的重叠比例，不保证任何两条保留片段都完全不重叠。Haar 最大脸不维护跨帧人物身份、不判断当前说话人、不识别商品或非人主体；“平滑”仅适用于该中心更新。参考仓库文件树未包含测试目录/测试文件，不能据此声称这些边界已经过回归验证。其 [`README.md`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/README.md#L192-L201) 将 local 称作 offline，但 [`pipeline.py`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/shorts_generator/pipeline.py#L3-L7) 与 `local/llm.py` 明示选段 LLM 仍远端，因此“全离线”不成立。

## 许可证、直接依赖与来源风险

根 [`LICENSE`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/LICENSE) 为 MIT、版权人 Anil Chandra Naidu Matcha（2026）；复制实质代码时须保留许可与版权文本。但 [`highlights.py` 文件头](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/shorts_generator/highlights.py#L1-L11) 自述逻辑移植自 ViralVadoo 的 `transcript_analysis/highlight_generator.py`，且 [`README` 安装步骤](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/README.md#L215-L220) 给出另一个所有者 `SamurAIGPT` 的克隆地址。两处是进一步核实代码来源与授权链的信号，不足以单独判定侵权；深度复制该模块前须核原项目、原提交及许可，不能仅凭当前根 MIT 结案。

直接依赖在 [`requirements.txt`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/requirements.txt) 与 [`requirements-local.txt`](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/a57bb938ba50bf9654c2d5ca2af1295163454349/requirements-local.txt)：requests、python-dotenv；本地另需 yt-dlp、faster-whisper、openai、google-genai、opencv-python；运行时还需 FFmpeg，可选 CUDA/PyTorch。清单均为最低版本约束，没有锁定可复现版本。Faster-Whisper [代码为 MIT](https://github.com/SYSTRAN/faster-whisper/blob/master/LICENSE)，OpenCV [许可证](https://github.com/opencv/opencv/blob/4.x/LICENSE)、yt-dlp [授权说明](https://github.com/yt-dlp/yt-dlp/blob/master/README.md#licensing)、[FFmpeg 构建的许可条件](https://ffmpeg.org/legal.html)须按实际分发物核对；Haar XML、Whisper 模型权重与取得渠道也须单独记录。MuAPI/OpenAI/Gemini 的服务条款、费用和媒体外发范围不受此 MIT 覆盖。yt-dlp 可下载不代表对目标视频有剪辑、再分发权。本次未引入或分发任何上述代码、模型、视频。

## 与当前项目的逐项差异

本项目当前已有 [`source_segment_index.py`](../../backend/app/source_segment_index.py#L87) 的 PySceneDetect 项目素材场景区间、关键帧和可恢复索引，[`segment_retrieval.py`](../../backend/app/segment_retrieval.py#L176) 的 SigLIP2 视觉检索与可披露豆包复排，以及 [`montage_planner.py`](../../backend/app/montage_planner.py#L214) 的脚本段落→候选素材→混剪提案；API 已在 [`main.py`](../../backend/app/main.py#L2525) 接入，前端有 [`SourceSegmentIndexPanel.tsx`](../../frontend/src/SourceSegmentIndexPanel.tsx#L38)、[`SegmentRetrievalPanel.tsx`](../../frontend/src/SegmentRetrievalPanel.tsx#L44) 和 [`BatchEditor.tsx`](../../frontend/src/BatchEditor.tsx#L736)。这些是当前项目自有/其他参考适配的源码能力，不能算作 Anil 仓库的源码复用。现有 [`run_tool.py`](../../backend/toolkit_worker/run_tool.py#L30) 通过 whisper.cpp 生成字幕，非该仓库 `faster-whisper` 转录→高光评分调用链；真实模型技术链路曾有[项目内演示证据](../../.scratch/batch-short-video-editing/public-demo-acceptance.md)，但不能证明本条参考链路或跨素材质量。当前 [`timeline_render.py`](../../backend/app/timeline_render.py#L388) 视频滤镜是 `scale=...decrease,pad=...black`，并非该仓库的最大脸跟随裁满。渲染重构图票据仍写明[未完成验收](../../.scratch/commercial-ai-montage/issues/05-render-reframes-and-graphic-templates.md)。

| 粘贴文本中的主张 | 本次判定 | 当前项目尚需闭合的差异 |
| --- | --- | --- |
| Whisper 转录 | 上游两模式均有接口；默认为 MuAPI 托管，本地为 `faster-whisper`。 | 项目已有 whisper.cpp 字幕，尚无“同一长视频转录直接驱动高光选段”的稳定来源/时码/模型版本链。 |
| 长视频分块与精彩片段评分 | 上游有文本分块、LLM 评分提示和分数排序；长视频时码风险待修证。 | 场景索引与脚本视觉检索不能代替基于整段语音内容的高光候选；需把候选、时间范围、证据与模型运行绑定项目素材。 |
| 重叠去重 | 上游有基于候选自身时长的非对称 50% 抑制。 | 项目规划器已防止同源范围重复分配，但业务是脚本驱动跨素材编排；若承诺对齐上游，需为转录高光候选明确算法与差异测试。 |
| 人脸检测/跟踪、横屏转 9:16、主体平滑居中 | 上游本地仅最大 Haar 正脸 + EMA 裁切；托管自动裁切内部不可见。 | 项目当前成片滤镜仍可能加黑边；无同形的人脸轨迹/裁剪路径和主体失效人工覆盖。商品等非脸主体不能靠此上游实现直接满足。 |
| 批量多个短视频 | 上游对**单个来源**按分数顺序裁 N 条；多来源是外部 `xargs`。 | 项目已有多变体、审核、导出，但未建立“单条长视频高光→N 条已入库短片”的自动服务闭环；两种批量语义不能混用。 |
| 深度模块复用、100% 对齐 | 上游 MIT 根许可允许有条件复制；本次未见当前仓库引入此仓库源码或留存其来源。 | 需先决定适用范围，修上游边界风险，实施适配并完成逐项真实验收；目前差异非零。 |

本项目[商用自动混剪规格](../../.scratch/commercial-ai-montage/spec.md) 的主线是已授权素材与已确认脚本驱动的商业编排；“长视频自身挖高光”可作为相邻输入能力接入，但不能把上游病毒传播提示词当成商业脚本与商品画面匹配的同义功能，也不能用参考仓库的简单人脸裁切证明商品主体安全区达标。

## “零差异”宣称的验收门槛

若仅宣称“对该固定提交约定范围完成对齐”，须先书面冻结 API 模式、本地模式及商业项目适配的具体范围，并对每项给出源码映射/复制记录、原始来源与 MIT 通知；上游 ViralVadoo 来源、所有实际依赖/权重及外发授权有明确结论。对入选范围至少完成以下证据：

1. 用同一批合法来源视频实际验证输入→转录→长视频分块→候选解析/评分→重叠去重→Top N→逐片裁切→项目素材入库/预览/审核/导出的完整时码、音画、版本关系；包括 30 分钟边界及第二、第三块，修复或明确排除上游绝对/相对时码缺陷。
2. 对无语音、无有效高光、模型无效响应/超时、单片裁切失败、下载中断、输出重名、缓存过期、取消与重试提供可观察的预期行为；成功数量、失败数量与保存结果一致，不以返回 JSON 代替真实 MP4 证据。
3. 对单人/多人、正脸消失、交叉说话、场景切换、快速移动、横竖比例及商品非脸主体做逐帧构图样例；证明目标尺寸、无黑边、主体/字幕安全区、声音同步与人工覆盖。若使用 MuAPI 裁切，须以真实服务输出和服务条款举证，不能说其服务端源码已复用。
4. 本项目独立验证已保存素材 ID、候选来源时段/版本、提案采用、冻结预览、审核失效及批准导出；与[商用自动混剪规格](../../.scratch/commercial-ai-montage/spec.md) 的金标镜头保留率、人工替换率及成片效果目标分别验收。对比矩阵逐项记“通过/失败/明确不适用”及可复现证据，约定范围未闭合差异为零后才可用该范围的对齐表述。

在上述门槛前，准确表述应是：**已明确并读过 Anil 参考仓库固定版本；当前项目有部分相邻基础能力；该仓库源码复用、长视频高光生成链和竖屏人脸跟随成片尚未证明完成，更不能称 100% 对齐或可商用。**
