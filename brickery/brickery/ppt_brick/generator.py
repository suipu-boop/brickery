"""端到端编排器：高层描述 -> 版式编排 -> 中间态 Slide 序列 -> .pptx。

对齐 specs/brick-ui-and-ppt-brick.md Step2 编排器目标：
- build_deck: 高层 structure dict -> 完整中间态 Slide 列表（纯编排，不碰 PPTX）；
- 编排规则：封面 -> 目录 -> 每章节(section + content)，自动页序；
- 内容分页：bullets 按页面容量拆页、超长单条先分句再入包、每章 content
  页数可配置（section.pages）；
- 页码：plan 阶段为每页标注 page/total，content 页 note 缺省时回填页码；
- 默认版式顺序与 registry 一致，可传 layout_ids 覆盖 role -> 版式名映射；
- generate_pptx: build_deck + render_pptx 一次落盘。

设计约束：
- 编排决策都收敛在 plan_deck（纯函数、可单测），build_deck 只负责把
  plan 各项交给注册表版式渲染；
- 不 import pptx；渲染全部委托 render.render_pptx；
- 品牌色缺失用 DEFAULT_BRAND 兜底（generate_pptx 入口）。
"""

from __future__ import annotations

import os
from copy import deepcopy
from typing import Callable, Dict, List, Optional

from . import theme
from .model import Slide
from .registry import REGISTRY
from .render import render_pptx

DEFAULT_BRAND = "1D4ED8"  # 品牌色缺省兜底
DEFAULT_MAX_BULLETS = 6  # 单张 content 页默认 bullets 容量
_LONG_BULLET_CHARS = 180  # 超过该长度的单条先分句再拆页
_RENDER_TRUNC = 150  # render 单条正文截断长度（registry align）

# role -> 版式名 默认映射（与 registry 预置一致）
DEFAULT_LAYOUT_IDS: Dict[str, str] = {
    "cover": "cover",
    "toc": "toc",
    "section": "section",
    "content": "content",
}


# ---------------------------------------------------------------------------
# 纯函数：内容切分（可单测）
# ---------------------------------------------------------------------------


def _sentence_split(text: str) -> List[str]:
    """把一条超长文案按语义标点切成多句（末位去空）。"""
    import re

    text = str(text).strip()
    if not text:
        return []
    parts = re.split(r"(?<=[。；;！？!?])\s*", text)
    out = [p.strip() for p in parts if p.strip()]
    # 兜底：未切开但确实超长的，整段返回
    return out or [text]


def split_long_bullet(bullet: str, max_chars: int = _LONG_BULLET_CHARS) -> List[str]:
    """单条过长 -> 分句多段；不超长 -> 原样单元素。"""
    bullet = str(bullet).strip()
    if not bullet:
        return []
    if len(bullet) <= max_chars:
        return [bullet]
    return _sentence_split(bullet)


def chunk_bullets(
    bullets: List[str],
    max_per_page: int = DEFAULT_MAX_BULLETS,
    forced_pages: Optional[int] = None,
) -> List[List[str]]:
    """bullets -> 按页均分的 chunk 列表（每页 <= max_per_page 条）。

    - forced_pages>0：先均分为 N 页（每页 ceil(len/N)），若单页仍超容量
      递归继续拆，直至每页 <= max_per_page；
    - 空输入返回 [[ ]]（单空 content 页）。
    """
    items = [str(b).strip() for b in bullets]
    items = [b for b in items if b]
    if not items:
        return [[]]

    if forced_pages and forced_pages > 0:
        import math

        per = math.ceil(len(items) / forced_pages)
        chunks = [items[i:i + per] for i in range(0, len(items), per)]
    else:
        chunks = [items[i:i + max_per_page]
                  for i in range(0, len(items), max_per_page)]
    if not chunks:
        chunks = [[]]
    # 单页仍超容量（forced_pages 拆太少）-> 递归压
    clean: List[List[str]] = []
    for c in chunks:
        if len(c) <= max_per_page:
            clean.append(c)
        else:
            clean.extend(chunk_bullets(c, max_per_page))
    return clean


# ---------------------------------------------------------------------------
# 纯函数：编排计划（可单测，不触渲染）
# ---------------------------------------------------------------------------


def plan_deck(structure: Optional[dict] = None) -> List[dict]:
    """高层描述 -> 编排计划（有序 dict 列表）。

    每项: {role, layout, page, total, data}，其中 data 是给对应版式
    schema 的字段 dict（缺省由版式兜底，故可为空/缺键）。
    """
    src = deepcopy(structure or {})
    sections = src.get("sections", []) or []
    cover = {
        "title": src.get("title", ""),
        "subtitle": src.get("subtitle", ""),
        "date": src.get("date", ""),
        "author": src.get("author", ""),
    }

    # 目录：显式 toc_items 优先，否则由 sections 标题推导（截断至 7）
    toc_items = src.get("toc_items", None)
    if not toc_items:
        toc_items = [sec.get("title", "") for sec in sections]
    toc_items = [str(t).strip() for t in toc_items if str(t).strip()][:7]

    pages: List[dict] = []
    pages.append({"role": "cover", "layout": "cover", "data": cover})

    if toc_items:
        pages.append({
            "role": "toc",
            "layout": "toc",
            "data": {"title": src.get("toc_title", "Contents"), "items": toc_items},
        })

    content_max = int(src.get("content_max_bullets", DEFAULT_MAX_BULLETS))
    for i, sec in enumerate(sections):
        sec = sec or {}
        number = str(sec.get("number") or f"{i + 1:02d}")[:4]
        pages.append({
            "role": "section",
            "layout": "section",
            "data": {
                "number": number,
                "title": sec.get("title", ""),
                "subtitle": sec.get("subtitle", ""),
            },
        })
        # 内容拆页输入：超长条先分句展开
        flat: List[str] = []
        for b in sec.get("bullets", []) or []:
            flat.extend(split_long_bullet(str(b)))
        per = int(sec.get("max_bullets") or content_max)
        forced = sec.get("pages")
        for chunk in chunk_bullets(flat, max_per_page=per, forced_pages=forced):
            pages.append({
                "role": "content",
                "layout": "content",
                "data": {
                    "title": sec.get("title", ""),
                    "items": chunk,
                    "note": sec.get("note", ""),
                },
            })

    # 页码：1..total（含封面）；content 页 note 由 build 阶段回填页码
    total = len(pages)
    for idx, p in enumerate(pages, start=1):
        p["page"] = idx
        p["total"] = total
    return pages


# ---------------------------------------------------------------------------
# 编排执行
# ---------------------------------------------------------------------------


def _resolve_layout_ids(
    layout_ids: Optional[Dict[str, str]],
) -> Dict[str, str]:
    merged = dict(DEFAULT_LAYOUT_IDS)
    if layout_ids:
        for k, v in layout_ids.items():
            if v is None:
                merged.pop(k, None)
            else:
                merged[k] = str(v)
    return merged


def build_deck(
    tokens: object,
    structure: Optional[dict] = None,
    layout_ids: Optional[Dict[str, str]] = None,
) -> List[Slide]:
    """高层描述 -> 完整中间态 Slide 列表。

    layout_ids: 覆盖 role -> 版式名 的映射（如 {"content": "content2"}）；
    传 None 表示 key 的 role 从编排中剔除。所有版式名须已注册。
    """
    ids = _resolve_layout_ids(layout_ids)
    slides: List[Slide] = []
    for p in plan_deck(structure):
        role = p["role"]
        lay_name = ids.get(role)
        if lay_name is None:
            continue  # 自定义映射删除了该 role
        lay = REGISTRY.get(lay_name)
        data = dict(p["data"])
        if p["layout"] == "content" and not str(data.get("note") or "").strip():
            # 页码回填：note 缺省给页脚页码信息（page / total）
            data["note"] = f"{p['page']} / {p['total']}"
        slides.extend(lay.render(tokens, data))
    return slides


def generate_pptx(
    structure: Optional[dict] = None,
    out_path: str = "deck.pptx",
    variant: str = "通用",
    semantics: str = "light",
    layout_ids: Optional[Dict[str, str]] = None,
) -> str:
    """build_deck + render 一次落盘，返回 out_path。

    品牌色缺失用 DEFAULT_BRAND 兜底；variant/语义档可调。
    """
    src = structure or {}
    brand = str(src.get("brand_color") or DEFAULT_BRAND)
    tokens = theme.derive_tokens(brand, variant=variant, semantics=semantics)
    slides = build_deck(tokens, src, layout_ids=layout_ids)
    return render_pptx(slides, out_path)


__all__ = [
    "DEFAULT_BRAND",
    "DEFAULT_LAYOUT_IDS",
    "split_long_bullet",
    "chunk_bullets",
    "plan_deck",
    "build_deck",
    "generate_pptx",
    "render_pptx",
]
