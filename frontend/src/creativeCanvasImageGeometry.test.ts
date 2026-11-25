import { describe, expect, it } from "vitest";

import {
  buildSplitPieces,
  normalizeCrop,
  operationLabel,
} from "./creativeCanvasImageGeometry";
import { derivationKey, resizeCrop } from "./CreativeCanvasImageEditor";

describe("creativeCanvasImageGeometry", () => {
  it("将越界的自由裁切规范到原图内，并保持最小可导出尺寸", () => {
    expect(
      normalizeCrop({ x: -0.2, y: 0.9, width: 0.04, height: 0.4 }),
    ).toEqual({
      x: 0,
      y: 0.6,
      width: 0.06,
      height: 0.4,
    });
  });

  it("导出锁定比例裁切时保留为维持比例而低于交互最小值的短边", () => {
    expect(
      normalizeCrop(
        { x: 0, y: 0, width: 0.06, height: 0.03375 },
        Number.EPSILON,
      ),
    ).toEqual({
      x: 0,
      y: 0,
      width: 0.06,
      height: 0.03375,
    });
  });

  it("按拖动后的切线计算每块的原图像素区域，不用平均网格覆盖自定义切线", () => {
    expect(
      buildSplitPieces(
        { width: 1000, height: 800 },
        { rows: 2, columns: 2, horizontalLines: [0.25], verticalLines: [0.3] },
      ),
    ).toEqual([
      { row: 0, column: 0, x: 0, y: 0, width: 300, height: 200 },
      { row: 0, column: 1, x: 300, y: 0, width: 700, height: 200 },
      { row: 1, column: 0, x: 0, y: 200, width: 300, height: 600 },
      { row: 1, column: 1, x: 300, y: 200, width: 700, height: 600 },
    ]);
  });

  it("将新素材命名为来源操作产物，使素材库中的文件可辨来源", () => {
    expect(operationLabel("产品主图.jpeg", "crop")).toBe("产品主图-裁切.png");
    expect(
      operationLabel("产品主图.jpeg", "split", { row: 1, column: 2 }),
    ).toBe("产品主图-切分-r2-c3.png");
  });

  it("裁切和切分只对相同来源与相同参数重用操作标识", () => {
    const params = { x: 0.1, y: 0.1, width: 0.5, height: 0.5 };
    expect(derivationKey("asset-1", "crop", params)).toBe(
      derivationKey("asset-1", "crop", params),
    );
    expect(derivationKey("asset-1", "crop", params)).not.toBe(
      derivationKey("asset-1", "split", { rows: 1, columns: 1 }),
    );
  });

  it("锁定比例在拖拽到最小尺寸时保持比例，不分别钳制宽高而变成方形", () => {
    const crop = resizeCrop(
      { x: 0.3, y: 0.3, width: 0.08, height: 0.045 },
      -0.04,
      0,
      "w",
      16 / 9,
      { width: 100, height: 100 } as DOMRect,
    );

    expect(crop.width / crop.height).toBeCloseTo(16 / 9, 5);
  });
});
