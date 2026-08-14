"""导出功能单测（O9）。"""
import json
import unittest

from brickery.memory import MemorySystem
from brickery.memory.export_utils import to_markdown, to_json
from .base import BaseMemoryTest


class TestExport(BaseMemoryTest):
    def test_export_all_structure(self):
        ms = MemorySystem()
        ms.update_portrait("名字", "随朴", evidence="自述")
        ms.create_drawer("d1", "测试抽屉")
        ms.add_node("d1", "anchor", "随朴", content="医生")
        ms.add_node("d1", "resource", "医专", content="学校")

        bundle = ms.export_all(include_core=False)
        self.assertEqual(bundle["schema"], "shadeling-memory-export/v1")
        self.assertIn("portrait", bundle)
        self.assertIn("drawers", bundle)
        self.assertIn("conversations", bundle)
        self.assertIsInstance(bundle["conversations"], list)
        # 画像已写入
        attrs = [p["attribute"] for p in bundle["portrait"]]
        self.assertIn("名字", attrs)
        # 抽屉已写入且含节点
        self.assertEqual(len(bundle["drawers"]), 1)
        self.assertGreaterEqual(len(bundle["drawers"][0]["nodes"]), 1)

    def test_export_core_respects_flag(self):
        ms = MemorySystem()
        ms.set_core("原则", "从一开始就决定好")
        # 默认不含固定核
        self.assertIsNone(ms.export_all(include_core=False).get("core"))
        # 显式含
        core = ms.export_all(include_core=True)["core"]
        self.assertIsNotNone(core)
        self.assertEqual(core.get("原则"), "从一开始就决定好")

    def test_to_markdown_and_json(self):
        ms = MemorySystem()
        ms.update_portrait("a", "b")
        bundle = ms.export_all()
        md = to_markdown(bundle)
        self.assertIn("# Shadeling 记忆导出", md)
        self.assertIn("用户画像", md)
        js = to_json(bundle)
        parsed = json.loads(js)
        self.assertEqual(parsed["schema"], "shadeling-memory-export/v1")


if __name__ == "__main__":
    unittest.main()
