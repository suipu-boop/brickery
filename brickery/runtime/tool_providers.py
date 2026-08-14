"""内置工具提供者注册表（Tool Provider Registry）。

预编译的 handler 模块池，技能通过 ``provides_tool`` 字段引用。技能包不携带
可执行代码——handler 始终来自 app 内置模块（安全红线，见《DocWrite 规格》§4.5）。

app 启动时由 ``ipc.py`` import 本模块，触发下方注册；技能加载时再从本表按名取工具。
"""
from typing import Callable, Dict, List, Optional

from .tools import Tool
from .docwrite import build_docwrite_tool
from .docwrite_pro import build_docwrite_pro_tool
from .vault_tool import build_vault_query_tool


class ToolProviderRegistry:
    """内置工具提供者注册表。"""

    _providers: Dict[str, Callable[..., Tool]] = {}

    @classmethod
    def register(cls, tool_name: str, factory: Callable[..., Tool]) -> None:
        """注册一个工具工厂。app 启动时调用。"""
        cls._providers[tool_name] = factory

    @classmethod
    def get(cls, tool_name: str, **ctx) -> Optional[Tool]:
        """按名称获取工具实例（含 handler）。不存在返回 None。

        **ctx 透传给工厂（如 home / skill），供需要运行时上下文的
        工具（DocWritePro 需定位并启动引擎二进制）使用。
        """
        factory = cls._providers.get(tool_name)
        if factory is None:
            return None
        # 仅透传 factory 签名接受的参数（如 DocWrite 只收 sandbox，
        # DocWritePro 收 home/skill），避免给不需要的 factory 传多余 kw 报错。
        import inspect
        sig = inspect.signature(factory)
        kw = {k: v for k, v in ctx.items() if k in sig.parameters}
        return factory(**kw)

    @classmethod
    def available_names(cls) -> List[str]:
        return list(cls._providers.keys())


# 注册内置模块池中的工具工厂（import 本模块即触发）。
ToolProviderRegistry.register("DocWrite", build_docwrite_tool)
ToolProviderRegistry.register("DocWritePro", build_docwrite_pro_tool)
ToolProviderRegistry.register("VaultQuery", build_vault_query_tool)
