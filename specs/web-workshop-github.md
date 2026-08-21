# 网页版积木工坊（GitHub Pages 化）

> 状态：方案已定稿，实施中
> 日期：2026-08-21
> 决策背景：工坊 = 网页版市场/下载 + 本地 app 组装/产出，两者并存。市场数据走 GitHub（与市场源 GitHub Only 一致）。先自己用，以后有外部用户再考虑服务器。

## 1. 目标

- 网页版（GitHub Pages 静态站）即开即用：积木市场浏览、下载 .brick 积木包、下载积木工坊 app 安装包、离线导入引导
- 本地积木工坊 app（BrickeryWorkbench.app）**保留**：负责组装校验与产出 agent 安装包（GitHub 网页做不了这个）
- 与既有「市场源 GitHub Only」架构一致，数据单一来源 = GitHub

## 2. 现状梳理（当前本地 8765 服务能力）

| 接口 | 功能 | 网页版能否保留 |
|------|------|---------------|
| GET /api/bricks | 积木清单（本地 vault 缓存） | 可改：前端直连 GitHub 市场源 |
| POST /api/brick-download | 拉市场源 → Python 打包 .brick 存桌面 | 可改：浏览器端 JSZip 打包 |
| POST /api/assemble | 组装校验 → 方案（拓扑/资源） | 静态无法跑 Python 校验 |
| POST /api/produce | 产出 agent 安装包（.app/.dmg） | 静态无法跑，需保留本地侧 |
| POST /api/sync | 从 GitHub 同步底座/积木到本地缓存 | 网页版不需要（数据直连 GitHub） |
| POST /api/dmg | 产出 DMG 安装包 | 同 produce，需保留本地侧 |

## 3. 关键约束（纯静态边界）

GitHub Pages 只能托管静态文件，**无法执行 Python 后端**。因此：

- ✅ 可行：市场浏览、详情、分类、搜索、`.brick` 下载（浏览器端打包）、离线导入使用说明
- ❌ 不可行：在线组装校验、在线产出 agent 安装包（需要 Python 运行时 + 引擎下载）

## 4. 待拍板决策

### 4.1 网页版能力边界（已拍板：方案甲 + app 下载）

- 网页版 = 积木市场浏览 + 下载 .brick + 下载积木工坊 app 安装包 + 离线导入引导
- 组装/产出 agent 包 = 本地 app（BrickeryWorkbench.app 保留）
- ~~方案乙~~：组装方案在线预览，不做（网页做不了真产出，预览意义有限）

### 4.2 产出 agent 包的本地链路（已拍板：保留 app）

- **本地积木工坊 app 保留**，负责组装校验与产出 agent 安装包（GitHub 网页不能组装 app 安装包，用户确认）
- 网页提供 app 安装包（.dmg）下载入口，来源 = GitHub Release

### 4.3 .brick 前端打包方式

- **方案 A（已实施）**：浏览器端自研极简 zip 生成器（stored/UTF-8/零目录条目），与 package.py 格式逐字段对齐（manifest.json + skill/<name>/brick.json + sha256），无 JSZip CDN、无后端、无延迟
- 方案 B：GitHub Actions 定时预打包 .brick 发到 Releases，网页给下载链接（更新有延迟，多一套 CI）
- 方案 C：同时允许直接下载单块 skills JSON（导入侧扩展支持单 JSON 拖入）

## 5. 实施记录（已完成）

1. 网页版前端：`site/index.html` 单页静态站（市场浏览/分类/搜索 + 单块/批量下载 + App 下载 + 离线导入引导）
2. 数据直连：前端 fetch `raw.githubusercontent.com/suipu-boop/shadeling-bricks/main/skills/index.json` 及各积木 JSON（CORS 已确认开放）
3. 浏览器端打包：自研 zip 生成器（`crc32` + local header + central directory + UTF-8 flag），`crypto.subtle` 计算 sha256，本地 `inspect/unpack` 端到端验证通过
4. 离线导入引导页：三步引导已内置
5. GitHub Pages 发布：`git subtree split --prefix site -b gh-pages` + force push，站点 = `https://suipu-boop.github.io/brickery/`
6. 本地链路保留：积木工坊 App（BrickeryWorkbench.app）保留，负责组装/产出；网页含 App dmg 下载入口（brickery Release v0.1.0）
7. 端到端验证：线上站点浏览器实测通过（市场渲染、单块/批量下载、toast 无错误）

## 6. 验收标准

- [x] 网页打开即可浏览积木市场（线上已实测 6 块积木渲染）
- [x] 网页点下载得 `.brick`，格式与 package.py 产物一致（本地 inspect/unpack 通过）
- [x] 本地 App 保留，可产出 agent 安装包
- [x] GitHub Pages 域名可访问（https://suipu-boop.github.io/brickery/ 200）

## 7. 风险

- GitHub 国内访问不稳定（用户已知，先自己用可接受）
- 浏览器端打包需与 Python 端格式严格一致，否则离线导入失败 → 已用本地 inspect/unpack 端到端验证
- crypto.subtle 需 HTTPS/localhost 环境（GitHub Pages 为 HTTPS，可用）
