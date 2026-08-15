# Web 组装工作台优化方案（小白视角）

> 状态：**方案已落盘，代码未改**（2026-08-15）
> 背景：用户以小白使用者身份审查本地 Web 面板，反馈「画面不好看、积木没有解释」，要求优化建设。
> 新会话续做：先读本文件 + `ROADMAP.md`，按「待办」执行。

## 现状（已审查）

- 前端：`/Users/suipu/Dev/brickery/web/index.html`（239 行，单文件，无外部依赖）
- 后端：`/Users/suipu/Dev/brickery/brickery/web/server.py`（纯 stdlib http.server，127.0.0.1:8765）
- 积木库：`/Users/suipu/Dev/brick-vault/`（index.json + bricks/<name>/brick.json）
- 服务进程：PID 15646 监听 127.0.0.1:8765（历史记录，重开会话后需确认是否存活）

## 小白视角审查结论（问题清单）

1. **积木没有解释（根因）**：`server.py` 的 `_api_bricks` 只返回组装字段
   （name/version/risk_level/requires/conflicts/resources），**未透传**
   brick.json 里已有的 `summary` / `description` / `category` / `tags` / `capabilities` / `dependencies`。
   前端 `renderBrickList` 里 `b.description` 恒为 undefined，兜底显示 `b.name`，等于没解释。
   - 根因在 `brickery/assembler.py` 的 `Brick` 类：`from_manifest` 只保留组装字段，丢弃展示字段。
2. **画面不好看**：浅灰卡片三栏 grid、单一蓝 accent、无图标、无分类分组、无视觉层级，
   属典型「AI 默认风」；无空状态/加载/错误状态设计。
3. **交互不直观**：小白不知道「点击积木加入」；风险等级（低/中/高）无解释；
   无分类筛选、无搜索高亮、无积木详情展开；产出成功后无下一步引导。

## 优化方案

### A. 后端（数据打通，积木解释的根因）

- `brickery/assembler.py`：`Brick` 类增加展示字段
  `summary` / `description` / `category` / `tags` / `capabilities` / `dependencies`，
  `from_manifest` 从 raw manifest 填充（不改变组装逻辑，纯增量）。
- `brickery/web/server.py`：`_api_bricks` 透传上述字段。
- 验证：`curl 127.0.0.1:8765/api/bricks` 应能看到 summary/description/category/tags。

### B. 前端（重设计 `web/index.html`）

设计方向：**「工坊蓝图」风**——暖纸感底色 + 墨色文字 + 琥珀/朱红强调，
积木像实体积木块；避免 AI slop（紫蓝渐变/霓虹/纯黑纯白）。

- 布局：三栏（积木库 / 组装区 / 方案与产出），顶部 header 带步骤引导（1 选积木 → 2 组装 → 3 产出）。
- 积木库：
  - 按 `category` 分组展示，组头可折叠；
  - 每块积木卡片：名称 + 分类标签 + 风险标签 + `summary` 一句话解释；
  - 点击展开详情：`description` 全文 + `tags` + `capabilities` + 依赖/冲突/资源；
  - 搜索框（名称/summary/tags 匹配 + 命中高亮）+ 风险等级筛选。
- 组装区：点选/拖拽加入，显示安装顺序与依赖关系，可移除。
- 方案与产出：保留 /api/assemble 校验（拓扑序 + 资源合计），
  产出成功后给出产物路径 + 「下一步」引导（打开目录 / 再做一个）。
- 状态设计：加载骨架屏、空状态引导文案、错误状态可重试。
- 技术：单文件 HTML+CSS+JS，无外部依赖（与现状一致，纯 stdlib 可服务）。

### C. 验收

- 启动服务 → 浏览器打开 127.0.0.1:8765 → 小白视角走一遍：
  看到积木解释、分类分组、能搜索/筛选、能组装、能产出。
- 全量单测不回归（`python -m unittest discover -s brickery/runtime/tests -t brickery -p "test_*.py"`）。

## 待办（重开会话后按序执行）

- [ ] A1：`assembler.py` Brick 类加展示字段 + `from_manifest` 填充
- [ ] A2：`server.py` `_api_bricks` 透传展示字段
- [ ] B1：重写 `web/index.html`（工坊蓝图风，见方案 B）
- [ ] C1：启动服务实测 + 单测回归
- [ ] C2：git commit（brickery 仓库）

## 相关文件

- 前端：`/Users/suipu/Dev/brickery/web/index.html`
- 后端：`/Users/suipu/Dev/brickery/brickery/web/server.py`
- 数据模型：`/Users/suipu/Dev/brickery/brickery/assembler.py`
- 积木库：`/Users/suipu/Dev/brick-vault/`
