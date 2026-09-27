import { useEffect, useRef, useState } from "react";
import { readApiError } from "./referenceMediaApi";
type Preview = { revision: number; bytes: number; fileCount: number; description: string };
type Props<T> = { label: string; previewUrl: string; deleteUrl: string; revision: number; disabled: boolean; onBusy: (busy: boolean) => void; onComplete: (value: T) => void };
export function HistoryCleanup<T>({ label, previewUrl, deleteUrl, revision, disabled, onBusy, onComplete }: Props<T>) {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const live = useRef(true);
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);
  async function request(remove: boolean) {
    if (busy || disabled || (remove && (!preview || preview.revision !== revision))) return;
    setBusy(true); setError(""); onBusy(true);
    try {
      const response = await fetch(remove ? deleteUrl : previewUrl, remove ? {
        method: "POST", headers: { "Content-Type": "application/json", "X-AIVRE-Intent": "semantic-analysis" }, body: JSON.stringify({ revision: preview!.revision }),
      } : {});
      if (!response.ok) throw new Error(await readApiError(response, "无法清理历史，请重新检查。"));
      const value = await response.json();
      if (remove) { onComplete(value as T); if (live.current) setPreview(null); }
      else if (live.current) setPreview(value as Preview);
    } catch (reason) {
      if (live.current) setError(reason instanceof Error ? reason.message : "无法连接本地服务，请重试。");
    } finally { onBusy(false); if (live.current) setBusy(false); }
  }
  const size = preview ? preview.bytes < 1024 ? `${preview.bytes} 字节` : preview.bytes < 1024 * 1024 ? `${(preview.bytes / 1024).toFixed(1)} KiB` : `${(preview.bytes / 1024 / 1024).toFixed(1)} MiB` : "";
  return <div className="history-cleanup">
    <button type="button" className="secondary-action" disabled={disabled || busy} onClick={() => void request(false)}>{label}</button>
    {preview && <div className="history-cleanup-preview" role="group" aria-label={`${label}影响范围`}>
      <p>{preview.description}</p><p>预计释放 {size}，涉及 {preview.fileCount} 个文件。</p>
      {preview.revision !== revision && <p>版本已变化，请重新预览清理范围。</p>}
      <button type="button" className="secondary-action" disabled={disabled || busy || preview.revision !== revision} onClick={() => void request(true)}>确认{label}</button>
      <button type="button" className="secondary-action" disabled={busy} onClick={() => { setPreview(null); setError(""); }}>取消清理</button>
    </div>}
    {error && <p role="alert">{error}</p>}
  </div>;
}
