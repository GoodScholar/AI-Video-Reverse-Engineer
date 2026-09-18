import { useEffect, useRef, useState } from 'react';
import type { Project } from './models';
import { artifactUrl, controlPipeline, controlTool, getCuts, getToolkit, saveCuts, startPipeline, startTools, toolBase, type Cuts, type ToolKind, type ToolkitState } from './videoToolkitApi';
import './videoToolkit.css';

const names:Record<ToolKind,string>={scenes:'镜头检测',subtitles:'字幕提取',mask:'主体跟踪',interpolate:'RIFE 补帧',upscale:'视频超分'};
const statuses={queued:'排队中',running:'处理中',completed:'已完成',failed:'失败',cancelled:'已取消',blocked:'未执行'};

export function SynchronizedComparison({source,result}:{source:string;result:string}){
 const original=useRef<HTMLVideoElement>(null);const processed=useRef<HTMLVideoElement>(null);
 const [zoom,setZoom]=useState('1');const [error,setError]=useState('');
 const sync=()=>{const a=original.current,b=processed.current;if(a&&b&&Math.abs(a.currentTime-b.currentTime)>.12)b.currentTime=a.currentTime;};
 return <div className="tool-comparison">
  <p>使用左侧播放器同步播放、暂停和定位；仅播放左侧音频。</p>
  <label>放大检查<select value={zoom} onChange={e=>setZoom(e.target.value)}><option value="1">100%</option><option value="2">200%</option></select></label>
  <div className="tool-comparison-grid">
   <div><strong>输入快照</strong><div className="tool-video-viewport"><video ref={original} src={source} controls preload="metadata" style={{width:`${Number(zoom)*100}%`,maxWidth:'none'}} onPlay={()=>{sync();const play=processed.current?.play();play?.catch(()=>{original.current?.pause();setError('结果视频尚未准备好，请稍后播放。');});}} onPause={()=>processed.current?.pause()} onSeeking={sync} onTimeUpdate={sync} onRateChange={()=>{if(original.current&&processed.current)processed.current.playbackRate=original.current.playbackRate;}} onEnded={()=>processed.current?.pause()}/></div></div>
   <div><strong>处理结果</strong><div className="tool-video-viewport"><video ref={processed} src={result} muted playsInline preload="metadata" style={{width:`${Number(zoom)*100}%`,maxWidth:'none'}} onError={()=>setError('无法加载结果视频。')}/></div></div>
  </div>{error&&<p role="alert">{error}</p>}
 </div>;
}

function CutEditor({pid,rid}:{pid:string;rid:string}){
 const [data,setData]=useState<Cuts|null>(null);const [text,setText]=useState('');const [error,setError]=useState('');const [busy,setBusy]=useState(false);
 const generation=useRef(0);
 useEffect(()=>{const token=++generation.current;getCuts(pid,rid).then(v=>{if(token===generation.current){setData(v);setText(v.cuts.join(', '));}},e=>{if(token===generation.current)setError(String(e.message));});return()=>{generation.current++;};},[pid,rid]);
 async function save(){const token=generation.current;setBusy(true);setError('');try{
  const values=text.trim()?text.split(/[,，\s]+/).filter(Boolean).map(Number):[];
  if(!data||values.some((v,i)=>!Number.isFinite(v)||v<=0||v>=data.duration||(i>0&&v<=values[i-1])))throw new Error('切点须严格递增，且在视频起止时间之间。');
  const next=await saveCuts(pid,rid,{revision:data.revision??0,cuts:values});
  if(token===generation.current){setData(next);setText(next.cuts.join(', '));}
 }catch(e){if(token===generation.current)setError(e instanceof Error?e.message:'保存失败');}finally{if(token===generation.current)setBusy(false);}}
 return <div className="tool-cut-editor"><label>切点（秒，逗号分隔；清空可合并为一个镜头）<input value={text} onChange={e=>setText(e.target.value)} disabled={!data||busy}/></label>
  {data&&<p>全长 {data.duration.toFixed(2)} 秒 · {data.cuts.length+1} 个镜头。此处修正保存在本次检测结果中。</p>}
  <button type="button" className="secondary-action" disabled={!data||busy} onClick={()=>void save()}>保存切点</button>{error&&<p role="alert">{error}</p>}
 </div>;
}

export function VideoToolkitPanel({project}:{project:Project}){
 const [state,setState]=useState<ToolkitState|null>(null);const [error,setError]=useState('');const [busy,setBusy]=useState(false);
 const [kind,setKind]=useState<ToolKind>('scenes');const [selected,setSelected]=useState<string[]>([]);
 const [points,setPoints]=useState<number[][]>([]);const [pointLabel,setPointLabel]=useState(1);const [x,setX]=useState(50);const [y,setY]=useState(50);
 const [language,setLanguage]=useState('auto');const [fps,setFps]=useState(60);const [resolution,setResolution]=useState('1080p');const [pipelineKind,setPipelineKind]=useState<ToolKind>('scenes');const [pipelineSteps,setPipelineSteps]=useState<ToolKind[]>([]);
 const [expanded,setExpanded]=useState<string|null>(null);const [refresh,setRefresh]=useState(0);const generation=useRef(0);
 const sourceId=project.referenceMedia?.id;
 useEffect(()=>{
  const token=++generation.current;let timer:ReturnType<typeof setTimeout>|undefined;let disposed=false;
  setState(null);setSelected([]);setPoints([]);setPipelineSteps([]);setExpanded(null);setError('');setBusy(false);
  async function poll(){try{const result=await getToolkit(project.id);if(disposed||token!==generation.current)return;setState(result);setError('');if(result.runs.some(r=>r.status==='queued'||r.status==='running')||(result.pipelines??[]).some(p=>p.status==='queued'||p.status==='running'))timer=setTimeout(()=>void poll(),2000);}catch(e){if(!disposed&&token===generation.current)setError(e instanceof Error?e.message:'无法连接本地服务。');}}
  void poll();return()=>{disposed=true;generation.current++;if(timer)clearTimeout(timer);};
 },[project.id,sourceId,refresh]);
 async function act(fn:()=>Promise<unknown>){const token=generation.current;setBusy(true);setError('');try{await fn();if(token===generation.current)setRefresh(v=>v+1);}catch(e){if(token===generation.current)setError(e instanceof Error?e.message:'处理请求失败。');}finally{if(token===generation.current)setBusy(false);}}
 const ready=state?.environment[kind]?.available===true;const pipelineReady=state?.environment[pipelineKind]?.available===true;
 const activeAsset=state?.assets.find(a=>a.id===selected[0]);
 function toggle(id:string){setPoints([]);setSelected(old=>old.includes(id)?old.filter(k=>k!==id):kind==='mask'?[id]:[...old,id]);}
 function addPoint(a:number,b:number){if(points.length<32&&a>=0&&a<=1&&b>=0&&b<=1)setPoints(old=>[...old,[a,b,pointLabel]]);}
 return <section className="video-toolkit" aria-labelledby="video-toolkit-title">
  <div className="tool-heading"><div><span className="eyebrow">视频处理</span><h2 id="video-toolkit-title">视频工具</h2><p>分析镜头与主体，或把已有视频处理为更清晰、流畅的成片。</p></div><button type="button" className="secondary-action" disabled={busy} onClick={()=>setRefresh(v=>v+1)}>刷新素材与环境</button></div>
  {error&&<p role="alert" className="tool-error">{error}</p>}
  {!state?<p role="status">{error?'连接失败，可刷新重试。':'正在读取素材与工具状态…'}</p>:<>
   <div className="tool-settings">
    <fieldset><legend>选择素材（最多 12 份）</legend>{state.assets.length===0?<p>请先上传参考视频，或完成一次视频生成。</p>:state.assets.map(asset=><label className="tool-asset" key={asset.id}><input type="checkbox" checked={selected.includes(asset.id)} onChange={()=>toggle(asset.id)} disabled={busy||(!selected.includes(asset.id)&&selected.length>=12)}/><span>{asset.label}</span></label>)}</fieldset>
    <div className="tool-options"><label>处理工具<select value={kind} disabled={busy} onChange={e=>{const next=e.target.value as ToolKind;setKind(next);setPoints([]);if(next==='mask')setSelected(old=>old.slice(0,1));}}>{Object.entries(names).map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>
     <p className={ready?'':'tool-dependency'}>{state.environment[kind]?.message??'工具环境未配置。'}</p>
     {kind==='scenes'&&<p>使用自适应镜头检测。完成后可添加、删除切点并导出 JSON。</p>}
     {kind==='subtitles'&&<label>语音语言<select value={language} onChange={e=>setLanguage(e.target.value)}><option value="auto">自动识别</option><option value="zh">中文</option><option value="en">英文</option><option value="ja">日文</option><option value="ko">韩文</option></select></label>}
     {kind==='interpolate'&&<><label>输出帧率<select value={fps} onChange={e=>setFps(Number(e.target.value))}><option value="30">30 FPS</option><option value="60">60 FPS</option></select></label><p>最长 60 秒，保持时长与音轨。明显切镜处保留原帧，快速运动仍可能出现伪影。</p></>}
     {kind==='upscale'&&<label>输出清晰度<select value={resolution} onChange={e=>setResolution(e.target.value)}><option value="1080p">1080P（短边 1080）</option><option value="2k">2K（短边 1440）</option></select></label>}
     <button type="button" className="primary-action" disabled={busy||!ready||!selected.length||(kind==='mask'&&!points.some(p=>p[2]===1))} onClick={()=>void act(()=>startTools(project.id,{assetIds:selected,kind,params:{language,fps,resolution,points}}))}>{busy?'正在提交…':'开始处理'}</button>
    </div>
   </div>
   {(kind==='mask'||pipelineSteps.includes('mask'))&&<div className="tool-mask"><p>选择一份视频，在首帧标记主体。绿色包含，红色排除；输出 8 FPS、最长边 640 的遮罩与跟踪数据，最长 30 秒。</p>
    <label>选点类型<select value={pointLabel} onChange={e=>setPointLabel(Number(e.target.value))}><option value="1">包含主体（绿色）</option><option value="0">排除区域（红色）</option></select></label>
    {activeAsset&&<div className="tool-point-image" onClick={e=>{const rect=e.currentTarget.getBoundingClientRect();addPoint((e.clientX-rect.left)/rect.width,(e.clientY-rect.top)/rect.height);}}><img src={`${toolBase(project.id)}/assets/${encodeURIComponent(activeAsset.id)}/frame`} alt="主体跟踪首帧，可点击标记主体" draggable={false}/>{points.map((p,i)=><span key={i} style={{left:`${p[0]*100}%`,top:`${p[1]*100}%`,background:p[2]?'#198354':'#b93829'}}>{i+1}</span>)}</div>}
   <div className="tool-point-controls"><label>横向位置 %<input type="number" min="0" max="100" value={x} onChange={e=>setX(Number(e.target.value))}/></label><label>纵向位置 %<input type="number" min="0" max="100" value={y} onChange={e=>setY(Number(e.target.value))}/></label><button type="button" className="secondary-action" disabled={!activeAsset||points.length>=32} onClick={()=>addPoint(x/100,y/100)}>添加选点</button><button type="button" className="secondary-action" onClick={()=>setPoints([])}>清空选点（{points.length}）</button></div>
   </div>}
   <div className="tool-pipeline" role="group" aria-label="自动处理流程"><h3>自动处理流程</h3><p>每份素材一次只能有一条进行中的自动处理流程。分析步骤始终使用输入快照；补帧和超分依次使用前一步的视频结果。至少选择两步，失败后可从失败步骤继续。</p>
    <label>流程步骤<select value={pipelineKind} disabled={busy} onChange={e=>setPipelineKind(e.target.value as ToolKind)}>{Object.entries(names).map(([id,name])=><option key={id} value={id}>{name}</option>)}</select></label>
    <p className={pipelineReady?'':'tool-dependency'}>{state.environment[pipelineKind]?.message??'工具环境未配置。'}</p>
    <button type="button" className="secondary-action" disabled={busy||!pipelineReady||pipelineSteps.includes(pipelineKind)||pipelineSteps.length>=5} onClick={()=>setPipelineSteps(old=>[...old,pipelineKind].sort((a,b)=>['scenes','subtitles','mask','interpolate','upscale'].indexOf(a)-['scenes','subtitles','mask','interpolate','upscale'].indexOf(b)))}>添加到流程</button>
    {pipelineSteps.length>0&&<ol className="tool-pipeline-steps">{pipelineSteps.map((step,index)=><li key={step}>{names[step]} <button type="button" className="secondary-action" disabled={busy} onClick={()=>setPipelineSteps(old=>old.filter((_,position)=>position!==index))}>移除</button></li>)}</ol>}
    {pipelineSteps.includes('interpolate')&&<label>流程输出帧率<select value={fps} onChange={e=>setFps(Number(e.target.value))}><option value="30">30 FPS</option><option value="60">60 FPS</option></select></label>}{pipelineSteps.includes('upscale')&&<label>流程输出清晰度<select value={resolution} onChange={e=>setResolution(e.target.value)}><option value="1080p">1080P（短边 1080）</option><option value="2k">2K（短边 1440）</option></select></label>}{pipelineSteps.includes('mask')&&<p>请在上方主体跟踪区域标记至少一个前景点。</p>}
    <button type="button" className="primary-action" disabled={busy||selected.length!==1||pipelineSteps.length<2||(pipelineSteps.includes('mask')&&!points.some(point=>point[2]===1))} onClick={()=>void act(()=>startPipeline(project.id,{assetId:selected[0],steps:pipelineSteps.map(step=>({kind:step,params:{language,fps,resolution,points:step==='mask'?points:[]} }))}))}>开始自动处理</button>{selected.length!==1&&<p>请选择一份素材后再创建自动处理流程。</p>}
   </div>
   <div className="tool-runs"><h3>处理记录</h3>{!state.runs.length&&<p>尚无处理记录。可选择多份素材，一次加入本地队列。</p>}{state.runs.map(run=><article key={run.id} className="tool-run">
    <div className="tool-run-heading"><div><strong>{names[run.kind]} · {run.label}</strong><p>{statuses[run.status]}{run.error?` · ${run.error}`:''}</p></div><div className="tool-run-actions">{(run.status==='queued'||run.status==='running')&&<button type="button" className="secondary-action" disabled={busy} onClick={()=>void act(()=>controlTool(project.id,run.id,'cancel'))}>取消</button>}{(run.status==='failed'||run.status==='cancelled')&&<button type="button" className="secondary-action" disabled={busy} onClick={()=>void act(()=>controlTool(project.id,run.id,'retry'))}>重试</button>}{run.status==='completed'&&<button type="button" className="secondary-action" onClick={()=>setExpanded(old=>old===run.id?null:run.id)}>{expanded===run.id?'收起':'查看结果'}</button>}</div></div>
    {run.status==='completed'&&<div className="tool-downloads">{run.artifacts.map(name=><a key={name} href={`${artifactUrl(project.id,run.id,name)}?download=true`}>下载 {name}</a>)}</div>}
   {expanded===run.id&&run.status==='completed'&&<>{run.kind==='scenes'&&<CutEditor pid={project.id} rid={run.id}/>} {(run.artifacts.includes('output.mp4')||run.artifacts.includes('overlay.mp4'))&&<SynchronizedComparison source={`${toolBase(project.id)}/runs/${run.id}/input`} result={artifactUrl(project.id,run.id,run.artifacts.includes('output.mp4')?'output.mp4':'overlay.mp4')}/>} {run.kind==='subtitles'&&<p>字幕文件已生成。下载 SRT 用于剪辑软件，JSON 保留识别时间信息。</p>}</>}
   </article>)}</div>
   <div className="tool-runs"><h3>自动处理记录</h3>{!(state.pipelines??[]).length&&<p>尚无自动处理流程。</p>}{(state.pipelines??[]).map(pipeline=><article key={pipeline.id} className="tool-run"><div className="tool-run-heading"><div><strong>{pipeline.label}</strong><p>{statuses[pipeline.status]}{pipeline.error?` · ${pipeline.error}`:''}</p></div><div className="tool-run-actions">{(pipeline.status==='queued'||pipeline.status==='running')&&<button type="button" className="secondary-action" disabled={busy} onClick={()=>void act(()=>controlPipeline(project.id,pipeline.id,'cancel'))}>取消自动处理</button>}{(pipeline.status==='failed'||pipeline.status==='cancelled')&&<button type="button" className="secondary-action" disabled={busy} onClick={()=>void act(()=>controlPipeline(project.id,pipeline.id,'retry'))}>从失败步骤继续</button>}</div></div><ol className="tool-pipeline-steps">{pipeline.steps.map(step=><li key={step.id}>{names[step.kind]} · {statuses[step.status]}{step.error?` · ${step.error}`:''}{step.artifacts.map(name=><a key={name} href={`${toolBase(project.id)}/pipelines/${pipeline.id}/steps/${step.id}/artifacts/${name}?download=true`}>下载 {name}</a>)}</li>)}</ol></article>)}</div>
  </>}
 </section>;
}
