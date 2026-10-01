# 16: 交付 faster-whisper 长视频绝对时码转录

**What to build:** 对已入库项目视频执行可恢复的 `faster-whisper` 本地转录，保存词级与段级全片绝对时码、模型和策略版本；超过 30 分钟时按默认 20 分钟块与 60 秒重叠处理，并修正参考实现的块内/全片时码风险。

**Blocked by:** 01: 冻结五张验收矩阵与三条金标接缝; 10: 建立统一视频来源合同并贯通本地导入

**Status:** blocked

## Scope

- 固定 Anil-matcha/AI-Youtube-Shorts-Generator 提交 `a57bb938ba50bf9654c2d5ca2af1295163454349` 的本地转录调用链为参考，根许可 MIT。
- 转录输入只能是项目素材身份及冻结版本；缓存键包含内容、语言、模型和策略版本。
- 分块提示可用相对时间，但进入领域数据前统一转换并校验为全片绝对时间。
- 运行支持进度、取消、检查点、失败重试、重启恢复和无语音状态。

## Source reuse prerequisite

- 阅读固定提交中的本地下载器、转录器、pipeline 和长视频分块源码，记录可复制代码、缺陷与必要适配。
- 核对 faster-whisper、模型权重、FFmpeg 和实际分发方式的许可证与版本；`whisper.cpp` 不能作为本票对齐证据。

## Acceptance criteria

- [ ] 短视频真实转录保存词级、段级、语言、模型、策略和全片绝对时间。
- [ ] 30 分钟边界和超过 30 分钟视频按版本化策略分块，默认 20 分钟块、60 秒重叠。
- [ ] 第二、第三块返回的候选时码转换到全片坐标且不重复叠加 offset。
- [ ] 取消、进程重启和失败从检查点恢复，不重复已完成块或覆盖历史运行。
- [ ] 无语音、损坏音轨和模型不可用产生确定状态，不伪造空成功。
- [ ] UI 可查看运行进度、转录证据、模型/策略版本和失败诊断。

## Verification evidence

- E2 使用合成时间标记音频验证 30 分钟边界、重叠、绝对时码和缓存失效。
- E3 至少一份授权短视频和一份超过 30 分钟视频完成真实 `faster-whisper` 转录。
- 人工抽查第二、第三块词级/段级时间与原视频播放位置一致。

## Comments

- 2026-10-02 E1 来源核查：Anil-matcha/AI-Youtube-Shorts-Generator 固定提交 `a57bb938ba50bf9654c2d5ca2af1295163454349`，根 `LICENSE` 为 MIT；核对 `local/transcriber.py:98-162`、`local/downloader.py:61-135`、`pipeline.py:17-53`、`highlights.py:64-68/174-197/287-300`。仅适配其本地 `faster-whisper` 调用边界；未复制下载器、整条 pipeline 或 highlights（ViralVadoo 授权和业务适配未决）。上游按整文件缓存且没有词级时间、持久块检查点、恢复/取消；本实现以 SHA-256、冻结 `assetVersionId`、语言、模型修订和策略键持久化分块状态，统一在领域边界做一次绝对时码转换。
- 2026-10-02 E2：`backend/tests/test_transcription.py` 使用注入 runner 返回带本地时间标记的块结果，覆盖 30 分钟边界、60 秒重叠和单次绝对偏移、无语音失败、重试历史；这不是 E3，也没有运行真实 faster-whisper。
- 2026-10-02 E3 阻塞：当前 `backend/.venv` 未安装 `faster_whisper` 或 `ctranslate2`；仅存在 MLX Whisper 缓存，不能作为 faster-whisper 权重或真实链路证据。已声明 `faster-whisper==1.1.1`，但未安装；本机 FFmpeg 为 Homebrew `ffmpeg-full 9.0.1`，构建配置含 `--enable-gpl`，生产分发前仍需完成依赖、模型权重和许可证审查。票据保持未完成状态。
- 2026-10-02 实施边界：模型别名不会冒充不可变权重修订；只有运行前验证到不可变模型修订或权重哈希后才形成缓存键。当前默认环境没有该验证依据，因此以 `model_identity_unverified` 明确失败。后端 E2 覆盖分块、第二/第三块绝对时码、输入失效后的运行历史、检查点恢复、取消、无语音、损坏音轨和模型不可用；前端仅显示实际返回的模型身份、进度、词级证据和可重试诊断。E3 仍需要安装并许可核查实际依赖/权重、使用授权短视频与超过 30 分钟视频运行，并人工抽查第二/第三块。
- 2026-10-02 P1 修正：请求中的 `small` 等别名不是模型修订或权重哈希。运行创建时明确保存 `modelRevision: null` / `modelIdentityStatus: unverified`，不将别名写入可复现缓存键；只有 worker 预检提供不可变模型身份后才生成缓存键并可完成。当前默认生产配置没有该预检身份，确定失败为 `model_identity_unverified`，而非伪称真实模型版本。失效的语言、模型或源内容会新建运行，但保留 `runs[]` 和 `previousRunId` 历史；E2 fixture 使用注入的测试模型身份，仅用于测试。

## Out of scope

- 高光评分、Top N 和竖屏重构。
- 把远端 Whisper 或 `whisper.cpp` 冒充本地链对齐。
