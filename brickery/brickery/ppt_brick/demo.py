"""渲染底座最小示例：1 标题文本 + 1 矩形 + 1 正文文本 -> demo_ppt.pptx。

运行：
    python demo.py
产出：
    /Users/suipu/Dev/brickery/output/demo_ppt.pptx
"""

from __future__ import annotations

import os
import sys

# 保证以脚本方式直接运行时也能 import 本包（包父目录：dest 最外层）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ppt_brick import (
    Box,
    BoxType,
    HAlign,
    ShapeType,
    Slide,
    TextContent,
    ShapeContent,
    render_pptx,
    text_box,
    shape_box,
    VAlign,
)

OUT_PATH = "/Users/suipu/Dev/brickery/output/demo_ppt.pptx"


def build_demo_slide() -> Slide:
    """构建最小示例：16:9 画布，标题 + 强调矩形 + 正文。"""
    slide = Slide(width=13.333, height=7.5, bg="FFFFFF")

    # 1) 标题文本（大字号、粗体、居中）
    slide.add(
        text_box(
            1.2, 0.9, 10.933, 1.6,
            "Hello, PPT Brick",
            font_size=44.0,
            bold=True,
            color="1F3864",
            align=HAlign.CENTER,
            valign=VAlign.MIDDLE,
            name="title",
        )
    )

    # 2) 强调矩形（标题下横条，原生 autoshape，双击可改样式）
    slide.add(
        shape_box(
            1.2, 2.6, 10.933, 0.12,
            shape_type=ShapeType.RECT,
            fill="2E75B6",
            line_color=None,
            name="accent_bar",
        )
    )

    # 3) 正文文本（多行段落）
    body = (
        "这是渲染底座的最小可跑闭环。\n"
        "中间态（SVG-like 绝对坐标，本包 model.py）\n"
        "渲染（本包 render.py）输出为原生 PPTX 元素：\n"
        "文本框 / 矩形 / 图片均为可双击编辑的原生对象，而非截图。"
    )
    slide.add(
        text_box(
            1.2, 3.1, 10.933, 3.0,
            body,
            font_size=20.0,
            color="333333",
            align=HAlign.LEFT,
            valign=VAlign.TOP,
            name="body",
        )
    )

    return slide


def main() -> str:
    slides = [build_demo_slide()]
    path = render_pptx(slides, OUT_PATH)
    print(f"demo pptx 已生成: {path} ({os.path.getsize(path)} bytes)")
    return path


if __name__ == "__main__":
    main()
