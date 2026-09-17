"""Brickery —— agent 底座内核。

底座（安装引导 + 聊天界面 + 积木激活 + 积木市场）即完整成品，经 GitHub
Release 直接分发（2026-09-16 方向修正：放弃组装步骤）。

模块：
- brick_runtime  动态激活协议：BrickLike 生命周期（委托宿主内核机制）
- skill_contract 积木契约：Skill 数据类（brick.json 直映射）
"""
from __future__ import annotations

__version__ = "0.1.0"

from .skill_contract import Skill

__all__ = [
    "Skill", "__version__",
]
