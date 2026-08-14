"""§4 引擎路由单测，含「零默认外连」专项（socket 拦截）。"""
import socket
from unittest.mock import MagicMock, patch

from brickery.runtime.config import EngineConfig
from brickery.runtime.engine_router import (
    EngineRouter, NoEngineConfigured, EngineResult, ToolCall)
from .base import RuntimeTestCase


class TestEngineRouter(RuntimeTestCase):
    def test_local_backend(self):
        eng = EngineConfig(backend="local")
        router = EngineRouter(eng, local_engine=lambda p: "本地回复")
        self.assertEqual(router.complete("hi"), "本地回复")

    def test_api_backend_explicit(self):
        eng = EngineConfig(backend="api", api_url="https://user.example/v1")
        api = MagicMock()
        api.complete.return_value = "api回复"
        router = EngineRouter(eng, api_engine=api)
        out = router.complete("hi")
        self.assertEqual(out, "api回复")
        api.complete.assert_called_once()

    def test_no_configured_raises_and_no_network(self):
        # 默认 local 但无引擎实例 -> 抛错，且**零出站连接**
        eng = EngineConfig(backend="local")
        router = EngineRouter(eng)
        with patch("socket.socket") as mocksock:
            with self.assertRaises(NoEngineConfigured):
                router.complete("hi")
            mocksock.assert_not_called()

    def test_api_backend_without_endpoint_raises(self):
        eng = EngineConfig(backend="api")
        router = EngineRouter(eng)
        with self.assertRaises(NoEngineConfigured):
            router.complete("hi")

    def test_hot_switch_to_api_requires_url(self):
        eng = EngineConfig(backend="local")
        router = EngineRouter(eng, local_engine=lambda p: "x")
        router.set_backend("api", api_url="https://user.example/v1")
        self.assertEqual(eng.backend, "api")
        self.assertEqual(eng.api_url, "https://user.example/v1")
        with self.assertRaises(ValueError):
            router.set_backend("api")  # 缺 api_url 必须报错

    # ----- Function-Calling 闭环（阶段 B）-----

    def test_run_turn_delegates_to_engine(self):
        eng = EngineConfig(backend="local")
        fake = MagicMock()
        fake.run_turn.return_value = EngineResult(
            text="", tool_calls=[ToolCall("echo", {"msg": "hi"})])
        router = EngineRouter(eng, local_engine=fake)
        res = router.run_turn("提示", tools=[{"type": "function"}])
        self.assertIsInstance(res, EngineResult)
        self.assertEqual(res.tool_calls[0].name, "echo")
        fake.run_turn.assert_called_once()

    def test_run_turn_degrades_to_text(self):
        # 旧式引擎（仅 callable，无 run_turn）退化成纯文本，不破坏既有 loop
        eng = EngineConfig(backend="local")
        router = EngineRouter(eng, local_engine=lambda p: "纯文本回复")
        res = router.run_turn("提示")
        self.assertIsInstance(res, EngineResult)
        self.assertEqual(res.text, "纯文本回复")
        self.assertEqual(res.tool_calls, [])

    def test_run_turn_no_engine_raises_no_network(self):
        eng = EngineConfig(backend="local")
        router = EngineRouter(eng)
        with patch("socket.socket") as mocksock:
            with self.assertRaises(NoEngineConfigured):
                router.run_turn("提示")
            mocksock.assert_not_called()

    # ----- 首推 API 为主、本地为备选（2026-08-06 决策）-----

    def test_api_primary_falls_back_to_local(self):
        eng = EngineConfig(backend="api", api_url="https://user.example/v1")
        api = MagicMock()
        api.complete.side_effect = RuntimeError("API 网络失败")
        router = EngineRouter(eng, local_engine=lambda p: "本地兜底回复",
                              api_engine=api)
        # API 首选失败 → 自动降级到本地 GGUF
        self.assertEqual(router.complete("hi"), "本地兜底回复")

    def test_local_primary_falls_back_to_api(self):
        eng = EngineConfig(backend="local", api_url="https://user.example/v1")
        api = MagicMock()
        api.complete.return_value = "api兜底回复"

        def _boom(p, **kw):
            raise RuntimeError("本地 OOM")

        router = EngineRouter(eng, local_engine=_boom, api_engine=api)
        self.assertEqual(router.complete("hi"), "api兜底回复")

    def test_run_turn_api_primary_falls_back(self):
        eng = EngineConfig(backend="api", api_url="https://user.example/v1")
        api = MagicMock()
        api.run_turn.side_effect = RuntimeError("API 失败")
        # 本地用旧式 callable（无 run_turn）兜底
        router = EngineRouter(eng, local_engine=lambda p: "本地文本",
                              api_engine=api)
        res = router.run_turn("提示")
        self.assertIsInstance(res, EngineResult)
        self.assertEqual(res.text, "本地文本")

    def test_both_configured_but_fail_raises(self):
        eng = EngineConfig(backend="api", api_url="https://user.example/v1")

        def _boom(p, **kw):
            raise RuntimeError("两个都崩")

        router = EngineRouter(eng, local_engine=_boom, api_engine=_boom)
        with self.assertRaises(Exception):
            router.complete("hi")
