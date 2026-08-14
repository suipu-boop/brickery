"""§4.5 内感受 — 感觉状态持久化（state.json）。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class InteroceptiveState:
    updated_at: float
    state: Dict[str, float]
    trend: Dict[str, str]
    alerts: List[str]
    summary: str
    readings: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "updated_at": self.updated_at,
            "state": self.state,
            "trend": self.trend,
            "alerts": self.alerts,
            "summary": self.summary,
            "readings": self.readings,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "InteroceptiveState":
        return cls(
            updated_at=d.get("updated_at", 0.0),
            state=d.get("state", {}),
            trend=d.get("trend", {}),
            alerts=d.get("alerts", []),
            summary=d.get("summary", ""),
            readings=d.get("readings", []),
        )


def save_state(home: Path, st: InteroceptiveState) -> None:
    p = Path(home) / "interoception" / "state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st.to_dict(), ensure_ascii=False), encoding="utf-8")


def load_state(home: Path) -> Optional[InteroceptiveState]:
    p = Path(home) / "interoception" / "state.json"
    if not p.exists():
        return None
    try:
        return InteroceptiveState.from_dict(json.loads(p.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, OSError, ValueError):
        return None
