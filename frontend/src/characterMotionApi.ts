import { readApiError } from "./referenceMediaApi";

export type MotionSettings = { width: number; height: number; frames: number; fps: number; seed: number };
export type MotionRun = { id: string; promptId: string | null; status: "submitting" | "queued" | "running" | "completed" | "failed" | "unknown"; createdAt: string; error: string | null; outputs: Array<{ filename: string; url: string }>; revision: number };
export type CharacterMotionState = { revision: number; prompt: string; settings: MotionSettings; comfyUrl: string; character: { id: string; originalName: string; width: number; height: number; sizeBytes: number } | null; driver: { id: string; originalName: string; width: number; height: number } | null; sourceHash: string | null; stale: boolean; runs: MotionRun[]; template: { status: "candidate"; requiredNodes: string[]; requiredModels: string[]; requiredCustomNodes?: string[] } };
export type ComfyCheck = { connected: boolean; ready: boolean; version: string | null; missingNodes: string[]; missingModels: string[]; message: string };
const headers = { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" };
const offline = "无法连接本地服务，请确认应用服务正在运行后重试。";
const base = (id: string) => `/api/projects/${encodeURIComponent(id)}/character-motion`;
async function request<T>(url: string, init: RequestInit, fallback: string): Promise<T> {
  let response: Response;
  try { response = await fetch(url, init); } catch { throw new Error(offline); }
  if (!response.ok) throw new Error(await readApiError(response, fallback));
  return response.json() as Promise<T>;
}
export const getCharacterMotion = (id: string) => request<CharacterMotionState>(base(id), {}, "无法读取角色动画方案。");
export const saveCharacterMotion = (id: string, value: Pick<CharacterMotionState, "revision" | "prompt" | "settings" | "comfyUrl">) => request<CharacterMotionState>(base(id), { method: "PUT", headers, body: JSON.stringify(value) }, "无法保存角色动画方案。");
export const checkCharacterMotion = (id: string) => request<ComfyCheck>(`${base(id)}/check`, { method: "POST", headers, body: "{}" }, "无法检查本地 ComfyUI。");
export const startCharacterMotion = (id: string, revision: number, preprocessorConfirmed: boolean) => request<CharacterMotionState>(`${base(id)}/runs`, { method: "POST", headers, body: JSON.stringify({ revision, disclosureAccepted: true, preprocessorConfirmed }) }, "无法提交本地生成。");
export const refreshCharacterMotion = (id: string, runId: string) => request<CharacterMotionState>(`${base(id)}/runs/${encodeURIComponent(runId)}/refresh`, { method: "POST", headers, body: "{}" }, "无法刷新生成状态。");
export const resolveCharacterMotion = (id: string, runId: string, body: { promptId: string | null; confirmedNotQueued: boolean }) => request<CharacterMotionState>(`${base(id)}/runs/${encodeURIComponent(runId)}/resolve`, { method: "POST", headers, body: JSON.stringify(body) }, "无法恢复未知生成状态。");
export async function uploadCharacterMotionImage(id: string, file: File): Promise<CharacterMotionState> {
  const form = new FormData(); form.append("file", file);
  let response: Response;
  try { response = await fetch(`${base(id)}/character`, { method: "POST", body: form }); } catch { throw new Error(offline); }
  if (!response.ok) throw new Error(await readApiError(response, "无法上传角色图片。"));
  return response.json() as Promise<CharacterMotionState>;
}
export async function downloadCharacterMotionPackage(id: string): Promise<void> {
  let response: Response;
  try { response = await fetch(`${base(id)}/package`); } catch { throw new Error(offline); }
  if (!response.ok) throw new Error(await readApiError(response, "无法导出角色动画包。"));
  const url = URL.createObjectURL(await response.blob()); const link = document.createElement("a");
  link.href = url; link.download = `character-motion-${id}.zip`; document.body.append(link); link.click(); link.remove(); URL.revokeObjectURL(url);
}
