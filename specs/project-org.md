# 三项目组织规划（工坊 / 生成 agent / 积木加工厂）

> 状态：**已拍板方案 A（三独立仓库），执行中**
> 日期：2026-08-22
> 提出：多对话并行推进需求——每个项目独立会话开展优化，会话间能了解项目联系

## 一、目标

把「积木工坊」「生成 agent」「积木加工厂」分成边界清晰、又有明确联系的三个项目，支持：
1. 随时用不同对话开展各项目的优化和建设
2. 每个会话都能了解项目之间的联系
3. GitHub 仓库层面同样清晰可分

## 二、三项目边界

| 项目 | 定位 | 边界（管什么） | 不管什么 |
|------|------|--------------|---------|
| **积木工坊**（Workbench） | 面向用户的组装+分发端 | 积木市场浏览、选积木、组装、产出 agent 安装包、网页下载站、Release 发布 | 积木生产、内核运行时 |
| **生成 agent**（Agent Forge） | agent 底座+产出链路 | 内核运行时、装配、安装引导、聊天界面、.brick 打包/导入 | 积木内容生产、市场分发 |
| **积木加工厂**（Brick Factory） | 积木生产端 | 积木创建/编辑/打包/测试/发布、brick.json 契约、验收闸门 | agent 组装、用户分发 |

## 三、三者联系（每个会话都能看到）

- **依赖方向（单向）**：加工厂产积木 → 工坊浏览选择 → 生成 agent 装配进底座 → 产出 agent 运行积木
- **顶层 ARCHITECTURE.md**：三项目关系图 + 各自入口（仓库/目录/文档）+ 联结点（接口契约：brick.json、.brick 包、API）
- **会话启动协议**：任何项目会话启动时，先读顶层 ARCHITECTURE.md 恢复全局视野，再进本项目 ROADMAP/specs

```
┌─────────────┐   积木(brick.json/.brick)   ┌─────────────┐
│  积木加工厂  │ ──────────────────────────▶ │   积木工坊   │
└─────────────┘                             └──────┬──────┘
                                                    │ 组装+产出
                                                    ▼
                                          ┌─────────────────┐
                                          │   生成 agent     │
                                          │ (底座+装配+引导)  │
                                          └─────────────────┘
```

## 四、GitHub 组织方案

### 方案 A：三个独立仓库（已拍板）

| 仓库 | 内容 | 定位 |
|------|------|------|
| `brickery-workbench` | app/、web/、site/、brickery/web/（工坊后端）、build 脚本、工坊 specs | 积木工坊 |
| `brickery`（保留改名定位） | brickery/ 内核（去 web/）、runtime/、produce 链路、装配/引导/聊天 specs | 生成 agent |
| `brick-vault`（已有） | 积木库 + 加工厂契约 | 积木加工厂 |
| `brickery-meta`（新增） | ARCHITECTURE.md 三项目关系/接口契约/会话协议 | 顶层导航 |

### 内核共享决策（2026-08-22 拍板 A 时确认）

- 工坊后端 `brickery/web/server.py` 依赖内核 `assembler/produce/package`（组装+产出 agent 链路）
- 决策：**内核单权威**——`brickery` 仓库为唯一内核源；工坊构建时从 GitHub 拉取内核并合并自己的 `brickery/web/`（工坊本就是在线工具，可接受）
- `app/`（Swift 壳）为两项目共享组件，**双仓库各存一份**，改动需同步（记入 ARCHITECTURE.md 共享组件清单）

## 五、执行清单（2026-08-22）

- [x] 代码归属调查（app 共享、web 依赖内核、produce 排除 web）
- [ ] 建 brickery-workbench 本地仓库（复制 app/web/site/brickery-web/工坊 specs）
- [ ] 建 brickery-meta 本地仓库（ARCHITECTURE.md）
- [ ] brickery 移除顶层 app/web/site、scripts/build_workbench_app.sh、brickery/web/
- [ ] build_workbench_app.sh 改造：构建时拉内核合并 brickery/web/
- [ ] 三仓库 git 提交 + GitHub 建仓推送
- [ ] 回归验证：工坊打包脚本、生成 agent e2e
- [ ] 各仓库 README/ROADMAP 归属整理

## 六、迁移影响（执行时逐项处理）

- [ ] site/ 网页站内 dmg/.brick 下载链接、镜像地址（gh-proxy/jsDelivr）——随工坊仓库迁移，URL 不变
- [ ] live_vault.py 直连的 brick-vault 地址不变
- [ ] clone 缓存路径（~/.brickery/base → brickery 仓库地址不变）
- [ ] 各 specs/ROADMAP 中的仓库引用
- [ ] 本地 Dev 目录结构（brickery-workbench / brickery / brick-vault / brickery-meta）
