# 工坊积木库同步入口（方案 A + B）

> 状态：已拍板（用户 2026-08-17 确认"按你的建议"）
> 背景：积木解释（brick.json）记录在 GitHub `suipu-boop/shadeling-bricks`，
> 工坊实际读取本地缓存 `~/.brickery/vault/`。此前无同步入口，GitHub 改动
> 不会自动反映到工坊。

## 目标

1. 工坊界面提供"从 GitHub 同步"按钮 + 积木库版本/更新时间展示（方案 A）。
2. 组装/产出前自动检查更新（方案 B），失败静默降级本地缓存，不阻塞。

## 改动点

### 1. 前端 `web/index.html`

- **header**：在 steps 左侧加同步区：
  - 状态文本：`积木库 <commit> · <更新时间>`（来自 `/api/sync-status`）
  - 按钮「从 GitHub 同步」→ `syncNow()`
- **JS 新增**：
  - `loadSyncStatus()`：页面加载时调 `GET /api/sync-status`，渲染 commit/更新时间
  - `syncNow()`：调 `POST /api/sync`，按钮转 loading，完成后刷新状态 + `loadBricks()`
- **组装前自动同步**：`produce()` 开头先 `await syncNow(true)`（静默模式，失败忽略），
  保证产出用的是最新积木。

### 2. 后端 `brickery/web/server.py`

- `_api_produce` 开头尝试 `sync_all()`，`SyncError` 时静默降级（用本地缓存继续），
  不阻塞产出。与前端自动同步双保险。

## 不做

- 不做启动时自动同步（打开工坊不拉网络，避免卡顿）。
- 不改 sync.py 本身（clone/pull 逻辑已可用）。

## 验证

- 打开工坊：header 显示积木库 commit 与更新时间。
- 点「从 GitHub 同步」：按钮 loading → 完成后积木列表刷新。
- 组装产出：自动触发同步，网络失败时仍能产出（本地缓存兜底）。
