"""文件柜 / 项目抽屉 + 项目图谱（cabinet，clean room 全新实现）。

规格来源（仅读描述，不读任何旧 agent 源码）：
- AGENTS.md 抽屉/图谱 v1 八条：1 项目=1 抽屉；记录本 SQLite 主 + 记录本.md 镜像；
  项目图谱 = graph_nodes + graph_edges，记录本三段做成锚定节点 R/S/P，与图谱同一份数据两种视图；
  节点类型化；? 解释（无模型降级展示原始内容）；agent 不擅自建抽屉；项目固定 kit；
  图谱编码清单当压缩代理。
- cascading Layer3 文件检索：文件落磁盘、搜索返回路径 + 命中片段。

本模块不 import 任何外部框架代码，纯标准库 + 本仓 config/db。
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .db import cabinet_conn
from brickery.runtime.paths import get_home

NODE_TYPES = {
    "goal", "decision", "risk", "task", "resource",
    "rule", "status", "progress", "anchor",
}

# 记录本三段：规则 / 现状 / 进度（对应 R/S/P 三个锚定节点）
RECORD_SECTIONS = [
    ("R", "规则（Rules）", "rule"),
    ("S", "现状（Status）", "status"),
    ("P", "进度（Progress）", "progress"),
]

# 立项意图关键词（detect_recommendation 用）
_INTENT_HINTS = [
    "立项", "项目计划", "开始做", "调研", "课题", "课题申报", "新项目",
    "做个项目", "启动", "规划", "研究计划", "开题",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(conn, table, pk_col, pk_val):
    cur = conn.execute(f"SELECT * FROM {table} WHERE {pk_col}=?", (pk_val,))
    r = cur.fetchone()
    return dict(r) if r else None


# --------------------------------------------------------------------------
# 抽屉（项目）
# --------------------------------------------------------------------------

def create_drawer(drawer_id: str, title: str, kit: Optional[List[str]] = None) -> dict:
    """建抽屉 + 自动建 R/S/P 三个锚定节点 + 同步记录本.md。"""
    now = _now_iso()
    kit_json = json.dumps(kit or [], ensure_ascii=False)
    with cabinet_conn() as c:
        c.execute(
            "INSERT INTO cabinet_drawers (drawer_id, title, kit, created_at, updated_at) "
            "VALUES (?,?,?,?,?)",
            (drawer_id, title, kit_json, now, now),
        )
        # 三个锚定节点：记录本 R/S/P 三段
        for code, label, ntype in RECORD_SECTIONS:
            c.execute(
                "INSERT INTO cabinet_nodes "
                "(node_id, drawer_id, type, label, content, namespace, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (f"{drawer_id}::{code}", drawer_id, "anchor", label, "",
                 code, now, now),
            )
    sync_recordbook(drawer_id)
    return get_drawer(drawer_id)


def get_drawer(drawer_id: str) -> Optional[dict]:
    with cabinet_conn() as c:
        d = _row(c, "cabinet_drawers", "drawer_id", drawer_id)
    if d is None:
        return None
    d["kit"] = json.loads(d["kit"]) if d.get("kit") else []
    return d


def list_drawers() -> List[dict]:
    with cabinet_conn() as c:
        rows = c.execute(
            "SELECT * FROM cabinet_drawers ORDER BY created_at DESC"
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["kit"] = json.loads(d["kit"]) if d.get("kit") else []
        out.append(d)
    return out


def update_drawer(drawer_id: str, title: Optional[str] = None,
                  kit: Optional[List[str]] = None) -> Optional[dict]:
    with cabinet_conn() as c:
        cur = _row(c, "cabinet_drawers", "drawer_id", drawer_id)
        if cur is None:
            return None
        new_title = title if title is not None else cur["title"]
        new_kit = json.dumps(kit, ensure_ascii=False) if kit is not None \
            else cur["kit"]
        c.execute(
            "UPDATE cabinet_drawers SET title=?, kit=?, updated_at=? "
            "WHERE drawer_id=?",
            (new_title, new_kit, _now_iso(), drawer_id),
        )
    return get_drawer(drawer_id)


def delete_drawer(drawer_id: str) -> bool:
    with cabinet_conn() as c:
        cur = _row(c, "cabinet_drawers", "drawer_id", drawer_id)
        if cur is None:
            return False
        c.execute("DELETE FROM cabinet_edges WHERE drawer_id=?", (drawer_id,))
        c.execute("DELETE FROM cabinet_nodes WHERE drawer_id=?", (drawer_id,))
        c.execute("DELETE FROM cabinet_drawers WHERE drawer_id=?", (drawer_id,))
    # 删除记录本镜像
    p = recordbook_path(drawer_id)
    if p.exists():
        try:
            p.unlink()
        except OSError:
            pass
    return True


# --------------------------------------------------------------------------
# 图谱节点 / 边
# --------------------------------------------------------------------------

def add_node(drawer_id: str, node_type: str, label: str,
             content: str = "", namespace: str = "") -> dict:
    if node_type not in NODE_TYPES:
        raise ValueError(f"非法节点类型：{node_type}（须为 {sorted(NODE_TYPES)}）")
    if get_drawer(drawer_id) is None:
        raise ValueError(f"抽屉不存在：{drawer_id}")
    now = _now_iso()
    ns = namespace or f"{drawer_id}:{node_type}"
    node_id = f"{drawer_id}::{ns}::{label}" if label else f"{drawer_id}::{ns}::{now}"
    with cabinet_conn() as c:
        c.execute(
            "INSERT INTO cabinet_nodes "
            "(node_id, drawer_id, type, label, content, namespace, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (node_id, drawer_id, node_type, label, content, ns, now, now),
        )
    # 若改动的是 R/S/P 锚点，重同步记录本
    sync_recordbook(drawer_id)
    return get_node(node_id)


def get_node(node_id: str) -> Optional[dict]:
    with cabinet_conn() as c:
        return _row(c, "cabinet_nodes", "node_id", node_id)


def update_node(node_id: str, label: Optional[str] = None,
                content: Optional[str] = None,
                node_type: Optional[str] = None) -> Optional[dict]:
    with cabinet_conn() as c:
        cur = _row(c, "cabinet_nodes", "node_id", node_id)
        if cur is None:
            return None
        if node_type is not None and node_type not in NODE_TYPES:
            raise ValueError(f"非法节点类型：{node_type}")
        new_label = label if label is not None else cur["label"]
        new_content = content if content is not None else cur["content"]
        new_type = node_type if node_type is not None else cur["type"]
        c.execute(
            "UPDATE cabinet_nodes SET label=?, content=?, type=?, updated_at=? "
            "WHERE node_id=?",
            (new_label, new_content, new_type, _now_iso(), node_id),
        )
    sync_recordbook(cur["drawer_id"])
    return get_node(node_id)


def delete_node(node_id: str) -> bool:
    with cabinet_conn() as c:
        cur = _row(c, "cabinet_nodes", "node_id", node_id)
        if cur is None:
            return False
        c.execute("DELETE FROM cabinet_edges WHERE source=? OR target=?",
                  (node_id, node_id))
        c.execute("DELETE FROM cabinet_nodes WHERE node_id=?", (node_id,))
    sync_recordbook(cur["drawer_id"])
    return True


def list_nodes(drawer_id: str) -> List[dict]:
    with cabinet_conn() as c:
        rows = c.execute(
            "SELECT * FROM cabinet_nodes WHERE drawer_id=? ORDER BY namespace, label",
            (drawer_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def add_edge(drawer_id: str, source: str, target: str,
             relation: str = "") -> dict:
    if get_drawer(drawer_id) is None:
        raise ValueError(f"抽屉不存在：{drawer_id}")
    # B2：校验源/目标节点存在（允许跨抽屉引用，但禁止指向不存在的悬空节点）
    if get_node(source) is None:
        raise ValueError(f"源节点不存在：{source}")
    if get_node(target) is None:
        raise ValueError(f"目标节点不存在：{target}")
    now = _now_iso()
    edge_id = f"{drawer_id}::{source}->{target}"
    with cabinet_conn() as c:
        # B1：幂等——同一条边重复添加不抛异常（INSERT OR IGNORE）
        c.execute(
            "INSERT OR IGNORE INTO cabinet_edges "
            "(edge_id, drawer_id, source, target, relation, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (edge_id, drawer_id, source, target, relation, now),
        )
    return {"edge_id": edge_id, "drawer_id": drawer_id,
            "source": source, "target": target, "relation": relation}


def delete_edge(edge_id: str) -> bool:
    with cabinet_conn() as c:
        cur = c.execute(
            "SELECT edge_id FROM cabinet_edges WHERE edge_id=?", (edge_id,)
        ).fetchone()
        if cur is None:
            return False
        c.execute("DELETE FROM cabinet_edges WHERE edge_id=?", (edge_id,))
    return True


def prune_orphan_edges(drawer_id: str, delete: bool = False) -> List[dict]:
    """B3：检测（可选清理）指向不存在节点的孤儿边。

    delete=False 仅返回孤儿边列表（只读诊断）；delete=True 一并清理。
    """
    orphans = []
    with cabinet_conn() as c:
        rows = c.execute(
            "SELECT edge_id, source, target FROM cabinet_edges WHERE drawer_id=?",
            (drawer_id,),
        ).fetchall()
        for r in rows:
            src = _row(c, "cabinet_nodes", "node_id", r["source"])
            tgt = _row(c, "cabinet_nodes", "node_id", r["target"])
            if src is None or tgt is None:
                orphans.append({"edge_id": r["edge_id"],
                                "source": r["source"], "target": r["target"]})
    if delete:
        with cabinet_conn() as c:
            for o in orphans:
                c.execute("DELETE FROM cabinet_edges WHERE edge_id=?", (o["edge_id"],))
    return orphans


def list_edges(drawer_id: str) -> List[dict]:
    with cabinet_conn() as c:
        rows = c.execute(
            "SELECT * FROM cabinet_edges WHERE drawer_id=? ORDER BY created_at",
            (drawer_id,),
        ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------------------
# 记录本镜像（R/S/P 由锚定节点生成）
# --------------------------------------------------------------------------

def recordbook_path(drawer_id: str) -> Path:
    return get_home() / "index" / "drawers" / drawer_id / "记录本.md"


def recordbook_text(drawer_id: str) -> str:
    p = recordbook_path(drawer_id)
    if p.exists():
        return p.read_text(encoding="utf-8")
    # 不存在则即时生成一份
    sync_recordbook(drawer_id)
    return p.read_text(encoding="utf-8") if p.exists() else ""


def sync_recordbook(drawer_id: str) -> None:
    """由 R/S/P 三个锚定节点重生成 记录本.md。"""
    sections = []
    with cabinet_conn() as c:
        for code, heading, _ in RECORD_SECTIONS:
            row = _row(c, "cabinet_nodes", "node_id", f"{drawer_id}::{code}")
            content = row["content"] if row else ""
            sections.append(f"## {heading}\n\n{content or '（待补充）'}\n")
    title = get_drawer(drawer_id) or {"title": drawer_id}
    md = f"# {title['title']} · 记录本\n\n" + "\n".join(sections)
    p = recordbook_path(drawer_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(md, encoding="utf-8")


# --------------------------------------------------------------------------
# 解释（? 按钮）— 无模型优雅降级
# --------------------------------------------------------------------------

def explain_node(node_id: str, engine=None) -> dict:
    """取节点真实内容，喂引擎解释；无引擎→降级返回原始内容。"""
    node = get_node(node_id)
    if node is None:
        raise ValueError(f"节点不存在：{node_id}")
    content = node["content"] or node["label"]
    if engine is None:
        return {"node_id": node_id, "degraded": True,
                "explanation": content, "raw": content}
    prompt = (
        "请用通俗语言解释下面这条项目记录，不超过 100 字：\n"
        f"【{node['label']}】{content}"
    )
    try:
        text = engine.complete(prompt)
    except Exception:
        return {"node_id": node_id, "degraded": True,
                "explanation": content, "raw": content}
    return {"node_id": node_id, "degraded": False,
            "explanation": text, "raw": content}


# --------------------------------------------------------------------------
# 立项意图检测（agent 不擅自建抽屉；命中才弹卡）
# --------------------------------------------------------------------------

def detect_recommendation(text: str) -> dict:
    """检测是否应推荐「建抽屉」。命中意图且无可匹配抽屉→推荐。"""
    if not text or not text.strip():
        return {"recommended": False}
    hit = any(h in text for h in _INTENT_HINTS)
    if not hit:
        return {"recommended": False}
    # 已有抽屉其标题/kit 命中输入 → 不重复推荐
    lowered = text.lower()
    for d in list_drawers():
        if d["title"] and d["title"].lower() in lowered:
            return {"recommended": False}
    # 从文本抽取建议标题：取首句或意图后片段
    title = _suggest_title(text)
    return {"recommended": True, "title_suggestion": title}


def _suggest_title(text: str) -> str:
    # 取第一个句号/换行前的内容，截到 24 字
    first = re.split(r"[。\n!?]", text.strip(), maxsplit=1)[0]
    return first[:24] if first else "新项目"
