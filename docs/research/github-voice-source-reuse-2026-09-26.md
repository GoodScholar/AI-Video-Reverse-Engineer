# GitHub 多音色试听与脚本配音源码复用核验

核验日期：2026-09-26。仅只读检查官方 GitHub 固定提交源码和供应商官方文档；未运行第三方项目、安装依赖、发送模型请求或验证声音质量。本报告服务于现有 FastAPI/Python、React 18/Vite 6/TypeScript、FFmpeg 时间线与字幕审核导出链路。用户要求优先搬匹配源码，使用标准音色、每次 AI 外发前确认；已知 Fal MiniMax 测试返回 `403 balance_exhausted`，本报告不授权重试付费请求或切换供应商。

## 结论

**首选拆取 MoneyPrinterTurbo 的服务端源码和试听缓存逻辑，保留本项目的界面、确认机制和 FFmpeg 时间线。** 其 Python 模块同时覆盖音色目录、音频合成、实长校验、短样例与全文试听、参数变化失效和全文试听结果复用，匹配程度最高。MiniMax 调用是供应商直连接口，不能拿 Fal Key 或 Fal 余额代用；迁入源码本身不表示启用直连服务。[配音源码](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/app/services/voice.py)、[试听源码](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/webui/Main.py#L5597-L6243)。

NarratoAI 可补充分镜批量配音结果结构；OpenShorts 可参考同文案多音色对比，但两者都不能直接解决标准音色的商业授权与精确字幕问题。**MIT 许可允许按其条件复用代码，不授予云端音色、第三方声音样本、模型权重或生成音频的商业权利。** 声音的商业使用必须按实际服务、账号套餐与合同核验。[MoneyPrinterTurbo MIT](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/LICENSE)、[NarratoAI MIT](https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/LICENSE)、[OpenShorts MIT](https://github.com/jnMetaCode/openshorts/blob/48df3591750ea9d0d08f58fb9ce154605e61dfa2/LICENSE)。

## 固定版本与许可证范围

| 项目 | 固定完整 SHA | 核验的许可与范围 |
| --- | --- | --- |
| harry0703/MoneyPrinterTurbo | `ad5496f1b729d1d7e361dd972015d26c08b0e052`，2026-09-24 提交 | 根 MIT，版权 Harry 2024；固定提交的完整文件树只发现根 `LICENSE`，目标 `app/services`、`webui` 没有发现另设许可文件。源码迁入须保留版权及许可文本。[提交](https://github.com/harry0703/MoneyPrinterTurbo/commit/ad5496f1b729d1d7e361dd972015d26c08b0e052)、[许可](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/LICENSE) |
| linyqh/NarratoAI | `9fa69e022d4add41205ee385207561df8796b3f1`，2026-09-17 提交 | 根 MIT，版权 linyq 2024；固定提交的完整文件树只发现根 `LICENSE`，目标 `app/services`、`webui/components` 没有发现另设许可文件。同样保留版权及许可。[提交](https://github.com/linyqh/NarratoAI/commit/9fa69e022d4add41205ee385207561df8796b3f1)、[许可](https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/LICENSE) |
| jnMetaCode/openshorts（开片） | `48df3591750ea9d0d08f58fb9ce154605e61dfa2` | 根 MIT，版权 OpenShorts contributors 2026；只核验所列三个源码文件、根许可和包定义，不宣称全仓附带素材或依赖许可证已审计。[提交](https://github.com/jnMetaCode/openshorts/commit/48df3591750ea9d0d08f58fb9ce154605e61dfa2)、[许可](https://github.com/jnMetaCode/openshorts/blob/48df3591750ea9d0d08f58fb9ce154605e61dfa2/LICENSE) |

以上“未发现子目录许可”是目录树核验结果，不是所有第三方依赖、外部服务或资源的授权保证。

## 1. MoneyPrinterTurbo：优先迁入的具体模块

| 文件与关键函数 | 实际实现 | 迁入边界 |
| --- | --- | --- |
| `app/services/voice.py`：`get_minimax_voice_catalog()`，1873 行；`get_all_azure_voices()`，354 行；`app/services/data/azure_voices.json` | MiniMax POST `get_voice` 后统一成 `voice_id/voice_name/voice_type`、去重；Azure 从本地 JSON 加载并按 locale 筛选。[目录函数](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/app/services/voice.py#L1873-L1950)、[Azure 数据](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/app/services/data/azure_voices.json) | MiniMax 函数默认 `all`，上游 UI 也请求 `all`；本项目须限定 `system` 并验证返回类别，禁止任意克隆 ID、声音样本上传与克隆入口。目录查询也必须按用户当前外发确认要求执行。 |
| `app/services/voice.py`：`minimax_tts()`，1987 行；`_write_validated_minimax_audio()`，1952 行 | 非流式 HTTP 合成，校验 HTTP/业务状态、hex 数据与尺寸；先写同目录临时文件，以 MoviePy 解码并校验正有限时长，最后原子替换。[合成与落盘](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/app/services/voice.py#L1952-L2050) | 可保留请求/结果解析与原子写入结构。供应商配置应接本项目服务对象，不迁入上游全局 Key 自动继承/地域推断。上游循环 3 次会对 HTTP 403 继续请求；迁入必须对余额不足、401/403、审核拒绝立即终止，并尊重一次确认对应一次请求的授权范围。 |
| `webui/Main.py`：`_get_voice_preview_sample()`、`_voice_preview_fingerprint()`、`_synthesize_voice_preview()`、`_render_voice_preview()`、`_get_reusable_full_voice_preview()` | 固定短文案试听与用户全文试听分开；读取真实文件时长和 MIME、缓存字节/文案摘要/参数，清理临时文件。缓存指纹含文案、供应商配置、音色、语速与音量；短样例不能进入正式任务，只有匹配的全文结果可复用。[试听函数组](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/webui/Main.py#L5597-L6243) | 这些是 Streamlit/Python 函数，不能直接当 React 组件导入。拆取缓存指纹、服务端试听生成与结果复用函数，薄适配现有 FastAPI 与 React 音频播放器。按钮点击直接合成的上游行为须接本项目“披露文本与参数 → 用户确认 → 提交”的既有机制。 |
| `app/services/voice.py`：`azure_tts_v2()`，1491 行；`_build_azure_v2_ssml()`，1466 行 | Azure 官方 Speech SDK 发送 SSML，订阅 WordBoundary 事件，记录文本与 100ns offset；有独立 Speech Key/region。[Azure 路径](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/app/services/voice.py#L1466-L1593) | 若用户将来明确选择 Azure，优先复用此官方路径；不把命名为 `azure_tts_v1()` 的免费 Edge 路径当作 Azure 商用服务。上游 `use_default_speaker=True` 需要按服务器写文件用途调整。当前不启用、不切换服务。 |

### 字幕、停顿与真实时长的关键限制

- MiniMax 路径返回 `populate_legacy_submaker_with_full_text()`：按标点断句、按字符比例分配**实测总时长**，不是服务端返回的逐词对齐。上游 `minimax_tts()` 未发送 `subtitle_enable` 或读取字幕结果。[估算函数](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/app/services/voice.py#L1091-L1161)、[MiniMax 请求](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/app/services/voice.py#L1987-L2050)。
- MiniMax 官方 HTTP 文档当前提供 `subtitle_enable`、`subtitle_type=sentence/word`，返回 `extra_info.audio_length`；它是可接的服务端能力，仍须核对目标渠道是否暴露相同字段及真实请求结果。不能把 MiniMax 直连文档等同于 Fal 契约。[官方 T2A HTTP](https://platform.minimax.io/docs/api-reference/speech-t2a-http)。
- `tts()` 中 `[pause:...]` 分段合成只用于 Edge 路径，其他供应商只删除标签；`_tts_with_pauses()` 的 PCM 样本累计偏移与静音拼接代码可供本地时间线适配，但不意味着 MiniMax 已支持这些标签，也会将一份脚本变成多个远端请求。[停顿路径](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/app/services/voice.py#L834-L1040)。
- 精确源边界已有时，可复用 `create_subtitle()` 的 WordBoundary/cues 聚合；`get_audio_duration(SubMaker)` 取最后字幕边界或对象时长，文件版本才解码媒体时长。正式时间线应采用本项目 FFmpeg/ffprobe 的实际音频时长，字幕需保留“服务边界/估算/人工校正”来源。[字幕聚合与时长](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/app/services/voice.py#L3040-L3127)。

### 最小依赖范围

上游全项目声明 Python ≥3.11，`edge-tts==7.2.7`、`requests==2.33.1`、`moviepy==2.2.1`、`loguru==0.7.3`、Azure SDK 1.41.1，及 Streamlit、Whisper、LLM 等大量无关依赖。`voice.py` 顶层还耦合 `app.config.config`、`app.utils.utils`、OpenAI 和 Edge SubMaker。**不要整文件/整 requirements 安装**：迁入目标函数、许可与来源注释，接现有配置/日志/文件路径/FFmpeg 探测；仅实际选择 Azure 时引入官方 Speech SDK。如此可保留原有解析逻辑，又避免为 MiniMax 试听带入全部服务与 UI。[依赖定义](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/pyproject.toml)、[顶层导入](https://github.com/harry0703/MoneyPrinterTurbo/blob/ad5496f1b729d1d7e361dd972015d26c08b0e052/app/services/voice.py#L1-L31)。

## 2. NarratoAI：分镜配音可借，时间线不能原样搬

| 模块 | 核验结果与限制 |
| --- | --- |
| `webui/components/audio_settings.py`：`render_qwen3_tts_settings()`，979 行；`render_voice_preview_new()`，1947 行 | Qwen 17 个显示名称映射到系统 voice 参数；点击试听后用固定样例实际合成、读取音频、`st.audio` 播放并清理。不是预置试听 URL，也不是零请求试听。其前端同样是 Streamlit。[音色选择](https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/webui/components/audio_settings.py#L979-L1055)、[试听](https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/webui/components/audio_settings.py#L1947-L2068) |
| `app/services/voice.py`：`qwen3_tts()`，1962 行 | DashScope SDK → 获取音频 URL → 下载文件；字幕用字符长度估算，`safe_speed` 计算后未用于请求，UI 的数值语速控件不能据此承诺生效。未校验音频实际解码成功。[Qwen 函数](https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/app/services/voice.py#L1962-L2055) |
| `app/services/voice.py`：`tts_multiple()`，1794 行 | 逐脚本片段生成，跳过 `OST=1`，结果包含 `_id/timestamp/audio_file/subtitle_file/duration/text`。适合迁入批量结果组织；仍只有统一音色参数，没有每个角色各选音色。Qwen/豆包等路径不产精确字幕；实长失败会按文件大小、文本长度甚至默认 3 秒估算，正式验收须去掉这些成功兜底。[批量函数与时长](https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/app/services/voice.py#L1794-L1930) |
| `app/services/audio_merger.py`：`merge_audio_files()`，21 行；`app/services/update_script.py`：`update_script_timestamps()` | 先建总长静音，再按 `duration` 累计位置 overlay；采用剪后顺序而非原始 `timestamp` 的绝对 start。缺少音频保留间隔；该合并函数本身不处理音频超出片段造成重叠/尾部截断。现有时间线应按实际 start/trim/实长和用户确认后的安排合成，不搬其整条视频流水线。[合并函数](https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/app/services/audio_merger.py#L21-L76)、[剪后时间计算](https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/app/services/update_script.py#L90-L198) |
| `app/services/voice.py`：`create_subtitle_from_multiple()`，1581 行 | 以 `script_duration/audio_duration` 比例缩放边界再平移到脚本 start；只有对应音频也以相同比例变速时才可保证对齐，不能单独搬作精确字幕。[字幕缩放](https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/app/services/voice.py#L1581-L1681) |

上游要求 Python ≥3.12；MoviePy 2.1.1、Edge TTS 7.2.7、Streamlit 1.56.0、pydub 0.25.1、DashScope ≥1.24.6、Azure/Tencent SDK 等。适合按函数拆取结果结构和选择数据，不整体迁入多引擎全局配置；不启用其 IndexTTS/VoxCPM/参考音频克隆路径。[依赖定义](https://github.com/linyqh/NarratoAI/blob/9fa69e022d4add41205ee385207561df8796b3f1/pyproject.toml)。

官方百炼文档将 `qwen3-tts-flash` 列为系统音色、不支持复刻或声音设计；非流式音频 URL 有效 24 小时，需下载为本项目资产。它是后续明确选型时的候选，当前不切换到百炼。[模型表](https://help.aliyun.com/zh/model-studio/tts-model)、[非实时指南](https://help.aliyun.com/zh/model-studio/non-realtime-tts-user-guide)。

## 3. OpenShorts（开片）：只借试听对比组织

- `scripts/preview-voices.mjs` 用同一句文案依次生成 6 个候选 MP3，覆盖 5 个中文音色（云扬有两种语速），逐项报错保留成功文件；没有直接可迁入的多音色 React 试听面板。[脚本](https://github.com/jnMetaCode/openshorts/blob/48df3591750ea9d0d08f58fb9ce154605e61dfa2/scripts/preview-voices.mjs)。
- `src/voice/edge-tts.mjs` 的 `synthesize()` 使用 `msedge-tts`，返回 `{file, buffer, durationMs, words}`；把 100ns 边界转毫秒，`durationMs` 取最后词 end，不是媒体解码总长。源码明确依赖微软非官方端点，不能据此确认客户营销音频的商业授权。[实现](https://github.com/jnMetaCode/openshorts/blob/48df3591750ea9d0d08f58fb9ce154605e61dfa2/src/voice/edge-tts.mjs)。
- `src/voice/synth.mjs` 的 `makeSynthesizer()` 在 Edge 失败后自动调用已配置 AO 供应商，回落结果没有逐词边界；该自动切换与用户要求冲突，不能迁入执行链路。[自动回落](https://github.com/jnMetaCode/openshorts/blob/48df3591750ea9d0d08f58fb9ce154605e61dfa2/src/voice/synth.mjs)。
- 包版本 2.0.0-alpha.26、Node ≥20；完整项目 React 19.3/Vite 8.3/Remotion 4，与当前 React 18/Vite 6 不同；即使仅取服务函数也需 Node 与 `msedge-tts`，不比 Python 候选更贴合现有后端。[包定义](https://github.com/jnMetaCode/openshorts/blob/48df3591750ea9d0d08f58fb9ce154605e61dfa2/package.json)。

## 商业权利与请求确认边界

1. **标准音色与克隆音色必须在接口和服务端同时区分。** MiniMax 官方 `get_voice` 将 system、voice_cloning、voice_generation 分开，查询时可指定类别；默认 `all` 不是“标准商用音色”证据。仅列 `system`，从实际合同核验输出用途。[MiniMax 官方目录](https://platform.minimax.io/docs/api-reference/voice-management-get)。
2. **代码 MIT ≠ 声音商业许可。** 微软产品条款对付费 TTS 客户的预置 neural voice 输出明确给出包含商业用途的权利；这不能套用到非官方 Edge 接口、免费档或别的供应商。MiniMax/Fal/百炼则须核对所用账号的实际适用协议；本次未完成各账号合同或音色权利确认。[微软产品条款](https://www.microsoft.com/licensing/terms/productoffering/MicrosoftAzure/OVOVS)。
3. **试听本身也会外发文本并可能计费。** 用户看到短样例或全文、服务/渠道/模型/系统音色 ID、语速/音量、发送次数后确认，才提交；参数或文本变化使确认失效。复用已批准且参数完全匹配的全文音频，不重新外发；六音色批量试听需要把六次请求明确纳入确认。上述是本项目边界，不是上游已实现的功能。
4. **当前余额错误保持终止。** `403 balance_exhausted` 不构成重新尝试或切换供应商的授权。目录、样例与全文接口可以先完成本地适配、mock/本地音频验证；真实付费质量、供应商时戳、账号能力和商业合同仍待用户明确授权与可用余额后验收。

## 最小迁入清单与验收标准

建议按以下顺序搬源码，保留每段来源仓库、完整 SHA、原函数名与 MIT 许可，不复制素材、参考音频、模型或上游全局配置：

1. MoneyPrinterTurbo 的音色目录解析、请求结果解析、原子音频落盘与试听缓存指纹 → 验证：system 过滤、参数变更失效、短样例不进入正式任务、无外发确认不触网、余额不足仅一次失败且不自动换服务。
2. MoneyPrinterTurbo 的试听实长/MIME/全文复用逻辑，薄适配 FastAPI 和 React `<audio>` → 验证：本地音频可播放、duration 来自真实探测、空/损坏音频不能进入资产与审核、已匹配全文复用不重新调用。
3. NarratoAI 的分镜结果结构 → 验证：每段音频保留脚本版本、音色 ID、供应商来源、实际长度；接现有 FFmpeg 时间线，处理语音超长时让用户修改脚本或时序，不能静默估长、重叠或剪尾。
4. 对接真实时间戳时优先采用被选渠道提供的句/词边界；估算字幕明确标记并人工审核 → 验证：首尾边界在实际音频范围内、分镜偏移准确、全文音频与字幕版本一致。

目前完成的是源码与许可核验。未验证真实多音色音质、供应商返回字段、账户商用资格、付费扣费或生成成片质量。
