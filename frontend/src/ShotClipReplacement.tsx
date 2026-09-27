import { useState } from "react";
import type { TimelineAsset, TimelineClip } from "./timelineApi";

export type ShotResult = { id: string; title: string; resultAssetId?: string | null };

export function ShotClipReplacement({ clip, assets, shots, disabled, onReplace }: {
  clip: TimelineClip; assets: TimelineAsset[]; shots: ShotResult[]; disabled: boolean; onReplace: (assetId: string) => void;
}) {
  const [shotId, setShotId] = useState("");
  const shot = shots.find((item) => item.id === shotId);
  const asset = assets.find((item) => item.id === shot?.resultAssetId && item.kind === "video");
  const required = clip.inPoint + clip.duration * clip.speed;
  const enough = asset?.duration !== undefined && Number.isFinite(asset.duration) && asset.duration + 1e-8 >= required;
  const same = asset?.id === clip.assetId;
  return <div className="timeline-replacement">
    <label>替换为镜头结果<select aria-label="替换为镜头结果" value={shotId} disabled={disabled} onChange={(event) => setShotId(event.target.value)}>
      <option value="">请选择镜头</option>
      {shots.filter((item) => item.resultAssetId).map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
    </select></label>
    <p>仅替换选中片段，保留位置、入点、时长、速度、音量和淡入淡出。可撤销，需保存。</p>
    {shot && <p>{!asset ? "镜头结果素材不可用，请重新读取工作台。" : same ? "此片段已使用该结果。" : !enough ? `结果时长不足：现有剪辑需要至少 ${Math.round(required * 1000) / 1000} 秒素材。` : `将使用：${asset.name}`}</p>}
    <button type="button" className="secondary-action" disabled={disabled || !asset || !enough || same} onClick={() => { if (!disabled && asset && enough && !same) onReplace(asset.id); }}>替换选中片段</button>
  </div>;
}
