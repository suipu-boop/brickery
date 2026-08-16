---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_8b780651997411f19bec525400826444
    ReservedCode1: gBANNUrgMfWZx2mKJWX3kldXb7Vvj+ng5RK2BxE1FTnTaqxFXca2YPa2qgbJPkNQTq/QacKM0+3EoBllqT31SU9lVVbakkhm8f2F/NVWSiC0SEav6e5zcBhJfHexoETPYmu8l2ONKmE4lqQYepY4JxLLl79KO+VJo5V6Hp2YnWoDfbb6dVC1JlfaaLg=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_8b780651997411f19bec525400826444
    ReservedCode2: gBANNUrgMfWZx2mKJWX3kldXb7Vvj+ng5RK2BxE1FTnTaqxFXca2YPa2qgbJPkNQTq/QacKM0+3EoBllqT31SU9lVVbakkhm8f2F/NVWSiC0SEav6e5zcBhJfHexoETPYmu8l2ONKmE4lqQYepY4JxLLl79KO+VJo5V6Hp2YnWoDfbb6dVC1JlfaaLg=
---

# chat_ui 功能导航改造设计（对齐 Shadeling 蓝本）

> 状态：待审阅（2026-08-16）
> 目标：把当前"纯聊天页"的 chat_ui 升级为带完整功能导航的桌面 agent 界面，对齐 Shadeling 蓝本 9 区块侧边栏结构。

## 1. 背景与问题

当前产出 agent 的聊天界面（`brickery/runtime/chat_ui.py`，端口 18767）只有标题栏 + 消息区 + 输入框，**零功能入口**。而 Shadeling 蓝本是带左侧常驻侧边栏的完整桌面 agent app，含 9 个功能区块：

| 区块 | 图标 | 视图 | 能力 |
|------|------|------|------|
| 聊天 | 💬 | ChatView | 会话列表（新建/重命名/删除）、选择消息、导出、生图、中断、附件、复制 |
| 技能库 | ✨ | SkillsView | 技能列表/启停/触发 |
| 记忆柜 | 📦 | CabinetView | 记忆条目管理 |
| 记忆 | 🧠 | MemoryView | 记忆检索/画像/核心 |
| 设置 | ⚙️ | SettingsView | 引擎/模型/配置 |
| 医生 | 🩺 | DoctorView | 自检 |
| 定时任务 | ✅ | TasksView | 任务提交/列表/取消 |
| 保险库 | 🛡️ | VaultView | 保险库条目/OCR/快照 |
| 工作台 | 🔨 | WorkbenchView | 图谱画布/抽屉 |

用户反馈：聊天界面打开了，但没有任何功能按钮，与蓝本差距很大。

## 2. 现状核查

### 2.1 当前 chat_ui.py 结构

- 单页 HTML（PAGE_HTML 常量），工坊蓝图风（深色 + 网格背景 + 橙色强调）
- 元素：header（标题 + 引擎徽章）、#messages 消息区、#inputBar（输入框 + 发送按钮）
- 路由：`GET /`、`GET /api/engine`、`GET/POST /api/sessions`、`POST /api/chat`
- 无任何功能区块入口

### 2.2 底座可用能力（IPC handler 全量盘点）

底座 `ipc.py` 已具备全部功能 handler，可直接桥接：

| 能力域 | handler | 对应积木 |
|--------|---------|---------|
| 聊天/会话 | `_h_chat` `_h_chat_stream` `_h_session_list/new/rename/delete` `_h_chat_cancel` | — |
| 技能库 | `_h_skill_list` `_h_skill_toggle` `_h_skill_trigger` `_h_skill_library_list/install/uninstall/upgrade/review` | skill-library |
| 记忆柜/保险库 | `_h_vault_list/add/delete/detail/ocr/snapshot/scan/enhance` `_h_vault_sync_skills` | vault |
| 记忆 | `_h_memory_search` `_h_recall` `_h_portrait` `_h_portrait_update` `_h_core_get/set` `_h_suggestions` | — |
| 设置/模型 | `_h_config_get/set` `_h_models_list` `_h_model_recommend` `_h_model_download_*` `_h_model_delete` | engine-api / engine-local |
| 医生 | `_h_doctor` `_h_health` | doctor |
| 定时任务 | `_h_task_submit/list/get/cancel` | scheduler |
| 工作台 | `_h_drawer_list/get/create/chat` `_h_node_update/delete` `_h_edge_add/delete` `_h_recordbook_get/sync` `_h_explain_node` | — |
| 备份恢复 | `_h_backup_export/restore/default/list` | backup-restore |
| 规则 | `_h_rules_list/add/remove/reload` | rules |
| 连接器 | `_h_feishu_setup` `_h_telegram_setup` | feishu / telegram |
| 其他 | `_h_mcp_list` `_h_tool_list/toggle/trigger` `_h_set_mode` `_h_daemon_start/stop/status` `_h_status` `_h_open_folder` | mcp / multi-agent / ax / browser |

**结论**：底座能力完备，缺口只在**前端界面**——没有把 handler 暴露成可点击的功能入口。

## 3. 目标界面结构

### 3.1 整体布局（对齐蓝本）

```
┌──────────┬──────────────────────────────────────┐
│ 侧边栏    │ 顶栏（区块标题 + 引擎状态 + daemon 启停）│
│ 220px    ├──────────────────────────────────────┤
│          │                                      │
│ 品牌区    │  内容区（按选中区块渲染对应功能页）      │
│ 导航列表  │                                      │
│ 底部状态  │                                      │
│          │                                      │
└──────────┴──────────────────────────────────────┘
```

### 3.2 侧边栏导航（9 区块 + 扩展）

| 区块 | 图标 | 前端页 | 桥接 handler |
|------|------|--------|-------------|
| 聊天 | 💬 | chat | `_h_chat` `_h_session_*` |
| 技能库 | ✨ | skills | `_h_skill_list/toggle/trigger` `_h_skill_library_*` |
| 记忆柜 | 📦 | cabinet | `_h_vault_list/add/delete/detail` |
| 记忆 | 🧠 | memory | `_h_memory_search` `_h_recall` `_h_portrait` `_h_core_*` |
| 设置 | ⚙️ | settings | `_h_config_get/set` `_h_models_list` `_h_model_*` |
| 医生 | 🩺 | doctor | `_h_doctor` `_h_health` |
| 定时任务 | ✅ | tasks | `_h_task_submit/list/get/cancel` |
| 保险库 | 🛡️ | vault | `_h_vault_*`（含 OCR/快照） |
| 工作台 | 🔨 | workbench | `_h_drawer_*` `_h_node_*` `_h_edge_*` `_h_recordbook_*` |

扩展区块（积木能力，蓝本无但底座有）：
| 区块 | 图标 | 桥接 handler |
|------|------|-------------|
| 备份恢复 | 💾 | `_h_backup_export/restore/default/list` |
| 规则 | 📜 | `_h_rules_list/add/remove/reload` |
| 连接器 | 🔌 | `_h_feishu_setup` `_h_telegram_setup` |

### 3.3 聊天页增强（对齐 ChatView）

- 会话列表（新建/重命名/删除）
- 选择消息 + 删除所选
- 导出对话（Markdown）
- 中断生成
- 添加附件
- 消息复制

## 4. 技术方案

### 4.1 前端架构

- 保持单页 HTML（PAGE_HTML），改为 SPA 结构：侧边栏 + 内容区，JS 按区块切换渲染
- 新增通用 IPC 桥接：`POST /api/ipc` `{method, params}` → 转发到 IPC 服务（18765）→ 返回结果
  - 复用 `ipc.py` 的 `IpcClient`（或直接 HTTP 转发），避免每个功能页单独写 handler
- 各功能页为 JS 渲染的视图函数（renderChat / renderSkills / renderCabinet / ...），共用侧边栏框架

### 4.2 后端改动（chat_ui.py）

- 新增 `POST /api/ipc` 通用桥接路由（method 白名单校验，防越权）
- 新增 `GET /api/status`（daemon 状态，供顶栏）
- 保留现有 `/api/chat` `/api/engine` 路由
- 页面 HTML 扩展：侧边栏 DOM + 各功能页模板 + 桥接 JS

### 4.3 与 IPC 服务通信（协议已确认）

- IPC 服务：`IpcServer`，端口 **18765**，TCP socket + **JSON Lines** 协议
- 请求帧：`{"req_id": <int>, "method": "<handler名>", "params": {...}}\n`
- 响应帧：`{"req_id": <int>, "ok": true|false, "data": {...}}\n`（失败时 `error` 字段）
- 流式 handler（如 `chat_stream`）自行逐行写回 `delta`/`done` 帧，`_dispatch` 不再重复发送
- 桥接实现：chat_ui 新增 `POST /api/ipc`，用 `socket` 直连 18765 转发 JSON Lines，method 走白名单校验

### 4.4 风格

- 沿用现有工坊蓝图风（深色 + 网格 + 橙色强调 `--accent: #ff7a18`）
- 侧边栏选中态：强调色浅底 + 左侧色条（对齐蓝本 SidebarNavButton）
- 顶栏：区块标题 + 引擎状态圆点 + daemon 启停按钮

## 5. 实施步骤

1. 确认 IPC 服务调用通道（IpcClient 协议），打通 `POST /api/ipc` 桥接
2. 改造 PAGE_HTML：加侧边栏框架 + 区块切换 JS
3. 实现聊天页增强（会话列表/导出/中断/附件）
4. 实现各功能页（skills/cabinet/memory/settings/doctor/tasks/vault/workbench + 扩展区块）
5. 同步底座 `~/.brickery/base` + 开发仓库，重新 produce 验证
6. 重打 DMG + 全量单测回归

## 6. 验证方案

- 单测：桥接路由白名单、各功能页 API 返回
- 手动：browser-agent 打开 18767，逐区块点击验证
- 端到端：重新产出 suipu-assistant，确认侧边栏 9+3 区块全部可交互

## 7. 待拍板

1. 侧边栏区块范围：仅蓝本 9 区块，还是含扩展 3 区块（备份/规则/连接器）？
2. 聊天页增强项：会话列表/导出/中断/附件是否全做，还是先做核心（会话列表 + 导出）？
3. 各功能页深度：先做"列表 + 基础操作"，还是完整对齐蓝本视图？
*（内容由AI生成，仅供参考）*
