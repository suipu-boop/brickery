---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_a6d45289994d11f18cca525400e6dd8f
    ReservedCode1: hbfgADjgVWs6tdxEApl4F1VCRKvtRxVWfvKXtw89T7UlNIVujzqWedKHmal3rTsfyave0bNr7iSiLWiTLjGpkUJswEFmeYKRBpGy+phFj1ysw8jVUHGKvIjKvGxPLP0GdBBBeifBLiYV9bJZRB5ykntTQbRxRKnqN9KfI3o/wRTzxZ7Uh5ygy/EOiuU=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_a6d45289994d11f18cca525400e6dd8f
    ReservedCode2: hbfgADjgVWs6tdxEApl4F1VCRKvtRxVWfvKXtw89T7UlNIVujzqWedKHmal3rTsfyave0bNr7iSiLWiTLjGpkUJswEFmeYKRBpGy+phFj1ysw8jVUHGKvIjKvGxPLP0GdBBBeifBLiYV9bJZRB5ykntTQbRxRKnqN9KfI3o/wRTzxZ7Uh5ygy/EOiuU=
---

# 产出 agent 原生界面：搬入 Shadeling 原生 SwiftUI app（积木平台组装逻辑下）

> **已作废（2026-08-16）**：用户改走「独立 app + 内嵌 web（WKWebView）」形态，
> 见 specs/native-webview.md。本文件保留供追溯。

> 2026-08-16 随朴 落盘。回应质疑：对话页面为何是 web 而非独立 app 内原生界面。
> 根因：brickery 迁移时只搬了 Python 内核，未搬 Shadeling 原生 SwiftUI app，界面用 web 复刻形态。

## 结论

用户质疑成立。Shadeling 的对话界面是**原生 SwiftUI app**（`app/Sources/ShadelingApp/`，
24 个 .swift 文件，NSWindow + NSHostingController，双击开原生窗口），通过 IpcClient
（TCP 127.0.0.1:18765，JSON 行协议）与 Python runtime 通信。brickery 底座界面
（chat_ui.py / setup_wizard.py）是本地 web 服务，产出 .app 后浏览器打开页面，形态不符。

**方案：把 Shadeling 原生 SwiftUI app 作为「底座原生壳」搬入 brickery，在积木平台
组装建设逻辑下使用——组装流程不变（web 工作台选积木 → 产出 .app），产出的 .app
= 底座原生壳 + brickery-runtime + 所选积木快照，双击开原生窗口，对齐 Shadeling 形态。**

## 核心约束：积木平台组装建设逻辑

搬原生 app **不是**把 Shadeling 当成品搬进来，而是把它作为**底座的一个原生界面壳**
（native shell，与 chat_ui/setup_wizard 同级的底座写死能力）。组装建设逻辑完全不变：

```
web 工作台（127.0.0.1:18766）选积木
  → /api/assemble 生成装配清单（agent.json + bricks 快照）
  → /api/produce 产出 .app（原生壳 + brickery-runtime + 所选积木）
  → 双击 .app 开原生窗口（原生壳拉起 IPC，加载所选积木）
```

- 原生壳是**底座能力**，随底座写死（对齐 chat_ui 的定位），不是积木、不进积木清单
- 积木仍是能力单元（bricks），原生壳是界面载体，两者经 runtime 组装
- 产出物形态：独立 .app（原生窗口），但**组装来源仍是积木平台 web 工作台**

## 现状对比

| 项 | Shadeling | brickery 现状 |
|---|---|---|
| 对话界面 | 原生 SwiftUI（ChatView.swift） | web（chat_ui.py 18767） |
| 安装引导 | 原生 OnboardingView.swift（八家正确预设） | web（setup_wizard.py 18766，预设旧/错） |
| 通信 | IpcClient → runtime/ipc.py（18765 JSON 行） | 浏览器 → chat_ui → ipc.py |
| 打包 | build_app.sh：swift build + rsync Python 包 | produce.py：bash launcher + 浏览器开页 |
| 数据目录 | ~/.shadeling（SHADELING_HOME） | ~/Library/Application Support/{name} |

## 关键事实（已核实）

- Shadeling 原生 app：`app/Sources/ShadelingApp/` 24 个 .swift，依赖 NimbusUI（Swift Package）。
  main.swift 的 AppDelegate 启动时 `Launcher.startIpc(port: 18765)` 拉起 Python IPC 子进程
  （`python3 -m runtime.supervisor --port 18765 --home ~/.shadeling`），AppModel(port: 18765)
  经 IpcClient 通信；窗口 920x660，菜单栏常驻，关窗隐藏不退出。
- IpcClient 协议：TCP 127.0.0.1:18765，JSON 行（req_id/method/params），支持流式
  （stream=true，delta/done 帧）。brickery `runtime/ipc.py`（2636 行）有 `_dispatch`，
  与 Shadeling ipc.py 同源，协议天然兼容。
- OnboardingView.swift（610 行）原生引导已含八家厂商**正确预设**（火山
  doubao-seed-2.1-pro-260628、混元 hunyuan-turbos-latest、DeepSeek deepseek-v4-flash、
  通义 qwen3.8-max、智谱 glm-5.2、Kimi kimi-k3、OpenAI gpt-5.5、xAI grok-4.3），
  与 Shadeling 原版 MODEL_SETUP.html 一致——原生引导自带正确预设，web setup_wizard 可弃用。
- brickery 产出链路：produce.py `_bundle_app` 生成 bash launcher，拉起
  IPC(18765)+setup_wizard(18766)+chat_ui(18767)，浏览器打开页面；brickery-runtime 已打包进
  Resources/brickery-runtime/。

## 方案

### 1. 代码迁移（brickery 新增 app/ 目录）

- 新建 `brickery/app/`，把 Shadeling `app/Sources/ShadelingApp/` 迁入，**命名空间改名
  BrickeryApp**（target 名 + 全部 import/引用，彻底去 Shadeling 化），
  `app/Package.swift` + `app/Assets/AppIcon.icns` 一并迁入。
- 依赖 NimbusUI **离线裁剪**：已核实仅 `Design.swift` 用 2 个修饰符
  （NimbusShadowModifier 阴影 + NimbusGradientBorderModifier 渐变边框），用系统 SwiftUI
  的 `.shadow()` / 自绘渐变边框替换，去掉 NimbusUI 依赖，产出 .app 编译不依赖网络。
  **不影响 web 组装**：web 组装（选积木/装配清单）是纯 Python 流程，不碰 Swift 编译。
- 裁剪范围**对齐当前 19 块积木局面**：保留与积木/底座能力对应的界面
  （聊天/引导/设置/医生/记忆柜/技能库/保险库/任务/图谱画布），**去掉与积木无关的
  WorkbenchView 组装台**（组装在 web 工作台，产出 agent 内不需要）；VaultOCR 视 vault
  积木能力定。逐 View 核对 IPC method 与 brickery ipc.py `_dispatch` 兼容性，
  缺失的 method 在 ipc.py 补实现。

### 2. 通信复用（不改协议）

- 原生 app 经 IpcClient 直连 brickery `runtime/ipc.py`（18765），协议同源零改动。
- 原生 app 自带界面渲染，**不再需要** chat_ui.py（web 聊天）与 setup_wizard.py（web 引导）。

### 3. 引导复用（原生 OnboardingView，8 步完整保留）

- 首次启动（数据目录无 config.json）→ 原生 OnboardingView 引导，八家正确预设直接可用。
- OnboardingView 共 8 步完整保留：0 欢迎+选后端 ｜ 1 后端配置（大厂预设/本地）｜
  2 认识我们 ｜ 3 空闲记忆整理 ｜ 4 飞书连接 ｜ 5 Telegram 连接 ｜
  **6 数据与备份（用户自选「备份文件夹」+「产出文件夹」）** ｜ 7 完成。
- 其中 step 6 即用户要求的"产出物自选文件夹"引导页：产出文件（文档/表格等）落用户自选
  的产出文件夹，备份落自选备份文件夹。
- 配置写 config.json（与 web 引导同路径，兼容既有数据）。

### 4. 打包改造（produce.py `_bundle_app`）

- 编译：`swift build -c release --disable-sandbox`（对齐 build_app.sh），原生可执行文件放
  `Contents/MacOS/{name}`。
- 内嵌：brickery-runtime 进 `Contents/Resources/brickery-runtime/`（沿用 `_bundle_runtime`）。
- Info.plist：`CFBundleExecutable={name}`、`NSPrincipalClass=NSApplication`、AppIcon 集成。
- 数据目录参数化：Launcher.swift 的 home 路径由写死 `~/.shadeling` 改为读
  `~/Library/Application Support/{name}`（经 Info.plist 或环境变量注入）。
- 原生 app 启动时自行拉起 IPC（对齐 Launcher.startIpc），bash launcher 不再需要。

### 5. 保留项

- status.html / run.sh / web 工作台（127.0.0.1:18766）保留：web 工作台是组装台，与产出 app 形态无关。
- chat_ui.py / setup_wizard.py 保留在底座（开发态/无 Swift 环境兜底），产出 app 不再使用。

## 已拍板（2026-08-16 用户确认）

1. **命名空间**：改名 BrickeryApp（target 名 + 全部 import/引用，彻底去 Shadeling 化）。
2. **依赖**：离线裁剪 NimbusUI（仅 Design.swift 2 处修饰符，系统 SwiftUI 替换），
   产出 .app 编译不依赖网络；不影响 web 组装（纯 Python 流程不碰 Swift）。
3. **裁剪范围**：对齐当前 19 块积木局面——保留与积木/底座能力对应的界面，
   去掉与积木无关的 WorkbenchView 组装台。
4. **数据目录**：保持 `~/Library/Application Support/{name}`；OnboardingView 8 步完整保留，
   含 step 6「数据与备份」用户自选备份/产出文件夹引导页。

## 验收标准

- 重新 produce 产出 .app，双击打开 → **原生窗口**（非浏览器），直接进入聊天界面
- 首次启动（无 config.json）→ 原生引导页，八家厂商预设正确，配置后写 config.json
- 二次启动 → 直接进聊天页，可对话（走 18765 IPC）
- 关窗隐藏不退出，菜单栏常驻；退出后无孤儿进程
- 单测 266 passed 无回归

## 实施步骤

1. 落盘本方案，用户确认待拍板项
2. 迁入 app/ 目录（24 个 .swift + Package.swift + AppIcon）
3. 改 Launcher.swift 数据目录参数化
4. 改 produce.py `_bundle_app`：swift build + 原生壳打包
5. 逐 View 核对 IPC method 兼容性，补 ipc.py 缺失 method
6. 重新 produce 产出 agent，双击验证原生窗口 + 引导 + 聊天
7. 重打 DMG，全量单测
*（内容由AI生成，仅供参考）*
