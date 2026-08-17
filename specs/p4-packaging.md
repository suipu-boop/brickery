# P4 打包方案：内嵌 Python + llama_cpp + 本地小模型推荐下载

> 2026-08-17 用户拍板：内嵌新版 Python、内嵌 llama_cpp、本地小模型保持智能推荐下载（不随包携带）。
> 本文档为 P4 唯一设计依据，实施后更新状态。

## 1. 背景与目标

当前产出 .app 不打包 Python 解释器（依赖系统 python3，macOS 自带 3.9.6 可能缺失）、
不打包 llama_cpp、不携带 GGUF 模型。导致目标机可能无法启动、本地推理不可用。

用户明确要求（2026-08-17）：
1. **内嵌新版 Python**（非系统 3.9，用新版独立发行版）
2. **内嵌 llama_cpp**（本地推理依赖，随包携带）
3. **本地小模型保持智能推荐下载**（不随包携带，安装引导页推荐下载，用户主动触发）
   —— 本地小模型是特色任务所需，很多小任务依赖它，不能丢
4. **本地小模型定位（2026-08-17 追加拍板）：只做规划任务，不做聊天兜底和推理**
   —— 本地小模型质量太差，不用于面向用户的聊天回复/推理兜底；
      仅用于规划类任务（任务拆解 / 记忆整理 / 语义嵌入等结构化工作）

## 2. 方案总览

| 项 | 方案 | 落点 |
|----|------|------|
| Python 解释器 | python-build-standalone（astral 发行版，macOS arm64，CPython 3.12.x） | `.app/Contents/Resources/python/` |
| llama_cpp | llama-cpp-python 预编译 wheel（macOS arm64 + Metal） | 内嵌 python 的 site-packages |
| 本地小模型 | 保持现状：setup_wizard 推荐下载，用户主动触发，不随包携带 | 不变 |
| 启动入口 | run.sh / launcher 改调内嵌 python | `.app/Contents/Resources/python/bin/python3` |

## 3. 内嵌 Python（python-build-standalone）

### 3.1 为什么选 python-build-standalone

- astral（uv 团队）维护的独立 CPython 发行版，macOS arm64 直接解压即用，无需编译
- 自带 pip，可离线安装 llama-cpp-python wheel
- 体积约 40-60MB（含标准库），可接受
- 版本：CPython 3.12.x（新版，非系统 3.9）

### 3.2 下载与打包

- 下载源：`https://github.com/astral-sh/python-build-standalone/releases`
  - 文件名形如 `cpython-3.12.x-aarch64-apple-darwin-install_only.tar.gz`
- 打包进 `.app/Contents/Resources/python/`（解压后目录结构：`bin/python3`、`lib/python3.12/`）
- 内嵌 python 的 site-packages 预装：`llama-cpp-python`（见 §4）

### 3.3 启动入口改造

- `run.sh`：`python3` → `$APP_DIR/Contents/Resources/python/bin/python3`
- `_bundle_native_shell` 的 launcher（Swift 壳）若直接调 python3，同样改调内嵌 python
- 环境变量：`PYTHONHOME` / `PYTHONPATH` 指向内嵌 python 与 brickery-runtime

## 4. 内嵌 llama_cpp

### 4.1 方案

- 用内嵌 python 的 pip 安装 `llama-cpp-python`（macOS arm64 预编译 wheel，带 Metal 支持）
- 安装到内嵌 python 的 site-packages，随 .app 打包
- 版本对齐当前已验证的 0.3.34（本机 llama_cpp 0.3.34 可用）

### 4.2 打包方式

- 在产出时（produce.py）用内嵌 python 执行：
  `$PYTHON/bin/pip install --no-deps llama-cpp-python==0.3.34`
  （--no-deps 避免拉入多余依赖；llama-cpp-python 依赖 numpy，需一并内嵌）
- 或：预先把 wheel 下载到本地缓存，产出时离线安装（网络不稳时兜底）

### 4.3 依赖

- llama-cpp-python 依赖 numpy，需一并内嵌（pip 正常安装即可）

## 5. 本地小模型：只做规划任务（2026-08-17 拍板）

- **不随包携带 GGUF**：模型权重体积大（2-5GB），不打包进 .app
- **保持现状**：setup_wizard 安装引导页展示 model_catalog 精选 GGUF 目录，
  按本机内存推荐，用户点「下载」主动触发（hf-mirror 镜像，标准库 urllib）
- **定位（重要变更）**：本地小模型**只做规划任务，不做聊天兜底和推理**
  - 不做：面向用户的聊天回复、推理兜底（断网/额度耗尽时的自动降级）——质量太差
  - 只做：规划类结构化任务，如任务拆解/规划、记忆整理（summarize/consolidation）、
    语义嵌入（embed，bge 模型）等
- **影响**：引擎路由（engine_router.py）中本地 GGUF 不再作为聊天/推理的自动降级兜底；
  聊天与推理一律走 API；本地小模型仅服务规划类任务
- **特色保留**：规划类小任务（影子记忆、任务拆解、语义检索）依赖本地小模型，
  内嵌 llama_cpp 后开箱即用，下载模型即可跑

## 6. 实施步骤

1. 下载 python-build-standalone（macOS arm64，CPython 3.12.x）到 temp/
2. 解压验证：内嵌 python 可运行、pip 可用
3. 用内嵌 python 安装 llama-cpp-python==0.3.34（含 numpy）
4. 改造 `produce.py`：
   - `_bundle_runtime` 增加内嵌 python 打包
   - `_write_run_script` 改调内嵌 python
   - launcher（Swift 壳）改调内嵌 python
5. 重产出 suipu-assistant，重打 DMG 到桌面验证
6. 端到端实测：本地 GGUF 推理 + API 推理 + 积木调用

## 7. 验收标准

- 目标机无系统 python3 也能启动（内嵌 python 兜底）
- 本地 GGUF 推理开箱即用（内嵌 llama_cpp）
- 本地小模型仍走安装引导页智能推荐下载（不随包携带）
- 全量单测通过

## 8. 风险与对策

| 风险 | 对策 |
|------|------|
| python-build-standalone 下载失败（github 不稳） | 本地缓存 wheel/发行版，产出时离线复用；或换镜像源 |
| llama-cpp-python wheel 与内嵌 python 版本不匹配 | 锁定 3.12.x + 0.3.34 组合，先本机验证再打包 |
| 包体积增大（+40-60MB python + llama_cpp） | 可接受；GGUF 仍不随包，体积可控 |
| 内嵌 python 与系统 python 冲突 | 启动入口显式用内嵌 python，不污染系统环境 |

## 9. 历史经验加固（2026-08-17 复盘，防再踩坑）

以下为过往执行中沉淀的关键点，P4 实施必须遵守：

### 9.1 稳定优先（用户核心诉求，最高优先级）

- 用户做 app 形式软件的核心目的：**稳定**，避免"安装过程下载各种依赖导致失败"的体验
- 用户明确反感：安装时还需下载/编译各种依赖导致失败
- 因此：**内嵌 Python + llama_cpp 随包携带，运行时零 pip 安装、零编译**；
  本地模型引擎走"可选下载"而非强制前置（用户主动触发才下载 GGUF）
- 验收红线：目标机**无系统 python3、无网络**也能启动并完成 API 推理

### 9.2 打包来源优先级（b19 经验，P4 同样适用）

- `_bundle_runtime` 优先打包 `~/.brickery/base/brickery`（GitHub 底座）而非本地仓库
- **本地改动须先 `cp` 到 base 再 produce 才进包**，否则产出的是旧代码
- P4 内嵌 python / llama_cpp 的安装产物同样要确认来源正确（base 优先）

### 9.3 重打 DMG 用 hdiutil create

- 系统 python3 缺 dmgbuild，`dmg.py` 不可用
- 重打 DMG 一律用 `hdiutil create`（历史已验证可行）

### 9.4 挂载验证路径坑

- 挂载验证时**取 `/Volumes/<name>` 而非设备名 `/dev/diskXXsX`**（曾取错导致验证失败）

### 9.5 删除旧产出用 delete 工具

- 删除旧产出目录用 delete 工具（进回收站）；shell rm 会被 DEL-201 规则拦截

### 9.6 服务进程加载旧代码坑

- web 工作台 / 服务进程若加载旧代码（如旧 assembler.py 无 files 字段），
  会导致 plan.files 为空、实现文件不落盘
- 改动代码后必须**重启服务进程**再产出，不能复用旧进程

### 9.7 本地小模型是特色，但只做规划任务（2026-08-17 拍板修正）

- 很多小任务（影子记忆、任务拆解、语义检索）依赖本地小模型
- 内嵌 llama_cpp 后本地推理开箱即用，GGUF 仍走安装引导页智能推荐下载
- **重要修正**：本地小模型**只做规划任务**（任务拆解/规划、记忆整理、语义嵌入），
  **不做聊天兜底和推理**——质量太差，面向用户的聊天与推理一律走 API
- 这是产品特色，不是可选项；但特色边界 = 规划类任务，不是聊天兜底

### 9.8 蓝本对齐

- 蓝本 = Shadeling 原版（MODEL_SETUP.html 等）
- 内嵌新版 python（3.12）后，蓝本中依赖新版 python 的改动才能抄过来
- 实施时对照蓝本逐项核对可迁移改动

### 9.9 GitHub 网络不稳

- github.com:443 常超时，下载 python-build-standalone 可能失败
- 对策：先本地 commit 就位，下载/推送延后待网络恢复重试，不盲重试

### 9.10 工作流约束

- 写代码只查 `specs/engine-interfaces.md` 接口速查表，不翻源码
- 核心代码改动前先落盘设计文档给用户审阅点，用户拍板后再实施
- 改动落地后须主动提示未 commit；用户口述「提交」=commit，「执行/推送」=push

## 状态

- [x] 设计定稿（2026-08-17）
- [ ] 下载 python-build-standalone
- [ ] 内嵌 python + llama_cpp 验证
- [ ] produce.py 改造
- [ ] 重产出 + 重打 DMG 验证
- [ ] 端到端实测
