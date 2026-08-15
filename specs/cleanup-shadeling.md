---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_18401b0d984811f18cca525400e6dd8f
    ReservedCode1: OaSNrO45KhYZyIMVuGvUyOS9tAuUSkIw4cXtLjOR2OWHnZ/0joK43ZZYcsm1c05LOYdMMBeliqI7QZJYJZWG74EiO9FJ3LxEvRaVqAyzhrCuhucS10sG7T8+FQyM32kXqViBes3T5gWHzS+Lo/MsWIRYOB7nxRssn24lXuxV35FuQZk9QPY/aRpUhW4=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_18401b0d984811f18cca525400e6dd8f
    ReservedCode2: OaSNrO45KhYZyIMVuGvUyOS9tAuUSkIw4cXtLjOR2OWHnZ/0joK43ZZYcsm1c05LOYdMMBeliqI7QZJYJZWG74EiO9FJ3LxEvRaVqAyzhrCuhucS10sG7T8+FQyM32kXqViBes3T5gWHzS+Lo/MsWIRYOB7nxRssn24lXuxV35FuQZk9QPY/aRpUhW4=
---

# 净化：清除 shadeling 残留

> 目标：brickery 仓库（本地 + GitHub）无 shadeling 污染，干净独立。

## 扫描结果（2026-08-15）

### 1. 无 shadeling 命名的文件/目录
`find -iname "*shadeling*"` 无结果。文件系统层面干净。

### 2. 代码残留（需清理）

| 位置 | 残留 | 风险 | 处理 |
|---|---|---|---|
| `brickery/brick_runtime.py` `_host_import` | 优先尝试 `shadeling.runtime.<module>`，其次本包 | 低 | 删 shadeling 候选，只走本包（B6 后独立运行时不需要宿主回退） |
| 16 个文件 39 处 | `SHADELING_HOME` / `~/.shadeling` 数据目录命名 | 中 | 改名 `BRICKERY_HOME` / `~/.brickery` + 数据迁移 |

`_host_import` 调用的模块（tool_providers / skill_library / binary_manager / engine_providers / config / engine_router / memory_providers / vault_store）已全部在 `brickery/runtime/`（B1–B5 迁入），删 shadeling 候选无影响。

### 3. 文档描述（建议保留）
`specs/rectify.md` / `b6-packaging.md` / `brickery.md` / `p3-runtime.md` 中的 shadeling 描述是**历史决策与规划记录**（Shadeling=产出物品牌），保留不删。

## 清理步骤

### A. brick_runtime.py 宿主回退（低风险，直接执行）✅ 已完成
- `_host_import` 候选列表去掉 `shadeling.runtime.<module>`，只保留 `brickery.runtime.<module>`
- docstring 同步更新

### B. SHADELING_HOME → BRICKERY_HOME（中风险，需确认后执行）✅ 已完成
- 16 个文件 50 处：`SHADELING_HOME` → `BRICKERY_HOME`，`~/.shadeling` → `~/.brickery`
- 涉及：memory/filing.py、runtime/{vault_store,repo_map,skills,paths,supervisor,ipc,tools,skill_library,sandbox,edsdk_pro,builtin_tools}.py、interoception/{system,__init__}.py、tests/{test_vault_store,test_sandbox}.py
- 数据迁移：`~/.shadeling` 是 Shadeling 仓库数据目录（Shadeling 仓库仍在引用），**不迁移**；brickery 统一用 `~/.brickery`（已存在）

### B2. 运行时标识 shadeling → brickery（需确认后执行）✅ 已完成
- 18 个文件 43 处：logger 名、环境变量（BRICKERY_HF_MIRROR / BRICKERY_SKIP_CONNECTORS / BRICKERY_BUILTIN_SKILLS）、keychain（brickery-vault）、默认产出目录（~/Documents/Brickery/Output）、测试临时目录前缀、打印前缀（[Brickery IPC]）、导出文件名、User-Agent
- schema 契约统一为 `brickery-memory-export/v1`（无旧导出数据，安全改名）

### 保留项（品牌身份 / 数据契约 / 迁移兼容 / 来源注释）
- 品牌身份：`你是 Shadeling`（产出 agent 系统提示词）、docwrite 文档元数据（dc:creator Shadeling）、MCP clientInfo——Shadeling=产出物品牌
- 数据契约：`shadeling-skill-repo/v1`（技能仓库 schema，外部仓库未改名）
- 迁移兼容：`~/shadeling-runtime`（旧数据迁移路径）、`Shadeling_*` 备份目录、`~/Dev/Shadeling` 源码保护区
- 来源注释：`从 Shadeling 迁入` 等 docstring

## 验收
- `grep -rni "shadeling" --include="*.py"` 仅剩品牌身份/数据契约/迁移兼容/来源注释，无路径与运行时标识残留
- 全量单测绿（263 passed + 1 skipped）
- 推送后 GitHub 同步干净

---

# 追加：GitHub 仓库清理（2026-08-15）

> 目标：`suipu-boop` 组织下无 shadeling 命名 / 废弃仓库，只留有效库。

## 仓库盘点

| 仓库 | 状态 | 判断 |
|---|---|---|
| `brickery` | 公开，活跃 | 主项目，**保留** |
| `shadeling` | 私有 | Shadeling 内核，**保留** |
| `shadeling-bricks` | 公开 | = 本地 `brick-vault` 的 remote（积木库远端），**保留** |
| `shadeling-skills` | 公开，已归档 | 技能市场源，brickery 默认源仍引用，**待迁移后删** |
| `shadling` | 公开，已归档 | 拼写错误早期废弃仓库（仅 index.html，2026-04），**删除** |

## 删除项

### 1. `shadling`（无依赖，直接删）
- 无任何代码/文档引用，删除无影响。
- 阻塞：gh token 缺 `delete_repo` scope，`gh auth refresh -h github.com -s delete_repo` 网络超时（GitHub 连接不稳定）。
- 出路：网络恢复后重试授权；或用户在 GitHub 网页手动删除。

### 2. `shadeling-skills`（有依赖，先迁移再删）
- 依赖：`brickery/runtime/ipc.py:1018` `DEFAULT_PUBLIC_SKILL_REPO_URL` 指向 `https://raw.githubusercontent.com/suipu-boop/shadeling-skills/main/index.json`（正式 .app 公网技能源）。
- 迁移步骤：
  1. 技能市场源内容（技能目录 + index.json）并入 `brick-vault`（shadeling-bricks），技能索引与积木索引并存；
  2. `ipc.py:1018` URL 改为 `https://raw.githubusercontent.com/suipu-boop/shadeling-bricks/main/<技能索引路径>`；
  3. 删除 GitHub `shadeling-skills` + 本地 `/Users/suipu/Dev/shadeling-skills` clone；
  4. 同步更新 `fixtures/skill_repo`（开发态本地源）来源。
- 待用户确认迁移方案后执行（涉及代码改动，按惯例先审方案）。

---

# 追加：整合方案（2026-08-15，用户确认：整合后删除，减少仓库数量）

> 目标：5 个仓库 → 3 个（brickery / shadeling / shadeling-bricks）。

| 仓库 | 处置 |
|---|---|
| `brickery` | 保留（平台） |
| `shadeling` | 保留（私有内核，Shadeling=产出物品牌，P5 待接入） |
| `shadeling-bricks` | 保留（= brick-vault 远端，积木库；并入技能市场源） |
| `shadeling-skills` | **并入 shadeling-bricks 后删除**（技能市场源） |
| `shadling` | **删除**（废弃） |

## 执行步骤
1. 技能市场源（`/Users/suipu/Dev/shadeling-skills` 内容：技能目录 + index.json）并入 `brick-vault`（shadeling-bricks），技能索引与积木索引并存；
2. `ipc.py:1018` `DEFAULT_PUBLIC_SKILL_REPO_URL` 改为指向 `shadeling-bricks` 的技能索引路径；
3. 删除 GitHub `shadeling-skills` + 本地 `/Users/suipu/Dev/shadeling-skills` clone；
4. 删除 GitHub `shadling`；
5. 同步更新 `fixtures/skill_repo`（开发态本地源）来源；
6. 跑全量单测 + e2e 验证。

## 阻塞
- 删除 GitHub 仓库需 `delete_repo` scope，当前 token 无此权限；`gh auth refresh` 需访问 github.com 主站，主站 DNS 解析到不可达 IP（20.205.243.166），可用 IP 140.82.112.3。
- 出路：临时改 /etc/hosts 指向可用 IP 完成授权后还原；或网络恢复后重试。
*（内容由AI生成，仅供参考）*
