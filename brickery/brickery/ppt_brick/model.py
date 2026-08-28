"""SVG-like 中间态数据模型（渲染底座 IR）。

设计意图（贴合 specs/brick-ui-and-ppt-brick.md 决策点 D4）：
PPT 渲染不直接碰 DrawingML/PPTX 数据层，而是先表达为一棵
「SVG-like 绝对坐标中间态」——Slide 为画布，Box 为绝对定位元素，
Content 描述元素语义（文本 / 图片 / 形状）。

render.py 负责把本模型的每个元素映射为 PowerPoint 原生对象
（原生文本框 sp / 原生 autoshape / 原生 picture），从而保证导出结果
在 PowerPoint/WPS 中双击可编辑。整条链路禁止位图化/截图。

与 SVG 的对应关系（决定能力边界）：
- Box.x / Box.y        -> 绝对坐标（英寸）；等价 SVG viewBox 内绝对位置
- Box.w / Box.h        -> 尺寸（英寸）
- Box.type             -> SVG 元素语义：text / image / shape
- Content 风格字段     -> 本底座仅支持可在原生对象上表达的样式；
  对 SVG 中不可落地的特性（mask / clipPath / textPath / filter /
  marker / 动画），本模型刻意不提供字段 —— 靠模型形状约束阻挡，
  而非在渲染层兜底（防生成后不可编辑）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Union

# ---------------------------------------------------------------------------
# 单位换算（中间态统一用英寸表达绝对坐标；EMU 供底层落盘用）
# ---------------------------------------------------------------------------
EMU_PER_INCH = 914400


def inches(value: float) -> int:
    """英寸 -> EMU（914400 EMU = 1 英寸）。"""
    return int(round(value * EMU_PER_INCH))


def emu(value: float) -> int:  # 别名，强调语义
    """同 inches()：英寸中间态 -> EMU。"""
    return inches(value)


# ---------------------------------------------------------------------------
# 枚举
# ---------------------------------------------------------------------------


class BoxType(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    SHAPE = "shape"


class ShapeType(str, Enum):
    """受支持的 SVG shape -> PowerPoint MSO_SHAPE 映射。"""

    RECT = "rect"
    ROUND_RECT = "round_rect"
    ELLIPSE = "ellipse"
    LINE = "line"
    TRIANGLE = "triangle"
    CHEVRON = "chevron"
    PENTAGON = "pentagon"

    def to_mso_shape(self) -> str:
        """返回 python-pptx MSO_SHAPE 枚举常量名（render.py 用）。"""
        mapping = {
            ShapeType.RECT: "RECTANGLE",
            ShapeType.ROUND_RECT: "ROUNDED_RECTANGLE",
            ShapeType.ELLIPSE: "OVAL",
            ShapeType.LINE: "STRAIGHT_CONNECTOR_1",
            ShapeType.TRIANGLE: "ISOCELES_TRIANGLE",
            ShapeType.CHEVRON: "CHEVRON",
            ShapeType.PENTAGON: "REGULAR_PENTAGON",
        }
        return mapping[self]


class VAlign(str, Enum):
    TOP = "top"
    MIDDLE = "middle"
    BOTTOM = "bottom"


class HAlign(str, Enum):
    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"


# ---------------------------------------------------------------------------
# 内容（Content）—— Box 的语义负载
# ---------------------------------------------------------------------------


@dataclass
class TextContent:
    """文本框内容：纯文本 + 整体式文本样式。

    对齐 D4 —— 与 SVG <text>/HTML <p> 同构：单块文本可带整体字号、
    颜色、对齐；后续迭代可扩展为 runs 分段样式（富文本）。
    """

    text: str
    font_size: float = 18.0  # pt
    bold: bool = False
    italic: bool = False
    color: str = "333333"  # hex 无 # 前缀，如 "333333"
    font_name: Optional[str] = None  # 缺省用主题字体
    align: HAlign = HAlign.LEFT
    valign: VAlign = VAlign.TOP


@dataclass
class ImageContent:
    """图片内容：本地文件路径或 base64 data URI。

    渲染为 PowerPoint 原生 picture 对象（可移动/可裁剪/可替换）。
    """

    src: str  # 绝对路径 或 "data:image/png;base64,...."
    fit: str = "contain"  # contain | cover | stretch（按原生图片对象能力）
    name: str = "Image"  # 透明名称


@dataclass
class ShapeContent:
    """形状内容：矩形/圆角矩形/椭圆/线条等。

    映射为 PowerPoint 原生 autoshape（可双击改颜色、改文字、加动画）。
    """

    shape_type: ShapeType = ShapeType.RECT
    fill: str = "E8EAF6"  # 填充色 hex；"none" 表示无填充
    line_color: Optional[str] = None  # 描边色；None 表示无描边
    line_width: float = 1.0  # pt


# ---------------------------------------------------------------------------
# Box —— 绝对定位元素（SVG-like）
# ---------------------------------------------------------------------------


@dataclass
class Box:
    """中间态元素：绝对坐标 + 尺寸 + 语义内容。

    x / y / w / h 单位均为「英寸」，语义与 SVG viewBox 绝对坐标一致。
    """

    type: BoxType
    x: float
    y: float
    w: float
    h: float
    content: Optional[Union[TextContent, ImageContent, ShapeContent]] = None
    name: str = ""
    bg: Optional[str] = None  # 元素自身背景色 hex
    border_radius: Optional[float] = None  # 英寸，仅用于装饰性 box（rect）

    def to_emu(self):
        """返回 (x, y, w, h) 的 EMU 值，供 render 落盘。"""
        return (inches(self.x), inches(self.y), inches(self.w), inches(self.h))


# ---------------------------------------------------------------------------
# Slide —— 画布（一张 PPT 页）
# ---------------------------------------------------------------------------


@dataclass
class Slide:
    """一页中间态：画布尺寸 + 背景 + 绝对定位元素列表。

    对应 PPT 一页（OneSlide）。尺寸默认 16:9。
    """

    boxes: List[Box] = field(default_factory=list)
    width: float = 13.333  # 英寸，16:9
    height: float = 7.5  # 英寸，16:9
    bg: str = "FFFFFF"  # 画布背景 hex；"transparent" 表示继承主题底

    def add(self, box: Box) -> "Slide":
        self.boxes.append(box)
        return self


# 便捷构造（贴合演示/脚本用法）


def text_box(
    x, y, w, h, text, font_size=18.0, bold=False, color="333333",
    align=HAlign.LEFT, valign=VAlign.TOP, name="",
) -> Box:
    return Box(
        type=BoxType.TEXT, x=x, y=y, w=w, h=h, name=name or text[:12],
        content=TextContent(
            text=text, font_size=font_size, bold=bold,
            color=color, align=align, valign=valign,
        ),
    )


def image_box(x, y, w, h, src, fit="contain", name="") -> Box:
    return Box(
        type=BoxType.IMAGE, x=x, y=y, w=w, h=h, name=name,
        content=ImageContent(src=src, fit=fit),
    )


def shape_box(x, y, w, h, shape_type=ShapeType.RECT, fill="E8EAF6",
              line_color=None, line_width=1.0, name="") -> Box:
    return Box(
        type=BoxType.SHAPE, x=x, y=y, w=w, h=h, name=name,
        content=ShapeContent(
            shape_type=shape_type, fill=fill,
            line_color=line_color, line_width=line_width,
        ),
    )
