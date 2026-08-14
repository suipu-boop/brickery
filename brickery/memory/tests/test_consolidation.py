"""§7 夜间巩固：串行处理、失败隔离、审计、队列不丢。"""
from brickery.memory import MemorySystem
from brickery.memory import consolidation
from brickery.memory.db import consolidation_conn
from .base import BaseMemoryTest


def _ok(payload):
    return "ok"


def _boom(payload):
    raise RuntimeError("boom")


class TestConsolidation(BaseMemoryTest):
    def _statuses(self):
        with consolidation_conn() as c:
            return [r["status"] for r in c.execute("SELECT status FROM queue").fetchall()]

    def _audit_count(self):
        with consolidation_conn() as c:
            return c.execute("SELECT COUNT(*) AS n FROM audit_log").fetchone()["n"]

    def test_serial_processes_all_pending(self):
        ms = MemorySystem()
        consolidation.register_processor("ok", _ok)
        ms.enqueue("ok")
        ms.enqueue("ok")
        summary = ms.run_consolidation()
        self.assertEqual(summary["processed"], 2)
        self.assertEqual(summary["succeeded"], 2)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(self._statuses(), ["done", "done"])
        self.assertEqual(self._audit_count(), 2)

    def test_failure_isolation(self):
        ms = MemorySystem()
        consolidation.register_processor("ok", _ok)
        consolidation.register_processor("boom", _boom)
        ms.enqueue("ok")
        ms.enqueue("boom")
        summary = ms.run_consolidation()
        self.assertEqual(summary["processed"], 2)
        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(summary["failed"], 1)
        # 失败项保留为 failed，不丢失；成功项 done
        self.assertIn("failed", self._statuses())
        self.assertIn("done", self._statuses())
        # 两项都有审计记录
        self.assertEqual(self._audit_count(), 2)

    def test_unregistered_type_noop_success(self):
        ms = MemorySystem()
        ms.enqueue("future_type")
        summary = ms.run_consolidation()
        self.assertEqual(summary["succeeded"], 1)
        self.assertEqual(self._statuses(), ["done"])
