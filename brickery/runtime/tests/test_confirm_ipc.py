"""§3.4 确认弹窗 IPC 测试（clean room）。

直接测 ConfirmBroker（创建/长轮询/裁决/超时）与 IpcConfirmationGateway 的阻塞往返，
并经 IpcServer 的 _h_confirm_next / _h_confirm_resolve 验证 IPC 分发映射。
不依赖 Swift / GGUF / 网络。
"""
from __future__ import annotations

import threading
import time
import unittest

from .base import RuntimeTestCase
from brickery.runtime.confirm import ConfirmBroker, IpcConfirmationGateway
from brickery.runtime.ipc import IpcServer


class TestConfirmBroker(unittest.TestCase):
    def test_create_then_next_pending(self):
        b = ConfirmBroker()
        cid, ev = b.create("Bash", {"command": "ls"})
        item = b.next_pending(wait_timeout=0)
        self.assertIsNotNone(item)
        self.assertEqual(item["id"], cid)
        self.assertEqual(item["tool_name"], "Bash")
        self.assertEqual(item["args"], {"command": "ls"})

    def test_next_pending_timeout_returns_none(self):
        b = ConfirmBroker()
        self.assertIsNone(b.next_pending(wait_timeout=0.1))

    def test_next_pending_all_returned_blocks_until_timeout(self):
        """根治：pending 非空但全 returned 时不得立即返回（否则前端无 sleep 忙循环烧端口）。

        回归场景：一个待确认项被取走（returned=True）但前端裁决超时未 resolve，
        悬挂在 pending 里。此时空循环进来，必须在超时后才返回 None，而不是立即返回。
        """
        import time
        b = ConfirmBroker()
        b.create("Bash", {"command": "ls"})
        self.assertIsNotNone(b.next_pending(wait_timeout=0))  # 取走，returned=True
        # 全 returned，再取必须阻塞到超时而非立即返回
        t0 = time.monotonic()
        self.assertIsNone(b.next_pending(wait_timeout=0.15))
        self.assertGreaterEqual(time.monotonic() - t0, 0.12)  # 确实阻塞了

    def test_next_pending_blocks_then_new_item_wakes(self):
        """阻塞期间新请求到来应立即返回（Condition 需 notify 而非 busy-wait）。"""
        import threading
        import time
        b = ConfirmBroker()
        b.create("Bash", {})
        b.next_pending(wait_timeout=0)  # 取走，全 returned
        result: dict = {}

        def poller():
            result["v"] = b.next_pending(wait_timeout=2.0)

        t = threading.Thread(target=poller)
        t.start()
        time.sleep(0.05)  # 确保已进入阻塞
        b.create("Write", {"path": "y"})  # 新请求，应唤醒 poller
        t.join(timeout=3)
        self.assertIsNotNone(result.get("v"))
        self.assertEqual(result["v"]["tool_name"], "Write")

    def test_resolve_approve(self):
        b = ConfirmBroker(timeout=5)
        cid, _ = b.create("Write", {"path": "x"})
        # 真实用法：loop 先 wait 阻塞，Swift 侧再 resolve 唤醒
        result: dict = {}
        def waiter():
            result["v"] = b.wait(cid, timeout=2)
        t = threading.Thread(target=waiter)
        t.start()
        time.sleep(0.05)  # 确保 waiter 已进入阻塞
        self.assertTrue(b.resolve(cid, True))
        t.join(timeout=3)
        self.assertTrue(result.get("v"))

    def test_resolve_deny_then_gone(self):
        b = ConfirmBroker(timeout=5)
        cid, _ = b.create("Bash", {})
        self.assertTrue(b.resolve(cid, False))
        self.assertFalse(b.resolve(cid, False))  # 已移除，二次命中失败
        self.assertFalse(b.wait(cid, timeout=1))

    def test_wait_timeout_denies(self):
        b = ConfirmBroker(timeout=0.2)
        cid, _ = b.create("Bash", {})
        # 无人裁决 -> 超时 -> 拒绝（安全默认：绝不静默放行高危）
        self.assertFalse(b.wait(cid, timeout=0.2))


class _ToolCall:
    def __init__(self, name, arguments):
        self.name = name
        self.arguments = arguments


class _Tool:
    def __init__(self, name):
        self.name = name


class TestIpcConfirmationGateway(unittest.TestCase):
    def test_ask_approve_via_resolver_thread(self):
        b = ConfirmBroker(timeout=5)
        gw = IpcConfirmationGateway(b)

        def resolver():
            it = b.next_pending(wait_timeout=2)
            if it:
                b.resolve(it["id"], True)

        t = threading.Thread(target=resolver)
        t.start()
        self.assertTrue(gw.ask(_ToolCall("Bash", {"command": "ls"}), _Tool("Bash")))
        t.join(timeout=3)

    def test_ask_deny_via_resolver_thread(self):
        b = ConfirmBroker(timeout=5)
        gw = IpcConfirmationGateway(b)

        def resolver():
            it = b.next_pending(wait_timeout=2)
            if it:
                b.resolve(it["id"], False)

        t = threading.Thread(target=resolver)
        t.start()
        self.assertFalse(gw.ask(_ToolCall("Write", {"path": "x"}), _Tool("Write")))
        t.join(timeout=3)

    def test_gateway_no_broker_auto_approves(self):
        gw = IpcConfirmationGateway(None)
        self.assertTrue(gw.ask(_ToolCall("Bash", {}), _Tool("Bash")))


class TestConfirmServerDispatch(RuntimeTestCase):
    """经 IpcServer 的确认 handler 验证 IPC 分发映射（不跑引擎）。"""

    def setUp(self):
        super().setUp()
        self.srv = IpcServer(host="127.0.0.1", port=0,
                             home=self.home, models_root=self.models,
                             build_real_engines=False)
        self.srv.start()
        for _ in range(50):
            if self.srv.port:
                break
            time.sleep(0.02)

    def tearDown(self):
        self.srv.stop()
        super().tearDown()

    def test_dispatch_roundtrip(self):
        cid, _ = self.srv._confirm_broker.create("Write", {"path": "a"})
        nxt = self.srv._h_confirm_next({"wait": 0})
        self.assertEqual(nxt["confirmation"]["id"], cid)
        self.assertEqual(nxt["confirmation"]["tool_name"], "Write")
        res = self.srv._h_confirm_resolve({"id": cid, "decision": True})
        self.assertTrue(res["ok"])
        # 已裁决后再次取应无 pending
        self.assertIsNone(self.srv._h_confirm_next({"wait": 0})["confirmation"])

    def test_resolve_missing_id_errors(self):
        with self.assertRaises(Exception):
            self.srv._h_confirm_resolve({"id": "nope", "decision": True})


if __name__ == "__main__":
    unittest.main()
