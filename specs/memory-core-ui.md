# 固定核 UI 与 agent 配置只读开放

日期：2026-08-24
状态：待实现
范围：brickery runtime（ipc.py / chat_ui.py）

## 背景

用户设计的记忆系统有"固定核"（每次必然推送到会话的小段内容，用户可自行修改）。
当前实现已具备固定核机制（fixed_core.py：手动槽 user + 智能槽 auto，每轮注入 prompt，
loop.py `_core_prompt_block`，2000 字符预算），但存在三处缺口：

1. 前端记忆栏没有固定核 tab，用户看不到也改不了（白名单虽有 core_get/core_set，
   但无智能槽与候选相关方法，前端无入口）。
2. "agent 发现重要内容询问用户是否写进固定核"未实现为对话内实时询问；
   当前是夜间归纳 `_auto_fill_core` 高置信自动写、中置信推 pending_candidates 表，
   但候选确认 UI 缺失，候选无人消费。
3. agent.json（agent 定义 + 积木装配清单）不在 IPC 白名单，用户无法在 UI 查看
   自己 agent 的配置。

## 改动

### ipc.py 新增 handler（`_h_` 自动注册）

- `agent_get`：只读返回 home/agent.json 内容。安全：agent.json 无密钥
  （api_key 在 config.json，config_get 已掩码），只读开放不泄露凭据。
  不开放写：agent.json 兼任"已初始化"标记（ipc.py:2618），写入破坏初始化语义。
- `core_smart_get`：返回智能槽全量（fixed_core.get_smart_slots，含置信度/命中数）。
- `core_smart_delete`：删除单条智能槽（fixed_core.delete_smart_slot），暴露纠错入口。
- `core_candidates`：列出 pending_candidates 中 status='pending' 的候选。
- `core_candidate_resolve`：确认候选 → set_smart_slot 写入智能槽，标记 status='resolved'。
- `core_candidate_dismiss`：否决候选 → 标记 status='dismissed'。

### chat_ui.py

- IPC_ALLOWED_METHODS 增加：agent_get, core_smart_get, core_smart_delete,
  core_candidates, core_candidate_resolve, core_candidate_dismiss。
- 记忆栏新增 tab「固定核」：
  - 手动槽列表：可编辑（复用 core_set，items 语义），空值删除。
  - 智能槽列表：可删除（core_smart_delete），展示置信度/命中数。
  - 候选确认区：core_candidates 轮询展示，确认→resolve、否决→dismiss。
- 设置页新增「Agent 配置」只读展示：agent_get 返回 agent.json 全文。

## 注意

- 内联 JS 换行必须写 `\\n`（双反斜杠），禁止裸 `\n`（历史踩坑：裸换行切断 JS 字面量，
  整段 script 不执行）。
- 改完 chat_ui.py 后必须重启 chat_ui 进程才生效；重启前先与用户确认。
- 内核库改动走 PR + 用户确认合并，不静默直推 main。

## 验证

- curl 抓 18767 页面，node --check 校验内联 JS 语法。
- IPC 直调六个新方法返回正常。
- 前端操作：固定核 tab 增删改、候选确认/否决、Agent 配置展示。
