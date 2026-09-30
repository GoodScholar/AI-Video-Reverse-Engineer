import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { useState } from "react";

import { PackagingControls, ReframeControls, defaultPackaging, mergeServerReframeFacts, type Packaging, type Reframe } from "./ReframeControls";

const reframe: Reframe = {
  schemaVersion: 1, sourceFingerprint: "a".repeat(64), sourceWidth: 1920, sourceHeight: 1080,
  targetAspect: { width: 720, height: 1280 }, detectorVersion: "mediapipe-object-detector-0.10.21",
  modelSha256: "2e04c53bfeac0ac2a30c057c7e2a777594ce39baaac35a92f74fb1e8c4fc4e0b",
  samples: [{ timeSeconds: 0, boxes: [{ x: .2, y: .1, width: .3, height: .7 }] }], uncertaintyReasons: [], status: "ready",
  override: { mode: "auto", keyframes: [] },
};

describe("ReframeControls", () => {
  it("分析前也播放真实原片并显示裁剪暗区，直到服务端检测事实返回才允许写入关键点", () => {
    const onAnalyze = vi.fn();
    render(<ReframeControls clipId="clip-pending" assetUrl="/source.mp4" reframe={undefined} analysis={undefined} disabled={false}
      onAnalyze={onAnalyze} onCancel={vi.fn()} onChange={vi.fn()} />);

    expect(screen.getByLabelText("clip-pending 原片")).toHaveAttribute("src", "/source.mp4");
    expect(screen.getByLabelText("clip-pending 主体裁剪原片")).toBeVisible();
    expect(screen.getByRole("slider", { name: "clip-pending 裁剪框" })).toHaveAttribute("aria-disabled", "true");
    fireEvent.click(screen.getByRole("button", { name: "分析主体" }));
    expect(onAnalyze).toHaveBeenCalledOnce();
  });

  it("拖动原片上的裁剪框只产生人工关键点，检测事实保持原样", () => {
    const onChange = vi.fn();
    render(<ReframeControls clipId="clip-a" assetUrl="/source.mp4" reframe={reframe} analysis={undefined} disabled={false}
      onAnalyze={vi.fn()} onCancel={vi.fn()} onChange={onChange} />);

    const stage = screen.getByLabelText("clip-a 主体裁剪原片");
    const crop = screen.getByRole("slider", { name: "clip-a 裁剪框" });
    Object.defineProperty(stage, "getBoundingClientRect", { value: () => ({ left: 0, width: 400 }) });
    const event = new Event("pointerdown", { bubbles: true });
    Object.defineProperty(event, "clientX", { value: 260 });
    fireEvent(crop, event);

    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({
      ...reframe, override: { mode: "manual", keyframes: [{ timeSeconds: 0, x: .65, y: .5 }] },
    }));
    expect(onChange.mock.calls[0][0].samples).toEqual(reframe.samples);
  });

  it("显示失败原因并可重试分析，分析状态不清除人工草稿", async () => {
    const onAnalyze = vi.fn();
    render(<ReframeControls clipId="clip-a" assetUrl="/source.mp4" reframe={{ ...reframe, override: { mode: "manual", keyframes: [{ timeSeconds: 0, x: .62, y: .5 }] } }}
      analysis={{ status: "failed", error: "模型不可用" }} disabled={false} onAnalyze={onAnalyze} onCancel={vi.fn()} onChange={vi.fn()} />);

    expect(screen.getByRole("alert")).toHaveTextContent("模型不可用");
    expect(screen.getByDisplayValue("0.62")).toBeVisible();
    await userEvent.click(screen.getByRole("button", { name: "重新分析主体" }));
    expect(onAnalyze).toHaveBeenCalledOnce();
  });

  it("从候选素材选择替换时只交出候选 id，由调用方重置来源相关构图事实", async () => {
    const onReplace = vi.fn();
    render(<ReframeControls clipId="clip-a" assetUrl="/source.mp4" reframe={reframe} analysis={undefined} disabled={false}
      onAnalyze={vi.fn()} onCancel={vi.fn()} onChange={vi.fn()} candidates={[{ id: "other", name: "候选原片.mp4" }]} onReplace={onReplace} />);

    await userEvent.selectOptions(screen.getByLabelText("clip-a 候选素材"), "other");
    expect(onReplace).toHaveBeenCalledWith("other");
  });
});

it("服务端迟到的检测事实合并进草稿但不覆盖人工关键点", () => {
  const manual = { ...reframe, override: { mode: "manual" as const, keyframes: [{ timeSeconds: 0, x: .72, y: .5 }] } };
  const server = { ...reframe, samples: [{ timeSeconds: 0, boxes: [{ x: .4, y: .1, width: .3, height: .7 }] }], override: { mode: "auto" as const, keyframes: [] } };
  const result = mergeServerReframeFacts([{ id: "video", name: "画面", kind: "video", muted: false, hidden: false, clips: [{ id: "clip-a", assetId: "a", start: 0, inPoint: 0, duration: 2, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0, reframe: manual }] }],
    [{ id: "video", name: "画面", kind: "video", muted: false, hidden: false, clips: [{ id: "clip-a", assetId: "a", start: 0, inPoint: 0, duration: 2, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0, reframe: server }] }]);
  expect(result[0].clips[0].reframe?.samples).toEqual(server.samples);
  expect(result[0].clips[0].reframe?.override).toEqual(manual.override);
});

describe("PackagingControls", () => {
  it("编辑单个包装文字、禁用和替换字幕 cue，完整保留包装合同", async () => {
    const onChange = vi.fn();
    function Editor() {
      const [value, setValue] = useState<Packaging>({ ...defaultPackaging(), elements: [
      { id: "title", kind: "title", enabled: true, start: 0, end: 2, text: "旧标题", position: "top", style: "banner" },
      { id: "subtitle", kind: "subtitle", enabled: true, cueId: "cue-a", position: "bottom", style: "plain" },
      ] });
      return <PackagingControls value={value} cues={[{ id: "cue-a", start: 0, end: 1, text: "第一句" }, { id: "cue-b", start: 1, end: 2, text: "第二句" }]}
        onChange={(next) => { setValue(next); onChange(next); }} disabled={false} />;
    }
    render(<Editor />);

    await userEvent.clear(screen.getByLabelText("title 文字"));
    await userEvent.type(screen.getByLabelText("title 文字"), "新标题");
    await userEvent.click(screen.getByLabelText("title 启用"));
    await userEvent.selectOptions(screen.getByLabelText("subtitle 字幕正本"), "cue-b");

    const latest = onChange.mock.calls[onChange.mock.calls.length - 1]?.[0];
    expect(latest).toEqual(expect.objectContaining({ templateId: "commercial-clean", safeArea: expect.any(Object), font: expect.any(Object) }));
    expect(latest.elements).toEqual(expect.arrayContaining([
      expect.objectContaining({ id: "title", text: "新标题", enabled: false }),
      expect.objectContaining({ id: "subtitle", cueId: "cue-b" }),
    ]));
  });

  it("将字体错误和安全区错误直接呈现给编辑者", async () => {
    render(<PackagingControls value={{ ...defaultPackaging(), font: { ...defaultPackaging().font, sha256: "bad" },
      safeArea: { left: .5, right: .5, top: .05, bottom: .05 } }} cues={[]} onChange={vi.fn()} disabled={false} />);
    expect(screen.getAllByRole("alert").map((item) => item.textContent).join(" ")).toContain("受控字体引用无效");
    expect(screen.getAllByRole("alert").map((item) => item.textContent).join(" ")).toContain("安全区没有可用空间");
  });
});
