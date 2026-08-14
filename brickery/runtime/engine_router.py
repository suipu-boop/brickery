"""§4 引擎路由（clean room）。

统一管理「用什么推理后端」（随朴 2026-08-06 决策：首推 API 为主、本地为备选）：
- **首选 = 用户显式指定的网络 API**（如 DeepSeek / 通义 / 智谱，国内可直连）：
  质量最高、function-calling 最稳，是首版主力。
- **备选 = 进程内嵌本地推理（GGUF + Metal）**：作为 API 不可用时的**自动降级兜底**
  （断网 / 额度耗尽 / 鉴权失败），隐私安全、不出本机。
- 红线：API 端点必须用户显式填写（不硬编码任何第三方推理地址）；本地 GGUF 仅作
  降级兜底，不偷偷外传记忆/内容；两个后端都不可用才抛 NoEngineConfigured，
  绝不静默连外网。
- 测试证明：无任何配置时调用 complete() 会抛 NoEngineConfigured 且**零出站连接**。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from .config import EngineConfig


class NoEngineConfigured(RuntimeError):
    """无任何可用推理后端时抛出（红线：不得静默连外网）。"""
    pass


# 引擎实例需满足：callable(prompt, **kw) -> str，或具备 .complete(prompt, **kw) -> str
EngineLike = Any


@dataclass
class ToolCall:
    """模型请求执行的一个工具调用。"""
    name: str
    arguments: dict = field(default_factory=dict)


@dataclass
class PromptUsage:
    """一轮推理的真实 token 用量（来自引擎返回的 usage 字段）。

    关键：这是**真实计费口径**，不是字符粗估。inneroception 与诊断据此量化
    上下文占用与 prompt 缓存命中率——小白省 token 方案最强调的教训就是
    「不能假设缓存有效，要能量化」（坑⑥）。
    """
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cached_tokens: int = 0        # prompt 缓存命中 token 数（DeepSeek / OpenAI 兼容）

    @property
    def cache_hit_rate(self) -> float:
        """缓存命中率 0~1；无用量信息时返回 0.0（低置信）。"""
        if self.prompt_tokens <= 0:
            return 0.0
        return min(1.0, self.cached_tokens / self.prompt_tokens)


@dataclass
class EngineResult:
    """一轮推理的统一返回结构（文本 + 可选的工具调用）。

    本地 GGUF 与绑定 API 两条路径都收敛到它，主循环无需关心后端差异。
    """
    text: str = ""
    tool_calls: List[ToolCall] = field(default_factory=list)
    usage: Optional["PromptUsage"] = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


def _invoke(engine: EngineLike, prompt: str, **kw) -> str:
    if callable(engine) and not hasattr(engine, "complete"):
        return engine(prompt, **kw)
    return engine.complete(prompt, **kw)


def _engine_available(engine: EngineLike) -> bool:
    """轻量可用性判断（不实际加载权重、不发请求）。

    引擎实例若实现 ``is_available()`` 则采用其结果；否则保守视为可用，
    保持向后兼容（旧式引擎无该方法时行为不变）。用于路由层「条件化降级」：
    不把请求发给根本跑不起来的兜底后端。
    """
    fn = getattr(engine, "is_available", None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:  # noqa: BLE001 - 可用性探测自身失败 = 不可用
            return False
    return True


class EngineRouter:
    """推理后端路由：API 为主、本地 GGUF 为自动降级兜底。

    两条路径都收敛到统一的 EngineResult，主循环无需关心后端差异。首选后端
    调用失败时（含未配置 / 网络错误 / 超时 / 鉴权失败）自动尝试备选后端；
    两个都不可用才抛 NoEngineConfigured。
    """

    def __init__(self, config: EngineConfig, *,
                 local_engine: EngineLike = None,
                 api_engine: EngineLike = None):
        self.config = config
        self._local = local_engine
        self._api = api_engine

    # ---- 内部：取某后端的引擎实例（未配置即抛 NoEngineConfigured）----
    def _get_engine(self, backend: str) -> EngineLike:
        if backend == "local":
            # 条件化降级闸门：本地兜底必须「真可用」——llama_cpp 可导入且存在
            # 可加载的 GGUF。否则视为未配置，绝不把请求发给跑不起来的引擎（坑：
            # 用户只配了网络 API 时，本地兜底不可用，应让首选后端的错误直接上浮，
            # 而不是冒出「本地推理依赖未安装」之类与用户选择无关的鬼话）。
            if self._local is None or not _engine_available(self._local):
                raise NoEngineConfigured(
                    "本地推理后端不可用（未放置 GGUF 模型，或未安装 llama-cpp-python）。"
                    "请放置本地模型，或在设置中填写网络 API 端点。"
                )
            return self._local
        if backend == "api":
            if not self._api or not _engine_available(self._api):
                raise NoEngineConfigured(
                    "API 推理后端未配置（缺少 api_url 或 api_key）。"
                    "请在设置中填写网络 API 端点，或放置本地 GGUF 模型改用本地推理。"
                )
            return self._api
        # 任何非预期 backend：明确报错，绝不回退到外部服务
        raise NoEngineConfigured(
            f"未配置推理后端（backend={backend!r}）。"
            "Shadeling 不会静默连接任何外部地址；"
            "请在配置中指定本地模型或显式 API 端点。"
        )

    def _others(self, preferred: str) -> List[str]:
        return [b for b in ("api", "local") if b != preferred]

    # ---- 纯文本补全（API 主 / 本地备 自动降级）----
    def complete(self, prompt: str, **kw) -> str:
        preferred = self.config.backend
        # 1) 先试首选
        try:
            return _invoke(self._get_engine(preferred), prompt, **kw)
        except NoEngineConfigured:
            pass  # 首选未配置 → 转备选
        except Exception as e:  # 首选配置了但调用失败（网络/超时/鉴权）
            return self._try_fallback(preferred, e, prompt, **kw)
        # 2) 首选未配置 → 试备选
        return self._try_fallback(preferred, None, prompt, **kw)

    def _try_fallback(self, preferred: str,
                      primary_err: Optional[Exception],
                      prompt: str, **kw) -> str:
        last_err: Optional[Exception] = primary_err
        for b in self._others(preferred):
            try:
                return _invoke(self._get_engine(b), prompt, **kw)
            except NoEngineConfigured:
                continue
            except Exception as e:
                last_err = e
                continue
        if last_err is not None:
            raise last_err
        raise NoEngineConfigured(
            f"未配置任何可用推理后端（preferred={preferred!r}）。"
            "请在配置中指定本地模型或显式 API 端点。"
        )

    # ---- Function-Calling 闭环（阶段 B，含自动降级）----
    def run_turn(self, prompt: str, tools: Optional[list] = None, *,
                 stream: bool = False,
                 on_token: Optional[Callable[[str], None]] = None,
                 **kw) -> "EngineResult":
        preferred = self.config.backend
        try:
            return self._run_on_backend(preferred, prompt, tools,
                                        stream=stream, on_token=on_token, **kw)
        except NoEngineConfigured:
            pass
        except Exception as e:
            return self._try_fallback_turn(preferred, e, prompt, tools,
                                           stream=stream, on_token=on_token, **kw)
        return self._try_fallback_turn(preferred, None, prompt, tools,
                                       stream=stream, on_token=on_token, **kw)

    def _try_fallback_turn(self, preferred, primary_err, prompt, tools, *,
                           stream: bool = False,
                           on_token: Optional[Callable[[str], None]] = None,
                           **kw):
        last_err = primary_err
        for b in self._others(preferred):
            try:
                return self._run_on_backend(b, prompt, tools,
                                            stream=stream, on_token=on_token, **kw)
            except NoEngineConfigured:
                continue
            except Exception as e:
                last_err = e
                continue
        if last_err is not None:
            raise last_err
        raise NoEngineConfigured(
            f"未配置任何可用推理后端（preferred={preferred!r}）。")

    def _run_on_backend(self, backend, prompt, tools, *,
                        stream: bool = False,
                        on_token: Optional[Callable[[str], None]] = None,
                        **kw) -> "EngineResult":
        engine = self._get_engine(backend)
        run_method = getattr(engine, "run_turn", None)
        if callable(run_method):
            try:
                res = run_method(prompt, tools,
                                 stream=stream, on_token=on_token, **kw)
            except TypeError:
                # 兼容实现了 run_turn 但不接受 tools 参数的旧引擎
                res = run_method(prompt, **kw)
            if isinstance(res, EngineResult):
                return res
            if isinstance(res, str):
                return EngineResult(text=res)
            return EngineResult(text=str(res))
        # 退化路径：旧式引擎无 run_turn
        text = _invoke(engine, prompt, **kw)
        return EngineResult(text=text)

    def set_backend(self, backend: str, *,
                    local_model: str = None,
                    api_url: str = None,
                    api_key: str = None,
                    api_model: str = None) -> None:
        """热切换后端（修改后下次调用生效，无需重启进程）。

        选择 api 后端时**强制要求**显式 api_url —— 不允许静默默认外连。
        """
        if backend not in ("local", "api"):
            raise ValueError(f"不支持的 backend：{backend!r}")
        if backend == "api" and not api_url:
            raise ValueError("选择 api 后端时必须显式提供 api_url"
                             "（用户主动指定的网络端点）。")
        self.config.backend = backend
        if local_model is not None:
            self.config.local_model = local_model
        if api_url is not None:
            self.config.api_url = api_url
        if api_key is not None:
            self.config.api_key = api_key
        if api_model is not None:
            self.config.api_model = api_model

    # ---- 积木接入点（P5 EngineBrick）：把外部构建好的引擎接入 / 拔出槽位 ----
    def set_engine(self, kind: str, engine: EngineLike) -> None:
        """把 EngineBrick 构建好的引擎接入对应后端槽位（不改 config.backend）。

        心脏路由与降级逻辑不变，仅替换 local / api 槽位的引擎实例。
        首选后端（config.backend）仍由用户显式选择，积木不越权改。
        """
        if kind == "local":
            self._local = engine
        elif kind == "api":
            self._api = engine
        else:
            raise ValueError(f"不支持的 engine_kind：{kind!r}（仅 local / api）")

    def clear_engine(self, kind: str) -> None:
        """拔出某后端槽位的引擎（EngineBrick.deactivate 回退）。"""
        if kind == "local":
            self._local = None
        elif kind == "api":
            self._api = None
        else:
            raise ValueError(f"不支持的 engine_kind：{kind!r}（仅 local / api）")
