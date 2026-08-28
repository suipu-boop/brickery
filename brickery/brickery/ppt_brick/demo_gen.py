"""Step2 第三小块示例：端到端编排器演示。

用一个多章节 structure 经 build_deck 编排并 render 出 demo_gen.pptx。
"""

import os
import sys

sys.path.insert(
    0,
    os.path.dirname(
        os.path.dirname(os.path.abspath(__file__))
    ),
)

from ppt_brick import theme
from ppt_brick.generator import build_deck, generate_pptx, plan_deck

OUT_DIR = "/Users/suipu/Dev/brickery/output"
OUT_PATH = os.path.join(OUT_DIR, "demo_gen.pptx")

STRUCTURE = {
    "brand_color": "#0EA5A5",
    "title": "端到端编排器：从描述到 PPT",
    "subtitle": "高层结构 → 版式编排 → 中间态 → 原生 PPTX",
    "author": "brickery · ppt_brick",
    "date": "2026-08-27",
    "toc_title": "内容结构",
    "sections": [
        {
            "title": "编排规则",
            "subtitle": "如何把段落映射为版式",
            "bullets": [
                "输入单一高层描述 dict：品牌色、标题、章节、要点。",
                "封面固定走 cover 版式；起始目录走 toc 版式（含章节序号推导）。",
                "每个章节先生成 section 分隔页，再按内容容量切出若干 content 页。",
                "编排决策全部收敛在 plan_deck 纯函数：可脱离渲染单独单测。",
                "页码由编排器自动维护（页序 + 每页 note 回填 page/total）。",
            ],
        },
        {
            "title": "内容分页策略",
            "subtitle": "容量与强制页数",
            "bullets": [
                "默认每张 content 页 6 条，可顶层 content_max_bullets 调整。",
                "单个章节可覆盖 max_bullets 或强制 pages=N 均分页数。",
                "超过 180 字的超长单条先按标点分句，再进入条数拆页，避免被截断。",
                "空 bullets 也产出 content 页（版式数据缺省兜底）。",
            ],
        },
        {
            "title": "版式覆盖与落盘",
            "subtitle": "自定义映射 + 一次渲染",
            "bullets": [
                "layout_ids 可整体替换 role → 版式名，甚至剔除某类角色页。",
                "generate_pptx 把 build_deck + render_pptx 收进一键，返回落盘路径。",
                "产物是原生 PPTX：可双击继续编辑，并非位图截图。",
                "本轮共生成演示页如下，建议用 PowerPoint 打开检查版式与页码。",
            ],
        },
    ],
}


def main() -> None:
    tokens = theme.derive_tokens(STRUCTURE["brand_color"])
    plan = plan_deck(STRUCTURE)
    pages = len(plan)
    slides = build_deck(tokens, STRUCTURE)
    assert len(slides) == pages
    print(f"plan pages: {pages}")
    for p in plan:
        print(f"  p{p['page']:02d}/{p['total']}  {p['role']:<8} -> {p['layout']}")
    out = generate_pptx(STRUCTURE, OUT_PATH)
    print(f"demox generated: {out}")


if __name__ == "__main__":
    main()
