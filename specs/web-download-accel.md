# 网页下载加速方案

状态: 已实施完成（2026-08-21），本地验证通过，未提交推送
关联: site/index.html（网页版工坊）

## 背景

线上站点 https://suipu-boop.github.io/brickery/ 的下载源全在 GitHub：
- 市场数据 + .brick 积木包: raw.githubusercontent.com/suipu-boop/shadeling-bricks
- 工坊 App dmg: github.com/suipu-boop/brickery/releases/download/v0.1.0

国内直连 GitHub 这两个域名慢/不稳，用户实测下载很慢。

## 方案（A + D 组合，零成本）

### 1. 市场源与 .brick 下载：jsDelivr 直连 + 镜像兜底

- 首选 `https://cdn.jsdelivr.net/gh/suipu-boop/shadeling-bricks@main/skills/...`
  - jsDelivr 免费 CDN，有国内节点；只覆盖仓库文件（index.json、.brick），不支持 Release
  - 缓存 TTL 约 12h；对分支引用会回源校验，可接受
- 失败回退 `https://raw.githubusercontent.com/...`
- 再失败回退 GitHub 加速镜像（列表见下），逐个尝试

### 2. App dmg 下载：镜像加速 + 直连兜底

- 主按钮改为「加速下载」：href 指向第一个可用镜像 + Release URL
- 保留「直连 GitHub」备选按钮（原直链，国内慢但可用）
- 镜像列表（raw 与 release 均支持的前缀代理）:
  - https://gh-proxy.com/
  - https://ghfast.top/
  - https://ghproxy.net/
  - https://mirror.ghproxy.com/
- 页面加载时可用 fetch HEAD 探测镜像可达性（跨域受限则静默跳过，按钮仍保留首个）

### 3. 实现要点

- 新增常量: JSDELIVR_BASE / RAW_BASE / MIRRORS / RELEASE_DMG_URL
- 新增 `fetchFirst(urls)`：依次 fetch，首个成功即返回（超时 8s，失败切换）
- `.brick` 单块/批量下载与市场 index 拉取统一走 fetchFirst
- dmg 下载按钮替换为加速/直连双按钮；全部版本链接不变
- 不引入任何新依赖（保持零构建纯静态）

## 验收

- 网页版在浏览器打开后市场列表正常加载（走 jsDelivr）
- .brick 单块/批量打包下载可完成
- dmg 区块出现「加速下载」与「直连 GitHub」两个按钮，加速链接可访问
- 断网模拟下市场加载报错提示清晰

## 后续（本期不做）

- 若加速镜像仍不理想：dmg 上传腾讯云 COS / 七牛 CDN，一劳永逸
