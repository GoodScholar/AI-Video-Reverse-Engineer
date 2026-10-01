import { readApiError } from "./referenceMediaApi";

export type GenerationRoute = { provider: string; model: string };
export type GenerationRoutingPolicy = { id: string; name: string; modality: "text" | "image" | "video"; routes: GenerationRoute[] };
export type GenerationRoutingPolicyRecord = GenerationRoutingPolicy & { revision: number };
export type GenerationRoutingPolicyRef = { id: string; revision: number };
export type GenerationRouteAttempt = GenerationRoute & {
  adapterId: string; adapterVersion: string; configurationVersion: string; capabilityVersion: string;
  status: "selected" | "failed" | "partial" | "cancelled" | "completed"; failureCode: string | null; fallbackDecision: string;
};
export type GenerationExecutionSemantics = {
  cancel: "supported" | "unsupported" | "requested" | "cancelled" | "unknown";
  cancelSupport?: "supported" | "unsupported"; cancelResult?: "requested" | "cancelled" | "unsupported" | "unknown" | null;
  stateConfidence: "known" | "unknown"; late: boolean; partial: boolean; billing: "known" | "unknown";
  actualRoute: GenerationRoute;
};

const endpoint = "/api/generation-routing-policies";

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) throw new Error(await readApiError(response, "无法保存生成路由策略。"));
  return response.json() as Promise<T>;
}

export function listGenerationRoutingPolicies(modality?: GenerationRoutingPolicy["modality"]) {
  const query = modality ? `?modality=${encodeURIComponent(modality)}` : "";
  return request<{ revision: number; policies: GenerationRoutingPolicyRecord[] }>(`${endpoint}${query}`);
}

export function saveGenerationRoutingPolicy(policy: GenerationRoutingPolicy, expectedRevision: number) {
  return request<{ policy: GenerationRoutingPolicyRecord }>(`${endpoint}/${encodeURIComponent(policy.id)}`, {
    method: "PUT", headers: { "Content-Type": "application/json", "x-aivre-intent": "semantic-analysis" },
    body: JSON.stringify({ expectedRevision, policy }),
  });
}
