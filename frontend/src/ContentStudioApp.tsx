import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { ArrowRight, Clapperboard, FolderOpen, Image, Layers3, Menu, Plus, Settings2, SlidersHorizontal, WandSparkles, X } from "lucide-react";
import type { AnalysisProviderConfiguration, Project } from "./models";
import type { BatchAsset } from "./batchEditingApi";
import { getBatchWorkspace } from "./batchEditingApi";
import { listAnalysisProviders } from "./analysisProviderApi";
import { AnalysisProviderSettings } from "./AnalysisProviderSettings";
import { ContentProductionWorkspace, type StudioProjectPage } from "./ContentProductionWorkspace";
import { ProjectBackup } from "./ProjectBackup";
import { ReferenceMediaPanel } from "./ReferenceMediaPanel";
import { uploadReferenceMedia } from "./referenceMediaApi";
import { LocalPreprocessingPanel } from "./LocalPreprocessingPanel";
import { SemanticAnalysisPanel } from "./SemanticAnalysisPanel";
import { DepthCapturePanel } from "./DepthCapturePanel";
import { ShotPreparationPanel } from "./ShotPreparationPanel";
import { ReproductionPanel } from "./ReproductionPanel";
import { CharacterMotionPanel } from "./CharacterMotionPanel";
import { VideoUpscalePanel } from "./VideoUpscalePanel";
import { VideoToolkitPanel } from "./VideoToolkitPanel";
import "./contentStudio.css";

type Page = StudioProjectPage | "projects" | "settings";
const projectPages: Array<{id:StudioProjectPage;label:string;icon:typeof Image}> = [
  {id:"create",label:"商品视频制作",icon:WandSparkles},{id:"assets",label:"项目素材",icon:Image},
  {id:"review",label:"成片与审片",icon:Clapperboard},{id:"edit",label:"单条精修",icon:SlidersHorizontal},
  {id:"reference",label:"参考视频复刻",icon:Layers3},
];
const labels:Record<Page,string>={projects:"项目中心",create:"商品视频制作",assets:"项目素材",review:"成片与审片",edit:"单条精修",reference:"参考视频复刻",tools:"本地工具",settings:"应用设置"};
async function projectsRequest<T>(init?:RequestInit):Promise<T>{
  const response=await fetch("/api/projects",init);
  if(!response.ok){const body=await response.json().catch(()=>null) as {detail?:string}|null;throw new Error(body?.detail??"无法读取本地项目。");}
  return response.json() as Promise<T>;
}
export function ContentStudioApp(){
  const [projects,setProjects]=useState<Project[]>([]);
  const [project,setProject]=useState<Project|null>(null);
  const [page,setPage]=useState<Page>("projects");
  const [menuOpen,setMenuOpen]=useState(false);
  const [loading,setLoading]=useState(true);
  const [error,setError]=useState("");
  const [providers,setProviders]=useState<AnalysisProviderConfiguration[]>([]);
  const [providerError,setProviderError]=useState("");
  const [heroAssets,setHeroAssets]=useState<BatchAsset[]>([]);
  const [creating,setCreating]=useState(false);
  const [name,setName]=useState("");
  const [saving,setSaving]=useState(false);
  const [saveError,setSaveError]=useState("");
  const [dirty,setDirty]=useState(false);
  const [settingsTab,setSettingsTab]=useState("services");
  const [depthPending,setDepthPending]=useState(false);
  const [personPending,setPersonPending]=useState(false);
  const heading=useRef<HTMLHeadingElement>(null);
  const navButton=useRef<HTMLButtonElement>(null);
  const newButton=useRef<HTMLButtonElement>(null);
  const navigate=useCallback((next:Page)=>{setPage(next);setMenuOpen(false);},[]);
  const onDirty=useCallback((value:boolean)=>setDirty(value),[]);
  async function loadProjects(){setLoading(true);setError("");try{setProjects(await projectsRequest<Project[]>());}catch(reason){setError(reason instanceof Error?reason.message:"无法读取项目。");}finally{setLoading(false);}}
  async function loadProviders(){setProviderError("");try{setProviders(await listAnalysisProviders());}catch{setProviderError("无法读取 AI 服务设置，请重新读取。");}}
  useEffect(()=>{void loadProjects();void loadProviders();},[]);
  useEffect(()=>{
    let active=true;setHeroAssets([]);
    if(projects[0])void getBatchWorkspace(projects[0].id).then(result=>{if(active)setHeroAssets(result.assets.filter(asset=>asset.kind==="image"||asset.kind==="video").slice(0,3));}).catch(()=>{});
    return()=>{active=false;};
  },[projects[0]?.id]);
  useEffect(()=>{heading.current?.focus();},[page]);
  useEffect(()=>{if(!dirty)return;const warn=(event:BeforeUnloadEvent)=>{event.preventDefault();event.returnValue="";};window.addEventListener("beforeunload",warn);return()=>window.removeEventListener("beforeunload",warn);},[dirty]);
  const openProject=(next:Project)=>{if(dirty&&project?.id!==next.id)return;setProject(next);setPage("create");setMenuOpen(false);};
  const updateProject=useCallback((updated:Project)=>{setProject(current=>current?.id===updated.id?updated:current);setProjects(current=>current.map(item=>item.id===updated.id?updated:item).sort((a,b)=>b.updatedAt.localeCompare(a.updatedAt)));},[]);
  async function createProject(event:FormEvent){
    event.preventDefault();if(!name.trim()||saving||dirty)return;setSaving(true);setSaveError("");
    try{const created=await projectsRequest<Project>({method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name:name.trim()})});setProjects(current=>[created,...current]);setProject(created);setCreating(false);setName("");setPage("create");}
    catch(reason){setSaveError(reason instanceof Error?reason.message:"创建失败，请重试。");}finally{setSaving(false);}
  }
  const provider=providers.find(item=>item.selectedProvider===item.provider)??null;
  const locked=project?.localPreprocessing?.status==="queued"||project?.localPreprocessing?.status==="running"||(project?.depthCaptures??[]).some(item=>item.status==="queued"||item.status==="running")||depthPending||personPending;
  const tools=project&&<div className="studio-tools-stack">
    <ReferenceMediaPanel project={project} onProjectUpdated={updateProject} upload={async(id,file)=>{const updated=await uploadReferenceMedia(id,file);updateProject(updated);return updated;}} preprocessingLocked={locked} hasPreprocessingResult={project.localPreprocessing!==null}/>
    <LocalPreprocessingPanel project={project} onProjectUpdated={updateProject}/>
    <SemanticAnalysisPanel project={project} provider={provider} onProjectUpdated={updateProject}/>
    <DepthCapturePanel project={project} onProjectUpdated={updateProject} onMutationPendingChange={setDepthPending}/>
    <ShotPreparationPanel project={project} analysisProviders={providers} onMutationPendingChange={setPersonPending}/>
    <ReproductionPanel project={project} analysisProviders={providers} preparationOnly/>
    <CharacterMotionPanel key={`${project.id}:${project.referenceMedia?.id??""}`} project={project} preparationOnly/>
    <VideoUpscalePanel project={project}/><VideoToolkitPanel project={project}/>
  </div>;
  const media=(asset:BatchAsset)=>asset.kind==="image"?<img src={asset.url} alt={asset.name}/>:<video src={asset.url} preload="metadata" muted playsInline aria-label={`${asset.name} 素材缩略图`}/>;
  return <div className="cs-root"><aside className={`sp-sidebar ${menuOpen?"is-open":""}`}>
    <button type="button" className="sp-brand" onClick={()=>navigate("projects")}><span><Clapperboard size={21}/></span><div><strong>内容工作室</strong><small>AI Video Studio</small></div></button>
    <button type="button" className="sp-mobile-close sp-icon-button" aria-label="关闭导航" onClick={()=>{setMenuOpen(false);navButton.current?.focus();}}><X size={20}/></button>
    <nav className="sp-global-nav" aria-label="应用导航">{([["projects",FolderOpen],["tools",Clapperboard],["settings",Settings2]] as const).map(([id,Icon])=><button key={id} type="button" aria-current={page===id?"page":undefined} onClick={()=>navigate(id)}><Icon size={18}/>{labels[id]}</button>)}</nav>
    <div className="sp-sidebar-project"><span>当前项目</span><strong><span className="sp-project-dot"/>{project?.name??"请先打开一个项目"}</strong></div>
    <nav className="sp-project-nav" aria-label="项目导航">{projectPages.map(({id,label,icon:Icon})=><button key={id} type="button" disabled={!project} aria-current={page===id?"page":undefined} onClick={()=>navigate(id)}><Icon size={18}/>{label}</button>)}</nav>
    <div className="sp-sidebar-bottom"><span className="sp-local-dot"/>项目与素材保存在本地<a href="?workspace=legacy">兼容工作台<ArrowRight size={15}/></a></div>
  </aside>{menuOpen&&<button type="button" className="sp-menu-backdrop" aria-label="关闭导航遮罩" onClick={()=>{setMenuOpen(false);navButton.current?.focus();}}/>}
    <div className="sp-shell"><header className="sp-topbar"><button ref={navButton} type="button" className="sp-menu-button sp-icon-button" aria-label="打开导航" onClick={()=>setMenuOpen(true)}><Menu size={20}/></button><div className="sp-breadcrumb"><span>内容工作室</span><ArrowRight size={13}/><h1 ref={heading} tabIndex={-1}>{labels[page]}</h1></div><span className="studio-service-badge">{dirty?"有未保存草稿":provider?`${provider.label??provider.provider} · ${provider.model??"未配置模型"}`:"本地内容工作室"}</span></header>
      <main className={`sp-main studio-page-${page}`}>
        {page==="projects"&&<>
          <div className="sp-home-intro"><div><h2>你的内容工作室</h2><p>继续上次制作，或开始一批新的商品短视频。</p></div><button ref={newButton} type="button" className="sp-button sp-primary" disabled={dirty} onClick={()=>{setCreating(true);setSaveError("");}}><Plus size={16}/>新建视频</button></div>
          <section className="sp-featured"><div className="sp-featured-copy"><span className="sp-tag">商品内容制作</span><h3>让你的素材，<br/>成为下一条作品。</h3><p>准备资料，核对脚本，选好声音。<br/>从同一个项目完成制作与交付。</p><button type="button" className="sp-button sp-primary" disabled={loading||!!error||(dirty&&project?.id!==projects[0]?.id)} onClick={()=>projects[0]?openProject(projects[0]):setCreating(true)}>{projects[0]?"继续最近项目":"开始第一个项目"}<ArrowRight size={16}/></button><span className="sp-featured-note">素材与成片使用项目真实文件</span></div>
            <div className="sp-contact-sheet">{heroAssets.length?heroAssets.map(asset=><div key={asset.id}>{media(asset)}</div>):<div className="studio-hero-empty"><Clapperboard size={42}/><strong>从一份商品资料开始</strong><span>素材 → 脚本 → 声音 → 成片</span></div>}</div></section>
          {creating&&<section className="studio-create-project"><h3>为这次制作起一个名称</h3><form onSubmit={event=>void createProject(event)}><label>项目名称<input autoFocus maxLength={100} value={name} onChange={event=>setName(event.target.value)} placeholder="例如：咖啡新品介绍"/></label>{saveError&&<p role="alert">{saveError}</p>}<div><button type="button" className="sp-button" disabled={saving} onClick={()=>{setCreating(false);newButton.current?.focus();}}>取消</button><button type="submit" className="sp-button sp-primary" disabled={saving||dirty||!name.trim()}>{saving?"创建中…":"创建并开始制作"}</button></div></form></section>}
          <div className="sp-section-heading"><h2>最近项目</h2><span>{projects.length} 个本地项目</span></div>
          {dirty&&<p className="studio-inline-status" role="status">当前项目仍有草稿。可以切换页面，切换其他项目或新建前请先保存。</p>}
          {loading&&<p role="status">正在读取本地项目…</p>}
          {error&&<div className="studio-error" role="alert"><p>{error}</p><button type="button" className="sp-button" onClick={()=>void loadProjects()}>重新读取项目</button></div>}
          {!loading&&!error&&<div className="sp-project-grid">{projects.map(item=><button type="button" className="sp-project studio-project-card" key={item.id} aria-label={`打开项目 ${item.name}`} disabled={dirty&&project?.id!==item.id} onClick={()=>openProject(item)}><div className="studio-project-visual"><Clapperboard size={32}/><span>本地项目</span></div><div><span className="sp-tag">内容项目</span><h3>{item.name}</h3><p>最近更新 {new Date(item.updatedAt).toLocaleDateString("zh-CN")}</p><span>继续制作<ArrowRight size={15}/></span></div></button>)}{!projects.length&&<div className="sp-empty"><FolderOpen size={28}/><h3>还没有项目</h3><p>填写项目名称，再添加获准使用的商品素材。</p></div>}</div>}
        </>}
        {providerError&&<div className="studio-error" role="alert"><p>{providerError}</p><button type="button" className="sp-button" onClick={()=>void loadProviders()}>重新读取 AI 服务设置</button></div>}
        {project&&<div hidden={page==="projects"||page==="settings"}><ContentProductionWorkspace key={project.id} project={project} tools={tools} active={page!=="projects"&&page!=="settings"} page={page==="projects"||page==="settings"?"create":page} onNavigate={navigate} onDirtyChange={onDirty}/></div>}
        {page==="tools"&&!project&&<div className="sp-empty"><Clapperboard size={32}/><h2>先选择需要处理的项目</h2><p>工具使用项目里的素材，并把结果保存回该项目。</p><button type="button" className="sp-button sp-primary" onClick={()=>navigate("projects")}>选择项目<ArrowRight size={16}/></button></div>}
        {page==="settings"&&<><div className="sp-work-heading"><div><h2>为制作准备好环境</h2><p>服务连接、声音与备份集中在这里。</p></div></div><div className="sp-settings-layout"><nav aria-label="设置分类">{[["services","AI 服务连接"],["local","声音与渲染说明"],["files","文件与备份"]].map(([id,label])=><button type="button" key={id} aria-pressed={settingsTab===id} onClick={()=>setSettingsTab(id)}>{label}<ArrowRight size={15}/></button>)}</nav><section className="sp-settings-panel">
          {settingsTab==="services"&&<AnalysisProviderSettings providers={providers} onProvidersChanged={setProviders}/>}
          {settingsTab==="local"&&<><h3>本地声音与视频制作</h3><p>选声制作步骤会读取真实音色目录与运行环境，支持描述、适用场景及试听。</p><p>配音使用已准备的本机 Qwen3-TTS；视频使用现有 FFmpeg。缺失环境会显示原因，不自动下载。</p><button type="button" className="sp-button sp-primary" onClick={()=>navigate(project?"create":"projects")}>前往制作<ArrowRight size={16}/></button></>}
          {settingsTab==="files"&&<ProjectBackup projectId={project?.id} hasUnsavedDraft={dirty} onRestored={restored=>{setProjects(current=>[restored,...current]);setProject(restored);setPage("create");}}/>}
        </section></div></>}
      </main><footer className="sp-footer"><span>本地内容工作室 · 发送脚本前展示内容并确认</span><span>数字人服务暂未启动</span></footer>
    </div>
  </div>;
}
