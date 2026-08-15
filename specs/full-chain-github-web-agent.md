# 方案：整条链路 —— GitHub 拉源 → Web 组装 → 生成合格 agent

## 目标（用户原话）

> 用 web 组装，从 GitHub 拉底座和积木块，生成合格的 agent。
> 这是一整条链路，目标从未改变。

即：**底座 + 积木都从 GitHub 拉取**，经 **web 工作台组装**，产出**对齐 Shadeling 形态的合格 agent**
（聊天界面 + 安装引导 + 全量积木激活 + 记忆 + 连接器 + 工具），最终打 DMG 分发。

## 链路全景

```
[源] GitHub
  ├─ 底座  brickery.git        (https://github.com/suipu-boop/brickery.git)
  └─ 积木  shadeling-bricks.git (https://github.com/suipu-boop/shadeling-bricks.git)
        │  git clone/pull → 本地缓存 ~/.brickery/vault/ 与 ~/.brickery/base/
        ▼
[组装] Web 工作台 (server.py + index.html)
  ├─ /api/sync    从 GitHub 拉取/更新底座与积木（首次 clone，之后 pull）
  ├─ /api/bricks  积木清单（来自 GitHub 拉下的 vault）
  ├─ /api/assemble 校验依赖/冲突/资源 → 拓扑序方案
  └─ /api/produce 产出 agent 包（含聊天界面/安装引导/积木激活）
        ▼
[生成] 合格 agent（对齐 Shadeling 形态）
  ├─ 聊天界面  runtime/chat_ui.py（本地 web 聊天，走 loop 引擎路由）
  ├─ 安装引导  runtime/setup_wizard.py（八家 API 预设 + 本地 GGUF，写 config.json）
  ├─ 积木激活  ipc.py 启动扫描 home/bricks/*.brick.json 注册进内核（技能非 0）
  ├─ builtin_skills 打包态补 ax/browser/visualize（skill.json + 二进制）
  └─ 记忆/连接器/工具 全量随底座打包
        ▼
[分发] dmg.py 打 DMG（.docs 安装引导 + Applications 软链）
```

## 现状与缺口

| 环节 | 现状 | 缺口 |
|---|---|---|
| 源 | 本地 `~/Dev/brick-vault` 硬编码 | 需从 GitHub 拉取/更新底座与积木 |
| 组装 | web 工作台已能选积木→assemble→produce→dmg | 无 GitHub 同步入口 |
| 生成 | 后台服务 + 状态页，技能列表为 0 | 缺聊天界面/安装引导/积木激活/builtin_skills |

## 改动点

### 一、GitHub 拉源（server.py + 新模块）

- 新增 `brickery/web/sync.py`：
  - `sync_vault()`：`~/.brickery/vault/` 不存在则 `git clone shadeling-bricks.git`，存在则 `git pull`。
  - `sync_base()`：`~/.brickery/base/` 不存在则 `git clone brickery.git`，存在则 `git pull`。
  - 返回各自 commit 与更新时间，供前端展示。
- `server.py`：
  - `vault_root` 默认改为 `~/.brickery/vault`（GitHub 拉下的缓存），保留 `--vault` 覆盖。
  - 新增 `POST /api/sync`：调 sync_vault + sync_base，返回结果。
  - `_api_produce` 的底座来源：优先用 `~/.brickery/base` 的 runtime（GitHub 最新），
    本地仓库仅作开发兜底。
- `web/index.html`：顶部加「从 GitHub 同步」按钮 + 源状态展示（底座/积木 commit、更新时间）。

### 二、合格 agent 生成（produce.py + runtime）

1. **安装引导进底座**：新增 `brickery/runtime/setup_wizard.py`
   - 本地 HTML 引导页（工坊蓝图风），内容转换自 Shadeling 的 MODEL_SETUP.html：
     八家 API 预设（火山/腾讯/DeepSeek/通义/智谱/Kimi/OpenAI/xAI）一键填端点+模型、只填 Key、
     自定义 Coding Plan、其他厂商普通 API、本地 GGUF、验证。
   - 写 `config.json` 接口（复用 EngineConfig 结构）。
   - `ipc.py` 启动检测：数据目录无 config.json 或未配置引擎 → 打开引导页；已配置 → 直接启动。
2. **聊天界面进底座**：新增 `brickery/runtime/chat_ui.py`
   - 本地 web 聊天界面（工坊蓝图风），对话经 IPC 调主循环（loop.py）走引擎路由。
   - 双击打开 → 未配置引擎进引导 → 配置完成 → 打开聊天界面。
3. **积木激活修复**：`ipc.py` 启动时扫描 `home/bricks/*.brick.json`
   - 按形态构造适配器（复用 `brick_runtime.build_brick`）：
     PromptBrick→SkillRegistry / EngineBrick→EngineRouter / MemoryBrick→MemoryHost /
     ConnectorBrick→Gateway / ToolBrick→ToolRegistry / ServiceBrick→VaultStore。
   - 激活结果写入 health，skill_list 返回非 0。
4. **builtin_skills 补齐**：`produce.py` 打包时从 vault 的 ax/browser/visualize 积木
   复制 skill.json + 二进制到 `.app/Resources/builtin_skills/`。
5. **全量积木**：web 工作台默认全选 28 个积木（index.json 实际 28 条，方案文档此前写 27 为笔误）。

### 三、出包与分发

- 重出包到 `~/.brickery/agents/<name>/`，cp 到 `/Applications/<name>.app`。
- `dmg.py` 重打 DMG 到桌面，布局含 app + Applications 软链 + 隐藏 .docs（安装引导）。

## 验证

1. `/api/sync` 从 GitHub 拉下底座与积木，前端显示 commit。
2. web 全选 28 积木 assemble 通过。
3. produce 出的 agent：首次启动进安装引导 → 配置 API → 写 config.json。
4. 配置完成后 skill_list 非 0，health 各子系统正常。
5. 二次启动直接进聊天界面，不再进引导。
6. 重打 DMG，从 dmg 复制 .app 模拟双击验证全流程。

## 已拍板（用户确认）

- **底座来源**：最终用户本地无底座，produce 时用 GitHub 拉下的 `~/.brickery/base` runtime；
  本地仓库仅作开发兜底。
- **安装引导**：直接从 Shadeling 现成包（桌面 Shadeling-0.3.37.dmg）提取 `.docs/MODEL_SETUP.html`
  + `README.html` 转换进底座 runtime，作为底座固有能力，不重新设计。
- **全量 28 积木全都要**：含 engine-api（Key 由安装引导让用户填）与 high-config-doc
  （193MB editor_sdk 二进制首次使用时自动下载）。
- **安装引导默认**「网络 API 为主 + 本地 GGUF 兜底」（同 Shadeling 策略）。
