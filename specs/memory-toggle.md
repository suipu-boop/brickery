# memory-* 积木开关方案（memory-toggle）

> 状态：已实施（2026-08-16）
> 拍板结论：memory-* 积木保留为开关，默认开、可关，不移除。

## 背景

记忆系统 8 能力（core / portrait / fixed-core / cluster / cooccurrence / suggest / consolidation / smol）已写死进内核 `brickery/memory/`，对应 memory-* 积木仅声明 memory_kind（见 `runtime/memory_providers.py` 的 MemoryCapabilityRegistry，方法清单来自 `memory.CAPABILITY_KINDS` 单一事实源）。

用户拍板：**保留为开关（默认开可关）**，不彻底移除。

## 改动点

### 1. `brickery/runtime/config.py`

- `Config` 类新增字段：`memory_enabled: bool = True`（默认开，可关）
- `save()` 写入 `"memory_enabled": self.memory_enabled`
- `load_config` 读取：`memory_enabled = bool(raw.get("memory_enabled", True))`（注意默认 True，与 `bricks_enabled` 默认 False 不同）
- 构造 Config 时传入 `memory_enabled=memory_enabled`

### 2. `brickery/runtime/ipc.py`

- 第 153 行 `self.memory = MemorySystem(engine=self._make_smol_engine())` 改为条件初始化：
  - `config.memory_enabled` 为 True → 正常初始化
  - 为 False → `self.memory = _DisabledMemory()`（桩对象），打日志「记忆系统已关闭（memory_enabled=false）」
- 新增 `_DisabledMemory` 桩类：`__getattr__` 通配所有方法返回 None、不抛异常，使 ipc 内 40+ 处 `self.memory.xxx` 调用点与 AgentLoop/Daemon 传参天然安全，无需逐点加防护

### 3. 测试

- 单测回归：`python -m pytest brickery/ -q`（全量 266 passed + 1 skipped）
- 新增用例：
  - `test_config.py`：`test_memory_enabled_default_true`（默认 True）、`test_memory_enabled_save_reload`（False 往返 + 旧配置回退 True）
  - `test_ipc.py`：`test_memory_disabled_uses_stub`（memory_enabled=false 时 self.memory 为桩，recall/list_drawers/enqueue 调用不抛异常）

## 不做的事

- 不移除 memory-* 积木声明（保留在 builtin 层）
- 不改 memory/ 内核实现（clean room 不动）
- 不引入 BrickMarket toggle（builtin 积木不在 home/bricks，走 config 开关更贴合现状）
