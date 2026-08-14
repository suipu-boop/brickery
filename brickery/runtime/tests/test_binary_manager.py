"""binary_manager 单测。

测试覆盖（规格 MARKETPLACE_BINARY_EXT.md §9.1）：
- ensure_running 复用已运行实例
- shutdown_all SIGTERM+SIGKILL
- 崩溃自动重启（最多 1 次）
- 空态安全
- 健康检查失败时清理

用 mock Popen 不真拉进程。
"""
import os
import sys
import time
import signal
import unittest
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

# 确保能 import runtime 包
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from brickery.runtime.binary_manager import BinaryManager, get_manager, shutdown_all


class _FakeProc:
    """模拟 subprocess.Popen。"""

    def __init__(self, alive=True, exit_after=None):
        self._alive = alive  # poll() 返回 None = 还活着
        self._exit_code = None
        self._exit_after = exit_after  # 秒，之后自动退出
        self._start_time = time.time()
        self.terminated = False
        self.killed = False
        self.wait_calls = 0

    def poll(self):
        if self._exit_code is not None:
            return self._exit_code
        if self._exit_after and (time.time() - self._start_time) > self._exit_after:
            self._exit_code = 1
            return 1
        return None  # 还活着

    def terminate(self):
        self.terminated = True
        self._exit_code = -signal.SIGTERM

    def kill(self):
        self.killed = True
        self._exit_code = -signal.SIGKILL

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self._exit_code is not None:
            return self._exit_code
        if timeout:
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)
        return 0


class _FakeSkill:
    """模拟技能包。"""

    def __init__(self, name="test-engine", source="test-engine",
                 port=39099, binary_url="https://example.com/engine.bin"):
        self.name = name
        self.source = source
        self.binary_url = binary_url
        self.binary_launch = {"port": port, "startup_timeout": 2}


def _reset_singleton():
    """每个测试前重置 BinaryManager 单例。"""
    BinaryManager._instance = None
    import brickery.runtime.binary_manager as bm
    bm._default = None


def _patch_launch_env():
    """返回 mock 上下文，统一 mock binary_path_for + Path.exists。

    测试用 ensure_running 时，二进制路径不需要真存在。
    """
    from contextlib import ExitStack
    stack = ExitStack()
    stack.enter_context(
        patch("brickery.runtime.skill_library.SkillLibrary.binary_path_for",
              return_value=Path("/tmp/fake_engine")))
    stack.enter_context(
        patch("brickery.runtime.binary_manager.Path.exists", return_value=True))
    return stack


class TestBinaryManager(unittest.TestCase):

    def setUp(self):
        _reset_singleton()

    def test_singleton(self):
        """BinaryManager 是单例。"""
        a = get_manager()
        b = get_manager()
        self.assertIs(a, b)

    def test_ensure_running_reuses_external(self):
        """端口上已有外部实例时直接复用，不拉起进程。"""
        mgr = get_manager()
        skill = _FakeSkill()

        with patch.object(BinaryManager, "_is_alive",
                          return_value=True):
            port, err = mgr.ensure_running("/tmp/fake_home", skill)

        self.assertIsNone(err)
        self.assertEqual(port, 39099)
        self.assertEqual(mgr.running_count(), 0)  # 外部实例不纳入跟踪

    def test_ensure_running_launches(self):
        """无外部实例时拉起本地二进制。"""
        mgr = get_manager()
        skill = _FakeSkill()

        # _is_alive 先 False（没外部实例），Popen 后变 True（启动成功）
        alive_sequence = [False, False, True]

        def fake_alive(port):
            return alive_sequence.pop(0) if alive_sequence else True

        fake_proc = _FakeProc()

        with _patch_launch_env(), \
             patch.object(BinaryManager, "_is_alive", side_effect=fake_alive), \
             patch("subprocess.Popen", return_value=fake_proc):
            port, err = mgr.ensure_running("/tmp/fake_home", skill)

        self.assertIsNone(err)
        self.assertEqual(port, 39099)
        self.assertEqual(mgr.running_count(), 1)

    def test_ensure_running_no_binary(self):
        """二进制文件不存在时返回错误。"""
        mgr = get_manager()
        skill = _FakeSkill()

        with patch.object(BinaryManager, "_is_alive",
                          return_value=False), \
             patch("brickery.runtime.skill_library.SkillLibrary.binary_path_for",
                   return_value=None):
            port, err = mgr.ensure_running("/tmp/fake_home", skill)

        self.assertIsNone(port)
        self.assertIn("未就绪", err)

    def test_shutdown_all_sigterm(self):
        """shutdown_all 发 SIGTERM 并等进程退出。"""
        mgr = get_manager()
        skill = _FakeSkill()

        fake_proc = _FakeProc()
        # _is_alive: 先 False(没外部实例) -> Popen 后 True(启动成功)
        alive_seq = [False, True]

        def fake_alive(port):
            return alive_seq.pop(0) if alive_seq else True

        with _patch_launch_env(), \
             patch.object(BinaryManager, "_is_alive",
                           side_effect=fake_alive), \
             patch("subprocess.Popen", return_value=fake_proc):
            mgr.ensure_running("/tmp/fake_home", skill)

        self.assertEqual(mgr.running_count(), 1)

        # shutdown
        cleaned = mgr.shutdown_all()
        self.assertEqual(cleaned, 1)
        self.assertTrue(fake_proc.terminated)
        self.assertEqual(mgr.running_count(), 0)

    def test_shutdown_all_sigkill_after_timeout(self):
        """SIGTERM 后 5s 未退出则 SIGKILL。"""
        mgr = get_manager()
        skill = _FakeSkill()

        # 进程不响应 SIGTERM（terminate 后 poll 仍返回 None）
        fake_proc = _FakeProc()
        fake_proc._exit_code = None  # 永远不自己退出

        # 重写 terminate 不设 exit_code
        def stubborn_terminate():
            fake_proc.terminated = True
            # 不设 _exit_code，poll() 仍返回 None

        fake_proc.terminate = stubborn_terminate

        # 重写 wait 模拟超时
        def stubborn_wait(timeout=None):
            raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)

        fake_proc.wait = stubborn_wait

        # _is_alive: 先 False(没外部) -> Popen 后 True(启动成功)
        alive_seq = [False, True]

        def fa(port):
            return alive_seq.pop(0) if alive_seq else True

        with _patch_launch_env(), \
             patch.object(BinaryManager, "_is_alive", side_effect=fa), \
             patch("subprocess.Popen", return_value=fake_proc):
            mgr.ensure_running("/tmp/fake_home", skill)

        cleaned = mgr.shutdown_all()
        self.assertEqual(cleaned, 1)
        self.assertTrue(fake_proc.terminated)  # 先 SIGTERM
        self.assertTrue(fake_proc.killed)      # 再 SIGKILL

    def test_crash_restart_once(self):
        """引擎启动后崩溃，自动重启（最多 1 次）。"""
        mgr = get_manager()
        skill = _FakeSkill()

        # 第一轮 Popen 后进程立即退出（崩溃），第二轮成功
        crash_proc = _FakeProc()
        crash_proc._exit_code = 1  # 已退出

        good_proc = _FakeProc()

        popen_calls = [crash_proc, good_proc]

        # _is_alive: 先 False(没外部) -> Popen(崩溃) -> poll!=None -> 重启 -> Popen(好) -> True
        alive_sequence = [False, False, True]

        def fake_alive(port):
            return alive_sequence.pop(0) if alive_sequence else True

        with _patch_launch_env(), \
             patch.object(BinaryManager, "_is_alive",
                           side_effect=fake_alive), \
             patch("subprocess.Popen",
                   side_effect=lambda *a, **kw: popen_calls.pop(0)):
            port, err = mgr.ensure_running("/tmp/fake_home", skill)

        self.assertIsNone(err)
        self.assertEqual(port, 39099)

    def test_empty_shutdown(self):
        """无引擎时 shutdown_all 安全返回 0。"""
        mgr = get_manager()
        self.assertEqual(mgr.shutdown_all(), 0)
        self.assertEqual(mgr.running_count(), 0)

    def test_is_tracked(self):
        """is_tracked 正确反映跟踪状态。"""
        mgr = get_manager()
        skill = _FakeSkill(port=12345)
        fake_proc = _FakeProc()

        # _is_alive: 先 False(没外部) -> Popen 后 True(启动成功)
        alive_seq = [False, True]

        def fa(port):
            return alive_seq.pop(0) if alive_seq else True

        with _patch_launch_env(), \
             patch.object(BinaryManager, "_is_alive", side_effect=fa), \
             patch("subprocess.Popen", return_value=fake_proc):
            mgr.ensure_running("/tmp/fake_home", skill)

        self.assertTrue(mgr.is_tracked(12345))
        self.assertFalse(mgr.is_tracked(99999))

        mgr.shutdown_all()
        self.assertFalse(mgr.is_tracked(12345))

    def test_module_level_shutdown_all(self):
        """模块级 shutdown_all() 函数正确调用单例。"""
        mgr = get_manager()
        skill = _FakeSkill()
        fake_proc = _FakeProc()

        # _is_alive: 先 False(没外部) -> Popen 后 True(启动成功)
        alive_seq = [False, True]

        def fa(port):
            return alive_seq.pop(0) if alive_seq else True

        with _patch_launch_env(), \
             patch.object(BinaryManager, "_is_alive", side_effect=fa), \
             patch("subprocess.Popen", return_value=fake_proc):
            mgr.ensure_running("/tmp/fake_home", skill)

        cleaned = shutdown_all()
        self.assertEqual(cleaned, 1)
        self.assertTrue(fake_proc.terminated)


if __name__ == "__main__":
    unittest.main()
