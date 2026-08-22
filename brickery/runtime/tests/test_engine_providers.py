"""ApiEngine 容错与错误分类单测：断连/超时/限流/鉴权/服务端/空内容重试。

零真实网络：全部 mock urllib 层（延续「零默认外连」专项约束）。
"""
import io
import socket
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

from brickery.runtime.engine_providers import ApiEngine
from brickery.runtime.engine_router import EngineResult, ToolCall


def _http_error(code: int, body: str = "") -> urllib.error.HTTPError:
    fp = io.BytesIO(body.encode("utf-8"))
    return urllib.error.HTTPError("https://user.example/v1/chat/completions",
                                  code, "err", {}, fp)


class TestClassifyHttp(unittest.TestCase):
    def test_401_auth_failed(self):
        msg = ApiEngine._classify_http(401, "https://user.example/v1", "bad key")
        self.assertIn("鉴权失败", msg)
        self.assertIn("api_key", msg)

    def test_403_auth_failed(self):
        msg = ApiEngine._classify_http(403, "https://user.example/v1", "")
        self.assertIn("鉴权失败", msg)

    def test_429_rate_limit(self):
        msg = ApiEngine._classify_http(429, "https://user.example/v1", "")
        self.assertIn("限流", msg)
        self.assertIn("429", msg)

    def test_5xx_server_error(self):
        msg = ApiEngine._classify_http(500, "https://user.example/v1", "boom")
        self.assertIn("服务端错误", msg)
        self.assertIn("500", msg)
        self.assertIn("boom", msg)

    def test_other_code_generic(self):
        msg = ApiEngine._classify_http(418, "https://user.example/v1", "teapot")
        self.assertIn("418", msg)


class TestApiEngineIsAvailable(unittest.TestCase):
    def test_url_and_key_required(self):
        self.assertTrue(ApiEngine("https://u/v1", "k").is_available())
        self.assertFalse(ApiEngine("", "k").is_available())
        self.assertFalse(ApiEngine("https://u/v1", "").is_available())
        self.assertFalse(ApiEngine("", "").is_available())


class TestRequestJson(unittest.TestCase):
    def setUp(self):
        self.eng = ApiEngine("https://user.example/v1", "secret-key")
        self.payload = {"model": "m", "messages": []}

    def _patch_urlopen(self, return_value=None, side_effect=None):
        return patch("urllib.request.urlopen",
                     return_value=return_value, side_effect=side_effect)

    def test_success_returns_json(self):
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = b'{"choices":[{"message":{"content":"hi"}}]}'
        resp.__enter__.return_value = resp
        with self._patch_urlopen(return_value=resp):
            data = self.eng._request_json(self.payload, timeout=30)
        self.assertEqual(data["choices"][0]["message"]["content"], "hi")

    def test_connection_failure_raises_network(self):
        err = urllib.error.URLError("connection refused")
        with self._patch_urlopen(side_effect=err):
            with self.assertRaises(RuntimeError) as ctx:
                self.eng._request_json(self.payload, timeout=30)
        self.assertIn("无法连接", str(ctx.exception))
        self.assertIn("user.example", str(ctx.exception))

    def test_timeout_raises(self):
        with self._patch_urlopen(side_effect=socket.timeout("timed out")):
            with self.assertRaises(RuntimeError) as ctx:
                self.eng._request_json(self.payload, timeout=30)
        self.assertIn("超时", str(ctx.exception))
        self.assertIn("30s", str(ctx.exception))

    def test_http_429_raises_rate_limit(self):
        with self._patch_urlopen(side_effect=_http_error(429)):
            with self.assertRaises(RuntimeError) as ctx:
                self.eng._request_json(self.payload, timeout=30)
        self.assertIn("限流", str(ctx.exception))

    def test_http_401_raises_auth(self):
        with self._patch_urlopen(side_effect=_http_error(401, "invalid key")):
            with self.assertRaises(RuntimeError) as ctx:
                self.eng._request_json(self.payload, timeout=30)
        self.assertIn("鉴权失败", str(ctx.exception))

    def test_http_500_raises_server(self):
        with self._patch_urlopen(side_effect=_http_error(500, "oops")):
            with self.assertRaises(RuntimeError) as ctx:
                self.eng._request_json(self.payload, timeout=30)
        self.assertIn("服务端错误", str(ctx.exception))


class TestRunTurnRetry(unittest.TestCase):
    def setUp(self):
        self.eng = ApiEngine("https://user.example/v1", "secret-key")

    def _ok_payload(self, text: str):
        return {"choices": [{"message": {"content": text, "tool_calls": None}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2}}

    def test_empty_content_retries_once_then_succeeds(self):
        # 第一次空内容 -> 重试；第二次正常返回
        with patch.object(ApiEngine, "_request_json",
                          side_effect=[self._ok_payload(""), self._ok_payload("ok")]):
            res = self.eng.run_turn("hi", timeout=30)
        self.assertIsInstance(res, EngineResult)
        self.assertEqual(res.text, "ok")

    def test_empty_content_twice_raises(self):
        with patch.object(ApiEngine, "_request_json",
                          side_effect=[self._ok_payload(""), self._ok_payload("")]):
            with self.assertRaises(RuntimeError) as ctx:
                self.eng.run_turn("hi", timeout=30)
        self.assertIn("空内容", str(ctx.exception))

    def test_network_error_does_not_retry(self):
        # 网络错误不是「空内容」，不触发重试，直接抛
        with patch.object(ApiEngine, "_request_json",
                          side_effect=RuntimeError("网络 API 请求超时（x，>60s 无响应）。")):
            with self.assertRaises(RuntimeError) as ctx:
                self.eng.run_turn("hi", timeout=60)
        self.assertIn("超时", str(ctx.exception))

    def test_tool_calls_parsed(self):
        payload = {"choices": [{"message": {"content": "", "tool_calls": [
            {"function": {"name": "echo", "arguments": '{"msg":"hi"}'}}]}}],
            "usage": None}
        with patch.object(ApiEngine, "_request_json", return_value=payload):
            res = self.eng.run_turn("hi", timeout=30)
        self.assertEqual(len(res.tool_calls), 1)
        self.assertEqual(res.tool_calls[0].name, "echo")
        self.assertEqual(res.tool_calls[0].arguments, {"msg": "hi"})


if __name__ == "__main__":
    unittest.main()
