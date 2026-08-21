# 工坊直连 GitHub 积木库改造（Live Market）

> 状态：已实施（2026-08-21 重新打包并替换 Release v0.1.0，网页下载即新版）
> 日期：2026-08-21
> 背景：下载版工坊（BrickeryWorkbench v0.1.0）首次启动依赖本地缓存 ~/.brickery/vault，
> 缓存缺失时报「积木库加载失败」，且 Release 打包落后于代码，体验断裂。

## 一、目标

工坊 App 改为**在线直连 GitHub 积木库**，取消「本地缓存 + 同步按钮」模型：

1. 打开工坊即显示 GitHub 上的最新积木库，无需任何本地缓存或同步操作；
2. 组装/产出 agent 时，按需从 GitHub 拉取选中积木到本地缓存再组装；
3. 不再依赖 `~/.brickery/vault` 预存在，缓存仅作运行时工作区，可随时重建；
4. 移除前端「从 GitHub 同步」按钮及同步状态展示。

## 二、现状与问题

- server.py：`DEFAULT_VAULT = ~/.brickery/vault`；`/api/bricks` 走 `load_vault(vault_root)` 读本地缓存；
  首次启动缓存不存在 → 报「清单不存在：.../vault/index.json」。
- `/api/sync` + `/api/sync-status`：首次 clone、之后 pull，需手动点按钮。
- 网页版（site/）已证明：GitHub raw 源 CORS 开放（`access-control-allow-origin: *`），
  前端直连 `raw.githubusercontent.com/suipu-boop/shadeling-bricks/main/skills/index.json` 可行。
- server.py 已有按需下载能力：`SkillLibrary(DEFAULT_SKILL_REPO, Path(vault_root))`、
  `_download_bricks_to_desktop()`——组装前拉取选中积木即可。

## 三、改造方案

### 3.1 积木库清单直连 GitHub（在线优先）

`/api/bricks` 改为：

1. 优先请求 `https://raw.githubusercontent.com/suipu-boop/shadeling-bricks/main/skills/index.json`
   （带超时，如 10s）；
2. 失败时依次尝试镜像（gh-proxy.com 等，与网页版 mirror-select 同思路），全部失败返回明确错误
   「无法连接积木库源，请检查网络」；
3. 不再读取本地 `~/.brickery/vault` 作为清单来源。

前端展示逻辑不变（分类、搜索、列表渲染复用），仅改数据来源与加载/错误文案。

### 3.2 组装/产出按需拉取

- 用户选中积木 → 组装/产出前，用 `SkillLibrary` 从 GitHub 按需下载所选积木
  （bricks/<name>/ 目录 + sha256 校验）到本地运行时缓存 `~/.brickery/vault`；
- 缓存仅作工作区：本次会话/本次产出拉取，缺了再拉，不承担「预置清单」职责；
- 保留 sha256 校验链路，防第三方镜像篡改。

### 3.3 移除同步概念

- 前端删除「从 GitHub 同步」按钮与积木库状态展示（sync-btn / sync-status / loadSyncStatus / syncNow）；
- 后端 `/api/sync`、`/api/sync-status` 接口移除或保留为内部调试（默认移除）；
- server 启动不再检查/初始化 vault，不再因 vault 缺失报错。

### 3.4 失败与体验

- 清单加载失败：页面显示错误 + 「重试」按钮，不出现「清单不存在」裸错；
- 组装中网络失败：明确提示失败积木与原因，可重试；
- GitHub 国内访问：清单与积木下载均走「直连 → 镜像」兜底策略（复用已有镜像列表）。

## 四、不改动

- 网页版 site/（已是直连模型，不动）；
- 积木格式与 .brick 打包链路（离线安装全链路保持）；
- 底座 runtime 拉取逻辑（产出 agent 时仍需 base，保持现状）。

## 五、实施顺序

1. server.py：`/api/bricks` 改在线直连 + 镜像兜底；移除 sync 接口依赖；
2. 组装链路：按需拉取选中积木（复用 SkillLibrary）；
3. web/index.html：删同步按钮/状态，改加载与错误文案；
4. 语法自测（py_compile + node --check 提取 script）；
5. 重新打包 dmg（build_workbench_app.sh）→ 本地验证首次启动无缓存可显示积木库；
6. 上传 Release 替换旧版，网页下载即得新版。

## 六、验收

- 全新机器（无 ~/.brickery）安装 App，打开即显示积木库（在线）；
- 选中积木 → 产出 agent 包成功，产物含所选积木；
- 断网时清单加载给出明确错误 + 重试按钮；
- 同步按钮消失，无「清单不存在」报错。

## 七、待拍板

- 镜像兜底：沿用网页版镜像列表（gh-proxy.com 等）即可？还是清单只走 GitHub 直连、失败即报错？
  （建议：沿用镜像列表，下载体验与网页版一致）
