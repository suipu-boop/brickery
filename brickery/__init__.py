"""Brickery —— 独立的「造 agent 的工厂」。

用户拖积木拼装，产出独立可运行的 agent（独立安装包）。
Shadeling 只是本平台产出的第一个成品。

模块：
- assembler      静态组装：依赖/冲突/资源校验，产出组装方案
- brick_runtime  动态激活协议：BrickLike 生命周期（委托宿主内核机制）
- skill_contract 积木契约：Skill 数据类（brick.json 直映射）
- produce        产出链路：方案 → 独立安装包
- web            本地 Web 面板（127.0.0.1）组装工作台
"""
from __future__ import annotations

__version__ = "0.1.0"

from .assembler import Assembler, AssemblyError, AssemblyPlan, Brick, load_vault
from .skill_contract import Skill

__all__ = [
    "Assembler", "AssemblyError", "AssemblyPlan", "Brick", "load_vault",
    "Skill", "__version__",
]
