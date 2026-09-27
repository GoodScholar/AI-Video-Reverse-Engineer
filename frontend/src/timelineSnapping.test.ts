import { expect, it } from 'vitest';
import { snappedStart } from './timelineSnapping';
import type { TimelineTrack } from './timelineApi';
const clip = { id: 'one', assetId: 'a', start: 1, duration: 2, inPoint: 0, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0 };
const tracks: TimelineTrack[] = [{ id: 'v', name: '视频', kind: 'video', muted: false, hidden: false, clips: [clip, { ...clip, id: 'two', start: 5 }] }];
it('起点与尾部均可吸附到片段边缘，保持时长', () => {
  expect(snappedStart(3.04, clip, 'v', tracks, 20, 100, true)).toBe(3);
  expect(snappedStart(7.04, clip, 'v', tracks, 20, 100, true)).toBe(7);
});
it('播放头吸附使用屏幕像素阈值，可临时关闭', () => {
  expect(snappedStart(9.94, clip, 'v', tracks, 10, 100, true)).toBe(10);
  expect(snappedStart(9.94, clip, 'v', tracks, 10, 180, true)).toBe(9.94);
  expect(snappedStart(9.94, clip, 'v', tracks, 10, 100, false)).toBe(9.94);
});
it('不能吸附到越界或重叠位置，也不吸附自身', () => {
  expect(snappedStart(4.9, clip, 'v', tracks, 20, 100, true)).toBe(clip.start);
  expect(snappedStart(1.04, clip, 'v', tracks, 20, 100, true)).toBe(1.04);
  expect(snappedStart(299, clip, 'v', tracks, 300, 100, true)).toBe(298);
});
