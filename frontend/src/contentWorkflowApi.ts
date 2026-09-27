import { readApiError } from "./referenceMediaApi";

export type ContentWorkflowStage = "draft" | "brief_ready" | "scripts_confirmed" | "voice_ready" | "preview_ready" |
  "review_pending" | "approved" | "rejected" | "delivered";
export type ContentWorkflowIssue = { code: string; message: string; taskId?: string };
export type ContentWorkflowVariant = { taskId: string; stage: ContentWorkflowStage;
  reviewStatus: "pending" | "approved" | "rejected" | "stale"; currentPreviewRunId: string | null;
  currentExportRunId: string | null; issues: ContentWorkflowIssue[] };
export type ContentWorkflowProjection = {
  stage: ContentWorkflowStage;
  currentStep: 0 | 1 | 2 | 3;
  completedSteps: [boolean, boolean, boolean, boolean];
  activeGenerationId: string | null;
  activeBatchId: string | null;
  counts: { total: number; pending: number; approved: number; rejected: number; delivered: number; failed: number };
  variants: ContentWorkflowVariant[];
  issues: ContentWorkflowIssue[];
};

export async function getContentWorkflow(projectId: string): Promise<ContentWorkflowProjection> {
  let response: Response;
  try { response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/content-workflow`); }
  catch { throw new Error("无法连接本地服务，请检查应用服务。"); }
  if (!response.ok) throw new Error(await readApiError(response, "无法读取内容制作进度。"));
  return response.json() as Promise<ContentWorkflowProjection>;
}
