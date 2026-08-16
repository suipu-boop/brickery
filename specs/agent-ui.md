# 产出 agent 用户界面（agent-ui）方案

状态：待审阅
日期：2026-08-16

## 背景与问题

web 工作台产出 agent 并生成 DMG 后，打开 `.app` 只看到静态 `status.html` 状态页（"运行中" + IPC 端口），没有聊天/功能界面。用户期望打开后是"app 的内容"——一个可用的 agent 界面。

## 现状

- 产出的 `.app` launcher：后台启动 `brickery.runtime.ipc`（端口 18765）→ 打开 `status.html` 后退出。
- ipc 已有聊天能力：`_h_chat` / `_h_chat_stream`（IPC 协议层 JSON-RPC），但无 web 界面。
- `brickery/web/server.py` 是"组装工作台"（拖拽 UI），不是产出的 agent 的聊天界面。
- 打包态 runtime 在 `<app>/Contents/Resources/brickery-runtime/`。

## 方案：产出 agent 内置 web 聊天界面

给产出的 agent 增加一个轻量 web 聊天界面，launcher 打开浏览器访问本地聊天页。

### 关键决策

1. **界面形态**：web 聊天页（浏览器打开 `http://127.0.0.1:<port>/`），复用 ipc 的 `_h_chat` 能力。不做原生窗口壳（pywebview 等），保持轻量、与现有 web 技术栈一致。
2. **服务承载**：在打包的 runtime 内新增一个轻量 HTTP 服务（`brickery.runtime.webui`），与 IPC 服务同进程或独立端口。聊天页通过 HTTP 转发到 ipc 的 `_h_chat`。
3. **launcher 改动**：启动 IPC 后，打开浏览器访问 `http://127.0.0.1:<port>/` 而非 `status.html`。
4. **status.html 保留**：作为兜底（服务未就绪时显示），或改为聊天页的加载页。

### 实施步骤

1. 新增 `brickery/runtime/webui.py`：轻量 HTTP 服务，提供：
   - `GET /` → 聊天界面 HTML（内嵌 CSS/JS，无外部依赖）
   - `POST /api/chat` → 转发到 ipc `_h_chat`（非流式）
   - `POST /api/chat/stream` → 转发到 `_h_chat_stream`（SSE 流式）
2. `produce.py` 打包时把 `webui.py` 纳入 runtime 拷贝。
3. launcher 改为：启动 IPC + webui 后，`open http://127.0.0.1:<port>/`。
4. 聊天页功能：消息输入、流式输出、会话历史（内存态）、agent 名称/版本展示。

### 验收

- 打开产出的 `.app` → 浏览器出现聊天界面（非状态页）。
- 输入消息能收到 agent 回复（走 `_h_chat`）。
- 未配置引擎时给出引导提示（复用 ipc 现有"引擎未配置"逻辑）。

### 风险

- 端口冲突：webui 与 IPC 端口需错开（IPC 18765，webui 用 18766 或动态分配）。
- 打包态 import 路径：webui 需用 `brickery.runtime` 包内相对导入，避免开发态/打包态差异。
