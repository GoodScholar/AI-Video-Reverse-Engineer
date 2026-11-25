# 05: 实现竖屏裁满、花字和字幕模板

**What to build:** 让混剪提案和现有编辑序列为每个镜头保存主体感知裁剪路径，并使用版本化包装模板渲染关键词花字、动态标题、字幕和片尾行动引导。横屏或其他比例素材进入 9:16 时应优先裁满并保持人物或商品主体完整；主体不明确时要求人工处理，不能用黑边冒充完成。

**Blocked by:** 03: 生成并采用可解释混剪提案（已完成）。

**Status:** resolved

- [x] 系统为候选片段生成带源时间坐标的主体框、平滑裁剪中心、目标比例、置信度和不确定原因。
- [x] 9:16 预览和最终导出使用同一裁剪路径，画面裁满且不产生非预期黑边，移动主体不会出现明显抖动或持续越界。
- [x] 主体无法安全裁满、多人冲突或商品被遮挡时，系统标记待人工构图，并提供中心裁剪、手工关键点或更换候选的明确选择。
- [x] 人工裁剪覆盖保存到当前变体版本；上游重新分析不得静默覆盖人工决定。
- [x] 包装模板统一保存字幕、关键词花字、动态标题、卖点强调、片尾行动引导、安全区、字体引用和模板版本。
- [x] 用户可以编辑、禁用或替换单个花字和字幕提示，而无需重新生成片段索引或混剪提案。
- [x] 普通字幕、花字和片尾元素在实际预览及最终导出中位置、时序和视觉样式一致，并避开主体和平台安全区。
- [x] 字体、模板及可选覆盖素材必须记录来源和商用授权；未知或缺失字体不得静默替换后继续授权旧审核。
- [x] 裁剪路径、包装模板、字体、字幕或花字改变后，依赖的旧预览、质检和人工批准正确失效。
- [x] 实际媒体测试覆盖横转竖、移动主体、主体丢失、多人冲突、字幕安全区、关键词花字、片尾行动引导和预览/导出一致性。

## Comments

2026-09-30 用户确认执行；复用任务 04 隔离工作区，从 `6bb8bf51d23e1f425db3a30764d36d9cebff1abb` 建立 `codex/montage-reframes`，不修改主工作目录其他未提交内容。

源码核查（实现前）：

- OpenShorts `89f030934388d5167b3b0ede164ef893dad09ae9`：实际阅读 `reframe_v2.py` 的轨迹分析、人工覆盖、sendcmd、渲染全流程，`main.py` 的 SmoothedCameraman 和 face detector，`dashboard/src/components/ReframeEditor.jsx` 的原片播放、拖动遮罩、恢复与保存交互。根目录 MIT，排除独立商业许可 cloud 及 YOLO 权重。优先复制适配平滑/裁剪命令和构图交互；不用其不确定时留边及全局 main 导入，避免引入无关云服务/YOLO。
- FFmpeg `ff2059afd90c46d7e1b0f92542b204accb408ffb`：`libavfilter/vf_crop.c` 的逐帧时间表达式、范围处理和 command，`vf_subtitles.c` 的 ASS/fontsdir/原始尺寸烧录；LGPL-2.1-or-later 源码，仅调用已安装二进制，不复制 C 实现。本机 `/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg` 提供 ASS，默认 ffmpeg 不提供，环境缺失须显式报错。
- MediaPipe AutoFlip `9519bb59bf55fc6a79ed5b9f283d72e6cdfb6678` 的 `scene_cropping_calculator.cc`：检测→关键帧区域→平滑镜头路径→裁剪/填充流程，Apache-2.0。其完整 C++ 图与现有 Python/FFmpeg 不兼容且带填充策略，不迁入整套运行时。
- MediaPipe `v0.10.21` 的 `tasks/python/vision/object_detector.py`：实际核查 RGB 输入、源图坐标框、置信度、VIDEO 时间单调约束及 create/detect 流程，Apache-2.0。复用现有 person_worker Python 环境，通过独立本地进程接入，不能将模型分数当作商品身份/遮挡已验证。
- TensorFlow EfficientDet `tfLite/lite0-detection-metadata/1` 权重：官方下载 `https://www.kaggle.com/api/v1/models/tensorflow/efficientdet/tfLite/lite0-detection-metadata/1/download`，模型 SHA-256 `2e04c53bfeac0ac2a30c057c7e2a777594ce39baaac35a92f74fb1e8c4fc4e0b`。Kaggle 官方 models/list API 中该精确变体明确 `licenseName: Apache 2.0`。已在本地真实视频帧检出人物；支持 COCO 类别，不把未知商品冒充可靠识别。
- libass `f61db567e6593df3470e91594bcd4ad2d0473aff`：`libass/ass_fontselect.c` 实际核查字体匹配和自动 fallback；ISC。通过 FFmpeg 集成；应用在进入渲染前验证字体文件 hash、字体名称及字形，禁止静默系统替换。
- OpenShorts 同一固定提交 `subtitles.py` 的 `generate_ass`、`subtitles_filter`、ASS转义/颜色/动画：实际核查字幕块→Style/Events→ASS烧录流程。优先适配其ASS头与动画标签；时间舍入边界、默认Verdana/font fallback不沿用，按本项目字幕正本、Noto白名单及主体安全区替换。
- Noto CJK `f8d157532fbfaeda587e826d4cd5b21a49186f7c`：`Sans/LICENSE` OFL-1.1，原版 `Sans/OTF/SimplifiedChinese/NotoSansCJKsc-Regular.otf`，SHA-256 `2c76254f6fc379fddfce0a7e84fb5385bb135d3e399294f6eeb6680d0365b74b`。保留字体内版权和随附许可，固定引用，不替换成机器字体。

环境补充：现有 `main.resolve_default_binary` 已支持 `FFMPEG_PATH`，并优先检测 Homebrew ffmpeg-full，因此不新增二进制解析层。Noto字体内元数据已读：family `Noto Sans CJK SC`，Version 2.004，© 2014-2021 Adobe，44810 个cmap字符；实际字体中含目标中文。现有person_worker有fontTools 4.65.0，主backend测试环境尚未安装，若需要新增正式依赖须记录在pyproject/锁中。

字体校验依赖候选：PyPI官方元数据核验 `fonttools==4.60.2` 为MIT、`requires_python >=3.9`，与backend合同兼容；使用cmap解析而不自行实现字体解析或用tofu像素猜测缺字。Task2按实际需要落依赖并更新现有锁。

字体实际度量补充核查：同一固定libass的 `ass_font.c` 已读 `set_font_metrics`、`ass_face_set_size` 和 `ass_font_get_asc_desc`，使用OS/2 Win ascent/descent及FreeType REAL_DIM。实际Noto文件为 unitsPerEm=1000、WinAscent=1160、WinDescent=288，PostScript名 `NotoSansCJKsc-Regular`。布局应使用实际advance/外框及相同缩放，考虑描边和动画，不以字数或CSS估宽；实际烧录像素边界仍需验收。

Task2接口核查补充：批量字幕已有cue.id，但识别parse_srt会复用line-1等ID，识别入库需增加运行身份以免新识别误绑旧样式。批量subtitle提示必填cueId，仅覆盖enabled/position/style，正文与时序由原cue编辑；普通时间线没有独立cue正本，手动subtitle元素自身text/start/end就是正本，不能出现悬空cueId。ASS控制符不能被执行或静默改成其他正文：已读libass同版本 `ass_render.c:parse_events` 和 `ass_parse.c:ass_get_next_char`，其支持反斜线转义左右花括号；反斜线后N/n/h会解释成控制符，编译器须明确处理并有实际渲染回归。布局以实际字体度量，并将有效时段内的源主体框经crop映射到目标，转场考虑两侧主体；放不下必须待人工。

媒体夹具：`/tmp/aivre-ticket05-media.aUh36E/` 中三段4秒、30fps、1280×720测试片由固定OpenShorts的 `dashboard/public/demo/clip-source.mp4` 经真实FFmpeg转成横向平移（moving-real-source）、双主体并列（multiple-real-source）、1.5秒后消失（lost-real-source）。仅本地QA，不入库分发，不是客户金标，也不以mock检测框代替实际worker；验证结果待补。

实施计划与接口将在本记录下维护，不另建重复计划文档。完整业务规格：原工作目录 `.scratch/commercial-ai-montage/spec.md`。

## 实施约定

**Goal:** 可编辑的主体感知满幅裁剪与合法字体包装，共用预览/导出，任何相关变化使审核失效。
**Architecture:** 视频 clip 承载源时间 reframe；编辑序列顶层承载 packaging。现有变体 revision、compute_queue、快照、渲染与审核机制继续作为正本。
**Tech Stack:** Python/FastAPI、MediaPipe 0.10.21 ObjectDetector、FFmpeg crop/libass、React/TypeScript。
**Spec:** `/Users/shen/SZG/AI Agent/AI Video Reverse Engineer/.scratch/commercial-ai-montage/spec.md`，本票全部十项验收。

### Global Constraints

- 先读固定参考功能源码再实现，复制/集成可用部分，保留许可；不引入云上传、YOLO、外部账户或付费服务。
- 使用同一项目/素材/编辑序列/变体/渲染/审核系统；旧文件不静默改写。新混剪提案必须携带待分析或已分析裁剪状态；历史序列可显式启用，不能把未分析声明为主体安全。
- 采用 proposal 或重新分析不能丢弃用户已保存人工构图；素材身份/范围/目标比例变化须报过期或待处理，不把旧轨迹当新分析。
- 所有验证和媒体证据只能声明实际通过的范围；Task 1/2 完成不等于票05达标。
- TDD：先补行为回归并记录 RED，再实现至 GREEN。完整后台/前台套件在最后统一运行，迭代只跑受影响检查。代码实现不自行启动子代理。

### Review Focus

- 非零入点、倍速、转场交叠下源时间映射正确；首尾点和源范围完整覆盖。
- 异步分析迟到/取消不能覆盖新草稿或人工构图；模型缺失/损坏不能伪成功。
- 商品无法确认、失踪、多主体冲突都保留原因，不用最高分任意挑一个冒充安全。
- 字体坏 hash、缺字、ASS 控制字符/路径转义、安全区放不下均显式处理。
- 旧客户端省略可选字段、备份恢复、配音重排、替换素材不能悄悄丢失包装或恢复旧批准。

### Task 1: 主体分析、源时间裁剪与真实满幅渲染

**Files:** 新建 `backend/app/montage_reframe.py`（校验、平滑、路径采样/FFmpeg适配）、`backend/person_worker/run_reframe.py`（本地对象检测）；修改 `timeline.py`、`timeline_render.py`、`montage_planner.py`、`batch_editing_api.py`、`main.py` 和必要的既有生命周期调用；新建 `backend/tests/test_montage_reframe.py` 并补相关API/渲染测试。小型路由模块可以单独新建，避免继续堆入巨大文件。

**Interfaces:** 视频 `TimelineClip.reframe` 可选，固定结构：`schemaVersion:1, sourceFingerprint:string, sourceWidth:int, sourceHeight:int, targetAspect:{width:int,height:int}, detectorVersion:string, modelSha256:string, samples:[{timeSeconds:number, boxes:[{x,y,width,height,label,confidence}], center:{x,y}, confidence:number}], uncertaintyReasons:string[], status:'ready'|'review_required', override:{mode:'auto'|'center'|'manual'|'replace', keyframes:[{timeSeconds,x,y}]}`。时间均为素材源秒，框/中心归一化；有效中心必须使满幅裁剪窗在源内。未分析提案允许空samples/空指纹/0源尺寸，但必须review_required并给原因；完成分析必须精确正尺寸、hash及区间覆盖。存储状态不是授权，渲染重新验证几何与当前素材。

- [ ] 阅读上文固定源码和本地既有API/队列/快照实现；复制适配OpenShorts平滑/sendcmd的适用部分，注明修改，排除YOLO/cloud。
- [ ] RED：测试source时间2→4且speed2、moving主体稳定/在窗内、miss/conflict/过大框→review_required、显式center/manual解除待处理、NaN/越界/乱序/源不匹配拒绝、重新分析保留override。
- [ ] 实现真实本地EfficientDet检测，复用person_worker环境。官方精确模型已在 `/tmp/aivre-ticket05-source.d3RLVs/1.tflite`，固定hash与许可见上。将权重安装到可忽略本地models并提供固定版本/hash验证的可重现安装说明或既有安装脚本，不隐式下载。允许测试注入检测器，但生产必须调用真实worker；读取图像/视频，保留源坐标和真实分数。丢失/类别不明/商品可能遮挡等保守转人工；不声称能确认任意商品。
- [ ] 使用现有计算队列实现 `POST /api/projects/{project_id}/batch-editing/tasks/{task_id}/variant/reframes/{clip_id}/analyze`（最终前缀以现有router为准），body `{revision}`，CAS绑定片段身份和源hash，投影状态在variant.reframeAnalysis，既有poll获得进度；失败/取消/过期可恢复。普通时间线需等价入口或共享分析请求，不要求先创建批量任务。返回payload沿用现有投影，不新建任务系统。
- [ ] 生成/替换混剪候选包含reframe状态，当前手工覆盖不能被同源重分析/再次采用静默覆盖。未知构图阻断带reframe的可见视频渲染；更换候选由已有替换路径消费。显式center是用户接受风险，不改写检测事实。
- [ ] 已选候选的真实分析接到提案生成/采用流程，不能仅放永久pending字段并要求用户逐片启动；异步分析有进度/取消/失败原因，未选候选可在替换时分析，显式analyze仍用于复核和历史序列。
- [ ] 修改同一timeline_render的视频链为path→crop→scale，保留转场/音频；路径在setpts之前源时间或正确映射到局部时间。无padding；预览与成片同样坐标轨迹。校验旋转/SAR/实际尺寸，无法支持明确拒绝不悄悄裁错。
- [ ] GREEN：运行专用/受影响后端tests，实际FFmpeg视频验证非零入点/倍速/移动路径、无黑边、manual保护、API CAS及取消。真实检测另跑可用媒体留证。不跑无关全套。自检并提交，报告RED/GREEN命令及输出、源码复用、接口和剩余后续切片。

### Task 2a: 包装接入前的交付安全校验边界

Task1第五轮规格通过，但交付门在全局锁内反复逐帧probe，质量问题未闭合；本项是Task2的前置步骤，不代表票05达标。现有已通过功能保持，先修这个后续字体/模板交付校验也依赖的边界。

**Files:** `backend/app/batch_editing_api.py`、`batch_production.py`、必要现有render预检缓存接口；相关API/production回归。不做ASS、字体或前端。

- [ ] 读现有submit_exports/download、export_approved_versions及require_current_reframe_safety实际源码。持锁仅取一致快照和提交；昂贵probe/内容hash/媒体校验在锁外完成。锁内以revision、preview/run身份和源文件身份CAS再次确认；期间发生编辑/源替换则明确可重试冲突，不交付过时证明。
- [ ] 复用现有两阶段导出及预检，不建新任务/导出系统。允许回调在锁内消费预计算证明、做轻量CAS，但不能再次启动ffprobe或完整读取素材。证明只在本请求有效；按实际源指纹、裁剪/快照身份memoize，同源多变体不重复逐帧探测。不能跨请求缓存过期授权。
- [ ] 若采用源文件stat作锁内快速复核，证明须在锁外对已解析路径读取hash并确认前后stat一致，锁内核对同路径/dev/ino/size/mtime_ns/ctime_ns及项目素材引用；变动/消失立即失效。不能仅相信客户端/source record自报hash。优先复用已有同等强度源身份工具，避免重复体系。
- [ ] RED/GREEN覆盖实际submit与completed download：阻塞probe期间普通读取/取消能完成；探测期间revision/preview/run/source任一变化拒绝交付；同请求多个变体共用源的probe次数有界且不因两阶段回调翻倍；无reframe legacy、有效自动构图、显式center/manual仍正常。已有130项相关结果按变动范围选择复用/重跑，不跑全套。
- [ ] 完成后自检提交并独立检查。本项关闭前，不把Task1质量或完整票05标记通过；关闭后继续Task2包装，不再询问用户是否继续。

### Task 2: 版本化包装、严格字体与同源 ASS 合成

**Files:** 新建 `backend/app/montage_packaging.py`（模板、严格校验、ASS编译/布局）；修改timeline/batch_production/batch_editing_api/timeline_api/timeline_render/project_backup中的保存/冻结/校验入口；字体/许可资源和部署说明；新增专用包装与渲染/生命周期回归测试。

**Interfaces:** 顶层 `packaging` 可选：`schemaVersion:1, templateId:'commercial-clean', templateVersion:1, font:{id:'noto-sans-cjk-sc-regular',version:string,sha256:string,source:string,license:'OFL-1.1'}, safeArea:{left,right,top,bottom}, elements:[{id,kind:'subtitle'|'keyword'|'title'|'selling-point'|'cta',enabled:boolean,start,end,text,position:'auto'|'top'|'middle'|'bottom',style:'plain'|'emphasis'|'banner'}]`。比例为0..1内边距；字体引用白名单且只从已核验资产解析。字幕cues仍正本，packaging中subtitle元素用稳定cue关联/覆盖（实现时在该接口追加明确cueId字段，不复制独立字幕正本）；普通字幕默认套用模板。

- [ ] RED：保存单元素/禁用/替换不改索引与提案；任何path/package/font/cue变更旧preview/review失效；未知模板/字体/hash/缺字拒绝；ASS文本转义；同时间多元素与主体/安全区冲突处理。
- [ ] 读固定libass/FFmpeg源码及许可，集成真实ass filter。Noto字体下载核验在 `/tmp/aivre-ticket05-source.d3RLVs/NotoSansCJKsc-Regular.otf`，许可同目录NotoSans-LICENSE；保留版权/来源/版本。不能用系统字体fallback，验证字符cmap与font hash，实际渲染不能替代未授权字体。
- [ ] 将package接入现有PUT原子保存/快照/预览/导出/备份；省略字段保留已保存值，显式null可关闭包装并失效。字体不可用时不能保持有效审核可交付。当前clip.reframe随tracks进入所有快照；记录源身份/资产一致性。
- [ ] 新混剪提案保存模板引用及按已确认脚本/卖点/CTA生成的初始包装提示，采用时一并进入variant；标题/关键词/卖点/片尾不编造商品事实。空信息留空不编造，已有手工包装不被再次采用静默替换。用户可另在历史序列启用模板。
- [ ] 一个ASS编译器支持字幕、关键词、动态标题、卖点、CTA，并共享preview/export。统一目标PlayRes/归一化安全区/主体轨迹避让；无可用区域明确待人工，不能静默遮挡/裁字。限制模板/动画，不实现任意脚本执行。FFmpeg缺libass明确环境错误，并提供配置本机ffmpeg-full的方法，不改系统symlink。
- [ ] GREEN：实际字幕烧录与花字、CTA视频，比较preview/export归一化位置/时序/样式；同帧主体不遮挡；不合法字体/缺字/文本注入/异常取消。运行受影响tests，自检提交并报告。

### Task 3: 编辑交互、全链路验收与交付记录

**Files:** `frontend/src/BatchEditor.tsx`、`TimelineEditor.tsx`（实际路径用rg定位）、共享ReframeControls/PackagingControls及类型/API、对应CSS和组件测试；必要后端集成修复、`DESIGN.md`、本任务记录。沿用既有视觉，不重设计整站。

**Interfaces:** 消费Task1 `clip.reframe` 与分析API、Task2 `packaging` 与既有原子PUT；使用原video/image素材URL、既有候选替换和字幕编辑入口。不得独立创建伪实时包装预览，最终核对使用真实渲染预览。

- [ ] 阅读固定OpenShorts ReframeEditor.jsx实际交互并直接适配可用的原片播放+拖框/外部暗区结构，保留MIT；增加源时间keyframe选择/增删、中心裁剪、重分析、待人工原因和更换候选入口，支持键盘/窄屏/取消/草稿保护。
- [ ] RED组件测试：人工点保存恢复、分析失败/迟到不覆盖草稿、单个包装文本编辑/禁用/替换、字幕覆盖、字体错误可见；BatchEditor与普通TimelineEditor共用控件/同接口，不只后端能用。
- [ ] 接入模板选择、safeArea、五类元素编辑及原有字幕正本；字段改变只更新草稿，保存后旧预览/批准失效；重载和备份恢复不丢字段。更新DESIGN商业满幅例外，不改其他页面默认contain。
- [ ] 实际媒体验收：横转竖、移动/丢失/多人、手工保留、商品遮挡/未知需人工、关键词/动态标题/卖点/字幕/CTA、安全区及主体避让、字体失败与审核失效、preview/export一致；包括真实浏览器操作而不是空页面截图。保留源/模型/字体/FFmpeg版本及产物证据路径。
- [ ] 运行全部后端pytest、前端vitest和build；只修本任务造成的问题，记录既有警告。自检提交。独立规范与规格审查后逐项关闭十项验收；未通过项不能勾选或称商用完成。

## 最终实施候选（2026-10-01，固定候选终审通过）

- 实现范围：视频与静态图片的服务端主体事实、源时间/单帧裁剪、人工覆盖与替换保护；普通时间线和批量变体共用分析、预览、审核、导出；五类包装、字幕 cue 正本、严格 Noto 字体、ASS 合成、安全区及主体/转场避让；共享前端裁剪和包装控件。
- 静态图片补充：固定 MediaPipe IMAGE 模式真实检测，唯一 0 秒样本与人物裁窗包含校验；真实 ordinary API 自动 ready 成片、普通 TimelineEditor 人工保存/刷新/预览无黑边、bulk child 人工构图→预览→审核→parent export 均留证。未知人物/商品仍保守转人工。
- 交付锁：源媒体与包装字体/事件证明均为锁外准备、锁内按 revision/run/source/font/snapshot/cue 做轻量 CAS；项目备份也采用两阶段 proof，恢复仍严格校验。请求内相同包装输入只准备一次证明。
- 固定来源/许可：OpenShorts `89f0309` MIT；MediaPipe `0.10.21`/EfficientDet Lite0 Apache-2.0，模型 SHA-256 `2e04c53b...c4fc4e0b`；FFmpeg `ff2059a` LGPL 调用；libass `f61db56` ISC；Noto CJK `f8d1575` OFL-1.1，字体 SHA-256 `2c76254f...365b74b`。对应 notice/许可及直接适配范围已保留在代码和报告。
- 最终自动检查候选（`7c0e2bb`）：后端完整 `1647 passed, 10 skipped`（`/tmp/aivre-ticket05-round5-final4-backend-pytest.log`）；前端 `49 files / 509 tests`（`/tmp/aivre-ticket05-round5-final3-frontend-vitest.log`）；build exit 0（`/tmp/aivre-ticket05-round5-final3-frontend-build.log`），仅既有 bundle-size 提示；`git diff --check 6bb8bf5..7c0e2bb` 通过。长片段/压缩/备份/包装聚焦 `121 passed`，最终 reframe/packaging `77 passed`，Task23 真实媒体包含在全量并通过 `6/6`。
- 真实媒体证据：`/tmp/aivre-ticket05-evidence/task23/`（五类包装 HTTP/像素矩阵、移动主体/转场、失败清理）；`/tmp/aivre-ticket05-fix3/`（parent→child 字幕 preview/export）；`/tmp/aivre-ticket05-image-api-qa/` 与 `/private/tmp/ticket05-ordinary-image-*`（图片自动/人工满幅）；Task 1/2/3 与 final acceptance 报告在 `.superpowers/sdd/05-render-reframes-and-graphic-templates/`。
- 第一轮全分支双审：Standards ✅；Spec ❌，发现普通时间线无 inline 字幕新增入口，以及无事件内主体事实的人工构图仍可授权包装避让。`4a78945` 增加普通/批量显式字幕模式并阻断无有效主体观测的包装事件；`031870e` 将 3 条旧渲染夹具改为真实可见对象与对应 PTS 事实。原有 preview/export 位置、时序、样式像素阈值全部保留，并增加文字与实际主体像素不相交证据。
- 第二轮全分支双审：Standards ❌、Spec ❌。开放差异为：静态图媒体类型只靠渲染临时字段，预检/下载证明会误拒；review-required 视频只需事件内一个主体样本，未证明整段时段；实际媒体断言先遮罩主体再判断文字不相交；标题只有静态位置而没有受控动态效果。
- `8be589b`/`8323a2f` 闭合第二轮差异：服务端 `assetId → image/video` 上下文贯穿预检、证明摘要/消费、渲染、普通与批量下载和备份，渲染时以实际 FFprobe 事实复核；review-required 视频只接受 `frameDurationSeconds` 的真实半开帧区间并集完整覆盖，不把分析采样间隔当延长；标题模板固定 `\fad(180,180)`；媒体证据在标题早/中/晚验证 preview/export 淡入淡出一致，并对五元素及转场红/蓝主体做未遮罩 IoU、保留率、像素数与 RGB 差值检查。合法静态图的 0 秒主体事实新增普通时间线保存→预检→渲染→completed 下载回归。
- 第三轮全分支双审：Standards ❌、Spec ❌，共同发现事件完全位于某真实帧内部时，覆盖校验认可 `[PTS, PTS+frameDuration)`，但主体投影只接受 PTS 落入事件，可能漏掉该帧主体；Spec 另指出短标题仍固定 `\fad(180,180)`，会让短事件没有稳定可见中段，且 ASS 厘秒舍入可能得到零时长。
- `58b6602` 闭合第三轮差异：主体投影与覆盖统一使用真实半开帧区间相交；包装拒绝 ASS 舍入后零时长事件，动态标题要求至少 0.10 秒并按事件时长将每侧 fade 限制在三分之一。120ms 标题的真实 preview/export early/middle/late 像素证据验证中段能量最高；转场淡出小主体继续用未遮罩 control-subject RGB 差和实际主体/文字框不相交验证，稳定大掩码额外保留 IoU、保留率和像素数门槛。
- 第四轮双审发现并闭合三类运行时事实缺口：`3b529d8` 以服务端 ffprobe 逐帧时长覆盖 worker 的空 timing 字段，worker 不再 2Hz 漏采；预览记录受控字体 stat identity，字体替换/缺失立即使旧预览及批准 stale。真实 worker 在 1 秒 30fps 素材对 9:16 返回 ready/30 帧、对 1:10 返回 review_required/30 帧，normalize 的 `frameDurationSeconds=0.033334`；原始 JSON 在 `/tmp/aivre-ticket05-round5-real-worker.jsonl`。
- 同轮 Standards 继续指出 9000 帧上限只覆盖输出而不覆盖 `300s × speed4` 的合法源区间，并追出 O(n²) 轨迹、72k DOM、随机 seek 及状态 JSON 超过备份 16MiB。`635e692` 显式定义 CFR 源最高 60fps、完整预算 72,000 帧，>60fps 保守转人工；worker 首帧 seek 后顺序解码，服务端与包装复用预构轨迹并二分查找/按事件切片，前端只显示保留首尾的 120 个等距导航点。完整事实仅在 JSON 边界无损 zlib 压缩，精确识别 reframe schema、整份状态累计解压上限 256MiB、逐节点样本数限制，Batch/Timeline load 后再走原严格结构校验；三份 72k 事实的持久化体积回归低于 16MiB，运行时/API 仍保留完整 samples。
- 固定全分支 `6bb8bf5..7c0e2bb` 的独立终审结论为 `Standards: ✅ PASS`、`Spec: ✅ PASS`，均无开放 Critical/Important；本票十项约定范围关闭。该结论仅针对 Ticket 05，不扩展为整个产品已可商用。

## Answer

Ticket 05 已在隔离分支 `codex/montage-reframes` 完成并通过固定提交终审。交付包括逐帧主体事实与满幅裁剪、人工构图保护、普通时间线/批量变体共用的五类 ASS 包装、严格字体与许可门、主体/安全区避让、预览—审核—导出失效链，以及长源逐帧证据的有界计算和无损持久化。

- 固定实现候选：`7c0e2bb`（基线 `6bb8bf51d23e1f425db3a30764d36d9cebff1abb`）。
- 自动验证：后端 `1647 passed, 10 skipped`；前端 `49 files / 509 tests`；生产 build exit 0；基线 `git diff --check` 通过。
- 真实证据：Task23 六项媒体测试、真实 30fps worker ready/review-required、静态图 ordinary/bulk 路径、五元素 preview/export 像素与主体避让证据均保留在上述日志和证据目录。
- 来源/许可：OpenShorts MIT、MediaPipe/EfficientDet Apache-2.0、FFmpeg LGPL 调用、libass ISC、Noto CJK OFL-1.1；固定版本、hash、notice 与适配范围已记录。
- 独立验收：Standards 与 Spec 均 PASS，无开放 Critical/Important。范围仅限本票十项，不代表整个产品百分之百对齐或整体商用完成。
