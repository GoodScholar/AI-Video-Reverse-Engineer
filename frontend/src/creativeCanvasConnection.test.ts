import { describe, expect, it } from "vitest";
import { getDataTypes, isTypeCompatible } from "./creativeCanvasConnection";

describe("Toonflow 连接类型兼容适配", () => {
  it("规范化逗号分隔的类型并支持通配符", () => {
    expect(getDataTypes(" text, image ")).toEqual(["text", "image"]);
    expect(isTypeCompatible("*", "image")).toBe(true);
  });
});
