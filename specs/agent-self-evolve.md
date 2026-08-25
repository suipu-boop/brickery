---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_ff7916bca02611f1a65b525400826444
    ReservedCode1: 4Yg2AVE0AQG4V9mlhyYq23EoMTRnypO54Lf3MMSdL6ZwUkBbcf+00mNctiK2J8XSljov9brZ2BRq+kIu9qkXv12Y0T8dZTEKLFEhCg6qvQuuW5UVW57sI5jz3eXsExmOJrpRMI6TG5V1X0TQ136ARQDCN/oIDH9325d0Y1hVbS4L4OkwcpkrEbhXEpo=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_ff7916bca02611f1a65b525400826444
    ReservedCode2: 4Yg2AVE0AQG4V9mlhyYq23EoMTRnypO54Lf3MMSdL6ZwUkBbcf+00mNctiK2J8XSljov9brZ2BRq+kIu9qkXv12Y0T8dZTEKLFEhCg6qvQuuW5UVW57sI5jz3eXsExmOJrpRMI6TG5V1X0TQ136ARQDCN/oIDH9325d0Y1hVbS4L4OkwcpkrEbhXEpo=
---

# 生成 agent 自进化（Agent Self-Evolve）

> 状态：草案待审
> 日期：2026-08-25
> 背景：借鉴 Hermes Agent（Nous Research，MIT 开源）Learning Loop：observe → distill → reuse → refine。让运行中的生成 agent 把解决过的多步任务自动蒸馏成可用积木，实现"越用越强"。
> 关联：`specs/hotplug.md`（热插拔）、`specs/memory-core-ui.md`（pending_candidates）、brick-vault verify 闸门

---

## 1. 目标与定位

### 1.1 目标

- 运行中的生成 agent，在多次成功解决同类任务后，自动把任务轨迹蒸馏成可复用积木
- 积木产出后走既有热插拔链路（BrickMarket）即时可用，无需重新组装/打包

### 1.2 定位（关键）

- **内置的是"蒸馏器"，不是"加工厂"**：只下沉"轨迹 → 候选积木"这一小段，不做完整生产 UI
- 共享层复用：brick.json 契约、verify 闸门、BrickMarket 激活，不复制
- 保留层归加工厂：正式生产、推积木库、版本发布仍是 brickery-factory 职责

### 1.3 非目标

- 不做工具/技能"语义推荐"：检索层保持关键词匹配（match/select 不动），自进化是**产出层**，两者不冲突
- 不替代加工厂：agent 自进化产出的是"本地私有候选"，不是正式市场积木

---

## 2. 闭环流程（每轮进化）

```
observe → 判定 → distill → verify → 确认 → reuse → refine →（回到 observe）
```

1. **observe**：记录多步任务轨迹（工具调用、决策分支、结果成败、耗时）
2. **判定**：同类任务累计 ≥ 3 次 → 触发蒸馏（防噪音）
3. **distill**：影子模型/LLM 把轨迹蒸馏为 brick.json 候选（trigger + content + 摘要）
4. **verify**：复用 verify 闸门校验契约与字段合法性
5. **确认**：候选进 `pending_candidates` 表，用户确认后激活
6. **reuse**：激活后进 `home/bricks`，BrickMarket 按关键词正常匹配触发
7. **refine**：使用结果反馈回写——成功强化、失败剪枝，影响后续蒸馏质量

---

## 3. 架构映射（全部复用现有机制）

| Hermes 环节 | brickery 复用 | 说明 |
|---|---|---|
| observe | `interoception`（工具轨迹/延迟）+ `consolidation` 影子引擎 | 扩展为记录完整多步任务轨迹，现有 last_tool_log 已有雏形 |
| distill | 新增蒸馏器（内核模块） | 影子模型 complete() 协议已支持，直接复用 |
| verify | brick-vault verify 闸门 | 同一套契约校验，不另起炉灶 |
| reuse | `BrickMarket` + `brick_runtime` 四型激活 | 产出即装即用，零改动 |
| refine | `suggester.push_feedback` 机制 | 采纳/忽略已有雏形，扩展为积木级成功/失败反馈 |
| 确认 | `pending_candidates` 表 | 记忆侧"中置信规律待确认"机制同构复用 |

---

## 4. 数据模型

候选积木 = brick.json 5 字段契约 + 以下扩展字段：

| 字段 | 说明 |
|---|---|
| `source` | `auto-evolve`（标记自进化产物） |
| `confidence` | 蒸馏置信度（0-1） |
| `attempts` | 触发蒸馏时的累计任务次数 |
| `task_trace_ref` | 源轨迹引用（可回溯） |
| `status` | `pending` → `active` / `rejected` / `disabled` |

---

## 5. 安全与质量闸门

1. **3+ 次门槛**：同类任务累计 ≥ 3 次才生成，避免一次任务噪音
2. **契约校验不过 → 丢弃或退回草稿**，绝不直接激活
3. **候选不自动激活**：一律进 pending_candidates 待用户确认
4. **本地私有**：默认不进市场、不进积木库；用户确认后可选择发布
5. **可逆**：激活后走 BrickMarket 既有卸载/停用语义（改名 .disabled），可恢复
6. **成本可控**：蒸馏由影子模型异步执行（O6 回合后），不阻塞主对话

---

## 6. 实施批次

- **批次 1（试点）**：observe 轨迹记录 + 蒸馏器 v1（仅纯 PromptBrick 形态）+ pending 确认 + 激活链路
- **批次 2**：refine 反馈精炼（成功/失败回写，失败剪枝）
- **批次 3（可选）**：候选积木发布积木库（经加工厂闸门 + 用户确认）

试点积木建议：会议纪要格式化、日志排查模板这类纯 PromptBrick，验证闭环后再扩展形态。

---

## 7. 边界与不变量

- 检索层不动：`SkillRegistry.match` / `ToolRegistry.select` 保持关键词匹配
- 仓库为真源：蒸馏器代码落 brickery 仓库（GitHub），副本只是运行实例
- 加工厂职责不变：正式生产/发布仍在加工厂，自进化是补充通道
- 内置小积木与市场积木分层不变：自进化产物归属"本地私有积木"，不混入内置/市场

---

## 8. 待确认问题（供审阅）

1. 同类任务判定：按"任务类型相似度"（如轨迹工具序列相似）还是"固定窗口内重复"？
2. 候选积木命名/分类规则：由 LLM 生成还是固定前缀（如 `evolve-<任务名>`）？
3. 发布路径：候选确认激活后是否允许直接发布积木库？还是本地复用 N 次后才开放发布？
4. 蒸馏器形态：内核能力（随底座分发）还是做成内置积木（可拔）？建议前者，底座行为不应可拔。
*（内容由AI生成，仅供参考）*

---

## 9. 实施状态（2026-08-25 更新，跨会话对齐用）

> 其他会话续做本主题时，先读本节再读正文。

### 批次 1：observe → 阈值 → distill → verify → pending → confirm/reject（已完成）

- **PR #9**（feat/agent-self-evolve → main，合并 commit 4fb173a）已合入：
  - `brickery/runtime/evolve.py`（新增）：observe / distill / observe_and_maybe_distill / list_candidates / confirm_candidate / reject_candidate / _verify
  - `brickery/runtime/loop.py`：回合后异步 `observe_and_maybe_distill`（try/except 静默保护）
  - `brickery/runtime/ipc.py`：新增 `_h_evolve_candidates` / `_h_evolve_confirm` / `_h_evolve_reject`
  - `brickery/runtime/chat_ui.py`：白名单放行 `evolve_candidates` / `evolve_confirm` / `evolve_reject`
  - `brickery/runtime/tests/test_evolve.py`（新增）：9 例全链路单测；runtime 全量 228 passed
- **运行中副本已同步**：/Applications/shadelingmac0.0.1.app/Contents/Resources/brickery-runtime/ 四个文件与仓库 diff 一致；chat_ui（18767）与底座 ipc（18765）均已重启加载新代码
- **实测验证**：`POST /api/ipc {"method":"evolve_candidates"}` 返回 `{"ok": true, "data": {"items": []}}`
- **重要事实**：evolve 数据落 `home/memory.db`（pending_candidates 表）与 `home/evolve.db`（evolve_traces 表）；home = `~/Library/Application Support/shadelingmac0.0.1`。历史代码曾误用 `paths.memory_db`，已改为 evolve.py 内部 `_memory_db(home)` 直接定位 `home/memory.db`（paths.py 只提供 `get_memory_db()` 且不带 home 参数，勿再回退）
- **当前限制**：~~chat_ui 前端无 evolve 候选展示 UI~~（2026-08-25 已消除：记忆页新增"自进化"tab，候选确认 + refine 统计均可界面操作）

### 批次 2（已完成）：refine 反馈精炼

- **PR #11**（feat/evolve-refine，待用户 GitHub 合并；依赖 #10 先合并后 diff 收敛）：
  - 提交 `e3e51c6`：feat: 自进化批次2 refine 反馈精炼（5 文件，+403/-6）
  - `brickery/runtime/evolve.py`：新增 `evolve_refine` 表（brick_name 主键 + usage/success/连成/连败/confidence/status）；`refine_from_trace`（成功 +0.1 / 失败 -0.15，conf<0.4 降级 degraded，conf<0.2 或连 5 败退役改名 `.retired-<name>` 不删数据，degraded 连续 3 成功自动恢复 active，retired 不自动复活）；`refine_stats` 只读统计；`observe_and_maybe_distill` 内嵌挂钩（静默保护，refine 失败不影响蒸馏主链路）
  - `brickery/runtime/ipc.py`：新增 `_h_evolve_refine_stats`（只读）
  - `brickery/runtime/chat_ui.py`：白名单放行 `evolve_refine_stats`；记忆页新增"自进化"tab：待确认候选（确认激活/拒绝）+ 已激活积木统计
  - `brickery/runtime/tests/test_evolve.py`：批次 2 新增 7 例（强化/降级恢复/双路径退役/退役不更新/非 evolve 不参与/统计形状）；runtime 全量 235 passed（`discover -s brickery/runtime/tests -t brickery/runtime`）
  - `specs/agent-self-evolve.md`：批次 2 状态更新 + §10.7 决策落盘
- **已决策项**（见 10.7）：惩罚 0.15 > 奖励 0.1；degraded 可自动恢复、retired 人工确认；批次 1+2 合并加"自进化"前端面板
- **运行中副本已同步**：/Applications/shadelingmac0.0.1.app/Contents/Resources/brickery-runtime/ 三个文件（evolve.py / ipc.py / chat_ui.py）与仓库 diff 一致；ipc（18765）与 chat_ui（18767）已重启加载新代码（新 PID 42541/42540）
- **实测验证**：`POST http://127.0.0.1:18767/api/ipc {"method":"evolve_refine_stats"}` 返回 `{"ok": true, "data": {"items": []}}`（无已激活积木，符合预期）；`evolve_candidates` 同返回空
- **重要踩坑**：
  - 浮点边界：0.5-0.15*2 计算得 0.1999... 会提前误触发 `conf<0.2` 退役判断 → `refine_from_trace` 内 conf 先 `round(...,3)` 再判定
  - 全量测试必须 `python -m unittest discover -s brickery/runtime/tests -t brickery/runtime`；直接 `discover -s brickery/runtime/tests` 因测试模块相对导入（`from .base import ...`）报 `_FailedTest`，属启动方式问题非代码问题
  - IPC 实测走 chat_ui 18767（HTTP 桥）；18765 底座为 JSON Lines 协议，curl 会 `Received HTTP/0.9 when not allowed`
  - 重启命令若被用户中断，进程可能已起：用 `lsof -i :18765 -i :18767` 验证新 PID，勿重复 kill（log 中 OSError Address already in use 为启动竞争噪声）

### 关联规则（已拍板）

- 快速迭代通道：`specs/agent-test-feedback-loop.md`（状态已确立）——先改运行中副本、重启即测、必须同步仓库
- 发布流程：`specs/release-process.md`（v1）——PR 合并涉及 runtime 等范围须走发布闭环；本次因判定为生成 agent 侧能力，未重建工坊产物

---

## 10. 批次 2 设计：refine 反馈精炼（2026-08-25 落盘，已按设计实现）

> 状态：已实现（代码 + 单测 + 前端面板 + 运行中副本同步 + IPC 实测通过）。

### 10.1 目标

批次 1 打通"候选产生 → 确认激活"。批次 2 打通激活后的**效果闭环**：让已激活的自进化积木随真实使用反馈自我强化/剪枝，避免"激活即永久"。

### 10.2 信号来源（复用，不新增埋点）

- 复用现有 `observe` 回合信号：每次回合记录 tools（含已激活 evolve 积木名）+ success 标志
- evolve 积木被命中调用时，其名称出现在回合 tools 中 → 该回合 success 即为该积木的效果信号
- 无需在技能执行路径新增埋点，纯数据层聚合

### 10.3 数据模型（evolve.db 新增 refine 统计，不落 manifest）

| 字段 | 说明 |
|---|---|
| brick_name | 已激活 evolve 积木名（主键） |
| usage_count | 被调用次数（回合 tools 含该名） |
| success_count | 其中 success=1 的次数 |
| confidence | 0.0-1.0，初始 0.5，激活时按批次 1 规则 |
| status | active / degraded / retired |
| last_result_at | 最近一次效果时间 |

> 不写 manifest：refine 是运行时统计，manifest 保持稳定（name/trigger/content 不变）。

### 10.4 精炼算法（每回合 observe 后异步执行，与蒸馏同线程池）

**强化（success）**：
- `confidence = min(1.0, confidence + 0.1)`；success_count+1
- 连续 3 次成功且 confidence ≥ 0.7 → status 保持 active，无需额外动作（已激活）

**剪枝（fail）**：
- `confidence = max(0.0, confidence - 0.15)`（失败惩罚权重高于成功奖励，防劣质积木虚胖）
- confidence < 0.4 → status = degraded：不再作为默认候选（SkillRegistry.match 命中后降权，后续会话不再优先注入该积木）
- confidence < 0.2 或连续 5 次失败 → status = retired：从运行副本中移出（bricks 目录改名 `.retired-<name>`，不删数据，可追溯）

**安全闸门**：
- 降级/退休自动执行（可逆：改名保留数据），**不删除任何文件**
- 退休即停止注入，恢复需用户手动确认（保留 `.retired` 目录即可恢复）
- refine 只作用于 `source` 以 `evolve:` 开头的积木，内置/市场积木不参与

### 10.5 对外接口

- IPC 新增 `evolve_refine_stats`：返回各 evolve 积木的 usage/success/confidence/status（只读）
- 现有 `evolve_candidates` 不变；refine 状态在 chat_ui 前端（若加面板）一并展示

### 10.6 验证方案

- 单测：构造 3 成功 / 2 成功+2 失败 / 连续 5 失败 三组轨迹，断言 confidence 升降与 status 迁移
- 全量回归：runtime/tests 228+ 用例不回归
- 实机：批次 1 已激活候选后，正常使用若干回合，`evolve_refine_stats` 可见计数变化

### 10.7 待确认问题（2026-08-25 已决策）

1. ~~惩罚权重 0.15 vs 奖励 0.1 是否合理？~~ → **已定：失败 -0.15 / 成功 +0.1**，惩罚更重防劣质积木虚胖
2. ~~retired 后是否允许自动恢复？~~ → **已定：degraded 可自动恢复（连续 3 次成功回 active）；retired 必须人工确认**，保留自动进化闭环同时守住安全底线
3. ~~前端是否需要 refine 统计展示面板？~~ → **已定：批次 1+2 合并加"自进化"面板**：候选列表（确认/拒绝）+ refine 统计（usage/success/confidence/status）
