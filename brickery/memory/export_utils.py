"""记忆导出序列化（clean room，纯自研）。

将 MemorySystem.export_all() 返回的结构化 bundle 序列化为：
- to_markdown：人类可读的 Markdown 文档
- to_json：机器可读的 JSON 字符串

不引入任何第三方依赖，仅标准库。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List


def to_json(bundle: Dict[str, Any]) -> str:
    return json.dumps(bundle, ensure_ascii=False, indent=2)


def _fmt_conf(conf: Any) -> str:
    if isinstance(conf, (int, float)):
        return f"（置信 {conf:.0%}）"
    return ""


def to_markdown(bundle: Dict[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# Brickery 记忆导出")
    lines.append("")
    lines.append(f"- 生成时间（UTC）：{bundle.get('generated_at', '未知')}")
    lines.append(f"- 格式版本：{bundle.get('schema', '未知')}")
    lines.append("")

    # 用户画像
    lines.append("## 用户画像")
    portrait = bundle.get("portrait") or []
    if not portrait:
        lines.append("_（空）_")
    for p in portrait:
        attr = p.get("attribute", "?")
        val = p.get("value", "")
        lines.append(f"- **{attr}**：{val} {_fmt_conf(p.get('confidence'))}")
    lines.append("")

    # 固定核
    core = bundle.get("core")
    lines.append("## 固定核")
    if not core:
        lines.append("_（未导出 / 空）_")
    else:
        for k, v in core.items():
            lines.append(f"- **{k}**：{v}")
    lines.append("")

    # 文件柜抽屉
    drawers = bundle.get("drawers") or []
    lines.append(f"## 文件柜抽屉（{len(drawers)}）")
    if not drawers:
        lines.append("_（空）_")
    for d in drawers:
        title = d.get("title") or d.get("drawer_id") or "?"
        lines.append(f"### 抽屉：{title}")
        nodes = d.get("nodes") or []
        edges = d.get("edges") or []
        rb = d.get("recordbook") or ""
        if nodes:
            lines.append("**节点：**")
            for n in nodes:
                nid = n.get("node_id") or n.get("id") or "?"
                label = n.get("label", "")
                ntype = n.get("node_type") or n.get("type") or ""
                lines.append(f"- `{nid}` [{ntype}] {label}")
        if edges:
            lines.append("**关系：**")
            for e in edges:
                s = e.get("source") or "?"
                t = e.get("target") or "?"
                rel = e.get("relation", "")
                lines.append(f"- {s} --{rel}--> {t}")
        if rb:
            lines.append("")
            lines.append("**记录本：**")
            lines.append("")
            lines.append("```")
            lines.append(rb)
            lines.append("```")
        lines.append("")

    # 对话影
    convs = bundle.get("conversations") or []
    lines.append(f"## 对话影（{len(convs)} 个已沉淀会话）")
    if not convs:
        lines.append("_（空）_")
    for c in convs:
        sid = c.get("session_id", "?")
        text = c.get("text", "")
        lines.append(f"### 会话 {sid}")
        lines.append("")
        lines.append(text)
        lines.append("")

    return "\n".join(lines)
