---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_283a8c40a39911f1abe1525400e6dd8f
    ReservedCode1: PdrQvvlDkjoMwa1BmRwyrGZYllreRZzoq5k+x9OFuzvjKsMfxTbH3d2hVm4r/npspZ6kJZv5+RzQavxdVfqulNeIPyhnHNEhrhpAtgjjeuwCnoehsGISB66e/EKnliTZNJganTqFp9gPfvPXTmwhX9UQ021qirJ5Jm9dE3jybJwgX1v8AmGqpdt15vg=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_283a8c40a39911f1abe1525400e6dd8f
    ReservedCode2: PdrQvvlDkjoMwa1BmRwyrGZYllreRZzoq5k+x9OFuzvjKsMfxTbH3d2hVm4r/npspZ6kJZv5+RzQavxdVfqulNeIPyhnHNEhrhpAtgjjeuwCnoehsGISB66e/EKnliTZNJganTqFp9gPfvPXTmwhX9UQ021qirJ5Jm9dE3jybJwgX1v8AmGqpdt15vg=
---

# M2 原生底座 · 实施 specs

> 状态：**全部拍板（2026-08-29）｜M2 实施中**
> 依据：product-line-simplify-native-v2.md（第五章风格规范）、handoff-native-app-v2.md、native-app.md（作废重启）
> 盘点：file-agent 2026-08-29（只读）+ native-app.md 核实

## 一、盘点事实（2026-08-29）

| 项 | 事实 |
|---|---|
| 工程形态 | Shadeling 仓库为 **SwiftPM 工程**（无 xcodeproj），`app/Package.swift`（tools 6.0 / macOS 14 / 依赖 NimbusUI 0.4.0），可执行 target `ShadelingApp` |
| 源文件 | `app/Sources/ShadelingApp/` **24 个 .swift**：main / AppModel / IpcClient / Launcher / ContentView / OnboardingView / ChatView / UIModels / ModelProfile / ModelProviders / Cabinet / Vault / VaultOCR / Memory / Tasks / Skills / Doctor / Workbench / GraphCanvas / Settings / Design / Localization / NotificationManager + builtin_skills/ax/axctl.swift |
| 引导 | **OnboardingView.swift（611 行）已完整实现**：8 步引导 + 八家正确预设（火山/混元/DeepSeek/通义/智谱/Kimi/OpenAI/xAI），首启闸门 `showOnboarding`，完成写 `~/.shadeling/.onboarded` |
| IPC 客户端 | **IpcClient.swift 已实现**：TCP 127.0.0.1:18765 JSON 行协议，`request` / `requestStream`（delta/done 帧），与 brickery runtime/ipc.py 同源兼容 |
| 进程拉起 | Launcher.swift：`startIpc` 执行 `runtime.supervisor --port 18765`；supervisor 默认**只拉起 `python -m runtime.ipc`**（三层防孤儿 App→supervisor→ipc，health 探活重启，父死自杀）；当前 18766/18767 仍在监听（旧架构未收敛） |
| IPC 服务端 | brickery `runtime/ipc.py`（153KB，~70 方法）：请求 `{"req_id","method","params"}`，响应 `{"ok","data"|"error"}`，流式 delta/done；127.0.0.1 回环绑定，无 token 鉴权 |
| 运行副本 | `/Applications/shadelingmac0.0.1.app` 为旧打包产物（CFBundleExecutable=BrickeryApp，bundle id `dev.brickery.shadelingmac0.0.1`），Resources/brickery-runtime/ 含 python 包 + bin（ax/visualize） |

**结论**：原生聊天、原生引导、IPC 客户端三大件**均已实现**。M2 不是从零写，而是**收敛进程链路 + 按新形态改造 UI 骨架 + 重打包验证**。

## 二、目标

双击 .app = 原生窗口（SwiftUI），无浏览器、无 web 服务；app 只拉起 IPC 18765 一个后端进程；首启原生 OnboardingView（八家预设），二次启动直进聊天；整体形态符合第五章风格规范（暗色陶土工坊 + 原生材质 + SF Symbols + 三栏 + 骨架屏）。

## 三、M2 范围

| # | 动作 | 说明 |
|---|---|---|
| B1 | 代码主阵地确认 | 以 **Shadeling 仓库**为主阵地开发（符合产品线三核心：Shadeling=app 本体），不迁入 brickery/app/（旧组装思路作废）【待拍板】 |
| B2 | 进程收敛 | Launcher 只拉起 supervisor→ipc(18765)；**移除 setup_wizard(18766) / chat_ui(18767) 启动路径**，18766/18767 不再监听；验证退出无孤儿进程 |
| B3 | 首启链路 | 复用 OnboardingView（8 步 + 八家预设）→ 完成写 config + `.onboarded` → 直连 18765 聊天；修正「引导完成后切换直连」逻辑缺口（现 Launcher 无条件拉起） |
| B4 | 数据目录参数化 | home 路径由写死 `~/.shadeling` 改为 `~/Library/Application Support/{appName}`（Info.plist/环境变量注入），兼容既有数据迁移检查 |
| B5 | 三栏 UI 骨架 | 按第五章：左侧边栏（会话列表 / 积木入口 ppt-studio·vault / 设置）、中栏聊天（气泡/输入/附件/产物卡）、右侧上下文面板；背景 `ultraThinMaterial` 分层、SF Symbols、加载骨架屏（Redacted）、转场 200-300ms；砍 WorkbenchView 组装台 |
| B6 | 依赖裁剪 | 沿用 2026-08-16 拍板：NimbusUI 仅 Design.swift 2 处修饰符，用系统 `.shadow()`/自绘渐变替换，**去依赖、离线可编译** |
| B7 | 打包改造 | `swift build -c release` + 组装 .app：可执行 `Contents/MacOS/{name}`、brickery-runtime 进 `Contents/Resources/brickery-runtime/`、Info.plist 关键项（NSPrincipalClass、本地网络 ATS）；**产出 .app 不启动任何 web 服务** |
| B8 | 验证 | 首启引导 → 配置 → 聊天直连 IPC；二次启动直进聊天；只 18765 监听；关窗隐藏、退出无孤儿 |

## 四、明确不做（后续里程碑承接）

- 17 个基础功能原生 UI → **M3**
- ppt-studio / vault 原生面板 + 原生 view 注册机制 → **M4**
- 签名公证 / DMG / CI 单测 → **M5**

## 五、待拍板

1. ~~代码主阵地~~ → **已拍板（2026-08-29）：Shadeling 仓库（/Users/suipu/Dev/Shadeling），不迁入 brickery/app/**。
2. ~~app 命名与 bundle id~~ → **已拍板（2026-08-29）：Shadeling（com.shadeling.app），与仓库/品牌一致。**
3. ~~三栏布局~~ → **已拍板（2026-08-29）：M2 落地基础三栏骨架（左会话/积木入口/设置 + 中聊天 + 右上下文），形态一步到位。**

## 六、验收标准

1. 双击 .app → 原生窗口直接可见（非浏览器、无"检测中"转圈）。
2. 首启（无配置）→ 原生 OnboardingView 八家预设正确，配置后写 config。
3. 二次启动 → 直进聊天页，走 18765 IPC 可正常对话（含流式）。
4. `lsof -i` 仅 18765 监听；无 18766/18767。
5. 关窗隐藏不退出、菜单栏常驻；退出后无孤儿进程。
6. 三栏骨架 + 暗色陶土材质 + SF Symbols + 骨架屏加载态符合第五章规范。

## 七、实施步骤（确认后执行）

1. 确认主阵地与命名 → 2. 进程收敛（B2）→ 3. 首启链路修正（B3）+ 数据目录（B4）→ 4. 三栏骨架与风格（B5）+ 依赖裁剪（B6）→ 5. 打包改造（B7）→ 6. 全链路验证（B8）→ 7. 提交推送 + 更新 specs 状态。
*（内容由AI生成，仅供参考）*
