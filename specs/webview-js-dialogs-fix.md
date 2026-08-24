# Spec: WKWebView JS 对话框静默吞没修复

## 背景

macOS 壳 `app/Sources/BrickeryApp/main.swift` 创建 `WKWebView` 时未设置 `uiDelegate`，导致 JS 侧 `alert` / `confirm` / `prompt` / 文件选择框被 WebKit 静默丢弃：

| 受影响功能 | JS 调用 | 用户表现 |
|---|---|---|
| 设置-通用「刷新状态」 | 无对话框，仅状态文本 | 刷新无任何反馈（含失败） |
| 数据与备份「更改」 | `prompt` | 无反应 |
| 数据与备份「保存目录」「一键备份」 | `alert` | 无反应 |
| 积木市场「导入积木包」 | 文件选择（`input[type=file]`） | 无反应 |
| 记忆柜「新建抽屉」 | `prompt` | 无反应 |

按钮逻辑实际已执行，只是结果对话框无法呈现，造成"按钮坏了"的观感。

## 方案

### 1. Swift 壳补 WKUIDelegate

`AppDelegate` 实现 `WKUIDelegate`，将四类 JS 交互桥接为原生 UI：

- `runJavaScriptAlertPanelWithMessage` → `NSAlert`（单按钮），回调 `completionHandler()`
- `runJavaScriptConfirmPanelWithMessage` → `NSAlert`（确定/取消），回调 `(Bool)`
- `runJavaScriptTextInputPanelWithPrompt` → `NSAlert` + `NSTextField` accessory，回调 `(String?)`
- `runOpenPanelWith` → `NSOpenPanel`（支持多选/目录），回调 `([URL]?)`

创建 `WKWebView` 后设置 `webView.uiDelegate = delegate`。

### 2. chat_ui.py 交互增强（配合修复）

- 一键备份：执行期间禁用按钮并显示「备份中…」，完成/失败后 `alert` 结果
- 刷新引擎状态：失败时 `alert` 明确报错，不再仅改状态文本

## 影响面

- 壳层为全局修复：工坊（Workbench）页面加载在同一个 `WKWebView` 内，其 `alert` / `confirm` 一并恢复
- 不改动前端业务逻辑，仅补 UI 呈现层
- 变更文件：
  - `app/Sources/BrickeryApp/main.swift`
  - `app/chat_ui.py`
  - 本 spec

## 验证

- `swift build` 通过
- `python3 -m py_compile app/chat_ui.py` 通过
- 重启 App 后逐项验证：刷新状态、更改目录 prompt、一键备份 alert、导入积木包文件选择、新建抽屉 prompt
