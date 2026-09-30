import type { SourceSegmentCandidate } from "./sourceSegmentApi";


export function formatTime(value: number) {
  const milliseconds = Math.max(0, Math.round(value * 1000));
  const minutes = Math.floor(milliseconds / 60_000);
  const seconds = Math.floor(milliseconds / 1000) % 60;
  const fraction = milliseconds % 1000;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${String(fraction).padStart(3, "0")}`;
}

export function SegmentPreview({ assetName, candidate, kind = "片段预览" }: {
  assetName: string;
  candidate: Pick<SourceSegmentCandidate, "preview">;
  kind?: string;
}) {
  const start = candidate.preview.startSeconds;
  const end = candidate.preview.endSeconds;
  const label = `${assetName} ${kind} ${formatTime(start)} 至 ${formatTime(end)}`;
  return <video
    controls
    playsInline
    preload="metadata"
    src={candidate.preview.url}
    aria-label={label}
    onLoadedMetadata={(event) => { event.currentTarget.currentTime = start; }}
    onTimeUpdate={(event) => {
      if (event.currentTarget.currentTime < start) {
        event.currentTarget.currentTime = start;
      } else if (event.currentTarget.currentTime >= end) {
        event.currentTarget.pause();
        event.currentTarget.currentTime = end;
      }
    }}
  />;
}
