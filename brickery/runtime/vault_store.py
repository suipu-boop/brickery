"""Vault 模块存储层（本地优先 / 敏感数据加密）。

设计依据：docs/vault_design.md（v0.1 + 2026-08-12 命令栏补充）。

- SQLite 存 assets 表，按 type 判别四子结构（document/image/webpage/skill_snapshot）
  + 自由文本 note；共用列 + payload(JSON) 扩展。
- 实际文件存 ~/.brickery/vault/<id>/...（本地优先，不联网不进 iCloud；路径与 ipc 同源，经 BRICKERY_HOME 推导）。
- 证件敏感字段（number_full）用 openssl AES-256-CBC 加密，密钥存 macOS 钥匙串
  （fallback 本地 keyfile）。列表/卡片只回脱敏；详情解锁才回明文。
- 网页抓取用 urllib + 轻量去标签抽取 excerpt，只存摘要+来源+url，不存整页。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import time
import datetime as _dt
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

# --------------------------------------------------------------------------
# 路径与常量
# --------------------------------------------------------------------------
def _resolve_vault_dir() -> Path:
    """与 ipc._vault() 同源：优先 BRICKERY_HOME 环境变量，回退 ~/.brickery。

    注：早期版本硬编码 ~/shadeling-runtime/vault，导致 UI(ipc) 与 agent(builtin_tools)
    写入两个独立库、互不可见（agent 永远查不到用户在界面存的资产）。此处统一为
    BRICKERY_HOME/vault，与 supervisor/ipc 推导口径一致。
    """
    home = os.environ.get("BRICKERY_HOME")
    base = Path(home) if home else (Path.home() / ".brickery")
    return base / "vault"


VAULT_DIR = _resolve_vault_dir()
DB_PATH = VAULT_DIR / "vault.db"
KEYCHAIN_SERVICE = "brickery-vault"
KEYCHAIN_ACCOUNT = "vault-key"

DOC_TYPES = ["身份证", "驾照", "护照", "资格证", "其他证件"]
ASSET_TYPES = ["document", "image", "webpage", "skill_snapshot", "note"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    title       TEXT NOT NULL,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    payload     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_assets_type ON assets(type);
"""


# --------------------------------------------------------------------------
# 加密层（openssl AES-256-CBC，密钥走钥匙串）
# --------------------------------------------------------------------------
def _gen_key() -> str:
    return subprocess.run(["openssl", "rand", "-hex", "32"],
                           capture_output=True, text=True).stdout.strip()


def _store_key(key: str) -> None:
    # 优先钥匙串
    try:
        subprocess.run(["security", "add-generic-password", "-s", KEYCHAIN_SERVICE,
                        "-a", KEYCHAIN_ACCOUNT, "-w", key, "-U"],
                       capture_output=True, timeout=5)
    except Exception:
        pass
    # fallback 本地 keyfile（仍在 shadeling-runtime 私有目录内）
    try:
        kf = VAULT_DIR / ".vault_key"
        kf.write_text(key)
        os.chmod(kf, 0o600)
    except Exception:
        pass


def _load_key() -> str:
    try:
        p = subprocess.run(["security", "find-generic-password", "-s", KEYCHAIN_SERVICE,
                            "-a", KEYCHAIN_ACCOUNT, "-w"],
                           capture_output=True, text=True, timeout=5)
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip()
    except Exception:
        pass
    kf = VAULT_DIR / ".vault_key"
    if kf.exists():
        return kf.read_text().strip()
    key = _gen_key()
    _store_key(key)
    return key


def encrypt(plain: str) -> str:
    key = _load_key()
    p = subprocess.run(["openssl", "enc", "-aes-256-cbc", "-salt", "-pbkdf2",
                        "-pass", f"pass:{key}", "-base64"],
                       input=plain, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"encrypt failed: {p.stderr}")
    return p.stdout.strip()


def decrypt(blob: str) -> str:
    key = _load_key()
    p = subprocess.run(["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2",
                        "-pass", f"pass:{key}", "-base64"],
                       input=blob, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"decrypt failed: {p.stderr}")
    return p.stdout


# --------------------------------------------------------------------------
# 网页抓取（urllib + 轻量去标签）
# --------------------------------------------------------------------------
def _mask(s: str) -> str:
    """证件号脱敏：前4后4保留，中间星号遮挡。"""
    s = str(s).strip()
    if len(s) <= 8:
        return s[0] + "*" * (len(s) - 1)
    return s[:4] + "*" * (len(s) - 8) + s[-4:]


def fetch_webpage(url: str, timeout: float = 12.0) -> Dict[str, str]:
    """抓取网页，返回 {title, excerpt, source}。失败返回带 error 的字典。"""
    import urllib.error
    import urllib.parse
    import urllib.request
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 BrickeryVault"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
        html = raw.decode(charset, errors="replace")
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return {"error": f"抓取失败：{type(e).__name__}: {e}"}
    # 抽 <title>
    mt = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    title = re.sub(r"\s+", " ", _strip_tags(mt.group(1))).strip() if mt else url
    # 抽正文：去掉 script/style/head，再取前若干可见文本
    body = re.sub(r"(?is)<(script|style|head|nav|footer)[^>]*>.*?</\1>", " ", html)
    text = _strip_tags(body)
    text = re.sub(r"\s+", " ", text).strip()
    excerpt = text[:600]
    return {"title": title[:120], "excerpt": excerpt, "source": _host_of(url)}


def _host_of(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc
    except Exception:
        return ""


def _strip_tags(s: str) -> str:
    return re.sub(r"<[^>]+>", " ", s)


# --------------------------------------------------------------------------
# 存储主体
# --------------------------------------------------------------------------
class VaultStore:
    def __init__(self, root: Optional[str] = None):
        self.root = Path(root) if root else VAULT_DIR
        # 迁移：早期版本 vault 落在 ~/shadeling-runtime/vault，统一到 BRICKERY_HOME/vault。
        # 仅当目标不存在、旧路径存在时移动，避免覆盖既有正确数据。
        legacy = Path(os.path.expanduser("~/shadeling-runtime/vault"))
        if legacy.exists() and not self.root.exists():
            try:
                shutil.move(str(legacy), str(self.root))
            except Exception:
                pass
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / "vault.db"
        self._conn = sqlite3.connect(str(self.db))
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---- 内部：序列化 / 反序列化 ----
    def _row_to_dict(self, row, include_sensitive: bool = False) -> Dict[str, Any]:
        aid, typ, title, created, updated, payload = row
        try:
            p = json.loads(payload)
        except Exception:
            p = {}
        fields = p.get("fields", {})
        enc = p.get("enc", {})
        file_ref = p.get("file_ref")
        d: Dict[str, Any] = {
            "id": aid, "type": typ, "title": title,
            "created_at": created, "updated_at": updated,
            "file_ref": file_ref,
            "has_sensitive": bool(enc),
        }
        d.update(fields)
        if include_sensitive and enc:
            for k, blob in enc.items():
                try:
                    d[k] = decrypt(blob)
                except Exception:
                    d[k] = "***解密失败***"
        # §14 B 路增强结果（附加字段，不覆盖原文）
        ai = p.get("ai", {})
        d["ai_summary"] = ai.get("summary", "")
        d["ai_tags"] = ai.get("ai_tags", [])
        d["ai_key_points"] = ai.get("key_points", [])
        return d

    # ---- 增 ----
    def add(self, params: Dict[str, Any]) -> Dict[str, Any]:
        typ = params.get("type")
        if typ not in ASSET_TYPES:
            raise ValueError(f"未知资产类型：{typ}")
        title = (params.get("title") or "").strip() or _default_title(typ)
        fields = dict(params.get("fields") or {})
        now = time.time()
        aid = uuid.uuid4().hex[:12]

        # 文件落盘（来自 Swift 传入的本地路径）
        file_ref = None
        fp = params.get("file_path")
        if fp and os.path.isfile(fp):
            fd = self.root / aid
            fd.mkdir(exist_ok=True)
            dest = fd / os.path.basename(fp)
            shutil.copyfile(fp, dest)
            file_ref = str(dest)

        enc: Dict[str, str] = {}
        if typ == "document":
            num = fields.pop("number_full", None)
            if num:
                enc["number_full"] = encrypt(str(num))
                fields["number_masked"] = _mask(str(num))
            # 网页/拖入时若给了 url 但没有 excerpt，尝试抓取
            if not fields.get("excerpt") and fields.get("url"):
                w = fetch_webpage(fields["url"])
                if "error" not in w:
                    fields.setdefault("title", w["title"])
                    fields["excerpt"] = w["excerpt"]
                    fields["source"] = w.get("source", "")
        elif typ == "webpage":
            if not fields.get("excerpt") and fields.get("url"):
                w = fetch_webpage(fields["url"])
                if "error" not in w:
                    fields.setdefault("title", w["title"])
                    fields["excerpt"] = w["excerpt"]
                    fields["source"] = w.get("source", "")
        elif typ == "skill_snapshot":
            pass  # 来自同步，字段已齐

        payload = {"fields": fields, "enc": enc, "file_ref": file_ref}
        self._conn.execute(
            "INSERT INTO assets (id,type,title,created_at,updated_at,payload) VALUES (?,?,?,?,?,?)",
            (aid, typ, title, now, now, json.dumps(payload, ensure_ascii=False)))
        self._conn.commit()
        return self._row_to_dict((aid, typ, title, now, now, json.dumps(payload, ensure_ascii=False)))

    # ---- 查列表（脱敏，供卡片墙）----
    def list(self, type: Optional[str] = None, q: Optional[str] = None,
             top_k: int = 200) -> List[Dict[str, Any]]:
        sql = "SELECT id,type,title,created_at,updated_at,payload FROM assets"
        wheres, args = [], []
        if type:
            wheres.append("type=?")
            args.append(type)
        if q:
            wheres.append("(title LIKE ? OR payload LIKE ?)")
            args += [f"%{q}%", f"%{q}%"]
        if wheres:
            sql += " WHERE " + " AND ".join(wheres)
        sql += " ORDER BY updated_at DESC LIMIT ?"
        args.append(top_k)
        rows = self._conn.execute(sql, args).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ---- 详情（可选解密敏感字段）----
    def get(self, aid: str, include_sensitive: bool = False) -> Optional[Dict[str, Any]]:
        row = self._conn.execute(
            "SELECT id,type,title,created_at,updated_at,payload FROM assets WHERE id=?",
            (aid,)).fetchone()
        return self._row_to_dict(row, include_sensitive=include_sensitive) if row else None

    # ---- 删 ----
    def delete(self, aid: str) -> bool:
        row = self._conn.execute("SELECT id FROM assets WHERE id=?", (aid,)).fetchone()
        if not row:
            return False
        fd = self.root / aid
        if fd.exists():
            shutil.rmtree(fd, ignore_errors=True)
        self._conn.execute("DELETE FROM assets WHERE id=?", (aid,))
        self._conn.commit()
        return True

    # ---- 检索（vault_query 工具用，脱敏）----
    def query(self, query: str, type: Optional[str] = None, top_k: int = 5) -> List[Dict[str, Any]]:
        items = self.list(type=type, q=query, top_k=top_k * 3)
        # 轻量关键词打分
        ql = query.lower()
        scored = []
        for it in items:
            blob = " ".join(str(v) for v in it.values() if isinstance(v, str)).lower()
            score = blob.count(ql)
            if score:
                scored.append((score, it))
        scored.sort(key=lambda x: -x[0])
        return [it for _, it in scored[:top_k]]

    # ---- 提醒：临近到期/生效的资产（pull 式，供 vault_query upcoming_days）----
    def upcoming(self, window_days: int = 30,
                 now: Optional[float] = None) -> List[Dict[str, Any]]:
        """返回 valid_to / valid_from 落在 [now, now+window_days] 内的资产。

        用于「各种提醒」的 **pull 式** 实现：在聊天中用户问起、或 agent 主动提时
        调用，不做后台定时推送（L4 另行设计）。仅扫 document / webpage。
        """
        now = now if now is not None else time.time()
        horizon = now + window_days * 86400
        out: List[Dict[str, Any]] = []
        for r in self._conn.execute(
                "SELECT id,type,title,created_at,updated_at,payload FROM assets").fetchall():
            d = self._row_to_dict(r)
            if d.get("type") not in ("document", "webpage"):
                continue
            for fld in ("valid_to", "valid_from"):
                raw = (d.get(fld) or "").strip()
                ts = _parse_date(raw)
                if ts is None or not (now <= ts <= horizon):
                    continue
                out.append({
                    "id": d["id"], "type": d["type"], "title": d.get("title", ""),
                    "field": fld, "date": raw,
                    "days_left": int((ts - now) / 86400),
                    "doc_type": d.get("doc_type", ""),
                })
        out.sort(key=lambda x: x["days_left"])
        return out

    # ---- 技能镜像同步（只读，来自 SkillsView）----
    def sync_skills(self, skills: List[Dict[str, Any]]) -> int:
        # 清旧快照
        self._conn.execute("DELETE FROM assets WHERE type='skill_snapshot'")
        n = 0
        for s in skills:
            aid = "sk_" + str(s.get("id") or s.get("name") or uuid.uuid4().hex[:8])
            fields = {
                "skill_id": s.get("id") or s.get("name"),
                "name": s.get("name"),
                "version": s.get("version"),
                "category": s.get("category"),
                "desc": s.get("summary") or s.get("description") or "",
                "enabled": s.get("enabled", True),
            }
            now = time.time()
            payload = {"fields": fields, "enc": {}, "file_ref": None}
            self._conn.execute(
                "INSERT OR REPLACE INTO assets (id,type,title,created_at,updated_at,payload) VALUES (?,?,?,?,?,?)",
                (aid, "skill_snapshot", fields["name"] or "技能", now, now,
                 json.dumps(payload, ensure_ascii=False)))
            n += 1
            self._conn.commit()
        return n

    # ---- B 层受控扫描（只读遍历，不擅自收纳）----
    def scan_dir(self, path: str, recursive: bool = True,
                 max_files: int = 800) -> List[Dict[str, Any]]:
        """列出目录下疑似数字资产的候选文件，供「从电脑导入」向导勾选。

        只做只读遍历 + 扩展名分类（启发式建议类型），不调 LLM、不 OCR、
        不触碰加密库、不擅自收纳。真正的归类/OCR 留给导入时的确认环节。
        """
        root = Path(path)
        if not root.exists() or not root.is_dir():
            raise ValueError(f"目录不存在或不是文件夹：{path}")
        img_ext = {"png", "jpg", "jpeg", "heic", "gif", "webp", "tiff", "bmp"}
        doc_ext = {"pdf"}
        note_ext = {"txt", "md", "markdown"}
        cands: List[Dict[str, Any]] = []
        it = root.rglob("*") if recursive else root.glob("*")
        for p in it:
            if not p.is_file():
                continue
            ext = p.suffix.lower().lstrip(".")
            if ext in img_ext:
                st = "image"
            elif ext in doc_ext:
                st = "document"
            elif ext in note_ext:
                st = "note"
            else:
                continue
            try:
                size = p.stat().st_size
            except OSError:
                size = 0
            cands.append({
                "path": str(p), "name": p.name, "ext": ext,
                "size": size, "suggested_type": st,
            })
            if len(cands) >= max_files:
                break
        return cands

    # ---- §14 B 路：写回 AI 增强结果（附加字段，不覆盖原文）----
    def update_enhancement(self, aid: str, summary: str = "", ai_tags=None,
                           key_points=None) -> bool:
        row = self._conn.execute(
            "SELECT payload FROM assets WHERE id=?", (aid,)).fetchone()
        if not row:
            return False
        try:
            p = json.loads(row[0])
        except Exception:
            return False
        ai = p.get("ai", {})
        if summary:
            ai["summary"] = summary
        if ai_tags:
            ai["ai_tags"] = list(ai_tags)
        if key_points:
            ai["key_points"] = list(key_points)
        p["ai"] = ai
        self._conn.execute(
            "UPDATE assets SET payload=?, updated_at=? WHERE id=?",
            (json.dumps(p, ensure_ascii=False), time.time(), aid))
        self._conn.commit()
        return True

    # ---- OCR 文本写回（不覆盖原文，仅补/改 fields 字段）----
    def update_fields(self, aid: str, fields: Dict[str, Any]) -> bool:
        """把 OCR 文本等附加字段写回已有资产（如 image 资产的 excerpt）。

        只更新传入的字段，不触碰 enc / file_ref / ai 等其它结构。
        """
        row = self._conn.execute(
            "SELECT payload FROM assets WHERE id=?", (aid,)).fetchone()
        if not row:
            return False
        try:
            p = json.loads(row[0])
        except Exception:
            return False
        f = p.get("fields", {})
        f.update(fields)
        p["fields"] = f
        self._conn.execute(
            "UPDATE assets SET payload=?, updated_at=? WHERE id=?",
            (json.dumps(p, ensure_ascii=False), time.time(), aid))
        self._conn.commit()
        return True


def _parse_date(s: str) -> Optional[float]:
    """宽松解析日期：YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD / 中文年月日 → unix 时间戳。"""
    s = (s or "").strip()
    if not s:
        return None
    m = re.search(r"(\d{4})[年/\-.](\d{1,2})[月/\-.](\d{1,2})", s)
    if not m:
        return None
    try:
        return _dt.datetime(int(m.group(1)), int(m.group(2)),
                            int(m.group(3))).timestamp()
    except Exception:
        return None


def _default_title(typ: str) -> str:
    return {
        "document": "未命名证件", "image": "未命名图片",
        "webpage": "未命名收藏", "skill_snapshot": "技能镜像", "note": "未命名笔记",
    }.get(typ, "未命名资产")
