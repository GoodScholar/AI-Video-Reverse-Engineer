// Design preview fixtures only; reuse existing, authorized local demo media.
import beans from "./contentPrototypeMedia/beans.jpg";
import pour from "./contentPrototypeMedia/pour.jpg";
import grounds from "./contentPrototypeMedia/grounds.jpg";
import beansVideo from "./contentPrototypeMedia/beans-demo.mp4";
import pourVideo from "./contentPrototypeMedia/pour-demo.mp4";
import catalog from "../../backend/app/voice_catalog.json";

const samples = import.meta.glob<string>("./contentPrototypeMedia/*.wav", { eager: true, query: "?url", import: "default" });
export const studioVoices = catalog.map(voice => ({ ...voice, url: samples[`./contentPrototypeMedia/${voice.sample}`] }));
export const studioAssets = [
  { id: "beans", name: "备豆与准备", notes: "咖啡豆倒入磨豆机", image: beans, video: beansVideo },
  { id: "pour", name: "缓缓注水", notes: "热水注入滤杯", image: pour, video: pourVideo },
  { id: "grounds", name: "浸润细节", notes: "咖啡粉浸润的静态画面", image: grounds, video: undefined },
];
export const studioScripts = [
  { title: "从备豆开始", angle: "制作过程", text: "先把咖啡豆倒入磨豆机。再把热水缓缓注入滤杯。", asset: 0 },
  { title: "看见手冲过程", angle: "动作特写", text: "将热水缓缓注入滤杯。近看咖啡粉慢慢浸润。", asset: 1 },
  { title: "慢一点，看细节", angle: "细节展示", text: "看咖啡粉在热水中浸润。记录一杯咖啡的制作过程。", asset: 2 },
  { title: "先准备，再注水", angle: "步骤说明", text: "从咖啡豆开始准备。把热水缓缓注入滤杯。", asset: 0 },
  { title: "一次手冲记录", angle: "过程记录", text: "近看咖啡豆的准备。再看热水缓缓注入滤杯。", asset: 1 },
];
