# setup_wizard 引导页分步化 + 文件夹选择器

## 背景
用户反馈产出 agent 的引导页对新手不友好：
1. 输入后没有「下一步」按钮，所有内容一页到底，无引导节奏。
2. 「数据与备份」的备份/产出文件夹只能手输路径，不能通过系统对话框选择。

## 方案

### 1. 分步引导（3 步）
- 第一步 · 选择推理后端：服务商预设下拉 + API URL / Key / 模型 / 本地模型（现有内容）
- 第二步 · 数据与备份：备份文件夹 + 产出文件夹（现有内容）
- 第三步 · 完成：汇总已填配置 + 「保存配置」「验证配置」按钮

交互：
- 每步底部「上一步 / 下一步」按钮，切换时显示/隐藏对应 `<section>`。
- 第一步无「上一步」，第三步无「下一步」。
- 保存/验证按钮移到第三步。
- 步骤指示器（1/2/3 高亮当前步）。

### 2. 文件夹选择器
- 每个文件夹输入框旁加「选择…」按钮。
- 点击调后端新接口 `POST /api/pick_folder`。
- 后端用 `osascript` 调 macOS 原生 `choose folder` 对话框（无第三方依赖），返回选中路径。
- 前端把返回路径填入对应输入框（用户仍可手改）。

### 3. 后端接口
```python
# POST /api/pick_folder
# 返回 {"ok": true, "path": "/Users/xxx/..."} 或 {"ok": false, "error": "..."}
import subprocess
r = subprocess.run(
    ["osascript", "-e", 'POSIX path of (choose folder with prompt "选择文件夹")'],
    capture_output=True, text=True, timeout=120)
if r.returncode == 0:
    return {"ok": True, "path": r.stdout.strip()}
return {"ok": False, "error": r.stderr.strip()}
```

## 影响范围
- 仅改 `brickery/runtime/setup_wizard.py`（HTML 结构 + JS + 一个后端接口）。
- 不涉及引擎逻辑、积木、produce 打包。
- 改后同步 `~/.brickery/base`，重打 DMG。
