"""引擎二进制生命周期管理（BinaryManager 单例）。

按 MARKETPLACE_BINARY_EXT.md §2.4 规格实现：
- 跟踪已启动引擎的 Popen 句柄（port -> Popen 映射）
- ensure_running: 复用已运行实例或拉起本地二进制
- 崩溃自动重启（最多 1 次）
- shutdown_all: SIGTERM -> 等 5s -> SIGKILL，不留孤儿进程

与 ipc.py 的 ppid watchdog 对齐：App 退出时确保所有引擎子进程被清理。
不抛未捕获异常；所有失败返回错误串由上层处理。
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

# 延迟导入 edsdk_pro 的 _mcp_alive 避免循环依赖
# （edsdk_pro.ensure_engine 会调回 BinaryManager）


class _ManagedEngine:
    """一个已启动引擎的运行时状态。"""

    __slots__ = ("proc", "port", "restart_count", "skill_id")

    def __init__(self, proc: subprocess.Popen, port: int, skill_id: str):
        self.proc = proc
        self.port = port
        self.skill_id = skill_id
        self.restart_count = 0  # 崩溃重启计数（上限 1 次）


class BinaryManager:
    """引擎二进制生命周期管理单例。

    设计要点：
    - 单进程内单例，所有引擎启动/关闭走这里。
    - port -> _ManagedEngine 映射，避免同一引擎被重复拉起。
    - shutdown_all 在 App 退出时调用（ipc _sig_handler / watchdog）。
    - 线程安全：用一个锁保护 _engines 字典（ensure_running 可能被多线程调用）。
    """

    _instance: Optional["BinaryManager"] = None

    def __new__(cls) -> "BinaryManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._engines: Dict[int, _ManagedEngine] = {}
            cls._instance._lock_initialized = False
        return cls._instance

    def _ensure_lock(self):
        """延迟初始化锁（避免 __new__ 阶段 threading 还没就绪的问题）。"""
        if not getattr(self, "_lock_initialized", False):
            import threading
            self._lock = threading.Lock()
            self._lock_initialized = True

    # ------------------------------------------------------------------
    # 健康检查（委托给 edsdk_pro._mcp_alive，避免重复实现）
    # ------------------------------------------------------------------

    @staticmethod
    def _is_alive(port: int) -> bool:
        """检查端口上的引擎是否在响应。"""
        try:
            from .edsdk_pro import _mcp_alive
            return _mcp_alive(port)
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 确保引擎运行
    # ------------------------------------------------------------------

    def ensure_running(self, home, skill) -> Tuple[Optional[int], Optional[str]]:
        """确保引擎在运行。返回 (port, 错误)。

        1. 检查是否已在本管理器中跟踪（复用 Popen 句柄）
        2. 检查端口是否已有外部实例在响应（复用，不重复拉起）
        3. 拉起本地二进制，等待健康检查
        4. 崩溃时自动重启（最多 1 次）
        """
        self._ensure_lock()

        launch = getattr(skill, "binary_launch", None) or {}
        port = int(launch.get("port", 39099))
        skill_id = getattr(skill, "source", "") or getattr(skill, "name", "unknown")

        with self._lock:
            # 1. 已跟踪且存活 -> 直接复用
            existing = self._engines.get(port)
            if existing and existing.proc.poll() is None:
                if self._is_alive(port):
                    return port, None
                # 进程还在但 health check 失败 -> 可能卡死，先杀
                self._kill_engine(existing)
                self._engines.pop(port, None)

            # 2. 端口上已有外部实例（如 WorkBuddy 已启动的 editor_sdk）
            if self._is_alive(port):
                return port, None  # 复用，不纳入跟踪（不是我们拉起的）

            # 3. 拉起本地二进制
            result = self._launch(home, skill, port, skill_id)
            # _launch 返回 (port, err)；err 为 None 表示成功
            return result

    def _launch(self, home, skill, port: int, skill_id: str) -> Tuple[Optional[int], Optional[str]]:
        """拉起引擎二进制。返回 (port, err)。"""
        from .skill_library import SkillLibrary

        bp = SkillLibrary.binary_path_for(home, skill)
        if not bp or not Path(bp).exists():
            return None, "引擎二进制未就绪（请先安装技能）"

        args = [str(bp), "--port", str(port)]
        launch = getattr(skill, "binary_launch", None) or {}
        for a in (launch.get("args") or []):
            args.append(str(a).replace("{port}", str(port))
                        .replace("{bin_path}", str(bp)))

        try:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # 引擎是子进程，App 退出时由 shutdown_all 清理；
                # 不用 daemon=True（macOS 上 daemon 线程的 Popen 行为不可靠）
            )
        except OSError as e:
            return None, f"启动引擎失败：{e}"

        engine = _ManagedEngine(proc=proc, port=port, skill_id=skill_id)
        self._engines[port] = engine

        # 等待健康检查
        timeout = int(launch.get("startup_timeout", 10))
        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                # 进程已退出
                break
            if self._is_alive(port):
                return port, None  # 启动成功
            time.sleep(0.2)

        # 启动超时或进程退出 -> 尝试重启一次
        if engine.restart_count < 1:
            engine.restart_count += 1
            self._kill_engine(engine)
            self._engines.pop(port, None)
            # 短暂等待后重试
            time.sleep(0.5)
            return self._launch(home, skill, port, skill_id)

        # 已达重启上限，清理
        self._kill_engine(engine)
        self._engines.pop(port, None)
        return None, "引擎启动超时（已重启 1 次仍失败）"

    # ------------------------------------------------------------------
    # 关闭所有引擎
    # ------------------------------------------------------------------

    def shutdown_all(self) -> int:
        """关闭所有由本管理器启动的引擎。返回已清理的数量。

        SIGTERM -> 等 5s -> SIGKILL，不留孤儿。
        在 App 退出时调用（ipc _sig_handler / watchdog）。
        """
        self._ensure_lock()
        cleaned = 0
        with self._lock:
            engines = list(self._engines.values())
            self._engines.clear()

        for engine in engines:
            self._kill_engine(engine)
            cleaned += 1

        return cleaned

    def _kill_engine(self, engine: _ManagedEngine) -> None:
        """SIGTERM -> 等 5s -> SIGKILL。"""
        proc = engine.proc
        if proc.poll() is not None:
            return  # 已退出

        try:
            proc.terminate()  # SIGTERM
        except OSError:
            return

        # 等 5 秒优雅退出
        try:
            proc.wait(timeout=5)
            return
        except subprocess.TimeoutExpired:
            pass

        # SIGKILL 强制清理
        try:
            proc.kill()
            proc.wait(timeout=3)
        except (OSError, subprocess.TimeoutExpired):
            pass  # 尽力而为，不阻塞退出流程

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------

    def running_count(self) -> int:
        """当前跟踪的运行中引擎数。"""
        self._ensure_lock()
        with self._lock:
            return sum(1 for e in self._engines.values()
                       if e.proc.poll() is None)

    def is_tracked(self, port: int) -> bool:
        """某端口是否由本管理器跟踪。"""
        self._ensure_lock()
        with self._lock:
            return port in self._engines


# 模块级单例（与 CHARTER「复用影子单实例」对齐）
_default: Optional[BinaryManager] = None


def get_manager() -> BinaryManager:
    """获取 BinaryManager 单例。"""
    global _default
    if _default is None:
        _default = BinaryManager()
    return _default


def shutdown_all() -> int:
    """关闭所有引擎（模块级便捷入口，供 ipc 退出时调用）。"""
    return get_manager().shutdown_all()
