"""§3.6 固定核（O8 / O9 / O8'）。

固定核 = 长期固定信息（我是谁 / 行为铁律 / 未结事项…），分两层：
- **手动槽**：用户手填（§7.5.3 三槽位），`set_core`/`get_core` 操纵。
- **智能槽**（O8' 2026-08-09）：归纳引擎自动填充，`set_smart_slot`/`get_smart_slots` 操纵。
  高置信（重复≥2次的项目名/偏好/习惯用语）自动写入；中置信推候选；高敏感硬拦截。

两层物理隔离（source 列区分），注入 prompt 时手动槽优先拼接。

- O9：导出 / 备份默认**不含**核；需显式勾选 + 警告（由导出模块调用 export_core）。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .db import memory_conn

# 固定核智能槽硬上限（条数）
SMART_SLOT_MAX = 5
# 单条智能槽上限（字符）
SMART_SLOT_CHAR_LIMIT = 60
# 固定核注入 prompt 的字符预算上限（防手插槽无限膨胀撑爆上下文；与 loop.py SKILL_CONTENT_CAP 同思路）
CORE_TEXT_CAP = 2000
# 高敏感检测正则（密码/私钥/身份证号/手机号/银行卡等格式）
_SENSITIVE_PATTERNS = [
    re.compile(r'(?:password|passwd|pwd|secret|token|api[_-]?key|private[_-]?key)\s*[:=]\s*\S', re.I),
    re.compile(r'\b\d{15,19}\b'),  # 身份证/银行卡号长度
    re.compile(r'\b1[3-9]\d{9}\b'),  # 手机号
    re.compile(r'-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----'),
    re.compile(r'sk-[a-zA-Z0-9]{20,}'),  # API key 格式
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_schema() -> None:
    """幂等 ALTER：fixed_core 表加 source/confidence 列（兼容旧库升级 + 新库建表）。"""
    with memory_conn() as c:
        # 先确保表存在
        c.execute("CREATE TABLE IF NOT EXISTS fixed_core("
                  "attribute TEXT PRIMARY KEY,"
                  "value TEXT NOT NULL,"
                  "updated_at TEXT NOT NULL)")
        cols = {r["name"] for r in c.execute("PRAGMA table_info(fixed_core)")}
        if "source" not in cols:
            c.execute("ALTER TABLE fixed_core ADD COLUMN source TEXT DEFAULT NULL")
        if "confidence" not in cols:
            c.execute("ALTER TABLE fixed_core ADD COLUMN confidence REAL DEFAULT NULL")
        if "hit_count" not in cols:
            c.execute("ALTER TABLE fixed_core ADD COLUMN hit_count INTEGER DEFAULT 1")
        if "last_seen" not in cols:
            c.execute("ALTER TABLE fixed_core ADD COLUMN last_seen TEXT DEFAULT NULL")


def _is_sensitive(text: str) -> bool:
    """高敏感硬拦截：密码/私钥/身份证号/手机号等格式绝不进自动槽。"""
    return any(p.search(text) for p in _SENSITIVE_PATTERNS)


def set_core(attribute: str, value: str, now: str | None = None) -> None:
    """手填 / 覆盖一条固定核（手动槽）。空值视为删除该条。"""
    _ensure_schema()
    if value is None or not str(value).strip():
        with memory_conn() as c:
            c.execute("DELETE FROM fixed_core WHERE attribute=? AND (source IS NULL OR source='user')", (attribute,))
        return
    ts = now or _now_iso()
    with memory_conn() as c:
        c.execute(
            "INSERT INTO fixed_core(attribute, value, source, updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(attribute) DO UPDATE SET value=excluded.value, "
            "source=COALESCE(excluded.source, fixed_core.source), "
            "confidence=COALESCE(excluded.confidence, fixed_core.confidence), "
            "updated_at=excluded.updated_at",
            (attribute, str(value), "user", ts),
        )


def get_core(attribute: Optional[str] = None) -> Optional[object]:
    """读固定核手动槽：指定 attribute 返回单值（或 None）；不指定返回全量 dict。"""
    _ensure_schema()
    with memory_conn() as c:
        if attribute:
            r = c.execute(
                "SELECT value FROM fixed_core WHERE attribute=? AND (source IS NULL OR source='user')",
                (attribute,),
            ).fetchone()
            return r["value"] if r else None
        rows = c.execute(
            "SELECT attribute, value FROM fixed_core WHERE source IS NULL OR source='user' ORDER BY attribute"
        ).fetchall()
        return {r["attribute"]: r["value"] for r in rows}


# ----- 智能槽（O8' 2026-08-09） -----

def set_smart_slot(label: str, value: str, confidence: float = 0.9, now: str | None = None) -> bool:
    """归纳引擎写入智能槽。

    防过度自信设计（A1/A3）：
    - 置信度按证据融合（0.6*旧 + 0.4*新），**不再单调 MAX 只增**，纠错时下降；
    - 首次写入置信度打折（≤0.7），重复≥2次才升到传入值——门槛本模块自感知；
    - 记录 hit_count / last_seen，供读取时实时衰减。
    高敏感硬拦截返回 False；超限自动淘汰最低置信度条目。
    """
    _ensure_schema()
    text = str(value).strip()
    if not text:
        return False
    if _is_sensitive(text):
        return False  # 硬拦截，不入槽
    text = text[:SMART_SLOT_CHAR_LIMIT]  # 截断至上限
    ts = now or _now_iso()
    with memory_conn() as c:
        # 检查是否已存在同 label
        existing = c.execute(
            "SELECT attribute, confidence, hit_count FROM fixed_core "
            "WHERE attribute=? AND source='auto'",
            (label,),
        ).fetchone()
        if existing:
            old_conf = existing["confidence"] or 0.0
            hits = (existing["hit_count"] or 0) + 1
            # 证据融合：向新证据靠拢，而非单调 MAX（防过度自信）
            new_conf = min(0.99, 0.6 * old_conf + 0.4 * confidence)
            c.execute(
                "UPDATE fixed_core SET value=?, confidence=?, hit_count=?, "
                "last_seen=?, updated_at=? WHERE attribute=? AND source='auto'",
                (text, new_conf, hits, ts, ts, label),
            )
            return True
        # 首次写入：单次猜测只算候选，置信度打折（重复≥2次才升，门槛本模块自感知）
        first_conf = min(confidence, 0.7)
        # 检查是否已满
        count = c.execute(
            "SELECT COUNT(*) AS n FROM fixed_core WHERE source='auto'"
        ).fetchone()["n"]
        if count >= SMART_SLOT_MAX:
            # 淘汰最低置信度条目
            c.execute(
                "DELETE FROM fixed_core WHERE attribute=(SELECT attribute FROM fixed_core "
                "WHERE source='auto' ORDER BY confidence ASC LIMIT 1)"
            )
        c.execute(
            "INSERT INTO fixed_core(attribute, value, source, confidence, hit_count, "
            "last_seen, updated_at) VALUES(?,?,?,?,?,?,?)",
            (label, text, "auto", first_conf, 1, ts, ts),
        )
        return True


def delete_smart_slot(label: str) -> bool:
    """删除单条智能槽（A4：暴露纠错入口，用户/UI 可否决机器猜测）。"""
    _ensure_schema()
    with memory_conn() as c:
        c.execute(
            "DELETE FROM fixed_core WHERE attribute=? AND source='auto'",
            (label,),
        )
    return True


def get_smart_slots() -> List[Dict[str, object]]:
    """读取智能槽全量（按置信度降序）。

    A1：读取时按 last_seen 实时衰减展示置信度（长时间无重申则自然下降，
    防过度自信），不修改存储值。
    """
    _ensure_schema()
    now = datetime.now(timezone.utc)
    with memory_conn() as c:
        rows = c.execute(
            "SELECT attribute, value, confidence, hit_count, last_seen, updated_at "
            "FROM fixed_core WHERE source='auto' ORDER BY confidence DESC"
        ).fetchall()
    out = []
    for r in rows:
        conf = r["confidence"] or 0.0
        if r["last_seen"]:
            try:
                age_days = (now - datetime.fromisoformat(r["last_seen"])).days
                conf = conf * (0.5 ** (age_days / 30))  # 每 30 天衰减一半
            except (ValueError, TypeError):
                pass
        out.append({"label": r["attribute"], "value": r["value"],
                    "confidence": conf, "hit_count": r["hit_count"],
                    "updated_at": r["updated_at"]})
    return out


def get_all_core_text() -> str:
    """拼接固定核全文（手动槽优先 → 智能槽），供 prompt 注入。

    A2：手动槽优先占用预算；智能槽仅在剩余预算不足时整体省略并**明确提示**，
    不再从头截断导致智能槽被静默丢弃。
    """
    # 手动槽（优先）
    user_core = get_core()
    user_lines = []
    if user_core and isinstance(user_core, dict):
        user_lines = [f"- {k}: {v}" for k, v in user_core.items()]
    user_text = "\n".join(user_lines)

    # 智能槽（次优先，受剩余预算约束）
    smart = get_smart_slots()
    smart_text = ""
    if smart:
        smart_lines = [f"- {s['label']}（置信度 {s['confidence']:.0%}）" for s in smart]
        smart_text = "\n【自动识别】\n" + "\n".join(smart_lines)

    if len(user_text) > CORE_TEXT_CAP:
        # 手动槽自身就超预算：截断手动槽，智能槽整体省略
        user_text = user_text[:CORE_TEXT_CAP] + "\n…（固定核手动部分已截断，完整内容见设置）"
        smart_text = ""
    elif len(smart_text) > CORE_TEXT_CAP - len(user_text):
        # 智能槽超出剩余预算：整体省略并提示（不静默丢）
        smart_text = "\n【自动识别】\n…（自动识别内容较多，已省略，可在设置中查看）"

    return (user_text + smart_text).strip()


def has_core() -> bool:
    _ensure_schema()
    with memory_conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM fixed_core").fetchone()["n"] > 0


def list_core_attributes() -> List[str]:
    _ensure_schema()
    with memory_conn() as c:
        return [r["attribute"] for r in
                c.execute("SELECT attribute FROM fixed_core WHERE source IS NULL OR source='user' ORDER BY attribute").fetchall()]


def export_core(include_core: bool) -> Optional[Dict[str, str]]:
    """O9：导出记忆库时是否含固定核。

    include_core=False（默认）且存在核 → 返回 None（导出模块据此**不含核 + 弹警告**）。
    include_core=True → 返回完整核 dict（用户显式勾选后）。
    """
    if not include_core:
        return None
    return get_core()  # type: ignore[return-value]
