"""§3 语义聚类：同主题归簇、异主题独立、失败降级。"""
import json
from brickery.memory import MemorySystem
from brickery.memory.db import memory_conn
from .base import BaseMemoryTest


class TestClusters(BaseMemoryTest):
    def _members(self, cid):
        with memory_conn() as c:
            r = c.execute("SELECT member_records FROM semantic_clusters WHERE cluster_id=?", (cid,)).fetchone()
        return json.loads(r["member_records"]) if r and r["member_records"] else []

    def test_same_topic_clusters_together(self):
        ms = MemorySystem()
        c1 = ms.cluster("r1", ["机器学习", "神经网络", "深度学习"])
        c2 = ms.cluster("r2", ["机器学习", "卷积网络", "训练"])
        c3 = ms.cluster("r3", ["神经网络", "深度学习", "模型"])
        self.assertEqual(c1, c2)
        self.assertEqual(c2, c3)
        self.assertEqual(len(self._members(c1)), 3)

    def test_different_topic_new_cluster(self):
        ms = MemorySystem()
        c1 = ms.cluster("r1", ["机器学习", "神经网络"])
        c2 = ms.cluster("r2", ["烹饪", "菜谱", "火候"])
        self.assertNotEqual(c1, c2)
        self.assertEqual(len(self._members(c2)), 1)

    def test_cluster_record_does_not_throw_on_bad_input(self):
        ms = MemorySystem()
        # 空关键词也应安全产出独立簇
        cid = ms.cluster("rX", [])
        self.assertTrue(cid)
        self.assertEqual(len(self._members(cid)), 1)
