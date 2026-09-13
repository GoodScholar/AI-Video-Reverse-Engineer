import type { Project } from "./models";

type ErrorBody = {
  detail?: string | { code?: unknown; message?: unknown };
};

export async function readApiError(response: Response, fallback: string): Promise<string> {
  const body = await response.json().catch(() => null) as ErrorBody | null;
  if (typeof body?.detail === "string") return body.detail;
  if (body?.detail && typeof body.detail === "object" && typeof body.detail.message === "string") {
    return body.detail.message;
  }
  return fallback;
}

export const referenceMediaContentUrl = (projectId: string, mediaId?: string): string => {
  const contentUrl = `/api/projects/${encodeURIComponent(projectId)}/reference-media/content`;
  return mediaId ? `${contentUrl}?version=${encodeURIComponent(mediaId)}` : contentUrl;
};

export async function uploadReferenceMedia(projectId: string, file: File): Promise<Project> {
  const form = new FormData();
  form.append("file", file);
  let response: Response;
  try {
    response = await fetch(`/api/projects/${encodeURIComponent(projectId)}/reference-media`, {
      method: "PUT",
      body: form,
    });
  } catch {
    throw new Error("无法连接本地服务，请确认应用服务正在运行后重试。");
  }
  if (!response.ok) {
    throw new Error(await readApiError(response, "无法上传并校验参考素材，请检查文件后重试。"));
  }
  return response.json() as Promise<Project>;
}
