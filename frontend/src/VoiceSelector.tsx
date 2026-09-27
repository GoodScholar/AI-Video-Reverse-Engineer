import { useEffect, useRef, useState } from 'react';
import type { BatchTask } from './batchEditingApi';
import { generateVoiceovers, getVoiceCatalog, type Voice, type VoiceCatalog, type VoiceTarget } from './voiceApi';
import './voiceSelector.css';

type Props={projectId:string;ownerId:string;targets:VoiceTarget[];disabled:boolean;
  onGenerated:(tasks:BatchTask[])=>void;onSelectionPending:(pending:boolean)=>void;autoLoad?:boolean;visible?:boolean;
  initialVoiceId?:string;onVoiceChange?:(voiceId:string)=>void;onError?:(message:string)=>void;pendingRequest?:boolean;onSubmitting?:(submitting:boolean)=>void};
export function VoiceSelector({projectId,ownerId,targets,disabled,onGenerated,onSelectionPending,autoLoad=false,visible=true,initialVoiceId='',onVoiceChange,onError,pendingRequest=false,onSubmitting}:Props) {
  const [catalog,setCatalog]=useState<VoiceCatalog|null>(null);
  const [voiceId,setVoiceId]=useState(initialVoiceId);
  const [group,setGroup]=useState<'all'|'chinese'|'other'|'recommended'>(initialVoiceId?'all':autoLoad?'recommended':'all');
  const [confirmed,setConfirmed]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [playing,setPlaying]=useState<string|null>(null);
  const audio=useRef<HTMLAudioElement|null>(null);
  const active=useRef(true);
  const signature=JSON.stringify(targets.map(task=>[task.id,task.script,task.contentRevision,task.variant.revision,task.variant.subtitles?.revision]));
  useEffect(()=>{setConfirmed(false);},[signature]);
  useEffect(()=>{active.current=true;return()=>{active.current=false;audio.current?.pause();};},[]);
  useEffect(()=>{if(autoLoad)void load();},[autoLoad]);
  useEffect(()=>{if(!visible){audio.current?.pause();setPlaying(null);}},[visible]);
  const processing=pendingRequest||targets.some(task=>task.voiceover?.status==='queued'||task.voiceover?.status==='running');
  async function load() {
    setBusy(true);setError('');
    try {const result=await getVoiceCatalog();if(active.current)setCatalog(result);}
    catch(reason){if(active.current)setError(reason instanceof Error?reason.message:'无法读取音色。');}
    finally{if(active.current)setBusy(false);}
  }
  function stop(){audio.current?.pause();audio.current=null;setPlaying(null);}
  async function audition(voice:Voice) {
    if(playing===voice.id){stop();return;}
    stop();setError('');
    if(!voice.sampleUrl){setError('此音色缺少真实试听文件，请检查本地试听目录。');return;}
    const sample=new Audio(voice.sampleUrl);audio.current=sample;
    sample.onended=()=>{if(audio.current===sample)setPlaying(null);};
    sample.onerror=()=>{if(audio.current===sample){setPlaying(null);setError('试听音频无法播放，请检查本地服务与文件。');}};
    try {await sample.play();if(active.current&&audio.current===sample)setPlaying(voice.id);}
    catch {if(active.current&&audio.current===sample){setPlaying(null);setError('浏览器未能播放此音频，请重试。');}}
  }
  async function generate() {
    if(!voiceId||!confirmed||!catalog?.available||processing||disabled)return;
    stop();setBusy(true);setError('');onSubmitting?.(true);
    try {const result=await generateVoiceovers(projectId,ownerId,voiceId,targets);
      onGenerated(result.tasks);onSelectionPending(false);if(active.current)setConfirmed(false);}
    catch(reason){const message=reason instanceof Error?reason.message:'无法生成配音。';onError?.(message);if(active.current)setError(message);}
    finally{onSubmitting?.(false);if(active.current)setBusy(false);}
  }
  return <section className="voice-selector" aria-label="混剪前音色选择">
    <h5>音色与本地配音</h5>
    {!catalog&&<button type="button" className="secondary-action" disabled={busy} onClick={()=>void load()}>查看音色与本地配音</button>}
    {catalog&&<>
      <p>{catalog.message}</p>
      <p>先看描述，再试听。同一句中文样音用于比较，实际配音使用下列已保存脚本；声音特点来自模型官方说明，场景为选用建议。</p>
      <p>共 {catalog.voices.length} 种音色，可查看全部或按原生语言筛选。</p>
      {catalog.voices.some(voice=>voice.group==='other')&&<div className="voice-selector-groups">
        {([['all','全部音色'],['chinese','原生中文'],['other','其他原生语言']] as const).map(([key,label])=><button key={key} type="button" className="secondary-action" aria-pressed={group===key} onClick={()=>setGroup(key)}>{label}</button>)}
        <button type="button" className="secondary-action" disabled={busy||processing} onClick={()=>{stop();setGroup(group==='other'?'chinese':'other');setVoiceId('');onVoiceChange?.('');setConfirmed(false);onSelectionPending(false);}}>都不满意，换一组</button>
      </div>}
      <div className="voice-selector-cards" role="radiogroup" aria-label="标准音色">{catalog.voices.filter((voice,index)=>group==='all'||(group==='recommended'?index<3:(voice.group??'chinese')===group)).map(voice=><article key={voice.id}>
        <h6>{voice.name}</h6><p>{voice.description}</p><p>适合：{voice.useCases}</p>
        <div><button type="button" className="secondary-action" disabled={!voice.sampleUrl} onClick={()=>void audition(voice)}>{playing===voice.id?'停止试听':'试听'} {voice.name}</button>
        <button type="button" role="radio" aria-checked={voiceId===voice.id} aria-label={`选择 ${voice.name}`} className="secondary-action" disabled={busy||processing}
          onClick={()=>{stop();setVoiceId(voice.id);onVoiceChange?.(voice.id);setConfirmed(false);onSelectionPending(targets.some(task=>task.voiceover?.voiceId!==voice.id));}}>{voiceId===voice.id?'已选择':'选择此音色'}</button></div>
      </article>)}</div>
      <p>所选音色：{catalog.voices.find(voice=>voice.id===voiceId)?.name??'尚未选择'}</p>
      <details><summary>本次配音脚本（{targets.length} 条）</summary>{targets.map((task,index)=><p key={task.id}>{index+1}. {task.script}</p>)}</details>
      <label><input type="checkbox" checked={confirmed} disabled={busy||processing} onChange={event=>setConfirmed(event.target.checked)}/>我已确认下列已保存脚本，按所选音色生成配音</label>
      <p>配音在本机离线生成。每段脚本对应一个画面片段，按实际配音时长调整镜头与字幕；原视频声音设为静音，已有配乐保留。画面长度不足时会提示修订，修改后重新预览审核。</p>
      <button type="button" className="primary-action" disabled={busy||disabled||processing||!catalog.available||!voiceId||!confirmed||!targets.length}
        onClick={()=>void generate()}>{busy?'提交中…':targets.length===1?'生成当前配音':`为 ${targets.length} 条生成配音`}</button>
    </>}
    {targets.filter(task=>task.voiceover).map(task=><p key={task.id} role="status">配音：{task.voiceover?.voiceId} · {task.voiceover?.status==='completed'?'已完成':task.voiceover?.status==='queued'?'排队中':task.voiceover?.status==='running'?'生成中':`失败：${task.voiceover?.error}`}
      {task.voiceover?.contentRevision!==(task.contentRevision??0)&&'（脚本已修改，需重新生成）'}</p>)}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
