"""§自进化（evolve）链路单测：observe → 阈值 → distill → verify → pending → confirm/reject。

全程 file:// 与临时 home，无需真服务器/模型：
- 无影子模型时蒸馏走规则降级（trigger=工具名，content=标准指令模板）
- verify 走内置契约校验（trigger 非空 / content 非空 / 无禁用字段）
- pending_candidates 与 evolve_bricks 均为临时 home 下的 memory.db
"""
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest import TestCase

from brickery.runtime.evolve import (
    EVOLVE_LABEL_PREFIX,
    observe,
    observe_and_maybe_distill,
    distill,
    _verify,
    list_candidates,
    confirm_candidate,
    reject_candidate,
    refine_from_trace,
    refine_stats,
)


def _db(home: Path):
    conn = sqlite3.connect(str(home / "memory.db"))
    conn.row_factory = sqlite3.Row
    return conn


def _pending_count(home: Path, status="pending") -> int:
    with _db(home) as c:
        c.execute(
            "CREATE TABLE IF NOT EXISTS pending_candidates("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "label TEXT NOT NULL, value TEXT NOT NULL,"
            "confidence REAL DEFAULT 0.5,"
            "created_at TEXT NOT NULL,"
            "status TEXT DEFAULT 'pending')"
        )
        return c.execute(
            "SELECT COUNT(*) AS n FROM pending_candidates WHERE status=?", (status,)
        ).fetchone()["n"]


def _trace_success_count(home: Path, task_key: str) -> int:
    conn = sqlite3.connect(str(home / "evolve.db"))
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE IF NOT EXISTS evolve_traces("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "task_key TEXT NOT NULL, session_id TEXT NOT NULL,"
        "tools TEXT NOT NULL, success INTEGER NOT NULL,"
        "input_text TEXT NOT NULL, output_text TEXT NOT NULL,"
        "created_at TEXT NOT NULL)"
    )
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM evolve_traces WHERE task_key=? AND success=1",
        (task_key,),
    ).fetchone()
    conn.close()
    return int(row["n"]) if row else 0


class TestEvolveObserve(TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="brickery_evolve_"))

    def test_observe_counts_success(self):
        key = observe(self.home, "sess-1", ["meeting_minutes"], "开会纪要", "已生成纪要", True)
        self.assertIsNotNone(key)
        self.assertEqual(_trace_success_count(self.home, key), 1)

    def test_observe_does_not_count_failure(self):
        key = observe(self.home, "sess-1", ["meeting_minutes"], "开会纪要", "失败", False)
        self.assertIsNotNone(key)
        self.assertEqual(_trace_success_count(self.home, key), 0)


class TestEvolveThreshold(TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="brickery_evolve_"))

    def test_no_candidate_below_threshold(self):
        for i in range(2):
            observe_and_maybe_distill(self.home, "sess-1", ["meeting_minutes"],
                                      "开会纪要", "已生成纪要", True, shadow=None)
        self.assertEqual(_pending_count(self.home), 0)

    def test_candidate_after_three_successes(self):
        for i in range(3):
            observe_and_maybe_distill(self.home, "sess-1", ["meeting_minutes"],
                                      "开会纪要", "已生成纪要", True, shadow=None)
        items = list_candidates(self.home)
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0]["name"].startswith("evolve-"))
        self.assertEqual(items[0]["task_key"], "meeting_minutes")
        self.assertTrue(items[0]["trigger"])
        # 未确认前不得出现在 home/bricks
        self.assertFalse((self.home / "bricks").exists())

    def test_distill_skip_without_tool_calls(self):
        key = observe(self.home, "sess-1", [], "闲聊", "嗯", True)
        self.assertIsNone(key)


class TestEvolveVerify(TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="brickery_evolve_"))

    def test_verify_rejects_empty_trigger(self):
        # 契约校验：空 trigger 必须拒绝（直接测 _verify）
        ok, err = _verify({"name": "x", "trigger": [], "content": "内容", "summary": ""})
        self.assertFalse(ok)
        self.assertIsNotNone(err)
        ok2, _ = _verify({"name": "x", "trigger": ["t"], "content": "内容", "summary": ""})
        self.assertTrue(ok2)
        self.assertEqual(_pending_count(self.home), 0)


class TestEvolveConfirmReject(TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="brickery_evolve_"))

    def _make_candidate(self):
        for i in range(3):
            observe_and_maybe_distill(self.home, "sess-1", ["meeting_minutes"],
                                      "开会纪要", "已生成纪要", True, shadow=None)
        items = list_candidates(self.home)
        self.assertEqual(len(items), 1)
        return items[0]

    def test_confirm_writes_brick(self):
        cand = self._make_candidate()
        ok, msg = confirm_candidate(self.home, cand["id"])
        self.assertTrue(ok, msg)
        brick_dir = self.home / "bricks" / cand["name"]
        self.assertTrue((brick_dir / "brick.json").is_file())
        manifest = json.loads((brick_dir / "brick.json").read_text(encoding="utf-8"))
        self.assertTrue(manifest["source"].startswith("evolve:"))
        self.assertEqual(manifest["risk_level"], "low")
        self.assertEqual(_pending_count(self.home, "resolved"), 1)

    def test_reject_marks_rejected(self):
        cand = self._make_candidate()
        ok, msg = reject_candidate(self.home, cand["id"])
        self.assertTrue(ok, msg)
        self.assertFalse((self.home / "bricks").exists())
        self.assertEqual(_pending_count(self.home, "rejected"), 1)

    def test_double_confirm_idempotent(self):
        cand = self._make_candidate()
        ok, _ = confirm_candidate(self.home, cand["id"])
        self.assertTrue(ok)
        ok2, msg2 = confirm_candidate(self.home, cand["id"])
        self.assertFalse(ok2)
        self.assertIn("不存在", msg2 or "")


class TestRefine(TestCase):
    """批次 2：refine 反馈精炼——强化 / 剪枝 / 降级 / 恢复 / 退役。"""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="brickery_evolve_refine_"))

    def _activate(self, name="evolve-test", source="evolve:task_a"):
        d = self.home / "bricks" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "brick.json").write_text(json.dumps({
            "name": name,
            "trigger": ["测试"],
            "content": "测试流程",
            "summary": "测试积木",
            "source": source,
        }, ensure_ascii=False), encoding="utf-8")

    def _stats(self, name):
        conn = sqlite3.connect(str(self.home / "evolve.db"))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM evolve_refine WHERE brick_name=?", (name,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None

    def test_reward_increases_confidence(self):
        self._activate()
        refine_from_trace(self.home, ["evolve-test"], True)
        st = self._stats("evolve-test")
        self.assertEqual(st["usage_count"], 1)
        self.assertEqual(st["success_count"], 1)
        self.assertEqual(st["consecutive_success"], 1)
        self.assertAlmostEqual(st["confidence"], 0.6)
        self.assertEqual(st["status"], "active")

    def test_penalty_degrades_then_recovers(self):
        self._activate()
        refine_from_trace(self.home, ["evolve-test"], False)
        st = self._stats("evolve-test")
        self.assertAlmostEqual(st["confidence"], 0.35)
        self.assertEqual(st["status"], "degraded")
        # degraded 连续 3 次成功自动恢复 active
        for _ in range(3):
            refine_from_trace(self.home, ["evolve-test"], True)
        st = self._stats("evolve-test")
        self.assertEqual(st["status"], "active")
        self.assertAlmostEqual(st["confidence"], 0.65)
        self.assertEqual(st["consecutive_success"], 3)

    def test_retire_by_low_confidence(self):
        self._activate()
        refine_from_trace(self.home, ["evolve-test"], False)   # 0.35 degraded
        refine_from_trace(self.home, ["evolve-test"], False)   # 0.20 degraded
        refine_from_trace(self.home, ["evolve-test"], False)   # 0.05 retired
        st = self._stats("evolve-test")
        self.assertEqual(st["status"], "retired")
        self.assertTrue((self.home / "bricks" / ".retired-evolve-test").is_dir())
        self.assertFalse((self.home / "bricks" / "evolve-test").exists())

    def test_retire_by_consecutive_fails(self):
        self._activate()
        # 先 5 次成功把置信抬到 1.0，再连败 5 次：conf 仍 >= 0.2，靠连败数触发退役
        for _ in range(5):
            refine_from_trace(self.home, ["evolve-test"], True)
        for _ in range(5):
            refine_from_trace(self.home, ["evolve-test"], False)
        st = self._stats("evolve-test")
        self.assertEqual(st["status"], "retired")
        self.assertEqual(st["consecutive_fail"], 5)
        self.assertTrue((self.home / "bricks" / ".retired-evolve-test").is_dir())

    def test_retired_not_updated_by_auto(self):
        self._activate()
        for _ in range(5):
            refine_from_trace(self.home, ["evolve-test"], False)
        before = self._stats("evolve-test")
        refine_from_trace(self.home, ["evolve-test"], True)
        after = self._stats("evolve-test")
        self.assertEqual(before["usage_count"], after["usage_count"])
        self.assertEqual(after["status"], "retired")

    def test_ignores_non_evolve_brick(self):
        self._activate(source="market:official")
        changed = refine_from_trace(self.home, ["evolve-test"], True)
        self.assertIsNone(changed)

    def test_stats_shape(self):
        self._activate()
        refine_from_trace(self.home, ["evolve-test"], True)
        items = refine_stats(self.home)
        self.assertEqual(len(items), 1)
        it = items[0]
        for k in ("brick_name", "usage_count", "success_count",
                  "consecutive_success", "consecutive_fail",
                  "confidence", "status", "last_result_at"):
            self.assertIn(k, it)
        self.assertEqual(it["brick_name"], "evolve-test")
