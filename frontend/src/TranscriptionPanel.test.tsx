import { render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";
import { TranscriptionPanel } from "./TranscriptionPanel";

vi.stubGlobal("fetch", vi.fn());
beforeEach(() => vi.mocked(fetch).mockResolvedValue(new Response(JSON.stringify({ transcriptions: [{ assetId: "video-a", runId: "r1", status: "failed", language: "zh", model: "small", modelRevision: "small", policyVersion: "faster-whisper-local-blocks-v1", progress: 0.5, completedBlocks: 1, blockCount: 2, sourceSha256: "sha", error: { code: "model_unavailable", message: "faster-whisper 未安装", retryable: true }, words: [] }] }))));

it("显示转录进度、模型策略和模型不可用诊断", async () => {
  render(<TranscriptionPanel projectId="p1" assets={[{ id: "video-a", name: "授权视频", kind: "video", role: "motion", url: "/a.mp4" }]} />);
  expect(await screen.findByText("进度：1/2 个逻辑块 · 50%")).toBeVisible();
  expect(screen.getByText("模型：small · 策略：faster-whisper-local-blocks-v1")).toBeVisible();
  expect(screen.getByRole("alert")).toHaveTextContent("faster-whisper 未安装");
});
