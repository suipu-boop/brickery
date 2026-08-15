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

**阶段二心脏归位：B1–B6 全部完成**
- 规划文档：`specs/p3-runtime.md`
- B1 纯数据层已迁入 `brickery/runtime/`（config / model_catalog / rules / textutil / paths），16 单测通过
- B2 引擎层已迁入（engine_router / engine_providers / supervisor），23 单测通过
- B3 工具技能层已迁入（tools / skills / skill_library / sandbox / mcp / binary_manager + builtin_tools / tool_providers / doc_tools / repo_map / vault_store / vault_tool / docwrite / docwrite_pro / docwrite_templates / edsdk_pro），77 单测通过
- B4 记忆层已迁入（memory/ 包 17 文件 + memory_providers），54 单测通过
- B5 服务层已迁入（ipc / daemon / sessions / scheduler / gateway / confirm / interoception/ + loop），195 单测通过；test_surfacing 迁回，memory 69 单测通过
- B6 产出链路已完成：produce.py 打包 brickery-runtime（runtime+memory）进 .app/Contents/Resources/，run.sh 改独立运行时入口（python3 -m brickery.runtime.ipc），不再依赖宿主 shadeling 命令；e2e 全链路通过，全量单测 263 passed + 1 skipped

## 今日进度（2026-08-16）

- **底座实施完成（commit `af7d401`，已 push）**：
  - `setup_wizard.py`：安装引导页（八家 API 预设 + 本地 GGUF 推荐下载 + 验证，写 config.json），127.0.0.1:18766
  - `chat_ui.py`：本地 web 聊天界面（工坊蓝图风，走引擎路由），127.0.0.1:18767，未配置引擎引导 18766
  - `ipc.py`：启动扫描 home/bricks 按形态激活积木（故障域隔离），未配置引擎打日志引导
  - `skill_library.py`：新增 BrickMarket（market_list/install/toggle/uninstall，安装写 home/bricks/<name>/brick.json）
  - `produce.py`：新增 mode 参数（base=预置7 / full=预置+按需17，内置10 写死内核不打包）
  - 全量测试 195 passed
- 接口速查表落盘：`specs/engine-interfaces.md`（根治重复读文件，写代码只查文档）
- 实施设计落盘：`specs/engine-buildout.md`

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
- **B6 产出链路完成**：produce.py 打包 brickery-runtime（runtime+memory）进 .app/Contents/Resources/，run.sh 改独立运行时入口（python3 -m brickery.runtime.ipc），不再依赖宿主 shadeling 命令；e2e 全链路通过（commit `b21e314`）
- **净化 shadeling 残留完成**：brick_runtime 宿主回退删除；SHADELING_HOME/~/.shadeling → BRICKERY_HOME/~/.brickery（50 处）；运行时标识（logger/环境变量/keychain/产出目录/测试前缀/打印前缀）→ brickery（43 处）；schema 契约统一 brickery-memory-export/v1；保留品牌身份（产出 agent 自称 Shadeling）/数据契约（shadeling-skill-repo/v1）/迁移兼容（~/shadeling-runtime、Shadeling_* 备份）/来源注释；方案落盘 `specs/cleanup-shadeling.md`
- **GitHub 仓库整合完成（5→3）**：技能市场源并入 `brick-vault/skills/`（commit `b4b2066` 已 push）；ipc.py 技能源 URL 改指向 shadeling-bricks；删除 GitHub `shadeling-skills` + `shadling`（204）；本地 shadeling-skills clone 已删；editor_sdk（193MB）git filter-repo 重写历史移除 + 上传 shadeling-bricks Release v1.0.0，brickery force push 成功（`b8188c0..f49e847`）；全量单测 263 passed + 1 skipped；方案与执行记录落盘 `specs/cleanup-shadeling.md`
- **Web 工作台优化完成**：`assembler.py` Brick 类新增展示字段（summary/description/category/tags/capabilities/dependencies），`server.py` `_api_bricks` 透传；`web/index.html` 重写为「工坊蓝图风」（暖纸底+墨字+琥珀/朱红，分类分组/搜索高亮/风险筛选/积木详情展开/骨架屏/空状态/错误重试/步骤引导）；单测回归 runtime 195 passed + memory 69 passed + 1 skipped 全绿；服务重启实测 28 积木展示字段透传正常；方案落盘 `specs/web-workbench.md`
- **热插拔方案拍板**：`specs/hotplug.md` 补全已拍板决策——① 单轨：只做小积木、不再提 skill、不做 skills 市场（skills/ 并入 bricks/ 或降级为内置实现库）；② 内置 vs 市场三层划分（底座能力不可拔 / 出厂内置积木 / 市场积木），记忆仅 memory-core 内置、8 扩展全走市场；③ skill-library 积木改造为 brick-market（热插拔入口）；④ GitHub 仓库路径结构更新为 bricks/ 唯一用户可见市场

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
| **阶段二** | **心脏归位（P3 独立运行时）** | **完成** |
| P4 | .dmg 打包 + 签名/公证 | 待办 |
| P5 | Shadeling 接入为第一个成品 | 待办 |
| P6 | 积木市场（brick-market 热插拔，brick-vault 在线浏览/安装） | 待办 |

## 阶段二待办（按批次）

- [x] B1 纯数据层：config / model_catalog / rules / textutil → brickery/runtime/（16 单测通过）
- [x] B2 引擎层：engine_router / engine_providers / supervisor → brickery/runtime/（23 单测通过；loop 依赖 B3/B5，待 B3 后迁）
- [x] B3 工具技能层：tools / skills / skill_library / sandbox / mcp / binary_manager + builtin_tools / tool_providers / doc_tools / repo_map / vault_store / vault_tool / docwrite / docwrite_pro / docwrite_templates / edsdk_pro → brickery/runtime/（77 单测通过；loop 依赖 B5 interoception，待 B5 后迁）
- [x] B4 记忆层：memory/ 包（17 文件）→ brickery/memory/，memory_providers → brickery/runtime/（54 单测通过）
- [x] B5 服务层：ipc / daemon / sessions / scheduler / gateway / confirm / interoception/ + loop → brickery/runtime/（195 单测通过；test_surfacing 迁回，memory 69 单测通过）
- [x] B6 产出链路：produce.py 打包 brickery-runtime（runtime+memory）进 .app/Contents/Resources/，run.sh 改独立运行时入口（python3 -m brickery.runtime.ipc），不再依赖宿主 shadeling 命令（e2e 全链路通过）

## 下一步

- **遗留拍板**：记忆系统 8 能力写死进内核后，对应 memory-* 积木彻底移除还是保留为开关（默认开可关）
- **P4 .dmg 打包**：重出包到 /Applications，重打 DMG 到桌面验证（用户：不急着打包）
- **P5 Shadeling 接入**为第一个成品
- **P6 积木市场**：brick-market 热插拔（BrickMarket 已就绪，接 web 工作台）

## 关键路径

- 平台代码：`/Users/suipu/Dev/brickery`
- 心脏来源：`/Users/suipu/Dev/Shadeling/runtime/`（迁移源）
- 积木库：`/Users/suipu/Dev/brick-vault`（不动）
- 规划文档：`specs/brickery.md`（平台规划）、`specs/rectify.md`（定位纠偏）、`specs/p3-runtime.md`（阶段二规划）
*（内容由AI生成，仅供参考）*
