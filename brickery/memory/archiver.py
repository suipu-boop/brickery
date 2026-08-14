"""§1 对话影存档（Conversation Archiving）。

把一段对话沉淀为结构化记录（conversation_records）。
- 优先用注入引擎抽取 摘要/关键词；无引擎则回落 KeywordExtractor。
- 幂等：同一 session_id 的未确认记录只更新、不重复创建（§1 验收）。
- finalize_session 在会话结束时标记 confirmed=1。
- 只写库、不修改任何既有记录、不发网络请求（§1 红线）。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone, timedelta
from typing import Iterable, List

from .db import memory_conn
from .engine import Engine, extract_via


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _merge_keywords(existing: str, new: List[str]) -> List[str]:
    try:
        base = set(json.loads(existing)) if existing else set()
    except (json.JSONDecodeError, TypeError):
        base = set()
    base.update(new)
    return list(base)[:32]


def archive(
    session_id: str,
    texts: Iterable[str],
    project: str = "",
    engine: Engine | None = None,
    now: str | None = None,
) -> str:
    """存档一段对话，返回 record_id。

    同一 session_id 已有未确认记录时，更新它（合并关键词、延展时间窗）；
    否则新建。满足幂等验收。空对话不写库。
    """
    text_list = [t for t in texts if t and t.strip()]
    if not text_list:
        return ""

    summary, keywords = extract_via(engine, text_list)
    ts = now or _now_iso()

    with memory_conn() as c:
        row = c.execute(
            "SELECT record_id, keywords, time_range FROM conversation_records "
            "WHERE session_id=? AND confirmed=0 ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()

        if row is None:
            record_id = uuid.uuid4().hex
            c.execute(
                "INSERT INTO conversation_records "
                "(record_id, session_id, time_range, topic_summary, raw_summary, keywords, file_refs, "
                "importance, project, created_at, last_active, confirmed) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
                (
                    record_id, session_id, f"{ts}..{ts}", summary, summary,
                    json.dumps(keywords, ensure_ascii=False), "[]",
                    0.5, project, ts, ts,
                ),
            )
            return record_id

        # 幂等更新：合并关键词、延展时间窗末端
        merged = _merge_keywords(row["keywords"], keywords)
        old_range = row["time_range"]
        end = old_range.split("..")[-1] if ".." in old_range else old_range
        new_range = f"{old_range.split('..')[0]}..{ts}" if ".." in old_range else f"{old_range}..{ts}"
        # R2 修正：保留首次摘要（topic_summary）不被后续重新摘要静默覆盖；
        # 摘要演进只由夜间 refine_summary 负责。raw_summary 在建记录时写入一次、永不被改。
        c.execute(
            "UPDATE conversation_records SET keywords=?, time_range=?, last_active=? "
            "WHERE record_id=?",
            (json.dumps(merged, ensure_ascii=False), new_range, ts, row["record_id"]),
        )
        return row["record_id"]


def finalize_session(session_id: str) -> int:
    """将会话的所有未确认记录标记为 confirmed=1，返回受影响行数。"""
    with memory_conn() as c:
        cur = c.execute(
            "UPDATE conversation_records SET confirmed=1 WHERE session_id=? AND confirmed=0",
            (session_id,),
        )
        return cur.rowcount


def idle_finalize(idle_minutes: float) -> List[str]:
    """O1 闲置触发落档：把超过闲置门槛仍未 confirmed 的会话标记完成，返回受影响 session 列表。

    与 finalize_session（切走会话显式触发）共同构成「双触发」落档策略。
    """
    if idle_minutes <= 0:
        return []
    threshold = (datetime.now(timezone.utc) - timedelta(minutes=idle_minutes)).isoformat()
    with memory_conn() as c:
        rows = c.execute(
            "SELECT DISTINCT session_id FROM conversation_records "
            "WHERE confirmed=0 AND last_active < ?",
            (threshold,),
        ).fetchall()
        sids = [r["session_id"] for r in rows]
        if sids:
            c.execute(
                "UPDATE conversation_records SET confirmed=1 "
                "WHERE confirmed=0 AND last_active < ?",
                (threshold,),
            )
        return sids


def log_access(record_id: str, now: str | None = None) -> None:
    with memory_conn() as c:
        c.execute(
            "INSERT INTO access_log (record_id, accessed_at) VALUES (?,?)",
            (record_id, now or _now_iso()),
        )


def refine_summary(session_id: str, summary: str, now: str | None = None) -> None:
    """夜间归纳写回（§7）：更新该会话最新 confirmed 记录的 topic_summary。

    仅覆盖摘要字段，不动原始记录其他内容；会话无 confirmed 记录时静默跳过。
    """
    with memory_conn() as c:
        row = c.execute(
            "SELECT record_id FROM conversation_records "
            "WHERE session_id=? AND confirmed=1 ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return
        c.execute(
            "UPDATE conversation_records SET topic_summary=? WHERE record_id=?",
            (summary, row["record_id"]),
        )


def write_structured(session_id: str,
                     entities: List[str] | None = None,
                     decisions: List[str] | None = None,
                     todos: List[str] | None = None) -> int:
    """影子归纳写回（O3/O5）：将 entities/decisions/todos **合并**写入该会话最新记录。

    合并语义（并集去重）而非覆盖——多轮归纳可累积，不丢前轮结果。
    仅处理传入的非空字段；会话无记录时静默跳过。
    供浮现/归纳引擎把对话抽成结构化记忆后回填。
    """
    fields = (("entities", entities), ("decisions", decisions), ("todos", todos))
    with memory_conn() as c:
        row = c.execute(
            "SELECT record_id, entities, decisions, todos FROM conversation_records "
            "WHERE session_id=? ORDER BY created_at DESC LIMIT 1",
            (session_id,),
        ).fetchone()
        if row is None:
            return 0
        updates: dict = {}
        for field, val in fields:
            if val is None:
                continue
            try:
                existing = set(json.loads(row[field]) if row[field] else [])
            except (json.JSONDecodeError, TypeError):
                existing = set()
            merged = list(existing | set(val))[:50]
            updates[field] = json.dumps(merged, ensure_ascii=False)
        if not updates:
            return 0
        set_clause = ", ".join(f"{k}=?" for k in updates)
        c.execute(
            f"UPDATE conversation_records SET {set_clause} WHERE record_id=?",
            list(updates.values()) + [row["record_id"]],
        )
        return 1
