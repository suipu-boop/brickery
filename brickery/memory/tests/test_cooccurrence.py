"""§4 共现分析：计数、相关词、缺失跳过。"""
from brickery.memory import MemorySystem
from .base import BaseMemoryTest


class TestCooccurrence(BaseMemoryTest):
    def test_pair_counted(self):
        ms = MemorySystem()
        ms.cooccur(["机器学习", "神经网络"])
        ms.cooccur(["机器学习", "神经网络"])
        res = ms.related_terms("机器学习", k=5)
        self.assertIn(("神经网络", 2), res)

    def test_single_keyword_skipped(self):
        ms = MemorySystem()
        ms.cooccur(["onlyone"])  # 不足两个，应跳过不报错
        self.assertEqual(ms.related_terms("onlyone"), [])

    def test_top_k_ordering(self):
        ms = MemorySystem()
        ms.cooccur(["a", "b"])
        ms.cooccur(["a", "b"])
        ms.cooccur(["a", "c"])
        res = ms.related_terms("a", k=2)
        self.assertEqual(res[0][0], "b")
        self.assertEqual(len(res), 2)
