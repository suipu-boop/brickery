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

# Brickery ROADMAP

> 阶段恢复锚点：**新会话续做先读本文件**对齐当前阶段与待办，无需再翻历史对话。

## 新会话恢复指引（先读这里）

1. **本文件**（`ROADMAP.md`）：当前阶段、今日进度、下一步、关键路径
2. `specs/brickery.md`：平台规划主体（定位 / 已拍板决策 / 路线图）
3. `specs/p3-runtime.md`：阶段二心脏归位分批计划（B1–B6）
4. `specs/rectify.md`：定位纠偏方案（brickery=平台，Shadeling=产出物品牌）

## 定位（一句话）

**别名**：积木平台 / 造 agent 的工厂 / brickery —— 均指本仓库 `/Users/suipu/Dev/brickery`。

brickery = 平台（拥有心脏/内核运行时），Shadeling = 它产出的品牌产品。产出 agent 本地独立运行，不依赖 Shadeling。

## 当前状态

**阶段一断寄生：已完成**（2026-08-15）
- Shadeling 内组装/积木代码已全部移除（commit `5cc35b5`，已 push）
- 工厂能力全部归 brickery

**阶段二心脏归位：B1+B2+B3+B4+B5 已完成，B6 待开工**
- 规划文档：`specs/p3-runtime.md`
- B1 纯数据层已迁入 `brickery/runtime/`（config / model_catalog / rules / textutil / paths），16 单测通过
- B2 引擎层已迁入（engine_router / engine_providers / supervisor），23 单测通过
- B3 工具技能层已迁入（tools / skills / skill_library / sandbox / mcp / binary_manager + builtin_tools / tool_providers / doc_tools / repo_map / vault_store / vault_tool / docwrite / docwrite_pro / docwrite_templates / edsdk_pro），77 单测通过
- B4 记忆层已迁入（memory/ 包 17 文件 + memory_providers），54 单测通过
- B5 服务层已迁入（ipc / daemon / sessions / scheduler / gateway / confirm / interoception/ + loop），195 单测通过；test_surfacing 迁回，memory 69 单测通过
- 下一步：B6 产出链路（produce.py 打包运行时进 .app，run.sh 改入口）

## 今日进度（2026-08-15）

- 阶段一断寄生完成：Shadeling 内组装/积木代码已清空（commit `5cc35b5`，已 push）
- 定位纠偏落盘：`specs/rectify.md`（commit `d4971d7`，已 push）
- 阶段二规划落盘：`specs/p3-runtime.md` + 本文件（commit `157f283`，已 push）
- 核心测试 12 通过；1 个 API 500 冒烟测试失败为改动前既有问题
- **B1 纯数据层迁移完成**：config / model_catalog / rules / textutil / paths 迁入 `brickery/runtime/`，路径改造为 brickery 专属（BRICKERY_HOME / ~/.brickery），16 单测通过
- **B2 引擎层迁移完成**：engine_router / engine_providers / supervisor 迁入 `brickery/runtime/`（engine_providers 3 处 `from config import paths` 改相对导入），23 单测通过
- **B3 工具技能层迁移完成**：核心 6 模块（tools / skills / skill_library / sandbox / mcp / binary_manager）+ 工具实现 10 模块（builtin_tools / tool_providers / doc_tools / repo_map / vault_store / vault_tool / docwrite / docwrite_pro / docwrite_templates / edsdk_pro）迁入 `brickery/runtime/`，fixtures/skill_repo 迁入 `brickery/fixtures/`，77 单测通过
- **B4 记忆层迁移完成**：memory/ 包（17 文件）迁入 `brickery/memory/`，memory_providers 迁入 `brickery/runtime/`，db/cabinet 的 config.paths 改 brickery.runtime.paths，测试基类改 BRICKERY_HOME，54 单测通过
- **B5 服务层迁移完成**：ipc / daemon / sessions / scheduler / gateway / confirm / loop 迁入 `brickery/runtime/`，interoception/ 包（7 文件）迁入 `brickery/runtime/interoception/`；test_scheduler 修复遗留失效 import（顶层 scheduler 包已不存在，改 brickery.runtime.scheduler）；test_confirm_pressure 子进程启动改 `python -m brickery.runtime.ipc` + BRICKERY_HOME；B4 暂存的 test_surfacing 迁回，195 单测通过
- 全量单测通过：runtime 195 + memory 69（`python -m unittest discover -s brickery/runtime/tests -t brickery -p "test_*.py"`）

## 与原本计划的差异

原本计划（`specs/brickery.md` 路线图 P0–P6）与实际推进的差异：

- **新增「阶段一断寄生」**：原计划只有"先迁后断"，实际把"断"提前独立成阶段一，Shadeling 内组装代码已全部清空
- **P3 细化为「阶段二心脏归位」**：拆成 B1–B6 六批迁移（纯数据 → 引擎 → 工具技能 → 记忆 → 服务 → 产出链路），每批先迁后断、逐批验证
- **定位明确**：brickery=平台（拥有心脏/内核运行时），Shadeling=产出物品牌（用心脏），不再是并列的"第一个成品"

## 路线图

| 阶段 | 内容 | 状态 |
|------|------|------|
| P0 | 仓库骨架 + 核心代码迁移 | 完成 |
| P1 | 产出链路（方案 → 独立安装包） | 完成 |
| P2 | 本地 Web 面板（127.0.0.1） | 完成 |
| 阶段一 | 断寄生（Shadeling 清空组装代码） | 完成 |
| **阶段二** | **心脏归位（P3 独立运行时）** | **待开工** |
| P4 | .dmg 打包 + 签名/公证 | 待办 |
| P5 | Shadeling 接入为第一个成品 | 待办 |
| P6 | 积木市场（brick-vault 在线浏览/安装） | 待办 |

## 阶段二待办（按批次）

- [x] B1 纯数据层：config / model_catalog / rules / textutil → brickery/runtime/（16 单测通过）
- [x] B2 引擎层：engine_router / engine_providers / supervisor → brickery/runtime/（23 单测通过；loop 依赖 B3/B5，待 B3 后迁）
- [x] B3 工具技能层：tools / skills / skill_library / sandbox / mcp / binary_manager + builtin_tools / tool_providers / doc_tools / repo_map / vault_store / vault_tool / docwrite / docwrite_pro / docwrite_templates / edsdk_pro → brickery/runtime/（77 单测通过；loop 依赖 B5 interoception，待 B5 后迁）
- [x] B4 记忆层：memory/ 包（17 文件）→ brickery/memory/，memory_providers → brickery/runtime/（54 单测通过）
- [x] B5 服务层：ipc / daemon / sessions / scheduler / gateway / confirm / interoception/ + loop → brickery/runtime/（195 单测通过；test_surfacing 迁回，memory 69 单测通过）
- [ ] B6 产出链路：produce.py 打包运行时进 .app，run.sh 改入口

## 下一步

**B6 产出链路**：produce.py 打包运行时进 .app，run.sh 改入口，产出独立可分发安装包。

## 关键路径

- 平台代码：`/Users/suipu/Dev/brickery`
- 心脏来源：`/Users/suipu/Dev/Shadeling/runtime/`（迁移源）
- 积木库：`/Users/suipu/Dev/brick-vault`（不动）
- 规划文档：`specs/brickery.md`（平台规划）、`specs/rectify.md`（定位纠偏）、`specs/p3-runtime.md`（阶段二规划）
*（内容由AI生成，仅供参考）*
