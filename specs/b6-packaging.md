# B6 产出链路：独立运行时打包

> 阶段二心脏归位最后一步。目标：产出的 agent 不装 Shadeling / 不依赖开发环境，双击即跑。

## 现状缺口

`brickery/produce.py` 已固化 agent.json + bricks/ 快照 + run.sh + .app 骨架，但：

- `run.sh` 用 `RUNTIME_CMD="${BRICKERY_RUNTIME:-shadeling}"`，依赖外部 `shadeling` 命令
- `.app` 骨架的 launcher 调 `run.sh`，run.sh 又依赖外部 shadeling
- 即：产出物不是独立运行时，违背 brickery 差异化护城河（独立安装包 / 双击即跑）

## 目标结构

```
<name>.app/
  Contents/
    MacOS/launcher          ← 启动入口（定位 .app 内运行时）
    Resources/
      brickery-runtime/     ← 打包进来的独立运行时（B1–B5 全部）
      agent.json            ← 装配清单
      bricks/               ← 积木快照
```

## 改动方案

### 1. `_bundle_app`：打包运行时进 .app

- 在 `.app/Contents/Resources/` 下建 `brickery-runtime/`
- 复制 `brickery/runtime/`（B1–B5 全部模块）与 `brickery/memory/` 进 `brickery-runtime/`
- 复制 `agent.json` 与 `bricks/` 进 `Resources/`
- 排除 `__pycache__`、`tests/`、`fixtures/`（运行时不需要测试）

### 2. `_write_run_script`：run.sh 改入口

- 不再依赖外部 `shadeling` 命令
- 优先用 .app 内打包的运行时：`python3 -m brickery.runtime.ipc --home <agent_home>`
- 回退：环境变量 `BRICKERY_RUNTIME` 或系统 python3 + 打包的 brickery-runtime 路径
- 启动入口：`brickery.runtime.ipc`（IPC 服务，产出 agent 的宿主）

### 3. launcher：定位 .app 内运行时

- `APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"` 定位 .app
- 运行时在 `$APP_DIR/Contents/Resources/brickery-runtime/`
- 启动 `run.sh`（run.sh 内部用打包运行时）

## 验收标准

- `python3 scripts/e2e_produce.py` 产出 agent 包
- 产出目录含 `.app/Contents/Resources/brickery-runtime/`（有 runtime + memory 模块）
- run.sh 不出现 `shadeling` 命令依赖
- 单测全绿（runtime 195 + memory 69）
