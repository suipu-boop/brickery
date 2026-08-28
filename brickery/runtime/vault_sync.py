"""vault_sync.py — vault 真身自动同步（specs: auto-follow-single-source §4.1）。

职责：让 `~/.brickery/vault`（非 git 运行视图）自动跟随 `brick-vault` main。

- 远端真源：`https://github.com/suipu-boop/shadeling-bricks.git`（main，shallow clone）；
- 增量补齐：缺失补齐；版本旧备份后覆盖；本地独有跳过（防用户自制积木被清）；
- legacy 字段迁移：brick.json 旧按钮形态（handler/params）→ 新协议（action/args）规范化落地；
- 失败静默降级：网络失败 / clone 失败 → 保留现状、仅记日志，绝不阻塞启动。

红线：
- 只同步"积木清单/积木包"类资产，绝不触碰用户配置（config.json、skills.json、
  vault.db、sessions、memory）与本地独有积木；
- 覆盖前必须先备份旧目录到 `vault_root/.backup/<ts>/`；
- dry_run=True 时不落盘，仅报告将执行的动作。
"""
from __future__ import annotations

import copy
import datetime
import json
import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

from . import paths

logger = logging.getLogger(__name__)

# brick-vault 积木清单 schema（vault 真身 index.json 同款）
INDEX_SCHEMA = "brick-registry/v1"
# 远端 clone 缓存目录名（相对 home/cache）
CACHE_DIRNAME = "brick-vault"
# 单次网络操作超时（秒）
CLONE_TIMEOUT = 60


@dataclass
class SyncReport:
    """一次同步的结果报告（dry_run 时动作不落盘，仅记录）。"""
    dry_run: bool
    remote_commit: str = ""
    added: List[str] = field(default_factory=list)          # 补齐
    upgraded: List[str] = field(default_factory=list)       # 版本升级（备份后覆盖）
    skipped: List[str] = field(default_factory=list)        # 本地已最新
    skipped_local_only: List[str] = field(default_factory=list)  # 本地独有，不动
    failed: List[str] = field(default_factory=list)         # 同步失败
    errors: List[str] = field(default_factory=list)         # 网络/clone 级错误

    def ok(self) -> bool:
        return not self.failed and not self.errors

    def summary(self) -> str:
        prefix = "[dry-run] " if self.dry_run else ""
        return (
            f"{prefix}vault 同步：新增 {len(self.added)} / 升级 {len(self.upgraded)}"
            f" / 已最新 {len(self.skipped)} / 本地独有跳过 {len(self.skipped_local_only)}"
            f" / 失败 {len(self.failed)} / 错误 {len(self.errors)}"
            f"（远端 commit {self.remote_commit[:12] if self.remote_commit else '-'}）"
        )


def _run(cmd: List[str], cwd: Optional[Path] = None,
         timeout: int = CLONE_TIMEOUT) -> Tuple[int, str]:
    """执行子进程，返回 (returncode, stdout+stderr)。永不抛异常。"""
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=timeout)
        out = (p.stdout or "") + (p.stderr or "")
        return p.returncode, out.strip()
    except FileNotFoundError:
        return 127, f"命令不存在：{cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "超时"
    except Exception as e:  # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def _remote_commit(cache_dir: Path) -> str:
    rc, out = _run(["git", "-C", str(cache_dir), "rev-parse", "HEAD"])
    return out.strip() if rc == 0 else ""


def _migrate_legacy_brick(raw: dict) -> dict:
    """legacy 字段迁移：旧按钮形态（handler/params）→ 新协议（action/args）。

    沿 brick 迁移规则（历史已实现 handler→action / params→args），仅当新字段
    缺失时迁移，保持向后兼容；views 的 handler 字段本就是合法契约，不动。
    """
    data = copy.deepcopy(raw)
    buttons = data.get("buttons")
    if isinstance(buttons, list):
        for b in buttons:
            if not isinstance(b, dict):
                continue
            if "handler" in b and "action" not in b:
                h = b.pop("handler")
                if isinstance(h, str):
                    b["action"] = h
            if "params" in b and "args" not in b:
                p = b.pop("params")
                if isinstance(p, dict):
                    b["args"] = p
    return data


def _copy_brick(remote_dir: Path, local_dir: Path) -> None:
    """把远端积木目录复制到本地，brick.json 经 legacy 迁移后落地。"""
    local_dir.mkdir(parents=True, exist_ok=True)
    for item in remote_dir.iterdir():
        if item.is_dir():
            shutil.copytree(item, local_dir / item.name, dirs_exist_ok=True)
        else:
            if item.name == "brick.json":
                try:
                    migrated = _migrate_legacy_brick(
                        json.loads(item.read_text(encoding="utf-8")))
                    (local_dir / item.name).write_text(
                        json.dumps(migrated, ensure_ascii=False, indent=2),
                        encoding="utf-8")
                except Exception:  # noqa: BLE001
                    # 迁移失败则原样复制，不因字段兜底阻塞同步
                    shutil.copy2(item, local_dir / item.name)
            else:
                shutil.copy2(item, local_dir / item.name)


def _read_brick_json(brick_dir: Path) -> Optional[dict]:
    bj = brick_dir / "brick.json"
    if not bj.exists():
        return None
    try:
        return json.loads(bj.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _version_of(d: Optional[dict], default: str = "0.0.0") -> Tuple[int, ...]:
    if not d or not d.get("version"):
        return tuple(int(p) if p.isdigit() else 0 for p in default.split("."))
    return tuple(int(p) if p.isdigit() else 0 for p in str(d["version"]).split("."))


def _rebuild_index(vault_root: Path) -> None:
    """按本地 bricks/ 目录重建 index.json（brick-registry/v1）。

    包含本地全部积木（含本地独有、用户自制），绝不因远端清单删条目。
    """
    bricks_dir = vault_root / "bricks"
    entries = []
    if bricks_dir.is_dir():
        for bdir in sorted(bricks_dir.iterdir()):
            if not bdir.is_dir():
                continue
            name = bdir.name
            d = _read_brick_json(bdir)
            if d is not None:
                name = str(d.get("name") or name)
                entries.append({
                    "name": name,
                    "version": str(d.get("version") or "1.0.0"),
                    "category": str(d.get("category") or "other"),
                    "risk_level": str(d.get("risk_level") or "low"),
                    "summary": str(d.get("summary") or ""),
                    "path": f"bricks/{bdir.name}/",
                })
            else:
                entries.append({
                    "name": name, "version": "1.0.0", "category": "other",
                    "risk_level": "low", "summary": "",
                    "path": f"bricks/{name}/",
                })
    idx = {
        "schema": INDEX_SCHEMA,
        "bricks": entries,
        "updated_at": datetime.datetime.now(
            datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    vault_root.mkdir(parents=True, exist_ok=True)
    (vault_root / "index.json").write_text(
        json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")


def sync_vault(home: Path, *, force: bool = False, dry_run: bool = False,
               repo_url: Optional[str] = None) -> SyncReport:
    """执行一次 vault 自动同步（增量补齐，不删本地）。

    Args:
        home: BRICKERY_HOME（默认 ~/.brickery）。
        force: 版本相同也强制覆盖（默认只升级版本或补齐缺失）。
        dry_run: 只报告将执行的动作，不落盘。
        repo_url: 远端积木库地址（默认 paths.DEFAULT_VAULT_REPO）。
    """
    home = Path(home).expanduser()
    repo_url = repo_url or paths.DEFAULT_VAULT_REPO
    report = SyncReport(dry_run=dry_run)
    cache_dir = home / "cache" / CACHE_DIRNAME
    vault_root = home / "vault"
    bricks_dir = vault_root / "bricks"

    # 1. 拉取远端快照（git shallow clone 主路径；github.com 不可达自动降级
    #    codeload tarball；均失败则静默降级返回）
    from .self_update import fetch_snapshot
    ok, err, sha = fetch_snapshot(cache_dir, repo_url)
    if not ok:
        report.errors.append(err)
        logger.warning("[vault-sync] 远端不可达，静默降级：%s", err)
        return report
    report.remote_commit = sha or _remote_commit(cache_dir)

    # 2. 读远端清单
    remote_index_file = cache_dir / "index.json"
    if not remote_index_file.exists():
        report.errors.append("远端 index.json 缺失")
        logger.warning("[vault-sync] 远端 index.json 缺失，跳过本次同步")
        return report
    try:
        remote_index = json.loads(remote_index_file.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        report.errors.append(f"远端 index.json 解析失败：{e}")
        logger.warning("[vault-sync] 远端 index.json 解析失败，跳过本次同步：%s", e)
        return report

    remote_bricks = remote_index.get("bricks") or []
    remote_names = set()
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_root = vault_root / ".backup" / ts

    # 3. 逐条比对与同步
    for entry in remote_bricks:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if not name or not isinstance(name, str):
            continue
        remote_names.add(name)
        rel = str(entry.get("path") or f"bricks/{name}/")
        # 路径防穿越：仅允许远端仓库内 bricks/<name>/ 形态
        parts = [p for p in rel.replace("\\", "/").split("/") if p and p not in (".", "..")]
        if len(parts) < 2 or parts[0] != "bricks":
            report.failed.append(name)
            logger.warning("[vault-sync] 远端条目路径非法，跳过：%s -> %s", name, rel)
            continue
        remote_brick_dir = (cache_dir / Path(*parts)).resolve()
        if not remote_brick_dir.is_dir():
            report.failed.append(name)
            logger.warning("[vault-sync] 远端积木目录缺失：%s", remote_brick_dir)
            continue
        local_brick_dir = bricks_dir / name

        if not local_brick_dir.exists():
            # 缺失 → 增量补齐
            report.added.append(name)
            if not dry_run:
                try:
                    _copy_brick(remote_brick_dir, local_brick_dir)
                except Exception as e:  # noqa: BLE001
                    report.failed.append(name)
                    logger.warning("[vault-sync] 补齐 %s 失败：%s", name, e)
            continue

        # 存在：比较版本；force 时无条件升级（仍走备份）
        remote_ver = _version_of(_read_brick_json(remote_brick_dir))
        local_ver = _version_of(_read_brick_json(local_brick_dir))
        if force or remote_ver > local_ver:
            report.upgraded.append(name)
            if not dry_run:
                try:
                    # 覆盖前先备份旧版
                    bk = backup_root / name
                    bk.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(local_brick_dir, bk, dirs_exist_ok=True)
                    # 整体替换：先删本地目录再复制（避免旧文件残留）
                    shutil.rmtree(local_brick_dir)
                    _copy_brick(remote_brick_dir, local_brick_dir)
                except Exception as e:  # noqa: BLE001
                    report.failed.append(name)
                    logger.warning("[vault-sync] 升级 %s 失败：%s", name, e)
            continue

        report.skipped.append(name)

    # 4. 本地独有（远端没有）→ 跳过，不动
    if bricks_dir.is_dir():
        for bdir in sorted(bricks_dir.iterdir()):
            if bdir.is_dir() and bdir.name not in remote_names:
                report.skipped_local_only.append(bdir.name)

    # 5. 重建本地索引（dry_run 不落盘）
    if not dry_run:
        try:
            _rebuild_index(vault_root)
        except Exception as e:  # noqa: BLE001
            report.errors.append(f"重建 index.json 失败：{e}")

    return report


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="vault 自动同步（手动触发）")
    ap.add_argument("--home", default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--repo-url", default=None)
    args = ap.parse_args()
    h = Path(args.home).expanduser() if args.home else paths.get_home()
    rep = sync_vault(h, force=args.force, dry_run=args.dry_run,
                     repo_url=args.repo_url)
    print(rep.summary())
    if rep.added:
        print("新增:", ", ".join(rep.added))
    if rep.upgraded:
        print("升级:", ", ".join(rep.upgraded))
    if rep.skipped_local_only:
        print("本地独有（跳过）:", ", ".join(rep.skipped_local_only))
    if rep.failed:
        print("失败:", ", ".join(rep.failed), file=sys.stderr)
    if rep.errors:
        for e in rep.errors:
            print("错误:", e, file=sys.stderr)
    sys.exit(0 if rep.ok() else 1)
