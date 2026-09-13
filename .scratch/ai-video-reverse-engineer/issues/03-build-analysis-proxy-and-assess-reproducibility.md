# 03: 生成分析代理并判断可复刻范围

**What to build:** 对已校验的参考视频执行本地预处理，生成分析代理，并明确判断素材是否处于 MVP 的可复刻范围。

**Blocked by:** 02/上传并校验参考视频

**Status:** ready-for-agent

- [x] 系统在本地完成视频解码、镜头检测、关键帧提取和运动数据计算。
- [x] 最高 4K 的参考视频使用低分辨率分析代理参与后续分析，而不改变本地保存的参考视频。
- [x] 分析代理包含后续语义分析所需的低分辨率关键帧组合和运动数据，不包含完整参考视频副本。
- [x] 用户能够看到本地预处理的阶段进度、完成状态和失败步骤。
- [ ] 系统能够识别多镜头、多个主要主体、复杂交互或超出轻中度运动等超出可复刻范围的特征。
- [ ] 超出可复刻范围不会阻止生成分析报告，但界面明确说明不承诺生成可靠的可执行工作流。
- [x] 本地预处理失败时提供具体错误和重试入口；成功产物在后续步骤失败时仍被保留。
- [x] 重新打开复刻项目后，已完成且仍有效的本地预处理无需重复执行。

## Comments

- 2026-09-11：需求规划已确认。设计规格见 `../03-build-analysis-proxy-and-assess-reproducibility-design.md`；实施计划见 `../03-build-analysis-proxy-and-assess-reproducibility-implementation-plan.md`。Ticket 03 只本地确认多镜头和运动强度，主要主体数量与复杂交互留待 Ticket 04 语义分析确认。
- 2026-09-11：Task 9 验收记录。设计规格见 `../03-build-analysis-proxy-and-assess-reproducibility-design.md`；实施计划见 `../03-build-analysis-proxy-and-assess-reproducibility-implementation-plan.md`；完整执行报告见 `../../../.superpowers/sdd/03-build-analysis-proxy-and-assess-reproducibility-implementation-plan/task-9-report.md`。自动验证：后端 `161 passed, 6 skipped`（1.28 秒）、前端 `68 passed`（3.30 秒）、颜色对比度通过、生产构建通过（0.48 秒）。真实 1280px 浏览器已确认：真实 3 秒 640×480 MP4 上传后不自动开始；仅用键盘 Space 启动返回 202，随后显示 `ffmpeg_filters_unavailable`/解码失败；Tab + Enter 重试同样可用，页面无控制台错误。1024px、1023px、390px 的完成态/关键帧失败态展示、只读边界和无横向滚动使用网络受控状态验证，已与真实处理证据分开记录。性能、真实完成态、真实硬切/高运动分类、产物目录和代理脱敏检查受本机所有可用 FFmpeg 均缺少 `drawtext` 阻塞：`/opt/homebrew/bin/ffmpeg` 缺该滤镜；`/Applications/TRAE SOLO CN.app/Contents/Resources/app/bin/ffmpeg` 也只启用 `scale/fps`。未将跳过项或受控状态标为真实处理通过；Ticket 保持 `ready-for-agent`，其余未勾选项等待具备完整滤镜的本机重新验收。
- 2026-09-12：最终审查结论为 **Approved with environment blocker**。已关闭六类缺陷：完成态产物校验与只读投影、副作用清理隔离、FFmpeg stderr 脱敏、运动样本持久化、稳定错误码映射、范围说明与 README 边界一致性。最新主控验证：后端 `176 passed, 6 skipped`（1.25 秒）、前端 `68 passed`（3.33 秒）、颜色对比度通过、Vite 构建通过（457ms）、`compileall` 通过。环境 blocker 与真实媒体未满足项不变；Ticket 继续保持 `ready-for-agent`，其余勾选及历史记录未改。
- 2026-09-12：完整 FFmpeg 真实重验发现产品缺陷，已按“发现即停止”原则终止后续验收，未修改生产代码。`/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg` 9.0.1 的五个必需滤镜均已实测存在；但真实 FastAPI/Vite 浏览器路径中，合法的 3 秒 640×480 30fps 单镜头 MP4（项目 `4292b2f8-ac73-471b-ab01-d8135d64bf3f`，预处理 `aae73197-21ff-4826-9d5b-41cd7cbc5141`）在解码、镜头检测完成后，于关键帧提取失败。独立诊断记录显示最后采样点 `-ss 2.967` 的 MJPEG 编码因 `Non full-range YUV is non-standard` 返回 234；完整真实证据、截图、样本哈希、命令和环境信息见 `../../../.superpowers/sdd/03-build-analysis-proxy-and-assess-reproducibility-implementation-plan/task-9-report.md` 的“完整 FFmpeg 真实重验”章节及同目录 `task-9-evidence/full-ffmpeg-real-revalidation-20260912/`。因此没有勾选任何新增验收项；硬切/高运动、恢复/替换、响应式、4K 性能、成功产物边界和本轮最终全量验证均未执行或宣称通过。主体数量和复杂交互仍明确留给 Ticket 04；Ticket 状态依分流规则保持 `ready-for-agent`。
- 2026-09-12：末帧关键帧缺陷已最小修复并在完整 FFmpeg 9 环境完成真实重验。1280px 真实单镜头完成五阶段，刷新/重新打开后结果复用，重复 `POST` 返回 `200` 且复用同一预处理 ID；真实硬切（2 个切换点）和真实高运动（P90 `14.123`）均正确为 `out_of_scope`。注入关键帧失败保留前两阶段并从失败阶段恢复；运行期间替换返回 409、失败替换保留旧结果、成功替换清空结果。1024px 可操作，1023px 与 390px 真实完成态只读且无横向溢出；补拍的全页图和树记录已确认状态、阶段、摘要、结论完整可达。4K 10 秒/30fps 三轮均完整完成，总时长 `3.716924s`、`3.693651s`、`3.387974s`，中位数 `3.693651s`。原视频 SHA-256 不变，完成目录无视频副本，精确递归扫描未发现原文件名、项目名、绝对路径、凭据或供应商字段；仅 `contact-sheet.jpg` 与 `analysis-proxy.json` 属于可发送代理。最终主控验证：后端 `183 passed`（零跳过，10.16s）、前端 `68 passed`（3.50s）、颜色对比/生产构建/compileall 通过。完整索引和原始证据见 `../../../.superpowers/sdd/03-build-analysis-proxy-and-assess-reproducibility-implementation-plan/task-9-report.md`。Ticket 03 已完成其批准的本地多镜头与运动分流、代理生成和范围说明；复合验收项中的主要主体数量、复杂交互判断，以及“分析报告”生成，均属于 Ticket 04 的语义分析范围，因此两项复合验收保持未勾选。状态仍使用项目既定的 `ready-for-agent`。
