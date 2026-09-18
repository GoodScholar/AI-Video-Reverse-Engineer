import { readApiError } from "./referenceMediaApi";

const CONNECTION_ERROR = "无法连接本地服务，请确认应用服务正在运行后重试。";
const UPSCALE_INTENT_HEADERS = {
  "Content-Type": "application/json",
  "X-AIVRE-Intent": "semantic-analysis",
};

export type UpscaleResolution = "1080p" | "2k";

export type UpscaleRun = {
  id: string;
  sourceId: string;
  scale?: 2 | 4 | null;
  outputResolution?: UpscaleResolution | null;
  status: "queued" | "running" | "completed" | "failed";
  stage: string;
  progress: number;
  error: string | null;
  output: null | {
    width: number;
    height: number;
    frameRate: number;
    durationSeconds: number;
  };
  createdAt: string;
};

export type VideoUpscaleState = {
  environment: {
    available: boolean;
    message: string;
  };
  runs: UpscaleRun[];
};

function endpoint(projectId: string) {
  return `/api/projects/${encodeURIComponent(projectId)}/upscale`;
}

async function request<T>(url: string, init: RequestInit | undefined, fallback: string): Promise<T> {
  let response: Response;
  try {
    response = init === undefined ? await fetch(url) : await fetch(url, init);
  } catch {
    throw new Error(CONNECTION_ERROR);
  }
  if (!response.ok) throw new Error(await readApiError(response, fallback));
  return response.json() as Promise<T>;
}

export function getVideoUpscaleState(projectId: string): Promise<VideoUpscaleState> {
  return request(endpoint(projectId), undefined, "无法读取本地视频超分状态，请重试。");
}

export function startVideoUpscale(projectId: string, sourceId: string, outputResolution: UpscaleResolution): Promise<UpscaleRun> {
  return request(
    endpoint(projectId),
    {
      method: "POST",
      headers: UPSCALE_INTENT_HEADERS,
      body: JSON.stringify({ sourceId, outputResolution }),
    },
    "无法启动本地视频超分，请重试。",
  );
}

export function upscaleVideoUrl(projectId: string, runId: string, download = false): string {
  return `${endpoint(projectId)}/${encodeURIComponent(runId)}/video${download ? "?download=true" : ""}`;
}
