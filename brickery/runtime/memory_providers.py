"""记忆能力注册表（Memory Capability Registry）。

仿 EngineProviderRegistry：把「记忆能力积木 → 门面方法清单」的映射暴露给
积木层做校验与组装。能力实现仍留在 memory/ 各子模块（clean room，不依赖
runtime/），本模块只提供运行时视图——积木 brick.json 只声明 memory_kind，
真正的方法清单来自 memory.CAPABILITY_KINDS（单一事实源，随内核演进）。

铁律：
- 积木不携带可执行代码，只声明 memory_kind；
- 能力实现与数据表全部在内核 memory/ 子系统，积木仅做「接入/摘除」开关。
"""
from __future__ import annotations

from typing import Dict, List

from brickery.memory import CAPABILITY_KINDS


class MemoryCapabilityRegistry:
    """memory_kind → 门面方法名清单（从 memory.CAPABILITY_KINDS 导入注册）。"""

    _kinds: Dict[str, List[str]] = {}

    @classmethod
    def register(cls, kind: str, methods: List[str]) -> None:
        cls._kinds[kind] = list(methods)

    @classmethod
    def methods_of(cls, kind: str) -> List[str]:
        return list(cls._kinds.get(kind, []))

    @classmethod
    def available_kinds(cls) -> List[str]:
        return sorted(cls._kinds.keys())


# 注册内置记忆能力池（import 本模块即触发）。
for _kind, _methods in CAPABILITY_KINDS.items():
    MemoryCapabilityRegistry.register(_kind, _methods)
