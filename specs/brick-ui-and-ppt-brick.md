---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_9ce5d797a22b11f1bc17525400826444
    ReservedCode1: fscHB2NTYzb0Re+yTjj6rjfwSWEaOiVrM8UlXGoi06DwRGfJXxiGtiX0QUMgx0gXl7k+egob3FQXG7C5ut36B7V+7XjYlE20odfDj2cVRIaQrVYAf1k9Y3/Rxdkn/iA1uWzOOxAvsvMmGcidfKc6UlDDtb5p7WD/P+nMch7/yMnn8dvhcKG1MPxIcwk=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_9ce5d797a22b11f1bc17525400826444
    ReservedCode2: fscHB2NTYzb0Re+yTjj6rjfwSWEaOiVrM8UlXGoi06DwRGfJXxiGtiX0QUMgx0gXl7k+egob3FQXG7C5ut36B7V+7XjYlE20odfDj2cVRIaQrVYAf1k9Y3/Rxdkn/iA1uWzOOxAvsvMmGcidfKc6UlDDtb5p7WD/P+nMch7/yMnn8dvhcKG1MPxIcwk=
---







# 积木自带 UI 机制设计 + 全功能 PPT 生成积木（brick-ui-and-ppt-brick）

> 状态：待审阅（V0.1，基于真实代码核对，未经用户拍板前不改代码）
> 适用范围：brickery 平台（内核 brickery + 运行 runtime shadelingmac0.0.1.app + 积木库 brick-vault）
> 相关调研输入：GitHub 开源 PPT 生成工具调研报告（Search Agent，2026-08）

---

## 一、现状盘点（基于代码事实）

### 1.1 积木 / 技能体系形态

brickery 平台存在四个仓库角色：**内核 / 工坊 / 工厂 / 运行 runtime**（`brickery`、`brickery-workbench`、`brickery-factory`、`shadelingmac0.0.1.app`）。

积木形态上，平台目前共有这样几类：
- **Prompt 类**：skill 包携带 `content`（注入主循环的完整上下文提示），运行时按 `trigger` 关键词在对话中被动触发。这是当前默认形态，前端不渲染任何按钮/卡片/加工界面。
- **Connector 类**：如 feishu（WebSocket 桥），走独立连接器链路。
- **Tool 类（ToolBrick）**：`brick.json` 声明 `provides_tool`，经由 `ToolProviderRegistry` 委托内核内置 handler，**技能包本身不携带可执行代码**。代表：`docwrite`。
- **Binary 类**：市场高配技能走 `binary_url/binary_sha256/binary_launch` 扩展字段（Skill dataclass 已具备字段）。
- **ServiceBrick（Portal 类）**：brickery 内核中不存在独立的"ServiceBrick"类型定义；`category` 字段是自由字符串（`tool` / `connector` / `内置` / 其它），不对应独立 dataclass。

分层关系：**Skill 与 brick 是同一概念的分层**——`runtime/skills.py` 的 `SkillRegistry` 管运行时匹配/落盘（叫 skill），工坊/积木市场策展时叫 brick；两边以同一份 `brick.json` 为唯一事实源（见 `brickery/brickery/skill_contract.py` 迁移说明）。

**积木库登记事实**（`/Users/suipu/Dev/brick-vault/`）：
- 顶层登记文件是 **`index.json`**（`schema: "brick-registry/v1"`，`bricks[]`，每条含 `name/version/category/risk_level/summary/path`），另有 `skills/index.json`。
- **仓库中不存在 `bronze.json`**（此前任务描述提到的"bronze.json"经核对不存在；积木登记事实文件为 `index.json`，后续登记均落此文件）。
- `docwrite/brick.json`：`category: tool`、`provides_tool: DocWrite`、`source: builtin`、`capabilities` 含 `tool.docwrite / doc.generate.docx / doc.generate.xlsx / doc.generate.pptx`，6 套模板（business-blue / forest-green / sunset-orange / mono-gray / royal-purple / midnight-dark）。**该文件无 `buttons` 字段。**

### 1.2 brick.json 契约与 Skill dataclass 结构

权威契约在 `brickery/brickery/skill_contract.py`（Skill dataclass，共 46 行，纯数据契约）：

```
name / trigger / content / disabled
summary / version / author / description / category / tags / license
source / installed_at / provides_tool
binary_url / binary_size / binary_sha256 / binary_launch   ← Binary 扩展
capabilities / dependencies / resources / risk_level / composition   ← P0 五字段
```

- **P0 五字段**（`capabilities / dependencies / resources / risk_level / composition`）由 `runtime/skill_library.py` 的 `_normalize_brick_fields` 严格校验：`risk_level` 走受控词表，`capabilities` 强制字符串数组。`validate_skill_package(raw) -> Skill` 完成规范化与构造。
- **关键结论：契约层与加载层均无 `buttons` 字段**。`skill_contract.py` 的 Skill dataclass 不定义 `buttons`；`skill_library.py` 的 `_normalize_brick_fields / validate_skill_package` 也不读取/校验 `buttons`。因此即便在 brick.json 里手写 `buttons`，也会在规范化时被直接丢弃、不进 Skill 对象——**"按钮入口"当前处于完全未打通状态（而非"定义了但仅展示"）**。这是本设计的核心改造起点。

### 1.3 chat_ui.py 固定 12 区块 NAV，无积木自定义 UI 机制

`runtime/chat_ui.py`（安装版 runtime）前端为完整侧边栏 SPA：

- `NAV`（9 项）+ `NAV_EXT`（扩展 3 项）= **12 区块固定写死**：
  - NAV：聊天 01 / 技能库 02 / 积木市场 03 / 记忆柜 04 / 记忆 05 / 设置 06 / 医生 07 / 定时任务 08 / 保险库 09
  - 扩展：备份恢复 10 / 规则 11 / 连接器 12
- `buildNav()` 对 `NAV` + `NAV_EXT` 两数组做死循环渲染；`switchSection(sec)` 按 `sec` 查找 `renderers[sec]` 派发渲染。
- 积木仅以 `skills`（技能库）/`market`（积木市场）区块内的 **item-card 列表**呈现（启停按钮 / 触发按钮 / 拖入 .brick 安装 / 卸载 / 升级），消息流中积木仅以 `used_skills` 元标签显示。
- 全仓 grep 无 `skillView / ui_registry / customView` 等按积木名注册自定义界面的逻辑。
- **结论：当前无积木驱动的动态 UI 注册/渲染机制**；缺口在"前端不渲染"，后端 `ipc.py` 已具备全套 `_h_*` handler（含 `_h_skill_list / _h_skill_trigger / _h_drawer_*` 等），IPC 分发是**通用**的（见下），只是没有面向 buttons 的消费链路。

### 1.4 DocWrite 当前 pptx 支持能力边界

- **读取**：`doc_tools.py` 的 `DocRead` 支持 docx/xlsx/**pptx** 的纯文本提取（zip + XML 解析，`_pptx_text` 遍历 `ppt/slides/slideN.xml`）。
- **生成**：`docwrite.py` — 纯 stdlib（zipfile + 手工 XML）拼装 docx/xlsx/pptx，**零 LLM token**：
  - pptx 生成走 `_build_pptx`：`_p_slide` 支持 `type: content`（标题+正文段落）/ `section`（标题+表头+行表格）等版式；主题来自 `docwrite_templates.py` 的 `DocTemplate`（`accent_color / primary_color` 等，共 6 套色系）。
  - **能力边界（grep 实证）**：源码中**无** `image/picture/blip/png/jpeg` 相关代码 —— **不支持图片/素材埋入**；**不支持上传模板解析**；**无 AI 大纲/内容生成**；**无设计反思（渲染-自检-修正）闭环**。即 DocWrite 目前是"固定 6 主题、固定版式、纯数据填充"的确定性渲染器，距离"全功能 PPT"的缺口在：素材参与、模板、AI 编排、设计多样性、加工界面。

---

## 二、积木 UI 注册机制设计（平台通用链路）

> 目标：把 `buttons` 从"契约未定义、写了也丢弃"打通为"前端真实渲染按钮卡片并调用后端"，形成**平台级通用能力**，后续任何积木可用（PPT 积木只是第一个消费方）。

### 2.1 契约翻译（Skill dataclass 收 buttons / views）

`skill_contract.py` 的 `Skill` 增加可选字段（缺省安全、向后兼容）：

```
buttons: List[dict] = field(default_factory=list)   # 聊天内/分区内的操作按钮
views:   List[dict] = field(default_factory=list)   # 独立界面声明（动态分区注册用，修订二）
```

单枚 button 元素建议 schema（与既有字段风格一致，均为可选）：

```
{
  "label": "生成PPT",            # 按钮文案
  "action": "ppt_generate",      # IPC method 名，对齐 _h_<action>
  "args": {...},                 # 可选：随请求携带的默认参数
  "view": "ppt_studio"           # 可选：绑定的前端界面 id（独立界面时）
}
```

单枚 view 元素 schema（修订二 · 平台级导航动态注册分区）：

```
{
  "nav_title": "PPT 加工台",     # 「工具/工作台」分区中的导航显示名
  "view_id":   "ppt_studio",     # 前端界面 id：渲染函数注册键 + switchSection 目标
  "handler":   "ppt_open_studio",# 进入界面时调用的 IPC method（对齐 _h_<handler>）
  "icon":      "▣"               # 可选：导航项图标
}
```

> 平台约定：`chat_ui` 侧边栏**固定 12 区块保持不变**；新增**动态分区「工具/工作台」**，其中每个导航项由一个声明了 `views` 的积木在启用时自动注册、禁用/卸载时自动移除——不再为单个积木硬编码 NAV 区块。

### 2.2 加载校验

`skill_library.py`：
- `_normalize_brick_fields` 增加 `buttons` / `views` 规范化：两者必须是数组；`buttons` 每项 `label` 非空字符串、`action` 匹配受控命名（如 `^[a-z][a-z0-9_]+$`）；`views` 每项 `view_id` 非空、`handler` 匹配受控命名；非法则降级（丢弃该按钮/视图并告警）而非整包报错，保持向后兼容。
- `validate_skill_package` 将规范化后的 `buttons` / `views` 分别传入 `Skill.buttons` / `Skill.views`。
- **控权（决策点 D7）**：`action` / `handler` 建议按能力前缀白名单校验（如 `ppt_`、`skill_` 等），防止积木在 UI 层越权调用任意 `_h_*`。

### 2.3 前端渲染形态

两条路线（**决策点 D1**，见五）：

- **A. 聊天内按钮卡片**：chat_ui 在消息流中渲染。积木被触发（`used_skills` 命中）或用户进入技能库时，渲染按钮卡片（复用/扩展现有 item-card 样式），点击按钮走 IPC 调用。改动面小、通用性强。
- **B. 导航动态注册分区**（修订二 · 平台级）：chat_ui 导航栏新增一个**动态分区容器「工具/工作台」**（固定 12 区块之下）。`skill_list` 返回各技能 `views` → 前端过滤声明了 `views` 的技能 → 自动在该分区生成导航项（`nav_title / view_id / handler`）；点击导航项进入积木声明的独立界面页（`switchSection + renderers[view_id]`，注册/卸载随积木启用/禁用动态进行）。**不再为每个工具积木硬编码 NAV 区块。**

落地建议：通用按钮链路先做 A（最小可用），独立加工界面等重 UI 走 B；两条路线共用同一套 `buttons` / `views` 契约与 IPC 路由。

### 2.4 IPC 调用路由

`runtime/ipc.py` 的分发是**通用动态分发**，无需改分发器：

```
_dispatch(req): method = req.get("method")
  handler = getattr(self, f"_h_{method}", None)
```

因此**新增一个按钮能力 = 在 IPC 类上新增一个 `_h_<action>` 方法 + 在 brick.json 声明 button**，前端零手工路由。前端按钮点击统一发 `{method: button.action, params: {...}}`，handler 返回结果回显聊天或区块。

### 2.5 数据流时序

```
skill_list / 技能触发  →  Skill.buttons + Skill.views 随列表下发
          ↓
前端渲染按钮卡片（聊天内 A）· 动态分区「工具/工作台」按 views 生成导航项（B）
          ↓
点击按钮 / 导航项 → Invoke{method: action|handler} → _dispatch → _h_<method>(params)
          ↓
结果（生成文件路径 / 预览数据）回显聊天消息 / 分区界面
```

注：动态分区导航项在积木**启用时注册、禁用/卸载时移除**（前端由 `skill_list` 的 views 驱动同步）；`switchSection(sec)` 与 `renderers[view_id]` 机制泛化为运行时注册表，固定 12 区块渲染不动、动态分区项运行时挂载。

---

## 三、PPT 生成积木设计

### 3.1 功能分层

| 层级 | 能力 | 说明 |
|---|---|---|
| **L1 直接生成** | 纯对话/主题 → 出可编辑 .pptx | 最小闭环，复用 DocWrite 或升级引擎 + AI 大纲 |
| **L2 素材参与** | 上传模板 / 图片素材参与生成 | 新增 IPC handler + 前端上传路径 |
| **L3 去 AI 味设计系统** | 主题/母版/版式多样性 + 视觉反思闭环 | 借鉴调研中 Marp 可插拔主题、PptxGenJS Master Slide、ppt-master SVG→DrawingML、PPTAgent 渲染-自检-修正闭环 |
| **L4 加工设计界面** | 独立加工设计界面，示范积木自带 UI | 作为 §2.3 动态分区（B 路线）落地的第一个工具，见 §3.5 |

### 3.2 去 AI 味与设计质量——开源能力吸收

> 本节把 GitHub 排名靠前开源项目的**可落地机制**吸收为 PPT 积木的设计质量能力层（来源项目/star 详见附注与调研报告）。每一项都收敛为可执行的中间态 / 引擎 / 校验器，而非泛泛建议。本节定义"PPT 积木要长成什么样"，§3.3 定义"用什么引擎平台实现"。

**① 渲染底座：SVG-like 绝对坐标中间态 → 原生 DrawingML / PPTX**（吸收 ppt-master + python-pptx）

- PPT 积木的渲染中间态统一为**绝对坐标矢量 IR**（rect / text / image 元素树，同 SVG 理念），由渲染器逐节点映射为 PowerPoint 原生形状 / 文本框 / 图片（DrawingML / OOXML）；**绝不走 HTML→截图、整页位图路线**（presentation-ai 的 PPTX 导出变形是反例）。
- 配**严格兼容性黑名单**：禁用 mask / clipPath / textPath / 外部 CSS / @font-face / marker 等（参考 ppt-master AGENTS.md），保证导出物在 PowerPoint 里可**双击编辑**——这是"去 AI 味 + 可加工"的不可妥协前提。

**② 版式引擎：版式注册表 + Schema/Component 双层契约**（吸收 ppt-master + Presenton）

- **版式注册表**：预置 cover / toc / section / content / chart / compare / end 等版式（参考 ppt-master `layouts_index.json` 的 `layout_id→{summary, canvas_format, page_count, page_types}`），每个版式声明**几何槽位（slot）+ 语义文本角色 + 对齐/容量行为**。
- **双层契约**（参考 Presenton Zod/TSX）：Schema 层（Zod 式）约束每页数据字段（`meta()` 给 AI 语义、`default()` 兜底），Component/渲染层负责布局——**AI 只填数据不碰布局**（AI 不写像素、不选坐标）。生成时先按内容类型匹配 `layout_id`，再按 schema 填数。

**③ 设计系统：Token 引擎 + 多变体 + WCAG-AA 门禁**（吸收 slidev-theme-tahta + Marp + ppt-master）

- **Token 引擎**：单品牌色经 **OKLCH** 自动派生全调色板（tints/shades + 图表系列色）；三级 token（primitives → semantic → variant bundles），换 variant 即换整包字体/形语/纹理/密度/配色。
- **Marp 纯 CSS 主题规范**：支持纯 CSS 定义主题（`@theme` 元数据），确定性、可版本管理、便于设计师参与。
- **预置 3–5 套"语气档"设计系统**：通用 / 咨询 / 投行等（参考 ppt-master General / Consultant / Consultant Top）。
- **质量门禁**：token 契约校验（无硬编码色值）+ **WCAG-AA 对比度检查**（参考 tahta CI 门控）。

**④ 去 AI 味三件套**（融合 PPTAgent + Presenton + guizang/ppt-master）

- **继承参考**：上传 .pptx / 历史 deck → 幻灯片聚类 + **内容 schema 抽取**（`{category, description, content}`），生成以参考为锚而非每次自由发挥，质量上限=参考 deck 质量（PPTAgent 阶段一 + Presenton PPTX→设计系统）。
- **强约束 + 校验器**：校验脚本门禁 AI 通病——禁止居中大标题 / 实验性布局 / 图片脱槽溢出（参考 guizang `validate-swiss-deck.mjs` 与 ppt-master `svg_quality_checker` 强制 0 错误）；布局只走有限版式池。
- **人类在环**：先生成 `design_spec`（主题 / 画布 / 页数 / 风格等确认项）+ 大纲，供人确认后再渲染（参考 ppt-master Strategist 的 design_spec + spec_lock）。

**⑤ 视觉反思闭环（质量护城河）**（吸收 PPTAgent / DeepPresenter + ppt-master）

- 生成 → **沙箱渲染截图** → **PPTEval 三维评分**（Content / Design / Coherence，1–5 分细化标准）→ 低分/错误**回灌模型迭代**（PPTAgent REPL 自纠 + 环境感知反思）。
- 引入**独立评审模型**做二审，克服 self-verification bias（DeepPresenter-9B 范式）；另提供 ppt-master 式 rubric 自审 + 画布"点元素→写改法→apply→重渲染"的手动精修通道。

**⑥ 配图多通道 + 品牌化后处理**（吸收 Presenton + ppt-master + tahta-imagine）

- **多通道**：内置图标库 + AI 生图 + 图库检索（Pexels / Pixabay），配图走 manifest 管理、与生成解耦（ppt-master `image_prompts.json`）。
- **品牌化后处理**：AI 生图后做确定性 `crop → scheme-aware duotone → grain`（tahta-imagine），**不裸用原图**，保证 on-brand。

**⑦ 上传模板即设计系统**（吸收 ppt-master /create-template + Presenton）

- 解析用户 .pptx 抽取主题色 / 字体 / 母版-版式 / 可复用图片，沉淀为**私有设计系统**（版式库 + token + 内容 schema 三件套），后续生成的 deck 全部 on-brand。

> 上述能力与 §3.3 的关系：①〜⑦ 定义引擎目标（渲染底座 / 版式引擎 / 设计系统 / 三件套 / 反思闭环 / 配图 / 模板吸收），§3.3 决定承载引擎（纯 stdlib 扩展或 python-pptx），两者正交叠加实现 L3 去 AI 味。

### 3.3 DocWrite 扩展方案

两条实现路线（**决策点 D4**，见五）：

- **A. 扩展现有 docwrite（纯 stdlib）**：在 `docwrite.py / docwrite_pro.py`（已存在 `docwrite_pro.py`）补齐：图片埋入（新增 picture/blip 关系与部件）、模板导入解析（解析用户 .pptx 的 layout/placeholders 复用）、主题扩展（DocTemplate 增加自定义色系与母版）。优点：零新增依赖、与现有 DocWrite 一致；缺点：模板/素材能力手工实现成本高。
- **B. 引入 python-pptx 作新引擎**：新增 `build_ppt_tool` 注册进 `ToolProviderRegistry`，以 `provides_tool: PPTStudio` 提供新 brick（`source: builtin`，随底座分发）。优点：模板/图表/图片/母版对象模型成熟（python-pptx 为调研结论中的"原生可编辑底座"）；缺点：新增第三方依赖、与 docwrite 并存。

**AI 编排层（去 AI 味的前提）**：采用调研建议的两阶段——阶段 A 分析（提取参考模板风格 / 文档语义）→ 阶段 B 产出 **schema 化 JSON 大纲/逐页布局蓝图**（借鉴 pptx-generator 的 `slides.json` 中间态），由渲染器（DocWrite 扩展或 PPTStudio）确定性渲染为 .pptx。LLM 只产出结构化 JSON，不做像素级摆放，从而压低 AI 味并降低幻觉。

### 3.4 新增 IPC handler 清单（method 对齐 `_h_` 方法）

| method (`_h_<name>`) | 功能 | 层级 |
|---|---|---|
| `ppt_generate` | 主题/大纲/AI 蓝图 → 生成 .pptx，返回路径 | L1 |
| `ppt_upload_template` | 接收用户模板（解析为主题/母版） | L2 |
| `ppt_upload_assets` | 接收图片素材（登记进本次生成上下文） | L2 |
| `ppt_view_slide` | 渲染指定页预览（截图/缩略/元数据） | L3/L4 |
| `ppt_restyle` | 已生成 pptx 换肤/换主题 | L3/L4 |
| `ppt_download` | 下载/导出 .pptx（或复用既有文件传输链路） | L1-L4 |

**前置依赖（决策点 D5）**：L2 依赖 IPC 具备"文件上传"通道。当前 IPC 未确认有通用文件上传 handler（`_h_*` 中无 file upload 类），需新增（如 base64 编码 params 或独立 upload 通道）+ 前端 file input 路径。

### 3.5 brick.json 的 buttons 与 views 设计（新积木 ppt-studio）

```
{
  "name": "ppt-studio",
  "trigger": ["做ppt", "生成ppt", "做幻灯片", "ppt-studio", ...],
  "category": "tool",
  "provides_tool": "PPTStudio",        // 若走引擎 B；复用 DocWrite 则填 DocWrite
  "capabilities": ["ppt.generate.pptx", "ppt.upload.template", "ppt.upload.assets", "ppt.studio.ui"],
  "risk_level": "low",
  "views": [                           // 修订二：动态分区注册（平台级）
    { "nav_title": "PPT 加工台", "view_id": "ppt_studio", "handler": "ppt_open_studio", "icon": "▣" }
  ],
  "buttons": [
    { "label": "生成 PPT",  "action": "ppt_generate",  "view": "ppt_studio" },
    { "label": "上传模板",  "action": "ppt_upload_template", "view": "ppt_studio" },
    { "label": "上传图片素材", "action": "ppt_upload_assets", "view": "ppt_studio" },
    { "label": "打开加工台", "action": "ppt_open_studio", "view": "ppt_studio" }
  ]
}
```

`ppt_studio` 是核心界面 view，作为**动态分区落地的第一个工具**：安装 ppt-studio 后，「工具/工作台」分区自动出现"PPT 加工台"导航项，点击进入独立加工设计界面（左侧版式/主题面板 + 中间逐页缩略 + 右侧参数：色系/母版/字体），改动内容经 `ppt_restyle / ppt_generate` 回写并重新生成 .pptx。

> **Step2/3 落地差异括注（2026-08-27，以落地为准）**：实际 `ppt-studio/brick.json` 中 `views` 的 `icon` 采用 `📽️`（非上示 `▣`）；`buttons` 仅「生成 PPT」（`action: ppt_generate`）落地为可执行按钮，「上传模板 / 上传图片素材 / 打开加工台」未落地（上传通道 D5 未实现）；`actions` 中的「应用外观」（`ppt_restyle`）为占位 **disabled** 动作。实际 brick.json 全文见 §4 Step2 落地实测记录。（**已更新 2026-08-28**：`ppt_restyle` 已实现为可执行动作 + 前端 5 档预设下拉，见 §六表 1 与 §4 Step5 验收指引；本括注为 Step2/3 落地时点的事实记录）

### 3.6 积木库登记

- 新增砖包目录 `brick-vault/bricks/ppt-studio/`（`brick.json` + 技能包；若走内置分发则放 `Shadeling/builtin_skills/ppt-studio/`，`source: builtin`，经 `produce.py` 随底座打包）。
- **登记文件是 `brick-vault/index.json`**（`schema: brick-registry/v1`，追加一条含 `name/version/category/risk_level/summary/path` 的条目）与 `skills/index.json`。**无 bronze.json**，不新增登记文件形态。
- `docwrite/brick.json` 若复用 DocWrite 路线需同步追加 `buttons`（及必要时的 `views`）字段以启用通用按钮与动态分区能力。

---

## 四、实现步骤拆解

> 依赖顺序：Step1 → Step2 → Step3 → Step4，每步有独立验收，验收通过才进下一步。

### Step1 打通最小链路（buttons/views → IPC → 前端渲染 + 动态分区容器）
- **改动文件**：`brickery/brickery/skill_contract.py`（Skill 加 `buttons` + `views`）；`runtime/skill_library.py`（`_normalize_brick_fields` + `validate_skill_package` 消费 buttons/views）；构建版 IPC（`brickery/runtime/ipc.py` 或运行 runtime）+ 安装版 `chat_ui.py`、`ipc.py`（新增示例 `_h_demo_button` + 前端按钮卡片渲染 + 动态分区「工具/工作台」容器与 views 导航项生成）。
- **改动面**：契约 1 处 + 加载校验 1 处 + IPC/前端各 1 处，平台级、不破坏既有行为（缺省安全）；固定 12 区块渲染不动。
- **验收**：任意 brick.json 写 `buttons` → 重启后：① 技能列表能返回 buttons/views；② 聊天/技能库出现按钮卡片；③ 点击按钮触发 `_h_<action>` 并在对话回显结果；④ 声明 `views` 的测试砖自动出现在「工具/工作台」动态分区，点击进入独立界面页，禁用/卸载后导航项移除。以 `hello-marvis` 或新增测试砖验证。

> **✅ Step1 落地实测记录（2026-08-27）**——计划已执行并通过接口级验证，以下为忠实记录：

- **代码落地与同步**：5 个核心文件完成改动并同步运行副本（`/Applications/shadelingmac0.0.1.app/Contents/Resources/brickery-runtime/brickery/` 对应路径）：
  1. `skill_contract.py` + `runtime/skills.py`——`Skill` dataclass 新增 `buttons` / `views` 字段（`default_factory=list`，缺省安全）+ `skills.py` 的 `to_dict` / `load_items` 序列化与解析回填；
  2. `runtime/skill_library.py`——`_normalize_ui_buttons` / `_normalize_ui_views` + D7 控权落点（`UI_ACTION_PREFIXES`：`ppt_ skill_ tool_ demo_` 白名单 + `UI_ACTION_BLOCKED` 拒绝 `system_ file_ backup_ daemon_` 及市场管理员方法；非法单条丢弃并告警、不整包报错）；`_normalize_brick_fields` 与 `validate_skill_package` 消费 buttons/views；
  3. `runtime/ipc.py`——`_skill_dict` 透传 buttons/views；新增只读 `_h_skill_views`（仅返回启用且声明 views 的技能）+ 示例 `_h_demo_button`（回显，验证最小链路）；
  4. `runtime/chat_ui.py`——`renderSkills` 渲染按钮卡片 + `invokeSkillButton`；`loadDynamicViews` / `renderDynamicViews`「工具/工作台」动态分区 + `renderGenericView` 通用视图容器；`switchSection` 兜底；固定 12 区块不动。（**补漏 2026-08-28**：初始实现未在初始化 `buildNav()` 后调用 `loadDynamicViews()`，动态分区入口不渲染；已修复 + 双进程重启 IPC 实连验证，见 §4 Step5.1）
  同步后 5 文件 `sha256` 与仓库完全一致。
- **实测发现并修复的缺口（白名单）**：`demo_button` 未进 IPC 白名单——`UI_DYNAMIC_METHOD_PREFIXES` 常量已定义但 `_ipc` 从未消费，导致 `POST /api/ipc {"method":"demo_button"}` 返回 403"method 不在白名单"。修复：`chat_ui.py` 新增 `_is_method_allowed(method, stream=False)`——静态白名单 ∪ `ppt_/demo_` 受控前缀双通道；流式通道严格白名单（防积木按钮把方法挂成 SSE）。新增 `tests/test_chat_ui_whitelist.py`（5 条用例：静态放行/动态前缀放行/流式拒绝/危险方法拒绝/边界）。
- **回归结果**：全量 `discover -s brickery/runtime/tests -t brickery/runtime` **249 通过 / 1 失败**；唯一失败为外部 fixture `test_fetch_index_ok`（断言 5 个策展技能，fixture 仓库新增第 6 个 `browser` 后 6≠5，与本改动无关，**建议顺手将断言更新为 6**）。
- **最小验证积木 demo-studio（纯验证物）**：`brick-vault/bricks/demo-studio/brick.json`（category=tool，buttons: `触发演示→demo_button`，views: `演示工作台→demo_view`，均在受控前缀 `demo_` 内）；已登记 `brick-vault/index.json`（bricks 数组 21 条）；已追加到运行实例 home `skills.json`（原文件备份在中间产物目录）。技能为**启动时静态加载**（`self.skills.load` 仅 `IpcServer.__init__` 执行一次，`skill_list`/`skill_views` 读内存快照），改 skills.json **需重启**生效。
- **重启复验通过（接口级）**：重启后 `skill_list` 含 `demo-studio` 且带 buttons/views；`skill_views` 返回「演示工作台」（nav_title=演示工作台，view_id=demo_studio）；`demo_button` 回显 `ok:true`「Demo 按钮已触发：buttons → IPC → 前端 最小链路打通」。
- **遗留待办**：
  1. ~~内置 `backup-restore` 技能自带 5 个 legacy 结构按钮点击 403~~（**已闭环 2026-08-28**：根因非白名单——方法名均已在静态白名单；403 来自 legacy 字段 `{id,label,handler,params}` 未迁移到新 `{action,args}` 协议、前端取 `action` 为空被拒；已迁移两处定义文件，白名单零改动，见 §六表 2）；
  2. 前端按钮卡片与「工具/工作台」动态分区的**最终目视验收**由用户在界面上确认（接口/数据层已验证）。

### Step2 PPT 积木本体（生成能力 + 素材上传 + 主题）
- **改动文件**：PPT 引擎（`docwrite.py/docwrite_pro.py` 扩展 **或** 新 `ppt_engine.py` + `tool_providers.py` 注册 PPTStudio）；`brick-vault/bricks/ppt-studio/`（brick.json + buttons）+ `index.json` 登记；IPC 新增 `_h_ppt_generate / _h_ppt_upload_template / _h_ppt_upload_assets`（含上传通道）；chat_ui 上传入口（file input + base64/file 传输）。
- **依赖**：Step1 的 buttons/views 链路 + D5 上传通道决策。
- **验收**：① `ppt_generate` 仅凭主题/大纲产出可编辑 .pptx（6 主题之上叠加 AI 大纲）；② 上传模板后可复用其版式/母版；③ 上传图片素材被真实埋入成片（zip 内存在 picture 部件）；④ 结果路径在对话中可点击下载。

> **✅ Step2 落地实测记录（2026-08-27）**——计划已执行并通过接口级验证，以下为忠实记录：

- **PPT 引擎落地：`ppt_brick` 五模块渲染底座**（开发仓库 `brickery/brickery/brickery/ppt_brick/`，已同步运行副本 `/Applications/shadelingmac0.0.1.app/Contents/Resources/brickery-runtime/brickery/ppt_brick/`；另含 `demo.py / demo_gen.py / demo_theme.py` 演示件与 `tests/`）：
  1. `model.py`（约 220 行）——**SVG-like 绝对坐标中间态 IR**（rect/text/image 元素树），渲染与版式统一走此中间表示；
  2. `render.py`（约 227 行）——**原生 DrawingML / OOXML**，用 python-pptx 把中间态逐节点映射为 PowerPoint 形状/文本框，生成物可双击编辑（满足"可加工"前提）；
  3. `theme.py`（约 503 行）——**OKLCH token 引擎**：单品牌色经 OKLCH 自动派生全调色板（tints/shades + 图表系列色）+ **WCAG-AA 对比度门禁** + `extract_brand_from_image` 从图片抽品牌色；
  4. `registry.py`（约 327 行）——**版式注册表**：cover / toc / section / content 四版式，纯函数 `(tokens, data) -> Slide[]`；
  5. `generator.py`（约 253 行）——`build_deck` 编排（封面 / 目录自动推导 / 每章节 section+content 分页 / 超长拆页 / 页码统一 / `layout_ids` 覆盖）+ `generate_pptx` 一次落盘；`DEFAULT_BRAND = "#1D4ED8"` 默认品牌色。
  **单测 49 过**（test_theme 12 / test_generator 20 / test_registry 7（其中 2 个 `parametrize` 展开为 8）+ test_ppt_brick 4 = 49，运行副本复验通过）。
- **IPC 落地：`runtime/ipc.py` 新增 `_h_ppt_generate`**：`structure` 必填校验（`title` 非空、`sections` 非空 list，缺失抛 `ValueError`）；`brand_color` 缺失时 `DEFAULT_BRAND`（#1D4ED8）兜底；落盘到运行实例 home **`output_pptx/`**（`config.home / "output_pptx"`，自动建目录）；文件名只取 basename（`Path(fname).name`，**防路径逃逸**），`.pptx` 后缀自动补齐；变体 `variant`/明暗 `semantics`/`layout_ids` 均透传 generator；返回 `{ok, path, pages}`（`pages` 用 python-pptx 数幻灯片，运行环境缺库时兜底 0）。**chat_ui 白名单无需改动**——Step1 已建的 `_is_method_allowed` 静态白名单 ∪ `ppt_/demo_` 受控前缀天然放行 `ppt_*`。
- **积木声明与登记**：`brick-vault/bricks/ppt-studio/brick.json`（category=tool，`provides_tool`/`source: local`，capabilities 含 `ui.buttons / ui.views / ppt.generate`，`dependencies: ["python-pptx"]`，`composition.requires: ["ppt_brick"]`；**buttons**：[生成 PPT→ppt_generate]；**views**：[PPT 加工台→ppt_open_studio]）；已登记 `brick-vault/index.json` 与运行实例 home `skills.json`（原 skills.json 已备份至中间产物目录）。
- **环境事实**：运行副本内嵌 python **3.12.14/arm64**，**python-pptx 1.0.2** 已随运行副本安装（cp312 wheel 可用）；demo/单测用例演进史 44 → 49 →（Step3 后）57；**仓库 `.venv` 为 python3.14，因缺 cp314 wheel 装不上 python-pptx**——**运行侧依赖基线已决策（2026-08-28）**：运行侧固定以 3.12 为准（运行副本内嵌 3.12.14 + python-pptx 1.0.2）；仓库测试侧已补装 python-pptx 1.0.2（支撑测试链路），见 §六表 4。
- **冒烟与运行验证**：直接调运行副本 handler 生成 **10 页中文 deck** 成功；**重启后三接口（skill_list / skill_views / ppt_generate）全通**。

### Step3 导航动态注册分区 + PPT 加工台（首个工具落地）
- **改动文件**：`chat_ui.py`（新增动态分区「工具/工作台」容器：`buildNav` 中额外渲染 views 驱动的导航项、`renderers` 按 `view_id` 运行时注册、`switchSection` 支持动态 view——该机制已在 Step1 实现，本步将由 PPT 加工台正式消费；PPT 加工台视图组件：左侧版式/主题面板 + 中间逐页缩略 + 右侧参数，改动经 `_h_ppt_restyle / _h_ppt_generate` 回写）；IPC 新增 `_h_ppt_view_slide / _h_ppt_restyle / _h_ppt_open_studio`。
- **依赖**：Step1 动态分区容器 + Step2 生成能力。
- **验收**：安装 ppt-studio 后「工具/工作台」自动出现"PPT 加工台"；从聊天按钮或导航项进入独立加工界面，可浏览逐页、切换主题、调整版式并重新出片；卸载后导航项消失；界面以 chat_ui 内置 SPA 方式承载，不另起 Web 服务。

> **✅ Step3 落地实测记录（2026-08-27）**——计划已执行并通过接口级验证，以下为忠实记录：

- **可交互视图契约（核心决策）**：**静态 `views` 契约零扩展**——brick.json 的 `views` 字段保持 Step1 已登记形态 `{nav_title, view_id, handler, icon?}` 不变（向后兼容）；**可交互 schema 全部走运行期下发**：进入视图时由 handler（`views[].handler` 对应 IPC method）返回 dict，字段约定：
  - `form.fields[]`：控件 `{name, label, type, default, required, placeholder?, options?, item_fields?}`，`type ∈ text / textarea / color / list`（list 为可增删列表，用 `item_fields` 递归声明子字段）；
  - `actions[]`：`{label, method, args?, disabled?, hint?}`，`method` 须命中受控前缀（`ppt_` 等）才被 `_is_method_allowed` 放行；
  - `preview`：`{supported, method, args?, trigger?}`（实时预览声明）。
  `$form` 约定：action/preview 的 `args` 里 `"$form"` 由前端替换为表单聚合的 structure。
- **IPC 落地**（`runtime/ipc.py`，已同步运行副本）：
  - `_ppt_studio_view()`（静态方法，纯数据无副作用）——返回 ppt-studio「PPT 加工台」视图定义：**6 字段表单**（title/subtitle/author/date/brand_color[color]/sections[list 嵌套 bullets list]）+ **actions**（生成 PPT→`ppt_generate`；应用外观→`ppt_restyle` 为 **disabled 占位**，hint 注明后续落地）+ **preview**（`ppt_preview`，`trigger: "on_change"`）；
  - `_h_ppt_open_studio`——返回视图定义（`{ok, skill, view_id, view}`），纯只读；
  - `_h_ppt_preview`——`structure` → `build_deck` 只产中间态不落盘，返回 `pages[]`（每页 `{page_no, role, layout, title, bullet_count, note}`）；必填校验与 generate 完全一致，**非法 structure 软失败**（`{ok:false, error}`，不下发异常）；版式/主题派生与 generate 对齐（brand 缺失 `DEFAULT_BRAND` 兜底，含 `layout_ids` 剔除/替换逻辑）。
  **单测 8 例全绿**（`runtime/tests/test_ppt_studio_view.py`）。
- **前端视图引擎落地**（`runtime/chat_ui.py`，已同步运行副本）：`renderGenericView`（1312 行）升级为路由——声明了 view handler 的 item 走 `renderDynamicViewEngine`（1451 行），handler 失败/无 schema 则回退 `renderGenericStaticShell`（1328 行，兼容 demo-studio 按钮卡片与 Step1 静态容器）；新增 **18 个视图引擎函数**（`veEsc/veJsonPath/vePathGet/vePathSet/vePathDel/veVMInit/veFieldHTML/veRowHTML/veListRowsHTML/veMaterializeLists/veOnFormInput/veOnFormClick/veDelRow/veAddRow/veRerenderList/veSchedulePreview/veRebind/veRenderPreview/veRunAction/veCopyPath` 等）：控件渲染、sections 列表增删、`$form` 聚合、**260ms 防抖实时预览**（`previewTimer = setTimeout(veRenderPreview, 260)`）、actions 执行、结果卡+复制路径。JS 提取后 `node --check` PASS（文本 12 万余字节量级），**18 项静态断言过**（覆盖 schema 映射与兼容回退）。
- **联调复验**：`ipc.py` / `chat_ui.py` 同步运行副本 `sha256` 均 MATCH + `py_compile` 通过；**重启后四接口（skill_views / ppt_open_studio / ppt_preview / ppt_generate）全通**，经 `ppt_preview` 生成的 6 页摘要与 `ppt_generate` 实际渲染 6 页磁盘复验一致（SMOKE PASS，未重启进程验证真实返回）。
- **遗留待办**（Step3 归属）：「应用外观」（`ppt_restyle`）占位禁用 → **已实现（2026-08-28）**：`_h_ppt_restyle` + 前端 5 档预设下拉（见 §六表 1 与 §4 Step5 验收指引）；前端「PPT 加工台」**最终目视验收交由用户在界面上确认**（验收步骤见 §4 Step5）。

### Step4 固化通用机制
- **改动文件**：契约与规范文档（把 buttons/views schema、动态分区注册约定、IPC 命名约定沉淀进 specs 与 skill_contract 注释）；`produce.py` 打包链路确认 `buttons` / `views` 随内置积木分发；`brick-vault/specs` 补充"带 UI 积木（按钮 + 独立界面）"编写指南。
- **验收**：第三方积木仅改 brick.json（buttons/views）+ 声明新 `_h_` handler 即可同时获得按钮入口与「工具/工作台」分区入口；文档覆盖字段、加载校验、前端渲染、动态分区、IPC 路由五层约定。

✅ **Step4 落地实测记录（2026-08-27）**

**泛化 vs 专用现状清单**（文件/结构级）：
- **已通用（平台层，Step1-3 即落地，本轮核查确认）**：
  - 契约：`Skill.buttons/views`（`skill_contract.py` + `runtime/skills.py`，缺省安全 `field(default_factory=list)`）
  - 加载校验/契约翻译：`skill_library.py` 的 `_normalize_ui_buttons / _normalize_ui_views / _normalize_brick_fields`（D7 控权全流程）
  - IPC 下发：`ipc.py` 的 `_skill_dict` 透传 + `_h_skill_views`（只读返回启用且声明 views 的技能）
  - 前端按钮卡：`renderSkills` + `invokeSkillButton`（读 `s.buttons` 动态渲染 brick-btn）
  - 前端动态分区：`loadDynamicViews`（skill_views 快照驱动 `nav-dyn-group`「工具/工作台」）+ `switchSection` 兜底 `renderGenericView`
  - 前端视图引擎：`renderGenericView → renderDynamicViewEngine`（运行期 schema：form/actions/preview/$form）+ 兼容回退 `renderGenericStaticShell`
  - 后端路由：`_dispatch` → `_h_{method}` 通用动态分发（无按积木硬编码分支）
  - 打包分发：`produce.py._brick_to_skill` 逐字段透传 brick.json → skill.json，**buttons/views 天然随内置积木分发**（已确认，无需改 produce.py）
- **本轮固化的漂移点（唯一代码改动）**：UI 方法前缀双副本——声明层 `skill_library.UI_ACTION_PREFIXES = ("ppt_","skill_","tool_","demo_")` vs IPC 放行层 `chat_ui.UI_DYNAMIC_METHOD_PREFIXES = ("ppt_","demo_")`。`skill_/tool_` 前缀积木声明校验通过但按钮调用被 IPC 拒绝。**已收敛为单一事实源**：`chat_ui.py` 改由 `from .skill_library import UI_ACTION_PREFIXES as UI_DYNAMIC_METHOD_PREFIXES` 导入，声明层与 IPC 放行层共用同一份「积木 UI 方法能力前缀注册表」；新增能力域（如 `report_/chart_`）只需登记 `UI_ACTION_PREFIXES`，chat_ui 零改动。
- **积木私有（不进平台层）**：`_h_demo_button` / `_h_ppt_open_studio` / `_h_ppt_preview` / `_h_ppt_restyle` / `_h_ppt_generate` / `ppt-studio` 视图 schema 与渲染逻辑。平台只提供三条通用通道：前缀注册表（起名权）+ `_dispatch` 路由（分发）+ 前端引擎（渲染）。
- **新增积木接入路径（Step4 固化后的「零额外代码」边界）**：① brick.json 声明 `buttons`（`{label, action, args?, view?}`）与 `views`（`{nav_title, view_id, handler, icon?}`），action 落在注册表前缀内；② 后端实现对应 `_h_<action>`；③ 前端若需可交互视图，声明 view 的 `actions/preview` schema（由通用引擎渲染）。三步之外无平台层改动。
- **测试固化**：新增 `tests/test_ui_prefix_registry.py`（单一事实源等效性、全前缀积木零额外代码放行、D7 控权、边界不回退，5 例）；新增 `tests/test_chat_ui_frontend_contract.py`（前端静态契约断言 5 例 + JS 块 `node --check` 语法校验，node 缺失 skip）。全量 278 tests：仅既有 brick-vault fixture 计数断言失败（6≠5，与本轮无关）+ 1 skip（node 缺失），其余全绿。

### Step5 前端目视验收指引（需用户重启后操作）

> 本步**无代码改动**，仅由用户在界面上验收 Step1-4 的落地效果。前置条件：运行副本已与仓库同步（`runtime/ipc.py` / `runtime/chat_ui.py` / `runtime/skill_library.py` 等 `sha256` 已 MATCH）；`skills.json` 为**启动时静态加载**（改 skills.json 需重启生效），故验收前需**完全退出并重启 shadelingmac0.0.1**。

按以下顺序逐项检查，每项给出预期现象：

1. **导航动态分区入口**：重启后进入主界面，检查左侧导航出现动态分区「工具/工作台」，其下存在 **「PPT 加工台」** 入口。
   - 预期：点击可进入 PPT 加工台独立界面页（由 `renderDynamicViewEngine` 承载）；未安装/禁用 `ppt-studio` 时该导航项消失（可反向验证动态注册）。
2. **PPT 加工台表单与实时预览**：在加工台填写 6 字段表单（`title / subtitle / author / date / brand_color / sections`），确认字段可编辑、`sections` 支持增删行。
   - 预期：内容录入后预览区按时刷新（260ms 防抖），页数与章节摘要正确；修改 `brand_color` 即时刷新预览配色。
3. **『应用外观 / 换肤』可用（ppt_restyle）**：确认「应用外观」动作**不再 disabled**，提供 **5 档预设下拉**；切换预设后预览配色**即时刷新**（无需重新生成）。
   - 预期：5 档预设均可切换，预览页配色随预设变化；「生成 PPT」按钮可点击并产出可下载 `.pptx` 文件。
4. **backup-restore 旧按钮不再 403**：进入「备份恢复」区块，点击 5 个按钮（`backup_default / backup_export / backup_restore / backup_list / backup_scheduled`）。
   - 预期：全部按钮正常响应，**不再返回 403**（`backup_scheduled` 走 `task_submit` action，其余走同名 action，均已被 `_is_method_allowed` 放行）。
5. **回归检查**：原有 12 个固定 NAV 区块（聊天/技能库/积木市场/…/备份恢复等）行为保持不变；按钮卡片渲染、模板/预览/生成链路无回归。

**失败排查顺序**：① 现象仍为旧行为 → 确认是否真的重启（进程需完全退出后重开）；② 接口层异常 → 对比运行副本与仓库 `sha256` 是否一致；③ 403 → 检查是否落在静态白名单或受控前缀内（见 §4 Step1 白名单机制）。

### Step5.1 前端补漏修复与重启验证（2026-08-28）

- **根因**：Step1 已实现 `loadDynamicViews` / `renderDynamicViews`，但前端**初始化块在 `buildNav()` 之后漏调用 `loadDynamicViews()`**，导致「工具/工作台」动态分区入口**永不渲染**。此前 Step1/Step3 的"重启复验通过"均为**接口级**——`skill_views` 返回数据正常，但前端渲染链路未走通。
- **修复**：`runtime/chat_ui.py` 初始化主流程补上 `loadDynamicViews()` 调用；仓库与运行副本 `sha256` 一致（本次已复验 MATCH）。
- **重启验证（双进程）**：旧进程 15982/15983 为 2026-08-27 23:07 启动、未加载新代码；已重启为新进程 **95694/95695**。IPC 实连验证通过：
  - `skill_views` 返回「PPT 加工台」（ppt_studio）与「演示工作台」（demo_studio）两个动态视图；
  - `skill_list` 共 22 技能，`backup-restore` 含 5 个按钮（`backup_default / backup_export / backup_restore / backup_list / backup_scheduled`）；
  - `ppt_restyle` 5 档预设可用，含 dark 背景切换；非法预设软失败（不下发异常）；
  - 未知方法被正确拒绝（白名单拦截仍生效）。
- **遗留**：前端「工具/工作台」动态分区、PPT 加工台界面与按钮卡的**最终目视验收仍需用户在界面确认**（步骤见 §4 Step5，可在本次重启后的 95694/95695 进程上直接操作）。

---

## 五、风险与决策点（需用户拍板）

| # | 决策点 | 选项 | 默认建议 | 位置 |
|---|---|---|---|---|
| **D1** | 按钮渲染形态 | A 聊天内按钮卡片 / B「工具/工作台」动态分区独立界面 / 两者 | 先 A 后 B（通用按钮走 A，重 UI 走 B） | §2.3 |
| **D2** | 导航动态注册分区（替代原"新增 NAV 第13区块"硬编码） | A 平台级动态分区：「工具/工作台」区由 `brick.json.views` 声明驱动，安装即出现、卸载即移除（气泡式容纳"以后很多类似工具"）/ B V1 硬编码 NAV 追加（快捷但不通用） | **A**——`chat_ui` 导航新增一个动态分区容器，带界面的积木声明 `{nav_title, view_id, handler}` 即自动注册；PPT 加工台作为该分区第一个工具，经 `produce.py` 随内置积木分发 | §2.1（views 契约）、§2.3（B 路线）、§3.5、§4 Step3 |
| **D3** | 去 AI 味做到什么程度 | 仅主题多样性（6→多套母版版式）/ 中间态（schema 蓝图 + 确定性渲染）/ 完整视觉反思闭环（渲染→截图→自检→修正，借鉴 PPTAgent） | 至少中间态 + §3.2 开源能力吸收（SVG-like→DrawingML 底座、版式注册表、校验器门禁、人类在环）；反思闭环（独立评审模型 + PPTEval 三维评分）作为 V2 迭代项（成本高，依赖沙箱截图与评审模型） | §3.2 |
| **D4** | PPT 渲染引擎路线 | A 扩展 docwrite（纯 stdlib，零依赖）/ B 引入 python-pptx 新引擎（成熟对象模型）/ C 采纳 ppt-master"SVG-like 中间态→DrawingML"两阶段（在 A/B 之上加矢量中间表示与兼容性黑名单） | C 为去 AI 味 + 可加工的前提，建议在 B（python-pptx 底座）或 A（纯 stdlib）上叠加 SVG-like 中间态；底座取舍看平台依赖红线 | §3.2、§3.3 | | **✅ 已落地（2026-08-27）**：选定 "python-pptx 底座（B）+ SVG-like 中间态（C 精神）"——`ppt_brick` 的 `model.py`（SVG-like IR）+ `render.py`（原生 DrawingML），见 §4 Step2 落地实测记录 |
| **D5** | 上传通道方案 | A IPC 新增通用 upload handler / B 仅临时方案 | 现有 IPC 无 file upload 类 handler，建议新增通用上传通道（base64 或独立 file 通道），平台级受益 | §3.4 | | **决策已记录（2026-08-27，本轮不实现）**：现有 IPC 无 file upload 通道——`_h_file_*`（file_index/update/remove/search）是**本地文件库索引/搜索**，非上传；前端仅有的 file input 是 `brickFileInput`（.brick 积木包导入，非素材上传）。**决策：本轮不实现通用上传**；推荐实现形态为方案 A——IPC 新增通用 upload handler（base64 编码 params 或独立 file/upload 通道）+ 前端 file input，留给「L2 素材参与」阶段落地；Step2/3 已验证纯结构参数（`structure`/`actions.args`）可闭环最小链路，`ppt_upload_template / ppt_upload_assets` 按钮维持未落地 |
| **D6** | ~~区块注册方式（硬编码 vs 动态生成）~~ | **已并入 D2**：由"导航动态注册分区"方案统一承接，不再作为独立决策；`chat_ui` 固定 12 区块不变、动态分区承载积木自带界面 | — | §2.3、§4 Step3 |
| **D7** | buttons.action 控权 | 开放 action 任意 method / action 白名单 | 白名单或前缀校验（`ppt_`, `skill_` 等）防积木越权调用任意 `_h_*` | §2.2 |

---

## 附：核对过的代码与数据来源（本文所有"现状"均基于以下真实文件，非推断）

- `brickery/brickery/skill_contract.py`（Skill dataclass，46 行，当前无 buttons/views 字段；§2.1 将新增二者作为平台级扩展）（**Step1 已落地**：已新增 buttons/views 字段，见 §4 Step1 落地实测记录）
- 运行 runtime `brickery/runtime/skill_library.py`（`_normalize_brick_fields` 校验 P0 五字段，无 buttons 消费）（**Step1 已落地**：已新增 `_normalize_ui_buttons`/`_normalize_ui_views` 并消费，见 §4 Step1 落地实测记录）
- 运行 runtime `brickery/runtime/chat_ui.py`（NAV 9 项 + NAV_EXT 3 项 = 12 区块固定写死）（**Step1 已落地**：按钮卡片 + 「工具/工作台」动态分区；**Step3 已落地**：`renderGenericView` 升级为视图引擎路由，新增 18 个 `ve*` 视图引擎函数，见 §4 Step1/Step3 落地实测记录）
- 运行 runtime `brickery/runtime/ipc.py`（`_dispatch` 动态分发 `_h_{method}`）（**Step2 已落地**：新增 `_h_ppt_generate`；**Step3 已落地**：新增 `_h_ppt_open_studio` / `_h_ppt_preview` / `_ppt_studio_view`，见 §4 Step2/Step3 落地实测记录）
- `brickery/brickery/brickery/ppt_brick/`——**Step2 新增 PPT 渲染底座**（model/render/theme/registry/generator 五模块 + tests，单测 49 过），已同步运行副本 `brickery-runtime/brickery/ppt_brick/`
- `brick-vault/bricks/ppt-studio/brick.json`——**Step2 新增**：PPT 生成积木声明（buttons：生成 PPT→ppt_generate；views：PPT 加工台→ppt_open_studio），已登记 `index.json` 与运行实例 home `skills.json`
- `brickery/runtime/doc_tools.py`、`docwrite.py`、`docwrite_pro.py`、`docwrite_templates.py`（pptx 支持边界：读取 ✓、生成 ✓、图片/模板/AI/反思 ✗）（**括注**：Step2 的 PPT 生成能力实际由 `ppt_brick`（python-pptx 底座）承载，docwrite 的 pptx 生成未被本积木使用）
- `brick-vault/index.json`（registry v1；**无 bronze.json**）、`brick-vault/bricks/docwrite/brick.json`（provides_tool=DocWrite、无 buttons）
- 调研输入：GitHub 开源 PPT 生成工具对比（python-pptx / PptxGenJS / Marp / Slidev / ppt-master / Presenton / PPTAgent / SlideCraft 等）

## 六、遗留待办汇总（截至 2026-08-27 落地后）

| # | 待办 | 归属 | 状态 |
|---|---|---|---|
| 1 | **ppt_restyle（应用外观/换肤）**：已实现——`runtime/ipc.py` 新增 `_h_ppt_restyle`，`chat_ui.py` 前端「应用外观」动作提供 **5 档预设下拉**，切换后经 `ppt_preview` 即时刷新预览配色 | Step3 | **✅ 已落地（2026-08-28）**，见 §4 Step3 落地记录 + §4 Step5 验收指引 |
| 2 | **backup-restore legacy 按钮 403**：已处置——**根因非白名单**：5 个 legacy 按钮的方法名本体均在静态白名单（`backup_scheduled` 按钮 action=`task_submit` 亦放行）；403 根因是按钮仍用 legacy 字段 `{id,label,handler,params}`，未迁移到 Step1 的 `{action,args}` 协议，前端取 `action` 为空 → `_is_method_allowed("")` 拒绝；已迁移两处定义文件至新协议，**白名单零改动**，重启后生效 | Step1 | **✅ 已处置（2026-08-28）**，见 §4 Step1 落地记录 + §4 Step5 验收指引 |
| 3 | **前端最终目视验收**：按钮卡片、「工具/工作台」动态分区、PPT 加工台界面由用户在界面上确认（接口/数据层已验证） | Step1/Step3 | 待用户验收（验收步骤见 §4 Step5 指引，需重启后操作） |
| 4 | **运行侧依赖基线（python3.14 vs 3.12）**：已决策——**运行侧固定以 3.12.14 为准**（运行副本内嵌 3.12.14 + python-pptx 1.0.2，cp312 wheel 可用）；仓库测试侧已补装 python-pptx 1.0.2 | Step2 | **✅ 已决策/已闭环（2026-08-28）**，见 §4 Step2 环境事实 |
| 5 | **上传通道（D5）**：文件上传 handler 未实现，`ppt_upload_template / ppt_upload_assets` 未落地 | Step2 | **决策已记录**（2026-08-27：本轮不实现，形态=IPC 通用 upload handler 方案 A，留给 L2 素材参与阶段，见 §五 D5） |
| 6 | ~~Step4 固化通用机制~~：buttons/views schema、动态分区注册约定、IPC 命名约定已沉淀为平台通用规范（含 `produce.py` 分发链路确认、前缀注册表单一事实源） | Step4 | **✅ 已落地（2026-08-27）**，见 §四 Step4 落地实测记录 + `brick-vault/specs/ui-brick-guide.md` |
*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
*（内容由AI生成，仅供参考）*
