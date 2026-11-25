import type { CreativeCanvasAsset, CreativeCanvasNode, CreativeCanvasRun } from "./creativeCanvasApi";

/** Resolve a selected result by identity; a missing/deleted selection never falls back to another image. */
export function canvasNodeOutput(node: CreativeCanvasNode | undefined, runs: CreativeCanvasRun[], assets: CreativeCanvasAsset[]) {
  if (!node) return undefined;
  const available = (id: string | undefined) => assets.find(asset => asset.id === id && asset.available !== false);
  if (node.kind === "media") return available(node.assetId);
  if (node.kind !== "generate") return undefined;
  if (node.currentOutputRef) {
    const selected = node.currentOutputRef;
    const run = runs.find(item => item.id === selected.runId && item.nodeId === node.id);
    if (!run) return undefined;
    const items = run.outputItems ?? run.outputAssetIds.map((assetId, index) => ({ resultId: `${run.id}:legacy:${index}`, assetId, status: "installed" }));
    const item = items.find(item => item.resultId === selected.resultId && item.status === "installed");
    return available(item?.assetId);
  }
  if (node.outputAssetId) return available(node.outputAssetId);
  for (const run of [...runs].reverse()) {
    if (run.nodeId !== node.id || run.status !== "completed" || run.cancellationRequested) continue;
    const ids = run.outputItems ? run.outputItems.filter(item => item.status === "installed" && !item.cancellationRequested).map(item => item.assetId) : run.outputAssetIds;
    const asset = ids.map(available).find(item => item?.kind === "image");
    if (asset || run.outputItems !== undefined) return asset;
  }
  return undefined;
}
