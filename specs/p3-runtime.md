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

# 阶段二：心脏归位（P3 独立运行时）规划

> 状态：**待用户审阅拍板**
> 背景：阶段一断寄生已完成（Shadeling 内组装代码已清空）。本阶段把**心脏（agent 内核运行时）**从 Shadeling 抽到 brickery，让产出 agent 自带运行时、本地独立运行，不再依赖 Shadeling 进程。

## 1. 目标（一句话）

**brickery 产出的 agent 自带内核运行时，用户拿到安装包双击即跑，不装 Shadeling、不依赖它。**

## 2. 现状 vs 目标

| | 现状 | 目标 |
|--|------|------|
| 心脏在哪 | Shadeling `runtime/` | brickery 自带 |
| 产出 agent 怎么跑 | `run.sh` 写 `shadeling run agent.json` | 双击 .app 即跑，自带运行时 |
| 依赖 | 依赖 Shadeling 进程 | 零依赖（除用户自配模型 API） |

## 3. 迁移范围（心脏 + 外围）

### 3.1 心脏核心（铁律：不积木化，原样搬）

| 模块 | 职责 | 内部依赖 |
|------|------|----------|
| `supervisor.py` | 主管/生命周期 | 无（纯标准库） |
| `loop.py` | 对话主循环 | engine_router / skills / tools / interoception |
| `engine_router.py` | 引擎路由 | config |
| `ipc.py` | 进程间通信 | 见 3.2 全部 |

### 3.2 心脏依赖的外围（一起搬）

| 模块 | 职责 |
|------|------|
| `config.py` / `model_catalog.py` / `rules.py` / `textutil.py` | 配置/模型/规则/文本 |
| `engine_providers.py` | 引擎接入（local/api） |
| `tools.py` / `tool_providers.py` / `builtin_tools.py` / `sandbox.py` / `mcp.py` | 工具层 |
| `skills.py` / `skill_library.py` / `binary_manager.py` | 技能/二进制 |
| `sessions.py` / `scheduler.py` / `daemon.py` / `gateway.py` / `confirm.py` | 会话/调度/守护/网关/确认 |
| `interoception/` | 内感系统 |
| `memory/` 包（16 文件）+ `memory_providers.py` + `vault_store.py` | 记忆系统 |

## 4. 分批迁移计划（先迁后断，每批验证）

| 批次 | 内容 | 验证 |
|------|------|------|
| **B1 纯数据层** | config / model_catalog / rules / textutil | 单测通过 |
| **B2 引擎层** | engine_router / engine_providers / loop / supervisor | 单测通过 |
| **B3 工具技能层** | tools / tool_providers / builtin_tools / sandbox / mcp / skills / skill_library / binary_manager | 单测通过 |
| **B4 记忆层** | memory/ 包 / memory_providers / vault_store | 单测通过 |
| **B5 服务层** | ipc / daemon / sessions / scheduler / gateway / confirm / interoception | 冒烟测试通过 |
| **B6 产出链路** | produce.py 打包运行时进 .app；run.sh 改入口 | e2e：产出 agent 不装 Shadeling 独立跑通 |

每批：brickery 内建 `brickery/runtime/` 目录，模块原样迁入（改包名 import），跑 Shadeling 对应测试验证；Shadeling 侧保留薄引用或逐步切换。

## 5. 产出 agent 的目标结构

```
<name>.app/
  Contents/
    MacOS/launcher          ← 启动入口
    Resources/
      brickery-runtime/     ← 打包进来的独立运行时（B1–B5 全部）
      agent.json            ← 装配清单
      bricks/               ← 积木快照
```

`run.sh` 改为：`exec "$APP_DIR/Contents/Resources/brickery-runtime/run" agent.json`，不再出现 `shadeling`。

## 6. 验收标准

- 不装 Shadeling 的机器上，产出 agent 双击 .app 能本地独立运行
- `run.sh` / `agent.json` 中不再出现 `shadeling` 字样
- Shadeling 本体功能不受影响（先迁后断）

## 7. 风险

- 迁移量大（36 个 runtime 模块 + 16 个 memory 模块），需分批、每批验证
- `ipc.py` 132KB 大文件，迁移时需拆解（服务层与协议层分离）
- 打包体积：运行时进 .app 会增大安装包，属预期
- 模型 API 由用户自配，运行时只负责调用，不内置密钥

## 8. 建议

- 阶段二工程量大，建议**按批次推进**：先 B1+B2（引擎层跑通），验证产出 agent 能独立对话，再补 B3–B5，最后 B6 打包
- 每批完成即本地 commit，push 等网络稳定
*（内容由AI生成，仅供参考）*
