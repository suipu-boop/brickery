"""§7 夜间巩固（Nightly Consolidation）。

离线、异步地把待处理记忆做归纳、压缩归档与审计。
- 串行调度：一次一 worker，逐条处理，显存/资源不叠加（§7 验收）。
- 失败不丢数据：某一项异常时该项保留为 failed、其余继续，不整体崩溃（§7 验收）。
- 只读影子、写归档结果，不修改原始影子内容（§7 红线）。
- 引擎可选：有则用于归纳摘要，无则仍运行纯规则「骨架整理」（prune 处理器，
  做聚类压实 / 孤儿簇清理 / 共现降噪），子系统始终可用（§9.5）。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

from .db import consolidation_conn, memory_conn

_PROCESSORS: Dict[str, Callable] = {}

# 可选能力开关（P6）：夜间巩固会清理 cluster / cooccurrence 的表，但这两项是
# 可选积木。宿主在 run_consolidation 前通过 set_optional_cap 注入真实安装状态，
# 未安装时对应清理步骤空转跳过（决策点 4：optional 依赖能力探测）。
_OPTIONAL_CAPS: Dict[str, bool] = {"cluster": True, "cooccurrence": True}


def set_optional_cap(cap: str, enabled: bool) -> None:
    """注入某可选能力积木的安装状态（True=已装，清理步骤正常执行）。"""
    _OPTIONAL_CAPS[cap] = bool(enabled)


def register_processor(item_type: str, fn: Callable[[dict], str]) -> None:
    """注册一个 item_type 的处理函数（payload -> 结果文本）。"""
    _PROCESSORS[item_type] = fn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def enqueue(item_type: str, payload: dict | None = None, priority: int = 0,
            now: str | None = None) -> int:
    """入队一条巩固任务，返回 item id。"""
    ts = now or _now_iso()
    with consolidation_conn() as c:
        cur = c.execute(
            "INSERT INTO queue (item_type, payload, priority, status, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?)",
            (item_type, json.dumps(payload or {}, ensure_ascii=False), priority,
             "pending", ts, ts),
        )
        return cur.lastrowid


def run_consolidation(engine=None, run_id: str | None = None,
                      now: str | None = None) -> dict:
    """串行处理队列中所有 pending 项。返回运行摘要。"""
    rid = run_id or uuid.uuid4().hex
    ts = now or _now_iso()
    processed, succeeded, failed = 0, 0, 0

    with consolidation_conn() as c:
        items = c.execute(
            "SELECT id, item_type, payload FROM queue WHERE status='pending' "
            "ORDER BY priority DESC, created_at ASC"
        ).fetchall()

        for it in items:  # 串行：一次一条，无并发
            item_id = it["id"]
            item_type = it["item_type"]
            try:
                payload = json.loads(it["payload"]) if it["payload"] else {}
            except (json.JSONDecodeError, TypeError):
                payload = {}

            try:
                fn = _PROCESSORS.get(item_type)
                if fn is not None:
                    result = fn(payload)
                elif engine is not None and item_type == "summarize":
                    text = payload.get("text", "")
                    session_id = payload.get("session_id", "")
                    result = engine.chat([{"role": "user", "content": text}])
                    if session_id and result:
                        from . import archiver
                        archiver.refine_summary(session_id, result)
                else:
                    # 未注册类型：作为占位成功（未来扩展点），不报错中断
                    result = "no-op"
                c.execute(
                    "UPDATE queue SET status='done', updated_at=? WHERE id=?",
                    (ts, item_id),
                )
                c.execute(
                    "INSERT INTO audit_log (run_id, item_id, action, result, at) "
                    "VALUES (?,?,?,?,?)",
                    (rid, item_id, item_type, str(result)[:500], ts),
                )
                succeeded += 1
            except Exception as e:  # 失败隔离：本项标 failed，继续下一项
                c.execute(
                    "UPDATE queue SET status='failed', updated_at=? WHERE id=?",
                    (ts, item_id),
                )
                c.execute(
                    "INSERT INTO audit_log (run_id, item_id, action, result, at) "
                    "VALUES (?,?,?,?,?)",
                    (rid, item_id, item_type, f"error: {e}"[:500], ts),
                )
                failed += 1
            processed += 1

        summary = json.dumps(
            {"processed": processed, "succeeded": succeeded, "failed": failed},
            ensure_ascii=False,
        )
        c.execute(
            "INSERT INTO nightly_results (run_id, summary, at) VALUES (?,?,?)",
            (rid, summary, ts),
        )

    return {"run_id": rid, "processed": processed,
            "succeeded": succeeded, "failed": failed}


# ── 内置纯规则处理器（无模型即可运行）─────────────────────────────────────
def _prune_records(payload: dict) -> str:
    """骨架整理（§7，无模型）：孤儿语义簇清理 + 共现噪声瘦身 + 过期冷标记。

    - 孤儿簇：member_records 解析为空 → 删除（聚类漂移残留）。
    - 共现噪声：count<=1 视作偶然共现 → 删除，降低 related_terms 噪声。
    - 过期冷标记：confirmed 且无访问记录、超 90 天 → importance 压到 0.2（不删，保留可检索）。
    只读/轻写，失败隔离由 run_consolidation 保证；不丢任何用户数据。
    """
    orphan_clusters = 0
    noise_pairs = 0
    with memory_conn() as c:
        if _OPTIONAL_CAPS.get("cluster", True):
            rows = c.execute(
                "SELECT cluster_id, member_records FROM semantic_clusters"
            ).fetchall()
            for r in rows:
                try:
                    members = json.loads(r["member_records"]) if r["member_records"] else []
                except (json.JSONDecodeError, TypeError):
                    members = []
                if not members:
                    c.execute("DELETE FROM semantic_clusters WHERE cluster_id=?",
                              (r["cluster_id"],))
                    orphan_clusters += 1
        if _OPTIONAL_CAPS.get("cooccurrence", True):
            cur = c.execute("DELETE FROM co_occurrence WHERE count<=1")
            noise_pairs = cur.rowcount
        c.execute(
            "UPDATE conversation_records SET importance=0.2 "
            "WHERE confirmed=1 AND importance>0.2 "
            "AND record_id NOT IN (SELECT record_id FROM access_log) "
            "AND created_at < datetime('now', '-90 days')"
        )
    return f"pruned orphan_clusters={orphan_clusters} noise_cooccur={noise_pairs}"


# 模块加载即注册：夜间整理即使无模型也跑骨架维护
register_processor("prune", _prune_records)
