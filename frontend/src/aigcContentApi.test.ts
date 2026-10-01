import { expect, it } from "vitest";
import { clearPendingAigcGenerationKey, pendingAigcGenerationKey } from "./aigcContentApi";

it("未知提交响应重试时复用同一 AIGC 幂等键", () => {
  const disclosure = { briefRevision: 1, provider: "p", model: "m", prompt: "发送内容", digest: "digest" };
  const first = pendingAigcGenerationKey("project", disclosure);
  expect(pendingAigcGenerationKey("project", disclosure)).toBe(first);
  clearPendingAigcGenerationKey("project", disclosure);
  expect(pendingAigcGenerationKey("project", disclosure)).not.toBe(first);
});
