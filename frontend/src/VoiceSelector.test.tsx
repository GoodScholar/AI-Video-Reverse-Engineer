import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, expect, it, vi } from 'vitest';
import { VoiceSelector } from './VoiceSelector';

afterEach(() => vi.unstubAllGlobals());
const response = (body: unknown) => new Response(JSON.stringify(body), {status:200,headers:{'Content-Type':'application/json'}});
const task = {id:'t1',contentRevision:2,variant:{revision:4,subtitles:{revision:1}},script:'倒入咖啡豆。'};

it('正式选声步骤自动展示描述，但不自动提交配音', async () => {
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response({available:true,message:'已就绪',voices:[
    {id:'serena',name:'Serena',description:'温暖女声',useCases:'咖啡',sampleUrl:'/serena.wav'}]})));
  render(<VoiceSelector projectId="p1" ownerId="t1" targets={[task]} disabled={false} autoLoad onGenerated={()=>{}} onSelectionPending={()=>{}} />);
  expect(await screen.findByText('温暖女声')).toBeVisible();
  expect(screen.getByRole('button',{name:'生成当前配音'})).toBeDisabled();
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(1);
  expect(vi.mocked(fetch).mock.calls[0][0]).toBe('/api/voices');
});

it('先展示音色描述，选择并确认当前脚本后才提交配音', async () => {
  vi.stubGlobal('fetch',vi.fn().mockResolvedValueOnce(response({available:true,message:'已就绪',voices:[
    {id:'serena',name:'Serena',description:'中文 / 温暖女声',useCases:'咖啡美食',sampleUrl:'/api/voices/serena/sample'}]}))
    .mockResolvedValueOnce(response({tasks:[]})));
  render(<VoiceSelector projectId="p1" ownerId="t1" targets={[task]} disabled={false} onGenerated={()=>{}} onSelectionPending={()=>{}} />);
  await userEvent.click(screen.getByRole('button',{name:'查看音色与本地配音'}));
  expect(await screen.findByText('中文 / 温暖女声')).toBeVisible();
  expect(screen.getByText('适合：咖啡美食')).toBeVisible();
  expect(screen.getByRole('button',{name:'生成当前配音'})).toBeDisabled();
  await userEvent.click(screen.getByRole('radio',{name:'选择 Serena'}));
  expect(screen.getByRole('button',{name:'生成当前配音'})).toBeDisabled();
  await userEvent.click(screen.getByRole('checkbox',{name:'我已确认下列已保存脚本，按所选音色生成配音'}));
  await userEvent.click(screen.getByRole('button',{name:'生成当前配音'}));
  expect(vi.mocked(fetch).mock.calls[1][1]?.headers).toMatchObject({'x-aivre-intent':'semantic-analysis'});
  expect(JSON.parse(vi.mocked(fetch).mock.calls[1][1]?.body as string)).toEqual({voiceId:'serena',confirmScript:true,
    targets:[{taskId:'t1',contentRevision:2,variantRevision:4,subtitleRevision:1}]});
});

it('脚本版本改变后旧确认失效；未就绪时不能发起生成', async () => {
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response({available:false,message:'环境缺失，不自动下载',voices:[
    {id:'serena',name:'Serena',description:'中文女声',useCases:'生活分享',sampleUrl:null}]})));
  const props={projectId:'p1',ownerId:'t1',targets:[task],disabled:false,onGenerated:()=>{},onSelectionPending:()=>{}};
  const view=render(<VoiceSelector {...props}/>);
  await userEvent.click(screen.getByRole('button',{name:'查看音色与本地配音'}));
  await screen.findByText('环境缺失，不自动下载');
  await userEvent.click(screen.getByRole('radio',{name:'选择 Serena'}));
  await userEvent.click(screen.getByRole('checkbox',{name:'我已确认下列已保存脚本，按所选音色生成配音'}));
  expect(screen.getByRole('button',{name:'生成当前配音'})).toBeDisabled();
  view.rerender(<VoiceSelector {...props} targets={[{...task,contentRevision:3,script:'新版脚本'}]}/>);
  expect(screen.getByRole('checkbox')).not.toBeChecked();
});

it('换一组候选保留脚本并要求重新选择音色', async () => {
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response({available:true,message:'已就绪',voices:[
    {id:'serena',name:'Serena',description:'中文温暖女声',useCases:'生活分享',group:'chinese',sampleUrl:'/serena.wav'},
    {id:'ryan',name:'Ryan',description:'原生英语男声',useCases:'动感口播',group:'other',sampleUrl:'/ryan.wav'}]})));
  render(<VoiceSelector projectId="p1" ownerId="t1" targets={[task]} disabled={false} onGenerated={()=>{}} onSelectionPending={()=>{}} />);
  await userEvent.click(screen.getByRole('button',{name:'查看音色与本地配音'}));
  await screen.findByText('中文温暖女声');
  await userEvent.click(screen.getByRole('radio',{name:'选择 Serena'}));
  await userEvent.click(screen.getByRole('button',{name:'都不满意，换一组'}));
  expect(screen.queryByRole('radio',{name:'选择 Serena'})).not.toBeInTheDocument();
  expect(screen.getByRole('radio',{name:'选择 Ryan'})).not.toBeChecked();
  expect(screen.getByRole('button',{name:'生成当前配音'})).toBeDisabled();
  await userEvent.click(screen.getByText('本次配音脚本（1 条）'));
  expect(screen.getByText('1. 倒入咖啡豆。')).toBeVisible();
});

it('配音提交后切页卸载仍向项目交付任务回包', async () => {
  let resolve!: (value: Response) => void;
  const pending = new Promise<Response>(done => { resolve = done; });
  vi.stubGlobal('fetch', vi.fn().mockResolvedValueOnce(response({ available: true, message: '就绪', voices: [
    { id: 'serena', name: 'Serena', description: '女声', useCases: '生活', sampleUrl: null }] })).mockReturnValueOnce(pending));
  const onGenerated = vi.fn(), onSelectionPending = vi.fn();
  const view = render(<VoiceSelector projectId="p1" ownerId="t1" targets={[task]} disabled={false} autoLoad onGenerated={onGenerated} onSelectionPending={onSelectionPending} />);
  await userEvent.click(await screen.findByRole('radio', { name: '选择 Serena' }));
  await userEvent.click(screen.getByRole('checkbox'));
  await userEvent.click(screen.getByRole('button', { name: '生成当前配音' }));
  view.unmount();
  await act(async () => resolve(response({ tasks: [{ ...task, voiceover: { status: 'queued' } }] })));
  expect(onGenerated).toHaveBeenCalledWith([expect.objectContaining({ id: 't1', voiceover: { status: 'queued' } })]);
  expect(onSelectionPending).toHaveBeenLastCalledWith(false);
});
