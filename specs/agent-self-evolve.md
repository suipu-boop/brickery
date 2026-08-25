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
