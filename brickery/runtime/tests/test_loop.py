"""§1 主循环单测：存档 / 出错不崩 / 可中断保留存档。"""
from unittest.mock import MagicMock

from brickery.runtime.config import EngineConfig
from brickery.runtime.engine_router import EngineRouter, NoEngineConfigured
from brickery.runtime.loop import AgentLoop
from brickery.runtime.skills import SkillRegistry
from brickery.runtime.tools import ToolRegistry
from .base import RuntimeTestCase


class TestLoop(RuntimeTestCase):
    def _loop(self, engine, memory=None):
        mem = memory if memory is not None else MagicMock()
        er = EngineRouter(EngineConfig(backend="local"), local_engine=engine)
        loop = AgentLoop(mem, er, tools=ToolRegistry(), skills=SkillRegistry())
        return loop, mem

    def test_run_archives_and_returns(self):
        mem = MagicMock()
        loop, mem = self._loop(lambda p: "回复", memory=mem)
        out = loop.run("你好")
        self.assertEqual(out, "回复")
        self.assertGreaterEqual(mem.archive.call_count, 2)  # 输入 + 回复
        args = [c.args for c in mem.archive.call_args_list]
        self.assertTrue(any("回复" in a[1] for a in args))

    def test_no_engine_friendly(self):
        mem = MagicMock()
        er = EngineRouter(EngineConfig(backend="local"))  # 无实例
        loop = AgentLoop(mem, er)
        out = loop.run("你好")
        self.assertIn("推理后端", out)
        mem.archive.assert_called()  # 输入已存档，不丢

    def test_interrupt_preserves_archive(self):
        mem = MagicMock()
        er = EngineRouter(EngineConfig(backend="local"), local_engine=lambda p: "回复")
        stop = {"flag": False}
        loop = AgentLoop(mem, er, should_stop=lambda: stop["flag"])
        stop["flag"] = True
        with self.assertRaises(InterruptedError):
            loop.run("你好")
        mem.archive.assert_called()
        args = mem.archive.call_args_list[0].args
        self.assertIn("你好", args[1])  # 用户输入已存档保留

    def test_engine_exception_friendly(self):
        mem = MagicMock()

        def boom(p):
            raise RuntimeError("推理炸了")

        loop, _ = self._loop(boom, memory=mem)
        out = loop.run("你好")
        self.assertIn("出错了", out)

    def test_open_context_injection(self):
        mem = MagicMock()
        loop, _ = self._loop(lambda p: "回复", memory=mem)
        prompt = loop._build_prompt(
            "你好", [], history=None,
            open_context_text="会话 s1 摘要：DocWrite；关键词：[]")
        self.assertIn("【近期上下文 · 新会话开场】", prompt)
        self.assertIn("会话 s1 摘要：DocWrite", prompt)

    def test_open_context_omitted_when_none(self):
        mem = MagicMock()
        loop, _ = self._loop(lambda p: "回复", memory=mem)
        prompt = loop._build_prompt("你好", [], history=None,
                                     open_context_text=None)
        self.assertNotIn("【近期上下文 · 新会话开场】", prompt)

    # --- 固定核身份引导（「认识我们」落点）---
    def test_core_text_identity_guidance(self):
        from memory.fixed_core import set_core, get_all_core_text
        set_core("assistant_name", "巴扎黑")
        set_core("user_name", "随朴")
        loop, _ = self._loop(lambda p: "回复", memory=MagicMock())
        text = loop._core_text()
        self.assertIn("你叫巴扎黑", text)
        self.assertIn("用户名叫随朴", text)
        self.assertIn("请用这些称呼对话", text)

    def test_core_text_empty_when_no_core(self):
        from memory.fixed_core import set_core
        # 清空
        set_core("assistant_name", "")
        set_core("user_name", "")
        loop, _ = self._loop(lambda p: "回复", memory=MagicMock())
        self.assertEqual(loop._core_text(), "")
