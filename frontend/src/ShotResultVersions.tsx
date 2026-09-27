import { useEffect, useState } from "react";
import { HistoryCleanup } from "./HistoryCleanup";
import { getAssetReferences, updateShotAdoptionReason, updateShotResultNote, type AssetReference, type PreproductionWorkspace, type PreproductionAsset, type PreproductionShot, type ShotResultVersion } from "./preproductionApi";

type Props = {
  shot: PreproductionShot;
  assets: PreproductionAsset[];
  dirty: boolean;
  busy: boolean;
  onSelect: (assetId: string) => void;
  onManage?: (assetId: string) => void;
  onFocusStep?: (nodeId: string) => void;
  onFocusClip?: (trackId: string, clipId: string) => void;
  projectId?: string;
  revision?: number;
  onBusy?: (busy: boolean) => void;
  onChanged?: (workspace: PreproductionWorkspace, action?: "note" | "reason") => void;
  onReview: (assetId: string) => void;
};

export function ShotResultVersions({ shot, assets, dirty, busy, onSelect, onReview, onManage, onFocusStep, onFocusClip, projectId, revision, onBusy, onChanged }: Props) {
  const versions = shot.resultVersions ?? (shot.resultAssetId ? [{ assetId: shot.resultAssetId, reviewed: false, planChanged: true }] : []);
  if (!versions.length) return null;
  return <section className="shot-result-versions" aria-label="镜头结果候选版本">
    <h4>候选版本 · {versions.length}</h4>
    <p>检查状态由你确认，不代表模型或系统自动验收。</p>
    {dirty && <p role="status">方案存在未保存更改，保存后重新判断检查状态；采用选择也需保存。</p>}
    <ol>{versions.map((version, index) => {
      const asset = assets.find((item) => item.id === version.assetId);
      const adopted = shot.resultAssetId === version.assetId;
      return <li key={version.assetId}>
        <strong>候选 {index + 1} · {asset?.name ?? "缺失素材"}{asset?.available === false ? " · 文件不可用" : ""}{adopted ? " · 当前采用" : ""}</strong>
        {asset?.notes && <p>{asset.notes}</p>}
        {asset && onManage && <button type="button" className="secondary-action" disabled={busy} onClick={() => onManage(asset.id)}>管理候选 {index + 1}</button>}
        <p>{dirty ? "检查状态待保存后更新" : version.planChanged ? "方案已变化或缺少关联记录，请重新检查" : version.reviewed ? "已按当前方案人工检查" : "尚未人工检查"}</p>
        {version.adoptionReason && <p>采用理由：{version.adoptionReason}</p>}
        <CandidateAssociation version={version} shot={shot} assets={assets} onManage={onManage} onFocusStep={onFocusStep} onFocusClip={onFocusClip} projectId={projectId} revision={revision} dirty={dirty} busy={busy} onBusy={onBusy} onChanged={onChanged} />
        {asset?.available !== false && asset && <a href={asset.url} target="_blank" rel="noreferrer">查看候选 {index + 1}</a>}
        <div className="shot-result-version-actions"><button type="button" className="secondary-action" aria-pressed={adopted} disabled={!asset || asset.available === false || busy || adopted} onClick={() => onSelect(version.assetId)}>采用候选 {index + 1}</button><button type="button" className="secondary-action" disabled={!asset || asset.available === false || dirty || busy || (version.reviewed && !version.planChanged)} onClick={() => onReview(version.assetId)}>确认已检查候选 {index + 1}</button></div>
        {projectId && revision !== undefined && onBusy && onChanged && <HistoryCleanup<PreproductionWorkspace>
          key={`${shot.id}:${version.assetId}`} label={`移除候选 ${index + 1}`} revision={revision} disabled={dirty || busy || adopted}
          previewUrl={`/api/projects/${encodeURIComponent(projectId)}/preproduction/shots/${encodeURIComponent(shot.id)}/results/${encodeURIComponent(version.assetId)}/cleanup-preview`}
          deleteUrl={`/api/projects/${encodeURIComponent(projectId)}/preproduction/shots/${encodeURIComponent(shot.id)}/results/${encodeURIComponent(version.assetId)}/remove`}
          onBusy={onBusy} onComplete={onChanged} />}
      </li>;
    })}</ol>
  </section>;
}

function CandidateAssociation({ version, shot, assets, onManage, onFocusStep, onFocusClip, projectId, revision, dirty, busy, onBusy, onChanged }: {
  version: ShotResultVersion; shot: PreproductionShot; assets: PreproductionAsset[];
  onManage?: (assetId: string) => void; onFocusStep?: (nodeId: string) => void;
  onFocusClip?: (trackId: string, clipId: string) => void;
  projectId?: string; revision?: number; dirty: boolean; busy: boolean;
  onBusy?: (busy: boolean) => void; onChanged?: (workspace: PreproductionWorkspace, action?: "note" | "reason") => void;
}) {
  const [note, setNote] = useState(version.externalNote ?? "");
  const [reason, setReason] = useState(version.adoptionReason ?? "");
  const [error, setError] = useState("");
  const [opened, setOpened] = useState(false);
  const [uses, setUses] = useState<AssetReference[] | null>(null);
  const association = version.association;
  useEffect(() => setNote(version.externalNote ?? ""), [version.assetId, version.externalNote]);
  useEffect(() => setReason(version.adoptionReason ?? ""), [version.assetId, version.adoptionReason]);
  useEffect(() => {
    if (!opened || !projectId) return;
    let active = true;
    getAssetReferences(projectId, version.assetId).then((result) => {
      if (active) setUses(result.references.filter((item) => item.kind === "timeline" || item.kind === "timeline_history"));
    }).catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "无法读取剪辑引用。"); });
    return () => { active = false; };
  }, [opened, projectId, version.assetId]);
  async function saveNote() {
    if (!projectId || revision === undefined || !onChanged || !onBusy) return;
    setError(""); onBusy(true);
    try { onChanged(await updateShotResultNote(projectId, shot.id, version.assetId, revision, note), "note"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "无法保存候选备注。"); }
    finally { onBusy(false); }
  }
  async function saveReason() {
    if (!projectId || revision === undefined || !onChanged || !onBusy) return;
    setError(""); onBusy(true);
    try { onChanged(await updateShotAdoptionReason(projectId, shot.id, version.assetId, revision, reason), "reason"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "无法保存采用理由。"); }
    finally { onBusy(false); }
  }
  return <details className="shot-result-association" onToggle={(event) => setOpened(event.currentTarget.open)}>
    <summary>查看关联时方案</summary>
    {association ? <div>
      <p>关联于已保存版本 {association.revision}。这是本应用保存的方案关联，不证明外部模型实际使用了这些输入。</p>
      <p>镜头：{association.shot.title || association.shot.id} · 计划 {association.shot.duration} 秒</p>
      <p>输入类型：{association.brief.inputKind ?? "reference_video"} · 目标时长：{association.brief.duration} 秒</p>
      <p>主题：{association.brief.theme || "未填写"} · 用途：{association.brief.purpose || "未填写"}</p>
      <p>风格：{association.brief.style || "未填写"} · 画幅：{association.brief.aspect || "未填写"}</p>
      {association.brief.mustPreserve && <p>必须保留：{association.brief.mustPreserve}</p>}
      <p>提示词：{association.shot.prompt || "未填写"}</p>
      {association.shot.negativePrompt && <p>反向提示词：{association.shot.negativePrompt}</p>}
      <p>关联素材：</p>{association.assets.length ? <ul>{association.assets.map((asset) => {
        const current = assets.find((item) => item.id === asset.id);
        return <li key={asset.id}>{asset.name} · {asset.role} {current?.available === false || !current ? <span>文件不可用</span> : onManage ? <button type="button" className="secondary-action" onClick={() => onManage(asset.id)}>定位素材 {asset.name}</button> : null}</li>;
      })}</ul> : <p>无</p>}
      <p>关联步骤：</p>{association.nodes.length ? <ol>{association.nodes.map((node) => {
        const current = shot.nodes.find((item) => item.id === node.id);
        return <li key={node.id}>{node.kind} · 输入 {node.input || "无"} · 参数 {JSON.stringify(node.params)}{node.outputs.length ? ` · 产物 ${node.outputs.join("、")}` : ""} {current && onFocusStep ? <button type="button" className="secondary-action" onClick={() => onFocusStep(node.id)}>定位步骤 {node.id}</button> : !current ? <span>原步骤已不存在</span> : null}</li>;
      })}</ol> : <p>无</p>}
    </div> : <p>历史关联内容不可还原；当前方案不能作为该候选的原始来源。</p>}
    {projectId && <><p>剪辑使用位置：</p>{uses === null ? <p>正在读取引用…</p> : uses.length ? <ul>{uses.map((use, index) => <li key={`${use.kind}:${use.runId ?? "current"}:${use.clipId ?? index}`}>{use.kind === "timeline" && use.trackId && use.clipId && onFocusClip ? <button type="button" className="secondary-action" onClick={() => onFocusClip(use.trackId!, use.clipId!)}>定位{use.label}</button> : use.label}</li>)}</ul> : <p>当前时间线和历史输出中没有引用。</p>}</>}
    <label>外部制作备注（用户记录，未经应用核实）<textarea value={note} maxLength={4000} onChange={(event) => setNote(event.target.value)} /></label>
    {projectId && revision !== undefined && onChanged && onBusy && <button type="button" className="secondary-action" disabled={dirty || busy || note === (version.externalNote ?? "")} onClick={() => void saveNote()}>保存候选备注</button>}
    <label>采用理由（可选）<textarea value={reason} maxLength={1000} onChange={(event) => setReason(event.target.value)} /></label>
    {projectId && revision !== undefined && onChanged && onBusy && <button type="button" className="secondary-action" disabled={dirty || busy || reason === (version.adoptionReason ?? "")} onClick={() => void saveReason()}>保存采用理由</button>}
    {error && <p role="alert">{error}</p>}
  </details>;
}
