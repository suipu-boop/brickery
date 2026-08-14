"""§4 共现分析（Co-occurrence）。

统计关键词共现，为画像提炼与推送相关性提供信号。
- 只统计、不修改影子原文；关键词缺失时跳过该条而非报错（§4 红线）。
- 增量更新，不重复全量重算（§4 验收）。
"""
from __future__ import annotations

from typing import Iterable, List, Tuple

from .db import memory_conn


def update_cooccurrence(keywords: Iterable[str]) -> None:
    """对一组关键词的所有无序对，计数 +1（kw_a < kw_b 规范化）。"""
    uniq = sorted({k for k in keywords if k})
    if len(uniq) < 2:
        return
    with memory_conn() as c:
        for i in range(len(uniq)):
            for j in range(i + 1, len(uniq)):
                a, b = uniq[i], uniq[j]
                c.execute(
                    "INSERT INTO co_occurrence (kw_a, kw_b, count) VALUES (?,?,1) "
                    "ON CONFLICT(kw_a, kw_b) DO UPDATE SET count=count+1",
                    (a, b),
                )


def related_terms(term: str, k: int = 5) -> List[Tuple[str, int]]:
    """返回与 term 共现次数最多的 k 个词（按 count 降序）。"""
    with memory_conn() as c:
        rows = c.execute(
            "SELECT kw_a, kw_b, count FROM co_occurrence "
            "WHERE kw_a=? OR kw_b=? ORDER BY count DESC", (term, term)
        ).fetchall()
    scored: dict[str, int] = {}
    for r in rows:
        other = r["kw_b"] if r["kw_a"] == term else r["kw_a"]
        if other == term:
            continue
        scored[other] = scored.get(other, 0) + r["count"]
    return sorted(scored.items(), key=lambda kv: kv[1], reverse=True)[:k]
