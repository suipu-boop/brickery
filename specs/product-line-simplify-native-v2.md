# 产品线简化 + 完全原生 app（v2）

> 状态：已拍板主体（2026-08-29 随朴）｜待确认：积木名单细节
> 背景：2026-08-16 拍板「独立 app + 内嵌 web（WKWebView）」（specs/native-webview.md）运行两周后，稳定性不达标：入口混乱、缓存反复、刷新不出内容。用户决定整体大改：**完全 app、剔除积木工坊、简化产品线**。

## 一、为什么内嵌 web 形态不稳定（复盘）

| 问题 | 根因 |
|---|---|
| app 打开显示引导页、卡"检测中" | 壳启动加载 18766 setup_wizard，检测逻辑卡住不跳 18767 |
| 界面刷新不出来内容 | WebView 缓存（WebKit NetworkCache）+ Safari 缓存双缓存打架，旧页面清不掉 |
| 两个界面并存 | 壳内 WebView 与 Safari 各自打开同一服务，界面归属混乱 |
| 多进程脆弱 | 壳 + ipc + setup_wizard + chat_ui 四个进程，任一时序竞争即异常 |
| 依赖本地 HTTP 服务 | 前端资源从 127.0.0.1 端口拉取，端口/进程/缓存任一异常即白屏 |

**结论：不稳定来自「浏览器内核 + 本地 HTTP 服务 + 多进程」整条链路，不是单个 bug。**

## 二、目标形态：完全原生 app

- **SwiftUI 原生界面**，直连 IPC（127.0.0.1:18765，JSON 行协议），不依赖任何 web 服务。
- 窗口 / Dock / 菜单栏 / 引导 / 设置全原生。
- **不再启动 setup_wizard（18766）、chat_ui（18767）服务进程**；app 只拉起 IPC 一个后端进程。
- 积木 UI 全部**原生重写**（SwiftUI view），废除 web 动态分区 / view schema 渲染。
- 恢复并升级 2026-08-16 作废的 native-app.md 思路（Shadeling 原生 SwiftUI app 迁入，24 个 .swift），裁剪范围按「完全 app」重新界定。

### 界面归属（已拍板）

| 界面 | 形态 | 来源 |
|---|---|---|
| 聊天 / 会话 | SwiftUI 原生 | 直连 IPC |
| 安装引导 | SwiftUI 原生 OnboardingView | 八家预设（native-app.md 已核实） |
| 设置 / 状态 | SwiftUI 原生 | 直连 IPC |
| 基础功能（原 19 小积木） | SwiftUI 原生，收进底座 | 直连 IPC |
| 积木 UI（PPT 加工台等） | SwiftUI 原生重写 | 原生 view 注册机制 |
| 浏览器 / web 形态 | 彻底废弃 | — |

## 三、产品线简化（已拍板）

### 保留（3 个核心）

| 仓库 | 角色 | 说明 |
|---|---|---|
| **Shadeling**（app） | 完全原生 app 本体 | 界面 + 壳 + 分发 |
| **brickery** | 内核 runtime | IPC / 技能 / 积木宿主，specs 与开发文档随仓库 |
| **brick-vault**（shadeling-bricks） | 积木库内容 | 积木生产与存放，随 app 内置快照 |

### 剔除（1 个）

| 仓库 | 处理 |
|---|---|
| **brickery-workbench**（积木工坊） | 从产品线剔除，仓库归档冻结（不删除历史） |

### 归并 / 内部化（3 个）

| 仓库 | 处理 |
|---|---|
| **brickery-factory**（加工厂） | 内部开发工具，不对外发布，不占产品线 |
| **brickery-meta**（导航元仓库） | 导航/索引文档并入 brickery 仓库 docs/，仓库归档 |
| **shadeling-skill-repo** | 待确认角色；若为技能源码则并入 brick-vault |

## 四、积木体系（保留，收口）

- **积木体系不废除**：brickery 内核保留积木宿主（IPC / 技能 / 契约），brick-vault 保留为积木库。
- **原生 UI 积木**（界面原生重写，直连 IPC）：
  - **ppt-studio**（PPT 加工台）：全功能 PPT 生成，核心大积木。
  - **vault**（文件柜/资产中枢）：本地资产增删查、检索、OCR 入库、网页快照入库，原生文件柜界面。
- **工具型积木**（无独立 UI，收进底座工具层，能力供 UI 积木与聊天调用）：
  - **docwrite + document-writer**（文档生成一组）：docwrite 为积木声明（ToolBrick，provides_tool=DocWrite），document-writer 为技能包（LLM 内容决策指令），配套生成 docx / xlsx / pptx，六套模板，纯 stdlib 零 token；**因支撑 PPT 生成链路必须保留**。
- **high-config-doc（DocWritePro）—— 冻结**：高配文档引擎积木，运行时下载 ~193MB editor_sdk 做复杂排版，违背简化/稳定方向，本轮不保留、不随 app 分发；内核实现（docwrite_pro.py / edsdk_pro.py）保留不删，留作将来评估。
- **demo-studio**（平台 UI 验证）：待确认是否作为正式积木（建议仅开发期工具）。
- **其余基础能力积木收进底座**：ax / backup-restore / browser / code-quality-chain / doctor / engine-api / engine-local / feishu / hello-marvis / mcp / meeting-minutes / multi-agent / rules / scheduler / skill-library / telegram / visualize —— 作为底座原生功能，不再以积木形式存在。
- **shadeling-skill-repo 归并**：技能市场源（document-writer / meeting-minutes / pdf-extractor / code-reviewer）整体并入 brick-vault，仓库归档。
- **后续积木生产**：原生 UI 框架成型后增量生产，新积木一律原生 UI。

## 五、风格形态规范（已拍板 2026-08-29）

### 定位：延续"陶土工坊"品牌

- 暗色为主，暖陶土色（陶土橙 / 砖红）做强调色，与现有 status 兜底页、ChatView 一脉相承，不另起炉灶。
- 身份资产已存在，用户认知不割裂；重画一套反而增加工作量与不确定性。

### 系统原生质感（SwiftUI 天然优势）

- 背景用系统材质（`ultraThinMaterial` / 分层背景），窗口磨砂感，不靠贴图。
- 图标全用 SF Symbols（原生、免费、自适应），仅在积木头像 / 品牌位用自绘图形。
- 控件直接用系统组件（Button / TextField / Toggle / Stepper），吃满 HIG 交互，不做自定义重绘。
- 配色集中在 Asset Catalog 的 Color Set，全局引用，换肤只改一处。

### 形态：单窗口 + 三栏结构

- 左侧边栏：会话列表 / 积木入口（ppt-studio、vault）/ 设置。
- 中栏：聊天主区（气泡、输入框、附件、产物卡片）。
- 右侧可选：当前会话上下文 / 积木操作面板。
- OnboardingView 保持八家预设卡片式（native-app.md 已核实）。
- 积木 UI 走原生 view 注册，每个积木一个独立面板，形态统一。

### 动效与状态反馈

- 转场用系统 `.transition` + 弹簧动画，克制（200-300ms）。
- 加载态用骨架屏（`Redacted`），替代 web 时代"检测中"转圈，直接消掉"卡检测中"体验。
- 错误态给原生 Alert / Sheet，不复刻 web 弹窗。

### 规避"web 迁移感"（重点）

- 不用圆角卡片堆叠冒充网页，用系统列表 + 材质分层。
- 不保留任何浏览器痕迹（地址栏、刷新按钮、加载进度条）。
- 字号 / 行距 / 间距遵循 HIG 标准值，不用 web 时代的 14px 密集排版。

## 六、里程碑

| 阶段 | 内容 | 验收 |
|---|---|---|
| M1 产品线瘦身 | 归档 workbench / meta / factory，明确三核心 | 产品线文档更新，仓库归档冻结 |
| M2 原生底座落地 | SwiftUI app 迁入，原生引导 + 聊天直连 IPC | 双击 .app = 原生窗口，无浏览器、无 web 服务 |
| M3 基础功能原生化 | 19 小积木按优先级分批收进底座（原生 UI） | 基础能力全走原生界面，无 web 依赖 |
| M4 积木 UI 原生框架 | 原生 view 注册机制 + ppt-studio / demo-studio 原生重写 | 积木 UI 原生渲染，直连 IPC |
| M5 发布闭环 | 签名公证、DMG、CI 单测 | 可分发、可自动构建 |

## 七、待确认

1. **「两个大积木」名单确认**：ppt-studio + demo-studio 是否即指这两个？demo-studio 是平台验证积木，正式产品中是否保留（还是仅开发期使用）？
2. **19 个基础积木收进底座的优先级**：哪些第一批原生落地（建议：聊天周边 / 文件 / 浏览器 / 定时任务 / 备份），哪些可后置？
3. **brick-vault 现有 21 个积木目录**：除两个大积木外，其余目录是否冻结归档（不再维护），随底座内置快照只取必要能力？
