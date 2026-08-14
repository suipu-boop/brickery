"""§4.5 内感受 — 系统主控（采集→融合→持久化）。

每回合由 loop.py 调用 observe_and_update()，完成：
1. 把本轮观测转为传感器读数（推入短期窗、更新长期基线）
2. 融合为五维感觉向量
3. 计算趋势 / 预警 / 自然语摘要
4. 持久化到 ~/.shadeling/interoception/state.json（含 baseline.json）
全程同步、报错静默，不阻塞主流程。
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Optional

from .metrics import TurnObservations
from .baseline import BaselineStore
from .sensors import build_readings
from .fusion import fuse, compute_trend, build_alerts, EmergenceDecision
from .state import InteroceptiveState, save_state, load_state


def _default_home() -> Path:
    """解析 Shadeling 运行时根（与 config.paths.get_home 一致）：
    SHADELING_HOME 环境变量可覆盖，默认 ~/.shadeling。"""
    return Path(os.environ.get("SHADELING_HOME",
                               os.path.expanduser("~/.shadeling")))


class InteroceptionSystem:
    def __init__(self, home: Optional[Path] = None):
        self.home = Path(home) if home else _default_home()
        self.store = BaselineStore(self.home)
        self._prev_state: Optional[dict] = None
        self._worsen_streak: dict = {}

    def observe_and_update(self, obs: TurnObservations) -> InteroceptiveState:
        readings = build_readings(obs, self.store)
        state = fuse(obs, self._prev_state)
        trend = compute_trend(state, self._prev_state)
        alerts = build_alerts(state, trend, readings)
        summary = EmergenceDecision.summary(state, trend)
        st = InteroceptiveState(
            updated_at=time.time(),
            state=state,
            trend=trend,
            alerts=alerts,
            summary=summary,
            readings=[r.__dict__ for r in readings],
        )
        save_state(self.home, st)
        self.store.save()
        self._prev_state = state
        # 累积恶化连击（供阶段 II 连续 3 轮判定）
        for k, t in trend.items():
            if t == "worsening":
                self._worsen_streak[k] = self._worsen_streak.get(k, 0) + 1
            else:
                self._worsen_streak[k] = 0
        return st

    def get_state(self) -> Optional[InteroceptiveState]:
        return load_state(self.home)
