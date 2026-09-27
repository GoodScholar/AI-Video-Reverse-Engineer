import { render, screen, waitFor } from '@testing-library/react';
import { expect, it, vi, afterEach } from 'vitest';
import { ClipWaveform, waveformBars } from './ClipWaveform';
afterEach(() => vi.unstubAllGlobals());
const clip = { id: 'c', assetId: 'a', start: 0, inPoint: 1, duration: 1, speed: 2, volume: 1, fadeIn: 0, fadeOut: 0 };
it('根据入点、速度和显示密度选取源波形，超出源区间不重复末尾', () => {
  const source = { status: 'ready' as const, peaksPerSecond: 2, duration: 3, peaks: [0, 0, .2, .4, .6, .8] };
  expect(waveformBars(source, clip, 4)).toEqual([.2, .4, .6, .8]);
  expect(waveformBars(source, { ...clip, inPoint: 2, speed: 1 }, 2)).toEqual([.6, .8]);
  expect(waveformBars(source, { ...clip, inPoint: 3 }, 2)).toEqual([0, 0]);
});
it('同一素材的并发片段共用请求，显示真实 SVG 波形', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ status: 'ready', peaksPerSecond: 2, duration: 3, peaks: [0, 0, .2, .4, .6, .8] })));
  vi.stubGlobal('fetch', fetcher);
  render(<><ClipWaveform projectId="p" clip={clip} pixelsPerSecond={100} refresh={0} /><ClipWaveform projectId="p" clip={{ ...clip, id: 'd' }} pixelsPerSecond={100} refresh={0} /></>);
  await waitFor(() => expect(document.querySelectorAll('svg[data-waveform]')).toHaveLength(2));
  expect(fetcher).toHaveBeenCalledTimes(1);
});
it('无音轨与失败独立提示，刷新后允许重试', async () => {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ status: 'no_audio', peaks: [], peaksPerSecond: 100, duration: 0 }))).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(new Response(JSON.stringify({ status: 'no_audio', peaks: [], peaksPerSecond: 100, duration: 0 }))));
  const view = render(<ClipWaveform projectId="status" clip={clip} pixelsPerSecond={100} refresh={0} />);
  await screen.findByText('无音轨');
  view.rerender(<ClipWaveform projectId="status" clip={clip} pixelsPerSecond={100} refresh={1} />);
  await screen.findByText('波形读取失败');
  view.rerender(<ClipWaveform projectId="status" clip={clip} pixelsPerSecond={100} refresh={2} />);
  await screen.findByText('无音轨');
});
