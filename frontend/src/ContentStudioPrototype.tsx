// PROTOTYPE: compare three content-workbench structures on the existing root route.
// In-memory fixtures only. No provider requests, project mutations, or video rendering.
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, Check, Clapperboard, Download, FileText, Film, Headphones, LoaderCircle, Play, SlidersHorizontal, Square, Upload } from "lucide-react";
import "./contentStudioPrototype.css";
import beansImage from "./contentPrototypeMedia/beans.jpg";
import pourImage from "./contentPrototypeMedia/pour.jpg";
import groundsImage from "./contentPrototypeMedia/grounds.jpg";
import beansDemo from "./contentPrototypeMedia/beans-demo.mp4";
import pourDemo from "./contentPrototypeMedia/pour-demo.mp4";

import vivianSample from "./contentPrototypeMedia/01-vivian.wav";
import serenaSample from "./contentPrototypeMedia/02-serena.wav";
import uncleFuSample from "./contentPrototypeMedia/03-uncle-fu.wav";
import dylanSample from "./contentPrototypeMedia/04-dylan.wav";
import ericSample from "./contentPrototypeMedia/07-eric.wav";
import ryanSample from "./contentPrototypeMedia/08-ryan.wav";
import aidenSample from "./contentPrototypeMedia/09-aiden.wav";
import onoAnnaSample from "./contentPrototypeMedia/10-ono-anna.wav";
import soheeSample from "./contentPrototypeMedia/11-sohee.wav";

type Layout = "A" | "B" | "C";
type Voice = { id: string; title: string; description: string; useCases: string };
const layoutNames = { A: "步骤式制作", B: "三栏剪辑台", C: "批量审片表" };
const stages = ["商品与素材", "脚本确认", "音色选择", "镜头编排", "审核交付"];
const initialScripts = [
  { title: "从备豆开始", text: "先把咖啡豆倒入磨豆机。再把热水缓缓注入滤杯。" },
  { title: "看见手冲过程", text: "将热水缓缓注入滤杯。近看咖啡粉慢慢浸润。" },
  { title: "从萃取到出杯", text: "看咖啡粉在热水中浸润。再看咖啡缓缓注入杯中。" },
  { title: "意式制作片段", text: "从咖啡豆开始准备。接着看意式咖啡的萃取过程。" },
  { title: "这一杯的来处", text: "近看咖啡液流出的过程。最后把咖啡注入杯中。" },
];
const voices: Voice[] = [
  { id: "vivian", title: "Vivian · 明亮女声", description: "中文 / 年轻感女声，明亮，个性鲜明", useCases: "适合：新品介绍、促销口播、年轻生活方式" },
  { id: "serena", title: "Serena · 温柔女声", description: "中文 / 年轻感女声，温暖、柔和", useCases: "适合：咖啡美食、生活分享、温和品牌介绍" },
  { id: "uncle_fu", title: "Uncle Fu · 沉稳男声", description: "中文 / 成熟感男声，低沉、圆润", useCases: "适合：品牌故事、产品讲解、舒缓叙述" },
  { id: "dylan", title: "Dylan · 自然男声", description: "中文（北京口音）/ 年轻感男声，清晰、自然", useCases: "适合：本地商家、日常分享、轻松聊天；标准普通话要求严格时先核对口音" },
  { id: "eric", title: "Eric · 活泼男声", description: "中文（四川口音）/ 男声，活泼、明亮，略带沙哑", useCases: "适合：四川本地商家、亲切口播；标准普通话要求严格时先核对口音" },
  { id: "ryan", title: "Ryan · 动感男声", description: "原生英语 / 男声，动感、节奏感强；当前提供中文试听", useCases: "建议：动感广告、英文商品介绍；中文口播先试听核对口音" },
  { id: "aiden", title: "Aiden · 阳光男声", description: "原生美式英语 / 男声，阳光、中音清晰；当前提供中文试听", useCases: "建议：轻松介绍、英文生活方式内容；中文口播先试听核对口音" },
  { id: "ono_anna", title: "Ono Anna · 俏皮女声", description: "原生日语 / 女声，俏皮、轻盈灵动；当前提供中文试听", useCases: "建议：轻快短片、日语内容；中文口播先试听核对口音" },
  { id: "sohee", title: "Sohee · 温暖女声", description: "原生韩语 / 女声，温暖、情感丰富；当前提供中文试听", useCases: "建议：情感叙述、韩语生活分享；中文口播先试听核对口音" },
];
const voiceSamples: Record<string, string> = { vivian: vivianSample, serena: serenaSample, uncle_fu: uncleFuSample, dylan: dylanSample, eric: ericSample, ryan: ryanSample, aiden: aidenSample, ono_anna: onoAnnaSample, sohee: soheeSample };
const auditionText = "这是一段本地配音试听。清晨，磨好咖啡豆，缓缓注入热水，让香气慢慢展开。你可以比较不同声音的语气、节奏和清晰度，再选择喜欢的音色。";
const assets = [
  { name: "备豆", image: "beans.jpg", description: "咖啡豆倒入磨豆机" },
  { name: "注水", image: "pour.jpg", description: "热水缓缓注入滤杯" },
  { name: "浸润", image: "grounds.jpg", description: "咖啡粉在水中浸润" },
];
const media: Record<string, string> = { "beans.jpg": beansImage, "pour.jpg": pourImage, "grounds.jpg": groundsImage, "beans-demo.mp4": beansDemo, "pour-demo.mp4": pourDemo };

export function ContentStudioPrototype() {
  const [layout, setLayout] = useState<Layout>(() => {
    const value = new URLSearchParams(location.search).get("variant");
    return value === "B" || value === "C" ? value : "A";
  });
  const [stage, setStage] = useState(0);
  const [brief, setBrief] = useState("咖啡制作过程演示；仅描述可观察的画面，不宣称商品功效。素材来自已取得的公开演示素材。");
  const [disclosed, setDisclosed] = useState(false);
  const [generated, setGenerated] = useState(false);
  const [scripts, setScripts] = useState(initialScripts);
  const [confirmed, setConfirmed] = useState<boolean[]>(Array(5).fill(false));
  const [selected, setSelected] = useState(0);
  const [voiceId, setVoiceId] = useState<string | null>(null);
  const [audioUrls, setAudioUrls] = useState<Record<string, string>>(voiceSamples);
  const [playing, setPlaying] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const localUrls = useRef<string[]>([]);
  const timer = useRef<ReturnType<typeof setTimeout>>();
  const [busy, setBusy] = useState(false);
  const [rendered, setRendered] = useState(false);
  const [reviews, setReviews] = useState<Array<"pending" | "approved" | "rejected">>(Array(5).fill("pending"));
  const [note, setNote] = useState("");
  const [notice, setNotice] = useState("先核对演示简报，再进入脚本生成。");
  const [clip, setClip] = useState(0);
  const [volume, setVolume] = useState(25);
  const [failure, setFailure] = useState(false);
  const allConfirmed = generated && confirmed.every(Boolean);
  const selectedVoice = voices.find((voice) => voice.id === voiceId);
  const changeLayout = (next: Layout) => {
    setLayout(next);
    const url = new URL(location.href); url.searchParams.set("variant", next);
    history.replaceState(null, "", url);
  };
  const cycleLayout = (direction: number) => {
    const keys: Layout[] = ["A", "B", "C"];
    changeLayout(keys[(keys.indexOf(layout) + direction + 3) % 3]);
  };
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.target instanceof HTMLElement && event.target.closest("input,textarea,select,button,video,audio,[contenteditable]")) return;
      if (event.key === "ArrowLeft" || event.key === "ArrowRight") { event.preventDefault(); cycleLayout(event.key === "ArrowLeft" ? -1 : 1); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [layout]);
  useEffect(() => () => {
    audioRef.current?.pause();
    clearTimeout(timer.current);
    localUrls.current.forEach((url) => URL.revokeObjectURL(url));
  }, []);
  const invalidate = () => { setRendered(false); setReviews(Array(5).fill("pending")); setNotice("内容已修改，需重新生成审片示意；旧审核已失效。"); };
  const stopAudio = () => { audioRef.current?.pause(); audioRef.current = null; setPlaying(null); };
  const go = (next: number) => { stopAudio(); setStage(next); };
  const canVisit = (index: number) => index < 2 || (index === 2 && allConfirmed) || (index === 3 && allConfirmed && Boolean(voiceId)) || (index === 4 && rendered);
  const generateScripts = () => {
    if (!disclosed) return;
    setBusy(true); setNotice("正在演示脚本生成状态，本次不会发送请求。");
    timer.current = setTimeout(() => { setGenerated(true); setConfirmed(Array(5).fill(false)); setBusy(false); go(1); setNotice("已载入 5 条演示候选，请逐条编辑并确认。"); }, 600);
  };
  const editScript = (text: string) => {
    setScripts((current) => current.map((script, index) => index === selected ? { ...script, text } : script));
    setConfirmed((current) => current.map((value, index) => index === selected ? false : value));
    invalidate(); setNotice("脚本已修改，该条确认与所有成片审核已失效。");
  };
  const audition = (voice: Voice) => {
    if (playing === voice.id) { stopAudio(); return; }
    stopAudio();
    if (!audioUrls[voice.id]) { setNotice("此方案还没有真实试听音频。可载入本地音频验证试听交互；正式音色服务尚未接通。"); return; }
    const audio = new Audio(audioUrls[voice.id]); audioRef.current = audio;
    audio.onended = () => setPlaying(null);
    audio.onerror = () => { setPlaying(null); setNotice("试听文件无法播放，请换一个 MP3 或 WAV 文件。"); };
    void audio.play().then(() => setPlaying(voice.id)).catch(() => { setPlaying(null); setNotice("浏览器未能播放此音频，请检查文件并重试。"); });
  };
  const render = () => {
    if (!allConfirmed || !voiceId) return;
    setBusy(true); setNotice("正在模拟配音与混剪的任务状态。");
    timer.current = setTimeout(() => {
      setBusy(false);
      if (failure) { setNotice("模拟失败：配音服务余额不足。保留脚本与音色选择，未采用备用声音。"); return; }
      setRendered(true); setReviews(Array(5).fill("pending")); go(4); setNotice("已载入审片示意。播放器使用现有公开素材样片，没有执行本轮渲染。");
    }, 800);
  };
  const download = () => {
    const blob = new Blob([JSON.stringify({ prototype: true, layout, brief, scripts, confirmed, voiceId, reviews, note, clip, volume }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = "内容工作台-原型审阅记录.json"; link.click(); URL.revokeObjectURL(url);
    setNotice("已下载原型审阅记录 JSON；这不是视频交付文件。");
  };
  const stepNav = <nav className="cp-stages" aria-label="制作步骤">{stages.map((name, index) => <button key={name} type="button" aria-current={stage === index ? "step" : undefined} disabled={!canVisit(index) || busy} onClick={() => go(index)}><span>{index + 1}</span>{name}</button>)}</nav>;
  const scriptList = <nav className="cp-script-list" aria-label="脚本候选">{scripts.map((script, index) => <button type="button" key={script.title} onClick={() => setSelected(index)} aria-pressed={selected === index}><span>{String(index + 1).padStart(2, "0")}</span><strong>{script.title}</strong><small>{confirmed[index] ? "已确认" : "待确认"}</small></button>)}</nav>;
  const assetPanel = <section className="cp-assets"><h3>可用画面</h3><p>公开素材演示 / 3 个片段</p>{assets.map((asset, index) => <button type="button" aria-pressed={clip === index} key={asset.name} onClick={() => { setClip(index); if (rendered) invalidate(); }}><img src={media[asset.image]} alt={asset.description} /><span><strong>{asset.name}</strong><small>{asset.description}</small></span><span className="cp-time">00:00–00:03</span></button>)}<details><summary>素材来源与授权</summary><p>来自前期已保存的 Pexels 演示素材，作者 Tim Douglas、Nicola Barts、Michael Burrows。原型沿用公开素材验收记录，未上传客户资料。</p><a href="https://www.pexels.com/license/" target="_blank" rel="noreferrer">查看 Pexels 许可</a></details></section>;
  const preview = <section className="cp-preview"><div className="cp-section-head"><h3>{stage === 4 ? "成片审阅示意" : "画面预览"}</h3><span>9:16 / 公开素材</span></div><div className="cp-player"><video key={selected % 2} src={media[selected % 2 ? "pour-demo.mp4" : "beans-demo.mp4"]} poster={media[clip === 0 ? "beans.jpg" : clip === 1 ? "pour.jpg" : "grounds.jpg"]} controls playsInline preload="metadata" aria-label="公开素材样片预览" /></div><p className="cp-caption">{generated ? scripts[selected].text : "从咖啡豆开始，记录一杯咖啡的制作过程。"}</p><p className="cp-muted">文案展示为编排示意。视频为已有样片，未按本次脚本或音色重新合成。</p></section>;
  const timeline = <section className="cp-timeline" aria-label="时间线示意"><div className="cp-section-head"><h3>时间线</h3><span>示意编排 / 00:06</span></div><div className="cp-track"><span><Film size={16} />画面</span><button type="button" onClick={() => { setClip(0); invalidate(); }} aria-pressed={clip === 0}>备豆 / 3 秒</button><button type="button" onClick={() => { setClip(1); invalidate(); }} aria-pressed={clip === 1}>注水 / 3 秒</button></div><div className="cp-track"><span><Headphones size={16} />配音</span><div>{selectedVoice ? `${selectedVoice.title} / 待实际合成` : "先确认脚本，再选择音色"}</div></div><div className="cp-track"><span><FileText size={16} />字幕</span><div>{scripts[selected].text}</div></div><label className="cp-mix">配乐音量（示意）<input type="range" min="0" max="100" value={volume} onChange={(event) => { setVolume(Number(event.target.value)); invalidate(); }} /><output>{volume}%</output></label></section>;

  const stagePanel = <section className="cp-stage-panel" aria-labelledby="cp-stage-title">
    <div className="cp-section-head"><h2 id="cp-stage-title">{stages[stage]}</h2><span>步骤 {stage + 1} / 5</span></div>
    {stage === 0 && <><p>核对事实与素材，再让 AI 生成内容。示例只描述咖啡制作过程。</p><label>商品事实与创作要求<textarea rows={4} value={brief} onChange={(event) => { setBrief(event.target.value); setDisclosed(false); setGenerated(false); setConfirmed(Array(5).fill(false)); invalidate(); }} /></label><div className="cp-inline-assets">{assets.map((asset) => <figure key={asset.name}><img src={media[asset.image]} alt={asset.description} /><figcaption>{asset.name}</figcaption></figure>)}</div><button className="cp-primary" type="button" onClick={() => go(1)}><FileText size={17} />进入脚本生成</button></>}
    {stage === 1 && <>{!generated ? <><p>一次提出 5 条候选；确认之后再进入音色选择。</p><div className="cp-disclosure"><h3>本次发送内容</h3><p>接收服务：原型演示（不实际发送）</p><pre>{brief + "\n可用素材：备豆、注水、浸润；只传名称和备注，不发送视频。\n生成 5 条脚本，每条保留事实依据。"}</pre><label className="cp-check"><input type="checkbox" checked={disclosed} onChange={(event) => setDisclosed(event.target.checked)} />我已核对本次内容</label></div><button type="button" className="cp-primary" disabled={!disclosed || busy || !brief.trim()} onClick={generateScripts}>{busy ? <LoaderCircle size={17} /> : <FileText size={17} />}{busy ? "载入演示候选…" : "模拟生成 5 条脚本"}</button></> : <><div className="cp-script-work">{scriptList}<div><h3>{scripts[selected].title}</h3><label>屏幕文案与配音脚本<textarea rows={5} value={scripts[selected].text} onChange={(event) => editScript(event.target.value)} /></label><p className="cp-muted">事实依据：画面中的备豆、注水、萃取与出杯过程。</p><button type="button" className="cp-secondary" disabled={confirmed[selected] || !scripts[selected].text.trim()} onClick={() => { setConfirmed((current) => current.map((value, index) => index === selected ? true : value)); setNotice(`第 ${selected + 1} 条脚本已确认。`); if (selected < 4) setSelected(selected + 1); }}><Check size={17} />{confirmed[selected] ? "该条已确认" : "确认该条并看下一条"}</button></div></div><div className="cp-action-row"><span>{confirmed.filter(Boolean).length} / 5 条已确认</span><button className="cp-primary" type="button" disabled={!allConfirmed} onClick={() => go(2)}>继续选择音色<ArrowRight size={17} /></button></div></>}</>}
    {stage === 2 && <><p>先看声音特点与适用场景，再试听你感兴趣的音色。</p><blockquote>{auditionText}</blockquote><p className="cp-voice-note">共 9 种预置音色，均提供本机 Qwen3-TTS 实测的同一句中文试听。声音特点参考模型官方说明，适用场景为选用建议；原生外语音色优先用于对应语言，中文效果请先试听。正式脚本配音仍待接入。</p><div className="cp-voices">{voices.map((voice) => <article key={voice.id} className={voiceId === voice.id ? "is-selected" : ""}><div className="cp-voice-title"><Headphones size={22} /><h3>{voice.title}</h3>{voiceId === voice.id && <span>已选择</span>}</div><p>{voice.description}</p><p>{voice.useCases}</p><small>{audioUrls[voice.id] === voiceSamples[voice.id] ? "本机实测试听 / 同一句文案" : "自定义试听文件 / 请自行核对声音是否对应"}</small><div className="cp-action-row"><button type="button" className="cp-secondary" onClick={() => audition(voice)}>{playing === voice.id ? <Square size={15} /> : <Play size={15} />}{playing === voice.id ? "停止试听" : "试听"}</button><button type="button" className="cp-secondary" aria-pressed={voiceId === voice.id} onClick={() => { stopAudio(); setVoiceId(voice.id); invalidate(); setNotice(`已选择「${voice.title}」方案，正式配音仍待接入。`); }}>选择</button></div><label className="cp-audio-upload"><Upload size={14} />载入试听文件<input type="file" accept="audio/*" aria-label={`为${voice.title}载入试听文件`} onChange={(event) => { const file = event.target.files?.[0]; if (!file) return; stopAudio(); const url = URL.createObjectURL(file); localUrls.current.push(url); setAudioUrls((current) => ({ ...current, [voice.id]: url })); }} /></label></article>)}</div><div className="cp-action-row"><span className="cp-muted">已展示当前模型全部 9 种预置音色</span><button type="button" className="cp-primary" disabled={!voiceId} onClick={() => go(3)}>用所选音色继续<ArrowRight size={16} /></button></div></>}
    {stage === 3 && <><p>逐句检查镜头与文案。配音生成后的真实时长决定正式编排；这里仅展示交互。</p><div className="cp-plan"><img src={media[assets[clip].image]} alt={assets[clip].description} /><div><h3>{scripts[selected].title}</h3><p>{scripts[selected].text}</p><label>第一段画面<select value={clip} onChange={(event) => { setClip(Number(event.target.value)); invalidate(); }} >{assets.map((asset, index) => <option value={index} key={asset.name}>{asset.name} / {asset.description}</option>)}</select></label><p className="cp-muted">匹配理由（示意）：所选画面对应制作过程中的具体动作。</p></div></div><p>配音方案：<strong>{selectedVoice?.title}</strong></p><label className="cp-check"><input type="checkbox" checked={failure} onChange={(event) => setFailure(event.target.checked)} />演示服务余额不足的失败状态</label><button type="button" className="cp-primary" disabled={busy || !allConfirmed || !voiceId} onClick={render}>{busy ? <LoaderCircle size={17} /> : <Clapperboard size={17} />}{busy ? "模拟处理…" : "模拟配音混剪并进入审片"}</button></>}
    {stage === 4 && <><p>逐条检查画面、声音与字幕。这里审核的是界面示意，不是新生成成片。</p>{scriptList}<div className="cp-review-state">当前：{scripts[selected].title}<strong>{reviews[selected] === "approved" ? "已通过（模拟）" : reviews[selected] === "rejected" ? "已退回（模拟）" : "待审核"}</strong></div><label>审核备注<textarea rows={2} value={note} onChange={(event) => setNote(event.target.value)} placeholder="例如：第一个镜头与文案不匹配" /></label><div className="cp-action-row"><button type="button" className="cp-secondary" disabled={!note.trim()} onClick={() => { setReviews((current) => current.map((value, index) => index === selected ? "rejected" : value)); setNotice("已模拟退回；可返回镜头编排修订。"); }}>退回修改</button><button type="button" className="cp-primary" disabled={!rendered} onClick={() => { setReviews((current) => current.map((value, index) => index === selected ? "approved" : value)); setNotice("该条审阅示意已通过。修改脚本、音色或编排后需重新审核。"); }}><Check size={16} />通过该条（模拟）</button></div><button type="button" className="cp-secondary cp-download" disabled={!rendered || !reviews.includes("approved")} onClick={download}><Download size={16} />下载原型审阅记录</button></>}
  </section>;

  return <main className={`app-shell cp-root cp-layout-${layout}`}>
    <header className="topbar"><a className="brand-button" href="/"><span className="brand-mark"><Clapperboard size={15} /></span><span>AI Video Reverse Engineer</span></a><span className="cp-prototype-label">可点击设计原型 / 不调用 AI</span></header>
    <div className="cp-project-heading"><div><a className="cp-back" href="/"><ArrowLeft size={15} />返回正式工作台</a><h1>咖啡内容制作</h1><p>从商品素材到可审核短视频的一次完整制作。</p></div><div className="cp-project-meta"><span>公开素材演示</span><span>5 条脚本候选</span><span>仅内存 / 刷新重置</span></div></div>
    <div className="cp-prototype-banner">布局 {layout}：{layoutNames[layout]}。脚本生成、配音混剪与审核结果为模拟；提供本机实测音色试听，正式配音生成待接入；视频使用现有样片。</div>
    {layout !== "C" && stepNav}
    {layout === "A" && <div className="cp-guided"><div>{stagePanel}</div><aside className="cp-guided-aside">{preview}<section className="cp-checklist"><h3>本批制作检查</h3><p><Check size={16} />公开素材已准备</p><p>{generated ? <Check size={16} /> : <FileText size={16} />}5 条脚本：{confirmed.filter(Boolean).length} 条已确认</p><p><Headphones size={16} />{selectedVoice ? `所选方案：${selectedVoice.title}` : "先确认脚本，再试听选音色"}</p><p><Clapperboard size={16} />{rendered ? "审片示意已载入" : "完成前置步骤后进入混剪"}</p></section></aside></div>}
    {layout === "B" && <><div className="cp-editor"><aside>{assetPanel}</aside><div>{preview}{timeline}</div><aside>{stagePanel}</aside></div></>}
    {layout === "C" && <div className="cp-review-layout"><div><section className="cp-batch-header"><h2>本批制作单</h2><p>一行一条内容，集中核对脚本、音色和审核进度。</p>{stepNav}</section><div className="cp-table-scroll"><table><caption>咖啡演示 / 5 条内容</caption><thead><tr><th>内容</th><th>脚本</th><th>声音方案</th><th>审核</th><th>操作</th></tr></thead><tbody>{scripts.map((script, index) => <tr key={script.title} className={selected === index ? "is-active" : ""}><th scope="row"><img src={media[assets[index % 3].image]} alt="" />{script.title}</th><td>{generated ? confirmed[index] ? "已确认" : "待确认" : "待生成"}</td><td>{selectedVoice?.title ?? "待选择"}</td><td>{!rendered ? "待制作" : reviews[index] === "approved" ? "通过（模拟）" : reviews[index] === "rejected" ? "退回（模拟）" : "待审核"}</td><td><button type="button" className="cp-secondary" onClick={() => { setSelected(index); go(rendered ? 4 : generated ? 1 : 0); }}>查看第 {index + 1} 条</button></td></tr>)}</tbody></table></div>{preview}</div><aside>{stagePanel}</aside></div>}
    <p className="cp-notice" role="status">{notice}</p>
    <details className="cp-debug"><summary><SlidersHorizontal size={14} />查看原型状态</summary><pre>{JSON.stringify({ layout, stage: stages[stage], scriptsGenerated: generated, confirmed, selectedScript: selected + 1, selectedVoice: voiceId, playing, rendered, reviews, simulatedFailure: failure }, null, 2)}</pre></details>
    <nav className="cp-switcher" aria-label="原型布局切换"><button type="button" aria-label="上一个布局" onClick={() => cycleLayout(-1)}><ArrowLeft size={18} /></button><span>{layout} / {layoutNames[layout]}</span><button type="button" aria-label="下一个布局" onClick={() => cycleLayout(1)}><ArrowRight size={18} /></button><select aria-label="选择原型布局" value={layout} onChange={(event) => changeLayout(event.target.value as Layout)}>{Object.entries(layoutNames).map(([key, name]) => <option key={key} value={key}>{key} {name}</option>)}</select></nav>
  </main>;
}
