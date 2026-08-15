"""§3.4 P0 真实工具单测：handler 真实执行 + 沙箱拦截 + 风险分级。"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from brickery.runtime.builtin_tools import build_p0_registry
from brickery.runtime.sandbox import Sandbox
from brickery.runtime.tools import RiskLevel


class TestP0Handlers(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="brickery_p0_"))
        # macOS 临时目录在 /var/folders 下会被出厂系统区前缀拦截，
        # 这里用 deny_prefixes=[] 仅验证 handler + 白名单逻辑（系统区另测）。
        self.sb = Sandbox(write_roots=[str(self.tmp)],
                          read_roots=[str(self.tmp)],
                          deny_prefixes=[])
        self.reg = build_p0_registry(self.sb)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _h(self, name):
        return self.reg.get(name).handler

    def test_read_write_roundtrip(self):
        p = self.tmp / "note.txt"
        out = self._h("Write")(path=str(p), content="hello world")
        self.assertIn("已写入", out)
        self.assertTrue(p.exists())
        content = self._h("Read")(path=str(p))
        self.assertEqual(content, "hello world")

    def test_edit_replaces(self):
        p = self.tmp / "f.txt"
        p.write_text("foo bar baz", encoding="utf-8")
        out = self._h("Edit")(path=str(p), old_string="bar",
                              new_string="QUX")
        self.assertIn("已替换", out)
        self.assertEqual(p.read_text(encoding="utf-8"), "foo QUX baz")

    def test_edit_missing_old(self):
        p = self.tmp / "f.txt"
        p.write_text("abc", encoding="utf-8")
        out = self._h("Edit")(path=str(p), old_string="zzz",
                              new_string="y")
        self.assertIn("未在文件中找到", out)

    def test_glob_lists(self):
        (self.tmp / "a.py").write_text("x")
        (self.tmp / "b.py").write_text("x")
        (self.tmp / "c.txt").write_text("x")
        out = self._h("Glob")(pattern="*.py", path=str(self.tmp))
        self.assertIn("a.py", out)
        self.assertIn("b.py", out)
        self.assertNotIn("c.txt", out)

    def test_grep_finds(self):
        (self.tmp / "code.py").write_text("def foo():\n  return SECRET\n")
        out = self._h("Grep")(pattern="SECRET", path=str(self.tmp))
        self.assertIn("code.py", out)
        self.assertIn("SECRET", out)

    def test_bash_runs(self):
        out = self._h("Bash")(command="echo pong")
        self.assertIn("pong", out)
        self.assertIn("returncode=0", out)

    def test_write_outside_sandbox_denied(self):
        out = self._h("Write")(path="/tmp/nope.txt", content="x")
        self.assertIn("沙箱拒绝", out)

    def test_read_outside_sandbox_denied(self):
        out = self._h("Read")(path="/etc/passwd")
        self.assertIn("沙箱拒绝", out)

    def test_bash_dangerous_blocked(self):
        out = self._h("Bash")(command="sudo rm -rf /")
        self.assertIn("沙箱拒绝", out)

    def test_risk_levels(self):
        self.assertEqual(self.reg.get("Read").risk, RiskLevel.LOW)
        self.assertEqual(self.reg.get("Glob").risk, RiskLevel.LOW)
        self.assertEqual(self.reg.get("Grep").risk, RiskLevel.LOW)
        self.assertEqual(self.reg.get("Edit").risk, RiskLevel.MEDIUM)
        self.assertEqual(self.reg.get("Write").risk, RiskLevel.MEDIUM)
        self.assertEqual(self.reg.get("Bash").risk, RiskLevel.HIGH)
        self.assertEqual(self.reg.get("CodeRun").risk, RiskLevel.MEDIUM)
        self.assertEqual(self.reg.get("web_search").risk, RiskLevel.MEDIUM)
        self.assertEqual(self.reg.get("WebFetch").risk, RiskLevel.MEDIUM)
        self.assertEqual(self.reg.get("repo_map").risk, RiskLevel.LOW)

    def test_registry_has_fifteen(self):
        names = {t.name for t in self.reg.all()}
        self.assertEqual(names, {"Read", "Edit", "Write", "Glob", "Grep",
                                 "Bash", "CodeRun", "web_search",
                                 "WebFetch", "repo_map",
                                 "DocRead", "TableStat", "ImageOps",
                                 "vault_query", "vault_save"})

    def test_default_registry_no_sandbox_arg(self):
        # 不传 sandbox 也能构建（用出厂默认沙箱）
        reg = build_p0_registry()
        self.assertEqual(len(reg.all()), 15)

    def test_core_tools_always_available(self):
        # 方案 b（随朴特批）：5 生命线工具常驻，选择器漏匹配也不致残
        core = {"Read", "Edit", "Write", "Bash", "Grep"}
        for name in core:
            self.assertTrue(self.reg.get(name).always_available,
                            f"{name} 应为常驻核心工具")

    def test_non_core_not_always_available(self):
        # 其余按需，不强制常驻（保持 token 经济性）
        for name in ("Glob", "CodeRun", "repo_map",
                     "DocRead", "TableStat", "ImageOps"):
            self.assertFalse(self.reg.get(name).always_available,
                             f"{name} 不应常驻")

    def test_select_never_empty_on_unmatched(self):
        # 即使关键词完全没命中，也至少推 5 核心，绝不返回空（防致残）
        sel = self.reg.select("随便聊聊今天天气真好")
        names = {t.name for t in sel}
        self.assertTrue({"Read", "Edit", "Write", "Bash", "Grep"}.issubset(names),
                        f"未命中也应含核心工具，实际: {names}")


if __name__ == "__main__":
    unittest.main()
