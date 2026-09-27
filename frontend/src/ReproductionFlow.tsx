import { useEffect, useState } from "react";
import type { Project } from "./models";
import type { PreproductionCheck, PreproductionWorkspace, ReproductionInputKind } from "./preproductionApi";
import { getTimeline, preflightTimeline, type TimelinePreflight, type TimelineWorkspace } from "./timelineApi";

type Destination = "assets" | "shots" | "tools" | "timeline" | "delivery";
type Props = {
  project: Project;
  workspace: PreproductionWorkspace;
  disabled: boolean;
  dirty: boolean;
  timelineDirty?: boolean;
  draftInputKind?: ReproductionInputKind;
  onImportShots: () => void;
  onKindChange: (kind: ReproductionInputKind) => void;
  onNavigate: (section: Destination, target?: string) => void;
  onLocateCheck?: (check: PreproductionCheck) => void;
  onFocusClip?: (trackId: string, clipId: string) => void;
};
const modes = [
  { kind: "reference_video", title: "参考视频", detail: "拆解原片的镜头、构图与节奏，再准备角色、场景和动作参考。" },
  { kind: "depth_video", title: "灰度深度视频", detail: "将已有灰度深度视频作为控制候选素材，直接裁切镜头；无需再次估计深度。需在目标模型中确认明暗方向、归一化和帧率。" },
  { kind: "white_model_video", title: "三维白模渲染视频", detail: "上传已渲染的视频，保留构图、镜头运动与空间关系；按目标模型需要另行准备深度或遮罩。这里不接收三维工程文件。" },
] as const;

export function ReproductionFlow(props: Props) {
  return <FlowContent key={props.project.id} {...props} />;
}

function FlowContent({ project, workspace, disabled, dirty, timelineDirty, draftInputKind, onImportShots, onKindChange, onNavigate, onLocateCheck, onFocusClip }: Props) {
  const preferenceKey = `aivre:flow-expanded:${project.id}`;
  const [expanded, setExpanded] = useState(() => {
    try { return localStorage.getItem(preferenceKey) !== "false"; } catch { return true; }
  });
  const [timeline, setTimeline] = useState<TimelineWorkspace | null>(null);
  const [preflight, setPreflight] = useState<TimelinePreflight | null>(null);
  const [timelineError, setTimelineError] = useState("");
  const [checkingTimeline, setCheckingTimeline] = useState(false);
  useEffect(() => {
    setTimeline(null); setPreflight(null); setTimelineError("");
  }, [timelineDirty]);
  async function checkTimeline() {
    setCheckingTimeline(true); setTimelineError("");
    try {
      const next = await getTimeline(project.id);
      if (!Array.isArray(next.tracks)) throw new Error("无法读取已保存时间线。");
      setTimeline(next); setPreflight(null);
      if (next.tracks.some((track) => track.clips.length)) {
        const check = await preflightTimeline(project.id, next.revision, "mp4");
        if (Array.isArray(check.issues)) setPreflight(check);
      }
    } catch (reason) { setTimelineError(reason instanceof Error ? reason.message : "无法读取已保存时间线。"); }
    finally { setCheckingTimeline(false); }
  }
  function toggle() {
    const next = !expanded;
    setExpanded(next);
    try { localStorage.setItem(preferenceKey, String(next)); } catch { /* 查看偏好不影响方案编辑 */ }
  }
  const kind = workspace.brief.inputKind ?? "reference_video";
  const selectedKind = draftInputKind ?? kind;
  const mode = modes.find((item) => item.kind === selectedKind) ?? modes[0];
  const ready = workspace.shots.filter((shot) => shot.duration > 0 && workspace.assets.some((asset) => asset.id === shot.resultAssetId && asset.kind === "video" && asset.available !== false && (asset.duration ?? 0) >= shot.duration)).length;
  const resultIssues: PreproductionCheck[] = [];
  let returned = 0;
  let reviewed = 0;
  for (const shot of workspace.shots) {
    const name = shot.title || shot.id;
    if (shot.resultVersions?.length || shot.resultAssetId) returned += 1;
    const adopted = shot.resultVersions?.find((version) => version.assetId === shot.resultAssetId);
    const asset = workspace.assets.find((item) => item.id === shot.resultAssetId);
    if (!shot.resultAssetId || !asset || asset.available === false) resultIssues.push({ level: "warning", code: "result_missing", shotId: shot.id, message: `${name}尚未采用可用结果` });
    else if (asset.kind !== "video" || (asset.duration ?? 0) < shot.duration || !shot.duration) resultIssues.push({ level: "warning", code: "result_duration_short", shotId: shot.id, message: `${name}的结果时长不足` });
    else if (!adopted?.reviewed || adopted.planChanged) resultIssues.push({ level: "warning", code: "result_review_required", shotId: shot.id, message: `${name}尚未人工复核` });
    if (adopted?.reviewed && !adopted.planChanged) reviewed += 1;
  }
  const savedClips = timeline?.tracks.reduce((total, track) => total + track.clips.length, 0) ?? 0;
  const preflightErrors = preflight?.issues.filter((issue) => issue.level === "error").length ?? 0;
  const preflightWarnings = preflight?.issues.filter((issue) => issue.level === "warning").length ?? 0;
  const reference = project.referenceMedia;
  const inputIssues = workspace.checks.filter((check) => check.code === "brief_incomplete");
  const shotIssues = workspace.checks.filter((check) => check.code !== "brief_incomplete" && check.code !== "result_review_required");
  const pendingShots = new Set(shotIssues.flatMap((check) => check.shotId ? [check.shotId] : [])).size;
  const pendingSteps = new Set(shotIssues.flatMap((check) => check.nodeId ? [`${check.shotId}:${check.nodeId}`] : [])).size;
  const issueList = (issues: PreproductionCheck[]) => issues.length ? <ul className="reproduction-flow-issues">{issues.map((check, index) => <li key={`${check.code ?? "issue"}:${check.shotId ?? "brief"}:${check.nodeId ?? index}`}>
    <span>{check.level === "error" ? "需修复" : "提醒"}：{check.message}</span>
    {onLocateCheck && <button type="button" className="secondary-action" aria-label={`定位${check.message}`} onClick={() => onLocateCheck(check)}>定位</button>}
  </li>)}</ul> : null;
  return <section className="reproduction-flow" aria-labelledby="reproduction-flow-title">
    <div className="reproduction-flow-heading"><h3 id="reproduction-flow-title">视频复刻流程</h3><button type="button" className="secondary-action" aria-expanded={expanded} aria-controls="reproduction-flow-content" onClick={toggle}>{expanded ? "收起流程" : "展开流程"}</button></div>
    {!expanded && <p className="reproduction-flow-summary">{mode.title} · {workspace.shots.length} 个镜头 · {ready} 个结果满足时长要求{(dirty || timelineDirty) && " · 未保存草稿，进度依据已保存版本"}</p>}
    <div id="reproduction-flow-content" hidden={!expanded}>
    {(dirty || timelineDirty) && <p role="status">有未保存草稿；下方进度依据已保存版本。</p>}
    <fieldset className="reproduction-input-modes" disabled={disabled}><legend>选择输入类型</legend>
      {modes.map((item) => <label key={item.kind}><input type="radio" name="reproduction-input-kind" checked={selectedKind === item.kind} onChange={() => onKindChange(item.kind)} />{item.title}</label>)}
    </fieldset>
    <p>{mode.detail}</p>
    <ol className="reproduction-flow-steps">
      <li><span>01 · 输入素材</span><small>{reference?.type === "video" ? `已登记：${reference.originalName}` : "需要上传视频"}；{inputIssues.length} 项需求提醒</small><button type="button" onClick={() => onNavigate("tools", "reference-media-title")}>上传或查看源视频</button>{issueList(inputIssues)}</li>
      <li><span>02 · 拆分镜头</span><small>方案中有 {workspace.shots.length} 个镜头 · 待处理 {pendingShots} 个镜头、{pendingSteps} 个步骤 · {shotIssues.filter((check) => check.level === "error").length} 项需修复 · {shotIssues.filter((check) => check.level === "warning").length} 项提醒</small><button type="button" onClick={() => onNavigate("tools", "local-preprocessing-title")}>预处理与拆镜</button><button type="button" disabled={disabled || dirty || project.localPreprocessing?.status !== "completed"} onClick={onImportShots}>导入已拆分镜头</button><button type="button" onClick={() => onNavigate("shots")}>整理镜头方案</button>{issueList(shotIssues)}</li>
      <li><span>03 · 控制素材</span><small>{kind === "depth_video" ? reference?.type === "video" ? "深度视频已登记，可按镜头裁切；控制效果需在目标工具确认" : "需登记深度视频输入" : "控制素材可选，按生成工具需要准备"}</small><button type="button" onClick={() => onNavigate(kind === "depth_video" ? "assets" : "tools", kind === "depth_video" ? undefined : "depth-capture-title")}>{kind === "depth_video" ? "导入并绑定深度素材" : "准备控制素材"}</button></li>
      <li><span>04 · 外部生成</span><small>待在应用外完成；本应用未验证外部模型运行</small><button type="button" onClick={() => onNavigate("delivery")}>检查并导出方案</button></li>
      <li><span>05 · 回传结果</span><small>已回传 {returned} / {workspace.shots.length} · 时长合格 {ready} · 已人工复核 {reviewed}</small><button type="button" onClick={() => onNavigate("shots")}>关联镜头结果</button>{issueList(resultIssues)}</li>
      <li><span>06 · 剪辑收尾</span><small>{timelineError || (timeline ? `${savedClips} 个已保存片段 · ${savedClips ? preflight ? preflightErrors ? `${preflightErrors} 项需修复` : `可导出 · ${preflightWarnings} 项提醒` : "正在检查导出条件" : "尚未加入剪辑片段"}` : "检查已保存时间线以查看进度")}</small><button type="button" onClick={() => onNavigate("timeline")}>进入剪辑</button><button type="button" disabled={checkingTimeline} onClick={() => void checkTimeline()}>{checkingTimeline ? "正在检查…" : "检查剪辑状态"}</button>{preflight?.issues.length ? <ul className="reproduction-flow-issues">{preflight.issues.map((issue, index) => <li key={index}>{issue.level === "error" ? "需修复" : "提醒"}：{issue.message}<button type="button" className="secondary-action" onClick={() => issue.trackId && issue.clipId && onFocusClip ? onFocusClip(issue.trackId, issue.clipId) : onNavigate("timeline")}>查看剪辑</button></li>)}</ul> : null}</li>
    </ol>
    </div>
  </section>;
}
