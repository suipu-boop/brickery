"""内置引擎提供者注册表（Engine Provider Registry）。

把「后端引擎的构建」从 ipc.py 硬编码抽出为内置模块池：积木 brick.json 只声明
engine_kind（local / api），真正构建逻辑来自本模块（安全红线：积木不携带可执行
代码、不携带端点）。app 启动时由 ipc.py import 本模块，触发下方注册。

铁律：
- API 端点 / 密钥仍由用户显式填写（EngineConfig），积木只负责「如何构建引擎」。
- 本地 GGUF 仅作降级兜底，不出本机。
"""
from __future__ import annotations

import json
import math
import re
import socket
import threading
import time
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from .config import EngineConfig
from .engine_router import EngineResult, ToolCall, PromptUsage

logger = logging.getLogger("shadeling.engine_providers")
def _parse_tool_calls_from_content(content: Optional[str]) -> List[ToolCall]:
    """解析 Qwen3 等模型在 content 中以原生标签产出的工具调用。

    llama_cpp 0.3.x 对 Qwen3 的 tool_calls 支持不完整：auto 模式下模型把调用
    写成 `<tool_call>\\n{"name":..., "arguments":...}\\n</tool_call>` 文本塞进
    content，而 OpenAI 风格的 `tool_calls` 字段为 None。这里做兜底解析，
    否则真实 Qwen3 权重的工具调用对主循环完全不可见。
    """
    calls: List[ToolCall] = []
    if not content:
        return calls
    for m in re.finditer(r"<tool_call>\s*(\{.*?\})\s*</tool_call>",
                         content, re.S | re.I):
        try:
            obj = json.loads(m.group(1))
        except (json.JSONDecodeError, ValueError, TypeError):
            continue
        if not isinstance(obj, dict):
            continue
        name = obj.get("name") or (obj.get("function") or {}).get("name") or ""
        args = obj.get("arguments") or obj.get("parameters") or {}
        if not isinstance(args, dict):
            args = {}
        if name:
            calls.append(ToolCall(name=str(name), arguments=args))
    return calls


def _parse_usage(raw: Optional[dict]) -> Optional[PromptUsage]:
    """从 API / 本地引擎返回的 usage 字段抽取真实 token 用量。

    缺失或结构异常时返回 None（不假设有缓存命中、不粗估）。容忍各厂商字段差异：
    - OpenAI / DeepSeek：`prompt_tokens_details.cached_tokens`
    - 部分实现：顶层 `cached_tokens` 或 `prompt_cache_hit_tokens`
    """
    if not isinstance(raw, dict):
        return None
    try:
        details = raw.get("prompt_tokens_details") or {}
        cached = (details.get("cached_tokens")
                  or raw.get("cached_tokens")
                  or raw.get("prompt_cache_hit_tokens") or 0)
        return PromptUsage(
            prompt_tokens=int(raw.get("prompt_tokens") or 0),
            completion_tokens=int(raw.get("completion_tokens") or 0),
            total_tokens=int(raw.get("total_tokens") or 0),
            cached_tokens=int(cached or 0),
        )
    except (TypeError, ValueError):
        return None


class LocalGGUFEngine:
    """进程内本地 GGUF 推理（llama-cpp-python + Metal）。延迟加载，缺依赖即报错。"""

    def __init__(self, model_path: Optional[str] = None):
        self.model_path = model_path
        self._llm = None
        self._embed_llm = None
        # llama_cpp 的 Llama 实例非线程安全。引擎单例化后前台/后台任务会并发
        # 共享同一实例，decode（create_chat_completion / 裸补全）必须串行化，
        # 否则并发撞车会得到错乱结果或崩溃（马维斯 P0-1 审查指出）。
        self._decode_lock = threading.Lock()

    def _resolve_model(self) -> Optional[str]:
        if self.model_path:
            p = Path(self.model_path).expanduser()
            if p.exists():
                return str(p)
            # 相对路径则按 models_root 解析
            from . import paths
            cand = paths.resolve_models_root() / self.model_path
            if cand.exists():
                return str(cand)
            return None
        # 未指定时自动探测 models_root 下首个 .gguf（跳过嵌入模型：
        # bge/embed/e5 等 n_ctx_train 仅 512，用对话 n_ctx 加载会溢出）
        from . import paths
        root = paths.resolve_models_root()
        for sub in ("gguf", ""):
            d = root / sub if sub else root
            if d.exists():
                hits = sorted(d.glob("*.gguf"))
                for h in hits:
                    name = h.name.lower()
                    if any(k in name for k in ("bge", "embed", "e5")):
                        continue
                    return str(h)
        return None

    def _ensure(self):
        if self._llm is not None:
            return self._llm
        path = self._resolve_model()
        if not path:
            raise RuntimeError("未找到本地 GGUF 模型文件。"
                               "请在设置中选择模型，或将其放入模型目录。")
        try:
            from llama_cpp import Llama
        except ImportError as e:
            raise RuntimeError("本地推理依赖未安装（llama-cpp-python）。"
                               "请安装后重试。") from e
        self._llm = Llama(model_path=path, n_ctx=4096, n_gpu_layers=-1)
        return self._llm

    def is_available(self) -> bool:
        """轻量可用性检查（不加载权重）：llama_cpp 可导入 且 存在可加载的 GGUF。

        仅供路由层判断「本地兜底是否真能用」，避免把请求发给跑不起来的引擎
        （例如用户只配了网络 API、本机没放 GGUF 时，本地兜底应判为不可用，
        从而让网络 API 的失败直接上浮，而不是冒出 llama-cpp 的报错）。
        """
        try:
            import llama_cpp  # noqa: F401  （仅检测可导入，不构造 Llama 实例）
        except Exception:
            return False
        try:
            return self._resolve_model() is not None
        except Exception:
            return False

    def complete(self, prompt: str, **kw) -> str:
        llm = self._ensure()
        max_tokens = int(kw.get("max_tokens", 512))
        temperature = float(kw.get("temperature", 0.7))
        try:
            # Qwen3 等 instruct 权重必须用 chat 模板（否则退化为裸续写、指令失效）。
            # 关思考（/no_think）避免把 <think> 噪声写进存档/浮现记忆。
            with self._decode_lock:
                out = llm.create_chat_completion(
                    messages=[
                        {"role": "system", "content":
                         "nothink\n你是 Shadeling，一个本地优先的个人 AI 助手。"
                         "根据给出的上下文直接、自然地回复用户，不要输出思考过程。"},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=max_tokens, temperature=temperature)
            return out["choices"][0]["message"]["content"]
        except Exception:
            # 回退：裸补全（部分非 instruct 权重无 chat 模板时仍可工作）
            with self._decode_lock:
                out = llm(prompt, max_tokens=max_tokens, temperature=temperature)
            if isinstance(out, dict) and "choices" in out:
                return out["choices"][0]["text"]
            return str(out)

    def run_turn(self, prompt: str, tools: Optional[list] = None, *,
                 stream: bool = False,
                 on_token: Optional[Callable[[str], None]] = None,
                 **kw) -> "EngineResult":
        """Function-Calling 闭环：让 Qwen3 等支持 tool_calls 的权重产出工具调用。

        通过 llama_cpp 的 tools 参数（OpenAI 格式）注入可用工具；模型据此输出
        tool_calls，解析为 ToolCall 列表。关思考避免思考噪声污染工具参数。
        任何失败（不支持 tools / 解析失败）均安全降级为纯文本 EngineResult。

        stream=True 时走 llama.cpp 的流式生成器：逐 token 回调 on_token，
        EngineResult.text 仍为完整文本（增量已实时回调）。
        """
        llm = self._ensure()
        max_tokens = int(kw.get("max_tokens", 900))
        temperature = float(kw.get("temperature", 0.7))
        messages = [
            {"role": "system", "content":
             "nothink\n你是 Shadeling，一个本地优先的个人 AI 助手。"
             "你可以使用提供的工具来完成任务。根据上下文直接、自然地回复用户，"
             "如需调用工具请按要求输出工具调用，不要输出思考过程。"},
            {"role": "user", "content": prompt},
        ]
        try:
            call_kw: dict = {"max_tokens": max_tokens, "temperature": temperature}
            if tools:
                call_kw["tools"] = tools
                call_kw["tool_choice"] = "auto"
            if stream:
                call_kw["stream"] = True
                with self._decode_lock:
                    gen = llm.create_chat_completion(messages=messages, **call_kw)
                text_parts: List[str] = []
                for chunk in gen:
                    try:
                        delta = (chunk.get("choices") or [{}])[0].get("delta") or {}
                    except Exception:  # noqa: BLE001 - 结构异常跳过该块
                        delta = {}
                    d = delta.get("content")
                    if d:
                        text_parts.append(d)
                        if on_token is not None:
                            try:
                                on_token(d)
                            except Exception:  # noqa: BLE001 - 回调失败不阻断生成
                                pass
                text = "".join(text_parts)
                if not text.strip():
                    return EngineResult(
                        text="本地引擎未返回内容（模型异常或未配置），请重试或检查本地模型。")
                return EngineResult(text=text)
            with self._decode_lock:
                out = llm.create_chat_completion(messages=messages, **call_kw)
            msg = out["choices"][0]["message"]
            text = msg.get("content") or ""
            calls: List[ToolCall] = []
            for tc in (msg.get("tool_calls") or []):
                fn = tc.get("function", {})
                raw = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw) if isinstance(raw, str) else raw
                except (json.JSONDecodeError, ValueError, TypeError):
                    args = {}
                if not isinstance(args, dict):
                    args = {}
                calls.append(ToolCall(name=fn.get("name", ""), arguments=args))
            # 兜底：Qwen3 在 auto 模式下把调用写成 content 里的 <tool_call> 标签，
            # OpenAI 风格 tool_calls 字段为 None。两条来源合并，避免真实权重调用丢失。
            if not calls and text:
                calls = _parse_tool_calls_from_content(text)
                if calls:
                    text = ""  # 该轮 content 仅是工具调用封装，不作为最终回复
            # 坑⑥ 修复：带出 llama_cpp 返回的真实 token 用量（含缓存命中）
            usage = _parse_usage(out.get("usage"))
            return EngineResult(text=text, tool_calls=calls, usage=usage)
        except Exception:
            # 回退：纯文本补全（部分非 instruct 权重无 chat 模板时仍可工作）
            # 马维斯 P2-1：异常/空回复不再静默吞掉——重试一次，仍失败则返回明确
            # 错误提示，让用户看到「本地引擎未返回内容」而非无声无息。
            for _attempt in (0, 1):
                try:
                    with self._decode_lock:
                        out = llm(prompt, max_tokens=max_tokens,
                                  temperature=temperature)
                    if isinstance(out, dict) and "choices" in out:
                        fallback_text = out["choices"][0].get("text")
                    else:
                        fallback_text = str(out)
                    if fallback_text and str(fallback_text).strip():
                        return EngineResult(text=str(fallback_text))
                except Exception:  # noqa: BLE001
                    fallback_text = ""
            return EngineResult(
                text="本地引擎未返回内容（模型异常或未配置），请重试或检查本地模型。")

    # ---- 记忆增强适配（memory-smol）：Engine 协议 chat + 语义嵌入 embed ----
    def _resolve_embed_model(self) -> Optional[str]:
        """定位嵌入模型：优先显式 model_path 目录下的 bge/embed 命名 GGUF，
        否则在 models_root 下按文件名关键词（bge / embed / e5）探测。"""
        from . import paths
        root = paths.resolve_models_root()
        for sub in ("gguf", ""):
            d = root / sub if sub else root
            if not d.exists():
                continue
            hits = sorted(d.glob("*.gguf"))
            for h in hits:
                name = h.name.lower()
                if any(k in name for k in ("bge", "embed", "e5")):
                    return str(h)
        return None

    def chat(self, messages: List[dict]) -> str:
        """适配 memory/engine.py 的 Engine 协议：messages -> str。

        供 memory-smol 的 summarize 等记忆增强调用。用 Qwen2.5 原生 chat 模板 +
        裸补全（绕开 llama_cpp 0.3.34 fallback chat format 的
        "Memory is not initialized" bug），与 complete/run_turn 同锁串行化。
        失败返回空串（smol 安全降级）。
        """
        llm = self._ensure()
        try:
            parts = []
            for m in messages:
                role = m.get("role", "user")
                content = m.get("content", "")
                if role == "system":
                    parts.append(f"<|im_start|>system\n{content}<|im_end|>")
                elif role == "assistant":
                    parts.append(f"<|im_start|>assistant\n{content}<|im_end|>")
                else:
                    parts.append(f"<|im_start|>user\n{content}<|im_end|>")
            parts.append("<|im_start|>assistant\n")
            prompt = "".join(parts)
            with self._decode_lock:
                out = llm(prompt, max_tokens=512, temperature=0.3,
                          stop=["<|im_end|>"])
            text = out["choices"][0].get("text") or ""
            return text.strip()
        except Exception:  # noqa: BLE001 —— 失败返回空串，调用方降级
            return ""

    def embed(self, text: str) -> Optional[List[float]]:
        """语义嵌入（bge-small-zh-v1.5，延迟加载单例）。

        供 memory-smol 的 semantic_recall 调用；返回 L2 归一化向量。
        无嵌入模型 / 加载失败返回 None（smol 安全降级到关键词打分）。
        """
        if not text or not text.strip():
            return None
        try:
            if self._embed_llm is None:
                from llama_cpp import Llama
                path = self._resolve_embed_model()
                if not path:
                    return None
                self._embed_llm = Llama(model_path=path, embedding=True,
                                        n_ctx=512, n_gpu_layers=-1, verbose=False)
            with self._decode_lock:
                out = self._embed_llm.create_embedding(text)
            vec = out["data"][0]["embedding"]
            norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            return [x / norm for x in vec]
        except Exception:  # noqa: BLE001 —— 失败返回 None，调用方降级
            return None


def _http_error_body(http_err: "urllib.error.HTTPError") -> str:
    """安全读取 HTTPError 响应体（用于把服务端错误透传给用户）。"""
    try:
        return (http_err.read().decode("utf-8", "replace") or "")
    except Exception:
        return ""


class ApiEngine:
    """用户显式指定的 OpenAI 兼容网络端点。仅当 api_url 非空时才出站。"""

    def __init__(self, api_url: str, api_key: str = "", api_model: str = ""):
        self.api_url = api_url.rstrip("/")
        self.api_key = api_key
        self.api_model = api_model

    def is_available(self) -> bool:
        """轻量可用性判断：要求 api_url 与 api_key 都非空。

        仅用于路由层「条件化降级」——缺其一则视为不可用，不拿必败的请求去兜底。
        """
        return bool(self.api_url and self.api_key)

    # ---- 共享：发请求 + 解析 JSON + 分类错误 + 日志（P3）----
    def _request_json(self, payload: dict, *, timeout: int = 60) -> dict:
        """向 /chat/completions 发请求并解析 JSON。

        任何网络/HTTP 失败都包成带上下文的 RuntimeError，并区分：
        401/403=密钥或鉴权错误、429=限流/额度、5xx=服务端、超时/不可达=网络。
        同时打日志（host/状态码/耗时），便于「思考后无响应」类问题排查。
        """
        url = self.api_url + "/chat/completions"
        host = self.api_url
        model = self.api_model or "默认"
        t0 = time.perf_counter()
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                url, data=data,
                headers={"Content-Type": "application/json",
                         "Authorization": f"Bearer {self.api_key}"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                status = getattr(resp, "status", 200)
            elapsed = (time.perf_counter() - t0) * 1000.0
            logger.info("ApiEngine 请求成功 host=%s model=%s status=%s %.0fms",
                        host, model, status, elapsed)
            return json.loads(raw)
        except urllib.error.HTTPError as e:
            body = _http_error_body(e)
            elapsed = (time.perf_counter() - t0) * 1000.0
            logger.warning("ApiEngine HTTP 错误 host=%s model=%s status=%s %.0fms body=%s",
                           host, model, e.code, elapsed, body[:500])
            raise RuntimeError(self._classify_http(e.code, host, body)) from e
        except urllib.error.URLError as e:
            elapsed = (time.perf_counter() - t0) * 1000.0
            logger.warning("ApiEngine 网络错误 host=%s model=%s %.0fms reason=%s",
                           host, model, elapsed, e.reason)
            raise RuntimeError(
                f"网络 API 请求失败（无法连接 {host}）：{e.reason}。"
                f"请检查网络、api_url 是否正确、以及该端点是否可达。") from e
        except (socket.timeout, TimeoutError) as e:
            elapsed = (time.perf_counter() - t0) * 1000.0
            logger.warning("ApiEngine 超时 host=%s model=%s %.0fms",
                           host, model, elapsed)
            raise RuntimeError(
                f"网络 API 请求超时（{host}，>{timeout}s 无响应）。"
                f"可能是网络慢、模型加载中或端点限流，请稍后重试。") from e
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000.0
            logger.warning("ApiEngine 未知错误 host=%s model=%s %.0fms %s",
                           host, model, elapsed, e)
            raise RuntimeError(f"网络 API 请求失败（{host}）：{e}") from e

    @staticmethod
    def _classify_http(code: int, host: str, body: str) -> str:
        """把 HTTP 状态码翻译成用户可操作的中文提示。"""
        if code in (401, 403):
            return (f"网络 API 鉴权失败（{host} 返回 {code}）。"
                    f"请检查 api_key 是否正确、是否过期或被禁用。"
                    f"服务端消息：{body[:300]}")
        if code == 429:
            return (f"网络 API 触发限流 / 额度耗尽（{host} 返回 429）。"
                    f"请稍后重试，或检查账户额度。")
        if 500 <= code < 600:
            return (f"网络 API 服务端错误（{host} 返回 {code}）。"
                    f"这是服务端问题，可稍后重试。服务端消息：{body[:300]}")
        return f"网络 API 返回 HTTP {code}（{host}）。服务端消息：{body[:300]}"

    def complete(self, prompt: str, **kw) -> str:
        payload = {
            "model": self.api_model or "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 512,
            "temperature": 0.7,
        }
        data = self._request_json(payload)
        try:
            return data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise RuntimeError(
                f"网络 API 返回结构异常（无法解析 choices）：{e}；"
                f"原始响应：{str(data)[:300]}") from e

    def run_turn(self, prompt: str, tools: Optional[list] = None, *,
                 stream: bool = False,
                 on_token: Optional[Callable[[str], None]] = None,
                 **kw) -> "EngineResult":
        """绑定 API 的 tool_use 闭环：把 tools 透传给 OpenAI 兼容端点。

        stream=True 时走 OpenAI 兼容 SSE：逐行解析 choices[0].delta.content，
        每段增量实时回调 on_token；tool_calls 分片按 index 累积合并。
        流式下 EngineResult.text 仍为完整文本（增量已实时回调），下游逻辑不变。
        """
        payload = {
            "model": self.api_model or "gpt-4o-mini",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": int(kw.get("max_tokens", 900)),
            "temperature": float(kw.get("temperature", 0.7)),
        }
        if tools:
            payload["tools"] = tools
        if stream:
            return self._run_turn_stream(payload, on_token,
                                         timeout=int(kw.get("timeout", 60)))
        data = self._request_json(payload, timeout=int(kw.get("timeout", 60)))
        msg = data["choices"][0]["message"]
        text = msg.get("content") or ""
        calls: List[ToolCall] = []
        for tc in (msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            raw = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw) if isinstance(raw, str) else raw
            except (json.JSONDecodeError, ValueError, TypeError):
                args = {}
            if not isinstance(args, dict):
                args = {}
            calls.append(ToolCall(name=fn.get("name", ""), arguments=args))
        # 兜底：兼容部分本地 OpenAI 兼容服务（llama.cpp / ollama）把调用写进 content
        if not calls and text:
            calls = _parse_tool_calls_from_content(text)
            if calls:
                text = ""
        # P2 空回复守卫：空内容且无工具调用 = 无效响应，转为可见错误而非静默无响应。
        # 这是「思考一下就没反应」的主因之一——端点偶尔返回空 choices，原代码会
        # 把空串存档，前端看不到任何气泡。
        if not text and not calls:
            raise RuntimeError(
                f"模型返回了空内容（api_model={self.api_model or '默认'}）。"
                f"请检查 api_model 是否正确、或该端点是否支持该模型；可尝试更换模型或重试。")
        # 坑⑥ 修复：带出真实 token 用量（含 prompt 缓存命中），供内感受/诊断量化。
        # 用量字段缺失时安全降级为 None（不假设有缓存）。
        usage = _parse_usage(data.get("usage"))
        return EngineResult(text=text, tool_calls=calls, usage=usage)

    def _run_turn_stream(self, payload: dict, on_token: Optional[Callable[[str], None]],
                         *, timeout: int = 60) -> "EngineResult":
        """OpenAI 兼容 SSE 流式推理：逐行解析 data: 块，content 增量实时回调。

        - tool_calls 在流式下是分片（按 index 累积 id/name/arguments 片段）。
        - 一旦出现 tool_calls 分片，后续 content 不再回调 on_token（工具轮不外流文本）。
        - 末尾 [DONE] 或连接结束即停止；usage 从最后一块（choices 为空）捕获。
        - 任何网络/HTTP 失败与 _request_json 同口径分类，抛带上下文的 RuntimeError。
        """
        url = self.api_url + "/chat/completions"
        host = self.api_url
        model = self.api_model or "默认"
        t0 = time.perf_counter()
        payload = dict(payload)
        payload["stream"] = True
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})
        text_parts: List[str] = []
        calls: List[ToolCall] = []
        # 流式 tool_calls 分片累积：index -> {id, name, args_parts}
        tc_buf: Dict[int, dict] = {}
        saw_tool_call = False
        usage_raw: Optional[dict] = None
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    chunk = line[len("data:"):].strip()
                    if not chunk or chunk == "[DONE]":
                        continue
                    try:
                        obj = json.loads(chunk)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if obj.get("usage"):
                        usage_raw = obj.get("usage")
                    choices = obj.get("choices") or []
                    if not choices:
                        continue
                    delta = choices[0].get("delta") or {}
                    for tc in (delta.get("tool_calls") or []):
                        saw_tool_call = True
                        try:
                            idx = int(tc.get("index", 0))
                        except (TypeError, ValueError):
                            idx = 0
                        buf = tc_buf.setdefault(idx, {"id": "", "name": "", "args": []})
                        if tc.get("id"):
                            buf["id"] = tc["id"]
                        fn = tc.get("function") or {}
                        if fn.get("name"):
                            buf["name"] = fn["name"]
                        if fn.get("arguments"):
                            buf["args"].append(fn["arguments"])
                    d = delta.get("content")
                    if d and not saw_tool_call:
                        text_parts.append(d)
                        if on_token is not None:
                            try:
                                on_token(d)
                            except Exception:  # noqa: BLE001 - 回调失败不阻断生成
                                pass
            elapsed = (time.perf_counter() - t0) * 1000.0
            logger.info("ApiEngine 流式请求成功 host=%s model=%s %.0fms",
                        host, model, elapsed)
        except urllib.error.HTTPError as e:
            body = _http_error_body(e)
            raise RuntimeError(self._classify_http(e.code, host, body)) from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"网络 API 请求失败（无法连接 {host}）：{e.reason}。"
                f"请检查网络、api_url 是否正确、以及该端点是否可达。") from e
        except (socket.timeout, TimeoutError) as e:
            raise RuntimeError(
                f"网络 API 请求超时（{host}，>{timeout}s 无响应）。"
                f"可能是网络慢、模型加载中或端点限流，请稍后重试。") from e
        # 合并 tool_calls 分片（按 index 排序，保证参数片段顺序）
        for idx in sorted(tc_buf):
            buf = tc_buf[idx]
            name = buf["name"]
            args_raw = "".join(buf["args"]) or "{}"
            try:
                args = json.loads(args_raw) if isinstance(args_raw, str) else args_raw
            except (json.JSONDecodeError, ValueError, TypeError):
                args = {}
            if not isinstance(args, dict):
                args = {}
            if name:
                calls.append(ToolCall(name=name, arguments=args))
        text = "".join(text_parts)
        # 兜底：兼容部分本地 OpenAI 兼容服务把调用写进 content
        if not calls and text:
            calls = _parse_tool_calls_from_content(text)
            if calls:
                text = ""
        # P2 空回复守卫：空内容且无工具调用 = 无效响应，转为可见错误而非静默无响应。
        if not text and not calls:
            raise RuntimeError(
                f"模型返回了空内容（api_model={self.api_model or '默认'}）。"
                f"请检查 api_model 是否正确、或该端点是否支持该模型；可尝试更换模型或重试。")
        usage = _parse_usage(usage_raw)
        return EngineResult(text=text, tool_calls=calls, usage=usage)


class EngineProviderRegistry:
    """内置引擎构建器注册表：engine_kind → 引擎工厂（读 EngineConfig 构建）。"""

    _builders: Dict[str, Callable[[EngineConfig], Any]] = {}

    @classmethod
    def register(cls, kind: str, factory: Callable[[EngineConfig], Any]) -> None:
        cls._builders[kind] = factory

    @classmethod
    def build(cls, kind: str, config: EngineConfig) -> Optional[Any]:
        """按 engine_kind 构建引擎实例；未知 kind 返回 None。"""
        factory = cls._builders.get(kind)
        if factory is None:
            return None
        return factory(config)

    @classmethod
    def available_kinds(cls) -> List[str]:
        return list(cls._builders.keys())


# 注册内置引擎池（import 本模块即触发）。
EngineProviderRegistry.register("local", lambda cfg: LocalGGUFEngine(cfg.local_model or None))
EngineProviderRegistry.register("api", lambda cfg: ApiEngine(cfg.api_url, cfg.api_key, cfg.api_model))
