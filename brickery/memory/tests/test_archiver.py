"""§1 对话存档：创建、空对话不写、幂等、终确认。"""
import json
from brickery.memory import MemorySystem
from brickery.memory.db import memory_conn
from .base import BaseMemoryTest


class TestArchiver(BaseMemoryTest):
    def _count(self):
        with memory_conn() as c:
            return c.execute("SELECT COUNT(*) AS n FROM conversation_records").fetchone()["n"]

    def test_archive_creates_record(self):
        ms = MemorySystem()
        rid = ms.archive("s1", ["今天讨论了机器学习模型部署的话题"], project="ai")
        self.assertTrue(rid)
        self.assertEqual(self._count(), 1)
        with memory_conn() as c:
            r = c.execute("SELECT topic_summary, keywords, project, confirmed FROM conversation_records").fetchone()
        self.assertEqual(r["project"], "ai")
        self.assertEqual(r["confirmed"], 0)
        self.assertIsInstance(json.loads(r["keywords"]), list)

    def test_empty_conversation_not_written(self):
        ms = MemorySystem()
        rid = ms.archive("s2", ["", "   "])
        self.assertEqual(rid, "")
        self.assertEqual(self._count(), 0)

    def test_idempotent_same_session(self):
        ms = MemorySystem()
        ms.archive("s3", ["第一次提到神经网络"])
        ms.archive("s3", ["第二次提到卷积网络"])
        self.assertEqual(self._count(), 1)  # 未确认会话只更新，不重复建
        with memory_conn() as c:
            r = c.execute("SELECT keywords FROM conversation_records WHERE session_id='s3'").fetchone()
        kws = json.loads(r["keywords"])
        # 2-gram 中文分词：神经网络→神经/网络；卷积网络→卷积/网络
        self.assertIn("神经", kws)
        self.assertIn("卷积", kws)

    def test_finalize_session(self):
        ms = MemorySystem()
        ms.archive("s4", ["内容A"])
        n = ms.finalize_session("s4")
        self.assertEqual(n, 1)
        with memory_conn() as c:
            self.assertEqual(c.execute("SELECT confirmed FROM conversation_records").fetchone()["confirmed"], 1)
