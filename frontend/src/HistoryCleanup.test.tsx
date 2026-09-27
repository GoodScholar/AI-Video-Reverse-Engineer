import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { HistoryCleanup } from './HistoryCleanup';
afterEach(() => vi.unstubAllGlobals());
const props = { label: '清理记录', previewUrl: '/preview', deleteUrl: '/delete', revision: 2, disabled: false, onBusy: vi.fn(), onComplete: vi.fn() };
it('先展示影响和空间，明确确认后才发送删除', async () => {
  const fetcher = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify({ revision: 2, bytes: 2048, fileCount: 2, description: '删除输出及副本，保留源素材' }))).mockResolvedValueOnce(new Response(JSON.stringify({ revision: 2, runs: [] })));
  vi.stubGlobal('fetch', fetcher);
  render(<HistoryCleanup {...props} />);
  await userEvent.click(screen.getByRole('button', { name: '清理记录' }));
  expect(await screen.findByText('删除输出及副本，保留源素材')).toBeVisible();
  expect(screen.getByText(/2.0 KiB/)).toBeVisible();
  expect(fetcher).toHaveBeenCalledTimes(1);
  await userEvent.click(screen.getByRole('button', { name: '确认清理记录' }));
  expect(fetcher).toHaveBeenLastCalledWith('/delete', expect.objectContaining({ method: 'POST', body: '{"revision":2}' }));
});
it('预览后版本变化禁止执行；取消不会发删除请求', async () => {
  const fetcher = vi.fn().mockResolvedValue(new Response(JSON.stringify({ revision: 2, bytes: 0, fileCount: 0, description: '仅移除候选关联' })));
  vi.stubGlobal('fetch', fetcher);
  const view = render(<HistoryCleanup {...props} />);
  await userEvent.click(screen.getByRole('button', { name: '清理记录' }));
  await screen.findByText('仅移除候选关联');
  view.rerender(<HistoryCleanup {...props} revision={3} />);
  expect(screen.getByRole('button', { name: '确认清理记录' })).toBeDisabled();
  await userEvent.click(screen.getByRole('button', { name: '取消清理' }));
  expect(fetcher).toHaveBeenCalledTimes(1);
});
