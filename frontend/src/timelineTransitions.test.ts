import { describe, it, expect } from "vitest";
import { changeBoundary, reconcileBoundaries } from "./timelineTransitions";
import type { TimelineTrack, TimelineTransition } from "./timelineApi";

const tracks: TimelineTrack[] = [{ id: "v", name: "画面", kind: "video", hidden: false, muted: false, clips: [
  { id: "a", assetId: "a", start: 0, duration: 2.3, inPoint: 0, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 },
  { id: "b", assetId: "b", start: 2, duration: 2, inPoint: 0, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 },
] }];
const boundary: TimelineTransition = { id: "t", leftClipId: "a", rightClipId: "b", template: "dissolve", enabled: true, duration: .3, overlap: .3, audioCrossfade: true, reason: "场景变化" };

describe("转场时序", () => {
  it("关闭效果仅收回左侧 handle，不移动后续时点", () => {
    const result = changeBoundary(tracks, [boundary], boundary, { enabled: false });
    expect(result.tracks[0].clips.map(clip => [clip.start, clip.duration])).toEqual([[0, 2], [2, 2]]);
    expect(result.transitions[0]).toMatchObject({ enabled: false, duration: .3 });
  });
  it("删除右片段清理边界与左侧 handle", () => {
    const result = reconcileBoundaries(tracks, [{ ...tracks[0], clips: [tracks[0].clips[0]] }], [boundary]);
    expect(result.transitions).toEqual([]);
    expect(result.tracks[0].clips[0].duration).toBe(2);
  });
  it("音量修改不丢失转场", () => {
    const result = reconcileBoundaries(tracks, [{ ...tracks[0], clips: tracks[0].clips.map(clip => ({ ...clip, volume: .5 })) }], [boundary]);
    expect(result.transitions).toEqual([boundary]);
    expect(result.tracks[0].clips[0].duration).toBe(2.3);
  });
  it("修改左片段时长时同步有效重叠，而不是丢弃转场留下非法重叠", () => {
    const result = reconcileBoundaries(tracks, [{ ...tracks[0], clips: tracks[0].clips.map(clip => clip.id === "a" ? { ...clip, duration: 2.4 } : clip) }], [boundary]);
    expect(result.transitions[0]).toMatchObject({ duration: .4, overlap: .4 });
    expect(result.tracks[0].clips.map(clip => [clip.start, clip.duration])).toEqual([[0, 2.4], [2, 2]]);
  });
  it("过长的重叠回退为无重叠硬切", () => {
    const result = reconcileBoundaries(tracks, [{ ...tracks[0], clips: tracks[0].clips.map(clip => clip.id === "a" ? { ...clip, duration: 5 } : clip) }], [boundary]);
    expect(result.transitions).toEqual([]);
    expect(result.tracks[0].clips[0].duration).toBe(2);
  });
});
