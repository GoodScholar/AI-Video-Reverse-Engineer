---
name: AI 视频复刻分析器
description: 以胶片检测台为视觉母体的本地视频复刻工作界面
colors:
  signal-rust: "#a83b20"
  signal-rust-deep: "#7f2b18"
  warm-white: "#fffaf1"
  inspection-paper: "#f2f0e8"
  iron-ink: "#202427"
  metadata-gray: "#5c625d"
  divider-gray: "#c7c6bc"
  film-charcoal: "#343938"
  secondary-surface: "#e9e7de"
  focus-bluegray: "#6c8990"
typography:
  display:
    fontFamily: "system-ui, PingFang SC, Hiragino Sans GB, sans-serif"
    fontSize: "clamp(30px, 4vw, 48px)"
    fontWeight: 700
    lineHeight: 1.12
    letterSpacing: "-0.04em"
  body:
    fontFamily: "system-ui, PingFang SC, Hiragino Sans GB, sans-serif"
    fontSize: "16px"
    fontWeight: 400
    lineHeight: 1.7
  label:
    fontFamily: "ui-monospace, SFMono-Regular, Menlo, monospace"
    fontSize: "12px"
    fontWeight: 400
    lineHeight: 1.4
    letterSpacing: "0.08em"
rounded:
  field: "2px"
  control: "3px"
spacing:
  compact: "10px"
  control-x: "17px"
  section-x: "40px"
  touch-target: "44px"
components:
  button-primary:
    backgroundColor: "{colors.signal-rust}"
    textColor: "{colors.warm-white}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 17px"
    height: "44px"
  button-primary-hover:
    backgroundColor: "{colors.signal-rust-deep}"
    textColor: "{colors.warm-white}"
    rounded: "{rounded.control}"
    height: "44px"
  button-secondary:
    backgroundColor: "transparent"
    textColor: "{colors.iron-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.control}"
    padding: "0 17px"
    height: "44px"
  input:
    backgroundColor: "#fffdf7"
    textColor: "{colors.iron-ink}"
    typography: "{typography.body}"
    rounded: "{rounded.field}"
    padding: "0 12px"
    height: "46px"
  project-row:
    backgroundColor: "transparent"
    textColor: "{colors.iron-ink}"
    rounded: "0"
    padding: "13px 12px"
    height: "93px"
  project-mark:
    backgroundColor: "{colors.film-charcoal}"
    textColor: "{colors.inspection-paper}"
    rounded: "0"
    height: "54px"
    width: "72px"
  status-line:
    backgroundColor: "transparent"
    textColor: "{colors.metadata-gray}"
    rounded: "0"
    height: "44px"
  film-rail:
    backgroundColor: "{colors.inspection-paper}"
    textColor: "{colors.iron-ink}"
    rounded: "0"
---

# Design System: AI 视频复刻分析器

## Overview

**Creative North Star: “胶片检测台”**

界面把复杂的视频复刻准备工作呈现为一张安静、精确、可持续使用的本地检片台。低饱和暖灰底承载信息，炭黑文字和细分隔线建立工具感，锈红只标记主操作、错误和胶片导轨。

视觉密度服务于操作而非展示：状态先被看清，项目能够继续，技术依赖不会喧宾夺主。系统不使用装饰性卡片堆叠、玻璃质感或浮夸光效。

**Key Characteristics:**

- 暖灰检片台底色与细线分区。
- 胶片孔带、时间码和等宽编号构成识别性细节。
- 锈红是稀缺的行动与异常信号。
- 平面、克制、无装饰性阴影。
- 桌面宽布局在窄屏下重排，不缩成不可操作的微型界面。

## Colors

色盘来自胶片工作台：暖纸面承载长时间操作，炭黑负责内容，锈红负责必须被看见的动作。

### Primary

- **信号锈红**：主按钮、错误边界、插入线和胶片导轨；只在需要明确行动或注意时出现。
- **深锈红**：主按钮悬停状态，保持同一色相并强化反馈。

### Neutral

- **检片纸面**：全局底色，避免纯白造成的长时间眩光。
- **铁墨黑**：标题、正文和品牌标识的主文字。
- **元数据灰**：时间、计数和解释性次级信息。
- **分隔灰**：顶栏、列表和区块之间的细线。
- **胶片炭黑**：缩略占位和胶片孔带的暗色材料。
- **次级台面**：悬停与项目初始状态的轻微层次。
- **焦点蓝灰**：键盘焦点环和断开状态点，避免与锈红动作混淆。

**The Sparse Signal Rule.** 锈红不用于大面积背景或普通装饰；一个视区内优先只保留一个主行动信号。

## Typography

**Display Font:** system-ui（回退至 PingFang SC、Hiragino Sans GB、sans-serif）  
**Body Font:** system-ui（同一中文工作字体栈）  
**Label/Mono Font:** ui-monospace（回退至 SFMono-Regular、Menlo、monospace）

**Character:** 标题使用紧凑、坚定的系统黑体，正文保持高可读性；时间码、序号和计数切换为等宽字体，形成检片记录的技术节奏。此本地 Operate 工具不依赖外部字体资产。

### Hierarchy

- **Display**（700，`clamp(30px, 4vw, 48px)`，1.12）：首页主命题；负字距仅用于大标题。
- **Section headline**（700，24–28px）：项目区与创建区标题。
- **Item title**（700，17px）：项目名称和空状态标题。
- **Body**（400，16px，1.7）：说明性正文和长句。
- **Label**（400，12px，0.08em）：计数、列表序号与时间码。

**The Timecode Voice Rule.** 只有计时、编号、计数和机器式标记使用等宽字体；普通中文内容不模拟终端。

## Layout

核心内容限制在 1120px 宽的居中工作区。桌面端区块水平内边距为 40px，首页首屏将主命题与单一主操作并列；最近项目在其下形成连续列表。顶栏横跨视口，状态位于右侧。

720px 及以下切换为纵向结构：顶栏、主命题和动作依次堆叠，左右内边距缩至 20px，项目行隐藏非必要序号但保留缩略占位、名称、更新时间和打开图标。所有交互目标保持至少 44px 高。

胶片轨道可以贴在项目集合或项目状态区左缘，但不得占用正文列；窄屏时为其保留明确的 22px 左侧槽位。

## Elevation & Depth

系统不使用阴影。深度由底色变化、1px 分隔线、3px 信号导轨和材质对比表达；悬停只改变台面色或按钮色，不让元素漂浮。

**The Flat Bench Rule.** 静止与交互状态都保持在同一平面，禁止用投影代替层级和边界。

## Shapes

形状以直角和极小圆角为主。输入框使用 2px 轻微圆角，主要按钮使用 3px 圆角；项目行、状态面板、缩略占位和区块保持方正。圆形只用于 7px 环境状态点，胶囊形不作为常规组件语言。

## Components

### Buttons

- **Shape:** 紧凑矩形（3px 圆角），最小高度 44px。
- **Primary:** 信号锈红底、暖白文字、水平 17px 内边距；图标与文字间距 8px。
- **Hover / Focus:** 悬停转深锈红；键盘焦点使用 3px 蓝灰轮廓和 3px 外偏移。
- **Secondary / Link:** 次按钮透明底配细灰边；返回和重试动作用带偏移的下划线表达。

### Cards / Containers

- **Corner Style:** 不圆角。
- **Background:** 默认与检片纸面连续，初始状态和悬停使用次级台面。
- **Shadow Strategy:** 无阴影。
- **Border:** 1px 分隔线；错误或关键状态可以使用锈红边界。

### Inputs / Fields

- **Style:** 暖白输入面、1px 深灰描边、2px 圆角、46px 高。
- **Focus:** 蓝灰外轮廓；插入光标使用信号锈红。
- **Error / Disabled:** 错误紧邻字段并保留可读文案；禁用动作降低不透明度但不改变布局。

### Navigation

顶栏保持单层平面结构，左侧为品牌，右侧为两个独立环境状态。移动端允许状态换行；不折叠为隐藏菜单，因为状态是进入工作的前置信息。

### Project Row

项目行是整行可点击的 93px 工作记录：左侧胶片缩略占位，中部名称与最近更新时间，右侧为等宽序号和打开图标。悬停只使用轻微台面色变化。

### Film Rail

连续胶片轨道由竖向孔带、等宽边码和一条锈红导轨组成。它是项目集合与项目初始状态的签名构件，不应散落到与时间或项目连续性无关的表单控件上。

## Do's and Don'ts

### Do:

- **Do** 用细线、台面色和对齐关系表达层级。
- **Do** 让分析服务与 ComfyUI 状态保持独立、真实且可重试。
- **Do** 将时间、序号和计数交给等宽字体。
- **Do** 保持主按钮、返回和重试等交互至少 44px 高并提供可见焦点。
- **Do** 在后续视频工作台继续沿用胶片轨道和时间码语言。

### Don't:

- **Don't** 引入玻璃拟态、发光边缘、厚重阴影或大圆角卡片。
- **Don't** 把锈红扩展为普通装饰色或让多个主行动互相竞争。
- **Don't** 用单一“在线/离线”概括多个本地依赖的真实状态。
- **Don't** 在未批准资产前加入远程或自托管字体依赖。
- **Don't** 把任务 05 的三栏分析工作台拓扑提前套到项目首页。
