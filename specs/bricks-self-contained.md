---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_08213665997211f19bec525400826444
    ReservedCode1: l8AwzvYq5kRg9aCiB/nzLvdFl7XqFQTBwZ9GSqBY0RvxXo9KJLocV5lLpiLqU4ZTBZWIAjJHshCTB5PGk9z75rcRUT8yKPhH/rWMJhhZiaJTIE/liE25ww1SmQf8Wtoxz+rbvlkLlJHL+IICJrRv8MvKhTOwwRm1KKjUHTrzGuOj4/LGpSkDElm9wvg=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_08213665997211f19bec525400826444
    ReservedCode2: l8AwzvYq5kRg9aCiB/nzLvdFl7XqFQTBwZ9GSqBY0RvxXo9KJLocV5lLpiLqU4ZTBZWIAjJHshCTB5PGk9z75rcRUT8yKPhH/rWMJhhZiaJTIE/liE25ww1SmQf8Wtoxz+rbvlkLlJHL+IICJrRv8MvKhTOwwRm1KKjUHTrzGuOj4/LGpSkDElm9wvg=
---



# 补进积木：让 4 块声明型积木自包含（路线 2 定稿）

> 状态：已实施（2026-08-16）
> 关联：p10-gap-bricks.md（B 类差距核查）、brick-schema.md（brick.json 契约）

## 1. 背景

底座（~/.brickery/base/brickery）与 Shadeling 存在 B 类功能差距：4 块积木（feishu / telegram / ax / visualize）在 vault 中仅为声明或引用，实现代码在 Shadeling 侧，底座无。

用户拍板：**补进积木**——把实现代码落盘到对应 vault 积木目录，使积木自包含，底座保持精简不重复带实现。

## 2. 前提核查：底座基础功能足够（已确认）

对 19 块积木逐一映射底座 runtime 覆盖情况：

| 积木 | content | 底座覆盖 | 结论 |
|------|---------|----------|------|
| docwrite / engine-local / engine-api / vault | 0（声明型） | docwrite.py / engine_providers.py+engine_router.py / vault_store.py+vault_tool.py | 已覆盖 |
| scheduler / rules / mcp / skill-library | 有 | scheduler.py / rules.py / mcp.py / skill_library.py | 已覆盖 |
| code-quality-chain / high-config-doc / meeting-minutes / multi-agent / backup-restore / doctor | 有 | content 内联，无外部脚本引用 | 已覆盖 |
| browser | 有 | content 自包含，引导装外部 bsk | 已覆盖 |
| **feishu / telegram** | 0 | 底座 connectors/ 仅空壳 __init__.py | **缺实现** |
| **ax** | 有 | 引用 ~/.shadeling/bin/ax/axctl，底座无 | **缺实现** |
| **visualize** | 有 | 引用 render_diagram.py，底座无 | **缺实现** |

**结论：底座基础功能足够，缺的正是这 4 项实现，补进积木即可闭环。**

## 3. 底座已预留扩展点（无需改内核）

- `runtime/connectors/__init__.py` 已声明：连接器框架进底座内核，具体连接器为按需积木，由积木市场安装后提供实现并注册；未装配时优雅降级。
- `runtime/ipc.py:2573-2584` 已有惰性导入 + try/except 优雅降级：`from .connectors.feishu import FeishuConnector` / `from .connectors.telegram import TelegramConnector`，存在即拉起，不存在不影响核心引擎。
- `runtime/gateway.py` 的 `Gateway` / `GatewayRegistry` 与 Shadeling 同构，feishu.py / telegram.py 的 `class FeishuConnector(Gateway)` 可直接继承。
- `runtime/paths.py` 有 `get_config_dir()`（返回 `~/.brickery/config`），与 Shadeling `config/paths.py` 等价。

**底座内核零改动**，只需积木侧提供实现文件。

## 4. 4 项实现落盘明细

| 积木 | 实现源（Shadeling） | 落盘目标 | 适配点 |
|------|---------------------|----------|--------|
| feishu | runtime/connectors/feishu.py（22KB） | vault/bricks/feishu/feishu.py | import 改 1 行：`from config.paths import get_config_dir` → `from runtime.paths import get_config_dir` |
| telegram | runtime/connectors/telegram.py（15KB） | vault/bricks/telegram/telegram.py | 同上 |
| ax | ~/.shadeling/bin/ax/axctl（82KB 二进制） | vault/bricks/ax/axctl | 无（独立 Swift 二进制） |
| visualize | builtin_skills/visualize/render_diagram.py | vault/bricks/visualize/render_diagram.py | 无（纯标准库） |

### 4.1 兼容性核查结论

- feishu.py / telegram.py 依赖 `runtime.gateway.Gateway`、`runtime.ipc.DEFAULT_PORT`（底座均有，import 路径不变）、`config.paths.get_config_dir`（底座为 `runtime.paths.get_config_dir`，**需改 1 行**）。
- render_diagram.py 仅依赖 argparse/json/sys/xml 标准库，零适配。
- axctl 为独立二进制，零适配。

## 5. 积木装配逻辑扩展（唯一内核侧改动）

当前积木仅 brick.json 一个文件，无 files/scripts 落盘机制。需扩展：

### 5.1 brick.json 新增 `files` 字段

```json
{
  "name": "feishu",
  "files": [
    { "src": "feishu.py", "dest": "runtime/connectors/feishu.py" }
  ]
}
```

- `src`：积木目录内相对路径（实现文件随积木分发）
- `dest`：装配后落盘目标，相对底座 home（~/.brickery）或底座 runtime 根

### 5.2 各积木 files 声明

| 积木 | src | dest |
|------|-----|------|
| feishu | feishu.py | runtime/connectors/feishu.py |
| telegram | telegram.py | runtime/connectors/telegram.py |
| ax | axctl | bin/ax/axctl |
| visualize | render_diagram.py | bin/visualize/render_diagram.py |

### 5.3 装配逻辑改动点

- `assembler.py`：`Brick.from_manifest` 解析 `files` 字段，纳入 AssemblyPlan。
- `brick_runtime.py`：`PromptBrick.install` / Connector 型 install 增加落盘步骤——按 files 声明把积木目录内文件复制到 dest（幂等，已存在则跳过或覆盖提示）。
- 落盘目标路径由 `runtime.paths.get_home()`（~/.brickery）派生，不硬编码。

## 6. 路径适配（~/.shadeling → ~/.brickery）

ax / visualize 积木 content 中写死的路径需同步改为底座 home：

| 积木 | 原路径 | 新路径 |
|------|--------|--------|
| ax | ~/.shadeling/bin/ax/axctl | ~/.brickery/bin/ax/axctl |
| visualize | ~/.shadeling/bin/visualize/render_diagram.py | ~/.brickery/bin/visualize/render_diagram.py |

browser 积木无 ~/.shadeling 引用，无需动。

## 7. 验证方案

1. 装配后检查 4 个 dest 文件存在且可执行（axctl 有 x 权限）。
2. `python3 -c "import sys; sys.path.insert(0,'<base>'); from runtime.connectors.feishu import FeishuConnector"` 验证 import 通过。
3. 启动底座，确认 ipc.py 惰性导入拉起连接器（无配置时优雅降级不报错）。
4. 跑既有测试确认无回归。

## 8. 实施顺序

1. 落盘 4 项实现到 vault 积木目录（feishu/telegram 改 import，ax/visualize 原样复制）。
2. 扩展 brick.json schema（files 字段）+ assembler.py / brick_runtime.py 落盘逻辑。
3. 更新 ax / visualize 积木 content 路径为 ~/.brickery。
4. 登记 index.json（如需要）。
5. 跑验证 + 既有测试。
6. 同步三处（开发仓库 / 底座 / .app 内嵌副本）。
*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
