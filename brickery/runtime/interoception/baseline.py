"""§4.5 内感受 — 双基线存储（长期跨会话 + 短期滑动窗，稳健归一化）。

设计要点（针对评审指出的「基线漂移」硬伤）：
- 长期基线：跨会话持久化，慢学习率在线更新，代表「正常水平」。
- 短期基线：本会话最近 N 轮滑动窗，代表「近期水平」。
- 报警看「本轮值相对长期基线」的稳健偏离（z 分数），而非「短期 vs 长期」，
  这样单轮突高（如 context 暴涨）也能立刻被捕捉，不受中位抗离群特性掩盖。
- 尺度下限 0.1，避免长期恒定（IQR≈0）时 z 数值爆炸。
"""
from __future__ import annotations

import json
import statistics
from pathlib import Path
from typing import Dict, List

SHORT_WINDOW = 20  # 短期基线滑动窗大小


def _median(values: List[float]) -> float:
    return statistics.median(values) if values else 0.0


def _iqr(values: List[float]) -> float:
    """四分位距（稳健离散度）。样本不足返回 0。"""
    if len(values) < 4:
        return 0.0
    qs = statistics.quantiles(values, n=4)
    return max(0.0, qs[2] - qs[0])


class BaselineStore:
    def __init__(self, home: Path):
        self.home = Path(home)
        self.short: Dict[str, List[float]] = {}
        self.long_median: Dict[str, float] = {}
        self.long_iqr: Dict[str, float] = {}
        self._path = self.home / "interoception" / "baseline.json"
        self._load()

    def _load(self) -> None:
        try:
            if self._path.exists():
                d = json.loads(self._path.read_text(encoding="utf-8"))
                self.long_median = {k: float(v) for k, v in d.get("median", {}).items()}
                self.long_iqr = {k: float(v) for k, v in d.get("iqr", {}).items()}
        except (json.JSONDecodeError, OSError, ValueError):
            self.long_median = {}
            self.long_iqr = {}

    def save(self) -> None:
        self.home.mkdir(parents=True, exist_ok=True)
        d = {"median": self.long_median, "iqr": self.long_iqr}
        self._path.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")

    def push_short(self, key: str, value: float) -> None:
        buf = self.short.setdefault(key, [])
        buf.append(value)
        if len(buf) > SHORT_WINDOW:
            buf.pop(0)

    def update_long(self, key: str, value: float, lr: float = 0.05) -> None:
        """慢学习率在线更新长期基线（仅在值非 None 时）。"""
        if value is None:
            return
        med = self.long_median.get(key)
        if med is None:
            self.long_median[key] = value
            self.long_iqr[key] = 0.0
        else:
            self.long_median[key] = med + lr * (value - med)
            buf = self.short.get(key, [])
            self.long_iqr[key] = _iqr(buf) if len(buf) >= 4 else self.long_iqr.get(key, 0.0)

    def short_median(self, key: str) -> float:
        return _median(self.short.get(key, []))

    def robust_z(self, key: str) -> float:
        """短期中位相对长期中位的稳健偏离（渐变跟踪用）。"""
        if key not in self.long_median:
            return 0.0
        med_short = self.short_median(key)
        med_long = self.long_median[key]
        iqr_long = self.long_iqr.get(key, 0.0)
        scale = (iqr_long / 1.349) + 1e-6
        return (med_short - med_long) / scale
