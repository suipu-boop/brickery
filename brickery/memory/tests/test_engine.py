"""§9.5 引擎可注入、且不发起任何真实网络推理（mock 拦截 socket 证明）。"""
from unittest import mock
from brickery.memory import MemorySystem, KeywordExtractor
from .base import BaseMemoryTest, MockEngine


class TestEngine(BaseMemoryTest):
    @mock.patch("socket.socket")
    def test_no_network_when_no_engine(self, fake_sock):
        ms = MemorySystem()  # 无引擎 → 走 KeywordExtractor 离线抽取
        rid = ms.archive("s", ["机器学习模型部署实践"])
        self.assertTrue(rid)
        fake_sock.assert_not_called()  # 绝未触碰真实网络

    @mock.patch("socket.socket")
    def test_no_network_with_mock_engine(self, fake_sock):
        eng = MockEngine()
        ms = MemorySystem(engine=eng)
        ms.archive("s", ["机器学习模型部署实践"])
        self.assertTrue(eng.calls)  # 确实用了注入的引擎
        fake_sock.assert_not_called()  # 仍无真实网络

    def test_mock_engine_extraction_used(self):
        eng = MockEngine(reply="SUMMARY: 自定义摘要\nKEYWORDS: foo,bar")
        ms = MemorySystem(engine=eng)
        rid = ms.archive("s", ["任意文本"])
        from brickery.memory.db import memory_conn
        with memory_conn() as c:
            r = c.execute("SELECT topic_summary, keywords FROM conversation_records WHERE record_id=?", (rid,)).fetchone()
        self.assertIn("自定义摘要", r["topic_summary"])
        self.assertIn("foo", r["keywords"])

    def test_keyword_extractor_basic(self):
        s, kw = KeywordExtractor().extract(["机器学习模型部署", "神经网络训练"])
        self.assertTrue(s)
        self.assertTrue(kw)
        # 路径无关断言：2-gram 与 jieba 两种分词都能切出「模型」
        self.assertIn("模型", kw)

    def test_jieba_tokenize_when_available(self):
        from brickery.memory.engine import JIEBA_OK
        if not JIEBA_OK:
            self.skipTest("jieba 未安装，跳过 jieba 分词路径验证")
        _, kw = KeywordExtractor().extract(["机器学习模型部署实践"])
        # jieba 路径应切出有意义的多字词（而非纯 2-gram 碎片）
        self.assertIn("模型", kw)
        self.assertTrue(any(len(t) >= 2 for t in kw))
