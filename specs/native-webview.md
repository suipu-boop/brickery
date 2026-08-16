# 产出 agent 形态：独立 app + 内嵌 web（WKWebView）

> 状态：已拍板（2026-08-16 用户确认「改成独立 app，内嵌 web」）
> 替代：specs/native-app.md（搬 Shadeling 原生 SwiftUI app 方案，作废）

## 目标形态

产出物是**独立 .app**（NSApplication + WKWebView 内嵌渲染），用户看到独立窗口、
Dock 图标、菜单栏，**无浏览器 UI**（无地址栏/标签页）。界面渲染引擎是 WebKit，
渲染底座已有的 web 界面（chat_ui / setup_wizard），用户感知不到浏览器存在。

对比当前形态（launcher 用 `open` 调起系统浏览器访问本地页面）：
- 当前：浏览器标签页里用，有浏览器 UI
- 新形态：独立 app 窗口，无浏览器 UI，内部 WebKit 渲染

## 界面来源（复用底座，不搬 Swift）

| 界面 | 来源 | 端口 | 说明 |
|---|---|---|---|
| 聊天 | 底座 `brickery/runtime/chat_ui.py` | 18767 | 复用，不改 |
| 安装引导 | 底座 `brickery/runtime/setup_wizard.py` | 18766 | 复用 + **补「自选文件夹」步骤** |

- 引导页当前是单页 API 配置（八家预设 + 保存 config.json），**无自选文件夹步骤**。
  需补一步：用户自选「备份文件夹」+「产出文件夹」（产出文件落自选产出文件夹，
  备份落自选备份文件夹），写入 config.json。
- 首次启动（无 config.json）→ 引导页；已配置 → 聊天页。
- 其他积木能力界面（记忆柜/医生/技能库/保险库/任务/图谱画布）**目前无 web 版**，
  内嵌 web 形态下暂不提供，后续按需补（对齐 19 块积木局面是渐进项，非本次范围）。

## 原生壳（brickery 新增 app/ 目录）

- 新建 `brickery/app/`，Swift 壳工程：NSApplication + WKWebView 窗口，
  命名 **BrickeryApp**（去 Shadeling 化）。
- 壳职责：启动时拉起本地服务（IPC 18765 + setup_wizard 18766 + chat_ui 18767），
  WKWebView 加载对应本地 URL（按 config.json 是否存在决定引导页/聊天页）。
- 无第三方依赖（不拉 NimbusUI），编译自包含，产出 .app 编译不依赖网络。
- 数据目录：`~/Library/Application Support/{name}`（与现状兼容，零迁移）。

## 打包改造（produce.py `_bundle_app`）

- `_bundle_app` 改为：swift build 原生壳 → 组装 .app（壳 + brickery-runtime +
  所选积木快照 + status.html 兜底）。
- 组装流程不变：web 工作台选积木 → /api/assemble → /api/produce 产出 .app。
- 产出物 = 原生壳 + brickery-runtime + 所选积木快照。

## 与纯原生 SwiftUI 方案对比（为何选内嵌 web）

| 维度 | 内嵌 web（本次） | 纯原生 SwiftUI（作废） |
|---|---|---|
| 界面迭代 | 改 HTML/CSS 即生效，不重新编译 | 改界面要改 Swift 重新编译 |
| 视觉自由度 | CSS/JS 高，易做精致效果 | SwiftUI 能做，成本高 |
| 复用底座 | 直接复用 chat_ui/setup_wizard | 要重写/迁移 24 个 .swift |
| 工作量 | 小（壳 + 桥接 + 补引导步骤） | 大（迁源码 + 裁剪 + 核对 IPC） |
| 系统集成 | 弱（菜单栏/快捷键要桥接） | 强 |
| 跨平台 | 同一套 web UI 可复用 | 仅 macOS |

## 验收标准

1. 产出 .app 双击打开为**独立窗口**，无浏览器 UI。
2. 首次启动（无 config.json）→ 引导页，含八家正确预设 + **自选文件夹步骤**。
3. 配置保存后 → 聊天页，可正常对话。
4. 组装流程不变：web 工作台选积木 → 产出 .app 全链路通。
5. 全量单测通过。

## 实施步骤

1. 新建 `brickery/app/` 原生壳工程（NSApplication + WKWebView，BrickeryApp）。
2. setup_wizard.py 补「自选文件夹」步骤（备份/产出文件夹 → config.json）。
3. produce.py `_bundle_app` 改为 swift build + 组装原生壳 app。
4. 重新 produce 产出 agent，验证独立窗口 + 引导（含自选文件夹）+ 聊天。
5. 重打 DMG + 全量单测。
