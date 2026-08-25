# 生成 agent Web 界面 UI 重塑计划（陶土工坊方向）

> 状态：待审阅（2026-08-25，仅计划，未动代码）
> 范围：brickery runtime — chat_ui.py（聊天主界面）、setup_wizard.py（安装引导）

## 背景

用户反馈当前界面"设计效果一般"。经诊断，两个界面呈现典型的 AI 生成风格指纹（AI Slop），缺乏品牌记忆点与设计层次。

## 一、现状诊断

| 特征 | 现状 | 问题定性 |
|------|------|----------|
| 配色 | GitHub 暗黑 `#0d1117` + 霓虹橙 `#ff7a18` + 青 `#39c5cf` | 暗底霓虹橙青是 AI 界面最大指纹 |
| 字体 | 全站等宽（SF Mono / JetBrains Mono） | 等宽字当"技术感"是偷懒表达 |
| 布局 | 大量 .card 套 .card、边框线分割 | 缺乏节奏，千篇一律 |
| 背景 | 青色网格线（24px） | 经典 AI slop 元素 |
| 状态 | 交互元素 hover 基本有，但 focus/disabled/active 不完整 | 五态不齐 |
| 弹窗 | chat_ui.py 残留约 19 处原生 `confirm()/prompt()/alert()`，在 App 壳 WKWebView 中被桥接为 macOS 原生 NSAlert（系统浅色样式），与暗色 UI 格格不入 | 弹窗体系双轨：自写 modal 组件与系统 NSAlert 并存 |
| 窗口明暗 | NSWindow 使用默认 macOS 标题栏（亮色/跟随系统），webview 内容为暗色，顶部一条亮色与主体割裂 | 壳层未做深色适配 |

## 二、设计方向：陶土工坊（Terracotta Workshop）

品牌锚点：Brickery = 积木 / 砖 / 工坊。取"烧砖的陶土"为视觉母题——温暖、实在、有工匠质感，天然区别于霓虹暗黑的 AI 审美。

**记忆点**：一块陶土砖色的大色块 + 工坊标签式的编号排版（ENGINE·01 / 02 / 03）。

### 2.1 设计原则

1. 暖调一切：深色底带陶土色相（非蓝黑），中性灰全部向品牌色相偏暖。
2. 少卡片：用背景分区、留白分组代替卡片堆叠；保留卡片处用柔影不用粗边框。
3. 字体层次：标题粗无衬线（系统强字重）+ 正文系统字体，等宽仅保留给代码/路径。
4. 五态齐全：每个可交互元素设计 default / hover / active / focus / disabled。
5. 动效克制：只用 transform + opacity，ease-out 曲线，尊重 prefers-reduced-motion。

### 2.2 配色系统（OKLCH）

```css
:root {
  /* 底色：暖调深棕黑（非蓝黑） */
  --bg: oklch(0.18 0.015 45);        /* 深陶土黑 */
  --panel: oklch(0.22 0.018 45);     /* 面板 */
  --panel2: oklch(0.26 0.02 45);     /* 次级面板 */

  /* 文字：暖白（非纯白） */
  --ink: oklch(0.92 0.01 80);        /* 主文字 */
  --dim: oklch(0.68 0.02 60);        /* 次级文字，带暖色相 */

  /* 主色：陶土砖红（非霓虹橙） */
  --accent: oklch(0.66 0.15 45);     /* 陶土红 */
  --accent-strong: oklch(0.72 0.17 45);
  --accent-soft: oklch(0.66 0.15 45 / 0.12);

  /* 辅助色：低饱和、暖调 */
  --ok: oklch(0.72 0.12 145);
  --err: oklch(0.62 0.16 25);
  --warn: oklch(0.78 0.12 85);

  /* 线条：柔化，弱对比 */
  --line: oklch(0.3 0.015 45);
}
```

禁用：纯黑 #000 / 纯白 #fff / 青霓虹 / 蓝紫渐变。

### 2.3 字体系统

| 用途 | 字体 | 说明 |
|------|------|------|
| 标题/品牌 | 系统强无衬线（-apple-system 700/800，苹方粗体） | 不用全站等宽 |
| 正文 | -apple-system / SF Pro Text 常规 | 字号 12-14px，行高 1.6 |
| 代码/路径/日志 | SF Mono / Menlo 保留 | 仅此场景用等宽 |
| 数字编号 | 等宽数字（tabular-nums） | 工坊编号感 |

字号采用 clamp() 流式缩放，层级用"字号+字重+颜色+间距"组合，不单靠字号。

### 2.4 布局与组件改造点

**chat_ui.py（聊天主界面）**
- 侧边栏：宽度保留 220px；去掉纯边框线，改用背景色差分区；导航项 active 用左侧陶土色块 + 底色弱化。
- 顶栏：去掉 2px 橙色粗边框，改为背景分区 + 底部 1px 暖色细线；标题编号化（ENGINE · 01 记忆柜）。
- 卡片：.card/.section-card 改柔影（box-shadow 双层弱影）替代边框；减少嵌套。
- 聊天区：消息气泡去边框，用背景色差 + 间距分组；用户/助手用不同暖调底色。
- 按钮：五态齐全；主按钮陶土红实底，次按钮透明+描边。
- 弹窗：.modal-box 保持（必要交互），圆角 10px 改 8px，阴影加深；遮罩暖调。
- 弹窗体系统一：**全量替换残留的原生 `confirm()/prompt()/alert()` 调用**（chat_ui.py 约 19 处，涉及会话删除、批量删消息、重命名、技能/工具触发、导入等），一律改走自写 appConfirm/appPrompt/appAlert；排障时 grep 确认 `confirm(`/`prompt(`/`alert(` 零残留，杜绝触发壳层 NSAlert 桥接。
- 开关/标签/列表项：全部过一遍五态与配色映射。

**setup_wizard.py（安装引导）**
- 步骤指示器：当前"长方形灰块"改为"工坊编号步骤条"（01 02 03 04，陶土色激活 + 连接线）。
- 卡片：柔影化；header 去掉橙色粗边框，改为品牌字标 + 暖色细线。
- 输入框：聚焦态陶土色描边 + 微光晕（focus ring 可见）。
- 身份页（第三步）：输入区分组化，用留白而非边框。
- 按钮：统一五态；ghost 按钮描边改陶土色。

### 2.5 壳层窗口明暗统一（app/Sources/BrickeryApp/main.swift）

webview 暗色内容 + 系统默认亮色标题栏 = 顶部亮条割裂。需在壳层消除：

- `window.styleMask` 增加 `.fullSizeContentView`，`window.titlebarAppearsTransparent = true`，让 webview 内容延伸至标题栏区域，暗色底铺满整个窗口。
- `window.appearance = NSAppearance(named: .darkAqua)` 固定深色外观，避免跟随系统亮色模式。
- 窗口圆角/阴影保留系统默认；注意 traffic light（红绿灯）按钮在暗色背景上的可见性，必要时给标题栏左侧留出安全间距（webview padding-top 或 CSS `env(titlebar-area-*)` 兜底）。
- setup_wizard / chat_ui / 工作台 / 加工厂各窗口统一处理。

### 2.6 动效规范

- 时长：过渡 120-180ms；出现/消失 200-300ms。
- 曲线：cubic-bezier(0.16, 1, 0.3, 1)（ease-out-quart 近似）。
- 只动 transform / opacity；禁 bounce / elastic。
- 全局尊重 `prefers-reduced-motion`。
- 状态反馈 <100ms（hover 即时变色）。

## 三、实施步骤（获批后执行）

1. **CSS 变量层先行**：两文件 :root 替换为 OKLCH 陶土色板（不影响 HTML 结构，改动可控）。
2. **chat_ui.py 分批改造**：侧边栏/顶栏 → 卡片与列表 → 聊天区 → 弹窗（含原生 confirm/prompt/alert 全量替换）→ 组件五态 → 动效。
3. **setup_wizard.py 改造**：步骤条 → 卡片/输入 → 身份页。
4. **壳层窗口明暗统一**：main.swift 标题栏透明化 + 深色外观，重编译 App。
5. **验证**：py_compile 语法检查 → 同步安装目录 → 重启 chat_ui / setup_wizard → curl 探活 + 截图目检；grep 确认原生弹窗零残留。
6. **回归**：重点走查记忆柜抽屉打开/删除、固定核编辑、身份页保存（涉及 JS 逻辑，避免 CSS 改动误伤）。

## 四、验收标准

- 通过 AI Slop Test：看不出"AI 生成"的第一印象。
- 交互元素五态齐全（default/hover/active/focus/disabled）。
- 文字对比度 WCAG AA（4.5:1）。
- 触控目标 ≥ 44px（桌面端可放宽至 32px）。
- 暗色模式统一暖调，非简单反色。
- 弹窗全部为自写 modal 组件样式，grep 无原生 `confirm(`/`prompt(`/`alert(` 残留，不再出现系统 NSAlert。
- 窗口顶部无亮色割裂条：webview 暗色底延伸至标题栏，深色外观固定。
- 动效尊重 prefers-reduced-motion。

## 五、不做的事

- 不做玻璃拟态（glassmorphism）堆砌。
- 不引入霓虹渐变、青色发光、蓝紫渐变。
- 不引入外部字体 CDN（离线环境，系统字体即可）。
- 不改功能逻辑与后端接口，纯 UI 层。
