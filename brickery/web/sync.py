"""GitHub 拉源：底座 + 积木从 GitHub clone/pull 到本地缓存。

链路起点（用户拍板）：最终用户本地无底座，produce 时用 GitHub 拉下的
`~/.brickery/base` runtime；积木用 `~/.brickery/vault`。本地仓库仅开发兜底。

    sync_vault()  → ~/.brickery/vault/   (shadeling-bricks.git)
    sync_base()   → ~/.brickery/base/    (brickery.git)

首次 clone，之后 pull。返回各自 commit 与更新时间，供前端展示。
"""
from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# GitHub 源（remote origin）
BRICKERY_REPO = "https://github.com/suipu-boop/brickery.git"
BRICKS_REPO = "https://github.com/suipu-boop/shadeling-bricks.git"

# 本地缓存根
CACHE_ROOT = Path.home() / ".brickery"
VAULT_DIR = CACHE_ROOT / "vault"      # 积木库
BASE_DIR = CACHE_ROOT / "base"        # 底座 runtime


class SyncError(RuntimeError):
    """GitHub 拉源失败。"""


def _git(*args: str, cwd: Optional[Path] = None) -> str:
    """执行 git 命令，返回 stdout（去尾空白）。失败抛 SyncError。"""
    try:
        r = subprocess.run(
            ["git", *args], cwd=str(cwd) if cwd else None,
            check=True, capture_output=True, text=True, timeout=120,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        detail = getattr(e, "stderr", "") or str(e)
        raise SyncError(f"git {' '.join(args)} 失败：{detail.strip()[:300]}") from e
    return r.stdout.strip()


def _repo_state(repo: Path) -> dict:
    """读取仓库当前 commit 与最近更新时间。"""
    try:
        commit = _git("rev-parse", "--short", "HEAD", cwd=repo)
    except SyncError:
        commit = ""
    try:
        ts = _git("log", "-1", "--format=%cI", cwd=repo)
    except SyncError:
        ts = ""
    return {"commit": commit, "updated_at": ts}


def _sync_repo(name: str, url: str, dest: Path) -> dict:
    """clone（不存在）或 pull（已存在）单个仓库，返回状态。"""
    dest.mkdir(parents=True, exist_ok=True)
    if not (dest / ".git").is_dir():
        _git("clone", url, str(dest))
        action = "clone"
    else:
        _git("pull", "--ff-only", cwd=dest)
        action = "pull"
    state = _repo_state(dest)
    state["action"] = action
    state["path"] = str(dest)
    return state


def sync_vault() -> dict:
    """同步积木库到 ~/.brickery/vault/。"""
    return _sync_repo("积木库", BRICKS_REPO, VAULT_DIR)


def sync_base() -> dict:
    """同步底座到 ~/.brickery/base/。"""
    return _sync_repo("底座", BRICKERY_REPO, BASE_DIR)


def sync_all() -> dict:
    """同步底座 + 积木，返回汇总（供 /api/sync）。"""
    vault = sync_vault()
    base = sync_base()
    return {
        "ok": True,
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "vault": vault,
        "base": base,
    }


def status() -> dict:
    """只读状态：两个缓存目录是否存在、commit、更新时间（不触发网络）。"""
    def _s(repo: Path) -> dict:
        if not (repo / ".git").is_dir():
            return {"present": False, "path": str(repo)}
        st = _repo_state(repo)
        st["present"] = True
        st["path"] = str(repo)
        return st

    return {
        "ok": True,
        "vault": _s(VAULT_DIR),
        "base": _s(BASE_DIR),
    }
