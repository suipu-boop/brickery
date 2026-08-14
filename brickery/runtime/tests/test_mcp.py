"""§4.4 / 阶段 MCP 单测：MCPClient（stdio JSON-RPC 链路）+ MCPManager（白名单/过滤）。

用 runtime/tests/mock_mcp_server.py 作为被测 stdio 服务器，验证：
- 握手 initialize → list_tools → call_tool 全链路；
- 远程 transport / disabled / allow-deny 过滤；
- 坏命令安全报错（不崩）。
"""
import sys
from pathlib import Path

from brickery.runtime.mcp import MCPClient, MCPError, MCPManager, load_mcp_servers
from .base import RuntimeTestCase

MOCK = Path(__file__).parent / "mock_mcp_server.py"


class TestMCPClient(RuntimeTestCase):
    def test_handshake_and_call(self):
        client = MCPClient([sys.executable, str(MOCK)], name="mock")
        try:
            init = client.initialize()
            self.assertIn("serverInfo", init)
            tools = client.list_tools()
            self.assertEqual(len(tools), 1)
            self.assertEqual(tools[0]["name"], "hello")
            out = client.call_tool("hello", {"name": "随朴"})
            self.assertIn("hello 随朴", out)
        finally:
            client.close()

    def test_bad_command_raises(self):
        with self.assertRaises(Exception):
            MCPClient(["/nonexistent/binary_xyz"])

    def test_close_idempotent(self):
        client = MCPClient([sys.executable, str(MOCK)], name="mock")
        client.close()
        client.close()  # 不抛


class TestMCPManager(RuntimeTestCase):
    def _cfg(self, **over):
        base = {
            "transport": "stdio",
            "command": [sys.executable, str(MOCK)],
            "enabled": True,
            "allow": ["*"],
        }
        base.update(over)
        return {"mock": base}

    def test_starts_local_stdio_and_merges_tools(self):
        mgr = MCPManager(self._cfg())
        try:
            mgr.start()
            tools = mgr.tools()
            self.assertEqual(len(tools), 1)
            self.assertEqual(tools[0].name, "mcp__mock__hello")
            self.assertIsNotNone(tools[0].handler)
            out = tools[0].handler(name="world")
            self.assertIn("hello world", out)
        finally:
            mgr.stop()

    def test_remote_transport_skipped(self):
        cfg = {"remote": {"transport": "http", "url": "https://x",
                          "enabled": True}}
        mgr = MCPManager(cfg)
        mgr.start()
        self.assertEqual(len(mgr.tools()), 0)
        self.assertTrue(any("远程" in e for e in mgr.errors))

    def test_disabled_skipped(self):
        mgr = MCPManager(self._cfg(enabled=False))
        mgr.start()
        self.assertEqual(len(mgr.tools()), 0)

    def test_deny_full_name_filter(self):
        cfg = self._cfg(deny=["mcp__mock__hello"])
        mgr = MCPManager(cfg)
        mgr.start()
        # deny 写真实 full name → 被过滤
        self.assertEqual(len(mgr.tools()), 0)

    def test_allow_whitelist_filters_unlisted(self):
        # allow 只列 other → hello 不在白名单 → 被过滤（最小权限）
        cfg = {"mock": {
            "transport": "stdio",
            "command": [sys.executable, str(MOCK)],
            "enabled": True,
            "allow": ["other_tool"],
        }}
        mgr = MCPManager(cfg)
        mgr.start()
        self.assertEqual(len(mgr.tools()), 0)

    def test_load_mcp_servers_missing_safe(self):
        self.assertEqual(load_mcp_servers(Path("/nonexistent/mcp.json")), {})


if __name__ == "__main__":
    __import__("unittest").main()
