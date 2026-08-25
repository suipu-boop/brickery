# 记忆柜抽屉/节点 id 字段不匹配修复

> 状态：已修复（2026-08-25）
> 范围：brickery runtime（chat_ui.py）

## 问题现象

生成 agent 前端打开记忆柜抽屉时弹「抽屉不存在」，删除抽屉无反应（`drawer_delete` 删空 id 无效）。

## 根因

cabinet 三表字段为：
- `cabinet_drawers.drawer_id`
- `cabinet_nodes.node_id`
- `cabinet_edges.source/target` 指向 `node_id`

前端 `chat_ui.py` 原用 `d.id` / `dr.id` / `n.id` 读取，导致传参 `undefined`：
- `drawer_get(undefined)` → 返回 null → 前端 alert「抽屉不存在」
- `drawer_delete(undefined)` → 删空 id 无效

另叠加启动竞态干扰：chat_ui 与 ipc 启动相差约 3 分钟期间 webview 请求被 ConnectionResetError 吞掉，进一步伪装成「抽屉不存在」。

## 修复

`chat_ui.py` 全部改用兼容写法 `drawer_id||id`、`node_id||id`：
- renderCabinet 列表：`d.drawer_id||d.id`
- openDrawer / deleteDrawer 传参修正
- 工作台 hint / 按钮（addNode / addEdge / syncRecordbook / editKit）：`d.drawer_id||d.id`
- editNode / saveNode：`dr.drawer_id||dr.id`，节点匹配 `n.node_id||n.id`
- 节点列表 / 图谱 / 记录本 R/S/P 锚点识别：`node_id||id`

## 验证

创建 `drw_bf2fc2ce1fc3` → 列表 → drawer_get → drawer_delete 全链路 ok。

## 影响范围

- 仅改 `brickery/runtime/chat_ui.py`（前端 JS 字段读取）。
- 改后同步 App 包运行时并重启 chat_ui 生效。
