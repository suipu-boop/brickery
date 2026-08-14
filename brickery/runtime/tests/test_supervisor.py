"""§X 监督器单测：拉起/监控/自愈/诊断，全纯代码确定性。

用 runtime/tests/_fake_backend.py 模拟后端，覆盖：
- 正常：后端存活，不重启。
- 运行中崩溃：自动重启后恢复。
- 持续启动即崩：超过最大重试进入 needs_attention，并生成纯代码诊断报告。
- classify_backend_error / compute_backoff 纯函数分支。
"""
import json
import os
import socket
import sys
import time
import unittest
from pathlib import Path

from brickery.runtime.supervisor import (
    Supervisor,
    classify_backend_error,
    compute_backoff,
)
from .base import RuntimeTestCase

FAKE = str(Path(__file__).resolve().parent / "_fake_backend.py")


def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def make_sup(self, port, *, backend_args, max_failures=3, **kw):
    return Supervisor(
        home=self.home, port=port,
        python=sys.executable,
        poll_interval=kw.pop("poll_interval", 0.1),
        base_backoff=kw.pop("base_backoff", 0.05),
        max_backoff=kw.pop("max_backoff", 0.3),
        max_failures=max_failures,
        backend_args=backend_args,
        **kw,
    )


class TestSupervisorBasics(RuntimeTestCase):
    def test_healthy_no_restart(self):
        port = free_port()
        sup = make_sup(self, port, backend_args=[FAKE, "--mode", "ok"])
        try:
            sup.start()
            time.sleep(1.2)
            self.assertEqual(sup.restarts, 0, "正常后端不应被重启")
            self.assertTrue(sup.healthy)
            self.assertEqual(sup.status, "running")
        finally:
            sup.stop()

    def test_crash_then_recover(self):
        port = free_port()
        state = self.home / "state.txt"
        sup = make_sup(self, port,
                       backend_args=[FAKE, "--crash-first", "1",
                                     "--state", str(state)])
        try:
            sup.start()
            # 第一次运行中崩溃 -> 自动重启 -> 第二次正常常驻
            time.sleep(2.0)
            self.assertGreaterEqual(sup.restarts, 1, "应至少自愈重启一次")
            self.assertTrue(sup.healthy, "最终应恢复健康")
            self.assertEqual(sup.status, "running")
        finally:
            sup.stop()

    def test_persistent_crash_enters_needs_attention(self):
        port = free_port()
        sup = make_sup(self, port, max_failures=3,
                       backend_args=[FAKE, "--mode", "import_error"])
        try:
            sup.start()
            # 连续启动即崩，超过 max_failures 进入 needs_attention 并出报告
            time.sleep(3.0)
            self.assertEqual(sup.status, "needs_attention")
            self.assertIsNotNone(sup.report_path)
            self.assertTrue((self.home / "logs" / "selfheal.report.json").exists())
            self.assertTrue((self.home / "logs" / "selfheal.report.txt").exists())
            rep = json.loads(
                (self.home / "logs" / "selfheal.report.json").read_text())
            self.assertEqual(rep["category"], "missing_dependency")
            self.assertIn("llama_cpp", rep["suggestion"])
        finally:
            sup.stop()

    def test_status_file_written_and_atomic(self):
        port = free_port()
        sup = make_sup(self, port, backend_args=[FAKE, "--mode", "ok"])
        try:
            sup.start()
            time.sleep(0.6)
            self.assertTrue((self.home / "supervisor.status.json").exists())
            data = json.loads(
                (self.home / "supervisor.status.json").read_text())
            self.assertIn(data["status"], ("running", "starting", "recovering"))
            self.assertIn("note", data)
        finally:
            sup.stop()


class TestSupervisorPure(RuntimeTestCase):
    def test_classify_port_in_use(self):
        d = classify_backend_error("OSError: [Errno 48] Address already in use")
        self.assertEqual(d["category"], "port_in_use")

    def test_classify_missing_dependency(self):
        d = classify_backend_error(
            "Traceback\nModuleNotFoundError: No module named 'llama_cpp'")
        self.assertEqual(d["category"], "missing_dependency")
        self.assertIn("llama_cpp", d["suggestion"])

    def test_classify_syntax_error(self):
        d = classify_backend_error("  File 'x', line 1\nSyntaxError: invalid syntax")
        self.assertEqual(d["category"], "syntax_error")

    def test_classify_out_of_memory(self):
        d = classify_backend_error("MemoryError: cannot allocate")
        self.assertEqual(d["category"], "out_of_memory")

    def test_classify_runtime_exception(self):
        d = classify_backend_error(
            "Traceback\nValueError: bad config value")
        self.assertEqual(d["category"], "runtime_exception")

    def test_classify_unknown(self):
        d = classify_backend_error("something weird happened with no keywords")
        self.assertEqual(d["category"], "unknown")

    def test_compute_backoff(self):
        self.assertEqual(compute_backoff(1), 1.0)
        self.assertEqual(compute_backoff(2), 2.0)
        self.assertEqual(compute_backoff(3), 4.0)
        self.assertEqual(compute_backoff(100, cap=30.0), 30.0)
        self.assertEqual(compute_backoff(0), 1.0)  # 视作第 1 次


if __name__ == "__main__":
    unittest.main()
