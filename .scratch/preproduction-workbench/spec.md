# 视频创作前置工作台

Status: resolved

用户已确认：借鉴 LibTV 的前置能力，不使用无限画布，不执行最终视频生成，保留角色动画/动作迁移的素材、控制与方案准备；包含节点功能。

## 本次验收

1. 项目需求可保存，多图片/视频/音频素材可上传并标注 character/scene/motion/audio/reference；镜头能绑定多个素材、编辑提示词和排序。
2. 节点以镜头内有序步骤显示，可选择素材或前一步产物为输入。实际运行 reference/trim/first_frame/last_frame/crop/resize/prompt 节点；输入变化自动令产物过期，禁止旧结果当成当前结果导出。
3. 复用已有分镜、字幕、深度、姿态、蒙版、插帧、超分面板（工具区域），不假称缺少模型的工具已就绪。已有分镜可以导入镜头表。
4. 交付检查给出缺失素材、无提示词、未执行/过期节点；ZIP 内包含需求、镜头、提示词、所引用素材和当前节点产物、检查报告。不声称通用包可直接被任意模型消费；专用工作流仍从已有方案导出。
5. 主界面按需求、素材、镜头、工具、交付导航；不提供最终生成提交按钮，不依赖 ComfyUI。

## 共享接口（实现者遵守此契约，变更先通知主代理）

后端新 router `create_preproduction_router(data_dir,get_project,compute_queue,*,ffmpeg_path,ffprobe_path,source_lock=None)`，主代理接入 main.py。
API base `/api/projects/{pid}/preproduction`。
GET base 返回 Workspace：
```
{revision:number, brief:{theme:string,purpose:string,style:string,duration:number,aspect:string,mustPreserve:string},
 assets:Asset[], shots:Shot[], checks:Check[], nodeCatalog:{kind:string,label:string}[]}
Asset={id,name,kind:'image'|'video'|'audio',role:'character'|'scene'|'motion'|'audio'|'reference',url,duration?:number,width?:number,height?:number}
Shot={id,title,duration:number,prompt:string,negativePrompt:string,assetIds:string[],nodes:Node[]}
Node={id,kind:'reference'|'trim'|'first_frame'|'last_frame'|'crop'|'resize'|'prompt',input:string,params:Record<string,unknown>,status:'pending'|'queued'|'running'|'completed'|'failed'|'stale',error?:string,artifacts:{name,url}[]}
Check={level:'error'|'warning',shotId?:string,nodeId?:string,message:string}
```
Node input 为 `asset:<id>` 或 `node:<id>`（仅允许同镜头更早的节点，不允许环）。prompt 节点输入可为空，params={text:string}，附加镜头 prompt/brief；reference 节点将素材复用为产物。
参数：trim {start:number,end:number}；crop {x:number,y:number,width:number,height:number} 归一化0..1；resize {width:number,height:number} 像素，保留内容适配填边；first/last_frame 无参数。

PUT base 请求 `{revision,brief,shots}`，response Workspace，CAS409冲突；忽略客户端status/artifacts，服务端独立推导失效，不允许修改正在运行的节点依赖。
POST `/assets?role=character` multipart file，response Workspace。限文件大小、验证真实媒体，不改变现有referenceMedia。新素材唯一ID不可变。
POST `/import-reference` `{revision}` 当前参考复制进素材库（不重复同来源ID），response Workspace。
POST `/import-shots` `{revision}` 从现有 preparation 当前时间轴追加镜头（重复调用避免重复），response Workspace。
POST `/shots/{shotId}/nodes/{nodeId}/run` `{revision}` 单节点提交queue，response Workspace；不隐式执行未就绪上游。GET轮询。启动恢复将未完成任务标记failed，可重试。
GET `/assets/{id}/file` / `/artifacts/{shotId}/{nodeId}/{name}` 安全下载，仅允许已存储文件和当前结果。
POST `/package` `{revision}` ZIP，可带warning但error阻断409；打包当前一致性快照，禁止路径注入。

## 节点执行引擎契约

新文件 `backend/app/preproduction_nodes.py`（独立于router）：
`execute_node(kind: str, source: Optional[Path], destination: Path, params: dict, *, ffmpeg_path: str, ffprobe_path: str) -> list[str]` 返回产物文件名，抛 `NodeExecutionError`，不返回外部路径。source由router安全解析；destination独立运行目录。FFmpeg有限超时且出错清理，真实解码验证、禁止无穷/非法边界参数，限制输出尺寸/时长，不运行shell。prompt写prompt.txt；reference复制source为reference.<ext>；其他输出约定output.mp4/output.png。短视频帧提取可靠。不运行视频生成模型。

## 任务与所有权

- 01 后端：preproduction.py/preproduction_api.py + 后端测试；使用上述execute_node（另一个代理实现），不得改main.py。
- 02 节点引擎：preproduction_nodes.py + 专属测试，不改其他模块。
- 03 界面：PreproductionWorkspace.tsx/preproductionApi.ts/preproduction.css + 专属测试，不改App.tsx；Props `{project:Project, tools:ReactNode}`；工具slot由主代理提供。所有导航与表单在此组件。使用已有styles变量，无新UI依赖。主代理集成。
- 主代理：main.py/App.tsx、旧生成面板 preparationOnly 模式、项目文档/集成/端到端验证。

## 验证

后端测试输入类型、CAS、依赖过期、路径/文件校验、任务恢复、ZIP一致性；真实FFmpeg截取/首尾帧/裁切/缩放冒烟。界面测试保存、绑定、节点配置运行与错误、导出、工具导航；TypeScript build；受影响旧面板回归。独立审查只覆盖本次改动，保留之前未提交工作。

## 实施结论

已实现上述工作台和七类本地节点。工具视频结果可导入素材库，真实分镜导入会绑定独立参考快照和对应截取节点。最终生成由主应用接口与前置模式共同阻止；历史代码/数据保留。

验证：后端98项、前端63项定向回归通过，生产构建通过。真实HTTP/FFmpeg验证3份素材、2个镜头、7类节点、依赖过期阻断、重新运行和ZIP相对路径；真实本地预处理分镜导入、编辑、重复导入去重通过。验证文件保存在 `/private/tmp/aivre-preproduction-validation/`，使用独立测试数据，没有改用户参考媒体。
