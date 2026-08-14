"""§7 会话存储（clean room，纯自研）。

会话是**运行时**产物（一次次对话的流水），与 memory 的**长期**产物（对话影 / 画像 /
聚类）刻意分库：schema 不混、生命周期不同、删会话不伤记忆。

设计取舍：前代规格明确记录过一条已知限制 ——「会话为内存存储，服务重启即丢」。
本模块不复刻该缺陷，直接落盘 SQLite。

红线：
- 只用标准库 sqlite3，不引第三方。
- 任何返回「会话对象」的方法都返回**完整形状**（含 messages 键），
  不返回半截对象，避免前端解码失败。
- 不预置任何示例会话（空白系统原则）。
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Iterator, List, Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    project     TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL,
    text        TEXT NOT NULL DEFAULT '',
    ts          TEXT NOT NULL,
    used_tools  TEXT NOT NULL DEFAULT '[]',
    used_skills TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
"""

_TITLE_MAX = 20


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _title_from(text: str) -> str:
    """用首条用户消息前若干字做标题；空则给中性默认名。"""
    t = (text or "").strip().replace("\n", " ")
    if not t:
        return "新会话"
    return t[:_TITLE_MAX] + ("…" if len(t) > _TITLE_MAX else "")


class SessionStore:
    """会话与消息的落盘存储。线程内串行使用；每次操作独立连接，避免跨线程共享游标。"""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._conn() as c:
            c.executescript(_SCHEMA)
            # 迁移：旧库 sessions 表无 profile_id 列（2026-08-13 新增
            # 「每会话独立绑定模型预设」）。CREATE TABLE IF NOT EXISTS 不会给
            # 已存在的表加列，这里用 PRAGMA 检测 + ALTER 补齐，旧会话默认 ''。
            cols = {r["name"] for r in c.execute(
                "PRAGMA table_info(sessions)").fetchall()}
            if "profile_id" not in cols:
                c.execute("ALTER TABLE sessions ADD COLUMN "
                          "profile_id TEXT NOT NULL DEFAULT ''")
                c.commit()

    @contextlib.contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        """开一条连接，退出时**提交并关闭**。

        注意：sqlite3.Connection 自身的 with 语义只管事务提交/回滚，**不关连接**。
        直接 `with sqlite3.connect(...)` 会在长跑守护进程里按请求泄漏文件描述符
        （Python 会报 ResourceWarning: unclosed database）。这里显式收口。
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")  # 与记忆层同一套连接习惯
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ----- 会话 -----

    def create(self, title: str = "", project: str = "",
                profile_id: str = "") -> dict:
        sid = "sess_" + uuid.uuid4().hex[:12]
        now = _now()
        with self._conn() as c:
            c.execute(
                "INSERT INTO sessions(id,title,project,created_at,updated_at,profile_id)"
                " VALUES(?,?,?,?,?,?)",
                (sid, title or "新会话", project, now, now, profile_id))
            c.commit()
        return self.get(sid) or {}

    def ensure(self, session_id: Optional[str], project: str = "",
               profile_id: str = "") -> str:
        """取已有会话 id；不存在则新建。供 chat 首轮无 session_id 时使用。

        profile_id 非空时：若会话已存在则更新其绑定；新建时写入绑定。
        """
        if session_id:
            with self._conn() as c:
                row = c.execute("SELECT id FROM sessions WHERE id=?",
                                (session_id,)).fetchone()
            if row:
                if profile_id:
                    self.set_profile_id(session_id, profile_id)
                return session_id
            # 显式传了 id 但库里没有：按该 id 建，保持调用方语义
            now = _now()
            with self._conn() as c:
                c.execute(
                    "INSERT INTO sessions(id,title,project,created_at,updated_at,profile_id)"
                    " VALUES(?,?,?,?,?,?)",
                    (session_id, "新会话", project, now, now, profile_id))
                c.commit()
            return session_id
        return self.create(project=project, profile_id=profile_id)["id"]

    def get(self, session_id: str) -> Optional[dict]:
        """返回完整形状：元数据 + messages 列表。"""
        with self._conn() as c:
            row = c.execute("SELECT * FROM sessions WHERE id=?",
                            (session_id,)).fetchone()
            if row is None:
                return None
            msgs = c.execute(
                "SELECT * FROM messages WHERE session_id=? ORDER BY ts,rowid",
                (session_id,)).fetchall()
        return {
            "id": row["id"],
            "title": row["title"],
            "project": row["project"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "profile_id": row["profile_id"] or "",
            "rounds": sum(1 for m in msgs if m["role"] == "user"),
            "messages": [self._msg_dict(m) for m in msgs],
        }

    def set_profile_id(self, session_id: str, profile_id: str) -> None:
        """持久化某会话绑定的模型预设（每会话独立绑定）。"""
        if not session_id:
            return
        with self._conn() as c:
            c.execute("UPDATE sessions SET profile_id=?,updated_at=? WHERE id=?",
                      (profile_id, _now(), session_id))
            c.commit()

    def profile_id_of(self, session_id: str) -> str:
        """读取某会话已绑定的模型预设 id（空=跟随全局 active）。"""
        if not session_id:
            return ""
        with self._conn() as c:
            row = c.execute("SELECT profile_id FROM sessions WHERE id=?",
                            (session_id,)).fetchone()
        return row["profile_id"] if row and row["profile_id"] else ""

    def list(self) -> List[dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT s.*,"
                " (SELECT COUNT(*) FROM messages m"
                "   WHERE m.session_id=s.id AND m.role='user') AS rounds"
                " FROM sessions s ORDER BY s.updated_at DESC").fetchall()
        return [{
            "id": r["id"], "title": r["title"], "project": r["project"],
            "created_at": r["created_at"], "updated_at": r["updated_at"],
            "profile_id": r["profile_id"] or "",
            "rounds": r["rounds"],
        } for r in rows]

    def rename(self, session_id: str, title: str) -> Optional[dict]:
        with self._conn() as c:
            c.execute("UPDATE sessions SET title=?,updated_at=? WHERE id=?",
                      (title, _now(), session_id))
            c.commit()
        return self.get(session_id)

    def delete(self, session_id: str) -> bool:
        with self._conn() as c:
            cur = c.execute("DELETE FROM sessions WHERE id=?", (session_id,))
            c.execute("DELETE FROM messages WHERE session_id=?", (session_id,))
            c.commit()
            return cur.rowcount > 0

    # ----- 消息 -----

    def append(self, session_id: str, role: str, text: str,
               used_tools: Optional[List[str]] = None,
               used_skills: Optional[List[str]] = None) -> dict:
        mid = "msg_" + uuid.uuid4().hex[:12]
        now = _now()
        with self._conn() as c:
            c.execute(
                "INSERT INTO messages(id,session_id,role,text,ts,used_tools,used_skills)"
                " VALUES(?,?,?,?,?,?,?)",
                (mid, session_id, role, text, now,
                 json.dumps(used_tools or [], ensure_ascii=False),
                 json.dumps(used_skills or [], ensure_ascii=False)))
            # 首条用户消息顺带把默认标题改成摘要
            if role == "user":
                row = c.execute("SELECT title FROM sessions WHERE id=?",
                                (session_id,)).fetchone()
                if row is not None and row["title"] in ("", "新会话"):
                    c.execute("UPDATE sessions SET title=? WHERE id=?",
                              (_title_from(text), session_id))
            c.execute("UPDATE sessions SET updated_at=? WHERE id=?",
                      (now, session_id))
            c.commit()
        return {"id": mid, "session_id": session_id, "role": role,
                "text": text, "ts": now,
                "used_tools": used_tools or [], "used_skills": used_skills or []}

    def history(self, session_id: str, limit: int = 20) -> List[dict]:
        """取最近若干条消息（时间正序），供多轮上下文使用。"""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM messages WHERE session_id=?"
                " ORDER BY ts DESC,rowid DESC LIMIT ?",
                (session_id, limit)).fetchall()
        return [self._msg_dict(r) for r in reversed(rows)]

    def count(self) -> int:
        with self._conn() as c:
            return c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]

    @staticmethod
    def _msg_dict(r: sqlite3.Row) -> dict:
        def _load(s: str) -> List[str]:
            try:
                v = json.loads(s)
                return v if isinstance(v, list) else []
            except (json.JSONDecodeError, TypeError):
                return []
        return {"id": r["id"], "session_id": r["session_id"], "role": r["role"],
                "text": r["text"], "ts": r["ts"],
                "used_tools": _load(r["used_tools"]),
                "used_skills": _load(r["used_skills"])}
