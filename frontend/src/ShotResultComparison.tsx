import { useEffect, useRef, useState } from "react";
import type { PreproductionAsset, PreproductionShot } from "./preproductionApi";
import "./shotComparison.css";

type Props = { shot: PreproductionShot; assets: PreproductionAsset[]; disabled?: boolean };
export function ShotResultComparison({ shot, assets, disabled = false }: Props) {
  const result = assets.find((asset) => asset.id === shot.resultAssetId && asset.kind === "video");
  if (!result) return null;
  const references = assets.filter((asset) => asset.kind === "video" && asset.id !== result.id);
  const trims = shot.nodes.filter((node) => node.kind === "trim" && references.some((asset) => node.input === `asset:${asset.id}`)
    && typeof node.params.start === "number" && typeof node.params.end === "number" && node.params.end > node.params.start && node.params.start >= 0);
  const bound = references.filter((asset) => shot.assetIds.includes(asset.id));
  const sourceId = trims.length === 1 ? trims[0].input.slice(6) : trims.length === 0 && bound.length === 1 ? bound[0].id : "";
  const start = trims.length === 1 ? Number(trims[0].params.start) : 0;
  const end = trims.length === 1 ? Number(trims[0].params.end) : shot.duration;
  const key = JSON.stringify([shot.id, result.id, result.url, sourceId, start, end, shot.duration]);
  return <ComparisonSetup key={key} references={references} result={result} sourceId={sourceId} initialStart={start} initialEnd={end} duration={shot.duration} disabled={disabled} />;
}

export function CandidateResultComparison({ shot, assets, disabled = false }: Props) {
  if (!shot.resultVersions?.length) return null;
  const candidates = shot.resultVersions.flatMap((version, index) => {
    const asset = assets.find((item) => item.id === version.assetId);
    return asset?.kind === "video" && asset.available !== false ? [{ asset, index }] : [];
  });
  return <CandidateComparisonSetup key={shot.id} candidates={candidates} adoptedId={shot.resultAssetId} duration={shot.duration} disabled={disabled} />;
}

function CandidateComparisonSetup({ candidates, adoptedId, duration, disabled }: { candidates: { asset: PreproductionAsset; index: number }[]; adoptedId?: string | null; duration: number; disabled: boolean }) {
  const [firstId, setFirstId] = useState(() => candidates.find((item) => item.asset.id !== adoptedId)?.asset.id ?? "");
  const [secondId, setSecondId] = useState(() => candidates.find((item) => item.asset.id === adoptedId)?.asset.id ?? candidates[1]?.asset.id ?? "");
  if (candidates.length < 2) return <section className="shot-comparison" aria-label="镜头候选 A/B 对照"><h4>候选 A/B 对照</h4><p>至少两个可播放候选才能对照。</p></section>;
  const selectedFirst = candidates.find((item) => item.asset.id === firstId);
  const selectedSecond = candidates.find((item) => item.asset.id === secondId);
  const first = (selectedFirst ?? candidates.find((item) => item.asset.id !== selectedSecond?.asset.id) ?? candidates[0]).asset;
  const second = (selectedSecond ?? candidates.find((item) => item.asset.id !== first.id) ?? candidates[1]).asset;
  return <section className="shot-comparison" aria-label="镜头候选 A/B 对照">
    <h4>候选 A/B 对照</h4>
    <p>任选同一镜头的两个候选同步查看；选择仅用于对照，不改变当前采用结果或剪辑。</p>
    <div className="shot-comparison-range">
      <label>候选 A<select value={first.id} onChange={(event) => setFirstId(event.target.value)}>{candidates.map(({ asset, index }) => <option key={asset.id} value={asset.id}>候选 {index + 1} · {asset.name}</option>)}</select></label>
      <label>候选 B<select value={second.id} onChange={(event) => setSecondId(event.target.value)}>{candidates.map(({ asset, index }) => <option key={asset.id} value={asset.id}>候选 {index + 1} · {asset.name}</option>)}</select></label>
    </div>
    {first.id === second.id ? <p role="status">请选择两个不同的候选。</p> : <ComparisonPlayers key={JSON.stringify([first.url, second.url, duration])} source={first} result={second} start={0} end={duration} duration={duration} disabled={disabled} mode="candidates" />}
  </section>;
}

type SetupProps = { references: PreproductionAsset[]; result: PreproductionAsset; sourceId: string; initialStart: number; initialEnd: number; duration: number; disabled: boolean };
function ComparisonSetup({ references, result, sourceId, initialStart, initialEnd, duration, disabled }: SetupProps) {
  const [selected, setSelected] = useState(sourceId);
  const [start, setStart] = useState(initialStart);
  const [end, setEnd] = useState(initialEnd);
  const source = references.find((asset) => asset.id === selected);
  const valid = Number.isFinite(start) && Number.isFinite(end) && start >= 0 && end > start && duration > 0;
  return <section className="shot-comparison" aria-label="镜头参考与结果对比">
    <h4>参考与结果对比</h4>
    <p>结果零秒对应参考选段起点。这里的选段仅用于查看，不修改镜头或剪辑。</p>
    <label>对比参考素材<select value={selected} onChange={(event) => { setSelected(event.target.value); setStart(0); setEnd(duration); }}><option value="">请选择参考视频</option>{references.map((asset) => <option key={asset.id} value={asset.id}>{asset.name}</option>)}</select></label>
    {source ? <><div className="shot-comparison-range"><label>参考起点（秒）<input type="number" min="0" step="0.01" value={start} onChange={(event) => setStart(Number(event.target.value))} /></label><label>参考终点（秒）<input type="number" min="0" step="0.01" value={end} onChange={(event) => setEnd(Number(event.target.value))} /></label></div>
      {valid ? <ComparisonPlayers key={JSON.stringify([source.url, result.url, start, end, duration])} source={source} result={result} start={start} end={end} duration={duration} disabled={disabled} /> : <p role="status">请设置有效的参考选段和镜头计划时长。</p>}
    </> : <p>请先选择对比参考；如未导入参考视频，可到素材页导入。</p>}
  </section>;
}

function ComparisonPlayers({ source, result, start, end, duration, disabled, mode = "reference" }: { source: PreproductionAsset; result: PreproductionAsset; start: number; end: number; duration: number; disabled: boolean; mode?: "reference" | "candidates" }) {
  const original = useRef<HTMLVideoElement>(null);
  const output = useRef<HTMLVideoElement>(null);
  const generation = useRef(0);
  const live = useRef(true);
  const playbackRequested = useRef(false);
  const [lengths, setLengths] = useState([0, 0]);
  const [time, setTime] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [starting, setStarting] = useState(false);
  const [fps, setFps] = useState(30);
  const [sound, setSound] = useState("reference");
  const [error, setError] = useState("");
  const limit = Math.max(0, Math.min(duration, end - start, lengths[0] - start, lengths[1]));
  const ready = lengths.every((value) => value > 0) && limit > 0;

  function stop() {
    playbackRequested.current = false;
    generation.current += 1;
    original.current?.pause(); output.current?.pause();
    setPlaying(false); setStarting(false);
  }
  function seek(value: number) {
    stop();
    const next = Math.min(limit, Math.max(0, Number.isFinite(value) ? value : 0));
    if (original.current) original.current.currentTime = start + next;
    if (output.current) output.current.currentTime = next;
    setTime(next);
  }
  async function play() {
    if (!ready || starting || disabled) return;
    if (time >= limit) seek(0);
    playbackRequested.current = true;
    setError(""); setStarting(true);
    const token = ++generation.current;
    try {
      await Promise.all([original.current!, output.current!].map(async (video) => {
        await video.play();
        if (!live.current || token !== generation.current) video.pause();
      }));
      if (live.current && token === generation.current) { setStarting(false); setPlaying(true); }
    } catch {
      if (live.current && token === generation.current) { stop(); setError("无法同步播放，请确认两侧视频可播放后重试。"); }
    }
  }
  function metadata(index: number, video: HTMLVideoElement) {
    setLengths((current) => current.map((value, i) => i === index && Number.isFinite(video.duration) ? video.duration : value));
    video.currentTime = index === 0 ? Math.min(start, video.duration) : 0;
  }
  function mediaError() { stop(); setLengths([0, 0]); setError("对比视频加载失败，请重新加载或更换素材。"); }
  function waiting() { if (playbackRequested.current) { seek(time); setError("视频正在缓冲，已暂停两侧；加载后可重新同步播放。"); } }
  useEffect(() => {
    const a = original.current, b = output.current;
    live.current = true;
    return () => { live.current = false; generation.current += 1; a?.pause(); b?.pause(); };
  }, []);
  useEffect(() => { if (disabled) stop(); }, [disabled]);
  useEffect(() => {
    if (!playing) return;
    let frame = 0;
    function tick() {
      const a = original.current, b = output.current;
      if (!a || !b) return;
      const relative = Math.max(0, a.currentTime - start);
      if (relative >= limit) { seek(limit); return; }
      if (Math.abs(b.currentTime - relative) > 0.06) b.currentTime = relative;
      setTime(relative);
      frame = requestAnimationFrame(tick);
    }
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [playing, start, limit]);

  return <>
    <div className="shot-comparison-videos">
      <figure><video aria-label={mode === "reference" ? "对比参考视频" : "候选 A 视频"} ref={original} src={source.url} preload="metadata" playsInline muted={sound !== "reference"} onLoadedMetadata={(event) => metadata(0, event.currentTarget)} onError={mediaError} onWaiting={waiting} onEnded={() => seek(limit)} /><figcaption>{mode === "reference" ? "参考选段" : "候选 A"} · {source.name}</figcaption></figure>
      <figure><video aria-label={mode === "reference" ? "对比结果视频" : "候选 B 视频"} ref={output} src={result.url} preload="metadata" playsInline muted={sound !== "result"} onLoadedMetadata={(event) => metadata(1, event.currentTarget)} onError={mediaError} onWaiting={waiting} onEnded={() => seek(limit)} /><figcaption>{mode === "reference" ? "生成结果" : "候选 B"} · {result.name}</figcaption></figure>
    </div>
    <div className="shot-comparison-controls"><button type="button" className="secondary-action" disabled={!ready} onClick={() => playing || starting ? stop() : void play()}>{playing || starting ? "同步暂停" : "同步播放"}</button><button type="button" className="secondary-action" disabled={!ready || time <= 0} onClick={() => seek(time - 1 / fps)}>上一帧</button><button type="button" className="secondary-action" disabled={!ready || time >= limit} onClick={() => seek(time + 1 / fps)}>下一帧</button></div>
    <label>镜头内定位（秒）<input type="number" min="0" max={limit} step="0.001" value={Number(time.toFixed(3))} disabled={!ready} onChange={(event) => seek(Number(event.target.value))} /></label>
    <input aria-label="对比进度" type="range" min="0" max={limit || 1} step="0.001" value={time} disabled={!ready} onChange={(event) => seek(Number(event.target.value))} />
    <div className="shot-comparison-range"><label>步进帧率<select value={fps} onChange={(event) => setFps(Number(event.target.value))}>{[24, 25, 30, 60].map((value) => <option key={value} value={value}>{value} fps</option>)}</select></label><label>对比声音<select value={sound} onChange={(event) => setSound(event.target.value)}><option value="reference">{mode === "reference" ? "参考视频" : "候选 A"}</option><option value="result">{mode === "reference" ? "生成结果" : "候选 B"}</option><option value="muted">静音</option></select></label></div>
    <p>按所选帧率的时间间隔步进；浏览器定位不保证变帧率视频的解码级逐帧精度。</p>
    {ready && <p>共同播放范围：0–{limit.toFixed(3)} 秒{limit < duration ? mode === "reference" ? "；共同范围短于镜头计划，请检查参考选段或结果时长。" : "；共同范围短于镜头计划，请检查候选时长。" : "。"}</p>}
    {lengths.every((value) => value > 0) && !ready && <p role="status">{mode === "reference" ? "参考选段超出实际视频范围" : "候选视频没有共同播放范围"}，无法同步播放。</p>}
    {error && <div><p role="alert">{error}</p><button type="button" className="secondary-action" onClick={() => { stop(); setTime(0); setLengths([0, 0]); setError(""); original.current?.load(); output.current?.load(); }}>重新加载对比视频</button></div>}
  </>;
}
