# 10: 完整文本节点

Status: ready-for-agent
Blocked by: 08

## What to build

手写编辑与模型生成双角色、引用输入及文本结果可继续连接。

依据：../spec.md 和 ../parity-acceptance.md。继承源码复用优先、商用验收及已授权持续执行要求。完成本票不代表整个画布完成。

## Acceptance criteria

- [ ] 文本弹窗/预览/下载/入库/字号和生图动作覆盖参考
- [ ] 模型生成、取消、错误、保存恢复与结果版本真实可用

## 固定源码与复用边界（2026-09-30）

- Toonflow `d86cce6`（MIT）的 `packages/nodes/textNode/src/index.vue`、`packages/nodeScaffold/src/nodeAi.ts`、`apps/server/src/routes/ai/generate.ts` 和 `apps/server/src/utils/ai/index.ts` 已逐段核查。文本节点有手写编辑、预览/全屏、`.txt` 下载、图文视频引用、供应商分组模型选择和逐 delta 生成；内容串行写入 `assets/<nodeId>/content.md`，生成失败时也尝试保存已有增量。服务端从配置取模型和密钥并返回 SSE，连接关闭触发本地 abort，但源码明确不能保证上游立即取消。节点无持久 run/结果版本，也没有服务器重启后的运行恢复。Vue/Vue Flow、Bun 工作区文件和 `pi-ai` 运行时不能直接移入 React/FastAPI；复用交互、引用顺序和增量协议，若直接复制代码/样式须保留 MIT 版权声明。
- Infinite Canvas `dab19ad`（MIT）的 `web/src/pages/canvas/project.tsx` 文本生成分支为每个候选各发一次请求、按候选 ID 写增量、完成后选主文本；`web/src/components/canvas/canvas-node.tsx` 展开候选并切主文本。`canvas-node-generation.ts` 按图连接展开组资源和 `@[node:id]`，但 `buildNodeResponseMessages` 实际只送文本与图片；视频/音频虽被收集，不能据此声称模型已收到它们。`services/api/image.ts` 的 `requestImageQuestion` 在浏览器直接使用供应商配置和流式响应；它的内存状态不满足本项目的持久运行/重启恢复。
- 本项目 `backend/app/aigc_content_api.py` 的脚本候选服务依赖项目 brief、发送披露摘要与结构化 JSON schema；`main.py` 的 `generate_marketing_candidates` 使用 `ProviderRequest(task="prompt_generation", responseSchema=...)`。`analysis_providers/base.py` 的请求角色只支持结构化分析/提示词生成；`openai_compatible_chat.py` 默认强制 JSON 响应且同步返回整段。故不能把自由文本节点接到脚本候选端点，也不能宣称现有服务已支持流式自由文本。可复用 `AnalysisProviderConfigurations` 的密钥隔离、供应商/模型配置与验证、配置 revision CAS，以及 `analysis_providers/http_transport.py` 的超时、大小限制和本地连接中断机制；自由文本与多模态需独立请求/响应适配器和经实际供应商验证的能力目录。08 图片模型目录只服务图片，不代表文本模型已可执行。

## 实施契约

1. **节点与图。** 保留旧 `{kind:"text",text}` 读写和文本输出。为文本节点增可选生成角色、prompt、稳定 `providerId/modelId/configurationVersion`、有序 `referenceOrder`、`currentTextVersionId`；旧手写节点按原样读取，不迁移或覆盖其 `text`。明确文本节点目标输入端口以及 text/image/video 类型矩阵；保持文本输出可连现有下游。`text` 是当前选中版本的兼容投影，切版本或手写保存才更新，流式临时预览不直接覆盖已选版本。前后端 schema、剪贴板、撤销/重做和项目备份须同步接受新增可选字段。
2. **运行与引用快照。** `POST /nodes/{id}/text-runs` 接画布 revision 与幂等键；服务端在同一项目/画布内校验目标、模型配置版本、引用来源、素材类型与可用性，冻结 prompt、供应商/模型/协议、配置 revision、按 `edgeId` 排序的 `{sourceNodeId,sourceHandle,textVersionId?,text?,assetId?,sourceRunId?,sourceResultId?}`。文本来源取提交时选中的具体版本；媒体来源取项目登记资产/结果，不信任客户端 URL、跨项目 ID 或节点当前后来变化。图片仅在选中模型确认支持时送入；视频/音频须有已验证传输适配后才启用，否则清楚提示不支持。`@` 提及用稳定来源 ID 持久化，显示编号可随排序变化，但历史快照不变。
3. **增量、版本与恢复。** 每次生成建立不可变 `runId` 和一个或多个独立 `versionId`（候选）；事件按 `{runId,versionId,seq,delta}` 递增并提供重连游标或状态轮询，服务端定期持久化部分文本和终态。断网/离页后可从服务器恢复已提交的 run 与内容；重启中断的运行标记 unknown，不自动重交供应商。成功版本可切主、预览、下载、入库或继续连线；失败保留可见的部分文本/错误，重试新建 run，不能重写旧 run。批量候选按 Infinite Canvas 的独立候选和主指针语义实现，单供应商不支持 `n` 时用多个受控单候选请求，不可静默缩成一份；防重复提交与费用提示沿用 09 运行规范。
4. **取消和晚到。** 排队取消必须阻止提交；运行中本地尽力中止网络连接，供应商确认不了时保持 `providerDisposition/billingUnknown` 的诚实状态，不能宣称远端已取消。晚到事件只更新原 run/version，不能覆盖用户后来选中的版本、手写内容或切换后画布；删除/软删源素材时检查文本 run 的冻结引用。严格区分失败可重试与 unknown 不可盲重试。
5. **界面验收。** 手写弹窗、节点预览/字号、全屏、下载 `.txt`、入库和文本生图动作与生成 prompt/引用列表/模型选择同节点可操作；生成中可看到稳定增量和取消/错误状态。至少验证图文混合引用、两候选切主、版本继续连接、编辑后撤销/重做、刷新/离页恢复、并发切画布的晚到隔离、无文本凭据时手写仍可用，以及真实供应商输出。视频/音频引用只有对应真实模型能力与端到端证据才勾选。

现有 `ProjectAssets` 的文件与引用保护边界可承载新记录，但 `CreativeCanvasAsset` 类型及素材上传/浏览只认 image/video/audio；“入库”必须明确新增 `text/plain` 文件和受保护的 `text` 资产类型（或可验证的独立文本库），不能只在节点内保存字符串后显示“已入库”。下载可以先从选中版本直接生成 `.txt`，与入库分开验收。

## Comments

- 2026-09-30 宿主联调：新增文本服务设置/模型目录，文本节点接入引用预览/排序/移除；修复 unknown 禁跑、模型配置变化后的重新选择、宿主错用图片配置版本、StrictMode 旧读取覆盖草稿、复制版本指针及并发版本采用的间接下游 stale。文本资产可在素材库读取预览并重新加入画布为可撤销的可编辑副本，媒体生产入口排除文本资产。
- 用户选择继续使用既有豆包。已核对 `analysis_providers/doubao.py`、`analysis_settings.py`、`credential_store.py`；复用模型 `doubao-seed-2.1-pro` 和 `https://ark.cn-beijing.volces.com/api/coding/v3`，在本机安全存储内复制已有凭据至文本命名空间，原分析配置字节未变，密钥未输出，未进行供应商请求。官方 Chat Completions 兼容地址依据：https://docs.volcengine.com/docs/ark/coding-plan-personal-ai-zcode?lang=zh 。浏览器已核实地址、模型、已保存密钥提示与文本/图文验证按钮；证据 `../evidence/10-doubao-text-settings.png`。
- 父代理验证：前端全量 633 项通过，随后行为增量涉及的 4 文件 88 项通过；最终 TypeScript/Vite build 通过（既有 chunk 大小警告）。Python 3.9 的图片生成、素材 API、项目备份联动共 91 项通过；文本服务及画布另 93 项通过。真实供应商文本/图文验证、实际生成入库与继续创作尚未执行，保留原完整验收口径和所有未闭合项。

- 2026-09-30：后端审查修复了 `backend/app/creative_canvas_api.py` 的文本流边界与运行中取消。生成正文按 `CanvasNode.text` 的 12,000 字符限制持久化；超限保留前 12,000 字符并以 `text_output_too_large` 失败，可入库下载但不可选为当前版本。运行中取消会置位本地 `cancel_event`、关闭已取得流并停止后续 `next()`；已在途的一个 delta 仍保留在原版本，未自动采用，远端结果保持 `providerDisposition=unsupported` 与 `billingUnknown=true`。接口兼容 `generate(snapshot, cancel_event=...)` 和可关闭流，为后续 OpenAI 兼容服务接线预留最小边界。
- 参考核查：Toonflow `d86cce6`（MIT，`packages/nodes/textNode/src/index.vue`、`packages/nodeScaffold/src/nodeAi.ts`）的逐 delta 写入及 Abort/reader cancel 语义；本项目保留持久 run/version 的既有差异。
- 验证：`cd backend && PYTHONPATH=. ./.venv/bin/pytest -q tests/test_creative_canvas.py`（74 passed）。新增恰好 12,000、超限截断/失败/入库、流关闭、取消后不再消费和已在途 delta 的回归覆盖。
- 残余：真实 OpenAI 兼容文本服务、实际供应商流取消与计费证据仍未接线，不能将本地尽力停止表述为远端已取消；本票保持 open。
- 2026-09-30：续接独立 OpenAI 兼容 Chat Completions 文本服务：`backend/app/text_generation.py` 与 `text_generation_api.py` 使用 `text-generation.json`、Keyring service `ai-video-reverse-engineer.text-generation`，独立于分析/图片密钥。公开配置不含密钥；配置 CAS、密钥写入回滚、配置变化使验证失效。显式“可能计费”的连接测试以文本 SSE 完整结束验证文本能力；图片模式发送固定红/蓝 PNG 并仅在返回按 red→blue 的次序匹配时记录 `imageVerified,maxImageReferences=2`。
- 生成仅使用 `POST /chat/completions` 的 `stream:true` 消息，不带 `response_format` 或工具；运行前后复验配置 revision/model 和验证状态。画布快照增加 `projectId/canvasId/runId/versionId` 上下文，图片引用从项目登记资产读取、解码并临时组成 data URL，持久公开 run 仍仅保存资产 ID。视频/音频拒绝。SSE 需 `[DONE]`，施加响应字节、总时间和 12,000 字符预算；取消事件会独立关闭响应/HTTP socket，网络、未完成流与取消保持计费未知。
- `main.py` 已注册 `/api/text-generation` 并将服务注入画布；workspace 额外返回无密钥的 `textModels` 目录，供画布模型选择复用。验证：`cd backend && PYTHONPATH=. ../.venv/bin/pytest -q tests/test_text_generation.py tests/test_creative_canvas.py`（Python 3.9.6，83 passed）。MockTransport/本地 SSE 覆盖配置 CAS/秘密不回显、文本及双图探测、项目图片引用、超长输出、取消关闭、普通 close 错误的 billing unknown、主应用路由和 workspace 目录。
- 残余：用户仍需自行配置实际可用的 endpoint、模型和凭据并明确启动付费连接测试/生成，当前夹具不构成真实供应商、取消或计费证据；本票保持 open。
- 2026-09-30：生产接线复审修复：`credentialState` 现仅反映独立 Keyring 凭据是否存在，`modelCatalog[].configured` 仍严格要求 `textVerified`，所以刚保存凭据可发起验证而不能生成。连接测试改为线程池执行，同一 ASGI 事件循环仍可处理健康/取消等请求；请求取消与 120 秒总截止监控在 `client.send` 前启动，覆盖收响应头、无换行阻塞流和正文，并通过既有 `http_transport` 的 `cancel_active()` 关闭实际 socket。正文读错误会关闭连接、保留已写 delta，并以 unknown 计费状态结束。
- SSE 只有收到 `finish_reason:"stop"` 后的 `[DONE]` 才成功；`length`、`content_filter` 保留片段并失败，缺失终止理由保留片段且标 unknown。图像探测改为随机、有序的双色 PNG 挑战，提示不包含色名，严格核对返回顺序；只复述提示词无法通过。按 Toonflow `utils/ai/index.ts` 的引用内容语义，冻结引用按原顺序插入 `{{ref N}}` 标记，图像随后附带本地临时 data URL。
- 验证：`cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest -q tests/test_text_generation.py tests/test_creative_canvas.py`（Python 3.9.6，91 passed）；并已通过 `py_compile` 与 `git diff --check`。新增覆盖刚保存凭据、线程池连接测试、响应中途读错误关闭/unknown、头部前取消、混合引用编号、随机图像探测假阳性、非 stop/缺失终止理由。
- 2026-09-30：超长截断复审修复：关闭本地流未证明供应商已停止，因此 `text_output_too_large` 现在保留 `outcome_unknown=false`（版本仍为 `failed`，前 12,000 字符仍可下载/入库且不可选用）但显式携带 `billing_unknown=true`。画布将“版本结果状态”和“计费未知”分别映射；超长这种本地确定失败的 run 保持 `failed`，但保留 `billingUnknown=true`，用户可修改提示词后以新幂等键显式发起新 run。验证：`cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest -q tests/test_text_generation.py tests/test_creative_canvas.py`（91 passed），并通过 `py_compile` 与 `git diff --check`。
- 2026-09-30：SSE 响应预算复审修复：不再先调用 `response.iter_lines()`（无换行输入会在 HTTPX 内部无限累积）；改为 `iter_bytes()` 每块先计入 8MB 上限，再用标准库 incremental UTF-8 decoder 与 CRLF 分行。跨块中文保持正确，连续无换行超过上限会立即关闭流并以计费未知失败。验证：`cd backend && PYTHONPATH=. ../.venv/bin/python -m pytest -q tests/test_text_generation.py tests/test_creative_canvas.py`（93 passed），新增无换行超限关闭/unknown 与跨块 UTF-8、CRLF 回归。
- 2026-09-30：更正超长截断聚合：`billingUnknown` 是计费提示，不能单独把本地确定的 `failed` 升格为 `unknown`。文本 run 仅在版本 `status=unknown`，或已取消/带 `cancellationRequested` 且计费未知时进入 `unknown`；普通 `failed + billingUnknown` 保持 `failed`，可用新幂等键显式提交下一次运行。取消和真实传输中断仍保持 unknown 禁止盲重试。验证：上述 93 项后端文本/画布回归通过。
- 2026-09-30：更正超长截断聚合：`billingUnknown` 是计费提示，不能单独把本地确定的 `failed` 升格为 `unknown`。文本 run 仅在版本 `status=unknown`，或已取消/带 `cancellationRequested` 且计费未知时进入 `unknown`；普通 `failed + billingUnknown` 保持 `failed`，可用新幂等键显式提交下一次运行。取消和真实传输中断仍保持 unknown 禁止盲重试。验证：上述 93 项后端文本/画布回归通过。
