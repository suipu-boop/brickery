"""§2 精准召回：跨会话、项目过滤、时间衰减。"""
from datetime import datetime, timezone
from brickery.memory import MemorySystem
from .base import BaseMemoryTest


class TestRecall(BaseMemoryTest):
    def test_recall_across_sessions(self):
        ms = MemorySystem()
        ms.archive("a", ["机器学习模型部署实践"], now="2026-01-01T00:00:00+00:00")
        ms.archive("b", ["医学影像分割方法"], now="2026-01-01T00:00:00+00:00")
        res = ms.recall("机器学习", now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        self.assertTrue(res)
        self.assertEqual(res[0]["session_id"], "a")
        # 不相关会话不应出现
        self.assertNotIn("b", [r["session_id"] for r in res])

    def test_project_filter(self):
        ms = MemorySystem()
        ms.archive("a", ["机器学习模型部署"], project="ai")
        ms.archive("b", ["机器学习训练技巧"], project="med")
        res = ms.recall("机器学习", project="ai")
        self.assertEqual([r["session_id"] for r in res], ["a"])

    def test_time_decay_ranks_recent_first(self):
        ms = MemorySystem()
        ms.archive("old", ["机器学习模型部署"], now="2026-01-01T00:00:00+00:00")
        ms.archive("new", ["机器学习训练技巧"], now="2026-08-01T00:00:00+00:00")
        res = ms.recall("机器学习", now=datetime(2026, 8, 1, tzinfo=timezone.utc))
        self.assertEqual(res[0]["session_id"], "new")
        self.assertGreater(res[0]["score"], res[1]["score"])

    def test_no_match_returns_empty(self):
        ms = MemorySystem()
        ms.archive("a", ["机器学习模型部署"], now="2026-01-01T00:00:00+00:00")
        self.assertEqual(ms.recall("天文学恒星"), [])
