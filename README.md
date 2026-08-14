# Brickery · 造 agent 的工厂

一个**独立的「造 agent 的工厂」**：用户拖积木拼装，产出**独立可运行的 agent**（独立安装包）。
Shadeling 只是本平台产出的第一个成品。

> 从 Shadeling 抽离而来（2026-08-14）。积木平台从来不是"植入 agent 中"，而是独立的产出平台。

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
| **brickery**（本仓） | agent 产出平台：组装工作台 + 产出运行时 |
| shadeling | 第一个成品 agent（被产出 / 被运行） |
| brick-vault | 积木库（brick.json 契约的唯一事实源） |

## 设计铁律

- **心脏不积木化**：agent 内核（supervisor / loop / engine_router）不积木化，积木只做能力组合
- **契约单一事实源**：brick.json schema 是积木契约的唯一事实源，平台与宿主内核通过它对齐
- **先迁后断**：抽离期间 Shadeling 现有功能保持可用，不破坏现状
