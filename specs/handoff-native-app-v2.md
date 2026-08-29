---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_62de6f19a34d11f1bc17525400826444
    ReservedCode1: JKtwLpq8w9tsd1ub/y4nJYiZgHYQ54uO3n4YzRMbHPt5G+togZqDimB8tmanUQqlMBSkWsXYqNEAMCdbmCBj9AFeGIK2GYKSVROmq1hyQEU6U5Mc39efCaQ8z2q18fwJDd3gzBVSJpshR8+L27IKzBkLkwUo9JOobOKslx+ysxdOG4+5vS8DBarEQ4M=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_62de6f19a34d11f1bc17525400826444
    ReservedCode2: JKtwLpq8w9tsd1ub/y4nJYiZgHYQ54uO3n4YzRMbHPt5G+togZqDimB8tmanUQqlMBSkWsXYqNEAMCdbmCBj9AFeGIK2GYKSVROmq1hyQEU6U5Mc39efCaQ8z2q18fwJDd3gzBVSJpshR8+L27IKzBkLkwUo9JOobOKslx+ysxdOG4+5vS8DBarEQ4M=
---

# 会话交接 · 完全原生 app 改造（新会话从这里开始）

> 生成时间：2026-08-29 09:1x
> 上一会话收尾：git 全部提交推送、遗留分支/worktree 已清理（6 仓库 main 同步干净）

## 拍板结论（上一会话已定）

**目标**：彻底废弃「app 外壳 + 本地 web 服务 + 浏览器」形态，做**完全原生 SwiftUI app**（界面直连 IPC 18765，不再启动 18766/18767 服务进程）。积木体系保留，UI 全部原生重写。

**方案文档**：`~/Dev/brickery/specs/product-line-simplify-native-v2.md`（commit fc0d06b，已推送 main）

### 产品线（已拍板）
- 保留：Shadeling（app 本体）、brickery（内核 runtime）、brick-vault（积木库）
- 剔除：brickery-workbench（积木工坊，归档冻结）
- 归并：brickery-factory（内部工具）、brickery-meta（并入 docs）、shadeling-skill-repo（并入 brick-vault）

### 积木（已拍板）
- 原生 UI 积木：**ppt-studio**（PPT 加工台）、**vault**（文件柜，原生文件柜界面）
- 工具积木：**docwrite + document-writer**（文档生成一组，支撑 PPT 链路，保留进底座工具层）
- **high-config-doc / DocWritePro**：冻结，不随 app 分发（运行时下载 193MB editor_sdk，违背简化方向）；内核实现保留不删
- **demo-studio**：待拍板（建议仅开发期验证工具）
- 其余 17 个基础能力积木：收进底座原生实现

## 待拍板（新会话第一件事）
1. demo-studio 是否作正式积木
2. 17 个基础功能进底座的优先级（建议第一批：聊天周边/文件读写/浏览器/定时任务/备份）
3. 归并动作确认（factory/meta/skill-repo 按方案执行）

## 新会话路线（M1 起）
- **M1 产品线瘦身**：归档 workbench/meta/factory 仓库（冻结不删历史）、shadeling-skill-repo 并入 brick-vault
- **M2 原生底座**：SwiftUI app 迁入（native-app.md 思路，作废重启），原生引导 OnboardingView（八家预设）+ 聊天直连 IPC
- **M3 基础功能原生化**：17 小积木分批收进底座
- **M4 积木原生 UI 框架**：原生 view 注册机制 + ppt-studio/vault 原生重写
- **M5 发布闭环**：签名公证、DMG、CI 单测

## 环境事实（沿用）
- 运行副本：`/Applications/shadelingmac0.0.1.app`，runtime 在 `Contents/Resources/brickery-runtime/`，home=`~/Library/Application Support/shadelingmac0.0.1`
- 进程：BrickeryApp(壳) + ipc(18765) + setup_wizard(18766) + chat_ui(18767)；新形态只留 ipc
- 运行副本 Python 3.12（依赖以 3.12 为准，勿用仓库 3.14 .venv）
- 技能静态加载：IpcServer 启动时一次性 load，改 skills.json 需重启
- 用户规则：称呼"老板"、禁用 emoji；先落 specs 供拍板；kill/重启先告知请求确认；不直推 main（方案类改动先落盘）
*（内容由AI生成，仅供参考）*
