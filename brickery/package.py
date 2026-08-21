"""§3.x .brick 积木包：打包 / 解包 / 校验（离线积木安装通道）。

背景：市场源改为 GitHub Only 后，无网/弱网/内网用户无法从市场拉积木。
本模块提供「积木包」通道：有网设备把积木打包成 .brick 单文件，
传到目标机后由底座导入，与联网安装的积木在本地存储、运行上完全一致。

格式（zip，扩展名 .brick）：

    manifest.json               # 必需：包元信息 + 逐积木清单（含 sha256 防篡改）
    skill/<name>/brick.json     # 必需：每块积木的完整 Skill JSON（与市场源同构）
    assets/...                  # 可选：附属资源（未来扩展，本模块解包时忽略结构校验）

manifest.json 字段（与市场 index 条目对齐）：

    {
      "format": "shadeling-brick/v1",
      "created_at": "2026-08-21T10:00:00",
      "packed_by": "brickery",
      "source": "offline",
      "entries": [
        {
          "id": "hello-brick",          # 积木 id（= 远程市场 id，本地导入时作为 source 对齐）
          "name": "Hello 积木",
          "version": "1.2.3",
          "author": "suipu-boop",
          "summary": "打招呼示例积木",
          "category": "demo",
          "tags": ["hello"],
          "path": "skill/hello-brick/brick.json",  # zip 内相对路径
          "sha256": "xxxx"               # 该 brick.json 内容哈希（防篡改/防损坏）
        }
      ]
    }

设计要点：
- 仅用标准库 zipfile / hashlib / json，零第三方依赖（与运行时红线一致）。
- 纯函数、无副作用：pack 只写 zip；unpack 只读 zip 返回积木 dict 列表，
  落盘/注册由调用方（IPC / 工坊服务）决定目标位置。
- 安全：成员名禁止绝对路径与 '..'（zip-slip 防御）；解包前逐条校验 sha256，
  不符即拒绝安装该积木；content 上限复用运行时 SKILL_CONTENT_MAX 语义。
- 单块与批量统一：批量包只是 entries 多于 1 条，结构完全一致。

红线：本模块不执行积木 content 中的任何代码；只做读取与结构校验。
"""
from __future__ import annotations

import hashlib
import json
import time
import zipfile
from pathlib import Path
from typing import List, Optional, Tuple

BRICK_FORMAT = "shadeling-brick/v1"
MANIFEST_NAME = "manifest.json"
BRICK_EXT = ".brick"
# 单块 brick.json 内容上限（与运行时 SKILL_CONTENT_MAX 同一量级，防恶意超大包）
BRICK_JSON_MAX = 2 * 1024 * 1024
# 单包最多积木数（防止 zip 炸弹式条目泛滥）
BRICK_MAX_ENTRIES = 200
# zip 成员名长度上限（防超长路径滥用）
BRICK_MEMBER_NAME_MAX = 512


class BrickPackageError(ValueError):
    """积木包校验失败（中文原因，可直接展示给用户）。"""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalize_name(name: str) -> str:
    """合法化积木目录名/文件名：去空白、去路径分隔、限长。"""
    n = str(name or "").strip().replace("/", "_").replace("\\", "_")
    if not n or n in (".", ".."):
        raise BrickPackageError(f"非法名称：{name!r}")
    return n[:120]


def _safe_member(name: str) -> None:
    """zip 成员名安全校验：相对路径、无 '..'、非空、限长。"""
    if not name or name.startswith("/") or name.startswith("\\"):
        raise BrickPackageError(f"非法包内路径（绝对路径）：{name}")
    if any(part in ("..", "") for part in name.split("/")):
        raise BrickPackageError(f"非法包内路径（穿越/空段）：{name}")
    if len(name) > BRICK_MEMBER_NAME_MAX:
        raise BrickPackageError(f"包内路径过长：{name[:60]}…")


def _build_manifest(entries: List[dict], created_at: str) -> dict:
    return {
        "format": BRICK_FORMAT,
        "created_at": created_at,
        "packed_by": "brickery",
        "source": "offline",
        "entries": entries,
    }


def _entry_from_skill(skill_raw: dict, member_path: str) -> dict:
    """由 Skill JSON 构造 manifest entry（含该 brick.json 的 sha256）。"""
    sid = str(skill_raw.get("id") or skill_raw.get("name") or "").strip()
    if not sid:
        raise BrickPackageError("积木缺少 id/name 字段，无法打包")
    if not isinstance(skill_raw, dict) or not skill_raw.get("content"):
        raise BrickPackageError(f"积木 {sid} 缺少有效 content 字段，无法打包")
    return {
        "id": sid,
        "name": str(skill_raw.get("name", sid)),
        "version": str(skill_raw.get("version", "")),
        "author": str(skill_raw.get("author", "")),
        "summary": str(skill_raw.get("summary", "")),
        "category": str(skill_raw.get("category", "")),
        "tags": list(skill_raw.get("tags") or []),
        "path": member_path,
        "sha256": _sha256(json.dumps(skill_raw, ensure_ascii=False, sort_keys=True).encode("utf-8")),
    }


def _member_for(skill_raw: dict) -> str:
    name = _normalize_name(str(skill_raw.get("name") or skill_raw.get("id") or ""))
    return f"skill/{name}/brick.json"


def pack_brick(skill_raw: dict, out_path: Path) -> Path:
    """单块打包：把一块积木的完整 Skill JSON 打包成 .brick 文件。

    返回写出路径。out_path 扩展名非 .brick 时自动补全。
    """
    return pack_bricks([skill_raw], out_path)


def pack_bricks(skill_list: List[dict], out_path: Path) -> Path:
    """批量打包：多块积木打进同一个 .brick（manifest entries 多条）。

    返回写出路径。skill_list 每项是完整 Skill JSON（含 content）。
    """
    if not skill_list:
        raise BrickPackageError("没有可打包的积木")
    if len(skill_list) > BRICK_MAX_ENTRIES:
        raise BrickPackageError(f"单包积木数 {len(skill_list)} 超过上限 {BRICK_MAX_ENTRIES}")
    out = Path(out_path)
    if out.suffix.lower() != BRICK_EXT:
        out = out.with_suffix(BRICK_EXT)
    created_at = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    entries = []
    members = {}  # member_path -> bytes（避免重复序列化，顺序稳定）
    for raw in skill_list:
        if not isinstance(raw, dict):
            raise BrickPackageError("积木必须是 JSON 对象")
        member = _member_for(raw)
        if member in members:
            raise BrickPackageError(f"积木名重复：{member}")
        payload = json.dumps(raw, ensure_ascii=False, sort_keys=True).encode("utf-8")
        if len(payload) > BRICK_JSON_MAX:
            raise BrickPackageError(
                f"积木 {raw.get('name', '?')} 过大（>{BRICK_JSON_MAX // 1024}KB），无法打包")
        entries.append(_entry_from_skill(raw, member))
        members[member] = payload
    manifest = _build_manifest(entries, created_at)
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(MANIFEST_NAME, json.dumps(manifest, ensure_ascii=False, indent=2))
            for member, payload in members.items():
                zf.writestr(member, payload)
    except OSError as e:
        raise BrickPackageError(f"写包失败：{e}") from e
    return out


def inspect(path: Path) -> Tuple[Optional[dict], Optional[str]]:
    """只读校验 .brick 包：zip 结构、manifest、逐条目路径/哈希。

    返回 (manifest, 错误)。错误为 None 表示校验通过。不写盘、不改动原包。
    """
    p = Path(path)
    if not p.exists():
        return None, f"文件不存在：{p}"
    if p.suffix.lower() != BRICK_EXT:
        return None, f"不是 .brick 文件：{p.name}"
    try:
        zf = zipfile.ZipFile(p)
    except (zipfile.BadZipFile, OSError) as e:
        return None, f"无法打开积木包（不是有效的 zip）：{e}"
    with zf:
        names = zf.namelist()
        if MANIFEST_NAME not in names:
            return None, "积木包缺少 manifest.json"
        for n in names:
            try:
                _safe_member(n)
            except BrickPackageError as e:
                return None, str(e)
        try:
            manifest = json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            return None, f"manifest.json 解析失败：{e}"
        if not isinstance(manifest, dict) or manifest.get("format") != BRICK_FORMAT:
            return None, "manifest 格式非法（缺少 shadeling-brick/v1 标记）"
        entries = manifest.get("entries")
        if not isinstance(entries, list) or not entries:
            return None, "manifest 缺少积木清单 entries"
        if len(entries) > BRICK_MAX_ENTRIES:
            return None, f"积木数 {len(entries)} 超过上限 {BRICK_MAX_ENTRIES}"
        for ent in entries:
            if not isinstance(ent, dict):
                return None, "entries 中存在非对象条目"
            member = str(ent.get("path") or "")
            try:
                _safe_member(member)
            except BrickPackageError as e:
                return None, f"积木 {ent.get('id', '?')}：{e}"
            if member not in names:
                return None, f"积木 {ent.get('id', '?')}：包内缺少 {member}"
            if ent.get("sha256"):
                data = zf.read(member)
                if _sha256(data) != str(ent["sha256"]):
                    return None, f"积木 {ent.get('id', '?')}：SHA256 校验失败（包已损坏或被篡改）"
    return manifest, None


def unpack(path: Path) -> Tuple[Optional[List[dict]], Optional[str]]:
    """解包 .brick：校验通过后返回逐块积木的完整 Skill JSON 列表。

    不落盘——安装位置与注册由调用方决定（IPC 层注册进 skills.json /
    工坊侧直接消费）。任一积木校验失败整体失败（返回错误）。
    """
    manifest, err = inspect(path)
    if err:
        return None, err
    out: List[dict] = []
    with zipfile.ZipFile(path) as zf:
        for ent in manifest["entries"]:
            member = str(ent["path"])
            try:
                raw = json.loads(zf.read(member).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                return None, f"积木 {ent.get('id', '?')}：brick.json 解析失败：{e}"
            if not isinstance(raw, dict):
                return None, f"积木 {ent.get('id', '?')}：brick.json 不是 JSON 对象"
            out.append(raw)
    return out, None


def collect_brick_paths(paths: List[str]) -> List[Path]:
    """把文件/文件夹展开成 .brick 文件列表（文件夹递归查找）。

    供 IPC 批量导入使用：支持一次传多个文件、文件夹（内含 .brick 递归）。
    返回按路径排序去重的 .brick 文件；找不到任何 .brick 返回空列表。
    """
    found: List[Path] = []
    seen = set()
    for p in (paths or []):
        pp = Path(p)
        if pp.is_file() and pp.suffix.lower() == BRICK_EXT:
            key = str(pp.resolve())
            if key not in seen:
                seen.add(key)
                found.append(pp)
        elif pp.is_dir():
            for child in sorted(pp.rglob(f"*{BRICK_EXT}")):
                if not child.is_file():
                    continue
                key = str(child.resolve())
                if key not in seen:
                    seen.add(key)
                    found.append(child)
    return found
