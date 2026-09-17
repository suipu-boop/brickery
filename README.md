---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_17b795ba97f911f19bec525400826444
    ReservedCode1: Lw7Kg4cms2CTn7Es1qybwLgeQjJT/746p6cYtg3AM6cRC3lvDL64g1/vLDBIwicyPb3+3RY2jIv4NEITVLRmVXEZXfjF8gmYOqVe98ArjT6CGEy42+zLef0/73JK25tVt+2yJkZqNPEEPzVpVhRCQQZSA0kihINSKw/BDOyaru+wdduH6drSpCBrSRM=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_17b795ba97f911f19bec525400826444
    ReservedCode2: Lw7Kg4cms2CTn7Es1qybwLgeQjJT/746p6cYtg3AM6cRC3lvDL64g1/vLDBIwicyPb3+3RY2jIv4NEITVLRmVXEZXfjF8gmYOqVe98ArjT6CGEy42+zLef0/73JK25tVt+2yJkZqNPEEPzVpVhRCQQZSA0kihINSKw/BDOyaru+wdduH6drSpCBrSRM=
---

# Brickery · agent 底座（完整可运行的底座 agent）

**三项目之一（2026-08-22 拆分，2026-09-16 方向修正）**：本仓库 = **agent 底座**，负责 agent 内核运行时、安装引导、聊天界面、积木激活/市场与 .brick 打包/导入。

- **积木工坊** → 独立仓库 [brickery-workbench](https://github.com/suipu-boop/brickery-workbench)（市场浏览/网页分发）
- **agent 底座** → 本仓库 brickery（内核/底座）
- **积木加工厂** → 独立仓库 [brick-vault](https://github.com/suipu-boop/brick-vault)（积木库/契约/验收）
- 三项目关系与接口契约见 [brickery-meta/ARCHITECTURE.md](https://github.com/suipu-boop/brickery-meta)（会话启动先读）

一个**完整可运行的底座 agent**（2026-09-16 方向修正）：底座（安装引导 + 聊天界面 + 积木激活 + 积木市场）即完整成品，直接经 GitHub Release 分发，用户下载安装即用；不再走"拖积木拼装产出独立 agent"组装链路。

- **brickery = 平台**：拥有 agent 内核运行时（心脏），底座即该心脏的完整成品形态
- **Shadeling = 品牌名**：底座 agent 自称 Shadeling，用 brickery 的心脏，不是心脏的提供者
- 底座 agent **本地独立运行**，不依赖 Shadeling 进程

> 从 Shadeling 抽离而来（2026-08-14）。

## 当前进度（2026-08-16；2026-09-16 方向修正）

- **阶段一断寄生：已完成** —— Shadeling 内组装/积木代码已清空，能力归 brickery
- **阶段二心脏归位：已完成** —— 心脏（内核运行时）已抽到 brickery，底座自带运行时、双击即跑
- **底座实施：已完成** —— 安装引导（setup_wizard）+ 聊天界面（chat_ui）+ 积木激活（ipc）+ 积木市场（BrickMarket）
- **方向修正（2026-09-16）** —— 放弃组装步骤，底座即完整成品，GitHub Release 直接分发；组装/产出链路相关描述已降级为历史
- 详细进度与下一步见 [`ROADMAP.md`](ROADMAP.md)；规划见 `specs/` 目录

## 怎么用

用户：从 GitHub Release 下载安装包（dmg）直接安装使用。

开发：运行入口与调试方式见 [`ROADMAP.md`](ROADMAP.md) 与 `brickery/runtime/` 各模块说明。

## 工作流

```
安装引导（setup_wizard）→ 聊天界面（chat_ui）→ 积木激活（ipc）→ 积木市场（BrickMarket）
```

## 目录结构

```
brickery/
├── brickery/            # Python 包
│   ├── brick_runtime.py # 动态激活协议：BrickLike 生命周期
│   ├── skill_contract.py# 积木契约：Skill 数据类（brick.json 直映射）
│   └── runtime/         # 内核运行时（引导/聊天/积木激活/市场）
├── scripts/             # 校验脚本
└── specs/               # 平台规划
```

## 与其它仓库的关系

| 仓库 | 角色 |
|------|------|
| **brickery**（本仓） | agent 底座：完整可运行的底座 + 心脏（内核运行时） |
| shadeling | 产出物品牌：brickery 产出的 agent（用 brickery 的心脏） |
| brick-vault | 积木库（brick.json 契约的唯一事实源，不动） |

## 设计铁律

- **心脏不积木化**：agent 内核（supervisor / loop / engine_router）不积木化，积木只做能力组合
- **契约单一事实源**：brick.json schema 是积木契约的唯一事实源，平台与宿主内核通过它对齐
- **先迁后断**：抽离期间 Shadeling 现有功能保持可用，不破坏现状
*（内容由AI生成，仅供参考）*
