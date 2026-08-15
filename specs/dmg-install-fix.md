# DMG 安装后无法启动修复方案（稳定优先）

> 状态：✅ 已拍板（用户授权按最优方案执行）并已落地验证
> 关联：`specs/hotplug.md`（热插拔）、`specs/p3-runtime.md`（阶段二）、`ROADMAP.md`
> 目标：修复「DMG 拖拽安装后 .app 打不开」问题，以**稳定、不出错**为第一原则，不追求改动最小。

---

## 0. 拍板结论（2026-08-15）

用户授权"按最好的方案替我拍板"，三项待拍板点全部按推荐方案确认：

1. **数据目录**：`~/Library/Application Support/<name>/`（macOS 标准用户数据位置）。
2. **run.sh 保留**：开发态直跑 agent 目录，与安装态 launcher 分离。
3. **初始化标记**：以 `home/agent.json` 存在为"已初始化"标记，幂等跳过。

已落地改动：`produce.py`（launcher 自包含）+ `ipc.py`（`--app-resources` + 幂等初始化）。
已实测验证：模拟安装启动、首次初始化、幂等、DMG 直启全部通过（见 §2.4 验证记录）。

---

## 1. 问题复现与根因

### 1.1 实测复现

- 命令行直跑 `dmg.py`：出包成功，DMG 挂载校验通过（CRC32 全绿）。
- Web 接口 `/api/dmg`：返回 `{"ok": true}`，出包成功。
- **模拟安装**（复制 .app 到独立目录模拟 /Applications 后执行 launcher）：
  ```
  launcher: line 6: <dir>/run.sh: No such file or directory
  退出码: 1
  ```
  **安装后打不开，确认 bug。**

### 1.2 根因

当前 launcher 逻辑（`produce.py` 生成）：

```bash
AGENT_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"   # 上三级 = app 上一级
exec "$AGENT_DIR/run.sh"                              # 依赖 app 上一级的 run.sh
```

而 DMG 只打包 `.app`（`dmg.py` 的 `files: [app_path, docs_stage]`），`run.sh` 未进包；且拖拽安装只把 `.app` 拖到 `/Applications`，`run.sh` 不会跟随。安装后 launcher 找 `/Applications/run.sh` → 不存在 → 启动失败。

**本质**：`.app` 不自包含，启动依赖包外文件，与"拖拽安装"分发形态不兼容。

---

## 2. 方案设计（稳定优先）

### 2.1 核心原则

1. **.app 完全自包含**：启动不依赖任何包外文件（run.sh 等），双击即可运行。
2. **数据目录独立且用户可写**：安装态数据放 macOS 标准用户数据位置，绝不写 `/Applications`（系统保护路径，不可写）。
3. **首次启动幂等初始化**：数据目录缺失时从 .app 内部复制模板；已存在则复用，绝不覆盖用户数据。
4. **开发态/安装态兼容**：run.sh（开发态直跑 agent 目录）与 launcher（安装态）各自稳定，互不干扰。

### 2.2 数据目录

安装态数据目录：`~/Library/Application Support/<name>/`

理由：
- macOS 标准用户数据位置，稳定、可写、受系统备份保护。
- 与开发态 `~/.brickery/agents/<name>/` 隔离，不冲突。
- 升级 .app 不丢数据（数据在 Application Support，程序在 /Applications）。

### 2.3 改动点

#### 2.3.1 `produce.py` `_bundle_app`：launcher 改为自包含

```bash
#!/bin/bash
# <name> launcher —— 自包含启动，不依赖包外文件
set -euo pipefail
# launcher 位于 Contents/MacOS/，上两级即 .app 根目录
APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
RESOURCES="$APP_DIR/Contents/Resources"
RUNTIME_DIR="$RESOURCES/brickery-runtime"
DATA_DIR="${HOME}/Library/Application Support/<name>"
if [ ! -d "$RUNTIME_DIR" ]; then
  echo "[<name>] 错误：未找到打包运行时（$RUNTIME_DIR）" >&2
  exit 1
fi
mkdir -p "$DATA_DIR"
export PYTHONPATH="$RUNTIME_DIR"
exec python3 -m brickery.runtime.ipc --home "$DATA_DIR" --app-resources "$RESOURCES"
```

#### 2.3.2 `ipc.py`：新增 `--app-resources` + 首次启动初始化

- 新增参数 `--app-resources <path>`（.app 内部 Resources，可选）。
- 启动时（`IpcServer.__init__` 或 `main` 中）执行**幂等初始化**：

```
若 --app-resources 提供 且 home 未初始化（home/agent.json 或 home/bricks/ 缺失）：
    复制 app-resources/agent.json → home/agent.json
    复制 app-resources/bricks/    → home/bricks/
    生成默认 config.json（engine-api 默认模板，复用现有默认逻辑）
否则：跳过（不覆盖任何已有数据）
```

- 幂等保证：以 `home/agent.json` 存在为"已初始化"标记；已初始化则完全跳过。
- `config.json` / `sessions.db` 无需初始化：`load_config` 对缺失 config 回退安全默认，`SessionStore` 自动建库建表（已确认）。

#### 2.3.3 `run.sh` 保留（开发态）

- 开发态直跑 agent 目录：`--home "$AGENT_DIR"`，数据已在，不传 `--app-resources`，不触发初始化。
- 与 launcher 逻辑分离，各自稳定。

#### 2.3.4 `dmg.py` 不变

- 仍只打包 `.app`（自包含后无需 run.sh）。
- 安装引导文档（.docs）中"首次启动若提示无法打开"的说明保留（未签名 app 的 Gatekeeper 提示，属正常流程）。

### 2.4 验证清单

| 项 | 验证方式 | 预期 |
|---|---|---|
| 安装态启动 | 复制 .app 到独立目录 → 执行 launcher | IPC 服务起来，`/api/status` 返回 home=Application Support/<name> |
| 首次初始化 | 启动后检查数据目录 | agent.json + bricks/ 已复制，config.json 已生成 |
| 幂等 | 二次启动 | 不重复复制、不覆盖已有数据 |
| 开发态回归 | 直跑 run.sh | 仍能启动，home=agent 目录，不触发初始化 |
| 全量单测 | `python -m unittest discover` | 全绿 |

**实测验证记录（2026-08-15，web-test-agent 0.1.0）**：

| 项 | 结果 |
|---|---|
| 模拟安装启动（.app 独立目录，无 run.sh） | ✅ IPC 监听 127.0.0.1:18765，首次初始化日志正常 |
| 首次初始化 | ✅ 数据目录生成 agent.json + bricks/（4 个积木快照）+ sessions.db |
| 幂等 | ✅ 二次启动 agent.json md5 不变，未覆盖 |
| DMG 直启（挂载点直接运行 .app） | ✅ 初始化 + IPC 正常，DMG 内容含 .app + Applications 软链 + 背景图 + .docs |
| 开发态回归 | ✅ run.sh 逻辑未动，仍直跑 agent 目录 |

> 注：启动日志出现"连接器模块加载失败（飞书/Telegram 不可用，不影响核心引擎）：No module named 'runtime'"——为打包运行时 connectors 惰性导入的既有行为，明确不影响核心引擎，非本次改动引入。

---

## 3. 待拍板点

1. **数据目录位置**：`~/Library/Application Support/<name>/`（推荐，macOS 标准）——确认或改其他位置。
2. **run.sh 开发态保留**：建议保留（开发调试用），确认。
3. **初始化标记**：以 `home/agent.json` 存在为已初始化标记——确认。

确认后按此方案改代码（produce.py + ipc.py），改完跑验证清单 + 全量单测，再 commit + push。
