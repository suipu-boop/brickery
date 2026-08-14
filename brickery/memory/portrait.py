"""§5 用户画像（User Portrait）+ 保守更新。

从影子提炼用户特征，形成可查询、可演进的画像。
最高红线（§5 / §0）：
  - 矛盾信息【只标注、绝不覆盖】已有画像值。
  - 画像只【增量追加证据】，绝不擅自降低既有 confidence。
  - 本层任何代码路径不得出现“用新值直接 UPDATE 覆盖旧画像值”的逻辑。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import List, Optional

from .db import memory_conn

_LOW_CONF_CAP = 0.3  # 冲突时新候选值的最高置信上限


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_list(evidence) -> List[str]:
    if evidence is None:
        return []
    if isinstance(evidence, (list, tuple)):
        return [str(e) for e in evidence if e]
    return [str(evidence)]


def _load_json(text: str) -> list:
    try:
        v = json.loads(text) if text else []
        return v if isinstance(v, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def update_portrait(
    attribute: str,
    value: str,
    evidence=None,
    confidence: float = 0.5,
    now: str | None = None,
) -> dict:
    """写入/合并一条画像候选。

    返回 {"status": "merged"|"inserted"|"conflict", ...}。
    保守规则：
      - 同 (attribute, value) 已存在 → 合并证据，confidence 取 max（不降）。
      - 同 attribute 但 value 不同（冲突）→ 不动既有行；新建低置信候选行，
        并向主导既有行追加 contradiction 标注。
      - 全新 attribute → 正常插入。
    """
    ev_list = _as_list(evidence)
    ts = now or _now_iso()

    with memory_conn() as c:
        existing_same = c.execute(
            "SELECT * FROM user_portrait WHERE attribute=? AND value=?",
            (attribute, value),
        ).fetchone()

        if existing_same is not None:
            old_ev = _load_json(existing_same["evidence"])
            merged_ev = old_ev + ev_list
            new_conf = max(float(existing_same["confidence"]), float(confidence))
            c.execute(
                "UPDATE user_portrait SET evidence=?, confidence=?, updated_at=? "
                "WHERE attribute=? AND value=?",
                (json.dumps(merged_ev, ensure_ascii=False), new_conf, ts, attribute, value),
            )
            return {"status": "merged", "attribute": attribute, "value": value,
                    "confidence": new_conf}

        # 检查同 attribute 的其它值（潜在冲突）
        others = c.execute(
            "SELECT * FROM user_portrait WHERE attribute=? AND value<>?",
            (attribute, value),
        ).fetchall()

        if others:
            # 冲突：主导行 = 同 attribute 中 confidence 最高者
            dominant = max(others, key=lambda r: float(r["confidence"]))
            contradictions = _load_json(dominant["contradictions"])
            contradictions.append({
                "conflicting_value": value,
                "evidence": ev_list,
                "at": ts,
            })
            c.execute(
                "UPDATE user_portrait SET contradictions=?, updated_at=? "
                "WHERE attribute=? AND value=?",
                (json.dumps(contradictions, ensure_ascii=False), ts,
                 dominant["attribute"], dominant["value"]),
            )
            # 新候选：低置信，绝不盖过主导值
            cand_conf = min(float(confidence), _LOW_CONF_CAP)
            c.execute(
                "INSERT INTO user_portrait (attribute, value, evidence, confidence, contradictions, updated_at) "
                "VALUES (?,?,?,?,?,?)",
                (attribute, value, json.dumps(ev_list, ensure_ascii=False),
                 cand_conf, "[]", ts),
            )
            return {"status": "conflict", "attribute": attribute, "value": value,
                    "capped_confidence": cand_conf,
                    "dominant_value": dominant["value"]}

        # 全新 attribute
        c.execute(
            "INSERT INTO user_portrait (attribute, value, evidence, confidence, contradictions, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (attribute, value, json.dumps(ev_list, ensure_ascii=False),
             float(confidence), "[]", ts),
        )
        return {"status": "inserted", "attribute": attribute, "value": value,
                "confidence": float(confidence)}


def get_portrait(attribute: Optional[str] = None) -> List[dict]:
    """读取画像。可指定 attribute 过滤。"""
    with memory_conn() as c:
        if attribute:
            rows = c.execute(
                "SELECT * FROM user_portrait WHERE attribute=? ORDER BY confidence DESC",
                (attribute,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT * FROM user_portrait ORDER BY attribute, confidence DESC"
            ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["evidence"] = _load_json(r["evidence"])
        d["contradictions"] = _load_json(r["contradictions"])
        out.append(d)
    return out
