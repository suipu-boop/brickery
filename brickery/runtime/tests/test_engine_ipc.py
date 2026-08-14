"""生产引擎 run_turn 单测（阶段 B）：LocalGGUFEngine / ApiEngine 的 tool_use 解析。

不加载真实 GGUF、不触网：分别 mock 底层 Llama 与 urllib，验证 tool_calls 解析。
"""
from __future__ import annotations

import json
import sys
import types
from unittest.mock import patch

# 让 mock.patch("llama_cpp.Llama", ...) 在缺少真实 llama_cpp 包时也能解析。
# 不加载真实 GGUF、不触网；运行期该属性被 _FakeLlama 覆盖，仅解决模块可寻址性。
if "llama_cpp" not in sys.modules:
    _fake_llama_cpp = types.ModuleType("llama_cpp")
    _fake_llama_cpp.Llama = object  # 占位，运行期被 patch 覆盖
    sys.modules["llama_cpp"] = _fake_llama_cpp

from brickery.runtime.ipc import LocalGGUFEngine, ApiEngine
from brickery.runtime.engine_router import EngineResult, ToolCall
from .base import RuntimeTestCase


_SAMPLE_TOOL = {
    "type": "function",
    "function": {"name": "echo",
                 "description": "回显",
                 "parameters": {"type": "object",
                                "properties": {"msg": {"type": "string"}}}},
}


class _FakeLlama:
    def __init__(self, *a, **kw):
        pass

    def create_chat_completion(self, messages, **kw):
        return {
            "choices": [{
                "message": {
                    "content": "",
                    "tool_calls": [{
                        "function": {"name": "echo",
                                     "arguments": '{"msg": "hi"}'},
                    }],
                },
            }],
        }

    def __call__(self, prompt, **kw):
        return {"choices": [{"text": "fallback"}]}


class _FakeResp:
    def __init__(self, payload: str):
        self._bytes = payload.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return self._bytes


class TestLocalGGUFEngineRunTurn(RuntimeTestCase):
    def test_parses_tool_calls(self):
        with patch.object(LocalGGUFEngine, "_resolve_model",
                          return_value="/tmp/fake.gguf"), \
                patch("llama_cpp.Llama", _FakeLlama):
            eng = LocalGGUFEngine()
            res = eng.run_turn("提示", tools=[_SAMPLE_TOOL])
        self.assertIsInstance(res, EngineResult)
        self.assertEqual(len(res.tool_calls), 1)
        self.assertEqual(res.tool_calls[0].name, "echo")
        self.assertEqual(res.tool_calls[0].arguments, {"msg": "hi"})

    def test_no_tool_calls_returns_text(self):
        class _TextLlama(_FakeLlama):
            def create_chat_completion(self, messages, **kw):
                return {"choices": [{"message": {"content": "纯文本",
                                                 "tool_calls": []}}]}
        with patch.object(LocalGGUFEngine, "_resolve_model",
                          return_value="/tmp/fake.gguf"), \
                patch("llama_cpp.Llama", _TextLlama):
            eng = LocalGGUFEngine()
            res = eng.run_turn("提示", tools=[_SAMPLE_TOOL])
        self.assertEqual(res.text, "纯文本")
        self.assertEqual(res.tool_calls, [])

    def test_malformed_arguments_safe(self):
        class _BadLlama(_FakeLlama):
            def create_chat_completion(self, messages, **kw):
                return {"choices": [{"message": {
                    "content": "",
                    "tool_calls": [{"function": {"name": "echo",
                                                 "arguments": "{坏json"}}]}}]}
        with patch.object(LocalGGUFEngine, "_resolve_model",
                          return_value="/tmp/fake.gguf"), \
                patch("llama_cpp.Llama", _BadLlama):
            eng = LocalGGUFEngine()
            res = eng.run_turn("提示", tools=[_SAMPLE_TOOL])
        self.assertEqual(res.tool_calls[0].name, "echo")
        self.assertEqual(res.tool_calls[0].arguments, {})  # 容错为空 dict

    def test_parses_usage_from_llama(self):
        """坑⑥ 回归：本地 GGUF（llama_cpp）返回的 usage 也要带出。"""
        class _UsageLlama(_FakeLlama):
            def create_chat_completion(self, messages, **kw):
                return {
                    "choices": [{"message": {"content": "ok",
                                             "tool_calls": []}}],
                    "usage": {
                        "prompt_tokens": 800,
                        "completion_tokens": 5,
                        "total_tokens": 805,
                        "prompt_tokens_details": {"cached_tokens": 700},
                    },
                }
        with patch.object(LocalGGUFEngine, "_resolve_model",
                          return_value="/tmp/fake.gguf"), \
                patch("llama_cpp.Llama", _UsageLlama):
            eng = LocalGGUFEngine()
            res = eng.run_turn("提示", tools=[_SAMPLE_TOOL])
        self.assertIsNotNone(res.usage)
        self.assertEqual(res.usage.prompt_tokens, 800)
        self.assertEqual(res.usage.cached_tokens, 700)


class TestApiEngineRunTurn(RuntimeTestCase):
    def test_parses_tool_calls(self):
        payload = json.dumps({
            "choices": [{"message": {
                "content": "",
                "tool_calls": [{"function": {"name": "echo",
                                             "arguments": '{"msg": "hi"}'}}]}}],
        })
        eng = ApiEngine("https://user.example/v1", api_key="k",
                        api_model="gpt-4o-mini")
        with patch("urllib.request.urlopen",
                   return_value=_FakeResp(payload)):
            res = eng.run_turn("提示", tools=[_SAMPLE_TOOL])
        self.assertEqual(res.tool_calls[0].name, "echo")
        self.assertEqual(res.tool_calls[0].arguments, {"msg": "hi"})

    def test_no_tool_calls_returns_text(self):
        payload = json.dumps({
            "choices": [{"message": {"content": "API纯文本",
                                     "tool_calls": None}}],
        })
        eng = ApiEngine("https://user.example/v1")
        with patch("urllib.request.urlopen",
                   return_value=_FakeResp(payload)):
            res = eng.run_turn("提示")
        self.assertEqual(res.text, "API纯文本")
        self.assertEqual(res.tool_calls, [])

    def test_parses_usage(self):
        """坑⑥ 回归：真实 token 用量（含缓存命中）必须带出，供内感受量化。"""
        payload = json.dumps({
            "choices": [{"message": {"content": "ok", "tool_calls": None}}],
            "usage": {
                "prompt_tokens": 1500,
                "completion_tokens": 12,
                "total_tokens": 1512,
                "prompt_tokens_details": {"cached_tokens": 1400},
            },
        })
        eng = ApiEngine("https://user.example/v1")
        with patch("urllib.request.urlopen",
                   return_value=_FakeResp(payload)):
            res = eng.run_turn("提示")
        self.assertIsNotNone(res.usage)
        self.assertEqual(res.usage.prompt_tokens, 1500)
        self.assertEqual(res.usage.cached_tokens, 1400)
        self.assertAlmostEqual(res.usage.cache_hit_rate, 1400 / 1500)

    def test_missing_usage_is_none(self):
        """用量字段缺失时安全降级为 None（不假设有缓存、不粗估）。"""
        payload = json.dumps({
            "choices": [{"message": {"content": "ok", "tool_calls": None}}],
        })
        eng = ApiEngine("https://user.example/v1")
        with patch("urllib.request.urlopen",
                   return_value=_FakeResp(payload)):
            res = eng.run_turn("提示")
        self.assertIsNone(res.usage)
