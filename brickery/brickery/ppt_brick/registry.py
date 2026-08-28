"""版式注册表：Schema/Component 双层契约（吸收 Presenton）。

对齐 specs/brick-ui-and-ppt-brick.md §3.2 ②：
- 版式注册表：预置 cover / toc / section / content 等版式，每个版式声明
  几何槽位（slot）+ 语义文本角色 + 对齐/容量行为；
- 双层契约：Schema 层（meta() 给 AI 语义、default() 兜底）约束每页数据
  字段；Component/渲染层负责布局——AI 只填数据、不碰像素坐标。

设计约束：
- 每个版式是「纯函数」：(tokens, data) -> list[Slide]（全是 model.py 的
  中间态对象），不 import pptx、不直接碰 PPTX；
- 注册表按 name 查询；同名注册抛 ValueError 防覆盖；未知名抛 KeyError；
- 布局只用 token 里的色值（换取 SVG-like 中间态可直接被 render 消费），
  token 契约/颜色进制收敛在 theme.py。
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import model
from .model import (
    Box,
    HAlign,
    ShapeType,
    Slide,
    VAlign,
    shape_box,
    text_box,
)

# 版式契约常量
CANVAS_W = 13.333  # 英寸，16:9
CANVAS_H = 7.5
_MARGIN = 0.9  # 页边距


@dataclass(frozen=True)
class FieldSpec:
    """Schema 层：单字段契约。meta 描述语义（AI 用），type/default 兜底。"""

    name: str
    meta: str = ""
    default: object = ""
    type: str = "str"  # str | list[str] | int | float


def _norm(data: Optional[dict], schema: Dict[str, FieldSpec], name: str):
    """按 schema 对数据做归一化：缺失字段用 default 兜底，多余字段丢弃。"""
    src = deepcopy(data or {})
    out: Dict[str, object] = {}
    for fn, spec in schema.items():
        v = src.pop(fn, spec.default)
        out[fn] = spec.default if v is None else v
    return out


def _items(data: dict, key: str = "items") -> List[str]:
    raw = data.get(key, []) or []
    if not isinstance(raw, list):
        raw = str(raw).split("\n")
    return [str(x).strip() for x in raw if str(x).strip()]


# ---------------------------------------------------------------------------
# 版式函数（Component 层：布局纯函数）
# ---------------------------------------------------------------------------

# 基础槽位：背景 / 首页条 / 强调 marker / 标题 / 正文 / 页脚
#   bg            : 页面主背景
#   topband       : 顶部通栏色带（封面/章节常用）
#   title         : 主标题（角色：大写、高对比）
#   subtitle      : 副题（角色：低对比、解释性）
#   body          : 要点正文列表
#   footer        : 页脚（日期 / 作者 / 页码类元信息）
#   accent_block  : 强调色块（左条 / 序号块 / 关键数据块）


# ---------------------------------------------------------------------------
# Schema 层：版式数据契约（meta 给 AI 语义、default 兜底）
# ---------------------------------------------------------------------------

_COVER_SCHEMA = {
    "title": FieldSpec("title", "主标题（封面核心信息）", "", "str"),
    "subtitle": FieldSpec("subtitle", "副题/一句话说明", "", "str"),
    "date": FieldSpec("date", "日期占位", "", "str"),
    "author": FieldSpec("author", "作者/演讲者/落款", "", "str"),
}
_TOC_SCHEMA = {
    "title": FieldSpec("title", "目录标题，默认 'Contents'", "Contents", "str"),
    "items": FieldSpec("items", "目录条目，list[str]，最多 7 条", [], "list[str]"),
}
_SECTION_SCHEMA = {
    "number": FieldSpec("number", "章节序号，默认 '01'", "01", "str"),
    "title": FieldSpec("title", "章节标题", "", "str"),
    "subtitle": FieldSpec("subtitle", "章节副题/引言", "", "str"),
}
_CONTENT_SCHEMA = {
    "title": FieldSpec("title", "内容页标题（页眉）", "", "str"),
    "items": FieldSpec("items", "要点列表，list[str]，最多 8 条", [], "list[str]"),
    "note": FieldSpec("note", "页脚注释（可选）", "", "str"),
}


def render_cover(tokens, data: Optional[dict] = None) -> List[Slide]:
    """封面：顶部色带 + 大标题 + 副题 + 底部元信息。"""
    sem = tokens["resolved"]
    d = _norm(data, _COVER_SCHEMA, "cover")
    s = Slide(bg=sem["background"])
    # 顶部通栏色带（accent 渐变的廉价等价：结实色带 + 边线）
    s.add(shape_box(0, 0, CANVAS_W, 0.34, fill=sem["accent"], name="topband"))
    s.add(shape_box(0, 0.34, CANVAS_W, 0.05, fill=sem["accent_strong"], name="topband-edge"))
    # 左侧强调条（强调角色）
    s.add(shape_box(_MARGIN, 1.9, 0.14, 1.7, ShapeType.ROUND_RECT,
                    fill=sem["accent_strong"], name="accent-rail"))
    # 大标题（title 角色）
    s.add(text_box(_MARGIN + 0.35, 1.75, CANVAS_W - 2 * _MARGIN, 1.5,
                   str(d["title"] or ""), font_size=40.0, bold=True,
                   color=sem["text"], name="title"))
    # 副题（subtitle 角色）
    s.add(text_box(_MARGIN + 0.35, 3.32, CANVAS_W - 2 * _MARGIN, 0.7,
                   str(d["subtitle"] or ""), font_size=19.0,
                   color=sem["text_muted"], name="subtitle"))
    # 底部元信息（footer 角色）
    meta_txt = "  ·  ".join(str(v) for v in (d["date"], d["author"]) if v)
    if meta_txt:
        s.add(text_box(_MARGIN, CANVAS_H - 1.0, CANVAS_W - 2 * _MARGIN, 0.5,
                       meta_txt, font_size=14.0, color=sem["text_muted"],
                       name="footer"))
    # 右下装饰块（accent_soft，纯视觉）
    s.add(shape_box(CANVAS_W - 3.3, CANVAS_H - 1.1, 2.4, 1.1,
                    ShapeType.ROUND_RECT, fill=sem["accent_soft"], name="deco"))
    s.add(shape_box(CANVAS_W - 3.05, CANVAS_H - 0.85, 1.9, 0.55,
                    ShapeType.ROUND_RECT, fill=sem["accent"], name="deco-2"))
    return [s]


def render_toc(tokens, data: Optional[dict] = None) -> List[Slide]:
    """目录：标题 + 编号条目列表（条目数 <= 7，超出截断并提示）。"""
    sem = tokens["resolved"]
    d = _norm(data, _TOC_SCHEMA, "toc")
    items = _items(d)[:7]
    s = Slide(bg=sem["background"])
    s.add(text_box(_MARGIN, 0.7, CANVAS_W - 2 * _MARGIN, 0.8,
                   str(d["title"]) or "Contents", font_size=28.0, bold=True,
                   color=sem["text"], name="toc-title"))
    s.add(shape_box(_MARGIN, 1.55, 1.1, 0.07, fill=sem["accent"], name="title-rule"))
    top = 2.0
    row_h = 0.62
    for i, item in enumerate(items):
        y = top + i * row_h
        num = f"{i + 1:02d}"
        # 编号块（accent_block 角色）
        s.add(shape_box(_MARGIN, y + 0.05, 0.5, 0.5, ShapeType.ROUND_RECT,
                        fill=sem["accent_strong"], name=f"toc-num-{i}"))
        s.add(text_box(_MARGIN + 0.05, y + 0.05, 0.4, 0.5, num,
                       font_size=15.0, bold=True, align=HAlign.CENTER,
                       valign=VAlign.MIDDLE, color=sem["accent_surface"],
                       name=f"toc-numtext-{i}"))
        s.add(text_box(_MARGIN + 0.75, y + 0.02, CANVAS_W - 2 * _MARGIN - 0.9,
                       0.56, item, font_size=17.0, color=sem["text"],
                       valign=VAlign.MIDDLE, name=f"toc-item-{i}"))
    if len(items) < len(_items(d)):
        s.add(text_box(_MARGIN, top + len(items) * row_h + 0.1,
                       CANVAS_W - 2 * _MARGIN, 0.4, "…", font_size=16.0,
                       color=sem["text_muted"], name="toc-ellipsis"))
    return [s]


def render_section(tokens, data: Optional[dict] = None) -> List[Slide]:
    """章节页：大序号 + 章节标题（居中构图，暗示分段）。"""
    sem = tokens["resolved"]
    d = _norm(data, _SECTION_SCHEMA, "section")
    s = Slide(bg=sem["background"])
    # 顶部强调带
    s.add(shape_box(0, 0, CANVAS_W, 0.18, fill=sem["accent"], name="topband"))
    # 背景装饰大圆（accent_soft，出画布半圆）
    s.add(shape_box(CANVAS_W - 1.6, -1.2, 3.0, 3.0, ShapeType.ELLIPSE,
                    fill=sem["accent_soft"], name="deco-circle"))
    # 序号（大、accent_strong）
    s.add(text_box(_MARGIN, 1.55, CANVAS_W - 2 * _MARGIN, 1.4,
                   str(d["number"]) if str(d["number"]).strip() else "01",
                   font_size=64.0, bold=True, align=HAlign.CENTER,
                   color=sem["accent_strong"], name="section-num"))
    # 章节标题
    s.add(text_box(_MARGIN + 1.5, 3.0, CANVAS_W - 2 * (_MARGIN + 1.5), 0.8,
                   str(d["title"]) or "Section", font_size=34.0, bold=True,
                   align=HAlign.CENTER, color=sem["text"], name="section-title"))
    # 短分隔线（居中）
    s.add(shape_box(CANVAS_W / 2 - 0.55, 3.95, 1.1, 0.06, fill=sem["accent"],
                    name="section-rule"))
    # 副题
    s.add(text_box(_MARGIN + 1.5, 4.15, CANVAS_W - 2 * (_MARGIN + 1.5), 0.6,
                   str(d["subtitle"] or ""), font_size=18.0,
                   align=HAlign.CENTER, color=sem["text_muted"],
                   name="section-subtitle"))
    return [s]


def render_content(tokens, data: Optional[dict] = None) -> List[Slide]:
    """内容页：页眉标题 + 分隔线 + 要点列表 + 可选页脚注释。"""
    sem = tokens["resolved"]
    d = _norm(data, _CONTENT_SCHEMA, "content")
    items = _items(d)
    s = Slide(bg=sem["background"])
    s.add(text_box(_MARGIN, 0.7, CANVAS_W - 2 * _MARGIN, 0.75,
                   str(d["title"]) or "", font_size=26.0, bold=True,
                   color=sem["text"], name="content-title"))
    s.add(shape_box(_MARGIN + 0.02, 1.5, 1.1, 0.06, fill=sem["accent"],
                    name="content-rule"))
    # 正文要点（容量 <= 8；超限截断）
    cap = min(max(len(items), 1), 8)
    top = 1.95
    row_h = (CANVAS_H - top - 1.0) / cap
    for i, item in enumerate(items[:cap]):
        y = top + i * row_h
        s.add(shape_box(_MARGIN, y + row_h / 2 - 0.09, 0.18, 0.18,
                        ShapeType.ELLIPSE, fill=sem["accent"], name=f"bullet-{i}"))
        body = str(item)
        if len(body) > 150:
            body = body[:147] + "…"
        s.add(text_box(_MARGIN + 0.45, y, CANVAS_W - 2 * _MARGIN - 0.6, row_h,
                       body, font_size=16.0, color=sem["text"],
                       valign=VAlign.TOP, name=f"item-{i}"))
    note = str(d["note"] or "").strip()
    if note:
        s.add(text_box(_MARGIN, CANVAS_H - 0.62, CANVAS_W - 2 * _MARGIN, 0.45,
                       note, font_size=12.0, color=sem["text_muted"],
                       name="note"))
    return [s]


# ---------------------------------------------------------------------------
# 注册表
# ---------------------------------------------------------------------------


@dataclass
class Layout:
    """注册表中的一个版式条目。"""

    name: str
    summary: str
    canvas_format: str = "16:9"
    page_count: int = 1
    schema: Dict[str, FieldSpec] = field(default_factory=dict)
    render: Callable = lambda tokens, data=None: [Slide()]

    def meta(self) -> Dict[str, dict]:
        """Schema 层：字段名 -> meta/dtype/default（给 AI 的语义描述）。"""
        return {fn: {
            "meta": sp.meta,
            "type": sp.type,
            "default": sp.default,
        } for fn, sp in self.schema.items()}


class Registry:
    """按 name 查询版式的注册表。同名注册抛 ValueError；未知名抛 KeyError。"""

    def __init__(self) -> None:
        self._layouts: Dict[str, Layout] = {}

    def register(
        self, name: str, render_fn: Callable,
        schema: Optional[Dict[str, FieldSpec]] = None,
        summary: str = "", canvas_format: str = "16:9", page_count: int = 1,
    ) -> Layout:
        if name in self._layouts:
            raise ValueError(f"版式已注册: {name}")
        lay = Layout(
            name=name, summary=summary, canvas_format=canvas_format,
            page_count=page_count, schema=schema or {}, render=render_fn,
        )
        self._layouts[name] = lay
        return lay

    def get(self, name: str) -> Layout:
        if name not in self._layouts:
            raise KeyError(
                f"未找到版式 '{name}'，可用: {sorted(self._layouts)}"
            )
        return self._layouts[name]

    def names(self) -> List[str]:
        return sorted(self._layouts)

    def has(self, name: str) -> bool:
        return name in self._layouts


REGISTRY = Registry()

REGISTRY.register(
    "cover", render_cover, dict(_COVER_SCHEMA),
    summary="封面：顶部色带 + 大标题 + 副题 + 底部元信息",
)
REGISTRY.register(
    "toc", render_toc, dict(_TOC_SCHEMA),
    summary="目录：编号条目列表（<=7）",
)
REGISTRY.register(
    "section", render_section, dict(_SECTION_SCHEMA),
    summary="章节页：大序号 + 标题（居中构图）",
)
REGISTRY.register(
    "content", render_content, dict(_CONTENT_SCHEMA),
    summary="内容页：标题 + 分隔线 + 要点列表（<=8）",
)

LAYOUTS = REGISTRY.names()

__all__ = [
    "FieldSpec",
    "Layout",
    "Registry",
    "REGISTRY",
    "LAYOUTS",
    "render_cover",
    "render_toc",
    "render_section",
    "render_content",
    "CANVAS_W",
    "CANVAS_H",
]
