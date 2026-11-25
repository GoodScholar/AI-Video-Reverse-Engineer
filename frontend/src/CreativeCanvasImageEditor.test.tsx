import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { CreativeCanvasImageEditor } from "./CreativeCanvasImageEditor";

describe("CreativeCanvasImageEditor", () => {
  it("保留裁切比例模式，并可切换到带可撤销切线的切分编辑器", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    render(<CreativeCanvasImageEditor
      open
      projectId="project-001"
      source={{ id: "asset-source", name: "产品主图.png", url: "/product.png", kind: "image" }}
      onClose={onClose}
      onAssetCreated={vi.fn()}
    />);

    expect(screen.getByRole("button", { name: "自由比例" })).toBeVisible();
    expect(screen.getByRole("button", { name: "原图比例" })).toBeVisible();
    await user.click(screen.getByRole("button", { name: "切分" }));
    expect(screen.getByRole("button", { name: "添加横向切线" })).toBeVisible();
    expect(screen.getByRole("button", { name: "撤销切线" })).toBeDisabled();
    expect(screen.getByRole("dialog", { name: "图片几何编辑" }).tagName).toBe("DIALOG");
    fireEvent.keyDown(screen.getByRole("dialog", { name: "图片几何编辑" }), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});
