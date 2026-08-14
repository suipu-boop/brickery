"""Vault 服务工具（vault_query）· P9 积木扩展。

Vault 是本地资产中枢（VaultStore，见 vault_store.py），存资产条目
（document / image / webpage / skill_snapshot / note），供 UI 命令栏 + AI
检索共用。本模块提供 AI 侧检索工具 vault_query，委托 VaultStore.query。

安全红线：query 只读、脱敏（不含敏感字段明文），不触发网络请求。
"""
from __future__ import annotations

from typing import Optional

from .tools import Tool
from .vault_store import VaultStore


def build_vault_query_tool(home: Optional[str] = None) -> Tool:
    """工厂：构造 VaultQuery 工具（委托 VaultStore.query，只读脱敏）。"""
    store = VaultStore()

    def handler(query: str = "", type: Optional[str] = None,
                top_k: int = 5, **_):
        q = (query or "").strip()
        if not q:
            return {"ok": False, "error": "query 必填（检索关键词）", "items": []}
        try:
            items = store.query(q, type=type, top_k=int(top_k))
            return {"ok": True, "items": items}
        except Exception as e:  # noqa: BLE001 —— 故障域隔离
            return {"ok": False, "error": f"{type(e).__name__}: {e}", "items": []}

    return Tool(
        name="VaultQuery",
        description="检索本地资产中枢（Vault）中的资产：文档/网页/笔记/图片/技能快照。"
                    "按关键词匹配标题与内容，返回脱敏条目（不含敏感字段明文）。",
        keywords=["vault", "资产", "文档", "网页", "笔记", "提醒", "收藏",
                  "资料", "检索资产", "查资产"],
        handler=handler,
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索关键词"},
                "type": {"type": "string",
                         "description": "资产类型过滤（document/image/webpage/"
                                        "skill_snapshot/note），可选"},
                "top_k": {"type": "integer", "description": "返回条数，默认 5"},
            },
            "required": ["query"],
        },
    )
