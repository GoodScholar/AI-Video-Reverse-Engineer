# 多轨剪辑时间线与音频制作

用户要求实现完整剪辑时间线、音频制作。本轮提供本地剪辑闭环。用户已明确选择本地音频编辑与录音，不调用 AI 音频或视频生成。

## 产品与验收
- 工作台增加「剪辑」导航，复用前置素材库（图片/视频/音频），不替换参考素材。
- 多视频/音频轨道，可增删和调整轨道顺序；时间标尺、缩放、播放头、片段选择/拖动、精确参数；分割、复制、删除、撤销/重做。
- 片段参数：时间线起点 start、源入点 inPoint、输出时长 duration、speed(0.25..4)、volume(0..2)、fadeIn/fadeOut(秒，作用于音频)。轨道 mute/hidden；视频轨道承载图片/视频，音频轨道承载音频或视频声音。
- 音频支持素材导入、视频音轨使用、录音上传、音量/静音、淡入淡出、叠加混音、WAV 导出。不自动获取麦克风，只有用户点击录音才申请。
- 本地 FFmpeg 按同一时间线渲染预览 MP4（640宽）及成片 MP4/WAV；当前编辑与旧预览区分，播放基于已渲染结果，禁止冒充实时特效预览。
- 保存版本校验、输入快照、异步任务状态、取消、失败重试（重新提交）、重启中断恢复；结果只发布已完成文件。新编辑保留旧结果但标记版本不一致。
- 范围：最长300秒、最多8轨、80片段、输出宽高偶数64..1920、fps24/25/30；全画幅视频层，后面的轨道覆盖前面的轨道，同轨视频不重叠（音频可叠加）。不是专业NLE全部功能，不包括字幕排版/特效/调色/画中画/AI声音。

## 共享数据与 API
base `/api/projects/{pid}/timeline`
GET -> {revision, settings:{width:1280,height:720,fps:30}, tracks:Track[], assets:Asset[], runs:Run[]}
Track={id,name,kind:'video'|'audio',muted:boolean,hidden:boolean,clips:Clip[]}
Clip={id,assetId,start,inPoint,duration,speed,volume,fadeIn,fadeOut}
Asset={id,name,kind:'image'|'video'|'audio',url,duration?,width?,height?} 从 preproduction state读取。
Run={id,revision,format:'preview'|'mp4'|'wav',status:'queued'|'running'|'completed'|'failed'|'cancelled',error:string|null,url?:string}
PUT base {revision,settings,tracks} -> workspace；409保留草稿。
POST /runs {revision,format} -> workspace；新提交必须当前已保存版本。POST /runs/{id}/cancel {} -> workspace。
GET /runs/{id}/output -> FileResponse only completed safe output。
音频录音：复用POST preproduction/assets?role=audio（WebM受支持），录音结束后刷新 GET timeline。
请求headers与现有preproductionApi一致。所有写入受 origin+intent保护。

## 渲染模块接口（渲染代理拥有）
`backend/app/timeline_render.py`
`render_timeline(timeline: dict, sources: dict[str,Path], output: Path, *, format: str, ffmpeg_path='ffmpeg', ffprobe_path='ffprobe', cancelled: Callable[[],bool]=lambda:False) -> None`
时间线参数是经过API校验的settings/tracks，sources已解析/隔离安全路径。renderer仍校验媒体解码/源时长不越界。失败/取消抛TimelineRenderError，取消时API依据cancel事件标记cancelled。渲染有限时(600s)和终止子进程，限制线程数，输出完整探测后才成功。WAV PCM 48kHz stereo；MP4 H264 AAC 48kHz stereo；空隙黑画面/静音；图片循环；视频原声音按轨道muted/volume加入；hidden视频不显示但允许音频（muted独立）。超过源尾不静默补帧，拒绝。淡入淡出以输出片段时长计；音频混合后限幅。preview保持比例缩小到最大宽640。

## 分工
- renderer：timeline_render.py + test_timeline_render.py，仅这两文件。
- frontend：TimelineEditor.tsx/timelineApi.ts/timeline.css和测试，挂载由root完成；React既有依赖不新增库。组件Props `{projectId:string}`。
- root：timeline.py/timeline_api.py、API测试、main集成、PreproductionWorkspace挂载、文档和完整验收。

所有代理禁止提交、删改既有工作、安装模型或发起生成。保留当前大量未提交内容。测试使用隔离数据。
