# 积木加工厂（Brickery Factory）规划

> 状态：定位已确认，待实施（2026-08-18 用户拍板）
> 定位：工坊（Workbench）= 消费积木；加工厂（Factory）= 生产积木。
> 一句话：**工坊用积木造 agent，加工厂造积木。**

## 为什么需要加工厂

积木是平台的核心资产，但当前只能手写 brick.json + 实现文件，靠 git 手工同步。
没有生产工具，积木生态长不大，也容易乱。加工厂是**规范化的生产工具**：
用模板堵住乱的空间，用验证器拦住不合格的积木。

## 核心功能

1. **积木清单管理**：浏览 brick-vault 全部积木，新建/编辑/删除
2. **积木编辑器**：
   - 编辑 brick.json 字段（name / summary / description / category / risk_level /
     requires / conflicts / tags / buttons / capabilities / dependencies 等）
   - 管理实现文件（PromptBrick 的 prompt / ServiceBrick 的 .py / 可执行脚本）
3. **自检验证器**（地基级，必须优先）：
   - JSON schema 校验
   - 按钮与内核 handler 对齐检查
   - 资源文件存在性
   - 依赖/冲突声明完整性
   - **发布前强制自检：不过验证器不让 push**
4. **脚手架**：从模板新建积木，自动长出标准壳
   - PromptBrick：提示词类（如 scheduler）
   - ServiceBrick：服务类（如 feishu / telegram）
   - ConnectorBrick：连接类
5. **发布同步**：一键 commit + push 到 GitHub（shadeling-bricks），同时刷新本地缓存 `~/.brickery/vault`

## 规范机制（防乱的三道闸）

| 机制 | 作用 |
|---|---|
| 三层边界 | 底座不可拔 / 出厂内置 / 市场积木，职责不越界；心脏、记忆写死内核，不积木化 |
| schema 契约 | brick.json 字段统一，结构固定，不各写各的 |
| 验证闸门 | 脚手架生成标准壳 + 发布前强制自检，不合格不进市场 |

## 形态

独立 web 面板（端口 8767），复用工坊蓝图风与 server 模式；后续需要再打包独立 app。

## 好玩积木示例（平台可扩展性的证明）

积木平台意味着**任何能力都能做成积木**，包括娱乐向：

- **内置小游戏**：如贪吃蛇 / 2048 / 扫雷，做成 ServiceBrick（前端界面 + 内核逻辑），
  用户在工坊里像选工具一样选一个游戏积木，产出的 agent 自带游戏入口
- 其他方向：画图板、记账、日记、番茄钟、随机点子生成器……

例子说明：积木不只是"工作能力"，它是**可组装的任意零件**。
加工厂就是让这些好玩的积木能源源不断被造出来、且保持规范的生产线。

## 实施顺序建议

1. **脚手架 + 验证器**（地基，先立规矩）
2. **积木编辑器**（清单 + 编辑 brick.json + 实现文件）
3. **发布同步**（commit/push + 刷新缓存）
4. （可选）打包独立 app

## 复用与新增

- **复用**：brick-vault 目录结构、brick.json schema（assembler.py 的 Brick）、
  sync.py 同步机制、web 蓝图风样式
- **新增**：factory 前端页面 + 路由（可并入现有 server.py 或独立 server）
- **不动**：工坊现有选积木/组装/产出逻辑

## 关联文档

- 工坊设计：`specs/web-workbench-app.md`、`specs/workbench_ui_redesign.md`
- 平台规划：`specs/brickery.md`、`ROADMAP.md`
