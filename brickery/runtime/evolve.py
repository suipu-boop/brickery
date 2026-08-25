"""自进化蒸馏器 v1（批次 1）· observe → 判定 → distill → verify → pending 确认。

对应 specs/agent-self-evolve.md 第 6 节批次 1：
- observe：记录多步任务轨迹（工具序列、结果成败）到 evolve_traces 表
- 判定：同 task_key 成功轨迹累计 >= 3 触发蒸馏（防噪音）
- distill：优先影子模型 complete() 生成候选积木（纯 PromptBrick），失败降级规则模板
- verify：契约字段校验（name / trigger / content 非空 + 长度上限）
- 确认：候选写入 pending_candidates（label 前缀 evolve:），用户确认后写
  home/bricks/<name>/brick.json，内核启动 _activate_bricks 激活（热插拔链路）

不变量：
- 零外连、出错静默、不阻塞主循环（loop 侧异步线程调用）
- 候选不自动激活：一律 pending 待用户确认
- 检索层不动：激活后仍由 SkillRegistry.match 按关键词触发
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# pending_candidates 表与 fixed_core 共享，label 前缀区分来源
EVOLVE_LABEL_PREFIX = "evolve:"
_DISTILL_THRESHOLD = 3          # 同类成功任务累计 >= 3 触发蒸馏
_TRACE_WINDOW = 3               # 蒸馏取最近 N 条成功轨迹
_MAX_NAME = 64
_MAX_TRIGGER = 10
_MAX_TRIGGER_LEN = 32
_MAX_SUMMARY = 200
_MAX_CONTENT = 4000

_STOPWORDS = {
    "的", "了", "是", "在", "我", "有", "和", "就", "不", "人", "都",
    "一", "一个", "这个", "那个", "请", "帮我", "把", "给", "做", "要",
    "the", "a", "an", "and", "or", "for", "to", "with", "please", "help",
}


def _memory_db(home: Path) -> Path:
    """自进化候选与记忆模块共用 memory.db（home 根目录）。"""
    return home / "memory.db"


def _db(home: Path) -> sqlite3.Connection:
    home.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(home / "evolve.db"))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS evolve_traces("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "task_key TEXT NOT NULL,"
        "session_id TEXT NOT NULL,"
        "tools TEXT NOT NULL,"
        "success INTEGER NOT NULL,"
        "input_text TEXT NOT NULL,"
        "output_text TEXT NOT NULL,"
        "created_at TEXT NOT NULL)"
    )
    conn.execute(
        "CREATE TABLE IF NOT EXISTS evolve_bricks("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "task_key TEXT UNIQUE NOT NULL,"
        "brick_name TEXT NOT NULL,"
        "trace_ids TEXT NOT NULL,"
        "status TEXT DEFAULT 'pending',"
        "created_at TEXT NOT NULL)"
    )
    conn.commit()
    return conn


# ---------- observe ----------

def _task_key(tool_names: List[str]) -> Optional[str]:
    """任务指纹：工具名有序去重序列。无工具调用（纯聊天）不参与自进化。"""
    seen: List[str] = []
    for t in tool_names:
        if t and t not in seen:
            seen.append(t)
    return "|".join(seen) if seen else None


def _success(reply: str, tool_names: List[str], crashed: bool) -> bool:
    """成功判定：未崩溃、有工具调用、最终回复非空。"""
    if crashed:
        return False
    if not tool_names:
        return False
    return bool(reply and reply.strip())


def observe(home: Path, session_id: str, tool_names: List[str],
            input_text: str, output_text: str, success: bool,
            now: Optional[str] = None) -> Optional[str]:
    """记录一条任务轨迹，返回 task_key；无工具调用返回 None。"""
    key = _task_key(tool_names)
    if key is None:
        return None
    ts = now or time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    with _db(home) as conn:
        conn.execute(
            "INSERT INTO evolve_traces(task_key, session_id, tools, success,"
            " input_text, output_text, created_at) VALUES(?,?,?,?,?,?,?)",
            (key, session_id, json.dumps(tool_names, ensure_ascii=False),
             1 if success else 0, input_text[:2000], output_text[:2000], ts),
        )
    return key


# ---------- 判定 ----------

def _success_count(home: Path, task_key: str) -> int:
    with _db(home) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM evolve_traces"
            " WHERE task_key=? AND success=1",
            (task_key,),
        ).fetchone()
    return int(row["n"]) if row else 0


def _recent_success_traces(home: Path, task_key: str,
                           limit: int = _TRACE_WINDOW) -> List[Dict[str, Any]]:
    with _db(home) as conn:
        rows = conn.execute(
            "SELECT tools, input_text, output_text FROM evolve_traces"
            " WHERE task_key=? AND success=1 ORDER BY id DESC LIMIT ?",
            (task_key, limit),
        ).fetchall()
    return [dict(r) for r in reversed(rows)]


def _already_distilled(home: Path, task_key: str) -> bool:
    with _db(home) as conn:
        row = conn.execute(
            "SELECT id FROM evolve_bricks WHERE task_key=?", (task_key,)
        ).fetchone()
    return row is not None


# ---------- distill ----------

def _pick_trigger(inputs: List[str]) -> List[str]:
    """从用户输入提取高频实义词作 trigger（规则降级 / 影子模型空返回时兜底）。"""
    freq: Dict[str, int] = {}
    for text in inputs:
        for tok in re.split(r"[\s,，。！？!?、；;：:（）()【】\[\]\n]+", text or ""):
            tok = tok.strip().lower()
            if not tok or tok in _STOPWORDS or len(tok) < 2:
                continue
            if re.fullmatch(r"[\d\W]+", tok):
                continue
            freq[tok] = freq.get(tok, 0) + 1
    ranked = sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))
    return [w for w, _ in ranked[:_MAX_TRIGGER]]


def _distill_with_shadow(shadow: Any, traces: List[Dict[str, Any]]) -> Optional[dict]:
    """影子模型蒸馏：输入轨迹摘要，要求输出 JSON 候选。失败返回 None。"""
    if shadow is None or not hasattr(shadow, "complete"):
        return None
    sample = traces[-1]
    prompt = (
        "你是积木蒸馏器。根据用户任务轨迹，生成一个可复用积木（纯提示词积木）。\n"
        "轨迹工具序列：" + ", ".join(json.loads(sample["tools"])) + "\n"
        "用户输入：" + (sample["input_text"] or "")[:500] + "\n"
        "成功输出：" + (sample["output_text"] or "")[:500] + "\n"
        "请只输出 JSON：{\"name\":\"英文短名\",\"trigger\":[\"触发词1\",\"触发词2\"],"
        "\"summary\":\"一句话描述\",\"content\":\"可复用的任务流程指令（中文）\"}\n"
    )
    try:
        raw = (shadow.complete(prompt) or "").strip()
    except Exception:
        return None
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return {
        "name": str(data.get("name", "")).strip(),
        "trigger": data.get("trigger") if isinstance(data.get("trigger"), list)
                   else [str(t).strip() for t in str(data.get("trigger", "")).split(",") if t.strip()],
        "summary": str(data.get("summary", "")).strip(),
        "content": str(data.get("content", "")).strip(),
    }


def _distill_fallback(traces: List[Dict[str, Any]]) -> dict:
    """规则降级：trigger 取高频词，content 用模板串起成功工具序列。"""
    inputs = [t.get("input_text", "") for t in traces]
    trigger = _pick_trigger(inputs) or ["evolve-task"]
    tool_seq = " → ".join(json.loads(traces[-1]["tools"]))
    sample = traces[-1]
    name = "evolve-" + re.sub(r"\W+", "-", trigger[0]).lower()[:40]
    content = (
        "【自进化积木】执行任务时按以下流程：\n"
        f"1. 调用工具序列：{tool_seq}\n"
        f"2. 参考成功示例输入：{sample.get('input_text', '')[:200]}\n"
        f"3. 输出结果需覆盖用户全部诉求，格式清晰。\n"
    )
    return {
        "name": name,
        "trigger": trigger,
        "summary": "自进化：从成功任务轨迹蒸馏的可复用流程",
        "content": content,
    }


def _verify(brick: dict) -> Tuple[bool, Optional[str]]:
    name = brick.get("name", "")
    trigger = brick.get("trigger") or []
    content = brick.get("content", "")
    if not name or len(name) > _MAX_NAME:
        return False, "name 缺失或超长"
    if not isinstance(trigger, list) or not trigger:
        return False, "trigger 缺失"
    if len(trigger) > _MAX_TRIGGER:
        return False, "trigger 过多"
    for t in trigger:
        if not isinstance(t, str) or not t.strip() or len(t) > _MAX_TRIGGER_LEN:
            return False, f"trigger 非法：{t!r}"
    if not content or len(content) > _MAX_CONTENT:
        return False, "content 缺失或超长"
    if len(brick.get("summary", "")) > _MAX_SUMMARY:
        return False, "summary 超长"
    return True, None


def _write_pending(home: Path, task_key: str, brick: dict,
                   confidence: float, trace_ids: List[int],
                   now: Optional[str] = None) -> Optional[int]:
    """候选写入 pending_candidates（label 前缀 evolve:，value 为候选 JSON）。"""
    ts = now or time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
    label = EVOLVE_LABEL_PREFIX + brick["name"]
    payload = json.dumps({
        "task_key": task_key,
        "brick": brick,
        "trace_ids": trace_ids,
    }, ensure_ascii=False)
    with _db(home) as conn:
        cur = conn.execute(
            "INSERT INTO evolve_bricks(task_key, brick_name, trace_ids, status, created_at)"
            " VALUES(?,?,?,?,?)",
            (task_key, brick["name"], json.dumps(trace_ids), "pending", ts),
        )
        brick_id = int(cur.lastrowid)
        # pending_candidates 与记忆模块共用同一 SQLite（memory.db）
        mem = sqlite3.connect(str(_memory_db(home)))
        mem.execute(
            "CREATE TABLE IF NOT EXISTS pending_candidates("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "label TEXT NOT NULL, value TEXT NOT NULL,"
            "confidence REAL DEFAULT 0.5,"
            "created_at TEXT NOT NULL,"
            "status TEXT DEFAULT 'pending')"
        )
        mem.execute(
            "INSERT INTO pending_candidates(label, value, confidence, created_at, status)"
            " VALUES(?,?,?,?, 'pending')",
            (label, payload, confidence, ts),
        )
        mem.commit()
        mem.close()
    return brick_id


def distill(home: Path, task_key: str, shadow: Any = None) -> Optional[dict]:
    """判定 + 蒸馏 + 写候选。返回候选信息；不满足条件或失败返回 None。"""
    if _already_distilled(home, task_key):
        return None
    if _success_count(home, task_key) < _DISTILL_THRESHOLD:
        return None
    traces = _recent_success_traces(home, task_key)
    if len(traces) < _DISTILL_THRESHOLD:
        return None
    brick = _distill_with_shadow(shadow, traces) or _distill_fallback(traces)
    ok, err = _verify(brick)
    if not ok:
        return None
    trace_ids = _trace_ids(home, task_key)
    confidence = min(0.95, 0.5 + 0.1 * (len(trace_ids) - _DISTILL_THRESHOLD))
    _write_pending(home, task_key, brick, confidence, trace_ids)
    return {"name": brick["name"], "task_key": task_key, "status": "pending"}


def _trace_ids(home: Path, task_key: str) -> List[int]:
    with _db(home) as conn:
        rows = conn.execute(
            "SELECT id FROM evolve_traces WHERE task_key=? AND success=1"
            " ORDER BY id DESC LIMIT ?",
            (task_key, _TRACE_WINDOW),
        ).fetchall()
    return [int(r["id"]) for r in rows]


def observe_and_maybe_distill(home: Path, session_id: str, tool_names: List[str],
                              input_text: str, output_text: str, success: bool,
                              shadow: Any = None) -> Optional[dict]:
    """loop 挂钩入口：记录轨迹；达标则蒸馏。出错静默。"""
    try:
        key = observe(home, session_id, tool_names, input_text, output_text, success)
        if key is None:
            return None
        return distill(home, key, shadow=shadow)
    except Exception:
        return None


# ---------- 确认 / 拒绝 ----------

def _pending_row(home: Path, candidate_id: int) -> Optional[sqlite3.Row]:
    mem = sqlite3.connect(str(_memory_db(home)))
    mem.row_factory = sqlite3.Row
    row = mem.execute(
        "SELECT id, label, value, created_at FROM pending_candidates"
        " WHERE id=? AND status='pending'",
        (candidate_id,),
    ).fetchone()
    if row is None or not str(row["label"]).startswith(EVOLVE_LABEL_PREFIX):
        mem.close()
        return None
    return row


def list_candidates(home: Path) -> List[dict]:
    """列出待确认的自进化候选（label 前缀 evolve:）。"""
    mem = sqlite3.connect(str(_memory_db(home)))
    mem.row_factory = sqlite3.Row
    rows = mem.execute(
        "SELECT id, label, value, confidence, created_at FROM pending_candidates"
        " WHERE status='pending' AND label LIKE ? ORDER BY id ASC",
        (EVOLVE_LABEL_PREFIX + "%",),
    ).fetchall()
    mem.close()
    out = []
    for r in rows:
        try:
            payload = json.loads(r["value"])
        except (json.JSONDecodeError, TypeError):
            continue
        brick = payload.get("brick", {})
        out.append({
            "id": r["id"],
            "name": brick.get("name", ""),
            "summary": brick.get("summary", ""),
            "trigger": brick.get("trigger", []),
            "confidence": r["confidence"],
            "created_at": r["created_at"],
            "task_key": payload.get("task_key", ""),
        })
    return out


def confirm_candidate(home: Path, candidate_id: int) -> Tuple[bool, Optional[str]]:
    """确认候选 → 写 home/bricks/<name>/brick.json → 激活（重启生效，同 market_install 语义）。

    返回 (ok, error_or_brick_name)。
    """
    row = _pending_row(home, candidate_id)
    if row is None:
        return False, "候选不存在或已处理"
    try:
        payload = json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        return False, "候选数据损坏"
    brick = payload.get("brick", {})
    name = brick.get("name", "")
    if not name:
        return False, "候选缺少积木名"
    # 契约直映射（Skill dataclass 字段子集）
    manifest = {
        "name": name,
        "trigger": brick.get("trigger", []),
        "content": brick.get("content", ""),
        "summary": brick.get("summary", ""),
        "version": "0.1.0",
        "author": "auto-evolve",
        "category": "evolve",
        "source": "evolve:" + payload.get("task_key", ""),
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
        "risk_level": "low",
    }
    dest_dir = home / "bricks" / name
    try:
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / "brick.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError as e:
        return False, f"写入积木失败：{e}"
    with _db(home) as conn:
        conn.execute(
            "UPDATE evolve_bricks SET status='confirmed' WHERE task_key=?",
            (payload.get("task_key", ""),),
        )
    mem = sqlite3.connect(str(_memory_db(home)))
    mem.execute(
        "UPDATE pending_candidates SET status='resolved' WHERE id=?", (candidate_id,)
    )
    mem.commit()
    mem.close()
    return True, name


def reject_candidate(home: Path, candidate_id: int) -> Tuple[bool, Optional[str]]:
    """拒绝候选：标记 rejected，不激活。"""
    row = _pending_row(home, candidate_id)
    if row is None:
        return False, "候选不存在或已处理"
    try:
        payload = json.loads(row["value"])
    except (json.JSONDecodeError, TypeError):
        payload = {"task_key": ""}
    mem = sqlite3.connect(str(_memory_db(home)))
    mem.execute(
        "UPDATE pending_candidates SET status='rejected' WHERE id=?", (candidate_id,)
    )
    mem.commit()
    mem.close()
    if payload.get("task_key"):
        with _db(home) as conn:
            conn.execute(
                "UPDATE evolve_bricks SET status='rejected' WHERE task_key=?",
                (payload["task_key"],),
            )
    return True, None
