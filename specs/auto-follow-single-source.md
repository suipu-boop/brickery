---
AIGC:
    Label: "1"
    ContentProducer: 001191440300708461136T1XGW3
    ProduceID: 1ff3ab34626ddcd667748776b4e29487_a32fa280a2d011f1bc17525400826444
    ReservedCode1: YI+V3THLVmVGGN9m1FbfC6IO9De9FJanp4730gaMS/ij/CsV1zdDZNoKTpVWeq6a/a4OQN9TuRQkLCdV5ONOTHyBSGm2RS82UeIgMfVdugXx8A/GONNN1ZioztsIfiQOClAs63Ea+H0gomnwXrGIGX3CANPkRbn57SRfFf34TxfvldRJvaZfxv5Bag4=
    ContentPropagator: 001191440300708461136T1XGW3
    PropagateID: 1ff3ab34626ddcd667748776b4e29487_a32fa280a2d011f1bc17525400826444
    ReservedCode2: YI+V3THLVmVGGN9m1FbfC6IO9De9FJanp4730gaMS/ij/CsV1zdDZNoKTpVWeq6a/a4OQN9TuRQkLCdV5ONOTHyBSGm2RS82UeIgMfVdugXx8A/GONNN1ZioztsIfiQOClAs63Ea+H0gomnwXrGIGX3CANPkRbn57SRfFf34TxfvldRJvaZfxv5Bag4=
---

# 单一真源 + 自动跟随一致性机制（auto-follow-single-source）

> 状态：待审阅（V0.1，基于真实代码核对，未经用户拍板前不改代码、不 push、不建 PR）
> 适用范围：brickery 平台全家（内核 brickery / 积木库 brick-vault / 工坊 brickery-workbench / 工厂 brickery-factory / 生成 app shadelingmac0.0.1 / 工坊 app BrickeryWorkbench / 网页 brickery-meta + gh-pages）
> 相关调研输入：2026-08-28 现场取证（本机仓库、打包产物、CI workflow、进程状态），全部结论可复现

---

## 一、现状盘点（基于代码事实）

### 1.1 五个仓库角色与"真源"关系

| 角色 | 仓库 / 产物 | 远端 | 当前一致性状态 |
|---|---|---|---|
| 内核 | `/Users/suipu/Dev/brickery` | github.com/suipu-boop/brickery（main=efe6011） | 本地=远端 main，无分叉 |
| 积木库 | `/Users/suipu/Dev/brick-vault` | github.com/suipu-boop/shadeling-bricks（main=42f8365） | 本地=远端 main |
| 工坊 | `/Users/suipu/Dev/brickery-workbench` | github.com/suipu-boop/brickery-workbench | 本地=远端 main；gh-pages 分支 3f3e73c（仅 index.html） |
| 工厂 | `/Users/suipu/Dev/brickery-factory` | github.com/suipu-boop/brickery-factory | 本地=远端 main |
| 元仓库 | `/Users/suipu/Dev/brickery-meta` | github.com/suipu-boop/brickery-meta（仅 main，无 gh-pages） | 纯文档，无站点 |

### 1.2 生成 app 与工坊 app 均为"构建时静态快照"

- **生成 app** `/Applications/shadelingmac0.0.1.app/Contents/Resources/brickery-runtime/brickery/`：内嵌内核副本 + `builtin_skills/`（22 项，source=builtin）。
- **工坊 app** `/Applications/BrickeryWorkbench.app/Contents/Resources/brickery-runtime/brickery/`：内嵌 runtime 与 `workbench/temp/runtime-merge` 逐字节 MATCH（构建时合并产物，未入 git）。
- **根因**：两个 app 的 runtime 都是"构建/打包那一刻"的静态快照。GitHub main 后续更新不会自动进入已装 app——这就是历史核对中发现"工坊 app 内嵌内核与 brickery main 全 DIFF"的结构性原因（构建后未重打包，而非有人改过）。

### 1.3 vault 同步现状：无任何"落地同步"机制

- `~/.brickery/vault/` 为运行视图：`bricks/`（22 项）+ `index.json` + `specs/` + `vault.db`，**非 git 仓库**。
- **工坊"在线直连"是唯一雏形**：
  - 前端 `brickery-workbench/web/index.html:336` 注释明示"积木库在线直连 GitHub，无需本地同步"；`loadBricks()` → `/api/bricks`。
  - 后端 `brickery-workbench/brickery/web/server.py`：`DEFAULT_SKILL_REPO = https://raw.githubusercontent.com/suipu-boop/shadeling-bricks/main/skills/index.json`，`_api_bricks` 用 `SkillLibrary(DEFAULT_SKILL_REPO, vault_root, timeout=20)` 在线拉清单；`_download_bricks_to_desktop` 仅按需下载到桌面。
  - 即：**工坊是"在线拉取即用"，从不落盘 vault**。
- **内核无 vault 同步代码**：grep `sync|同步|pull` 命中的 `vault_store.py::sync_skills` / `ipc.py::_h_vault_sync_skills` 是个人文件柜（vault.db）技能同步，与积木库无关。内核没有任何"从 brick-vault 拉积木"的实现。
- **结论**：vault 真身无自动同步；历史记忆中的"从 GitHub 同步"按钮已被在线直连替代。

### 1.4 生成 app 启动序列（自检插入点候选）

Swift 壳 `brickery/app/Sources/BrickeryApp/main.swift`（生成 app 与工坊 app 共用同一壳源码，按 bundleIdentifier 区分 RunMode）：

```
ServiceManager.start()
├─ assistant 模式：ipc(18765) + setup_wizard(18766) + chat_ui(18767)
├─ workbench 模式：brickery.web.server(8765)
└─ factory 模式：factory.server(8767)
   每个服务先 portInUse 检查再 launch()
   launch() 用内嵌 python：Resources/python/bin/python3 -m brickery.runtime.<module>
```

`ipc.py`（IpcServer 初始化，`IpcServer.__init__` 约 217 行）skills 加载序列：

```
1. scheduler.start()
2. self.skills = SkillRegistry()
3. load_builtin_skills(self.skills, home)     ← 扫描 brickery-runtime/brickery/builtin_skills/*/skill.json（source=builtin）
4. self.skills.load(home / "skills.json")     ← 用户清单，同名覆盖 builtin
5. _sync_skill_tools()
```

- **skills 加载路径确认**：`builtin_skills` 目录扫描（随包分发）+ `BRICKERY_HOME/skills.json`（用户可管理，`SkillRegistry.save` 过滤 builtin）。
- **自检插入点（两个候选，见 §4.2）**：A. Swift 壳 `ServiceManager.start()` 拉服务之前（更早、可整包替换后重启服务）；B. `ipc.py` 中 `SkillRegistry()` 之后、`load_builtin_skills` 之前（更贴近 skills 数据源）。

### 1.5 工坊构建打包脚本（自更新器可复用）

`brickery-workbench/scripts/build_workbench_app.sh`：

```
0)  拉取内核：CORE_REPO=https://github.com/suipu-boop/brickery.git
    clone --depth 1 → temp/brickery-core；后续 pull --ff-only --depth 1（失败沿用缓存）
0.5) 拉取积木库：VAULT_REPO=https://github.com/suipu-boop/shadeling-bricks.git
    clone --depth 1 → temp/brick-vault（vendored 快照源，失败仅告警）
    合并：rsync CORE_DIR/brickery/ → temp/runtime-merge/brickery/
          再以工坊自身 brickery/web/ 覆盖 web 子包
1)  swift build -c release（app/ Swift 壳）
2)  组装 .app：Contents/Info.plist + MacOS/BrickeryApp
3)  打包 runtime：brickery-runtime/brickery/（含 web 子包）+ brickery-runtime/web/index.html + Resources/python/
4)  DMG：复用内核 brickery/dmg.py
```

- 可配置环境变量：`BRICKERY_CORE_REPO` / `BRICKERY_VAULT_REPO` / `BRICKERY_WORKBENCH_NAME` / `BRICKERY_WORKBENCH_VERSION` / `BRICKERY_WORKBENCH_PORT` / `BRICKERY_WORKBENCH_OUT`。
- **要点**：工坊 runtime 的来源就是"构建时从 GitHub 拉内核再合并"。自更新器可复用同一套拉取+合并逻辑，只是触发时机从"手动构建"变为"启动自检"。

### 1.6 网页与 CI 现状

| 仓库 | workflow | 现状 |
|---|---|---|
| brickery | `ci.yml`（import 冒烟）+ `notify-workbench.yml` | push `brickery/**` → repository_dispatch 通知 workbench 发布（需 RELEASE_PAT） |
| brick-vault | `ci.yml` | `python scripts/verify_bricks.py --strict` 验证闸门 |
| brickery-workbench | `release.yml` | push 相关路径 / workflow_dispatch / repository_dispatch 触发，macos-14 构建 + release；**未见 Pages 部署步骤** |
| brickery-meta | 无 | 纯 md（ARCHITECTURE / ROADMAP / SESSION-START / specs），无站点 |
| brickery-factory | `ci.yml` | 测试 |

- **workbench gh-pages（3f3e73c）**：仅 `index.html`——"Brickery 积木工坊 · 网页版 + 积木加工厂 App 下载链接"下载站，提交历史为手动推送（`c89932f site: 下载站迁移 GitHub Pages`）。
- **meta**：无 index.html、无 gh-pages。
- **结论**：网页 CI 有雏形（release.yml / notify-workbench.yml），但 **gh-pages 部署未纳入 CI，meta 站点形态未定义**。

### 1.7 已有可复用资产清单

1. `SkillLibrary.fetch_index`（内核 `runtime/skill_library.py`）：从 GitHub raw 拉 skills/index.json + 本地缓存，可作 vault 同步的拉取骨架。
2. `build_workbench_app.sh` 的 clone+pull+rsync 合并逻辑：自更新器可直接复用。
3. `brick-vault/scripts/verify_bricks.py --strict`：发布闸门，可挂入 CI 与 check_alignment.sh。
4. `brickery/notify-workbench.yml`：仓库联动范式（repository_dispatch），网页 CI 可仿此。
5. Swift 壳统一入口 + `portInUse` 守护：为"更新后重启服务"提供机制基础。

---

## 二、目标与背景

**问题**：当前生态存在五处静态快照（生成 app runtime、工坊 app runtime、vault 真身、workbench gh-pages、meta 站点），各自在"构建/推送那一刻"固定，与 GitHub main 脱节。任何一次修复（如 2026-08-28 的三处不一致）都需要人工逐副本对齐，且无法保证下次发布不再漂移。

**目标**：确立 **GitHub 为唯一真源（single source of truth）**，所有消费方（vault、生成 app、工坊 app、网页）自动跟随 main，消除"打包产物静态快照断链"：

1. `brickery` main → 生成 app / 工坊 app 的 runtime 与 builtin_skills；
2. `brick-vault` main → 本地 vault 真身（`~/.brickery/vault`）；
3. `brickery-workbench` / `brickery-meta` main → 网页（GitHub Pages 自动部署）。

**原则**：
- 自动跟随不等于静默覆盖：任何本地落盘更新均需用户授权（首次弹确认/设置开关），失败回滚、断网静默降级。
- 只同步"代码/积木清单"类资产，绝不触碰用户数据（config、skills.json 用户覆盖、vault.db、sessions、memory）。
- 多 app 共用同一套自检更新设计，实现放内核（brickery 包内），避免两份漂移。

---

## 三、总体架构

```
                    ┌─────────────────────────────────────────────┐
                    │              GitHub（唯一真源）              │
                    │  brickery main ─ brick-vault main ─ 网页 repos│
                    └───────┬──────────────────┬──────────────────┘
                            │ git clone/pull   │ raw.githubusercontent
              ┌─────────────▼──────────┐  ┌────▼─────────────────────┐
              │  vault 真身             │  │  工坊 web（在线直连）      │
              │  ~/.brickery/vault      │  │  SkillLibrary.fetch_index │
              │  （自动同步，§4.1）      │  │  （已有雏形，不落盘）      │
              └─────────────▲──────────┘  └──────────────────────────┘
                            │
   ┌────────────────────────┼───────────────────────────────┐
   ▼                        ▼                               ▼
┌───────────────┐   ┌──────────────────┐   ┌──────────────────────┐
│ 生成 app        │   │ 工坊 app          │   │ 网页                   │
│ shadelingmac   │   │ BrickeryWorkbench │   │ workbench gh-pages    │
│ 启动自检更新    │   │ 启动自检更新       │   │ + meta 站点            │
│ （§4.2 共用）   │   │ （§4.2 共用）      │   │ CI 自动部署（§4.3）     │
└───────────────┘   └──────────────────┘   └──────────────────────┘
                            │
                            ▼
                ┌───────────────────────┐
                │ check_alignment.sh     │  四层核对（§4.4）
                │ 仓库 / 副本 / 远端 / 进程│
                └───────────────────────┘
```

分层表：

| 层 | 实体 | 跟随源 | 机制 | 触发 |
|---|---|---|---|---|
| L0 真源 | GitHub（brickery / brick-vault / workbench / meta main） | — | PR 合入 | 开发者 |
| L1 源码 | 本地 4 仓库 + brick-vault | 各自远端 main | git pull --ff-only | 手动/check_alignment 提示 |
| L2 vault | ~/.brickery/vault | brick-vault main | §4.1 自动同步 | app 启动 / 手动 / 定时 |
| L3 运行 | 生成 app runtime、工坊 app runtime | brickery main + workbench web | §4.2 启动自检更新 | app 启动 |
| L4 网页 | workbench gh-pages、meta 站点 | workbench / meta main | §4.3 CI 部署 | push main |

---

## 四、分机制设计

### 4.1 vault 自动同步

**目标**：`~/.brickery/vault`（非 git 运行视图）自动跟随 `brick-vault` main。

**触发时机**（三级，互为兜底）：
1. **生成 app 启动时**（Swift 壳拉起 ipc 前或 IpcServer 初始化早期，见 §4.2 插入点 A/B 的前置步骤）；
2. **手动**：设置面板"同步积木库"按钮（复用工坊历史"从 GitHub 同步"语义，现为在线直连，可新增落地按钮）；
3. **定时**（可选）：后台每 24h 一次，静默检查不落地。

**拉取源**：`https://github.com/suipu-boop/shadeling-bricks.git`（main），走 shallow clone（`--depth 1`）到 `~/.brickery/cache/brick-vault/`，失败沿用缓存（复用 `build_workbench_app.sh` 的 `pull --ff-only --depth 1` 逻辑）。

**同步策略**（增量补齐，不删本地）：
1. 以 `brick-vault/index.json` 为登记清单（schema `brick-registry/v1`）；
2. 逐条比对 `~/.brickery/vault/bricks/<name>/` 与远端 `bricks/<name>/`：
   - 缺失 → 增量补齐；
   - 存在但版本旧 → 覆盖前先备份旧版到 `~/.brickery/vault/.backup/<ts>/`；
   - 本地存在而远端没有 → **跳过**（本地未入库文件不动，防用户自制积木被清）；
3. 字段迁移兼容：沿 `brick.json → skill.json` 迁移规则（历史已实现 handler→action / params→args），同步时若远端仍为旧字段形态，按 `skill_contract.py` 规范化后落地；`index.json` 同步后重建本地索引；
4. **失败静默降级**：网络失败 / clone 失败 → 保留现状、仅记日志，不弹错误打断启动。

**与已有"在线直连"的衔接**：工坊 web 保持在线直连（L4 消费方无本地依赖诉求）；vault 同步解决的是"生成 app 运行时 + 本地积木市场"的数据源问题。二者共用 `DEFAULT_SKILL_REPO` 常量定义，URL 统一收敛到内核 `paths.py`（或新 `sync.py`）常量。

**落盘位置**：vault 同步器作为内核模块 `brickery/runtime/vault_sync.py`（新文件），入口函数 `sync_vault(home, force=False) -> SyncReport`；IpcServer 初始化时以低优先级线程调用（不阻塞启动）。

### 4.2 App 启动自检更新（生成 app 与工坊 app 共用同一设计）

**检查点**：Swift 壳 `ServiceManager.start()` 在拉起任何服务**之前**（插入点 A，首选）执行更新检查；若在 ipc.py 内实现（插入点 B），则在 `self.skills = SkillRegistry()` 之后、`load_builtin_skills` 之前。

**版本标识**：
- 远端：GitHub API `repos/suipu-boop/brickery/commits/main`（或 raw `version.json`）取最新 commit SHA；工坊额外对照 `repos/suipu-boop/brickery-workbench/commits/main`（web 后端覆盖部分）。
- 本地：打包时写入 `brickery-runtime/brickery/version.json`（字段：`core_commit`、`builtin_skills_commit`、`workbench_commit`、`built_at`）。`build_workbench_app.sh` 与 `produce.py::_bundle_runtime` 打包步骤末尾追加生成该文件。

**拉取范围**（白名单，仅这些路径可被替换）：
- `brickery/` 包内 Python 代码（runtime/、web/、顶层模块）；
- `brickery/builtin_skills/`（随底座分发的 builtin 积木清单）；
- 工坊 app 额外：`brickery/web/`（工坊后端覆盖）+ `web/index.html`（前端）。
- **排除**：`Resources/python/`（内嵌解释器，不自动升级）、`Resources/bricks/`（用户组装产物）、任何 `~/.brickery/` 用户数据。

**落盘策略（原子替换 + 备份 + 回滚）**：
1. 拉取到 `Resources/brickery-runtime/.update/<sha>/`（新目录完整构建合并）；
2. 校验：目录结构完整、`brickery/__init__.py` 存在、`version.json` 可解析、sha 与预期一致；
3. 原子替换：旧 `brickery/` 整目录改名为 `.backup/<ts>/`，新目录移入为 `brickery/`；
4. 失败回滚：任一步失败 → 恢复 `.backup/<ts>/` 并删除 `.update/`，本次启动加载旧版。

**生效策略**：更新落地后**本次启动仍加载旧版**（保持启动原子性），写 `pending_restart` 标记；UI 提示"发现新版本，已下载，重启后生效"并给出"立即重启 / 稍后"选项。Swift 壳已具备 `portInUse` + `launch()`，可对自身服务做"优雅重启"（kill 旧服务进程 → 重新 launch）。

**安全边界**：
- 仅允许从 URL 白名单拉取：`github.com/suipu-boop/brickery`、`github.com/suipu-boop/brickery-workbench`、`github.com/suipu-boop/shadeling-bricks`（域名 + 仓库双重校验，禁止跳转第三方 CDN）；
- 落盘路径严格限定在 `.app/Contents/Resources/brickery-runtime/` 内，禁止 `../` 越界（沿 core 路径遍历防护）；
- **不覆盖用户配置**：`~/.brickery/config.json`、`skills.json`、`vault.db`、`sessions.db`、`memory*` 一律只读不动；
- **更新需授权**：默认"启动自检发现更新 → 提示用户确认后才下载落盘"（可设"自动下载、重启前确认"）；绝不静默覆盖运行中代码。

**多 app 共用**：检查/拉取/落盘/回滚逻辑放内核 `brickery/runtime/self_update.py`（新文件）；生成 app 与工坊 app 仅传不同参数（`core_repo`、`extra_paths`：工坊追加 web 覆盖）；Swift 壳只做"启动前调用 + 结果回显"，避免双份实现。

### 4.3 网页 CI（GitHub Actions）

**A. workbench gh-pages 纳入 CI**：在 `brickery-workbench/.github/workflows/release.yml` 中追加 Pages 部署 job（或新建 `pages.yml`）：

```
on:
  push:
    branches: [main]
    paths: ['web/**', 'site/**', '.github/workflows/pages.yml']
workflow_dispatch:
permissions: { contents: read, pages: write, id-token: write }
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - checkout
      - 将 web/index.html 及其静态资源复制到部署目录（保留下载链接）
      - actions/upload-pages-artifact
      - actions/deploy-pages
```

**B. meta 站点定义**：brickery-meta 当前纯 md。方案（二选一，文档先定方向）：
- 方案一（轻量）：meta 增加 `index.html` 静态导航页（ARCHITECTURE / ROADMAP / SESSION-START / specs 索引），启用 gh-pages；
- 方案二（文档站）：引入 MkDocs/MDBook 构建 specs 站点，CI push main → build → deploy Pages。
- 本期推荐**方案一**（零构建依赖，与现有纯 md 仓库形态一致）。

**C. 发布联动（沿用已有范式）**：`brickery/notify-workbench.yml` 已示范 `repository_dispatch`。新增 `brick-vault` push main → dispatch workbench（若需）；网页部署独立于 App 发布，无需联动。

**D. 验收**：push main 后 Pages 自动更新；workflow 失败发 PR 检查失败，不阻断主分支合入（仅部署步骤失败回滚上一版本站点）。

### 4.4 一致性校验脚本 check_alignment.sh

**目标**：把"四层核对"（2026-08-28 人工核对方法论）固化为可重复执行工具，纳入发布闭环。

**四层核对**：
1. **仓库层**：对 4 仓库 + brick-vault 逐个 `git fetch origin` 并比对本地 main 与 `origin/main` HEAD（`git rev-parse`），报告"同步 / 落后 / 分叉"；
2. **运行副本层**：逐文件 `shasum` 比对：
   - 生成 app runtime vs 本地 brickery main（`brickery/` 包 + `builtin_skills/`）；
   - 工坊 app runtime vs `workbench/temp/runtime-merge`（含 web 覆盖）；
   - `~/.brickery/vault/bricks/` vs `brick-vault/bricks/`（22 项 + index.json）；
3. **远端比对**：本地 main vs `origin/main` 的 `git ls-remote` SHA（防"本地已同步但未 push"）；
4. **进程与端口**：`lsof -iTCP:18765/18766/18767/8765/8767 -sTCP:LISTEN` 检查各 app 服务是否在线，输出 PID。

**输出**：结构化文本报告（每层 PASS/FAIL + 明细 + 差异文件列表），退出码：0=全 PASS，1=有差异（可用于 CI gate）。

**放置**：`/Users/suipu/Dev/brickery/scripts/check_alignment.sh`（内核仓库，随发布闭环执行）；brickery 的 `ci.yml` 增加"仓库自检"步骤（在 CI 侧跑仓库层 + 远端层），本地发布前跑全四层。

**纳入发布闭环**：发布检查单（见 specs/release-process.md 增补）——"发布前必须 check_alignment.sh 全 PASS"。

---

## 五、实施分期

### Phase 1 — vault 自动同步
- 改动文件：
  - 新增 `brickery/brickery/runtime/vault_sync.py`（同步器核心）；
  - `brickery/brickery/runtime/ipc.py`（初始化早期低优先级触发 + 手动 IPC handler `_h_vault_sync_now`）；
  - `brickery/brickery/runtime/paths.py`（收敛 `DEFAULT_VAULT_REPO` / `DEFAULT_SKILL_REPO` 常量）；
  - 工坊 `web/index.html` + `brickery/web/server.py`（可选：新增"同步到本地 vault"按钮，复用在线直连数据）。
- 影响面：生成 app 重启生效；vault 真身首次补齐增量；无用户数据改动。
- 验收：删除 vault 某积木 → 重启生成 app → 自动补齐；断网启动不报错。

### Phase 2 — App 启动自检更新
- 改动文件：
  - 新增 `brickery/brickery/runtime/self_update.py`（检查/拉取/落盘/回滚）；
  - `brickery/app/Sources/BrickeryApp/main.swift`（启动前调用 + pending_restart 提示 + 服务优雅重启）；
  - 打包侧：`brickery/brickery/produce.py::_bundle_runtime` 与 `brickery-workbench/scripts/build_workbench_app.sh` 末尾写入 `version.json`；
  - 工坊 app 侧：构建脚本增量（web 覆盖路径传入 self_update 白名单）。
- 影响面：生成 app + 工坊 app 双端；下次发布后旧安装包可自愈。
- 验收：改内核 main → 启动 app → 提示有更新 → 确认后下载 → 重启生效；人为破坏 runtime → 回滚成功。

### Phase 3 — 网页 CI
- 改动文件：
  - `brickery-workbench/.github/workflows/pages.yml`（gh-pages 自动部署）；
  - `brickery-meta/index.html` + `.github/workflows/pages.yml`（方案一：静态导航页 + Pages）；
  - `brick-vault/.github/workflows/ci.yml`（如需要：push main → dispatch）。
- 影响面：网页自动更新；无本地改动。
- 验收：push 任一网页仓库 main → Pages 站点数分钟内更新。

### Phase 4 — check_alignment.sh 固化
- 改动文件：
  - 新增 `brickery/scripts/check_alignment.sh`；
  - `brickery/.github/workflows/ci.yml`（仓库层 + 远端层自检步骤）；
  - `brickery/specs/release-process.md`（发布检查单增补）。
- 影响面：发布流程强制核对，防漂移回归。
- 验收：故意制造一处副本差异 → 脚本 FAIL 并列出文件；修复后 PASS。

---

## 六、风险与边界

| 风险 | 缓解 |
|---|---|
| 自更新失败导致 app 不可启动 | 原子替换 + `.backup/<ts>/` + 失败回滚；版本文件校验不过即放弃本次 |
| 断网 / GitHub 不可达 | 静默降级：保留现状，仅记日志；vault 沿用本地缓存 |
| 覆盖用户自制积木 / 本地数据 | 同步只做增量补齐，本地有远端无的条目跳过；用户配置目录只读 |
| 静默自动更新引发反感 / 行为突变 | 更新需授权（默认确认后下载），不静默覆盖；UI 明示"已下载、重启生效" |
| 双 app 各自实现导致再次漂移 | 更新逻辑统一放内核 `self_update.py`，两 app 只传参数 |
| 工坊 web 覆盖与内核升级冲突 | 工坊 self_update 的 extra_paths 白名单固定为 `brickery/web/` + `web/index.html`，与构建脚本同一来源 |
| 网页 CI 部署失败 | Pages 部署失败回滚上一版本站点；部署步骤不阻断主分支 |
| 恶意仓库/URL 注入 | 域名 + 仓库双白名单，仅允许 suipu-boop/* 三个仓库；路径严格限定 runtime 目录内 |

**边界声明**：本机制只解决"代码/积木清单/网页"三类资产的自动跟随；用户数据（会话、记忆、文件柜 vault.db、配置）永远不在同步范围；二进制产物（binary_url 指向的外部二进制）仅同步清单，不自动下载实体（延续 high-config-doc 现状）。

---

## 七、落地记录

> 待实现后按阶段填写：每期记录日期、改动 commit、验证结果（check_alignment.sh 输出摘要）、回滚事件。
*（内容由AI生成，仅供参考）*
