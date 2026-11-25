import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { StrictMode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { CreativeCanvasMediaPlayer } from "./CreativeCanvasMediaPlayer";

function readyVideo() {
  const video = screen.getByRole("application", { name: "商品视频" }) as HTMLVideoElement;
  Object.defineProperties(video, {
    duration: { configurable: true, value: 65 }, videoWidth: { configurable: true, value: 1920 }, videoHeight: { configurable: true, value: 1080 },
    currentTime: { configurable: true, writable: true, value: 0 }, volume: { configurable: true, writable: true, value: 1 }, muted: { configurable: true, writable: true, value: false },
  });
  fireEvent.loadedMetadata(video); fireEvent.loadedData(video);
  return video;
}

afterEach(() => vi.restoreAllMocks());

describe("CreativeCanvasMediaPlayer", () => {
  it("按 Toonflow 控件播放、seek 和切换音量", async () => {
    render(<CreativeCanvasMediaPlayer src="/video.mp4" label="商品视频" />);
    const video = readyVideo();
    const play = vi.spyOn(video, "play").mockResolvedValue();
    const pause = vi.spyOn(video, "pause").mockImplementation(() => undefined);

    fireEvent.click(screen.getByRole("button", { name: "播放视频" }));
    await waitFor(() => expect(play).toHaveBeenCalled());
    fireEvent.play(video);
    fireEvent.click(screen.getByRole("button", { name: "暂停视频" }));
    expect(pause).toHaveBeenCalled();

    fireEvent.change(screen.getByRole("slider", { name: "视频播放进度" }), { target: { value: "30" } });
    expect(video.currentTime).toBe(30);
    fireEvent.click(screen.getByRole("button", { name: "静音视频" }));
    expect(video.muted).toBe(true);
  });

  it("截取当前帧时生成真实 PNG 文件并交给画布回调", async () => {
    const capture = vi.fn();
    const context = { drawImage: vi.fn() } as unknown as CanvasRenderingContext2D;
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(context);
    vi.spyOn(HTMLCanvasElement.prototype, "toBlob").mockImplementation(callback => callback(new Blob(["frame"], { type: "image/png" })));
    render(<StrictMode><CreativeCanvasMediaPlayer src="/video.mp4" label="商品视频" onCaptureFrame={capture} /></StrictMode>);
    readyVideo();

    fireEvent.click(screen.getByRole("button", { name: "截取视频帧" }));
    fireEvent.click(screen.getByRole("menuitem", { name: "截取当前帧" }));
    await waitFor(() => expect(capture).toHaveBeenCalledWith(expect.objectContaining({ type: "image/png", name: "商品视频 · 当前帧.png" }), "current"));
    expect((context.drawImage as ReturnType<typeof vi.fn>)).toHaveBeenCalled();
  });
});
