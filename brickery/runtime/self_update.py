"""self_update.py — App 启动自检更新（specs: auto-follow-single-source §4.2）。

生成 app（shadelingmac）与工坊 app（BrickeryWorkbench）共用同一套自检更新逻辑，
实现放内核（本模块），两 app 仅传不同参数（core_repo / extra_repo）。

行为：
- 检查：远端 GitHub main 最新 commit SHA vs 本地 `brickery/version.json` 的
  `core_commit`；有更新 → 报告 update_available，默认不自动落地（需授权）。
- 拉取：白名单仅允许 suipu-boop/brickery（+ 可选 suipu-boop/brickery-workbench
  的 web 覆盖：brickery/web/ + web/index.html），shallow clone 到 home/cache。
- 落盘（apply_update）：构建 `.update/<sha>/` → 校验（__init__.py 存在、
  version.json 可解析）→ 备份旧版到 `.backup/<ts>/` → 原子替换 → 写
  `pending_restart.json`（提示重启生效）。
- 生效：本次启动仍加载旧版；下次启动后新代码生效（UI 提示"重启后生效"）。
- 失败回滚：任一步失败恢复 `.backup/<ts>/`，删除 `.update/`，本次启动加载旧版。

安全边界：
- 域名 + 仓库双白名单；禁止跳转第三方 CDN；落盘路径严格限定 runtime 根内；
- 绝不触碰 `~/.brickery/` 任何用户数据（config、skills.json、vault.db、
  sessions、memory）与 runtime 白名单外路径（python/ 解释器、bricks/ 产物）；
- 断网 / clone 失败 → 返回 no-update / 错误信息，由调用方决定静默降级。
"""
from __future__ import annotations

import datetime
import json
import logging
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urlparse

from . import paths

logger = logging.getLogger(__name__)

# 域名 + 仓库白名单（防 URL 注入 / 跳转第三方 CDN）
ALLOWED_GIT_HOSTS = {"github.com"}
ALLOWED_OWNER = "suipu-boop"
ALLOWED_REPOS = {"brickery", "brickery-workbench", "shadeling-bricks"}
# 白名单路径：仅这些可被替换（runtime 相对 brickery-runtime 根）
ALLOWED_REL_PREFIXES = ("brickery/", "web/")
# 用户数据/解释器/产物：永远不动（排除清单，兜底防越界）
NEVER_TOUCH = {"python", "bricks", "bin", "models"}
# 运行时根目录名
RUNTIME_DIRNAME = "brickery-runtime"
# 版本标识文件名（打包时写入；self_update 落盘时也写）
VERSION_FILE = "version.json"
PENDING_FILE = "pending_restart.json"
# 缓存目录名（相对 home/cache）
CACHE_DIRNAME = "self-update"
CLONE_TIMEOUT = 60
DEFAULT_TIMEOUT = 15


@dataclass
class UpdateInfo:
    """检查结果：是否有可用更新。"""
    runtime_root: Optional[Path]
    local_sha: str = ""
    remote_sha: str = ""
    update_available: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


@dataclass
class UpdateResult:
    """apply_update 结果。"""
    applied: bool = False
    old_sha: str = ""
    new_sha: str = ""
    backup_dir: Optional[Path] = None
    rolled_back: bool = False
    error: str = ""
    copied_paths: List[str] = field(default_factory=list)


def _validate_repo_url(url: str) -> Tuple[bool, str]:
    """校验仓库 URL：必须命中白名单（域名+owner+repo），否则拒绝拉取。"""
    try:
        u = urlparse(url)
        host = (u.hostname or "").lower()
        if host not in ALLOWED_GIT_HOSTS:
            return False, f"域名不在白名单：{host or '(空)'}"
        parts = [p for p in u.path.split("/") if p]
        if len(parts) < 2:
            return False, "URL 缺少 owner/repo"
        owner, repo = parts[0], parts[1].replace(".git", "")
        if owner != ALLOWED_OWNER:
            return False, f"仓库 owner 不在白名单：{owner}"
        if repo not in ALLOWED_REPOS:
            return False, f"仓库不在白名单：{repo}"
        return True, ""
    except Exception as e:  # noqa: BLE001
        return False, f"URL 解析失败：{e}"


def _run(cmd: List[str], cwd: Optional[Path] = None,
         timeout: int = CLONE_TIMEOUT) -> Tuple[int, str]:
    try:
        p = subprocess.run(
            cmd, cwd=str(cwd) if cwd else None,
            capture_output=True, text=True, timeout=timeout)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except FileNotFoundError:
        return 127, f"命令不存在：{cmd[0]}"
    except subprocess.TimeoutExpired:
        return 124, "超时"
    except Exception as e:  # noqa: BLE001
        return 1, f"{type(e).__name__}: {e}"


def runtime_root_from_executable() -> Optional[Path]:
    """从内嵌解释器推导 runtime 根：Resources/python/bin/python3 →
    Resources/brickery-runtime。开发态（系统 python）推导失败返回 None。

    必须基于 sys.executable（而非 __file__）：开发仓库内 __file__ 向上会
    误命中用户主目录下恰好同名目录，可能把真实用户目录当成 runtime。
    """
    try:
        exe = Path(sys.executable).resolve()
    except Exception:  # noqa: BLE001
        return None
    # 内嵌解释器相对 app：.../Resources/python/bin/python3
    # parents[1]=python, parents[2]=Resources, parents[3]=Contents
    for cand in (exe.parents[1], exe.parents[2], exe.parents[3], exe.parents[4]):
        rt = cand / RUNTIME_DIRNAME
        if rt.is_dir():
            return rt
    return None


def read_local_version(runtime_root: Path) -> Optional[dict]:
    vf = runtime_root / "brickery" / VERSION_FILE
    if not vf.exists():
        return None
    try:
        return json.loads(vf.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None


def _local_sha(runtime_root: Path) -> str:
    v = read_local_version(runtime_root)
    if v and v.get("core_commit"):
        return str(v["core_commit"])
    return ""


def _remote_sha(cache_dir: Path) -> str:
    """git 缓存目录的 HEAD commit SHA（tarball 快照返回空串，用 API sha）。"""
    rc, out = _run(["git", "-C", str(cache_dir), "rev-parse", "HEAD"])
    return out.strip() if rc == 0 else ""


def _repo_parts(repo_url: str) -> Tuple[str, str]:
    u = urlparse(repo_url)
    parts = [p for p in u.path.split("/") if p]
    owner = parts[0] if parts else ""
    repo = parts[1].replace(".git", "") if len(parts) > 1 else ""
    return owner, repo


def _http_bytes(url: str, timeout: int = 30) -> bytes:
    """下载 URL 内容（带 UA，防 GitHub 拒绝）。失败抛异常。"""
    req = urllib.request.Request(url, headers={
        "User-Agent": "brickery-self-update/1.0",
        "Accept": "application/vnd.github+json, */*",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _api_remote_sha(repo_url: str, ref: str = "main",
                    timeout: int = 15) -> str:
    """经 GitHub API 取远端 main commit SHA（api.github.com 可达时）。"""
    owner, repo = _repo_parts(repo_url)
    if not owner or not repo:
        return ""
    url = f"https://api.github.com/repos/{owner}/{repo}/commits/{ref}"
    try:
        data = json.loads(_http_bytes(url, timeout=timeout).decode("utf-8"))
        return str(data.get("sha") or "")
    except Exception:  # noqa: BLE001
        return ""


def _fetch_tarball(cache_dir: Path, repo_url: str, ref: str = "main") -> str:
    """降级通道：github.com 不可达时经 codeload 下载 main 快照（tar.gz）。

    解压后内容平铺到 cache_dir（无 .git 目录，等效 shallow 快照）。
    返回远端 commit SHA（API 可达时；否则空串）。
    """
    owner, repo = _repo_parts(repo_url)
    if not owner or not repo:
        raise ValueError("无法解析仓库 URL")
    tb_url = (f"https://codeload.github.com/{owner}/{repo}/"
              f"tar.gz/refs/heads/{ref}")
    data = _http_bytes(tb_url, timeout=60)
    extract_dir = cache_dir.parent / f"{cache_dir.name}.extract"
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
        tf.extractall(extract_dir)
    # 顶层单目录（<repo>-<ref>/）内容平铺到 cache_dir
    children = [p for p in extract_dir.iterdir() if p.is_dir()]
    src = children[0] if len(children) == 1 else extract_dir
    if cache_dir.exists():
        shutil.rmtree(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        shutil.move(str(item), str(cache_dir / item.name))
    shutil.rmtree(extract_dir, ignore_errors=True)
    return _api_remote_sha(repo_url, ref)


def fetch_snapshot(cache_dir: Path, repo_url: str, ref: str = "main"
                   ) -> Tuple[bool, str, str]:
    """拉取远端 main 快照到 cache_dir，返回 (ok, err, remote_sha)。

    主路径 git shallow clone（github.com 可达）；不可达且无 git 缓存时
    降级 codeload tarball 快照。remote_sha 优先 git rev-parse，其次 API。
    """
    ok, err = _validate_repo_url(repo_url)
    if not ok:
        return False, err, ""
    # 已有 git 缓存：优先快进更新，失败沿用
    if (cache_dir / ".git").exists():
        rc, _ = _run(["git", "-C", str(cache_dir), "pull", "--ff-only",
                      "--depth", "1", "origin", "main"])
        if rc == 0:
            return True, "", _remote_sha(cache_dir)
        logger.warning("[snapshot] git 快进失败，沿用本地缓存：%s", err)
        return True, "沿用本地缓存", _remote_sha(cache_dir)
    # 无缓存：先 git clone，失败降级 tarball
    rc, out = _run(["git", "clone", "--depth", "1", repo_url, str(cache_dir)])
    if rc == 0:
        return True, "", _remote_sha(cache_dir)
    try:
        sha = _fetch_tarball(cache_dir, repo_url, ref)
        return True, f"git 不可用已降级 tarball（{out[:60]}）", sha
    except Exception as e:  # noqa: BLE001
        return False, f"clone 与 tarball 均失败：{e}", ""


def _remote_sha(cache_dir: Path) -> str:
    rc, out = _run(["git", "-C", str(cache_dir), "rev-parse", "HEAD"])
    return out.strip() if rc == 0 else ""


def check_for_update(runtime_root: Optional[Path], home: Path, *,
                     core_repo: str = paths.DEFAULT_CORE_REPO,
                     extra_repo: Optional[str] = None,
                     timeout: int = DEFAULT_TIMEOUT) -> UpdateInfo:
    """检查远端 brickery main 是否有比本地 version.json 更新的 commit。

    任何失败（断网 / 本地无 version.json / clone 失败）都返回 no-update 或
    带 error 的 UpdateInfo，由调用方决定静默降级。
    """
    home = Path(home).expanduser()
    if runtime_root is None:
        return UpdateInfo(runtime_root=None, error="无法定位 runtime 根（开发态跳过自检更新）")
    if not (runtime_root / "brickery").is_dir():
        return UpdateInfo(runtime_root=runtime_root, error=f"runtime 缺少 brickery 包：{runtime_root}")

    cache_dir = home / "cache" / CACHE_DIRNAME / "brickery"
    ok, err, remote = fetch_snapshot(cache_dir, core_repo)
    if not ok:
        return UpdateInfo(runtime_root=runtime_root, error=f"远端不可达：{err}")
    if not remote:
        return UpdateInfo(runtime_root=runtime_root, error="无法解析远端 commit")
    local = _local_sha(runtime_root)
    if not local:
        # 本地无版本标识（旧安装包未带 version.json）：不自动更新，等待重打包
        return UpdateInfo(runtime_root=runtime_root, local_sha="(未标识)",
                          remote_sha=remote, update_available=False,
                          error="本地无 version.json（旧安装包），跳过自动更新")
    return UpdateInfo(runtime_root=runtime_root, local_sha=local, remote_sha=remote,
                      update_available=(remote != local))


def _copy_tree(src: Path, dst: Path, copied: List[str]) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    for item in src.iterdir():
        if item.name in ("__pycache__", ".git", ".update", ".backup"):
            continue
        if item.is_dir():
            _copy_tree(item, dst / item.name, copied)
        else:
            shutil.copy2(item, dst / item.name)
            copied.append(str(dst / item.name))


def _write_version(update_root: Path, runtime_root: Path, *,
                   core_sha: str, workbench_sha: str) -> None:
    now = datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    v = {
        "schema": "brickery-version/v1",
        "core_commit": core_sha,
        "workbench_commit": workbench_sha or "",
        "built_at": now,
        "previous": _local_sha(runtime_root),
    }
    vf = update_root / "brickery" / VERSION_FILE
    vf.parent.mkdir(parents=True, exist_ok=True)
    vf.write_text(json.dumps(v, ensure_ascii=False, indent=2), encoding="utf-8")


def _validate_update_root(update_root: Path) -> Tuple[bool, str]:
    pkg = update_root / "brickery"
    if not (pkg / "__init__.py").exists():
        return False, "brickery/__init__.py 缺失"
    vf = pkg / VERSION_FILE
    if not vf.exists():
        return False, f"{VERSION_FILE} 缺失"
    try:
        v = json.loads(vf.read_text(encoding="utf-8"))
        if not v.get("core_commit"):
            return False, "version.json 缺少 core_commit"
    except Exception as e:  # noqa: BLE001
        return False, f"version.json 不可解析：{e}"
    return True, ""


def _clean_update(update_root: Path) -> None:
    """清理 .update/<sha>/ 及空的 .update 父目录。"""
    shutil.rmtree(update_root, ignore_errors=True)
    parent = update_root.parent
    try:
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        pass


def apply_update(runtime_root: Path, home: Path, *,
                 core_repo: str = paths.DEFAULT_CORE_REPO,
                 extra_repo: Optional[str] = None,
                 timeout: int = DEFAULT_TIMEOUT) -> UpdateResult:
    """拉取远端并原子替换 runtime（备份旧版，失败回滚）。

    仅替换白名单路径（brickery/ 包 + 可选 web/ 覆盖）；python/ 解释器、
    bricks/ 用户产物、~/.brickery 用户数据一律不动。
    """
    home = Path(home).expanduser()
    res = UpdateResult()
    if not (runtime_root / "brickery").is_dir():
        res.error = f"runtime 缺少 brickery 包：{runtime_root}"
        return res

    # 1. 拉取内核（白名单校验在 fetch_snapshot 内；git 不可达自动降级 tarball）
    core_cache = home / "cache" / CACHE_DIRNAME / "brickery"
    ok, err, core_sha = fetch_snapshot(core_cache, core_repo)
    if not ok:
        res.error = f"内核拉取失败：{err}"
        return res
    if not core_sha:
        res.error = "无法解析内核 commit"
        return res
    local = _local_sha(runtime_root)
    res.old_sha = local or "(未标识)"

    # 2. 可选工坊 web 覆盖
    workbench_sha = ""
    if extra_repo:
        wb_cache = home / "cache" / CACHE_DIRNAME / "workbench"
        ok, err, workbench_sha = fetch_snapshot(wb_cache, extra_repo)
        if not ok:
            res.error = f"工坊覆盖拉取失败：{err}"
            return res

    # 3. 构建 .update/<sha>/
    update_root = runtime_root / ".update" / core_sha
    if update_root.exists():
        shutil.rmtree(update_root)
    copied: List[str] = []
    _copy_tree(core_cache / "brickery", update_root / "brickery", copied)
    if extra_repo and wb_cache:
        # 工坊后端覆盖：wb/brickery/web/ → update/brickery/web/
        wb_web = wb_cache / "brickery" / "web"
        if wb_web.is_dir():
            _copy_tree(wb_web, update_root / "brickery" / "web", copied)
        # 工坊前端覆盖：wb/web/index.html → update/web/index.html
        wb_index = wb_cache / "web" / "index.html"
        if wb_index.exists():
            dst = update_root / "web" / "index.html"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(wb_index, dst)
            copied.append(str(dst))

    # 4. 写 version.json + 校验
    _write_version(update_root, runtime_root, core_sha=core_sha,
                   workbench_sha=workbench_sha)
    valid, verr = _validate_update_root(update_root)
    if not valid:
        _clean_update(update_root)
        res.error = f"更新包校验失败：{verr}"
        return res
    res.new_sha = core_sha
    res.copied_paths = copied

    # 5. 备份旧版 + 原子替换 + 回滚保护
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_dir = runtime_root / ".backup" / ts
    old_pkg = runtime_root / "brickery"
    new_pkg = update_root / "brickery"
    try:
        # 备份旧 brickery/（与 .update/.backup 并存，回滚用）
        backup_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(old_pkg, backup_dir / "brickery", dirs_exist_ok=True)
        # 若旧 web/（工坊前端）存在，一并备份（后续可能被覆盖）
        if (runtime_root / "web").exists():
            shutil.copytree(runtime_root / "web", backup_dir / "web",
                            dirs_exist_ok=True)
        res.backup_dir = backup_dir
        # 原子替换：先改名旧目录 → 移入新目录（同分区 os.replace 原子）
        old_moved = runtime_root / ".backup" / ts / "brickery_old"
        old_pkg.rename(old_moved)
        new_pkg.rename(old_pkg)
        # web/ 前端覆盖（若本次更新携带 web/index.html）
        if (update_root / "web").exists():
            web_dst = runtime_root / "web"
            if web_dst.exists():
                shutil.rmtree(web_dst)
            shutil.copytree(update_root / "web", web_dst)
        # 清理 .update
        _clean_update(update_root)
        # 6. 写 pending_restart 标记：本次启动仍加载旧版，重启后生效
        (runtime_root / PENDING_FILE).write_text(
            json.dumps({
                "old_sha": res.old_sha, "new_sha": core_sha,
                "backup": str(backup_dir),
                "pending_at": datetime.datetime.now(
                    datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        res.applied = True
        return res
    except Exception as e:  # noqa: BLE001
        # 7. 失败回滚：恢复备份
        res.error = f"替换失败：{e}"
        try:
            if not old_pkg.exists() and (backup_dir / "brickery_old").exists():
                (backup_dir / "brickery_old").rename(old_pkg)
            elif (backup_dir / "brickery").exists() and not old_pkg.exists():
                shutil.copytree(backup_dir / "brickery", old_pkg,
                                dirs_exist_ok=True)
            res.rolled_back = True
            logger.warning("[self-update] 已回滚到旧版：%s", e)
        except Exception as re_:  # noqa: BLE001
            res.error += f"；回滚失败：{re_}"
        _clean_update(update_root)
        return res


if __name__ == "__main__":
    import argparse
    import sys

    ap = argparse.ArgumentParser(description="App 启动自检更新（手动触发）")
    ap.add_argument("--runtime", default=None,
                    help="brickery-runtime 根（默认自动推导）")
    ap.add_argument("--home", default=None)
    ap.add_argument("--extra-repo", default=None,
                    help="工坊覆盖仓库（可选）")
    ap.add_argument("--apply", action="store_true", help="拉取并落盘更新")
    args = ap.parse_args()
    h = Path(args.home).expanduser() if args.home else paths.get_home()
    rt = Path(args.runtime).expanduser() if args.runtime else runtime_root_from_executable()
    if args.apply:
        if rt is None:
            print("无法定位 runtime 根", file=sys.stderr)
            sys.exit(2)
        r = apply_update(rt, h, extra_repo=args.extra_repo)
        print(json.dumps({
            "applied": r.applied, "old_sha": r.old_sha, "new_sha": r.new_sha,
            "backup": str(r.backup_dir) if r.backup_dir else "",
            "rolled_back": r.rolled_back, "error": r.error,
            "copied": len(r.copied_paths),
        }, ensure_ascii=False, indent=2))
        sys.exit(0 if r.applied else 1)
    info = check_for_update(rt, h, extra_repo=args.extra_repo)
    print(json.dumps({
        "runtime": str(rt) if rt else None,
        "local_sha": info.local_sha, "remote_sha": info.remote_sha,
        "update_available": info.update_available, "error": info.error,
    }, ensure_ascii=False, indent=2))
    sys.exit(0 if (info.ok and not info.update_available) else 1)
