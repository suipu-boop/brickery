"""§3 语义聚类（Semantic Clustering）。

将相关影子归并为语义簇，支撑画像与推送的话题组织。
- 采用关键词重叠法（不依赖 LLM，可离线、可测）。
- 新影子落入重叠最高的已有簇；低于阈值则新建独立簇（§3 验收）。
- 聚类失败时降级为单影子独立成簇，不中断存档主流程（§3 红线）。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Iterable, List, Set

from .db import memory_conn

_OVERLAP_THRESHOLD = 0.2  # Jaccard 低于此值不开新归入，建独立簇


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _overlap(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cluster_record(record_id: str, keywords: Iterable[str]) -> str:
    """将一条记录归入最相关簇；无合适簇则新建。返回 cluster_id。"""
    kw_set = {k for k in keywords if k}
    try:
        with memory_conn() as c:
            rows = c.execute(
                "SELECT cluster_id, member_records, keywords FROM semantic_clusters"
            ).fetchall()
            best_id, best_score = None, 0.0
            for r in rows:
                try:
                    ckw = set(json.loads(r["keywords"]))
                except (json.JSONDecodeError, TypeError):
                    ckw = set()
                score = _overlap(kw_set, ckw)
                if score > best_score:
                    best_score, best_id = score, r["cluster_id"]

            if best_id is not None and best_score >= _OVERLAP_THRESHOLD:
                r = c.execute(
                    "SELECT member_records, keywords FROM semantic_clusters WHERE cluster_id=?",
                    (best_id,),
                ).fetchone()
                members = json.loads(r["member_records"]) if r["member_records"] else []
                ckw = set(json.loads(r["keywords"])) if r["keywords"] else set()
                members.append(record_id)
                merged_kw = list(ckw | kw_set)
                c.execute(
                    "UPDATE semantic_clusters SET member_records=?, keywords=? WHERE cluster_id=?",
                    (json.dumps(members, ensure_ascii=False), json.dumps(merged_kw, ensure_ascii=False), best_id),
                )
                return best_id

            # 新建簇
            new_id = uuid.uuid4().hex
            c.execute(
                "INSERT INTO semantic_clusters (cluster_id, label, member_records, keywords, created_at) "
                "VALUES (?,?,?,?,?)",
                (new_id, "", json.dumps([record_id], ensure_ascii=False),
                 json.dumps(list(kw_set), ensure_ascii=False), _now_iso()),
            )
            return new_id
    except Exception:
        # 降级：独立成簇，不影响调用方
        with memory_conn() as c:
            new_id = uuid.uuid4().hex
            c.execute(
                "INSERT INTO semantic_clusters (cluster_id, label, member_records, keywords, created_at) "
                "VALUES (?,?,?,?,?)",
                (new_id, "", json.dumps([record_id], ensure_ascii=False),
                 json.dumps(list(kw_set), ensure_ascii=False), _now_iso()),
            )
            return new_id


def members_of(cluster_id: str) -> List[str]:
    with memory_conn() as c:
        r = c.execute(
            "SELECT member_records FROM semantic_clusters WHERE cluster_id=?", (cluster_id,)
        ).fetchone()
    if not r or not r["member_records"]:
        return []
    return json.loads(r["member_records"])
