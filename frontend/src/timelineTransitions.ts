import type { TimelineTrack, TimelineTransition, TimelineTransitionTemplate } from "./timelineApi";

export const transitionLabels: Record<TimelineTransitionTemplate, string> = {
  cut: "硬切", dissolve: "叠化", push: "推拉", wipe: "擦除", "flash-white": "闪白", pixelize: "像素化", "speed-change": "受控速度变化",
};
export const effectTransitions = new Set<TimelineTransitionTemplate>(["dissolve", "push", "wipe", "flash-white", "pixelize"]);
export function changeBoundary(tracks: TimelineTrack[], transitions: TimelineTransition[], boundary: TimelineTransition, change: Partial<TimelineTransition>) {
  let next = { ...boundary, ...change };
  if (change.template !== undefined) {
    const duration = effectTransitions.has(change.template) ? (effectTransitions.has(boundary.template) ? boundary.duration : .3) : 0;
    next = { ...next, enabled: true, duration, overlap: duration, audioCrossfade: duration > 0 };
  }
  if (change.duration !== undefined && effectTransitions.has(next.template)) next.overlap = change.duration;
  const overlap = next.enabled && effectTransitions.has(next.template) ? next.overlap : 0;
  return {
    tracks: tracks.map(track => {
      const left = track.clips.find(clip => clip.id === next.leftClipId);
      const right = track.clips.find(clip => clip.id === next.rightClipId);
      if (!left || !right) return track;
      return { ...track, clips: track.clips.map(clip => clip === left ? { ...clip, duration: Number((right.start - left.start + overlap).toFixed(6)) } : clip) };
    }),
    transitions: transitions.some(item => item.id === boundary.id) ? transitions.map(item => item.id === boundary.id ? next : item) : [...transitions, next],
  };
}

// Geometry edits detach invalid boundaries and remove only the old transition's tail handle.
// Other tracks and unrelated valid boundaries keep their exact timing.
export function reconcileBoundaries(before: TimelineTrack[], tracks: TimelineTrack[], transitions: TimelineTransition[]) {
  const oldClips = new Map(before.flatMap(track => track.clips.map(clip => [clip.id, clip] as const)));
  const pairs = new Map(tracks.filter(track => track.kind === "video" && !track.hidden).flatMap(track => {
    const ordered = [...track.clips].sort((a, b) => a.start - b.start);
    return ordered.slice(0, -1).map((clip, i) => [`${clip.id}\n${ordered[i + 1].id}`, [clip, ordered[i + 1]]] as const);
  }));
  const removed: TimelineTransition[] = [];
  const kept = transitions.flatMap(item => {
    const pair = pairs.get(`${item.leftClipId}\n${item.rightClipId}`);
    const overlap = item.enabled && effectTransitions.has(item.template) ? item.overlap : 0;
    const valid = pair && Math.abs(pair[0].start + pair[0].duration - pair[1].start - overlap) < .000001;
    if (valid) return [item];
    const changedOverlap = pair && Number((pair[0].start + pair[0].duration - pair[1].start).toFixed(6));
    if (pair && overlap > 0 && changedOverlap && changedOverlap >= .05 && changedOverlap <= 2
      && changedOverlap < Math.min(pair[0].duration, pair[1].duration)) {
      return [{ ...item, duration: changedOverlap, overlap: changedOverlap }];
    }
    removed.push(item);
    return [];
  });
  const tails = new Map(removed.filter(item => item.enabled && effectTransitions.has(item.template)).map(item => [item.leftClipId, item.overlap]));
  return {
    transitions: kept,
    tracks: tracks.map(track => ({ ...track, clips: track.clips.map(clip => {
      const tail = tails.get(clip.id);
      if (!tail) return clip;
      const duration = clip.duration === oldClips.get(clip.id)?.duration ? clip.duration - tail : clip.duration;
      const right = [...track.clips].sort((a, b) => a.start - b.start).find(other => other.start > clip.start);
      return { ...clip, duration: Number(Math.min(duration, right ? right.start - clip.start : duration).toFixed(6)) };
    }) })),
  };
}
