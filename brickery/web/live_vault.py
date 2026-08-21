"""在线积木库客户端（Live Market）：工坊直连 GitHub，取消本地预置缓存依赖。

设计（specs/workbench-live-market.md）：
- 展示：/api/bricks 每次在线拉取 shadeling-bricks 的 index.json + 各 brick.json，
  直连 raw.githubusercontent.com，失败走镜像（gh-proxy.com 等）兜底；
- 工作区：拉取结果写入 ~/.brickery/vault（纯运行时工作区，可随时删除重建）；
- 组装：确保选中积木的完整目录落盘（GitHub API 列目录 + raw 下载）后复用原组装链路。

仅标准库，无第三方依赖。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib import request as urllib_request
from urllib.error import URLError
from urllib.parse import urljoin

# shadeling-bricks 仓库（积木库源）
BRICKS_REPO_RAW = "https://raw.githubusercontent.com/suipu-boop/shadeling-bricks/main"
BRICKS_REPO_API = "https://api.github.com/repos/suipu-boop/shadeling-bricks"
BRICKS_BRANCH = "main"

# 镜像前缀（raw.githubusercontent.com 的代理，按顺序兜底；空串=直连）
MIRROR_PREFIXES = [
    "",                            # 直连
    "https://gh-proxy.com/",       # 网页版实测最快（~1.58 MB/s）
    "https://ghfast.top/",
    "https://ghproxy.net/",
]

REQUEST_TIMEOUT = 15

_USER_AGENT = "BrickeryWorkbench/0.2"


def _http_get(url: str, timeout: int = REQUEST_TIMEOUT) -> Tuple[Optional[bytes], Optional[str]]:
    """读取 URL，返回 (内容, 错误)。错误为 None 表示成功。"""
    req = urllib_request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib_request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), None
    except URLError as e:
        return None, f"网络错误：{getattr(e, 'reason', e)}"
    except (OSError, ValueError) as e:
        return None, f"读取失败：{e}"


def fetch_raw(path: str, timeout: int = REQUEST_TIMEOUT) -> Tuple[Optional[bytes], Optional[str]]:
    """从 GitHub raw 读取仓库内文件，直连失败依次走镜像。

    path 形如 "index.json" / "bricks/feishu/brick.json"。
    返回 (内容, 错误)。
    """
    last_err: Optional[str] = None
    for prefix in MIRROR_PREFIXES:
        url = prefix + f"{BRICKS_REPO_RAW}/{path}"
        data, err = _http_get(url, timeout=timeout)
        if data is not None:
            return data, None
        last_err = err
    return None, f"积木库源不可达（已尝试直连与 {len(MIRROR_PREFIXES)-1} 个镜像）：{last_err}"


def fetch_json(path: str, timeout: int = REQUEST_TIMEOUT) -> Tuple[Optional[dict], Optional[str]]:
    """读取并解析仓库内 JSON 文件。"""
    data, err = fetch_raw(path, timeout=timeout)
    if err:
        return None, err
    try:
        return json.loads(data.decode("utf-8")), None
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return None, f"积木库清单解析失败（{path}）：{e}"


def fetch_bricks_online(vault_root: str) -> Tuple[Optional[List[dict]], Optional[str]]:
    """在线拉取完整积木清单（index.json + 各 brick.json），并写入工作区缓存。

    返回 (bricks 列表[dict, 与旧 /api/bricks 结构一致], 错误)。
    任一块 brick.json 拉取失败不影响整体：该块以 index.json 摘要字段降级展示，
    并在 error 字段记录警告。
    """
    root = Path(vault_root)
    index, err = fetch_json("index.json")
    if err:
        return None, err
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.json").write_text(
        json.dumps(index, ensure_ascii=False), encoding="utf-8")

    bricks: List[dict] = []
    warnings: List[str] = []
    for entry in index.get("bricks") or []:
        name = str(entry.get("name") or "").strip()
        if not name:
            continue
        rel = str(entry.get("path") or f"bricks/{name}/").rstrip("/")
        manifest_path = f"{rel}/brick.json"
        raw_manifest, merr = fetch_json(manifest_path)
        if merr:
            # 降级：用 index.json 摘要字段，标记警告
            warnings.append(f"{name} 详情拉取失败：{merr}")
            bricks.append({
                "name": name,
                "version": str(entry.get("version") or "*"),
                "risk_level": str(entry.get("risk_level") or "low"),
                "requires": [], "conflicts": [], "resources": {},
                "summary": str(entry.get("summary") or ""),
                "description": "", "category": str(entry.get("category") or ""),
                "tags": [], "capabilities": [], "dependencies": [],
                "_partial": True,
            })
            continue
        # 完整详情落盘工作区（组装链路复用）
        brick_dir = root / rel
        brick_dir.mkdir(parents=True, exist_ok=True)
        (brick_dir / "brick.json").write_text(
            json.dumps(raw_manifest, ensure_ascii=False), encoding="utf-8")
        comp = raw_manifest.get("composition") or {}
        bricks.append({
            "name": str(raw_manifest.get("name") or name),
            "version": str(raw_manifest.get("version") or "*"),
            "risk_level": str(raw_manifest.get("risk_level") or "low"),
            "requires": [str(r) for r in (comp.get("requires") or [])],
            "conflicts": [str(c) for c in (comp.get("conflicts_with") or [])],
            "resources": dict(raw_manifest.get("resources") or {}),
            "summary": str(raw_manifest.get("summary") or ""),
            "description": str(raw_manifest.get("description") or ""),
            "category": str(raw_manifest.get("category") or ""),
            "tags": [str(t) for t in (raw_manifest.get("tags") or [])],
            "capabilities": [str(c) for c in (raw_manifest.get("capabilities") or [])],
            "dependencies": list(raw_manifest.get("dependencies") or []),
            "path": rel + "/",
        })
    if warnings:
        return bricks, "部分积木详情拉取失败：" + "；".join(warnings[:3])
    return bricks, None


def _api_dir_listing(rel_dir: str, timeout: int = REQUEST_TIMEOUT) -> Tuple[Optional[list], Optional[str]]:
    """用 GitHub Contents API 列出仓库目录，返回 [{name, download_url}]。"""
    url = f"{BRICKS_REPO_API}/contents/{rel_dir}?ref={BRICKS_BRANCH}"
    data, err = _http_get(url, timeout=timeout)
    if err:
        return None, err
    try:
        items = json.loads(data.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        return None, f"目录解析失败：{e}"
    if not isinstance(items, list):
        return None, f"目录结构异常：{items}"
    return items, None


def ensure_brick_local(vault_root: str, brick_rel: str) -> Tuple[bool, Optional[str]]:
    """确保积木完整目录落盘工作区（含 brick.json 与 files 引用的资源文件）。

    brick_rel 形如 "bricks/feishu"。用 GitHub API 列目录 + raw 下载每个文件。
    返回 (是否就绪, 错误)。
    """
    root = Path(vault_root)
    target = root / brick_rel
    listing, err = _api_dir_listing(brick_rel)
    if err:
        return False, f"无法读取积木目录 {brick_rel}：{err}"
    target.mkdir(parents=True, exist_ok=True)
    ok = True
    for item in listing:
        if not isinstance(item, dict):
            continue
        fname = str(item.get("name") or "")
        dl = str(item.get("download_url") or "")
        if not fname:
            continue
        if not dl:
            return False, f"积木 {brick_rel} 文件 {fname} 无下载地址"
        local = target / fname
        if local.exists() and local.stat().st_size > 0:
            continue  # 已缓存
        data, derr = _http_get(dl)
        if derr:
            ok = False
            continue
        local.write_bytes(data)
    if not ok:
        return False, f"积木 {brick_rel} 部分文件下载失败（工作区不完整）"
    return True, None


def ensure_bricks_local(vault_root: str, bricks: Dict[str, dict]) -> Tuple[bool, Optional[str]]:
    """确保所选积木（及其依赖）完整落盘。bricks 为 name → 清单项 dict。

    返回 (是否全部就绪, 错误)。
    """
    root = Path(vault_root)
    need: List[str] = []
    seen: set = set()

    def collect(name: str) -> None:
        if name in seen:
            return
        seen.add(name)
        b = bricks.get(name)
        if b is None:
            return
        for dep in b.get("requires") or []:
            collect(dep)
        need.append(name)

    for n in bricks:
        collect(n)

    for name in need:
        b = bricks[name]
        rel = str(b.get("path") or f"bricks/{name}/").rstrip("/")
        ok, err = ensure_brick_local(root, rel)
        if not ok:
            return False, err
    return True, None
