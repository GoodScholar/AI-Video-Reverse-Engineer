# 11: 视频音频素材与生成节点

Status: ready-for-agent
Blocked by: 08,09

## What to build

上传、替换、预览、处理、生成视频音频并衔接下游。

依据：../spec.md 和 ../parity-acceptance.md。继承源码复用优先、商用验收及已授权持续执行要求。完成本票不代表整个画布完成。

## Acceptance criteria

- [ ] 扩展文档与端口时兼容已有节点
- [ ] 模型参数、首尾帧/参考、时长等按实际能力显示
- [ ] 真实服务输出入库、取消恢复和失败重试验证

## 2026-09-30 第 11 项视频源码核查与实施合同

### 固定来源与实际行为

- Toonflow `d86cce6b689916ab73f75bc4c1f42a2a7424516c`，MIT：`packages/nodes/videoGenerationNode/src/index.vue` 的 `getMatchingModes`、`startGeneration`、`replaceOutput`；`components/generationSettings.vue`；`packages/nodes/videoNode/src/index.vue` 的 `uploadVideo/setVideo`；`packages/tools/mediaGeneration/src/runtime.ts` 的 `videoGenerationSchema`；`apps/server/src/utils/media/generation.ts` 的 `listMediaModels/readReference/generateMedia`；`packages/providers/src/media/tfRouter.ts` 的 `generateTask/generateVideo`，以及 `meta.ts` 的 `pollVideo/generateVideo`。节点具有多模态输入与 VIDEO 输出、按引用数量筛选模型模式、首尾帧映射、时长和分辨率联动、可选音频、上传替换、播放与下载。`startFrameOptional` 只有一图时把它作为尾帧，两图则依序为首尾帧；`endFrameOptional` 一图为首帧。模型的 `durationResolutionMap` 和 `audio` 来自目录，界面的五个比例只是通用候选，源码明确未声称供应商逐项支持。媒体工具 schema 对工作区相对路径、媒体 MIME、参数和数量做严格验证，服务端读取文件、按模型调用 provider、下载并校验媒体、写工作区文件。
- Toonflow 的 `nodeAi.generateMedia` 经前端 `/api/ai/media/generate` 发起一个长 HTTP 请求；上述 provider 在该请求内提交并轮询任务，`tfRouter.generateTask` 源码注释明确任务 ID 尚未单独保存。这套 Vue/Bun/工作区文件、动态插件提供商、长连接与本项目 React/Python/项目素材/持久运行不同；可借用节点交互、模式和请求校验思路，不能把其前端 Abort 当成远端已取消，也不能原样复制为可重启的后端运行器。
- Infinite Canvas `dab19adc0847e32e39b7fc8ff90cb392561fb826`，MIT：`web/src/services/api/video.ts` 的 `createVideoGenerationTask/pollVideoGenerationTask/waitForVideoGenerationTask/storeGeneratedVideo`；`web/src/pages/canvas/project.tsx` 的 `completeVideoNodeTask/pollVideoNodeTask/handleGenerateNode/captureVideoNodeFrame`；`web/src/lib/canvas/canvas-video-frame.ts` 的 `captureVideoFrame`；`web/src/components/canvas/canvas-node-generation.ts` 的 `buildNodeGenerationContext`。它先存 OpenAI/Gemini 远端 taskId，再按 2.5 秒、最多 120 次轮询；页面重开时可据 taskId 续查，完成后入媒体存储。浏览器抽首、尾、当前帧，生成新图片节点并建立来源连线。其存储、配置、浏览器轮询和插件结果 `Map` 均非本项目持久服务；`storeGeneratedVideo` 下载失败时还可退回远端 URL，本项目应以项目资产已成功安装为完成条件。此处的 OpenAI/Gemini 形状只是该仓库适配代码，不可推断任意用户供应商兼容。

### 本项目可用边界

- `backend/app/creative_canvas.py` 已有 project/canvas 隔离文档、输出身份及素材引用扫描；`creative_canvas_api.py`、`durable_runs.py` 已有图片的冻结快照、run/resultId、排队/取消/未知/重启栅栏和候选历史。应复用这些状态与作用域规则，但当前 `CanvasNode.kind`、`CanvasEdge`、`snapshot_for`、输出安装均限定图片，不能直接把视频塞入现有 `generate` 图片参数与 image handle。
- `preproduction_api.py` 的 `/assets`、`_probe_media`、`ProjectAssets.install`、`/assets/{id}/file` 已支持项目级视频/音频验证、入库、播放和下载；`preproduction_nodes.py` 的 `_extract_frame` 与 `CreativeCanvasMediaPlayer.tsx` 可复用抽帧和播放。当前播放器的抽帧若无 `onCaptureFrame` 仅下载文件，尚未形成项目图片资产和可连线节点。`ImageGenerationConfigurations` 的 CAS/凭据和能力目录模式可作为视频配置边界的样板，但图片凭据/模型/协议不能隐式视作视频授权。现有 `final_generation_policy.py` 限制的是旧镜头最终视频提交；独立画布视频生成需有独立、明确的服务接入，不借旧镜头流程冒充。

### 最小数据、API 与界面合同

1. 保留旧 `generate` 图片节点和旧文档；增加明确的视频生成节点身份及视频参数（稳定 `providerId/modelId/configurationVersion`、`mode`、`duration`、`resolution`、`ratio`、`generateAudio`），媒体节点继续指向项目 `assetId`。边/端口增量支持 `video`、`audio` 及视频生成目标的图/视频/音频/文本输入；保存时按资产实际 kind、源/目标端口和环路校验，旧图原样可读。复用 `referenceOrder` 对所有入边排序，明确模式把有序图片映射为首尾帧；快照记录每条边的 `edgeId/sourceNodeId/sourceHandle/assetId/sourceRunId/sourceResultId` 或文本版本，不能只存合并后的文件列表。
2. 视频模型目录仅列真实已配置、已知能力；按模型验证模式所需引用数量、时长×分辨率组合、比例声明与音频开关。未声明能力不得用 Toonflow 通用五比例或 Infinite Canvas 默认 720p/8 秒伪装可用；目录缺失时保留旧节点显示但禁用新提交并说明原因。多模态数组模式逐类型验证上限；非法首尾帧顺序、缺失/跨项目/已删除输入在提交前失败。
3. 独立的视频 run API 至少有“提交保存修订＋幂等键、查询/刷新、取消、明确失败后重试”，结果复用 `{runId,resultId,assetId,mediaType:'video'}` 身份及 `currentOutputRef`，原图片 API 与旧 run 不重解释。运行快照冻结配置版本、适配器、模型、完整参数、输入资产及有序引用；密钥只留安全存储，公开响应不含密钥或签名媒体 URL。提交前持久化本地请求身份；远端返回 taskId 后立即持久化，再轮询；重启若已有 taskId 则只查询，若提交结果可能已发生但 taskId 未落盘则置 `unknown`，不得自动再付费提交。取消区分未发送、本地停止、供应商确认停止和计费未知；晚到视频可安装为历史候选但不自动设为当前输出。
4. 下载远端结果须限定安全地址/大小、校验实际可解码视频及时长/尺寸，原子入库为本项目新资产，只有入库与 run/result 记录完成才报成功；失败、取消、重试不得覆盖旧输出或生成重复资产。视频输出变更标记依赖节点 stale；手选、删除与分支按精确 resultId，历史快照不可变。页面节点提供参考列表和顺序、能力驱动设置、生成/停止/状态、预览/全屏/替换/下载；首尾/当前帧抽取应可入库为新图片资产并保留来源关系，浏览器失败时可考虑复用服务端 `_extract_frame`。

### 高风险边界与真实服务验收

- 远端提交成功但 taskId 响应/持久写入失败、轮询超时、重启、删除画布或切换项目时，必须以项目＋canvas＋run＋远端 taskId 作用域防重、保留 unknown 与费用风险；不能因浏览器离页丢任务，也不能把 Abort 当作供应商取消。视频结果下载到本地/入库失败时应保留远端身份并只重试安装/查询，不能重做付费生成。批次/多候选若以后支持，沿用第 09 项逐候选幂等与冻结引用，不能以单次模拟响应替代。
- 当前仓库没有供画布使用的**真实视频生成 adapter、视频模型能力目录与独立视频凭据**；现有图片服务、ComfyUI 角色动画、FFmpeg 加工都不等于文生/图生视频服务。实施前须明确至少一个实际可调用、已授权的供应商及其创建、查询、下载、可选取消协议。模拟器可验证正常/失败/超时/取消/重启/晚到/幂等；商用闭环另需同项目真实凭据下的提交 taskId、轮询、视频资产入库、播放下载、下游连接与费用/取消状态证据。音频生成仍属本票独立剩余范围，不因视频合同完成而标记 11 达标。
