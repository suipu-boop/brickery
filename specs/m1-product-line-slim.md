---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_4038a5b5a39311f1abe1525400e6dd8f
    ReservedCode1: onOsFUwGq1nyV8vf9+yl9+S8Fn7t/wZfNDJDgmuGBJ5L8GxK/hiKP8E4DscLn7mci6RSey7S1XWe82gPX2CMz9TxCP5t8Y3dp8IuqFGv7g0rGO8eegSjJWil7jinLj8Y8wR1rYhaPG3W9E7qPfmsxsZpzHC8sWJvQXdA8GwOkdzIS2xmHhklkap4fm8=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_4038a5b5a39311f1abe1525400e6dd8f
    ReservedCode2: onOsFUwGq1nyV8vf9+yl9+S8Fn7t/wZfNDJDgmuGBJ5L8GxK/hiKP8E4DscLn7mci6RSey7S1XWe82gPX2CMz9TxCP5t8Y3dp8IuqFGv7g0rGO8eegSjJWil7jinLj8Y8wR1rYhaPG3W9E7qPfmsxsZpzHC8sWJvQXdA8GwOkdzIS2xmHhklkap4fm8=
---

# M1 产品线瘦身 · 实施 specs

> 状态：已执行（2026-08-29）
> 依据：product-line-simplify-native-v2.md（全部已拍板）、handoff-native-app-v2.md
> 盘点：file-agent 2026-08-29（只读，未改动任何文件）

## 一、盘点事实（2026-08-29）

| 仓库 | 绝对路径 | 远程 | 分支 | 工作区 |
|---|---|---|---|---|
| brickery（内核） | /Users/suipu/Dev/brickery | git@github.com:suipu-boop/brickery.git | main | **不干净**（2 份 specs 未提交） |
| brickery-factory | /Users/suipu/Dev/brickery-factory | git@github.com:suipu-boop/brickery-factory.git | main | 干净 |
| brickery-meta | /Users/suipu/Dev/brickery-meta | https://github.com/suipu-boop/brickery-meta.git | main | 干净 |
| brickery-workbench | /Users/suipu/Dev/brickery-workbench | https://github.com/suipu-boop/brickery-workbench.git | main | 干净 |
| brick-vault（shadeling-bricks） | /Users/suipu/Dev/brick-vault | git@github.com:suipu-boop/shadeling-bricks.git | main | 干净 |
| Shadeling（app） | /Users/suipu/Dev/Shadeling | https://github.com/suipu-boop/shadeling.git | main | 干净 |
| shadeling-skill-repo | /Users/suipu/Dev/shadeling-skill-repo | **非 git 仓库（无 .git）** | — | — |

关键事实：
1. **brick-vault `bricks/` 实际 22 个积木目录**（原文档写 21，按实际 22 修正）：ax / backup-restore / browser / code-quality-chain / demo-studio / doctor / docwrite / engine-api / engine-local / feishu / hello-marvis / high-config-doc / mcp / meeting-minutes / multi-agent / ppt-studio / rules / scheduler / skill-library / telegram / vault / visualize。
2. **shadeling-skill-repo 不是 git 仓库**：并入 brick-vault 采用"内容搬运 + 核对合并"，不 git init。
3. **brick-vault `skills/` 已含 code-reviewer / document-writer / meeting-minutes / pdf-extractor**，与 skill-repo 技能目录重叠 → 以 brick-vault 现有版本为基准，核对差异后补缺，不重复覆盖。
4. brickery 仓库 2 份 specs（handoff / product-line v2）为本次改造文档，M1 开工先提交。

## 二、动作清单

| # | 动作 | 说明 | 风险 |
|---|---|---|---|
| A1 | 提交 brickery specs 修改 | 提交并推送 handoff-native-app-v2.md + product-line-simplify-native-v2.md + m1-product-line-slim.md（本文件） | 低（文档提交） |
| A2 | 本地归档 factory / meta / workbench | 每仓库打冻结 tag（`archive-2026-08-29`），整体移入 `~/Dev/archive/`（保留 .git 与全部历史），原路径放 README 指针 | 🟡 移动目录（可逆，历史保留） |
| A3 | skill-repo 并入 brick-vault | 逐目录核对 code-reviewer / document-writer / meeting-minutes / pdf-extractor 与 vault `skills/` 差异，缺失内容补入；index.json 对照更新 | 🟡 内容合并（先比对后写入） |
| A4 | 冻结 skill-repo 目录 | 原目录移入 `~/Dev/archive/shadeling-skill-repo` | 低 |
| A5 | brick-vault 冻结标记 | 新增 `FROZEN.md`（22 积木分类清单：保留活跃 / 冻结保留 / 冻结归档），不删任何目录 | 低 |
| A6 | 产品线文档更新 | 在 brickery specs 记录归档清单、三核心说明、冻结清单 | 低 |

### 冻结清单（22 个积木分类）

| 分类 | 积木 | 后续处理 |
|---|---|---|
| 保留活跃 | ppt-studio、vault | 原生 UI 积木，M4 原生重写 |
| 工具层保留 | docwrite | 进底座工具层（支撑 PPT 链路） |
| 冻结保留（不删） | high-config-doc、demo-studio | 内核实现保留；demo-studio 仅开发期验证工具 |
| 冻结归档（17 个） | ax / backup-restore / browser / code-quality-chain / doctor / engine-api / engine-local / feishu / hello-marvis / mcp / meeting-minutes / multi-agent / rules / scheduler / skill-library / telegram / visualize | 收进底座原生实现（M3 一次全收），vault 目录冻结不再维护 |

## 三、验收标准

1. factory / meta / workbench 三仓库已打 tag 并移入 `~/Dev/archive/`，远程仓库数据未动（历史完整保留）。
2. shadeling-skill-repo 技能源码已并入 brick-vault（差异核对记录可查），原目录冻结。
3. brick-vault 新增 FROZEN.md，22 积木分类明确，无任何目录被删除。
4. brickery specs 已提交推送，产品线三核心（Shadeling / brickery / brick-vault）文档明确。

## 四、风险与待确认

1. **GitHub 端 archive**：本地冻结不影响远程仓库可访问性。是否同时将 factory / meta / workbench 在 GitHub 上标记 archive？若需要，用 gh CLI 或 browser-agent 操作（执行前告知）。
2. **skill-repo 与 vault skills/ 重叠取舍**：默认以 vault 现有版本为基准（skill-repo 可能是旧发布源），仅补 vault 缺失内容。若老板要求以 skill-repo 为准则调整。
3. 归档目录位置约定为 `~/Dev/archive/`，后续 M2-M5 不再触碰。
*（内容由AI生成，仅供参考）*

## 五、执行结果记录（2026-08-29 已执行）

| # | 动作 | 实际结果 | 关键证据 |
|---|---|---|---|
| A1 | 提交推送 brickery specs | ✅ 完成 | commit `d26ef6e`，已推送 origin/main（3 文件：handoff-native-app-v2.md / product-line-simplify-native-v2.md / m1-product-line-slim.md） |
| A2 | 归档 factory / meta / workbench | ✅ 完成 | 三仓库各打 tag `archive-2026-08-29`，整体移入 `~/Dev/archive/`（.git 与历史完整保留）；原路径写入 README.md 指针 |
| A3 | skill-repo 并入 brick-vault | ✅ 完成 | 四个技能目录与 vault `skills/` 逐项 diff 完全一致（IDENTICAL），vault 无缺失内容，未新增/覆盖/删除任何文件；vault skills/index.json 已覆盖全部 4 技能，无需修改；差异核对记录见 temp/a3-skill-merge-diff-record.md |
| A4 | 冻结 skill-repo | ✅ 完成 | `~/Dev/shadeling-skill-repo` → `~/Dev/archive/shadeling-skill-repo` |
| A5 | brick-vault 冻结标记 | ✅ 完成 | 新增 `/Users/suipu/Dev/brick-vault/FROZEN.md`，22 积木分类齐全，未删除任何目录 |
| A6 | 产品线文档更新 | ✅ 完成 | 本文件状态行已改"已执行"，本节约为执行记录 |

归档后目录布局（~/Dev 现状）：

| 路径 | 说明 |
|---|---|
| /Users/suipu/Dev/brickery | 内核仓库（主开发） |
| /Users/suipu/Dev/brick-vault | 积木仓库（保留活跃 ppt-studio/vault + 工具层 docwrite，其余冻结） |
| /Users/suipu/Dev/Shadeling | app 仓库 |
| /Users/suipu/Dev/archive/brickery-factory | 已冻结（tag archive-2026-08-29） |
| /Users/suipu/Dev/archive/brickery-meta | 已冻结（tag archive-2026-08-29） |
| /Users/suipu/Dev/archive/brickery-workbench | 已冻结（tag archive-2026-08-29） |
| /Users/suipu/Dev/archive/shadeling-skill-repo | 已冻结（旧技能发布源，内容已并入 vault） |

