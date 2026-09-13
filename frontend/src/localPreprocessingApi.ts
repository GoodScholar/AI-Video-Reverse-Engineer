import type { Project } from "./models";
import { readApiError } from "./referenceMediaApi";

async function requestProject(url: string, init?: RequestInit): Promise<Project> {
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch {
    throw new Error("无法连接本地服务，请确认应用服务正在运行后重试。");
  }
  if (!response.ok) {
    throw new Error(await readApiError(response, "无法读取本地预处理状态，请重试。"));
  }
  return response.json() as Promise<Project>;
}

export function startLocalPreprocessing(projectId: string): Promise<Project> {
  return requestProject(
    `/api/projects/${encodeURIComponent(projectId)}/local-preprocessing`,
    { method: "POST" },
  );
}

export function getProject(projectId: string): Promise<Project> {
  return requestProject(`/api/projects/${encodeURIComponent(projectId)}`);
}
