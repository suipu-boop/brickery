"""§5 守护进程（clean room）。

一个可后台常驻的进程，负责挂接「记忆夜间巩固」等离线任务、响应启动 / 停止信号、
向桌面 App 暴露健康状态。
红线：不得持有全局单例导致无法停止；不得在推理时阻塞巩固；停止时必须释放资源、不留孤儿。

本骨架用后台线程实现（便于测试与优雅停止）；真实部署可由桌面 App 以子进程托管，
但停止契约一致：收到停止信号 → 释放 Event → 线程 / 进程退出 → 状态标记 stopped。
"""
from __future__ import annotations

import json
import os
import signal
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from .config import Config


class Daemon:
    # 生产默认轮询间隔（秒）。切忌调小：整理任务会开库 / 可能触发推理，
    # 高频轮询等于持续占用 CPU 与 GPU。真正的触发门槛由调用方的「空闲时长」
    # 判断承担（见 NightlyConfig.idle_minutes），轮询只负责定期来看一眼。
    # 测试用例显式传入更小值以缩短用时，不受此默认影响。
    DEFAULT_POLL_INTERVAL = 60.0

    def __init__(self, memory, config: Config, *,
                 poll_interval: float = DEFAULT_POLL_INTERVAL,
                 consolidate: Optional[Callable[[], dict]] = None):
        self.memory = memory
        self.config = config
        self.home = config.home
        self.status_file = self.home / "daemon.status"
        self.poll_interval = poll_interval
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last: Optional[str] = None
        self._consolidate = consolidate or (lambda: memory.run_consolidation())

    def _now(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S")

    def _write_status(self, state: str) -> None:
        data = {"pid": os.getpid(), "state": state,
                "last_consolidation": self._last}
        self.status_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def _pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False

    def is_running(self) -> bool:
        if not self.status_file.exists():
            return False
        try:
            d = json.loads(self.status_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return False
        if d.get("state") != "running":
            return False
        pid = d.get("pid")
        return bool(pid) and self._pid_alive(int(pid))

    def _idle_finalize(self) -> None:
        """O1 闲置触发落档：超过 idle_minutes 仍未确认的会话标记完成。失败隔离。"""
        try:
            idle = self.config.nightly.idle_minutes
            self.memory.idle_finalize(idle)
        except Exception:
            pass

    def _run(self) -> None:
        while not self._stop.is_set():
            # 任务失败隔离：单任务异常不影响守护进程存活，记日志后继续下一轮
            try:
                self._consolidate()
                self._idle_finalize()
                self._last = self._now()
            except Exception:
                pass
            # 可中断等待（stop 会立即唤醒，不阻塞）
            self._stop.wait(self.poll_interval)

    def start(self, block: bool = False) -> None:
        if self.is_running():
            raise RuntimeError("守护进程已在运行，拒绝重复启动")
        self._stop.clear()
        self._write_status("running")
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        if block:
            self._thread.join()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._write_status("stopped")

    def install_signal_handlers(self) -> None:
        """注册 SIGTERM / SIGINT 优雅停止（部署时调用；测试不装避免干扰测试进程）。"""
        def _handler(signum, frame):
            self.stop()
        try:
            signal.signal(signal.SIGTERM, _handler)
            signal.signal(signal.SIGINT, _handler)
        except (ValueError, OSError):
            pass  # 非主线程等环境忽略
