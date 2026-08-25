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

    # ----- 只走显式选择的后端、无自动降级（2026-08-25 拍板）-----

    def test_api_failure_does_not_fallback_to_local(self):
        eng = EngineConfig(backend="api", api_url="https://user.example/v1")
        api = MagicMock()
        api.complete.side_effect = RuntimeError("API 网络失败")
        local = MagicMock()
        local.complete.return_value = "本地兜底回复"
        router = EngineRouter(eng, local_engine=local, api_engine=api)
        # API 失败 → 直接上浮报错，**绝不**静默降级到本地
        with self.assertRaises(RuntimeError):
            router.complete("hi")
        local.complete.assert_not_called()

    def test_local_failure_does_not_fallback_to_api(self):
        eng = EngineConfig(backend="local")
        api = MagicMock()
        api.complete.return_value = "api兜底回复"

        def _boom(p, **kw):
            raise RuntimeError("本地 OOM")

        router = EngineRouter(eng, local_engine=_boom, api_engine=api)
        # 本地失败 → 直接上浮报错，**绝不**静默切到 API
        with self.assertRaises(RuntimeError):
            router.complete("hi")
        api.complete.assert_not_called()

    def test_run_turn_api_failure_does_not_fallback(self):
        eng = EngineConfig(backend="api", api_url="https://user.example/v1")
        api = MagicMock()
        api.run_turn.side_effect = RuntimeError("API 失败")
        local = MagicMock()
        local.run_turn.return_value = EngineResult(text="本地文本")
        router = EngineRouter(eng, local_engine=local, api_engine=api)
        with self.assertRaises(RuntimeError):
            router.run_turn("提示")
        local.run_turn.assert_not_called()

    def test_both_configured_but_fail_raises(self):
        eng = EngineConfig(backend="api", api_url="https://user.example/v1")

        def _boom(p, **kw):
            raise RuntimeError("两个都崩")

        router = EngineRouter(eng, local_engine=_boom, api_engine=_boom)
        with self.assertRaises(Exception):
            router.complete("hi")

    # ----- 引擎容错：断连 / 超时 / 限流直接上浮（2026-08-25 改）-----

    def _api_engine_raising(self, message: str) -> MagicMock:
        api = MagicMock()
        api.complete.side_effect = RuntimeError(message)
        return api

    def test_api_disconnect_raises_without_fallback(self):
        eng = EngineConfig(backend="api", api_url="https://user.example/v1")
        api = self._api_engine_raising("网络 API 请求失败（无法连接 user.example）：refused")
        local = MagicMock()
        local.complete.return_value = "本地兜底"
        router = EngineRouter(eng, local_engine=local, api_engine=api)
        with self.assertRaisesRegex(RuntimeError, "无法连接"):
            router.complete("hi")
        local.complete.assert_not_called()

    def test_api_timeout_raises_without_fallback(self):
        eng = EngineConfig(backend="api", api_url="https://user.example/v1")
        api = self._api_engine_raising("网络 API 请求超时（user.example，>60s 无响应）。")
        local = MagicMock()
        local.complete.return_value = "本地兜底"
        router = EngineRouter(eng, local_engine=local, api_engine=api)
        with self.assertRaisesRegex(RuntimeError, "请求超时"):
            router.complete("hi")
        local.complete.assert_not_called()

    def test_api_rate_limit_raises_without_fallback(self):
        eng = EngineConfig(backend="api", api_url="https://user.example/v1")
        api = self._api_engine_raising("网络 API 触发限流 / 额度耗尽（user.example 返回 429）。")
        local = MagicMock()
        local.complete.return_value = "本地兜底"
        router = EngineRouter(eng, local_engine=local, api_engine=api)
        with self.assertRaisesRegex(RuntimeError, "429"):
            router.complete("hi")
        local.complete.assert_not_called()

    def test_api_called_once_no_retry(self):
        # API 失败直接上浮，不重试、不降级：complete 只调用一次
        eng = EngineConfig(backend="api", api_url="https://user.example/v1")
        api = self._api_engine_raising("网络 API 请求失败（无法连接 user.example）：refused")
        router = EngineRouter(eng, local_engine=lambda p: "本地兜底",
                              api_engine=api)
        with self.assertRaises(RuntimeError):
            router.complete("hi")
        api.complete.assert_called_once()

    def test_unavailable_local_with_api_configured(self):
        # 本地 is_available()=False 时：backend=api 不受影响（api 正常调用）
        eng = EngineConfig(backend="api", api_url="https://user.example/v1")
        api = MagicMock()
        api.complete.return_value = "api回复"
        local = MagicMock()
        local.is_available.return_value = False
        router = EngineRouter(eng, local_engine=local, api_engine=api)
        self.assertEqual(router.complete("hi"), "api回复")
        api.complete.assert_called_once()
