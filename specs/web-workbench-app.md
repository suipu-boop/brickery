---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_3c9fb2249a1f11f18cca525400e6dd8f
    ReservedCode1: SOHfyf+ZS451ZOekLk6hNBFodu0GtMPE8MqR9hj1ZpeLj5UcG23pfXvlVR4CHxbx6cSuCoFiBKMqiN1W8aJT9nZ9a9+NIVJvTLq7lLUv1EdfLgvYyA/Lu3xlhaX6xXMY6RH5uSMMZ8BUo8bPmfIeOeD9dH+pvG+jXoNYoEDXzkaD+/a0DPDJ1YdOqK4=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_3c9fb2249a1f11f18cca525400e6dd8f
    ReservedCode2: SOHfyf+ZS451ZOekLk6hNBFodu0GtMPE8MqR9hj1ZpeLj5UcG23pfXvlVR4CHxbx6cSuCoFiBKMqiN1W8aJT9nZ9a9+NIVJvTLq7lLUv1EdfLgvYyA/Lu3xlhaX6xXMY6RH5uSMMZ8BUo8bPmfIeOeD9dH+pvG+jXoNYoEDXzkaD+/a0DPDJ1YdOqK4=
---

# 积木平台打包为独立 app（web-workbench-app）

> 状态：方案待确认（2026-08-17）
> 目标：把积木平台（web 工作台）打包成与 suipu-assistant 同级的稳定 macOS app

## 1. 背景与可行性

积木平台当前形态：`brickery/web/server.py`（纯标准库 HTTP 服务，端口 8765）
+ `web/index.html`（单文件前端，604 行），用户通过浏览器访问 `http://127.0.0.1:8765` 使用。

用户诉求：像 suipu-assistant 一样做成**稳定 app**，避免通用 agent 安装过程下载依赖失败。

**可行性结论：能，且比 suipu-assistant 更简单**：

| 对比项 | suipu-assistant | 积木平台 |
|--------|----------------|---------|
| 服务依赖 | ipc/setup_wizard/chat_ui + llama_cpp + numpy | 仅 `http.server`（纯标准库） |
| 第三方依赖 | llama-cpp-python 等需内嵌 | **零第三方依赖** |
| 前端 | 多页面 | 单文件 index.html |
| 端口 | 18765/18766/18767 | 8765 |

## 2. 方案总览

复用 suipu-assistant P4 打包链路（specs/p4-packaging.md），差异点仅在于服务与前端来源。

| 项 | 方案 | 落点 |
|----|------|------|
| Python 解释器 | python-build-standalone（astral，macOS arm64，CPython 3.12.x） | `.app/Contents/Resources/python/` |
| brickery 代码 | 内嵌 brickery 包（含 web/server.py + assembler + produce） | `.app/Contents/Resources/brickery-runtime/` |
| 前端 | 内嵌 web/index.html（随 brickery 包） | 同上 |
| 积木库 vault | 保持 `~/.brickery/vault`（用户数据，不随包） | 不变 |
| 产出目录 agents | 保持 `~/.brickery/agents` | 不变 |
| 启动入口 | Swift 壳拉起内嵌 python 跑 `python -m brickery.web.server` | `.app/Contents/Resources/python/bin/python3` |
| 界面 | WKWebView 内嵌加载 `http://127.0.0.1:8765`，无浏览器 UI | 复用 native-webview 壳 |

## 3. 实施步骤

1. **Swift 壳改造**：`app/Sources/BrickeryApp/main.swift` 增加积木工作台模式
   - 启动 `python -m brickery.web.server --port 8765`（内嵌 python + PYTHONPATH 指向 brickery-runtime）
   - WKWebView 加载 `http://127.0.0.1:8765`
   - 端口占用检测（lsof）避免重复拉起；父进程退出即杀子进程
   - 服务日志落 `~/Library/Application Support/BrickeryApp/web.log`
2. **打包脚本**：新增 `scripts/build_workbench_app.sh`（或复用 produce.py 的 `_bundle_app`）
   - 内嵌 python（复用 P4 已下载的 python-build-standalone）
   - 拷贝 brickery 包 + web/index.html 到 Resources/brickery-runtime
   - 编译 Swift 壳 → 组装 .app → 生成 DMG
3. **验证**：安装后启动，确认工作台可组装/产出，vault 与 agents 数据不丢失

## 4. 与 suipu-assistant 的边界

- 积木平台 app 与 suipu-assistant app 是**两个独立 app**，互不干扰
- 共用 `~/.brickery/vault` 与 `~/.brickery/agents`（同一份积木库与产出）
- 端口 8765 与 suipu-assistant 的 18765-18767 不冲突

## 5. 风险与对策

| 风险 | 对策 |
|------|------|
| 内嵌 python 体积（40-60MB） | 可接受，与 P4 一致 |
| vault 缓存落后本地推送 | 沿用既有 sync 机制，app 内可加「从 GitHub 同步」按钮（后续迭代） |
| 端口被占用 | 启动前 lsof 检测，占用则提示 |
*（内容由AI生成，仅供参考）*
