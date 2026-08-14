"""§3.4 确认弹窗 IPC 中枢（进程内，零外部依赖）。

把主循环里 MEDIUM/HIGH 风险工具的「阻塞确认」与 Swift 真弹窗（经 IPC 长轮询）
对接起来：

- loop 侧：``IpcConfirmationGateway.ask()`` 调 ``ConfirmBroker.create()`` 拿到
  ``(cid, event)``，再 ``wait(cid, timeout)`` 阻塞等裁决。
- Swift 侧：经现有 IPC socket 长轮询 ``confirm_next`` 取待确认项，弹 NSAlert，
  用户点选后 ``confirm_resolve`` 裁决。

设计要点：
- 仅用 stdlib ``threading``，不引入任何网络/重型依赖。
- broker 为单例挂在 IpcServer 上，被 loop（chat 线程）与 IPC 长轮询（另一连接线程）
  并发访问，全部操作加锁。
- 无人应答（如 headless 无 Swift 轮询）则超时→拒绝（安全默认：绝不静默放行高危）。
"""
from __future__ import annotations

import threading
import time as _time
from typing import Any, Dict, Optional, Tuple

from .tools import ConfirmationGateway, Mode, RiskLevel

# 统一的单调时钟来源（测试可比对），避免与系统时间混用
_monotonic = _time.monotonic


class ConfirmBroker:
    """进程内确认请求/响应中枢。"""

    def __init__(self, timeout: float = 120.0):
        self.timeout = timeout
        self._lock = threading.Lock()
        self._pending: Dict[str, dict] = {}
        self._counter = 0
        # 用 Condition 而非裸 Event：next_pending 在「pending 非空但全 returned」
        # 时也必须阻塞等待新请求，杜绝无 sleep 忙循环烧本地临时端口。
        # （条件=存在「未取过」的待确认项；create 时 notify，resolve 移除后用
        #  下一次 create 重新 notify，避免 lost wakeup。）
        self._new = threading.Condition(self._lock)

    def create(self, tool_name: str, args: Optional[dict]) -> Tuple[str, threading.Event]:
        """注册一个待确认请求，返回 (cid, event)。loop 侧拿到后 wait(cid)。"""
        with self._lock:
            self._counter += 1
            cid = f"cfm_{self._counter}"
            ev = threading.Event()
            self._pending[cid] = {
                "event": ev,
                "decision": None,
                "returned": False,  # 是否已被 Swift 长轮询取走过
                "info": {"tool_name": tool_name, "args": args or {}},
            }
            self._new.notify_all()  # 有新请求，唤醒可能阻塞在 next_pending 的轮询
        return cid, ev

    def next_pending(self, wait_timeout: float = 0.0) -> Optional[dict]:
        """Swift 长轮询：等一个未取过的待确认项并返回其只读信息；超时返回 None。

        返回的 dict 形如 ``{"id": cid, "tool_name": ..., "args": ...}``。

        关键：无论是否已有 pending，只要「当前没有未取过的项」，就阻塞等待
        （wait_timeout 内），而不是立即返回 None。否则前端在无待确认项时收到
        空响应会立即无 sleep 重发，形成忙循环、每次新建 socket 烧本地临时端口。
        """
        with self._lock:
            deadline = _monotonic() + max(0.0, wait_timeout)
            while True:
                for cid, it in self._pending.items():
                    if not it["returned"]:
                        it["returned"] = True
                        return {"id": cid, **it["info"]}
                remaining = deadline - _monotonic()
                if remaining <= 0:
                    return None
                self._new.wait(timeout=remaining)

    def resolve(self, cid: str, decision: bool) -> bool:
        """Swift 裁决：设置决策并唤醒阻塞的 loop 线程。返回是否命中待确认项。"""
        with self._lock:
            it = self._pending.get(cid)
            if it is None:
                return False
            it["decision"] = bool(decision)
            it["event"].set()
            # 注意：不再 clear()——Condition 模式下新请求由 create() 的 notify_all 唤醒，
            # 无需手动清标志（旧 Event.clear() 语义已由「无未取项即阻塞」替代）。
            # 裁决后移除，避免长会话累积（event 已置位，wait 侧用本地引用读取，安全）
            del self._pending[cid]
        return True

    def wait(self, cid: str, timeout: Optional[float] = None) -> bool:
        """loop 侧阻塞等裁决。返回 True=批准执行，False=拒绝/超时。"""
        it = self._pending.get(cid)
        if it is None:
            return False
        to = self.timeout if timeout is None else timeout
        if it["event"].wait(timeout=to):
            return bool(it["decision"])
        return False


class IpcConfirmationGateway(ConfirmationGateway):
    """把 loop 的确认请求转交 ConfirmBroker，阻塞等 Swift 弹窗裁决。

    broker 为 None 时安全降级为全部批准（headless / 测试兼容）。

    扩展（§5 模式切换 + 确认弹窗打磨）：
    - ``mode``：PLAN 模式下 MEDIUM/HIGH 风险工具直接拒绝，不弹窗（只读思考）。
    - ``remembered``：会话级「记住本次会话的决定」，命中则跳过弹窗直接采信。
    """

    def __init__(self, broker: Optional[ConfirmBroker], timeout: float = 120.0,
                 mode: "Mode" = Mode.NORMAL):
        self.broker = broker
        self.timeout = timeout
        self.mode = mode if isinstance(mode, Mode) else Mode.from_str(mode)
        self.remembered: dict = {}   # tool_name -> decision（会话级记忆）

    def set_mode(self, mode: "Mode") -> None:
        self.mode = mode if isinstance(mode, Mode) else Mode.from_str(mode)

    def remember_decision(self, tool_name: str, decision: bool) -> None:
        """记录「本次会话对该工具的决策」，后续同类调用跳过弹窗。"""
        self.remembered[str(tool_name)] = bool(decision)

    def ask(self, tool_call: Any, tool: Any) -> bool:
        if self.broker is None:
            return True
        name = tool.name if tool is not None else getattr(tool_call, "name", "?")
        # Plan 模式：写/执行类默认拒绝，不弹窗（只读思考）
        if (self.mode == Mode.PLAN
                and getattr(tool, "risk", None) in (RiskLevel.MEDIUM,
                                                     RiskLevel.HIGH)):
            return False
        # 会话级「记住决定」：命中则跳过弹窗直接采信
        if name in self.remembered:
            return self.remembered[name]
        try:
            args = getattr(tool_call, "arguments", None)
            cid, _ = self.broker.create(name, args)
            return self.broker.wait(cid, self.timeout)
        except Exception:
            # 任何异常都按拒绝处理（安全默认），不静默放行高危工具
            return False
