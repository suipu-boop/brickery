"""DocWrite 模板系统（纯数据，零逻辑）。

依据《DocWrite 规格》§3 冻结的 6 套模板参数集。模板只控制文档的视觉风格
（配色 / 字号 / 间距 / 表格纹路），不含任何生成逻辑。新增模板不视为"新增工具"，
不受 CHARTER §4.6.1 内置工具硬顶约束。

代码纪律：本文件只放数据，不放函数（除 dataclass 定义）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DocTemplate:
    """文档生成模板参数集（控制视觉风格，不改生成逻辑）。"""

    id: str                     # 模板标识，如 "business-blue"
    name: str                   # 显示名，如 "商务蓝"
    # --- 配色（6 位十六进制，不含 #）---
    primary_color: str          # 主色：标题色块 / 表头底色
    accent_color: str           # 强调色：标题 2/3 文本、色块描边
    text_color: str             # 正文色
    zebra_color: str            # 斑马纹交替行底色
    # --- 字号（half-point，OOXML 惯例：32 = 16pt）---
    heading1_size: int = 32
    heading2_size: int = 28
    heading3_size: int = 24
    body_size: int = 22
    table_header_size: int = 22
    # --- 间距（twips，1pt = 20twips）---
    line_spacing: int = 276     # 1.15 倍行距（240=单倍）
    space_after: int = 120      # 段后间距（6pt）
    # --- 表格 ---
    table_header_bold: bool = True
    table_zebra: bool = True
    # --- 背景（仅深色模板用；浅色模板为白色，不单独配）---
    page_bg: str = "FFFFFF"     # 页面底色（深色模板为深色，浅色为白）
    body_on_dark: bool = False  # 正文是否落在深色底上（决定正文默认浅色）


# 冻结 6 套模板（§3.2）。配色取自规格表，字号/间距为合理默认。
TEMPLATES: dict[str, DocTemplate] = {
    "business-blue": DocTemplate(
        id="business-blue", name="商务蓝",
        primary_color="1F4E79", accent_color="2E75B6",
        text_color="333333", zebra_color="F2F2F2",
    ),
    "forest-green": DocTemplate(
        id="forest-green", name="森林绿",
        primary_color="2D5F2D", accent_color="548235",
        text_color="333333", zebra_color="EAF1EA",
    ),
    "sunset-orange": DocTemplate(
        id="sunset-orange", name="日落橙",
        primary_color="C55A11", accent_color="ED7D31",
        text_color="333333", zebra_color="FBE9DD",
    ),
    "mono-gray": DocTemplate(
        id="mono-gray", name="极简灰",
        primary_color="595959", accent_color="808080",
        text_color="333333", zebra_color="F2F2F2",
    ),
    "royal-purple": DocTemplate(
        id="royal-purple", name="皇家紫",
        primary_color="5B2C6F", accent_color="8E44AD",
        text_color="333333", zebra_color="F3EAF6",
    ),
    "midnight-dark": DocTemplate(
        id="midnight-dark", name="暗夜",
        primary_color="1A1A2E", accent_color="0F3460",
        text_color="E0E0E0", zebra_color="16213E",
        page_bg="1A1A2E", body_on_dark=True,
    ),
}


def get_template(template_id: str) -> DocTemplate:
    """按 id 取模板；未知 id 回退默认 business-blue（不抛异常）。"""
    return TEMPLATES.get((template_id or "").strip().lower()) or TEMPLATES["business-blue"]


def template_ids() -> list[str]:
    """所有可用模板 id（供工具 schema enum 使用）。"""
    return list(TEMPLATES.keys())
