"""repo_map 代码索引单测：Python ast 抽取、正则兜底、目录遍历、沙箱拦截、错误处理。"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from brickery.runtime.builtin_tools import build_p0_registry
from brickery.runtime.repo_map import build_repo_map
from brickery.runtime.sandbox import Sandbox


_PY = '''
import os

class Animal:
    def speak(self):
        return "..."

def make_cat():
    return Animal()

async def run():
    pass
'''

_JS = '''
function foo() { return 1; }
class Widget {
  render() { return "x"; }
}
const bar = () => 2;
'''


class TestRepoMap(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="brickery_rmap_"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_py_symbols(self):
        f = self.tmp / "a.py"
        f.write_text(_PY, encoding="utf-8")
        out = build_repo_map(str(f))
        self.assertIn("Animal", out)
        self.assertIn("make_cat", out)
        self.assertIn("run", out)
        self.assertIn("speak", out)
        # 应带行范围标注
        self.assertIn("L", out)

    def test_dir_walk(self):
        (self.tmp / "sub").mkdir()
        (self.tmp / "sub" / "m.py").write_text(_PY, encoding="utf-8")
        (self.tmp / "w.js").write_text(_JS, encoding="utf-8")
        (self.tmp / "note.txt").write_text("ignored", encoding="utf-8")
        out = build_repo_map(str(self.tmp))
        self.assertIn("Animal", out)       # python
        self.assertIn("Widget", out)       # js via regex
        self.assertIn("render", out)
        self.assertIn("foo", out)
        self.assertNotIn("note.txt", out)  # 非代码扩展名跳过

    def test_missing_path(self):
        out = build_repo_map(str(self.tmp / "nope"))
        self.assertIn("[repo_map] 路径不存在", out)

    def test_no_symbols(self):
        f = self.tmp / "blank.py"
        f.write_text("# only comments\n", encoding="utf-8")
        out = build_repo_map(str(f))
        self.assertIn("中识别到代码符号", out)

    def test_registered_in_p0(self):
        reg = build_p0_registry()
        names = {t.name for t in reg.all()}
        self.assertIn("repo_map", names)
        self.assertIn("WebFetch", names)
        # 已知内置工具：10 基础 + Vault 2（vault_save/vault_query）+ 发布版通用 3
        for expected in ("repo_map", "WebFetch", "vault_save", "vault_query"):
            self.assertIn(expected, names)
        self.assertEqual(len(reg.all()), 15)   # 10 基础 + Vault 2 + 发布版通用 3

    def test_handler_respects_sandbox(self):
        # 在仅允许 tmp 读写的沙箱里，读 /etc 应被拒
        sb = Sandbox(write_roots=[str(self.tmp)],
                     read_roots=[str(self.tmp)], deny_prefixes=[])
        reg = build_p0_registry(sb)
        out = reg.get("repo_map").handler(path="/etc")
        self.assertIn("沙箱拒绝", out)


if __name__ == "__main__":
    unittest.main()
