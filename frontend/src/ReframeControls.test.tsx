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
    const video = screen.getByLabelText("clip-a 原片");
    const crop = screen.getByRole("slider", { name: "clip-a 裁剪框" });
    Object.defineProperty(stage, "getBoundingClientRect", { value: () => ({ left: 0, top: 0, width: 400, height: 300 }) });
    Object.defineProperty(video, "getBoundingClientRect", { value: () => ({ left: 0, top: 0, width: 400, height: 300 }) });
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


  it("按真实视频显示矩形拖动，留边不参与源像素坐标", () => {
    const onChange = vi.fn();
    render(<ReframeControls clipId="letterbox" assetUrl="/source.mp4" reframe={reframe} analysis={undefined} disabled={false}
      onAnalyze={vi.fn()} onCancel={vi.fn()} onChange={onChange} />);
    const stage = screen.getByLabelText("letterbox 主体裁剪原片");
    const video = screen.getByLabelText("letterbox 原片");
    Object.defineProperty(stage, "getBoundingClientRect", { value: () => ({ left: 0, top: 0, width: 400, height: 300 }) });
    Object.defineProperty(video, "getBoundingClientRect", { value: () => ({ left: 100, top: 0, width: 200, height: 300 }) });
    fireEvent.loadedMetadata(video);
    const event = new Event("pointerdown", { bubbles: true }); Object.defineProperty(event, "clientX", { value: 250 });
    fireEvent(screen.getByRole("slider", { name: "letterbox 裁剪框" }), event);
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ override: expect.objectContaining({ keyframes: [expect.objectContaining({ x: .75 })] }) }));
  });

  it("800×360 播放器中的 1120×630 原片按实际内容矩形保存横裁位置", () => {
    const onChange = vi.fn();
    const horizontal = { ...reframe, sourceWidth: 1120, sourceHeight: 630, targetAspect: { width: 720, height: 1280 } };
    render(<ReframeControls clipId="crop-axis" assetUrl="/wide.mp4" reframe={horizontal} analysis={undefined} disabled={false}
      onAnalyze={vi.fn()} onCancel={vi.fn()} onChange={onChange} />);
    const stage = screen.getByLabelText("crop-axis 主体裁剪原片");
    const video = screen.getByLabelText("crop-axis 原片") as HTMLVideoElement;
    Object.defineProperty(stage, "getBoundingClientRect", { value: () => ({ left: 50, top: 40, width: 800, height: 360 }) });
    Object.defineProperty(video, "getBoundingClientRect", { value: () => ({ left: 50, top: 40, width: 800, height: 360 }) });
    Object.defineProperty(video, "videoWidth", { value: 1120 }); Object.defineProperty(video, "videoHeight", { value: 630 });
    fireEvent.loadedMetadata(video);
    const event = new Event("pointerdown", { bubbles: true });
    // 画面实际显示为 640×360，左侧留边 80px；全元素坐标会错误保存为 .70。
    Object.defineProperty(event, "clientX", { value: 50 + 80 + 640 * .75 });
    Object.defineProperty(event, "clientY", { value: 40 + 180 });
    fireEvent(screen.getByRole("slider", { name: "crop-axis 裁剪框" }), event);
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({
      override: expect.objectContaining({ keyframes: [expect.objectContaining({ x: .75, y: .5 })] }),
    }));
  });

  it("竖源转横按服务端二维裁剪高度绘制并允许纵向拖框", () => {
    const onChange = vi.fn();
    const vertical = { ...reframe, sourceWidth: 630, sourceHeight: 1120, targetAspect: { width: 1280, height: 720 } };
    render(<ReframeControls clipId="vertical" assetUrl="/vertical.mp4" reframe={vertical} analysis={undefined} disabled={false} onAnalyze={vi.fn()} onCancel={vi.fn()} onChange={onChange} />);
    const stage = screen.getByLabelText("vertical 主体裁剪原片"); const video = screen.getByLabelText("vertical 原片") as HTMLVideoElement;
    Object.defineProperty(stage, "getBoundingClientRect", { value: () => ({ left: 0, top: 0, width: 400, height: 300 }) });
    Object.defineProperty(video, "getBoundingClientRect", { value: () => ({ left: 0, top: 0, width: 400, height: 300 }) });
    Object.defineProperty(video, "videoWidth", { value: 630 }); Object.defineProperty(video, "videoHeight", { value: 1120 }); fireEvent.loadedMetadata(video);
    const crop = screen.getByRole("slider", { name: "vertical 裁剪框" });
    expect(parseFloat((crop as HTMLElement).style.height)).toBeCloseTo(94.9, 0);
    const event = new Event("pointerdown", { bubbles: true }); Object.defineProperty(event, "clientX", { value: 200 }); Object.defineProperty(event, "clientY", { value: 210 }); fireEvent(crop, event);
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ override: expect.objectContaining({ keyframes: [expect.objectContaining({ x: .5, y: .7 })] }) }));
  });

  it("播放插值中心并在选择样本时 seek 到绝对源时间", () => {
    const onChange = vi.fn(); const twoPoints = { ...reframe, samples: [{ timeSeconds: 4, boxes: [], center: { x: .2, y: .3 }, confidence: .9 }, { timeSeconds: 6, boxes: [], center: { x: .8, y: .7 }, confidence: .9 }] };
    render(<ReframeControls clipId="interpolate" assetUrl="/source.mp4" sourceStart={4} sourceEnd={6} reframe={twoPoints} analysis={undefined} disabled={false} onAnalyze={vi.fn()} onCancel={vi.fn()} onChange={onChange} />);
    const video = screen.getByLabelText("interpolate 原片") as HTMLVideoElement;
    Object.defineProperty(video, "currentTime", { value: 5, writable: true }); fireEvent.timeUpdate(video);
    expect(screen.getByRole("slider", { name: "interpolate 裁剪框" })).toHaveAttribute("aria-valuenow", "0.5");
    fireEvent.click(screen.getByRole("button", { name: "0:06" }));
    expect(video.currentTime).toBe(6);
  });

  it("播放从非零入点同步源时间，重复新增覆盖同一关键点且不允许删除最后一点", () => {
    const onChange = vi.fn();
    render(<ReframeControls clipId="source-time" assetUrl="/source.mp4" sourceStart={4} sourceEnd={6} reframe={{ ...reframe, samples: [{ ...reframe.samples[0], timeSeconds: 4 }] }} analysis={undefined} disabled={false}
      onAnalyze={vi.fn()} onCancel={vi.fn()} onChange={onChange} />);
    const video = screen.getByLabelText("source-time 原片") as HTMLVideoElement;
    Object.defineProperty(video, "currentTime", { value: 5.2, writable: true }); fireEvent.timeUpdate(video);
    fireEvent.click(screen.getByRole("button", { name: "新增关键点" }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ override: expect.objectContaining({ keyframes: [expect.objectContaining({ timeSeconds: 5.2 })] }) }));
    fireEvent.click(screen.getByRole("button", { name: "新增关键点" }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ override: expect.objectContaining({ keyframes: [expect.any(Object)] }) }));
    expect(screen.getByRole("button", { name: "删除关键点" })).toBeDisabled();
    fireEvent.change(screen.getByLabelText("关键点秒"), { target: { value: "99" } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ override: expect.objectContaining({ keyframes: [expect.objectContaining({ timeSeconds: 6 })] }) }));
  });

  it("静态图片使用 img，且不暴露视频主体分析操作", () => {
    render(<ReframeControls clipId="still" assetUrl="/source.png" mediaKind="image" reframe={reframe} analysis={undefined} disabled={false}
      onAnalyze={vi.fn()} onCancel={vi.fn()} onChange={vi.fn()} />);
    expect(screen.getByRole("img", { name: "still 原片" })).toHaveAttribute("src", "/source.png");
    expect(screen.queryByRole("button", { name: "分析主体" })).toBeNull();
    expect(document.querySelector('video[aria-label="still 原片"]')).toBeNull();
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

it("来源素材或源时间变化时拒绝迟到的服务端检测事实", () => {
  const local = { ...reframe, override: { mode: "manual" as const, keyframes: [{ timeSeconds: 0, x: .72, y: .5 }] } };
  const server = { ...reframe, samples: [{ timeSeconds: 0, boxes: [{ x: .4, y: .1, width: .3, height: .7 }] }] };
  const result = mergeServerReframeFacts([{ id: "video", name: "画面", kind: "video", muted: false, hidden: false, clips: [{ id: "clip-a", assetId: "new", start: 0, inPoint: 1, duration: 2, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0, reframe: local }] }],
    [{ id: "video", name: "画面", kind: "video", muted: false, hidden: false, clips: [{ id: "clip-a", assetId: "old", start: 0, inPoint: 0, duration: 2, speed: 1, volume: 1, fadeIn: 0, fadeOut: 0, reframe: server }] }]);
  expect(result[0].clips[0].reframe).toEqual(local);
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

  it("模板可选择且可一次关闭全部已启用包装", async () => {
    const onChange = vi.fn(); const value = { ...defaultPackaging(), elements: [{ id: "title", kind: "title" as const, enabled: true, start: 0, end: 2, text: "标题", position: "top" as const, style: "banner" as const }] };
    render(<PackagingControls value={value} cues={[]} onChange={onChange} disabled={false} />);
    expect(screen.getByLabelText("包装模板")).toHaveValue("commercial-clean");
    await userEvent.click(screen.getByRole("button", { name: "关闭已启用包装" }));
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ elements: [expect.objectContaining({ enabled: false })] }));
  });

  it("删除后新增同类元素仍生成唯一 id", async () => {
    function Editor() {
      const [value, setValue] = useState<Packaging>({ ...defaultPackaging(), elements: [{ id: "title-1", kind: "title", enabled: true, start: 0, end: 2, text: "旧", position: "top", style: "banner" }, { id: "title-2", kind: "title", enabled: true, start: 0, end: 2, text: "留存", position: "top", style: "banner" }] });
      return <PackagingControls value={value} cues={[]} onChange={setValue} disabled={false} />;
    }
    render(<Editor />);
    await userEvent.click(screen.getAllByRole("button", { name: "删除" })[0]);
    await userEvent.click(screen.getByRole("button", { name: "添加title" }));
    expect(screen.getAllByLabelText(/title-\d+ 文字/)).toHaveLength(2);
  });
});
