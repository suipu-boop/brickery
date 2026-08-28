"""主题 + 版式演示：一套品牌色 -> 多页 deck -> demo_themed.pptx。

覆盖版式：cover / toc / section / content（每页均为原生 PPT 元素）。

运行（需装有 python-pptx 的解释器，如 brickery/temp/python）：
    python demo_theme.py
产出：
    /Users/suipu/Dev/brickery/output/demo_themed.pptx
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ppt_brick import render_pptx  # noqa: E402
from ppt_brick.registry import REGISTRY  # noqa: E402
from ppt_brick.theme import derive_tokens, validate_contrast  # noqa: E402

OUT_PATH = "/Users/suipu/Dev/brickery/output/demo_themed.pptx"
BRAND = "#1D4ED8"  # 品牌主色（示例：靛蓝）


def build_deck():
    """用一套品牌色 + 4 种版式构建整份 deck（中间态 Slide 列表）。"""
    tokens = derive_tokens(BRAND)

    cover = REGISTRY.get("cover").render(tokens, {
        "title": "OKLCH 主题系统与版式注册表",
        "subtitle": "单一品牌色自动派生完整设计系统 · 衔接渲染底座",
        "date": "2026-08-27",
        "author": "brickery · PPT Brick",
    })

    toc = REGISTRY.get("toc").render(tokens, {
        "title": "Agenda",
        "items": [
            "Token 引擎：OKLCH 派生全调色板",
            "版式注册表：Schema/Component 双层契约",
            "渲染底座闭环：中间态 -> 原生 PPTX",
            "下一块：素材通道与语气档扩容",
        ],
    })

    section = REGISTRY.get("section").render(tokens, {
        "number": "01",
        "title": "设计系统",
        "subtitle": "primitives → semantic → variant bundles",
    })

    content = REGISTRY.get("content").render(tokens, {
        "title": "Token 引擎的一分钟速览",
        "items": [
            "输入单一品牌色 hex，在 OKLCH 色彩空间做确定性派生",
            "primitive 层：品牌色 + tints/shades 各 6 档 + 图表系列色",
            "semantic 层：明暗两套（text/background/accent/gradient/语义色）",
            "WCAG-AA 对比度门禁：text 与 background 组合自动达标",
            "variant 层：通用 / 咨询 / 投行三种语气档整包切换",
        ],
        "note": "颜色数学纯 stdlib，无随机性，同一输入永远同一输出。",
    })

    content2 = REGISTRY.get("content").render(tokens, {
        "title": "版式注册表（Component 层）",
        "items": [
            "每个版式是纯函数：(tokens, data) -> list[Slide]",
            "只产中间态 model 对象，不 import pptx、不碰像素",
            "Schema 层 meta() 给 AI 语义、default() 兜底",
            "预置 cover / toc / section / content 四种版式",
            "AI 只填数据字段，不写坐标 —— 布局全部收敛在注册表内",
        ],
        "note": "demo_themed.pptx 中所有元素均为 PowerPoint 原生对象。",
    })

    return tokens, cover + toc + section + content + content2


def main() -> str:
    tokens, slides = build_deck()
    checks = [c for c in validate_contrast(tokens) if not c["ok"]]
    path = render_pptx(slides, OUT_PATH)
    print(f"decks pages: {len(slides)}")
    print(f"contrast gate: {'PASS' if not checks else 'FAIL ' + str(checks)}")
    print(f"demo_themed.pptx 已生成: {path} ({os.path.getsize(path)} bytes)")
    return path


if __name__ == "__main__":
    main()
