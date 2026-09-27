import { beforeEach, expect, it } from "vitest";
import { createDraftCache } from "./draftStorage";

beforeEach(() => { localStorage.clear(); sessionStorage.clear(); });
it("另一窗口的干净刷新不能清除已写入的草稿", async () => {
  const a = createDraftCache("draft"), b = createDraftCache("draft");
  expect(await a.read()).toBeNull(); expect(await b.read()).toBeNull();
  await a.write("A"); await b.write(null);
  expect(localStorage.getItem("draft")).toBe("A");
});
it("并发编辑拒绝覆盖其他窗口的草稿，原草稿保留", async () => {
  const a = createDraftCache("draft"), b = createDraftCache("draft");
  await a.read(); await b.read();
  await a.write("A");
  await expect(b.write("B")).rejects.toThrow("另一窗口");
  expect(localStorage.getItem("draft")).toBe("A");
  await a.write("A2"); await a.write(null);
  expect(localStorage.getItem("draft")).toBeNull();
});
it("同窗口快速编辑按顺序保存，并迁移旧会话缓存", async () => {
  sessionStorage.setItem("draft", "legacy");
  const cache = createDraftCache("draft");
  expect(await cache.read()).toBe("legacy");
  await Promise.all([cache.write("first"), cache.write("latest")]);
  expect(localStorage.getItem("draft")).toBe("latest");
  expect(sessionStorage.getItem("draft")).toBeNull();
});

it("同一时刻两个窗口写入时只有一方能更新缓存", async () => {
  const a = createDraftCache("simultaneous"), b = createDraftCache("simultaneous");
  await a.read(); await b.read();
  const results = await Promise.allSettled([a.write("A"), b.write("B")]);
  expect(results.map((result) => result.status)).toEqual(["fulfilled", "rejected"]);
  expect(localStorage.getItem("simultaneous")).toBe("A");
});
it("不支持跨窗口锁时拒绝不安全写入", async () => {
  const locks = navigator.locks;
  Object.defineProperty(navigator, "locks", { configurable: true, value: undefined });
  try {
    const cache = createDraftCache("unsupported"); await cache.read();
    await expect(cache.write("draft")).rejects.toThrow("不支持安全");
    expect(localStorage.getItem("unsupported")).toBeNull();
  } finally { Object.defineProperty(navigator, "locks", { configurable: true, value: locks }); }
});
