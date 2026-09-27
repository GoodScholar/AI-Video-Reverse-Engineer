import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { AssetManager } from './AssetManager';
const asset = { id: 'a1', name: '原素材', kind: 'video' as const, role: 'reference' as const, url: '/file' };
const props = { projectId: 'p1', asset, revision: 2, blocked: false, onChanged: vi.fn(), onBusy: vi.fn() };
beforeEach(() => { localStorage.clear(); sessionStorage.clear(); vi.clearAllMocks(); vi.stubGlobal('fetch', vi.fn()); });
const response = (value: unknown) => new Response(JSON.stringify(value));
it('保存名称和备注，向父组件返回新工作台', async () => {
  vi.mocked(fetch).mockResolvedValueOnce(response({ references: [] })).mockResolvedValueOnce(response({ revision: 3, assets: [{ ...asset, name: '新素材', notes: '保留动作' }] }));
  render(<AssetManager {...props} />);
  await screen.findByText('未发现已保存的引用。');
  await userEvent.clear(screen.getByLabelText('素材名称'));
  await userEvent.type(screen.getByLabelText('素材名称'), '新素材');
  await userEvent.type(screen.getByLabelText('素材备注'), '保留动作');
  await userEvent.click(screen.getByRole('button', { name: '保存素材信息' }));
  await waitFor(() => expect(props.onChanged).toHaveBeenCalledWith(expect.objectContaining({ revision: 3 })));
  expect(JSON.parse(String(vi.mocked(fetch).mock.calls[1][1]?.body))).toEqual({ revision: 2, name: '新素材', notes: '保留动作' });
});
it('展示引用且禁止删除', async () => {
  vi.mocked(fetch).mockResolvedValue(response({ references: [{ kind: 'candidate', label: '镜头一 · 候选 1' }] }));
  render(<AssetManager {...props} />);
  await screen.findByText('镜头一 · 候选 1');
  expect(screen.getByRole('button', { name: '删除素材' })).toBeDisabled();
});
it('删除前再次阻止未保存的时间线草稿', async () => {
  vi.mocked(fetch).mockResolvedValue(response({ references: [] }));
  render(<AssetManager {...props} />);
  await screen.findByText('未发现已保存的引用。');
  await userEvent.click(screen.getByRole('button', { name: '删除素材' }));
  localStorage.setItem('aivre:timeline-draft:p1', '{}');
  await userEvent.click(screen.getByRole('button', { name: '确认删除原素材' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('草稿');
  expect(fetch).toHaveBeenCalledTimes(1);
});
it('引用读取失败时不开放删除且可重试', async () => {
  vi.mocked(fetch).mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce(response({ references: [] }));
  render(<AssetManager {...props} />);
  await screen.findByRole('alert');
  expect(screen.getByRole('button', { name: '删除素材' })).toBeDisabled();
  await userEvent.click(screen.getByRole('button', { name: '刷新引用' }));
  await screen.findByText('未发现已保存的引用。');
  expect(screen.getByRole('button', { name: '删除素材' })).toBeEnabled();
});
it('保存期间切换页面仍将成功结果交给父工作台', async () => {
  let resolveSave!: (value: Response) => void;
  vi.mocked(fetch).mockResolvedValueOnce(response({ references: [] })).mockImplementationOnce(() => new Promise((resolve) => { resolveSave = resolve; }));
  const { unmount } = render(<AssetManager {...props} />);
  await screen.findByText('未发现已保存的引用。');
  await userEvent.type(screen.getByLabelText('素材备注'), '跨页面保存');
  await userEvent.click(screen.getByRole('button', { name: '保存素材信息' }));
  unmount();
  resolveSave(response({ revision: 3, assets: [{ ...asset, notes: '跨页面保存' }] }));
  await waitFor(() => expect(props.onChanged).toHaveBeenCalledWith(expect.objectContaining({ revision: 3 })));
  expect(props.onBusy).toHaveBeenLastCalledWith(false);
});
