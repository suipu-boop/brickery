# 产出 agent 界面：复用底座 chat_ui（废弃轻量 web 方案）

> 2026-08-16 随朴 落盘。回应质疑：底座本有 app 页面，为何另起轻量 web。

## 结论

用户质疑成立。底座**已有完整聊天界面** `brickery/runtime/chat_ui.py`（127.0.0.1:18767，
工坊蓝图风，版面照搬 Shadeling 形态，走引擎路由，未配置引擎时跳转安装引导页 18766）。
此前 specs/agent-ui.md 提"新增 webui.py 轻量 web 聊天界面"是**重复造轮子**，废弃。

## 根因

产出 .app 的 launcher（produce.py `_bundle_app`）只做了两件事：

1. 后台启动 IPC（端口 18765）
2. 打开 `status.html` 静态状态页

**从未启动 chat_ui 服务，也未打开聊天页**。故打开 .app 只见状态页、无聊天界面。
chat_ui.py 已随 `_bundle_runtime` 整体复制进 .app 的 brickery-runtime，无需新增文件。

## 方案（改 produce.py launcher）

launcher 启动序列改为：

1. 后台启动 IPC：`python3 -m brickery.runtime.ipc --home "$DATA_DIR" --app-resources "$RESOURCES"`
2. 后台启动聊天界面：`python3 -m brickery.runtime.chat_ui --home "$DATA_DIR"`（端口 18767）
3. 打开 `http://127.0.0.1:18767/` 聊天页（替代 status.html）

细节：

- 端口占用检测：18765（IPC）与 18767（chat_ui）任一在跑即视为已运行，直接打开聊天页
- chat_ui 未配置引擎时自带跳转安装引导页（setup_wizard 18766），安装引导链路天然打通
- status.html 保留为兜底（聊天页打不开时回退），不再作为默认打开页
- run.sh（开发态）同步：`exec python3 -m brickery.runtime.ipc` 后追加拉起 chat_ui

## 验收

- 重新 produce 产出 .app，双击打开 → 直接进入聊天界面（非状态页）
- 未配置引擎 → 聊天页引导跳转安装引导页，配置后回到聊天页可对话
- 二次双击不重复起服务，直接打开聊天页

## 实施状态（2026-08-16 已落地）

- produce.py `_bundle_app` launcher：启动 IPC 后追加拉起 chat_ui（BRICKERY_HOME=$DATA_DIR），
  打开 http://127.0.0.1:18767/ 聊天页；端口占用检测改判 18767；status.html 保留为兜底
- `_write_run_script` run.sh（开发态）：同步改为后台拉起 IPC + chat_ui 并打开聊天页
- 验证：重新 produce 产出 web-test-agent，双击启动后 18767 监听、页面/`/api/engine`/`/api/chat`
  均正常响应；未配置引擎时正确返回引导（guide_url 18766）
- 单测 266 passed 无回归

## 安装引导修复（2026-08-16 追加）

**根因**：launcher 只拉起 IPC + chat_ui，未拉起 setup_wizard（18766），且首次启动直接打开
聊天页——chat_ui 里跳转 18766 是死链，违背 Shadeling 安装引导流程（首次启动先进引导页）。

**修复**（produce.py launcher + run.sh 同步）：

1. 后台拉起三个服务：IPC（18765）+ setup_wizard 安装引导（18766）+ chat_ui（18767）
2. 首次启动（数据目录无 config.json）→ 打开安装引导页 18766，配置引擎写 config.json
3. 已配置（config.json 存在）→ 打开聊天页 18767
4. 端口占用检测：18766/18767 任一在跑即视为已运行，按 config.json 是否存在打开对应页

**验证**：清空数据目录模拟首次启动，双击 .app → 18766 引导页打开（八家厂商预设正常），
18767 聊天页服务就绪；配置引擎后二次启动走聊天页分支。
