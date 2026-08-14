"""§3.5 浮现 / 影子引擎单测：闸门 / 归纳 / 浮现检索 / 200 轮连贯性。

全程用 MockEngine / MockShadowEngine，绝不发起真实网络推理或加载 GGUF。
"""
import time
import unittest

from brickery.memory import MemorySystem
from brickery.memory import surfacing
from brickery.memory.surfacing import SurfaceGate, ShadowEngine
from brickery.runtime.config import EngineConfig
from brickery.runtime.engine_router import EngineRouter
from brickery.runtime.loop import AgentLoop
from .base import BaseMemoryTest


class MockShadowEngine:
    """影子模型桩：consolidate 抽实体（含'项目X'则记），decide_surface 留空。"""

    def __init__(self):
        self.calls = []

    def consolidate(self, texts):
        self.calls.append(list(texts))
        joined = "\n".join(texts)
        entities = ["项目X"] if "项目X" in joined else []
        return {"entities": entities, "decisions": [], "todos": []}

    def decide_surface(self, query, candidates):
        return []


class TestSurfaceGate(BaseMemoryTest):
    def setUp(self):
        super().setUp()
        self.gate = SurfaceGate()

    def test_pronoun_triggers(self):
        self.assertTrue(self.gate.should_trigger("之前说的那个方案呢"))
        self.assertTrue(self.gate.should_trigger("remember what we discussed"))
        self.assertFalse(self.gate.should_trigger("今天天气不错"))

    def test_long_idle_triggers(self):
        self.assertTrue(self.gate.should_trigger("随便聊聊", idle_seconds=40 * 60))
        self.assertFalse(self.gate.should_trigger("随便聊聊", idle_seconds=10))

    def test_topic_shift_triggers(self):
        hist = ["我们在调 SwiftUI 的布局约束", "Xcode 里跑模拟器很卡"]
        # 当前消息与历史关键词零重叠 → 跳变触发
        self.assertTrue(self.gate.should_trigger("今晚吃火锅去哪好", recent_history=hist))
        # 同主题 → 不触发
        self.assertFalse(self.gate.should_trigger("SwiftUI 的 List 怎么懒加载", recent_history=hist))


class TestShadowEngine(BaseMemoryTest):
    def test_consolidate_no_engine_returns_empty(self):
        e = ShadowEngine(engine=None)
        self.assertEqual(e.consolidate(["聊点啥"]),
                         {"entities": [], "decisions": [], "todos": []})

    def test_consolidate_extracts(self):
        e = ShadowEngine(engine=lambda p: '{"entities":["A"],"decisions":["B"],"todos":["C"]}')
        out = e.consolidate(["我们决定用 A 做 B，待办 C"])
        self.assertEqual(out["entities"], ["A"])
        self.assertEqual(out["decisions"], ["B"])
        self.assertEqual(out["todos"], ["C"])

    def test_consolidate_bad_json_safe(self):
        e = ShadowEngine(engine=lambda p: "我不是json")
        self.assertEqual(e.consolidate(["x"]),
                         {"entities": [], "decisions": [], "todos": []})

    def test_singleton_cache_by_path(self):
        loader = {"called": 0}

        def _loader(path):
            loader["called"] += 1
            return lambda p: "r"

        a = ShadowEngine.get("/model/qwen3.gguf", _loader)
        b = ShadowEngine.get("/model/qwen3.gguf", _loader)
        self.assertIs(a, b)  # 同路径缓存单例
        self.assertEqual(loader["called"], 1)
        ShadowEngine.clear_cache()


class TestSurfacingRecall(BaseMemoryTest):
    def test_no_trigger_returns_empty(self):
        ms = MemorySystem()
        ms.archive("s1", ["我们讨论项目X用Swift"], project="")
        ms.finalize_session("s1")
        # 普通消息（无指代词、无跳变、无长间隔）→ 不注入
        cands = ms.surface("今天天气真不错", recent_history=[], idle_seconds=0)
        self.assertEqual(cands, [])

    def test_pronoun_triggers_recall(self):
        ms = MemorySystem()
        ms.archive("s1", ["我们讨论项目X用Swift写macOS"], project="")
        ms.finalize_session("s1")
        cands = ms.surface("之前说的项目X现在进度怎样", recent_history=["今天天气好"], idle_seconds=0)
        self.assertTrue(cands)
        self.assertTrue(any("项目X" in (c.get("topic_summary") or "") for c in cands))


class TestLoopSurfacing(BaseMemoryTest):
    def _engine_recording(self):
        prompts = []

        def _e(p):
            prompts.append(p)
            return "好的"
        return prompts, _e

    def test_loop_injects_related_memory(self):
        ms = MemorySystem()
        ms.archive("s_old", ["我们讨论项目X用Swift"], project="")
        ms.finalize_session("s_old")
        prompts, engine = self._engine_recording()
        er = EngineRouter(EngineConfig(backend="local"), local_engine=engine)
        loop = AgentLoop(ms, er, history_window=8)
        loop.run("之前说的项目X现在怎样", history=[])
        self.assertTrue(any("【相关记忆】" in p for p in prompts))

    def test_loop_async_consolidate_writes_structured(self):
        ms = MemorySystem()
        shadow = MockShadowEngine()
        prompts, engine = self._engine_recording()
        er = EngineRouter(EngineConfig(backend="local"), local_engine=engine)
        loop = AgentLoop(ms, er, shadow_engine=shadow, session_id="SX", history_window=8)
        loop.run("我们决定项目X用Swift落地", history=[])
        # 等异步线程完成
        time.sleep(0.3)
        rows = ms.recall("项目X")
        self.assertTrue(rows)
        ents = rows[0].get("entities") or []
        self.assertIn("项目X", ents)


class Test200RoundContinuity(BaseMemoryTest):
    """A9 验收：200 轮规模对话，中段记忆滑出窗口后仍能被浮现捞出。"""

    def _chat(self, loop, history, text):
        out = loop.run(text, history=history)
        history.append({"role": "user", "text": text})
        history.append({"role": "assistant", "text": out})
        return out

    def test_mid_conversation_recalled(self):
        ms = MemorySystem()
        shadow = MockShadowEngine()
        er = EngineRouter(EngineConfig(backend="local"), local_engine=lambda p: "好的")

        # 第 1 段：100 轮闲聊（session A）
        loopA = AgentLoop(ms, er, shadow_engine=shadow, session_id="A", history_window=8)
        histA = []
        for i in range(100):
            self._chat(loopA, histA, f"今天天气{i}号出门散步")
        ms.finalize_session("A")

        # 第 2 段：聊项目X（session B，中段关键记忆）
        loopB = AgentLoop(ms, er, shadow_engine=shadow, session_id="B", history_window=8)
        histB = []
        self._chat(loopB, histB, "我们定了项目X用Swift写macOS原生应用")
        ms.finalize_session("B")

        # 第 3 段：99 轮无关（session C）
        loopC = AgentLoop(ms, er, shadow_engine=shadow, session_id="C", history_window=8)
        histC = []
        for i in range(99):
            self._chat(loopC, histC, f"晚饭吃{i}号餐厅味道不错")
        ms.finalize_session("C")

        # 后续提问项目X（指代词触发闸门），中段记忆应被浮现捞出
        cands = ms.surface("之前说的项目X现在进度怎样了",
                           recent_history=["晚饭吃98号餐厅味道不错"], idle_seconds=10)
        self.assertTrue(cands, "中段项目X记忆应被浮现捞出")
        self.assertTrue(any("项目X" in (c.get("topic_summary") or "") for c in cands))


class FakeMem:
    """桩：recall 返回固定候选，绕开真实 DB，专注测 surfacing_for 的影子过滤。"""

    def __init__(self, cands):
        self._cands = cands

    def recall(self, query, project=None, limit=8):
        return self._cands


class FakeShadowPick:
    """影子桩：按索引挑候选（decide_surface 返回 record_id 列表）。"""

    def __init__(self, pick_idx):
        self.pick_idx = set(pick_idx)
        self.calls = []

    def consolidate(self, texts):
        return {"entities": [], "decisions": [], "todos": []}

    def decide_surface(self, query, candidates):
        self.calls.append((query, candidates))
        return [candidates[i]["record_id"] for i in self.pick_idx
                if 0 <= i < len(candidates)]


class TestSurfacingShadowJudgment(unittest.TestCase):
    """蓝图 A 档：浮现注入应接回影子判断（自行判断该想起什么）。"""

    def _cands(self):
        return [
            {"record_id": "r1", "topic_summary": "项目X进度", "keywords": ["项目X"]},
            {"record_id": "r2", "topic_summary": "晚饭火锅", "keywords": ["晚饭"]},
            {"record_id": "r3", "topic_summary": "Swift布局", "keywords": ["Swift"]},
        ]

    def test_shadow_filters_candidates(self):
        mem = FakeMem(self._cands())
        shadow = FakeShadowPick([0, 2])  # 只想要 r1 和 r3
        out = surfacing.surfacing_for(mem, "之前说的项目X怎样", shadow=shadow)
        self.assertEqual([c["record_id"] for c in out], ["r1", "r3"])
        self.assertTrue(shadow.calls, "影子 decide_surface 必须被调用")

    def test_no_shadow_keeps_all(self):
        mem = FakeMem(self._cands())
        out = surfacing.surfacing_for(mem, "之前说的项目X怎样", shadow=None)
        self.assertEqual([c["record_id"] for c in out], ["r1", "r2", "r3"])

    def test_shadow_empty_falls_back_to_all(self):
        # 影子返回空（不可用/超时）→ 回落规则全量，不退化
        mem = FakeMem(self._cands())
        out = surfacing.surfacing_for(mem, "之前说的项目X怎样", shadow=FakeShadowPick([]))
        self.assertEqual([c["record_id"] for c in out], ["r1", "r2", "r3"])


if __name__ == "__main__":
    unittest.main()
