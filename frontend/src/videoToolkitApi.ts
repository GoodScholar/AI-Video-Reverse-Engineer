import { readApiError } from './referenceMediaApi';
export type ToolKind = 'scenes'|'subtitles'|'mask'|'interpolate'|'upscale';
export type ToolAsset = {id:string;label:string;url:string};
export type ToolRun = {id:string;assetId:string;label:string;kind:ToolKind;status:'queued'|'running'|'completed'|'failed'|'cancelled';stage:string;error:string|null;artifacts:string[];revision:number;params:Record<string,unknown>};
export type PipelineStep = {id:string;kind:ToolKind;status:'queued'|'running'|'completed'|'failed'|'cancelled'|'blocked';stage?:string;error?:string|null;artifacts:string[];params?:Record<string,unknown>};
export type ToolPipeline = {id:string;assetId:string;label:string;status:'queued'|'running'|'completed'|'failed'|'cancelled';error:string|null;steps:PipelineStep[]};
export type ToolkitState = {assets:ToolAsset[];runs:ToolRun[];pipelines?:ToolPipeline[];environment:Record<ToolKind,{available:boolean;message:string}>};
export type Cuts = {duration:number;cuts:number[];revision?:number};
export const toolBase=(pid:string)=>`/api/projects/${encodeURIComponent(pid)}/toolkit`;
export const artifactUrl=(pid:string,rid:string,name:string)=>`${toolBase(pid)}/runs/${encodeURIComponent(rid)}/artifacts/${encodeURIComponent(name)}`;
async function request<T>(url:string,method='GET',body?:unknown):Promise<T>{
 const response=await fetch(url,{method,...(body===undefined?{}:{headers:{'Content-Type':'application/json','X-AIVRE-Intent':'semantic-analysis'},body:JSON.stringify(body)})});
 if(!response.ok)throw new Error(await readApiError(response,'视频工具请求失败，请刷新重试。'));
 return response.json() as Promise<T>;
}
export const getToolkit=(pid:string)=>request<ToolkitState>(toolBase(pid));
export const startTools=(pid:string,body:{assetIds:string[];kind:ToolKind;params:Record<string,unknown>})=>request<{runs:ToolRun[]}>(`${toolBase(pid)}/runs`,'POST',body);
export const controlTool=(pid:string,rid:string,action:'cancel'|'retry')=>request<ToolRun>(`${toolBase(pid)}/runs/${encodeURIComponent(rid)}/${action}`,'POST',{});
export const startPipeline=(pid:string,body:{assetId:string;steps:{kind:ToolKind;params?:Record<string,unknown>}[]})=>request<{pipeline:ToolPipeline}>(`${toolBase(pid)}/pipelines`,'POST',body);
export const controlPipeline=(pid:string,pipelineId:string,action:'cancel'|'retry')=>request<ToolPipeline>(`${toolBase(pid)}/pipelines/${encodeURIComponent(pipelineId)}/${action}`,'POST',{});
export const getCuts=(pid:string,rid:string)=>request<Cuts>(artifactUrl(pid,rid,'scenes.json'));
export const saveCuts=(pid:string,rid:string,body:{revision:number;cuts:number[]})=>request<Cuts>(`${toolBase(pid)}/runs/${encodeURIComponent(rid)}/cuts`,'PUT',body);
