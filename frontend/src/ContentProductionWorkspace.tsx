import { useCallback, useEffect, useState, type ReactNode } from "react";
import type { Project } from "./models";
import type { PreproductionWorkspace as Workspace } from "./preproductionApi";
import { AigcCreator } from "./AigcCreator";
import { BatchEditor } from "./BatchEditor";
import { PreproductionWorkspace } from "./PreproductionWorkspace";
import { StudioSteps } from "./StudioPrototypePrimitives";
import { getContentWorkflow, type ContentWorkflowProjection } from "./contentWorkflowApi";
import type { AspectResolution } from "./videoAspect";

export type StudioProjectPage = "create" | "assets" | "review" | "edit" | "reference" | "tools";
export function ContentProductionWorkspace({ project, page, tools, onNavigate, onDirtyChange, active = true }: {
  project: Project; page: StudioProjectPage; tools: ReactNode;
  onNavigate: (page: StudioProjectPage) => void; onDirtyChange: (dirty: boolean) => void; active?:boolean;
}) {
  const [projection, setProjection] = useState<ContentWorkflowProjection>();
  const [projectionError, setProjectionError] = useState("");
  const [viewStep, setViewStep] = useState(0);
  const [assets, setAssets] = useState<Workspace["assets"]>();
  const [importedWorkspace, setImportedWorkspace] = useState<{projectId:string;workspace:Workspace}>();
  const onWorkspaceImported = useCallback((workspace:Workspace)=>{setAssets(workspace.assets);setImportedWorkspace({projectId:project.id,workspace});},[project.id]);
  const [taskId, setTaskId] = useState<string>();
  const [aigcDirty, setAigcDirty] = useState(false);
  const [batchDirty, setBatchDirty] = useState(false);
  const [preproductionDirty, setPreproductionDirty] = useState(false);
  const [previewAssetId, setPreviewAssetId] = useState<string | null>(null);
  const [previewAspect, setPreviewAspect] = useState<AspectResolution>({ resolvedAspect: "9:16", width: 720, height: 1280, reason: "商品制作默认比例。" });
  const onPreproductionDirty = useCallback((_id:string,dirty:boolean)=>setPreproductionDirty(dirty),[]);
  useEffect(()=>onDirtyChange(aigcDirty||batchDirty||preproductionDirty),[aigcDirty,batchDirty,preproductionDirty,onDirtyChange]);
  useEffect(() => {
    let current = true;
    setProjection(undefined); setProjectionError(""); setViewStep(0); setTaskId(undefined);
    void getContentWorkflow(project.id).then((result) => {
      if (!current) return;
      setProjection(result); setViewStep(result.currentStep);
    }).catch((reason) => { if (current) setProjectionError(reason instanceof Error ? reason.message : "无法读取内容制作进度。"); });
    return () => { current = false; };
  }, [project.id]);
  const selectedAsset = assets?.find(asset => asset.id === previewAssetId) ?? assets?.find(asset => asset.kind === "image" || asset.kind === "video");
  const batchVisible = page === "review" || page === "edit" || (page === "create" && viewStep >= 2);
  const oldVisible = page === "assets" || page === "reference" || page === "tools";
  const mode = page === "edit" ? "edit" : page === "review" || viewStep === 3 ? "review" : "voice";
  const reviewReady = projection ? ["preview_ready","review_pending","approved","rejected","delivered"].includes(projection.stage) : false;
  return <>
    <div className="sp-work-heading"><div><h2>{project.name}</h2><p>资料、脚本、声音与成片，都留在同一个项目。</p></div><span className="sp-tag">本地项目</span></div>
    {page === "create" && projection && <StudioSteps steps={["准备资料","确认脚本","选声制作","审核导出"]} current={viewStep}
      completed={projection.completedSteps} onStepClick={setViewStep} />}
    {page === "create" && !projection && !projectionError && <p role="status">正在读取制作进度…</p>}
    {page === "create" && projectionError && <p role="alert" className="preproduction-error">{projectionError}</p>}
    <div className={page === "create" && viewStep < 2 ? "sp-workspace" : ""}>
      <div hidden={page !== "create" || viewStep > 1} className="sp-work-panel">
        <AigcCreator projectId={project.id} guidedStep={viewStep === 1 ? 1 : 0} mediaAssets={assets}
          onWorkspaceImported={onWorkspaceImported} onDraftChange={setAigcDirty} onStepChange={setViewStep} onPreviewAsset={setPreviewAssetId} onAspectChange={setPreviewAspect}
          onOpenAssets={()=>onNavigate("assets")} onBatchCreated={id=>{setTaskId(id);setViewStep(2);}} />
      </div>
      {page === "create" && viewStep < 2 && <aside aria-label="项目素材预览" className="sp-preview studio-live-preview"><div className="sp-preview-title"><span>所选画面</span><span>项目真实素材</span></div>
        {selectedAsset ? selectedAsset.kind === "image" ? <img src={selectedAsset.url} alt={selectedAsset.name} style={{ aspectRatio: `${previewAspect.width} / ${previewAspect.height}` }} />
          : <video key={selectedAsset.id} src={selectedAsset.url} controls playsInline preload="metadata" aria-label="所选项目素材预览" style={{ aspectRatio: `${previewAspect.width} / ${previewAspect.height}` }} />
          : <div className="studio-preview-empty"><p>添加项目素材后，在这里核对画面。</p><button type="button" className="secondary-action" onClick={()=>onNavigate("assets")}>添加素材</button></div>}
        <div className="sp-preview-caption"><h3>{selectedAsset?.name ?? "画面尚未准备"}</h3><p>输出画布 {previewAspect.resolvedAspect} · {previewAspect.width}×{previewAspect.height}。素材会完整适配画布；生成后请在成片与审片中核对真实结果。</p></div>
      </aside>}
    </div>
    <div hidden={!batchVisible}><BatchEditor projectId={project.id} presentation={mode} focusTaskId={projection?.activeBatchId ?? taskId} projectAssets={assets} visible={active&&batchVisible}
      onDraftChange={setBatchDirty} onOpenEdit={()=>onNavigate("edit")} />
      {page === "create" && viewStep === 2 && <div className="studio-stage-actions"><p>配音与预览就绪后，逐条审核真实成片。</p><button type="button" className="primary-action" disabled={!reviewReady} onClick={()=>{setViewStep(3);onNavigate("review");}}>进入审核导出</button></div>}
    </div>
    <div hidden={!oldVisible} className={`studio-existing studio-existing-${page}`}><PreproductionWorkspace project={project} tools={tools} studioMode
      sectionOverride={page === "assets" ? "assets" : page === "tools" ? "tools" : "shots"}
      importedWorkspace={importedWorkspace} onDraftChange={onPreproductionDirty} onAssetsChanged={setAssets} /></div>
  </>;
}
