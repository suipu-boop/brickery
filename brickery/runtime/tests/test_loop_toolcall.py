"""§3.1 Function-Calling 闭环单测：执行 / 截断 / 上限 / 权限闸门 / 安全降级。

采用「Mock 引擎注入预制 EngineResult」的方式验证循环逻辑（不依赖真实推理抖动）。
阶梯 C 的真实工具 handler（Read/Write/Bash）尚未落地，这里用最简 mock 工具即可。
"""
from unittest.mock import MagicMock

from brickery.runtime.config import EngineConfig
from brickery.runtime.engine_router import EngineRouter, EngineResult, ToolCall, PromptUsage
from brickery.runtime.loop import AgentLoop
from brickery.runtime.skills import SkillRegistry, Skill
from brickery.runtime.tools import (
    Tool, ToolRegistry, AllowAllPolicy, DenyListPolicy)
from .base import RuntimeTestCase


class _ScriptedToolEngine:
    """按脚本顺序返回 EngineResult 的假引擎（实现 run_turn）。"""

    def __init__(self, scripted):
        self._script = list(scripted)
        self.calls = 0

    def run_turn(self, prompt, tools=None, **kw):
        self.calls += 1
        if self._script:
            return self._script.pop(0)
        return EngineResult(text="（完成）")


class TestLoopToolCall(RuntimeTestCase):
    def _loop(self, engine, tools=None, permission=None, **kw):
        mem = MagicMock()
        er = EngineRouter(EngineConfig(backend="local"), local_engine=engine)
        reg = tools if tools is not None else ToolRegistry()
        loop = AgentLoop(mem, er, tools=reg, skills=SkillRegistry(),
                         permission=permission, **kw)
        return loop, mem

    def _echo_tool(self):
        calls = {"n": 0}

        def handler(msg="", **_):
            calls["n"] += 1
            return f"echo:{msg}"
        t = Tool(name="echo", description="回显", keywords=["回显", "echo"],
                 handler=handler, parameters={"type": "object",
                                              "properties": {"msg": {"type": "string"}}})
        return t, calls

    def test_tool_call_executes_and_replies(self):
        tool, calls = self._echo_tool()
        reg = ToolRegistry(); reg.register(tool)
        engine = _ScriptedToolEngine([
            EngineResult(text="", tool_calls=[ToolCall("echo", {"msg": "hi"})]),
            EngineResult(text="已经帮你回显了 hi。"),
        ])
        loop, _ = self._loop(engine, tools=reg)
        out = loop.run("请帮我回显 hi")
        self.assertEqual(out, "已经帮你回显了 hi。")
        self.assertEqual(calls["n"], 1)
        self.assertIn("echo", loop.last_tools)
        self.assertTrue(any("echo:hi" in line for line in loop.last_tool_log))

    def test_tool_result_truncated(self):
        def handler(**_):
            return "X" * 5000  # 远超默认 4000 上限
        t = Tool(name="big", description="大输出", keywords=["big"],
                 handler=handler)
        reg = ToolRegistry(); reg.register(t)
        engine = _ScriptedToolEngine([
            EngineResult(text="", tool_calls=[ToolCall("big", {})]),
            EngineResult(text="done"),
        ])
        loop, _ = self._loop(engine, tools=reg, tool_result_limit=4000)
        loop.run("big")
        self.assertTrue(any("已截断" in line for line in loop.last_tool_log))
        # 截断后长度不应再膨胀：截断标记约 8 字
        for line in loop.last_tool_log:
            if "已截断" in line:
                self.assertLess(len(line), 4200)

    def test_max_tool_calls_limit(self):
        # 工具永远成功，引擎每轮都返回同一个工具调用 -> 应在上限处停下
        def handler(**_):
            return "ok"
        t = Tool(name="loop", description="循环", keywords=["loop"],
                 handler=handler)
        reg = ToolRegistry(); reg.register(t)
        engine = _ScriptedToolEngine([
            EngineResult(text="", tool_calls=[ToolCall("loop", {})]),
        ])  # 只给一条；run_turn 会被反复调用（脚本空后返回纯文本，但本例故意无限）
        # 用真正无限返回的引擎：每次都给 tool_call
        class InfiniteToolEngine:
            def run_turn(self, prompt, tools=None, **kw):
                return EngineResult(text="", tool_calls=[ToolCall("loop", {})])
        loop, _ = self._loop(InfiniteToolEngine(), tools=reg, max_tool_calls=3)
        out = loop.run("loop")
        self.assertIn("上限", out)
        # 执行了恰好 3 次（达上限即停，不越界）。last_tool_log 每行对应一次执行。
        executed_lines = [l for l in loop.last_tool_log if l.startswith("调用 loop")]
        self.assertEqual(len(executed_lines), 3)

    def test_permission_denies_tool(self):
        tool, calls = self._echo_tool()
        reg = ToolRegistry(); reg.register(tool)
        engine = _ScriptedToolEngine([
            EngineResult(text="", tool_calls=[ToolCall("echo", {"msg": "secret"})]),
            EngineResult(text="（已拒绝，换种方式）"),
        ])
        loop, _ = self._loop(engine, tools=reg, permission=DenyListPolicy(["echo"]))
        out = loop.run("请用 echo 说 secret")
        self.assertEqual(out, "（已拒绝，换种方式）")
        self.assertEqual(calls["n"], 0)  # 未真正执行
        self.assertTrue(any("被权限策略拒绝" in line for line in loop.last_tool_log))

    def test_unknown_tool_safe(self):
        engine = _ScriptedToolEngine([
            EngineResult(text="", tool_calls=[ToolCall("ghost", {})]),
            EngineResult(text="没这工具，算了"),
        ])
        loop, _ = self._loop(engine)  # 空注册表
        out = loop.run("调用不存在的工具")
        self.assertEqual(out, "没这工具，算了")
        self.assertTrue(any("工具不存在" in line for line in loop.last_tool_log))

    def test_tool_no_handler_safe(self):
        # 工具已注册但无 handler（仅参与上下文筛选）
        t = Tool(name="noop", description="无实现", keywords=["noop"])
        reg = ToolRegistry(); reg.register(t)
        engine = _ScriptedToolEngine([
            EngineResult(text="", tool_calls=[ToolCall("noop", {})]),
            EngineResult(text="这个工具跑不起来"),
        ])
        loop, _ = self._loop(engine, tools=reg)
        out = loop.run("用 noop")
        self.assertEqual(out, "这个工具跑不起来")
        self.assertTrue(any("无可执行实现" in line for line in loop.last_tool_log))

    def test_no_engine_toolcall_friendly(self):
        # 无引擎配置时，闭环首轮即抛 NoEngineConfigured，应安全降级为友好提示
        er = EngineRouter(EngineConfig(backend="local"))
        loop = AgentLoop(MagicMock(), er)
        out = loop.run("你好")
        self.assertIn("推理后端", out)

    def test_no_tool_call_returns_text(self):
        # 引擎直接返回文本、无工具调用 -> 单轮结束，不进入循环
        engine = _ScriptedToolEngine([EngineResult(text="普通回复")])
        loop, _ = self._loop(engine)
        out = loop.run("你好")
        self.assertEqual(out, "普通回复")
        self.assertEqual(engine.calls, 1)


class _FakeSkill:
    """最小技能桩：只需 .content 供 _build_prompt 注入。"""
    def __init__(self, content):
        self.content = content


class TestLoopPromptOrderAndUsage(RuntimeTestCase):
    def _loop(self, engine, tools=None, permission=None, **kw):
        from brickery.runtime.config import EngineConfig
        from brickery.runtime.engine_router import EngineRouter
        from brickery.runtime.skills import SkillRegistry
        from brickery.runtime.tools import ToolRegistry
        mem = MagicMock()
        er = EngineRouter(EngineConfig(backend="local"), local_engine=engine)
        reg = tools if tools is not None else ToolRegistry()
        loop = AgentLoop(mem, er, tools=reg, skills=SkillRegistry(),
                         permission=permission, **kw)
        return loop, mem

    def test_prompt_order_stable_before_volatile(self):
        """坑⑥ 回归：稳定块（规则+历史）必须排在变动块（技能/记忆）之前，
        否则跨轮前缀缓存被变动块打断。"""
        loop, _ = self._loop(_ScriptedToolEngine([EngineResult(text="ok")]))
        history = [{"role": "user", "text": "上一轮的问题"},
                   {"role": "assistant", "text": "上一轮的回答"}]
        skill = _FakeSkill("技能注入内容XYZ")
        prompt = loop._build_prompt(
            "本轮输入", [skill], history=history,
            memory_text="浮现记忆内容ABC", rules=["规则1"])
        i_history = prompt.index("【对话历史】")
        i_skill = prompt.index("技能注入内容XYZ")
        i_memory = prompt.index("浮现记忆内容ABC")
        i_rule = prompt.index("规则1")
        i_input = prompt.index("本轮输入")
        # 稳定块（规则、历史）全部在变动块（技能、记忆）之前
        self.assertLess(i_rule, i_history)
        self.assertLess(i_history, i_skill)
        self.assertLess(i_history, i_memory)
        # 输入永远最后
        self.assertGreater(i_input, i_memory)
        self.assertGreater(i_input, i_skill)

    def test_interoception_uses_real_usage(self):
        """坑⑥/⑦ 回归：内感受必须用引擎真实 token 用量（非字符粗估），
        分母用真实窗口（默认 128K），并带出缓存命中数。"""
        loop, _ = self._loop(_ScriptedToolEngine([EngineResult(text="ok")]))
        captured = {}
        loop.intero.observe_and_update = lambda obs: captured.setdefault("obs", obs)
        loop._collect_interoception(
            "u", "r", [], "first_prompt_text", 0,
            usage=PromptUsage(prompt_tokens=5000, cached_tokens=4000))
        obs = captured["obs"]
        self.assertEqual(obs.context_tokens, 5000)
        self.assertEqual(obs.context_token_source, "real")
        self.assertEqual(obs.prompt_cache_hit_tokens, 4000)
        self.assertEqual(obs.context_window, 128_000)
        # util 应基于 5000 / 128000（≈0.039），而非旧逻辑 5000/8192（≈0.61 虚高）
        self.assertAlmostEqual(obs.context_utilization, 5000 / 128_000, places=5)

    def test_interoception_falls_back_to_estimate_without_usage(self):
        """无 usage 时回退字符粗估，但仍用真实窗口分母（不虚高）。"""
        loop, _ = self._loop(_ScriptedToolEngine([EngineResult(text="ok")]))
        captured = {}
        loop.intero.observe_and_update = lambda obs: captured.setdefault("obs", obs)
        loop._collect_interoception(
            "u", "r", [], "first_prompt_text_长长长", 0, usage=None)
        obs = captured["obs"]
        self.assertEqual(obs.context_token_source, "estimate")
        self.assertEqual(obs.prompt_cache_hit_tokens, 0)
        self.assertEqual(obs.context_window, 128_000)

    def test_ab_injection_summary_preferred(self):
        """A+B 分级注入：有 summary 时以 summary 作轻量目录，短 content 附在后面。"""
        loop, _ = self._loop(_ScriptedToolEngine([EngineResult(text="ok")]))
        s = Skill(name="S", trigger=["x"], summary="一句话摘要",
                  content="完整内容ABC")
        text = loop._skill_injection_text(s)
        self.assertIn("一句话摘要", text)
        self.assertIn("完整内容ABC", text)

    def test_ab_injection_long_content_truncated_with_hint(self):
        """A+B：summary + 超长 content → 不附全文，给「可手动触发」提示（防灌爆）。"""
        loop, _ = self._loop(_ScriptedToolEngine([EngineResult(text="ok")]))
        s = Skill(name="S", trigger=["x"], summary="一句话摘要",
                  content="长内容" * 1000)  # 远超 2000 上限
        text = loop._skill_injection_text(s)
        self.assertIn("一句话摘要", text)
        self.assertNotIn("长内容长内容长内容长内容长内容", text)
        self.assertIn("手动触发", text)

    def test_ab_injection_no_summary_falls_back_to_content(self):
        """向后兼容：无 summary 的旧技能，直接注入 content（按上限截断）。"""
        loop, _ = self._loop(_ScriptedToolEngine([EngineResult(text="ok")]))
        s = Skill(name="S", trigger=["x"], content="短内容XYZ")
        text = loop._skill_injection_text(s)
        self.assertIn("短内容XYZ", text)
        # 超长 content 截断（cap=2000，超长需明显超过）
        s2 = Skill(name="S2", trigger=["x"], content="超长" * 1500)  # 3000 字
        text2 = loop._skill_injection_text(s2)
        self.assertIn("已截断", text2)
        self.assertLess(len(text2), 2100)  # 截断后总长度受控，不膨胀
        # 不再含完整 3000 字（仅保留前 2000 + 截断标记）
        self.assertLess(text2.count("超长"), 1001)
