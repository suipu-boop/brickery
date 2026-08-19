# 安装引导页对齐蓝本：Coding Plan 填写入口

> 日期：2026-08-19
> 范围：`brickery/runtime/setup_wizard.py`（端口 18766，三步向导 · 第一步 API 面板）
> 目标：对齐桌面 Shadeling 蓝本 OnboardingView.swift 的「自定义 Coding Plan」入口体验

## 背景

用户反馈：安装引导页（18766）没有 coding plan 的填数据页面，而桌面 Shadeling 蓝本中有。
现状核实：引导页预设下拉**已存在**隐藏选项「自定义 Coding Plan」（value=-1，选中后清空模板手填），
但可发现性差：无显眼入口、无专用端点提示、无示例文案，与蓝本差距明显。

## 蓝本对齐点（Shadeling/app/Sources/ShadelingApp/OnboardingView.swift）

1. 大厂普通 API 预设（点选自动填 URL+模型名）→ 引导页已有服务商预设下拉，保留
2. 显眼按钮「＋ 自定义 Coding Plan」：点击后清空 URL/Model，进入 Coding Plan 填写态
3. 显眼按钮「＋ 其他厂商普通 API」：点击后清空 URL/Model，进入普通手填态
4. 凭证输入区：API URL / API Key / API Model，回车落盘
5. codingHint 提示：各厂商 Coding Plan 是 OpenAI 兼容接口，但 Base URL 与普通 API 不同
   （填错会走普通额度）；示例：火山方舟 `https://ark.cn-beijing.volces.com/api/coding/v3`、
   腾讯混元 `https://api.lkeap.cloud.tencent.com/coding/v3`（对齐 SettingsView.swift 的
   DisclosureGroup 提示内容）

## 改动方案（仅 setup_wizard.py 前端 HTML/JS，不动后端数据模型）

### UI
- 在「服务商预设」下拉下方新增两个按钮（一行两列）：
  - `＋ 自定义 Coding Plan`
  - `＋ 其他厂商普通 API`
- 新增隐藏提示面板 `#codingHintPanel`（默认 display:none），内容为蓝本 codingHint 的
  中文文案 + 两个端点示例（火山方舟 / 腾讯混元 coding/v3），可复制。

### JS 状态
- 新增 `let planMode = "preset"`（preset | coding | custom）：
  - 预设下拉选择（含 -1 旧选项，保留兼容）→ planMode=preset / coding
  - 点「＋ 自定义 Coding Plan」→ planMode=coding：
    - 清空 api_url / api_model / api_key / api_name
    - 预填 api_name = "我的 Coding Plan"
    - placeholder 切换：API URL → `Base URL（Coding Plan 专用，如 …/api/coding/v3）`；
      模型名 → `模型名（Coding Plan 里的 endpoint ID / 模型名）`
    - 显示 `#codingHintPanel`
  - 点「＋ 其他厂商普通 API」→ planMode=custom：
    - 清空 api_url / api_model / api_name
    - 恢复默认 placeholder，隐藏 `#codingHintPanel`
- 总结页（第三步 summary）增加一行「模式：Coding Plan / 普通 API」

### 保存兜底
- `saveBtn` 提交 body 时：若 planMode=coding 且 api_name 为空，兜底 `api_name="我的 Coding Plan"`，
  便于聊天页/设置页识别。
- 后端 `_save_config` 无需改动（Coding Plan 本质就是一套 api_url/api_key/api_model，
  底座推理不区分额度类型）。

## 验收清单

1. 打开 18766 引导页，API 面板出现两个「＋」按钮
2. 点「＋ 自定义 Coding Plan」：表单清空、name 预填、placeholder 切换、提示面板展开（含两示例）
3. 点「＋ 其他厂商普通 API」：表单清空、placeholder 恢复、提示面板收起
4. 预设下拉仍可正常点选回填（含旧「自定义 Coding Plan」选项）
5. 保存后 summary 显示模式，config.json 中 api_name="我的 Coding Plan"（如未手改）
6. 保存跳转 18767 聊天页，设置页 API 卡片可见该 Coding Plan 配置
