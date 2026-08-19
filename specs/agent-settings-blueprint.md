---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_d0a563179bb911f18cca525400e6dd8f
    ReservedCode1: rySzx6E16CEvC1bgl+Zvto8Yzy45epqdZN1+wkkN7A0gaQxMM2wGg6cBstl9An9UXF0WuRj8ed/tmYyDmrKQjBEGV7kJbfzmAprZhEU3pL4DaLtMKYqJ1bYdxoTORoR4I7pkVTZPXFWXvxwFd3JfXogtDTpq43/dCT0X9uIWFH+VQW4VPmefAdOvfvE=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_d0a563179bb911f18cca525400e6dd8f
    ReservedCode2: rySzx6E16CEvC1bgl+Zvto8Yzy45epqdZN1+wkkN7A0gaQxMM2wGg6cBstl9An9UXF0WuRj8ed/tmYyDmrKQjBEGV7kJbfzmAprZhEU3pL4DaLtMKYqJ1bYdxoTORoR4I7pkVTZPXFWXvxwFd3JfXogtDTpq43/dCT0X9uIWFH+VQW4VPmefAdOvfvE=
---

# 产出 agent 两问题修复设计（引导跳转 + 设置页对齐蓝本）

> 状态：待实施（2026-08-19）
> 范围：`brickery/runtime/setup_wizard.py`、`brickery/runtime/chat_ui.py`（底座，非积木）
> 背景：用户测试产出 agent（桌面 shadeling），发现 ① 引导安装后未直接进入聊天页；② 设置界面布局乱，未按 Shadeling 蓝本分区。已核实底座仓库与 base 缓存、产出包内代码一致，问题为功能本身未实现。

## 一、修复 1：引导保存后自动进入聊天页

### 根因
`setup_wizard.py` 的 `saveBtn.onclick` 保存成功仅 `setStatus(st, "配置已保存", true)`，无跳转。用户停留在 18766 引导页，需手动另开 18767。

### 方案
- 前端 `saveBtn` 保存成功回调里，延时 800ms 后 `location.href = "http://127.0.0.1:18767/"`（launcher 已同时拉起 chat_ui，端口固定 18767）。
- 保存失败仍停留在引导页并提示错误。
- 附带：`verifyBtn` 验证成功后不跳转（验证≠完成配置，保持现状）。

### 改动点
`brickery/runtime/setup_wizard.py`：`saveBtn.onclick` 内 `if (r.ok)` 分支追加跳转。

## 二、修复 2：设置页按蓝本分区布局

### 蓝本结构（SettingsView.swift 分区）
| 分区 | 内容 |
|------|------|
| 通用 | 语言、**引擎状态**（当前后端/本地是否就位+探测路径/网络是否绑定/规模/刷新按钮）、执行模式、红线说明 |
| 模型 | 后端按钮式切换（本地 GGUF / 网络 API）、本地模型区 或 **网络模型卡片网格** + 新建/编辑表单（厂商分组选择器 + 自定义 Coding Plan + 名称/URL/Key/Model） |
| 记忆 | 夜间巩固、会话回顾、通知开关 |
| 数据与备份 | 备份/产出文件夹（打开/更改）、一键备份、导出/恢复 |
| 关于 | 版本、作者、重新运行首次引导 |

### 当前 chat_ui.py 设置页问题
`renderSettings` 为 `grid2` 左右两栏：左=引擎配置（后端下拉+本地模型+保存），右=网络API预设卡片+模型目录+其他（备份/产出/超时/开关）。功能大体齐备（引擎状态、API 卡片、coding plan 入口已有），但**分区混杂、无层次**，与蓝本观感差距大。

### 方案：renderSettings 重排为纵向分区（单列 Stack）
按蓝本顺序渲染五个卡片区块：
1. **引擎状态**：当前后端 / 本地模型（就位/未找到+路径）/ 网络 API（已绑定/未绑定+模型）/ 规模 /「刷新状态」按钮（复用 `renderEngineStatus`）。
2. **后端选择**：本地 GGUF / 网络 API 按钮式切换（复用 `cfgBackend`，改为两个大按钮高亮选中态，保存即生效）。
3. **模型配置**：
   - 本地档：本地模型路径 + 选择按钮 + 模型目录列表（现有）。
   - 网络档：网络 API 预设卡片网格（现有 apiCards）+「新建网络模型」主按钮 + 编辑表单（名称/URL/Key/Model/超时 + 厂商分组选择器 + 自定义 Coding Plan 提示，现有 modal 基础上补厂商分组下拉）。
4. **数据与备份**：备份/产出文件夹（打开/更改/一键备份/导出/恢复按钮，`backup_*`/`open_folder` 白名单已有）。
5. **关于**：版本、作者、链接、「重新运行首次引导」（删除 onboarding 标记后提示重开引导页）。

### 保留不动
- 超时、工具/技能开关 → 归入「通用」区底部（单列内一行）。
- `saveConfig`/`renderApiCards`/`openApiModal` 等函数复用，仅 HTML 结构重排 + 少量 CSS（分区卡片 `section-card` + 区块标题）。

### 改动点
- `brickery/runtime/chat_ui.py`：重写 `renderSettings` HTML 结构；`saveConfig` 增加 `set_mode`/`open_folder` 等既有白名单能力入口；CSS 加 `section-card` 样式。
- 不动后端 handler（IPC 白名单已覆盖全部所需）。

## 三、同步与验证
1. 改完先本地语法自测（`python -m py_compile`）。
2. 同步 `~/.brickery/base/brickery`（cp 覆盖 chat_ui.py / setup_wizard.py）。
3. 由主链路重新 produce 产出 agent（桌面 shadeling），确认包内代码更新。
4. 用户重装后验证：引导保存 → 自动进聊天页；设置页分区布局与蓝本一致。

## 四、待确认
- 设置页是否需要「执行模式」「记忆开关」等蓝本区块（当前 chat_ui 侧无对应前端，但 IPC 有 `set_mode`）？默认本轮先做布局对齐 + 既有功能重排，执行模式/记忆开关作为可选补充，如需要再补。
*（内容由AI生成，仅供参考）*
