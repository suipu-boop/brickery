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

# Brickery · 生成 agent（agent 底座 + 产出链路）

**三项目之一（2026-08-22 拆分）**：本仓库 = **生成 agent**，负责 agent 内核运行时、装配、安装引导、聊天界面、.brick 打包/导入与产出链路。

- **积木工坊** → 独立仓库 [brickery-workbench](https://github.com/suipu-boop/brickery-workbench)（市场浏览/组装/网页分发）
- **生成 agent** → 本仓库 brickery（内核/底座/产出）
- **积木加工厂** → 独立仓库 [brick-vault](https://github.com/suipu-boop/brick-vault)（积木库/契约/验收）
- 三项目关系与接口契约见 [brickery-meta/ARCHITECTURE.md](https://github.com/suipu-boop/brickery-meta)（会话启动先读）

一个**独立的「造 agent 的工厂」**：用户拖积木拼装，产出**独立可运行的 agent**（独立安装包）。

- **brickery = 平台**：拥有 agent 内核运行时（心脏），是唯一造 agent 的地方
- **Shadeling = 产出物品牌**：brickery 产出的 agent 都可以叫 Shadeling，它用 brickery 的心脏，不是心脏的提供者
- 产出的 agent **本地独立运行**，不依赖 Shadeling 进程

> 从 Shadeling 抽离而来（2026-08-14）。积木平台从来不是"植入 agent 中"，而是独立的产出平台。

## 当前进度（2026-08-16）

- **阶段一断寄生：已完成** —— Shadeling 内组装/积木代码已清空，工厂能力全部归 brickery
- **阶段二心脏归位：已完成** —— 心脏（内核运行时）已抽到 brickery，产出 agent 自带运行时、双击即跑
- **底座实施：已完成** —— 安装引导（setup_wizard）+ 聊天界面（chat_ui）+ 积木激活（ipc）+ 积木市场（BrickMarket）+ 全量/基础出包（produce mode）
- 详细进度与下一步见 [`ROADMAP.md`](ROADMAP.md)；规划见 `specs/` 目录

## 怎么用

```bash
# 启动本地组装工作台（浏览器打开 http://127.0.0.1:8765）
python3 -m brickery.web.server

# 命令行直接组装 + 产出
python3 scripts/e2e_produce.py --bricks ax,docwrite,memory-core --name my-agent
```

## 工作流

```
拖积木（brick-vault） → 静态组装校验（依赖/冲突/资源） → 产出独立 agent 包
```

产出物在 `~/.brickery/agents/<name>/`：

| 文件 | 说明 |
|------|------|
| `agent.json` | 装配清单（元信息 + 拓扑序 + 资源合计） |
| `bricks/` | 选中积木的 brick.json 快照（自包含） |
| `run.sh` | 启动脚本（拉起宿主运行时） |
| `<name>.app` | macOS 独立安装包骨架（可打包 .dmg 分发） |

## 目录结构

```
brickery/
├── brickery/            # Python 包
│   ├── assembler.py     # 静态组装：依赖/冲突/资源校验
│   ├── brick_runtime.py # 动态激活协议：BrickLike 生命周期
│   ├── skill_contract.py# 积木契约：Skill 数据类（brick.json 直映射）
│   ├── produce.py       # 产出链路：方案 → 独立安装包
│   └── web/             # 本地 Web 面板后端（127.0.0.1）
├── web/                 # 组装工作台前端（拖拽 UI）
├── scripts/             # e2e 验证脚本
└── specs/               # 平台规划
```

## 与其它仓库的关系

| 仓库 | 角色 |
|------|------|
| **brickery**（本仓） | agent 产出平台：组装工作台 + 心脏（内核运行时） |
| shadeling | 产出物品牌：brickery 产出的 agent（用 brickery 的心脏） |
| brick-vault | 积木库（brick.json 契约的唯一事实源，不动） |

## 设计铁律

- **心脏不积木化**：agent 内核（supervisor / loop / engine_router）不积木化，积木只做能力组合
- **契约单一事实源**：brick.json schema 是积木契约的唯一事实源，平台与宿主内核通过它对齐
- **先迁后断**：抽离期间 Shadeling 现有功能保持可用，不破坏现状
*（内容由AI生成，仅供参考）*
