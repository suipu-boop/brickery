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
> **方向修正（2026-09-16）**：放弃组装步骤，底座即完整成品，GitHub Release 直接下载安装；本文件"拖积木拼装产出独立 agent"相关描述降级为历史。
> 本文件是 brickery 仓库的规划主体（白皮书）；Shadeling 侧 `specs/agent-factory.md` 为抽离方案记录。

## 1. 定位

**完整可运行的底座 agent**（2026-09-16 修正）：底座（安装引导 + 聊天界面 + 积木激活 + 积木市场）即完整成品，直接经 GitHub Release 分发，用户下载安装即用；不再走"拖积木拼装产出独立 agent"组装链路。

- **brickery = 平台**：拥有 agent 内核运行时（心脏），底座即该心脏的完整成品形态
- **Shadeling = 品牌名**：底座 agent 自称 Shadeling，用 brickery 的心脏，不是心脏的提供者
- 底座 agent **本地独立运行**，不依赖 Shadeling 进程

## 2. 已拍板决策

| 决策 | 结论 |
|------|------|
| A 平台载体 | 本地 Web 面板（127.0.0.1）：浏览器打开即组装工作台 |
| B 产出的 agent | 独立安装包：可独立安装、独立运行、可分发的 agent 包 |
| C 代码边界 | 独立仓库 `brickery`，assembler / brick_runtime / 组装 UI / 产出运行时全部在此 |
| D 与 Shadeling 关系 | Shadeling 降级为工厂产出的第一个成品 |
| E 积木分层 | 内置10（engine 双后端 + 记忆8，写死内核）/ 预置7 / 按需10；skill-library 改造为积木市场（brick-market） |
| F 分发方式（2026-09-16） | 放弃组装步骤；底座即完整成品，GitHub Release 直接下载安装；P2 组装工作台 / P1 产出链路相关描述降级为历史 |
| G 加工厂（2026-09-17） | 冻结归档，不删历史；未来生态需要时解冻 |

## 3. 核心模块

| 模块 | 职责 | 来源 |
|------|------|------|
| `brick_runtime.py` | 动态激活协议：BrickLike 生命周期，委托宿主内核机制 | 从 Shadeling 迁移（Skill 依赖解耦到 skill_contract） |
| `skill_contract.py` | 积木契约：Skill 数据类（brick.json 直映射） | 从 Shadeling skills.py 提取纯数据部分 |
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
- [ ] P5 Shadeling 接入为第一个成品（底座即成品形态，待启动）
- [ ] P6 积木市场：从 brick-vault 在线浏览/安装积木（BrickMarket 已就绪，待底座内热插拔）

> 与原本计划的差异：原路线图只有 P0–P6；实际推进新增「阶段一断寄生」，并把 P3 细化为「阶段二心脏归位」（B1–B6 分批迁移，详见 `p3-runtime.md`）；阶段二后追加「底座实施」（安装引导 / 聊天界面 / 积木激活 / 积木市场 / 全量出包，详见 `base-kernel-design.md` / `engine-buildout.md`）。

## 6. 验收标准

- 不打开 Shadeling，底座也能独立安装运行（GitHub Release 下载 dmg → 安装 → 引导配置 → 聊天 → 积木调用全链路可用）
- 底座安装包可独立安装、独立运行、可分发
- Shadeling 作为底座产物的品牌名被使用

## 7. 设计铁律

- **心脏不积木化**：agent 内核（supervisor / loop / engine_router）不积木化
- **契约单一事实源**：brick.json schema 是积木契约的唯一事实源
- **先迁后断**：抽离期间 Shadeling 现有功能保持可用
*（内容由AI生成，仅供参考）*
