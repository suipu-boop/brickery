"""产出链路：组装方案 → 独立可安装的 agent 包。

B 决策（用户拍板）：产出的 agent 是**独立安装包**——可独立安装、独立运行、
可分发的 agent 包，不只是配置。

产出物结构（`~/.brickery/agents/<name>/`）：
    agent.json        装配清单（元信息 + 拓扑序 + 资源合计）
    bricks/           选中积木的 brick.json 快照（自包含，不依赖 brick-vault）
    run.sh            启动脚本（拉起产出 agent 的独立运行时）
    <name>.app/       macOS 独立安装包（.app 骨架，可打包 .dmg 分发）

本模块只做「固化 + 打包」，不负责动态激活（激活由 brick_runtime 委托宿主内核）。
"""
from __future__ import annotations

import json
import os
import shutil
import stat
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .assembler import AssemblyPlan, AssemblyError

# 产出根目录：~/.brickery/agents/
DEFAULT_AGENTS_ROOT = Path.home() / ".brickery" / "agents"


@dataclass
class ProduceMeta:
    """产出 agent 的元信息。"""

    name: str
    description: str = ""
    version: str = "0.1.0"
    author: str = ""
    entry: str = "run.sh"          # 启动入口（相对 agent 目录）
    runtime: str = "brickery"      # 独立运行时（B6 起打包 brickery-runtime，不依赖宿主）


class ProduceError(ValueError):
    """产出失败（重名 / 缺积木 / 打包失败）。"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def produce(plan: AssemblyPlan, vault_root: str, meta: ProduceMeta,
            agents_root: Optional[Path] = None,
            *, overwrite: bool = False, port: int = 18765) -> Path:
    """把组装方案固化成独立 agent 包，返回产出目录。

    - 校验：agent 名合法、不重名（除非 overwrite）
    - 固化：agent.json + bricks/ 快照 + run.sh
    - 打包：生成 <name>.app 骨架（macOS）
    """
    name = meta.name.strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise ProduceError(f"非法 agent 名：{name!r}")

    root = agents_root or DEFAULT_AGENTS_ROOT
    out_dir = root / name
    if out_dir.exists() and not overwrite:
        raise ProduceError(f"agent 已存在：{out_dir}（用 overwrite=True 覆盖）")

    vault = Path(vault_root)
    # 1) 校验选中积木的 brick.json 都在
    for brick_name in plan.order:
        manifest = _find_manifest(vault, brick_name)
        if manifest is None:
            raise ProduceError(f"积木 {brick_name} 的 brick.json 缺失")

    # 2) 固化目录
    if out_dir.exists():
        shutil.rmtree(out_dir)
    bricks_dir = out_dir / "bricks"
    bricks_dir.mkdir(parents=True)

    # 3) 复制积木快照
    for brick_name in plan.order:
        manifest = _find_manifest(vault, brick_name)
        shutil.copy2(manifest, bricks_dir / f"{brick_name}.brick.json")

    # 4) 写装配清单
    manifest_data = {
        "schema": "brickery-agent/v1",
        "name": name,
        "description": meta.description,
        "version": meta.version,
        "author": meta.author,
        "runtime": meta.runtime,
        "entry": meta.entry,
        "produced_at": _now_iso(),
        "assembly": {
            "order": plan.order,
            "resources_total": plan.resources_total,
        },
    }
    (out_dir / "agent.json").write_text(
        json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 5) 生成启动脚本
    _write_run_script(out_dir, meta)

    # 6) 打包 .app 骨架（macOS）
    _bundle_app(out_dir, meta, port=port)

    return out_dir


def _find_manifest(vault: Path, brick_name: str) -> Optional[Path]:
    """在 brick-vault 里定位 brick.json（index.json 的 path 或默认路径）。"""
    index_path = vault / "index.json"
    if index_path.exists():
        try:
            raw = json.loads(index_path.read_text(encoding="utf-8"))
            for entry in raw.get("bricks") or []:
                if entry.get("name") == brick_name:
                    p = vault / (entry.get("path") or f"bricks/{brick_name}/")
                    m = p / "brick.json"
                    if m.exists():
                        return m
        except (json.JSONDecodeError, OSError):
            pass
    m = vault / "bricks" / brick_name / "brick.json"
    return m if m.exists() else None


def _write_run_script(out_dir: Path, meta: ProduceMeta) -> None:
    """生成 run.sh：拉起产出 agent 的独立运行时。

    只走 .app 内打包的 brickery-runtime（B1–B5 全部，双击即跑），
    不依赖任何宿主运行时命令（Shadeling 等）。
    """
    script = f"""#!/bin/bash
# {meta.name} —— 由 Brickery 产出的独立 agent
# 启动入口：.app 内打包的 brickery-runtime，不依赖宿主运行时
set -euo pipefail
AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$(ls -d "$AGENT_DIR"/*.app 2>/dev/null | head -1 || true)"
RUNTIME_DIR="$APP_DIR/Contents/Resources/brickery-runtime"
if [ -z "$APP_DIR" ] || [ ! -d "$RUNTIME_DIR" ]; then
  echo "[{meta.name}] 错误：未找到打包运行时（$RUNTIME_DIR）" >&2
  exit 1
fi
echo "[{meta.name}] 启动（独立运行时）"
export PYTHONPATH="$RUNTIME_DIR"
exec python3 -m brickery.runtime.ipc --home "$AGENT_DIR"
"""
    run_sh = out_dir / "run.sh"
    run_sh.write_text(script, encoding="utf-8")
    run_sh.chmod(run_sh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _bundle_app(out_dir: Path, meta: ProduceMeta, *, port: int = 18765) -> None:
    """生成 macOS .app（可打包 .dmg 分发）。

    结构：Contents/Info.plist + MacOS/launcher + Resources/brickery-runtime/
    （打包进来的独立运行时 B1–B5 全部）+ Resources/agent.json + Resources/bricks/。
    完整签名 / 公证 / dmg 打包留待后续阶段。
    """
    app_dir = out_dir / f"{meta.name}.app"
    contents = app_dir / "Contents"
    macos = contents / "MacOS"
    resources = contents / "Resources"
    macos.mkdir(parents=True, exist_ok=True)
    resources.mkdir(parents=True, exist_ok=True)

    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>{meta.name}</string>
    <key>CFBundleDisplayName</key><string>{meta.name}</string>
    <key>CFBundleIdentifier</key><string>dev.brickery.{meta.name}</string>
    <key>CFBundleVersion</key><string>{meta.version}</string>
    <key>CFBundleShortVersionString</key><string>{meta.version}</string>
    <key>CFBundleExecutable</key><string>launcher</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>LSMinimumSystemVersion</key><string>12.0</string>
</dict>
</plist>
"""
    (contents / "Info.plist").write_text(plist, encoding="utf-8")

    launcher = f"""#!/bin/bash
# {meta.name} launcher —— 自包含启动：数据目录在 ~/Library/Application Support/{meta.name}/
# 不依赖包外任何文件（run.sh 仅开发态使用）。
set -euo pipefail
# launcher 位于 Contents/MacOS/，上两级即 .app 根目录
APP_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
RESOURCES="$APP_DIR/Contents/Resources"
RUNTIME_DIR="$RESOURCES/brickery-runtime"
DATA_DIR="$HOME/Library/Application Support/{meta.name}"
STATUS_PAGE="$RESOURCES/status.html"
if [ ! -d "$RUNTIME_DIR" ]; then
  echo "[{meta.name}] 错误：未找到打包运行时（$RUNTIME_DIR）" >&2
  exit 1
fi
mkdir -p "$DATA_DIR"
# 已在运行（端口占用）→ 直接打开状态页，不重复启动
if lsof -iTCP:{port} -sTCP:LISTEN >/dev/null 2>&1; then
  open "$STATUS_PAGE" 2>/dev/null || true
  exit 0
fi
export PYTHONPATH="$RUNTIME_DIR"
# launcher 只是启动器：IPC 作为独立服务存活，不随 launcher 退出自杀
# （ipc.py 的父进程 watchdog 在 BRICKERY_NO_WATCHDOG=1 时跳过）
export BRICKERY_NO_WATCHDOG=1
# 后台启动 IPC 服务，launcher 立即退出（避免 Dock 图标一直弹跳）
nohup python3 -m brickery.runtime.ipc --home "$DATA_DIR" --app-resources "$RESOURCES" \\
  > "$DATA_DIR/ipc.log" 2>&1 &
# 等 IPC 起来后打开状态页，给用户可见反馈
sleep 2
open "$STATUS_PAGE" 2>/dev/null || true
exit 0
"""
    launcher_path = macos / "launcher"
    launcher_path.write_text(launcher, encoding="utf-8")
    launcher_path.chmod(launcher_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # 状态页：双击后浏览器打开，给用户可见反馈（工坊蓝图风，与 web/index.html 一致）
    (resources / "status.html").write_text(
        _status_page(meta.name, meta.version, port), encoding="utf-8")

    # 打包独立运行时：复制 brickery 包（B1–B5 全部）进 Resources/brickery-runtime/
    _bundle_runtime(resources)

    # 复制装配清单与积木快照进 Resources/
    shutil.copy2(out_dir / "agent.json", resources / "agent.json")
    shutil.copytree(out_dir / "bricks", resources / "bricks")


def _status_page(name: str, version: str, port: int) -> str:
    """生成双击启动后的状态页（工坊蓝图风，与 web/index.html 一致）。

    用占位符替换（非 f-string），避免 CSS 花括号转义。
    """
    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>__NAME__ · 运行中</title>
<style>
  :root {
    --paper: #f2ecdf; --card: #faf6ec; --line: #d6cbb4; --line-strong: #b8a98c;
    --ink: #2b2620; --ink-soft: #6b6152; --ink-faint: #9a8f7c;
    --amber: #b45309; --vermilion: #a63a2a; --ok: #3f6b3f;
    --shadow: 0 1px 3px rgba(80,60,30,.12), 0 4px 14px rgba(80,60,30,.08);
    --radius: 10px;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, "PingFang SC", "Songti SC", sans-serif;
    background:
      radial-gradient(circle at 12% 8%, rgba(180,120,40,.06), transparent 40%),
      radial-gradient(circle at 88% 90%, rgba(166,58,42,.05), transparent 40%),
      var(--paper);
    color: var(--ink); font-size: 14px; line-height: 1.6;
    min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px;
  }
  .card {
    background: var(--card); border: 1px solid var(--line);
    border-radius: var(--radius); box-shadow: var(--shadow);
    max-width: 520px; width: 100%; padding: 32px 36px;
  }
  .badge {
    display: inline-flex; align-items: center; gap: 8px;
    background: #eef3ea; color: var(--ok); border: 1px solid #c9d8c4;
    border-radius: 20px; padding: 4px 14px; font-size: 13px; font-weight: 700;
  }
  .badge .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--ok); }
  h1 { font-size: 24px; font-weight: 800; margin: 18px 0 4px; letter-spacing: .5px; }
  h1 .accent { color: var(--vermilion); }
  .ver { color: var(--ink-faint); font-size: 13px; margin-bottom: 22px; }
  .row {
    display: flex; justify-content: space-between; gap: 16px;
    padding: 10px 0; border-top: 1px dashed var(--line); font-size: 13px;
  }
  .row .k { color: var(--ink-soft); white-space: nowrap; }
  .row .v { color: var(--ink); font-family: ui-monospace, Menlo, monospace; word-break: break-all; text-align: right; }
  .tip {
    margin-top: 20px; padding: 12px 14px; background: var(--paper);
    border: 1px solid var(--line); border-radius: 8px;
    color: var(--ink-soft); font-size: 12.5px;
  }
  .tip code {
    font-family: ui-monospace, Menlo, monospace; background: #e9e1cf;
    padding: 1px 6px; border-radius: 4px; color: var(--ink);
  }
</style>
</head>
<body>
  <div class="card">
    <span class="badge"><span class="dot"></span>运行中</span>
    <h1>__NAME__ <span class="accent">·</span> 已启动</h1>
    <div class="ver">版本 __VERSION__ · Brickery 独立 agent</div>
    <div class="row"><span class="k">IPC 端口</span><span class="v">127.0.0.1:__PORT__</span></div>
    <div class="row"><span class="k">数据目录</span><span class="v">__DATA_DIR__</span></div>
    <div class="row"><span class="k">运行日志</span><span class="v">ipc.log</span></div>
    <div class="tip">
      这是一个后台服务 agent，供宿主或程序通过本地 IPC 调用。
      如需停止，在终端执行：<code>pkill -f "brickery.runtime.ipc --home __DATA_DIR__"</code>
    </div>
  </div>
</body>
</html>
"""
    return (html
            .replace("__NAME__", name)
            .replace("__VERSION__", version)
            .replace("__PORT__", str(port))
            .replace("__DATA_DIR__", f"~/Library/Application Support/{name}"))


def _bundle_runtime(resources: Path) -> None:
    """把 brickery 包（B1–B5 全部）复制进 Resources/brickery-runtime/。

    底座来源优先级（用户拍板）：GitHub 拉下的 `~/.brickery/base/brickery` 优先
    （最终用户本地无底座），本地仓库仅作开发兜底。
    排除 __pycache__ / tests / fixtures / web（运行时不需要）。
    """
    base_src = Path.home() / ".brickery" / "base" / "brickery"
    local_src = Path(__file__).resolve().parent  # brickery/ 包目录
    src = base_src if base_src.is_dir() else local_src
    dst = resources / "brickery-runtime" / "brickery"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests", "fixtures", "web"),
    )


def list_agents(agents_root: Optional[Path] = None) -> List[dict]:
    """列出已产出的 agent 包。"""
    root = agents_root or DEFAULT_AGENTS_ROOT
    if not root.exists():
        return []
    out: List[dict] = []
    for d in sorted(root.iterdir()):
        if not d.is_dir():
            continue
        manifest_path = d / "agent.json"
        if not manifest_path.exists():
            continue
        try:
            m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append({
            "name": m.get("name", d.name),
            "version": m.get("version", ""),
            "description": m.get("description", ""),
            "runtime": m.get("runtime", ""),
            "produced_at": m.get("produced_at", ""),
            "bricks": len(m.get("assembly", {}).get("order", [])),
            "path": str(d),
        })
    return out
