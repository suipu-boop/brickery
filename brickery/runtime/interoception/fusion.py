"""§4.5 内感受 — 信号融合（五维感觉向量 + 趋势 + 预警 + 浮现判定）。

融合权重沿用原研究逻辑，但每个维度先经「归一化」映射到 0~1（1=最差），
再加权。缺失（None）维度在加权时跳过，不影响其它维度。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

# 融合权重（原函数，归一化后维度均 0~1，1=最差）
_WEIGHTS = {
    "cognitive_load": {
        "context_utilization": 0.4,
        "reasoning_depth": 0.3,
        "reasoning_backtrack": 0.2,
        "tool_retry_count": 0.1,
    },
    "execution_friction": {
        "tool_failure_rate": 0.5,
        "tool_latency_internal": 0.3,
        "tool_retry_count": 0.2,
    },
    "memory_coherence": {
        "context_fragmentation": 0.5,
        "memory_retrieval_quality": 0.5,
    },
    "output_fluency": {
        "output_repetition": 1.0,
    },
}

# 越高=越差 的维度需反转（1 - norm）
_HIGHER_IS_WORSE = {
    "context_utilization", "reasoning_depth", "reasoning_backtrack",
    "tool_retry_count", "tool_failure_rate", "tool_latency_internal",
    "context_fragmentation", "output_repetition",
}

_LABELS = {
    "cognitive_load": "认知负荷",
    "execution_friction": "执行阻力",
    "memory_coherence": "记忆一致性",
    "output_fluency": "输出流畅度",
    "overall_ease": "整体舒适度",
}


# 超出 0~1 原生量纲的维度，先做合理缩放（初始标定，后续可据实测调参）
_SCALE = {
    "tool_latency_internal": 2000.0,   # ms：2s 视为最差
    "reasoning_depth": 20.0,           # 工具调用次数：20 次视为上限
}

def _norm(val, key: str) -> Optional[float]:
    if val is None:
        return None
    raw = float(val)
    if key in _SCALE:
        raw = max(0.0, min(1.0, raw / _SCALE[key]))
    else:
        raw = max(0.0, min(1.0, raw))
    return raw if key in _HIGHER_IS_WORSE else (1.0 - raw)


def _wsum(weights: Dict[str, float], d: Dict[str, float]) -> float:
    num = 0.0
    den = 0.0
    for k, w in weights.items():
        nv = _norm(d.get(k), k)
        if nv is None:
            continue
        num += w * nv
        den += w
    return (num / den) if den > 0 else 0.0


def fuse(obs: "TurnObservations", prev_state: Optional[dict] = None) -> Dict[str, float]:
    """融合观测为五维感觉向量（0~1，越高=越差），含整体舒适度。"""
    d = obs.as_dict()
    cog = _wsum(_WEIGHTS["cognitive_load"], d)
    fric = _wsum(_WEIGHTS["execution_friction"], d)

    memq = _norm(d.get("memory_retrieval_quality"), "memory_retrieval_quality")
    frag = _norm(d.get("context_fragmentation"), "context_fragmentation")
    # memory_coherence 越高越好；碎片(frag 已归一化，越高=越差)取反向
    frag_good = (1.0 - frag) if frag is not None else 0.5
    if memq is None:
        mem = frag_good
    else:
        mem = 0.5 * memq + 0.5 * frag_good

    rep = _norm(d.get("output_repetition"), "output_repetition")
    flu = 1.0 - rep if rep is not None else 0.5

    # overall_ease = 1 - 加权负荷（负荷越高越不舒适）
    overall = 1.0 - (0.4 * cog + 0.3 * fric + 0.2 * (1.0 - mem) + 0.1 * (1.0 - flu))

    return {
        "cognitive_load": round(cog, 3),
        "execution_friction": round(fric, 3),
        "memory_coherence": round(mem, 3),
        "output_fluency": round(flu, 3),
        "overall_ease": round(overall, 3),
    }


def compute_trend(state: Dict[str, float], prev: Optional[dict]) -> Dict[str, str]:
    if not prev:
        return {k: "stable" for k in state}
    out = {}
    for k, v in state.items():
        pv = prev.get(k)
        if pv is None:
            out[k] = "stable"
        elif v > pv + 0.05:
            out[k] = "worsening"
        elif v < pv - 0.05:
            out[k] = "improving"
        else:
            out[k] = "stable"
    return out


def build_alerts(state: Dict[str, float], trend: Dict[str, str],
                 readings: List["SensorReading"]) -> List[str]:
    alerts = []
    for r in readings:
        if abs(r.deviation) >= 2.0 and r.confidence >= 0.5:
            direction = "偏高" if r.deviation > 0 else "偏低"
            alerts.append(f"{r.sensor_id} {direction}（偏离基线 {r.deviation:.1f}σ）")
    for k, t in trend.items():
        if t == "worsening" and state.get(k, 0) > 0.6:
            alerts.append(f"{k} 连续恶化，当前 {state[k]:.2f}")
    return alerts[:5]


class EmergenceDecision:
    """浮现触发判定（阶段 II 使用，O2 条件触发风格）。"""

    @staticmethod
    def should_emerge(state: Dict[str, float], trend: Dict[str, str],
                      readings: List["SensorReading"]) -> bool:
        for r in readings:
            if abs(r.deviation) >= 0.5 and r.confidence >= 0.4:
                return True
        for k, t in trend.items():
            if t == "worsening" and state.get(k, 0) > 0.7:
                return True
        return False

    @staticmethod
    def intensity(state: Dict[str, float], trend: Dict[str, str],
                  readings: List["SensorReading"]) -> str:
        max_dev = max((abs(r.deviation) for r in readings), default=0.0)
        if max_dev >= 2.0:
            return "strong"
        if max_dev >= 1.0:
            return "medium"
        if max_dev >= 0.5:
            return "weak"
        return "none"

    @staticmethod
    def summary(state: Dict[str, float], trend: Dict[str, str]) -> str:
        parts = []
        for k in ("cognitive_load", "execution_friction", "memory_coherence", "output_fluency"):
            v = state.get(k, 0.0)
            t = trend.get(k, "stable")
            if v >= 0.6 or t == "worsening":
                word = "偏高" if v >= 0.6 else "上升"
                parts.append(f"{_LABELS[k]}{word}")
        return "，".join(parts) if parts else "状态良好"
