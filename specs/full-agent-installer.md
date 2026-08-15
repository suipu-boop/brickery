# 方案：完整 agent + 首次安装引导

## 背景与目标

用户反馈：brickery 产出的 full-agent 安装包"根本不是我们要的 agent"。

参照物：桌面上 workbuddy 做的 `Shadeling-0.3.37.dmg`（Shadeling.app），其形态为
**自包含 .app，包含所有底座 + 所有小积木，双击打开即完整可用**，且带安装引导
（.docs 的 README.html / MODEL_SETUP.html 引导配置模型 API）。

目标：重做一版**完整 agent**，满足：

1. **全量积木**：27 个积木全部组装（所有底座 + 所有小积木），不是 15 个。
2. **安装引导**：首次启动进入**交互式安装引导**，引导配置 API Key / 引擎 /
   连接器 / MCP 等，配置完成后 agent 才真正可用；不是直接弹状态页。
3. **积木真正激活**：修复打包态技能列表为 0 的缺口，积木注册进内核
   （SkillRegistry / EngineRouter / MemoryHost / ToolRegistry / Gateway）。

## 现状差距

| 维度 | Shadeling.app（参照） | full-agent（现状） |
|---|---|---|
| 积木/技能 | builtin_skills(ax/browser/visualize) + 记忆全家桶 + 连接器 + 全部内核 | 15/27 积木，技能列表为 0（未激活） |
| 打开形态 | 完整可用 agent + 安装引导 | 纯后台服务，双击只弹状态页 |
| 底座 | 完整 runtime（ipc/loop/sessions/skills/tools/connectors/memory） | 精简 runtime，无 builtin_skills/memory/connectors |

## 方案设计

### 〇、目标形态（功能全面的 agent，参照 Shadeling 完整形态）

产出的 agent 打开后是**功能全面的完整 agent**，全都要：

1. **聊天界面**：本地 web 聊天界面（工坊蓝图风，复用 brickery web 技术栈），
   能直接对话、操作，不是纯后台服务。
2. **安装引导**：首次启动引导配置 API/引擎（底座固有能力）。
3. **全量积木**：27 个积木全部组装，技能真正激活。
4. **记忆系统**：memory 全家桶（core/portrait/smol/cabinet/suggest/...）。
5. **连接器**：飞书/Telegram 可配可用。
6. **工具**：ax/browser/docwrite/visualize 等全部可用。

### 一、完整组装（全量 27 积木）

用 `assembler.assemble()` 全量选择 27 个积木，拓扑序自动展开，校验依赖/冲突/资源。
积木清单（brick-vault）：

- 引擎：engine-local, engine-api
- 记忆：memory-core, memory-portrait, memory-fixed-core, memory-cluster,
  memory-cooccurrence, memory-suggest, memory-consolidation, memory-cabinet, memory-smol
- 连接器：feishu, telegram
- 动手：ax, browser
- 文档：docwrite, high-config-doc
- 服务：vault
- 内置：code-quality-chain, meeting-minutes, multi-agent, scheduler, rules, mcp,
  skill-library, doctor, backup-restore, visualize

### 二、安装引导（底座固有能力，非出包临时拼装）

**原则**：安装引导是底座 runtime 的固有能力，从 Shadeling 搬过来时就该在底座里。
任何 brickery 产出的 agent 都自动带安装引导，不因出包流程而缺失。

**底座已预置**：`brickery/runtime/config.py` 的 `EngineConfig`（backend=api/local 双后端、
api_url/api_key/api_model、多模型预设 profiles）已从 Shadeling 原样继承，引擎配置机制
无需重造。

**缺口**：Shadeling 的安装引导（`.docs/README.html` + `MODEL_SETUP.html`）未随底座搬入
brickery runtime。需补进底座：

- 新增 `brickery/runtime/setup_wizard.py`（底座模块）：提供安装引导页（本地 HTML，
  工坊蓝图风）+ 配置写入 config.json 的接口。内容转换自 Shadeling 的 MODEL_SETUP.html：
  网络 API 八家厂商预设（火山方舟/腾讯混元/DeepSeek/通义千问/智谱/Kimi/OpenAI/xAI，
  一键填端点+模型，只填 Key）、自定义 Coding Plan、其他厂商普通 API、本地 GGUF、
  验证是否配置成功。
- `ipc.py` 启动时检测：数据目录无 `config.json` 或未配置引擎 → 打开安装引导页 →
  用户填 API Key 等 → 写 config.json → 启动完整 agent；已配置则直接启动 + 状态页。
- 引导页/状态页/README 等静态资源随底座打包（`_bundle_runtime` 自动带上），
  任何 agent 出包即含安装引导。

### 三、积木激活链路修复（内核改动）

`brickery/runtime/ipc.py` 启动时：

1. 扫描 `home/bricks/*.brick.json`（打包态快照）。
2. 按形态构造适配器（复用 `brick_runtime.build_brick`）：
   - PromptBrick → SkillRegistry.register（ax/browser/visualize/code-quality-chain/...）
   - EngineBrick → EngineRouter.set_engine（engine-local/engine-api）
   - MemoryBrick → MemoryHost.install_kind（memory-*）
   - ConnectorBrick → Gateway.on_start（feishu/telegram，缺凭据则 disabled-by-config）
   - ToolBrick → ToolRegistry.register（docwrite/high-config-doc）
   - ServiceBrick → VaultStore 挂载（vault）
3. 激活结果写入状态页/health，skill_list 返回非 0。

**注意**：打包态 runtime 需补 `builtin_skills/` 目录（ax/browser/visualize 的
skill.json + 二进制），否则 PromptBrick 无内容可注册。

### 四、聊天界面（本地 web，底座能力）

**缺口**：brickery 底座无聊天界面（web/index.html 是"造 agent 的工坊"，非聊天界面）；
Shadeling 的 GUI 是原生界面，未随底座搬入。

**方案**：新增底座模块 `brickery/runtime/chat_ui.py`，提供本地 web 聊天界面
（工坊蓝图风，与状态页/引导页风格统一）：

- 本地 web 服务（复用 IPC 端口或独立端口），页面含：对话输入框 + 消息列表 +
  会话管理 + 引擎状态。
- 对话经 IPC 调主循环（loop.py），走引擎路由（API 为主 + 本地 GGUF 兜底）。
- 双击打开 → 未配置引擎则进安装引导 → 配置完成 → 打开聊天界面。
- 静态资源随底座打包（`_bundle_runtime` 自动带上），任何 agent 出包即含聊天界面。

### 五、出包与分发

- 重出包到 `~/.brickery/agents/<name>/`，cp 到 `/Applications/<name>.app`。
- 用 `dmg.py` 重打 DMG 到桌面，布局含 app + Applications 软链 + 隐藏 .docs
  （安装引导 README.html / MODEL_SETUP.html，参照 Shadeling）。

## 改动点清单

1. `brickery/runtime/ipc.py`：启动时扫描 bricks/ 激活积木（核心改动）。
2. `brickery/produce.py`：launcher 增加"未初始化 → 引导流程"分支；
   `_bundle_app` 补 builtin_skills/ 目录；`_status_page` 增加引导入口。
3. 新增引导服务模块（如 `brickery/runtime/setup_wizard.py`）：本地引导页 +
   配置写入 config.json。
4. `brickery/dmg.py`：DMG 布局补 .docs 安装引导文档。

## 验证

1. 全量 27 积木 assemble 通过（无依赖/冲突/资源错误）。
2. 首次启动（清空数据目录）→ 打开安装引导页，配置 API Key → 写入 config.json。
3. 配置完成后启动完整 agent → skill_list 非 0，health 各子系统正常。
4. 二次启动（已初始化）→ 直接启动 + 状态页，不再进引导。
5. 重打 DMG → 从 dmg 复制 .app 模拟双击验证全流程。

## 待确认

- [ ] 安装引导是否做成**独立引导服务 + 本地页面**（推荐），还是复用状态页内嵌表单？
- [ ] 引擎配置是否默认"网络 API 为主 + 本地 GGUF 兜底"（同 Shadeling 策略）？
- [ ] 全量 27 积木是否包含 engine-api（需用户填 API Key）与 high-config-doc（需二进制）？
