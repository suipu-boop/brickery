"""§3.x 在线技能市场客户端（clean room，纯自研，零第三方依赖）。

职责：从「在线技能源（repo）」拉取目录索引、按需下载技能包、做安全校验、
写入本地 skills.json 并打上 provenance（来源 / 版本 / 安装时间），支持卸载与升级。

设计要点：
- 网络全在 Python 侧（与 WebFetch / API 一致），不依赖 Swift 的网络授权。
- 仅用标准库 urllib，支持 file://（本地 fixture / 离线测试）与 http(s)://。
- 优雅失败：索引不可达 / 损坏 / 超时 → 返回错误字典，绝不崩溃、绝不静默连外网。
- 安全校验：强制 name/trigger/content 字段；content 长度上限；禁止任何路径穿越
  （包内若含文件清单，文件名不得含 `..` 或绝对路径）。技能 content 会注入系统提示，
  属 prompt-injection 攻击面，故默认仅连策展源、且安装前由 UI 展示内容供审阅。

红线：本模块不得执行技能 content 中的任意代码；下载仅做读取与落盘。
"""
from __future__ import annotations

import json
import time
import urllib.request
import urllib.error
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
from urllib.parse import urljoin, urlparse

from .skills import Skill

# 索引 schema 标识（未来破坏性变更时升级）
REPO_SCHEMA = "shadeling-skill-repo/v1"
# 单技能 content 长度上限（字符），防单个技能灌爆上下文 / 防恶意超长包
SKILL_CONTENT_MAX = 50_000
# 索引缓存有效期（秒）：不可达时回退到上次成功缓存，过期则报不可达
INDEX_CACHE_TTL = 6 * 3600
# 网络超时（秒）
DEFAULT_TIMEOUT = 15

# P0 brick 契约：受控词表
RISK_LEVELS = {"low", "medium", "high", "critical"}
DEPENDENCY_TYPES = {"skill", "binary", "python"}
MEMORY_SCOPES = {"session", "project", "user", "workspace", "longterm"}


def _http_get(url: str, timeout: int = DEFAULT_TIMEOUT) -> Tuple[Optional[bytes], Optional[str]]:
    """读取 URL（file:// 或 http(s)://），返回 (内容, 错误)。错误为 None 表示成功。"""
    try:
        if urlparse(url).scheme == "file":
            # file:// 直接读本地，绕过网络栈，便于离线 fixture 测试
            raw = Path(urlparse(url).path).read_bytes()
            return raw, None
        req = urllib.request.Request(url, headers={"User-Agent": "Brickery-SkillLib/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read(), None
    except urllib.error.URLError as e:
        return None, f"网络错误：{e.reason if hasattr(e, 'reason') else e}"
    except (OSError, ValueError) as e:
        return None, f"读取失败：{e}"


def _split_version(v: str) -> Tuple[int, ...]:
    """把 '1.2.3' 解析成 (1,2,3)；非数字段记 0。用于版本比较。"""
    out = []
    for part in (v or "").split("."):
        try:
            out.append(int(part))
        except ValueError:
            out.append(0)
    return tuple(out)


def split_version(v: str) -> Tuple[int, ...]:
    """公开版本解析（供 IPC 等外部模块比较版本）。"""
    return _split_version(v)


class SkillPackageError(ValueError):
    """技能包校验失败。"""


def _normalize_brick_fields(raw: dict, name: str) -> dict:
    """校验并规范化 P0 brick 契约的 5 个字段。缺省安全值；一旦声明则严格校验。"""
    risk_level = raw.get("risk_level", "low")
    if risk_level not in RISK_LEVELS:
        raise SkillPackageError(
            f"risk_level 非法：{risk_level}（应为 {'/'.join(sorted(RISK_LEVELS))}）")

    capabilities = raw.get("capabilities") or []
    if (not isinstance(capabilities, list)
            or not all(isinstance(c, str) and c.strip() for c in capabilities)):
        raise SkillPackageError("capabilities 必须是字符串数组")

    dependencies = raw.get("dependencies") or []
    if not isinstance(dependencies, list):
        raise SkillPackageError("dependencies 必须是数组")
    deps = []
    for d in dependencies:
        if not isinstance(d, dict):
            raise SkillPackageError("dependencies 每项必须是对象")
        dname = d.get("name")
        if not dname or not isinstance(dname, str) or not dname.strip():
            raise SkillPackageError("dependencies 每项必须有 name")
        dtype = d.get("type", "skill")
        if dtype not in DEPENDENCY_TYPES:
            raise SkillPackageError(f"dependencies.type 非法：{dtype}")
        deps.append({"name": str(dname).strip(), "type": dtype,
                     "version": str(d.get("version", "*") or "*")})

    resources = raw.get("resources") or {}
    if not isinstance(resources, dict):
        raise SkillPackageError("resources 必须是对象")
    for k in ("memory_mb", "disk_mb"):
        if k in resources and (not isinstance(resources[k], int)
                               or isinstance(resources[k], bool)
                               or resources[k] < 0):
            raise SkillPackageError(f"resources.{k} 必须是非负整数")
    ports = resources.get("ports") or []
    if not isinstance(ports, list) or not all(
            isinstance(p, int) and not isinstance(p, bool) for p in ports):
        raise SkillPackageError("resources.ports 必须是整数数组")
    if "network" in resources and not isinstance(resources["network"], bool):
        raise SkillPackageError("resources.network 必须是布尔值")

    composition = raw.get("composition") or {}
    if not isinstance(composition, dict):
        raise SkillPackageError("composition 必须是对象")
    for sub in ("requires", "conflicts_with"):
        if sub in composition and not isinstance(composition[sub], list):
            raise SkillPackageError(f"composition.{sub} 必须是数组")
    memory_scope = composition.get("memory_scope") or []
    if not isinstance(memory_scope, list):
        raise SkillPackageError("composition.memory_scope 必须是数组")
    for m in memory_scope:
        if m not in MEMORY_SCOPES:
            raise SkillPackageError(f"composition.memory_scope 非法：{m}")
    if name and name in (composition.get("conflicts_with") or []):
        raise SkillPackageError(f"composition.conflicts_with 不得包含自身 {name}")

    return {
        "capabilities": [str(c) for c in capabilities],
        "dependencies": deps,
        "resources": resources,
        "risk_level": risk_level,
        "composition": composition,
    }



def validate_skill_package(raw: dict) -> Skill:
    """校验并构造 Skill。失败抛 SkillPackageError（带中文原因）。"""
    if not isinstance(raw, dict):
        raise SkillPackageError("技能包不是 JSON 对象")
    name = raw.get("name")
    if not name or not isinstance(name, str):
        raise SkillPackageError("缺少有效 name 字段")
    if not name.strip():
        raise SkillPackageError("name 不能为空")
    trigger = raw.get("trigger") or []
    if not isinstance(trigger, list) or not all(isinstance(t, str) for t in trigger):
        raise SkillPackageError("trigger 必须是字符串数组")
    content = raw.get("content", "")
    if not isinstance(content, str):
        raise SkillPackageError("content 必须是字符串")
    if len(content) > SKILL_CONTENT_MAX:
        raise SkillPackageError(
            f"content 长度 {len(content)} 超过上限 {SKILL_CONTENT_MAX}")
    tags = raw.get("tags") or []
    if not isinstance(tags, list):
        tags = []
    # 路径穿越防御：若包内含文件清单，文件名不得绝对或含 '..'
    for fn in (raw.get("files") or []):
        if not isinstance(fn, str) or fn.startswith("/") or ".." in fn:
            raise SkillPackageError(f"非法文件路径：{fn}")
    # provides_tool 校验（安全红线 §4.5.2）：纯字符串、长度≤64、
    # 匹配 [A-Za-z][A-Za-z0-9_]*。缺省空 = 纯提示技能。
    import re
    provides_tool = raw.get("provides_tool", "")
    if provides_tool:
        if not isinstance(provides_tool, str):
            raise SkillPackageError("provides_tool 必须是字符串")
        if len(provides_tool) > 64:
            raise SkillPackageError("provides_tool 长度超过 64")
        if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", provides_tool):
            raise SkillPackageError(
                f"provides_tool 格式非法：{provides_tool}")
    # 二进制扩展字段（高配技能，见 MARKETPLACE_BINARY_EXT.md）
    binary_url = str(raw.get("binary_url", ""))
    if binary_url and not isinstance(binary_url, str):
        raise SkillPackageError("binary_url 必须是字符串")
    binary_size = raw.get("binary_size", 0) or 0
    if binary_size and (not isinstance(binary_size, int) or binary_size < 0):
        raise SkillPackageError("binary_size 必须是非负整数")
    binary_sha256 = str(raw.get("binary_sha256", ""))
    binary_launch = raw.get("binary_launch") or {}
    if binary_launch and not isinstance(binary_launch, dict):
        raise SkillPackageError("binary_launch 必须是对象")
    if binary_url and not binary_launch.get("port"):
        raise SkillPackageError("声明 binary_url 必须同时提供 binary_launch.port")
    brick = _normalize_brick_fields(raw, str(name).strip())
    return Skill(
        name=str(name).strip(),
        trigger=[str(t) for t in trigger],
        content=content,
        disabled=False,
        summary=str(raw.get("summary", "")),
        version=str(raw.get("version", "")),
        author=str(raw.get("author", "")),
        description=str(raw.get("description", "")),
        category=str(raw.get("category", "")),
        tags=[str(t) for t in tags],
        license=str(raw.get("license", "")),
        source=str(raw.get("source", "")),
        installed_at=str(raw.get("installed_at", "")),
        provides_tool=str(provides_tool),
        binary_url=binary_url,
        binary_size=int(binary_size),
        binary_sha256=binary_sha256,
        binary_launch=binary_launch,
        capabilities=brick["capabilities"],
        dependencies=brick["dependencies"],
        resources=brick["resources"],
        risk_level=brick["risk_level"],
        composition=brick["composition"],
    )


@dataclass
class LibraryEntry:
    """目录里一个技能的轻量元数据（供 UI 展示，不含完整 content）。"""
    id: str
    name: str
    version: str
    author: str
    summary: str
    category: str
    tags: List[str]
    download_url: str
    description: str = ""   # 长解释；目录(index.json)有则取自目录，否则留空由审阅弹窗补全
    installed_version: Optional[str] = None   # 本地已装的版本；None=未装


class SkillLibrary:
    """在线技能源客户端。一次构造，多次 list/install/uninstall/upgrade。"""

    def __init__(self, repo_url: str, home: Path,
                 timeout: int = DEFAULT_TIMEOUT):
        self.repo_url = (repo_url or "").rstrip("/")
        self.home = Path(home)
        self.timeout = timeout
        self._cache_dir = self.home / "cache" / "skill_library"
        self._index_cache = self._cache_dir / "index.cache.json"

    # ---------- 索引 ----------
    def fetch_index(self, force: bool = False) -> Tuple[Optional[dict], Optional[str]]:
        """拉取目录索引。成功返回 (index_dict, None)；失败返回 (None, 错误)。

        force=False 且本地有未过期缓存时优先用缓存；网络不可达则回退缓存；
        缓存也过期/缺失则报不可达（不静默）。
        """
        if not self.repo_url:
            return None, "未配置技能源地址（skill_repo_url 为空）"
        if not force and self._index_cache.exists():
            try:
                cached = json.loads(self._index_cache.read_text(encoding="utf-8"))
                ts = cached.get("_fetched_at", 0)
                if time.time() - ts < INDEX_CACHE_TTL:
                    return cached, None
            except (json.JSONDecodeError, OSError, ValueError):
                pass
        url = f"{self.repo_url}/index.json" if not self.repo_url.endswith(
            ".json") else self.repo_url
        data, err = _http_get(url, self.timeout)
        if err:
            # 网络失败：回退到任何已有缓存（即使过期），否则报不可达
            if self._index_cache.exists():
                try:
                    return json.loads(self._index_cache.read_text(encoding="utf-8")), None
                except (json.JSONDecodeError, OSError, ValueError):
                    pass
            return None, err
        try:
            index = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return None, f"索引 JSON 解析失败：{e}"
        if not isinstance(index, dict):
            return None, "索引格式非法（非 JSON 对象）"
        # 兼容两种索引：旧市场 skills 数组 / brick-registry bricks 数组
        if not isinstance(index.get("skills"), list) and not isinstance(index.get("bricks"), list):
            return None, "索引格式非法（缺少 skills 或 bricks 数组）"
        index["_fetched_at"] = time.time()
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            self._index_cache.write_text(json.dumps(index, ensure_ascii=False),
                                         encoding="utf-8")
        except OSError:
            pass  # 缓存失败不致命
        return index, None

    def list_entries(self, skills_registry,
                     force: bool = False) -> Tuple[Optional[List[LibraryEntry]], Optional[str]]:
        """列出目录（含本地已装版本）。返回 (entries, 错误)。"""
        index, err = self.fetch_index(force=force)
        if err:
            return None, err
        installed = {s.source: s for s in skills_registry.all() if s.source}
        base = self.repo_url + "/"
        out: List[LibraryEntry] = []
        items = index.get("skills") or index.get("bricks") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            sid = str(item.get("id") or item.get("name") or "")
            dl = str(item.get("download_url") or "")
            if not dl:
                # brick-registry 条目用 path 定位 brick.json
                p = str(item.get("path") or "")
                dl = urljoin(base, (p.rstrip("/") + "/brick.json") if p else f"{sid}/brick.json")
            elif not urlparse(dl).scheme:
                dl = urljoin(base, dl)
            local = installed.get(sid)
            out.append(LibraryEntry(
                id=sid,
                name=str(item.get("name", sid)),
                version=str(item.get("version", "")),
                author=str(item.get("author") or "Shadeling"),
                summary=str(item.get("summary", "")),
                category=str(item.get("category", "")),
                tags=list(item.get("tags") or []),
                description=str(item.get("description", "")),
                download_url=dl,
                installed_version=local.version if local else None,
            ))
        return out, None

    # ---------- 安装 / 卸载 / 升级 ----------
    def _download_skill(self, dl_url: str) -> Tuple[Optional[Skill], Optional[str]]:
        data, err = _http_get(dl_url, self.timeout)
        if err:
            return None, err
        try:
            raw = json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return None, f"技能包 JSON 解析失败：{e}"
        try:
            return validate_skill_package(raw), None
        except SkillPackageError as e:
            return None, f"技能包校验失败：{e}"

    def install(self, skill_id: str, skills_registry,
                force: bool = False) -> Tuple[Optional[Skill], Optional[str]]:
        """从目录安装一个技能。返回 (已装 Skill, 错误)。成功已写入 skills.json。"""
        entries, err = self.list_entries(skills_registry, force=force)
        if err:
            return None, err
        entry = next((e for e in entries if e.id == skill_id), None)
        if entry is None:
            return None, f"目录中找不到技能：{skill_id}"
        if (not force and entry.installed_version
                and _split_version(entry.version) <= _split_version(entry.installed_version)):
            return None, f"已安装 {entry.installed_version}，无需升级"
        skill, err = self._download_skill(entry.download_url)
        if err:
            return None, err
        # 打 provenance（必须先于二进制下载：下载路径与运行时 binary_path_for
        # 都依赖 source 定位 home/bin/<source>/，顺序错会导致引擎找不到）
        skill.source = skill_id
        skill.installed_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
        # 高配技能：下载引擎二进制到 BRICKERY_HOME/bin/<source>/
        if skill.binary_url:
            ok, berr = self._download_binary(skill)
            if not ok:
                return None, f"二进制下载失败：{berr}"
        skills_registry.register(skill)  # 同名覆盖
        skills_registry.save(self.home / "skills.json")
        return skill, None

    def _download_binary(self, skill: Skill) -> Tuple[bool, Optional[str]]:
        """下载技能声明的引擎二进制到 home/bin/<source>/<filename>，并设为可执行。

        返回 (成功, 错误)。网络失败返回错误、不崩溃、不静默。
        二进制较大（如 editor_sdk ~193MB），用较长超时；本地源仍很快。
        """
        url = skill.binary_url
        from urllib.parse import urlparse
        name = Path(urlparse(url).path).name or f"{skill.source}_engine"
        dest_dir = self.home / "bin" / (skill.source or skill.name)
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return False, f"创建目录失败：{e}"
        dest = dest_dir / name
        # 已存在且大小匹配则跳过重复下载（幂等，便于重装/升级）
        if dest.exists() and (skill.binary_size == 0 or dest.stat().st_size == skill.binary_size):
            dest.chmod(0o755)
            return True, None
        data, err = _http_get(url, timeout=max(self.timeout, 600))
        if err:
            return False, err
        if not data:
            return False, "下载到空内容"
        if skill.binary_size and len(data) != skill.binary_size:
            return False, f"大小不符：期望 {skill.binary_size}，实际 {len(data)}"
        if skill.binary_sha256:
            import hashlib
            if hashlib.sha256(data).hexdigest() != skill.binary_sha256:
                return False, "SHA256 校验失败"
        try:
            dest.write_bytes(data)
            dest.chmod(0o755)
        except OSError as e:
            return False, f"落盘失败：{e}"
        return True, None

    @staticmethod
    def binary_path_for(home: Path, skill: Skill) -> Optional[Path]:
        """返回已下载的二进制路径（若存在），否则 None。供运行时 ensure_engine 使用。"""
        if not skill.binary_url:
            return None
        from urllib.parse import urlparse
        name = Path(urlparse(skill.binary_url).path).name or f"{skill.source}_engine"
        p = Path(home) / "bin" / (skill.source or skill.name) / name
        return p if p.exists() else None

    def uninstall(self, skill_id: str, skills_registry) -> Tuple[bool, Optional[str]]:
        """卸载一个 marketplace 技能（按 source==skill_id 匹配）。"""
        target = next((s for s in skills_registry.all() if s.source == skill_id), None)
        if target is None:
            return False, f"未找到已安装的技能：{skill_id}"
        skills_registry._skills.pop(target.name, None)
        skills_registry.save(self.home / "skills.json")
        return True, None

    def upgrade(self, skill_id: str, skills_registry) -> Tuple[Optional[Skill], Optional[str]]:
        """升级：仅当远程版本高于本地已装版本时重下；否则提示已最新。"""
        return self.install(skill_id, skills_registry, force=True)

    def review(self, skill_id: str, skills_registry,
               force: bool = False) -> Tuple[Optional[Skill], Optional[str]]:
        """下载技能包返回完整 Skill（供 UI 安装前审阅 content）。不写入本地。"""
        entries, err = self.list_entries(skills_registry, force=force)
        if err:
            return None, err
        entry = next((e for e in entries if e.id == skill_id), None)
        if entry is None:
            return None, f"目录中找不到技能：{skill_id}"
        return self._download_skill(entry.download_url)
