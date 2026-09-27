import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

import { AspectRatioPicker } from "./AspectRatioPicker";
import { resolveProductAspect } from "./videoAspect";

it("提供七种原生单选比例并显示实际输出尺寸", async () => {
  const onChange = vi.fn();
  render(<AspectRatioPicker name="test-aspect" value="9:16" onChange={onChange}
    resolution={{ resolvedAspect: "9:16", width: 720, height: 1280, reason: "商品制作默认比例。" }} />);

  expect(screen.getAllByRole("radio")).toHaveLength(7);
  expect(screen.getByRole("radio", { name: "9:16" })).toBeChecked();
  expect(screen.getByText("实际输出 9:16 · 720×1280")).toBeVisible();
  await userEvent.click(screen.getByRole("radio", { name: "16:9" }));
  expect(onChange).toHaveBeenCalledWith("16:9");
});

it("智能比例不受素材输入顺序影响", () => {
  const assets = [
    { id: "asset-b", width: 1600, height: 1000 },
    { id: "asset-a", width: 1616, height: 1000 },
  ];

  expect(resolveProductAspect("smart", assets)).toEqual(resolveProductAspect("smart", [...assets].reverse()));
});
