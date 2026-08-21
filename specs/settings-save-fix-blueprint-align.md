# 设置页修复：模型保存不上 + 对齐蓝本增量方案

> 状态：待实施（2026-08-21）
> 范围：`brickery/runtime/chat_ui.py`（底座，非积木）
> 背景：用户实测产出 agent，反馈 ① 设置页填写模型保存不上；② 设置页与 Shadeling 蓝本仍有差距，质疑是否读不懂蓝本。已核对蓝本源码（`/Users/suipu/Dev/Shadeling/app/Sources/ShadelingApp/SettingsView.swift`、`ModelProfile.swift`）与本实现（`chat_ui.py renderSettings` 及 IPC `config_set`）。

## 一、问题 1：填写模型保存不上（根因）

### 现象
设置页「网络 API 预设 → 新建/编辑 → 保存」后，模型配置未持久化；重启后丢失。

### 根因
前端 `saveApiModal()` 只把表单写入内存数组 `apiProfiles` 并重渲染卡片，**没有调用 `ipc("config_set", ...)` 落盘**。用户以为 modal 点「保存」即保存，实际必须再点页面底部「保存配置」按钮才持久化。这是两步式交互的语义误导。

- 后端 `_h_config_set` 链路实测正常（profiles + active_profile_id 投影到 engine，config.json 落盘 OK）。
- 蓝本（SwiftUI）为**即时落盘**：`model.saveConfigDebounced()`，编辑即保存。

### 修复
1. 新增 `saveApiProfiles()`：组装 `{profiles, active_profile_id}` 调 `ipc("config_set", ...)` 落盘。
2. `saveApiModal()` 保存成功后**立即调用** `saveApiProfiles()`（静默保存，失败 alert 提示）。
3. `delApiCard()` / `setActiveApi()` 同样即时落盘（删除/切换默认后无需再点底部按钮）。
4. 底部「保存配置」按钮保留（覆盖目录/超时/开关等其它字段），modal 相关字段保存后无需再点。

## 二、问题 2：与蓝本差距分析（对照 SettingsView.swift）

蓝本 `SettingsCategory` 五大分区：通用 / 模型 / 记忆 / 数据与备份 / 关于。

| 蓝本分区 | 蓝本内容 | 当前 chat_ui 实现 | 差距 |
|---|---|---|---|
| 通用 | 语言、引擎状态、执行模式（§5）、红线说明 | 引擎状态卡片 + 通用卡片（超时/工具/技能开关） | 缺执行模式、红线说明；语言对 web 不适用 |
| 模型 | 后端按钮切换 + 本地模型区 + **网络厂商分组选择器**（国内直连/国外需代理/自定义 Coding Plan）+ 名称/URL/Key/Model 表单 | 后端按钮切换 + 本地模型 + API 预设卡片 + 新建/编辑 modal | **缺厂商分组选择器**；modal 无厂商模板 |
| 记忆 | 夜间空闲整理、开场回顾、通知开关 | 无 | **整块缺失**（后端 `config_set.params.nightly` / `open_session_context` 已支持） |
| 数据与备份 | 备份/产出目录（打开/更改）、一键备份、导出/恢复 | 备份/产出目录输入 + 保存 | 缺打开/更改/一键备份/导出/恢复按钮（IPC `backup_*`/`open_folder` 白名单已覆盖） |
| 关于 | 版本、作者、重新运行首次引导 | 版本、关于、重新运行首次引导 | 基本一致 |

结论：**读得懂蓝本**。specs/agent-settings-blueprint.md（08-19）已按蓝本分区重排了设置页布局，功能大体齐备；本轮差距在增量功能未补齐（厂商分组、记忆分区、备份操作）与保存交互不一致（两步式）。

## 三、本轮改动清单（chat_ui.py）

1. **即时落盘**：`saveApiModal` / `delApiCard` / `setActiveApi` 保存后调 `saveApiProfiles()` 落盘。
2. **厂商分组选择器**：modal 增加「厂商」下拉，分组：国内直连（火山方舟 / DeepSeek / 通义千问 / 智谱）、国外需代理（OpenAI / Anthropic / Gemini）、自定义；选中后预填 base URL 与模型名占位；含「我的 Coding Plan」入口（复用现有 coding 模式）。
3. **记忆分区卡片**：新增「记忆」section-card：夜间空闲整理开关（`nightly.enabled`）、使用本地模型（`nightly.use_local_model`）、开场回顾开关（`open_session_context`）；随 `saveConfig()` 一并提交。
4. **数据与备份增强**：备份/产出目录行加「打开」「更改」按钮；一键备份按钮（复用 `open_folder`/`backup_*` 白名单 handler）。
5. **执行模式**：通用卡片补模式选择（`set_mode`，白名单已覆盖）——可选，本轮先补 UI 入口。

### 不动
- 后端 `_h_config_set` 逻辑（已实测正常）。
- IPC 白名单（已覆盖全部所需）。

## 四、验证与同步
1. `python3 -m py_compile brickery/runtime/chat_ui.py` 语法自测。
2. 同步 `~/.brickery/base/brickery`（cp 覆盖 chat_ui.py）。
3. 本地重产出桌面 shadeling（绕过旧工坊，见 08-21 发现：工坊内嵌旧 produce.py 缺 fixtures 修复）。
4. 用户重装验证：modal 保存即生效（重启后模型保留）；厂商分组可选；记忆分区可见。

## 五、待确认
- 侧边分类导航（蓝本左侧 5 分类）是否本轮改？当前单列卡片分区顺序已对齐，观感仍与蓝本有差距。
- 执行模式/红线是否本轮补全？
