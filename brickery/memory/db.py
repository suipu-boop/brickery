"""Shadeling 记忆子系统 —— 三库连接与建表（全新实现，clean room）。

所有持久化路径由 config.paths 派生，绝不硬编码绝对路径。
三个数据库：
  - memory.db        对话记录 / 画像 / 聚类 / 共现 / 推送日志
  - filing.db        文件柜全文索引（FTS5，触发器同步）
  - consolidation.db 夜间巩固队列 / 审计 / 归档结果
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from brickery.runtime.paths import get_memory_db, get_filing_db, get_consolidation_db, get_cabinet_db


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def memory_conn():
    conn = _connect(get_memory_db())
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def filing_conn():
    conn = _connect(get_filing_db())
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def consolidation_conn():
    conn = _connect(get_consolidation_db())
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


@contextmanager
def cabinet_conn():
    conn = _connect(get_cabinet_db())
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


_MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_records (
  record_id   TEXT PRIMARY KEY,
  session_id  TEXT NOT NULL,
  time_range  TEXT NOT NULL,
  topic_summary TEXT NOT NULL,
  keywords    TEXT NOT NULL DEFAULT '[]',
  entities    TEXT NOT NULL DEFAULT '[]',
  decisions   TEXT NOT NULL DEFAULT '[]',
  todos       TEXT NOT NULL DEFAULT '[]',
  file_refs   TEXT NOT NULL DEFAULT '[]',
  importance  REAL NOT NULL DEFAULT 0.5,
  project     TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  last_active TEXT NOT NULL,
  confirmed   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_records_session ON conversation_records(session_id);
CREATE INDEX IF NOT EXISTS idx_records_project ON conversation_records(project);
CREATE INDEX IF NOT EXISTS idx_records_created ON conversation_records(created_at);

CREATE TABLE IF NOT EXISTS user_portrait (
  attribute     TEXT NOT NULL,
  value         TEXT NOT NULL,
  evidence      TEXT NOT NULL DEFAULT '[]',
  confidence    REAL NOT NULL DEFAULT 0.5,
  contradictions TEXT NOT NULL DEFAULT '[]',
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (attribute, value)
);
CREATE INDEX IF NOT EXISTS idx_portrait_attr ON user_portrait(attribute);

CREATE TABLE IF NOT EXISTS semantic_clusters (
  cluster_id     TEXT PRIMARY KEY,
  label          TEXT NOT NULL DEFAULT '',
  member_records TEXT NOT NULL DEFAULT '[]',
  keywords       TEXT NOT NULL DEFAULT '[]',
  created_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS co_occurrence (
  kw_a   TEXT NOT NULL,
  kw_b   TEXT NOT NULL,
  count  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (kw_a, kw_b)
);

CREATE TABLE IF NOT EXISTS access_log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  record_id   TEXT NOT NULL,
  accessed_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS push_feedback (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  item_ref  TEXT NOT NULL,
  feedback  TEXT NOT NULL,
  at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS push_events (
  id             INTEGER PRIMARY KEY AUTOINCREMENT,
  context_hash   TEXT NOT NULL,
  suggestions    TEXT NOT NULL,
  at             TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fixed_core (
  attribute  TEXT NOT NULL,
  value      TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (attribute)
);
"""

_FILING_SCHEMA = """
CREATE TABLE IF NOT EXISTS filing_index (
  doc_id     TEXT PRIMARY KEY,
  path       TEXT NOT NULL,
  title      TEXT NOT NULL,
  content    TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS filing_fts USING fts5(
  title, content, content='filing_index', content_rowid='rowid',
  tokenize='trigram'
);
CREATE TRIGGER IF NOT EXISTS filing_ai AFTER INSERT ON filing_index BEGIN
  INSERT INTO filing_fts(rowid, title, content) VALUES (new.rowid, new.title, new.content);
END;
CREATE TRIGGER IF NOT EXISTS filing_ad AFTER DELETE ON filing_index BEGIN
  INSERT INTO filing_fts(filing_fts, rowid, title, content) VALUES ('delete', old.rowid, old.title, old.content);
END;
CREATE TRIGGER IF NOT EXISTS filing_au AFTER UPDATE ON filing_index BEGIN
  INSERT INTO filing_fts(filing_fts, rowid, title, content) VALUES ('delete', old.rowid, old.title, old.content);
  INSERT INTO filing_fts(rowid, title, content) VALUES (new.rowid, new.title, new.content);
END;
"""

_CONSOLIDATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS queue (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  item_type  TEXT NOT NULL,
  payload    TEXT NOT NULL DEFAULT '{}',
  priority   INTEGER NOT NULL DEFAULT 0,
  status     TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id    TEXT NOT NULL,
  item_id   INTEGER NOT NULL,
  action    TEXT NOT NULL,
  result    TEXT NOT NULL DEFAULT '',
  at        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS nightly_results (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id    TEXT NOT NULL,
  summary   TEXT NOT NULL DEFAULT '',
  at        TEXT NOT NULL
);
"""

_CABINET_SCHEMA = """
CREATE TABLE IF NOT EXISTS cabinet_drawers (
  drawer_id  TEXT PRIMARY KEY,
  title      TEXT NOT NULL,
  kit        TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cabinet_drawers_title ON cabinet_drawers(title);

CREATE TABLE IF NOT EXISTS cabinet_nodes (
  node_id    TEXT PRIMARY KEY,
  drawer_id  TEXT NOT NULL,
  type       TEXT NOT NULL,
  label      TEXT NOT NULL DEFAULT '',
  content    TEXT NOT NULL DEFAULT '',
  namespace  TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cabinet_nodes_drawer ON cabinet_nodes(drawer_id);

CREATE TABLE IF NOT EXISTS cabinet_edges (
  edge_id    TEXT PRIMARY KEY,
  drawer_id  TEXT NOT NULL,
  source     TEXT NOT NULL,
  target     TEXT NOT NULL,
  relation   TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cabinet_edges_drawer ON cabinet_edges(drawer_id);
"""


def _migrate_memory(conn: sqlite3.Connection) -> None:
    """补齐 conversation_records 因历史 schema 演进而缺失的列（幂等、安全）。

    真实安装若从旧版本升级，conversation_records 可能缺 entities/decisions/
    todos/last_active 等列，而 archiver 会写入这些列 —— 不迁移则首次 chat 直接
    OperationalError 崩溃。这里按声明逐项 ALTER ADD COLUMN（带默认值，兼容非空约束
    与既有数据）。新增列时在此 extend 即可。
    """
    expected = {
        "entities": "TEXT NOT NULL DEFAULT '[]'",
        "decisions": "TEXT NOT NULL DEFAULT '[]'",
        "todos": "TEXT NOT NULL DEFAULT '[]'",
        "last_active": "TEXT NOT NULL DEFAULT ''",
        # R2 修正：原始（首次）摘要列，夜间归纳覆盖 topic_summary 后仍有源可溯
        "raw_summary": "TEXT NOT NULL DEFAULT ''",
    }
    have = {r["name"] for r in conn.execute(
        "PRAGMA table_info(conversation_records)").fetchall()}
    for col, ddl in expected.items():
        if col not in have:
            conn.execute(
                f"ALTER TABLE conversation_records ADD COLUMN {col} {ddl}")


def init_schemas() -> None:
    """创建全部表（幂等，可重复调用）；并对已存在旧表做增量迁移。"""
    with memory_conn() as c:
        c.executescript(_MEMORY_SCHEMA)
        _migrate_memory(c)
    with filing_conn() as c:
        c.executescript(_FILING_SCHEMA)
    with consolidation_conn() as c:
        c.executescript(_CONSOLIDATION_SCHEMA)
    with cabinet_conn() as c:
        c.executescript(_CABINET_SCHEMA)
