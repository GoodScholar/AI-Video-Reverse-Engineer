import type { DepthDevicePreference, Project } from "./models";
import { readApiError } from "./referenceMediaApi";

const CONNECTION_ERROR = "无法连接本地服务，请确认应用服务正在运行后重试。";

async function requestProject(url: string, init: RequestInit, fallback: string): Promise<Project> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch {
    throw new Error(CONNECTION_ERROR);
  }
  if (!response.ok) {
    throw new Error(await readApiError(response, fallback));
  }
  return response.json() as Promise<Project>;
}

export const referenceVideoContentUrl = (projectId: string): string =>
  `/api/projects/${encodeURIComponent(projectId)}/reference-video/content`;

export const depthPreviewUrl = (projectId: string, captureId: string): string =>
  `/api/projects/${encodeURIComponent(projectId)}/depth-captures/${encodeURIComponent(captureId)}/preview`;

export function startDepthCapture(
  projectId: string,
  devicePreference: DepthDevicePreference = "auto",
): Promise<Project> {
  return requestProject(
    `/api/projects/${encodeURIComponent(projectId)}/depth-captures`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ devicePreference }),
    },
    "无法启动本地深度捕捉，请重试。",
  );
}

export function confirmDepthReview(projectId: string, captureId: string): Promise<Project> {
  return requestProject(
    `/api/projects/${encodeURIComponent(projectId)}/depth-captures/${encodeURIComponent(captureId)}/confirm-review`,
    { method: "POST" },
    "无法确认深度质量复核，请重试。",
  );
}
