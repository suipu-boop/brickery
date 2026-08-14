"""§4.5 内感受 — 传感器读数（本轮值 vs 长期基线 + 偏离 + 置信度）。

每个传感器输出连续值 + 偏离程度（稳健 z）+ 置信度（数据越足越高）。
偏离基于「本轮值相对长期基线」，能捕捉单轮突高（如 context 暴涨），
不受中位抗离群特性掩盖。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:
    from .metrics import TurnObservations
    from .baseline import BaselineStore


@dataclass
class SensorReading:
    sensor_id: str
    value: float
    baseline: float
    deviation: float       # 稳健 z（本轮值相对长期基线）
    confidence: float      # 0~1，数据越足越高


def build_readings(obs: "TurnObservations", store: "BaselineStore") -> List[SensorReading]:
    out: List[SensorReading] = []
    d = obs.as_dict()
    for sid, val in d.items():
        # 仅处理连续数值维度（int/float）。跳过 None（低置信）与分类/文本字段
        # （如 context_token_source="real"/"estimate"），避免把它们当传感器读数。
        if not isinstance(val, (int, float)):
            continue
        v = float(val)
        store.push_short(sid, v)
        store.update_long(sid, v)
        med_long = store.long_median.get(sid, 0.0)
        iqr_long = store.long_iqr.get(sid, 0.0)
        # 尺度下限 0.1，避免长期恒定（IQR≈0）时 z 数值爆炸
        scale = max(iqr_long / 1.349, 0.1) + 1e-6
        z = (v - med_long) / scale
        n = len(store.short.get(sid, []))
        conf = min(1.0, n / 10.0)  # 10 轮后满置信
        out.append(SensorReading(
            sensor_id=sid, value=v, baseline=med_long,
            deviation=z, confidence=conf))
    return out
