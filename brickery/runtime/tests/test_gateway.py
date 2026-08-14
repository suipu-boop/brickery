"""§7 平台网关预留点单测：注册 / 未注册不影响主循环。"""
from brickery.runtime.gateway import Gateway, GatewayRegistry
from .base import RuntimeTestCase


class TestGateway(RuntimeTestCase):
    def test_register_and_get(self):
        class MyGW(Gateway):
            name = "my"

            def on_message(self, payload):
                return {"echo": payload}

        GatewayRegistry.clear()
        GatewayRegistry.register(MyGW())
        self.assertIsNotNone(GatewayRegistry.get("my"))
        self.assertEqual(len(GatewayRegistry.all()), 1)
        GatewayRegistry.clear()
        self.assertEqual(GatewayRegistry.all(), [])

    def test_unregistered_no_effect(self):
        GatewayRegistry.clear()
        self.assertEqual(GatewayRegistry.all(), [])
