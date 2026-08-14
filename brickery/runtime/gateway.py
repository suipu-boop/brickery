"""§7 平台网关 / MCP 接入（预留扩展点，本阶段仅抽象基类 + 注册钩子）。

不实现具体协议，仅定义干净的扩展契约，使未来接入外部平台 / MCP 不破坏现有骨架。
红线：网关接口不得要求外部推理服务；不得在本阶段引入具体第三方协议依赖。
"""
from __future__ import annotations

from typing import Dict, List


class Gateway:
    """抽象基类：未来接入外部平台 / MCP 的接入点。"""

    name: str = "abstract"

    def on_start(self) -> None:
        """守护进程 / 主循环启动时调用。"""
        pass

    def on_stop(self) -> None:
        """守护进程 / 主循环退出时调用。默认无操作；子类可在此关闭连接。"""
        pass

    def on_message(self, payload: dict) -> dict:
        """处理来自外部平台的一条消息，返回响应。

        子类必须实现。
        """
        raise NotImplementedError(f"网关 {self.name!r} 未实现 on_message")


class GatewayRegistry:
    """网关注册表（注册钩子）。未注册任何网关时主循环正常运行。"""

    _registry: Dict[str, Gateway] = {}

    @classmethod
    def register(cls, gateway: Gateway) -> None:
        cls._registry[gateway.name] = gateway

    @classmethod
    def get(cls, name: str) -> "Gateway | None":
        return cls._registry.get(name)

    @classmethod
    def all(cls) -> List["Gateway"]:
        return list(cls._registry.values())

    @classmethod
    def clear(cls) -> None:
        cls._registry.clear()
