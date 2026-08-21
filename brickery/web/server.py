"""Brickery 本地 Web 面板后端（127.0.0.1）。

A1 决策（用户拍板）：浏览器打开即组装工作台，零安装、跨平台、易迭代。

API：
    GET  /                组装工作台前端（拖拽 UI）
    GET  /api/bricks      积木清单（来自 brick-vault）
    POST /api/assemble    组装校验 → 返回方案（拓扑序 + 资源合计）
    POST /api/produce     产出 agent 包 → 返回产出目录

仅标准库（http.server），无第三方依赖。
"""
from __future__ import annotations

import json
import os
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from ..assembler import AssemblyError, load_vault
from ..produce import DEFAULT_AGENTS_ROOT, ProduceError, ProduceMeta, produce

# 积木库默认来自 GitHub 拉下的缓存（~/.brickery/vault），保留 --vault 覆盖
DEFAULT_VAULT = str(Path.home() / ".brickery" / "vault")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# 公网默认市场源（与 runtime 一致；GitHub Only，无本地离线兜底）
DEFAULT_SKILL_REPO = ("https://raw.githubusercontent.com/"
                      "suipu-boop/shadeling-bricks/main/skills/index.json")

# DMG 打包需要 dmgbuild + PIL，web server（系统 python3）无此依赖，
# 通过 subprocess 调受管 venv python 执行 brickery/dmg.py。
# 可用环境变量 BRICKERY_DMG_PY 覆盖。
DEFAULT_DMG_PY = "/Users/suipu/.workbuddy/binaries/python/envs/default/bin/python3"
# DMG 输出目录（默认桌面）
DEFAULT_DMG_OUT = Path.home() / "Desktop"

# 前端文件目录：仓库根 web/（server.py 位于 brickery/web/，向上三级）
_FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / "web"


class BrickeryHandler(BaseHTTPRequestHandler):
    vault_root: str = DEFAULT_VAULT
    agents_root: Optional[Path] = None

    # ---- 路由 ----
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/":
            self._serve_frontend("index.html")
        elif path == "/api/bricks":
            self._api_bricks()
        elif path == "/api/sync-status":
            self._api_sync_status()
        else:
            self._json({"error": "not found"}, status=404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0)))
                              or b"{}")
        except (json.JSONDecodeError, ValueError):
            body = {}
        if path == "/api/assemble":
            self._api_assemble(body)
        elif path == "/api/produce":
            self._api_produce(body)
        elif path == "/api/dmg":
            self._api_dmg(body)
        elif path == "/api/sync":
            self._api_sync(body)
        elif path == "/api/brick-download":
            self._api_brick_download(body)
        else:
            self._json({"error": "not found"}, status=404)

    # ---- 前端 ----
    def _serve_frontend(self, filename: str) -> None:
        f = _FRONTEND_DIR / filename
        if not f.exists():
            self._json({"error": f"frontend missing: {f}"}, status=500)
            return
        data = f.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ---- API ----
    def _api_sync(self, body: dict) -> None:
        """从 GitHub 拉取/更新底座与积木（首次 clone，之后 pull）。"""
        from ..web.sync import SyncError, sync_all, status
        try:
            result = sync_all()
        except SyncError as e:
            self._json({"ok": False, "error": str(e)})
            return
        result["status"] = status()
        self._json(result)

    def _api_sync_status(self) -> None:
        """只读源状态（不触发网络）。"""
        from ..web.sync import status
        self._json(status())

    def _api_brick_download(self, body: dict) -> None:
        """按积木 id 从公网市场源拉取，打包成 .brick 存到桌面。

        支持单块 / 批量（ids 多值打进同一个包）。离线导入通道的「打包」侧。
        """
        ids = [str(i).strip() for i in (body.get("ids") or []) if str(i).strip()]
        if not ids:
            self._json({"ok": False, "error": "缺少要打包的积木 id"})
            return
        self._json(_download_bricks_to_desktop(ids, self.vault_root))

    def _api_bricks(self) -> None:
        try:
            asm = load_vault(self.vault_root)
        except AssemblyError as e:
            self._json({"error": str(e)}, status=400)
            return
        bricks = []
        engines = []
        for name, b in sorted(asm.bricks.items()):
            item = {
                "name": b.name,
                "version": b.version,
                "risk_level": b.risk_level,
                "requires": b.requires,
                "conflicts": b.conflicts,
                "resources": b.resources,
                # 展示字段（来自 brick.json，供前端解释积木）
                "summary": b.summary,
                "description": b.description,
                "category": b.category,
                "tags": b.tags,
                "capabilities": b.capabilities,
                "dependencies": b.dependencies,
            }
            if b.category == "engine":
                engines.append(item)  # engine 为底座默认能力，单独返回供底座区展示
            else:
                bricks.append(item)
        self._json({"bricks": bricks, "engines": engines})

    def _api_assemble(self, body: dict) -> None:
        selected = body.get("selected") or []
        try:
            asm = load_vault(self.vault_root)
            plan = asm.assemble(selected)
        except AssemblyError as e:
            self._json({"ok": False, "error": str(e)})
            return
        self._json({"ok": True, "plan": plan.as_dict()})

    def _api_produce(self, body: dict) -> None:
        selected = body.get("selected") or []
        meta = ProduceMeta(
            name=str(body.get("name") or "").strip(),
            description=str(body.get("description") or ""),
            version=str(body.get("version") or "0.1.0"),
            author=str(body.get("author") or ""),
        )
        try:
            port = int(body.get("port") or 18765)
        except (TypeError, ValueError):
            port = 18765
        # 产出前自动从 GitHub 拉最新积木/底座；失败静默降级本地缓存，不阻塞产出
        try:
            from ..web.sync import sync_all
            sync_all()
        except Exception:
            pass
        try:
            asm = load_vault(self.vault_root)
            plan = asm.assemble(selected)
            out = produce(plan, self.vault_root, meta,
                          agents_root=self.agents_root, port=port)
        except (AssemblyError, ProduceError) as e:
            self._json({"ok": False, "error": str(e)})
            return
        self._json({
            "ok": True,
            "path": str(out),
            "name": meta.name,
            # 真实磁盘体积（MB）：积木声明的 disk_mb 是资源预算，非包体积
            "real_disk_mb": _dir_size_mb(out),
        })

    def _api_dmg(self, body: dict) -> None:
        """对已产出 agent 生成 DMG 安装包（桌面）。"""
        name = str(body.get("name") or "").strip()
        if not name:
            self._json({"ok": False, "error": "缺少 agent 名称"})
            return
        version = str(body.get("version") or "0.1.0")
        try:
            port = int(body.get("port") or 18765)
        except (TypeError, ValueError):
            port = 18765
        root = self.agents_root or DEFAULT_AGENTS_ROOT
        agent_dir = root / name
        if not agent_dir.is_dir():
            self._json({"ok": False, "error": f"agent 不存在：{name}"})
            return
        out_dmg = DEFAULT_DMG_OUT / f"{name}-{version}.dmg"
        py_bin = os.environ.get("BRICKERY_DMG_PY", DEFAULT_DMG_PY)
        dmg_script = Path(__file__).resolve().parent.parent / "dmg.py"
        try:
            subprocess.run(
                [py_bin, str(dmg_script), "--agent", str(agent_dir),
                 "--out", str(out_dmg), "--name", name,
                 "--version", version, "--port", str(port)],
                check=True, capture_output=True, text=True, timeout=180,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
            self._json({"ok": False, "error": f"DMG 生成失败：{e}"})
            return
        self._json({"ok": True, "path": str(out_dmg)})

    # ---- 工具 ----
    def _json(self, obj: dict, status: int = 200) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        # 精简日志：只留请求行
        if fmt.startswith('"%s '):
            print(f"[brickery] {args[0]}")


def _dir_size_mb(path: Path) -> int:
    """计算目录真实体积（MB，du -sm 取整）。"""
    try:
        return int(subprocess.check_output(["du", "-sm", str(path)]).split()[0])
    except (subprocess.SubprocessError, OSError, ValueError, IndexError):
        return 0


def _safe_filename(name: str) -> str:
    """合法化 .brick 文件名（去路径分隔/空白/控制字符，限长）。"""
    import re
    n = re.sub(r'[\\/:*?"<>|\s]+', "-", str(name or "")).strip("-")
    return (n or "brick")[:80]


def _download_bricks_to_desktop(ids: list, vault_root: str) -> dict:
    """从公网市场源拉取积木（完整 Skill JSON），打包 .brick 到桌面。

    返回 {ok, path, count, names} 或 {ok: False, error}。
    单块包名用积木名；多块用 bricks-<时间戳>。
    """
    import time as _time
    from dataclasses import asdict

    from ..package import BrickPackageError, pack_bricks
    from ..runtime.skill_library import SkillLibrary

    class _NoSkills:
        """list_entries 需要 skills_registry 查询已装版本；下载侧不关心，给空注册表。"""

        def all(self):
            return []

    lib = SkillLibrary(DEFAULT_SKILL_REPO, Path(vault_root), timeout=20)
    entries, err = lib.list_entries(_NoSkills(), force=True)
    if err:
        return {"ok": False, "error": f"市场源不可达：{err}"}
    by_id = {e.id: e for e in entries}
    by_name = {e.name: e for e in entries}
    wanted = []
    for i in ids:
        e = by_id.get(i) or by_name.get(i)
        if e is None:
            return {"ok": False, "error": f"市场源中找不到积木：{i}"}
        if e not in wanted:
            wanted.append(e)
    skills = []
    for e in wanted:
        skill, derr = lib._download_skill(e.download_url)
        if derr:
            return {"ok": False, "error": f"积木 {e.name} 拉取失败：{derr}"}
        skills.append(asdict(skill))
    if len(skills) == 1:
        fname = _safe_filename(str(skills[0].get("name") or skills[0].get("id") or "brick"))
    else:
        fname = "bricks-" + _time.strftime("%Y%m%d-%H%M%S")
    out = Path.home() / "Desktop" / f"{fname}.brick"
    try:
        pack_bricks(skills, out)
    except BrickPackageError as e:
        return {"ok": False, "error": str(e)}
    return {
        "ok": True,
        "path": str(out),
        "count": len(skills),
        "names": [str(s.get("name") or s.get("id") or "?") for s in skills],
    }


def serve(vault_root: str = DEFAULT_VAULT,
          host: str = DEFAULT_HOST, port: int = DEFAULT_PORT,
          agents_root: Optional[Path] = None) -> None:
    """启动本地 Web 面板。"""
    BrickeryHandler.vault_root = vault_root
    BrickeryHandler.agents_root = agents_root
    httpd = ThreadingHTTPServer((host, port), BrickeryHandler)
    print(f"Brickery 组装工作台：http://{host}:{port}")
    print(f"积木库：{vault_root}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Brickery 本地 Web 面板")
    ap.add_argument("--vault", default=DEFAULT_VAULT, help="brick-vault 路径")
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--agents-root", default=None, help="产出目录（默认 ~/.brickery/agents）")
    args = ap.parse_args()
    serve(vault_root=args.vault, host=args.host, port=args.port,
          agents_root=Path(args.agents_root) if args.agents_root else None)
