"""§5 守护进程单测：启动/优雅停止/重复启动拒绝/巩固失败隔离。"""
import json
import threading

from brickery.runtime.config import Config, EngineConfig
from brickery.runtime.daemon import Daemon
from .base import RuntimeTestCase


class TestDaemon(RuntimeTestCase):
    def _daemon(self, consolidate):
        cfg = Config(home=self.home, models_root=self.models,
                     engine=EngineConfig())
        mem = object()
        return Daemon(mem, cfg, consolidate=consolidate, poll_interval=0.01)

    def test_start_stop(self):
        ran = {"n": 0, "ev": threading.Event()}

        def cons():
            ran["n"] += 1
            ran["ev"].set()
            return {}

        d = self._daemon(cons)
        d.start()
        self.assertTrue(d.status_file.exists())
        self.assertTrue(ran["ev"].wait(timeout=2))  # 至少跑一次巩固
        d.stop()
        st = json.loads(d.status_file.read_text())
        self.assertEqual(st["state"], "stopped")

    def test_no_orphan_on_double_start(self):
        ran = {"n": 0}

        def cons():
            ran["n"] += 1
            return {}

        d = self._daemon(cons)
        d.start()
        with self.assertRaises(RuntimeError):
            d.start()  # 应拒绝重复启动
        d.stop()

    def test_consolidation_failure_isolated(self):
        ran = {"ok": False, "ev": threading.Event()}
        state = {"calls": 0}

        def cons():
            state["calls"] += 1
            if state["calls"] == 1:
                raise RuntimeError("巩固失败")
            ran["ok"] = True
            ran["ev"].set()
            return {}

        d = self._daemon(cons)
        d.start()
        # 第一次失败被隔离，第二次成功；守护进程仍存活
        self.assertTrue(ran["ev"].wait(timeout=2))
        self.assertTrue(d.is_running())
        d.stop()
