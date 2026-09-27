import { useEffect, useRef, useState } from "react";

import { searchProductImages, importProductImages, type ProductImageSearch } from "./productImagesApi";
import type { PreproductionWorkspace } from "./preproductionApi";
import { listAnalysisProviders } from "./analysisProviderApi";
import type { AnalysisProviderConfiguration } from "./models";
import type { TimelineAsset } from "./timelineApi";
import { AspectRatioPicker } from "./AspectRatioPicker";
import { resolveProductAspect, type AspectResolution } from "./videoAspect";
import { confirmAigcCandidate, discloseAigcRequest, generateAigcCandidates, getAigcWorkspace, handoffAigcGeneration, saveAigcBrief, saveAigcCandidate,
  type AigcBrief, type AigcCandidate, type AigcDisclosure, type AigcAsset } from "./aigcContentApi";

function lines(value: string): string[] { return value.split("\n").map((item) => item.trim()).filter(Boolean); }
function message(reason: unknown): string { return reason instanceof Error ? reason.message : "操作失败，请重试。"; }

export function AigcCreator({ projectId, onDraftChange, onBatchCreated, guidedStep, onStepChange, onProgress, onOpenAssets, mediaAssets, onPreviewAsset, onAspectChange, onWorkspaceImported }: { projectId: string;
  onDraftChange?: (dirty: boolean) => void; onBatchCreated?: (taskId: string) => void;
  guidedStep?: 0 | 1; onStepChange?: (step: 0 | 1) => void; onProgress?: (progress: { hasCandidates: boolean; ready: boolean }) => void;
  onOpenAssets?: () => void; mediaAssets?: Array<TimelineAsset & {notes?:string;available?:boolean}>; onPreviewAsset?: (id:string|null)=>void;
  onAspectChange?: (aspect: AspectResolution) => void; onWorkspaceImported?: (workspace:PreproductionWorkspace)=>void }) {
  const projectRef = useRef(projectId);
  projectRef.current = projectId;
  const [imageSearch, setImageSearch] = useState<ProductImageSearch|null>(null);
  const [failedImages, setFailedImages] = useState<string[]>([]);
  const [imageOperation, setImageOperation] = useState<"search"|"import"|null>(null);
  const [imageIds, setImageIds] = useState<string[]>([]);
  const [imagesAccepted, setImagesAccepted] = useState(false);
  const [continueAfterImages, setContinueAfterImages] = useState(false);
  const searchRequestRef = useRef(0);
  const [brief, setBrief] = useState<AigcBrief | null>(null);
  const [savedBrief, setSavedBrief] = useState<AigcBrief | null>(null);
  const [factsText, setFactsText] = useState("");
  const [sellingPointsText, setSellingPointsText] = useState("");
  const [forbiddenText, setForbiddenText] = useState("");
  const [assets, setAssets] = useState<AigcAsset[]>([]);
  const [candidates, setCandidates] = useState<AigcCandidate[]>([]);
  const [candidateDrafts, setCandidateDrafts] = useState<Record<string, AigcCandidate>>({});
  const [providers, setProviders] = useState<AnalysisProviderConfiguration[]>([]);
  const [providerId, setProviderId] = useState("");
  const [disclosure, setDisclosure] = useState<AigcDisclosure | null>(null);
  const [accepted, setAccepted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [handoffTaskId, setHandoffTaskId] = useState("");
  const [handoffSkipped, setHandoffSkipped] = useState(0);
  const [selectedCandidate, setSelectedCandidate] = useState(0);
  const [previewBeatIndex, setPreviewBeatIndex] = useState(0);
  useEffect(() => { setPreviewBeatIndex(0); }, [projectId, selectedCandidate]);
  const guided = guidedStep !== undefined;
  const dirty = Boolean(brief && savedBrief && JSON.stringify(brief) !== JSON.stringify(savedBrief));
  const candidateDirty = Object.keys(candidateDrafts).length > 0;
  const latestGroup = candidates.filter(candidate => candidate.generationId === candidates[candidates.length - 1]?.generationId);
  const visibleCandidates = guided ? latestGroup : candidates;
  const availableAssets = guided && mediaAssets ? mediaAssets.filter(asset=>asset.kind === "image" || asset.kind === "video") : assets;
  const usableAssets = availableAssets.filter(asset=>!("available" in asset) || asset.available !== false);
  const productAspect = resolveProductAspect(brief?.aspectMode ?? "9:16", usableAssets.filter(asset => brief?.assetIds.includes(asset.id)));
  useEffect(() => { onAspectChange?.(productAspect); }, [onAspectChange, productAspect.resolvedAspect, productAspect.width, productAspect.height, productAspect.reason]);
  const ready = !dirty && !candidateDirty && latestGroup.length === 5 && latestGroup.every(candidate => candidate.briefRevision === brief?.revision && candidate.confirmedRevision === candidate.revision);
  useEffect(() => { onProgress?.({ hasCandidates: latestGroup.length > 0, ready }); }, [latestGroup.length, ready, onProgress]);
  const currentCandidate = latestGroup[selectedCandidate];
  const previewAssetId = guidedStep === 1 ? (currentCandidate && (candidateDrafts[currentCandidate.id] ?? currentCandidate))?.beats[previewBeatIndex]?.assetId : brief?.assetIds[0];
  useEffect(()=>{onPreviewAsset?.(previewAssetId??null);},[previewAssetId,onPreviewAsset]);

  useEffect(() => {
    let active = true;
    searchRequestRef.current += 1; setImageSearch(null); setImageIds([]); setFailedImages([]); setImagesAccepted(false); setContinueAfterImages(false); setImageOperation(null);
    setLoading(true); setBusy(false); setBrief(null); setSavedBrief(null); setCandidateDrafts({}); setDisclosure(null); setAccepted(false); setError(""); setHandoffTaskId(""); setHandoffSkipped(0);
    void Promise.all([getAigcWorkspace(projectId), listAnalysisProviders()]).then(([workspace, available]) => {
      if (!active) return;
      setBrief(workspace.brief); setSavedBrief(workspace.brief); setAssets(workspace.assets);
      setFactsText(workspace.brief.facts.map((item) => item.text).join("\n"));
      setSellingPointsText(workspace.brief.sellingPoints.join("\n"));
      setForbiddenText(workspace.brief.forbiddenPhrases.join("\n"));
      setCandidates(workspace.candidates); setProviders(available);
      setProviderId(available.find((item) => item.provider === item.selectedProvider && item.model && item.verificationState === "available")?.provider
        ?? available.find((item) => item.model && item.verificationState === "available")?.provider ?? "");
    }).catch((reason) => { if (active) setError(message(reason)); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [projectId]);
  useEffect(() => { onDraftChange?.(dirty || candidateDirty); }, [dirty, candidateDirty, onDraftChange]);

  function change(update: Partial<AigcBrief>) {
    if (update.productName !== undefined) { searchRequestRef.current += 1; setImageSearch(null); setImageIds([]); setFailedImages([]); setImagesAccepted(false); }
    const autoFact = brief && `商品名称：${brief.productName.trim()}。仅描述所选画面可核对的信息，不推断功效、价格或市场表现。`;
    if (update.productName !== undefined && brief?.facts.length===1 && brief.facts[0].id==="product-name" && brief.facts[0].text===autoFact) {
      update = {...update,facts:[]}; setFactsText("");
    }
    setBrief((current) => current ? { ...current, ...update } : current);
    setDisclosure(null); setAccepted(false);
  }

  async function reloadProviders() {
    setBusy(true); setError(""); setDisclosure(null); setAccepted(false);
    try {
      const available = await listAnalysisProviders();
      if (projectRef.current !== projectId) return;
      setProviders(available);
      const verified = available.filter(item => item.model && item.verificationState === "available");
      setProviderId(current => verified.some(item => item.provider === current) ? current :
        verified.find(item => item.provider === item.selectedProvider)?.provider ?? verified[0]?.provider ?? "");
    } catch (reason) { if (projectRef.current === projectId) setError(message(reason)); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  async function save() {
    if (!brief || !savedBrief) return;
    setBusy(true); setError("");
    try {
      const result = await saveAigcBrief(projectId, { ...brief, revision: savedBrief.revision });
      if (projectRef.current !== projectId) return;
      setBrief(result.brief); setSavedBrief(result.brief); setDisclosure(null); setAccepted(false);
      setFactsText(result.brief.facts.map((item) => item.text).join("\n"));
      setSellingPointsText(result.brief.sellingPoints.join("\n"));
      setForbiddenText(result.brief.forbiddenPhrases.join("\n"));
    } catch (reason) { if (projectRef.current === projectId) setError(message(reason)); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  async function preview() {
    const provider = providers.find((item) => item.provider === providerId);
    if (!provider?.model || provider.verificationState !== "available") return;
    setBusy(true); setError("");
    try {
      const result = await discloseAigcRequest(projectId, providerId, provider.model);
      if (projectRef.current !== projectId) return;
      setDisclosure(result); setAccepted(false);
    } catch (reason) { if (projectRef.current === projectId) setError(message(reason)); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  async function findImages(continueNext = false) {
    if (!brief?.productName.trim()) return;
    const requestId = ++searchRequestRef.current; setImageOperation("search");
    setBusy(true); setError(""); setImageSearch(null); setImageIds([]); setFailedImages([]); setImagesAccepted(false); setContinueAfterImages(continueNext);
    try {
      const result = await searchProductImages(projectId, brief.productName.trim());
      if (projectRef.current === projectId && requestId === searchRequestRef.current) setImageSearch(result);
    } catch (reason) { if (projectRef.current === projectId && requestId === searchRequestRef.current) setError(message(reason)); }
    finally { if (projectRef.current === projectId && requestId === searchRequestRef.current) {setBusy(false);setImageOperation(null);} }
  }

  async function prepareRequest(input = brief, suppliedIds?: string[]) {
    const provider = providers.find(item => item.provider === providerId);
    if (!input || !savedBrief || candidateDirty || !provider?.model || provider.verificationState !== "available") return;
    const selected = input.assetIds.filter(id=>usableAssets.some(asset=>asset.id===id));
    const ids = suppliedIds ?? [...new Set([...selected, ...usableAssets.map(asset=>asset.id)])].slice(0, Math.max(2, selected.length));
    if (ids.length < 2) { change({assetIds:ids}); await findImages(true); return; }
    const prepared = { ...input, assetIds: ids, audience: input.audience,
      facts: input.facts.length ? input.facts : [{ id: "product-name", text: `商品名称：${input.productName.trim()}。仅描述所选画面可核对的信息，不推断功效、价格或市场表现。` }] };
    setBusy(true); setError(""); setDisclosure(null); setAccepted(false);
    try {
      if (JSON.stringify(prepared) !== JSON.stringify(savedBrief)) {
        const result = await saveAigcBrief(projectId, { ...prepared, revision: savedBrief.revision });
        if (projectRef.current !== projectId) return;
        setBrief(result.brief); setSavedBrief(result.brief); setFactsText(result.brief.facts.map(item=>item.text).join("\n"));
      }
      const result = await discloseAigcRequest(projectId, providerId, provider.model);
      if (projectRef.current === projectId) setDisclosure(result);
    } catch (reason) { if (projectRef.current === projectId) setError(message(reason)); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  async function importImages() {
    if (!imageSearch || !imagesAccepted || imageIds.length < 1 || !brief) return;
    const requestId = searchRequestRef.current; setImageOperation("import");
    setBusy(true); setError("");
    try {
      const result = await importProductImages(projectId, imageSearch.searchId, imageIds);
      if (projectRef.current !== projectId || requestId !== searchRequestRef.current) return;
      onWorkspaceImported?.(result.workspace);
      setAssets(result.workspace.assets.filter((asset): asset is typeof asset & {kind:"image"|"video"}=>asset.kind === "image" || asset.kind === "video"));
      const ids = [...new Set([...brief.assetIds, ...result.importedAssetIds])];
      change({assetIds:ids}); setImageSearch(null); setImageIds([]); setFailedImages([]); setImagesAccepted(false);
      if (continueAfterImages && ids.length >= 2) await prepareRequest({...brief,assetIds:ids},ids);
    } catch (reason) { if (projectRef.current === projectId && requestId === searchRequestRef.current) setError(message(reason)); }
    finally { if (projectRef.current === projectId && requestId === searchRequestRef.current) {setBusy(false);setImageOperation(null);} }
  }

  async function generate() {
    if (!disclosure || !accepted || candidateDirty) return;
    setBusy(true); setError("");
    try {
      const result = await generateAigcCandidates(projectId, disclosure);
      if (projectRef.current !== projectId) return;
      setCandidates((current) => [...current, ...result.candidates]);
      setSelectedCandidate(0); onStepChange?.(1);
      setDisclosure(null); setAccepted(false);
    } catch (reason) { if (projectRef.current === projectId) setError(message(reason)); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  function editCandidate(candidate: AigcCandidate, update: Partial<AigcCandidate>) {
    const current = candidateDrafts[candidate.id] ?? candidate;
    const next = { ...current, ...update };
    setCandidateDrafts((drafts) => {
      const updated = { ...drafts };
      if (next.sellingPoint === candidate.sellingPoint && JSON.stringify(next.beats) === JSON.stringify(candidate.beats)) delete updated[candidate.id];
      else updated[candidate.id] = next;
      return updated;
    });
  }

  async function saveCandidate(candidate: AigcCandidate) {
    const draft = candidateDrafts[candidate.id] ?? candidate;
    if (!candidateDrafts[candidate.id] && candidate.briefRevision === brief?.revision) return;
    setBusy(true); setError("");
    try {
      const result = await saveAigcCandidate(projectId, draft);
      if (projectRef.current !== projectId) return;
      setCandidates((current) => current.map((item) => item.id === candidate.id ? result.candidate : item));
      setCandidateDrafts((current) => { const next = { ...current }; delete next[candidate.id]; return next; });
    } catch (reason) { if (projectRef.current === projectId) setError(message(reason)); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  async function confirmCandidate(candidate: AigcCandidate) {
    setBusy(true); setError("");
    try {
      const result = await confirmAigcCandidate(projectId, candidate);
      if (projectRef.current !== projectId) return;
      setCandidates((current) => current.map((item) => item.id === candidate.id ? result.candidate : item));
    } catch (reason) { if (projectRef.current === projectId) setError(message(reason)); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  async function saveAndConfirm(candidate: AigcCandidate) {
    setBusy(true); setError("");
    try {
      let current = candidate;
      if (candidateDrafts[candidate.id] || candidate.briefRevision !== brief?.revision) {
        current = (await saveAigcCandidate(projectId, candidateDrafts[candidate.id] ?? candidate)).candidate;
        if (projectRef.current !== projectId) return;
        setCandidates(items => items.map(item => item.id === current.id ? current : item));
        setCandidateDrafts(items => { const next = { ...items }; delete next[current.id]; return next; });
      }
      const result = await confirmAigcCandidate(projectId, current);
      if (projectRef.current !== projectId) return;
      setCandidates(items => items.map(item => item.id === result.candidate.id ? result.candidate : item));
      setSelectedCandidate(index => Math.min(index + 1, latestGroup.length - 1));
    } catch (reason) { if (projectRef.current === projectId) setError(message(reason)); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  async function handoff(generationId: string) {
    setBusy(true); setError("");
    try {
      const result = await handoffAigcGeneration(projectId, generationId);
      if (projectRef.current !== projectId) return;
      setHandoffTaskId(result.task.id);
      setHandoffSkipped(result.skippedTaskIds?.length ?? 0);
      onBatchCreated?.(result.task.id);
    } catch (reason) { if (projectRef.current === projectId) setError(message(reason)); }
    finally { if (projectRef.current === projectId) setBusy(false); }
  }

  return <section className={`batch-editor-aigc ${guided ? "studio-aigc" : ""}`} aria-label={`项目 ${projectId} 的 AI 商品内容创作`}>
    <h4>{guided ? guidedStep === 0 ? "准备资料与素材" : "确认脚本与对应画面" : "AI 商品内容创作"}</h4>
    <p>{guided ? "只填商品名称即可开始。有图片就直接用，没有就自动找图；其他资料可选填。" : "用已有商品素材生成 5 条脚本候选。核对商品事实后，每次发送仍由你确认。"}</p>
    {error && <p role="alert" className="preproduction-error">{error}</p>}
    {loading && <p>读取创作简报中…</p>}
    {brief && <fieldset disabled={guided && busy} className="studio-aigc-fields"><div className="preproduction-form-grid">
      <div className="studio-brief-fields" hidden={guided && guidedStep !== 0}>
      <label>商品名称<input value={brief.productName} onChange={(event) => change({ productName: event.target.value })} /></label>
      <AspectRatioPicker name={`product-aspect-${projectId}`} value={brief.aspectMode ?? "9:16"}
        onChange={(aspectMode) => change({ aspectMode })} resolution={productAspect} />
      <details className="studio-optional-brief" open={!guided || undefined}><summary>补充资料（选填）</summary>
      <label>商品事实（每行一条）<textarea value={factsText}
        onChange={(event) => { setFactsText(event.target.value); change({ facts: lines(event.target.value).map((text, index) =>
          ({ id: brief.facts[index]?.id ?? `fact-${index + 1}`, text })) }); }} /></label>
      <label>目标受众<input value={brief.audience} onChange={(event) => change({ audience: event.target.value })} /></label>
      <label>卖点（每行一条）<textarea value={sellingPointsText}
        onChange={(event) => { setSellingPointsText(event.target.value); change({ sellingPoints: lines(event.target.value) }); }} /></label>
      <label>行动引导<input value={brief.callToAction} onChange={(event) => change({ callToAction: event.target.value })} /></label>
      <label>禁用表达（每行一条）<textarea value={forbiddenText}
        onChange={(event) => { setForbiddenText(event.target.value); change({ forbiddenPhrases: lines(event.target.value) }); }} /></label>
      </details>
      <fieldset><legend>客户已有画面素材</legend>{usableAssets.map((asset) => <label key={asset.id}>
        {guided && mediaAssets?.find(item => item.id === asset.id) && <div className="studio-asset-thumb">{asset.kind === "image" ? <img src={mediaAssets.find(item => item.id === asset.id)!.url} alt={asset.name} /> : <video src={mediaAssets.find(item => item.id === asset.id)!.url} preload="metadata" muted aria-label={`${asset.name} 缩略预览`} />}</div>}
        <input type="checkbox" aria-label={`选择素材 ${asset.name}`} checked={brief.assetIds.includes(asset.id)}
          onChange={() => change({ assetIds: brief.assetIds.includes(asset.id)
            ? brief.assetIds.filter((id) => id !== asset.id) : [...brief.assetIds, asset.id] })} />
        {asset.name} · {asset.kind === "image" ? "图片" : "视频"}{asset.notes ? ` · ${asset.notes}` : ""}
      </label>)}</fieldset>
      {guided && <button type="button" className="secondary-action" disabled={busy || !brief.productName.trim()} onClick={()=>void findImages()}>自动查找商品图片</button>}
      {guided && imageOperation && <p role="status">{imageOperation === "search" ? "正在按商品名称搜索图片并读取来源，请稍候…" : "正在下载并校验商品图片…"}</p>}
      {guided && imageSearch && <section className="studio-image-search" aria-label="网络商品图片">
        <p>{imageSearch.candidates.length ? "选择与商品及规格一致的图片（最多 4 张），来源可打开核对。" : "未找到可用商品图片，请换一个更具体的商品名称或上传图片。"}</p>
        <div className="studio-image-grid">{imageSearch.candidates.map(image=><article key={image.id}>
          {failedImages.includes(image.id) ? <p>图片无法读取，请选择其他图片</p> : <img src={image.previewUrl} alt={image.title} loading="lazy" onError={()=>{setFailedImages(ids=>[...ids,image.id]);setImageIds(ids=>ids.filter(id=>id!==image.id));setImagesAccepted(false);}} />}
          <label><input type="checkbox" aria-label={`选择网络图片 ${image.title}`} checked={imageIds.includes(image.id)} disabled={failedImages.includes(image.id)||(!imageIds.includes(image.id)&&imageIds.length>=4)}
            onChange={()=>{setImageIds(ids=>ids.includes(image.id)?ids.filter(id=>id!==image.id):[...ids,image.id]);setImagesAccepted(false);}} />{image.title}</label>
          <a href={image.pageUrl} target="_blank" rel="noreferrer">查看图片来源</a>
        </article>)}</div>
        {imageSearch.candidates.length>0 && <><label><input type="checkbox" checked={imagesAccepted} onChange={event=>setImagesAccepted(event.target.checked)} />我已核对商品、规格与图片来源</label>
          <p>图片会保存到本地项目。来源记录不代表商业授权；请选择符合本次用途的素材。</p>
          <button type="button" className="primary-action" disabled={busy||!imagesAccepted||!imageIds.length||(continueAfterImages&&imageIds.length+brief.assetIds.length<2)} onClick={()=>void importImages()}>{continueAfterImages?"导入选中图片并继续":"导入选中图片"}</button></>}
      </section>}
      {guided && onOpenAssets && <button type="button" className="secondary-action" onClick={onOpenAssets}>上传或管理项目素材</button>}
      <button type="button" className={guided ? "secondary-action" : "primary-action"} disabled={busy || !dirty} onClick={() => void save()}>{guided ? "保存资料" : "保存创作简报"}</button>
      <label>脚本生成服务<select value={providerId} onChange={(event) => { setProviderId(event.target.value); setDisclosure(null); setAccepted(false); }}>
        <option value="">请选择已验证服务</option>{providers.filter((item) => item.model && item.verificationState === "available").map((item) =>
          <option key={item.provider} value={item.provider}>{item.label} · {item.model}</option>)}</select></label>
      {guided && <button type="button" className="secondary-action" disabled={busy} onClick={() => void reloadProviders()}>重新读取脚本服务</button>}
      {guided ? <button type="button" className="primary-action" disabled={busy || candidateDirty || !providerId || !brief.productName.trim()}
        onClick={() => void prepareRequest(brief)}>生成 5 条脚本</button> : <button type="button" className="secondary-action" disabled={busy || dirty || !providerId}
        onClick={() => void preview()}>预览发送内容</button>}
      {guided && !providerId && <p role="status">尚无已通过连接验证的脚本服务，请在应用设置中配置并验证后重新读取。</p>}
      {guided && brief.assetIds.length < 2 && <p role="status">已有素材会优先使用；不足两份时会查找商品图片。</p>}
      {guided && candidateDirty && <div className="studio-inline-status"><p>有未保存脚本，请先返回核对并保存，避免重新生成后遗漏修改。</p><button type="button" className="secondary-action" onClick={() => onStepChange?.(1)}>返回未保存脚本</button></div>}
      {disclosure && <div className="batch-editor-disclosure">
        <p>目标服务：{providers.find((item) => item.provider === disclosure.provider)?.label ?? disclosure.provider} · {disclosure.model}</p>
        <p>只发送以下文字；原始图片、视频及本地文件不会发送。</p>
        <pre>{disclosure.prompt}</pre>
        <label><input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} />我已核对本次发送内容</label>
        <button type="button" className="primary-action" disabled={busy || !accepted || candidateDirty}
          onClick={() => void generate()}>确认发送并生成 5 条候选</button>
      </div>}
      </div>
      {guided && guidedStep === 1 && candidates.length === 0 && <div className="sp-empty"><p>这个项目尚无脚本候选。请先准备资料并生成脚本；已有制作任务可在选声制作或成片与审片中继续。</p><button type="button" className="secondary-action" onClick={() => onStepChange?.(0)}>返回准备资料</button></div>}
      {candidates.length > 0 && <div hidden={guided && guidedStep !== 1} className="studio-candidates"><h5>脚本候选</h5>
        {guided && <nav className="studio-candidate-rail" aria-label="脚本候选导航">{visibleCandidates.map((candidate, index) => <button type="button" key={candidate.id} aria-pressed={selectedCandidate === index} onClick={() => setSelectedCandidate(index)}>{index + 1}. {candidate.sellingPoint}<small>{candidate.confirmedRevision === candidate.revision && candidate.briefRevision === brief.revision && !candidateDrafts[candidate.id] ? "已确认" : "待确认"}</small></button>)}</nav>}
        {visibleCandidates.map((candidate, index) => {
        const draft = candidateDrafts[candidate.id] ?? candidate;
        const confirmed = candidate.confirmedRevision === candidate.revision && candidate.briefRevision === brief.revision;
        return <article key={candidate.id} hidden={guided && index !== selectedCandidate}>
          <h6>{candidate.sellingPoint}</h6>
          <p>{confirmed ? `已确认版本 ${candidate.revision}` : "待确认"}</p>
          <label>候选 {index + 1} 卖点<input value={draft.sellingPoint}
            onChange={(event) => editCandidate(candidate, { sellingPoint: event.target.value })} /></label>
          <ol>{draft.beats.map((beat, beatIndex) => <li key={beatIndex}>
            {beatIndex > 0 && <button type="button" className="secondary-action"
              onClick={() => { const reordered = [...draft.beats]; [reordered[beatIndex - 1], reordered[beatIndex]] =
                [reordered[beatIndex], reordered[beatIndex - 1]]; editCandidate(candidate, { beats: reordered }); }}>
              上移候选 {index + 1} 镜头 {beatIndex + 1}</button>}
            <label>候选 {index + 1} 镜头 {beatIndex + 1} 屏幕文案<input value={beat.text}
              onChange={(event) => editCandidate(candidate, { beats: draft.beats.map((item, position) =>
                position === beatIndex ? { ...item, text: event.target.value } : item) })} /></label>
            <label>候选 {index + 1} 镜头 {beatIndex + 1} 画面素材<select value={beat.assetId}
              onChange={(event) => { setPreviewBeatIndex(beatIndex); editCandidate(candidate, { beats: draft.beats.map((item, position) =>
                position === beatIndex ? { ...item, assetId: event.target.value } : item) }); }}>
              {availableAssets.map((asset) => <option key={asset.id} value={asset.id}>{asset.name}</option>)}</select></label>
            {guided && <button type="button" className="secondary-action" aria-pressed={previewBeatIndex === beatIndex} onClick={() => setPreviewBeatIndex(beatIndex)}>查看候选 {index + 1} 镜头 {beatIndex + 1} 画面</button>}
            <fieldset><legend>依据的商品事实</legend>{brief.facts.map((fact) => <label key={fact.id}>
              <input type="checkbox" checked={beat.factIds.includes(fact.id)} onChange={() => editCandidate(candidate, {
                beats: draft.beats.map((item, position) => position === beatIndex ? { ...item,
                  factIds: item.factIds.includes(fact.id) ? item.factIds.filter((id) => id !== fact.id) : [...item.factIds, fact.id] } : item),
              })} />{fact.text}</label>)}</fieldset>
          </li>)}</ol>
          {!guided && <><button type="button" className="secondary-action" disabled={busy || (!candidateDrafts[candidate.id] && candidate.briefRevision === brief.revision)}
            onClick={() => void saveCandidate(candidate)}>保存候选 {index + 1}</button>
          <button type="button" className="primary-action" disabled={busy || Boolean(candidateDrafts[candidate.id]) || confirmed || candidate.briefRevision !== brief.revision}
            onClick={() => void confirmCandidate(candidate)}>确认候选 {index + 1}</button></>}
          {guided && <button type="button" className="primary-action" disabled={busy || (confirmed && !candidateDrafts[candidate.id])} onClick={() => void saveAndConfirm(candidate)}>{confirmed && !candidateDrafts[candidate.id] ? "这条已确认" : "确认这条并看下一条"}</button>}
        </article>;
      })}
        {[...new Set(visibleCandidates.map((candidate) => candidate.generationId))].map((generationId) => {
          const group = candidates.filter((candidate) => candidate.generationId === generationId);
          const ready = !dirty && !candidateDirty && group.length === 5 && group.every((candidate) =>
            candidate.briefRevision === brief.revision && candidate.confirmedRevision === candidate.revision);
          return <div key={generationId}>
            <button type="button" className="primary-action" disabled={busy || !ready}
              onClick={() => void handoff(generationId)}>{guided ? "继续选声音" : "将 5 条已确认候选交给批量混剪"}</button>
          </div>;
        })}
        {handoffTaskId && <p role="status">{handoffSkipped
          ? `已更新批量任务；${handoffSkipped} 条失败变体已有人工修改，未自动覆盖，请在批量任务中继续修复。`
          : "已创建批量任务，可继续预览和审核。"}</p>}
      </div>}
    </div></fieldset>}
  </section>;
}
