import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it, vi } from 'vitest';
import { ProjectBackup } from './ProjectBackup';

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); vi.stubGlobal('fetch', vi.fn()); });
it('缓存为空但当前窗口仍有未保存编辑时阻止备份', async () => {
  render(<ProjectBackup projectId="p1" hasUnsavedDraft />);
  await userEvent.click(screen.getByRole('button', { name: '备份项目 ZIP' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('未保存草稿');
  expect(fetch).not.toHaveBeenCalled();
});
it('恢复 ZIP 后返回新项目，原始文件作为 ZIP 请求体提交', async () => {
  const project = { id: 'restored', name: '恢复项目' };
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify(project), { status: 201 }));
  const restored = vi.fn();
  render(<ProjectBackup onRestored={restored} />);
  const file = new File(['zip'], 'backup.zip', { type: 'application/zip' });
  await userEvent.upload(screen.getByLabelText('选择项目备份 ZIP'), file);
  expect(await screen.findByRole('status')).toHaveTextContent('恢复成功');
  expect(restored).toHaveBeenCalledWith(project);
  expect(fetch).toHaveBeenCalledWith('/api/project-backups/restore', expect.objectContaining({ body: file, method: 'POST' }));
});
it('存在未保存草稿时不导出遗漏编辑内容的备份', async () => {
  localStorage.setItem('aivre:timeline-draft:p1', '{}');
  render(<ProjectBackup projectId="p1" />);
  await userEvent.click(screen.getByRole('button', { name: '备份项目 ZIP' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('草稿');
  expect(fetch).not.toHaveBeenCalled();
});
it('恢复失败展示错误且允许重试', async () => {
  vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ detail: '备份文件校验失败' }), { status: 422 }));
  render(<ProjectBackup onRestored={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('选择项目备份 ZIP'), { target: { files: [new File(['bad'], 'bad.zip')] } });
  expect(await screen.findByRole('alert')).toHaveTextContent('校验失败');
  expect(screen.getByRole('button', { name: '恢复项目 ZIP' })).toBeEnabled();
});
