"""§6 主动推送（Proactive Push / Suggester）。

根据当前上下文，主动把相关记忆推到眼前，带相关性分级。
- 分级：strong / medium / weak（按 相关性×时间衰减×反馈 综合打分）。
- 反馈闭环：用户对建议的采纳/忽略写入 push_feedback，影响后续同条排序（§6 验收）。
- 推送只读数据源，不修改被推送的影子/文件本身（§6 红线）。
- 无上下文时安静返回空列表，不刷屏（§6 红线）。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import List, Optional

from .db import memory_conn
from .engine import KeywordExtractor
from .recall import recall

_STRONG, _MEDIUM = 1.5, 0.8


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _feedback_factor(record_id: str) -> float:
    with memory_conn() as c:
        rows = c.execute(
            "SELECT feedback FROM push_feedback WHERE item_ref=?", (record_id,)
        ).fetchall()
    accepts = sum(1 for r in rows if r["feedback"] == "accept")
    ignores = sum(1 for r in rows if r["feedback"] == "ignore")
    factor = 1.0 + 0.15 * accepts - 0.25 * ignores
    return max(0.3, min(2.0, factor))


def _grade(score: float) -> str:
    if score >= _STRONG:
        return "strong"
    if score >= _MEDIUM:
        return "medium"
    return "weak"


def suggest(context: str, project: str | None = None, limit: int = 5,
            now=None, shadow=None) -> List[dict]:
    """给定上下文文本，返回带分级的相关记忆建议。

    shadow：本地小模型（影子）。传入时让其从召回候选里挑最相关的
    （蓝图 A 档「影子自行判断该想起什么」）；缺失或返回空 → 回落规则全量，不退化。
    """
    if not context or not context.strip():
        return []

    results = recall(context, project=project, limit=limit * 2 or 10, now=now)
    # 影子判断：只保留影子判为相关的候选（无影子 / 返回空 → 规则全量）
    if shadow is not None and results:
        chosen = shadow.decide_surface(context, results)
        if chosen:
            chosen_set = set(chosen)
            results = [r for r in results if r.get("record_id") in chosen_set]
    out = []
    for r in results:
        base = r.get("score", 0.0)
        factor = _feedback_factor(r["record_id"])
        final = base * factor
        out.append({
            "type": "memory",
            "record_id": r["record_id"],
            "topic_summary": r["topic_summary"],
            "project": r["project"],
            "grade": _grade(final),
            "score": round(final, 4),
            "feedback_factor": round(factor, 2),
            "reason": "关键词重叠×时间衰减×反馈",
        })
    out.sort(key=lambda x: x["score"], reverse=True)
    out = out[:limit]

    # 记录一次推送事件（只读建议，不改数据源）
    ctx_hash = hashlib.sha1(context.encode("utf-8")).hexdigest()[:16]
    with memory_conn() as c:
        c.execute(
            "INSERT INTO push_events (context_hash, suggestions, at) VALUES (?,?,?)",
            (ctx_hash, json.dumps([o["record_id"] for o in out], ensure_ascii=False), _now_iso()),
        )
    return out


def record_feedback(item_ref: str, feedback: str, now: str | None = None) -> None:
    """记录用户对某条建议的反馈（accept / ignore）。"""
    if feedback not in ("accept", "ignore"):
        raise ValueError("feedback 必须为 'accept' 或 'ignore'")
    with memory_conn() as c:
        c.execute(
            "INSERT INTO push_feedback (item_ref, feedback, at) VALUES (?,?,?)",
            (item_ref, feedback, now or _now_iso()),
        )
