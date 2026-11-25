# P0 全链路与交付收口验收

- 验收日期：2026-09-29
- 对应票据：[06-close-p0-end-to-end-acceptance.md](../issues/06-close-p0-end-to-end-acceptance.md)
- 实现对比点：`6ea7bef`（票据 05）；本票同时纳入票据 02 已用于真实试点但尚未入库的豆包 Coding Plan/300 秒调用、调用前披露界面及对应测试，并补齐票据 01、02、06 的验收证据

## 结论

| 判定层级 | 结论 | 边界 |
| --- | --- | --- |
| P0 技术基线 | **完成** | 正式 UI 重放、失效门禁、隔离/失败/取消/重试、重启、备份恢复、引用保护、旧最终生成关闭、媒体与完整回归均通过。 |
| P0 真实商品试点交付 | **完成** | 同一真实商品项目中已有五条确认候选，两条作品分别经人工退回、修改、重新预览、批准和最终导出。 |
| 商业交付验收 | **未发生** | 尚无真实客户签收、投放平台验收或商业效果结果；本报告不把内部试点完成扩大为商业验收。 |

## 验收对象与稳定身份

- 项目：`大窑 · 网络图片验收`
- 项目 ID：`697b8d71-b525-4011-ab90-b7f4563bc8f2`
- 真实 AI 生成批次：`332a1aff24994a0d84b9805cfcb96dce`
- 批量制作父任务：`571545fe92234cc999f52161ac650b18`
- 制作批次：`a5c21efddf994128bc3e747c40e71c96`

两条交付链的身份未复用、未覆盖：

| 作品 | 变体 | 当前预览 | 当前批准 | 最终交付 | 版本（脚本/变体/字幕） |
| --- | --- | --- | --- | --- | --- |
| `a815aca7e9544592ada771e57b2c6085` | `f11de3d5953b48dcb6b6f6c7eb69a36b` | `5517952a4a1f47c08c7006c3ce3204ef` | `61adab11349140bdbdf41dedbe2f81d7` | `d4fdb668d417479e9d7d18597999a238` | `2/4/2` |
| `1a2bde6e1556404a8d952e626b2c1e97` | `593ab3eb0ba84be891bedd2ae162c00c` | `1da0f37c7f194e9a9404135310c42815` | `47ae9e1ce8b14db58f2c761cf26c3375` | `e498624a8d12463994b5774019be2741` | `2/4/2` |

## 验收矩阵

| 验收项 | 结果 | 可复核证据 |
| --- | --- | --- |
| 正式界面打开项目并重放至两条导出 | 通过 | [项目重开](06/screenshots/02-pilot-opened.png)、[简报与允许素材](06/screenshots/03-brief-and-assets.png)、[五条确认脚本](06/screenshots/04-five-confirmed-scripts.png)、[第二条批准交付](06/screenshots/07-second-approved-delivery.png)、[正式 UI 导出重放](06/screenshots/11-ui-export-replay.png)、[原始网络记录](06/ui-export-network.log)、[导出请求与幂等结果](06/ui-export-replay.json) |
| 输入变化失效传播与重新审核门禁 | 通过 | `test_review_lifecycle_and_version_isolation`、`test_voiceover_timing_tracks_and_review_invalidation`、`test_batch_export_gate_isolation_retry_and_history`；均在定向检查及完整后端套件中通过。 |
| 两条变体隔离、失败/取消/重试 | 通过 | `test_bulk_previews_isolate_failure_cancellation_and_retry`、`test_batch_export_gate_isolation_retry_and_history`、`test_failed_variant_reports_issue_without_overwriting_siblings`。 |
| 服务与应用重启恢复 | 通过 | 重启当前代码后语音目录恢复为 10 个可用音色；恢复项目由新应用实例重开后 P0 状态相同；再修改作品一脚本并重启，作品一保持 `stale`/导出 409，作品二仍为 `approved`。[重启后选声制作](06/screenshots/06-voice-after-restart.png)、[恢复结果](06/pilot-restore-result.json) |
| 真实试点备份与独立恢复 | 通过 | [恢复结果](06/pilot-restore-result.json)、[可重复验证器](06/verify_pilot_restore.py) |
| 两份最终媒体真实性 | 通过 | [媒体探测与解码结果](06/final-media-verification.json)、[作品一抽帧](06/final-export-1-contact-sheet.png)、[作品二抽帧](06/final-export-2-contact-sheet.png) |
| 素材引用保护 | 通过 | 恢复项目中被引用素材查询到 7 类当前/历史引用，直接删除返回 `409`；完整套件同时覆盖无引用删除。 |
| 旧最终生成入口关闭 | 通过 | 两个模块共 6 个创建/刷新/恢复写入口均返回 `410 final_generation_disabled`；正式界面只显示外部生成准备与回传。[正式边界](06/screenshots/10-legacy-final-generation-closed.png) |
| 桌面、键盘、移动与错误反馈 | 通过 | 1280×900 桌面操作；通过聚焦筛选按钮并按 `Enter` 从 2 条切回 5 条；390×844 页面保留全部审片与下载控件；流程页明确列出“需修复/提醒”。[键盘焦点](06/screenshots/08-keyboard-focus.png)、[移动审片](06/screenshots/09-mobile-review.png) |
| 自动化、类型和生产构建 | 通过 | 见“验证命令与结果”。 |

## 正式界面重放

1. 从项目中心打开唯一试点项目，而不是直接修改状态文件。
2. “准备资料”显示 9:16、720×1280，以及仅两项允许素材；五项历史素材保持排除。
3. “确认脚本”显示 5 条候选均已确认，并能回溯简报 revision 4、候选 revision 0 和稳定素材 ID。
4. “选声制作”显示两条已完成预览、三条待预览，Vivian 与 Serena 两条配音均已完成。
5. “审核导出”显示 5 条作品、2 条已通过；逐条打开时，预览、审核历史、交付版本与下载入口一致。
6. 作品一和作品二均保留一次具体退回意见和一次当前版本批准，未用生成成功或自动检查替代人工批准。
7. 本轮在正式界面点击“导出已通过项”，浏览器记录 `POST /batch-edits/{parentId}/exports` 返回 202；两条当前批准版本分别幂等复用原交付 ID，交付计数仍各为 1，文件哈希不变。

重放开始时，后台仍是票据 05 提交前启动的无热重载旧进程，语音目录请求返回 `404 Not Found`。停止旧进程并从当前工作树重新启动服务后，`GET /api/projects/{id}/voices` 返回 10 个音色；刷新、返回项目中心并重新打开同一项目后，上述状态完整恢复。该问题由进程版本滞后造成，无需修改产品代码。

## 失效传播、隔离和恢复

公共 HTTP 边界测试验证了以下契约：

- 脚本、字幕、编辑序列、画幅或新预览变化后，旧审核转为 `stale`，旧预览不能重新批准，旧批准不能导出。
- 配音变化推进其关联版本，并使依赖该版本的旧预览/批准失效。
- 候选或简报版本变化后，旧 AIGC 交接键不再被视为当前输入。
- 一条变体失败、取消、重试或修改时，兄弟变体的任务 ID、输入快照、预览、审核和交付不变。
- 失败与取消不会发布部分输出；重试追加新运行历史，不覆盖旧失败/取消记录。
- 只有重新生成当前预览、再次人工批准后，导出门禁才重新打开。

干净快照中的定向检查：

```text
10 passed, 1 warning in 1.48s
```

覆盖的测试包括批量预览、逐条审核、配音失效、内容工作流投影、导出隔离、备份恢复、素材引用与最终生成边界。

## 备份、恢复与重启

`verify_pilot_restore.py` 只复制 `backend/data` 到系统临时目录，所有备份、恢复、删除尝试和重启验证均发生在副本中，不修改真实试点：

```bash
PYTHONPATH=backend backend/.venv/bin/python \
  .scratch/product-scope-freeze/evidence/06/verify_pilot_restore.py \
  --data-dir backend/data
```

本次留档的可识别备份运行：

- 大小：`38,955,665` 字节
- SHA-256：`63457bd99a11c03e4073db826a063cf3b3dccffa67e1c93b196f976cb55c39a8`
- 独立恢复项目 ID：`b4d86c01-e70e-4765-9b07-49e0aec6eaec`
- 恢复项目名：`大窑 · 网络图片验收（恢复）`

恢复验证不是只比计数：脚本通过公共接口归一化项目 ID 后，完整比较了 `preproduction`（排除不属于本票业务契约的画布布局修订）、`aigc-content`、`batch-edits` 与 `content-workflow` 投影。恢复后保持全部素材记录、简报 revision 4、两项允许素材、五条候选 ID 与确认版本、五任务交接、预览运行、退回/批准历史及两份交付；两条作品的任务、变体、预览、审核、交付 ID、`2/4/2` 版本关系和输出哈希全部不变。验证器还通过恢复项目的公开下载地址读取两项允许素材和两份当前预览，逐字节哈希与源项目相同；关闭第一个应用实例后用第二个实例重新读取并再次下载，结果仍一致。

- CC0 原图：`5751bc9bf360a99e5ce28d74e71c1f474586be6c1742d166c945f033ca0eb16e`
- 派生静态视频：`e96d27dfe9cb26d702a4b9c428383e742ec7bf40c1dd828cf5aff8b0a5da8800`
- 当前预览一：`68eabc15ba38e7647548e66ae347059bc9e2410d7ae498858e4be5335e54ec11`
- 当前预览二：`1534611d6f0507096bf48a77885cde97a05fa47b94ebb62656aef6874a492a46`

随后在恢复副本中将作品一脚本从 content revision 2 推进到 3：作品一审核转为 `stale` 且导出返回 409，作品二仍为 `approved`。再次关闭并重建应用实例后，这组阶段状态仍保持，证明失效与兄弟任务隔离不依赖进程内状态。

同一恢复场景还验证：

- 被引用素材 `asset-e4af7b3f8a514a908590b200ed3e405e` 查询到 `content_brief`、`script_candidate`、`batch_variant`、`batch_preview`、`batch_review`、`batch_delivery`、`batch_aigc_source`；直接删除返回 409。
- `reproduction` 和 `character-motion` 的创建、刷新、恢复/重试共 6 个写入口全部返回 410；准备读取与外部工作流仍可用。

## 最终媒体

| 交付文件 | 规格 | 音视频时长 | SHA-256 | 完整解码 |
| --- | --- | --- | --- | --- |
| `d4fdb668d417479e9d7d18597999a238.mp4` | H.264 720×1280 30fps；AAC | 视频 12.466667s；音频/容器 12.48s | `1245a21b2d8a13069aaa3a0fe64ddb93ff7120b106083eaa80be9c2470f0ff46` | `ffmpeg` 退出码 0 |
| `e498624a8d12463994b5774019be2741.mp4` | H.264 720×1280 30fps；AAC | 视频/音频/容器 10.80s | `30c58212ad26f1ce1118cd4fa17606e9710cbafdc31e68c55f1deaf4b9691a4d` | `ffmpeg` 退出码 0 |

使用 `ffprobe` 探测流和容器，使用 `ffmpeg -v error -i <file> -f null -` 完整解码。两张最终交付抽帧均显示商品画面与对应烧录字幕，不是占位文件、空壳容器或仅元数据记录。

## 桌面、键盘、移动和反馈

- 桌面：1280×900，项目导航、筛选、预览、审核历史和下载均可见。
- 键盘：聚焦“全部 5”后按 `Enter`，筛选由“已通过 2”切回全部五条，功能与鼠标一致。
- 移动：390×844，侧栏折叠为“打开导航”，审片筛选、五条作品、播放器、交付下载与审核区域均保留。
- 错误反馈：参考复刻流程同时显示“创作需求尚未填写”的提醒与“尚未添加镜头，无法交付”的需修复项，并提供定位按钮；最终生成边界返回稳定错误码与说明。
- 浏览器控制台无运行错误。Axe WCAG 2 A/AA 自动检查为 0 个 violations；React Flow 控件 ARIA 和被遮挡元素对比度有 2 个 `incomplete`（工具无法自动判定），未被冒充为已自动证明通过。

## 验证命令与结果

共享工作树在本票开始前已有未暂存的产品与文档改动。除票据 02 的豆包 Coding Plan/300 秒调用、调用前披露界面及其直接测试外，其余既有改动均未纳入本票暂存或提交。正式 UI 对真实试点的人工验收在该共享工作树中执行。为把提交内容与剩余未暂存改动隔离，自动化检查另在临时目录中执行：先 `git archive HEAD`，再仅应用本票暂存差异，并链接既有依赖目录。因此以下计数归属于“`6ea7bef` + 本次纳入的票据 02 依赖实现/测试 + 票据 01、02、06 证据”的干净快照。

```bash
# 工作目录：backend
.venv/bin/python -m pytest -q \
  tests/test_batch_review_api.py::test_review_lifecycle_and_version_isolation \
  tests/test_batch_voiceovers_api.py::test_voiceover_timing_tracks_and_review_invalidation \
  tests/test_batch_bulk_api.py::test_bulk_previews_isolate_failure_cancellation_and_retry \
  tests/test_batch_export_api.py::test_batch_export_gate_isolation_retry_and_history \
  tests/test_content_workflow.py::test_current_versions_roll_back_stale_preview_review_and_delivery \
  tests/test_content_workflow.py::test_mixed_variants_keep_independent_review_and_delivery_stages \
  tests/test_content_workflow.py::test_failed_variant_reports_issue_without_overwriting_siblings \
  tests/test_local_workflow_acceptance.py::test_local_workflow_from_upload_to_restore_and_cleanup \
  tests/test_asset_management.py::test_aigc_and_batch_references_share_the_delete_guard \
  tests/test_preproduction_boundary.py::test_final_generation_is_excluded_but_preparation_is_available
# 10 passed, 1 warning in 1.48s

# 工作目录：backend
.venv/bin/python -m pytest -q
# 1392 passed, 10 skipped, 1 warning in 63.69s

# 工作目录：frontend
npm test -- --run
# 45 files passed；455 tests passed

# 工作目录：frontend
npx tsc -b --pretty false
# 退出码 0

# 工作目录：frontend
npm run build
# 成功；保留既有 >500 kB chunk 提示
```

唯一 Python 警告为 Starlette `TestClient` 使用 AnyIO 旧别名的依赖弃用提示；没有测试失败。构建的 chunk 大小提示是既有非阻断警告，本票未扩大到性能拆包。

## 前序证据索引

1. 商品与素材依据：[票据 01 核实记录](01-pilot-product-and-assets-audit.md)；含真实商品、330mL 包装、CC0 原图、派生视频、哈希、许可边界和五项被排除历史素材。
2. 真实 AI 调用与五条候选：[豆包调用记录](02-doubao-call-attempt.md)、[五条候选与交接](02-five-doubao-candidates-and-handoff.md)、[调用前披露](02-pre-call-disclosure.md)。
3. 两条作品人工听看、修改、批准与导出：票据 03 记录，以及[重开后的两条批准导出](03-approved-exports-after-reopen.png)、[作品一最终预览](03-final-preview-1-contact-sheet.png)、[作品二最终预览](03-final-preview-2-contact-sheet.png)。
4. 素材引用保护：票据 04 的统一服务端查询与界面删除门禁，本轮又在真实试点恢复副本中复核。
5. 旧最终生成入口关闭：[票据 05 入口清单与验证](05-final-generation-submission-disabled.md)，本轮又在恢复项目中逐一复核六个写入口。

## 最终判定说明

P0 技术基线和 P0 真实商品试点交付均已满足本票的可验证完成条件。这里的“试点交付完成”仅指内部真实项目已经形成两份可播放、可追溯、经人工批准的最终 MP4；没有客户签收、广告平台发布或商业效果数据，因此商业交付验收明确标记为**未发生**。
