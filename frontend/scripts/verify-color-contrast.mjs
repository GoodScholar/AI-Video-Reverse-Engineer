import { readFile } from "node:fs/promises";

const stylesheet = await readFile(new URL("../src/styles.css", import.meta.url), "utf8");
const token = (name) => {
  const match = stylesheet.match(new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!match) throw new Error(`缺少颜色令牌 ${name}`);
  return match[1];
};
const luminance = (hex) => {
  const channels = hex.slice(1).match(/../g).map((value) => Number.parseInt(value, 16) / 255);
  const linear = channels.map((value) => value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4);
  return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2];
};
const contrast = (first, second) => {
  const [lighter, darker] = [luminance(first), luminance(second)].sort((a, b) => b - a);
  return (lighter + 0.05) / (darker + 0.05);
};
const requiredPairs = [
  ["--primary-button", "--primary-button-text"],
  ["--primary-button-hover", "--primary-button-text"],
  ["--metadata-text", "--surface"],
];

for (const [foreground, background] of requiredPairs) {
  const ratio = contrast(token(foreground), token(background));
  if (ratio < 4.5) throw new Error(`${foreground}/${background} 对比度 ${ratio.toFixed(2)}，低于 4.5:1`);
}

console.log("颜色对比度验证通过");
