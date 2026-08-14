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

> 阶段恢复锚点：每轮续做先读本文件对齐当前阶段与待办。

## 定位（一句话）

brickery = 平台（拥有心脏/内核运行时），Shadeling = 它产出的品牌产品。产出 agent 本地独立运行，不依赖 Shadeling。

## 当前状态

**阶段一断寄生：已完成**（2026-08-15）
- Shadeling 内组装/积木代码已全部移除（commit `5cc35b5`，已 push）
- 工厂能力全部归 brickery

**阶段二心脏归位：规划已落盘，待开工**
- 规划文档：`specs/p3-runtime.md`
- 状态：待用户审阅拍板后开工

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

- [ ] B1 纯数据层：config / model_catalog / rules / textutil → brickery/runtime/
- [ ] B2 引擎层：engine_router / engine_providers / loop / supervisor（跑通独立对话）
- [ ] B3 工具技能层：tools / tool_providers / builtin_tools / sandbox / mcp / skills / skill_library / binary_manager
- [ ] B4 记忆层：memory/ 包 / memory_providers / vault_store
- [ ] B5 服务层：ipc / daemon / sessions / scheduler / gateway / confirm / interoception
- [ ] B6 产出链路：produce.py 打包运行时进 .app，run.sh 改入口

## 下一步

**B1+B2 引擎层迁移**：把 config / model_catalog / rules / textutil / engine_router / engine_providers / loop / supervisor 迁入 `brickery/runtime/`，跑通产出 agent 独立对话。

## 关键路径

- 平台代码：`/Users/suipu/Dev/brickery`
- 心脏来源：`/Users/suipu/Dev/Shadeling/runtime/`（迁移源）
- 积木库：`/Users/suipu/Dev/brick-vault`（不动）
- 规划文档：`specs/brickery.md`（平台规划）、`specs/rectify.md`（定位纠偏）、`specs/p3-runtime.md`（阶段二规划）
*（内容由AI生成，仅供参考）*
