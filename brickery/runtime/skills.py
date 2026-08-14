"""§3 技能注册与筛选（clean room，纯自研）。

与工具类似，但技能是「更高层的组合能力包」（一组预设行为 / 提示 / 流程）。
按上下文匹配应触发的技能，命中后注入主循环。
红线：技能内容不得包含外部推理服务回退逻辑；技能加载不得执行未声明的副作用。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from .textutil import tokenize


@dataclass
class Skill:
    name: str
    trigger: List[str]          # 触发关键词
    content: str = ""           # 注入主循环的完整上下文提示（A+B：按需展开）
    disabled: bool = False
    # —— 以下为 marketplace / 分级注入扩展字段，全部可选，向后兼容旧 skills.json ——
    summary: str = ""           # 一句话描述：UI 展示 + A+B 轻量注入（替代全量 content）
    version: str = ""           # 语义化版本，如 "1.0.0"
    author: str = ""            # 作者 / 来源署名
    description: str = ""       # 较长介绍（UI 详情用）
    category: str = ""          # 分类（UI 分组/标签）
    tags: List[str] = field(default_factory=list)
    license: str = ""           # 许可证标识，如 "MIT"
    source: str = ""            # 来源：""=本地手写；否则为库 id 或源 URL（provenance）
    installed_at: str = ""      # 安装时间戳（ISO），仅 marketplace 安装写入
    provides_tool: str = ""     # 声明携带的内置工具名（见《DocWrite 规格》§4）；
                                # 非空时从 ToolProviderRegistry 取 handler 注册为工具。
                                # 空 = 纯提示技能（现状不变）。
    # —— 二进制扩展（市场高配技能，见 MARKETPLACE_BINARY_EXT.md）——
    # 技能可声明需下载的引擎二进制（如 editor_sdk），安装时落盘到
    # SHADELING_HOME/bin/<source>/，运行时按需启动。纯提示技能这些为空。
    binary_url: str = ""        # 二进制下载地址（http/https/file）
    binary_size: int = 0        # 字节；0=未声明
    binary_sha256: str = ""     # 可选校验和（策展源应提供）
    binary_launch: dict = field(default_factory=dict)  # 启动配置：
                                # {command, args:[], port, health_check, startup_timeout}

    # —— P0 brick 契约新增字段（缺省安全，向后兼容旧 skills.json）——
    capabilities: List[str] = field(default_factory=list)   # 能力标签（机器匹配）
    dependencies: List[dict] = field(default_factory=list)  # 依赖声明（依赖体检）
    resources: dict = field(default_factory=dict)           # 资源需求（冲突检测）
    risk_level: str = "low"                                  # 风险分级
    composition: dict = field(default_factory=dict)          # 组合规则 + 记忆域归属

class SkillRegistry:
    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        self._skills[skill.name] = skill

    def register_many(self, skills: List[Skill]) -> None:
        for s in skills:
            self.register(s)

    def all(self) -> List[Skill]:
        return list(self._skills.values())

    def match(self, context: str) -> List[Skill]:
        """按语境命中技能。disabled 不命中；仅做匹配，不执行任何副作用。"""
        if not self._skills:
            return []
        ctx = tokenize(context)
        hits: List[Skill] = []
        for sk in self._skills.values():
            if sk.disabled:
                continue
            if not sk.trigger:
                continue
            tokens = set()
            for t in sk.trigger:
                tokens |= tokenize(t)
            if ctx & tokens:
                hits.append(sk)
        return hits

    def get(self, name: str) -> Skill | None:
        return self._skills.get(name)

    def set_disabled(self, name: str, disabled: bool) -> Skill | None:
        """启用 / 停用一个技能。停用后 match() 不再命中。"""
        sk = self._skills.get(name)
        if sk is None:
            return None
        sk.disabled = bool(disabled)
        return sk

    def save(self, path: Path) -> None:
        """技能清单落盘 SHADELING_HOME/skills.json（用户可管理）。

        红线：source=="builtin" 的内置技能绝不写入用户文件——它们随包分发、
        每次启动从包内只读载入，避免升级 app 后内置技能被旧用户文件覆盖，也避免污染用户清单。
        """
        data = [
            {
                "name": s.name,
                "trigger": s.trigger,
                "content": s.content,
                "disabled": s.disabled,
                # marketplace / 分级注入扩展字段（缺失则省略，保持文件整洁）
                **({"summary": s.summary} if s.summary else {}),
                **({"version": s.version} if s.version else {}),
                **({"author": s.author} if s.author else {}),
                **({"description": s.description} if s.description else {}),
                **({"category": s.category} if s.category else {}),
                **({"tags": s.tags} if s.tags else {}),
                **({"license": s.license} if s.license else {}),
                **({"source": s.source} if s.source else {}),
                **({"installed_at": s.installed_at} if s.installed_at else {}),
                **({"provides_tool": s.provides_tool} if s.provides_tool else {}),
                **({"binary_url": s.binary_url} if s.binary_url else {}),
                **({"binary_size": s.binary_size} if s.binary_size else {}),
                **({"binary_sha256": s.binary_sha256} if s.binary_sha256 else {}),
                **({"binary_launch": s.binary_launch} if s.binary_launch else {}),
                **({"capabilities": s.capabilities} if s.capabilities else {}),
                **({"dependencies": s.dependencies} if s.dependencies else {}),
                **({"resources": s.resources} if s.resources else {}),
                **({"risk_level": s.risk_level} if s.risk_level != "low" else {}),
                **({"composition": s.composition} if s.composition else {}),
            }
            for s in self._skills.values()
            if s.source != "builtin"
        ]
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, path: Path) -> int:
        """从 skills.json 载入（与 save 配对）。

        红线：文件缺失 / 损坏一律安全回退为「不载入」，不崩溃、不联网、不预置样本。
        返回成功载入的条数。扩展字段缺失时按默认处理（向后兼容）。
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
            trigger = item.get("trigger") or []
            if not isinstance(trigger, list):
                trigger = []
            tags = item.get("tags") or []
            if not isinstance(tags, list):
                tags = []
            caps = item.get("capabilities") or []
            if not isinstance(caps, list):
                caps = []
            deps = item.get("dependencies") or []
            if not isinstance(deps, list):
                deps = []
            res = item.get("resources") or {}
            if not isinstance(res, dict):
                res = {}
            comp = item.get("composition") or {}
            if not isinstance(comp, dict):
                comp = {}
            self.register(Skill(
                name=str(item["name"]),
                trigger=[str(t) for t in trigger],
                content=str(item.get("content", "")),
                disabled=bool(item.get("disabled", False)),
                summary=str(item.get("summary", "")),
                version=str(item.get("version", "")),
                author=str(item.get("author", "")),
                description=str(item.get("description", "")),
                category=str(item.get("category", "")),
                tags=[str(t) for t in tags],
                license=str(item.get("license", "")),
                source=str(item.get("source", "")),
                installed_at=str(item.get("installed_at", "")),
                provides_tool=str(item.get("provides_tool", "")),
                binary_url=str(item.get("binary_url", "")),
                binary_size=int(item.get("binary_size", 0) or 0),
                binary_sha256=str(item.get("binary_sha256", "")),
                binary_launch=item.get("binary_launch") or {},
                capabilities=[str(c) for c in caps],
                dependencies=deps,
                resources=res,
                risk_level=str(item.get("risk_level", "low") or "low"),
                composition=comp,
            ))
            n += 1
        return n
