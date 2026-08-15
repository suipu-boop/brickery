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
