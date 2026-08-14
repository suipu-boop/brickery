"""记忆子系统单测基类：每个测试用独立临时 BRICKERY_HOME，全隔离。"""
import os
import shutil
import tempfile
import unittest


class BaseMemoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="shadeling_test_")
        os.environ["BRICKERY_HOME"] = self.tmp

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class MockEngine:
    """测试用引擎桩：记录调用、返回固定文本，绝不发起真实网络推理。"""

    def __init__(self, reply="SUMMARY: mock摘要\nKEYWORDS: alpha,beta"):
        self.reply = reply
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        return self.reply
