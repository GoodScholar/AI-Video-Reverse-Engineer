// @ts-expect-error Vitest 的 Node 运行时用于注入生产样式并验证真实级联结果。
import { readFileSync } from "node:fs";
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

it("准备资料页不会把视频比例组件套用为资料字段三列布局", () => {
  const styles = document.createElement("style");
  styles.textContent = ["aspectRatioPicker.css", "preproduction.css", "contentStudio.css"]
    .map((file) => readFileSync(`src/${file}`, "utf8")).join("\n");
  document.head.append(styles);
  const { container } = render(<div className="cs-root"><div className="studio-brief-fields">
    <AspectRatioPicker name="preparation-aspect" value="9:16" onChange={() => undefined}
      resolution={{ resolvedAspect: "9:16", width: 720, height: 1280, reason: "使用项目选择的 9:16 比例。" }} />
  </div></div>);

  const picker = container.querySelector(".aspect-ratio-picker")!;
  const options = container.querySelector(".aspect-ratio-options")!;
  const firstOption = container.querySelector(".aspect-ratio-options label")!;
  const lastOption = container.querySelector(".aspect-ratio-options label:last-child")!;
  const labelRule = [...styles.sheet!.cssRules].find((rule): rule is CSSStyleRule =>
    rule instanceof CSSStyleRule && rule.selectorText === ".aspect-ratio-picker .aspect-ratio-options>label");

  expect(getComputedStyle(picker).display).toBe("block");
  expect(getComputedStyle(options).display).toBe("grid");
  expect(getComputedStyle(options).gridTemplateColumns).toBe("repeat(7,minmax(0,1fr))");
  expect(firstOption.firstElementChild?.nextElementSibling).toHaveClass("aspect-ratio-icon");
  expect(labelRule?.style.display).toBe("flex");
  expect(labelRule?.style.getPropertyValue("flex-direction")).toBe("column");
  expect(labelRule?.style.getPropertyValue("align-items")).toBe("center");
  expect(labelRule?.style.getPropertyValue("justify-content")).toBe("center");
  expect(getComputedStyle(lastOption).gridColumn).toBe("auto");
  styles.remove();
});
