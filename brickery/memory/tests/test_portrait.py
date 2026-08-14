"""§5 用户画像 + 保守更新红线（含 §9 端到端矛盾测试）。"""
from brickery.memory import MemorySystem
from .base import BaseMemoryTest


class TestPortrait(BaseMemoryTest):
    def test_insert_new(self):
        ms = MemorySystem()
        r = ms.update_portrait("role", "医生", evidence="自述双肩挑", confidence=0.8)
        self.assertEqual(r["status"], "inserted")
        p = ms.get_portrait("role")
        self.assertEqual(len(p), 1)
        self.assertEqual(p[0]["value"], "医生")
        self.assertEqual(p[0]["confidence"], 0.8)
        self.assertIn("自述双肩挑", p[0]["evidence"])

    def test_merge_same_value_keeps_max_conf_and_appends_evidence(self):
        ms = MemorySystem()
        ms.update_portrait("role", "医生", evidence="自述A", confidence=0.8)
        r = ms.update_portrait("role", "医生", evidence="自述B", confidence=0.5)
        self.assertEqual(r["status"], "merged")
        p = ms.get_portrait("role")[0]
        self.assertEqual(p["confidence"], 0.8)  # 不降
        self.assertIn("自述A", p["evidence"])
        self.assertIn("自述B", p["evidence"])

    def test_conflict_labels_not_overwrites(self):
        """§9 端到端：先写 A，再写矛盾的 B → A 原值保留、B 以低置信候选存在、confidence 未降。"""
        ms = MemorySystem()
        ms.update_portrait("city", "商丘", evidence="身份证地址", confidence=0.9)
        r = ms.update_portrait("city", "郑州", evidence="新提到的城市", confidence=0.95)
        self.assertEqual(r["status"], "conflict")

        p = {x["value"]: x for x in ms.get_portrait("city")}
        # A 原值保留
        self.assertIn("商丘", p)
        self.assertEqual(p["商丘"]["confidence"], 0.9)  # 未降
        # 主导行记录了矛盾标注
        self.assertTrue(p["商丘"]["contradictions"])
        self.assertEqual(p["商丘"]["contradictions"][0]["conflicting_value"], "郑州")
        # B 作为低置信候选存在，被上限封顶
        self.assertIn("郑州", p)
        self.assertLessEqual(p["郑州"]["confidence"], 0.3)

    def test_lower_confidence_does_not_downgrade_existing(self):
        ms = MemorySystem()
        ms.update_portrait("lang", "中文", confidence=0.9)
        ms.update_portrait("lang", "中文", confidence=0.3)
        self.assertEqual(ms.get_portrait("lang")[0]["confidence"], 0.9)
