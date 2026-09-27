import { useEffect, useRef, useState, type ChangeEvent } from 'react';
import type { Project } from './models';
import { readApiError } from './referenceMediaApi';
import './projectBackup.css';

export function ProjectBackup({ projectId, onRestored, hasUnsavedDraft = false }: { hasUnsavedDraft?: boolean; projectId?: string; onRestored?: (project: Project) => void }) {
  const input = useRef<HTMLInputElement>(null);
  const live = useRef(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  useEffect(() => { live.current = true; return () => { live.current = false; }; }, []);

  async function backup() {
    if (!projectId || busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      if (hasUnsavedDraft) throw new Error('项目还有未保存草稿，请先保存或处理草稿，再备份项目。');
      for (const kind of ['timeline', 'preproduction']) {
        const key = `aivre:${kind}-draft:${projectId}`;
        if (localStorage.getItem(key) || sessionStorage.getItem(key)) throw new Error('项目还有未保存草稿，请先保存或处理草稿，再备份项目。');
      }
      const response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/backup`, { method: 'POST', headers: { 'Content-Type': 'application/json', 'x-aivre-intent': 'semantic-analysis' }, body: '{}' });
      if (!response.ok) throw new Error(await readApiError(response, '无法备份项目。'));
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement('a'); link.href = url; link.download = `project-${projectId}.zip`;
      document.body.append(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      if (live.current) setNotice('备份已生成并交给浏览器下载，请确认 ZIP 已保存。');
    } catch (reason) { if (live.current) setError(reason instanceof Error ? reason.message : '无法连接本地服务。'); }
    finally { if (live.current) setBusy(false); }
  }

  async function restore(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; event.target.value = '';
    if (!file || busy) return;
    setBusy(true); setError(''); setNotice('');
    try {
      if (file.size > 4 * 1024 ** 3) throw new Error('备份 ZIP 不能超过 4 GiB。');
      const response = await fetch('/api/project-backups/restore', { method: 'POST', headers: { 'Content-Type': 'application/zip', 'x-aivre-intent': 'semantic-analysis' }, body: file });
      if (!response.ok) throw new Error(await readApiError(response, '无法恢复项目。'));
      const project = await response.json() as Project;
      if (live.current) { setNotice('恢复成功，已创建独立的新项目。'); onRestored?.(project); }
    } catch (reason) { if (live.current) setError(reason instanceof Error ? reason.message : '无法连接本地服务。'); }
    finally { if (live.current) setBusy(false); }
  }

  return <section className="project-backup" aria-label={projectId ? '项目备份' : '项目恢复'}>
    {projectId ? <button type="button" className="secondary-action" disabled={busy} onClick={() => void backup()}>{busy ? '正在打包项目…' : '备份项目 ZIP'}</button> : <><button type="button" className="secondary-action" disabled={busy} onClick={() => input.current?.click()}>{busy ? '正在校验并恢复…' : '恢复项目 ZIP'}</button><input hidden ref={input} type="file" accept=".zip,application/zip" aria-label="选择项目备份 ZIP" disabled={busy} onChange={(event) => void restore(event)} /></>}
    <p>{projectId ? '包含已保存的素材、镜头方案、时间线与已有产物，不含浏览器草稿、账号配置或模型。' : '从备份创建独立新项目，原项目不变；恢复不会自动运行任务。'}最多 4 GiB。</p>
    {error && <p role="alert" className="inline-error">{error}</p>}
    {notice && <p role="status">{notice}</p>}
  </section>;
}
