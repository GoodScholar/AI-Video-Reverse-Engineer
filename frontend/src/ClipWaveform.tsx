import { useEffect, useMemo, useState } from "react";
import type { TimelineClip } from "./timelineApi";
import { readApiError } from "./referenceMediaApi";

type Waveform = { status: "ready" | "no_audio"; peaks: number[]; duration: number; peaksPerSecond: number };
const lanes: Promise<unknown>[] = [Promise.resolve(), Promise.resolve()];
let nextLane = 0;
const pending = new Map<string, Promise<Waveform>>();
function loadWaveform(projectId: string, assetId: string, refresh: number): Promise<Waveform> {
  const url = `/api/projects/${encodeURIComponent(projectId)}/timeline/assets/${encodeURIComponent(assetId)}/waveform`;
  const key = `${url}:${refresh}`;
  const existing = pending.get(key);
  if (existing) return existing;
  const lane = nextLane++ % lanes.length;
  const request = lanes[lane].then(() => fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-AIVRE-Intent": "semantic-analysis" }, body: "{}" }))
    .then(async (response) => {
      if (!response.ok) throw new Error(await readApiError(response, "无法读取波形。"));
      return response.json() as Promise<Waveform>;
    }).finally(() => pending.delete(key));
  lanes[lane] = request.catch(() => undefined);
  pending.set(key, request);
  return request;
}

export function waveformBars(source: Waveform, clip: Pick<TimelineClip, "inPoint" | "duration" | "speed">, count: number): number[] {
  return Array.from({ length: count }, (_, index) => {
    const start = (clip.inPoint + index / count * clip.duration * clip.speed) * source.peaksPerSecond;
    const end = (clip.inPoint + (index + 1) / count * clip.duration * clip.speed) * source.peaksPerSecond;
    let peak = 0;
    for (let sample = Math.max(0, Math.floor(start)); sample < Math.min(source.peaks.length, Math.ceil(end)); sample++) peak = Math.max(peak, source.peaks[sample]);
    return peak;
  });
}

export function ClipWaveform({ projectId, clip, pixelsPerSecond, refresh, onError }: { projectId: string; clip: TimelineClip; pixelsPerSecond: number; refresh: number; onError?: (assetId: string, message: string) => void }) {
  const [source, setSource] = useState<Waveform | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setSource(null); setError(""); onError?.(clip.assetId, "");
    loadWaveform(projectId, clip.assetId, refresh).then((value) => { if (active) setSource(value); })
      .catch((reason) => { if (active) { const message = reason instanceof Error ? reason.message : "无法读取波形。"; setError(message); onError?.(clip.assetId, message); } });
    return () => { active = false; };
  }, [projectId, clip.assetId, refresh, onError]);
  const width = clip.duration * pixelsPerSecond;
  const count = Math.max(1, Math.min(1000, Math.ceil(width / 3)));
  const bars = useMemo(() => source?.status === "ready" ? waveformBars(source, clip, count) : [], [source, clip.inPoint, clip.duration, clip.speed, count]);
  if (error) return <span className="timeline-waveform-state" title={`${error} 可使用“重新加载波形”重试。`}>波形读取失败</span>;
  if (!source) return <span className="timeline-waveform-state">波形读取中…</span>;
  if (source.status === "no_audio") return <span className="timeline-waveform-state">无音轨</span>;
  return <svg data-waveform="true" className="timeline-waveform" aria-hidden="true" width={width} height="20" viewBox={`0 0 ${count} 20`} preserveAspectRatio="none">
    <path d={bars.map((peak, index) => peak === 0 ? "" : `M${index + .5},${10 - peak * 9}v${Math.max(.35, peak * 18)}`).join(" ")} fill="none" stroke="currentColor" strokeWidth="0.65" />
  </svg>;
}
