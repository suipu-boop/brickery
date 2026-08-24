"""积木组装器（Assembler）· 第一阶段：静态组装。

按 `specs/brickery.md` 契约，把选定的积木 + 心脏拼装成可运行 agent。
本模块只做**静态组装**：依赖解析 / 冲突检查 / 资源检查，产出可验证的组装方案；
动态激活（activate / invoke）由 BrickLike 适配器承接。

零内核依赖（仅标准库），可独立测试。

来源：从 Shadeling `runtime/assembler.py` 迁移（2026-08-14，Brickery 抽离）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class AssemblyError(ValueError):
    """组装失败（依赖缺失 / 冲突 / 资源超限）。"""


@dataclass
class Brick:
    """积木的静态视图：组装字段 + 展示字段（供 Web 工作台等前端使用）。

    组装字段：name / version / risk_level / requires / conflicts / resources。
    展示字段：summary / description / category / tags / capabilities / dependencies，
    仅透传 brick.json 原始信息，不参与组装逻辑（纯增量）。
    """

    name: str
    version: str
    risk_level: str
    requires: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    resources: dict = field(default_factory=dict)
    # ---- 自包含实现文件（files 落盘清单，src=积木目录内相对路径，dest=相对 home） ----
    files: List[dict] = field(default_factory=list)
    # ---- 展示字段（不参与组装） ----
    summary: str = ""
    description: str = ""
    category: str = ""
    tags: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)
    dependencies: List[dict] = field(default_factory=list)

    @classmethod
    def from_manifest(cls, raw: dict) -> "Brick":
        comp = raw.get("composition") or {}
        return cls(
            name=str(raw.get("name") or "").strip(),
            version=str(raw.get("version") or "*"),
            risk_level=str(raw.get("risk_level") or "low"),
            requires=[str(r) for r in (comp.get("requires") or [])],
            conflicts=[str(c) for c in (comp.get("conflicts_with") or [])],
            resources=dict(raw.get("resources") or {}),
            files=list(raw.get("files") or []),
            summary=str(raw.get("summary") or "").strip(),
            description=str(raw.get("description") or "").strip(),
            category=str(raw.get("category") or "").strip(),
            tags=[str(t) for t in (raw.get("tags") or [])],
            capabilities=[str(c) for c in (raw.get("capabilities") or [])],
            dependencies=list(raw.get("dependencies") or []),
        )


@dataclass
class AssemblyPlan:
    """一次组装方案：拓扑序安装清单 + 检查结论。"""

    order: List[str]
    resources_total: dict = field(default_factory=dict)
    # name -> files 落盘清单（自包含实现文件，供 produce / 运行时落盘）
    files: Dict[str, List[dict]] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "order": self.order,
            "resources_total": self.resources_total,
            "files": self.files,
        }


class Assembler:
    """静态组装器：读积木清单，校验依赖 / 冲突 / 资源，产出方案。"""

    def __init__(self, bricks: Dict[str, Brick],
                 *, memory_mb_limit: int = 0,
                 disk_mb_limit: int = 0,
                 allow_network: bool = True):
        self.bricks = bricks
        self.memory_mb_limit = memory_mb_limit
        self.disk_mb_limit = disk_mb_limit
        self.allow_network = allow_network

    # ---- 入口：组装 ----
    def assemble(self, selected: List[str]) -> AssemblyPlan:
        """把 selected 及其传递依赖展开成拓扑序方案，校验通过后返回。

        允许空 selected（纯底座方案）：内置积木开箱即用、不占组装，零选择也能产出。
        """
        selected = self._dedupe(selected)
        order = self._resolve(selected)
        self._check_conflicts(order)
        total = self._check_resources(order)
        files = {n: list(self.bricks[n].files) for n in order if self.bricks[n].files}
        return AssemblyPlan(order=order, resources_total=total, files=files)

    # ---- 依赖解析（含传递依赖） ----
    def _resolve(self, selected: List[str]) -> List[str]:
        """把 selected 展开成「依赖先行」的拓扑序。缺依赖 / 有环即抛错。"""
        known = set(self.bricks)
        # 收集 selected 的传递依赖闭包
        needed: List[str] = []
        visiting: List[str] = []
        visited: set = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            if name in visiting:
                raise AssemblyError(f"依赖成环：{' -> '.join(visiting + [name])}")
            if name not in known:
                raise AssemblyError(f"依赖缺失：积木 {name} 未在清单中")
            visiting.append(name)
            for dep in self.bricks[name].requires:
                visit(dep)
            visiting.pop()
            visited.add(name)
            needed.append(name)

        for s in selected:
            visit(s)
        return needed  # DFS 后序 = 依赖先行

    # ---- 冲突检查 ----
    def _check_conflicts(self, order: List[str]) -> None:
        chosen = set(order)
        for name in order:
            for c in self.bricks[name].conflicts:
                if c == name:
                    raise AssemblyError(f"积木 {name} 声明与自身冲突")
                if c in chosen:
                    raise AssemblyError(f"积木冲突：{name} 与 {c} 不可同装")

    # ---- 资源检查 ----
    def _check_resources(self, order: List[str]) -> dict:
        mem = disk = 0
        ports_seen: set = set()
        for name in order:
            r = self.bricks[name].resources
            mem += int(r.get("memory_mb") or 0)
            disk += int(r.get("disk_mb") or 0)
            for p in (r.get("ports") or []):
                if p in ports_seen:
                    raise AssemblyError(f"端口冲突：{p} 被多个积木占用")
                ports_seen.add(int(p))
            if r.get("network") and not self.allow_network:
                raise AssemblyError(f"积木 {name} 需联网，但当前不允许联网")
        if self.memory_mb_limit and mem > self.memory_mb_limit:
            raise AssemblyError(
                f"内存超限：合计 {mem}MB > 上限 {self.memory_mb_limit}MB")
        if self.disk_mb_limit and disk > self.disk_mb_limit:
            raise AssemblyError(
                f"磁盘超限：合计 {disk}MB > 上限 {self.disk_mb_limit}MB")
        return {"memory_mb": mem, "disk_mb": disk, "ports": sorted(ports_seen)}

    # ---- 工具 ----
    @staticmethod
    def _dedupe(items: List[str]) -> List[str]:
        out: List[str] = []
        seen: set = set()
        for i in items:
            if i not in seen:
                seen.add(i)
                out.append(i)
        return out


def load_vault(vault_root: str, index_file: str = "index.json") -> Assembler:
    """从 brick-vault 仓库装载积木清单，构造 Assembler。

    index.json 里每项含 path（指向 bricks/<name>/），据此读 brick.json。
    """
    root = Path(vault_root)
    index_path = root / index_file
    if not index_path.exists():
        raise AssemblyError(f"清单不存在：{index_path}")
    raw_index = json.loads(index_path.read_text(encoding="utf-8"))
    bricks: Dict[str, Brick] = {}
    for entry in raw_index.get("bricks") or []:
        name = entry.get("name")
        manifest_path = root / (entry.get("path") or f"bricks/{name}/")
        manifest_path = manifest_path / "brick.json"
        if not manifest_path.exists():
            raise AssemblyError(f"积木清单缺失：{manifest_path}")
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        brick = Brick.from_manifest(raw)
        bricks[brick.name] = brick
    return Assembler(bricks)
