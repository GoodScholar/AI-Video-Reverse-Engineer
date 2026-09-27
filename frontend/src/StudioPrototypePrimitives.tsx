// Adapted from OpenShorts, commit dc22ac715710260d59b02b61a2ce877dda8d9d95.
// StepIndicator.jsx and the GalleryCard.jsx visibility observer.
// Copyright (c) 2024 OpenShorts. MIT; notice in studioPrototype.LICENSE.md.
import { useEffect, useRef, useState } from "react";
import { Check } from "lucide-react";

export function StudioSteps({ steps, current, completed, onStepClick }: {
  steps: string[]; current: number; completed: boolean[]; onStepClick: (index: number) => void;
}) {
  return <ol className="sp-steps" aria-label="商品视频制作步骤">{steps.map((label, index) => {
    const done = completed[index];
    const active = index === current;
    const clickable = index === 0 || completed.slice(0, index).every(Boolean);
    return <li key={label}><button type="button" disabled={!clickable} aria-current={active ? "step" : undefined}
      onClick={() => onStepClick(index)} className={done ? "is-done" : ""}>
      <span>{done ? <Check size={15} aria-hidden="true" /> : index + 1}</span><strong>{label}</strong>
    </button>{index < steps.length - 1 && <i aria-hidden="true" />}</li>;
  })}</ol>;
}

export function StudioSamplePlayer({ src, poster, label }: { src: string; poster: string; label: string }) {
  const [visible, setVisible] = useState(false);
  const cardRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const observer = new IntersectionObserver(entries => {
      entries.forEach(entry => { if (entry.isIntersecting) { setVisible(true); observer.unobserve(entry.target); } });
    }, { rootMargin: "200px", threshold: 0.1 });
    const node = cardRef.current;
    if (node) observer.observe(node);
    return () => observer.disconnect();
  }, []);
  return <div ref={cardRef} className="sp-sample-player">{visible
    ? <video src={src} poster={poster} controls playsInline preload="metadata" aria-label={label} />
    : <img src={poster} alt={label} />}</div>;
}
