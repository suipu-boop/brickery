"""§2 工具注册与筛选单测。"""
from brickery.runtime.tools import Tool, ToolRegistry
from .base import RuntimeTestCase


class TestTools(RuntimeTestCase):
    def _reg(self):
        r = ToolRegistry()
        r.register_many([
            Tool("search", "网络检索", keywords=["搜索", "检索", "查找"]),
            Tool("calc", "数学计算", keywords=["计算", "数学", "算式"]),
            Tool("translate", "翻译", keywords=["翻译", "语言"]),
        ])
        return r

    def test_select_relevant_only(self):
        r = self._reg()
        sel = r.select("帮我搜索一些资料")
        names = [t.name for t in sel]
        self.assertIn("search", names)
        self.assertNotIn("calc", names)
        self.assertNotIn("translate", names)

    def test_empty_registry(self):
        r = ToolRegistry()
        self.assertEqual(r.select("任何上下文"), [])

    def test_always_available(self):
        r = ToolRegistry()
        r.register(Tool("chat", "对话", always_available=True))
        self.assertEqual([t.name for t in r.select("无关内容")], ["chat"])

    def test_keyword_change_affects(self):
        r = ToolRegistry()
        r.register(Tool("search", "检索", keywords=["搜索"]))
        self.assertEqual([t.name for t in r.select("帮我搜索")], ["search"])
        r.register(Tool("search", "检索", keywords=["绘画"]))  # 改关键词
        self.assertEqual(r.select("帮我搜索"), [])
        self.assertEqual([t.name for t in r.select("帮我绘画")], ["search"])
