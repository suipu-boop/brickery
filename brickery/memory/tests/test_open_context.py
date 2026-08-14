"""§跨会话记忆：新会话开场上下文 open_session_context()。

验证：空库返回空串；有 confirmed 记录时返回「近期会话」摘要 + 「近期待办」；
limit 限制会话条数；todos 为 '[]' 时只呈现会话摘要。
"""
from brickery.memory import MemorySystem
from brickery.memory.db import memory_conn
from .base import BaseMemoryTest


class TestOpenSessionContext(BaseMemoryTest):
    def _seed(self, rows):
        """插入若干 confirmed 会话记录（topic_summary / keywords / todos）。"""
        with memory_conn() as c:
            for i, (summary, kws, todos) in enumerate(rows):
                c.execute(
                    "INSERT INTO conversation_records "
                    "(record_id, session_id, time_range, topic_summary, keywords, todos, "
                    "file_refs, importance, project, created_at, last_active, confirmed) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
                    (f"r{i}", f"s{i}", "t..t", summary, kws, todos,
                     "[]", 0.5, "", f"2026-08-0{i+1}T00:00:00",
                     f"2026-08-0{i+1}T00:00:00"))

    def test_empty_returns_blank(self):
        ms = MemorySystem()
        self.assertEqual(ms.open_session_context(), "")

    def test_returns_sessions_and_todos(self):
        ms = MemorySystem()
        self._seed([
            ("讨论了DocWrite工具实现", '["docwrite","文档"]', '["写技能市场规划"]'),
            ("研究了跨会话记忆", '["记忆","失忆"]', '["部署GitHub市场"]'),
        ])
        out = ms.open_session_context()
        self.assertIn("【近期会话】", out)
        self.assertIn("讨论了DocWrite工具实现", out)
        self.assertIn("【近期待办】", out)
        self.assertIn("写技能市场规划", out)
        self.assertIn("部署GitHub市场", out)

    def test_only_todos_when_present(self):
        ms = MemorySystem()
        self._seed([("会话A摘要", "[]", '["待办X"]')])
        out = ms.open_session_context()
        self.assertIn("【近期会话】", out)
        self.assertIn("会话A摘要", out)
        self.assertIn("【近期待办】", out)
        self.assertIn("待办X", out)

    def test_limit_caps_session_count(self):
        ms = MemorySystem()
        self._seed([
            ("s1", "[]", "[]"),
            ("s2", "[]", "[]"),
            ("s3", "[]", "[]"),
            ("s4", "[]", "[]"),
        ])
        out = ms.open_session_context(limit=2)
        # nightly_pending_sessions 的 text 形如「会话 sN 摘要：...」（sN 是 session_id）
        self.assertEqual(out.count("会话 s"), 2)

    def test_todos_dedup_and_cap(self):
        ms = MemorySystem()
        self._seed([
            ("s1", "[]", '["重复","唯一A"]'),
            ("s2", "[]", '["重复","唯一B"]'),
        ])
        out = ms.open_session_context(max_todos=3)
        self.assertIn("重复", out)
        self.assertIn("唯一A", out)
        self.assertIn("唯一B", out)
        # 去重后至多 max_todos 条；这里 3 条（重复/唯一A/唯一B）刚好
        todo_block = out.split("【近期待办】", 1)[1]
        self.assertLessEqual(todo_block.count("- "), 3)
