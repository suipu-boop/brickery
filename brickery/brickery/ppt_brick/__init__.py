"""ppt_brick —— PPT 生成积木的渲染底座（中间态 → 原生 PPTX）。

对应 specs/brick-ui-and-ppt-brick.md 决策点 D4：
渲染底座采用 SVG-like 绝对坐标中间态（本包 model.py），由 render.py
逐节点映射为 PowerPoint 原生 DrawingML 对象，绝不走 HTML→截图路线，
保证产物可在 PowerPoint/WPS 中双击编辑。

本包当前为「可独立运行的渲染底座最小闭环」：
- model.py : SVG-like 中间态数据模型
- render.py: 中间态 → 原生 PPTX 落盘（python-pptx）
- demo.py  : 程序化构建最小示例并渲染 demo_ppt.pptx
"""

from .model import (
    Slide,
    Box,
    BoxType,
    TextContent,
    ImageContent,
    ShapeContent,
    ShapeType,
    HAlign,
    VAlign,
    text_box,
    image_box,
    shape_box,
    emu,
    inches,
)
from .render import render_pptx

__all__ = [
    "Slide",
    "Box",
    "BoxType",
    "TextContent",
    "ImageContent",
    "ShapeContent",
    "ShapeType",
    "HAlign",
    "VAlign",
    "text_box",
    "image_box",
    "shape_box",
    "emu",
    "inches",
    "render_pptx",
]
