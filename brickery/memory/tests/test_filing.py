"""§8 文件柜索引：检索命中、增改删触发器同步（trigram 中文子串可用）。"""
from brickery.memory import MemorySystem
from .base import BaseMemoryTest


class TestFiling(BaseMemoryTest):
    def test_index_and_search(self):
        ms = MemorySystem()
        ms.index_file("d1", "/docs/a.md", "部署指南", "机器学习模型部署实践")
        hits = ms.search_files("机器学习")
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["doc_id"], "d1")

    def test_update_syncs_index(self):
        ms = MemorySystem()
        ms.index_file("d1", "/docs/a.md", "部署指南", "机器学习模型部署实践")
        ms.update_file("d1", content="医学影像分割新方法")
        # 旧内容不可检索
        self.assertEqual(ms.search_files("机器学习"), [])
        # 新内容可检索
        self.assertEqual(len(ms.search_files("医学影像")), 1)

    def test_remove_syncs_index(self):
        ms = MemorySystem()
        ms.index_file("d1", "/docs/a.md", "标题", "机器学习部署")
        ms.remove_file("d1")
        self.assertEqual(ms.search_files("机器学习"), [])

    def test_empty_query_returns_empty(self):
        ms = MemorySystem()
        ms.index_file("d1", "/x", "标题", "内容")
        self.assertEqual(ms.search_files(""), [])
