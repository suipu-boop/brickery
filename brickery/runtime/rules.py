"""§P2 持久规则（Hooks 轻量版）：项目级 rules.json / SHADERULES.md 启动时注入 system prompt。

等价于 Claude Code Hooks / Cursor Rules —— 用户写的「始终遵循的指令」，每次对话前
自动注入。纯本地文件读取，零依赖、零外连、不执行任意代码（仅读文本行）。
优先级：rules.json 的 rules 数组优先；SHADERULES.md 作为补充（非标题行逐条）。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import List


def load_rules(home) -> List[str]:
    home = Path(home)
    rules: List[str] = []
    # 1) rules.json：{"rules": ["...", "..."]}
    rj = home / "rules.json"
    if rj.exists():
        try:
            data = json.loads(rj.read_text(encoding="utf-8"))
            rs = data.get("rules") or []
            if isinstance(rs, list):
                for r in rs:
                    s = str(r).strip()
                    if s:
                        rules.append(s)
        except (json.JSONDecodeError, OSError, ValueError):
            pass
    # 2) SHADERULES.md：非标题行（去 -/* 前缀）逐条作为规则
    md = home / "SHADERULES.md"
    if md.exists():
        try:
            for line in md.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                s = s.lstrip("-*").strip()
                if s:
                    rules.append(s)
        except OSError:
            pass
    return rules
