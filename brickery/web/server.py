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
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from ..assembler import AssemblyError, load_vault
from ..produce import ProduceError, ProduceMeta, produce

DEFAULT_VAULT = str(Path.home() / "Dev" / "brick-vault")
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

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
    def _api_bricks(self) -> None:
        try:
            asm = load_vault(self.vault_root)
        except AssemblyError as e:
            self._json({"error": str(e)}, status=400)
            return
        bricks = []
        for name, b in sorted(asm.bricks.items()):
            bricks.append({
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
            })
        self._json({"bricks": bricks})

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
            asm = load_vault(self.vault_root)
            plan = asm.assemble(selected)
            out = produce(plan, self.vault_root, meta,
                          agents_root=self.agents_root)
        except (AssemblyError, ProduceError) as e:
            self._json({"ok": False, "error": str(e)})
            return
        self._json({"ok": True, "path": str(out), "name": meta.name})

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
