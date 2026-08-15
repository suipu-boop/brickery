"""DMG 安装包生成（P4 能力，供 web 工作台一键出包）。

需要 dmgbuild + PIL（受管 venv 已装）。web server 用系统 python3（无此依赖），
通过 subprocess 调用本模块：

    python3 -m brickery.dmg --agent <agent_dir> --out <out.dmg> \
        --name <name> --version <version> --port <port>

布局：<name>.app + Applications 软链 + 隐藏 .docs（自动生成的安装引导）。
背景图 720x500 与 window_rect 1:1。dmgbuild / PIL 延迟导入，系统 python3
import 本模块不报错。
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# 背景图 / 安装引导的默认端口（产出 agent 的 IPC 监听端口）
DEFAULT_PORT = 18765


def _font(path, size, index=0):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(path, size, index=index)
    except Exception:
        return ImageFont.load_default()


def _gen_background(out: Path, name: str, port: int) -> None:
    """生成 DMG 安装窗口背景图（720x500，浅色保证 Finder 标签可读）。"""
    from PIL import Image, ImageDraw, ImageFilter

    W, H = 720, 500
    CJK_FONT = "/System/Library/Fonts/STHeiti Light.ttc"
    LAT_FONT = "/System/Library/Fonts/HelveticaNeue.ttc"
    APP_CX, APP_CY = 160, 255
    APP_LINK_CX, APP_LINK_CY = 560, 255

    def vgrad(top, bottom):
        base = Image.new("RGBA", (W, H), top)
        d = ImageDraw.Draw(base)
        for y in range(H):
            t = y / (H - 1)
            c = tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,)
            d.line([(0, y), (W, y)], fill=c)
        return base

    bg = vgrad((247, 248, 251), (234, 237, 243))
    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse([W // 2 - 220, -200, W // 2 + 220, 170], fill=(99, 122, 255, 42))
    glow = glow.filter(ImageFilter.GaussianBlur(70))
    bg = Image.alpha_composite(bg, glow)
    d = ImageDraw.Draw(bg)

    d.text((W // 2, 60), name, font=_font(LAT_FONT, 44), fill=(26, 30, 40), anchor="mm")
    d.text((W // 2, 108), "Brickery 独立 AI Agent  ·  双击即跑",
           font=_font(CJK_FONT, 17), fill=(88, 94, 108), anchor="mm")

    # 中部箭头：app -> Applications
    arrow_y = APP_CY
    ax1, ax2 = 300, 420
    d.line([(ax1, arrow_y), (ax2, arrow_y)], fill=(54, 82, 214), width=6, joint="curve")
    ah = 20
    d.polygon([(ax2 + ah, arrow_y), (ax2 - 8, arrow_y - ah), (ax2 - 8, arrow_y + ah)],
              fill=(54, 82, 214))

    # 底部安装步骤引导
    d.text((W // 2, 380), f"第 1 步：将 {name} 拖到「应用程序」文件夹",
           font=_font(CJK_FONT, 16), fill=(40, 44, 56), anchor="mm")
    d.text((W // 2, 410), f"第 2 步：双击运行，服务自动启动（端口 {port}）",
           font=_font(CJK_FONT, 16), fill=(40, 44, 56), anchor="mm")
    d.text((W // 2, 444), "Step 1: Drag to Applications   Step 2: Double-click to run",
           font=_font(LAT_FONT, 13), fill=(110, 116, 130), anchor="mm")

    out.parent.mkdir(parents=True, exist_ok=True)
    bg.save(out)


def _gen_readme(out: Path, name: str, version: str, port: int) -> None:
    """生成 .docs/README.html 安装引导（自包含，不依赖外部文件）。"""
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{name} 安装引导</title>
<style>
  body {{ font-family: -apple-system, "PingFang SC", "Helvetica Neue", sans-serif;
         max-width: 720px; margin: 40px auto; padding: 0 20px; color: #1a1e28; line-height: 1.7; }}
  h1 {{ font-size: 26px; border-bottom: 2px solid #3652d6; padding-bottom: 10px; }}
  h2 {{ font-size: 19px; margin-top: 28px; color: #3652d6; }}
  .step {{ background: #f4f6fb; border-left: 4px solid #3652d6; padding: 12px 16px;
          border-radius: 6px; margin: 12px 0; }}
  code {{ background: #eef0f5; padding: 2px 6px; border-radius: 4px; font-size: 14px; }}
  .warn {{ background: #fff7e6; border-left: 4px solid #f5a623; padding: 12px 16px;
          border-radius: 6px; margin: 12px 0; }}
  table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
  th, td {{ border: 1px solid #dde1ea; padding: 8px 12px; text-align: left; font-size: 14px; }}
  th {{ background: #f4f6fb; }}
</style>
</head>
<body>
<h1>{name} 安装引导</h1>
<p>Brickery 产出的独立 AI Agent，自包含运行时，双击即跑，无需额外安装依赖。</p>

<h2>安装步骤（一步一步）</h2>
<div class="step"><b>第 1 步</b>：将 <code>{name}.app</code> 拖入「应用程序」文件夹（或任意位置）。</div>
<div class="step"><b>第 2 步</b>：双击 <code>{name}.app</code> 启动。首次启动若提示"无法打开"，请在「系统设置 → 隐私与安全性」中点击"仍要打开"。</div>
<div class="step"><b>第 3 步</b>：服务启动后监听本机 <code>127.0.0.1:{port}</code>，可通过 IPC 协议连接使用。</div>

<h2>验证是否启动成功</h2>
<p>打开「终端」执行：</p>
<pre><code>lsof -iTCP:{port} -sTCP:LISTEN</code></pre>
<p>看到 <code>python3 ... brickery.runtime.ipc</code> 监听即表示启动成功。</p>

<h2>配置推理后端（重要）</h2>
<div class="warn"><b>注意</b>：本包默认未配置推理后端。首次对话前需在
<code>{name}.app/Contents/Resources/config.json</code> 中填写引擎配置，
否则对话会返回"没有可用的推理后端"。</div>
<p>config.json 引擎配置示例（OpenAI 兼容 API）：</p>
<pre><code>{{
  "engine": {{
    "backend": "api",
    "api_url": "https://open.bigmodel.cn/api/paas/v4/",
    "api_key": "你的 API Key",
    "api_model": "glm-4-flash"
  }}
}}</code></pre>
<p>或使用本地 GGUF 模型：</p>
<pre><code>{{
  "engine": {{
    "backend": "local",
    "local_model": "/绝对路径/模型.gguf"
  }}
}}</code></pre>

<h2>常见问题</h2>
<table>
  <tr><th>现象</th><th>处理</th></tr>
  <tr><td>双击无反应</td><td>检查是否被 Gatekeeper 拦截，在"隐私与安全性"中允许打开</td></tr>
  <tr><td>对话返回"没有可用的推理后端"</td><td>按上文配置 config.json 引擎</td></tr>
  <tr><td>端口被占用</td><td>先结束旧进程再启动：<code>pkill -f brickery.runtime.ipc</code></td></tr>
</table>

<p style="margin-top:40px; color:#888; font-size:13px;">由 Brickery 工坊产出 · {name} v{version}</p>
</body>
</html>
"""
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")


def build_dmg(agent_dir, out_dmg, *, name, version="0.1.0", port=DEFAULT_PORT) -> str:
    """把已产出 agent 的 .app 打包成 dmg 安装包，返回 dmg 路径。

    - agent_dir：产出目录（~/.brickery/agents/<name>），内含 <name>.app
    - out_dmg：输出 dmg 路径（如 ~/Desktop/<name>-<version>.dmg）
    """
    import dmgbuild  # 延迟导入：仅受管 venv 运行本模块时可用

    agent_dir = Path(agent_dir)
    out_dmg = Path(out_dmg)
    if out_dmg.parent and not out_dmg.parent.exists():
        out_dmg.parent.mkdir(parents=True, exist_ok=True)
    app_path = agent_dir / f"{name}.app"
    if not app_path.is_dir():
        apps = list(agent_dir.glob("*.app"))
        if not apps:
            raise FileNotFoundError(f"未找到 .app：{agent_dir}")
        app_path = apps[0]
        name = app_path.name[:-4]

    stage = tempfile.mkdtemp(prefix=f"{name}_dmg_stage_")
    try:
        # 1) 背景图
        bg_path = Path(stage) / "dmg_background.png"
        _gen_background(bg_path, name, port)

        # 2) 随包文档 -> .docs/（之后 hide）
        docs_stage = Path(stage) / ".docs"
        _gen_readme(docs_stage / "README.html", name, version, port)

        # 3) 计算体积（.app 体积 + 20MB 余量）
        app_mb = int(subprocess.check_output(["du", "-sm", str(app_path)]).split()[0]) + 20
        size = f"{app_mb}M"

        # 4) dmgbuild 出包
        settings = {
            "format": "UDZO",
            "size": size,
            "files": [str(app_path), str(docs_stage)],
            "symlinks": {"Applications": "/Applications"},
            "hide": [".docs"],
            "background": str(bg_path),
            "icon_locations": {
                f"{name}.app": (160, 255),
                "Applications": (560, 255),
            },
            "window_rect": ((100, 100), (720, 500)),
            "icon_size": 128,
            "text_size": 16,
            "show_sidebar": False,
            "show_status_bar": False,
            "show_toolbar": False,
            "show_pathbar": False,
            "show_tab_view": False,
        }
        if out_dmg.exists():
            out_dmg.unlink()
        dmgbuild.build_dmg(str(out_dmg), f"{name}-{version}", settings=settings)
        return str(out_dmg)
    finally:
        shutil.rmtree(stage, ignore_errors=True)


def main() -> None:
    ap = argparse.ArgumentParser(description="Brickery DMG 安装包生成")
    ap.add_argument("--agent", required=True, help="agent 产出目录")
    ap.add_argument("--out", required=True, help="输出 dmg 路径")
    ap.add_argument("--name", required=True, help="agent 名")
    ap.add_argument("--version", default="0.1.0")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    try:
        out = build_dmg(args.agent, args.out, name=args.name,
                        version=args.version, port=args.port)
    except Exception as e:  # noqa: BLE001
        print(f"DMG 生成失败：{e}", file=sys.stderr)
        sys.exit(1)
    print(f"DMG 已生成：{out}")


if __name__ == "__main__":
    main()
