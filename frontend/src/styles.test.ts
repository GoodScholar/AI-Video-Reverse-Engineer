// @ts-expect-error 此测试在 Vitest 的 Node 运行时读取样式源文件。
import { readFileSync } from "node:fs";
import { expect, it } from "vitest";

const styles = readFileSync("src/styles.css", "utf8");

it("减少动态效果时在语义分析动画之后覆盖披露、任务与结果", () => {
  const semanticAnimationIndex = styles.indexOf(".semantic-analysis-result { display: grid;");
  const reducedMotionIndex = styles.indexOf("@media (prefers-reduced-motion: reduce)");
  const reducedMotionRule = styles.slice(reducedMotionIndex, styles.indexOf("}", reducedMotionIndex) + 1);

  expect(reducedMotionIndex).toBeGreaterThan(semanticAnimationIndex);
  expect(reducedMotionRule).toContain(".semantic-analysis-disclosure");
  expect(reducedMotionRule).toContain(".semantic-analysis-task");
  expect(reducedMotionRule).toContain(".semantic-analysis-result");
  expect(reducedMotionRule).toContain("animation: none");
});
