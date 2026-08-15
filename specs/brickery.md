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

# Brickery 平台规划

> 状态：**已授权开工（2026-08-14，用户拍板：A1 / B 独立安装包 / C1+Brickery / D1）**
> 进度：**阶段一断寄生已完成（2026-08-15）**；**阶段二心脏归位已完成（2026-08-16）**；**底座实施已完成（2026-08-16）**。
> 本文件是 brickery 仓库的规划主体（白皮书）；Shadeling 侧 `specs/agent-factory.md` 为抽离方案记录。

## 1. 定位

**独立的「造 agent 的工厂」**：用户拖积木拼装，产出**独立可运行的 agent**（独立安装包）。

- **brickery = 平台**：拥有 agent 内核运行时（心脏），是唯一造 agent 的地方
- **Shadeling = 产出物品牌**：brickery 产出的 agent 都可以叫 Shadeling，它用 brickery 的心脏，不是心脏的提供者
- 产出的 agent **本地独立运行**，不依赖 Shadeling 进程

## 2. 已拍板决策

| 决策 | 结论 |
|------|------|
| A 平台载体 | 本地 Web 面板（127.0.0.1）：浏览器打开即组装工作台 |
| B 产出的 agent | 独立安装包：可独立安装、独立运行、可分发的 agent 包 |
| C 代码边界 | 独立仓库 `brickery`，assembler / brick_runtime / 组装 UI / 产出运行时全部在此 |
| D 与 Shadeling 关系 | Shadeling 降级为工厂产出的第一个成品 |
| E 积木分层 | 内置10（engine 双后端 + 记忆8，写死内核）/ 预置7 / 按需10；skill-library 改造为积木市场（brick-market） |

## 3. 核心模块

| 模块 | 职责 | 来源 |
|------|------|------|
| `assembler.py` | 静态组装：依赖/冲突/资源校验，产出方案 | 从 Shadeling 迁移（零依赖） |
| `brick_runtime.py` | 动态激活协议：BrickLike 生命周期，委托宿主内核机制 | 从 Shadeling 迁移（Skill 依赖解耦到 skill_contract） |
| `skill_contract.py` | 积木契约：Skill 数据类（brick.json 直映射） | 从 Shadeling skills.py 提取纯数据部分 |
| `produce.py` | 产出链路：方案 → 独立安装包（mode：base=预置7 / full=预置+按需17） | 新建 |
| `web/server.py` | 本地 Web 面板后端（127.0.0.1） | 新建 |
| `web/index.html` | 组装工作台前端（拖拽 UI） | 新建 |
| `runtime/setup_wizard.py` | 安装引导页（八家 API 预设 + 本地 GGUF 推荐下载 + 验证，写 config.json） | 新建（2026-08-16） |
| `runtime/chat_ui.py` | 本地 web 聊天界面（工坊蓝图风，走引擎路由） | 新建（2026-08-16） |
| `runtime/ipc.py` | 服务层：启动扫描 home/bricks 激活积木 + 引擎路由 + 未配置引导 | 迁入（B5）+ 扩展（2026-08-16） |
| `runtime/skill_library.py` | 积木市场 BrickMarket（market_list/install/toggle/uninstall） | 迁入（B3）+ 扩展（2026-08-16） |

> 阶段二（2026-08-16）后：心脏（内核运行时）已全部迁入 `brickery/runtime/`，产出 agent 自带运行时、双击即跑；底座实施完成（安装引导 / 聊天界面 / 积木激活 / 积木市场 / 全量出包）。

### 3.1 已实现能力清单（2026-08-16）

| 能力 | 载体 | 说明 |
|------|------|------|
| 安装引导 | `runtime/setup_wizard.py`（127.0.0.1:18766） | 八家 API 预设 + 本地 GGUF 推荐下载 + 验证，写 config.json |
| 聊天界面 | `runtime/chat_ui.py`（127.0.0.1:18767） | 本地 web 聊天界面（工坊蓝图风），走引擎路由，未配置时跳引导 |
| 积木激活 | `runtime/ipc.py` `_activate_bricks` | 启动扫描 `home/bricks/*/brick.json` 按形态激活，故障域隔离 |
| 积木市场 | `runtime/skill_library.py` `BrickMarket` | market_list / install / toggle(.disabled) / uninstall 全流程 |
| 出包 | `produce.py` | mode：base=预置7 / full=预置+按需17；内置10 写死内核不打包；agent.json 记 mode |
| 测试 | `runtime/tests` | 全量 195 passed（runtime 195） |

**积木分层清单**（用户拍板）：

- **内置10（写死内核，不打包）**：engine-local / engine-api / memory-core / memory-portrait / memory-fixed-core / memory-cluster / memory-cooccurrence / memory-suggest / memory-consolidation / memory-smol
- **预置7（base 出包）**：docwrite / scheduler / rules / doctor / backup-restore / meeting-minutes / visualize
- **按需10（full 出包追加）**：feishu / telegram / ax / browser / high-config-doc / code-quality-chain / multi-agent / mcp / memory-cabinet / vault

## 4. 动态激活的宿主委托

brick_runtime 是「平台侧激活协议」，真正激活时委托宿主内核机制（registry / factory）。
`_host_import` 优先尝试 `brickery.runtime.<module>`（P0 修复后），其次本包；均不可用时报「宿主内核未提供该能力」。

阶段二（2026-08-16）后：产出 agent 的独立运行时已全部搬进 brickery（`brickery/runtime/`），不再依赖 Shadeling 运行时。

## 5. 路线图

- [x] P0 仓库骨架 + 核心代码迁移（assembler / brick_runtime / skill_contract）
- [x] P1 产出链路：方案 → 独立安装包（agent.json + bricks 快照 + run.sh + .app 骨架）
- [x] P2 本地 Web 面板：拖拽组装工作台（127.0.0.1）
- [x] 阶段一 断寄生：Shadeling 内组装/积木代码清空（2026-08-15）
- [x] 阶段二 心脏归位（P3 独立运行时）：B1–B6 分批迁移完成（2026-08-16）
- [x] 底座实施：安装引导 + 聊天界面 + 积木激活 + 积木市场 + 全量/基础出包（2026-08-16）
- [ ] P4 .dmg 打包 + 签名/公证，真正可分发的安装包
- [ ] P5 Shadeling 接入为第一个成品（产出 Shadeling 装配方案）
- [ ] P6 积木市场：从 brick-vault 在线浏览/安装积木（BrickMarket 已就绪，待接 web 工作台）

> 与原本计划的差异：原路线图只有 P0–P6；实际推进新增「阶段一断寄生」，并把 P3 细化为「阶段二心脏归位」（B1–B6 分批迁移，详见 `p3-runtime.md`）；阶段二后追加「底座实施」（安装引导 / 聊天界面 / 积木激活 / 积木市场 / 全量出包，详见 `base-kernel-design.md` / `engine-buildout.md`）。

## 6. 验收标准

- 不打开 Shadeling，也能用 brickery 独立拼装并产出一个可安装运行的 agent
- 产出的 agent 是独立安装包，可独立安装、独立运行、可分发
- Shadeling 可作为该平台产出的一个 agent 被运行

## 7. 设计铁律

- **心脏不积木化**：agent 内核（supervisor / loop / engine_router）不积木化
- **契约单一事实源**：brick.json schema 是积木契约的唯一事实源
- **先迁后断**：抽离期间 Shadeling 现有功能保持可用
*（内容由AI生成，仅供参考）*
