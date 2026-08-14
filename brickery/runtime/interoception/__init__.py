"""§4.5 自我状态感知（内感受子系统）— 包入口（clean room，纯自研）。

让 Shadeling 在回合后同步采集自身运行状态，融合为「感觉状态向量」，
持久化到 ~/.shadeling/interoception/，供浮现层（阶段 II）与 UI（阶段 III）消费。
"""
from __future__ import annotations

from .metrics import TurnObservations, compute_token_estimate, ngram_repetition
from .baseline import BaselineStore
from .sensors import SensorReading, build_readings
from .fusion import (
    fuse, compute_trend, build_alerts, EmergenceDecision,
)
from .state import InteroceptiveState, load_state, save_state
from .system import InteroceptionSystem

__all__ = [
    "TurnObservations", "compute_token_estimate", "ngram_repetition",
    "BaselineStore", "SensorReading", "build_readings",
    "fuse", "compute_trend", "build_alerts", "EmergenceDecision",
    "InteroceptiveState", "load_state", "save_state", "InteroceptionSystem",
]
