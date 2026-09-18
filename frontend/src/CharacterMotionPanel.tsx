import { ChangeEvent, useEffect, useRef, useState } from "react";
import { Check, CircleAlert, Download, LoaderCircle, Play, RefreshCw, Save, Upload } from "lucide-react";
import type { Project } from "./models";
import { checkCharacterMotion, downloadCharacterMotionPackage, getCharacterMotion, refreshCharacterMotion, resolveCharacterMotion, saveCharacterMotion, startCharacterMotion, uploadCharacterMotionImage, type CharacterMotionState, type ComfyCheck } from "./characterMotionApi";
import "./characterMotion.css";

type Props = { project: Project; preparationOnly?: boolean };
type Session = { scope: string; generation: number };

const offlineState = (): CharacterMotionState => ({ revision: 0, prompt: "", settings: { width: 512, height: 512, frames: 81, fps: 16, seed: 42 }, comfyUrl: "http://127.0.0.1:8188", character: null, driver: null, sourceHash: null, stale: false, runs: [], template: { status: "candidate", requiredNodes: [], requiredModels: [] } });
const label = (status: string) => ({ submitting: "正在提交", queued: "队列中", running: "生成中", completed: "已完成", failed: "失败", unknown: "状态未知" }[status] ?? status);
const planKey = (value: CharacterMotionState) => JSON.stringify({ revision: value.revision, prompt: value.prompt, settings: value.settings, comfyUrl: value.comfyUrl, character: value.character?.id, driver: value.driver?.id, sourceHash: value.sourceHash });

export function CharacterMotionPanel({ project, preparationOnly = false }: Props) {
  const [state, setState] = useState<CharacterMotionState>(offlineState());
  const [draft, setDraft] = useState<CharacterMotionState>(offlineState());
  const [connected, setConnected] = useState(true);
  const [error, setError] = useState("");
  const [action, setAction] = useState("");
  const [check, setCheck] = useState<ComfyCheck | null>(null);
  const [pollAttempt, setPollAttempt] = useState(0);
  const [promptIds, setPromptIds] = useState<Record<string, string>>({});
  const [confirmedNotQueued, setConfirmedNotQueued] = useState<Record<string, boolean>>({});
  const [preprocessorConfirmed, setPreprocessorConfirmed] = useState(false);
  const scope = `${project.id}:${project.referenceMedia?.id ?? ""}`;
  const scopeRef = useRef(scope); scopeRef.current = scope;
  const generationRef = useRef(0);
  const stateRef = useRef(state); stateRef.current = state;
  const draftRef = useRef(draft); draftRef.current = draft;
  const same = planKey(state) === planKey(draft);
  const requiresSave = !same || state.stale;
  const active = state.runs.some((run) => ["submitting", "queued", "running"].includes(run.status));
  const usable = connected && !!draft.character && !!draft.driver && !!draft.prompt.trim() && !requiresSave;
  const editingLocked = !!action && action !== "check";
  const isCurrent = (session: Session) => scopeRef.current === session.scope && generationRef.current === session.generation;
  const session = (): Session => ({ scope, generation: generationRef.current });

  function apply(next: CharacterMotionState, request: Session, preserveDirtyDraft = false) {
    if (!isCurrent(request)) return false;
    const dirty = planKey(stateRef.current) !== planKey(draftRef.current) || stateRef.current.stale;
    setState(next); if (!preserveDirtyDraft || !dirty) setDraft(next); setCheck(null); setConnected(true); setPreprocessorConfirmed(false);
    return true;
  }

  useEffect(() => {
    const request = { scope, generation: generationRef.current + 1 };
    generationRef.current = request.generation;
    setState(offlineState()); setDraft(offlineState()); setConnected(true); setError(""); setAction(""); setCheck(null); setPromptIds({}); setConfirmedNotQueued({}); setPreprocessorConfirmed(false); setPollAttempt(0);
    void getCharacterMotion(project.id).then((next) => { apply(next, request); }).catch(() => {
      if (isCurrent(request)) { setConnected(false); setState(offlineState()); setDraft(offlineState()); }
    });
  }, [project.id, project.referenceMedia?.id]);

  useEffect(() => {
    if (preparationOnly || !active || !connected) return undefined;
    const run = state.runs.find((item) => ["submitting", "queued", "running"].includes(item.status));
    if (!run) return undefined;
    const request = session(); let disposed = false;
    const timer = window.setTimeout(() => {
      void refreshCharacterMotion(project.id, run.id).then((next) => {
        if (!disposed && isCurrent(request)) {
          const replaceDraft = planKey(stateRef.current) === planKey(draftRef.current) && !stateRef.current.stale;
          setState(next); if (replaceDraft) setDraft(next);
        }
      }).catch(() => {
        if (!disposed && isCurrent(request)) setPollAttempt((value) => value + 1);
      });
    }, 1500);
    return () => { disposed = true; window.clearTimeout(timer); };
  }, [preparationOnly, active, connected, pollAttempt, project.id, scope, state.runs]);

  async function upload(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; if (!file || !connected || action) return;
    const request = session(); setAction("upload"); setError("");
    try { apply(await uploadCharacterMotionImage(project.id, file), request); }
    catch (reason) { if (isCurrent(request)) setError(reason instanceof Error ? reason.message : "无法上传角色图片。"); }
    finally { if (isCurrent(request)) setAction(""); }
  }
  async function save() {
    if (!connected || (!requiresSave) || !draft.prompt.trim() || action) return;
    const request = session(); setAction("save"); setError("");
    try { apply(await saveCharacterMotion(project.id, { revision: draft.revision, prompt: draft.prompt, settings: draft.settings, comfyUrl: draft.comfyUrl }), request); }
    catch (reason) { if (isCurrent(request)) setError(reason instanceof Error ? reason.message : "无法保存角色动画方案。"); }
    finally { if (isCurrent(request)) setAction(""); }
  }
  async function inspect() {
    if (!usable || action) return;
    const request = session(); const savedAtStart = planKey(state); const draftAtStart = planKey(draft); setAction("check"); setError("");
    try {
      const result = await checkCharacterMotion(project.id);
      if (isCurrent(request) && planKey(stateRef.current) === savedAtStart && planKey(draftRef.current) === draftAtStart) setCheck(result);
    } catch (reason) { if (isCurrent(request)) setError(reason instanceof Error ? reason.message : "无法检查 ComfyUI。"); }
    finally { if (isCurrent(request)) setAction(""); }
  }
  async function generate() {
    if (preparationOnly || !usable || !check?.ready || action) return;
    const request = session(); const message = "无法提交本地生成。"; setAction("run"); setError("");
    try { apply(await startCharacterMotion(project.id, state.revision, true), request); }
    catch (reason) {
      const failure = reason instanceof Error ? reason.message : message;
      try { apply(await getCharacterMotion(project.id), request); } catch { /* 保留原提交失败信息 */ }
      if (isCurrent(request)) setError(failure);
    } finally { if (isCurrent(request)) setAction(""); }
  }
  async function exportPackage() {
    if (!usable || action) return;
    const request = session(); setAction("package"); setError("");
    try { await downloadCharacterMotionPackage(project.id); }
    catch (reason) { if (isCurrent(request)) setError(reason instanceof Error ? reason.message : "无法导出角色动画包。"); }
    finally { if (isCurrent(request)) setAction(""); }
  }
  async function refreshRun(runId: string) {
    if (action) return;
    const request = session(); setAction(`refresh:${runId}`); setError("");
    try { apply(await refreshCharacterMotion(project.id, runId), request, true); }
    catch (reason) { if (isCurrent(request)) setError(reason instanceof Error ? reason.message : "无法刷新生成状态。"); }
    finally { if (isCurrent(request)) setAction(""); }
  }
  async function resolveRun(runId: string, body: { promptId: string | null; confirmedNotQueued: boolean }) {
    if (action) return;
    const request = session(); setAction(`resolve:${runId}`); setError("");
    try { apply(await resolveCharacterMotion(project.id, runId, body), request, true); }
    catch (reason) { if (isCurrent(request)) setError(reason instanceof Error ? reason.message : "无法恢复未知状态。"); }
    finally { if (isCurrent(request)) setAction(""); }
  }
  const editDraft = (next: CharacterMotionState) => { if (!editingLocked) { setCheck(null); setPreprocessorConfirmed(false); setDraft(next); } };

  return <section className="character-motion-panel" aria-labelledby="character-motion-title">
    <div className="section-heading"><div><p className="eyebrow">WAN ANIMATE · CANDIDATE</p><h2 id="character-motion-title">角色动画与动作迁移</h2><p>Move 模式使用角色图片背景；驱动截取开头 frames/fps 秒，短素材保持末帧。</p></div></div>
    {preparationOnly ? <p className="character-template">准备角色、动作驱动与提示词，导出候选工作流和素材包。此处不执行视频生成，无需安装 ComfyUI。</p> : <p className="character-template">尚未安装 ComfyUI 时，可保存和导出；连接安装有模型的本机或局域网 ComfyUI 后才能生成。<a href="https://docs.comfy.org/installation/desktop/macos" target="_blank" rel="noreferrer">官方安装说明</a>。环境检查不能验证 DWPose 的私有预处理权重；首次运行扩展会自动下载。</p>}
    {!connected && <p className="character-motion-offline" role="status">{preparationOnly ? "本地服务未连接：仍可编辑方案，服务恢复后可保存和导出。" : "本地服务未连接：仍可编辑方案。保存、环境检查和生成将在服务恢复后可用。"}</p>}
    {error && <p className="inline-error" role="alert"><CircleAlert size={16} />{error}</p>}
    <div className="character-motion-grid">
      <label className="character-upload">角色图片<input aria-label="角色图片" type="file" accept="image/png,image/jpeg,image/webp" disabled={!connected || editingLocked} onChange={upload} /><span><Upload size={16} />{draft.character ? `${draft.character.originalName} · ${draft.character.width}×${draft.character.height}` : "上传独立角色图"}</span></label>
      <p className="character-driver"><strong>参考驱动</strong>{draft.driver ? `${draft.driver.originalName} · ${draft.driver.width}×${draft.driver.height}` : "当前项目尚无可用视频；请在上方上传 MP4/MOV。"}</p>
      <label>动作提示词<textarea aria-label="动作提示词" disabled={editingLocked} value={draft.prompt} onChange={(event) => editDraft({ ...draft, prompt: event.target.value })} placeholder="例如：full-body dancer moves naturally, stable face" /></label>
      {!preparationOnly && <label>本地 ComfyUI 地址<input aria-label="本地 ComfyUI 地址" disabled={editingLocked} value={draft.comfyUrl} onChange={(event) => editDraft({ ...draft, comfyUrl: event.target.value })} /></label>}
      {([ ["width", "宽度"], ["height", "高度"], ["frames", "帧数"], ["fps", "帧率"], ["seed", "随机种子"] ] as const).map(([key, text]) => <label key={key}>{text}<input type="number" disabled={editingLocked} value={draft.settings[key]} onChange={(event) => editDraft({ ...draft, settings: { ...draft.settings, [key]: Number(event.target.value) } })} /></label>)}
    </div>
    {requiresSave && <p className="character-unsaved">{preparationOnly ? "有未保存修改或素材绑定已过期，请先保存再导出。" : "有未保存修改或素材绑定已过期。保存前不能导出、检查或提交生成。"}</p>}
    <p className="character-template">需要：{draft.template.requiredNodes.join("、") || "保存后读取环境依赖"}。官方模板仅为候选，必须在你的 ComfyUI 环境检查。</p>
    <div className="form-actions"><button className="secondary-action" disabled={!connected || !requiresSave || !draft.prompt.trim() || !!action} onClick={() => void save()}><Save size={16} />保存方案</button><button className="secondary-action" disabled={!usable || !!action} onClick={() => void exportPackage()}><Download size={16} />导出离线包</button>{!preparationOnly && <><button className="secondary-action" disabled={!usable || !!action} onClick={() => void inspect()}><RefreshCw size={16} />检查 ComfyUI</button><label><input aria-label="我已在目标 ComfyUI 运行过 DWPose，并确认 yolox_l.onnx 与 dw-ll_ucoco_384_bs5.torchscript.pt 已缓存" type="checkbox" disabled={!usable || !!action} checked={preprocessorConfirmed} onChange={(event) => setPreprocessorConfirmed(event.target.checked)} />我已在目标 ComfyUI 运行过 DWPose，并确认 yolox_l.onnx 与 dw-ll_ucoco_384_bs5.torchscript.pt 已缓存</label><button className="primary-action" disabled={!usable || !check?.ready || !preprocessorConfirmed || !!action} onClick={() => void generate()}>{action === "run" ? <LoaderCircle className="loading-spinner" size={16} /> : <Play size={16} />}提交本地生成</button></>}</div>
    {check && <p className={check.ready ? "character-check ready" : "character-check"}><Check size={16} />{check.message}{check.missingNodes.length ? ` 缺少节点：${check.missingNodes.join("、")}` : ""}{check.missingModels.length ? ` 缺少模型：${check.missingModels.join("、")}` : ""}</p>}
    {!preparationOnly && <div className="character-runs">{state.runs.map((run) => <article key={run.id}><strong>{label(run.status)}</strong><span>{run.error}</span>{["queued", "running", "unknown"].includes(run.status) && <button className="secondary-action" disabled={!!action} onClick={() => void refreshRun(run.id)}>手动刷新</button>}{run.status === "unknown" && <><label>ComfyUI prompt ID<input disabled={!!action} value={promptIds[run.id] ?? ""} onChange={(event) => setPromptIds({ ...promptIds, [run.id]: event.target.value })} /></label><button className="secondary-action" disabled={!promptIds[run.id]?.trim() || !!action} onClick={() => void resolveRun(run.id, { promptId: promptIds[run.id], confirmedNotQueued: false })}>继续跟踪</button><label><input aria-label="确认 ComfyUI 中未排队此任务" type="checkbox" disabled={!!action} checked={!!confirmedNotQueued[run.id]} onChange={(event) => setConfirmedNotQueued({ ...confirmedNotQueued, [run.id]: event.target.checked })} />确认 ComfyUI 中未排队此任务</label><button className="secondary-action" disabled={!confirmedNotQueued[run.id] || !!action} onClick={() => void resolveRun(run.id, { promptId: null, confirmedNotQueued: true })}>确认未排队并恢复提交</button></>}{run.outputs.map((output) => <div key={output.url}><video aria-label={`生成结果 ${output.filename}`} controls src={output.url} /><a href={`${output.url}?download=true`}>下载 {output.filename}</a></div>)}</article>)}</div>}
  </section>;
}
