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
            *, overwrite: bool = False) -> Path:
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
    _bundle_app(out_dir, meta)

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


def _bundle_app(out_dir: Path, meta: ProduceMeta) -> None:
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
# {meta.name} launcher —— 从 .app 内定位 agent 目录并启动
set -euo pipefail
# launcher 位于 Contents/MacOS/，上三级即 agent 目录（run.sh 所在处）
AGENT_DIR="$(cd "$(dirname "$0")/../../.." && pwd)"
exec "$AGENT_DIR/run.sh"
"""
    launcher_path = macos / "launcher"
    launcher_path.write_text(launcher, encoding="utf-8")
    launcher_path.chmod(launcher_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # 打包独立运行时：复制 brickery 包（B1–B5 全部）进 Resources/brickery-runtime/
    _bundle_runtime(resources)

    # 复制装配清单与积木快照进 Resources/
    shutil.copy2(out_dir / "agent.json", resources / "agent.json")
    shutil.copytree(out_dir / "bricks", resources / "bricks")


def _bundle_runtime(resources: Path) -> None:
    """把 brickery 包（B1–B5 全部）复制进 Resources/brickery-runtime/。

    排除 __pycache__ / tests / fixtures / web（运行时不需要）。
    """
    src = Path(__file__).resolve().parent  # brickery/ 包目录
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
