---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_88c551b39f8d11f1a65b525400826444
    ReservedCode1: iNL0E6MFR2dC/YPZj478t1TBCHqiEs68PaZtPjKj5Rl5bB+DGr2pCUXLidygqi9OImePr+aLHFovDSnhvudxsCLXD//uf51oxZP7PsAPIO9O+8Zu6UoUinseXfbsFRw6Mwgabq/Hl+d5ZLyx2jawMZgZDBcmuykr/plyU0u/obGq4vRJoxzvFpJta7g=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_88c551b39f8d11f1a65b525400826444
    ReservedCode2: iNL0E6MFR2dC/YPZj478t1TBCHqiEs68PaZtPjKj5Rl5bB+DGr2pCUXLidygqi9OImePr+aLHFovDSnhvudxsCLXD//uf51oxZP7PsAPIO9O+8Zu6UoUinseXfbsFRw6Mwgabq/Hl+d5ZLyx2jawMZgZDBcmuykr/plyU0u/obGq4vRJoxzvFpJta7g=
---

# 小积木内置进底座：全链路对齐方案

状态: 待审阅（内核改动走 PR + GitHub 确认，不再直推 main）
日期: 2026-08-24

## 背景与目标

小积木（无二进制、几 KB）直接内置进生成 agent 底座，开箱即用，用户组装零选择；大引擎/第三方积木留待选区/市场按需下载。

**最终体验口径（已与用户确认）**：
- 工坊左侧积木库（待选区）：只显示非内置积木（带二进制/第三方），小积木完全不显示。
- 生成 agent 积木市场：只显示按需下载的大积木/第三方，小积木不出现。
- 生成 agent 技能库：显示运行时可用的全部技能（含内置小积木），可开关、可手动触发。
- 分工：市场管「装不装」，技能库管「用不用」；内置积木不经过安装环节，但作为已就绪能力出现在技能库。

## 现状盘点（各层对 builtin 的认知）

| 层 | builtin 概念 | 现状 |
|---|---|---|
| runtime（生成 agent 底座） | 有，完整 | `load_builtin_skills` 从 `builtin_skills/` 目录加载，source 强制 `builtin`、不写用户 skills.json、可被用户安装的同名技能覆盖、辅助产物装 `~/.brickery/bin/<技能名>/` |
| 工坊 UI | 有，前端标记 | `binary_size` 空/0 → builtin → 过滤待选区/自动进组装区/内置角标/禁移除。已合 main，未构建进 App |
| 内核 produce | 无 | `_bundle_runtime` 不打包 builtin_skills；选中积木一律走 bricks/ 快照（用户级） |
| 生成 agent 市场 | 无 | `skill_library_list` 不区分 builtin，小积木以「未装」出现在市场 |

**核心断裂点**：runtime 已设计好「内置技能」机制（source=builtin、随包分发、只读），但 produce 从不把任何积木送进该通道；工坊 builtin 判定（binary_size）与 runtime builtin 通道（builtin_skills 目录）是两套互不相干的逻辑。

## 改动方案

### 1. 判定标准（沿用，统一为唯一口径）

- `brick.json` 未声明二进制（`binary_size` 空/0）→ 内置积木（builtin=True）。
- 声明二进制（>0，如 high-config-doc 的 editor_sdk）→ 非内置，留待选区/市场按需下载。
- `_partial`（详情拉取失败）强制非内置，防误判。
- 动态计算，不写死 id。

### 2. 内核 `produce.py`：内置积木走 builtin 通道

- 组装计划中标记为 builtin 的积木：不再写入 `bricks/` 快照（用户级），改为生成 `builtin_skills/<name>/skill.json`（转换 brick.json → runtime skill.json 格式，保留 content/依赖/元数据）。
- `_bundle_runtime` 打包时携带 `builtin_skills/` 目录进产物底座（`brickery-runtime/brickery/builtin_skills/`），与 `load_builtin_skills` 的打包态查找路径一致。
- 非内置积木仍走 bricks/ 快照，行为不变。
- 内置积木的辅助产物（脚本/二进制等）同样放入 `builtin_skills/<name>/`，由 `load_builtin_skills` 安装到 `~/.brickery/bin/`。

### 3. 生成 agent 市场：过滤内置

- `skill_library.list_entries`：条目若与已注册的 builtin 技能同名（`skills_registry` 中 `source=="builtin"`），从市场列表过滤，不显示「未装」。
- 市场 `已装 N / 共 M` 计数同步基于过滤后列表。

### 4. 技能库：不改（现状即符合）

- 内置技能 source=builtin 注册进 registry，技能库面板天然列出，可开关/可触发。仅确认回归，无代码改动。

### 5. 工坊 App：重新构建

- 工坊 UI 分流（PR #2 已合 main）需重新构建 `BrickeryWorkbench.app` 才生效。
- 构建流程（`build_workbench_app.sh`）确认携带最新 web 前端。

## 验证方案

1. 产出 agent 包内存在 `brickery-runtime/brickery/builtin_skills/`，含全部内置小积木；`bricks/` 快照不含内置积木。
2. 安装生成 agent 后，技能库列出内置小积木，可开关、可触发；用户 `~/.brickery/skills.json` 不含内置积木条目（不写用户文件）。
3. 生成 agent 积木市场：小积木不显示；大积木（high-config-doc）正常显示/可安装。
4. 工坊 App：待选区只显示非内置积木；组装区内置积木默认选中、带「内置」角标、无移除按钮。
5. 用户安装同名积木可覆盖内置（沿用 runtime 既有语义）。

## 风险与红线

- 不改变「空白安装包」原则：引擎二进制仍按需下载，不进包。
- 内置积木只读、不写用户文件；disable 状态暂不跨重启持久化（沿用第一版简化）。
- 内核改动走 PR，合入 main 后重新构建工坊/生成 agent 才生效。
*（内容由AI生成，仅供参考）*
