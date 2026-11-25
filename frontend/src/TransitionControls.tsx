import { useEffect, useState } from "react";
import type { TimelineTransition, TimelineTransitionTemplate } from "./timelineApi";
import { effectTransitions, transitionLabels } from "./timelineTransitions";

export function TransitionControls({ boundary, name, onChange }: { boundary: TimelineTransition; name: string; onChange: (change: Partial<TimelineTransition>) => void }) {
  const [mobile, setMobile] = useState(() => window.matchMedia?.("(max-width: 600px)").matches ?? false);
  useEffect(() => {
    const query = window.matchMedia?.("(max-width: 600px)");
    const update = () => setMobile(query?.matches ?? false);
    query?.addEventListener?.("change", update);
    return () => query?.removeEventListener?.("change", update);
  }, []);
  return <fieldset className="batch-editor-transition" disabled={mobile}>
    <legend>{name} 镜头边界</legend>
    {mobile && <><p>移动端为转场只读查看；请在桌面端调整。</p><p>当前转场：{transitionLabels[boundary.template]} · {boundary.duration} 秒 · {boundary.enabled ? "已启用" : "已关闭"}</p></>}
    <label>{name} 启用转场<input type="checkbox" checked={boundary.enabled} onChange={event => onChange({ enabled: event.target.checked })} /></label>
    <label>{name} 转场类型<select aria-label={`${name} 转场类型`} value={boundary.template} onChange={event => onChange({ template: event.target.value as TimelineTransitionTemplate })}>
      {Object.entries(transitionLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
    </select></label>
    <label>{name} 持续时间（秒）<input aria-label={`${name} 持续时间（秒）`} type="number" min="0.05" max="2" step="0.05" disabled={!effectTransitions.has(boundary.template)} value={boundary.duration} onChange={event => onChange({ duration: Number(event.target.value) })} /></label>
    <label>{name} 音频交叉淡化<input type="checkbox" disabled={!effectTransitions.has(boundary.template)} checked={boundary.audioCrossfade} onChange={event => onChange({ audioCrossfade: event.target.checked })} /></label>
    <p>{boundary.reason}</p>
    {boundary.template === "speed-change" && <p>请在相邻片段的速度字段设置非 1 倍速度；此边界不使用重叠。</p>}
  </fieldset>;
}
