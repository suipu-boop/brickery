# 内核多镜像 fallback + 积木源 vendored 离线快照

状态: 待审阅（本轮改动走 PR + GitHub 确认，不再直推 main）
日期: 2026-08-24

## 背景

1. **直连 GitHub 大文件不稳定**：实测 50MB 直连 109s 断连；gh-proxy.com 镜像 12.7MB/s 稳定。
2. **单镜像无兜底**：当前 `_mirror_url()` 只写死 gh-proxy.com，该镜像挂掉则整个市场不可用。
3. **离线可用性**：积木源元数据仅 28KB（7 个 JSON），适合打进安装包；引擎二进制（editor_sdk 193MB）保持按需下载，不进包。

## 改动方案

### 1. 内核 `runtime/skill_library.py`：多镜像 fallback

- 新增镜像前缀表（按优先级）：
  ```python
  MIRROR_PREFIXES = ("https://gh-proxy.com/", "https://ghfast.top/")
  ```
- `_mirror_url(url)` 保留为兼容薄封装，返回首选候选；新增：
  - `_mirror_candidates(url) -> List[str]`：已带任一镜像前缀 → 原样单候选（防镜像套镜像）；`github.com` / `raw.githubusercontent.com` → `[镜像1+url, 镜像2+url, 原url]`；其它（含 file://）→ 原样单候选。
  - `_http_get_mirrored(url, timeout)`：按候选顺序逐个尝试，全部失败返回最后一个错误。
- 改动点：
  - `_download_skill`：`_http_get(dl_url)` → `_http_get_mirrored(dl_url)`（列表目录本身来自镜像源时 dl 已带前缀，行为不变；目录来自直连时自动获得镜像兜底）。
  - `_download_binary`：去掉手动 `_mirror_url()`，改 `_http_get_mirrored(binary_url, timeout=600)`；文件名解析改回原始 `skill.binary_url`。

### 2. 内核 `runtime/ipc.py`：市场源优先 vendored 离线快照

- 新增 `_vendored_repo_url()`：解析 `<app>/Contents/Resources/brickery-runtime/vendored/skills/index.json`（`Path(__file__).parent.parent.parent / "vendored" / "skills" / "index.json"`），存在则返回 `file://` URI，否则 None。
- `_resolve_skill_repo_url()`：vendored 存在 → 返回它（离线可用）；否则回退 `DEFAULT_PUBLIC_SKILL_REPO_URL`（在线镜像源）。开发环境无快照，行为不变。
- `list_entries` 已支持相对 `download_url` 的 `urljoin(base, dl)` 拼接，`_http_get` 已支持 `file://`，无需改动 index.json 内容。

### 3. 工坊 `brickery-workbench/scripts/build_workbench_app.sh`：构建期打包积木源快照

- 新增可配置项：`BRICKERY_VAULT_REPO`（默认 `https://github.com/suipu-boop/brick-vault.git`）、`VAULT_DIR`（`<repo>/temp/brick-vault`）。
- 构建流程中拉取 vault（clone/pull，失败仅告警不中断），步骤 3 打包运行时处把 `skills/` 拷贝到 `$RUNTIME_DIR/vendored/skills/`（约 28KB，相对 68MB DMG 可忽略）。
- 引擎二进制不进包，仍按需下载（走多镜像）。

## 验证方案

1. 单元级：`_mirror_candidates` 对三种输入（已带前缀 / github 原始 / 其它）返回正确。
2. 断链验证：临时将 MIRROR_PREFIXES 首位换成无效地址，安装 high-config-doc 应自动 fallback 到 ghfast.top 成功。
3. 离线验证：构建产物中手工放置 vendored 快照，`_resolve_skill_repo_url` 应返回 file://；删除后回退在线源。
4. 回归：正常在线安装 1 块积木，确认 provenance / 二进制落盘不受影响。

## 风险与红线

- 不引入任何第三方依赖（仅标准库 urllib）。
- 不动 index.json 内容；不改变「空白安装包」原则（二进制仍按需下载）。
- 内核改动走 PR，合入 main 后工坊构建拉取才生效。
