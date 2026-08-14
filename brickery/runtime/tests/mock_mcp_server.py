"""测试用 mock MCP stdio 服务器（仅测试）。

读 stdin 一行一行的 JSON-RPC 2.0，按方法回写响应。用法：
    python3 mock_mcp_server.py
被 MCPClient 作为子进程拉起，验证 stdio JSON-RPC 链路。
"""
import json
import sys


def main():
    tools = [{
        "name": "hello",
        "description": "向某人问好",
        "inputSchema": {"type": "object",
                        "properties": {"name": {"type": "string"}},
                        "required": ["name"]},
    }]
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        method = msg.get("method")
        rid = msg.get("id")
        # 通知（无 id）不回
        if rid is None:
            continue
        if method == "initialize":
            resp = {"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "serverInfo": {"name": "mock", "version": "0.1"},
            }}
        elif method == "tools/list":
            resp = {"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}}
        elif method == "tools/call":
            args = (msg.get("params") or {}).get("arguments", {})
            name = (msg.get("params") or {}).get("name", "")
            text = f"hello {(args.get('name') or 'world')} (called {name})"
            resp = {"jsonrpc": "2.0", "id": rid, "result": {
                "content": [{"type": "text", "text": text}],
                "isError": False,
            }}
        else:
            resp = {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32601, "message": f"unknown {method}"}}
        sys.stdout.write(json.dumps(resp) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
