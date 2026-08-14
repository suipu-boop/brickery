"""P2 调度内核单测：TaskStore 持久化/崩溃恢复 + Scheduler submit/wait/cancel/notifier。"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from brickery.runtime.scheduler import Scheduler, TaskStore, Task, TaskStatus  # noqa: E402


class _FakeLoop:
    def __init__(self, reply):
        self._reply = reply

    def run(self, prompt, project=""):
        return f"{self._reply}:{prompt}"


class SchedulerTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(__file__).resolve().parent / "_tmp_sched"
        if self.home.exists():
            for f in self.home.glob("*"):
                f.unlink()
        self.home.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        import shutil
        if self.home.exists():
            shutil.rmtree(self.home, ignore_errors=True)

    def _factory(self, reply="ok"):
        def fac(**kw):
            return _FakeLoop(reply)
        return fac

    def test_submit_then_wait_returns_result(self):
        s = Scheduler(self._factory("done"), home=self.home, max_workers=1)
        s.start()
        try:
            t = s.submit("hello")
            self.assertEqual(t.status, TaskStatus.QUEUED)
            got = s.wait(t.id, timeout=10)
            self.assertIsNotNone(got)
            self.assertEqual(got.status, TaskStatus.DONE)
            self.assertEqual(got.result, "done:hello")
        finally:
            s.stop()

    def test_notifier_called_on_complete(self):
        called = []
        s = Scheduler(self._factory("ok"), home=self.home, max_workers=1,
                      notifier=lambda tk: called.append(tk.id))
        s.start()
        try:
            t = s.submit("x")
            s.wait(t.id, timeout=10)
            self.assertIn(t.id, called)
        finally:
            s.stop()

    def test_cancel_queued(self):
        # 用一个会卡住的 factory 来制造 RUNNING 窗口；fake loop 尊重 should_stop
        import time

        def slow_factory(**kw):
            stop = kw.get("should_stop")

            class _L:
                def run(self, prompt, project=""):
                    for _ in range(40):
                        if stop and stop():
                            raise InterruptedError()
                        time.sleep(0.05)
                    return "late"
            return _L()

        s = Scheduler(slow_factory, home=self.home, max_workers=1)
        s.start()
        try:
            t = s.submit("slow")
            # 立刻取消（worker 已起跑但 run 在轮询 should_stop，应被中断）
            ok = s.cancel(t.id)
            self.assertTrue(ok)
            got = s.wait(t.id, timeout=10)
            self.assertIsNotNone(got)
            self.assertIn(got.status,
                          (TaskStatus.CANCELLED, TaskStatus.FAILED))
        finally:
            s.stop()

    def test_crash_recovery_marks_running_failed(self):
        # 写一个遗留 RUNNING 任务，TaskStore 加载应标为 FAILED
        store = TaskStore(self.home)
        live = Task(id="task_old", prompt="p", status=TaskStatus.RUNNING,
                    created_at="2026-01-01T00:00:00")
        store.put(live)
        # 重新加载（模拟重启）
        store2 = TaskStore(self.home)
        reloaded = store2.get("task_old")
        self.assertIsNotNone(reloaded)
        self.assertEqual(reloaded.status, TaskStatus.FAILED)

    def test_parent_records_subtask(self):
        s = Scheduler(self._factory("ok"), home=self.home, max_workers=1)
        s.start()
        try:
            parent = s.submit("parent")
            child = s.submit("child", parent_id=parent.id)
            s.wait(child.id, timeout=10)
            p = s.get(parent.id)
            self.assertIn(child.id, p.subtasks)
        finally:
            s.stop()


if __name__ == "__main__":
    unittest.main()
