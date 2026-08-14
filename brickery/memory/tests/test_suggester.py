"""§6 主动推送：分级、空上下文安静返回、反馈闭环影响排序。"""
from brickery.memory import MemorySystem
from .base import BaseMemoryTest


class TestSuggester(BaseMemoryTest):
    def test_suggest_returns_graded(self):
        ms = MemorySystem()
        rid = ms.archive("a", ["机器学习模型部署实践"])
        res = ms.suggest("机器学习")
        self.assertTrue(res)
        self.assertEqual(res[0]["record_id"], rid)
        self.assertIn(res[0]["grade"], ("weak", "medium", "strong"))

    def test_empty_context_returns_empty(self):
        ms = MemorySystem()
        ms.archive("a", ["机器学习模型部署"])
        self.assertEqual(ms.suggest(""), [])
        self.assertEqual(ms.suggest("   "), [])

    def test_feedback_affects_ranking_factor(self):
        ms = MemorySystem()
        rid = ms.archive("a", ["机器学习模型部署"])
        before = ms.suggest("机器学习")[0]["feedback_factor"]
        self.assertEqual(before, 1.0)
        ms.record_feedback(rid, "accept")
        after = ms.suggest("机器学习")[0]["feedback_factor"]
        self.assertGreater(after, before)
        # ignore 应降低
        ms.record_feedback(rid, "ignore")
        low = ms.suggest("机器学习")[0]["feedback_factor"]
        self.assertLess(low, after)

    def test_feedback_rejects_invalid(self):
        ms = MemorySystem()
        rid = ms.archive("a", ["机器学习模型部署"])
        with self.assertRaises(ValueError):
            ms.record_feedback(rid, "maybe")


class FakeShadowPick:
    """影子桩：按索引挑候选（decide_surface 返回 record_id 列表）。"""

    def __init__(self, pick_idx):
        self.pick_idx = set(pick_idx)

    def consolidate(self, texts):
        return {"entities": [], "decisions": [], "todos": []}

    def decide_surface(self, query, candidates):
        return [candidates[i]["record_id"] for i in self.pick_idx
                if 0 <= i < len(candidates)]


class TestSuggesterShadow(BaseMemoryTest):
    """蓝图 A 档：§6 主动推送应接回影子判断过滤候选。"""

    def _setup(self):
        ms = MemorySystem()
        rid_a = ms.archive("a", ["机器学习模型部署实践"])
        rid_b = ms.archive("b", ["机器学习晚饭安排"])
        return ms, rid_a, rid_b

    def test_shadow_filters_suggestions(self):
        ms, rid_a, _ = self._setup()
        shadow = FakeShadowPick([0])  # 只想要机器学习那条
        res = ms.suggest("机器学习", shadow=shadow)
        self.assertEqual([r["record_id"] for r in res], [rid_a])

    def test_no_shadow_returns_all(self):
        ms, rid_a, rid_b = self._setup()
        res = ms.suggest("机器学习")  # 无影子 → 规则全量
        self.assertEqual(set(r["record_id"] for r in res), {rid_a, rid_b})
