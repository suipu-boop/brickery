"""§3.6 固定核单测：空态 / 手填 / 导出不含核（O8 / O9）。

O8 铁律：纯用户手填，无任何自动填逻辑。O9：导出默认不含核。
"""
from brickery.memory import MemorySystem
from .base import BaseMemoryTest


class TestFixedCore(BaseMemoryTest):
    def test_empty_by_default(self):
        ms = MemorySystem()
        self.assertFalse(ms.has_core())
        self.assertIsNone(ms.get_core("anything"))
        self.assertEqual(ms.get_core(), {})

    def test_set_and_get(self):
        ms = MemorySystem()
        ms.set_core("whoami", "随朴，商丘医生")
        self.assertTrue(ms.has_core())
        self.assertEqual(ms.get_core("whoami"), "随朴，商丘医生")
        self.assertEqual(ms.get_core()["whoami"], "随朴，商丘医生")

    def test_empty_value_deletes(self):
        ms = MemorySystem()
        ms.set_core("x", "v")
        ms.set_core("x", "")
        self.assertIsNone(ms.get_core("x"))

    def test_export_core_default_excludes(self):
        ms = MemorySystem()
        ms.set_core("whoami", "随朴")
        # O9：默认不含核
        self.assertIsNone(ms.export_core(include_core=False))
        # 显式勾选才含
        self.assertEqual(ms.export_core(include_core=True), {"whoami": "随朴"})

    def test_no_auto_fill(self):
        # 铁律：没有任何自动填核的逻辑；空态即无核
        ms = MemorySystem()
        self.assertFalse(ms.has_core())

    def test_smart_slot_first_write_capped(self):
        # A1/A3：首次写入置信度打折（≤0.7），不轻易 0.9
        ms = MemorySystem()
        ok = ms.set_smart_slot("偏好A", "喜欢简洁", confidence=0.9)
        self.assertTrue(ok)
        slots = ms.get_smart_slots()
        self.assertEqual(len(slots), 1)
        self.assertLessEqual(slots[0]["confidence"], 0.7)
        self.assertEqual(slots[0]["hit_count"], 1)

    def test_smart_slot_not_monotonic(self):
        # A1：同 label 更新走证据融合（0.6*旧+0.4*新），不再单调 MAX 只增
        ms = MemorySystem()
        ms.set_smart_slot("X", "v1", confidence=0.9)   # 首次 → 0.7
        ms.set_smart_slot("X", "v2", confidence=0.5)   # 融合 → 0.6*0.7+0.4*0.5 = 0.62
        slots = ms.get_smart_slots()
        self.assertAlmostEqual(slots[0]["confidence"], 0.62, places=4)
        self.assertEqual(slots[0]["hit_count"], 2)
        # 纠错（低置信新证据）应使置信度下降，而非上升
        ms.set_smart_slot("X", "v3", confidence=0.1)
        self.assertLess(ms.get_smart_slots()[0]["confidence"], slots[0]["confidence"])

    def test_smart_slot_decay(self):
        # A1：旧 last_seen 读取时实时衰减展示置信度
        ms = MemorySystem()
        ms.set_smart_slot("Y", "v", confidence=0.9, now="2026-01-01T00:00:00+00:00")
        slots = ms.get_smart_slots()
        # 当前(2026-08)距 1 月约 7 个月(>210天) → 衰减远小于原始 0.7
        self.assertLess(slots[0]["confidence"], 0.7)
        self.assertGreater(slots[0]["confidence"], 0.0)

    def test_delete_smart_slot(self):
        # A4：暴露删除单条智能槽入口（用户/UI 可否决机器猜测）
        ms = MemorySystem()
        ms.set_smart_slot("Z", "v")
        self.assertEqual(len(ms.get_smart_slots()), 1)
        self.assertTrue(ms.delete_smart_slot("Z"))
        self.assertEqual(len(ms.get_smart_slots()), 0)

    def test_get_all_core_text_priority(self):
        # A2：手动槽超长时智能槽被明确省略（不静默丢），且提示可见
        ms = MemorySystem()
        for i in range(60):
            ms.set_core(f"k{i}", "x" * 40)
        ms.set_smart_slot("auto1", "自动内容")
        text = ms.get_all_core_text()
        self.assertIn("（固定核手动部分已截断", text)  # 手动槽自身截断提示
        self.assertNotIn("auto1", text)                # 智能槽被整体省略，无半截无提示
