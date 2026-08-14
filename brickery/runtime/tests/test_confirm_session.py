"""§3.4 确认弹窗会话记忆：IpcConfirmationGateway 的 mode 推导 + remember 行为。

- PLAN 模式：MEDIUM/HIGH 风险工具直接拒绝，不弹窗（只读思考）。
- 会话级「记住本次会话的决定」：命中则跳过弹窗直接采信。
- 完整 broker 链路：create → 长轮询取用 → resolve 唤醒阻塞的 loop 线程；
  超时无人裁决则安全拒绝（绝不静默放行高危）。
"""
import threading
import time
import unittest

from brickery.runtime.confirm import ConfirmBroker, IpcConfirmationGateway
from brickery.runtime.tools import Mode, RiskLevel


def _tool(risk):
    t = type("T", (), {})()
    t.name = "Edit"
    t.risk = risk
    return t


def _tc(name="Edit", args=None):
    c = type("C", (), {})()
    c.name = name
    c.arguments = args or {}
    return c


class TestIpcGatewayDefaults(unittest.TestCase):
    def test_broker_none_approves(self):
        # broker=None 安全降级为全部批准（headless / 测试兼容）
        gw = IpcConfirmationGateway(broker=None)
        self.assertTrue(gw.ask(_tc(), _tool(RiskLevel.HIGH)))

    def test_plan_rejects_medium_high(self):
        # 必须传真实 broker：broker=None 会短路为全部批准（headless 安全默认）
        gw = IpcConfirmationGateway(broker=ConfirmBroker(), mode=Mode.PLAN)
        self.assertFalse(gw.ask(_tc(), _tool(RiskLevel.MEDIUM)))
        self.assertFalse(gw.ask(_tc(), _tool(RiskLevel.HIGH)))

    def test_plan_allows_low(self):
        gw = IpcConfirmationGateway(broker=None, mode=Mode.PLAN)
        self.assertTrue(gw.ask(_tc(), _tool(RiskLevel.LOW)))

    def test_remember_allow_overrides_popup(self):
        broker = ConfirmBroker()
        gw = IpcConfirmationGateway(broker=broker, mode=Mode.NORMAL)
        gw.remember_decision("Edit", True)
        # 即便有 broker（尚未取用），命中 remembered 直接采信 True
        self.assertTrue(gw.ask(_tc(), _tool(RiskLevel.MEDIUM)))

    def test_remember_deny_overrides(self):
        broker = ConfirmBroker()
        gw = IpcConfirmationGateway(broker=broker, mode=Mode.NORMAL)
        gw.remember_decision("Edit", False)
        self.assertFalse(gw.ask(_tc(), _tool(RiskLevel.MEDIUM)))

    def test_set_mode_to_plan_blocks_remembered(self):
        broker = ConfirmBroker()
        gw = IpcConfirmationGateway(broker=broker, mode=Mode.NORMAL)
        gw.remember_decision("Edit", True)
        gw.set_mode(Mode.PLAN)
        # 切到 PLAN 后，写类静态拒绝，无视 remember 的允许
        self.assertFalse(gw.ask(_tc(), _tool(RiskLevel.MEDIUM)))


class TestBrokerResolveFlow(unittest.TestCase):
    def test_wait_blocks_until_resolve(self):
        broker = ConfirmBroker(timeout=5.0)
        gw = IpcConfirmationGateway(broker=broker, mode=Mode.NORMAL)

        resolved = {}

        def resolve_later():
            time.sleep(0.2)
            pending = broker.next_pending()
            self.assertIsNotNone(pending)
            broker.resolve(pending["id"], True)

        t = threading.Thread(target=resolve_later)
        t.start()
        decision = gw.ask(_tc(), _tool(RiskLevel.HIGH))
        t.join()
        self.assertTrue(decision)

    def test_timeout_denies(self):
        # 无人裁决 → 超时返回 False（安全默认，绝不静默放行高危）
        broker = ConfirmBroker(timeout=0.3)
        gw = IpcConfirmationGateway(broker=broker, mode=Mode.NORMAL,
                                    timeout=0.3)
        decision = gw.ask(_tc(), _tool(RiskLevel.HIGH))
        self.assertFalse(decision)


if __name__ == "__main__":
    unittest.main()
