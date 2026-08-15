# 底座补全设计（4 项全做）

> 2026-08-15 拍板：剩余未做完项全做。本文档为唯一设计依据，实施后更新状态。

## 1. chat_ui.py —— 聊天界面（新增）

**定位**：用户直接用的通用 agent 聊天界面，本地 web，走引擎路由，工坊蓝图风，版面照搬 Shadeling 形态。

- 服务：`127.0.0.1:18767`，`serve(host, port, daemon=True)` 与 setup_wizard 同构
- 路由：
  - `GET /` 聊天页（消息流 + 输入框 + 会话侧栏）
  - `POST /api/chat` `{messages:[...]}` → 走 `EngineRouter.complete`，返回回复文本
  - `GET /api/engine` 引擎状态（backend / 是否已配置）
  - `GET /api/sessions` / `POST /api/sessions` 会话持久化（复用 sessions.db 或独立 json）
- 未配置引擎（`NoEngineConfigured`）→ 页面提示并引导跳转 `http://127.0.0.1:18766` 安装引导
- 样式：复用 setup_wizard 的工坊蓝图风（深色 + 蓝图网格 + 橙色/青色高亮）

## 2. ipc.py 积木激活（核心改动）

**目标**：启动时扫描 `home/bricks` 按形态激活注册进内核，积木数非 0 才激活；未配置引擎进安装引导。

- 在 `IPC.__init__` 末尾新增 `_activate_bricks()`：
  - 扫描 `self.config.home / "bricks"` 下每个子目录的 `brick.json`
  - 按 `type`（prompt/connector/tool）用 `brick_runtime` 的 `PromptBrick`/`ConnectorBrick`/`ToolBrick` 构造
  - 调 `activate(ctx)` 注册进 `self.skills` / connectors / `self.tools`
  - 记录激活结果到 `self._brick_states`，`GET /api/bricks` 可查
- 未配置引擎（backend 无有效端点且无本地模型）→ 启动时打印引导地址，聊天请求返回 `NoEngineConfigured` 提示

## 3. skill_library.py → 积木市场 brick-market（改造）

**目标**：skill-library 改造为"积木市场"，管理功能积木热插拔（安装/卸载/启用/停用）。

- 保留 `SkillLibrary` 核心（fetch_index / list_entries / validate），新增市场语义：
  - `market_list()` 列出可安装积木（来自 brick-vault index）
  - `market_install(name)` 安装到 `home/bricks/<name>/`（含 brick.json + 资源）
  - `market_uninstall(name)` 卸载（移入回收站语义：改名 `.disabled`）
  - `market_toggle(name, enabled)` 启用/停用（热插拔，配合 ipc 激活）
- 兼容层：`SkillLibrary` 类名保留，新增 `BrickMarket` 别名，旧调用不破坏

## 4. produce.py 全量/基础出包（改动 + 验证）

**目标**：全量/基础两种出包模式 + 重打 DMG 到桌面验证。

- `produce()` 已支持固化 + .app 骨架；新增 `mode` 参数：
  - `full`：全量积木（内置10 + 预置7 + 按需10 全打包）
  - `base`：基础积木（内置10 + 预置7）
- 出包后重打 DMG 到桌面（`~/Desktop/`），验证双击可跑
- 用户此前说"不急着打包"，本次仅完成代码与出包脚本，DMG 重打待用户确认后执行

## 实施顺序

1. chat_ui.py（新增，独立）
2. ipc.py 积木激活（核心改动）
3. skill_library.py → brick-market（改造）
4. produce.py 全量/基础出包（改动）

## 状态

- [x] 设计定稿
- [ ] chat_ui.py
- [ ] ipc.py 积木激活
- [ ] skill_library.py → brick-market
- [ ] produce.py 全量/基础出包
