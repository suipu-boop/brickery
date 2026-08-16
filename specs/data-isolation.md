---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_c4147ed7997f11f19bec525400826444
    ReservedCode1: J1AOyfN+Sfw3LKTR+ie1CP3e+kOpZTy10pptFsC4Mr+AUwheHHJCY8TjaUDwZ5PIcyEpE/zl08ih9cJgRncgcpz9OIqPCHmcblPS05adaD5XnNvmJdXmRRO/4ZMo6RzOORm5qxJDosXcmUoz3bFTSiLTxSQFF11aqMySQ6IwOI0FbujSgztndERsWNU=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_c4147ed7997f11f19bec525400826444
    ReservedCode2: J1AOyfN+Sfw3LKTR+ie1CP3e+kOpZTy10pptFsC4Mr+AUwheHHJCY8TjaUDwZ5PIcyEpE/zl08ih9cJgRncgcpz9OIqPCHmcblPS05adaD5XnNvmJdXmRRO/4ZMo6RzOORm5qxJDosXcmUoz3bFTSiLTxSQFF11aqMySQ6IwOI0FbujSgztndERsWNU=
---

# 产出 Agent 测试数据与底座/积木库的隔离机制

> 状态：已定稿（2026-08-16）
> 适用范围：Brickery 产出 agent（如 suipu-assistant）的测试与运行
> 目标：**测试数据污染必须绝对避免** —— 产出 agent 的任何运行痕迹不得写入底座 `~/.brickery/base` 与积木库 `~/.brickery/vault`

## 1. 背景与目标

### 1.1 背景

Brickery 底座（`~/.brickery/base`）与积木库（`~/.brickery/vault`）是**共享的、可复用的资产**：

- `~/.brickery/base`：底座运行时源码、specs 设计文档、web 前端等，是后续所有 agent 产出的母本；
- `~/.brickery/vault`：积木库（bricks / skills / index.json），是产出 agent 组装时的积木来源。

产出 agent（如 `suipu-assistant`）在测试/运行时会生成大量运行时数据（会话、记忆、配置、日志、保险库条目等）。若这些数据被写入底座或积木库，会造成：

- 底座/积木库被测试数据污染，后续产出 agent 携带脏数据；
- 测试会话、测试记忆、测试配置混入正式资产，无法区分；
- 积木库被测试写入后，`index.json` 与 bricks 目录不一致，组装校验失败。

### 1.2 目标

1. **绝对隔离**：产出 agent 运行时数据只落自身 home，绝不触碰 `~/.brickery/base` 与 `~/.brickery/vault`；
2. **可验证**：通过 grep 引用核查 + 时间戳核查，可随时证明隔离成立；
3. **可清理**：测试数据集中在两个明确目录，一键清理即可恢复干净环境。

## 2. 三层隔离机制

产出 agent 通过以下三层机制实现与底座/积木库的物理隔离：

### 2.1 第一层：独立运行时（内嵌 brickery-runtime）

产出 agent 的 `.app` 内**打包了独立的 brickery-runtime**（`<agent>.app/Contents/Resources/brickery-runtime/`），启动时通过 `run.sh` 显式设置：

```bash
export PYTHONPATH="$RUNTIME_DIR"   # 只指向 .app 内嵌运行时
export BRICKERY_NO_WATCHDOG=1
```

- 运行时从 `.app` 内嵌目录加载，**不依赖**宿主 `~/.brickery/base` 下的运行时；
- 内嵌 runtime 目录内容为**拷贝快照**（见 2.3），与底座源码物理分离；
- 核查结果：`.app/Contents/Resources/` 下仅含 `agent.json / brickery-runtime / bricks / status.html`，无任何指向 base/vault 的引用。

### 2.2 第二层：独立数据目录（home）

产出 agent 运行时数据全部落在**自身 home 目录**，由 `run.sh` 通过 `--home` 参数显式指定：

```bash
nohup python3 -m brickery.runtime.ipc --home "$AGENT_DIR" > "$AGENT_DIR/ipc.log" 2>&1 &
BRICKERY_HOME="$AGENT_DIR" nohup python3 -m brickery.runtime.setup_wizard > "$AGENT_DIR/setup_wizard.log" 2>&1 &
BRICKERY_HOME="$AGENT_DIR" nohup python3 -m brickery.runtime.chat_ui > "$AGENT_DIR/chat_ui.log" 2>&1 &
```

运行时数据（会话库 `sessions.db`、记忆库 `memory.db`、配置 `config.json`、保险库 `vault/`、日志 `*.log` 等）全部落 home，与底座数据目录完全隔离。

### 2.3 第三层：bricks 拷贝快照（非软链）

产出 agent 的 `bricks/` 目录是积木库的**拷贝快照**，**严禁使用软链**：

- 核查结果：`~/.brickery/agents/suipu-assistant/bricks/` 下 7 个 `.brick.json` 均为**普通文件**（`-rw-r--r--`），非符号链接；
- 拷贝时间戳与积木库一致（`Aug 16 11:38`），证明是产出时从 vault 拷贝的快照；
- 意义：产出 agent 对自身 bricks 的任何修改（启停、增删）只影响快照，**不会反向写回积木库**；积木库 `index.json` 与 bricks 目录保持纯净。

## 3. 数据流向说明

```
┌─────────────────────── 产出 agent（suipu-assistant）───────────────────────┐
│                                                                           │
│  ~/.brickery/agents/suipu-assistant/        （产出目录，只读快照）          │
│    ├── agent.json         组装清单                                          │
│    ├── bricks/            积木拷贝快照（非软链）                            │
│    ├── run.sh             启动入口                                          │
│    └── suipu-assistant.app  内嵌独立 brickery-runtime                      │
│                                                                           │
│  /Users/suipu/Library/Application Support/suipu-assistant/  （运行时 home）│
│    ├── config.json        引擎/备份/产出配置                                │
│    ├── sessions.db        会话库                                            │
│    ├── memory.db          记忆库                                            │
│    ├── cabinet.db / filing.db / consolidation.db                           │
│    ├── vault/             保险库（agent 自身）                              │
│    └── *.log              运行日志                                          │
└───────────────────────────────────────────────────────────────────────────┘
        │ 运行时数据只落自身 home
        ▼
  备份：config.backup_dir → iCloud 云盘
  /Users/suipu/Library/Mobile Documents/com~apple~CloudDocs/小黑/
  （output_dir 同指向 小黑/备份）
```

### 3.1 运行时数据流向

- 会话、记忆、配置、保险库、日志等运行时数据**只落 agent 自身 home**（`/Users/suipu/Library/Application Support/<name>/`）；
- 产出目录（`~/.brickery/agents/<name>/`）在运行期**只读**，不产生写入。

### 3.2 备份数据流向

- 备份走 `config.backup_dir`，指向 **iCloud 云盘**（`/Users/suipu/Library/Mobile Documents/com~apple~CloudDocs/小黑/`）；
- 产出物（output_dir）同样指向 iCloud 云盘 `小黑/备份`；
- 核查结果：`config.json` 中 `home: None`（不覆盖默认）、`backup_dir` 与 `output_dir` 均指向 iCloud 云盘，**不指向** `~/.brickery/base` 或 `~/.brickery/vault`。

## 4. 污染防护原则与红线

### 4.1 核心红线（绝对禁止）

> **产出 agent 运行时禁止写 `~/.brickery/base` 与 `~/.brickery/vault`。**

以下操作一律禁止：

| 红线 | 说明 |
|------|------|
| 禁止写 `~/.brickery/base` | 产出 agent 运行时不得向底座目录写入任何文件（源码、specs、web、日志、配置） |
| 禁止写 `~/.brickery/vault` | 产出 agent 运行时不得向积木库写入/修改/删除任何 bricks、skills、index.json |
| 禁止软链 bricks | 产出 agent 的 `bricks/` 必须是拷贝快照，严禁 `ln -s` 指向积木库 |
| 禁止 `--home` 指向底座 | 启动参数 `--home` / `BRICKERY_HOME` 不得指向 `~/.brickery/base` 或 `~/.brickery/vault` |
| 禁止备份指向底座 | `config.backup_dir` / `output_dir` 不得指向 `~/.brickery/base` 或 `~/.brickery/vault` |

### 4.2 防护原则

1. **只读母本**：底座与积木库对产出 agent 而言是只读资产，产出 agent 只从 vault 拷贝 bricks、从 base 拷贝运行时，绝不回写；
2. **快照隔离**：一切复用（运行时、bricks）均以拷贝快照形式进入 agent 目录，杜绝共享引用；
3. **home 收敛**：所有运行时写入收敛到 agent 自身 home，路径可预期、可清理；
4. **备份外置**：备份与产出物外置到 iCloud 云盘，不占用底座/积木库空间，也不污染其内容。

## 5. 测试数据清理指引

产出 agent 的测试数据集中在**两个数据目录**，清理时删除即可恢复干净环境：

| 目录 | 内容 | 清理方式 |
|------|------|---------|
| `~/.brickery/agents/<name>/` | 产出目录：agent.json、bricks 快照、run.sh、.app | 删除整个目录（重新产出即可重建） |
| `/Users/suipu/Library/Application Support/<name>/` | 运行时 home：sessions.db、memory.db、config.json、vault/、*.log | 删除整个目录（下次启动自动重建） |

以 `suipu-assistant` 为例：

```bash
# 1. 停止 agent 相关进程（ipc / setup_wizard / chat_ui）
# 2. 删除产出目录
rm -rf ~/.brickery/agents/suipu-assistant
# 3. 删除运行时数据目录
rm -rf "/Users/suipu/Library/Application Support/suipu-assistant"
# 4. 清理 iCloud 云盘中的测试备份/产出（可选）
#    /Users/suipu/Library/Mobile Documents/com~apple~CloudDocs/小黑/
```

> 注意：清理前请确认无需要保留的会话/记忆/备份数据；清理后底座 `~/.brickery/base` 与积木库 `~/.brickery/vault` 保持原样，无需也不应清理。

## 6. 验证方法

### 6.1 grep 引用核查（无 base/vault 引用）

对产出 agent 目录做全量 grep，确认无任何指向底座/积木库的引用：

```bash
# 产出 agent 目录内不应出现 base/vault 路径引用
grep -rn "\.brickery/base\|\.brickery/vault" \
  ~/.brickery/agents/suipu-assistant/ \
  "/Users/suipu/Library/Application Support/suipu-assistant/" \
  || echo "无 base/vault 引用 ✓"

# 内嵌 runtime 不应引用宿主运行时
grep -rn "\.brickery/base" \
  ~/.brickery/agents/suipu-assistant/suipu-assistant.app/Contents/Resources/ \
  || echo "内嵌 runtime 无 base 引用 ✓"
```

核查结果：产出 agent 目录、运行时 home、内嵌 runtime 中**均无**指向 base/vault 的引用（空=无引用）。

### 6.2 时间戳核查

通过底座/积木库的最近修改时间判断是否被产出 agent 写入：

```bash
# 记录测试前基线
stat -f "%Sm %N" ~/.brickery/base ~/.brickery/vault
# 测试完成后复查，时间戳应保持不变
```

核查基线（2026-08-16）：

| 目录 | 最近修改时间 | 判定 |
|------|-------------|------|
| `~/.brickery/base` | Aug 16 21:04:41 | 需区分：该时间戳来自**开发仓库同步**（specs/chat-ui-nav.md 等文档更新），非产出 agent 运行时写入 |
| `~/.brickery/vault` | Aug 16 11:38:23 | 与产出 agent bricks 拷贝时间一致，为**拷贝读取**，积木库本身未被写入 ✓ |

> 判定要点：`vault` 时间戳停留在产出拷贝时刻（11:38），证明产出 agent 运行期未写积木库；`base` 若有更新，须先确认来源是开发仓库同步（`git`/拷贝操作）而非产出 agent 运行时，可通过 `grep` 引用核查 + 检查写入内容是否含测试数据来区分。

### 6.3 完整验证清单

1. [ ] `grep -rn "\.brickery/base\|\.brickery/vault"` 产出 agent 目录 → 无引用
2. [ ] `bricks/` 下文件为普通文件（`-rw-r--r--`），非符号链接（`ls -la` 无 `->`）
3. [ ] `config.json` 中 `backup_dir` / `output_dir` 指向 iCloud 云盘，非 base/vault
4. [ ] 测试前后 `~/.brickery/vault` 时间戳不变
5. [ ] 测试前后 `~/.brickery/base` 时间戳不变，或变更可归因于开发仓库同步
6. [ ] 运行时数据（sessions.db / memory.db / vault/ / *.log）全部位于 agent 自身 home
*（内容由AI生成，仅供参考）*
