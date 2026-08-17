#!/bin/bash
# 把积木平台（web 工作台）打包成独立 macOS app + DMG。
#
# 结构（对齐 produce.py 的 _bundle_app / _bundle_runtime / _bundle_embedded_python）：
#   <name>.app/Contents/Info.plist
#   <name>.app/Contents/MacOS/BrickeryApp                    （编译自 app/ 的 Swift 壳）
#   <name>.app/Contents/Resources/brickery-runtime/brickery/ （brickery 包，含 web 子包）
#   <name>.app/Contents/Resources/brickery-runtime/web/index.html （工作台前端）
#   <name>.app/Contents/Resources/python/                    （内嵌 python）
#
# 说明：
#   - 与 produce.py 的 _bundle_runtime 不同，这里【保留 brickery/web 子包】，
#     因为工作台模式要跑 `python -m brickery.web.server`；前端 index.html 复制到
#     brickery-runtime/web/（server.py 的 _FRONTEND_DIR 解析为该目录）。
#   - Info.plist 的 CFBundleIdentifier 为 dev.brickery.workbench，main.swift 的
#     detectRunMode() 据此自动进入「积木工作台模式」（端口 8765）。
#   - DMG 复用 brickery/dmg.py（dmgbuild 驱动），通过受管 venv python 执行。
#
# 幂等：重复执行会先清掉旧的 .app / .dmg 再重建。
#
# 用法：
#   scripts/build_workbench_app.sh
# 可配置环境变量：
#   BRICKERY_WORKBENCH_NAME     app 名（默认 BrickeryWorkbench）
#   BRICKERY_WORKBENCH_VERSION  版本（默认 0.1.0）
#   BRICKERY_WORKBENCH_PORT     工作台端口（默认 8765）
#   BRICKERY_WORKBENCH_OUT      产出目录（默认 <repo>/output）
#   BRICKERY_DMG_PY             dmgbuild 受管 python（默认 ~/.workbuddy/...）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# ---- 可配置项 ----
APP_NAME="${BRICKERY_WORKBENCH_NAME:-BrickeryWorkbench}"
VERSION="${BRICKERY_WORKBENCH_VERSION:-0.1.0}"
PORT="${BRICKERY_WORKBENCH_PORT:-8765}"
OUT_DIR="${BRICKERY_WORKBENCH_OUT:-$REPO_ROOT/output}"
DMG_PY="${BRICKERY_DMG_PY:-/Users/suipu/.workbuddy/binaries/python/envs/default/bin/python3}"

APP_SRC="$REPO_ROOT/app"
RUNTIME_SRC="$REPO_ROOT/brickery"
EMBEDDED_PY="$REPO_ROOT/temp/python"
FRONTEND_SRC="$REPO_ROOT/web/index.html"

APP_DIR="$OUT_DIR/$APP_NAME.app"
DMG_PATH="$OUT_DIR/$APP_NAME-$VERSION.dmg"
STAGE_DIR="$OUT_DIR/.dmg_stage_$APP_NAME"

# ---- 前置检查 ----
[ -f "$APP_SRC/Package.swift" ] || { echo "错误：未找到 Swift 壳工程 $APP_SRC" >&2; exit 1; }
[ -d "$RUNTIME_SRC" ] || { echo "错误：未找到 brickery 包 $RUNTIME_SRC" >&2; exit 1; }
[ -f "$FRONTEND_SRC" ] || { echo "错误：未找到工作台前端 $FRONTEND_SRC" >&2; exit 1; }
[ -x "$EMBEDDED_PY/bin/python3" ] || {
    echo "错误：未找到内嵌 python $EMBEDDED_PY/bin/python3（请先按 specs/p4-packaging.md 准备）" >&2
    exit 1
}

# ---- 幂等清理 ----
rm -rf "$APP_DIR" "$STAGE_DIR"
rm -f "$DMG_PATH"
mkdir -p "$OUT_DIR"

# ---- 1) 编译 Swift 壳 ----
echo "==> 编译 Swift 壳（release）"
swift build -c release --package-path "$APP_SRC"
BINARY="$APP_SRC/.build/release/BrickeryApp"
[ -x "$BINARY" ] || { echo "错误：Swift 壳产物缺失 $BINARY" >&2; exit 1; }

# ---- 2) 组装 .app 骨架 ----
echo "==> 组装 $APP_DIR"
CONTENTS="$APP_DIR/Contents"
MACOS="$CONTENTS/MacOS"
RESOURCES="$CONTENTS/Resources"
mkdir -p "$MACOS" "$RESOURCES"

cat > "$CONTENTS/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>$APP_NAME</string>
    <key>CFBundleDisplayName</key><string>$APP_NAME</string>
    <key>CFBundleIdentifier</key><string>dev.brickery.workbench</string>
    <key>CFBundleVersion</key><string>$VERSION</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleExecutable</key><string>BrickeryApp</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>LSMinimumSystemVersion</key><string>12.0</string>
    <key>NSAppTransportSecurity</key>
    <dict>
        <key>NSAllowsLocalNetworking</key><true/>
    </dict>
</dict>
</plist>
PLIST

cp "$BINARY" "$MACOS/BrickeryApp"
chmod +x "$MACOS/BrickeryApp"

# ---- 3) 打包 brickery 运行时（保留 web 子包，供工作台模式运行） ----
echo "==> 打包 brickery 运行时"
RUNTIME_DIR="$RESOURCES/brickery-runtime"
mkdir -p "$RUNTIME_DIR"
# 原生壳 Swift 工程（produce.py 产出 agent 时需从 brickery-runtime/app 编译）
rsync -a --exclude ".build" "$APP_SRC/" "$RUNTIME_DIR/app/"
rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude 'tests' \
      --exclude 'fixtures' \
      "$RUNTIME_SRC/" "$RUNTIME_DIR/brickery/"

# 工作台前端：server.py 的 _FRONTEND_DIR 解析为 brickery-runtime/web/
mkdir -p "$RUNTIME_DIR/web"
cp "$FRONTEND_SRC" "$RUNTIME_DIR/web/index.html"

# ---- 4) 打包内嵌 python（对齐 _bundle_embedded_python） ----
echo "==> 打包内嵌 python"
rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '*.pyo' \
      "$EMBEDDED_PY/" "$RESOURCES/python/"
# produce.py 产出 agent 时从 brickery-runtime/temp/python 解析内嵌 python
mkdir -p "$RUNTIME_DIR/temp"
rsync -a --exclude '__pycache__' --exclude '*.pyc' --exclude '*.pyo' \
      "$EMBEDDED_PY/" "$RUNTIME_DIR/temp/python/"

# ---- 5) 生成 DMG（复用 brickery/dmg.py 的 dmgbuild 链路） ----
echo "==> 生成 DMG"
[ -x "$DMG_PY" ] || DMG_PY="$(command -v python3 || echo /usr/bin/python3)"
mkdir -p "$STAGE_DIR"
cp -R "$APP_DIR" "$STAGE_DIR/"
# 隔离外部 PYTHONPATH：WorkBuddy/Hermes 会注入其 site-packages（含损坏的 PIL），
# 令受管 venv 错载而 DMG 背景图生成崩溃。受管 venv 自带完好的 dmgbuild/PIL。
PYTHONPATH= "$DMG_PY" "$REPO_ROOT/brickery/dmg.py" \
    --agent "$STAGE_DIR" --out "$DMG_PATH" \
    --name "$APP_NAME" --version "$VERSION" --port "$PORT"
rm -rf "$STAGE_DIR"

echo "==> 完成"
echo "  .app: $APP_DIR"
echo "  .dmg: $DMG_PATH"
