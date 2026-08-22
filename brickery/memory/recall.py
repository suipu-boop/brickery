"""§2 精准召回（Recall）。

按关键词 / 语义 + 时间衰减，从影子中检索相关记忆。
- 跨会话不丢；支持 project 过滤；时间衰减让近期记忆排序靠前（§2 验收）。
- 只读：不写库、不改写任何记录（§2 红线）。
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import List, Optional

from .db import memory_conn
from .engine import KeywordExtractor

_HALF_LIFE_DAYS = 30.0
# R5 修正：时间衰减下限。避免老但重要的记忆被近期闲聊彻底压沉
# （exp(-days/30) 对半年前记忆≈0.0025，几乎不可能被召回）。
_DECAY_FLOOR = 0.1


def _parse_ts(ts: str) -> datetime:
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return datetime.now(timezone.utc)


def _days_ago(ts: str, now: Optional[datetime]) -> float:
    ref = now or datetime.now(timezone.utc)
    return max(0.0, (ref - _parse_ts(ts)).total_seconds() / 86400.0)


def recall(
    query: str,
    project: str | None = None,
    limit: int = 10,
    now: datetime | None = None,
    sessions: Optional[list] = None,
) -> List[dict]:
    """返回按 相关性×时间衰减 排序的影子列表。

    sessions：可选会话白名单过滤。传 None（默认）保持跨会话全局召回
    （§2 设计行为）；传 ["sess-X"] 则仅从指定会话召回，用于多会话隔离。
    """
    q_kw = set(KeywordExtractor()._tokenize(query))
    if not q_kw:
        return []

    conds, params = [], []
    if project:
        conds.append("project=?")
        params.append(project)
    if sessions:
        placeholders = ",".join("?" * len(sessions))
        conds.append(f"session_id IN ({placeholders})")
        params.extend(sessions)
    where = ("WHERE " + " AND ".join(conds)) if conds else ""

    with memory_conn() as c:
        rows = c.execute(
            f"SELECT * FROM conversation_records {where}", params
        ).fetchall()

    q_raw = (query or "").strip()
    scored = []
    for r in rows:
        try:
            r_kw = set(json.loads(r["keywords"]))
        except (json.JSONDecodeError, TypeError):
            r_kw = set()
        overlap = len(q_kw & r_kw)
        # 兜底：无分词器（如 jieba 缺失）时 2-gram 关键词可能被 Top-N 截断丢失，
        # 若查询原文完整出现在记录摘要中，按弱命中召回（§2 可靠性兜底，不依赖分词）。
        substring_hit = False
        if overlap == 0 and q_raw:
            raw_s = r["raw_summary"] if "raw_summary" in r.keys() else ""
            top_s = r["topic_summary"] if "topic_summary" in r.keys() else ""
            substring_hit = q_raw in (raw_s or "") or q_raw in (top_s or "")
        if overlap == 0 and not substring_hit:
            continue
        decay = math.exp(-_days_ago(r["created_at"], now) / _HALF_LIFE_DAYS)
        decay = max(decay, _DECAY_FLOOR)  # 保底，老记忆仍有召回机会
        try:
            importance = float(r["importance"] or 0.5)
        except (ValueError, TypeError):
            importance = 0.5
        # 重要性加权：importance∈[0,1] → 因子 [0.5,1.5]，重要记忆排序更靠前
        # 子串兜底弱命中按 0.5 基准分，低于正常关键词命中，但保证不丢记忆
        base = float(overlap) if overlap else 0.5
        score = base * decay * (0.5 + importance)
        rec = dict(r)
        rec["keywords"] = list(r_kw)
        # 暴露原文摘要，供 _format_memory 在 topic_summary 为空时兜底（R2 修正）
        rec["raw_summary"] = r["raw_summary"] if "raw_summary" in r.keys() else ""
        for field in ("entities", "decisions", "todos"):
            try:
                rec[field] = json.loads(r[field]) if r[field] else []
            except (json.JSONDecodeError, TypeError):
                rec[field] = []
        rec["score"] = round(score, 4)
        scored.append(rec)

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]
