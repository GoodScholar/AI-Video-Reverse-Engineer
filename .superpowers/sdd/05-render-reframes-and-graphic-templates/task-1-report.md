# Task 1：主体分析与满幅裁剪报告

## 实现

- 新增 `backend/app/montage_reframe.py`：固定 schema、源 hash/尺寸/区间二次校验、人工 override 保留、源时间插值和 `sendcmd → crop → scale` 满幅轨迹。
- 新增 `backend/person_worker/run_reframe.py`：MediaPipe `0.10.21` EfficientDet Lite0 本地检测，输出归一化 boxes、中心、分数与保守 `review_required` 原因；不上传素材、不使用 YOLO。
- 混剪提案的每个首选 clip 现在包含 `review_required` 的 `reframe`，创建时通过既有 `compute_queue` 一次提交该 proposal 的 selected clips。采用发生在分析中时会另提交 variant CAS job；迟到结果只在 asset/inPoint/duration/speed/source hash 仍一致时写回。单 clip 复核入口：`POST /api/projects/{project_id}/batch-edits/{task_id}/variant/reframes/{clip_id}/analyze`，body `{revision}`。
- `TimelineClip.reframe` 可选；保存只做结构检查，预览/导出重新检查当前 source hash、真实尺寸、SAR/rotation、目标比例和样本覆盖。auto pending 阻断渲染；center/manual/replace 均不删除原 boxes 或 uncertainty。
- 渲染链为源时间 `sendcmd → crop → scale` 后才 `setpts`，每个 clip 的 crop 实例名唯一。长路径写入渲染目录下受控临时 cmd 文件并以 cwd 相对名引用；取消/完成后清理。

## 源码复用与许可

- 实际阅读并适配 OpenShorts `reframe_v2.py` / `main.py`，commit `89f030934388d5167b3b0ede164ef893dad09ae9`，MIT；仅采用平滑/sendcmd 的纯本地思路，排除 `cloud/` 和 YOLO。
- 实际阅读 FFmpeg `vf_crop.c`（`ff2059a`），据其运行时 crop 参数和 SAR 语义拒绝非方形像素/旋转源。
- 实际阅读 MediaPipe 0.10.21 `object_detector.py`，使用其 VIDEO API；模型固定 SHA-256 `2e04c53bfeac0ac2a30c057c7e2a777594ce39baaac35a92f74fb1e8c4fc4e0b`，Apache-2.0，来源与可重现安装说明在 `backend/MODEL_NOTICES.md` 和 `backend/person_worker/README.md`。

## RED / GREEN 证据

- RED：`/Users/shen/SZG/AI Agent/AI Video Reverse Engineer/backend/.venv/bin/python -m pytest tests/test_montage_reframe.py -q` → `ModuleNotFoundError: app.montage_reframe`（5 个新契约测试无法收集）。
- GREEN：同一命令 → `5 passed`。
- 受影响回归：`... python -m pytest tests/test_batch_editing_api.py tests/test_montage_planner.py tests/test_montage_reframe.py tests/test_timeline_render.py -q` → `82 passed, 1 warning`。
- 实际 FFmpeg：新增 `test_reframe_moves_a_full_bleed_crop_in_source_time_without_black_padding` 在 2× 速度、非零移动路径下通过，首帧红、末帧蓝且输出无 padding。
- 真实检测：原 person worker Python 对 OpenShorts `dashboard/public/demo/clip-source.mp4`（0–2 秒）→ `ready 5 640 364`，固定模型 hash 匹配。
- 全套 `pytest -q` 在执行环境 30 秒窗口被截断于约 43%，未取得完成结果；未将其声明为通过。

## 文件

`backend/app/montage_reframe.py`、`backend/person_worker/run_reframe.py`、`backend/app/timeline.py`、`backend/app/timeline_render.py`、`backend/app/montage_planner.py`、`backend/app/batch_editing_api.py`、对应测试和模型说明。

## 风险与后续

- 当前本地 worker runtime/model 需按 README 预装，故缺失环境被标为可重试失败且绝不隐式下载。
- Task 2/3 的包装、字幕避让 UI 与模板未包含在本 Task。

## 审查修复（round 1）

- 候选替换不再丢失 `reframe`：相同 asset/inPoint/duration 继承原分析；其他替换写入新的 pending 并自动重新入队。采用时仅在当前源 hash 相同的情况下保留既有 manual/center。
- 空指纹/0 尺寸的真实 `pending_reframe` 在 manual/center 时由当前渲染 probe 绑定几何和 hash；它仍保持 `review_required`、空 samples 和原 uncertainty，不伪造 ready。
- batch 与普通 timeline 都有唯一 analysis run id、queued/running/completed/failed/cancelled 投影、取消入口和写回 CAS；写回再次计算真实 source hash，源或 clip 身份改变会失败为可重试。
- 新增普通入口 `POST /api/projects/{project_id}/timeline/reframes/{clip_id}/analyze` 和 `/cancel`；timeline 预检现在报告 pending auto 或过期 reframe，而非等 FFmpeg 才失败。
- 轨迹按渲染 fps 重采样插值，所有 crop 实例唯一；worker 检查多人/丢失、过大主体框和疑似商品遮挡并保守 `review_required`。`
  render_timeline` 的 cmd 文件创建也已纳入 finally 清理。
- `dedupe_sendcmd_lines` 直接改编自 OpenShorts 固定提交，保留 copyright/MIT 于 `backend/THIRD_PARTY_NOTICES.md`；二维轨迹仅为两次调用和唯一实例名适配。

本轮 focused：`python -m pytest tests/test_montage_reframe.py tests/test_timeline_render.py tests/test_timeline_api.py tests/test_batch_editing_api.py tests/test_montage_planner.py -q` → `97 passed, 1 warning`（Starlette/AnyIO 的 `BlockingPortal` 弃用警告）。真实派生 QA 媒体：moving 结果 `ready/9 samples`；multiple 与 lost 均 `review_required`，分别具有“多个可能主体”和“未检测到唯一人物主体”原因。

## round1 补充回归证据

`test_pending_reframe_binds_center_override_to_the_current_real_source_without_inventing_detection_facts` 断言真实空 hash/0 尺寸 pending 的 center 渲染路径与未伪造检测事实。`test_timeline_reframe_analyze_cancel_and_retry_are_durable` 覆盖普通 timeline API 的 queued、取消、迟到不写回、retry、完成投影；`test_timeline_reframe_invalidates_when_source_changes_while_queued` 覆盖分析过程中 source hash 变化作废。命令：`python -m pytest tests/test_timeline_api.py tests/test_montage_reframe.py -q` → `22 passed, 1 warning`。

`test_batch_reframe_repeat_cancel_retry_and_source_cas` 覆盖 batch 片段重复提交的 token 隔离、取消后旧 job 不写回、重试完成和 revision 仅递增一次。命令：`python -m pytest tests/test_batch_editing_api.py -q` → `22 passed, 1 warning`。

## round1 proposal 回归补齐

本轮没有新的产品逻辑缺口；此前未完成四组 proposal API 回归是执行遗漏，不是接口、夹具或环境阻碍。为让异步时序可复现，`create_app` 现可显式注入既有 compute queue 所使用的 `reframe_analyzer`，测试以延迟队列执行真实 proposal 路由和 job handler。

- `test_montage_candidate_replacement_keeps_pending_reframe_and_queues_analysis`：候选替换后断言新 clip 保留真实 pending/review_required（非伪造 ready）、自动新增分析 job，执行 job 后提案 clip 为 ready。
- `test_two_clip_proposal_adoption_publishes_one_aggregate_reframe_revision`：两片段 draft 采用后同一 variant job 写回两个结果，revision 从采用后的 1 仅递增到 2。
- `test_pending_proposal_apply_publishes_to_variant_without_overwriting_later_manual_reframe`：分析 pending 即采用后，迟到 job 写入 adopted variant，并保留随后保存的 manual keyframe 及 worker samples。
- `test_reapplying_identical_source_preserves_manual_reframe_but_changed_range_or_source_does_not`：相同 asset/hash/range 重新采用保留 manual；仅 range 改变或换 asset 均回到 pending 的 auto override，不误继承。

GREEN：`/Users/shen/SZG/AI Agent/AI Video Reverse Engineer/backend/.venv/bin/python -m pytest tests/test_batch_editing_api.py -q -k 'montage_candidate_replacement_keeps_pending_reframe_and_queues_analysis or two_clip_proposal_adoption_publishes_one_aggregate_reframe_revision or pending_proposal_apply_publishes_to_variant_without_overwriting_later_manual_reframe or reapplying_identical_source_preserves_manual_reframe_but_changed_range_or_source_does_not'`（在 `backend/`）→ `4 passed, 22 deselected, 1 warning`；完整相关文件 → `26 passed, 1 warning`。warning 仍为第三方 Starlette/AnyIO `BlockingPortal` 弃用提示。

本轮首次命令从仓库根目录调用 backend 测试，因 Python 模块搜索路径未包含 `backend/` 报 `ModuleNotFoundError: app`；改在 `backend/` 后全部通过，非产品回归失败。

## round2 修复与证据

- 修复 worker 的半开区间：检测末帧是真实解码的 `end - 1/fps`，`normalize_reframe` 现在以实际相邻采样间距证明 `[start,end)` 覆盖，不再要求伪造 `end` 样本。`test_normalize_reframe_accepts_a_real_half_open_final_frame_only_when_sampling_cadence_covers_it` 同时拒绝采样间隔无法覆盖末尾的结果。
- proposal 采用不再覆盖 draft analysis token；当 identity 相同，原 proposal job 标记为 adopted，完成时原子写入 proposal 与 variant，且仅将 variant revision 增加一次。`test_two_clip_proposal_adoption_publishes_one_aggregate_reframe_revision` 和 `test_pending_proposal_apply_publishes_to_variant_without_overwriting_later_manual_reframe` 均只执行唯一的原 proposal job（队列长度断言为 1），不会以新的 variant job 掩盖移交。
- `run_local_detector` 将 stdout/stderr 落入临时文件，避免大 JSON 填满 PIPE；取消或超时会 terminate、超时后 kill 并 wait/reap。`test_local_detector_spools_large_child_output_and_reaps_cancelled_child` 用真实 Python 子进程验证 200KB 输出及取消回收。
- 分析写回重新取 hash 遇文件删除会变为 `failed` 可重试而不是 500/running；`test_batch_reframe_repeat_cancel_retry_and_source_cas` 与 `test_timeline_reframe_source_deleted_while_queued_becomes_retryable_failure` 分别覆盖 batch/timeline 删除源。启动恢复把遗留 queued/running reframe analysis 标为可重试 failed。
- worker 同时按目标 crop window 的宽、高比例检查超大主体；`test_worker_checks_height_against_the_real_full_bleed_crop_window` 覆盖竖源转横输出时小于 1 的真实 crop 高度。timeline preflight 改为调用和 render 相同的 crop builder，从而校验 target aspect、rotation/SAR 后的 probe 尺寸、hash 和 pending override。
- `analyze` revision 检查与 identity capture 已合并进 schedule 锁；manual 继承要求 asset/inPoint/duration/speed 和非空当前 source hash 同时匹配，空 hash pending 不会跨采用继承。

RED：round2 修复前，worker 对 0–2 秒 QA 视频返回最后样本 `1.966667`，旧 normalize 以 `end - .001` 拒绝；采用后原 draft job 的 record 被 variant job token 替换，无法发布。GREEN：`python -m pytest tests/test_reframe_worker.py tests/test_montage_reframe.py tests/test_batch_editing_api.py tests/test_timeline_api.py tests/test_timeline_render.py -q` → `93 passed, 1 warning`。

真实闭环（派生 QA `moving-real-source.mp4`，不分发）：使用固定本地 MediaPipe runtime/model、timeline API 的 `/reframes/clip/analyze`、队列 handler 与 `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg` 渲染，输出 `API_RENDER_READY 292827`；API analysis 状态为 `completed`，输出文件已由该命令在临时目录验证后清理。

## round2 补充可复核证据

- 启动恢复：`test_timeline_reframe_restart_recovers_queued_analysis_as_retryable` 先持久化 queued analysis、再重新构造 router，断言状态为 `failed` 且原因含“重启”，可重试。
- 原子 revision：`test_timeline_reframe_rejects_stale_revision_before_it_can_enqueue` 在 revision 由 1 保存为 2 后以 1 提交 analyze，断言 `409` 且 queue 为空；revision 比较和 identity capture 处于同一 schedule 锁。
- 继承边界：`test_proposal_same_asset_bytes_change_or_speed_change_never_reuses_manual_reframe` 断言 speed 改变不能继承 manual，且同 asset 字节变动后的已采用 proposal 不可修改（409，不会静默重用）；`test_reapplying_empty_hash_manual_pending_does_not_inherit_it` 断言空 hash pending manual 重采用后为 auto。先前 `test_reapplying_identical_source_preserves_manual_reframe_but_changed_range_or_source_does_not` 已覆盖同源保留、换源/换区间拒绝继承。
- 命令：`python -m pytest tests/test_timeline_api.py tests/test_batch_editing_api.py -q -k 'restart_recovers or stale_revision_before or same_asset_bytes or empty_hash'` → `4 passed, 43 deselected, 1 warning`。

可重复真实链脚本：`.superpowers/sdd/05-render-reframes-and-graphic-templates/qa-reframe-api-render.py`。从 `backend/` 运行：`AIVRE_PERSON_WORKER_PYTHON='/Users/shen/SZG/AI Agent/AI Video Reverse Engineer/backend/person_worker/.venv/bin/python' '/Users/shen/SZG/AI Agent/AI Video Reverse Engineer/backend/.venv/bin/python' ../.superpowers/sdd/05-render-reframes-and-graphic-templates/qa-reframe-api-render.py`。输出保留为 `/tmp/aivre-ticket05-round2-qa/api-render.mp4` 和 `result.json`：analysis `completed`、samples `[0.0, 1.966667]`、720×1280、时长 2.000000 秒、292827 bytes；FFprobe 命令见本轮执行记录。

## round3 修复与证据

1. adopted proposal writeback 重新校验 adopted variant 的 asset/inPoint/duration/speed 与当前 source hash；只有 manual override 可在身份一致时保留。`test_adopted_proposal_job_never_overwrites_variant_identity_edited_after_apply` 参数化 source/range/speed，且只执行原 proposal job，均断言 pending 未被覆盖、analysis failed。
2. `renderable_changed` 改为累计 OR。`test_adopted_multi_clip_job_increments_once_when_first_result_succeeds_and_last_source_is_lost` 删除第二素材，断言第一片 ready、第二仍 pending、variant revision 仅从 1 到 2。
3. worker 输出真实 `frameDurationSeconds=1/fps` 与 `samplingIntervalSeconds=1/sample_fps`；新 worker 输出不再由 samples 最大 gap 自证。`test_normalize_reframe_rejects_worker_timing_holes_and_long_tail_but_accepts_real_frame_tolerance` 覆盖 `[0,10]`/20 秒末端、内部 hole 拒绝及 24fps 末帧 `1.958333` 合法。
4. worker 以 `CAP_PROP_POS_FRAMES - 1` 的 CFR PTS 记录实际解码帧，重复/未知 frame 标记不可靠而非用请求时间覆盖。`test_worker_keeps_early_cfr_frame_pts_and_rejects_repeated_frames` 覆盖早帧与重复帧。
5. `_validate_reframe_media` 为预检和 render 共享门槛。`test_reframe_preflight_rejects_the_same_sar_or_rotation_sources_as_render` 参数化非方形 SAR 与 rotation，断言两边同样拒绝。
6. `test_proposal_same_asset_bytes_change_or_speed_change_never_reuses_manual_reframe` 先覆盖 speed，随后冻结检索 provenance 仅为抵达独立 hash gate、替换同 asset bytes、再次采用，断言旧 manual 为 auto（生产路径还会更早以 stale search 阻止）。

round3 GREEN：`python -m pytest tests/test_reframe_worker.py tests/test_montage_reframe.py tests/test_batch_editing_api.py tests/test_timeline_api.py tests/test_timeline_render.py -q` → `105 passed, 1 warning`。真实 QA 脚本已按 worker 时间元数据改动重跑：`result.json` 为 completed、`[0.0,1.966667]`、`/tmp/aivre-ticket05-round2-qa/api-render.mp4` 293079 bytes；ffprobe 720×1280，时长 2.000000 秒。

## round4 可信源时间合同修复与证据

### 根因与最小协议实现

- 根因是先前自动 `ready` 的时间连续性由 worker/客户端可提交的 `frameDurationSeconds` 与 `samplingIntervalSeconds` 推定；旧 `ready` 记录缺少这些字段时还会以样本最大间距回退。worker 也将 `CAP_PROP_POS_FRAMES / CAP_PROP_FPS` 当作 PTS，`avg_frame_rate == r_frame_rate` 的 VFR 视频没有被识别。
- `backend/app/montage_reframe.py` 新增不可变 `SourceTiming` 和 `probe_source_timing(source, ffprobe_path)`：一次 `ffprobe -select_streams v:0 -show_frames -show_streams` 读取解码显示顺序的 `best_effort_timestamp_time`。时间以首 PTS 为零点；重复/倒序 PTS、明显 VFR（逐帧间隔相对中位数的容差为 `max(1ms, 3%)`）、缺失或不能与中位间隔吻合的末帧 duration、ffprobe 失败均为 `cfr=False` 且携带原因。末帧优先 `pkt_duration_time`，缺失时只接受 `stream.start_time + stream.duration - lastPTS`。
- `run_local_detector` 将该服务端合同以私有临时 JSON 交给 worker；`backend/person_worker/run_reframe.py` 不再根据 fps 生成时间。它选择与半开 `[start,end)` 相交的首/末真实帧及不超过 2Hz 的中间帧，按指定索引读取并核验 OpenCV 实际返回的索引，写入对应 `PTS-firstPTS`。不可确认的时间/索引直接 `review_required`，不伪造 ready；采样上限同步调整为 640（300 秒 2Hz 的 601 个样本可用）。
- `normalize_reframe` 的 auto 门只读取 `SourceTiming`：每一个 sample 必须对应真实 PTS，首末必须是相交帧，间隔不大于 `0.5s + 可信帧时长 + 1ms`，并由首/末帧显示区间覆盖 clip。客户端 timing 字段只保留为旧 schema 的说明，完全不参与授权。旧 ready、无逐帧证据、VFR 或不匹配的记录投影为 `review_required`，追加“需重新分析源时间”，并保留 samples、boxes、reasons 与人工 override。
- timeline、batch 两个分析入口均以源 fingerprint 缓存 probe，向 worker/normalize 传入 server probe 和可信 ffprobe 尺寸；渲染 `_collect_clips`、预检和 `_video_filter → reframe_filter` 传递同一 SourceTiming。因此导出不能复用旧预览/批准绕过当前源 probe。`center/manual` 仍可在降级后渲染，且不删除检测 facts。
- timeline 与 batch `PUT` 都通过 `retain_server_reframe_facts`：客户端仅能保留原服务端 record 并修改合法 `override`，不能写入、替换或删除 detector facts/status。异步 CAS、人工 override 保护与恢复的结构读取保持不变。

### RED / GREEN 与回归范围

- RED：先添加 `SourceTiming` 契约测试后，`/Users/shen/SZG/AI Agent/AI Video Reverse Engineer/backend/.venv/bin/python -m pytest tests/test_montage_reframe.py tests/test_reframe_worker.py -q` 在收集阶段失败：`ImportError: cannot import name 'SourceTiming'`。这是缺少服务端 PTS 合同的预期失败。
- GREEN（第一次新增契约）：同命令 → `13 passed`。其中 `[0,10]` 不再能覆盖 `[0,20)`，worker 选择真实相交帧，不再使用 index/fps。
- 初次受影响回归发现 7 个旧夹具期望：它们以字节伪装的 `.mp4` 或只有 0/3 秒两点的“ready”作可信自动分析。按新合同这些必须 `review_required`，不是产品失败；保留其生命周期、CAS、manual 保护的原断言，只将 facts 改为服务端分析/真实 PTS 或将可信性断言更新为降级。渲染夹具改为真实 24fps PTS 的 0.5 秒采样，仍验证移动满幅路径。
- 最终 focused 命令（从 `backend/`）：`/Users/shen/SZG/AI Agent/AI Video Reverse Engineer/backend/.venv/bin/python -m pytest tests/test_reframe_worker.py tests/test_montage_reframe.py tests/test_batch_editing_api.py tests/test_timeline_api.py tests/test_timeline_render.py -q` → `108 passed, 1 warning in 10.47s`。唯一 warning 是第三方 Starlette/AnyIO `BlockingPortal` deprecation。
- 新增回归包括：名义速率可相等但 PTS 为 `0,.04,.10` 的 VFR 必须降级；普通与 batch PUT 篡改 `status=ready` 都返回 422；旧/伪造宽容 cadence 得到 `review_required`；真实 render fixture 的自动样本必须包含首相交帧、末相交帧和 2Hz 内部点。

### 真实媒体证据（产物保留）

- 原有 API→队列→MediaPipe→FFmpeg QA 重新运行：从 `backend/` 执行 `AIVRE_PERSON_WORKER_PYTHON='/Users/shen/SZG/AI Agent/AI Video Reverse Engineer/backend/person_worker/.venv/bin/python' '/Users/shen/SZG/AI Agent/AI Video Reverse Engineer/backend/.venv/bin/python' ../.superpowers/sdd/05-render-reframes-and-graphic-templates/qa-reframe-api-render.py` → `analysisStatus=completed`、samples `[0.0,1.966667]`、`/tmp/aivre-ticket05-round2-qa/api-render.mp4`（289013 bytes）。
- 真实媒体 `/tmp/aivre-time-evidence.2NuG7Y/`：由移动人物源生成 24fps 与 30fps MP4，二者容器第一显示 PTS 均为 `2.000000`；以 `inPoint=.3, end=2.1` 跑固定本地 worker/模型和 normalize。24fps 样本为 `[.291667,.833333,1.333333,1.833333,2.083333]`，30fps 为 `[.3,.8,1.3,1.8,2.1]`，均 `status=ready`。使用同一 reframe 直接 FFmpeg 渲染的非零入点产物为 `24fps-offset-inpoint-render.mp4`（248077 bytes，1.8s）和 `30fps-offset-inpoint-render.mp4`（260904 bytes，1.8s）。这同时验证非零首 PTS 已按 elapsed 归零、非整帧入点首/末相交帧和实际预览/导出路径。
- VFR 人工路径：`/tmp/aivre-vfr-evidence.cljayD/vfr.mp4` 由不规则帧选择生成；probe 返回 `cfr=False / 源视频为可变帧率或帧间隔不可信`。对保存的 `center` override 以 `inPoint=.1` 实际渲染成功，产物 `/tmp/aivre-vfr-evidence.cljayD/vfr-center.mp4`（199765 bytes）；原 `samples` 数量仍为 1、存储 facts/status 未被渲染投影删除。自动 override 在同一 probe 下会被统一闸门阻断。

### schema/API、许可与未扩展范围

- 持久化 schema 没有新增客户端可写字段；`frameDurationSeconds/samplingIntervalSeconds` 保持可读兼容但不再授权。worker 私有 `--timing-json` 仅包含服务端 frame index/elapsed/duration，不经 API 暴露或持久化。
- 更新 `backend/MODEL_NOTICES.md`：EfficientDet 精确来源为 Kaggle `tensorflow/efficientdet/tfLite/lite0-detection-metadata/1` 下载 API，官方列表标注 Apache2.0；本地模型路径、固定 SHA-256 和“人工下载、验 hash、绝不隐式下载”的复现步骤均明确记录。
- 未实现 VFR 重采样、额外队列或迁移；VFR/探测失败明确走人工路径。全套测试按任务边界未运行，交由 Task 3 统一执行。

## round5 scoped 最终修复与证据

1. **半开区间末帧使用统一整数时间基。** `person_worker/run_reframe.py` 和
   `app/montage_reframe.py` 现在都将 worker 持久化的六位 PTS、ffprobe PTS 与
   clip 边界量化为每秒 `1_000_000` 个整数 tick 后再做 `[start,end)` 相交、首末帧
   与采样间隔校验。它不是扩大浮点 epsilon：非零容器 PTS 的
   `4.066667 - 2.0 + .033333` 会精确落在 `2.100000`，故选中真正的末相交帧
   `2.066667`，而名义 `2.1` 起始帧仍排除。`test_worker_uses_the_ffprobe_microsecond_boundary_for_the_last_half_open_frame`
   和 `test_normalize_reframe_uses_the_same_integer_half_open_boundary_as_the_worker`
   都先以旧浮点实现 RED（worker 错选 index 3、normalize 降级），后 GREEN。

2. **尺寸只能来自可信媒体 probe。** timeline/batch 分析 job 在启动 worker 或接收
   注入分析器前都要求 `_probe_media` 给出正整数 width/height；probe 失败、无视频尺寸
   或素材在队列中删除会保留已有 reframe facts/人工 override，并把 analysis 记为可重试
   `failed`，而不是以 worker 的 `raw.sourceWidth/Height` 写出看似 ready 的记录。
   `test_timeline_reframe_never_accepts_worker_dimensions_when_the_source_probe_has_none` 与
   `test_batch_reframe_never_accepts_worker_dimensions_when_the_source_probe_has_none` 的 RED
   均为 `completed`，GREEN 均为 `failed` 且错误含“可信源尺寸”。此前生命周期测试的
   伪 `.mp4` 输入已替换为真实 64×48 CFR 视频，避免以假媒体掩盖该合同；真实普通
   分析仍能完成。

3. **历史 completed 交付重新经过同一 reframe 预检。** `batch_production` 在当前 preview
   与同版本的 queued/running/completed delivery 复用前接受 `reframe_safe` 回调；API 实现
   `require_current_reframe_safety`，仅在 snapshot 含 reframe 时以 `inspect_timeline_media`
   同时检查 frozen snapshot source 与当前项目 source。下载 completed delivery 也走该检查。
   当前 PTS/VFR/source-hash/尺寸使 auto 失效时返回可恢复的
   `batch_export_reframe_review_required`（提示重新分析，或选择 center/manual 后重新预览审核）；
   没有 reframe 的旧 delivery 不进入这条路径，center/manual 仍由既有显式人工覆盖门放行。
   `test_export_does_not_reuse_a_completed_frozen_delivery_when_current_reframe_preflight_requires_review`
   先 RED（不支持安全回调），后 GREEN，且断言不会用重新渲染代替验证；既有
   `test_export_reuses_the_current_version_delivery_without_copying_sources_again` 仍 GREEN。
   `test_completed_export_download_rechecks_real_reframe_sources_but_keeps_center_and_legacy_available`
   则通过实际 `/exports/{run}/output` 路由，以真实 30fps source、由
   `normalize_reframe` 生成的 auto facts、真实 frozen/current source 文件验证：初次下载成功，
   新 probe 仅将当前 source 降为 VFR 时 completed 下载为 409，改为 center 后恢复下载，
   无 reframe 的 legacy completed delivery 仍可下载。

4. **同源 timing probe 缓存。** `_collect_clips` 不再用会急切计算 default 的
   `dict.setdefault`，改为显式 cache miss。`test_collect_clips_probes_reframe_timing_once_per_source`
   RED 时同一 source 调用两次，GREEN 时仅一次；fingerprint 变更仍是新 key。

本轮受影响检查（在 `backend/`）：

`/Users/shen/SZG/AI Agent/AI Video Reverse Engineer/backend/.venv/bin/python -m pytest tests/test_reframe_worker.py tests/test_montage_reframe.py tests/test_batch_editing_api.py tests/test_timeline_api.py tests/test_timeline_render.py tests/test_batch_production.py -q`

结果：`130 passed, 1 warning`；唯一 warning 是第三方 Starlette/AnyIO
`BlockingPortal` deprecation。

真实 API→MediaPipe→FFmpeg QA：第一次复跑发现固定旧 QA 目录含已保存 timeline，脚本以
`revision:0` PUT 时发生预期 CAS 冲突；为保留旧产物将目录移动至
`/tmp/aivre-ticket05-round2-qa-round5-before`，再干净重跑同一脚本。命令：

`AIVRE_PERSON_WORKER_PYTHON='/Users/shen/SZG/AI Agent/AI Video Reverse Engineer/backend/person_worker/.venv/bin/python' '/Users/shen/SZG/AI Agent/AI Video Reverse Engineer/backend/.venv/bin/python' ../.superpowers/sdd/05-render-reframes-and-graphic-templates/qa-reframe-api-render.py`

结果保留于 `/tmp/aivre-ticket05-round2-qa/`：analysis `completed`，精确 samples 端点
`[0.0, 1.966667]`，`api-render.mp4` 为 `289013` bytes。现成非零 PTS 24/30fps 与 VFR
证据目录保持未改；round5 新逻辑以整数 tick 覆盖其同一半开区间合同。
