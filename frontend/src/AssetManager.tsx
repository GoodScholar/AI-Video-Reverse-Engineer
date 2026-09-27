import { useEffect, useRef, useState } from "react";
import { deletePreproductionAsset, getAssetReferences, updateAssetMetadata, type AssetReference, type PreproductionAsset, type PreproductionWorkspace } from "./preproductionApi";

type Props = {
  projectId: string; asset: PreproductionAsset; revision: number; blocked: boolean;
  onChanged: (workspace: PreproductionWorkspace) => void;
  onBusy: (busy: boolean) => void;
};

export function AssetManager({ projectId, asset, revision, blocked, onChanged, onBusy }: Props) {
  const [name, setName] = useState(asset.name);
  const [notes, setNotes] = useState(asset.notes ?? "");
  const [references, setReferences] = useState<AssetReference[] | null>(null);
  const [refresh, setRefresh] = useState(0);
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  useEffect(() => {
    let active = true;
    setReferences(null);
    setConfirm(false);
    getAssetReferences(projectId, asset.id).then((result) => { if (active) setReferences(result.references); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "无法读取引用。"); });
    return () => { active = false; };
  }, [projectId, asset.id, revision, refresh]);

  async function mutate(remove: boolean) {
    if (blocked || busy) return;
    setError(""); setMessage("");
    if (remove) {
      try {
        for (const kind of ["preproduction", "timeline"]) {
          const key = `aivre:${kind}-draft:${projectId}`;
          if (localStorage.getItem(key) || sessionStorage.getItem(key)) throw new Error("存在未保存草稿，请先保存或处理草稿后再删除素材。");
        }
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "无法检查草稿，暂不能删除。");
        return;
      }
    }
    setBusy(true); onBusy(true);
    try {
      const next = remove ? await deletePreproductionAsset(projectId, asset.id, revision)
        : await updateAssetMetadata(projectId, asset.id, revision, name.trim(), notes);
      onChanged(next);
      if (live.current) {
        setName(name.trim());
        setMessage(remove ? "素材已删除。" : "素材信息已保存。");
        setConfirm(false);
      }
    } catch (reason) {
      if (live.current) { setError(reason instanceof Error ? reason.message : "操作失败。"); setRefresh((value) => value + 1); }
    } finally {
      onBusy(false);
      if (live.current) setBusy(false);
    }
  }
  return <section className="asset-manager" aria-label={`管理素材 ${asset.name}`}>
    <h4>素材信息 · {asset.name}</h4>
    <p>名称和备注在素材库、候选列表中共用，不改变媒体内容。</p>
    {asset.source && <p>网络图片 · {asset.source.productName} · <a href={asset.source.pageUrl} target="_blank" rel="noreferrer">查看原始来源</a></p>}
    <label>素材名称<input value={name} maxLength={255} disabled={busy} onChange={(event) => setName(event.target.value)} /></label>
    <label>素材备注<textarea value={notes} maxLength={4000} disabled={busy} onChange={(event) => setNotes(event.target.value)} /></label>
    <button type="button" className="secondary-action" disabled={blocked || busy || !name.trim() || (name.trim() === asset.name && notes === (asset.notes ?? ""))} onClick={() => void mutate(false)}>保存素材信息</button>
    {blocked && <p>请先保存工作台更改，并等待正在执行的操作完成。</p>}
    <h4>引用位置</h4>
    <button type="button" className="secondary-action" disabled={busy} onClick={() => { setError(""); setRefresh((value) => value + 1); }}>刷新引用</button>
    {references === null ? <p>尚未完成引用检查。</p> : references.length ? <><ul>{references.map((reference, index) => <li key={index}>{reference.label}</li>)}</ul><p>被引用的素材不能删除。候选与输出历史也会保留引用；可在候选列表或渲染记录清理历史后重新检查。</p></> : <p>未发现已保存的引用。</p>}
    <button type="button" className="secondary-action" disabled={blocked || busy || references === null || references.length > 0} onClick={() => setConfirm(true)}>删除素材</button>
    {confirm && <div role="group" aria-label="确认删除素材"><p>将永久删除「{asset.name}」及其素材库文件。服务端会再次检查引用。</p><button type="button" className="secondary-action" disabled={blocked || busy} onClick={() => void mutate(true)}>确认删除{asset.name}</button><button type="button" className="secondary-action" disabled={busy} onClick={() => setConfirm(false)}>取消删除</button></div>}
    {error && <p role="alert">{error}</p>}{message && <p role="status">{message}</p>}
  </section>;
}
