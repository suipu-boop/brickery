"""§4.4 / 阶段 MCP · 标准协议扩展面（首版提入，白名单本地优先）。

纯 stdlib 实现 MCP（Model Context Protocol）客户端：JSON-RPC 2.0 over stdio
（换行分隔的 JSON，与 MCP 官方 stdio transport 对齐）。零外部依赖。

设计红线（CHARTER §4.4 / E4）：
- **只接本地 stdio 服务器**（命令在白名单内、用户显式声明）。
- **远程 HTTP/SSE 默认关**：配置里 ``transport`` 非 ``stdio`` 的一律跳过并告警，
  绝不默认联网连第三方。
- **工具级 allow/deny**：每个服务器可声明 ``allow``/``deny`` 单工具名单，
  未列入白名单的工具不注入。
- 不自建 MCP 服务器给自己用；只消费市面既有服务器（filesystem / Puppeteer / GitHub…），
  直接复用海量工具，不必自研。
"""
from __future__ import annotations

import json
import subprocess
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from .tools import Tool, ToolRegistry, RiskLevel


class MCPError(RuntimeError):
    """MCP 协议层错误（初始化失败 / 调用报错 / 服务器退出）。"""


class MCPClient:
    """一个 MCP stdio 服务器的 JSON-RPC 客户端。

    生命周期：构造即启动子进程 → ``initialize()`` 握手 → ``list_tools()`` /
    ``call_tool()`` 使用 → ``close()`` 退出。线程安全（单次收发加锁）。
    """

    def __init__(self, command: List[str], timeout: float = 30.0,
                 name: str = "mcp"):
        if not command:
            raise MCPError("MCP 命令为空")
        self.name = name
        self.command = list(command)
        self.timeout = timeout
        self._lock = threading.Lock()
        self._req_id = 0
        self._proc: Optional[subprocess.Popen] = None
        try:
            self._proc = subprocess.Popen(
                self.command,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1,
                env=None,  # 继承当前环境（PATH 等）
            )
        except (FileNotFoundError, OSError) as e:
            raise MCPError(f"启动 MCP 服务器失败 {self.command}: {e}")

    # ----- 底层收发 -----
    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _send(self, method: str, params: Optional[dict],
              notify: bool = False) -> Any:
        if self._proc is None or self._proc.poll() is not None:
            raise MCPError("MCP 服务器已退出")
        rid = self._next_id() if not notify else None
        msg = {"jsonrpc": "2.0", "method": method}
        if not notify:
            msg["id"] = rid
        if params is not None:
            msg["params"] = params
        line = json.dumps(msg, ensure_ascii=False) + "\n"
        with self._lock:
            try:
                self._proc.stdin.write(line)
                self._proc.stdin.flush()
            except (BrokenPipeError, OSError) as e:
                raise MCPError(f"写入 MCP 服务器失败：{e}")
            if notify:
                return None
            return self._read_response(rid)

    def _read_response(self, rid: int) -> Any:
        assert self._proc is not None
        assert self._proc.stdout is not None
        deadline = __import__("time").time() + self.timeout
        while True:
            if self._proc.poll() is not None:
                raise MCPError("MCP 服务器在等待响应期间退出")
            if __import__("time").time() > deadline:
                raise MCPError(f"等待 MCP 响应超时（id={rid}）")
            raw = self._proc.stdout.readline()
            if not raw:
                raise MCPError("MCP 服务器关闭了 stdout")
            raw = raw.strip()
            if not raw:
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue  # 跳过非 JSON 行（部分服务器会打日志到 stdout）
            if msg.get("id") != rid:
                # 通知或别的请求的响应：跳过
                continue
            if "error" in msg:
                err = msg["error"]
                raise MCPError(f"MCP 错误：{err.get('message', err)}")
            return msg.get("result")

    # ----- 协议方法 -----
    def initialize(self) -> dict:
        result = self._send("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "Shadeling", "version": "0.1"},
        })
        # 通知服务器初始化完成
        self._send("notifications/initialized", {}, notify=True)
        return result or {}

    def list_tools(self) -> List[dict]:
        result = self._send("tools/list", {})
        if not result:
            return []
        return result.get("tools", []) or []

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._send("tools/call",
                            {"name": name, "arguments": arguments or {}})
        if not result:
            return ""
        # content 是 [{type, text}, ...]
        parts = []
        for item in result.get("content", []) or []:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        # 若 isError 标记，附上提示
        if result.get("isError"):
            parts.insert(0, "[工具返回错误] ")
        return "\n".join(parts)

    def close(self) -> None:
        if self._proc is None:
            return
        for f in (self._proc.stdin, self._proc.stdout, self._proc.stderr):
            try:
                if f is not None:
                    f.close()
            except OSError:
                pass
        try:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        self._proc = None


def _tool_risk(name: str) -> RiskLevel:
    """按工具名推测风险分级（保守默认 MEDIUM，写/执行类更高）。"""
    nl = name.lower()
    if any(k in nl for k in ("read", "search", "list", "get", "fetch")):
        return RiskLevel.LOW
    if any(k in nl for k in ("write", "edit", "create", "delete", "remove",
                             "update", "save")):
        return RiskLevel.MEDIUM
    return RiskLevel.MEDIUM  # 其余默认 MEDIUM（需确认），不轻易放行高危


class MCPManager:
    """MCP 服务器聚合器：启动白名单本地 stdio 服务器，把它们的工具并入侵 Shadeling。

    配置形态（mcp_servers.json）：::

        {
          "filesystem": {
            "transport": "stdio",
            "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/allowed/path"],
            "enabled": true,
            "allow": ["*"],            // 或具体工具名列表
            "deny": ["filesystem_delete"]
          },
          "remote_example": {
            "transport": "http",
            "url": "https://...",
            "enabled": false           // 远程默认关
          }
        }

    仅 ``transport == "stdio"`` 且 ``enabled`` 为真才启动；远程一律跳过并告警。
    """

    def __init__(self, servers_config: Optional[Dict[str, dict]] = None):
        self.servers_config = servers_config or {}
        self._clients: Dict[str, MCPClient] = {}
        self._tools: List[Tool] = []
        self.errors: List[str] = []

    def start(self) -> None:
        """启动所有白名单本地 stdio 服务器并拉取工具。失败安全降级（记录错误，不崩）。"""
        self._tools = []
        self.errors = []
        for sname, cfg in self.servers_config.items():
            cfg = cfg or {}
            transport = (cfg.get("transport") or "stdio").lower()
            if transport != "stdio":
                # 远程 HTTP/SSE 默认关（E4 红线）
                self.errors.append(
                    f"[MCP] 服务器 {sname} 使用远程传输 {transport}，按策略默认跳过"
                    f"（需显式 enabled + 本地 stdio 才接入）。")
                continue
            if not cfg.get("enabled", True):
                continue
            command = cfg.get("command") or []
            if not command:
                self.errors.append(f"[MCP] 服务器 {sname} 缺少 command，跳过。")
                continue
            try:
                client = MCPClient(command, name=sname)
                client.initialize()
                remote_tools = client.list_tools()
            except MCPError as e:
                self.errors.append(f"[MCP] 服务器 {sname} 启动/握手失败：{e}")
                continue
            # allow/deny 单工具白名单
            allow = cfg.get("allow")
            deny = set(cfg.get("deny") or [])
            allow_set = set(allow) if isinstance(allow, list) else None
            added = 0
            for t in remote_tools:
                tname = t.get("name")
                if not tname:
                    continue
                full = f"mcp__{sname}__{tname}"  # Claude Code 分层命名
                if full in deny:
                    continue
                if allow_set is not None and "*" not in allow_set \
                        and tname not in allow_set:
                    continue  # 白名单未列，跳过（最小权限）
                schema = t.get("inputSchema") or {"type": "object", "properties": {}}
                risk = _tool_risk(tname)
                self._tools.append(Tool(
                    name=full,
                    description=t.get("description", f"MCP 工具 {full}"),
                    keywords=[sname, tname, "mcp"],
                    parameters=schema if isinstance(schema, dict) else
                    {"type": "object", "properties": {}},
                    risk=risk,
                    handler=self._make_handler(client, tname),
                ))
                added += 1
            if added == 0:
                # 没有工具通过白名单：及时关掉子进程，避免泄漏
                try:
                    client.close()
                except Exception:
                    pass
            else:
                self._clients[sname] = client

    @staticmethod
    def _make_handler(client: MCPClient, tool_name: str):
        def handler(**kwargs):
            return client.call_tool(tool_name, kwargs)
        return handler

    def tools(self) -> List[Tool]:
        return list(self._tools)

    def stop(self) -> None:
        for c in self._clients.values():
            try:
                c.close()
            except Exception:
                pass
        self._clients.clear()

    def to_registry(self) -> ToolRegistry:
        reg = ToolRegistry()
        reg.register_many(self._tools)
        return reg


def load_mcp_servers(path: Path) -> Dict[str, dict]:
    """从 mcp_servers.json 读取服务器配置（损坏/缺失则安全返回空 dict）。"""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        import json as _json
        raw = _json.loads(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except (json.JSONDecodeError, OSError, ValueError):
        return {}
