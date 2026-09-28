import type { PreproductionShot } from "./preproductionApi";

export type ShotPreparationStatus = "待准备" | "处理中" | "已准备" | "需处理";

export function getShotPreparationStatus(shot: Pick<PreproductionShot, "nodes">): ShotPreparationStatus {
  if (shot.nodes.some((node) => node.status === "failed" || node.status === "stale")) return "需处理";
  if (shot.nodes.some((node) => node.status === "queued" || node.status === "running")) return "处理中";
  if (shot.nodes.length > 0 && shot.nodes.every((node) => node.status === "completed")) return "已准备";
  return "待准备";
}
