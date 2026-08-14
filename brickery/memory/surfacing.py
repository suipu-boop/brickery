"""§3.5 浮现 / 影子引擎（clean room，对应 O2 / O4 / O5 / O6）。

设计要点：
- 影子模型（ShadowEngine）：单实例串行加载本地 GGUF（O4 = C 单实例串行）。
  按模型路径缓存单例（避免重复加载同一权重、浪费内存）。只封装两类调用：
    ① consolidate —— 把对话文本归纳成 entities / decisions / todos（O5 尺寸，O6 回合后异步）
    ② decide_surface —— 判断该想起哪段旧记忆（可选增强；默认用规则闸门）
- 浮现闸门（SurfaceGate）：条件触发（O2）——指代词 / 话题跳变 / 长间隔，
  明确**否决每轮灌**（只在真需要时注入，呼应宪章记忆子系统基调）。
- surfacing_for：组合 recall + 闸门，给定当前上下文返回应注入的记忆片段。

本模块不强制依赖 llama_cpp：ShadowEngine 接受一个 `complete(prompt)->str` 协议对象
（与 EngineRouter 一致），运行时层注入本地 GGUF complete，测试注入 mock。
无引擎时所有方法安全降级（不阻断存档/推理）。
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import threading
from typing import Callable, List, Optional

# ---- 单例缓存（O4：按模型路径缓存，避免重复加载同一权重）----
_ENGINE_CACHE: dict = {}
_ENGINE_LOCK = threading.Lock()

# 指代词 → 触发浮现（中英文）
_PRONOUN_HINTS = [
    "那", "这个", "那个", "之前", "上次", "刚才", "上文", "前文", "前面",
    "之前说的", "之前提", "还记得", "记得吗", "当时", "之前我们", "刚才说",
    "那次", "你说的", "他说的", "之前聊", "前面提到",
    "that", "this", "previous", "earlier", "above", "remember", "the one",
    "what we", "as i said", "like you said",
]

# 长间隔阈值（秒）：超过则视为「重新开场」，触发浮现以重建上下文
_LONG_IDLE_SECONDS = 30 * 60  # 30 分钟


def _extract_json(text: str) -> dict:
    """从模型输出里尽量抠出第一个 JSON 对象（O5：4-bit 格式遵循脆，需兜底）。"""
    try:
        s = text.strip()
        if s.startswith("```"):
            s = s.split("```")[1]
            if s.startswith("json"):
                s = s[4:]
        start = s.find("{")
        end = s.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(s[start:end + 1])
    except (json.JSONDecodeError, ValueError):
        pass
    return {}


class ShadowEngine:
    """影子模型封装（O4 C 单实例串行）。

    接受 engine 协议 callable(prompt)->str，不强制 llama_cpp。
    生产由 runtime 注入本地 GGUF complete；测试注入 mock。
    """

    def __init__(self, engine: Optional[Callable[[str], str]] = None):
        self._engine = engine

    @classmethod
    def get(cls, model_path: str,
            loader: Callable[[str], Callable[[str], str]]) -> "ShadowEngine":
        """按模型路径缓存单例（O4：避免重复加载同一权重）。

        loader(model_path) -> complete_fn，由 runtime 提供（持 GGUF 加载逻辑）。
        """
        with _ENGINE_LOCK:
            if model_path in _ENGINE_CACHE:
                return _ENGINE_CACHE[model_path]
            inst = cls(engine=loader(model_path))
            _ENGINE_CACHE[model_path] = inst
            return inst

    @classmethod
    def clear_cache(cls) -> None:
        with _ENGINE_LOCK:
            _ENGINE_CACHE.clear()

    def consolidate(self, texts: List[str]) -> dict:
        """把对话文本归纳成结构化记忆（O5 / O6）。

        返回 {"entities":[], "decisions":[], "todos":[]}。
        无引擎或失败时返回空结构（不阻断主流程）。
        """
        joined = "\n".join(t for t in texts if t and t.strip())
        if not joined.strip():
            return {"entities": [], "decisions": [], "todos": []}
        if not self._engine:
            return {"entities": [], "decisions": [], "todos": []}
        prompt = (
            "请从以下对话中提取结构化记忆，严格只输出一个 JSON 对象，不要解释：\n"
            "{\n"
            '  "entities": ["涉及的人 / 物 / 项目 / 概念"],\n'
            '  "decisions": ["达成的决定 / 结论"],\n'
            '  "todos": ["待办 / 行动项"]\n'
            "}\n\n" + joined
        )
        try:
            out = self._engine(prompt)
            data = _extract_json(out)
            return {
                "entities": data.get("entities", []) or [],
                "decisions": data.get("decisions", []) or [],
                "todos": data.get("todos", []) or [],
            }
        except Exception:
            return {"entities": [], "decisions": [], "todos": []}

    def decide_surface(self, query: str, candidates: List[dict],
                       timeout: float = 3.0) -> List[str]:
        """判断应浮现哪些候选记忆（蓝图 A 档「影子自行判断该想起什么」）。

        无引擎或失败时返回空（上层回落到规则候选）。
        timeout：边界保护——影子模型调用可能卡顿（尤其低端机），超时即放弃，
        由上层回落规则候选，绝不因浮现拖垮主聊天延迟。
        """
        if not self._engine or not candidates:
            return []
        cand_lines = "\n".join(
            f"[{i}] {c.get('topic_summary', '')} | 关键词: {', '.join(c.get('keywords', []))}"
            for i, c in enumerate(candidates)
        )
        prompt = (
            "用户当前说：「" + query + "」\n"
            "以下是候选旧记忆，请选出与当前最相关的编号（逗号分隔，无关留空）：\n"
            + cand_lines
            + "\n只输出编号，例如 0,2"
        )
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                fut = ex.submit(self._engine, prompt)
                out = fut.result(timeout=timeout)
            ids = [int(x) for x in re.findall(r"\d+", out)]
            return [candidates[i]["record_id"] for i in ids
                    if 0 <= i < len(candidates)]
        except Exception:
            return []


class SurfaceGate:
    """浮现条件闸门（O2）：指代词 / 话题跳变 / 长间隔。

    三种信号任一命中即触发浮现注入；全不命中则不注入（否决每轮灌）。
    """

    def __init__(self, long_idle_seconds: float = _LONG_IDLE_SECONDS):
        self.long_idle_seconds = long_idle_seconds

    @staticmethod
    def _has_pronoun(text: str) -> bool:
        t = text.lower()
        return any(h in t for h in _PRONOUN_HINTS)

    @staticmethod
    def _topic_shift(current: str, recent_history: List[str]) -> bool:
        """当前消息关键词与最近历史重叠低 → 视为话题跳变。

        简易 n-gram 重叠比：低于阈值则触发浮现（可能需切回旧上下文）。
        """
        if not recent_history:
            return False

        def toks(s: str) -> set:
            return set(re.findall(r"[a-zA-Z][a-zA-Z0-9]+", s.lower())) | \
                   set(re.findall(r"[一-鿿]{2,}", s))

        cur = toks(current)
        if not cur:
            return False
        hist: set = set()
        for h in recent_history[-3:]:
            hist |= toks(h)
        if not hist:
            return False
        overlap = len(cur & hist) / len(cur)
        return overlap < 0.2

    def should_trigger(self, user_message: str,
                       recent_history: Optional[List[str]] = None,
                       idle_seconds: float = 0.0) -> bool:
        if self._has_pronoun(user_message):
            return True
        if idle_seconds >= self.long_idle_seconds:
            return True
        if self._topic_shift(user_message, recent_history or []):
            return True
        return False


def surfacing_for(memory, query: str, project: str | None = None,
                  limit: int = 8, gate: Optional[SurfaceGate] = None,
                  recent_history: Optional[List[str]] = None,
                  idle_seconds: float = 0.0,
                  shadow: Optional[ShadowEngine] = None) -> List[dict]:
    """组合 recall + 闸门，返回应注入的记忆片段（O2 条件触发）。

    流程：先 recall 拿相关性×时间衰减候选 → 闸门判定是否注入 →
    有影子时让其从候选里挑最相关的（蓝图 A 档「自行判断该想起什么」）→
    不触发闸门返回空；无影子 / 影子返回空（不可用或超时）→ 回落规则全量。

    注意：仅做「是否浮现 + 取哪些」的检索决策；记忆内容本身由调用方注入 prompt。
    无引擎也可工作（gate 是纯规则）。
    """
    candidates = memory.recall(query, project=project, limit=limit)
    if not candidates:
        return []
    if gate is None:
        gate = SurfaceGate()
    if not gate.should_trigger(query, recent_history=recent_history,
                               idle_seconds=idle_seconds):
        return []
    # 影子判断（蓝图 A 档）：让本地小模型从候选里挑最相关的，而非全量灌入。
    # 影子缺失 / 返回空（不可用或超时）→ 回落规则全量，不退化。
    if shadow is not None:
        chosen = shadow.decide_surface(query, candidates)
        if chosen:
            chosen_set = set(chosen)
            filtered = [c for c in candidates if c.get("record_id") in chosen_set]
            if filtered:
                return filtered
    return candidates
