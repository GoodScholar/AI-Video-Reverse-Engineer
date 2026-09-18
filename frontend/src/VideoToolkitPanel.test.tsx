import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { VideoToolkitPanel } from './VideoToolkitPanel';
import type { Project } from './models';

const api=vi.hoisted(()=>({getToolkit:vi.fn(),startTools:vi.fn(),controlTool:vi.fn(),getCuts:vi.fn(),saveCuts:vi.fn(),startPipeline:vi.fn(),controlPipeline:vi.fn()}));
vi.mock('./videoToolkitApi',()=>({...api,artifactUrl:(p:string,r:string,n:string)=>`/${p}/${r}/${n}`,toolBase:(p:string)=>`/${p}`}));
const project={id:'p1',referenceMedia:{id:'v1',type:'video'}} as Project;
const state=()=>({assets:[{id:'reference:v1',label:'舞蹈.mp4',url:'/video'}],runs:[],environment:Object.fromEntries(['scenes','subtitles','mask','interpolate','upscale'].map(k=>[k,{available:k==='scenes',message:k==='scenes'?'就绪':'请配置模型'}]))});
beforeEach(()=>{vi.clearAllMocks();api.getToolkit.mockResolvedValue(state());api.startTools.mockResolvedValue({runs:[]});});
describe('视频工具',()=>{
 it('missing model disables run but retains asset and analysis controls',async()=>{
  const user=userEvent.setup();render(<VideoToolkitPanel project={project}/>);
  await screen.findByText('舞蹈.mp4');
  await user.selectOptions(screen.getByLabelText('处理工具'),'mask');
  expect(screen.getByRole('button',{name:'开始处理'})).toBeDisabled();
  expect(screen.getByText('请配置模型')).toBeInTheDocument();
 });
 it('submits selected asset IDs and tool params explicitly',async()=>{
  const user=userEvent.setup();render(<VideoToolkitPanel project={project}/>);
  await user.click(await screen.findByLabelText('舞蹈.mp4'));
  await user.click(screen.getByRole('button',{name:'开始处理'}));
  await waitFor(()=>expect(api.startTools).toHaveBeenCalledWith('p1',expect.objectContaining({assetIds:['reference:v1'],kind:'scenes'})));
 });
 it('does not show old project results after a late response',async()=>{
  let resolve!:(s:unknown)=>void;
  api.getToolkit.mockImplementationOnce(()=>new Promise(r=>{resolve=r}));
  const {rerender}=render(<VideoToolkitPanel project={project}/>);
  rerender(<VideoToolkitPanel project={{...project,id:'p2'}}/>);
  await screen.findByText('舞蹈.mp4');
  resolve({...state(),assets:[{id:'old',label:'旧项目文件',url:'/old'}]});
  await waitFor(()=>expect(screen.queryByText('旧项目文件')).not.toBeInTheDocument());
 });
  it('exposes cancel for queued jobs and retry for failed jobs',async()=>{
  api.getToolkit.mockResolvedValue({...state(),runs:[{id:'r1',kind:'scenes',label:'clip',status:'queued',artifacts:[],params:{}},{id:'r2',kind:'mask',label:'clip',status:'failed',error:'模型缺失',artifacts:[],params:{}}]});
  render(<VideoToolkitPanel project={project}/>);
  expect(await screen.findByRole('button',{name:'取消'})).toBeEnabled();
  expect(screen.getByRole('button',{name:'重试'})).toBeEnabled();
  });
  it('submits a selected multi-step pipeline and shows its failed step for resume',async()=>{
    const user=userEvent.setup();
    api.getToolkit.mockResolvedValue({...state(),environment:Object.fromEntries(['scenes','subtitles','mask','interpolate','upscale'].map(k=>[k,{available:k==='scenes'||k==='interpolate',message:'test'}])),pipelines:[{id:'p-run',label:'clip',status:'failed',error:'模型缺失',steps:[{id:'s1',kind:'interpolate',status:'completed',artifacts:['output.mp4']},{id:'s2',kind:'upscale',status:'failed',error:'模型缺失',artifacts:[]}]}]});
    api.startPipeline.mockResolvedValue({pipeline:{id:'new'}});
    render(<VideoToolkitPanel project={project}/>);
    await user.click(await screen.findByLabelText('舞蹈.mp4'));
    await user.click(screen.getByRole('button',{name:'添加到流程'}));
    await user.selectOptions(screen.getByLabelText('流程步骤'),'RIFE 补帧');
    await user.click(screen.getByRole('button',{name:'添加到流程'}));
    await user.click(screen.getByRole('button',{name:'开始自动处理'}));
    await waitFor(()=>expect(api.startPipeline).toHaveBeenCalledWith('p1',expect.objectContaining({assetId:'reference:v1',steps:[expect.objectContaining({kind:'scenes'}),expect.objectContaining({kind:'interpolate'})]})));
    expect(screen.getByText('RIFE 补帧 · 已完成')).toBeInTheDocument();
    await user.click(screen.getByRole('button',{name:'从失败步骤继续'}));
    expect(api.controlPipeline).toHaveBeenCalledWith('p1','p-run','retry');
  });
});

it('synchronizes comparison playback and seeking',async()=>{
 const {SynchronizedComparison}=await import('./VideoToolkitPanel');
 const play=vi.spyOn(HTMLMediaElement.prototype,'play').mockResolvedValue();
 const pause=vi.spyOn(HTMLMediaElement.prototype,'pause').mockImplementation(()=>undefined);
 const {container}=render(<SynchronizedComparison source="/in.mp4" result="/out.mp4"/>);
 const [left,right]=container.querySelectorAll('video');
 left.currentTime=1.5;
 left.dispatchEvent(new Event('seeking'));
 expect(right.currentTime).toBe(1.5);
 left.dispatchEvent(new Event('play'));
 expect(play).toHaveBeenCalled();
 left.dispatchEvent(new Event('pause'));
 expect(pause).toHaveBeenCalled();
 play.mockRestore();pause.mockRestore();
});
