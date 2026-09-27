import type { TimelineClip, TimelineTrack } from "./timelineApi";

/** Snap either clip edge within eight screen pixels, preserving legal placement. */
export function snappedStart(proposed: number, moving: TimelineClip, trackId: string, tracks: TimelineTrack[], playhead: number, pixelsPerSecond: number, enabled: boolean): number {
  const track = tracks.find((item) => item.id === trackId);
  const bounded = Math.max(0, Math.min(300 - moving.duration, proposed));
  const valid = (start: number) => start >= 0 && start + moving.duration <= 300
    && (track?.kind !== "video" || !track.clips.some((clip) => clip.id !== moving.id
      && start < clip.start + clip.duration - 1e-8 && start + moving.duration > clip.start + 1e-8));
  if (enabled) {
    const anchors = [0, playhead, ...tracks.flatMap((item) => item.clips
      .filter((clip) => item.id !== trackId || clip.id !== moving.id)
      .flatMap((clip) => [clip.start, clip.start + clip.duration]))];
    const candidates = anchors.flatMap((edge) => [edge, edge - moving.duration])
      .filter((start) => Math.abs(start - bounded) * pixelsPerSecond <= 8 && valid(start))
      .sort((left, right) => Math.abs(left - bounded) - Math.abs(right - bounded));
    if (candidates.length) return candidates[0];
  }
  return valid(bounded) ? bounded : moving.start;
}
