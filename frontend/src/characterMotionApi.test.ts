import { afterEach, describe, expect, it, vi } from "vitest";

import { saveCharacterMotion } from "./characterMotionApi";

afterEach(() => vi.unstubAllGlobals());

describe("saveCharacterMotion", () => {
  it("保存请求只发送后端 SavePlan 接受的四个字段", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    await saveCharacterMotion("项目 / 1", { revision: 7, prompt: "walk", settings: { width: 512, height: 512, frames: 81, fps: 16, seed: 42 }, comfyUrl: "http://127.0.0.1:8188" });
    const [, request] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(request.body))).toEqual({ revision: 7, prompt: "walk", settings: { width: 512, height: 512, frames: 81, fps: 16, seed: 42 }, comfyUrl: "http://127.0.0.1:8188" });
  });
});
