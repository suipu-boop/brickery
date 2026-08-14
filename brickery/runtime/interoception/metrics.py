"""§4.5 内感受 — 回合观测与指标计算（clean room，纯自研）。

负责把一轮对话的「原始观测值」与「派生指标」算清楚。所有维度均为
连续值（非布尔），缺失用 None 表示（低置信，融合时弱化）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

_CODE_BLOCK_RE = re.compile(r"```.*?```", flags=re.S)


@dataclass
class TurnObservations:
    """一轮对话采集到的原始观测值。允许 None 表示暂时无法观测（低置信）。"""

    tool_latency_internal: float = 0.0          # 内部工具调用平均延迟(ms)
    tool_failure_rate: float = 0.0              # 工具失败/拒绝比例 0~1
    tool_retry_count: float = 0.0               # 重试次数
    context_utilization: float = 0.0            # 上下文占用 0~1
    context_fragmentation: float = 0.0          # 上下文碎片 0~1
    reasoning_depth: float = 0.0                # 推理步数（工具调用次数）
    reasoning_backtrack: float = 0.0            # 推理回退频率 0~1
    output_repetition: float = 0.0              # 输出重复率 0~1
    memory_retrieval_quality: Optional[float] = None  # 记忆检索质量 0~1
    # —— 真实 token 用量（坑⑥ 量化落点，来自引擎 usage 字段）——
    context_tokens: int = 0                      # 本轮 prompt 真实 token 数（无用量时为字符粗估）
    context_token_source: str = "estimate"       # "real"(引擎精确) | "estimate"(字符粗估)
    prompt_cache_hit_tokens: int = 0             # prompt 缓存命中 token 数（可见化命中率）
    context_window: int = 0                      # 计算利用率时用的真实窗口分母

    def as_dict(self) -> dict:
        return {
            "tool_latency_internal": self.tool_latency_internal,
            "tool_failure_rate": self.tool_failure_rate,
            "tool_retry_count": self.tool_retry_count,
            "context_utilization": self.context_utilization,
            "context_fragmentation": self.context_fragmentation,
            "reasoning_depth": self.reasoning_depth,
            "reasoning_backtrack": self.reasoning_backtrack,
            "output_repetition": self.output_repetition,
            "memory_retrieval_quality": self.memory_retrieval_quality,
            "context_tokens": self.context_tokens,
            "context_token_source": self.context_token_source,
            "prompt_cache_hit_tokens": self.prompt_cache_hit_tokens,
            "context_window": self.context_window,
        }


def compute_token_estimate(text: str) -> int:
    """粗略 token 估算：CJK 按字、其它按 4 字符≈1 token。用于上下文占用估计。"""
    if not text:
        return 0
    cjk = sum(1 for ch in text if "一" <= ch <= "鿿")
    other = len(text) - cjk
    return cjk + other // 4 + (1 if other % 4 else 0)


def ngram_repetition(text: str, n: int = 3) -> float:
    """自然语言段落的 n-gram 重复率 0~1（代码块豁免，避免误报）。

    返回值越高代表输出越「车轱辘话」——这是「输出流畅度」的反向信号。
    """
    if not text:
        return 0.0
    cleaned = _CODE_BLOCK_RE.sub("", text).strip()  # 豁免代码块
    if len(cleaned) < n + 1:
        return 0.0
    grams = [cleaned[i : i + n] for i in range(len(cleaned) - n + 1)]
    if not grams:
        return 0.0
    seen = set()
    dup = 0
    for g in grams:
        if g in seen:
            dup += 1
        else:
            seen.add(g)
    return dup / len(grams)
