---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_186fa11297f911f18cca525400e6dd8f
    ReservedCode1: 6rt9IJKuFl6DLisb0kBcMRBuu6dZslFtx9hVFMf3YyVjPPPYLh/zdYeQbJeYQ4M79R7x8neJZONEmJOajzi/1dtHgwyrf/neAc6m08zr7o84pAtrkM57RIUWM7S142uQAaeVuvJeWPxPgCdSUfgXkWU1zYOfzx6VUp/qXHA5hrHe96720uFoD6dXiFc=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_186fa11297f911f18cca525400e6dd8f
    ReservedCode2: 6rt9IJKuFl6DLisb0kBcMRBuu6dZslFtx9hVFMf3YyVjPPPYLh/zdYeQbJeYQ4M79R7x8neJZONEmJOajzi/1dtHgwyrf/neAc6m08zr7o84pAtrkM57RIUWM7S142uQAaeVuvJeWPxPgCdSUfgXkWU1zYOfzx6VUp/qXHA5hrHe96720uFoD6dXiFc=
---

# 定位纠偏修正方案（Rectify）

> 状态：**待用户审阅拍板**
> 背景：用户最终确认定位——**brickery 是平台（拥有心脏/内核运行时），Shadeling 是它产出的品牌产品（用心脏）**。
> 凡违背这一思路的存在，本方案逐一列出并给出修正步骤。

## 1. 定位铁律（本次确认）

- **brickery = 平台**：拥有 agent 内核运行时（心脏），是唯一造 agent 的地方
- **Shadeling = 产出物品牌**：brickery 产出的 agent 都可以叫 Shadeling，它用 brickery 的心脏，不是心脏的提供者
- 产出的 agent 必须**本地独立运行**，不依赖 Shadeling 进程

## 2. 违背思路的存在清单

### 违背点 A：心脏住在 Shadeling 里（最核心）

以下内核运行时文件目前只存在于 Shadeling，归 brickery 所有：

| 文件 | 职责 |
|------|------|
| `runtime/supervisor.py` | 主管/生命周期 |
| `runtime/loop.py` | 对话主循环 |
| `runtime/engine_router.py` | 引擎路由 |
| `runtime/ipc.py` | 进程间通信 |
| `runtime/engine_providers.py` | 引擎接入 |
| `runtime/memory_providers.py` | 记忆接入 |
| `runtime/sessions.py` / `scheduler.py` / `daemon.py` / `gateway.py` | 会话/调度/守护/网关 |
| `runtime/config.py` / `model_catalog.py` / `sandbox.py` / `mcp.py` | 配置/模型/沙箱/MCP |
| `runtime/tools.py` / `tool_providers.py` / `builtin_tools.py` | 工具层 |
| `runtime/skill_library.py` / `skills.py` / `binary_manager.py` / `vault_store.py` | 技能/二进制/积木库存储 |

### 违背点 B：工厂能力寄生在 Shadeling 里（已迁未断）

brickery 已迁好副本，Shadeling 内残骸待断：

| 文件 | 处置 |
|------|------|
| `runtime/assembler.py` | 删除（brickery 已有） |
| `runtime/brick_runtime.py` | 删除（brickery 已有） |
| `runtime/ipc.py` 内 6 个 brick_* handler | 移除 |
| `app/.../AssemblerView.swift` | 移除（组装 UI 归 brickery Web 面板） |
| `app/.../SkillLibraryView.swift` | 移除（积木市场 UI 归 brickery） |
| `scripts/e2e_assemble.py` / `e2e_daemon_bricks.py` / `e2e_engine_brick.py` / `e2e_memory_brick.py` / `e2e_p9_bricks.py` / `e2e_p10_bricks.py` / `e2e_p12_assembler.py` / `e2e_skill_market.py` / `verify_bricks.py` | 移除或迁移到 brickery/scripts |

### 违背点 C：产出的 agent 依赖 Shadeling 运行

- `brickery/brickery/produce.py` 产出的 `run.sh` 目前写 `shadeling run agent.json`
- 修正：P3 独立运行时，产出 agent 自带 brickery 运行时，双击即跑

### 违背点 D：命名/定位混乱

- Shadeling 的 README / AGENTS.md / specs 中仍可能把自己描述为"平台/宿主"
- 修正：统一口径——Shadeling 是 brickery 产出的品牌产品

## 3. 修正步骤（分阶段）

### 阶段一：断寄生（低风险，可立即做）
1. 删除 Shadeling 内已迁走的组装代码（违背点 B 清单）
2. 移除 ipc.py 的 brick_* handler 及对应 Swift 引用
3. 清理积木相关 e2e 脚本
4. 跑 Shadeling 现有测试，确认不破坏现状

### 阶段二：心脏归位（P3，大工程）
1. 把违背点 A 的内核运行时抽到 brickery，作为共享底座
2. 产出 agent 打包时自带该运行时
3. `run.sh` 改为直接启动 brickery 运行时，不再依赖 Shadeling

### 阶段三：文档定位统一
1. 更新 Shadeling README / AGENTS.md / specs 口径
2. 更新 brickery README / specs 口径

## 4. 验收标准

- Shadeling 内不再有任何组装/积木代码（工厂能力全部在 brickery）
- brickery 产出的 agent 不依赖 Shadeling 进程即可本地独立运行
- 文档口径统一：brickery=平台，Shadeling=产出物品牌

## 5. 风险

- 阶段二（心脏归位）涉及几十个内核文件，需分批迁移并保持 Shadeling 可用（先迁后断）
- 阶段一删除前需确认 brickery 副本完整且 e2e 通过（已通过）
*（内容由AI生成，仅供参考）*
