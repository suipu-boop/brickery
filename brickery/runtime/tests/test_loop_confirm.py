"""§3.4 确认网关单测：三态（静态 allow → MEDIUM/HIGH 交互确认 → 执行）。

采用「Mock 引擎注入预制 EngineResult」验证循环确认逻辑（不依赖真实推理）。
"""
from unittest.mock import MagicMock

from brickery.runtime.config import EngineConfig
from brickery.runtime.engine_router import EngineRouter, EngineResult, ToolCall
from brickery.runtime.loop import AgentLoop
from brickery.runtime.skills import SkillRegistry
from brickery.runtime.tools import (
    Tool, ToolRegistry, AllowAllPolicy, AutoApproveGateway,
    AutoDenyGateway, CallbackGateway, RiskLevel,
)
from .base import RuntimeTestCase


class _ScriptedToolEngine:
    def __init__(self, scripted):
        self._script = list(scripted)

    def run_turn(self, prompt, tools=None, **kw):
        if self._script:
            return self._script.pop(0)
        return EngineResult(text="（完成）")


class TestLoopConfirmation(RuntimeTestCase):
    def _loop(self, tool, *, confirmation=None, permission=None):
        reg = ToolRegistry()
        reg.register(tool)
        engine = _ScriptedToolEngine([
            EngineResult(text="", tool_calls=[ToolCall(tool.name, {"x": "1"})]),
            EngineResult(text="已处理。"),
        ])
        er = EngineRouter(EngineConfig(backend="local"), local_engine=engine)
        loop = AgentLoop(
            MagicMock(), er, tools=reg, skills=SkillRegistry(),
            permission=permission or AllowAllPolicy(),
            confirmation=confirmation,
        )
        return loop

    def _tool(self, name, risk):
        calls = {"n": 0}

        def handler(x="", **_):
            calls["n"] += 1
            return f"did:{x}"
        t = Tool(name=name, description="t", keywords=[name],
                 handler=handler, always_available=True, risk=risk)
        return t, calls

    def test_medium_auto_approve_executes(self):
        t, calls = self._tool("Edit", RiskLevel.MEDIUM)
        loop = self._loop(t, confirmation=AutoApproveGateway())
        out = loop.run("用 Edit")
        self.assertEqual(out, "已处理。")
        self.assertEqual(calls["n"], 1)
        self.assertTrue(any("调用 Edit(" in line for line in loop.last_tool_log))

    def test_medium_auto_deny_blocked(self):
        t, calls = self._tool("Edit", RiskLevel.MEDIUM)
        loop = self._loop(t, confirmation=AutoDenyGateway())
        out = loop.run("用 Edit")
        self.assertEqual(out, "已处理。")
        self.assertEqual(calls["n"], 0)
        self.assertTrue(any("被用户拒绝" in line for line in loop.last_tool_log))

    def test_medium_callback_false_blocks(self):
        t, calls = self._tool("Edit", RiskLevel.MEDIUM)
        loop = self._loop(t, confirmation=CallbackGateway(lambda tc, tool: False))
        loop.run("用 Edit")
        self.assertEqual(calls["n"], 0)

    def test_medium_callback_true_executes(self):
        t, calls = self._tool("Edit", RiskLevel.MEDIUM)
        loop = self._loop(t, confirmation=CallbackGateway(lambda tc, tool: True))
        loop.run("用 Edit")
        self.assertEqual(calls["n"], 1)

    def test_low_bypasses_deny_gateway(self):
        # LOW 风险工具不受确认网关约束（AutoDeny 只挡 MEDIUM/HIGH）
        t, calls = self._tool("Read", RiskLevel.LOW)
        loop = self._loop(t, confirmation=AutoDenyGateway())
        loop.run("用 Read")
        self.assertEqual(calls["n"], 1)

    def test_high_static_deny_short_circuits(self):
        # 静态策略先否决：即便确认网关批准，也不执行
        from brickery.runtime.tools import DenyListPolicy
        t, calls = self._tool("Bash", RiskLevel.HIGH)
        loop = self._loop(t, confirmation=AutoApproveGateway(),
                          permission=DenyListPolicy([t.name]))
        loop.run("用 Bash")
        self.assertEqual(calls["n"], 0)
        self.assertTrue(any("被权限策略拒绝" in line for line in loop.last_tool_log))


if __name__ == "__main__":
    unittest.main()
