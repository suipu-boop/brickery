---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_8236fbc0998111f19bec525400826444
    ReservedCode1: j1jM/yLDAVy9jfgDPHXCR0VhsehKXD5Q8qesFaP5WdoOFmDi3TgnbgJ7xW9RpOLaIZyRdlWG3KjQLbdaMt2oDb5ldDuDAuKA52x2k3BNuCGEtmVJl26LjexuKdkVWo09w6eixp0aRiu3gmTmYIGZZjJ8RCUdreyedt/Fsw/kYP4Sxljg3HYXPUhGad8=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_8236fbc0998111f19bec525400826444
    ReservedCode2: j1jM/yLDAVy9jfgDPHXCR0VhsehKXD5Q8qesFaP5WdoOFmDi3TgnbgJ7xW9RpOLaIZyRdlWG3KjQLbdaMt2oDb5ldDuDAuKA52x2k3BNuCGEtmVJl26LjexuKdkVWo09w6eixp0aRiu3gmTmYIGZZjJ8RCUdreyedt/Fsw/kYP4Sxljg3HYXPUhGad8=
---

# 底座与积木库对接矩阵审计报告

> 审计日期：2026-08-16
> 审计范围：底座 `/Users/suipu/Dev/brickery`（69 个 py 文件，93 个 IPC handler）与积木库 `/Users/suipu/.brickery/vault`（19 块积木）
> 审计方法：静态全检（py_compile / 导入完整性 / files 字段存在性）→ handler 白名单比对 → 只读实测验证

## 1. 结论速览

| 分级 | 数量 | 积木 |
|------|------|------|
| 🟢 绿（接口齐全 + 结构完整） | 15 | ax、backup-restore、code-quality-chain、doctor、docwrite、engine-api、engine-local、feishu、mcp、meeting-minutes、multi-agent、scheduler、telegram、vault、visualize |
| 🟡 黄（接口存在但参数存疑 / 外部依赖未就绪） | 4 | rules、skill-library、browser、high-config-doc |
| 🔴 红（缺接口或文件） | 0 | — |

**总体判定：底座与积木库对接基本健康。19 块积木引用的全部 IPC handler 均在底座 93 个 handler 白名单内，无缺接口、无缺文件；4 块黄项均为「参数名不匹配」或「外部依赖未安装」，不涉及底座接口缺失。**

## 2. 静态全检结果

### 2.1 语法编译（py_compile）

- 底座 py 文件数（排除 tests / __pycache__）：**69**
- py_compile 失败：**0**（全部通过）

### 2.2 files 字段实现文件存在性

4 块积木声明了 `files` 字段，实现文件**全部存在**：

| 积木 | src | dest | 存在性 |
|------|-----|------|--------|
| ax | axctl | bin/ax/axctl | 存在，且可执行（-rwxr-xr-x） |
| feishu | feishu.py | runtime/connectors/feishu.py | 存在 |
| telegram | telegram.py | runtime/connectors/telegram.py | 存在 |
| visualize | render_diagram.py | bin/visualize/render_diagram.py | 存在 |

### 2.3 导入完整性

- feishu.py / telegram.py 引用的 `Gateway`、`GatewayRegistry`（gateway.py）、`DEFAULT_PORT`（ipc.py）、`get_config_dir`（paths.py）**均在底座存在**；
- 底座 `runtime/connectors/__init__.py` 明确声明：连接器框架进底座内核，具体连接器（飞书/Telegram）为**按需积木**，由积木市场安装后提供实现并注册，未装配时惰性导入失败即优雅降级。故 connectors 目录当前无 feishu.py/telegram.py 属**设计如此**，非缺陷。

### 2.4 类型挂载机制核查

| 积木类型 | 声明字段 | 底座挂载点 | 核查结果 |
|---------|---------|-----------|---------|
| ToolBrick | provides_tool=DocWrite / DocWritePro | ToolProviderRegistry | 已注册 DocWrite、DocWritePro、VaultQuery ✓ |
| EngineBrick | engine_kind=api / local | EngineProviderRegistry | 已注册 api→ApiEngine、local→LocalGGUFEngine ✓ |
| ServiceBrick | service_kind=vault | brick_runtime._SERVICE_FACTORIES | 含 "vault": "_make_vault_service" ✓ |
| ConnectorBrick | files→runtime/connectors/ | GatewayRegistry | 符号依赖齐全 ✓ |

## 3. 对接矩阵表

| # | 积木 | 类型 | 对接方式 | 引用的底座接口 | 静态检查 | 只读实测 | 判定 |
|---|------|------|---------|--------------|---------|---------|------|
| 1 | ax | PromptBrick | 外部二进制 axctl | 无 IPC | axctl 存在且可执行 | — | 🟢 |
| 2 | backup-restore | PromptBrick | IPC handler | backup_default / export / restore / list | 4 handler 全存在 | backup_list OK | 🟢 |
| 3 | browser | PromptBrick | 外部 bsk CLI | 无 IPC | bsk 未安装（外部依赖） | — | 🟡 |
| 4 | code-quality-chain | PromptBrick | 纯 prompt | 无 | 无依赖 | — | 🟢 |
| 5 | doctor | PromptBrick | IPC handler | doctor | _h_doctor 存在 | doctor OK | 🟢 |
| 6 | docwrite | ToolBrick | provides_tool=DocWrite | ToolProviderRegistry | 已注册 | — | 🟢 |
| 7 | engine-api | EngineBrick | engine_kind=api | EngineProviderRegistry | 已注册 ApiEngine | — | 🟢 |
| 8 | engine-local | EngineBrick | engine_kind=local | EngineProviderRegistry | 已注册 LocalGGUFEngine | — | 🟢 |
| 9 | feishu | ConnectorBrick | files→connectors/feishu.py | Gateway / DEFAULT_PORT / get_config_dir | 符号齐全 | — | 🟢 |
| 10 | high-config-doc | BinaryBrick | provides_tool=DocWritePro + binary | ToolProviderRegistry | 已注册；editor_sdk 未下载 | — | 🟡 |
| 11 | mcp | PromptBrick | IPC handler | mcp_list | _h_mcp_list 存在 | mcp_list OK | 🟢 |
| 12 | meeting-minutes | PromptBrick | 纯 prompt | 无 | 无依赖 | — | 🟢 |
| 13 | multi-agent | PromptBrick | IPC handler | task_submit / get / list / cancel | 4 handler 全存在 | task_list OK | 🟢 |
| 14 | rules | PromptBrick | IPC handler | rules_list / add / remove / reload | 4 handler 存在，**参数名不匹配** | rules_list OK | 🟡 |
| 15 | scheduler | PromptBrick | IPC handler | task_submit / list / get / cancel | 4 handler 全存在 | task_list OK | 🟢 |
| 16 | skill-library | PromptBrick | IPC handler | skill_library_list / install / uninstall / upgrade / review | 5 handler 存在，**参数名不匹配** | skill_library_list OK | 🟡 |
| 17 | telegram | ConnectorBrick | files→connectors/telegram.py | Gateway / DEFAULT_PORT / get_config_dir | 符号齐全 | — | 🟢 |
| 18 | vault | ServiceBrick | service_kind=vault | vault_ocr / snapshot | _SERVICE_FACTORIES 含 vault | vault_list OK | 🟢 |
| 19 | visualize | PromptBrick | 外部脚本 render_diagram.py | 无 IPC | 脚本存在 | — | 🟢 |

## 4. 黄项明细与修复建议

### 4.1 rules（参数名不匹配）

积木 buttons 声明的参数与底座 handler 实际读取的参数不一致：

| 按钮 | 积木传参 | 底座读取 | 后果 |
|------|---------|---------|------|
| rules_add | `content` | `rule` | 传 content 时 rule 为空，返回「缺少 rule」 |
| rules_remove | `rule_id` | `index` | 传 rule_id 时 index 为 None，返回「缺少 index」 |

**修复建议**（二选一）：
- 改积木：`rules_add` 参数 `content` → `rule`；`rules_remove` 参数 `rule_id` → `index`（index 为整数下标）；
- 或改底座：`_h_rules_add` 兼容 `params.get("rule") or params.get("content")`；`_h_rules_remove` 兼容 `params.get("index") or params.get("rule_id")`。

### 4.2 skill-library（参数名不匹配）

| 按钮 | 积木传参 | 底座读取 | 后果 |
|------|---------|---------|------|
| skill_library_install | `skill_id` | `id` / `name` | 传 skill_id 时 id/name 为空，返回「缺少技能 id」 |
| skill_library_uninstall | `skill_id` | `id` / `name` | 同上 |
| skill_library_upgrade | `skill_id` | `id` / `name` | 同上 |
| skill_library_review | `skill_id` | `id` / `name` | 同上 |

**修复建议**（二选一）：
- 改积木：4 个按钮参数 `skill_id` → `id`；
- 或改底座：4 个 handler 兼容 `params.get("id") or params.get("name") or params.get("skill_id")`。

### 4.3 browser（外部依赖未安装）

- 依赖腾讯 BrowserSkill（bsk CLI），当前 `bsk` 不在 PATH；
- 属外部工具，不随安装包分发，需用户执行官方一行安装后可用；
- 积木 content 已内置安装引导与 `bsk status` 健康检查流程，对接逻辑完整。

### 4.4 high-config-doc（二进制未下载）

- 依赖 editor_sdk 原生引擎（约 193MB，`binary_url` 指向 GitHub release），当前 `~/.brickery/bin` 下无该二进制；
- 属安装时下载（一次性），未下载前 DocWritePro 不可用；`binary_sha256` 已声明，下载后可校验完整性；
- 底座 `binary_manager.py` / `edsdk_pro.py` 已具备下载与拉起机制。

## 5. 只读实测验证记录

> 验证方式：以**临时 home** 构造 IpcServer（`build_real_engines=False`），仅调用只读/验证类 handler，全程无写操作、无副作用，测试后关闭 scheduler 并隔离临时目录。

| handler | 对应积木 | 结果 |
|---------|---------|------|
| `_h_health` | 通用 | OK：`{"status":"ok","home":"<临时目录>"}` |
| `_h_rules_list` | rules（黄项） | OK：返回空规则列表（接口可用） |
| `_h_skill_library_list` | skill-library（黄项） | OK：返回技能列表（含 high-config-doc 等） |
| `_h_mcp_list` | mcp | OK：`{"tools":[],"errors":[]}` |
| `_h_backup_list` | backup-restore | OK：返回备份列表与 backup_dir |
| `_h_task_list` | scheduler / multi-agent | OK：`{"items":[]}` |
| `_h_vault_list` | vault | OK：`{"ok":true,"items":[]}` |
| `_h_doctor` | doctor | OK：返回自检报告（运行环境 / Python 运行时等） |

**实测结论**：黄项积木的只读接口（rules_list、skill_library_list）均可用，接口存在性无问题；黄项根因是**写操作类按钮的参数名不匹配**（静态代码比对确认），不影响只读查询。

## 6. 分级结论

1. **无红项**：19 块积木引用的全部 IPC handler 均在底座 93 个 handler 白名单内；files 字段实现文件全部存在；py_compile 全过；无缺接口、无缺文件。
2. **15 块绿**：接口齐全、结构完整，可直接对接使用。
3. **4 块黄**：
   - 2 块（rules、skill-library）为**参数名不匹配**，写操作按钮调用会失败，需按 §4.1 / §4.2 修复（改积木或改底座均可，改动量小）；
   - 2 块（browser、high-config-doc）为**外部依赖未就绪**（bsk 未安装、editor_sdk 未下载），属安装期动作，非代码缺陷。
4. **建议**：优先修复 rules 与 skill-library 的参数名不匹配（影响按钮功能）；browser 与 high-config-doc 在安装对应依赖后即可转绿。
*（内容由AI生成，仅供参考）*
