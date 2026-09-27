import { Maximize2 } from "lucide-react";
import { ASPECT_OPTIONS, type AspectMode, type AspectResolution } from "./videoAspect";
import "./aspectRatioPicker.css";

export function AspectRatioPicker({ name, value, onChange, resolution, disabled = false }: {
  name: string; value: AspectMode; onChange: (value: AspectMode) => void; resolution: AspectResolution; disabled?: boolean;
}) {
  return <fieldset className="aspect-ratio-picker" disabled={disabled}>
    <legend>视频比例</legend>
    <div className="aspect-ratio-options">
      {ASPECT_OPTIONS.map((option) => <label key={option.value}>
        <input type="radio" name={name} value={option.value} checked={value === option.value}
          onChange={() => onChange(option.value)} />
        <span className="aspect-ratio-icon" aria-hidden="true">{option.ratio
          ? <i style={{ aspectRatio: String(option.ratio) }} /> : <Maximize2 size={22} />}</span>
        <span>{option.label}</span>
      </label>)}
    </div>
    <p className="aspect-ratio-summary"><strong>实际输出 {resolution.resolvedAspect} · {resolution.width}×{resolution.height}</strong><span>{resolution.reason}</span></p>
  </fieldset>;
}
