"""§2 工具注册与筛选（clean room，纯自研）。

运行时持有一组**可选**工具，主循环按当前上下文筛选「相关子集」提供给引擎，
而不是把所有工具一股脑塞进去。
红线：工具筛选不得发起网络请求；不得加载目录外的任意可执行文件。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, List, Optional

from .textutil import tokenize


class RiskLevel(Enum):
    """工具风险分级（CHARTER §4.4 / CAPABILITY_PLAN §3.4 Claude Code 式）。

    - LOW：直接执行（如读类）。
    - MEDIUM：需用户确认（如改/写文件）。
    - HIGH：需用户确认 + 更高警觉（如执行命令）。
    """
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class Tool:
    name: str
    description: str
    keywords: List[str] = field(default_factory=list)
    handler: Optional[Callable] = None
    always_available: bool = False
    disabled: bool = False       # 与 Skill.disabled 对称：停用后不参与筛选
    # 工具参数 JSON Schema（OpenAI function-calling 格式）。
    # 默认空 object = 无参数。阶段 C 的真实工具（Read/Write/Bash）在此声明参数。
    parameters: dict = field(default_factory=dict)
    # 风险分级：MEDIUM/HIGH 工具在执行前需经确认网关（ConfirmationGateway）。
    risk: RiskLevel = RiskLevel.LOW


def tool_to_schema(tool: "Tool") -> dict:
    """把 Tool 转成 OpenAI tools 格式，喂给引擎的 tool_use 接口。"""
    params = tool.parameters
    if not isinstance(params, dict) or not params:
        params = {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": params,
        },
    }


class PermissionPolicy:
    """执行层权限闸门（§4.4 Claude Code 式权限模型的前置抽象）。

    主循环在执行每个工具调用前调用 authorize()。返回 False 即拒绝执行，
    循环继续（模型会收到「被拒绝」反馈，可调整策略）。
    默认 AllowAllPolicy 用于 headless / 测试；UI 确认弹窗在阶段 E 接入此接口。
    """

    def authorize(self, tool_call: "object", tool: Optional["Tool"]) -> bool:
        raise NotImplementedError


class AllowAllPolicy(PermissionPolicy):
    """默认策略：全部放行（headless / 测试 / 用户已在工作模式全局授权）。"""

    def authorize(self, tool_call: "object", tool: Optional["Tool"]) -> bool:
        return True


class DenyListPolicy(PermissionPolicy):
    """拒绝名单：拒绝指定工具名（其余放行）。用于测试与「禁用某些工具」场景。"""

    def __init__(self, denied: Optional[List[str]] = None):
        self.denied = set(denied or [])

    def authorize(self, tool_call: "object", tool: Optional["Tool"]) -> bool:
        name = getattr(tool_call, "name", None) or (tool.name if tool else None)
        return name not in self.denied


import fnmatch


def _call_arg_text(tool_call: "object", tool: Optional["Tool"]) -> str:
    """从一次工具调用里抽取「用于 scope 匹配的参数字符串」。

    - Bash：取 command 参数（shell 命令）。
    - Read/Edit/Write：取 path 参数（文件路径）。
    - 其余：拼接所有参数值的字符串，并对 ``key:glob`` 形式的规则提供单参数匹配。
    """
    args = getattr(tool_call, "arguments", None) or {}
    if not isinstance(args, dict):
        args = {}
    tname = (tool.name if tool else None) or getattr(tool_call, "name", "") or ""
    if tname == "Bash" and "command" in args:
        return str(args["command"])
    if tname in ("Read", "Edit", "Write") and "path" in args:
        return str(args["path"])
    if "url" in args:
        return str(args["url"])
    # 通用：拼接全部参数值
    return " ".join(str(v) for v in args.values())


def _parse_scope_rule(rule: str) -> Optional[tuple]:
    """解析 ``Tool(glob)`` 形式的范围规则。

    返回 ``(tool_filter, pattern)``；tool_filter 为 ``*`` 表示不限工具。
    ``pattern`` 用于 fnmatch 匹配调用参数字符串。非法格式返回 None。
    """
    rule = (rule or "").strip()
    m = __import__("re").match(r"^([A-Za-z0-9_\*]+)\((.*)\)$", rule)
    if not m:
        return None
    tool_filter = m.group(1)
    pattern = m.group(2).strip()
    if not pattern:
        return None
    return tool_filter, pattern


class ScopePolicy(PermissionPolicy):
    """Claude Code 式 scope 化权限（§4.4 / CAPABILITY_PLAN §11.2）。

    规则形如 ``ToolName(glob)``：
    - ``Bash(git *)``         允许 Bash 且命令以 ``git `` 开头
    - ``Read(/Users/suipu/Desktop/**)``  允许读取该目录
    - ``*`` / ``Bash(*)``     允许一切 / 允许一切 Bash
    - ``WebFetch(domain:github.com)``  （通用匹配：拼接参数含该串）

    语义：
    - **deny 优先**：任一 deny 规则命中 → 直接拒绝（覆盖 allow）。
    - **allow 设定后默认拒绝**：若配置了任意 allow 规则，则只有命中的调用放行，
      其余一律拒绝（最小权限，默认关）；未配 allow 则退化为「放行」（交给风险/确认层）。
    - 仅做字符串匹配，**不执行任何 handler、不触网**。
    """

    def __init__(self, allow: Optional[List[str]] = None,
                 deny: Optional[List[str]] = None):
        self.allow = [r for r in (allow or []) if _parse_scope_rule(r)]
        self.deny = [r for r in (deny or []) if _parse_scope_rule(r)]

    def _match(self, rules, tool_name: str, arg_text: str) -> bool:
        for r in rules:
            tool_filter, pattern = _parse_scope_rule(r)
            if tool_filter != "*" and tool_filter != tool_name:
                continue
            if fnmatch.fnmatch(arg_text, pattern) or fnmatch.fnmatch(
                    arg_text, pattern.replace("**/", "*/")):
                return True
        return False

    def authorize(self, tool_call: "object", tool: Optional["Tool"]) -> bool:
        name = (tool.name if tool else None) or getattr(tool_call, "name", "") or ""
        arg_text = _call_arg_text(tool_call, tool)
        # deny 优先
        if self._match(self.deny, name, arg_text):
            return False
        # allow 设定后默认拒绝
        if self.allow:
            return self._match(self.allow, name, arg_text)
        return True


class Mode(Enum):
    """执行模式（CHARTER §4.4 / §5 模式切换）。

    - PLAN（日常记忆模式）：只读思考，绝不碰文件 / 跑命令。
    - ACCEPT_EDITS（可信会话）：MEDIUM 写操作自动批准，HIGH 命令仍逐次确认。
    - NORMAL（工作模式）：MEDIUM/HIGH 均逐次确认（现状）。
    """
    PLAN = "plan"
    ACCEPT_EDITS = "accept_edits"
    NORMAL = "normal"

    @classmethod
    def from_str(cls, s: str) -> "Mode":
        try:
            return cls(s) if not isinstance(s, Mode) else s
        except ValueError:
            return cls.NORMAL


class PlanPermissionPolicy(PermissionPolicy):
    """Plan 模式静态闸门：只允许 LOW 风险（读类），MEDIUM/HIGH 一律拒绝。

    与 §4.4「Plan 模式（只读思考，不碰文件/不跑命令）」对齐——写/执行类调用
    在静态层即被否决，根本不会进入确认网关。
    """

    def authorize(self, tool_call: "object", tool: Optional["Tool"]) -> bool:
        risk = getattr(tool, "risk", None)
        if risk in (RiskLevel.MEDIUM, RiskLevel.HIGH):
            return False
        return True


class ConfirmationGateway:
    """交互确认网关（§4.4 / CAPABILITY_PLAN §3.4）。

    静态层 PermissionPolicy 放行后，对 MEDIUM/HIGH 风险工具，主循环阻塞调用 ask()
    等用户/UI 裁决（返回 True=批准执行，False=拒绝）。LOW 风险工具跳过此网关。
    Swift 真弹窗经 IPC 实现此接口；headless/测试用下方默认实现。
    """

    def ask(self, tool_call: "object", tool: Optional["Tool"]) -> bool:
        raise NotImplementedError


class AcceptEditsGateway(ConfirmationGateway):
    """ACCEPT_EDITS 模式确认网关：MEDIUM 写操作自动批准；HIGH 仍走 base（默认批准）。

    headless 下 base=AutoApprove → HIGH 也自动（可信会话）；生产 UI 侧会注入带
    IpcConfirmationGateway 的 base，使 HIGH 仍弹确认。
    """

    def __init__(self, base: Optional[ConfirmationGateway] = None):
        self.base = base or AutoApproveGateway()

    def ask(self, tool_call: "object", tool: Optional["Tool"]) -> bool:
        risk = getattr(tool, "risk", None)
        if risk == RiskLevel.MEDIUM:
            return True
        return self.base.ask(tool_call, tool)



class AutoApproveGateway(ConfirmationGateway):
    """默认：全部批准（headless / 测试 / 用户已在工作模式全局授权）。"""

    def ask(self, tool_call: "object", tool: Optional["Tool"]) -> bool:
        return True


class AutoDenyGateway(ConfirmationGateway):
    """默认：全部拒绝（Plan 模式 / 纯只读思考）。"""

    def ask(self, tool_call: "object", tool: Optional["Tool"]) -> bool:
        return False


class CallbackGateway(ConfirmationGateway):
    """用注入函数裁决（测试桩 / 自定义逻辑）。fn(tool_call, tool) -> bool。"""

    def __init__(self, fn):
        self.fn = fn

    def ask(self, tool_call: "object", tool: Optional["Tool"]) -> bool:
        return bool(self.fn(tool_call, tool))


class ToolRegistry:
    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def register_many(self, tools: List[Tool]) -> None:
        for t in tools:
            self.register(t)

    def unregister(self, name: str) -> bool:
        """按名移除工具。返回是否真移除。技能卸载时清掉其携带工具用。"""
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def all(self) -> List[Tool]:
        return list(self._tools.values())

    def select(self, context: str) -> List[Tool]:
        """按上下文筛选相关工具子集（基于关键词 2-gram 重叠）。

        工具筛选不发起网络请求；不执行任何 handler（仅做筛选）。
        """
        if not self._tools:
            return []
        ctx = tokenize(context)
        selected: List[Tool] = []
        for tool in self._tools.values():
            if tool.disabled:
                continue
            if tool.always_available:
                selected.append(tool)
                continue
            if not tool.keywords:
                continue
            tool_tokens = set()
            for kw in tool.keywords:
                tool_tokens |= tokenize(kw)
            if ctx & tool_tokens:
                selected.append(tool)
        return selected

    def to_schema(self) -> List[dict]:
        """生成当前所有**可用**工具的 OpenAI tools schema（供引擎 tool_use）。"""
        return [tool_to_schema(t) for t in self._tools.values()
                if not t.disabled]

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def set_disabled(self, name: str, disabled: bool) -> Optional[Tool]:
        """启用 / 停用一个工具。停用后 select() 不再选中。"""
        t = self._tools.get(name)
        if t is None:
            return None
        t.disabled = bool(disabled)
        return t

    def save(self, path: Path) -> None:
        """工具定义落盘 BRICKERY_HOME/tools.json（用户可增删），不进记忆库。

        注意：handler 是运行期可调用对象，**不落盘**（落盘等于执行任意代码入口）。
        """
        data = [
            {"name": t.name, "description": t.description,
             "keywords": t.keywords, "always_available": t.always_available,
             "disabled": t.disabled,
             "parameters": t.parameters if isinstance(t.parameters, dict) else {},
             "risk": t.risk.value if hasattr(t.risk, "value") else str(t.risk)}
            for t in self._tools.values()
        ]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, path: Path) -> int:
        """从 tools.json 载入（与 save 配对）。

        红线：只恢复**声明性字段**，绝不从文件恢复 handler —— 配置解析不得执行任意代码。
        文件缺失 / 损坏一律安全回退为「不载入」。返回成功载入的条数。
        """
        p = Path(path)
        if not p.exists():
            return 0
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, ValueError):
            return 0
        if not isinstance(raw, list):
            return 0
        n = 0
        for item in raw:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            kws = item.get("keywords") or []
            if not isinstance(kws, list):
                kws = []
            params = item.get("parameters") or {}
            if not isinstance(params, dict):
                params = {}
            name = str(item["name"])
            existing = self._tools.get(name)
            # 风险分级落盘恢复（缺省按 LOW）
            rk = item.get("risk", "low")
            try:
                risk = RiskLevel(rk) if not isinstance(rk, RiskLevel) else rk
            except ValueError:
                risk = RiskLevel.LOW
            self.register(Tool(
                name=name,
                description=str(item.get("description", "")),
                keywords=[str(k) for k in kws],
                # 保留已在进程内注册的 handler 与 parameters，不从文件恢复
                handler=existing.handler if existing else None,
                parameters=existing.parameters if existing else params,
                always_available=bool(item.get("always_available", False)),
                disabled=bool(item.get("disabled", False)),
                risk=risk,
            ))
            n += 1
        return n
