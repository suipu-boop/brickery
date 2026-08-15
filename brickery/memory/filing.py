"""§8 文件柜索引（Filing Cabinet FTS5）。

对本地文件柜建全文索引，支撑文件级精准召回，与对话记忆打通。
- 使用 FTS5 外部内容表 + 触发器：INSERT/UPDATE/DELETE 自动同步索引（§8 验收）。
- 索引与内容分离：重建索引不破坏内容表。
- 文件内容只在本机 BRICKERY_HOME 体系内，不向外发（§8 红线）。
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from .db import filing_conn

_FTS = "filing_fts"
_INDEX = "filing_index"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def index_file(doc_id: str, path: str, title: str, content: str) -> None:
    with filing_conn() as c:
        c.execute(
            f"INSERT INTO {_INDEX} (doc_id, path, title, content, updated_at) "
            "VALUES (?,?,?,?,?)",
            (doc_id, path, title, content, _now_iso()),
        )


def update_file(doc_id: str, title: Optional[str] = None,
                content: Optional[str] = None, path: Optional[str] = None) -> None:
    with filing_conn() as c:
        row = c.execute(
            f"SELECT doc_id FROM {_INDEX} WHERE doc_id=?", (doc_id,)
        ).fetchone()
        if row is None:
            return
        new_title = title if title is not None else row_doc(c, doc_id)["title"]
        new_path = path if path is not None else row_doc(c, doc_id)["path"]
        new_content = content if content is not None else row_doc(c, doc_id)["content"]
        c.execute(
            f"UPDATE {_INDEX} SET title=?, content=?, path=?, updated_at=? WHERE doc_id=?",
            (new_title, new_content, new_path, _now_iso(), doc_id),
        )


def row_doc(c, doc_id):
    return c.execute(
        f"SELECT title, content, path FROM {_INDEX} WHERE doc_id=?", (doc_id,)
    ).fetchone()


def remove_file(doc_id: str) -> None:
    with filing_conn() as c:
        c.execute(f"DELETE FROM {_INDEX} WHERE doc_id=?", (doc_id,))


def search_files(query: str, limit: int = 10) -> List[dict]:
    """全文检索文件柜。返回命中文档（含 snippet）。"""
    if not query or not query.strip():
        return []
    with filing_conn() as c:
        rows = c.execute(
            f"SELECT i.doc_id, i.path, i.title, snippet({_FTS}, 1, '[', ']', '…', 12) AS snippet "
            f"FROM {_FTS} f JOIN {_INDEX} i ON i.rowid = f.rowid "
            f"WHERE {_FTS} MATCH ? ORDER BY rank LIMIT ?",
            (query, limit),
        ).fetchall()
    return [dict(r) for r in rows]
