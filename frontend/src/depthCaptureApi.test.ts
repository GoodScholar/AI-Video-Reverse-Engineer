import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  confirmDepthReview,
  depthPreviewUrl,
  referenceVideoContentUrl,
  startDepthCapture,
} from "./depthCaptureApi";
import type { Project } from "./models";

// 这两个断言防止前端把固定后端顺序退化成任意数组。
// @ts-expect-error 阶段必须是固定四项元组，不能只提供一项。
const incompleteStages: NonNullable<Project["depthCaptures"]>[number]["stages"] = [{
  name: "preparing", status: "completed", startedAt: null, completedAt: null,
}];
const incompleteChecks: NonNullable<Project["depthCaptures"]>[number]["qualityAssessment"] = {
  status: "passed",
  thresholdVersion: 1,
  // @ts-expect-error 质量检查必须是固定六项元组，不能只提供一项。
  checks: [{
    criterion: "completeness", status: "passed", message: "通过", evidence: "测试",
    metric: 0, threshold: 1, sampleTimestamps: [],
  }],
};
void incompleteStages;
void incompleteChecks;

const project: Project = {
  id: "project/001",
  name: "深度项目",
  createdAt: "2026-09-12T00:00:00+00:00",
  updatedAt: "2026-09-12T00:00:00+00:00",
  referenceMedia: null,
  localPreprocessing: null,
  depthCaptures: [{
    id: "capture/001",
    sourceReferenceVideoId: "video-001",
    algorithmVersion: 1,
    status: "completed",
    devicePreference: "mps",
    executionDevice: "mps",
    modelIdentity: {
      modelId: "video-depth-anything-small-relative",
      upstreamCommit: "4f5ae23172ba60fd7bc11ef671cca678842c7072",
      checkpointSha256: "13379300b739e659f076a59d52e9801bd8d38c541a7e71f73bbca4dcfb013609",
    },
    normalizationDirection: "near_white_far_black",
    currentStage: null,
    stages: [{
      name: "preparing",
      status: "completed",
      startedAt: "2026-09-12T00:00:00+00:00",
      completedAt: "2026-09-12T00:00:01+00:00",
    }, {
      name: "estimatingDepth",
      status: "completed",
      startedAt: "2026-09-12T00:00:01+00:00",
      completedAt: "2026-09-12T00:00:02+00:00",
    }, {
      name: "encoding",
      status: "completed",
      startedAt: "2026-09-12T00:00:02+00:00",
      completedAt: "2026-09-12T00:00:03+00:00",
    }, {
      name: "qualityAssessment",
      status: "completed",
      startedAt: "2026-09-12T00:00:03+00:00",
      completedAt: "2026-09-12T00:00:04+00:00",
    }],
    outputSummary: {
      width: 640,
      height: 360,
      frameRate: 8,
      frameCount: 16,
      durationSeconds: 2,
    },
    qualityAssessment: {
      status: "passed",
      thresholdVersion: 1,
      checks: [{
        criterion: "completeness",
        status: "passed",
        message: "通过",
        evidence: "全部帧存在",
        metric: 0,
        threshold: 1,
        sampleTimestamps: [],
      }, {
        criterion: "dynamicRange",
        status: "passed",
        message: "通过",
        evidence: "范围正常",
        metric: 0,
        threshold: 1,
        sampleTimestamps: [],
      }, {
        criterion: "temporalFlicker",
        status: "passed",
        message: "通过",
        evidence: "稳定",
        metric: 0,
        threshold: 1,
        sampleTimestamps: [],
      }, {
        criterion: "directionStability",
        status: "passed",
        message: "通过",
        evidence: "方向稳定",
        metric: 0,
        threshold: 1,
        sampleTimestamps: [],
      }, {
        criterion: "edgeContinuity",
        status: "passed",
        message: "通过",
        evidence: "边缘连续",
        metric: 0,
        threshold: 1,
        sampleTimestamps: [],
      }, {
        criterion: "timelineAlignment",
        status: "passed",
        message: "通过",
        evidence: "时间线对齐",
        metric: 0,
        threshold: 1,
        sampleTimestamps: [],
      }],
    },
    reviewConfirmedAt: null,
    error: null,
    queuedAt: "2026-09-12T00:00:00+00:00",
    startedAt: "2026-09-12T00:00:00+00:00",
    updatedAt: "2026-09-12T00:00:01+00:00",
    completedAt: "2026-09-12T00:00:01+00:00",
  }],
  activeDepthCaptureId: "capture/001",
};

const response = (body: unknown, status = 200) => new Response(JSON.stringify(body), {
  status,
  headers: { "Content-Type": "application/json" },
});

describe("depthCaptureApi", () => {
  beforeEach(() => vi.stubGlobal("fetch", vi.fn()));

  it("builds same-origin encoded media URLs", () => {
    expect(referenceVideoContentUrl("project/id")).toBe(
      "/api/projects/project%2Fid/reference-video/content",
    );
    expect(depthPreviewUrl("project/id", "capture/id")).toBe(
      "/api/projects/project%2Fid/depth-captures/capture%2Fid/preview",
    );
  });

  it("starts depth capture with the default JSON device preference", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(project, 202));

    await expect(startDepthCapture("project/id")).resolves.toEqual(project);

    expect(fetch).toHaveBeenCalledWith(
      "/api/projects/project%2Fid/depth-captures",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ devicePreference: "auto" }),
      },
    );
  });

  it("starts depth capture with the selected device preference", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(project, 202));

    await startDepthCapture("project-001", "mps");

    expect(vi.mocked(fetch).mock.calls[0][1]).toMatchObject({
      body: JSON.stringify({ devicePreference: "mps" }),
    });
  });

  it("confirms a review with an empty POST", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(response(project));

    await expect(confirmDepthReview("project/id", "capture/id")).resolves.toEqual(project);

    expect(fetch).toHaveBeenCalledWith(
      "/api/projects/project%2Fid/depth-captures/capture%2Fid/confirm-review",
      { method: "POST" },
    );
  });

  it("preserves structured and string API errors", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(response({ detail: { code: "depth_device_unavailable", message: "设备不可用" } }, 409))
      .mockResolvedValueOnce(response({ detail: "深度捕捉不存在" }, 404));

    await expect(startDepthCapture("project-001")).rejects.toThrow("设备不可用");
    await expect(confirmDepthReview("project-001", "capture-001")).rejects.toThrow("深度捕捉不存在");
  });

  it("uses stable fallbacks for non-JSON errors and network failures", async () => {
    vi.mocked(fetch)
      .mockResolvedValueOnce(new Response("offline", { status: 503 }))
      .mockRejectedValueOnce(new TypeError("network failed"));

    await expect(startDepthCapture("project-001")).rejects
      .toThrow("无法启动本地深度捕捉，请重试。");
    await expect(confirmDepthReview("project-001", "capture-001")).rejects
      .toThrow("无法连接本地服务，请确认应用服务正在运行后重试。");
  });
});
