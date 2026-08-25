# 发布流程规范（Release Process）

> 状态：v1（2026-08-24 确立）
> 适用范围：brickery 系列仓库（brickery / brickery-workbench / brickery-factory / brick-vault / brickery-meta）的所有对外发布动作。

## 背景与教训

历史上多次出现"代码已合并、发布物还是旧的"问题：

- PR #3 合并后重建了 App，但 dmg 只落在本地 `output/`，Release v0.1.0 资产仍是 8/22 旧包，站点下载入口长期指向旧版。
- 站点市场源 `shadeling-bricks` 与积木库 `brick-vault` 分离，库更新不会自动反映到市场页。

根因：**代码合并 ≠ 发布物更新**。合并与发布之间缺少强制闭环步骤。

## 硬性规则

**任何 PR 合并进 main 且涉及以下任一范围，必须在合并后执行完整发布流程，不得跳过：**

1. 内核代码（produce / skill_library / runtime 等）——影响 App 行为
2. 工坊 / 工厂前端（web/index.html 等）——影响 App 界面
3. 构建脚本（build_workbench_app.sh 等）——影响打包产物
4. 积木库（brick-vault bricks/）——影响市场数据与内置积木集合

## 发布流程（合并后必走步骤）

### 第 1 步：重建产物

```
BRICKERY_CORE_REPO=/Users/suipu/Dev/brickery \
BRICKERY_VAULT_REPO=/Users/suipu/Dev/brick-vault \
bash scripts/build_workbench_app.sh
```

### 第 2 步：核对新产物

- 记录新包大小、md5、构建时间，与 Release 现有资产对比，确认确实不同（旧包时间戳更早）。
- 关键点：**新包构建时间必须晚于合并的 PR commit 时间**，否则说明构建没吃到最新代码。

### 第 3 步：替换 Release 资产（必走）

同名资产无法直接覆盖，必须"先删后传"（tag 不变）：

```
scripts/upload_release_asset.sh suipu-boop/brickery-workbench v0.1.0 \
  BrickeryWorkbench-0.1.0.dmg output/BrickeryWorkbench-0.1.0.dmg
```

脚本内部逻辑：取 token（git credential fill）→ 查 release id → 删同名旧资产 → 上传新资产 → 打印新 asset id/size 供核对。

### 第 4 步：核对站点（gh-pages）

- 下载链接指向的 Release 资产已更新（`suipu-boop.github.io/brickery/` 部署约 1-2 分钟生效）。
- 市场数据源与积木库一致（当前市场源 = `suipu-boop/brick-vault`）。
- 市场只显示按需大积木/第三方；内置小积木（`binary_size` 空/0 且无 `binary_url`）不出现在市场。

### 第 5 步：记录

- 在本次发布的对话/文档中记录：新包 md5、上传后的 asset id、站点核对结果。
- 未完成发布流程前，不认为本次合并"已发布"。

## 与快速迭代通道的衔接

日常测试阶段允许「先改运行中副本、重启即测、再同步仓库」的快速通道（见 `specs/agent-test-feedback-loop.md` 第 5 步，已确立）。但该通道只解决「本机即时生效」，**不改变本规范的发布义务**：

- 快速通道的改动必须同时落到仓库（仓库为真源），凡落在硬性规则 4 类范围内的改动，最终合入 main 时仍须走完整发布流程。
- 未走 PR 合并的副本改动，视为临时验证状态，不算「已发布」；只有完成第 1-5 步后才算发布完成。
- 快速通道适合单问题快速验证；多改动集中确认后应尽快收敛为 PR 合并，避免副本与仓库长期漂移。

## 例外与边界

- 仅改动 specs/ 文档、README、发布说明等不进入产物的内容，可跳过 1-3 步，但需在 PR 描述中注明"无产物变更"。
- Release tag 被占用（历史 Release 遗留同名 tag）时：`git tag -f <tag> HEAD && git push --force origin <tag>` 强制更新，再用 `gh release edit <tag>` 同步说明。

## 待办沉淀

- [ ] 将此流程接入 CI（合并 main 后自动重建 + 自动替换资产 + 自动核对），当前为人工必走步骤。
