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
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from .assembler import AssemblyPlan, AssemblyError

# 产出根目录：写死为桌面（用户要求产出的 agent 安装包都放桌面）
DEFAULT_AGENTS_ROOT = Path("/Users/suipu/Desktop")


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


# 积木分层（用户拍板）：预置/按需=可打包。
# engine（engine-local/engine-api）已从积木清单移除，作为底座默认能力，
# 引擎类型由设置页 config 决定，组装时无需选择。
BRICK_TIERS = {
    "preset": ["docwrite", "scheduler", "rules", "doctor", "backup-restore",
               "meeting-minutes", "visualize"],
    "ondemand": ["feishu", "telegram", "ax", "browser", "high-config-doc",
                 "code-quality-chain", "multi-agent", "mcp", "memory-cabinet", "vault"],
}


def _bricks_for_mode(mode: str) -> List[str]:
    """按出包模式返回积木集合（内置写死内核，不打包）。"""
    if mode == "full":
        return BRICK_TIERS["preset"] + BRICK_TIERS["ondemand"]
    if mode == "base":
        return list(BRICK_TIERS["preset"])
    raise ProduceError(f"未知出包模式：{mode!r}（可选 full/base）")


def _is_builtin_brick(manifest: Path) -> bool:
    """brick.json 未声明二进制（binary_size 空/0 且无 binary_url）→ 内置积木。

    内置积木随底座分发（builtin_skills 通道），不写入 bricks/ 快照；
    声明二进制的大引擎/第三方留待选区/市场按需下载。动态计算，不写死 id。
    """
    try:
        raw = json.loads(manifest.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    return not (raw.get("binary_size") or raw.get("binary_url"))


def _brick_to_skill(manifest: Path) -> dict:
    """brick.json → runtime skill.json（source 强制 builtin，随包只读分发）。"""
    raw = json.loads(manifest.read_text(encoding="utf-8"))
    raw["source"] = "builtin"
    raw.pop("binary_url", None)
    raw.pop("binary_size", None)
    raw.pop("binary_sha256", None)
    return raw


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def produce(plan: AssemblyPlan, vault_root: str, meta: ProduceMeta,
            agents_root: Optional[Path] = None,
            *, overwrite: bool = False, port: int = 18765,
            mode: Optional[str] = None) -> Path:
    """把组装方案固化成独立 agent 包，返回产出目录。

    - 校验：agent 名合法、不重名（除非 overwrite）
    - 固化：agent.json + bricks/ 快照 + run.sh
    - 打包：生成 <name>.app 骨架（macOS）
    - mode：None=按 plan.order；"full"=预置+按需全打包；"base"=仅预置
    """
    name = meta.name.strip()
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise ProduceError(f"非法 agent 名：{name!r}")

    root = agents_root or DEFAULT_AGENTS_ROOT
    out_dir = root / name
    if out_dir.exists() and not overwrite:
        raise ProduceError(f"agent 已存在：{out_dir}（用 overwrite=True 覆盖）")

    order = _bricks_for_mode(mode) if mode else list(plan.order)
    vault = Path(vault_root)
    # 1) 校验选中积木的 brick.json 都在
    for brick_name in order:
        manifest = _find_manifest(vault, brick_name)
        if manifest is None:
            raise ProduceError(f"积木 {brick_name} 的 brick.json 缺失")

    # 2) 固化目录
    if out_dir.exists():
        shutil.rmtree(out_dir)
    bricks_dir = out_dir / "bricks"
    bricks_dir.mkdir(parents=True)
    builtin_skills_dir = out_dir / "builtin_skills"

    # 3) 复制积木快照 / 生成内置技能
    #    内置积木（binary_size 空/0）→ builtin_skills/<name>/（source=builtin，
    #    随底座分发，运行时 load_builtin_skills 加载，不写用户文件）；
    #    非内置积木 → bricks/ 快照（自包含复制整个目录，含实现文件；其余单文件快照）。
    #    内置积木开箱即用：无论用户是否选择，全部扫描 vault 内置进底座，零选择也能产出。
    builtin_names: List[str] = []
    for manifest in _vault_builtin_manifests(vault):
        brick_name = manifest.parent.name
        builtin_names.append(brick_name)
        dst_dir = builtin_skills_dir / brick_name
        shutil.copytree(
            manifest.parent, dst_dir,
            ignore=shutil.ignore_patterns("brick.json", "__pycache__", "*.pyc"))
        (dst_dir / "skill.json").write_text(
            json.dumps(_brick_to_skill(manifest), ensure_ascii=False, indent=2),
            encoding="utf-8")
    for brick_name in order:
        manifest = _find_manifest(vault, brick_name)
        if _is_builtin_brick(manifest):
            continue  # 已随底座内置，避免重复
        brick_files = (plan.files or {}).get(brick_name) or []
        if brick_files:
            src_dir = manifest.parent
            dst_dir = bricks_dir / brick_name
            shutil.copytree(
                src_dir, dst_dir,
                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
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
        "mode": mode or "plan",
        "assembly": {
            "order": order,
            "builtin": builtin_names,
            "resources_total": plan.resources_total if not mode else len(order),
        },
    }
    (out_dir / "agent.json").write_text(
        json.dumps(manifest_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # 5) 生成启动脚本
    _write_run_script(out_dir, meta)

    # 6) 打包 .app 骨架（macOS）
    _bundle_app(out_dir, meta, port=port, files=plan.files)

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


def _vault_builtin_manifests(vault: Path):
    """扫描 vault 全部 brick.json，产出内置积木的 manifest。

    内置积木开箱即用：无论用户是否选择，全部随底座分发（builtin_skills 通道）。
    以 index.json 声明的 path 为准，缺失时兜底扫描 bricks/*/brick.json，去重。
    """
    seen: set = set()
    index_path = vault / "index.json"
    if index_path.exists():
        try:
            raw = json.loads(index_path.read_text(encoding="utf-8"))
            for entry in raw.get("bricks") or []:
                name = entry.get("name")
                if not name or name in seen:
                    continue
                m = _find_manifest(vault, name)
                if m is not None and _is_builtin_brick(m):
                    seen.add(name)
                    yield m
        except (json.JSONDecodeError, OSError):
            pass
    for m in sorted((vault / "bricks").glob("*/brick.json")):
        name = m.parent.name
        if name in seen:
            continue
        if _is_builtin_brick(m):
            seen.add(name)
            yield m


def _write_run_script(out_dir: Path, meta: ProduceMeta) -> None:
    """生成 run.sh：拉起产出 agent 的独立运行时。

    只走 .app 内打包的 brickery-runtime（B1–B5 全部，双击即跑），
    不依赖任何宿主运行时命令（Shadeling 等）。
    """
    script = f"""#!/bin/bash
# {meta.name} —— 由 Brickery 产出的独立 agent
# 启动入口：.app 内打包的 brickery-runtime + 内嵌 python，不依赖宿主运行时
set -euo pipefail
AGENT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$(ls -d "$AGENT_DIR"/*.app 2>/dev/null | head -1 || true)"
RUNTIME_DIR="$APP_DIR/Contents/Resources/brickery-runtime"
PYTHON_BIN="$APP_DIR/Contents/Resources/python/bin/python3"
if [ -z "$APP_DIR" ] || [ ! -d "$RUNTIME_DIR" ]; then
  echo "[{meta.name}] 错误：未找到打包运行时（$RUNTIME_DIR）" >&2
  exit 1
fi
if [ ! -x "$PYTHON_BIN" ]; then
  echo "[{meta.name}] 错误：未找到内嵌 python（$PYTHON_BIN）" >&2
  exit 1
fi
echo "[{meta.name}] 启动（独立运行时 + 内嵌 python）"
export PYTHONPATH="$RUNTIME_DIR"
export BRICKERY_NO_WATCHDOG=1
# 后台拉起 IPC、安装引导与聊天界面（复用底座），按是否已配置打开对应页
nohup "$PYTHON_BIN" -m brickery.runtime.ipc --home "$AGENT_DIR" > "$AGENT_DIR/ipc.log" 2>&1 &
BRICKERY_HOME="$AGENT_DIR" nohup "$PYTHON_BIN" -m brickery.runtime.setup_wizard > "$AGENT_DIR/setup_wizard.log" 2>&1 &
BRICKERY_HOME="$AGENT_DIR" nohup "$PYTHON_BIN" -m brickery.runtime.chat_ui > "$AGENT_DIR/chat_ui.log" 2>&1 &
sleep 2
if [ -f "$AGENT_DIR/config.json" ]; then
  open "http://127.0.0.1:18767/" 2>/dev/null || true
else
  open "http://127.0.0.1:18766/" 2>/dev/null || true
fi
"""
    run_sh = out_dir / "run.sh"
    run_sh.write_text(script, encoding="utf-8")
    run_sh.chmod(run_sh.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _bundle_app(out_dir: Path, meta: ProduceMeta, *, port: int = 18765,
                files: Optional[Dict[str, List[dict]]] = None) -> None:
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
    <key>CFBundleExecutable</key><string>BrickeryApp</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>LSMinimumSystemVersion</key><string>12.0</string>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsLocalNetworking</key><true/>
    </dict>
</dict>
</plist>
"""
    (contents / "Info.plist").write_text(plist, encoding="utf-8")

    # 编译原生壳（NSApplication + WKWebView，内嵌渲染底座 web 界面）
    _bundle_native_shell(macos)

    # 状态页：双击后浏览器打开，给用户可见反馈（工坊蓝图风，与 web/index.html 一致）
    (resources / "status.html").write_text(
        _status_page(meta.name, meta.version, port), encoding="utf-8")

    # 打包独立运行时：复制 brickery 包（B1–B5 全部）进 Resources/brickery-runtime/
    _bundle_runtime(resources)

    # 复制装配清单与积木快照进 Resources/
    shutil.copy2(out_dir / "agent.json", resources / "agent.json")
    if (out_dir / "bricks").exists():
        shutil.copytree(out_dir / "bricks", resources / "bricks")

    # 内置技能随底座分发：打包进 brickery-runtime/brickery/builtin_skills/
    # （load_builtin_skills 打包态查找路径：ipc.py 的 parents[1]）
    if (out_dir / "builtin_skills").exists():
        builtin_dst = resources / "brickery-runtime" / "brickery" / "builtin_skills"
        shutil.copytree(out_dir / "builtin_skills", builtin_dst)

    # 自包含积木实现文件落盘进打包 runtime（connectors / bin 等）
    _install_brick_files(resources, files or {})


def _bundle_native_shell(macos: Path) -> None:
    """编译原生壳（NSApplication + WKWebView）并放入 Contents/MacOS/。

    壳源码在 brickery/app/（Swift Package，命名 BrickeryApp，无第三方依赖）。
    编译产物 .build/release/BrickeryApp 复制为 Contents/MacOS/BrickeryApp。
    """
    app_src = Path(__file__).resolve().parents[1] / "app"
    if not (app_src / "Package.swift").exists():
        raise ProduceError(f"未找到原生壳工程：{app_src}")
    build = subprocess.run(
        ["swift", "build", "-c", "release", "--package-path", str(app_src)],
        capture_output=True, text=True)
    if build.returncode != 0:
        raise ProduceError(f"原生壳编译失败：{build.stderr[-800:]}")
    binary = app_src / ".build" / "release" / "BrickeryApp"
    if not binary.exists():
        raise ProduceError(f"原生壳产物缺失：{binary}")
    dest = macos / "BrickeryApp"
    shutil.copy2(binary, dest)
    dest.chmod(dest.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


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
    /* —— 陶土工坊 · OKLCH 色板（与底座 chat_ui/setup_wizard 同源） —— */
    --bg: oklch(0.21 0.025 30);          /* 深陶土黑底 */
    --panel: oklch(0.26 0.028 32);       /* 卡片面 */
    --panel2: oklch(0.30 0.030 33);      /* 次级面 */
    --line: oklch(0.40 0.035 34);        /* 陶线 */
    --ink: oklch(0.97 0.015 75);         /* 暖白字 */
    --dim: oklch(0.72 0.03 70);          /* 陶灰 */
    --accent: oklch(0.66 0.16 42);       /* 陶土砖红主色 */
    --green: oklch(0.78 0.13 130);
    /* —— 字体体系 —— */
    --font-display: "Songti SC", "Songti TC", "New York", "Noto Serif SC", Georgia, serif;
    --font-body: -apple-system, BlinkMacSystemFont, "PingFang SC", "Hiragino Sans GB", "Noto Sans SC", "Segoe UI", sans-serif;
    /* —— 陶土材质（压印光影） —— */
    --inset-hi: inset 0 1px 0 oklch(1 0.02 60 / 0.05);
    --inset-lo: inset 0 -1px 0 oklch(0 0 0 / 0.08);
    --shadow-soft: 0 1px 2px oklch(0.12 0.02 30 / 0.2), 0 10px 28px oklch(0.12 0.02 30 / 0.14), var(--inset-hi), var(--inset-lo);
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: var(--font-body); color: var(--ink); font-size: 14px; line-height: 1.6;
    min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 24px;
    background-color: var(--bg);
    background-image: url("data:image/svg+xml;utf8,<svg xmlns='http://www.w3.org/2000/svg' width='160' height='160'><filter id='n'><feTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='2' stitchTiles='stitch'/><feColorMatrix type='saturate' values='0'/></filter><rect width='160' height='160' filter='url(%23n)' opacity='0.5'/></svg>");
  }
  .stamp {
    display: inline-flex; align-items: center; justify-content: center;
    width: 44px; height: 44px; margin-bottom: 16px;
    border: 1px solid var(--line); border-radius: 10px;
    background: var(--panel2); color: var(--accent);
    font-family: var(--font-display); font-size: 20px; font-weight: 800;
    box-shadow: var(--inset-hi), var(--inset-lo);
  }
  .card {
    background: var(--panel); border: 1px solid var(--line);
    border-radius: 14px; box-shadow: var(--shadow-soft);
    max-width: 520px; width: 100%; padding: 32px 36px;
  }
  .badge {
    display: inline-flex; align-items: center; gap: 8px;
    background: var(--accent); color: var(--bg); border-radius: 20px;
    padding: 4px 14px; font-size: 13px; font-weight: 700;
  }
  .badge .dot { width: 9px; height: 9px; border-radius: 50%; background: var(--bg); }
  h1 { font-family: var(--font-display); font-size: 26px; font-weight: 800; margin: 18px 0 4px; letter-spacing: .5px; }
  h1 .accent { color: var(--accent); }
  .ver { color: var(--dim); font-size: 13px; margin-bottom: 22px; }
  .row {
    display: flex; justify-content: space-between; gap: 16px;
    padding: 10px 0; border-top: 1px dashed var(--line); font-size: 13px;
  }
  .row .k { color: var(--dim); white-space: nowrap; }
  .row .v { color: var(--ink); font-family: ui-monospace, Menlo, monospace; word-break: break-all; text-align: right; }
  .tip {
    margin-top: 20px; padding: 12px 14px; background: var(--panel2);
    border: 1px solid var(--line); border-radius: 8px;
    color: var(--dim); font-size: 12.5px;
  }
  .tip code {
    font-family: ui-monospace, Menlo, monospace; background: oklch(0.18 0.022 30);
    padding: 1px 6px; border-radius: 4px; color: var(--ink);
  }
</style>
</head>
<body>
  <div class="card">
    <span class="stamp">砖</span>
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
    排除 __pycache__ / tests / web（运行时不需要）；市场组件固定从公网 GitHub
    拉取，不携带离线源（spec: skill-repo-github-only）；离线安装走「积木包导入」通道。
    """
    base_src = Path.home() / ".brickery" / "base" / "brickery"
    local_src = Path(__file__).resolve().parent  # brickery/ 包目录
    src = base_src if base_src.is_dir() else local_src
    dst = resources / "brickery-runtime" / "brickery"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "tests", "web"),
    )
    # P4：内嵌 python（含 llama_cpp）随包携带，目标机无系统 python3 也能启动
    _bundle_embedded_python(resources)
    # 写 runtime 版本标识（self_update 自检更新依据；打包源优先 base）
    _write_runtime_version(dst)


def _write_runtime_version(brick_pkg: Path) -> None:
    """在打包的 brickery 包内写入 version.json（self_update 的本地版本标识）。

    core_commit 取本地 brickery 仓库 HEAD（开发机打包时本地=远端 main 基线一致；
    base 拉取源同源于 GitHub，HEAD 语义相同）。取不到时置空串，不影响打包。
    """
    repo = Path(__file__).resolve().parents[1]
    sha = ""
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=5)
        if out.returncode == 0:
            sha = out.stdout.strip()
    except Exception:  # noqa: BLE001
        sha = ""
    v = {"schema": "brickery-version/v1", "core_commit": sha,
         "built_at": _now_iso(), "previous": ""}
    (brick_pkg / "version.json").write_text(
        json.dumps(v, ensure_ascii=False, indent=2), encoding="utf-8")


def _bundle_embedded_python(resources: Path) -> None:
    """把内嵌 python（python-build-standalone + llama_cpp）复制进 Resources/python/。

    来源：项目 temp/python（已用内嵌 pip 装好 llama-cpp-python==0.3.34 + numpy）。
    目标机无系统 python3 也能启动；运行时零 pip 安装、零编译。
    """
    src = Path(__file__).resolve().parents[1] / "temp" / "python"
    if not (src / "bin" / "python3").exists():
        raise ProduceError(
            f"未找到内嵌 python（{src}）。请先按 specs/p4-packaging.md §6 步骤 1-3 "
            "下载 python-build-standalone 并用内嵌 pip 安装 llama-cpp-python。")
    dst = resources / "python"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        src, dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo"),
    )


def _install_brick_files(resources: Path, files: Dict[str, List[dict]]) -> None:
    """把自包含积木的实现文件落盘进打包 runtime（幂等）。

    dest 语义：以 `runtime/` 开头 → 相对打包 brickery 包（brickery-runtime/
    brickery/，如 runtime/connectors/feishu.py，供 ipc 相对导入拉起）；其余 →
    相对打包 runtime 根（brickery-runtime/，如 bin/ax/axctl）。源文件在
    Resources/bricks/<name>/ 内（produce 第 3 步已复制整个积木目录）。保留可执行位。
    """
    runtime_root = resources / "brickery-runtime"
    brick_pkg = runtime_root / "brickery"
    for name, flist in (files or {}).items():
        # 内置积木实现文件随 builtin_skills 分发（bricks/ 快照不包含内置）
        brick_dir = resources / "bricks" / name
        if not brick_dir.exists():
            brick_dir = brick_pkg / "builtin_skills" / name
        for f in flist:
            src = brick_dir / str(f.get("src") or "")
            dest_raw = str(f.get("dest") or "")
            dest = (brick_pkg / dest_raw) if dest_raw.startswith("runtime/") \
                else (runtime_root / dest_raw)
            if not src.is_file():
                raise ProduceError(f"积木 {name} 实现文件缺失：{src}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            if src.stat().st_mode & 0o111:  # 保留可执行位
                dest.chmod(dest.stat().st_mode | 0o111)


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
