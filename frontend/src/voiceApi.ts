import { readApiError } from './referenceMediaApi';
import type { BatchTask } from './batchEditingApi';

export type Voice = {id:string;name:string;description:string;useCases:string;group?:"chinese"|"other";sampleUrl:string|null};
export type VoiceCatalog = {available:boolean;message:string;voices:Voice[]};
export type VoiceTarget = {id:string;script:string;contentRevision?:number;voiceover?:BatchTask['voiceover'];
  variant:{revision:number;subtitles?:{revision:number}}};

export async function getVoiceCatalog():Promise<VoiceCatalog> {
  const response=await fetch('/api/voices');
  if(!response.ok) throw new Error(await readApiError(response,'无法读取音色库。'));
  return response.json();
}

export async function generateVoiceovers(projectId:string,ownerId:string,voiceId:string,targets:VoiceTarget[]):Promise<{tasks:BatchTask[]}> {
  const response=await fetch(`/api/projects/${encodeURIComponent(projectId)}/batch-edits/${encodeURIComponent(ownerId)}/voiceovers`,{
    method:'POST',headers:{'Content-Type':'application/json','x-aivre-intent':'semantic-analysis'},body:JSON.stringify({voiceId,confirmScript:true,
      targets:targets.map(task=>({taskId:task.id,contentRevision:task.contentRevision??0,variantRevision:task.variant.revision,
        subtitleRevision:task.variant.subtitles?.revision??0}))})});
  if(!response.ok) throw new Error(await readApiError(response,'无法提交本地配音。'));
  return response.json();
}
