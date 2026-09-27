// PROTOTYPE: approved whole-site visual direction, all states in memory.
// Read-only demo media and real voice samples. Never calls production mutations.
import { useEffect, useRef, useState } from "react";
import { ArrowLeft, ArrowRight, AudioLines, Check, ChevronDown, ChevronRight, Clapperboard, Clock3, FileText,
  Film, FolderOpen, Image, Layers3, LayoutGrid, List, Menu, Play, Plus, Search, Settings2, SlidersHorizontal,
  Square, Upload, WandSparkles, X } from "lucide-react";
import { studioAssets as assets, studioScripts as initialScripts, studioVoices as voices } from "./studioPrototypeData";
import { StudioSamplePlayer, StudioSteps } from "./StudioPrototypePrimitives";
import "./wholeSiteStudioPrototype.css";

type Page = "projects" | "create" | "assets" | "edit" | "review" | "reference" | "tools" | "settings";
type Review = "pending" | "approved" | "rejected";
const pages: Record<Page, { label: string; icon: typeof Film }> = {
  projects: { label: "项目中心", icon: FolderOpen }, create: { label: "商品视频制作", icon: WandSparkles },
  assets: { label: "项目素材", icon: Image }, edit: { label: "单条精修", icon: SlidersHorizontal },
  review: { label: "成片与审片", icon: Film }, reference: { label: "参考视频复刻", icon: Layers3 },
  tools: { label: "本地工具", icon: Clapperboard }, settings: { label: "应用设置", icon: Settings2 },
};
const steps = ["准备资料", "确认脚本", "选声制作", "审核导出"];
const pageFromUrl = (): Page => {
  const value = new URLSearchParams(location.search).get("page");
  return value && value in pages ? value as Page : "projects";
};

export function WholeSiteStudioPrototype() {
  const [page, setPage] = useState<Page>(pageFromUrl);
  const [stage, setStage] = useState(0);
  const [menuOpen, setMenuOpen] = useState(false);
  const [scripts, setScripts] = useState(initialScripts);
  const [selected, setSelected] = useState(0);
  const [confirmed, setConfirmed] = useState<boolean[]>(Array(5).fill(false));
  const [generated, setGenerated] = useState(false);
  const [rendered, setRendered] = useState(false);
  const [voiceId, setVoiceId] = useState("serena");
  const [allVoices, setAllVoices] = useState(false);
  const [playing, setPlaying] = useState<string | null>(null);
  const [reviews, setReviews] = useState<Review[]>(Array(5).fill("pending"));
  const [chosenAssets, setChosenAssets] = useState(assets.map(asset => asset.id));
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState("all");
  const [listView, setListView] = useState(false);
  const [disclose, setDisclose] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [facts, setFacts] = useState("画面包含备豆、注水和咖啡粉浸润过程。\n只描述画面可观察的制作动作，不添加功效承诺。");
  const [product, setProduct] = useState("咖啡制作过程");
  const [audience, setAudience] = useState("想了解手冲过程的观众");
  const [briefNote, setBriefNote] = useState("关注制作步骤、动作和细节。");
  const [reviewNotes, setReviewNotes] = useState<string[]>(Array(5).fill(""));
  const reviewNote = reviewNotes[selected];
  const setReviewNote = (value: string) => setReviewNotes(current => current.map((note, index) => index === selected ? value : note));
  const [notice, setNotice] = useState("");
  const [settingsTab, setSettingsTab] = useState("services");
  const [callToAction, setCallToAction] = useState("了解制作步骤");
  const [forbidden, setForbidden] = useState("不添加口味、价格或功效承诺");
  const audio = useRef<HTMLAudioElement | null>(null);
  const title = useRef<HTMLHeadingElement>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const allConfirmed = confirmed.every(Boolean);
  const selectedVoice = voices.find(voice => voice.id === voiceId)!;
  const stopAudio = () => { audio.current?.pause(); audio.current = null; setPlaying(null); };
  const navigate = (next: Page) => {
    stopAudio(); setPage(next); setMenuOpen(false); setQuery(""); setFilter("all");
    if (next === "create") setStage(current => !generated ? 0 : !allConfirmed ? Math.min(current, 1) : !rendered ? Math.min(current, 2) : current);
    const url = new URL(location.href); url.searchParams.set("page", next); history.pushState(null, "", url);
    window.scrollTo(0, 0);
  };
  useEffect(() => {
    const onBack = () => { stopAudio(); setPage(pageFromUrl()); setMenuOpen(false); setQuery(""); setFilter("all"); };
    window.addEventListener("popstate", onBack);
    return () => { window.removeEventListener("popstate", onBack); audio.current?.pause(); };
  }, []);
  useEffect(() => { title.current?.focus(); }, [page]);
  useEffect(() => { if (disclose && !dialog.current?.open) dialog.current?.showModal(); }, [disclose]);
  const invalidate = (index?: number) => {
    setRendered(false);
    setReviews(current => current.map((value, i) => index === undefined || i === index ? "pending" : value));
  };
  const changeScript = (text: string) => {
    setScripts(current => current.map((script, i) => i === selected ? { ...script, text } : script));
    setConfirmed(current => current.map((value, i) => i === selected ? false : value));
    invalidate(selected); setStage(1); setNotice("文案已修改，这条需要重新确认并制作。其他脚本已保留。");
  };
  const playVoice = (id: string) => {
    if (playing === id) { stopAudio(); return; }
    stopAudio(); const voice = voices.find(item => item.id === id)!;
    const player = new Audio(voice.url); audio.current = player;
    player.onended = () => setPlaying(null);
    player.onerror = () => { setPlaying(null); setNotice("样音未能播放，请重新试听。"); };
    void player.play().then(() => setPlaying(id)).catch(() => setNotice("浏览器未能播放，请再次点击试听。"));
  };
  const markReview = (value: Review) => {
    setReviews(current => current.map((status, i) => i === selected ? value : status));
    setNotice(value === "approved" ? "这条已标记通过（演示）。" : "已记录修改意见（演示），可进入单条精修。");
  };
  const preview = (large = false) => <aside className={`sp-preview ${large ? "sp-preview-large" : ""}`}>
    <div className="sp-preview-title"><span>画面预览</span><span>公开样片 · 9:16</span></div>
    {assets[scripts[selected].asset].video
      ? <StudioSamplePlayer key={`${selected}-${scripts[selected].asset}`} src={assets[scripts[selected].asset].video!} poster={assets[scripts[selected].asset].image} label="公开咖啡素材样片" />
      : <div className="sp-sample-player"><img src={assets[scripts[selected].asset].image} alt="咖啡粉浸润的静态画面" /></div>}
    <div className="sp-preview-caption"><span>{scripts[selected].angle}</span><h3>{scripts[selected].title}</h3><p>{scripts[selected].text}</p></div>
    <p className="sp-preview-note">已有样片，未按当前脚本或音色重新制作。</p>
  </aside>;
  const primary = (label: string, action: () => void, disabled = false) => <button type="button" className="sp-button sp-primary" disabled={disabled} onClick={action}>{label}<ArrowRight size={16} aria-hidden="true" /></button>;
  const assetStrip = <div className="sp-asset-strip">{assets.map(asset => <button type="button" key={asset.id}
    className={chosenAssets.includes(asset.id) ? "is-selected" : ""} aria-pressed={chosenAssets.includes(asset.id)}
    onClick={() => { setChosenAssets(current => current.includes(asset.id) ? current.filter(id => id !== asset.id) : [...current, asset.id]); setGenerated(false); setConfirmed(Array(5).fill(false)); invalidate(); }}>
    <img src={asset.image} alt={asset.notes} /><span>{asset.name}{chosenAssets.includes(asset.id) && <Check size={15} aria-hidden="true" />}</span>
  </button>)}</div>;
  const scriptsRail = <nav className="sp-script-rail" aria-label="五条脚本候选">{scripts.map((script, index) => <button key={script.title} type="button"
    aria-pressed={selected === index} onClick={() => setSelected(index)}><span>{index + 1}</span>
    <div><strong>{script.title}</strong><small>{script.angle}</small></div>{confirmed[index] && <Check size={16} aria-label="已确认" />}
  </button>)}</nav>;

  let content;
  if (page === "projects") content = <>
    <div className="sp-home-intro"><div><h2>你的内容工作室</h2><p>继续上次制作，或开始一批新的商品短视频。</p></div>{primary("新建视频", () => navigate("create"))}</div>
    <section className="sp-featured"><div className="sp-featured-copy"><span className="sp-tag">公开素材演示</span><h3>一杯咖啡，<br />五个内容角度。</h3><p>从商品资料到脚本、声音和成片，<br />沿着一个流程完成制作。</p>{primary("继续制作", () => navigate("create"))}<span className="sp-featured-note">已有 3 份素材 · 5 条示例文案</span></div>
      <div className="sp-contact-sheet"><img src={assets[0].image} alt={assets[0].notes} /><img src={assets[1].image} alt={assets[1].notes} /><img src={assets[2].image} alt={assets[2].notes} /></div></section>
    <div className="sp-section-heading"><h2>最近项目</h2><span>示例项目，切换页面会保留本次草稿</span></div>
    <div className="sp-project-grid"><button type="button" className="sp-project" onClick={() => navigate("create")}><img src={assets[0].image} alt="咖啡项目素材" /><div><span className="sp-tag">商品短视频</span><h3>咖啡制作过程</h3><p>{generated ? `已确认 ${confirmed.filter(Boolean).length}/5 条脚本` : "从商品资料与素材开始"}</p><span>继续制作<ChevronRight size={16} /></span></div></button>
      <button type="button" className="sp-project" onClick={() => navigate("reference")}><img src={assets[1].image} alt="注水参考画面" /><div><span className="sp-tag sp-tag-neutral">参考复刻</span><h3>手冲动作与镜头</h3><p>查看参考、拆镜和结果准备</p><span>查看复刻流程<ChevronRight size={16} /></span></div></button>
      <button type="button" className="sp-new-project" onClick={() => navigate("create")}><Plus size={28} /><strong>开始新的创作</strong><span>商品资料与授权素材准备好即可开始</span></button></div>
  </>;
  else if (page === "create") content = <>
    <div className="sp-work-heading"><div><h2>{product || "商品视频制作"}</h2><p>资料、脚本、声音与成片，都留在同一个项目。</p></div><span className="sp-tag">5 条视频 · 竖版</span></div>
    <StudioSteps steps={steps} current={stage} completed={[generated, allConfirmed, rendered, reviews.every(status => status === "approved")]}
      onStepClick={index => { stopAudio(); setStage(index); }} />
    <div className="sp-workspace"><section className="sp-work-panel">
      <div className="sp-section-heading"><h2>{steps[stage]}</h2><span>第 {stage + 1} 步 / 共 4 步</span></div>
      {stage === 0 && <><p className="sp-lead">告诉我们要表达什么，再选这次可以使用的画面。</p><div className="sp-form"><label>商品或内容名称<input value={product} onChange={event => { setProduct(event.target.value); setAccepted(false); }} /></label>
        <label>已确认事实<textarea rows={3} value={facts} onChange={event => { setFacts(event.target.value); setAccepted(false); setGenerated(false); setConfirmed(Array(5).fill(false)); invalidate(); }} /></label>
        <div className="sp-form-row"><label>目标受众<input value={audience} onChange={event => { setAudience(event.target.value); setAccepted(false); }} /></label><label>想突出的内容<input value={briefNote} onChange={event => { setBriefNote(event.target.value); setAccepted(false); }} /></label></div></div>
        <div className="sp-section-heading sp-subheading"><h3>这次使用的素材</h3><span>已选 {chosenAssets.length} 份</span></div>{assetStrip}
        <button type="button" className="sp-text-button" onClick={() => setNotice("设计预览不上传文件；这里将接入当前项目的素材上传。") }><Upload size={16} />添加更多素材</button>
        <details className="sp-details"><summary>行动引导与禁用表达<ChevronDown size={16} /></summary><div className="sp-form-row"><label>行动引导<input value={callToAction} onChange={event => setCallToAction(event.target.value)} /></label><label>禁用表达<input value={forbidden} onChange={event => setForbidden(event.target.value)} /></label></div></details>
        <div className="sp-action-bar"><span>下一步会展示本次发送内容</span>{primary("生成 5 条脚本", () => { setAccepted(false); setDisclose(true); }, !product.trim() || !facts.trim() || !chosenAssets.length)}</div></>}
      {stage === 1 && <><p className="sp-lead">逐条核对文案与画面，修改后直接确认。</p><div className="sp-script-workspace">{scriptsRail}<div><span className="sp-tag">{scripts[selected].angle}</span><h3>{scripts[selected].title}</h3><label>配音与屏幕文案<textarea rows={5} value={scripts[selected].text} onChange={event => changeScript(event.target.value)} /></label>
        <div className="sp-shot-match"><img src={assets[scripts[selected].asset].image} alt={assets[scripts[selected].asset].notes} /><div><strong>对应画面</strong><p>{assets[scripts[selected].asset].notes}</p><small>示例对应关系，正式制作需核对</small></div></div>
        <button type="button" className="sp-button" disabled={confirmed[selected] || !scripts[selected].text.trim()} onClick={() => { setConfirmed(current => current.map((value, i) => i === selected ? true : value)); if (selected < 4) setSelected(selected + 1); }}><Check size={16} />{confirmed[selected] ? "这条已确认" : "确认这条并看下一条"}</button></div></div>
        <div className="sp-action-bar"><span>已确认 {confirmed.filter(Boolean).length}/5 条</span>{primary("继续选声音", () => setStage(2), !allConfirmed)}</div></>}
      {stage === 2 && <><p className="sp-lead">先看声音特点，再试听你感兴趣的音色。</p><div className="sp-voice-summary"><AudioLines size={22} /><div><strong>本批声音：{selectedVoice.name.split(" · ")[0]}</strong><span>同一句中文样音，可直接比较声音</span></div></div>
        <div className="sp-voice-grid">{(allVoices ? voices : voices.slice(0, 3)).map(voice => <article key={voice.id} className={`sp-voice ${voiceId === voice.id ? "is-selected" : ""}`}><div className="sp-voice-heading"><span className="sp-voice-initial">{voice.name.slice(0, 1)}</span><div><h3>{voice.name.split(" · ")[0]}</h3><span>{voice.name.split(" · ")[1]}</span></div>{voiceId === voice.id && <Check size={18} aria-label="已选择" />}</div><p>{voice.description}</p><small>{voice.useCases}</small><div><button type="button" className="sp-button" onClick={() => playVoice(voice.id)}>{playing === voice.id ? <Square size={14} /> : <Play size={14} />}{playing === voice.id ? "停止" : `试听 ${voice.name.split(" · ")[0]}`}</button><button type="button" className="sp-voice-select" aria-pressed={voiceId === voice.id} onClick={() => { stopAudio(); setVoiceId(voice.id); invalidate(); }}>{voiceId === voice.id ? "已选" : "选用"}</button></div></article>)}</div>
        <button type="button" className="sp-text-button" onClick={() => setAllVoices(value => !value)}>{allVoices ? "收起，先看 3 种推荐声音" : "查看全部 9 种声音"}<ChevronDown size={16} /></button>
        <div className="sp-inline-note"><FileText size={18} /><p>正式制作将使用已确认的 5 条脚本。此处可试听真实样音，制作操作仅演示界面。</p></div>
        <div className="sp-action-bar"><span>声音：{selectedVoice.name.split(" · ")[0]} · 5 条脚本</span>{primary("演示制作并进入审片", () => { stopAudio(); setRendered(true); setReviews(Array(5).fill("pending")); setStage(3); setNotice("已载入审片示意，未调用配音或视频渲染。"); }, !allConfirmed)}</div></>}
      {stage === 3 && <><p className="sp-lead">五条内容已进入审片示意，接下来逐条检查画面、声音与字幕。</p><div className="sp-ready-strip">{assets.map(asset => <img key={asset.id} src={asset.image} alt={asset.notes} />)}</div><h3>在同一处完成核对与修改</h3><p>每条都有自己的文案、声音和审核状态。需要调整时，直接进入单条精修。</p><div className="sp-action-bar"><span>{reviews.filter(value => value === "approved").length}/5 条示意通过</span>{primary("打开批量审片", () => navigate("review"))}</div></>}
    </section>{preview()}</div>
  </>;
  else if (page === "assets") content = <><div className="sp-work-heading"><div><h2>让素材更容易找到</h2><p>咖啡制作过程 · 3 份公开素材，只属于当前演示项目。</p></div><button className="sp-button sp-primary" type="button" onClick={() => setNotice("设计预览不上传文件，正式页面将复用已有素材上传。") }><Upload size={16} />上传素材</button></div>
    <div className="sp-list-toolbar"><label className="sp-search"><Search size={17} /><input aria-label="搜索素材" placeholder="搜索画面或素材名称" value={query} onChange={event => setQuery(event.target.value)} /></label><div className="sp-tabs"><button type="button" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>全部</button><button type="button" aria-pressed={filter === "video"} onClick={() => setFilter("video")}>视频</button><button type="button" aria-pressed={filter === "image"} onClick={() => setFilter("image")}>图片</button></div><button className="sp-icon-button" type="button" aria-label={listView ? "切换为网格" : "切换为列表"} onClick={() => setListView(value => !value)}>{listView ? <LayoutGrid size={18} /> : <List size={18} />}</button></div>
    <div className={`sp-media-grid ${listView ? "sp-media-list" : ""}`}>{assets.filter(asset => `${asset.name}${asset.notes}`.includes(query) && (filter === "all" || (filter === "video" ? !!asset.video : !asset.video))).map((asset, index) => <article key={asset.id} className="sp-media-card"><img src={asset.image} alt={asset.notes} /><div><span className="sp-tag sp-tag-neutral">{asset.video ? "视频" : "图片"}</span><h3>{asset.name}</h3><p>{asset.notes}</p><button type="button" className="sp-text-button" onClick={() => { setSelected(asset.id === "pour" ? 1 : asset.id === "grounds" ? 2 : 0); navigate("edit"); }}>查看画面<ChevronRight size={15} /></button><span className="sp-media-index">素材 {index + 1}</span></div></article>)}</div>
    {!assets.some(asset => `${asset.name}${asset.notes}`.includes(query) && (filter === "all" || (filter === "video" ? !!asset.video : !asset.video))) && <div className="sp-empty"><Search size={28} /><h3>没有找到这份素材</h3><p>换一个名称，或查看全部素材。</p><button type="button" className="sp-button" onClick={() => { setQuery(""); setFilter("all"); }}>清除筛选</button></div>}
    <div className="sp-inline-note"><FolderOpen size={18} /><p>素材在制作、精修和审片之间复用，无需重复上传。</p></div></>;
  else if (page === "edit") content = <><div className="sp-work-heading"><div><button type="button" className="sp-text-button" onClick={() => navigate("review")}><ArrowLeft size={16} />返回本批审片</button><h2>{scripts[selected].title}</h2></div><button type="button" className="sp-button sp-primary" onClick={() => setNotice("已记录精修草稿（仅当前预览）；正式页面会保存版本并生成新预览。")}>保存修改</button></div>
    <div className="sp-editor"><aside className="sp-editor-assets"><h3>换一个画面</h3>{assets.map((asset, index) => <button type="button" key={asset.id} aria-pressed={scripts[selected].asset === index} onClick={() => { setScripts(current => current.map((script, i) => i === selected ? { ...script, asset: index } : script)); invalidate(selected); setNotice("画面选择已改变（演示），原审核失效。"); }}><img src={asset.image} alt={asset.notes} /><span>{asset.name}</span></button>)}</aside>{preview(true)}<section className="sp-editor-details"><div className="sp-section-heading"><h3>文案与字幕</h3><FileText size={18} /></div><label>修改这条文案<textarea rows={6} value={scripts[selected].text} onChange={event => changeScript(event.target.value)} /></label><div className="sp-shot-match"><AudioLines size={22} /><div><strong>{selectedVoice.name}</strong><p>{selectedVoice.description}</p><button type="button" className="sp-text-button" onClick={() => playVoice(voiceId)}>{playing ? "停止试听" : "试听当前音色"}</button></div></div><details className="sp-details"><summary>精细调整<ChevronDown size={16} /></summary><label>画面入点（演示）<input type="number" min="0" defaultValue="0" /></label><label>声音音量（演示）<input type="range" min="0" max="100" defaultValue="80" /></label><p>正式时间线将在此展开。</p></details><div className="sp-inline-note"><Clock3 size={18} /><p>修改后需要生成新的真实预览，再次审核。</p></div></section></div>
    <section className="sp-timeline"><div className="sp-section-heading"><h3>镜头与文案对照</h3><span>编排示意</span></div><div><span><Film size={16} />画面</span>{assets.slice(0, 2).map(asset => <img key={asset.id} src={asset.image} alt={asset.name} />)}</div><div><span><AudioLines size={16} />声音</span><p>{selectedVoice.name} · 正式配音待制作</p></div><div><span><FileText size={16} />字幕</span><p>{scripts[selected].text}</p></div></section></>;
  else if (page === "review") content = <><div className="sp-work-heading"><div><h2>把每一条看清楚</h2><p>播放画面、核对声音与字幕，再确认这条内容。</p></div><button className="sp-button sp-primary" type="button" disabled={!rendered || !reviews.includes("approved")} onClick={() => setNotice(`已选 ${reviews.filter(value => value === "approved").length} 条示意通过项。设计预览不导出视频，正式导出将校验当前版本。`)}>导出通过项<ArrowRight size={16} /></button></div>
    {!rendered && <div className="sp-inline-note"><Film size={18} /><p>当前展示公开样片与审核布局。先完成制作演示，才能标记审核状态。</p>{primary("继续制作", () => navigate("create"))}</div>}
    <div className="sp-list-toolbar"><div className="sp-tabs">{[["all", "全部 5"], ["pending", `待审核 ${reviews.filter(value => value === "pending").length}`], ["approved", `已通过 ${reviews.filter(value => value === "approved").length}`], ["rejected", "需修改"]].map(([value, label]) => <button key={value} type="button" aria-pressed={filter === value} onClick={() => setFilter(value)}>{label}</button>)}</div><span>审核状态为演示记录</span></div>
    <div className="sp-review-layout"><div className="sp-result-grid">{scripts.map((script, index) => ({ script, index })).filter(({ index }) => filter === "all" || reviews[index] === filter).map(({ script, index }) => <button type="button" className={`sp-result-card ${selected === index ? "is-selected" : ""}`} key={index} onClick={() => setSelected(index)} aria-pressed={selected === index}><div><img src={assets[script.asset].image} alt={assets[script.asset].notes} /><span className="sp-result-play"><Play size={19} /></span><span className="sp-result-number">{index + 1}</span></div><h3>{script.title}</h3><p>{script.angle} · {selectedVoice.name.split(" · ")[0]}</p><span className={`sp-status sp-status-${reviews[index]}`}>{reviews[index] === "approved" ? "通过（演示）" : reviews[index] === "rejected" ? "需修改（演示）" : "待审核"}</span></button>)}</div>
      <section className="sp-review-inspector">{preview()}<label>这条的审核意见<textarea value={reviewNote} rows={2} onChange={event => setReviewNote(event.target.value)} placeholder="需要调整的画面、读音或字幕" /></label><div className="sp-review-actions"><button type="button" className="sp-button" onClick={() => navigate("edit")}><SlidersHorizontal size={15} />修改这条</button><button type="button" className="sp-button" disabled={!rendered || !reviewNote.trim()} onClick={() => markReview("rejected")}>退回</button><button type="button" className="sp-button sp-primary" disabled={!rendered} onClick={() => markReview("approved")}><Check size={16} />通过（演示）</button></div></section></div>
    {filter !== "all" && !reviews.includes(filter as Review) && <div className="sp-empty"><Check size={26} /><h3>这个分类暂无作品</h3><button type="button" className="sp-button" onClick={() => setFilter("all")}>查看全部</button></div>}
  </>;
  else if (page === "reference") content = <><div className="sp-work-heading"><div><h2>从参考画面，整理复刻方案</h2><p>独立的参考流程，保留镜头、动作与外部生成准备。</p></div><button className="sp-button sp-primary" type="button" onClick={() => setNotice("设计预览不上传参考视频；正式入口保留原分析与准备能力。") }><Upload size={16} />添加参考视频</button></div>
    <div className="sp-reference-layout">{preview(true)}<section className="sp-work-panel"><h3>参考复刻准备</h3><div className="sp-reference-stages">{["选择参考视频", "拆解镜头与节奏", "准备角色、场景与动作", "导出方案，外部生成", "回填结果与收尾剪辑"].map((label, index) => <button key={label} type="button" onClick={() => setNotice(`「${label}」为流程预览。正式接入时使用已有能力与真实就绪状态。`)}><span>{index + 1}</span><div><strong>{label}</strong><small>{index === 3 ? "最终视频生成在外部工具执行" : "查看该环节的素材与准备内容"}</small></div><ChevronRight size={18} /></button>)}</div><div className="sp-inline-note"><Layers3 size={18} /><p>参考视频、灰度深度与三维白模输入保持各自的能力边界。</p></div></section></div></>;
  else if (page === "tools") content = <><div className="sp-work-heading"><div><h2>选择素材，再处理它</h2><p>工具围绕当前素材展开，处理记录与结果放在一起。</p></div><button type="button" className="sp-button" onClick={() => navigate("assets")}>查看项目素材<ArrowRight size={16} /></button></div>
    <div className="sp-tools-layout"><section className="sp-work-panel"><h3>当前素材</h3>{assetStrip}<div className="sp-inline-note"><FolderOpen size={18} /><p>这里展示三份公开演示素材，不提交实际处理。</p></div></section><section className="sp-tool-list"><h3>可用工具与准备状态</h3>{[["拆分镜头", "整理视频里的镜头与时间范围", Film], ["语音字幕", "识别已有音频，校对后使用", FileText], ["视频超分", "提高画面清晰度，需对应本地引擎", Image], ["深度控制素材", "准备空间与动作控制，需对应模型", Layers3]].map(([label, description, Icon]) => { const ToolIcon = Icon as typeof Film; return <button type="button" key={String(label)} onClick={() => setNotice(`${label}：正式页面将显示真实环境检查，本预览不会执行工具。`)}><ToolIcon size={22} /><div><strong>{String(label)}</strong><small>{String(description)}</small></div><span>查看准备<ChevronRight size={15} /></span></button>; })}</section></div>
    <section className="sp-processing-empty"><Clock3 size={22} /><div><h3>处理记录会显示在这里</h3><p>可以查看进度、继续失败步骤，并在完成后预览结果。</p></div></section></>;
  else content = <><div className="sp-work-heading"><div><h2>为制作准备好环境</h2><p>应用连接与本地制作设置集中在这里，项目资料留在项目内。</p></div><span className="sp-tag sp-tag-neutral">设置布局预览</span></div>
    <div className="sp-settings-layout"><nav aria-label="设置分类">{[["services", "AI 服务连接"], ["local", "本地声音与渲染"], ["files", "文件与备份"]].map(([value, label]) => <button key={value} type="button" aria-pressed={settingsTab === value} onClick={() => setSettingsTab(value)}>{label}<ChevronRight size={16} /></button>)}</nav><section className="sp-settings-panel">
      {settingsTab === "services" && <><h3>AI 脚本服务</h3><p>生成脚本前仍会展示本次发送内容，由你确认后发送。</p><div className="sp-setting-row"><div><strong>当前服务</strong><p>正式页面读取已有配置与连接测试结果</p></div><span className="sp-tag sp-tag-neutral">预览不读取凭据</span></div><label>服务配置<input disabled value="在正式工作台配置" readOnly /></label><button type="button" className="sp-button" onClick={() => setNotice("视觉预览不会读取或修改 API Key。正式应用继续使用安全存储与连接验证。")}>查看配置说明<ArrowRight size={16} /></button></>}
      {settingsTab === "local" && <><h3>本地配音与视频制作</h3><div className="sp-setting-row"><div><strong>声音目录</strong><p>已有 9 种 Qwen3-TTS 真实样音</p></div><button className="sp-button" type="button" onClick={() => { setAllVoices(true); setNotice("试听在制作的第三步；先完成五条脚本确认即可选声。"); navigate("create"); }}>前往选声<ArrowRight size={16} /></button></div><div className="sp-setting-row"><div><strong>视频渲染</strong><p>继续复用现有 FFmpeg，正式入口显示实际状态</p></div><span>本预览不执行渲染</span></div><p>新增软件、依赖或模型下载需事先确认。</p></>}
      {settingsTab === "files" && <><h3>项目文件与备份</h3><div className="sp-setting-row"><div><strong>项目文件保存在本地</strong><p>备份包括已保存素材、方案与产物，不含账号凭据或模型</p></div><FolderOpen size={22} /></div><div className="sp-setting-row"><div><strong>备份与恢复</strong><p>正式应用保留现有 ZIP 校验与独立项目恢复</p></div><button type="button" className="sp-button" onClick={() => setNotice("本次视觉预览不创建备份或恢复项目，可返回正式工作台操作。")}>查看备份入口</button></div></>}
    </section></div></>;

  return <div className="sp-root"><aside className={`sp-sidebar ${menuOpen ? "is-open" : ""}`}>
    <button type="button" className="sp-brand" onClick={() => navigate("projects")}><span><Clapperboard size={21} /></span><div><strong>内容工作室</strong><small>AI Video Studio</small></div></button><button type="button" className="sp-mobile-close sp-icon-button" aria-label="关闭导航" onClick={() => setMenuOpen(false)}><X size={20} /></button>
    <nav className="sp-global-nav" aria-label="应用导航">{(["projects", "tools", "settings"] as Page[]).map(value => { const Icon = pages[value].icon; return <button type="button" key={value} aria-current={page === value ? "page" : undefined} onClick={() => navigate(value)}><Icon size={18} />{pages[value].label}</button>; })}</nav>
    <div className="sp-sidebar-project"><span>当前演示项目</span><strong><span className="sp-project-dot" />咖啡制作过程</strong></div>
    <nav className="sp-project-nav" aria-label="项目导航">{(["create", "assets", "review", "reference"] as Page[]).map(value => { const Icon = pages[value].icon; return <button type="button" key={value} aria-current={page === value || (value === "review" && page === "edit") ? "page" : undefined} onClick={() => navigate(value)}><Icon size={18} />{pages[value].label}{value === "review" && rendered && <span>{reviews.filter(status => status === "pending").length}</span>}</button>; })}</nav>
    <div className="sp-sidebar-bottom"><span className="sp-local-dot" />公开素材 · 本地样音<a href="/">返回正式工作台<ArrowRight size={15} /></a></div>
  </aside>{menuOpen && <button type="button" className="sp-menu-backdrop" aria-label="关闭导航遮罩" onClick={() => setMenuOpen(false)} />}
    <div className="sp-shell"><header className="sp-topbar"><button type="button" className="sp-menu-button sp-icon-button" aria-label="打开导航" onClick={() => setMenuOpen(true)}><Menu size={20} /></button><div className="sp-breadcrumb"><span>内容工作室</span><ChevronRight size={14} /><h1 ref={title} tabIndex={-1}>{pages[page].label}</h1></div><span className="sp-preview-badge">设计预览 · 示例数据</span></header>
      <main className={`sp-main sp-page-${page}`}>{content}</main><footer className="sp-footer"><span>全站视觉稿 · 生成、保存与审核仅演示；样音可真实播放</span><button type="button" onClick={() => setNotice("示例使用已保存的公开 Pexels 咖啡素材；状态只保存在当前标签页内存中，刷新重置。")}>关于这份预览</button></footer>
    </div>
    {notice && <div className="sp-notice" role="status"><span>{notice}</span><button type="button" className="sp-icon-button" aria-label="关闭提示" onClick={() => setNotice("")}><X size={16} /></button></div>}
    {disclose && <dialog ref={dialog} className="sp-dialog" aria-labelledby="sp-disclose-title" onCancel={() => setDisclose(false)}><button type="button" autoFocus className="sp-dialog-close sp-icon-button" aria-label="关闭发送内容预览" onClick={() => setDisclose(false)}><X size={20} /></button><h2 id="sp-disclose-title">先确认这次发送的内容</h2><p>本次为视觉演示，不实际调用 AI。正式页面会显示你所选的真实服务。</p><dl><dt>商品或内容</dt><dd>{product}</dd><dt>已确认事实</dt><dd>{facts}</dd><dt>受众与内容方向</dt><dd>{audience} / {briefNote}</dd><dt>行动引导与禁用表达</dt><dd>{callToAction} / {forbidden}</dd><dt>所选素材名称与说明</dt><dd>{assets.filter(asset => chosenAssets.includes(asset.id)).map(asset => `${asset.name}：${asset.notes}`).join("；")}</dd></dl><p className="sp-muted">只发送上述文字与素材说明，不发送原始图片或视频。</p><label className="sp-check"><input type="checkbox" checked={accepted} onChange={event => setAccepted(event.target.checked)} />我已核对这次内容</label><div className="sp-dialog-actions"><button type="button" className="sp-button" onClick={() => setDisclose(false)}>返回修改</button>{primary("确认并载入示例脚本", () => { setGenerated(true); setConfirmed(Array(5).fill(false)); invalidate(); setStage(1); setDisclose(false); setNotice("已载入 5 条示例脚本，未向任何服务发送请求。"); }, !accepted)}</div></dialog>}
  </div>;
}
