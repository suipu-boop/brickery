# 带 UI 积木编写指南（buttons / views · 平台通用注册机制）

> 本文档固化「brickery 平台积木自带 UI」通用注册机制（Step4 落地，2026-08-27）。
> 目标：**新积木只需在 brick.json 声明 `buttons` / `views` + 实现对应 `_h_` handler，
> 即可零平台层改动获得聊天按钮入口与「工具/工作台」动态分区入口**。
> 权威链路与实测记录见 `specs/brick-ui-and-ppt-brick.md` §四 Step4。

## 0. 一句话本质

平台只提供三条通用通道，剩下的全是积木自己的事：

```
前缀注册表(起名权)  →  _dispatch 路由(分发)  →  前端渲染引擎(渲染)
     \_/                     \_/                    \_/
 skill_library.        ipc.py                     chat_ui.py
 UI_ACTION_PREFIXES    _h_{method}               renderSkillView /
                                                 renderDynamicViewEngine
```

## 1. 字段契约（brick.json）

```jsonc
{
  "name": "my-brick",
  "trigger": ["我的积木"],
  "content": "",
  "buttons": [
    { "label": "生成",   "action": "ppt_generate", "args": { }, "view": "ppt_studio" }
  ],
  "views": [
    { "nav_title": "PPT 加工台", "view_id": "ppt_studio",
      "handler": "ppt_open_studio", "icon": "📽️" }
  ]
}
```

| 字段 | 类型 | 说明 |
|---|---|---|
| `buttons[]` | 数组 | 聊天内按钮卡：`label`（文案）、`action`（IPC 方法名，走 `_h_<action>`）、`args?`（可选参）、`view?`（可选，点击切到指定视图） |
| `views[]` | 数组 | 动态分区入口：`nav_title`（分区展示名）、`view_id`（前端缓存 id）、`handler`（取视图内容的 IPC 方法，走 `_h_<handler>`）、`icon?` |

- 两字段均可选、缺省安全（缺省为空列表，向后兼容旧 skills.json）。
- 所有字段缺省安全、非法单条会被**降级丢弃并告警**（不阻塞其余按钮）。
- 由 `produce.py._brick_to_skill` 逐字段透传 brick.json → 内置 skill.json，
  `buttons/views` 天然随内置积木分发，无需额外打包改动。

## 2. 加载校验与命名控权（skill_library.py）

- 契约翻译：`_normalize_ui_buttons` / `_normalize_ui_views` 把 brick.json 原始 dict
  归一化为前端消费的结构；`_normalize_brick_fields` 在加载积木包时统一调用。
- **action / handler 命名**：小写字母开头，仅 `[a-z0-9_]`（`UI_DIRECTIVE_RE`）。
- **能力前缀白名单（平台唯一登记处）**：`UI_ACTION_PREFIXES = ("ppt_", "skill_", "tool_", "demo_")`
  ——声明层校验与前端 IPC 放行层共用此常量（单一事实源）。积木 UI 只能声明这些前缀下的
  按钮/视图，防止越权调用任意 `_h_*`。
- **管理员级动作显式排除**：`UI_ACTION_BLOCKED`（如 `skill_library_install`、`skill_toggle`、
  `tool_toggle` 等）命中前缀但仍被丢弃。

### 新增能力域（如 report_ / chart_）时的唯一平台改动

在 `skill_library.UI_ACTION_PREFIXES` 追加前缀即可，`chat_ui` 白名单零改动
（`chat_ui.UI_DYNAMIC_METHOD_PREFIXES` 已从本常量导入）。

## 3. IPC 接口（ipc.py）

- 后端实现 `_h_<action>`（积木私有，不进平台层）；`_dispatch` 自动路由，无需注册。
- 平台层只读接口 `_h_skill_views`：返回所有**启用且声明 views** 的技能的
  `buttons/views`，前端以此驱动动态分区与按钮卡。
- 放行判定 `_is_method_allowed`：静态白名单 ∪ 受控动态前缀（**仅非流式**）；
  积木交互不可挂 SSE 长连接（流式仅静态白名单）。

## 4. 前端渲染（chat_ui.py，全部通用，积木零 JS）

| 入口 | 机制 |
|---|---|
| 按钮卡 | `renderSkills` 读 `s.buttons` 渲染 `brick-btn`，点击走 `invokeSkillButton` → IPC |
| 动态分区 | `loadDynamicViews` 用 `skill_views` 快照驱动「工具 / 工作台」`nav-dyn-group` 导航项（启用即出现 / 卸载即移除） |
| 视图路由 | `switchSection` 命中动态分区时兜底 `renderGenericView` |
| 视图引擎 | `renderGenericView → renderDynamicViewEngine`：运行期 schema（`form` 控件 / `actions` 动作 / `preview` 实时预览 / `$form` 表单聚合）通用渲染 |
| 兼容回退 | 无法解析的 schema 回退 `renderGenericStaticShell`（静态容器 + 按钮） |

可交互视图需后端 `_h_<handler>` 返回形如 `{form: [...], actions: [...], preview: {...}}` 的
schema（参考 `IpcServer._ppt_studio_view()` 与 `test_ppt_studio_view.py` 的 schema 断言）。

## 5. 新积木接入清单（三步）

1. brick.json 声明 `buttons` / `views`（action/handler 落在注册表前缀内）；
2. 后端实现 `_h_<action>` 与（可选）`_h_<view handler>`（返回 schema）；
3. （可交互视图）确保 schema 字段与前端引擎约定一致。

三步之外**无平台层改动**。

## 6. 上传通道现状（D5 决策，2026-08-27）

- **现状**：平台暂无通用文件上传通道。`_h_file_index/update/remove/search` 是
  本地文件库**索引/搜索**，非上传；前端唯一 file input 是 `brickFileInput`
  （.brick 积木包导入）。`ppt_upload_template` / `ppt_upload_assets` 按钮保持未落地。
- **决策**：本轮不实现通用上传；推荐形态 = IPC 通用 upload handler
  （base64 编码 params 或独立 file/upload 通道）+ 前端 file input，留给「L2 素材参与」阶段。
- 现阶段素材类输入一律以结构参数（`actions.args` / `structure`）传递。

## 7. 自检清单

- [ ] action/handler 落在注册表前缀内
- [ ] 无敏感单元名（对照 `UI_ACTION_BLOCKED`）
- [ ] 前端钩子函数存在（`npm`/`node --check` 不强制；可跑
      `runtime/tests/test_chat_ui_frontend_contract.py` 静态断言回归）
- [ ] 声明层 + IPC 放行回归：`runtime/tests/test_ui_prefix_registry.py`
