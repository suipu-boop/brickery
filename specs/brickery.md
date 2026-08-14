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
> 进度：**阶段一断寄生已完成（2026-08-15）**；阶段二心脏归位规划已落盘（`p3-runtime.md`），待开工。
> 本文件是 brickery 仓库的规划主体；Shadeling 侧 `specs/agent-factory.md` 为抽离方案记录。

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

## 3. 核心模块

| 模块 | 职责 | 来源 |
|------|------|------|
| `assembler.py` | 静态组装：依赖/冲突/资源校验，产出方案 | 从 Shadeling 迁移（零依赖） |
| `brick_runtime.py` | 动态激活协议：BrickLike 生命周期，委托宿主内核机制 | 从 Shadeling 迁移（Skill 依赖解耦到 skill_contract） |
| `skill_contract.py` | 积木契约：Skill 数据类（brick.json 直映射） | 从 Shadeling skills.py 提取纯数据部分 |
| `produce.py` | 产出链路：方案 → 独立安装包 | 新建 |
| `web/server.py` | 本地 Web 面板后端（127.0.0.1） | 新建 |
| `web/index.html` | 组装工作台前端（拖拽 UI） | 新建 |

> 阶段一（2026-08-15）后：Shadeling 内组装/积木代码已全部清空，工厂能力全部归本仓；心脏（内核运行时）仍在 Shadeling，待阶段二迁入 `brickery/runtime/`。

## 4. 动态激活的宿主委托

brick_runtime 是「平台侧激活协议」，真正激活时委托宿主内核机制（registry / factory）。
`_host_import` 优先尝试 `shadeling.runtime.<module>`，其次本包；均不可用时报「宿主内核未提供该能力」。

后续阶段：把产出 agent 的独立运行时逐步搬进 brickery，摆脱对 Shadeling 的运行时依赖。

## 5. 路线图

- [x] P0 仓库骨架 + 核心代码迁移（assembler / brick_runtime / skill_contract）
- [x] P1 产出链路：方案 → 独立安装包（agent.json + bricks 快照 + run.sh + .app 骨架）
- [x] P2 本地 Web 面板：拖拽组装工作台（127.0.0.1）
- [x] 阶段一 断寄生：Shadeling 内组装/积木代码清空（2026-08-15）
- [ ] 阶段二 心脏归位（P3 独立运行时）：规划已落盘（`p3-runtime.md`，B1–B6 分批），待开工
- [ ] P4 .dmg 打包 + 签名/公证，真正可分发的安装包
- [ ] P5 Shadeling 接入为第一个成品（产出 Shadeling 装配方案）
- [ ] P6 积木市场：从 brick-vault 在线浏览/安装积木

> 与原本计划的差异：原路线图只有 P0–P6；实际推进新增「阶段一断寄生」，并把 P3 细化为「阶段二心脏归位」（B1–B6 分批迁移，详见 `p3-runtime.md`）。

## 6. 验收标准

- 不打开 Shadeling，也能用 brickery 独立拼装并产出一个可安装运行的 agent
- 产出的 agent 是独立安装包，可独立安装、独立运行、可分发
- Shadeling 可作为该平台产出的一个 agent 被运行

## 7. 设计铁律

- **心脏不积木化**：agent 内核（supervisor / loop / engine_router）不积木化
- **契约单一事实源**：brick.json schema 是积木契约的唯一事实源
- **先迁后断**：抽离期间 Shadeling 现有功能保持可用
*（内容由AI生成，仅供参考）*
