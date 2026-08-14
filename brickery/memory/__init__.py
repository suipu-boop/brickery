"""Shadeling 记忆子系统（clean room，纯自研）。

阶段二交付门面：MemorySystem 聚合 §1–§8 全部行为。
- 引擎可选注入（运行时层注入 chat 接口）；无引擎时子系统仍可离线工作、可测。
- 所有持久化经 config.paths 派生，绝不依赖任何外部项目。
"""
from __future__ import annotations

from typing import Iterable, List, Optional

from . import archiver, clusters, cooccurrence, consolidation, filing, portrait, recall, suggester, cabinet, surfacing, fixed_core, smol
from .db import init_schemas
from .engine import Engine, KeywordExtractor

# 能力积木 → 门面方法名清单（P6 记忆积木化的单一事实源）。
# 每个 memory_kind 对应一组 MemorySystem 方法；安装/卸载能力积木即增删这些方法。
# core = 必装核心（archive+recall+surface），其余 7 个为可选扩展。
CAPABILITY_KINDS = {
    "core": [
        "archive", "finalize_session", "idle_finalize", "write_structured",
        "recall", "surface",
    ],
    "portrait": ["update_portrait", "get_portrait"],
    "fixed-core": [
        "set_core", "get_core", "has_core", "export_core",
        "set_smart_slot", "get_smart_slots", "delete_smart_slot", "get_all_core_text",
    ],
    "cluster": ["cluster", "cluster_members"],
    "cooccurrence": ["cooccur", "related_terms"],
    "suggest": ["suggest", "record_feedback"],
    "consolidation": [
        "enqueue", "nightly_pending_sessions", "open_session_context", "run_consolidation",
    ],
    "cabinet": [
        "index_file", "update_file", "remove_file", "search_files",
        "create_drawer", "get_drawer", "list_drawers", "update_drawer", "delete_drawer",
        "add_node", "update_node", "delete_node", "list_nodes",
        "add_edge", "delete_edge", "list_edges",
        "recordbook_text", "sync_recordbook", "explain_node", "detect_recommendation",
        "prune_orphan_edges",
    ],
    "smol": ["summarize", "semantic_recall"],
}


def _make_uninstalled(method_name: str, kind: str):
    """未安装能力的方法桩：调用即抛 NotImplementedError（提示缺哪个积木）。"""
    def _missing(*args, **kwargs):
        raise NotImplementedError(
            f"记忆能力「{method_name}」不可用：依赖 memory-{kind} 积木，"
            f"请先安装并激活该积木。")
    _missing.__name__ = method_name
    _missing.__qualname__ = f"MemorySystem.{method_name}(未安装)"
    return _missing


class MemorySystem:
    """记忆子系统统一入口（P6 起同时充当 MemoryHost 组装门面）。

    无参构造默认全装（向后兼容 ipc.py 等调用方）；显式传入 install 可只装指定
    能力积木，未安装的能力方法调用会抛 NotImplementedError。
    """

    def __init__(self, engine: Engine | None = None,
                 install: Iterable[str] | None = None):
        init_schemas()
        self.engine = engine
        self._installed: set = set()
        if install is None:
            # 默认全装：向后兼容无参构造的调用方
            self._installed = set(CAPABILITY_KINDS)
        else:
            for kind in install:
                if kind not in CAPABILITY_KINDS:
                    raise ValueError(
                        f"未知记忆能力 kind：{kind}（可用：{sorted(CAPABILITY_KINDS)}）")
                self._installed.add(kind)
            for kind in CAPABILITY_KINDS:
                if kind not in self._installed:
                    for m in CAPABILITY_KINDS[kind]:
                        setattr(self, m, _make_uninstalled(m, kind))

    # ---- 能力积木开关（P6）----
    def install_kind(self, kind: str) -> None:
        """安装某个记忆能力积木：激活其门面方法。"""
        if kind not in CAPABILITY_KINDS:
            raise ValueError(
                f"未知记忆能力 kind：{kind}（可用：{sorted(CAPABILITY_KINDS)}）")
        self._installed.add(kind)
        for m in CAPABILITY_KINDS[kind]:
            self.__dict__.pop(m, None)  # 摘除方法桩，恢复类方法

    def uninstall_kind(self, kind: str) -> None:
        """卸载某个记忆能力积木：其门面方法变为不可用桩。"""
        if kind not in CAPABILITY_KINDS:
            raise ValueError(
                f"未知记忆能力 kind：{kind}（可用：{sorted(CAPABILITY_KINDS)}）")
        self._installed.discard(kind)
        for m in CAPABILITY_KINDS[kind]:
            setattr(self, m, _make_uninstalled(m, kind))

    def has_kind(self, kind: str) -> bool:
        """能力探测：某能力积木是否已安装。"""
        return kind in self._installed

    def installed_kinds(self) -> List[str]:
        """已安装的能力积木清单（排序）。"""
        return sorted(self._installed)

    # §1 对话存档
    def archive(self, session_id: str, texts: Iterable[str], project: str = "",
                 now: str | None = None) -> str:
        return archiver.archive(session_id, texts, project=project, engine=self.engine, now=now)

    def finalize_session(self, session_id: str) -> int:
        return archiver.finalize_session(session_id)

    def idle_finalize(self, idle_minutes: float) -> List[str]:
        """O1 闲置触发落档：超时未确认会话标记完成，返回受影响 session 列表。"""
        return archiver.idle_finalize(idle_minutes)

    # §1.5 影子归纳写回（O3/O5）
    def write_structured(self, session_id: str,
                         entities: list | None = None,
                         decisions: list | None = None,
                         todos: list | None = None) -> int:
        return archiver.write_structured(session_id, entities=entities,
                                          decisions=decisions, todos=todos)

    # §2 召回
    def recall(self, query: str, project: str | None = None, limit: int = 10,
               now=None) -> List[dict]:
        return recall.recall(query, project=project, limit=limit, now=now)

    # §3.5 浮现（条件触发，O2）
    def surface(self, query: str, project: str | None = None, limit: int = 8,
                gate=None, recent_history=None, idle_seconds: float = 0.0,
                shadow=None) -> List[dict]:
        """组合 recall + 闸门，返回应注入的记忆片段（O2 条件触发）。

        gate 缺省用默认 SurfaceGate（指代词 / 话题跳变 / 长间隔）。
        不触发返回空（否决每轮灌）。无引擎也可工作（gate 是纯规则）。
        shadow：本地小模型（影子），传入则让其从候选里挑最相关的（蓝图 A 档）。
        """
        return surfacing.surfacing_for(
            self, query, project=project, limit=limit, gate=gate,
            recent_history=recent_history, idle_seconds=idle_seconds, shadow=shadow,
        )

    # §3.6 固定核（O8 手填 / O9 导出）
    def set_core(self, attribute: str, value: str, now: str | None = None) -> None:
        return fixed_core.set_core(attribute, value, now=now)

    def get_core(self, attribute: str | None = None):
        return fixed_core.get_core(attribute)

    def has_core(self) -> bool:
        return fixed_core.has_core()

    def export_core(self, include_core: bool) -> Optional[dict]:
        """O9：导出记忆库时是否含固定核（默认不含 + 警告由调用方处理）。"""
        return fixed_core.export_core(include_core)

    # §3.6 固定核智能槽（O8'：归纳引擎自动填充，可衰减/可删，防过度自信）
    def set_smart_slot(self, label: str, value: str, confidence: float = 0.9,
                       now: str | None = None) -> bool:
        return fixed_core.set_smart_slot(label, value, confidence=confidence, now=now)

    def get_smart_slots(self) -> List[dict]:
        return fixed_core.get_smart_slots()

    def delete_smart_slot(self, label: str) -> bool:
        return fixed_core.delete_smart_slot(label)

    def get_all_core_text(self) -> str:
        return fixed_core.get_all_core_text()

    # §9 导出（O9）
    def export_all(self, include_core: bool = False) -> dict:
        """O9：导出记忆库全量快照（结构化 dict）。

        聚焦已沉淀资产：用户画像、固定核（可选）、文件柜抽屉（节点/边/记录本）、
        已确认会话的对话影（摘要+关键词）。不导出对话原始逐条文本、不依赖推理引擎。
        """
        from datetime import datetime, timezone
        bundle: dict = {
            "schema": "shadeling-memory-export/v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            # 能力探测降级：可选积木未装时对应资产置空/缺省，导出仍可用
            "portrait": self.get_portrait() if self.has_kind("portrait") else [],
            "core": self.export_core(include_core) if self.has_kind("fixed-core") else None,
            "drawers": [],
            "conversations": (
                [{"session_id": sid, "text": text}
                 for sid, text in self.nightly_pending_sessions(limit=500)]
                if self.has_kind("consolidation") else []
            ),
        }
        if self.has_kind("cabinet"):
            for d in (self.list_drawers() or []):
                did = d.get("drawer_id") or d.get("id")
                if not did:
                    continue
                drawer = self.get_drawer(did) or {}
                drawer["nodes"] = self.list_nodes(did)
                drawer["edges"] = self.list_edges(did)
                drawer["recordbook"] = self.recordbook_text(did)
                bundle["drawers"].append(drawer)
        return bundle

    # §3 聚类
    def cluster(self, record_id: str, keywords: Iterable[str]) -> str:
        return clusters.cluster_record(record_id, keywords)

    def cluster_members(self, cluster_id: str) -> List[str]:
        return clusters.members_of(cluster_id)

    # §4 共现
    def cooccur(self, keywords: Iterable[str]) -> None:
        cooccurrence.update_cooccurrence(keywords)

    def related_terms(self, term: str, k: int = 5) -> List[tuple]:
        return cooccurrence.related_terms(term, k=k)

    # §5 画像
    def update_portrait(self, attribute: str, value: str, evidence=None,
                        confidence: float = 0.5, now: str | None = None) -> dict:
        return portrait.update_portrait(attribute, value, evidence=evidence,
                                        confidence=confidence, now=now)

    def get_portrait(self, attribute: str | None = None) -> List[dict]:
        return portrait.get_portrait(attribute)

    # §6 主动推送
    def suggest(self, context: str, project: str | None = None, limit: int = 5,
                now=None, shadow=None) -> List[dict]:
        return suggester.suggest(context, project=project, limit=limit, now=now,
                                 shadow=shadow)

    def record_feedback(self, item_ref: str, feedback: str, now: str | None = None) -> None:
        suggester.record_feedback(item_ref, feedback, now=now)

    # §7 夜间巩固
    def enqueue(self, item_type: str, payload: dict | None = None, priority: int = 0,
                now: str | None = None) -> int:
        return consolidation.enqueue(item_type, payload=payload, priority=priority, now=now)

    def nightly_pending_sessions(self, limit: int = 10) -> List[tuple]:
        """返回待归纳会话 [(session_id, text)]：最近 limit 个有 confirmed 记录的会话。

        供夜间归纳增强使用——把每个会话的碎片摘要/关键词拼成归纳输入。
        """
        from .db import memory_conn
        import json
        with memory_conn() as c:
            rows = c.execute(
                "SELECT session_id, topic_summary, keywords FROM conversation_records "
                "WHERE confirmed=1 ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        out: List[tuple] = []
        seen: set = set()
        for r in rows:
            sid = r["session_id"]
            if sid in seen:
                continue
            seen.add(sid)
            try:
                kw = json.loads(r["keywords"]) if r["keywords"] else []
            except (json.JSONDecodeError, TypeError):
                kw = []
            text = f"会话 {sid} 摘要：{r['topic_summary']}；关键词：{', '.join(kw)}"
            out.append((sid, text))
        return out

    def open_session_context(self, limit: int = 3, max_todos: int = 8) -> str:
        """新会话开场上下文：近期会话摘要 + 近期待办（消灭「失忆感」）。

        复用 nightly_pending_sessions 拿近期会话摘要，并另查最近 confirmed 记录的
        todos 合并去重。返回可直接注入 prompt 的块文本；无可呈现内容时返回空串。
        """
        from .db import memory_conn
        import json as _json

        sessions = self.nightly_pending_sessions(limit=limit)
        summaries = [text for _, text in sessions if text and text.strip()]

        todos: List[str] = []
        try:
            with memory_conn() as c:
                rows = c.execute(
                    "SELECT todos FROM conversation_records "
                    "WHERE confirmed=1 AND todos IS NOT NULL AND todos <> '[]' "
                    "ORDER BY created_at DESC LIMIT ?", (limit,)
                ).fetchall()
            for r in rows:
                try:
                    items = _json.loads(r["todos"]) if r["todos"] else []
                except (json.JSONDecodeError, TypeError):
                    items = []
                for it in items:
                    if it and it not in todos:
                        todos.append(it)
                if len(todos) >= max_todos:
                    break
        except Exception:
            todos = []

        if not summaries and not todos:
            return ""
        lines = []
        if summaries:
            lines.append("【近期会话】")
            for s in summaries:
                lines.append(f"- {s}")
        if todos:
            lines.append("【近期待办】")
            for t in todos:
                lines.append(f"- {t}")
        return "\n".join(lines)

    def run_consolidation(self, engine: Engine | None = None, run_id: str | None = None,
                          now: str | None = None) -> dict:
        eng = engine if engine is not None else self.engine
        # 能力探测（决策点 4）：未装 cluster/cooccurrence 积木时，
        # 夜间巩固的对应清理步骤空转跳过，避免清理未安装能力的表。
        consolidation.set_optional_cap("cluster", self.has_kind("cluster"))
        consolidation.set_optional_cap("cooccurrence", self.has_kind("cooccurrence"))
        return consolidation.run_consolidation(engine=eng, run_id=run_id, now=now)

    # §8 文件柜
    def index_file(self, doc_id: str, path: str, title: str, content: str) -> None:
        filing.index_file(doc_id, path, title, content)

    def update_file(self, doc_id: str, title: str | None = None,
                    content: str | None = None, path: str | None = None) -> None:
        filing.update_file(doc_id, title=title, content=content, path=path)

    def remove_file(self, doc_id: str) -> None:
        filing.remove_file(doc_id)

    def search_files(self, query: str, limit: int = 10) -> List[dict]:
        return filing.search_files(query, limit=limit)

    # §9 文件柜 / 项目抽屉 + 项目图谱（clean room 重写，凭规格实现）
    def create_drawer(self, drawer_id: str, title: str, kit=None):
        return cabinet.create_drawer(drawer_id, title, kit=kit)

    def get_drawer(self, drawer_id: str):
        return cabinet.get_drawer(drawer_id)

    def list_drawers(self):
        return cabinet.list_drawers()

    def update_drawer(self, drawer_id: str, title=None, kit=None):
        return cabinet.update_drawer(drawer_id, title=title, kit=kit)

    def delete_drawer(self, drawer_id: str) -> bool:
        return cabinet.delete_drawer(drawer_id)

    def add_node(self, drawer_id: str, node_type: str, label: str,
                 content: str = "", namespace: str = ""):
        return cabinet.add_node(drawer_id, node_type, label, content=content,
                                namespace=namespace)

    def update_node(self, node_id: str, label=None, content=None, node_type=None):
        return cabinet.update_node(node_id, label=label, content=content,
                                   node_type=node_type)

    def delete_node(self, node_id: str) -> bool:
        return cabinet.delete_node(node_id)

    def list_nodes(self, drawer_id: str):
        return cabinet.list_nodes(drawer_id)

    def add_edge(self, drawer_id: str, source: str, target: str, relation: str = ""):
        return cabinet.add_edge(drawer_id, source, target, relation=relation)

    def delete_edge(self, edge_id: str) -> bool:
        return cabinet.delete_edge(edge_id)

    def list_edges(self, drawer_id: str):
        return cabinet.list_edges(drawer_id)

    def recordbook_text(self, drawer_id: str) -> str:
        return cabinet.recordbook_text(drawer_id)

    def sync_recordbook(self, drawer_id: str) -> None:
        cabinet.sync_recordbook(drawer_id)

    def explain_node(self, node_id: str, engine=None):
        return cabinet.explain_node(node_id, engine=engine)

    def detect_recommendation(self, text: str):
        return cabinet.detect_recommendation(text)

    def prune_orphan_edges(self, drawer_id: str, delete: bool = False) -> List[dict]:
        return cabinet.prune_orphan_edges(drawer_id, delete=delete)

    # §P9 本地小模型增强（memory-smol 可选积木）
    def summarize(self, texts, limit: int = 120):
        """内容总结：优先引擎（小模型），无引擎回落 KeywordExtractor。"""
        return smol.summarize(texts, self.engine)

    def semantic_recall(self, query: str, texts, top_k: int = 5):
        """语义召回：优先引擎（嵌入模型），无嵌入模型回落关键词打分。"""
        return smol.semantic_recall(query, texts, self.engine, top_k=top_k)


__all__ = ["MemorySystem", "MemoryHost", "Engine", "KeywordExtractor", "CAPABILITY_KINDS"]

# 记忆宿主别名：P6 起 MemorySystem 同时充当「记忆宿主」（不积木化地基）。
MemoryHost = MemorySystem
