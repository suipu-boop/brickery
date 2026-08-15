---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_a71354a7989511f19bec525400826444
    ReservedCode1: 1geVbRz6TU4HreB5yymtuIsJ5Op1qReVoCNka1bBX39vt/9GkmDf5jwyx3tVmvdUzFdAU8k1phiCumT0nTvN5WSZFdXncuREXMoIjqDZDr+z3DDoTDDh0M1jROgfYT/ZN0SmEnxG4sEXJnDo393qRbbqK6qpUcXjv7dEqtOb8EIy5RRup1HJ4wm9K+s=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_a71354a7989511f19bec525400826444
    ReservedCode2: 1geVbRz6TU4HreB5yymtuIsJ5Op1qReVoCNka1bBX39vt/9GkmDf5jwyx3tVmvdUzFdAU8k1phiCumT0nTvN5WSZFdXncuREXMoIjqDZDr+z3DDoTDDh0M1jROgfYT/ZN0SmEnxG4sEXJnDo393qRbbqK6qpUcXjv7dEqtOb8EIy5RRup1HJ4wm9K+s=
---



# 热插拔积木方案（Hot-Plug Bricks）

> 状态：**已拍板（2026-08-15）**，待按批次实施
> 关联：`specs/brickery.md`（平台规划）、`specs/p3-runtime.md`（阶段二）、`ROADMAP.md`（路线图）
> 目标：宿主底座支持**运行期热插拔积木**——用户生成软件后，日常使用中可直接插/拔小积木，无需重新组装、重新打包、重启。

---

## 0. 已拍板决策（2026-08-15）

### 0.1 单轨：只做小积木，不再提 skill

- **对外统一"积木"**：用户只看到小积木，UI / 文档 / 市场只提"积木"。
- **不做 skills 市场**：GitHub 上 `skills/` 不再作为用户可见市场，并入 `bricks/` 或降级为积木的"内置实现库"（不对外展示）。
- **内核保留**：Skill 数据类 / SkillRegistry / SkillLibrary 原样保留，作为积木的底层实现（积木激活、下载复用它们），"技能"只出现在代码注释与内部日志。
- **热插拔 = 差异化创新点**：宿主底座运行期插拔积木，是区别于静态装配的核心卖点。

### 0.2 内置 vs 市场（以用户体验为核心）

**三层结构，用户视角清晰**：

| 层 | 内容 | 用户感知 |
|---|---|---|
| 底座内置能力（不可拔） | multi-agent / scheduler / rules / mcp / doctor / vault / memory-core | 开箱就有，不用管 |
| 出厂内置积木（可拔，默认已装） | engine（local/api 二选一）/ visualize / docwrite / meeting-minutes | 开箱即用，能对话/画图/写文档/记纪要 |
| 市场积木（按需安装） | browser / ax / feishu / telegram / code-quality-chain / high-config-doc / backup-restore / 8 个记忆扩展 | 需要时去积木市场装 |

**划分原则（4 条）**：
1. 核心 vs 扩展：缺了会哑火/不可用 → 内置；锦上添花 → 市场
2. 通用 vs 垂直：人人要用 → 内置；特定场景 → 市场
3. 低风险 vs 高风险：无副作用 → 内置；操作真实环境（browser/ax）→ 市场（需显式授权）
4. 无依赖 vs 需凭据：零配置 → 内置；需外部账号（feishu/telegram）→ 市场

**记忆积木**：仅 memory-core 内置（唯一必装），其余 8 个扩展（portrait / fixed-core / cluster / cooccurrence / suggest / consolidation / cabinet / smol）全走市场，避免选择困难。

### 0.3 skill-library 积木 → 改造为 brick-market

- 现有 `skill-library` 积木（技能市场）**退役改名**为 `brick-market`（积木市场）。
- 用户装 `brick-market` 即可在对话中浏览 / 安装 / 卸载其他积木，是热插拔的天然入口，形成闭环。

---

## 1. 背景与目标

### 1.1 现状

- 产出 agent 的积木集合在**组装期固化**：`produce.py` 把选中积木的 `brick.json` 快照复制进包内 `bricks/`，运行时一次性加载。
- 要换积木 = 重新组装 + 重新产出 + 重新安装，链路重、体验差。
- 用户明确不再走"skill 形式"的静态装配，要求**宿主底座热插拔**：运行中直接插拔小积木。

### 1.2 目标

1. 产出 agent 运行中，可**动态安装 / 卸载 / 启停**积木，即时生效，无需重启。
2. 积木来源走 **GitHub 仓库**（对齐最终架构：积木存 GitHub，用户选→下载→组合）。
3. 热插拔的积木集合**持久化**，重启后保留。
4. 插拔有**依赖/冲突校验**，拔掉被依赖积木时阻止或级联提示。

### 1.3 结论先行

**完全可行，且是架构的自然演进，不是推倒重来。** 现状已具备生命周期协议与在线拉取机制，缺的是"把两者接起来 + 暴露 IPC + 持久化 + 统一路径解析"。

---

## 2. 现状盘点

### 2.1 已具备（复用，不重造）

| 能力 | 位置 | 说明 |
|---|---|---|
| 积木生命周期协议 | `brickery/brick_runtime.py` | `BrickLike`：`install / activate / invoke / health / deactivate` 五件套；**deactivate=拔，activate=插** |
| 七种积木形态 | `brick_runtime.py` | Prompt / Connector / Tool / Binary / Engine / Memory / Service，全部委托内核现有机制，积木仅声明式 `brick.json`，**不携带可执行代码**（安全红线已守） |
| 在线拉取/安装/卸载/升级 | `brickery/runtime/skill_library.py` | `SkillLibrary` 已跑通 GitHub 拉取 + 在线安装/卸载/升级（三级回退：用户配置 > 本地 fixtures > 公网） |
| 数据同构 | `brick_runtime.py` `_SKILL_FIELDS` | `brick.json` 与 Skill 数据类字段完全同构，积木市场机制可整体复用 |
| 静态校验 | `brickery/assembler.py` | 依赖/冲突/拓扑序校验逻辑可复用 |

### 2.2 缺口（4 个）

1. **运行时动态添加积木**：`BrickRuntime.load()` 启动时一次性读 vault，无"运行中新增"入口。
2. **IPC 接口**：无 `brick_install / brick_uninstall / brick_list / brick_toggle` 等 handler。
3. **持久化**：热插拔积木集合未落盘，重启即失。
4. **统一路径解析**：见 §4 积木路径问题专项。

---

## 3. 热插拔核心设计

### 3.1 架构总览

```
┌─────────────────────────────────────────────────────────┐
│  产出 agent（运行中）                                     │
│                                                         │
│  IPC handler 层（新增）                                  │
│   brick_list / brick_install / brick_uninstall /        │
│   brick_toggle / brick_status                           │
│                                                         │
│  BrickHotplugManager（新增）                             │
│   ├─ 运行时 vault：~/.brickery/bricks/（持久化）          │
│   ├─ 来源解析：GitHub URL → 本地缓存 → 激活              │
│   ├─ 依赖/冲突校验（复用 assembler 逻辑）                 │
│   └─ 生命周期编排：build_brick → activate / deactivate   │
│                                                         │
│  BrickRuntime（已有）                                    │
│   build_brick → 七型适配器 → 委托内核机制                 │
└─────────────────────────────────────────────────────────┘
```

### 3.2 积木来源与路径（对齐最终架构）

- **积木仓库**：GitHub `suipu-boop/shadeling-bricks`，路径 `bricks/<name>/brick.json`（与本地 `~/Dev/brick-vault/bricks/` 同构）。
- **运行时 vault**：`~/.brickery/bricks/`（用户级，持久化，热插拔积木落这里）。
- **三级回退**（复用 skills 模式）：
  1. 用户配置（`config.json` 显式指定 vault 路径/URL）
  2. 本地 fixtures（`brickery/fixtures/` 或包内 `bricks/`）
  3. 公网 GitHub（`raw.githubusercontent.com/suipu-boop/shadeling-bricks/main/bricks/index.json`）

### 3.3 生命周期语义

| 操作 | 语义 | 委托机制 |
|---|---|---|
| `install` | 从 GitHub 下载 `brick.json`（+二进制）到运行时 vault，校验通过 | `SkillLibrary` 下载逻辑复用 |
| `activate` | 构造适配器并激活（注册进内核） | `BrickRuntime.build_brick().activate()` |
| `deactivate` | 优雅停用（摘除注册/停连接器/停引擎） | `BrickRuntime` 对应适配器 `.deactivate()` |
| `uninstall` | 停用 + 从运行时 vault 删除清单 | 持久化清理 |
| `toggle` | 启停切换（保留清单，不删） | activate/deactivate |

### 3.4 IPC 接口契约

新增 handler（照 `skill_library` 的 handler 模式）：

```
brick_list        → { bricks: [{name, version, state, health, source}] }
brick_install     {name, source?} → {ok, brick, error}
brick_uninstall   {name}          → {ok, error}
brick_toggle      {name}          → {ok, state, error}
brick_status      {name?}         → {bricks: [{name, state, health}]}
brick_market_list → {bricks: [{name, version, summary, category, risk_level}]}  # 浏览 GitHub 市场
```

### 3.5 持久化格式

```
~/.brickery/bricks/
├── index.json          # 已安装积木注册表（schema: brick-registry/v1）
└── <name>/
    └── brick.json      # 积木清单快照（+ 可选二进制/资源）
```

`index.json` 每项含：`name / version / source（github url 或 local）/ installed_at / enabled`。

### 3.6 依赖/冲突校验

- **安装时**：校验 `dependencies` 是否已装、`conflicts` 是否冲突、`resources` 是否超预算（复用 assembler 校验）。
- **卸载时**：检查是否有其他已装积木依赖它，有则阻止并列出依赖方。
- **拔掉使用中积木**：deactivate 前检查活跃会话/工具是否在调用，优雅降级（先停用再摘除）。

### 3.7 风险对策

| 风险 | 对策 |
|---|---|
| 拔掉正在使用的积木 | deactivate 前检查活跃会话，返回"使用中"提示或强制降级 |
| 依赖被拔 | 阻止卸载，列出依赖方 |
| 二进制积木（BinaryBrick） | 二进制来自 GitHub 下载，加哈希/版本校验（复用 `_download_binary`） |
| 恶意/损坏积木 | 复用 `validate_skill_package` 校验；积木不携带可执行代码（红线） |
| 端口冲突 | 产出 agent 动态分配端口或检测占用（沿用待办） |

---

## 4. 积木路径问题专项（用户指定纳入）

### 4.1 现状问题

**三处重复的路径解析逻辑**，规则一致但各自实现，改一处漏三处：

| 位置 | 逻辑 |
|---|---|
| `produce.py` `_find_manifest` | `vault / (entry.path or f"bricks/{name}/") / brick.json` |
| `assembler.py` `load_vault` | `root / (entry.path or f"bricks/{name}/") / brick.json` |
| `brick_runtime.py` `load()` | `vault_root / (entry.path or f"bricks/{name}/") / brick.json` |

**三个未决问题**：

1. **GitHub 仓库路径未定**：本地 `brick-vault` 有 `bricks/` 目录，但 GitHub `shadeling-bricks` 只并入了 `skills/`，`bricks/` 未推。积木在 GitHub 的路径结构需明确。
2. **`index.json` 的 `path` 字段解析规则不统一**：相对路径（`bricks/<name>/`）与 URL（GitHub raw）混用场景未定义。
3. **运行时 vault 路径缺失**：热插拔需要"本地 vault / 运行时 vault / GitHub URL"三级来源，当前只有本地。

### 4.2 方案：统一路径解析

**新增单一解析函数**（放 `brickery/runtime/paths.py` 或 `brickery/brick_paths.py`）：

```
resolve_brick_manifest(source, entry) -> Path | URL
  source ∈ {本地路径, 运行时 vault, GitHub URL}
  entry.path 存在 → source / entry.path / brick.json
  entry.path 缺失 → source / bricks/<name> / brick.json
```

**三处调用方统一改为调用该函数**，消除重复。

### 4.3 GitHub 仓库路径结构（拍板）

```
shadeling-bricks/
├── bricks/                    # 积木市场（唯一用户可见市场，推 GitHub）
│   ├── index.json             # 积木注册表（brick-registry/v1）
│   └── <name>/brick.json      # 各积木清单
└── skills/                    # 技能市场（单轨决策：不再对外，并入 bricks/ 或降级为内置实现库）
    ├── index.json
    └── <name>/...
```

- **用户只看到 `bricks/`**；`skills/` 不再作为用户可见市场，其内容并入 `bricks/` 或作为积木的底层实现库（不对外展示）。
- 本地 `~/Dev/brick-vault` 与 GitHub `shadeling-bricks/bricks/` 保持同构，本地即仓库工作副本。

### 4.4 三级回退（对齐积木市场）

```
1. 用户配置：config.json 显式指定 vault（本地路径或 GitHub URL）
2. 本地 fixtures：brickery/fixtures/ 或包内 bricks/
3. 公网 GitHub：raw.githubusercontent.com/suipu-boop/shadeling-bricks/main/bricks/index.json
```

---

## 5. 实施批次

| 批次 | 内容 | 验证 |
|---|---|---|
| H1 | 统一路径解析函数 + 三处调用方改造 | 单测：本地/运行时/GitHub 三级解析 |
| H2 | `BrickHotplugManager`：运行时 vault + 持久化 + 依赖校验 | 单测：安装/卸载/启停/持久化 |
| H3 | IPC handler 五件套 + 市场浏览 | 单测 + e2e：运行中插拔即时生效 |
| H4 | 积木推 GitHub（`bricks/` 并入 shadeling-bricks） | 实测：从 GitHub 拉取安装 |
| H5 | 前端（web 工作台/产出 agent 面板）插拔 UI | 冒烟：用户视角插拔 |

> 注：H1–H3 为内核改动，先落本方案审阅；H4 涉及 GitHub 仓库操作，单独确认；H5 为纯前端，可直接动手。

---

## 6. 验证方案

1. **单测**：路径解析三级回退、生命周期状态机、依赖/冲突校验、持久化读写。
2. **e2e**：产出 agent 运行中 `brick_install` 新积木 → 立即可用；`brick_uninstall` → 立即摘除；重启后已装积木保留。
3. **冒烟**：从 GitHub 拉取安装一个真实积木（如 `visualize`），对话中触发生效。

---

## 7. 与路线图的关系

- 热插拔是 **P6（积木市场）的运行时形态**：P6 原本"在线浏览/安装"，本方案推进到"运行期插拔"，是 P6 的升级版而非新增阶段。
- 依赖待办：端口冲突根治（产出 agent 动态分配端口）、config.json 自动生成（engine-api 默认模板）。
*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
